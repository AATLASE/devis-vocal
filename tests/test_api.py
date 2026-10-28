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


# ---------------------------------------------------------------------------
# Le compte et le template
# ---------------------------------------------------------------------------
#
# Ces routes passent par l'application entière, middlewares compris, mais sans son
# `lifespan` : aucun de ces tests ne lance Chromium.

PIRATE = "REGLEZ SUR IBAN FR76 PIRATE"


@pytest.fixture
async def client():
    import httpx

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _fichier(html: str) -> dict:
    return {"template": ("template.html", html.encode("utf-8"), "text/html")}


@pytest.mark.parametrize("methode, chemin, corps", [
    ("GET", "/api/profil", {}),
    ("PUT", "/api/profil", {"json": {"prenom": "A", "nom": "B", "entreprise": "C", "role": "D"}}),
    ("GET", "/api/template", {}),
    ("PUT", "/api/template", {"files": _fichier(f"<p>{PIRATE}</p>")}),
    ("DELETE", "/api/template", {}),
    ("POST", "/api/template/apercu", {"files": _fichier(f"<p>{PIRATE}</p>")}),
    ("GET", "/api/template/modele", {}),
])
async def test_sans_firebase_les_routes_du_compte_n_existent_pas(client, methode, chemin, corps):
    """« Sans identifiants Firebase, tout est inactif. » Ces routes rangeaient tout sous
    le même utilisateur local : la fiche et le template d'un visiteur devenaient ceux
    de tous les autres."""
    reponse = await client.request(methode, chemin, **corps)

    assert reponse.status_code == 404


async def test_sans_firebase_le_template_d_un_visiteur_ne_s_imprime_pas_chez_un_autre(client):
    """Le scénario de la revue : A pose un template au faux IBAN, B relit son devis."""
    from app import templates

    pose = await client.put("/api/template", files=_fichier(f"<p>{PIRATE}</p>"))
    # Même un template rangé en mémoire par l'ancien chemin ne s'applique plus.
    templates.enregistrer("local", "pirate.html", f"<p>{PIRATE}</p>")
    try:
        apercu = await client.post(
            "/api/apercu",
            content=templates.devis_exemple().model_dump_json(),
            headers={"Content-Type": "application/json"},
        )
    finally:
        templates.vider_la_memoire()

    assert pose.status_code == 404
    assert apercu.status_code == 200
    assert PIRATE not in apercu.text
    assert "X-Template-Repli" not in apercu.headers


async def test_un_template_qui_calcule_longtemps_ne_gele_pas_le_serveur(client, monkeypatch):
    """Le rendu d'un template tiers tournait dans la boucle du seul worker : pendant
    qu'il calculait, plus aucune requête n'était servie, `/health` compris. Il tourne
    désormais à côté, et s'arrête au délai."""
    import asyncio
    import threading

    from app import authentification, pdf
    from app import templates
    from app.authentification import Utilisateur

    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", '{"type": "service_account"}')
    get_config.cache_clear()
    monkeypatch.setattr(authentification, "verifier_jeton",
                        lambda _jeton: Utilisateur(uid="artisan-1"))
    monkeypatch.setattr(pdf, "DELAI_JINJA_S", 2)

    def pas_de_firestore():
        raise AssertionError("aucun test n'appelle Firestore")

    monkeypatch.setattr(templates, "_firestore", pas_de_firestore)

    # Pour ne demander la configuration qu'une fois le rendu du template commencé.
    commence = threading.Event()
    rendre = templates.render_html

    def rendre_en_le_signalant(*args, **kwargs):
        commence.set()
        return rendre(*args, **kwargs)

    monkeypatch.setattr(templates, "render_html", rendre_en_le_signalant)

    ordre: list[str] = []
    lourd = "{% set r = range(100000) %}{% for i in r %}{% for j in r %}{% endfor %}{% endfor %}"

    async def poser():
        reponse = await client.put("/api/template", files=_fichier(lourd),
                                   headers={"Authorization": "Bearer jeton"})
        ordre.append("template")
        return reponse

    async def demander():
        while not commence.is_set():
            await asyncio.sleep(0.01)
        reponse = await client.get("/api/firebase")
        ordre.append("firebase")
        return reponse

    pose, config = await asyncio.wait_for(asyncio.gather(poser(), demander()), 30)

    assert ordre == ["firebase", "template"]
    assert config.status_code == 200
    assert pose.status_code == 400
    assert "dépassé" in pose.json()["detail"]
