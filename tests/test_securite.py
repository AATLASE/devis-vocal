"""Les garde-fous — ce qui protège le crédit API et la machine.

Ces tests valent surtout par ce qu'ils empêchent de casser plus tard. Un code d'accès
qu'on retire par inadvertance, une limite de débit qu'un refactor court-circuite, un
plafond quotidien qui cesse de compter : rien de tout ça ne se voit à l'usage. La panne
n'arrive que le jour où quelqu'un trouve l'URL, et elle se lit sur une facture.

Tout tourne hors ligne, sans Chromium et sans la moindre clé.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.testclient import TestClient

from app import securite
from app.config import get_config
from app.main import app, health
from tests.aide import requete

CODE = "chantier-2026"


@pytest.fixture(autouse=True)
def etat_neuf():
    """`get_config` est en cache et les compteurs vivent dans le module : sans ce
    nettoyage, le premier test déciderait pour tous les suivants."""
    get_config.cache_clear()
    securite.reinitialiser()
    yield
    get_config.cache_clear()
    securite.reinitialiser()


@pytest.fixture
def porte_fermee(monkeypatch):
    monkeypatch.setenv("ACCES_CODE", CODE)


# ---------------------------------------------------------------------------
# La porte
# ---------------------------------------------------------------------------


def test_sans_code_configure_tout_est_ouvert():
    """C'est le mode développement, et c'est celui des tests. La protection s'arme
    quand on renseigne la variable, pas avant."""
    assert not securite.acces_requis()
    securite.verifier_acces(requete())  # ne lève pas


def test_le_code_configure_ferme_la_porte(porte_fermee):
    assert securite.acces_requis()
    with pytest.raises(HTTPException) as echec:
        securite.verifier_acces(requete())
    assert echec.value.status_code == 401


@pytest.mark.parametrize("faux", ["", "autre", "chantier-2025", "chantier-2026 ", "CHANTIER-2026"])
def test_un_code_faux_est_refuse(porte_fermee, faux):
    with pytest.raises(HTTPException):
        securite.verifier_acces(requete({"X-Acces": faux}))


def test_le_bon_code_passe(porte_fermee):
    securite.verifier_acces(requete({"X-Acces": CODE}))  # ne lève pas


async def test_health_tait_le_moteur_a_un_inconnu(porte_fermee):
    """Le mode et le moteur disent quelles clés tournent derrière. Un visiteur de
    passage n'a pas à l'apprendre — il doit seulement savoir qu'un code est demandé,
    sinon la page ne peut pas le lui réclamer."""
    ouvert = await health(requete())
    assert ouvert == {"status": "ok", "acces": "requis"}
    assert "provider" not in ouvert


async def test_health_repond_completement_avec_le_code(porte_fermee, monkeypatch):
    """« ouvert » et non « requis » : le champ dit l'état de la requête, pas la
    configuration du serveur. Les confondre donne une porte qui ne s'ouvre jamais."""
    monkeypatch.setenv("USE_FIXTURES", "true")
    info = await health(requete({"X-Acces": CODE}))
    assert info["acces"] == "ouvert"
    assert info["mode"] == "fixtures"


# ---------------------------------------------------------------------------
# Le débit
# ---------------------------------------------------------------------------


def test_le_debit_est_plafonne(monkeypatch):
    monkeypatch.setenv("LIMITE_PAR_MINUTE", "5")
    r = requete(ip="198.51.100.4")

    for _ in range(5):
        securite.verifier_debit(r)

    with pytest.raises(HTTPException) as echec:
        securite.verifier_debit(r)
    assert echec.value.status_code == 429


def test_le_debit_est_compte_par_adresse(monkeypatch):
    """Un artisan pressé ne doit pas faire taire son voisin."""
    monkeypatch.setenv("LIMITE_PAR_MINUTE", "3")
    for _ in range(3):
        securite.verifier_debit(requete(ip="198.51.100.4"))

    securite.verifier_debit(requete(ip="198.51.100.9"))  # ne lève pas


def test_le_suivi_des_adresses_ne_gonfle_pas_indefiniment(monkeypatch):
    """Une entrée par adresse jamais revue, c'est une fuite de mémoire lente sur un
    service exposé."""
    monkeypatch.setenv("LIMITE_PAR_MINUTE", "1")
    for n in range(2500):
        try:
            securite.verifier_debit(requete(ip=f"198.51.100.{n % 256}.{n}"))
        except HTTPException:
            pass
    assert securite.compteurs()["adresses_suivies"] <= 2500


# ---------------------------------------------------------------------------
# Le portefeuille
# ---------------------------------------------------------------------------


def test_le_plafond_quotidien_arrete_les_appels_payants(monkeypatch):
    """Le garde-fou qui compte vraiment : celui qui protège la carte bancaire quand le
    code a circulé ou qu'un script tourne en boucle."""
    monkeypatch.setenv("DEVIS_PAR_JOUR", "3")

    for _ in range(3):
        securite.consommer_devis()

    with pytest.raises(HTTPException) as echec:
        securite.consommer_devis()
    assert echec.value.status_code == 429
    assert securite.compteurs()["devis_du_jour"] == 3


def test_le_plafond_se_desactive_a_zero(monkeypatch):
    monkeypatch.setenv("DEVIS_PAR_JOUR", "0")
    monkeypatch.setenv("DEVIS_PAR_MOIS", "0")
    for _ in range(50):
        securite.consommer_devis()  # ne lève jamais


def test_le_plafond_mensuel_attrape_ce_que_le_journalier_laisse_passer(monkeypatch):
    """80 par jour, c'est 2 400 par mois : un plafond journalier seul ne protège pas
    d'une fuite lente. Ici le mensuel arrête alors que le journalier n'a rien vu."""
    monkeypatch.setenv("DEVIS_PAR_JOUR", "50")
    monkeypatch.setenv("DEVIS_PAR_MOIS", "4")

    for _ in range(4):
        securite.consommer_devis()

    with pytest.raises(HTTPException) as echec:
        securite.consommer_devis()
    assert "mois" in echec.value.detail
    assert securite.compteurs()["devis_du_jour"] == 4  # le journalier n'a pas bronché


def test_le_compteur_survit_a_un_redemarrage(monkeypatch):
    """LE test de ce fichier.

    Un plafond qu'on remet à zéro en relançant l'application ne protège de rien — et
    c'est exactement au redéploiement qu'on aurait envie de tricher. `reinitialiser()`
    vide l'état en mémoire : c'est ce que fait un redémarrage.
    """
    monkeypatch.setenv("DEVIS_PAR_JOUR", "3")

    securite.consommer_devis()
    securite.consommer_devis()
    assert securite.compteurs()["devis_du_jour"] == 2

    securite.reinitialiser()  # le processus redémarre

    assert securite.compteurs()["devis_du_jour"] == 2, "le compteur est reparti de zéro"
    securite.consommer_devis()
    with pytest.raises(HTTPException):
        securite.consommer_devis()


def test_un_fichier_abime_ne_fait_pas_tomber_le_service(monkeypatch):
    """Repartir de zéro est le mauvais côté du compromis, mais refuser de servir
    parce qu'un fichier de compteurs est illisible transformerait un garde-fou en
    panne — devant un artisan."""
    chemin = get_config().compteurs_fichier
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text("{ceci n'est pas du JSON", encoding="utf-8")
    securite.reinitialiser()

    securite.consommer_devis()  # ne lève pas
    assert securite.compteurs()["devis_du_jour"] == 1


def test_le_changement_de_jour_ne_remet_pas_le_mois_a_zero(monkeypatch):
    """Sinon le plafond mensuel serait annulé chaque nuit, et ne servirait à rien."""
    import json
    from datetime import date, timedelta

    hier = date.today() - timedelta(days=1)
    chemin = get_config().compteurs_fichier
    chemin.parent.mkdir(parents=True, exist_ok=True)
    chemin.write_text(json.dumps({
        "jour": hier.isoformat(), "devis_jour": 40,
        "mois": date.today().strftime("%Y-%m"), "devis_mois": 200,
    }), encoding="utf-8")
    securite.reinitialiser()

    securite.consommer_devis()
    etat = securite.compteurs()
    assert etat["devis_du_jour"] == 1     # le jour a tourné
    assert etat["devis_du_mois"] == 201   # le mois a continué


# ---------------------------------------------------------------------------
# Les middlewares
# ---------------------------------------------------------------------------
#
# `TestClient` sans gestionnaire de contexte ne déclenche pas le `lifespan` : aucun
# Chromium ne démarre. On évite simplement /api/pdf.


@pytest.fixture
def client():
    return TestClient(app)


def test_un_corps_trop_gros_est_refuse_avant_lecture(client):
    """Sans ce contrôle, `await audio.read()` charge d'abord tout en mémoire et ne
    vérifie la taille qu'ensuite : une seule requête suffit à faire tomber la machine."""
    reponse = client.post(
        "/api/devis",
        content=b"{}",
        headers={"Content-Type": "application/json", "Content-Length": str(50 * 1024 * 1024)},
    )
    assert reponse.status_code == 413


def test_un_corps_normal_passe(client):
    """Le garde-fou ne doit pas gêner l'usage courant."""
    reponse = client.post("/api/devis", json={"transcription": ""})
    assert reponse.status_code != 413


def test_les_entetes_de_securite_sont_poses(client):
    entetes = client.get("/health").headers
    assert "frame-ancestors 'none'" in entetes["content-security-policy"]
    assert "default-src 'self'" in entetes["content-security-policy"]
    assert entetes["x-content-type-options"] == "nosniff"
    assert entetes["x-frame-options"] == "DENY"
    assert entetes["referrer-policy"] == "no-referrer"


def test_le_micro_reste_autorise():
    """Une Permissions-Policy trop zélée couperait la dictée — la fonction centrale
    du produit — sans le moindre message d'erreur."""
    assert "microphone=(self)" in securite.ENTETES_SECURITE["Permissions-Policy"]
    assert "camera=()" in securite.ENTETES_SECURITE["Permissions-Policy"]


def test_la_page_et_les_fichiers_statiques_restent_libres(client, monkeypatch):
    """La page où l'on saisit le code doit pouvoir se charger sans le code."""
    monkeypatch.setenv("ACCES_CODE", CODE)
    get_config.cache_clear()

    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200
    assert client.post("/api/devis", json={"transcription": "x"}).status_code == 401


def test_le_code_ouvre_les_routes_protegees(client, monkeypatch):
    monkeypatch.setenv("ACCES_CODE", CODE)
    monkeypatch.setenv("USE_FIXTURES", "true")
    get_config.cache_clear()

    reponse = client.get("/api/entreprise?q=du", headers={"X-Acces": CODE})
    assert reponse.status_code == 200


# ---------------------------------------------------------------------------
# Le journal
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("nom", [
    "vocal.wav/../../../../../../evil.txt",
    "a.b/../../evil",
    "x.wav\\..\\..\\evil.txt",
    "sans-extension",
])
def test_le_journal_n_ecrit_jamais_hors_de_son_dossier(tmp_path, monkeypatch, nom):
    """Le nom de fichier vient du client et sert à construire un chemin.

    `transcribe()` refuse déjà tout ce qui n'est pas une extension connue, donc rien de
    dangereux n'arrive jusqu'ici — mais cette sécurité est un effet de bord de la
    validation, pas une intention. Elle doit tenir même appelée seule.
    """
    from app import journal

    monkeypatch.setenv("JOURNAL", "true")
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    get_config.cache_clear()

    journal.noter_vocal(b"\x00\x01", nom, "Une transcription quelconque.")

    ecrits = [c for c in tmp_path.rglob("*") if c.is_file()]
    assert ecrits, "le journal n'a rien écrit"
    for chemin in ecrits:
        assert tmp_path in chemin.resolve().parents
        assert "evil" not in chemin.name
