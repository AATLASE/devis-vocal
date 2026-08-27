"""Fumigation du rendu PDF.

Huit lignes qui protègent la démo : un template cassé se voit dans un test rouge, pas
devant un client. Aucun appel API — le PDF se rend depuis les fixtures.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from app.models import DevisExtraction, to_devis
from app.pdf import arreter, f_euro, f_nombre, f_pourcent, render, render_html
from tests.test_structuration import ENTREPRISE, INSTANT, noms_fixtures

FIXTURES = Path(__file__).parent / "fixtures"


def devis_de(nom: str):
    extraction = DevisExtraction.model_validate_json((FIXTURES / f"{nom}.json").read_text(encoding="utf-8"))
    return to_devis(extraction, entreprise=ENTREPRISE, maintenant=INSTANT)


@pytest.mark.parametrize("nom", noms_fixtures())
def test_le_html_se_rend(nom):
    html = render_html(devis_de(nom))
    assert "DEV-20260827-1432" in html
    assert "Bon pour accord" in html
    assert "279-0 bis" in html  # la mention de TVA réduite doit être là


def test_les_prix_estimes_sont_signales_dans_le_html():
    assert "Prix estimé" in render_html(devis_de("sdb"))
    assert "Prix estimé" not in render_html(devis_de("peinture"))


async def test_le_pdf_se_genere():
    contenu = await render(devis_de("sdb"))
    try:
        assert contenu[:4] == b"%PDF"
        assert len(contenu) > 10_000
    finally:
        await arreter()


# --- Formatage français : c'est ce que le client lit sur le devis ---


def test_les_montants_sont_formates_a_la_francaise():
    assert f_euro(1234.5) == "1\u00a0234,50\u00a0€"
    assert f_euro(0) == "0,00\u00a0€"
    assert f_euro(1234567.89) == "1\u00a0234\u00a0567,89\u00a0€"


def test_les_quantites_n_affichent_pas_de_decimales_inutiles():
    assert f_nombre(15) == "15"
    assert f_nombre(0.5) == "0,5"
    assert f_nombre(12.50) == "12,5"


def test_les_pourcentages():
    assert f_pourcent(0.30) == "30"
    assert f_pourcent(0.10) == "10"
