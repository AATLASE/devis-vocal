"""Isole la suite de tests du poste sur lequel elle tourne.

`CLAUDE.md` demande que `uv run pytest` passe sans la moindre clé. La règle était
tenue à l'envers : elle passait tant que le `.env` restait vide, et tombait dès qu'un
développeur armait Firebase sur sa machine — huit tests qui affirment « sans
identifiants, l'authentification est inactive » lisaient les identifiants du poste.

Un test qui dépend du `.env` ne teste pas le produit, il teste la machine. On coupe
donc les deux canaux par lesquels la configuration s'infiltre :

1. le fichier `.env`, que `pydantic-settings` lit directement — neutralisé une fois
   pour toutes en retirant `env_file` des deux classes de réglages ;
2. les vraies variables d'environnement, qui existent en déploiement (Coolify les
   injecte) et sur le poste de qui en exporte — effacées avant chaque test.

Ce qui reste, ce sont les valeurs par défaut écrites dans `app/config.py` : le seul
état que le projet s'engage à faire marcher partout. Un test qui a besoin d'autre
chose le déclare lui-même avec `monkeypatch.setenv`, et le voit dans son propre code.

Un troisième canal s'y ajoute depuis que les plafonds de dépense sont écrits sur
disque : le fichier de compteurs. Laissé à sa valeur par défaut, la suite écrirait
dans le dépôt et les tests se contamineraient — le plafond atteint par l'un ferait
échouer le suivant, avec un message sans aucun rapport.
"""

from __future__ import annotations

import os

import pytest

from app import securite
from app.config import Config, EntrepriseSettings, get_config

# Fait à l'import du conftest, donc avant la collecte : un `get_config()` appelé au
# chargement d'un module de test verrait déjà la configuration nue.
Config.model_config["env_file"] = None
EntrepriseSettings.model_config["env_file"] = None

# Les préfixes sous lesquels le projet range ses réglages. `_PRÉFIXES` plutôt qu'une
# liste nominative : une variable ajoutée demain à `Config` serait sinon oubliée ici,
# et la fuite reviendrait sans que personne la voie.
_PREFIXES = ("FIREBASE_", "ENTREPRISE_", "ANTHROPIC_", "GROQ_", "OPENAI_",
             "TRANSCRIPTION_", "STRUCTURATION_")
_AUTRES = ("USE_FIXTURES", "STRUCTURATION_PROVIDER", "JOURNAL", "JOURNAL_DIR",
           "LOG_DIR", "LOG_CONTENU", "LOG_RETENTION_JOURS", "TVA_DEFAUT",
           "VALIDITE_JOURS", "ACOMPTE_PCT", "MAX_UPLOAD_MO", "MAX_GABARIT_KO",
           # Les garde-fous. Un ACCES_CODE exporté sur le poste fermerait la porte
           # pendant les tests qui la croient ouverte, et le plafond du jour d'un
           # développeur ferait échouer la suite d'un autre.
           "ACCES_CODE", "LIMITE_PAR_MINUTE", "DEVIS_PAR_JOUR", "DEVIS_PAR_MOIS",
           "MAX_JSON_KO", "MAX_LIGNES_DEVIS", "RENDUS_SIMULTANES",
           "COMPTEURS_FICHIER")


@pytest.fixture(autouse=True)
def environnement_nu(tmp_path, monkeypatch):
    """Chaque test part de la configuration par défaut, quelle que soit la machine."""
    for nom in list(os.environ):
        majuscule = nom.upper()
        if majuscule.startswith(_PREFIXES) or majuscule in _AUTRES:
            monkeypatch.delenv(nom, raising=False)

    # Les compteurs vont dans le dossier temporaire du test, jamais dans le dépôt.
    monkeypatch.setenv("COMPTEURS_FICHIER", str(tmp_path / "compteurs.json"))

    # Le cache est vidé des deux côtés du test : à l'entrée pour oublier une
    # configuration construite pendant la collecte, à la sortie pour ne pas léguer
    # au suivant celle qu'un `monkeypatch.setenv` vient de fabriquer. Les compteurs
    # vivent dans leur module et suivent la même règle.
    get_config.cache_clear()
    securite.reinitialiser()
    yield
    get_config.cache_clear()
    securite.reinitialiser()
