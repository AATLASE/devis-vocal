# Polices embarquées

Les trois familles du design sont auto-hébergées plutôt que chargées depuis le CDN
Google Fonts. Raison : le PDF est rendu par Chromium côté serveur et la démo peut
tourner hors-ligne (`USE_FIXTURES=true`). Une police manquante au moment du rendu
ferait retomber le document sur Georgia et system-ui — plus le design livré.

Sous-jeux **latin** et **latin-ext**. Le `†` des lignes estimées vit en U+2020,
donc dans latin-ext : le retirer ferait un carré vide sur le devis.

## Variable ou statique — ce n'est pas un détail

Chromium **n'embarque pas les polices variables** dans le PDF qu'il produit. Il ne
se rabat pas sur une autre police pour autant : il dessine les lettres en courbes.
Le document reste donc visuellement exact, mais son texte cesse d'être du texte —
ni sélection, ni recherche, ni copier-coller.

D'où deux traitements différents :

| Famille | Forme | Pourquoi |
| --- | --- | --- |
| **IBM Plex Sans** | aplatie en 400 / 500 / 600 | Elle porte tout le texte du devis : adresse du client, désignations, observations, mentions légales. C'est celui-là qu'on veut pouvoir copier. Un seul axe (`wght`), donc l'aplatir ne coûte rien. |
| **IBM Plex Mono** | statique d'origine (400, 500) | Google la sert déjà en statique. Les chiffres et les libellés sortent en vrai texte. |
| **Source Serif 4** | **laissée variable** | Elle porte en plus l'axe optique `opsz`, que le handoff demande explicitement et qui adapte le dessin à la taille — visible entre « DEVIS » à 30 px et un titre courant à 15 px. Ses six titres sortent en courbes ; personne ne copie « DEVIS ». L'échange est le bon. |

L'aplatissement se rejoue avec `scripts/aplatir_polices.py`. Les fichiers produits
sont commités : rien ne tourne au déploiement. `tests/test_pdf.py` vérifie que le
sans et le mono sont bien embarqués, pour qu'on ne repasse pas en variable sans
s'en apercevoir.

## Origine et licences

Fichiers `.woff2` récupérés depuis `fonts.gstatic.com`.

| Famille | Licence |
| --- | --- |
| Source Serif 4 | SIL Open Font License 1.1 |
| IBM Plex Sans | SIL Open Font License 1.1 |
| IBM Plex Mono | SIL Open Font License 1.1 |

L'OFL autorise la redistribution avec le logiciel. Texte intégral :
<https://openfontlicense.org/open-font-license-official-text/>

Sources amont : <https://github.com/adobe-fonts/source-serif> ·
<https://github.com/IBM/plex>
