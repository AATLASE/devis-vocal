"""Le devis corrigé par l'artisan — sans réseau, sans clé.

Ce qui est vérifié ici est l'arithmétique d'après correction : c'est le moment où un
total faux serait le plus traître, parce que l'artisan vient de toucher au devis et
le croit donc relu.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.edition import Corrections, EditionRefusee, recalculer
from app.main import DemandeCorrection, api_devis_corriger
from app.models import Client, DevisExtraction, Entreprise, to_devis

FIXTURES = Path(__file__).parent / "fixtures"

ENTREPRISE = Entreprise(
    nom="Bâti Rénov", forme_juridique="SARL au capital de 10 000 €", metier="Rénovation",
    adresse="12 rue des Compagnons", code_postal_ville="75011 Paris",
    telephone="01 00 00 00 00", email="contact@example.com", siret="394 659 882 00010",
    code_ape="4322A", tva_intracom="FR83394659882", assurance="Assureur",
    assurance_police="123", iban="FR76 0000 0000 0000 0000 0000 000",
)

INSTANT = datetime(2026, 3, 14, 9, 30)


def devis_sdb():
    extraction = DevisExtraction(**json.loads((FIXTURES / "sdb.json").read_text(encoding="utf-8")))
    return to_devis(extraction, entreprise=ENTREPRISE, transcription="vocal", maintenant=INSTANT)


def corrections_depuis(devis, **remplacements) -> Corrections:
    """Les corrections que renverrait l'écran si l'artisan n'avait rien touché."""
    brut = {
        "client": devis.client.model_dump(),
        "type_travaux": devis.type_travaux,
        "duree_estimee": devis.duree_estimee,
        "taux_tva": str(devis.taux_tva),
        "lignes": [ligne.model_dump(mode="json") for ligne in devis.lignes],
        "notes": devis.notes,
    }
    brut.update(remplacements)
    return Corrections(**brut)


def test_sans_correction_le_devis_ressort_identique():
    devis = devis_sdb()
    corrige = recalculer(devis, corrections_depuis(devis), max_lignes=60)
    assert corrige.model_dump() == devis.model_dump()


def test_une_quantite_corrigee_refait_tous_les_totaux():
    devis = devis_sdb()
    lignes = [ligne.model_dump(mode="json") for ligne in devis.lignes]
    lignes[0]["quantite"] = "20"       # 15 m² devenus 20 m² à 24 € : +120 € HT

    corrige = recalculer(devis, corrections_depuis(devis, lignes=lignes), max_lignes=60)

    assert corrige.lignes[0].total_ht == Decimal("480.00")
    assert corrige.total_ht == devis.total_ht + Decimal("120.00")
    assert corrige.montant_tva == (corrige.total_ht * corrige.taux_tva).quantize(Decimal("0.01"))
    assert corrige.total_ttc == corrige.total_ht + corrige.montant_tva


def test_les_totaux_envoyes_par_le_navigateur_sont_ignores():
    """Un total vient de Python, jamais d'un champ modifiable côté client."""
    devis = devis_sdb()
    truque = devis.model_copy(update={"total_ht": Decimal("1"), "total_ttc": Decimal("1")})
    lignes = [{**ligne.model_dump(mode="json"), "total_ht": "0.01"} for ligne in devis.lignes]

    corrige = recalculer(truque, corrections_depuis(devis, lignes=lignes), max_lignes=60)

    assert corrige.total_ht == devis.total_ht
    assert corrige.total_ttc == devis.total_ttc
    assert [l.total_ht for l in corrige.lignes] == [l.total_ht for l in devis.lignes]


def test_le_numero_et_les_dates_ne_bougent_pas():
    """Une correction n'est pas un nouveau devis : le client doit pouvoir rapprocher les deux."""
    devis = devis_sdb()
    corrige = recalculer(devis, corrections_depuis(devis, client={"nom": "M. Martin"}), max_lignes=60)
    assert corrige.numero == devis.numero == "DEV-20260314-0930"
    assert corrige.date_emission == date(2026, 3, 14)
    assert corrige.date_validite == devis.date_validite
    assert corrige.entreprise == devis.entreprise
    assert corrige.transcription == "vocal"


def test_le_client_corrige_et_les_champs_vides_redeviennent_absents():
    devis = devis_sdb()
    client = {"nom": "  M. Martin ", "adresse": "", "telephone": "06 12 34 56 78"}
    corrige = recalculer(devis, corrections_depuis(devis, client=client), max_lignes=60)
    assert corrige.client == Client(nom="M. Martin", adresse=None, telephone="06 12 34 56 78")


def test_une_ligne_ajoutee_a_la_main_est_chiffree():
    devis = devis_sdb()
    lignes = [ligne.model_dump(mode="json") for ligne in devis.lignes]
    lignes.append({"designation": "Évacuation des gravats", "quantite": "1",
                   "unite": "forfait", "prix_unitaire_ht": "180"})

    corrige = recalculer(devis, corrections_depuis(devis, lignes=lignes), max_lignes=60)

    assert corrige.lignes[-1].total_ht == Decimal("180.00")
    assert corrige.lignes[-1].a_valider is False
    assert corrige.total_ht == devis.total_ht + Decimal("180.00")


def test_un_prix_confirme_sort_des_prix_estimes():
    devis = devis_sdb()
    assert devis.total_ht_estime > 0
    lignes = [{**ligne.model_dump(mode="json"), "a_valider": False} for ligne in devis.lignes]

    corrige = recalculer(devis, corrections_depuis(devis, lignes=lignes), max_lignes=60)

    assert corrige.total_ht_estime == Decimal("0.00")
    assert not corrige.a_des_prix_estimes


def test_le_passage_a_20_pourcent_refait_la_tva_et_l_acompte():
    devis = devis_sdb()
    corrige = recalculer(devis, corrections_depuis(devis, taux_tva="0.20"), max_lignes=60)
    assert corrige.taux_tva == Decimal("0.20")
    assert corrige.montant_tva == (devis.total_ht * Decimal("0.20")).quantize(Decimal("0.01"))
    assert corrige.montant_acompte == (corrige.total_ttc * devis.acompte_pct).quantize(Decimal("0.01"))


def test_les_quantites_decimales_restent_exactes():
    """0,1 m³ × 3 ne doit pas donner 0,30000000000000004 quelque part en chemin."""
    devis = devis_sdb()
    lignes = [{"designation": "Béton", "quantite": "0.1", "unite": "m³", "prix_unitaire_ht": "0.3"}]
    corrige = recalculer(devis, corrections_depuis(devis, lignes=lignes), max_lignes=60)
    assert corrige.lignes[0].quantite == Decimal("0.1")
    assert corrige.lignes[0].total_ht == Decimal("0.03")


@pytest.mark.parametrize("ligne, message", [
    ({"designation": "  "}, "Ligne 1 : la désignation est vide"),
    ({"quantite": "0"}, "Ligne 1 : la quantité"),
    ({"quantite": "-2"}, "Ligne 1 : la quantité"),
    ({"prix_unitaire_ht": "-5"}, "Ligne 1 : le prix unitaire"),
    ({"prix_unitaire_ht": "1e12"}, "Ligne 1 : le prix unitaire"),
])
def test_une_ligne_inchiffrable_est_refusee_avec_son_numero(ligne, message):
    devis = devis_sdb()
    base = {"designation": "Pose", "quantite": "1", "unite": "u", "prix_unitaire_ht": "10"}
    with pytest.raises(EditionRefusee, match=message):
        recalculer(devis, corrections_depuis(devis, lignes=[{**base, **ligne}]), max_lignes=60)


def test_un_devis_sans_ligne_ou_trop_long_est_refuse():
    devis = devis_sdb()
    with pytest.raises(EditionRefusee, match="au moins une ligne"):
        recalculer(devis, corrections_depuis(devis, lignes=[]), max_lignes=60)
    with pytest.raises(EditionRefusee, match="dépasser 3 lignes"):
        recalculer(devis, corrections_depuis(devis), max_lignes=3)


def test_un_taux_de_tva_inconnu_est_refuse():
    devis = devis_sdb()
    with pytest.raises(EditionRefusee, match="10 % ou 20 %"):
        recalculer(devis, corrections_depuis(devis, taux_tva="0.055"), max_lignes=60)


async def test_la_route_rend_le_devis_corrige_et_traduit_les_refus():
    devis = devis_sdb()
    corrige = await api_devis_corriger(DemandeCorrection(
        devis=devis, corrections=corrections_depuis(devis, taux_tva="0.20")))
    assert corrige.taux_tva == Decimal("0.20")

    with pytest.raises(HTTPException) as refus:
        await api_devis_corriger(DemandeCorrection(
            devis=devis, corrections=corrections_depuis(devis, lignes=[])))
    assert refus.value.status_code == 400
