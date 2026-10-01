# Travailler sur ce projet

Bienvenue. Ce document est fait pour que tu sois productif en dix minutes sans avoir à
poser de question.

## En dix minutes, sans aucune clé API

```bash
git clone https://github.com/Totolescroc/devis-vocal
cd devis-vocal

uv venv --python 3.12                    # https://docs.astral.sh/uv/ si uv manque
uv pip install -r requirements.txt
uv run playwright install chromium       # ~150 Mo, une seule fois

cp .env.example .env                     # ne rien modifier pour l'instant
uv run python -m uvicorn app.main:app
```

<http://localhost:8000> — déplie « Coller une transcription », colle le contenu de
n'importe quel `tests/fixtures/*.txt`, et tu as le parcours complet jusqu'au PDF.

Aucune dépendance système à installer, pas de Docker, pas de WSL, pas de droits admin.
Windows, macOS et Linux se comportent pareil.

> **Sous Windows, retire `--reload`.** uvicorn bascule alors sur une boucle asyncio
> incapable de lancer un sous-processus, et Chromium — donc le PDF — ne démarre pas :
> l'application s'arrête au démarrage. Pour garder le rechargement à chaud, fais
> redémarrer uvicorn en entier plutôt que par son reloader interne :
>
> ```
> uv run watchfiles "uvicorn app.main:app" app templates
> ```

> Sous Windows, si `uv run uvicorn …` échoue avec « Une stratégie de contrôle
> d'application a bloqué ce fichier », utilise `uv run python -m uvicorn …`.

### Pourquoi ça marche sans clé

`.env.example` livre `USE_FIXTURES=true`. Dans ce mode, l'étape de chiffrage ne va pas
voir Claude : elle rejoue des extractions déjà enregistrées dans `tests/fixtures/*.json`.

Tu peux donc développer indéfiniment, gratuitement, hors ligne. **La quasi-totalité du
travail sur le PDF et le front se fait dans ce mode.** Ne passe en réel que pour vérifier
la qualité de compréhension d'un vocal.

```bash
uv run pytest        # tourne sans clé, ne coûte rien
```

## Comprendre le projet en une minute

Trois étapes, trois endpoints indépendants, **aucun état côté serveur** : le devis fait
l'aller-retour en JSON entre le navigateur et l'API.

```
note vocale ──▶ POST /api/transcribe ──▶ transcription
                                            │
                            POST /api/devis ▼
                                         Devis chiffré (JSON)
                                            │
                              POST /api/pdf ▼
                                         devis.pdf
```

| Fichier | Rôle |
|---|---|
| `app/models.py` | **Le contrat.** `DevisExtraction`, `Devis`, `to_devis()` |
| `app/transcription.py` | `transcribe(audio, filename) -> str` |
| `app/structuration.py` | `structure(transcript) -> DevisExtraction` |
| `app/prompts/structuration.md` | Le prompt de chiffrage. Le cœur de la valeur |
| `app/pdf.py` | `render(devis) -> bytes` |
| `templates/devis.html` | Le devis A4, en pages |
| `templates/pdf.css` | La feuille A4 du handoff design |
| `app/static/` | Les quatre écrans, JS vanilla · `tokens.css` fait foi pour toute valeur visuelle |

## Les deux règles qui comptent

### 1. Le LLM ne calcule jamais

Un modèle de langage se trompe en arithmétique. Un total faux sur un devis montré à un
artisan, et la démo est morte — il n'ira pas plus loin.

Donc le LLM produit un `DevisExtraction` : des désignations, des quantités, des prix
unitaires. Rien d'autre. C'est `to_devis()`, une fonction Python pure, qui multiplie,
applique la TVA, calcule l'acompte et arrondit.

**Si tu es tenté de demander un total au modèle, c'est un bug.**

### 2. `app/models.py` est le contrat entre nous deux

C'est le seul fichier que nous touchons tous les deux. Une modification de `Devis` casse
l'autre moitié du projet.

**Toute modification de `models.py` passe par une PR qu'on relit à deux.** Le reste, on
peut le faire filer.

## Qui fait quoi

Le découpage naturel, et la raison pour laquelle il tient : l'interface entre les deux est
`Devis`, et elle est déjà figée.

| | Fichiers | Clé API nécessaire |
|---|---|---|
| **Compréhension du vocal** | `transcription.py`, `structuration.py`, `prompts/`, `models.py`, fixtures | oui, pour itérer sur le prompt |
| **Rendu** | `pdf.py`, `templates/`, `app/static/` | **non** — tout se fait sur fixtures |

Prends celui des deux qui t'attire. Si tu prends le rendu, tu n'as strictement besoin
d'aucune clé.

## Le périmètre — c'est une démo

L'objectif n'est pas un produit, c'est une preuve à montrer à des artisans en rendez-vous.
Time-box : un week-end.

**Ne pas construire :** comptes / auth · bibliothèque de prix par artisan · apprentissage
des devis passés · WhatsApp Business API · édition en ligne du devis · envoi par email ·
signature électronique · facturation · paiement · CRM · base de données · file d'attente ·
cache · gestion d'erreurs exhaustive · design poussé du front.

Une seule exception à la sobriété : **le PDF**. C'est la vitrine, il doit être impeccable.

Si une idée te semble déborder du périmètre, dis-le plutôt que de la coder.

## Conventions

- **Commits en français, à l'impératif, courts** : « ajoute la génération PDF »
- **Branches courtes** : `feat/…`, `fix/…`. Merge via PR, même à deux — c'est un point de
  relecture rapide, pas de la bureaucratie. Auto-merge autorisé si l'autre n'est pas dispo.
- **`main` doit toujours être démontrable.** On ne merge rien qui casse le pipeline de bout
  en bout. C'est la règle qui compte le plus : on doit pouvoir montrer le projet à tout moment.
- **Code et commentaires en français**, comme le domaine.
- **Jamais de secret commité.** `.env` est dans `.gitignore` et doit y rester.

## Recettes

### Travailler la mise en page du PDF

Le plus rapide n'est pas de regénérer un PDF à chaque essai, mais d'ouvrir le HTML dans un
onglet :

```bash
uv run python -c "
from datetime import datetime
from pathlib import Path
from app.config import get_config
from app.models import DevisExtraction, to_devis
from app.pdf import render_html

ex = DevisExtraction.model_validate_json(Path('tests/fixtures/sdb.json').read_text(encoding='utf-8'))
d = to_devis(ex, entreprise=get_config().entreprise, maintenant=datetime(2026,8,27,14,32))
Path('apercu.html').write_text(render_html(d), encoding='utf-8')
"
```

Puis ouvre `apercu.html` et utilise l'aperçu avant impression du navigateur (Ctrl+P) :
c'est exactement le moteur qui produit le PDF final. `apercu.html` est ignoré par git.

### Ajouter un cas de test

Chaque fixture est une paire `nom.txt` (la transcription) + `nom.json` (l'extraction).
Les tests et le mode hors-ligne s'appuient sur les deux.

Avec une clé OpenAI — un appel payé une fois, rejoué gratuitement ensuite :

```bash
uv run python scripts/enregistrer_fixture.py mon_cas chemin/vers/transcription.txt
```

Sans clé : écris les deux fichiers à la main. `uv run pytest` vérifiera que l'extraction
est valide et que les totaux tombent juste.

### Itérer sur le prompt sans exploser le budget

Le prompt vit dans `app/prompts/structuration.md`, jamais en dur dans le code.

Pour les essais, baisse le modèle dans ton `.env` :

```
MODEL_STRUCTURATION_OPENAI=gpt-5-mini
```

Repasse sur `gpt-5` pour juger la qualité réelle : c'est lui qui tourne en démo.

## Passer en réel

Une seule clé dans le `.env`, puis `USE_FIXTURES=false` :

| Variable | Où | Coût |
|---|---|---|
| `OPENAI_API_KEY` | <https://platform.openai.com/api-keys> | facturé à l'usage |

Un abonnement ChatGPT ne donne **pas** accès à l'API : ce sont deux produits facturés
séparément.
