"""Le gabarit de devis propre à chaque artisan : stockage, validation, conformité.

Hors du périmètre d'origine (`CLAUDE.md` exclut « base de données » et « comptes /
auth / multi-tenant »), ajouté sur décision explicite. Le module tient trois rôles :

1. **Stocker** le HTML téléversé — Firestore quand Firebase est configuré, un simple
   dictionnaire en mémoire sinon. Le repli n'est pas une commodité : c'est ce qui fait
   passer `uv run pytest` sans le moindre secret, et ce qui garde le démonstrateur
   utilisable hors-ligne.
2. **Valider** avant d'enregistrer, jamais au moment du rendu. Un gabarit qui explose
   pendant la démonstration, devant l'artisan, est exactement ce qu'on ne peut pas se
   permettre : on refuse donc à l'enregistrement ce qui ne rend pas.
3. **Contrôler les mentions obligatoires**. Le gabarit livré garantit la conformité du
   devis ; un gabarit téléversé ne garantit rien, et c'est le risque assumé de cette
   fonctionnalité. Plutôt que de le taire, on rend un devis d'exemple avec le gabarit
   et on vérifie que les valeurs qui doivent figurer sur un devis français y sont. Ce
   qui manque est listé, montré à l'écran, et conservé sur le gabarit.
"""

from __future__ import annotations

import html as html_module
import logging
import re
import threading
from datetime import date, datetime
from decimal import Decimal

from app.authentification import UID_LOCAL, application
from app.config import get_config
from app.models import Client, Devis, Entreprise, Gabarit, LigneDevis, Unite, arrondi
from app.pdf import GabaritError, f_date_fr, f_date_longue, f_montant, render_html

logger = logging.getLogger("devis-vocal")

COLLECTION = "gabarits"

# Les deux blancs que produit le formatage français du devis : l'espace fine insécable
# du séparateur de milliers et l'insécable ordinaire. Écrites en échappement comme
# dans `pdf.py` — à l'œil, dans un fichier source, elles sont indistinguables d'une
# espace normale, et une comparaison de chaînes qui échoue sur ce point est indébogable.
FINE_INSECABLE = "\u202f"
INSECABLE = "\u00a0"


class GabaritRefuse(Exception):
    """Le gabarit ne peut pas être enregistré. Le message part vers le navigateur :
    il doit dire à l'artisan quoi corriger, pas décrire une pile d'appels."""


# ---------------------------------------------------------------------------
# Le devis d'exemple — sert à valider un gabarit et à en montrer l'aperçu
# ---------------------------------------------------------------------------

# Les montants sont volontairement peu ronds. Le contrôle des mentions cherche des
# valeurs dans le document rendu : avec « 1 000,00 », une coïncidence typographique
# suffirait à faire croire qu'un total est imprimé alors qu'il ne l'est pas.
_LIGNES_EXEMPLE = [
    ("Dépose de l'ancienne salle de bain et évacuation", None, "1", Unite.FORFAIT, "687.40", False),
    ("Fourniture et pose d'un receveur extra-plat 120×90", "Bac résine, coloris au choix",
     "1", Unite.U, "1243.90", False),
    ("Faïence murale toute hauteur", "Pose droite, joints époxy", "18.5", Unite.M2, "78.60", True),
    ("Alimentation et évacuation, reprise complète", None, "1", Unite.FORFAIT, "946.30", False),
    ("Pose d'un sèche-serviettes électrique", "Fourniture client", "1", Unite.U, "214.75", True),
]


def devis_exemple(entreprise: Entreprise | None = None) -> Devis:
    """Un devis complet et figé, pour valider un gabarit et en produire l'aperçu.

    Figé au sens strict — dates comprises. Un aperçu qui changerait d'un appel à
    l'autre empêcherait de comparer deux versions d'un gabarit côte à côte.
    """
    entreprise = entreprise or get_config().entreprise

    lignes = [
        LigneDevis(
            designation=designation,
            detail=detail,
            quantite=Decimal(quantite),
            unite=unite,
            prix_unitaire_ht=Decimal(prix),
            total_ht=arrondi(Decimal(quantite) * Decimal(prix)),
            a_valider=estime,
        )
        for designation, detail, quantite, unite, prix, estime in _LIGNES_EXEMPLE
    ]
    # `arrondi()` et pas `quantize()` nu : la règle commerciale française est
    # ROUND_HALF_UP, alors que le défaut de Decimal arrondit au pair le plus proche.
    # Sur ce devis-là, la TVA tombe pile sur la demi-unité — 454,645 — et les deux
    # règles ne donnent pas le même centime. Ce document part en aperçu sous les yeux
    # d'un artisan : il doit s'additionner comme le vrai.
    total_ht = arrondi(sum((ligne.total_ht for ligne in lignes), Decimal("0")))
    montant_tva = arrondi(total_ht * Decimal("0.10"))
    total_ttc = arrondi(total_ht + montant_tva)

    return Devis(
        numero="DEV-EXEMPLE-0001",
        date_emission=date(2026, 3, 12),
        validite_jours=30,
        date_validite=date(2026, 4, 11),
        entreprise=entreprise,
        client=Client(
            nom="Madame Hélène Vasseur",
            adresse="8 allée des Tilleuls, 69300 Caluire-et-Cuire",
            telephone="06 12 34 56 78",
        ),
        type_travaux="Rénovation complète d'une salle de bain",
        duree_estimee="8 jours ouvrés",
        lignes=lignes,
        notes=[
            "Le carrelage est à la charge du client, livré avant le démarrage.",
            "Coupure d'eau d'une demi-journée à prévoir le premier jour.",
        ],
        taux_tva=Decimal("0.10"),
        total_ht=total_ht,
        montant_tva=montant_tva,
        total_ttc=total_ttc,
        acompte_pct=Decimal("0.30"),
        montant_acompte=arrondi(total_ttc * Decimal("0.30")),
        total_ht_estime=arrondi(
            sum((ligne.total_ht for ligne in lignes if ligne.a_valider), Decimal("0"))
        ),
    )


# ---------------------------------------------------------------------------
# Nettoyage
# ---------------------------------------------------------------------------

_SCRIPT = re.compile(r"<script\b[^>]*>.*?</script\s*>", re.IGNORECASE | re.DOTALL)
_SCRIPT_OUVERT = re.compile(r"<script\b[^>]*/?>", re.IGNORECASE)
_ATTRIBUT_EVENEMENT = re.compile(r"""\son[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)


def nettoyer(html: str) -> str:
    """Retire le JavaScript du gabarit.

    Un devis est un document, pas une application : rien de ce qu'un script pourrait
    faire n'a sa place dans un PDF. Et Chromium exécute vraiment ce qu'on lui donne —
    un gabarit avec un `fetch` ferait dépendre le rendu du réseau au moment précis où
    on montre le devis à un artisan, et pourrait poster ailleurs ce qu'il contient.

    Le filtre est un jeu d'expressions régulières, donc perfectible face à quelqu'un
    qui chercherait à le contourner. Ce n'est pas la frontière de sécurité : celle-ci
    est le bac à sable Jinja de `pdf.py`, plus le fait qu'un artisan ne téléverse un
    gabarit que sur son propre compte. C'est une mesure d'hygiène du document.
    """
    html = _SCRIPT.sub("", html)
    html = _SCRIPT_OUVERT.sub("", html)
    return _ATTRIBUT_EVENEMENT.sub("", html)


# ---------------------------------------------------------------------------
# Contrôle des mentions obligatoires
# ---------------------------------------------------------------------------


def _mentions_attendues(devis: Devis) -> list[tuple[str, list[str]]]:
    """Le libellé montré à l'artisan, et les chaînes dont au moins une doit figurer.

    On cherche les **valeurs**, pas des mots-clés : vérifier que le gabarit contient
    le mot « SIRET » ne dit pas qu'il imprime le SIRET.
    """
    e = devis.entreprise
    return [
        ("Le numéro du devis", [devis.numero]),
        ("La date d'émission", [f_date_fr(devis.date_emission), f_date_longue(devis.date_emission)]),
        ("La date de fin de validité",
         [f_date_fr(devis.date_validite), f_date_longue(devis.date_validite)]),
        ("Le nom de l'entreprise", [e.nom]),
        ("L'adresse de l'entreprise", [e.adresse, e.code_postal_ville]),
        ("Le numéro SIRET", [e.siret]),
        ("Le numéro de TVA intracommunautaire", [e.tva_intracom]),
        ("L'assurance professionnelle", [e.assurance, e.assurance_police]),
        ("Le nom du client", [devis.client.nom or ""]),
        ("Le détail des prestations", [devis.lignes[0].designation]),
        ("Le total HT", [f_montant(devis.total_ht)]),
        ("Le montant de la TVA", [f_montant(devis.montant_tva)]),
        ("Le total TTC", [f_montant(devis.total_ttc)]),
        ("La mention « bon pour accord »", ["bon pour accord"]),
    ]


_BALISE = re.compile(r"<[^>]+>")
_BLANCS = re.compile(r"\s+")


def _normaliser(texte: str) -> str:
    """Blancs unifiés, casse abaissée.

    Les insécables du formatage français deviennent des espaces simples : sans ça,
    « 4 251,30 » cherché dans un document qui l'affiche correctement — avec une fine
    insécable entre les milliers — ne se trouverait jamais.
    """
    texte = texte.replace(FINE_INSECABLE, " ").replace(INSECABLE, " ")
    return _BLANCS.sub(" ", texte).strip().lower()


def _texte_rendu(html: str) -> str:
    """Le document réduit à son texte."""
    return _normaliser(html_module.unescape(_BALISE.sub(" ", html)))


def controler_les_mentions(html_rendu: str, devis: Devis) -> list[str]:
    """Les mentions obligatoires absentes du document rendu."""
    texte = _texte_rendu(html_rendu)

    def presente(valeurs: list[str]) -> bool:
        for valeur in valeurs:
            attendu = _normaliser(valeur) if valeur else ""
            if attendu and attendu in texte:
                return True
        return False

    return [libelle for libelle, valeurs in _mentions_attendues(devis) if not presente(valeurs)]


# ---------------------------------------------------------------------------
# Validation complète — ce qui décide si un gabarit peut être enregistré
# ---------------------------------------------------------------------------


def valider(html: str) -> tuple[str, str, list[str]]:
    """Valide un gabarit. Renvoie `(html nettoyé, html rendu, mentions manquantes)`.

    Lève `GabaritRefuse` sur ce qui ne peut pas être enregistré : gabarit vide, trop
    gros, syntaxe Jinja fautive, ou explosion au rendu. Les mentions manquantes, elles,
    ne bloquent pas — elles sont signalées.
    """
    config = get_config()

    if not html.strip():
        raise GabaritRefuse("Le fichier est vide.")

    if len(html.encode("utf-8")) > config.max_gabarit_octets:
        raise GabaritRefuse(
            f"Le fichier dépasse {config.max_gabarit_ko} Ko. Les images doivent être "
            "des URI `data:` compactes, ou rester hors du gabarit."
        )

    propre = nettoyer(html)
    exemple = devis_exemple()

    # Le rendu d'essai a deux fonctions : refuser tout de suite un gabarit cassé, et
    # produire le texte sur lequel on contrôle les mentions.
    try:
        rendu = render_html(exemple, gabarit=propre)
    except GabaritError as err:
        raise GabaritRefuse(f"Le gabarit n'a pas pu être rendu. {err}") from err
    except Exception as err:  # noqa: BLE001 — un gabarit tiers échoue de mille façons
        logger.warning("  gabarit rendu d'essai échoué : %r", err)
        raise GabaritRefuse(
            f"Le gabarit n'a pas pu être rendu. {type(err).__name__} : {err}"
        ) from err

    return propre, rendu, controler_les_mentions(rendu, exemple)


# ---------------------------------------------------------------------------
# Stockage
# ---------------------------------------------------------------------------

# Le repli hors-Firebase. Volontairement en mémoire et non sur disque : le projet n'a
# pas de base de données, et n'en gagne pas une par la porte de derrière. Un gabarit
# posé sans Firebase vit le temps du processus, ce qui suffit pour travailler la mise
# en page et pour les tests.
_memoire: dict[str, Gabarit] = {}
_verrou = threading.Lock()


def _firestore():
    from firebase_admin import firestore

    return firestore.client(app=application())


def _actif() -> bool:
    """Firestore n'est sollicité que si Firebase est réellement configuré."""
    return get_config().auth_active


def lire(uid: str) -> Gabarit | None:
    """Le gabarit de cet artisan, ou None s'il utilise celui livré."""
    if not _actif():
        return _memoire.get(uid)
    try:
        document = _firestore().collection(COLLECTION).document(uid).get()
    except Exception as err:  # noqa: BLE001
        # Firestore injoignable ne doit pas empêcher de sortir un devis : on retombe
        # sur le gabarit livré, qui est de toute façon celui qu'on sait conforme.
        logger.warning("  gabarit lecture Firestore impossible (%s) : %r", uid, err)
        return None
    if not document.exists:
        return None
    try:
        return Gabarit(**(document.to_dict() or {}))
    except Exception as err:  # noqa: BLE001
        logger.warning("  gabarit document illisible (%s) : %r", uid, err)
        return None


def enregistrer(uid: str, nom: str, html: str) -> Gabarit:
    """Valide puis enregistre le gabarit de cet artisan. Lève `GabaritRefuse`."""
    propre, _, manquantes = valider(html)

    gabarit = Gabarit(
        proprietaire=uid,
        nom=(nom or "gabarit.html").strip()[:120],
        html=propre,
        modifie_le=datetime.now(),
        mentions_manquantes=manquantes,
    )

    if not _actif():
        with _verrou:
            _memoire[uid] = gabarit
        return gabarit

    donnees = gabarit.model_dump()
    donnees["modifie_le"] = gabarit.modifie_le.isoformat()
    try:
        _firestore().collection(COLLECTION).document(uid).set(donnees)
    except Exception as err:  # noqa: BLE001
        logger.error("  gabarit écriture Firestore impossible (%s) : %r", uid, err)
        raise GabaritRefuse(
            "Le gabarit n'a pas pu être enregistré : la base est injoignable. Réessayez."
        ) from err
    return gabarit


def supprimer(uid: str) -> None:
    """Revient au gabarit livré. Sans effet si l'artisan n'en avait pas posé."""
    if not _actif():
        with _verrou:
            _memoire.pop(uid, None)
        return
    try:
        _firestore().collection(COLLECTION).document(uid).delete()
    except Exception as err:  # noqa: BLE001
        logger.error("  gabarit suppression Firestore impossible (%s) : %r", uid, err)
        raise GabaritRefuse(
            "Le gabarit n'a pas pu être supprimé : la base est injoignable. Réessayez."
        ) from err


def html_pour(uid: str | None) -> str | None:
    """Le HTML à passer à `pdf.render()` pour cet artisan, ou None pour celui livré.

    Appelée à chaque PDF, y compris pour les visiteurs non connectés — ce sont eux la
    majorité des rendus en démonstration. D'où le court-circuit : quand Firebase est
    actif, `UID_LOCAL` désigne quelqu'un qui n'est pas connecté, donc qui ne peut rien
    posséder. Interroger Firestore pour un document qui n'existera jamais coûterait un
    aller-retour réseau sur le chemin le plus chaud du produit.
    """
    if not uid:
        return None
    if uid == UID_LOCAL and _actif():
        return None
    gabarit = lire(uid)
    return gabarit.html if gabarit else None


def vider_la_memoire() -> None:
    """Pour les tests, qui ne doivent pas se transmettre de gabarit d'un cas à l'autre."""
    with _verrou:
        _memoire.clear()
