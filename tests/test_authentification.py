"""L'authentification Firebase, et surtout ce qu'elle ne doit pas casser.

Deux propriétés que le projet s'était données avant l'ajout des comptes, et qui
comptent plus que les comptes eux-mêmes :

- `uv run pytest` passe sans aucun secret ;
- le démonstrateur tourne hors-ligne, dans un sous-sol sans réseau.

L'authentification est donc **inactive tant que le serveur ne peut pas vérifier un
jeton**, et c'est la moitié de ce fichier. L'autre moitié vérifie qu'une fois active,
elle refuse vraiment : une porte qui laisse tout passer est pire qu'une absence de
porte, puisqu'on la croit fermée.

Rien ici n'appelle Firebase. `verifier_jeton` est la couture, et on la remplace.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import authentification
from app.authentification import (
    UID_LOCAL,
    AuthError,
    Utilisateur,
    utilisateur_optionnel,
    utilisateur_requis,
)
from app.config import get_config


@pytest.fixture(autouse=True)
def config_neuve():
    get_config.cache_clear()
    authentification.reinitialiser()
    yield
    get_config.cache_clear()
    authentification.reinitialiser()


@pytest.fixture
def firebase_configure(monkeypatch):
    """Fait croire à la config qu'un compte de service est en place, sans en avoir un.
    C'est `auth_active` qu'on veut allumer, pas le SDK."""
    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", '{"type": "service_account"}')
    get_config.cache_clear()


ARTISAN = Utilisateur(uid="uid-artisan", email="jean@example.com", nom="Jean")


# ---------------------------------------------------------------------------
# Sans Firebase : rien ne change
# ---------------------------------------------------------------------------


def test_l_authentification_est_inactive_sans_identifiants():
    """La garantie de base. Si elle tombe, les tests et la démo hors-ligne tombent."""
    assert get_config().auth_active is False


def test_la_config_publique_seule_n_active_rien(monkeypatch):
    """Une clé publique ne permet pas de vérifier un jeton. Servir un écran de
    connexion sur cette seule base donnerait une porte peinte sur un mur — on
    croirait l'API gardée alors qu'elle ne l'est pas."""
    monkeypatch.setenv("FIREBASE_API_KEY", "AIza-quelque-chose")
    monkeypatch.setenv("FIREBASE_PROJECT_ID", "devis-vocal")
    get_config.cache_clear()

    assert get_config().auth_active is False


async def test_sans_firebase_l_endpoint_garde_laisse_passer():
    """Poser un gabarit reste possible sur un poste local sans compte."""
    assert (await utilisateur_requis(authorization=None)).uid == UID_LOCAL


async def test_sans_firebase_le_devis_reste_anonyme():
    assert (await utilisateur_optionnel(authorization=None)).uid == UID_LOCAL


# ---------------------------------------------------------------------------
# Avec Firebase : la porte est fermée
# ---------------------------------------------------------------------------


async def test_sans_jeton_l_endpoint_garde_refuse(firebase_configure):
    with pytest.raises(HTTPException) as refus:
        await utilisateur_requis(authorization=None)

    assert refus.value.status_code == 401


@pytest.mark.parametrize("entete", [
    "",
    "abc",                    # pas de schéma
    "Basic abc",              # mauvais schéma
    "Bearer",                 # schéma sans jeton
    "Bearer    ",             # jeton vide
])
async def test_un_entete_mal_forme_ne_passe_pas(firebase_configure, entete):
    with pytest.raises(HTTPException) as refus:
        await utilisateur_requis(authorization=entete)

    assert refus.value.status_code == 401


async def test_un_jeton_invalide_est_refuse(firebase_configure, monkeypatch):
    def refuser(_jeton):
        raise AuthError("Session invalide. Reconnectez-vous.")

    monkeypatch.setattr(authentification, "verifier_jeton", refuser)

    with pytest.raises(HTTPException) as refus:
        await utilisateur_requis(authorization="Bearer jeton-bidon")

    assert refus.value.status_code == 401
    assert "Reconnectez-vous" in refus.value.detail


async def test_un_jeton_valide_donne_l_utilisateur(firebase_configure, monkeypatch):
    monkeypatch.setattr(authentification, "verifier_jeton", lambda _: ARTISAN)

    assert await utilisateur_requis(authorization="Bearer bon-jeton") == ARTISAN


async def test_le_schema_bearer_est_insensible_a_la_casse(firebase_configure, monkeypatch):
    """Les clients écrivent « bearer », « Bearer », parfois « BEARER »."""
    monkeypatch.setattr(authentification, "verifier_jeton", lambda _: ARTISAN)

    assert await utilisateur_requis(authorization="bearer bon-jeton") == ARTISAN


# ---------------------------------------------------------------------------
# Le pipeline du devis ne doit jamais dépendre du compte
# ---------------------------------------------------------------------------


async def test_un_jeton_expire_ne_casse_pas_le_devis(firebase_configure, monkeypatch):
    """Le point le plus important du fichier. La structuration prend quarante
    secondes : un jeton peut expirer entre la dictée et le PDF. Faire échouer le
    devis à ce moment-là ferait perdre à l'artisan sa dictée et la démonstration.
    On retombe sur l'anonyme, donc sur le gabarit livré — l'état d'avant."""
    def expire(_jeton):
        raise AuthError("Votre session a expiré. Reconnectez-vous.")

    monkeypatch.setattr(authentification, "verifier_jeton", expire)

    assert (await utilisateur_optionnel(authorization="Bearer perime")).uid == UID_LOCAL


async def test_sans_jeton_le_devis_passe_quand_meme(firebase_configure):
    """Un artisan non connecté dicte et sort son devis, exactement comme avant."""
    assert (await utilisateur_optionnel(authorization=None)).uid == UID_LOCAL


async def test_un_jeton_valide_identifie_le_devis(firebase_configure, monkeypatch):
    """C'est ce qui permet de retrouver son gabarit au moment du PDF."""
    monkeypatch.setattr(authentification, "verifier_jeton", lambda _: ARTISAN)

    assert (await utilisateur_optionnel(authorization="Bearer bon-jeton")).uid == "uid-artisan"


# ---------------------------------------------------------------------------
# Le compte de service
# ---------------------------------------------------------------------------


def test_un_json_de_service_illisible_est_signale(monkeypatch):
    """Le cas de déploiement le plus banal : la variable est collée de travers dans
    Coolify. Le message doit dire quoi faire, pas dérouler une pile JSON."""
    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", "{ceci n'est pas du json")
    get_config.cache_clear()

    with pytest.raises(AuthError, match="FIREBASE_CREDENTIALS_JSON"):
        authentification._credentials()


def test_un_fichier_de_service_absent_est_signale(monkeypatch, tmp_path):
    monkeypatch.setenv("FIREBASE_CREDENTIALS", str(tmp_path / "nulle-part.json"))
    get_config.cache_clear()

    with pytest.raises(AuthError, match="introuvable"):
        authentification._credentials()
