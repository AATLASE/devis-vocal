# Polices embarquées

Les trois familles du design sont auto-hébergées plutôt que chargées depuis le CDN
Google Fonts. Raison : le PDF est rendu par Chromium côté serveur et la démo peut
tourner hors-ligne (`USE_FIXTURES=true`). Une police manquante au moment du rendu
ferait retomber le document sur Georgia et system-ui — plus le design livré.

Fichiers `.woff2` récupérés depuis `fonts.gstatic.com`, sous-jeux **latin** et
**latin-ext** (le `†` des lignes estimées vit dans latin-ext, U+2020).

| Famille | Fichiers | Licence |
| --- | --- | --- |
| Source Serif 4 (variable, wght 400–700) | `source-serif-4-*.woff2` | SIL Open Font License 1.1 |
| IBM Plex Sans (variable, wght 400–600) | `ibm-plex-sans-*.woff2` | SIL Open Font License 1.1 |
| IBM Plex Mono (statique, 400 et 500) | `ibm-plex-mono-*.woff2` | SIL Open Font License 1.1 |

L'OFL autorise la redistribution avec le logiciel. Texte intégral :
<https://openfontlicense.org/open-font-license-official-text/>

Sources amont : <https://github.com/adobe-fonts/source-serif> ·
<https://github.com/IBM/plex>
