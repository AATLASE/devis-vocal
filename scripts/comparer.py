"""Compare les fournisseurs de chiffrage sur les mêmes transcriptions.

    uv run python scripts/comparer.py anthropic openai groq
    uv run python scripts/comparer.py groq --fixtures sdb elec

Chaque fournisseur reçoit exactement le même prompt et les mêmes transcriptions ; le
résultat est confronté aux extractions de référence de `tests/fixtures/*.json`.

La colonne qui compte n'est pas le total, c'est le **découpage dicté / estimé** :

- Les lignes dont l'artisan a dicté le prix doivent ressortir au centime près, chez
  tout le monde. Un écart là-dessus est un défaut de compréhension, pas de chiffrage.
- Les lignes estimées sont le seul endroit où la connaissance du marché français joue.
  C'est là que les fournisseurs se séparent — et c'est aussi là que le catalogue de
  prix de l'artisan rendrait la question sans objet.

Chaque exécution coûte de l'argent chez les fournisseurs payants : une passe complète,
c'est un appel par fixture et par fournisseur.
"""

from __future__ import annotations

import io
import os
import sys
from decimal import Decimal
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RACINE))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app.config import get_config  # noqa: E402
from app.models import DevisExtraction  # noqa: E402
from app.structuration import StructurationError, structure  # noqa: E402

FIXTURES = RACINE / "tests" / "fixtures"
# `autre` désigne ce qui est branché sur STRUCTURATION_BASE_URL — c'est ainsi qu'on
# mesure un fournisseur inconnu contre la référence avant de lui faire confiance.
FOURNISSEURS = ("anthropic", "openai", "groq", "autre")


def total(extraction: DevisExtraction, *, estimees: bool | None = None) -> Decimal:
    """Total HT, restreint aux lignes estimées (True) ou dictées (False)."""
    return sum(
        (
            Decimal(str(l.quantite)) * Decimal(str(l.prix_unitaire_ht))
            for l in extraction.lignes
            if estimees is None or l.a_valider is estimees
        ),
        Decimal("0"),
    )


def ecart(obtenu: Decimal, reference: Decimal) -> str:
    if reference == 0:
        return "  —  " if obtenu == 0 else "  n/a"
    return f"{(obtenu - reference) / reference * 100:+5.0f}%"


def basculer(fournisseur: str) -> None:
    os.environ["USE_FIXTURES"] = "false"
    os.environ["STRUCTURATION_PROVIDER"] = fournisseur
    get_config.cache_clear()


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    demandes = [a for a in args if a in FOURNISSEURS] or list(FOURNISSEURS)

    if "--fixtures" in sys.argv:
        noms = args[args.index(sys.argv[sys.argv.index("--fixtures") + 1]) :]
        noms = [n for n in noms if (FIXTURES / f"{n}.json").exists()]
    else:
        noms = sorted(p.stem for p in FIXTURES.glob("*.json"))

    if not noms:
        print("Aucune fixture à comparer.")
        return 1

    print(f"{len(noms)} fixtures × {len(demandes)} fournisseurs = "
          f"{len(noms) * len(demandes)} appels.\n")

    for nom in noms:
        transcript = (FIXTURES / f"{nom}.txt").read_text(encoding="utf-8")
        ref = DevisExtraction.model_validate_json((FIXTURES / f"{nom}.json").read_text(encoding="utf-8"))

        dictees_ref, estimees_ref = total(ref, estimees=False), total(ref, estimees=True)
        print(f"── {nom} ── référence : {total(ref):>9} HT "
              f"({len(ref.lignes)} lignes, dont {sum(1 for l in ref.lignes if l.a_valider)} estimées)")
        print(f"   {'fournisseur':<12} {'lignes':>6} {'total HT':>10} {'écart':>7} "
              f"{'dicté':>10} {'écart':>7} {'estimé':>10} {'écart':>7}")

        for fournisseur in demandes:
            basculer(fournisseur)
            try:
                got = structure(transcript)
            except StructurationError as err:
                print(f"   {fournisseur:<12} — {str(err)[:70]}")
                continue

            print(f"   {fournisseur:<12} {len(got.lignes):>6} "
                  f"{total(got):>10} {ecart(total(got), total(ref)):>7} "
                  f"{total(got, estimees=False):>10} {ecart(total(got, estimees=False), dictees_ref):>7} "
                  f"{total(got, estimees=True):>10} {ecart(total(got, estimees=True), estimees_ref):>7}")
        print()

    print("Lecture : la colonne « dicté » devrait être à 0 % partout — c'est de la")
    print("compréhension, pas du jugement. Les écarts sur « estimé » sont le vrai sujet.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
