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
    monkeypatch.setenv("GROQ_API_KEY", "")
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
        with pytest.raises(TranscriptionError, match="GROQ_API_KEY"):
            transcribe(b"x" * 100, nom)


def test_la_majuscule_dans_l_extension_ne_gene_pas():
    with pytest.raises(TranscriptionError, match="GROQ_API_KEY"):
        transcribe(b"x" * 100, "VOCAL.M4A")


@pytest.mark.skipif(not (SAMPLES / "vocal_synthese.wav").exists(), reason="échantillon absent")
def test_l_echantillon_de_synthese_passe_les_controles():
    audio = (SAMPLES / "vocal_synthese.wav").read_bytes()
    with pytest.raises(TranscriptionError, match="GROQ_API_KEY"):
        transcribe(audio, "vocal_synthese.wav")
