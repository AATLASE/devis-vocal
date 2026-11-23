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

apprentissage des devis passés · intégration WhatsApp
Business API · envoi par email · signature électronique · facturation · paiement · CRM ·
file d'attente · cache · gestion d'erreurs exhaustive · design poussé du front

**Si une demande déborde de ce périmètre, le signaler au lieu de l'implémenter.**

### Exception assumée : comptes et template par artisan

« comptes / auth / multi-tenant » et « base de données » figuraient dans la liste
ci-dessus. Ils en ont été retirés sur décision explicite, après que le conflit a été
signalé. Ce qui existe désormais : Firebase Auth (Google et e-mail/mot de passe), et
un template HTML de devis par compte, rangé dans Firestore.

Cette exception ne s'étend pas d'elle-même. Elle couvre l'authentification et le
template — et la fiche de l'artisan. Le tarif de l'artisan a sa propre exception, plus
bas. Rien d'autre : ni historique des devis, ni stockage
du devis lui-même. Le pipeline reste sans état — le devis fait toujours l'aller-retour
en JSON, et le serveur n'en garde rien.

Deux garde-fous la tiennent, et ils ne sont pas négociables :

- **Sans identifiants Firebase, tout est inactif.** Pas d'écran de connexion, pas de
  template, comportement d'avant à l'identique. C'est ce qui fait passer `uv run pytest`
  sans le moindre secret et ce qui garde la démonstration hors-ligne possible.
- **Le compte ne conditionne jamais la dictée.** Un artisan non connecté dicte et sort
  son devis exactement comme avant. Le compte ne sert qu'à retrouver son template et son tarif.

Le coût est réel et documenté : un template téléversé ne garantit plus les mentions
obligatoires. `app/templates.py` le mesure à l'enregistrement — quatorze contrôles sur
le document rendu — et l'écran affiche ce qui manque. Voir `docs/templates.md`.

### Exception assumée : correction du devis à l'écran

« édition en ligne du devis » figurait dans la liste ci-dessus. Elle en a été retirée
sur décision explicite, parce que c'est la première question d'un artisan devant la
démonstration : « et si c'est faux, je corrige comment ? ». Ce qui existe désormais, à
l'écran de relecture : corriger les lignes (désignation, quantité, unité, prix, ajout,
retrait), confirmer un prix estimé, et corriger le client, l'objet, la durée, la TVA et
les observations.

Elle tient par les règles du reste du projet, pas par des règles à elle :

- **Le navigateur ne calcule pas.** Il renvoie le devis d'origine et les corrections à
  `POST /api/devis/corriger`, et `app/edition.py` les repasse par `to_devis()`. Un seul
  chemin de calcul ; les totaux envoyés par le navigateur sont ignorés.
- **Le serveur ne garde rien.** Pas d'historique, pas de brouillon enregistré.
- **`models.py` n'a pas bougé.** Les corrections ont leur modèle dans `edition.py`.

S'y rattache la saisie du client : l'adresse s'autocomplète depuis la Base Adresse
Nationale (`app/adresse.py`, via la Géoplateforme), et un client professionnel se
retrouve dans l'annuaire des entreprises déjà branché. Il n'existe pas d'annuaire des
particuliers, et il ne faut pas en chercher un.

Les prix estimés s'appuient sur les **fourchettes de référence** de
`app/prompts/structuration.md`, communes à tous — c'est du prompt, donc dans le
périmètre. Le tarif propre à un artisan est l'objet de l'exception suivante.

### Exception assumée : tarif de l'artisan

« bibliothèque de prix par artisan » figurait dans la liste ci-dessus. Elle en a été
retirée sur décision explicite, après que le conflit a été signalé : les fourchettes de
référence du prompt, même recalées sur le marché, ne donneront jamais les prix d'un
artisan donné. Ce qui existe désormais : un artisan connecté enregistre **son tarif** —
prestation, unité, prix HT, précision —, rangé dans Firestore, et le chiffrage s'en sert
avant les fourchettes. Une ligne chiffrée sur son tarif est un prix confirmé : pas de
« estimé † » sur le PDF, une mention « votre prix » à la relecture.

Elle couvre la saisie à la main et quatre imports : la dictée, un fichier Excel ou CSV, le
PDF d'anciens devis, la photo d'un devis papier. Chaque import est une **extraction
ponctuelle que l'artisan relit et valide** à l'écran. Ce n'est pas de l'« apprentissage
des devis passés », qui reste hors scope : rien n'apprend des devis générés ni des
corrections, et il n'y a pas d'historique.

Elle tient par les règles du reste du projet, plus deux à elle :

- **Rien n'est enregistré sans l'artisan.** Un import propose des lignes ; seul
  « Enregistrer mon tarif » écrit.
- **Python vérifie, il n'impose pas.** Une ligne n'est « votre prix » que si le modèle a
  recopié l'identifiant de la ligne du tarif, la même unité et exactement le même prix
  (`structuration._verifier_les_refs`). Sinon elle redevient une ligne ordinaire, dictée
  ou estimée.
- **Le LLM ne calcule pas, même à l'import** : un prix TTC est signalé, jamais converti.
- **Les garde-fous des comptes s'appliquent** : sans Firebase, rien n'existe ; un
  visiteur non connecté chiffre exactement comme avant ; la démonstration sur fixtures
  ignore le tarif.

Voir `app/grilles.py` et la section « Le tarif de l'artisan » du prompt.

Le PDF est la seule exception à la sobriété : c'est la vitrine, il doit être impeccable.

## Stack

- **Backend** : Python 3.12, FastAPI
- **Transcription** : API OpenAI (gpt-4o-transcribe), derrière `transcribe(audio, filename) -> str`
- **Structuration** : API OpenAI, sortie JSON contrainte, derrière `structure(transcript, grille=None) -> DevisExtraction`
- **PDF** : Playwright (Chromium headless) rendant `templates/devis.html`
- **Front** : une page HTML servie par FastAPI, JS vanilla, pas de framework
- **Comptes** : Firebase Auth, jetons vérifiés par `firebase-admin`, derrière les deux
  dépendances de `app/authentification.py`. Templates dans Firestore. Tout facultatif —
  voir l'exception assumée plus bas.
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

**Pas de sur-ingénierie.** Pas de queue, pas de cache. Le devis fait l'aller-retour en
JSON entre le navigateur et l'API ; le serveur ne garde rien. Firestore ne stocke que les
templates, les fiches et les tarifs des artisans — jamais un devis, jamais une transcription.

**L'authentification s'isole derrière deux dépendances.** `utilisateur_requis` et
`utilisateur_optionnel`, dans `app/authentification.py`. Aucun autre module n'importe
`firebase_admin`. C'est ce qui garde la décision réversible.

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

L'ajout des comptes traverse les deux moitiés : `authentification.py` et `templates.py`
sont côté données, mais `pdf.render(devis, template)` et l'écran du template sont côté
rendu. `Template`, dans `models.py`, est le point de couture — donc soumis à la même
règle que le reste du contrat : **PR relue à deux**.
