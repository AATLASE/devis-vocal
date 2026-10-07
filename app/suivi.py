"""Journalisation d'exécution — ce qui s'est passé, quand, et ce que ça a coûté.

À ne pas confondre avec `app/journal.py`, qui garde les vocaux d'artisans comme matière
première pour retravailler le prompt. Ici on suit le pipeline lui-même : chaque étape,
sa durée, le fournisseur appelé, les tokens consommés, le devis qui en sort, et la pile
complète quand ça casse.

Un seul chemin à connaître : `logs/devis-vocal.log`. À minuit il est archivé sous
`logs/devis-vocal.log.2026-09-01` et un fichier neuf reprend. Le fichier à ouvrir ne
change donc jamais, et la journée d'un rendez-vous reste retrouvable ensuite. Les trente
derniers jours sont gardés, le dossier est ignoré par git.

Tout ce qui va dans le fichier passe aussi par le terminal : pendant une démo, on veut
voir défiler sans changer de fenêtre — et retrouver la même chose le soir.

Les lignes s'écrivent sans caractère exotique (ni flèche, ni symbole monétaire) : sous
Windows la console n'est pas toujours en UTF-8, et un log qui plante à l'écriture ferait
tomber la requête qu'il était censé observer.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from contextlib import contextmanager
from collections.abc import Iterator
from decimal import Decimal
from time import perf_counter
from typing import Any

from app.config import get_config

logger = logging.getLogger("devis-vocal")

# Tarifs Anthropic en dollars par million de tokens. Seul le fournisseur de référence
# figure ici : Groq facture la transcription à la seconde d'audio et non au token, et
# les tarifs OpenAI ne sont pas repris pour ne pas afficher un chiffre faux. Pour les
# modèles absents de cette table, on journalise les tokens sans estimer le coût.
PRIX_PAR_MILLION = {
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}

_configure = False


def configurer() -> None:
    """Branche le fichier et la console. Appelée une fois, au démarrage de l'app."""
    global _configure
    if _configure:
        return

    config = get_config()
    config.log_dir.mkdir(parents=True, exist_ok=True)

    forme = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S")
    # Pas de date dans l'horodatage : le fichier couvre une journée et une seule, elle
    # est déjà dans son nom.

    fichier = logging.handlers.TimedRotatingFileHandler(
        config.log_dir / "devis-vocal.log",
        when="midnight",
        backupCount=config.log_retention_jours,
        encoding="utf-8",
    )
    fichier.setFormatter(forme)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(forme)

    racine = logging.getLogger()
    racine.setLevel(logging.INFO)
    racine.addHandler(fichier)
    racine.addHandler(console)

    # uvicorn tient ses propres handlers et coupe la remontée vers le logger racine :
    # sans ça, les requêtes HTTP et les erreurs de démarrage resteraient à l'écran et
    # ne seraient nulle part le lendemain.
    #
    # Deux loggers, pas trois : `uvicorn.error` n'a pas de handler à lui et remonte à
    # `uvicorn`. Lui donner aussi le fichier écrirait chaque démarrage deux fois.
    for nom in ("uvicorn", "uvicorn.access"):
        logging.getLogger(nom).addHandler(fichier)

    # Le détail des sockets et des retries HTTP n'apprend rien ; la ligne de httpx,
    # elle, donne l'URL et le code de retour de chaque appel API, et on la garde.
    for nom in ("httpcore", "urllib3", "openai", "anthropic", "asyncio"):
        logging.getLogger(nom).setLevel(logging.WARNING)

    _configure = True

    logger.info("--- Devis Vocal demarre ---")
    logger.info(
        "  configuration  mode=%s fournisseur=%s structuration=%s transcription=%s journal=%s",
        "fixtures" if config.use_fixtures else "reel",
        config.structuration_provider,
        config.modele_structuration,
        f"{config.transcription_provider}/{config.modele_transcription_actif}",
        "arme" if config.journal else "off",
    )
    logger.info("  suivi          %s", config.log_dir / "devis-vocal.log")


# ---------------------------------------------------------------------------
# Étapes
# ---------------------------------------------------------------------------

def _valeur(v: Any) -> str:
    if isinstance(v, float):
        return f"{v:.1f}"
    if isinstance(v, Decimal):
        return f"{v:.2f}"
    return str(v)


def _details(details: dict[str, Any]) -> str:
    return "".join(f" {cle}={_valeur(valeur)}" for cle, valeur in details.items() if valeur is not None)


@contextmanager
def etape(nom: str, *, attendu: type[BaseException] | tuple[type[BaseException], ...] = (),
          **details: Any) -> Iterator[dict[str, Any]]:
    """Encadre une étape du pipeline : une ligne à l'entrée, une à la sortie avec sa durée.

    Le dictionnaire cédé sert à joindre à la ligne de sortie ce qu'on ne savait pas en
    entrant — le nombre de caractères transcrits, le poids du PDF.

    `attendu` liste les erreurs *prévues*, celles qu'on renvoie telles quelles à
    l'utilisateur (fichier trop lourd, format refusé). Elles tiennent en une ligne :
    dérouler trente lignes de pile pour un message qu'on a écrit soi-même noierait les
    vraies pannes, qui, elles, arrivent avec leur trace complète.
    """
    logger.info("%s%s", nom, _details(details))
    debut = perf_counter()
    sortie: dict[str, Any] = {}
    try:
        yield sortie
    except attendu as err:  # type: ignore[misc]
        logger.warning("  %s refuse  %.1f s : %s", nom, perf_counter() - debut, err)
        raise
    except Exception:
        logger.exception("  %s EN ECHEC  %.1f s", nom, perf_counter() - debut)
        raise
    logger.info("  %s ok  %.1f s%s", nom, perf_counter() - debut, _details(sortie))


def bloc(titre: str, texte: str) -> None:
    """Recopie un contenu métier (transcription, JSON d'extraction) dans le log, indenté.

    C'est ce qui permet de comprendre un devis sorti de travers sans rien réarmer :
    l'entrée et la sortie du modèle sont là, côte à côte, dans la journée du rendez-vous.
    Coupable de faire grossir le fichier — `LOG_CONTENU=false` l'éteint.
    """
    if not get_config().log_contenu:
        return
    corps = "\n".join("    " + ligne for ligne in texte.strip().splitlines())
    logger.info("  %s (%d caracteres)\n%s", titre, len(texte), corps)


def appel(fournisseur: str, modele: str, entree: int | None = None,
          sortie: int | None = None) -> None:
    """Une ligne par appel facturé : qui, quel modèle, combien de tokens, combien de dollars."""
    details = {"fournisseur": fournisseur, "modele": modele, "tokens_entree": entree,
               "tokens_sortie": sortie}

    prix = PRIX_PAR_MILLION.get(modele)
    if prix and entree is not None and sortie is not None:
        # Formaté ici plutôt que laissé en flottant : un devis coûte quelques
        # millièmes de dollar, et un arrondi à la décimale afficherait 0.0.
        details["cout_usd"] = f"{(entree * prix[0] + sortie * prix[1]) / 1_000_000:.4f}"

    logger.info("  appel%s", _details(details))
