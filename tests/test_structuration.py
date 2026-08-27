"""Tests du chiffrage — sur fixtures, sans aucun appel API.

Ce qui est vérifié ici, c'est l'arithmétique : c'est la seule partie du pipeline qui doit
être juste à tous les coups. Le reste (la qualité de compréhension du vocal) se juge à
l'œil sur le PDF, pas dans un test.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.models import (
    Devis,
    DevisExtraction,
    Entreprise,
    LigneExtraction,
    TauxTVA,
    Unite,
    arrondi,
    to_devis,
)

FIXTURES = Path(__file__).parent / "fixtures"
INSTANT = datetime(2026, 8, 27, 14, 32)

ENTREPRISE = Entreprise(
    nom="Test SARL",
    forme_juridique="SARL",
    adresse="1 rue du Test",
    code_postal_ville="75001 Paris",
    telephone="01 02 03 04 05",
    email="test@test.fr",
    siret="000 000 000 00000",
    code_ape="4399C",
    tva_intracom="FR00000000000",
    assurance="Assureur",
    assurance_police="0000",
    iban="FR00",
)


def noms_fixtures() -> list[str]:
    return sorted(p.stem for p in FIXTURES.glob("*.json"))


def charge(nom: str) -> DevisExtraction:
    return DevisExtraction.model_validate_json((FIXTURES / f"{nom}.json").read_text(encoding="utf-8"))


def devis_de(nom: str, **kwargs) -> Devis:
    return to_devis(charge(nom), entreprise=ENTREPRISE, maintenant=INSTANT, **kwargs)


# ---------------------------------------------------------------------------
# Les fixtures elles-mêmes
# ---------------------------------------------------------------------------


def test_il_y_a_des_fixtures():
    assert noms_fixtures(), "Aucune fixture : le mode hors-ligne et les tests sont vides."


@pytest.mark.parametrize("nom", noms_fixtures())
def test_chaque_fixture_a_sa_transcription(nom):
    """Le mode hors-ligne apparie .json et .txt : l'un sans l'autre est inutilisable."""
    assert (FIXTURES / f"{nom}.txt").exists()


@pytest.mark.parametrize("nom", noms_fixtures())
def test_chaque_fixture_est_valide(nom):
    charge(nom)  # lève si le schéma ne colle pas


# ---------------------------------------------------------------------------
# L'arithmétique
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nom", noms_fixtures())
def test_les_totaux_sont_coherents(nom):
    devis = devis_de(nom)

    # Le total doit être la somme de ce que le client additionnera lui-même en relisant.
    assert devis.total_ht == sum((l.total_ht for l in devis.lignes), Decimal("0"))
    assert devis.montant_tva == arrondi(devis.total_ht * devis.taux_tva)
    assert devis.total_ttc == devis.total_ht + devis.montant_tva
    assert devis.montant_acompte == arrondi(devis.total_ttc * devis.acompte_pct)


@pytest.mark.parametrize("nom", noms_fixtures())
def test_chaque_ligne_est_quantite_fois_prix(nom):
    for ligne in devis_de(nom).lignes:
        assert ligne.total_ht == arrondi(ligne.quantite * ligne.prix_unitaire_ht)


@pytest.mark.parametrize("nom", noms_fixtures())
def test_tous_les_montants_sont_au_centime(nom):
    devis = devis_de(nom)
    montants = [devis.total_ht, devis.montant_tva, devis.total_ttc, devis.montant_acompte]
    montants += [l.prix_unitaire_ht for l in devis.lignes]
    montants += [l.total_ht for l in devis.lignes]
    for montant in montants:
        assert montant == montant.quantize(Decimal("0.01")), f"{montant} n'est pas au centime"


def test_arrondi_commercial_a_la_hausse():
    """ROUND_HALF_UP, pas la règle du banquier : 0,125 se facture 0,13."""
    assert arrondi("0.125") == Decimal("0.13")
    assert arrondi("0.135") == Decimal("0.14")


def test_un_total_juste_meme_avec_des_centimes():
    extraction = DevisExtraction(
        client_nom=None, client_adresse=None, client_telephone=None, type_travaux=None,
        lignes=[
            LigneExtraction(designation="A", detail=None, quantite=3, unite=Unite.U,
                            prix_unitaire_ht=0.335, a_valider=False),
            LigneExtraction(designation="B", detail=None, quantite=3, unite=Unite.U,
                            prix_unitaire_ht=0.335, a_valider=False),
        ],
        taux_tva_suggere=TauxTVA.REDUIT, notes=[],
    )
    devis = to_devis(extraction, entreprise=ENTREPRISE, maintenant=INSTANT)

    # 0,335 est arrondi à 0,34 avant multiplication : le client vérifie 3 x 0,34 = 1,02.
    assert devis.lignes[0].prix_unitaire_ht == Decimal("0.34")
    assert devis.lignes[0].total_ht == Decimal("1.02")
    assert devis.total_ht == Decimal("2.04")


# ---------------------------------------------------------------------------
# TVA, acompte, dates
# ---------------------------------------------------------------------------


def test_la_tva_suggeree_par_le_llm_est_appliquee():
    assert devis_de("sdb").taux_tva == Decimal("0.10")


def test_la_tva_peut_etre_forcee():
    devis = devis_de("sdb", tva_forcee=Decimal("0.20"))
    assert devis.taux_tva == Decimal("0.20")
    assert devis.montant_tva == arrondi(devis.total_ht * Decimal("0.20"))
    assert devis.taux_tva_libelle == "20"


def test_le_libelle_de_tva_est_a_la_francaise():
    assert devis_de("sdb", tva_forcee=Decimal("0.055")).taux_tva_libelle == "5,5"


def test_la_validite_decale_la_date():
    devis = devis_de("sdb", validite_jours=45)
    assert devis.date_emission == date(2026, 8, 27)
    assert devis.date_validite == date(2026, 10, 11)


def test_le_numero_est_deterministe():
    assert devis_de("sdb").numero == "DEV-20260827-1432"


# ---------------------------------------------------------------------------
# Le signalement des prix estimés
# ---------------------------------------------------------------------------


def test_a_valider_remonte_jusqu_au_devis():
    assert devis_de("sdb").a_des_prix_estimes is True      # aucun prix dicté
    assert devis_de("peinture").a_des_prix_estimes is False  # tous les prix dictés


def test_a_valider_est_conserve_ligne_par_ligne():
    source = charge("elec")
    devis = devis_de("elec")
    assert [l.a_valider for l in devis.lignes] == [l.a_valider for l in source.lignes]
    assert devis.a_des_prix_estimes is True  # les saignées sont estimées, le tableau non


# ---------------------------------------------------------------------------
# Le cas où il n'y a rien à chiffrer
# ---------------------------------------------------------------------------


def test_un_vocal_sans_prestation_ne_fabrique_pas_de_devis():
    devis = devis_de("vague")
    assert devis.lignes == []
    assert devis.total_ht == Decimal("0.00")
    assert devis.total_ttc == Decimal("0.00")
    assert devis.notes, "Le devis vide doit au moins expliquer pourquoi il est vide."


def test_les_champs_client_absents_restent_vides():
    """Ne jamais inventer : la fixture n'a pas de nom, le devis n'en invente pas."""
    devis = devis_de("plomberie")
    assert devis.client.nom is None
    assert devis.client.adresse == "8 avenue Gambetta, 2e étage"


# ---------------------------------------------------------------------------
# Le mode hors-ligne
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nom", noms_fixtures())
def test_le_mode_fixtures_retrouve_la_bonne_extraction(nom, monkeypatch):
    monkeypatch.setenv("USE_FIXTURES", "true")
    from app.config import get_config
    from app.structuration import structure

    get_config.cache_clear()
    try:
        transcript = (FIXTURES / f"{nom}.txt").read_text(encoding="utf-8")
        assert structure(transcript) == charge(nom)
    finally:
        get_config.cache_clear()


def test_le_mode_fixtures_refuse_une_transcription_inconnue(monkeypatch):
    monkeypatch.setenv("USE_FIXTURES", "true")
    from app.config import get_config
    from app.structuration import StructurationError, structure

    get_config.cache_clear()
    try:
        with pytest.raises(StructurationError, match="aucune fixture"):
            structure("Un vocal qui n'a jamais été enregistré en fixture.")
    finally:
        get_config.cache_clear()


# ---------------------------------------------------------------------------
# Le vrai appel API — hors du lot par défaut
# ---------------------------------------------------------------------------


@pytest.mark.live
@pytest.mark.skipif(not os.getenv("ANTHROPIC_API_KEY"), reason="ANTHROPIC_API_KEY absente")
def test_appel_reel(monkeypatch):
    """Coûte quelques centimes. Lancer avec : pytest -m live"""
    monkeypatch.setenv("USE_FIXTURES", "false")
    from app.config import get_config
    from app.structuration import structure

    get_config.cache_clear()
    try:
        extraction = structure((FIXTURES / "sdb.txt").read_text(encoding="utf-8"))
    finally:
        get_config.cache_clear()

    assert extraction.lignes, "Un vocal de rénovation doit produire des lignes."
    assert all(l.prix_unitaire_ht > 0 for l in extraction.lignes)
    assert extraction.client_nom and "Durand" in extraction.client_nom
    # Le carrelage est fourni par la cliente : rien ne doit facturer sa fourniture.
    assert any("fourniture client" in (l.detail or "").lower() for l in extraction.lignes)
