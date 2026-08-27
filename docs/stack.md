> **Note d'exploration, antérieure au code.** Les choix réellement retenus sont documentés
> dans `CLAUDE.md` et `README.md` (Groq pour la transcription, Playwright pour le PDF).

# Stack — pistes

## Speech-to-text

| Option | Note |
|---|---|
| Whisper (API OpenAI ou self-hosted) | Bon en FR, gère le bruit de chantier moyennement |
| Deepgram Nova | Rapide, streaming, tarif au volume |
| AssemblyAI | Bonne diarisation si plusieurs voix |

À tester en conditions réelles : bruit de fond, jargon métier, accents, noms propres.

## Extraction structurée

LLM avec sortie structurée (JSON schema) → lignes de devis.
Anthropic Claude / structured outputs. Le prompt doit avoir accès au catalogue de
prestations de l'utilisateur pour faire le rapprochement (RAG léger ou simple
injection du catalogue s'il est court).

## Génération PDF

- Template HTML + rendu headless (Playwright / Puppeteer)
- Ou une lib PDF native

## Canal d'entrée — trois hypothèses

1. **App mobile / PWA** — contrôle total de l'UX, mais il faut la faire installer.
2. **WhatsApp Business API** — l'artisan envoie un vocal, reçoit le PDF. Zéro
   installation, canal déjà utilisé au quotidien. Piste forte.
3. **Numéro de téléphone** — il appelle et parle à un agent vocal. Le plus naturel,
   le plus coûteux à construire.

## Backend

À définir selon le canal retenu. Contraintes : stockage des audios (RGPD),
données clients de l'artisan = données personnelles de tiers.

## RGPD / légal

- Hébergement des données en UE
- Durée de conservation des enregistrements audio (les supprimer après traitement ?)
- Mentions obligatoires du devis en France (voir brief.md)
