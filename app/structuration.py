"""Transcription -> devis structuré, via Claude en sortie JSON contrainte.

Interface unique : `structure(transcript) -> DevisExtraction`.

Le schéma est imposé par le SDK à partir du modèle Pydantic (`output_format`), donc la
réponse est validée avant de nous parvenir : pas de parsing JSON à la main, pas de regex,
pas de « et si le modèle rajoutait du texte autour ».
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import anthropic

from app.config import RACINE, get_config
from app.models import DevisExtraction

PROMPT_PATH = Path(__file__).parent / "prompts" / "structuration.md"
FIXTURES = RACINE / "tests" / "fixtures"


class StructurationError(RuntimeError):
    pass


@lru_cache
def _prompt_systeme() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _depuis_fixture(transcript: str) -> DevisExtraction:
    """Mode hors-ligne : rejoue une extraction enregistrée. Zéro appel réseau, zéro euro.

    On apparie sur le début de la transcription pour pouvoir coller n'importe laquelle des
    fixtures dans le navigateur et retrouver son extraction.
    """
    empreinte = " ".join(transcript.split())[:60].lower()
    for chemin in sorted(FIXTURES.glob("*.json")):
        txt = chemin.with_suffix(".txt")
        if txt.exists() and " ".join(txt.read_text(encoding="utf-8").split())[:60].lower() == empreinte:
            return DevisExtraction.model_validate_json(chemin.read_text(encoding="utf-8"))

    disponibles = sorted(p.stem for p in FIXTURES.glob("*.json"))
    raise StructurationError(
        "USE_FIXTURES est actif mais aucune fixture ne correspond à cette transcription. "
        f"Fixtures disponibles : {', '.join(disponibles) or 'aucune'}. "
        "Colle le contenu d'un fichier tests/fixtures/*.txt, ou repasse USE_FIXTURES=false."
    )


def structure(transcript: str) -> DevisExtraction:
    """Transforme une transcription en devis structuré, non chiffré."""
    transcript = transcript.strip()
    if not transcript:
        raise StructurationError("Transcription vide.")

    config = get_config()
    if config.use_fixtures:
        return _depuis_fixture(transcript)

    if not config.anthropic_api_key:
        raise StructurationError(
            "ANTHROPIC_API_KEY absente. Renseigne-la dans le .env, ou passe USE_FIXTURES=true "
            "pour travailler sur les extractions enregistrées."
        )

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    try:
        reponse = client.messages.parse(
            model=config.model_structuration,
            max_tokens=16000,
            system=_prompt_systeme(),
            messages=[{"role": "user", "content": transcript}],
            output_format=DevisExtraction,
        )
    except anthropic.APIError as err:  # clé invalide, crédit épuisé, surcharge...
        raise StructurationError(f"Appel Anthropic en échec : {err}") from err

    extraction = reponse.parsed_output
    if extraction is None:
        raise StructurationError("Le modèle n'a pas renvoyé de devis exploitable.")
    return extraction


def enregistrer_fixture(nom: str, transcript: str, extraction: DevisExtraction) -> None:
    """Fige une extraction sur disque pour alimenter le mode hors-ligne et les tests.

    Utilisé par `scripts/enregistrer_fixtures.py` — un appel API payé une fois, rejoué
    gratuitement ensuite pendant tout le développement du PDF et du front.
    """
    FIXTURES.mkdir(parents=True, exist_ok=True)
    (FIXTURES / f"{nom}.txt").write_text(transcript.strip() + "\n", encoding="utf-8")
    (FIXTURES / f"{nom}.json").write_text(
        json.dumps(extraction.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
