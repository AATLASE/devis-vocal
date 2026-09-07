"""La fiche de l'artisan : nom, prénom, entreprise, rôle. Stockage et validation.

Hors du périmètre d'origine, comme `gabarits.py`, et sur la même décision explicite.
Ce module en reprend délibérément la construction — Firestore quand Firebase est
configuré, un dictionnaire en mémoire sinon — pour que les deux se lisent pareil et
se retirent pareil.

Deux propriétés à ne pas perdre de vue :

- **Sans Firebase, rien de tout ceci n'existe.** Le repli en mémoire n'est pas une
  commodité de développement : c'est ce qui fait passer `uv run pytest` sans le
  moindre secret, et ce qui garde la démonstration hors-ligne possible.
- **Ce profil n'entre jamais dans le devis.** Voir la classe `Profil` dans
  `app/models.py` : la raison sociale du devis vient de l'`Entreprise` de la
  configuration, avec son SIRET et son assurance. Mélanger les deux produirait un
  document incohérent.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime

from app.authentification import UID_LOCAL, application
from app.config import get_config
from app.models import Profil

logger = logging.getLogger("devis-vocal")

COLLECTION = "profils"

# Longueur maximale de chaque champ. Ce ne sont pas des contraintes métier mais des
# garde-fous : le formulaire est libre, et rien n'oblige un navigateur à respecter le
# `maxlength` du balisage.
MAX_CHAMP = 120

# Les rôles proposés à l'inscription. La liste est là pour éviter les fautes de frappe
# et accélérer la saisie au pouce, pas pour enfermer : « Autre » ouvre un champ libre,
# et c'est ce texte-là qui est enregistré. Le serveur n'impose donc aucune valeur — il
# vérifie seulement que le champ n'est pas vide.
ROLES = (
    "Gérant · Chef d'entreprise",
    "Artisan",
    "Conducteur de travaux",
    "Chargé d'affaires",
    "Assistant · Secrétariat",
)


class ProfilRefuse(Exception):
    """La fiche est incomplète ou aberrante."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

# L'ordre compte : c'est celui du formulaire, donc celui dans lequel on signale le
# premier champ manquant. Renvoyer « le rôle est vide » alors que le prénom l'est
# aussi ferait remonter l'artisan dans le formulaire pour rien.
CHAMPS = (
    ("prenom", "votre prénom"),
    ("nom", "votre nom"),
    ("entreprise", "le nom de votre entreprise"),
    ("role", "votre rôle dans l'entreprise"),
)


def valider(donnees: dict) -> dict[str, str]:
    """Nettoie les quatre champs. Lève `ProfilRefuse` sur le premier qui manque.

    Les quatre sont obligatoires : c'est une étape d'inscription, elle a été voulue
    bloquante. La coupe à `MAX_CHAMP` est silencieuse — un nom de 400 caractères est
    un copier-coller malheureux, pas une intention, et refuser la fiche entière pour
    ça ferait tout ressaisir.
    """
    propre: dict[str, str] = {}
    for champ, libelle in CHAMPS:
        valeur = str(donnees.get(champ) or "").strip()
        if not valeur:
            raise ProfilRefuse(f"Renseignez {libelle}.")
        propre[champ] = valeur[:MAX_CHAMP]
    return propre


# ---------------------------------------------------------------------------
# Stockage
# ---------------------------------------------------------------------------

# Le repli hors-Firebase, en mémoire et non sur disque : le projet n'a pas de base de
# données et n'en gagne pas une par la porte de derrière. Une fiche posée sans
# Firebase vit le temps du processus, ce qui suffit aux tests.
_memoire: dict[str, Profil] = {}
_verrou = threading.Lock()


def _firestore():
    from firebase_admin import firestore

    return firestore.client(app=application())


def _actif() -> bool:
    """Firestore n'est sollicité que si Firebase est réellement configuré."""
    return get_config().auth_active


def lire(uid: str | None) -> Profil | None:
    """La fiche de cet artisan, ou None s'il n'en a pas encore.

    Comme pour les gabarits : quand Firebase est actif, `UID_LOCAL` désigne quelqu'un
    qui n'est pas connecté, donc qui ne peut rien posséder. On s'épargne l'aller-retour.
    """
    if not uid:
        return None
    if uid == UID_LOCAL and _actif():
        return None

    if not _actif():
        return _memoire.get(uid)

    try:
        document = _firestore().collection(COLLECTION).document(uid).get()
    except Exception as err:  # noqa: BLE001
        # Firestore injoignable ne doit pas bloquer l'entrée dans le produit : on
        # répond « pas de fiche », l'artisan la ressaisira. Le pire serait de le
        # laisser devant un écran qui ne se résout pas.
        logger.warning("  profil lecture Firestore impossible (%s) : %r", uid, err)
        return None
    if not document.exists:
        return None
    try:
        return Profil(**(document.to_dict() or {}))
    except Exception as err:  # noqa: BLE001
        logger.warning("  profil document illisible (%s) : %r", uid, err)
        return None


def enregistrer(uid: str, donnees: dict) -> Profil:
    """Valide puis enregistre la fiche de cet artisan. Lève `ProfilRefuse`."""
    champs = valider(donnees)

    profil = Profil(proprietaire=uid, modifie_le=datetime.now(), **champs)

    if not _actif():
        with _verrou:
            _memoire[uid] = profil
        return profil

    stockage = profil.model_dump()
    stockage["modifie_le"] = profil.modifie_le.isoformat()
    try:
        _firestore().collection(COLLECTION).document(uid).set(stockage)
    except Exception as err:  # noqa: BLE001
        logger.error("  profil écriture Firestore impossible (%s) : %r", uid, err)
        raise ProfilRefuse(
            "Votre fiche n'a pas pu être enregistrée : la base est injoignable. Réessayez."
        ) from err
    return profil


def vider_la_memoire() -> None:
    """Pour les tests, qui ne doivent pas se transmettre de fiche d'un cas à l'autre."""
    with _verrou:
        _memoire.clear()
