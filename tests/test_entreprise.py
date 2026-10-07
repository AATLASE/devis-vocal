"""Tests de l'identité d'entreprise — sans réseau.

Tout ce qui est vérifié ici est arithmétique ou traduction : la clé de Luhn, la clé de
TVA, la mise en forme. C'est la partie qui doit être juste à tous les coups, parce
qu'elle finit imprimée sous les mentions obligatoires d'un devis.

La recherche elle-même appelle une API publique : elle est testée séparément, en `live`.
"""

from __future__ import annotations

import pytest

from app.entreprise import (
    EntrepriseTrouvee,
    _mapper,
    capitaliser_fr,
    chiffres,
    forme_juridique,
    formater_siret,
    luhn_valide,
    metier_depuis_ape,
    normaliser_ape,
    numero_tva,
    rechercher,
)

# SARL DUPONT PLOMBERIE, relevée dans la base publique — un vrai numéro, donc une vraie
# clé de contrôle. Un numéro inventé passerait à côté de ce que le test vérifie.
SIREN = "394659882"
SIRET = "39465988200010"
TVA = "FR83394659882"


# --- Luhn ------------------------------------------------------------------


def test_luhn_accepte_un_siren_et_un_siret_reels():
    assert luhn_valide(SIREN)
    assert luhn_valide(SIRET)


def test_luhn_accepte_la_mise_en_forme():
    """L'artisan recopie depuis un papier, avec les espaces. Ça doit passer."""
    assert luhn_valide("394 659 882 00010")
    assert luhn_valide("394 659 882")


@pytest.mark.parametrize(
    "faux",
    [
        "39465988200011",  # dernier chiffre changé
        "39465988200020",  # avant-dernier changé
        "34965988200010",  # deux chiffres intervertis
        "394659883",       # SIREN faux d'une unité
    ],
)
def test_luhn_attrape_la_faute_de_frappe(faux):
    assert not luhn_valide(faux)


@pytest.mark.parametrize("mauvais", ["", "  ", "abc", "1234", "3946598820001", "394659882000100"])
def test_luhn_refuse_ce_qui_n_est_pas_un_numero(mauvais):
    assert not luhn_valide(mauvais)


def test_luhn_connait_l_exception_la_poste():
    """La Poste ne suit pas Luhn mais la divisibilité par 5. Aucun artisan n'est
    concerné — mais un numéro valide refusé serait un bug incompréhensible."""
    assert luhn_valide("35600000009075")


# --- Numéro de TVA ---------------------------------------------------------


def test_tva_calculee_depuis_le_siren():
    """La clé est une formule, pas une donnée : un champ obligatoire qu'on ne demande pas.

    La valeur attendue est celle que renvoie aussi l'API publique — deux chemins
    indépendants qui tombent d'accord.
    """
    assert numero_tva(SIREN) == TVA


def test_tva_accepte_un_siret_complet():
    """Les neuf premiers chiffres d'un SIRET sont le SIREN : inutile de le faire extraire."""
    assert numero_tva(SIRET) == TVA
    assert numero_tva("394 659 882 00010") == TVA


@pytest.mark.parametrize("siren, attendu", [
    ("552100554", "FR96552100554"),  # Peugeot SA
    ("542107651", "FR13542107651"),  # Engie
])
def test_tva_sur_d_autres_sirens(siren, attendu):
    """Deux SIREN pris hors du bâtiment : la formule ne doit rien devoir au jeu d'essai.

    Le second est confronté au numéro que publie l'API. Le premier ne l'est pas — l'API
    ne le connaît pas, et c'est précisément pourquoi on calcule au lieu de lire.
    """
    assert numero_tva(siren) == attendu


@pytest.mark.parametrize("mauvais", ["", "abc", "1234"])
def test_tva_vide_plutot_que_fausse(mauvais):
    """Information absente = vide. La règle du projet vaut aussi ici."""
    assert numero_tva(mauvais) == ""


# --- Mise en forme ---------------------------------------------------------


def test_siret_formate_comme_sur_un_devis():
    assert formater_siret(SIRET) == "394 659 882 00010"


def test_siret_deja_formate_reste_stable():
    assert formater_siret("394 659 882 00010") == "394 659 882 00010"


def test_siret_incomplet_rendu_tel_quel():
    """On ne mutile pas ce qu'on ne reconnaît pas : l'artisan doit revoir sa saisie."""
    assert formater_siret("3946") == "3946"


def test_chiffres_ne_garde_que_les_chiffres():
    assert chiffres("394 659 882 00010") == SIRET
    assert chiffres(None) == ""


@pytest.mark.parametrize("brut, attendu", [
    ("88 AVENUE DE GRANDE BRETAGNE", "88 Avenue de Grande Bretagne"),
    ("AIX-EN-PROVENCE", "Aix-en-Provence"),
    ("RUE DE LA PAIX", "Rue de la Paix"),
    ("PLACE DE L'EGLISE", "Place de l'Eglise"),
    ("BOULOGNE-SUR-MER", "Boulogne-sur-Mer"),
    ("SAINT-ETIENNE", "Saint-Etienne"),
    ("LEZENNES", "Lezennes"),
    ("", ""),
])
def test_capitalisation_francaise(brut, attendu):
    """La base INSEE est tout en capitales. Un `.title()` naïf donnerait « Avenue De
    Grande Bretagne » et « Aix-En-Provence » — sur l'en-tête d'un document dont
    l'argument est justement d'être irréprochable."""
    assert capitaliser_fr(brut) == attendu


def test_ape_perd_son_point():
    assert normaliser_ape("43.22A") == "4322A"
    assert normaliser_ape("4322a") == "4322A"
    assert normaliser_ape("") == ""


# --- Traductions -----------------------------------------------------------


@pytest.mark.parametrize("code, attendu", [
    ("5499", "SARL"),
    ("5498", "EURL"),
    ("5710", "SAS"),
    ("5720", "SASU"),
    ("1000", "Entrepreneur individuel"),
    ("6540", "SCI"),
])
def test_formes_juridiques_courantes(code, attendu):
    assert forme_juridique(code) == attendu


def test_forme_juridique_inconnue_retombe_sur_la_famille():
    """5485 n'est pas cartographié, mais 54 est la famille des SARL."""
    assert forme_juridique("5485") == "SARL"
    assert forme_juridique("1900") == "Entrepreneur individuel"


def test_forme_juridique_vide_plutot_qu_inventee():
    assert forme_juridique("9999") == ""
    assert forme_juridique("") == ""


def test_metier_depuis_le_code_ape():
    assert metier_depuis_ape("43.22A") == "Plomberie · Chauffage · Sanitaire"
    assert metier_depuis_ape("4331Z") == "Plâtrerie · Cloisons · Doublage"


def test_metier_vide_hors_batiment():
    """Un code hors bâtiment ne doit pas produire une ligne d'accent absurde."""
    assert metier_depuis_ape("6201Z") == ""


# --- Traduction d'un résultat d'API ---------------------------------------


BRUT = {
    "siren": SIREN,
    "nom_complet": "SARL DUPONT PLOMBERIE",
    "nature_juridique": "5499",
    "activite_principale": "43.22A",
    "date_creation": "1994-01-01",
    "statut_diffusion": "O",
    "siege": {
        "siret": SIRET,
        "adresse": "88 AVENUE DE GRANDE BRETAGNE 31300 TOULOUSE",
        "code_postal": "31300",
        "libelle_commune": "TOULOUSE",
        "activite_principale": "43.22A",
    },
}


def test_mapper_remplit_les_sept_champs():
    e = _mapper(BRUT)
    assert e is not None
    assert e.nom == "SARL DUPONT PLOMBERIE"
    assert e.siret == "394 659 882 00010"
    assert e.forme_juridique == "SARL"
    assert e.code_postal_ville == "31300 Toulouse"
    assert e.code_ape == "4322A"
    assert e.metier == "Plomberie · Chauffage · Sanitaire"
    assert e.tva_intracom == TVA


def test_mapper_separe_la_voie_de_la_ville():
    """L'API donne l'adresse en un bloc ; le devis l'écrit sur deux lignes.

    Sans ça, la ville apparaît deux fois dans l'en-tête — le genre de détail qui décrédibilise
    un document dont l'argument est justement d'être conforme.
    """
    e = _mapper(BRUT)
    assert e.adresse == "88 Avenue de Grande Bretagne"
    assert "31300" not in e.adresse


def test_mapper_prefere_l_etablissement_correspondant():
    """Recherche par SIRET : l'artisan attend l'adresse de l'agence qu'il a nommée,
    pas celle du siège."""
    brut = dict(BRUT)
    brut["matching_etablissements"] = [{
        "siret": "39465988200028",
        "adresse": "5 RUE DES ARTS 31000 TOULOUSE",
        "code_postal": "31000",
        "libelle_commune": "TOULOUSE",
        "activite_principale": "43.22A",
    }]
    e = _mapper(brut)
    assert e.siret == "394 659 882 00028"
    assert e.adresse == "5 Rue des Arts"


def test_mapper_refuse_un_resultat_sans_identifiant():
    assert _mapper({"siren": "abc"}) is None
    assert _mapper({"siren": SIREN, "siege": {}}) is None


async def test_recherche_trop_courte_n_appelle_pas_le_reseau():
    """Deux lettres, c'est une frappe en cours, pas une recherche. On n'appelle rien."""
    assert await rechercher("du") == []
    assert await rechercher("") == []


@pytest.mark.live
async def test_recherche_reelle():
    """Contre l'API publique. Vérifie que la forme de la réponse n'a pas changé."""
    trouvees = await rechercher(SIRET)
    assert trouvees, "l'API publique ne répond plus, ou sa forme a changé"
    e = trouvees[0]
    assert isinstance(e, EntrepriseTrouvee)
    assert chiffres(e.siret) == SIRET
    assert e.tva_intracom == TVA
