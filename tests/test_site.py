"""La vitrine : des pages statiques, et l'application déplacée sur `/app`.

Rien d'arithmétique ici. On vérifie seulement que chaque adresse donnée à un
artisan répond, que chaque page mène à l'application, et que les fichiers
qu'elles appellent existent : une photo manquante passe inaperçue en local
et saute aux yeux en rendez-vous.
"""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import PAGES_SITE, app

STATIQUE = Path(__file__).parent.parent / "app" / "static"


@pytest.fixture
def client():
    return TestClient(app)


@pytest.mark.parametrize("chemin", list(PAGES_SITE))
def test_chaque_page_de_la_vitrine_repond_et_mene_a_l_application(client, chemin):
    reponse = client.get(chemin)
    assert reponse.status_code == 200
    assert reponse.headers["content-type"].startswith("text/html")
    assert 'href="/app' in reponse.text


def test_l_application_est_servie_sur_app(client):
    reponse = client.get("/app")
    assert reponse.status_code == 200
    assert 'id="ecran-connexion"' in reponse.text


@pytest.mark.parametrize("chemin", list(PAGES_SITE))
def test_les_fichiers_appeles_par_la_vitrine_existent(client, chemin):
    page = client.get(chemin).text
    for ressource in re.findall(r'(?:src|href)="(/static/[^"]+)"', page):
        assert (STATIQUE / ressource.removeprefix("/static/")).is_file(), ressource


def test_la_vitrine_n_a_aucun_script_en_ligne(client):
    """La CSP n'autorise que les scripts servis par l'application."""
    for chemin in PAGES_SITE:
        page = client.get(chemin).text
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", page), chemin
