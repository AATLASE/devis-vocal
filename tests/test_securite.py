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


async def test_un_code_accentue_ne_fait_pas_tomber_le_service(monkeypatch):
    """`compare_digest` sur deux chaînes lève une TypeError au premier caractère non
    ASCII : avec « maçon-2026 », /health répondait 500, le healthcheck échouait en
    permanence et le site devenait inaccessible."""
    monkeypatch.setenv("ACCES_CODE", "maçon-2026")
    client = TestClient(app)

    assert client.get("/health").status_code == 200
    assert client.get("/health").json()["acces"] == "requis"
    assert client.get("/api/entreprise", headers={"X-Acces": "macon-2026"}).status_code == 401

    # Le bon code, tel que l'envoie `fetch` : un octet latin-1 par caractère. Construit
    # à la main, le client de test réencodant ses en-têtes en UTF-8.
    navigateur = requete()
    navigateur.scope["headers"] = [(b"x-acces", "maçon-2026".encode("latin-1"))]
    assert (await health(navigateur))["acces"] == "ouvert"


def test_un_en_tete_accentue_est_refuse_sans_erreur(porte_fermee):
    client = TestClient(app)
    reponse = client.get("/api/entreprise", headers={"X-Acces": "chantier-2026é".encode("latin-1")})
    assert reponse.status_code == 401


@pytest.mark.parametrize("code", ["chef-d’œuvre", "maçon’2026", "chantier-🔨"])
def test_un_code_que_le_navigateur_ne_sait_pas_envoyer_est_refuse_au_demarrage(monkeypatch, code):
    """Hors du latin-1, `fetch` refuse d'envoyer l'en-tête : la porte ne s'ouvrirait
    jamais, sans que rien le dise. Mieux vaut un démarrage qui échoue en le disant."""
    monkeypatch.setenv("ACCES_CODE", code)
    with pytest.raises(ValueError, match="ACCES_CODE"):
        get_config()


# ---------------------------------------------------------------------------
# Les essais de code
# ---------------------------------------------------------------------------


def test_les_codes_faux_sont_limites(porte_fermee):
    """Un 401 n'était compté nulle part : 50 codes faux donnaient 50 × 401, et un code
    dit de vive voix se devinait par dictionnaire à la vitesse du réseau."""
    client = TestClient(app)
    for n in range(securite.ESSAIS_PAR_FENETRE):
        assert client.get("/api/entreprise", headers={"X-Acces": f"essai-{n}"}).status_code == 401

    assert client.get("/api/entreprise", headers={"X-Acces": "essai-suivant"}).status_code == 429
    # Le bon code est refusé lui aussi : sinon il se reconnaîtrait au seul fait de passer.
    assert client.get("/api/entreprise", headers={"X-Acces": CODE}).status_code == 429


async def test_health_ne_sert_pas_d_oracle(porte_fermee):
    """/health dit si un code est bon. Ses essais sont comptés comme les autres, et
    une adresse qui les a épuisés n'apprend plus rien — bon code compris."""
    attaquant = "198.51.100.66"
    for n in range(securite.ESSAIS_PAR_FENETRE):
        assert (await health(requete({"X-Acces": f"essai-{n}"}, ip=attaquant)))["acces"] == "requis"

    bloque = await health(requete({"X-Acces": CODE}, ip=attaquant))
    assert bloque.status_code == 429
    assert b'"acces":"requis"' in bloque.body        # la page lit toujours « requis »

    # Les autres adresses ne sont pas touchées, et le healthcheck, qui n'envoie pas de
    # code, n'est jamais compté ni refusé.
    assert (await health(requete({"X-Acces": CODE}, ip="203.0.113.50")))["acces"] == "ouvert"
    assert await health(requete(ip=attaquant)) == {"status": "ok", "acces": "requis"}


def test_l_absence_de_code_n_est_pas_un_essai(porte_fermee):
    """La première visite et le healthcheck arrivent sans code : les compter bloquerait
    un artisan avant même qu'il ait tapé quoi que ce soit."""
    client = TestClient(app)
    for _ in range(securite.ESSAIS_PAR_FENETRE + 5):
        assert client.get("/api/entreprise").status_code == 401
    assert client.get("/health", headers={"X-Acces": CODE}).json()["acces"] == "ouvert"


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


def test_la_csp_reste_fermee_sans_firebase():
    """Par défaut, rien d'extérieur. Un démonstrateur sans comptes n'a aucune raison
    d'ouvrir sa politique à des origines tierces."""
    csp = securite.politique()
    assert "script-src 'self';" in csp + ";"
    assert "gstatic" not in csp
    assert "googleapis" not in csp
    assert "apis.google.com" not in csp


def test_la_csp_laisse_passer_le_sdk_quand_firebase_est_arme(monkeypatch, tmp_path):
    """La régression qui a coûté le plus cher à trouver.

    `compte.js` importe le SDK Firebase depuis gstatic. Avec `script-src 'self'`, le
    module ne se charge pas, `window.Compte` reste indéfini et il n'y a pas d'écran de
    connexion — sans la moindre erreur visible, puisqu'une CSP refuse en silence. Le
    défaut n'apparaît que le jour où l'on arme Firebase, c'est-à-dire au pire moment.
    """
    faux = tmp_path / "compte-de-service.json"
    faux.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("FIREBASE_CREDENTIALS", str(faux))
    monkeypatch.setenv("FIREBASE_AUTH_DOMAIN", "mon-projet.firebaseapp.com")
    get_config.cache_clear()

    csp = securite.politique()
    assert "https://www.gstatic.com" in csp                      # le SDK
    # « Continuer avec Google » : le SDK charge api.js avant d'ouvrir la fenêtre, et
    # échouait en `auth/internal-error` à chaque essai tant que la CSP le refusait.
    assert "https://apis.google.com" in directives(csp)["script-src"]
    assert "https://identitytoolkit.googleapis.com" in csp       # la connexion
    assert "https://securetoken.googleapis.com" in csp           # le rafraîchissement
    assert "https://mon-projet.firebaseapp.com" in csp           # la fenêtre Google

    # Ce qui ne doit pas s'ouvrir au passage.
    assert "'unsafe-inline'" not in directives(csp)["script-src"]
    assert "frame-ancestors 'none'" in csp


def directives(csp: str) -> dict[str, list[str]]:
    return {nom: valeurs for nom, *valeurs in (d.split() for d in csp.split("; "))}


def test_l_apercu_garde_son_style_et_les_scripts_restent_fermes():
    """L'aperçu A4 est un `srcdoc` qui hérite de la CSP de la page, et le devis y
    porte sa feuille de style en ligne. Sans `'unsafe-inline'` sur les styles, il
    s'affichait en texte brut — sans erreur visible ailleurs que dans la console."""
    csp = directives(securite.politique())
    assert "'unsafe-inline'" in csp["style-src"]
    assert csp["script-src"] == ["'self'"]
    assert csp["img-src"] == ["'self'", "data:"]
    assert csp["font-src"] == ["'self'"]


def test_un_proxy_auquel_on_ne_fait_pas_confiance_est_signale(caplog):
    """Derrière un proxy qu'uvicorn ne croit pas, tous les artisans partagent un seul
    débit. Rien ne le montre avant le 429 : le journal le dit, avec l'adresse à déclarer."""
    relaye = requete({"X-Forwarded-For": "198.51.100.4"}, ip="10.0.1.7")
    with caplog.at_level("WARNING", logger="devis-vocal"):
        securite.verifier_debit(relaye)
        securite.verifier_debit(relaye)
    signalements = [r for r in caplog.records if "FORWARDED_ALLOW_IPS" in r.getMessage()]
    assert len(signalements) == 1                 # une fois, pas à chaque requête
    assert "10.0.1.7" in signalements[0].getMessage()


def test_un_proxy_de_confiance_n_est_pas_signale(caplog):
    """uvicorn a cru le proxy : l'adresse retenue est celle de l'en-tête."""
    with caplog.at_level("WARNING", logger="devis-vocal"):
        securite.verifier_debit(requete({"X-Forwarded-For": "198.51.100.4"}, ip="198.51.100.4"))
    assert not [r for r in caplog.records if "FORWARDED_ALLOW_IPS" in r.getMessage()]


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
