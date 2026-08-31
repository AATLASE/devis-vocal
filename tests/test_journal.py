"""Le journal de démonstration.

Ce qu'on protège ici : qu'il soit muet tant qu'on ne l'a pas armé — il enregistre la
voix de quelqu'un —, qu'il rapproche l'audio de son chiffrage sans que le serveur ait
retenu quoi que ce soit entre deux requêtes indépendantes, et qu'une panne de sa part
ne coûte jamais un devis à l'artisan qui est en face.

Aucune clé API : le journal ne fait que de l'écriture de fichiers.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from app import journal
from app.config import get_config
from app.models import DevisExtraction

from tests.test_structuration import noms_fixtures
from tests.test_pdf import FIXTURES

INSTANT = datetime(2026, 8, 31, 10, 34, 12)
VOCAL = "Alors la salle de bain de madame Durand, 12 rue des Lilas."


@pytest.fixture
def journal_arme(tmp_path, monkeypatch):
    get_config.cache_clear()
    monkeypatch.setenv("JOURNAL", "true")
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    yield tmp_path
    get_config.cache_clear()


@pytest.fixture
def journal_eteint(tmp_path, monkeypatch):
    get_config.cache_clear()
    monkeypatch.setenv("JOURNAL", "false")
    monkeypatch.setenv("JOURNAL_DIR", str(tmp_path))
    yield tmp_path
    get_config.cache_clear()


def extraction_de(nom: str) -> DevisExtraction:
    return DevisExtraction.model_validate_json((FIXTURES / f"{nom}.json").read_text(encoding="utf-8"))


def dossiers(racine):
    return sorted(p for p in racine.rglob("*") if p.is_dir() and "-" in p.name and len(p.name) > 10)


def test_le_journal_est_muet_tant_qu_on_ne_l_a_pas_arme(journal_eteint):
    """Il enregistre la voix d'un artisan : rien ne doit s'écrire par défaut, sur la
    machine d'un développeur comme sur un serveur."""
    journal.noter_vocal(b"des octets", "dictee.webm", VOCAL, maintenant=INSTANT)
    journal.noter_extraction(VOCAL, extraction_de("sdb"), maintenant=INSTANT)

    assert list(journal_eteint.rglob("*")) == []


def test_un_vocal_laisse_son_audio_sa_transcription_et_ses_meta(journal_arme):
    journal.noter_vocal(b"des octets", "dictee.webm", VOCAL, maintenant=INSTANT)

    dossier = journal_arme / "2026-08-31" / f"103412-{journal._cle(VOCAL)}"
    assert (dossier / "audio.webm").read_bytes() == b"des octets"
    assert (dossier / "transcription.txt").read_text(encoding="utf-8") == VOCAL

    meta = json.loads((dossier / "meta.json").read_text(encoding="utf-8"))
    assert meta["octets"] == 10
    assert meta["fichier"] == "dictee.webm"


def test_le_chiffrage_rejoint_le_vocal_dont_il_est_issu(journal_arme):
    """Les deux requêtes sont indépendantes et le serveur ne retient rien entre elles.
    C'est l'empreinte de la transcription qui les rapproche — sans quoi on récolterait
    deux dossiers sans lien pour un seul vocal."""
    journal.noter_vocal(b"des octets", "dictee.webm", VOCAL, maintenant=INSTANT)
    journal.noter_extraction(VOCAL, extraction_de("sdb"), maintenant=INSTANT)

    assert len(dossiers(journal_arme)) == 1
    dossier = dossiers(journal_arme)[0]
    assert (dossier / "audio.webm").exists()
    assert (dossier / "extraction.json").exists()


def test_le_rapprochement_survit_a_la_ponctuation_et_a_la_casse(journal_arme):
    """La transcription revient du navigateur telle qu'elle est partie, mais on ne veut
    pas qu'un espace en trop ouvre un second dossier."""
    journal.noter_vocal(b"octets", "dictee.webm", VOCAL, maintenant=INSTANT)
    journal.noter_extraction("  Alors la salle de bain de Madame DURAND, 12 rue des Lilas.  ",
                             extraction_de("sdb"), maintenant=INSTANT)

    assert len(dossiers(journal_arme)) == 1


def test_une_transcription_collee_ouvre_son_propre_dossier(journal_arme):
    """Sans vocal — texte collé dans le repli — il n'y a pas d'audio, mais l'extraction
    reste utile pour travailler le prompt."""
    journal.noter_extraction(VOCAL, extraction_de("peinture"), maintenant=INSTANT)

    dossier = dossiers(journal_arme)[0]
    assert not list(dossier.glob("audio.*"))
    assert (dossier / "transcription.txt").read_text(encoding="utf-8") == VOCAL
    assert (dossier / "extraction.json").exists()


def test_les_meta_disent_ce_qui_a_chiffre_et_combien_de_prix_sont_estimes(journal_arme):
    """C'est ce qu'on relit entre deux rendez-vous : quel moteur tournait, et quelle
    part du devis reposait sur des prix devinés."""
    journal.noter_vocal(b"octets", "dictee.webm", VOCAL, maintenant=INSTANT)
    journal.noter_extraction(VOCAL, extraction_de("sdb"), maintenant=INSTANT)

    meta = json.loads((dossiers(journal_arme)[0] / "meta.json").read_text(encoding="utf-8"))
    assert meta["fournisseur"] in {"anthropic", "groq", "openai"}
    assert meta["lignes"] == 7
    assert meta["lignes_estimees"] == 7   # sdb : aucun prix n'est dicté
    assert "modele_transcription" in meta   # les meta du vocal ne sont pas écrasées


@pytest.mark.parametrize("nom", noms_fixtures())
def test_toutes_les_fixtures_se_journalisent(journal_arme, nom):
    journal.noter_extraction(f"transcription de {nom}", extraction_de(nom), maintenant=INSTANT)
    assert len(dossiers(journal_arme)) == 1


def test_un_journal_en_panne_ne_coute_pas_un_devis(journal_arme, monkeypatch):
    """Le journal est un observateur. S'il ne peut pas écrire — disque plein, dossier
    en lecture seule — l'artisan en face ne doit rien en savoir."""
    def refuser(*a, **k):
        raise OSError("disque plein")

    monkeypatch.setattr(journal.Path, "mkdir", refuser)

    journal.noter_vocal(b"octets", "dictee.webm", VOCAL, maintenant=INSTANT)
    journal.noter_extraction(VOCAL, extraction_de("sdb"), maintenant=INSTANT)
    # Aucune exception n'est remontée : c'est tout ce qui compte.
