"""Aplatit IBM Plex Sans en instances statiques, une par graisse.

Pourquoi : Chromium **n'embarque pas les polices variables** dans le PDF qu'il
produit. Il ne se rabat pas sur une autre police pour autant — il dessine les
lettres en courbes, si bien que le document reste visuellement exact. Mais son
texte n'est alors ni sélectionnable ni cherchable, et sur un devis c'est
l'adresse du client, les désignations et les mentions légales qui deviennent
des images.

Le sans porte tout ce texte-là. Il n'a qu'un axe (`wght`), donc l'aplatir donne
de vraies polices statiques, que Chromium embarque : le texte redevient du texte.

Source Serif 4 reste variable, délibérément. Il porte en plus un axe optique
(`opsz`) que le handoff demande explicitement, et qui adapte le dessin à la
taille — c'est visible entre un titre à 30 px et un intertitre à 15 px. Le figer
pour gagner la sélection de six titres serait un mauvais échange : ces titres,
personne ne les copie.

Usage — les dépendances sont éphémères, rien ne s'ajoute au projet :

    uv run --with "fonttools[woff]" --with brotli python scripts/aplatir_polices.py

Les fichiers produits sont commités : la génération n'a pas à tourner au
déploiement.
"""

from __future__ import annotations

from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

POLICES = Path(__file__).resolve().parent.parent / "app" / "static" / "fonts"

# Les graisses réellement utilisées, et rien de plus : chaque fichier en trop est
# du poids inliné dans chaque PDF rendu.
GRAISSES = (400, 500, 600)
SOUS_JEUX = ("latin", "latin-ext")


def aplatir(source: Path, graisse: int, destination: Path) -> None:
    police = TTFont(source)
    instancer.instantiateVariableFont(police, {"wght": graisse}, inplace=True, updateFontNames=True)
    police.flavor = "woff2"
    police.save(destination)


def main() -> None:
    for sous_jeu in SOUS_JEUX:
        source = POLICES / f"ibm-plex-sans-variable-{sous_jeu}.woff2"
        if not source.exists():
            raise SystemExit(
                f"Source introuvable : {source.name}. Récupère la police variable depuis "
                "fonts.gstatic.com avant d'aplatir (voir fonts/LICENCE.md)."
            )
        for graisse in GRAISSES:
            cible = POLICES / f"ibm-plex-sans-{graisse}-{sous_jeu}.woff2"
            aplatir(source, graisse, cible)
            print(f"{cible.name:38} {cible.stat().st_size:>7} o")


if __name__ == "__main__":
    main()
