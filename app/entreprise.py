"""Identité de l'entreprise — retrouvée plutôt que saisie.

Faire taper un SIRET à un artisan, c'est quatorze chiffres sans signification, sur un
téléphone, debout dans un couloir. C'est le geste le plus coûteux du parcours et il ne
produit rien : l'information est publique et l'État la tient déjà à jour.

Ce module la retrouve à partir du nom. L'artisan tape « dupont plomberie », choisit dans
une liste, et sept champs de l'en-tête se remplissent.

Deux garanties, dans cet ordre :

- **Rien n'est inventé.** Ce qui n'est pas dans la source publique reste vide, comme
  partout ailleurs ici. Le numéro de TVA n'est qu'une exception apparente : il n'est pas
  deviné, il se *calcule* depuis le SIREN par une formule exacte — et l'API le renvoie
  aussi, ce qui donne deux sources qu'on peut confronter.
- **La saisie manuelle ne disparaît jamais.** Beaucoup d'artisans sont en entreprise
  individuelle et peuvent s'opposer à la diffusion de leurs données : leur nom est alors
  masqué dans la base. Le formulaire reste donc toujours ouvert, avec la clé de Luhn pour
  attraper une faute de frappe sans même appeler le réseau.

Source : l'API publique « Recherche d'entreprises » — ouverte, gratuite, sans clé ni
compte. https://recherche-entreprises.api.gouv.fr

Tout ce qui précède la section « Recherche » est pur : aucun réseau, testable nu.
"""

from __future__ import annotations

import logging
import re

import httpx
from pydantic import BaseModel, ConfigDict

from app.models import Entreprise

logger = logging.getLogger("devis-vocal")

API_URL = "https://recherche-entreprises.api.gouv.fr/search"

# L'API est publique et sans authentification ; s'annoncer est la moindre des politesses,
# et c'est ce qui permet à son exploitant de nous joindre plutôt que de nous filtrer.
ENTETES = {"User-Agent": "devis-vocal (demonstrateur artisans)"}

DELAI = 6.0  # secondes : au-delà, la saisie manuelle est plus rapide que l'attente


# ---------------------------------------------------------------------------
# Fonctions pures — la partie qui doit être juste à tous les coups
# ---------------------------------------------------------------------------


def chiffres(valeur: str) -> str:
    """Ne garde que les chiffres : « 394 659 882 00010 » et « 39465988200010 » sont un."""
    return re.sub(r"\D", "", valeur or "")


def luhn_valide(numero: str) -> bool:
    """Contrôle de Luhn — c'est ce qui rend un SIREN ou un SIRET auto-vérifiable.

    Les identifiants d'entreprise portent leur propre clé de contrôle : une faute de
    frappe se détecte hors ligne, immédiatement, sans rien demander à personne. C'est le
    filet de la saisie manuelle, celle qui reste quand la recherche ne trouve rien.

    Attention à ce que ça ne prouve pas : un numéro peut passer Luhn sans correspondre à
    la moindre entreprise. On attrape la coquille, pas le mensonge.
    """
    n = chiffres(numero)
    if len(n) not in (9, 14) or not n.isdigit():
        return False

    # La Poste échappe à la règle depuis toujours — ses établissements se contrôlent par
    # la divisibilité par 5. Aucun artisan n'est concerné, mais un numéro valide refusé
    # est un bug qu'on ne comprend qu'au pire moment.
    if n.startswith("356000000"):
        return sum(int(c) for c in n) % 5 == 0

    total = 0
    # Luhn se lit de droite à gauche : on double un chiffre sur deux en partant de la fin.
    for position, caractere in enumerate(reversed(n)):
        chiffre = int(caractere)
        if position % 2 == 1:
            chiffre *= 2
            if chiffre > 9:
                chiffre -= 9
        total += chiffre
    return total % 10 == 0


def numero_tva(siren: str) -> str:
    """Le numéro de TVA intracommunautaire français, calculé depuis le SIREN.

    `FR` + une clé à deux chiffres + le SIREN. La clé n'est pas une donnée à chercher,
    c'est une formule — donc un champ obligatoire de moins à demander à l'artisan, et
    aucun aller-retour réseau pour l'obtenir.
    """
    n = chiffres(siren)[:9]
    if len(n) != 9:
        return ""
    cle = (12 + 3 * (int(n) % 97)) % 97
    return f"FR{cle:02d}{n}"


def formater_siret(siret: str) -> str:
    """« 39465988200010 » -> « 394 659 882 00010 », la découpe lisible du devis."""
    n = chiffres(siret)
    if len(n) != 14:
        return siret or ""
    return f"{n[0:3]} {n[3:6]} {n[6:9]} {n[9:]}"


def normaliser_ape(code: str) -> str:
    """« 43.22A » -> « 4322A ». L'API pointe, le devis écrit sans point."""
    return (code or "").replace(".", "").replace(" ", "").upper()


# Les particules restent en bas de casse, sauf en tête. La base INSEE est tout en
# capitales, et un `.title()` naïf écrit « Avenue De Grande Bretagne » ou
# « Aix-En-Provence » : personne n'écrit une adresse comme ça, et celle-ci finit
# imprimée en tête d'un document dont l'argument est le sérieux.
PARTICULES = {"de", "du", "des", "d", "le", "la", "les", "l", "et", "en", "sur",
              "sous", "au", "aux", "lès", "ès"}


def capitaliser_fr(texte: str) -> str:
    """Met une chaîne en capitales françaises. Coupe aussi sur les traits et apostrophes.

    Ce qu'on ne peut pas faire : rendre les accents. La source n'en a pas, et les
    deviner produirait des fautes — « Chateauneuf » reste tel quel plutôt que de risquer
    un « Châteauneuf » sur une commune qui s'écrit sans accent.
    """
    if not texte:
        return ""

    morceaux = re.split(r"([ \-'])", texte.strip().lower())
    sortie = []
    debut = True
    for morceau in morceaux:
        if morceau in (" ", "-", "'"):
            sortie.append(morceau)
            continue
        if not morceau:
            continue
        if debut or morceau not in PARTICULES:
            sortie.append(morceau[:1].upper() + morceau[1:])
        else:
            sortie.append(morceau)
        debut = False
    return "".join(sortie)


# Catégories juridiques INSEE. On ne cartographie que ce qu'un artisan peut être ; le
# reste retombe sur le préfixe, qui donne la famille sans se tromper. Un libellé faux
# sur un devis serait pire qu'un champ que l'artisan complète lui-même.
FORMES_EXACTES = {
    "1000": "Entrepreneur individuel",
    "5202": "SNC",
    "5410": "SARL",
    "5415": "SARL",
    "5426": "SARL",
    "5498": "EURL",
    "5499": "SARL",
    "5505": "SA",
    "5510": "SA",
    "5710": "SAS",
    "5720": "SASU",
    "6540": "SCI",
}

FORMES_PREFIXES = {
    "1": "Entrepreneur individuel",
    "52": "SNC",
    "54": "SARL",
    "55": "SA",
    "57": "SAS",
    "65": "SCI",
}


def forme_juridique(code: str) -> str:
    """Libellé court de la catégorie juridique. Vide si on ne sait pas."""
    code = (code or "").strip()
    if code in FORMES_EXACTES:
        return FORMES_EXACTES[code]
    for longueur in (2, 1):
        if code[:longueur] in FORMES_PREFIXES:
            return FORMES_PREFIXES[code[:longueur]]
    return ""


# Le métier tel qu'il s'écrit sur le devis, déduit du code APE. Ce n'est pas le libellé
# officiel de la nomenclature — « Travaux d'installation d'eau et de gaz en tous locaux »
# ne va pas sous une raison sociale. C'est la ligne d'accent, et elle reste modifiable.
METIERS_APE = {
    "4120A": "Construction de maisons individuelles",
    "4120B": "Bâtiment · Gros œuvre",
    "4321A": "Électricité générale",
    "4321B": "Réseaux · Télécommunications",
    "4322A": "Plomberie · Chauffage · Sanitaire",
    "4322B": "Chauffage · Climatisation",
    "4329A": "Isolation",
    "4329B": "Second œuvre",
    "4331Z": "Plâtrerie · Cloisons · Doublage",
    "4332A": "Menuiserie bois et PVC",
    "4332B": "Menuiserie métallique · Serrurerie",
    "4332C": "Agencement de lieux de vente",
    "4333Z": "Revêtements sols et murs · Carrelage",
    "4334Z": "Peinture · Vitrerie",
    "4339Z": "Travaux de finition",
    "4391A": "Charpente",
    "4391B": "Couverture · Zinguerie",
    "4399A": "Étanchéité",
    "4399C": "Maçonnerie générale",
    "4399D": "Rénovation · Second œuvre",
    "4399E": "Terrassement",
    "8121Z": "Nettoyage · Remise en état",
}


def metier_depuis_ape(code: str) -> str:
    """Ligne de métier proposée depuis le code APE. Vide si le code n'est pas du bâtiment."""
    return METIERS_APE.get(normaliser_ape(code), "")


# ---------------------------------------------------------------------------
# Recherche — la seule partie qui touche au réseau
# ---------------------------------------------------------------------------


class EntrepriseTrouvee(BaseModel):
    """Ce qu'on sait d'une entreprise après recherche.

    Volontairement pas un `Entreprise` : celui-ci exige treize champs, or la source
    publique n'en connaît que sept. Les six autres — téléphone, e-mail, assurance
    décennale, IBAN — n'appartiennent qu'à l'artisan. Les fusionner ici reviendrait à
    inventer des vides, et le front n'aurait plus aucun moyen de distinguer « rempli
    par la base » de « à demander ».
    """

    siren: str
    siret: str
    nom: str
    forme_juridique: str = ""
    adresse: str = ""
    code_postal_ville: str = ""
    code_ape: str = ""
    metier: str = ""
    tva_intracom: str = ""

    # Contexte d'affichage : deux entreprises peuvent porter le même nom, c'est la ville
    # et la date qui permettent à l'artisan de reconnaître la sienne.
    ville: str = ""
    date_creation: str = ""


def _mapper(brut: dict) -> EntrepriseTrouvee | None:
    """Traduit un résultat de l'API en identité de devis. `None` si inexploitable."""
    siren = chiffres(brut.get("siren") or "")
    if len(siren) != 9:
        return None

    # Quand la recherche porte sur un SIRET, l'API désigne l'établissement concerné dans
    # `matching_etablissements` — qui n'est pas forcément le siège. C'est celui-là qu'il
    # faut retenir : un artisan qui donne le SIRET de son agence attend son adresse.
    correspondants = brut.get("matching_etablissements") or []
    etablissement = correspondants[0] if correspondants else (brut.get("siege") or {})

    siret = chiffres(etablissement.get("siret") or "")
    if len(siret) != 14:
        return None

    code_postal = (etablissement.get("code_postal") or "").strip()
    commune = capitaliser_fr(etablissement.get("libelle_commune") or "")

    # `adresse` arrive en un seul bloc, ville comprise : « 88 AVENUE ... 31300 TOULOUSE ».
    # Le devis affiche la rue et la ville sur deux lignes, donc on retire la queue.
    voie = (etablissement.get("adresse") or "").strip()
    if code_postal and code_postal in voie:
        voie = voie[: voie.index(code_postal)].strip()

    code_ape = etablissement.get("activite_principale") or brut.get("activite_principale") or ""

    # L'API renvoie déjà le numéro de TVA. On préfère quand même le calcul : c'est la
    # même valeur (vérifié), il fonctionne quand le champ est absent, et il reste juste
    # si la forme de la réponse change un jour.
    tva = numero_tva(siren)

    return EntrepriseTrouvee(
        siren=siren,
        siret=formater_siret(siret),
        nom=(brut.get("nom_complet") or brut.get("nom_raison_sociale") or "").strip(),
        forme_juridique=forme_juridique(brut.get("nature_juridique") or ""),
        adresse=capitaliser_fr(voie),
        code_postal_ville=f"{code_postal} {commune}".strip(),
        code_ape=normaliser_ape(code_ape),
        metier=metier_depuis_ape(code_ape),
        tva_intracom=tva,
        ville=commune,
        date_creation=(brut.get("date_creation") or "")[:4],
    )


async def rechercher(requete: str, limite: int = 6) -> list[EntrepriseTrouvee]:
    """Cherche une entreprise par nom, ville ou numéro. Liste vide si rien ne colle.

    Ne lève pas sur un échec réseau : la panne de l'annuaire ne doit pas empêcher de
    faire un devis, elle doit renvoyer l'artisan vers la saisie manuelle.
    """
    requete = (requete or "").strip()
    if len(requete) < 3:
        return []

    # Un numéro qui ne passe pas sa propre clé de contrôle ne peut désigner aucune
    # entreprise : autant le dire tout de suite plutôt que de faire attendre l'artisan
    # le temps d'un aller-retour dont on connaît déjà la réponse.
    nu = chiffres(requete)
    if nu == requete.replace(" ", "") and len(nu) in (9, 14) and not luhn_valide(nu):
        logger.info("Numéro rejeté avant appel (clé de contrôle) : %s", requete)
        return []

    parametres = {
        "q": requete,
        "per_page": str(min(limite, 10)),
        # Une entreprise fermée sur un devis, c'est un devis nul. On ne les propose pas.
        "etat_administratif": "A",
    }

    try:
        async with httpx.AsyncClient(timeout=DELAI, headers=ENTETES) as client:
            reponse = await client.get(API_URL, params=parametres)
            reponse.raise_for_status()
            resultats = reponse.json().get("results") or []
    except Exception as err:
        # Le message part dans le journal ; l'artisan, lui, verra simplement que la
        # recherche n'a rien donné et que le formulaire l'attend.
        logger.warning("Recherche d'entreprise indisponible (%s) : %s", requete, err)
        return []

    trouvees = []
    for brut in resultats:
        # Non-diffusible : l'entreprise a demandé que ses données ne soient pas
        # publiées, et l'API renvoie un nom masqué. L'afficher ne servirait qu'à
        # proposer une ligne vide à l'artisan.
        if (brut.get("statut_diffusion") or "O") != "O":
            continue
        mappee = _mapper(brut)
        if mappee and mappee.nom:
            trouvees.append(mappee)

    return trouvees[:limite]


# ---------------------------------------------------------------------------
# Ce que le navigateur renvoie
# ---------------------------------------------------------------------------


class EntrepriseSaisie(BaseModel):
    """L'identité telle qu'elle revient du navigateur, où elle est conservée.

    Tous les champs sont facultatifs, et c'est le point important. Un artisan doit
    pouvoir sortir un devis après avoir choisi son entreprise dans la liste, sans avoir
    encore renseigné son IBAN ni son assurance : ce qui manque retombe sur la
    configuration du serveur au lieu de faire échouer la requête.

    C'est aussi ce qui rend la chose durable. Si `Entreprise` gagne un champ demain, les
    identités déjà enregistrées dans les navigateurs ne deviennent pas invalides — le
    champ nouveau prend simplement sa valeur par défaut. Un 422 en rendez-vous parce
    qu'un artisan a gardé une ancienne version en mémoire serait une panne absurde.
    """

    model_config = ConfigDict(extra="ignore")

    nom: str | None = None
    forme_juridique: str | None = None
    metier: str | None = None
    adresse: str | None = None
    code_postal_ville: str | None = None
    telephone: str | None = None
    email: str | None = None
    siret: str | None = None
    code_ape: str | None = None
    tva_intracom: str | None = None
    assurance: str | None = None
    assurance_police: str | None = None
    iban: str | None = None

    def fusionner(self, defaut: Entreprise) -> Entreprise:
        """Pose les champs renseignés par-dessus ceux du serveur.

        Un champ vide n'est pas une valeur : c'est une absence de saisie. Il laisse donc
        passer la valeur de la configuration, ce qui permet de préremplir un `.env` avant
        un rendez-vous sans que le navigateur ne l'écrase avec du vide.
        """
        valeurs = defaut.model_dump()
        for champ, valeur in self.model_dump().items():
            if isinstance(valeur, str) and valeur.strip():
                valeurs[champ] = valeur.strip()
        return Entreprise(**valeurs)
