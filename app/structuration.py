"""Transcription -> devis structuré, via un LLM en sortie JSON contrainte.

Interface unique : `structure(transcript, grille=None) -> DevisExtraction`.

`grille` est le tarif de l'artisan connecté, quand il en a un. Sans tarif, l'appel au
modèle est exactement celui d'avant : même prompt, même message.

Le schéma est imposé par le SDK à partir du modèle Pydantic (`output_format`), donc la
réponse est validée avant de nous parvenir : pas de parsing JSON à la main, pas de regex,
pas de « et si le modèle rajoutait du texte autour ».
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path

import anthropic
from pydantic import BaseModel

from app import suivi
from app.config import RACINE, get_config
from app.models import DevisExtraction, GrillePrix, arrondi

logger = logging.getLogger("devis-vocal")

PROMPT_PATH = Path(__file__).parent / "prompts" / "structuration.md"
FIXTURES = RACINE / "tests" / "fixtures"


class StructurationError(RuntimeError):
    pass


@lru_cache
def _prompt_systeme() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _depuis_fixture(transcript: str) -> DevisExtraction:
    """Mode hors-ligne : rejoue une extraction enregistrée. Zéro appel réseau, zéro euro.

    On apparie sur le début de la transcription pour pouvoir coller n'importe laquelle des
    fixtures dans le navigateur et retrouver son extraction.
    """
    empreinte = " ".join(transcript.split())[:60].lower()
    for chemin in sorted(FIXTURES.glob("*.json")):
        txt = chemin.with_suffix(".txt")
        if txt.exists() and " ".join(txt.read_text(encoding="utf-8").split())[:60].lower() == empreinte:
            # Sans cette ligne, rien dans le log ne distingue un devis chiffré par le
            # modèle d'un devis rejoué depuis le disque.
            suivi.appel("fixtures", chemin.stem)
            return DevisExtraction.model_validate_json(chemin.read_text(encoding="utf-8"))

    disponibles = sorted(p.stem for p in FIXTURES.glob("*.json"))
    raise StructurationError(
        "USE_FIXTURES est actif mais aucune fixture ne correspond à cette transcription. "
        f"Fixtures disponibles : {', '.join(disponibles) or 'aucune'}. "
        "Colle le contenu d'un fichier tests/fixtures/*.txt, ou repasse USE_FIXTURES=false."
    )


def _schema_strict(modele: type[BaseModel] = DevisExtraction) -> dict:
    """Le schéma JSON d'une sortie (le devis par défaut), durci pour les sorties
    contraintes façon OpenAI.

    Ces API exigent `additionalProperties: false` et un `required` exhaustif sur *chaque*
    objet, y compris les définitions imbriquées — ce que Pydantic ne génère pas seul.
    """

    def durcir(noeud):
        if isinstance(noeud, dict):
            if noeud.get("type") == "object":
                noeud["additionalProperties"] = False
                if "properties" in noeud:
                    noeud["required"] = list(noeud["properties"])
            for valeur in noeud.values():
                durcir(valeur)
        elif isinstance(noeud, list):
            for valeur in noeud:
                durcir(valeur)
        return noeud

    return durcir(modele.model_json_schema())


def _bloc_grille(grille: GrillePrix) -> str:
    """Le tarif de l'artisan, tel qu'il part au modèle à côté du prompt.

    Une ligne par prix, colonnes séparées par une barre verticale — `grilles.valider` la
    retire des textes, donc les colonnes ne se décalent jamais. Le prix s'écrit avec un
    point et deux décimales, comme le modèle doit le recopier dans `prix_unitaire_ht`.
    Les règles d'usage, elles, vivent dans le prompt (« Le tarif de l'artisan »).
    """
    lignes = []
    for p in grille.lignes:
        colonnes = [p.id, p.prestation, p.unite.value, f"{p.prix_unitaire_ht:.2f}"]
        if p.precision:
            colonnes.append(p.precision)
        lignes.append(" | ".join(colonnes))
    return (
        "Le tarif de l'artisan qui dicte ce vocal. Colonnes : identifiant | prestation | "
        "unité | prix unitaire HT | précision éventuelle.\n"
        "<tarif_artisan>\n" + "\n".join(lignes) + "\n</tarif_artisan>"
    )


def _generer_compatible_openai(
    modele: type[BaseModel], systeme: list[str], contenu: str, *, api_key: str,
    base_url: str | None, model: str, fournisseur: str, delai: float, nom_schema: str,
):
    """Appel via une API au format OpenAI — sert à la fois pour Groq et pour OpenAI.

    Ces deux-là partagent le même SDK et la même forme de sortie contrainte ; seuls
    l'URL, la clé et le modèle changent. Un seul chemin de code, donc une comparaison
    honnête entre fournisseurs : ils reçoivent exactement le même prompt.
    """
    from openai import OpenAI, OpenAIError

    if not api_key:
        # OpenAI est le repli quand aucune clé n'est trouvée : ce message est celui
        # qu'on lit en premier sur un poste neuf, il doit dire quoi faire.
        raise StructurationError(
            f"Clé API absente : impossible de chiffrer via {fournisseur}. Renseigne-la "
            "dans le .env, ou passe USE_FIXTURES=true pour travailler sur les "
            "extractions enregistrées."
        )

    # Un délai et une seule nouvelle tentative : par défaut le SDK attend 600 s et
    # réessaie deux fois, soit une demi-heure pour un fournisseur qui traîne.
    client = OpenAI(api_key=api_key, base_url=base_url, timeout=delai, max_retries=1)

    # Parler le format OpenAI ne veut pas dire accepter les mêmes sorties contraintes.
    # OpenAI et Groq gèrent `json_schema` strict ; DeepSeek ou un Ollama local n'ont
    # que `json_object`. On demande le plus contraint et on redescend d'un cran s'il
    # est refusé. Pydantic reste le filet : un modèle qui divague échoue franchement
    # au lieu de produire un devis à moitié faux.
    schema = _schema_strict(modele)
    tentatives = (
        ("json_schema",
         {"type": "json_schema",
          "json_schema": {"name": nom_schema, "schema": schema, "strict": True}},
         None),
        ("json_object",
         {"type": "json_object"},
         "Réponds uniquement par un objet JSON conforme à ce schéma, sans texte autour : "
         + json.dumps(schema, ensure_ascii=False)),
    )

    derniere: Exception | None = None
    for mode, format_sortie, consigne in tentatives:
        messages = [{"role": "system", "content": systeme[0]}]
        if consigne:
            # Le schéma part dans un message à lui : le prompt de référence ne bouge
            # pas, et la comparaison entre fournisseurs reste honnête.
            messages.append({"role": "system", "content": consigne})
        # Ce qui accompagne le prompt (le tarif de l'artisan), même principe : un
        # message à lui, après le prompt.
        messages.extend({"role": "system", "content": texte} for texte in systeme[1:])
        messages.append({"role": "user", "content": contenu})

        try:
            reponse = client.chat.completions.create(
                model=model, messages=messages, response_format=format_sortie,
            )
        except OpenAIError as err:
            derniere = err
            # Un 400 trahit en général un `response_format` non supporté. Tout le
            # reste — clé invalide, crédit épuisé, panne — échouerait pareil au
            # second essai : inutile de le payer deux fois.
            if getattr(err, "status_code", None) != 400:
                break
            logger.warning("%s refuse le mode %s : %s", fournisseur, mode, str(err)[:200])
            continue

        # Ce qu'a coûté l'appel, tel que le fournisseur le rapporte. Posé ici et
        # pas après la boucle : un essai refusé n'a rien consommé, seul celui qui
        # aboutit compte. Le mode retenu part dans la ligne suivante.
        usage = getattr(reponse, "usage", None)
        suivi.appel(fournisseur, model,
                    getattr(usage, "prompt_tokens", None),
                    getattr(usage, "completion_tokens", None))

        sortie = reponse.choices[0].message.content
        if not sortie:
            raise StructurationError(f"{fournisseur} n'a rien renvoyé.")
        if mode != "json_schema":
            # Ça doit se voir : le schéma n'est plus imposé par l'API, seulement
            # demandé au modèle. La sortie est moins sûre qu'elle en a l'air.
            logger.warning(
                "%s a chiffré en mode %s : schéma non imposé par l'API.", fournisseur, mode,
            )
        return modele.model_validate_json(sortie)

    # Le message du fournisseur porte son URL, ses noms de modèles et parfois des
    # détails de compte. Il a sa place dans le journal du serveur, pas dans une
    # réponse HTTP que n'importe qui peut provoquer.
    logger.error("Appel %s en échec : %s", fournisseur, derniere)
    raise StructurationError(
        "Le service de chiffrage n'a pas répondu. Réessayez dans un instant."
    ) from derniere


# Un nombre tel qu'une transcription l'écrit : « 48 », « 12,50 », et les milliers groupés
# « 1 150 » ou « 1.150 ».
_NOMBRE = re.compile(r"\d+(?:[.,]\d+)?")
_MILLIERS = re.compile(r"\d{1,3}(?:[   .]\d{3})+(?:,\d+)?")


def nombres_dits(transcript: str) -> set[float]:
    """Tous les nombres de la transcription, lus de toutes les façons plausibles.

    On lit large exprès : un nombre en trop ne fait que rater un signalement, alors qu'un
    nombre manqué ferait relire un prix que l'artisan a bien dicté.
    """
    dits = {round(float(m.group().replace(",", ".")), 2) for m in _NOMBRE.finditer(transcript)}
    for m in _MILLIERS.finditer(transcript):
        dits.add(round(float(re.sub(r"[   .]", "", m.group()).replace(",", ".")), 2))
    return dits


def _verifier_les_refs(extraction: DevisExtraction, grille: GrillePrix | None) -> DevisExtraction:
    """Ne garde une référence au tarif que si elle est vérifiable de bout en bout.

    Le modèle dit d'où vient un prix (`ref_grille`) ; on ne le croit pas sur parole. La
    référence tient si l'identifiant existe, si l'unité est la même et si le prix recopié
    est **exactement** celui du tarif. Sinon on la retire, et la ligne redevient ordinaire :
    le garde-fou suivant tranche entre prix dicté et prix à valider.

    On vérifie, on n'impose jamais. Imposer le prix du tarif sur une référence mal
    recopiée — l'identifiant du parquet sur une ligne de faïence — afficherait un prix
    faux comme confirmé, sans que rien ne le signale.
    """
    index = {p.id: p for p in grille.lignes} if grille else {}
    for ligne in extraction.lignes:
        if ligne.ref_grille is None:
            continue
        ref = index.get(ligne.ref_grille.strip())
        if (ref is None or ref.unite != ligne.unite
                or arrondi(ligne.prix_unitaire_ht) != ref.prix_unitaire_ht):
            logger.warning(
                "Référence au tarif %r non vérifiée pour « %s » (%s %s) : retirée.",
                ligne.ref_grille, ligne.designation, ligne.prix_unitaire_ht, ligne.unite.value,
            )
            ligne.ref_grille = None
        else:
            ligne.ref_grille = ref.id
    return extraction


def _signaler_les_prix_non_dictes(extraction: DevisExtraction, transcript: str) -> DevisExtraction:
    """Marque `a_valider` toute ligne « dictée » dont le prix n'apparaît pas dans le vocal.

    Le prompt l'exige déjà ; ceci le garantit. Le garde-fou ajoute des signalements, il
    n'en retire jamais : au pire il fait relire un prix dicté en toutes lettres, jamais il
    ne fait passer une estimation pour un prix confirmé.

    Les lignes au prix du tarif de l'artisan sont épargnées : leur prix n'a pas été
    dicté, il a été vérifié contre le tarif par `_verifier_les_refs`, juste avant.
    """
    dits = nombres_dits(transcript)
    for ligne in extraction.lignes:
        if ligne.ref_grille is not None:
            continue
        if not ligne.a_valider and round(ligne.prix_unitaire_ht, 2) not in dits:
            logger.warning(
                "Prix %s absent de la transcription : « %s » passe à valider.",
                ligne.prix_unitaire_ht, ligne.designation,
            )
            ligne.a_valider = True
    return extraction


def structure(transcript: str, grille: GrillePrix | None = None) -> DevisExtraction:
    """Transforme une transcription en devis structuré, non chiffré.

    `grille` : le tarif de l'artisan connecté. Ses prix passent avant les fourchettes de
    référence, et chaque ligne qui s'en réclame est vérifiée ici avant de sortir.
    """
    transcript = transcript.strip()
    if not transcript:
        raise StructurationError("Transcription vide.")
    bloc = _bloc_grille(grille) if grille and grille.lignes else None
    extraction = _verifier_les_refs(_extraire(transcript, bloc), grille)
    return _signaler_les_prix_non_dictes(extraction, transcript)


def _extraire(transcript: str, bloc: str | None = None) -> DevisExtraction:
    """Confie la transcription au fournisseur configuré, ou la rejoue depuis les fixtures."""
    config = get_config()
    if config.use_fixtures:
        return _depuis_fixture(transcript)
    systeme = [_prompt_systeme()] + ([bloc] if bloc else [])
    return generer(DevisExtraction, systeme=systeme, contenu=transcript,
                   delai=config.delai_chiffrage_s, nom_schema="devis")


def generer(modele: type[BaseModel], *, systeme: list[str], contenu: str, delai: float,
            max_tokens: int = 16000, nom_schema: str = "reponse"):
    """Le seul point d'appel au modèle de langage, quel que soit le fournisseur.

    `systeme` : le prompt d'abord, puis ce qui l'accompagne (le tarif de l'artisan).
    `modele` : la sortie attendue, imposée par l'API — le devis pour le chiffrage, les
    prix lus pour l'import d'un tarif. Avec un seul texte système, l'appel est
    exactement celui d'avant : une chaîne chez Anthropic, un message chez OpenAI.
    """
    config = get_config()

    if config.structuration_provider == "groq":
        return _generer_compatible_openai(
            modele, systeme, contenu,
            api_key=config.groq_api_key,
            base_url=config.groq_base_url,
            model=config.model_structuration_groq,
            fournisseur="Groq",
            delai=delai, nom_schema=nom_schema,
        )

    if config.structuration_provider == "openai":
        return _generer_compatible_openai(
            modele, systeme, contenu,
            api_key=config.openai_api_key,
            base_url=None,  # api.openai.com
            model=config.model_structuration_openai,
            fournisseur="OpenAI",
            delai=delai, nom_schema=nom_schema,
        )

    if config.structuration_provider == "autre":
        if not config.structuration_base_url or not config.model_structuration_autre:
            raise StructurationError(
                "STRUCTURATION_PROVIDER=autre exige STRUCTURATION_BASE_URL et "
                "MODEL_STRUCTURATION_AUTRE dans le .env."
            )
        return _generer_compatible_openai(
            modele, systeme, contenu,
            api_key=config.structuration_api_key,
            base_url=config.structuration_base_url,
            model=config.model_structuration_autre,
            # Le nom du fournisseur est son URL : c'est ce qui identifie vraiment
            # qui a chiffré, et ça se retrouve tel quel dans les logs.
            fournisseur=config.structuration_base_url,
            delai=delai, nom_schema=nom_schema,
        )

    if not config.anthropic_api_key:
        raise StructurationError(
            "ANTHROPIC_API_KEY absente. Renseigne-la dans le .env, ou passe USE_FIXTURES=true "
            "pour travailler sur les extractions enregistrées."
        )

    client = anthropic.Anthropic(api_key=config.anthropic_api_key,
                                 timeout=delai, max_retries=1)
    try:
        reponse = client.messages.parse(
            model=config.model_structuration,
            max_tokens=max_tokens,
            # Un seul texte : une simple chaîne, comme avant le tarif de l'artisan.
            system=(systeme[0] if len(systeme) == 1 else [
                {"type": "text", "text": texte} for texte in systeme
            ]),
            messages=[{"role": "user", "content": contenu}],
            output_format=modele,
        )
    except anthropic.APIError as err:  # clé invalide, crédit épuisé, surcharge...
        logger.error("Appel Anthropic en échec : %s", err)
        raise StructurationError(
            "Le service de chiffrage n'a pas répondu. Réessayez dans un instant."
        ) from err

    suivi.appel("anthropic", config.model_structuration,
                reponse.usage.input_tokens, reponse.usage.output_tokens)

    sortie = reponse.parsed_output
    if sortie is None:
        raise StructurationError("Le modèle n'a pas renvoyé de réponse exploitable.")
    return sortie


def enregistrer_fixture(nom: str, transcript: str, extraction: DevisExtraction) -> None:
    """Fige une extraction sur disque pour alimenter le mode hors-ligne et les tests.

    Utilisé par `scripts/enregistrer_fixtures.py` — un appel API payé une fois, rejoué
    gratuitement ensuite pendant tout le développement du PDF et du front.
    """
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"{nom}.txt").write_text(transcript.strip() + "\n", encoding="utf-8")
    (FIXTURES / f"{nom}.json").write_text(
        json.dumps(extraction.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
