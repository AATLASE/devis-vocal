"""Ce que le fichier de suivi doit garantir.

Le suivi est un observateur : il ne doit ni changer le déroulé d'une requête, ni la
faire tomber. Deux choses se testent quand même ici.

La première est arithmétique — l'estimation du coût d'un appel — et le projet ne
laisse aucun calcul non testé.

La seconde est le tri des erreurs. Une erreur prévue (fichier trop lourd, format
refusé) tient en une ligne ; une panne imprévue arrive avec sa pile complète. Si ce
tri s'inverse, le fichier se remplit de piles pour des messages qu'on a écrits
soi-même, et la vraie panne passe inaperçue au milieu.
"""

from __future__ import annotations

import logging

import pytest

from app import suivi
from app.config import get_config


@pytest.fixture(autouse=True)
def config_neuve():
    get_config.cache_clear()
    yield
    get_config.cache_clear()


@pytest.fixture(autouse=True)
def journalisation(caplog):
    caplog.set_level(logging.INFO, logger="devis-vocal")


def test_une_etape_reussie_donne_son_entree_et_sa_duree(caplog):
    with suivi.etape("structuration", caracteres=656) as detail:
        detail["lignes"] = 4

    entree, sortie = caplog.records
    assert entree.getMessage() == "structuration caracteres=656"
    assert sortie.levelno == logging.INFO
    assert " structuration ok " in sortie.getMessage()
    assert sortie.getMessage().endswith("lignes=4")


def test_une_erreur_prevue_tient_en_une_ligne(caplog):
    with pytest.raises(ValueError):
        with suivi.etape("transcription", attendu=ValueError):
            raise ValueError("Fichier audio vide.")

    echec = caplog.records[-1]
    assert echec.levelno == logging.WARNING
    assert echec.exc_info is None  # pas de pile pour un message qu'on a écrit soi-même
    assert "Fichier audio vide." in echec.getMessage()


def test_une_panne_imprevue_emporte_sa_pile(caplog):
    with pytest.raises(ZeroDivisionError):
        with suivi.etape("structuration", attendu=ValueError):
            raise ZeroDivisionError

    echec = caplog.records[-1]
    assert echec.levelno == logging.ERROR
    assert echec.exc_info is not None


def test_le_cout_dun_appel_suit_le_tarif_du_modele(caplog):
    # claude-opus-5 : 5 $ le million en entrée, 25 $ en sortie.
    suivi.appel("anthropic", "claude-opus-5", 1_000, 2_000)

    assert "cout_usd=0.0550" in caplog.records[-1].getMessage()


def test_un_modele_hors_tarif_journalise_les_tokens_sans_inventer_de_cout(caplog):
    suivi.appel("Groq", "openai/gpt-oss-120b", 1_000, 2_000)

    ligne = caplog.records[-1].getMessage()
    assert "tokens_entree=1000 tokens_sortie=2000" in ligne
    assert "cout_usd" not in ligne


def test_un_appel_sans_tokens_annonce_quand_meme_le_modele(caplog):
    suivi.appel("fixtures", "elec")

    assert caplog.records[-1].getMessage() == "  appel fournisseur=fixtures modele=elec"


def test_le_contenu_metier_se_coupe(monkeypatch, caplog):
    monkeypatch.setenv("LOG_CONTENU", "false")

    suivi.bloc("transcription", "Alors, pour la salle de bain de Mme Ferrand...")

    assert caplog.records == []
