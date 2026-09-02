# Devis Vocal — démonstrateur

> **Tu rejoins le projet ?** Commence par [CONTRIBUTING.md](CONTRIBUTING.md) : setup en
> dix minutes sans aucune clé API, découpage du travail, conventions.

Un artisan dépose une note vocale décrivant son chantier. Il récupère un devis PDF
propre et envoyable, en français, conforme aux mentions obligatoires.

> **C'est une démo, pas un produit.** Elle sert à montrer le concept à des artisans en
> rendez-vous pour valider qu'ils paieraient. Le périmètre est volontairement réduit au
> chemin nominal — voir `CLAUDE.md`.

## Démarrer

Aucune dépendance système, aucun droit administrateur, aucun Docker. Fonctionne à
l'identique sur Windows, macOS et Linux.

```bash
git clone https://github.com/Totolescroc/devis-vocal && cd devis-vocal

uv venv --python 3.12                    # https://docs.astral.sh/uv/ si uv manque
uv pip install -r requirements.txt
uv run playwright install chromium       # ~150 Mo, une seule fois

cp .env.example .env                     # laisser USE_FIXTURES=true pour commencer
uv run python -m uvicorn app.main:app --port 8000
```

Puis <http://localhost:8000>.

Avec `USE_FIXTURES=true` (la valeur par défaut du `.env.example`), l'application rejoue
des extractions enregistrées : **aucun appel API, aucune clé, aucun euro**. Déplie
« Coller une transcription » et colle le contenu de n'importe quel `tests/fixtures/*.txt`.
C'est le mode de travail pour itérer sur le PDF et le front.

> **Sous Windows, retire `--reload`.** uvicorn bascule alors sur une boucle asyncio
> incapable de lancer un sous-processus, et Chromium — donc le PDF — ne démarre pas :
> l'application s'arrête au démarrage. Pour garder le rechargement à chaud, fais
> redémarrer uvicorn en entier plutôt que par son reloader interne :
>
> ```
> uv run watchfiles "uvicorn app.main:app" app templates
> ```

> Si `uv run uvicorn …` échoue avec « Une stratégie de contrôle d'application a bloqué ce
> fichier », c'est Windows qui bloque le `.exe` du paquet : utiliser
> `uv run python -m uvicorn …`, comme ci-dessus.

## Passer en réel

Deux clés à mettre dans le `.env`, puis `USE_FIXTURES=false` :

| Variable | Où l'obtenir | Coût |
|---|---|---|
| `GROQ_API_KEY` | <https://console.groq.com> — inscription Google/GitHub, sans carte bancaire | gratuit |
| `ANTHROPIC_API_KEY` | <https://console.anthropic.com> — pas de tier gratuit | 5 € de crédit ≈ plusieurs centaines de devis |

### Avec la clé que tu as déjà

Anthropic est la référence, mais rien n'y oblige. Quasiment toutes les API de modèles
parlent le format OpenAI, donc trois variables suffisent à en brancher une que le projet
ne connaît pas — Mistral, DeepSeek, OpenRouter, xAI, ou un modèle local sous Ollama :

```bash
STRUCTURATION_PROVIDER=autre
STRUCTURATION_BASE_URL=https://api.mistral.ai/v1
STRUCTURATION_API_KEY=...
MODEL_STRUCTURATION_AUTRE=mistral-large-latest
```

Pour les fournisseurs que le projet connaît déjà, **la clé suffit** : `STRUCTURATION_PROVIDER`
se déduit de ce qui est renseigné. Poser `OPENAI_API_KEY` et rien d'autre chiffre chez
OpenAI. On ne le précise que pour trancher quand plusieurs clés cohabitent — typiquement
une `GROQ_API_KEY` présente pour la transcription alors qu'Anthropic doit chiffrer, ce
que la déduction fait déjà dans le bon sens. Le moteur retenu est annoncé sur `/health`
et affiché dans le bandeau : déduit ne veut pas dire invisible.

Même chose pour l'audio avec `TRANSCRIPTION_BASE_URL` et `TRANSCRIPTION_API_KEY` —
laissées vides, elles retombent sur Groq, dont le Whisper est gratuit et difficile à
battre. Le `.env.example` liste les URL des fournisseurs courants.

Un fournisseur qui ne sait pas imposer un schéma JSON bascule tout seul sur un mode moins
contraint, avec un avertissement dans les logs. La sortie reste validée par Pydantic,
donc un devis faux échoue au lieu de passer — mais il échouera plus souvent. Avant de
faire confiance à un nouveau fournisseur devant un artisan, mesure-le :

```bash
uv run python scripts/comparer.py anthropic autre
```

Renseigner aussi les variables `ENTREPRISE_*` **avant chaque rendez-vous** : un devis au
nom de l'artisan qu'on a en face, avec son vrai SIRET, est le meilleur argument du
produit. Huit des dix lignes sont publiques — cherche son entreprise sur
<https://annuaire-entreprises.data.gouv.fr> et tu as le SIRET, le code APE, l'adresse et
de quoi former le n° de TVA. Seuls l'assurance décennale et l'IBAN doivent lui être
demandés.

Les identifiants légaux livrés par défaut sont à zéro, et pas remplis de valeurs
plausibles : un devis dont l'argument est la conformité aux mentions obligatoires ne peut
pas porter un faux SIRET crédible.

## Tests

```bash
uv run pytest              # ne touche à aucune API, ne coûte rien
uv run pytest -m live      # appelle réellement Claude (quelques centimes)
```

## Comment ça marche

Trois étapes, trois endpoints indépendants, **aucun état côté serveur** : le devis fait
l'aller-retour en JSON entre le navigateur et l'API.

```
note vocale ──▶ POST /api/transcribe ──▶ transcription
                                            │
                            POST /api/devis ▼
                                         devis chiffré (JSON)
                                            │
                              POST /api/pdf ▼
                                         devis.pdf
```

Le découpage du code suit ce pipeline :

| Fichier | Rôle |
|---|---|
| `app/models.py` | **Le contrat.** `DevisExtraction`, `Devis`, et `to_devis()` entre les deux |
| `app/transcription.py` | `transcribe(audio, filename) -> str` — Groq derrière l'interface |
| `app/structuration.py` | `structure(transcript) -> DevisExtraction` — Claude en sortie JSON contrainte |
| `app/prompts/structuration.md` | Le prompt de chiffrage, hors du code. C'est le cœur de la valeur |
| `app/pdf.py` | `render(devis) -> bytes` — Chromium headless via Playwright, et la pagination du document |
| `templates/devis.html` | Le devis A4, en pages : prestations, récapitulatif, mentions |
| `templates/pdf.css` | La feuille A4, reprise du handoff design |
| `app/static/` | Les cinq écrans du parcours, JS vanilla, polices embarquées |
| `app/journal.py` | Garde les vocaux réels des rendez-vous — hors du parcours, désactivé par défaut |

### Ce que fait le navigateur

Le produit s'appelle « Devis Vocal » : l'action principale de l'accueil est de parler, pas
de chercher un fichier. Le dépôt de fichier et le texte collé restent en repli, dépliables.

- **La dictée** s'enregistre dans la page (`MediaRecorder`) et part vers le même
  `POST /api/transcribe` que n'importe quel fichier. Le micro exige un **contexte
  sécurisé** : `localhost` convient, une adresse IP en clair non — pour tester depuis un
  téléphone, il faut un tunnel https. Sans micro, l'écran le dit et déplie le dépôt.
- **Le point qui bat suit la voix**, pas une horloge : un analyseur mesure le niveau en
  continu. Si le pic n'a jamais dépassé le seuil, l'enregistrement n'est pas envoyé — un
  micro coupé se découvrirait sinon au retour de la transcription, trop tard.
- **On réécoute avant d'envoyer.** L'arrêt ne déclenche plus le devis : il propose
  « Établir le devis » ou « Refaire ».
- **Si le chiffrage échoue**, la transcription est conservée et l'écran d'erreur propose de
  rejouer cette seule étape — sans refaire parler l'artisan.
- **L'enregistrement est léger** : mono, 32 kbit/s. Chrome enregistre par défaut quatre
  fois plus gros, alors que Whisper ramène tout en 16 kHz mono avant de transcrire. Ça
  compte quand on téléverse depuis une camionnette.
- **Le bouton final partage** le PDF par la feuille du système (`navigator.share`) quand
  l'appareil sait le faire — donc vers WhatsApp ou les messages, depuis un téléphone — et
  retombe sur un téléchargement partout ailleurs. Le PDF est fabriqué dès l'affichage du
  devis, pendant la relecture : `navigator.share()` doit partir dans la fenêtre
  d'activation du geste, que deux secondes de Chromium laissaient expirer.
- **Un bandeau prévient** quand le chiffrage est rejoué depuis une fixture, ou calculé par
  un moteur de secours plutôt que par le moteur de référence.

### Le journal, seule chose que le serveur garde

Le produit ne stocke rien : le devis fait l'aller-retour en JSON et le serveur l'oublie.
Une exception, désactivée par défaut, à armer avant une tournée de rendez-vous :

```bash
JOURNAL=true
```

Chaque vocal réel est alors conservé avec ce que le modèle en a tiré :

```
journal/2026-08-31/103412-a1b2c3d4e5/
    audio.webm          le vocal tel qu'il a été dicté
    transcription.txt   ce que Whisper en a compris
    extraction.json     ce que le modèle en a tiré
    meta.json           quand, par quels modèles, combien de prix estimés
```

C'est de la matière première, pas du stockage : **rien n'est jamais relu par
l'application**. Ce qui fait progresser le prompt, ce ne sont pas des fonctionnalités,
ce sont de vrais vocaux d'artisans — dictés vite, en camionnette, avec les mots du
métier. On en croise cinq dans une semaine de rendez-vous, et sans trace il n'en reste
rien le lendemain.

Les deux requêtes du pipeline sont indépendantes et le serveur reste sans état : c'est
l'empreinte de la transcription qui rapproche l'audio de son chiffrage.

> Le journal enregistre la voix de quelqu'un. Le dossier est ignoré par git et ne doit
> pas quitter la machine ; le dire à l'artisan avant d'enregistrer n'est pas une option.

### Deux modèles Pydantic, et c'est volontaire

Un LLM se trompe en arithmétique. Un total faux devant un artisan, c'est la démo morte.

Donc le LLM ne calcule rien : il produit un `DevisExtraction` (des lignes, des quantités,
des prix unitaires). C'est `to_devis()`, une fonction Python pure, qui fait les
multiplications, la TVA, l'acompte et les arrondis. C'est aussi elle que testent les tests.

### Les prix estimés sont signalés

Quand l'artisan n'a pas dicté de prix, le modèle en estime un et marque la ligne
`a_valider`. À l'écran, la ligne porte une pastille « prix estimé · à valider » ; sur le
PDF, la mention `estimé †` et une note en pied de tableau. Le dague plutôt qu'une couleur :
c'est ce qui subsiste sur l'imprimante noir et blanc d'un artisan.

C'est délibéré : un devis où tout paraît validé alors que la moitié est devinée, c'est un
devis qu'on envoie sans relire.

### Le design vient d'un handoff, pas d'improvisations

`app/static/tokens.css` est la source unique des couleurs, des polices et des rayons :
aucune valeur de couleur ou de graisse ne s'écrit ailleurs. `app/static/app.css` et
`templates/pdf.css` reprennent les feuilles livrées ; ce qui a été ajouté par-dessus est
regroupé en fin de fichier sous « ajouts au handoff », en français, et se justifie sur
place.

Les trois familles Google (Source Serif 4, IBM Plex Sans, IBM Plex Mono) sont
auto-hébergées dans `app/static/fonts/` et inlinées en base64 dans le PDF : le document
sort identique sans réseau, ce qui est exactement la situation d'une démo chez un artisan.

## Enregistrer une nouvelle fixture

Pour ajouter un cas au mode hors-ligne (un appel API payé une fois, rejoué gratuitement
ensuite) :

```bash
uv run python scripts/enregistrer_fixture.py sdb2 samples/vocal.m4a --audio
```

## Déploiement

```bash
docker compose up --build
```

L'image part de l'image officielle Playwright : Chromium et ses dépendances sont déjà
dedans. Sur Coolify, pointer sur le `Dockerfile`, exposer le port 8000, healthcheck sur
`/health`, et renseigner les variables d'environnement du `.env`.

Docker n'est **pas** nécessaire pour développer.
