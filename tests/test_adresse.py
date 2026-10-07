"""L'adresse du client — la traduction est testée nue, la recherche en `live`."""

from __future__ import annotations

import pytest

from app.adresse import _mapper, rechercher


def feature(**proprietes):
    return {"type": "Feature", "properties": proprietes}


def test_une_adresse_s_ecrit_comme_sur_une_enveloppe():
    trouvee = _mapper(feature(
        name="12 Rue des Compagnons", postcode="49480", city="Verrières-en-Anjou",
        context="49, Maine-et-Loire, Pays de la Loire", type="housenumber",
    ))
    assert trouvee.libelle == "12 Rue des Compagnons, 49480 Verrières-en-Anjou"
    assert trouvee.contexte == "49, Maine-et-Loire, Pays de la Loire"


def test_une_commune_seule_ou_incomplete_n_est_pas_proposee():
    assert _mapper(feature(name="Lyon", postcode="69001", city="Lyon", type="municipality")) is None
    assert _mapper(feature(name="Rue Haute", postcode="", city="Lyon")) is None
    assert _mapper({}) is None


async def test_une_saisie_trop_courte_n_appelle_pas_le_reseau():
    assert await rechercher("12 r") == []
    assert await rechercher("") == []


@pytest.mark.live
async def test_recherche_reelle():
    trouvees = await rechercher("12 rue des compagnons")
    assert trouvees
    assert all(t.code_postal and t.ville for t in trouvees)
