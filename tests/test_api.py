"""Ce que l'API annonce d'elle-même, et sous quelle identité elle établit les devis.

Deux sujets. D'abord `/health`, le plus petit endpoint. Il ne calcule
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

from pathlib import Path

from app.config import get_config
from app.entreprise import EntrepriseSaisie
from app.main import DemandeDevis, api_devis, health
from tests.aide import requete

FIXTURES = Path(__file__).parent / "fixtures"


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

    info = await health(requete())
    assert info["status"] == "ok"
    assert info["acces"] == "ouvert"
    assert info["mode"] == "fixtures"
    assert info["provider"] == "groq"


async def test_health_distingue_le_mode_reel(monkeypatch):
    monkeypatch.setenv("USE_FIXTURES", "false")
    monkeypatch.setenv("STRUCTURATION_PROVIDER", "anthropic")

    info = await health(requete())
    assert info["mode"] == "reel"
    assert info["provider"] == "anthropic"


# ---------------------------------------------------------------------------
# L'identité de l'artisan
# ---------------------------------------------------------------------------
#
# Elle vient du navigateur, où elle est conservée, et non plus seulement du `.env`.
# Ce que ces tests protègent, c'est la règle de fusion : ce qui est saisi gagne, ce
# qui est vide laisse passer la configuration. Sans elle, choisir son entreprise dans
# l'annuaire sans avoir encore renseigné son IBAN effacerait l'IBAN du serveur — et le
# devis sortirait avec un champ de moins qu'avant.


@pytest.fixture
def mode_fixtures(monkeypatch):
    """Rejoue une extraction enregistrée : aucun appel API, aucun euro."""
    monkeypatch.setenv("USE_FIXTURES", "true")
    return (FIXTURES / "plomberie.txt").read_text(encoding="utf-8")


async def test_sans_identite_le_devis_prend_celle_du_serveur(mode_fixtures):
    devis = await api_devis(DemandeDevis(transcription=mode_fixtures))
    assert devis.entreprise.nom == get_config().entreprise.nom


async def test_l_identite_du_navigateur_prime(mode_fixtures):
    devis = await api_devis(DemandeDevis(
        transcription=mode_fixtures,
        entreprise=EntrepriseSaisie(
            nom="SARL DUPONT PLOMBERIE",
            siret="394 659 882 00010",
            tva_intracom="FR83394659882",
        ),
    ))
    assert devis.entreprise.nom == "SARL DUPONT PLOMBERIE"
    assert devis.entreprise.siret == "394 659 882 00010"
    assert devis.entreprise.tva_intracom == "FR83394659882"


async def test_un_champ_vide_ne_efface_pas_la_configuration(mode_fixtures):
    """Un champ non saisi est une absence, pas une valeur.

    C'est le cas courant : l'artisan choisit son entreprise dans l'annuaire, ce qui
    remplit sept champs, et n'a pas encore tapé son IBAN. Celui du `.env` doit rester.
    """
    defaut = get_config().entreprise
    devis = await api_devis(DemandeDevis(
        transcription=mode_fixtures,
        entreprise=EntrepriseSaisie(nom="SARL DUPONT PLOMBERIE", iban="   "),
    ))
    assert devis.entreprise.nom == "SARL DUPONT PLOMBERIE"
    assert devis.entreprise.iban == defaut.iban


async def test_une_identite_incomplete_ne_fait_pas_echouer_la_requete(mode_fixtures):
    """`Entreprise` exige treize champs ; le navigateur n'en connaît pas forcément autant.

    Si le modèle gagne un champ demain, les identités déjà enregistrées dans les
    navigateurs doivent continuer de passer. Un 422 en rendez-vous parce qu'un artisan
    a gardé une ancienne version en mémoire serait une panne absurde.
    """
    devis = await api_devis(DemandeDevis(
        transcription=mode_fixtures,
        entreprise=EntrepriseSaisie(nom="EURL Martin"),
    ))
    assert devis.entreprise.nom == "EURL Martin"
    assert devis.entreprise.code_ape  # hérité de la configuration


async def test_les_totaux_ne_dependent_pas_de_l_identite(mode_fixtures):
    """L'en-tête change, l'arithmétique non. La garantie la plus importante du lot."""
    nu = await api_devis(DemandeDevis(transcription=mode_fixtures))
    habille = await api_devis(DemandeDevis(
        transcription=mode_fixtures,
        entreprise=EntrepriseSaisie(nom="SARL DUPONT PLOMBERIE"),
    ))
    assert nu.total_ht == habille.total_ht
    assert nu.total_ttc == habille.total_ttc
    assert nu.montant_acompte == habille.montant_acompte
