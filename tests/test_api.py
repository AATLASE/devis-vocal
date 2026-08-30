"""Ce que l'API annonce d'elle-même.

Un seul endpoint est testé ici, et c'est le plus petit : `/health`. Il ne calcule
rien, mais c'est lui qui dit au navigateur si le chiffrage affiché a été calculé ou
rejoué, et par quel moteur. Le front s'en sert pour poser une bande d'avertissement
en tête de page. Si ces deux champs disparaissent ou changent de nom, la bande ne
s'affiche plus — silencieusement — et on peut montrer une fixture à un artisan en
croyant qu'elle sort du modèle.

`health()` est appelée directement, sans TestClient : passer par l'application ferait
tourner son `lifespan`, donc démarrer Chromium, pour vérifier trois chaînes.
"""

from __future__ import annotations

import pytest

from app.config import get_config
from app.main import health


@pytest.fixture(autouse=True)
def config_neuve():
    """`get_config` est mise en cache pour la durée du processus : sans ce nettoyage,
    le premier test figerait la configuration pour tous les suivants."""
    get_config.cache_clear()
    yield
    get_config.cache_clear()


async def test_health_annonce_le_mode_et_le_moteur(monkeypatch):
    monkeypatch.setenv("USE_FIXTURES", "true")
    monkeypatch.setenv("STRUCTURATION_PROVIDER", "groq")

    assert await health() == {"status": "ok", "mode": "fixtures", "provider": "groq"}


async def test_health_distingue_le_mode_reel(monkeypatch):
    monkeypatch.setenv("USE_FIXTURES", "false")
    monkeypatch.setenv("STRUCTURATION_PROVIDER", "anthropic")

    info = await health()
    assert info["mode"] == "reel"
    assert info["provider"] == "anthropic"
