# Devis Vocal — consignes de travail

## Ce que c'est

Un **démonstrateur**, pas un produit. Il sert à montrer le concept à des artisans du
bâtiment en rendez-vous, pour valider qu'ils paieraient. Time-box : un week-end.

Critère de réussite : partir d'un vocal réaliste et brouillon de 1 à 2 minutes, et sortir
un PDF qu'on enverrait à un client sans y toucher.

## Périmètre — le chemin nominal, rien d'autre

Dans le scope :

- Dépôt d'un fichier audio, transcription, structuration, chiffrage
- Génération d'un PDF conforme aux mentions obligatoires françaises
- Signalement des prix estimés
- Mode hors-ligne sur fixtures

**Hors scope — ne pas construire :**

comptes / auth / multi-tenant · bibliothèque de prix par artisan · apprentissage des devis
passés · intégration WhatsApp Business API · édition en ligne du devis · envoi par email ·
signature électronique · facturation · paiement · CRM · base de données · file d'attente ·
cache · gestion d'erreurs exhaustive · design poussé du front

**Si une demande déborde de ce périmètre, le signaler au lieu de l'implémenter.**

Le PDF est la seule exception à la sobriété : c'est la vitrine, il doit être impeccable.

## Stack

- **Backend** : Python 3.12, FastAPI
- **Transcription** : API OpenAI (gpt-4o-transcribe), derrière `transcribe(audio, filename) -> str`
- **Structuration** : API OpenAI, sortie JSON contrainte, derrière `structure(transcript) -> DevisExtraction`
- **PDF** : Playwright (Chromium headless) rendant `templates/devis.html`
- **Front** : une page HTML servie par FastAPI, JS vanilla, pas de framework
- **Déploiement** : Docker sur VPS via Coolify. Docker n'est pas requis pour développer.

> Le brief initial imposait WeasyPrint. Il a été remplacé par Playwright parce que
> WeasyPrint exige des libs système natives (GTK, Pango) qui auraient imposé WSL2 ou
> Docker à chaque développeur. Le changement est confiné à `app/pdf.py` ; l'interface
> `render(devis) -> bytes` et le template HTML/CSS sont inchangés.

## Règles d'architecture

**Le LLM ne calcule jamais.** Il produit un `DevisExtraction` : des lignes, des quantités,
des prix unitaires. Toute l'arithmétique (totaux, TVA, acompte, arrondis) est faite par
`to_devis()`, une fonction Python pure. Un total faux devant un artisan tue la démo.

**`app/models.py` est le contrat.** C'est le point de synchronisation entre les deux
développeurs. Toute modification passe par une PR relue à deux.

**Pas de sur-ingénierie.** Pas de base de données, pas de queue, pas de cache. Le devis
fait l'aller-retour en JSON entre le navigateur et l'API ; le serveur ne garde rien.

**Le prompt vit dans un fichier**, `app/prompts/structuration.md`, jamais en dur dans le
code. C'est le cœur de la valeur : c'est là qu'on itère quand un devis sort mal.

**Information absente = `null`**, jamais une invention plausible. Seule exception : les
prix, qui peuvent être estimés mais sont alors systématiquement marqués `a_valider: true`.

## Tests

`uv run pytest` doit passer **sans aucune clé API** : les tests tournent sur les fixtures
textuelles de `tests/fixtures/`. Les tests marqués `@pytest.mark.live` appellent réellement
les API et sont exclus par défaut.

Ce qui est testé, c'est l'arithmétique et le formatage — la seule partie qui doit être
juste à tous les coups. La qualité de compréhension du vocal se juge à l'œil sur le PDF.

## Conventions

- Commits en français, à l'impératif, courts : « ajoute la génération PDF »
- Branches courtes `feat/…` `fix/…`, merge via PR même à deux
- `main` doit toujours être démontrable : rien qui casse le pipeline de bout en bout
- Jamais de secret commité ; chacun sa clé dans son `.env` local
- Code et commentaires en français, comme le domaine

## Découpage à deux

`app/models.py` figé en premier, ensemble. Ensuite :

- **Dev A** — `transcription.py`, `structuration.py`, le prompt, `to_devis()`, les fixtures
- **Dev B** — `pdf.py`, `templates/`, `app/static/` — travaille sur les fixtures, donc sans clé API
