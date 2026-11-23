"""Le tarif de l'artisan : ses prestations, ses unités, ses prix HT. Stockage et validation.

Hors du périmètre d'origine, sur décision explicite (CLAUDE.md, « Exception assumée :
tarif de l'artisan »). Construit exactement comme `profils.py` et `templates.py` —
Firestore quand Firebase est configuré, un dictionnaire en mémoire sinon — pour que les
trois se lisent pareil et se retirent pareil.

Ce que ce module garantit :

- **Sans Firebase, rien de tout ceci n'existe.** `pour()` renvoie None, le chiffrage
  tourne sur les fourchettes de référence, exactement comme avant.
- **Un tarif enregistré est propre.** Unités connues, prix bornés et arrondis au
  centime, identifiants courts et uniques, pas deux prix pour la même prestation. Le
  chiffrage peut alors s'y fier sans revérifier.
- **Rien n'y entre sans l'artisan.** Ce module n'est appelé que par `PUT /api/grille`,
  c'est-à-dire quand l'artisan a validé son tableau. Les imports (dictée, fichier) ne
  font que proposer des lignes.
"""

from __future__ import annotations

import logging
import re
import threading
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation

from app.authentification import UID_LOCAL, application
from app.config import get_config
from app.models import GrillePrix, PrixArtisan, Unite, arrondi

logger = logging.getLogger("devis-vocal")

COLLECTION = "grilles"

# Un tarif d'artisan tient en quelques dizaines de lignes ; 300 laisse de la marge sans
# faire exploser le prompt (chaque ligne y coûte une trentaine de tokens) ni le document
# Firestore (plafonné à 1 Mo).
MAX_LIGNES = 300
PRESTATION_MAX = 200
PRECISION_MAX = 200
PRIX_MAX = Decimal("100000")

# « P12 » : court parce que le modèle doit le recopier, et un identifiant long se recopie
# mal. Quatre chiffres au plus suffisent largement à MAX_LIGNES.
_ID = re.compile(r"^P\d{1,4}$")

# Ce que l'artisan peut taper pour une unité, au-delà des valeurs exactes de l'enum.
_ALIAS_UNITES = {"m2": Unite.M2, "m3": Unite.M3, "unite": Unite.U, "unité": Unite.U,
                 "jour": Unite.J, "heure": Unite.H}


class GrilleRefusee(Exception):
    """Le tarif soumis est incomplet ou aberrant, ou la base est injoignable."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _texte(valeur, maximum: int) -> str:
    """Une ligne de texte sans retour à la ligne ni barre verticale.

    La barre verticale sépare les colonnes du bloc envoyé au modèle (voir
    `structuration._bloc_grille`) : en laisser passer une décalerait les colonnes.
    """
    propre = re.sub(r"[\r\n\t|]+", " ", str(valeur or ""))
    return re.sub(r"\s+", " ", propre).strip()[:maximum]


def _unite(valeur) -> Unite | None:
    brut = str(valeur or "").strip()
    try:
        return Unite(brut)
    except ValueError:
        return _ALIAS_UNITES.get(brut.casefold())


def _prix(valeur) -> Decimal | None:
    """Le prix tel que l'artisan l'a saisi : « 42,50 », « 42.5 », « 1 200 », 42.5."""
    brut = re.sub(r"[\s  €]", "", str(valeur if valeur is not None else ""))
    brut = brut.replace(",", ".")
    try:
        prix = Decimal(brut)
    except InvalidOperation:
        return None
    if not prix.is_finite():
        return None
    return arrondi(prix)


def cle(prestation: str, precision: str | None, unite: Unite) -> tuple[str, str, str]:
    """Ce qui fait qu'une ligne est la même qu'une autre : sans accents, casse ni espaces."""

    def norme(texte: str | None) -> str:
        sans_accents = unicodedata.normalize("NFKD", texte or "")
        sans_accents = "".join(c for c in sans_accents if not unicodedata.combining(c))
        return re.sub(r"\s+", " ", sans_accents).strip().casefold()

    return norme(prestation), norme(precision), unite.value


def _euro(prix: Decimal) -> str:
    return f"{prix:.2f}".replace(".", ",") + " €"


def valider(lignes: list[dict]) -> list[PrixArtisan]:
    """Nettoie le tarif soumis. Lève `GrilleRefusee` sur la première ligne fautive.

    Les messages nomment la ligne (« Ligne 12 : … ») : l'artisan corrige dans un tableau,
    il doit savoir où regarder.
    """
    if not isinstance(lignes, list):
        raise GrilleRefusee("Le tarif doit être une liste de lignes.")
    if len(lignes) > MAX_LIGNES:
        raise GrilleRefusee(
            f"Un tarif ne peut pas dépasser {MAX_LIGNES} lignes ({len(lignes)} reçues)."
        )

    propres: list[PrixArtisan] = []
    deja: dict[tuple[str, str, str], PrixArtisan] = {}
    for rang, donnees in enumerate(lignes, start=1):
        if not isinstance(donnees, dict):
            raise GrilleRefusee(f"Ligne {rang} : ligne illisible.")
        prestation = _texte(donnees.get("prestation"), PRESTATION_MAX)
        if not prestation:
            raise GrilleRefusee(f"Ligne {rang} : indiquez la prestation.")
        unite = _unite(donnees.get("unite"))
        if unite is None:
            raise GrilleRefusee(f"Ligne {rang} (« {prestation} ») : choisissez une unité.")
        prix = _prix(donnees.get("prix_unitaire_ht"))
        if prix is None or prix <= 0:
            raise GrilleRefusee(f"Ligne {rang} (« {prestation} ») : indiquez un prix HT.")
        if prix > PRIX_MAX:
            raise GrilleRefusee(
                f"Ligne {rang} (« {prestation} ») : un prix unitaire ne peut pas "
                f"dépasser {_euro(PRIX_MAX)}."
            )
        precision = _texte(donnees.get("precision"), PRECISION_MAX) or None

        ligne = PrixArtisan(id=str(donnees.get("id") or "").strip(), prestation=prestation,
                            unite=unite, prix_unitaire_ht=prix, precision=precision)
        k = cle(prestation, precision, unite)
        if k in deja:
            if deja[k].prix_unitaire_ht == prix:
                continue  # le même prix saisi deux fois : on garde le premier, sans bruit
            raise GrilleRefusee(
                f"« {prestation} » ({unite.value}) apparaît deux fois avec des prix "
                f"différents : {_euro(deja[k].prix_unitaire_ht)} et {_euro(prix)}. "
                "Gardez-en un, ou précisez ce qui les distingue."
            )
        deja[k] = ligne
        propres.append(ligne)

    return _numeroter(propres)


def _numeroter(lignes: list[PrixArtisan]) -> list[PrixArtisan]:
    """Garde les identifiants valides et uniques, en donne un neuf aux autres.

    Garder ceux qui existent compte : une ligne du tarif qui change de numéro à chaque
    enregistrement rendrait illisibles les références des devis en cours de relecture.
    """
    pris: set[str] = set()
    a_numeroter: list[int] = []
    for i, ligne in enumerate(lignes):
        if _ID.match(ligne.id) and ligne.id not in pris:
            pris.add(ligne.id)
        else:
            a_numeroter.append(i)

    suivant = 1
    for i in a_numeroter:
        while f"P{suivant}" in pris:
            suivant += 1
        lignes[i] = lignes[i].model_copy(update={"id": f"P{suivant}"})
        pris.add(f"P{suivant}")
    return lignes


# ---------------------------------------------------------------------------
# Stockage
# ---------------------------------------------------------------------------

# Le repli hors-Firebase, en mémoire : il ne sert qu'aux tests. Sans Firebase, les routes
# répondent 404 et `pour()` renvoie None, donc personne d'autre n'y touche.
_memoire: dict[str, GrillePrix] = {}
_verrou = threading.Lock()


def _firestore():
    from firebase_admin import firestore

    return firestore.client(app=application())


def _actif() -> bool:
    """Firestore n'est sollicité que si Firebase est réellement configuré."""
    return get_config().auth_active


def lire(uid: str | None) -> GrillePrix | None:
    """Le tarif de cet artisan, ou None s'il n'en a pas (ou si la base ne répond pas)."""
    if not uid:
        return None
    if uid == UID_LOCAL and _actif():
        return None

    if not _actif():
        return _memoire.get(uid)

    try:
        document = _firestore().collection(COLLECTION).document(uid).get()
    except Exception as err:  # noqa: BLE001
        # Firestore injoignable : on chiffre sans tarif. Les lignes sortent alors
        # « estimé », ce qui se voit et ne trompe personne — l'inverse d'une panne.
        logger.warning("  tarif lecture Firestore impossible (%s) : %r", uid, err)
        return None
    if not document.exists:
        return None
    try:
        return GrillePrix(**(document.to_dict() or {}))
    except Exception as err:  # noqa: BLE001
        logger.warning("  tarif document illisible (%s) : %r", uid, err)
        return None


def pour(uid: str | None) -> GrillePrix | None:
    """Le tarif à utiliser pour chiffrer, ou None.

    Appelé à chaque devis. Comme `templates.html_pour` : un visiteur non connecté, ou un
    poste sans Firebase, chiffre exactement comme avant — sur les fourchettes de référence.
    """
    if not uid or uid == UID_LOCAL or not get_config().auth_active:
        return None
    grille = lire(uid)
    return grille if grille and grille.lignes else None


def enregistrer(uid: str, lignes: list[dict]) -> GrillePrix | None:
    """Valide puis enregistre le tarif. Une liste vide le supprime. Lève `GrilleRefusee`."""
    propres = valider(lignes)
    if not propres:
        supprimer(uid)
        return None

    grille = GrillePrix(proprietaire=uid, lignes=propres, modifie_le=datetime.now())

    if not _actif():
        with _verrou:
            _memoire[uid] = grille
        return grille

    try:
        # `mode="json"` : Firestore ne sait pas ranger un Decimal ; il devient une
        # chaîne, que `GrillePrix(**...)` relit sans perte à la lecture.
        _firestore().collection(COLLECTION).document(uid).set(grille.model_dump(mode="json"))
    except Exception as err:  # noqa: BLE001
        logger.error("  tarif écriture Firestore impossible (%s) : %r", uid, err)
        raise GrilleRefusee(
            "Votre tarif n'a pas pu être enregistré : la base est injoignable. Réessayez."
        ) from err
    return grille


def supprimer(uid: str) -> None:
    """Retire le tarif de cet artisan. Lève `GrilleRefusee` si la base ne répond pas."""
    if not _actif():
        with _verrou:
            _memoire.pop(uid, None)
        return
    try:
        _firestore().collection(COLLECTION).document(uid).delete()
    except Exception as err:  # noqa: BLE001
        logger.error("  tarif suppression Firestore impossible (%s) : %r", uid, err)
        raise GrilleRefusee(
            "Votre tarif n'a pas pu être supprimé : la base est injoignable. Réessayez."
        ) from err


def vider_la_memoire() -> None:
    """Pour les tests, qui ne doivent pas se transmettre de tarif d'un cas à l'autre."""
    with _verrou:
        _memoire.clear()
