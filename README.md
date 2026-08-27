# Devis Vocal — démonstrateur

Un artisan dépose une note vocale décrivant son chantier. Il récupère un devis PDF
propre et envoyable, en français, conforme aux mentions obligatoires.

> **C'est une démo, pas un produit.** Elle sert à montrer le concept à des artisans en
> rendez-vous pour valider qu'ils paieraient. Le périmètre est volontairement réduit au
> chemin nominal — voir `CLAUDE.md`.

## Démarrer

Aucune dépendance système, aucun droit administrateur, aucun Docker. Fonctionne à
l'identique sur Windows, macOS et Linux.

```bash
git clone <url-du-repo> && cd devis-vocal

uv venv --python 3.12                    # https://docs.astral.sh/uv/ si uv manque
uv pip install -r requirements.txt
uv run playwright install chromium       # ~150 Mo, une seule fois

cp .env.example .env                     # laisser USE_FIXTURES=true pour commencer
uv run python -m uvicorn app.main:app --reload --port 8000
```

Puis <http://localhost:8000>.

Avec `USE_FIXTURES=true` (la valeur par défaut du `.env.example`), l'application rejoue
des extractions enregistrées : **aucun appel API, aucune clé, aucun euro**. Déplie
« Coller une transcription » et colle le contenu de n'importe quel `tests/fixtures/*.txt`.
C'est le mode de travail pour itérer sur le PDF et le front.

> Si `uv run uvicorn …` échoue avec « Une stratégie de contrôle d'application a bloqué ce
> fichier », c'est Windows qui bloque le `.exe` du paquet : utiliser
> `uv run python -m uvicorn …`, comme ci-dessus.

## Passer en réel

Deux clés à mettre dans le `.env`, puis `USE_FIXTURES=false` :

| Variable | Où l'obtenir | Coût |
|---|---|---|
| `GROQ_API_KEY` | <https://console.groq.com> — inscription Google/GitHub, sans carte bancaire | gratuit |
| `ANTHROPIC_API_KEY` | <https://console.anthropic.com> — pas de tier gratuit | 5 € de crédit ≈ plusieurs centaines de devis |

Renseigner aussi les variables `ENTREPRISE_*` : un devis au nom de l'artisan qu'on a en
face de soi est nettement plus convaincant qu'un devis « Bâti Rénov ».

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
| `app/pdf.py` | `render(devis) -> bytes` — Chromium headless via Playwright |
| `templates/devis.html` | La mise en page du devis |

### Deux modèles Pydantic, et c'est volontaire

Un LLM se trompe en arithmétique. Un total faux devant un artisan, c'est la démo morte.

Donc le LLM ne calcule rien : il produit un `DevisExtraction` (des lignes, des quantités,
des prix unitaires). C'est `to_devis()`, une fonction Python pure, qui fait les
multiplications, la TVA, l'acompte et les arrondis. C'est aussi elle que testent les tests.

### Les prix estimés sont signalés

Quand l'artisan n'a pas dicté de prix, le modèle en estime un et marque la ligne
`a_valider`. Elle porte alors un astérisque sur le PDF, avec la mention « prix estimé, à
confirmer ». C'est délibéré : un devis où tout paraît validé alors que la moitié est
devinée, c'est un devis qu'on envoie sans relire.

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
