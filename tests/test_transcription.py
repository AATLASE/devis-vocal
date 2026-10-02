"""Garde-fous de la transcription — aucun appel API.

Ces messages d'erreur sont ceux que l'artisan lira à l'écran : ils doivent dire quoi faire,
pas seulement que ça a raté.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_config
from app.transcription import TranscriptionError, transcribe

SAMPLES = Path(__file__).parent.parent / "samples"


@pytest.fixture(autouse=True)
def sans_cle(monkeypatch):
    """Aucune clé : on teste les garde-fous, jamais le fournisseur."""
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "openai")
    get_config.cache_clear()
    yield
    get_config.cache_clear()


def test_un_fichier_vide_est_refuse():
    with pytest.raises(TranscriptionError, match="vide"):
        transcribe(b"", "vocal.m4a")


def test_un_format_inconnu_est_refuse_avec_la_liste_des_formats():
    with pytest.raises(TranscriptionError) as err:
        transcribe(b"xxxx", "chantier.pdf")
    assert ".m4a" in str(err.value) and ".ogg" in str(err.value)


def test_un_fichier_sans_extension_est_refuse():
    with pytest.raises(TranscriptionError, match="inconnu"):
        transcribe(b"xxxx", "enregistrement")


def test_un_fichier_trop_lourd_est_refuse_avant_tout_appel(monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MO", "1")
    get_config.cache_clear()
    with pytest.raises(TranscriptionError, match="trop lourd"):
        transcribe(b"x" * 2_000_000, "vocal.m4a")


def test_les_formats_du_telephone_et_de_whatsapp_passent_le_controle():
    """m4a (iPhone), ogg/opus (WhatsApp), webm et mp4 (ce que produit la dictée dans la
    page, selon le navigateur) : ils doivent aller jusqu'au contrôle de clé, donc échouer
    sur la clé manquante et pas sur le format."""
    for nom in ["vocal.m4a", "vocal.ogg", "vocal.opus", "vocal.mp3", "vocal.wav",
                "dictee.webm", "dictee.mp4"]:
        with pytest.raises(TranscriptionError, match="OPENAI_API_KEY"):
            transcribe(b"x" * 100, nom)


def test_la_majuscule_dans_l_extension_ne_gene_pas():
    with pytest.raises(TranscriptionError, match="OPENAI_API_KEY"):
        transcribe(b"x" * 100, "VOCAL.M4A")


def test_la_cle_reclamee_est_celle_du_fournisseur_choisi(monkeypatch):
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "groq")
    get_config.cache_clear()
    with pytest.raises(TranscriptionError, match="GROQ_API_KEY"):
        transcribe(b"x" * 100, "vocal.m4a")


def test_sans_fournisseur_nomme_une_cle_groq_seule_transcrit_chez_groq(monkeypatch):
    monkeypatch.delenv("TRANSCRIPTION_PROVIDER")
    monkeypatch.setenv("GROQ_API_KEY", "g")
    get_config.cache_clear()
    config = get_config()
    assert config.transcription_provider == "groq"
    assert config.transcription_url == config.groq_base_url


def test_sans_fournisseur_nomme_la_cle_openai_l_emporte_partout(monkeypatch):
    """OpenAI est le défaut : quand plusieurs clés cohabitent, c'est lui qui sert."""
    monkeypatch.delenv("TRANSCRIPTION_PROVIDER")
    for variable in ("OPENAI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(variable, "cle")
    get_config.cache_clear()
    config = get_config()
    assert config.transcription_provider == "openai"
    assert config.structuration_provider == "openai"


@pytest.mark.skipif(not (SAMPLES / "vocal_synthese.wav").exists(), reason="échantillon absent")
def test_l_echantillon_de_synthese_passe_les_controles():
    audio = (SAMPLES / "vocal_synthese.wav").read_bytes()
    with pytest.raises(TranscriptionError, match="OPENAI_API_KEY"):
        transcribe(audio, "vocal_synthese.wav")


# ---------------------------------------------------------------------------
# Le choix du fournisseur — TRANSCRIPTION_PROVIDER
# ---------------------------------------------------------------------------


class _FauxClient:
    """Tient la place du SDK : retient avec quoi on l'a construit et appelé."""

    construits: list[dict] = []
    appels: list[dict] = []

    def __init__(self, **options):
        self.construits.append(options)
        self.audio = self
        self.transcriptions = self

    def create(self, **options):
        self.appels.append(options)

        class Reponse:
            text = "  Bonjour, c'est pour la salle de bain.  "

        return Reponse()


@pytest.fixture
def faux_client(monkeypatch):
    from app import transcription

    _FauxClient.construits, _FauxClient.appels = [], []
    monkeypatch.setattr(transcription, "OpenAI", _FauxClient)
    return _FauxClient


def test_par_defaut_une_seule_cle_suffit(monkeypatch, faux_client):
    """Le cas « une seule clé » : sans rien régler, la transcription part chez OpenAI,
    et rien ne part chez Groq — ni la clé ni l'audio."""
    monkeypatch.delenv("TRANSCRIPTION_PROVIDER")
    monkeypatch.setenv("OPENAI_API_KEY", "cle-openai")
    get_config.cache_clear()

    assert transcribe(b"x" * 100, "vocal.m4a") == "Bonjour, c'est pour la salle de bain."
    assert faux_client.construits == [{"api_key": "cle-openai", "base_url": None}]
    assert faux_client.appels[0]["model"] == "gpt-4o-transcribe"
    assert faux_client.appels[0]["language"] == "fr"


def test_avec_groq_la_transcription_part_chez_groq(monkeypatch, faux_client):
    monkeypatch.setenv("TRANSCRIPTION_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "cle-groq")
    monkeypatch.setenv("OPENAI_API_KEY", "cle-openai")   # présente, et pourtant inutile
    get_config.cache_clear()

    assert transcribe(b"x" * 100, "vocal.m4a") == "Bonjour, c'est pour la salle de bain."
    assert faux_client.construits == [
        {"api_key": "cle-groq", "base_url": "https://api.groq.com/openai/v1"}
    ]
    assert faux_client.appels[0]["model"] == "whisper-large-v3-turbo"


def test_un_vocal_whatsapp_est_envoye_sous_un_nom_que_le_fournisseur_accepte(monkeypatch, faux_client):
    """« .opus » n'est pas dans la liste des fournisseurs ; « .ogg » si, et c'est le
    même fichier."""
    monkeypatch.setenv("OPENAI_API_KEY", "cle-openai")
    get_config.cache_clear()

    transcribe(b"x" * 100, "PTT-20260901-WA0003.opus")
    assert faux_client.appels[0]["file"].name == "PTT-20260901-WA0003.ogg"
