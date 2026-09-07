"""Isolation commune à tous les tests.

Les compteurs de dépense sont désormais écrits sur disque — c'est ce qui leur permet
de survivre à un redémarrage. Sans le détournement ci-dessous, la suite écrirait dans
le dépôt et, pire, les tests se contamineraient entre eux : le plafond atteint par
l'un ferait échouer le suivant, avec un message qui n'aurait aucun rapport.
"""

from __future__ import annotations

import pytest

from app import securite
from app.config import get_config


@pytest.fixture(autouse=True)
def compteurs_isoles(tmp_path, monkeypatch):
    monkeypatch.setenv("COMPTEURS_FICHIER", str(tmp_path / "compteurs.json"))
    get_config.cache_clear()
    securite.reinitialiser()
    yield
    get_config.cache_clear()
    securite.reinitialiser()
