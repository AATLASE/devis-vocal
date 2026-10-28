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
# Un chiffrage en cours ne gèle pas le serveur
# ---------------------------------------------------------------------------


async def test_health_repond_pendant_un_chiffrage(monkeypatch):
    """Le client du fournisseur est synchrone. Appelé dans la boucle, il gelait tout le
    serveur pendant les vingt à quarante secondes d'un chiffrage : la page, l'aperçu,
    le PDF et la dictée des autres artisans, et le healthcheck Docker avec.

    `structure()` est remplacée par une attente de 3 s, que le test lève dès que
    /health a répondu. Serveur gelé, /health ne passerait qu'au bout des 3 s.
    """
    import asyncio
    import threading
    import time

    import httpx

    from app import main
    from app.models import DevisExtraction

    extraction = DevisExtraction.model_validate_json(
        (FIXTURES / "plomberie.json").read_text(encoding="utf-8")
    )
    commence, libere = threading.Event(), threading.Event()

    def chiffrage_lent(transcription):
        commence.set()
        libere.wait(timeout=3)
        return extraction

    monkeypatch.setattr(main, "structure", chiffrage_lent)

    # ASGITransport ne déclenche pas le `lifespan` : pas de Chromium.
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # Le chrono part avec le devis : une boucle gelée le serait pendant la pause
        # qui suit, et /health partirait ensuite sur un serveur redevenu libre.
        debut = time.perf_counter()
        devis = asyncio.create_task(client.post("/api/devis", json={"transcription": "x"}))
        await asyncio.sleep(0.2)
        sante = await client.get("/health")
        duree = time.perf_counter() - debut
        libere.set()

        assert commence.is_set(), "le chiffrage n'avait pas commencé"
        assert sante.status_code == 200
        assert duree < 1.5, f"/health a attendu {duree:.1f} s derrière le chiffrage"
        assert (await devis).status_code == 200


def test_le_chiffrage_est_borne_dans_le_temps(monkeypatch):
    """Sans délai, le SDK attend 600 s et réessaie deux fois. Un modèle local lent
    (Ollama) se règle par DELAI_CHIFFRAGE_S plutôt qu'en retirant la borne."""
    import openai

    from app.structuration import structure

    construits = []

    class Construit(Exception):
        pass

    def faux_client(**options):
        construits.append(options)
        raise Construit

    monkeypatch.setenv("STRUCTURATION_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "cle")
    monkeypatch.setattr(openai, "OpenAI", faux_client)

    with pytest.raises(Construit):
        structure("Pose d'un lavabo.")
    assert construits[0]["timeout"] == 90
    assert construits[0]["max_retries"] == 1


# ---------------------------------------------------------------------------
# L'identité de l'artisan
# ---------------------------------------------------------------------------
#
# Elle vient du navigateur, où elle est conservée, et non plus seulement du `.env`.
# Ce que ces tests protègent, c'est la règle de fusion : ce qui est saisi gagne, et ce
# qui est vide reste vide. La configuration est l'identité d'une autre entreprise : la
# laisser combler les trous imprimait ses coordonnées sous le nom de l'artisan. Voir
# `EntrepriseSaisie.fusionner` et tests/test_entreprise.py.


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


async def test_un_champ_vide_ne_reprend_pas_la_configuration(mode_fixtures):
    """Un champ non saisi est une absence, pas une valeur — et pas celle du `.env`.

    C'est le cas courant : l'artisan choisit son entreprise dans l'annuaire, ce qui
    remplit sept champs, et n'a pas encore tapé son IBAN. Celui du `.env` est celui
    d'une autre entreprise : l'acompte partirait sur son compte.
    """
    devis = await api_devis(DemandeDevis(
        transcription=mode_fixtures,
        entreprise=EntrepriseSaisie(nom="SARL DUPONT PLOMBERIE", iban="   "),
    ))
    assert devis.entreprise.nom == "SARL DUPONT PLOMBERIE"
    assert devis.entreprise.iban == ""


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
    assert devis.entreprise.code_ape == ""  # vide, et pas emprunté à la configuration


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
