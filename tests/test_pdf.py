"""Fumigation du rendu PDF.

Ce qui est protégé ici : la pagination, le formatage français, et le fait que le gabarit
se rende sur chacune des fixtures. Un template cassé se voit dans un test rouge, pas devant
un client. Aucun appel API, aucun accès réseau — les polices voyagent avec le document.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.models import DevisExtraction, LigneDevis, Unite, to_devis
from app.pdf import (
    arreter,
    f_date_longue,
    f_euro,
    f_montant,
    f_nombre,
    f_pourcent,
    _hauteur_lignes,
    paginer,
    render,
    render_html,
)
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
    assert ENTREPRISE.metier in html  # la ligne de métier sous la raison sociale


@pytest.mark.parametrize("nom", noms_fixtures())
def test_le_html_a_exactement_le_nombre_de_pages_annonce(nom):
    """Une page de plus dans le PDF que dans le HTML signifierait un débordement ;
    ici on vérifie déjà que le gabarit produit ce que `paginer()` a décidé."""
    devis = devis_de(nom)
    _, _, total_pages = paginer(devis.lignes, devis.notes)
    assert render_html(devis).count('<article class="page">') == total_pages


def test_les_prix_estimes_sont_signales_dans_le_html():
    # Sur le papier, le signal est le dague : c'est ce qui survit à une imprimante
    # noir et blanc, là où la teinte brique disparaît.
    assert "estimé †" in render_html(devis_de("sdb"))
    assert "estimé †" not in render_html(devis_de("peinture"))


async def test_le_pdf_se_genere():
    contenu = await render(devis_de("sdb"))
    try:
        assert contenu[:4] == b"%PDF"
        assert len(contenu) > 10_000
    finally:
        await arreter()


async def test_le_pdf_a_exactement_le_nombre_de_pages_annonce():
    """Le filet de la pagination. `paginer()` raisonne sur des hauteurs mesurées ; si un
    jour le gabarit change et que ces mesures se périment, le contenu déborde et Chromium
    ajoute une page blanche — invisible au HTML, bien visible chez le client. Ce test rend
    réellement le PDF et compare. Il tourne sur les six fixtures et sur deux cas placés de
    part et d'autre de la frontière mesurée."""
    cas = [devis_de(nom) for nom in noms_fixtures()]

    court = devis_de("peinture").model_copy(deep=True)
    court.notes = ["Une seule remarque, courte."]
    cas.append(court)

    charge = devis_de("peinture").model_copy(deep=True)
    charge.notes = ["Le support n'a pas été précisé, un doublage hydrofuge peut "
                    "être nécessaire et n'est pas compté au présent devis."] * 5
    cas.append(charge)

    try:
        for devis in cas:
            _, _, attendu = paginer(devis.lignes, devis.notes)
            contenu = await render(devis)
            pages = int(re.search(rb"/Count (\d+)", contenu).group(1))
            assert pages == attendu, (
                f"{len(devis.lignes)} lignes, {len(devis.notes)} observations : "
                f"{pages} pages rendues pour {attendu} annoncées"
            )
    finally:
        await arreter()


async def test_le_pdf_embarque_ses_polices_de_texte():
    """Chromium n'embarque pas les polices variables : il en dessine les lettres en
    courbes. Le document reste juste à l'œil, mais son texte cesse d'être du texte —
    plus de sélection, plus de recherche, sur l'adresse du client comme sur les
    mentions légales. Le sans et le mono sont donc aplatis en graisses statiques
    (`scripts/aplatir_polices.py`), et ce test empêche qu'on les y remette.

    Source Serif 4 est volontairement absent de cette liste : il garde son axe
    optique, et ses six titres sortent en courbes. C'est assumé."""
    contenu = await render(devis_de("sdb"))
    try:
        familles = {m.decode().split("+")[-1] for m in re.findall(rb"/BaseFont\s*/([\w+#\-,]+)", contenu)}
        assert any(f.startswith("IBMPlexSans") for f in familles), familles
        assert any(f.startswith("IBMPlexMono") for f in familles), familles
    finally:
        await arreter()


# --- Pagination : fonction pure, aucun navigateur ---


def _lignes(n: int) -> list[LigneDevis]:
    from decimal import Decimal

    return [
        LigneDevis(
            designation=f"Prestation {i}",
            quantite=Decimal("1"),
            unite=Unite.U,
            prix_unitaire_ht=Decimal("100"),
            total_ht=Decimal("100"),
        )
        for i in range(n)
    ]


@pytest.mark.parametrize(
    "nb, tranches, cloture_sous_le_tableau, total_pages",
    [
        (0, [0], True, 2),    # devis vide : le gabarit ne doit pas casser pour autant
        (4, [4], True, 2),
        (7, [7], True, 2),    # la page 1 est pleine, la clôture y tient encore
        (8, [7, 1], True, 3),
        (12, [7, 5], True, 3),  # les douze prestations du design livré, sur ses trois pages
        (13, [7, 6], False, 4),
        (25, [7, 14, 4], True, 4),
    ],
)
def test_la_pagination(nb, tranches, cloture_sous_le_tableau, total_pages):
    pages, cloture, total = paginer(_lignes(nb))
    assert [len(p) for p in pages] == tranches
    assert cloture is cloture_sous_le_tableau
    assert total == total_pages


def test_des_observations_volumineuses_chassent_la_cloture_de_la_page_1():
    """Deux devis de quatre lignes n'occupent pas la même place : c'est la hauteur qui
    décide, pas le nombre de lignes. Sans ça, la clôture déborderait sur une page
    fantôme — une page blanche de plus dans le PDF que dans le HTML."""
    longue = "Le support n'a pas été précisé, un doublage hydrofuge peut être nécessaire. " * 3
    _, avec_peu, pages_peu = paginer(_lignes(4), ["Une remarque courte."])
    _, avec_beaucoup, pages_beaucoup = paginer(_lignes(4), [longue] * 5)
    assert (avec_peu, pages_peu) == (True, 2)
    assert (avec_beaucoup, pages_beaucoup) == (False, 3)


def test_la_pastille_estime_pousse_la_designation_au_repli():
    """« estimé † » est en ligne dans la désignation : il la fait se replier plus tôt,
    et donc grandir. Une ligne estimée est plus haute qu'une ligne dictée."""
    from decimal import Decimal

    def ligne(a_valider):
        return LigneDevis(
            designation="Fourniture et pose de plaques de plâtre hydrofuges BA13",
            quantite=Decimal("1"), unite=Unite.U, a_valider=a_valider,
            prix_unitaire_ht=Decimal("100"), total_ht=Decimal("100"),
        )

    assert _hauteur_lignes([ligne(True)]) > _hauteur_lignes([ligne(False)])


def test_les_lignes_sont_numerotees_en_continu_d_une_page_a_l_autre():
    pages, _, _ = paginer(_lignes(12))
    numeros = [numero for page in pages for numero, _ in page]
    assert numeros == list(range(1, 13))


# --- Formatage français : c'est ce que le client lit sur le devis ---


def test_les_montants_sont_formates_a_la_francaise():
    assert f_euro(1234.5) == "1\u202f234,50\u202f€"
    assert f_euro(0) == "0,00\u202f€"
    assert f_euro(1234567.89) == "1\u202f234\u202f567,89\u202f€"


def test_le_tableau_du_devis_n_affiche_pas_le_symbole():
    assert f_montant(1234.5) == "1\u202f234,50"


def test_les_quantites_n_affichent_pas_de_decimales_inutiles():
    assert f_nombre(15) == "15"
    assert f_nombre(0.5) == "0,5"
    assert f_nombre(12.50) == "12,5"


def test_les_pourcentages():
    assert f_pourcent(0.30) == "30"
    assert f_pourcent(0.10) == "10"


def test_les_dates_s_ecrivent_en_toutes_lettres_sur_le_devis():
    from datetime import date

    assert f_date_longue(date(2026, 8, 27)) == "27 août 2026"
    assert f_date_longue(date(2026, 12, 1)) == "1 décembre 2026"
