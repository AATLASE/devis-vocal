"""Authentification Firebase — vérification des jetons côté serveur.

Hors du périmètre d'origine du démonstrateur : `CLAUDE.md` classe « comptes / auth /
multi-tenant » dans ce qu'il ne faut pas construire. Ajouté sur décision explicite.
Ce module est écrit pour que cette décision reste réversible : rien d'autre dans le
projet n'importe `firebase_admin`, et tout passe par les deux dépendances du bas.

**Sans identifiants Firebase, l'authentification est inactive** et l'application se
comporte exactement comme avant. Ce n'est pas une facilité de développement, c'est ce
qui garde deux propriétés que le projet s'est données : `uv run pytest` passe sans
aucun secret, et la démonstration hors-ligne tourne dans un sous-sol sans réseau.

Le jeton voyage dans l'en-tête `Authorization: Bearer <idToken>`. Il est vérifié à
chaque requête — signature, expiration, audience — par le SDK admin, jamais décodé à
la main : un jeton JWT dont on ne vérifie que le contenu n'authentifie personne.
"""

from __future__ import annotations

import json
import logging
import threading

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel

from app.config import get_config

logger = logging.getLogger("devis-vocal")


class Utilisateur(BaseModel):
    """L'identité telle qu'elle sort du jeton. Rien de plus que ce qu'on utilise."""

    uid: str
    email: str | None = None
    nom: str | None = None

    @property
    def local(self) -> bool:
        """Utilisateur de repli, quand l'authentification est inactive."""
        return self.uid == UID_LOCAL


# Quand l'authentification est inactive, tout le monde est ce même utilisateur. Le
# nom est explicite à dessein : s'il apparaît dans un log de production, c'est que
# Firebase n'est pas configuré là où on le croyait.
UID_LOCAL = "local"
UTILISATEUR_LOCAL = Utilisateur(uid=UID_LOCAL, email=None, nom="Poste local")


class AuthError(Exception):
    """Le jeton est absent, expiré ou invalide."""


# ---------------------------------------------------------------------------
# Initialisation du SDK admin
# ---------------------------------------------------------------------------

# `firebase_admin.initialize_app()` explose si on l'appelle deux fois, et uvicorn peut
# très bien traiter deux requêtes de front au premier démarrage. Un verrou coûte moins
# cher qu'un 500 à la première connexion.
_verrou = threading.Lock()
_application = None


def _credentials():
    """Le compte de service, depuis la variable d'environnement ou le fichier."""
    from firebase_admin import credentials

    config = get_config()
    if config.firebase_credentials_json:
        try:
            donnees = json.loads(config.firebase_credentials_json)
        except json.JSONDecodeError as err:
            raise AuthError(
                "FIREBASE_CREDENTIALS_JSON n'est pas un JSON valide. Colle le contenu "
                "entier du fichier de compte de service, guillemets compris."
            ) from err
        return credentials.Certificate(donnees)

    chemin = config.firebase_credentials
    if chemin and chemin.exists():
        return credentials.Certificate(str(chemin))

    raise AuthError(
        f"Le fichier de compte de service Firebase est introuvable : {chemin}. "
        "Renseigne FIREBASE_CREDENTIALS ou FIREBASE_CREDENTIALS_JSON."
    )


def application():
    """L'app Firebase admin, initialisée une seule fois."""
    global _application
    if _application is not None:
        return _application
    with _verrou:
        if _application is None:
            import firebase_admin

            # Un rechargement à chaud réimporte ce module mais pas le SDK, qui garde
            # son app enregistrée : on la récupère plutôt que d'échouer.
            try:
                _application = firebase_admin.get_app()
            except ValueError:
                _application = firebase_admin.initialize_app(_credentials())
    return _application


def reinitialiser() -> None:
    """Oublie l'app mémorisée. Pour les tests, qui changent la configuration."""
    global _application
    _application = None


# ---------------------------------------------------------------------------
# Vérification
# ---------------------------------------------------------------------------


# Le SDK refuse par défaut un jeton dont l'heure d'émission est dans le futur, à la
# seconde près. Or un jeton sort tout juste des serveurs de Google au moment de la
# connexion : il suffit que le poste retarde de quelques secondes pour que la toute
# première requête soit refusée (« Token used too early ») — et c'est celle qui lit la
# fiche. Observé sur un portable dont la synchronisation Windows était arrêtée. Soixante
# secondes est le maximum que le SDK accepte ; la contrepartie, un jeton expiré toléré
# une minute de plus, ne pèse rien.
TOLERANCE_HORLOGE_S = 60


def verifier_jeton(jeton: str) -> Utilisateur:
    """Vérifie un ID token Firebase et en tire l'utilisateur.

    Lève `AuthError` sur tout ce qui n'est pas un jeton valide et vivant.
    """
    from firebase_admin import auth as firebase_auth

    try:
        revendications = firebase_auth.verify_id_token(
            jeton, app=application(), clock_skew_seconds=TOLERANCE_HORLOGE_S
        )
    except firebase_auth.ExpiredIdTokenError as err:
        raise AuthError("Votre session a expiré. Reconnectez-vous.") from err
    except firebase_auth.RevokedIdTokenError as err:
        raise AuthError("Votre session a été révoquée. Reconnectez-vous.") from err
    except Exception as err:
        # On ne recopie pas le message du SDK vers le navigateur : il est en anglais et
        # décrit la cryptographie du jeton. Il a en revanche sa place dans le log.
        logger.warning("  auth   jeton refusé : %s", err)
        raise AuthError("Session invalide. Reconnectez-vous.") from err

    return Utilisateur(
        uid=revendications["uid"],
        email=revendications.get("email"),
        nom=revendications.get("name") or revendications.get("email"),
    )


def _jeton_de_l_entete(autorisation: str | None) -> str | None:
    if not autorisation:
        return None
    schema, _, valeur = autorisation.partition(" ")
    if schema.lower() != "bearer" or not valeur.strip():
        return None
    return valeur.strip()


# ---------------------------------------------------------------------------
# Les deux dépendances FastAPI — le seul point d'entrée du reste de l'application
# ---------------------------------------------------------------------------


async def utilisateur_optionnel(authorization: str | None = Header(default=None)) -> Utilisateur:
    """L'utilisateur s'il est connecté, l'utilisateur local sinon. Ne refuse jamais.

    C'est ce que consomment les endroits où l'authentification ne doit rien changer au
    parcours — dicter un devis reste possible sans compte, sinon on aurait transformé
    le démonstrateur en produit à inscription obligatoire.
    """
    if not get_config().auth_active:
        return UTILISATEUR_LOCAL
    jeton = _jeton_de_l_entete(authorization)
    if not jeton:
        return UTILISATEUR_LOCAL
    try:
        return verifier_jeton(jeton)
    except AuthError:
        # Un jeton expiré ne doit pas faire échouer un devis en cours de dictée : on
        # retombe sur le gabarit par défaut, ce qui est exactement l'état d'avant.
        return UTILISATEUR_LOCAL


async def utilisateur_requis(authorization: str | None = Header(default=None)) -> Utilisateur:
    """L'utilisateur connecté, ou 401. Garde les endpoints du gabarit."""
    if not get_config().auth_active:
        return UTILISATEUR_LOCAL
    jeton = _jeton_de_l_entete(authorization)
    if not jeton:
        raise HTTPException(status_code=401, detail="Connectez-vous pour accéder à cette page.")
    try:
        return verifier_jeton(jeton)
    except AuthError as err:
        raise HTTPException(status_code=401, detail=str(err)) from err


# Alias lisibles côté endpoints : `utilisateur: Connecte` se lit mieux qu'un Depends nu.
Connecte = Depends(utilisateur_requis)
Visiteur = Depends(utilisateur_optionnel)
