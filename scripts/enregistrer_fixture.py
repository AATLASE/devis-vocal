"""Fige une extraction sur disque pour alimenter le mode hors-ligne et les tests.

Un appel API payé une fois, rejoué gratuitement ensuite pendant tout le développement.

    python scripts/enregistrer_fixture.py mon_chantier chemin/vers/transcription.txt

Ou directement depuis un fichier audio :

    python scripts/enregistrer_fixture.py mon_chantier samples/vocal.m4a --audio
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_config  # noqa: E402
from app.structuration import enregistrer_fixture, structure  # noqa: E402
from app.transcription import transcribe  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 1

    nom, chemin = sys.argv[1], Path(sys.argv[2])
    depuis_audio = "--audio" in sys.argv

    if get_config().use_fixtures:
        print("USE_FIXTURES=true : passe-le à false pour appeler réellement l'API.")
        return 1

    if depuis_audio:
        print(f"Transcription de {chemin.name}…")
        transcript = transcribe(chemin.read_bytes(), chemin.name)
        print(f"  → {transcript[:100]}…")
    else:
        transcript = chemin.read_text(encoding="utf-8")

    print("Chiffrage…")
    extraction = structure(transcript)
    enregistrer_fixture(nom, transcript, extraction)

    estimees = sum(1 for l in extraction.lignes if l.a_valider)
    print(f"Fixture « {nom} » enregistrée : {len(extraction.lignes)} lignes ({estimees} estimées).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
