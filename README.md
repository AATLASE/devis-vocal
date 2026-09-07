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

## L'identité de l'artisan

Un devis au nom de l'artisan qu'on a en face, avec son vrai SIRET, est le meilleur
argument du produit. Il se renseigne **depuis la page**, pas depuis le `.env` : la
ligne « Votre entreprise » sur l'écran d'accueil ouvre un champ de recherche.

L'artisan tape le nom de sa boîte — ou son SIRET — et choisit dans la liste. Sept champs
se remplissent d'un coup depuis l'annuaire public de l'État : raison sociale, forme
juridique, adresse, ville, SIRET, code APE, et le numéro de TVA, qui n'est pas cherché
mais *calculé* depuis le SIREN. Restent le téléphone, l'e-mail, l'assurance décennale et
l'IBAN, qui ne figurent dans aucune base et n'appartiennent qu'à lui.

L'identité est conservée par le navigateur et repart avec chaque demande de devis : le
serveur n'en garde rien, et changer d'artisan entre deux rendez-vous ne demande plus
d'éditer un fichier ni de redémarrer quoi que ce soit.

Deux réserves à connaître. Une entreprise peut s'opposer à la diffusion de ses données —
c'est fréquent chez les entrepreneurs individuels — et elle est alors introuvable : le
formulaire de saisie reste ouvert dessous, et la clé de contrôle du SIRET attrape les
fautes de frappe hors ligne. Et le capital social, mention obligatoire pour une société,
n'est publié nulle part : l'écran le réclame plutôt que de l'inventer.

Les variables `ENTREPRISE_*` du `.env` restent utiles comme valeurs de repli, notamment
pour préremplir avant un rendez-vous. Ce qui est saisi dans la page les recouvre ; ce qui
est laissé vide les laisse passer.

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
                        ┌───────────────────┴───────────────────┐
          POST /api/apercu ▼                                    ▼ POST /api/pdf
     la feuille A4 en HTML, pour l'aperçu                    devis.pdf
     affiché pendant la relecture
```

`/api/apercu` et `/api/pdf` rendent **le même document, sur le même gabarit** : l'un
par le navigateur de l'artisan, l'autre par Chromium. L'aperçu est là tout de suite et
se redimensionne, là où le PDF coûte une à deux secondes de rendu ; il ne peut donc pas
mentir sur le fond, et ne se réserve qu'un cheveu d'écart sur le rendu des polices.

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
| `app/static/tokens.css` | **La source unique du design.** Couleurs, barème typographique, espacement, mouvement — lue par l'application *et* inlinée dans le PDF |
| `app/static/` | Les sept écrans du parcours sous une ossature commune, JS vanilla, polices embarquées |
| `app/journal.py` | Garde les vocaux réels des rendez-vous — hors du parcours, désactivé par défaut |
| `app/authentification.py` | Firebase Auth. Deux dépendances FastAPI, et le seul module qui importe `firebase_admin` |
| `app/profils.py` | La fiche de l'artisan — nom, prénom, entreprise, rôle. Demandée à l'inscription, **jamais dans le devis** |
| `app/gabarits.py` | Le gabarit de devis par artisan : stockage, validation, contrôle des mentions |

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
- **Un bandeau prévient** quand le chiffrage est rejoué depuis une fixture, calculé par
  un moteur de secours plutôt que par le moteur de référence, ou sorti sur la mise en page
  livrée alors que l'artisan croit voir la sienne. Trois substitutions silencieuses, trois
  fois la même bande : le produit ne montre jamais autre chose que ce qu'il annonce.
- **La relecture montre les deux lectures du devis** : à gauche ce qu'on vérifie — les
  lignes, les quantités, ce qui est estimé — à droite la feuille A4 telle qu'elle partira.
  Côte à côte au-delà de 1180px ; en dessous elles se relaient sous un commutateur, parce
  qu'empiler un A4 sous un tableau ne donne à lire ni l'un ni l'autre.

### Le design du front

Une ossature persistante — marque, position dans le parcours, compte — porte les sept
écrans, qui se redécouvraient chacun auparavant. Tout le reste sort de
`app/static/tokens.css` : barème typographique fluide (`clamp`, donc écrit une fois au
lieu d'être redéclaré à trois seuils), échelle d'espacement de 4px, trois durées et trois
courbes de mouvement, trois niveaux d'élévation.

Deux règles ne se négocient pas, et sont vérifiées et non supposées :

- **Contraste AA partout.** La rampe de texte a été descendue d'un cran : le gris des
  libellés donnait 2,91:1 sur le fond papier — sous le seuil de 4,5:1 — et il portait les
  libellés en capitales, les indices sous les zones de dépôt et la pagination du devis
  imprimé. La discrétion se fait désormais par la taille, la graisse et l'interlettrage.
- **48px de cible tactile, un anneau de focus sur tout ce qui se focalise.** La cible ici
  est un pouce ganté sur un chantier ; et au clavier, le produit se traversait à l'aveugle.

`prefers-reduced-motion` coupe toutes les animations sauf une : le dévoilement de la
transcription, qui devient instantané au lieu de disparaître — c'est du contenu, pas de
la décoration.

### La fiche de l'artisan

Créer un compte demande une dernière étape : **prénom, nom, entreprise, rôle**. Les
quatre sont obligatoires, et le même écran sert aux deux façons de s'inscrire — c'est
l'absence de fiche qui le déclenche, pas la façon d'arriver. Un compte Google y passe
donc à sa première connexion, avec son nom déjà pré-rempli.

**Cette fiche n'entre jamais dans le devis, et c'est délibéré.** L'en-tête du document
porte treize mentions légalement obligatoires — SIRET, TVA intracommunautaire,
assurance décennale, IBAN. N'y faire remonter que la raison sociale produirait un devis
affichant un nom d'entreprise qui ne correspond plus à son SIRET : pas seulement
inutile, mais moins bon que de ne rien faire. Le devis continue donc de sortir sur
l'`Entreprise` de la configuration, et la fiche ne sert qu'à savoir qui est connecté —
la barre de compte affiche « Camille Durand · Bâti Rénov » à la place de l'adresse.

Le jour où le devis devra vraiment porter l'entreprise de l'artisan, c'est
`Entreprise` qu'il faudra collecter en entier, pas `Profil` qu'il faudra brancher.
`tests/test_profils.py` tient ce garde-fou.

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

### Suivre ce qui se passe

Tout passe par `logs/devis-vocal.log`, en même temps qu'à l'écran. Rien à armer.

```
15:08:15 INFO    transcription fichier=vocal.webm ko=412.3
15:08:15 INFO      appel fournisseur=groq modele=whisper-large-v3-turbo
15:08:21 INFO      transcription ok  5.8 s caracteres=1240
15:08:21 INFO      transcription (1240 caracteres)
    Alors, pour la salle de bain de Mme Ferrand, rue des Lilas...
15:08:23 INFO    structuration caracteres=1240
15:08:54 INFO      appel fournisseur=anthropic modele=claude-opus-5 tokens_entree=2841 tokens_sortie=1102 cout_usd=0.0417
15:08:54 INFO      structuration ok  31.4 s lignes=9 estimees=2
15:08:54 INFO      extraction (2180 caracteres)
    { ... le JSON complet renvoyé par le modèle ... }
15:08:54 INFO      devis  numero=DEV-20260902-1508 lignes=9 tva=0.1 total_ht=4374.55 ...
15:08:59 INFO      pdf ok  1.9 s ko=248.1
```

Chaque étape avec sa durée, le fournisseur et le modèle appelés, les tokens et le coût
estimé, les totaux calculés — à comparer d'un coup d'œil avec le PDF — et la pile
complète quand ça casse. Une erreur qu'on renvoie soi-même à l'utilisateur (fichier trop
lourd, format refusé) tient en une ligne : dérouler trente lignes de pile pour un message
qu'on a écrit noierait les vraies pannes.

Le fichier est archivé chaque nuit sous `logs/devis-vocal.log.2026-09-01` — le chemin à
ouvrir ne change jamais, la journée d'un rendez-vous reste retrouvable. Trente jours
gardés, dossier ignoré par git. `LOG_CONTENU=false` cesse d'y recopier les
transcriptions et les extractions si le fichier devient lourd.

> À ne pas confondre avec le journal ci-dessus : celui-ci suit l'exécution, l'autre
> garde les vocaux. Le suivi recopie des transcriptions, donc parfois des noms et des
> adresses de clients — il ne quitte pas la machine non plus.

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

## Comptes et gabarit par artisan

> **Hors du périmètre d'origine**, ajouté sur décision explicite. `CLAUDE.md` classait
> « comptes / auth / multi-tenant » et « base de données » dans ce qu'il ne fallait pas
> construire ; l'exception et ses garde-fous y sont maintenant décrits.

Un artisan connecté peut remplacer la mise en page A4 livrée par la sienne, un fichier
HTML qu'il téléverse. Le mode d'emploi côté artisan est dans **`docs/gabarits.md`**.

**Sans identifiants Firebase, tout ceci est inactif** : pas d'écran de connexion, pas de
gabarit, et le démonstrateur se comporte exactement comme avant. C'est le mode par
défaut — et celui dans lequel `uv run pytest` passe, sans le moindre secret.

Pour l'armer, renseigner les variables `FIREBASE_*` du `.env.example` : un compte de
service côté serveur (c'est lui qui décide), et les trois clés publiques du SDK
navigateur. Le détail des écrans de la console Firebase y est.

Deux règles tiennent la fonctionnalité, et elles sont plus importantes qu'elle :

- **Le compte ne conditionne jamais la dictée.** Un artisan non connecté dicte et sort
  son devis comme avant. Le compte ne sert qu'à retrouver son gabarit — un jeton qui
  expire pendant les quarante secondes de structuration ne fait donc rien perdre.
- **Un gabarit téléversé ne garantit plus les mentions obligatoires.** C'est le coût
  réel de la fonctionnalité. Il n'est pas tu : à l'enregistrement, le gabarit est rendu
  sur un devis d'exemple et passé au crible de quatorze contrôles — SIRET, TVA
  intracommunautaire, assurance, totaux, « bon pour accord »… Ce qui manque s'affiche à
  l'écran et y reste. Le gabarit livré, lui, passe les quatorze.

Le gabarit est validé à l'enregistrement, jamais au moment du rendu : un gabarit qui
n'aurait pas compilé est refusé tout de suite, pas devant le client. Et si un gabarit
valide échoue quand même sur un vrai devis — vingt lignes au lieu de cinq, un champ nul
— le serveur rejoue sur le gabarit livré et **le signale par un bandeau**, au lieu de
substituer une mise en page en silence.

```
GET    /api/firebase        la config publique du SDK navigateur
GET    /api/profil          la fiche de l'artisan connecté, et `complet`
PUT    /api/profil          l'enregistrer (les quatre champs sont requis)
GET    /api/gabarit         le gabarit de l'artisan connecté
PUT    /api/gabarit         en poser un (validé avant enregistrement)
DELETE /api/gabarit         revenir au gabarit livré
POST   /api/gabarit/apercu  le PDF d'un gabarit, avant de l'enregistrer
GET    /api/gabarit/modele  le gabarit livré, comme point de départ
```

## Avant de mettre en ligne

Le démonstrateur tourne sur **tes** clés API. Sur une URL publique sans protection,
n'importe qui peut faire tourner le chiffrage à tes frais — il suffit du lien, aucune
compétence requise. Trois gestes, dans cet ordre d'importance :

**1. Poser un plafond de dépense chez ton fournisseur.** Deux minutes, gratuit, et c'est
la seule barrière qu'un bug dans ce dépôt ne peut pas contourner. Console Anthropic ou
tableau de bord OpenAI, plafond mensuel. Le tier gratuit de Groq est déjà sûr : pas de
carte enregistrée, donc pas de facture possible.

**2. Renseigner `ACCES_CODE` dans le `.env`.** Vide, tout est ouvert — c'est le mode de
développement. Rempli, les routes coûteuses exigent le code et la page le réclame à
l'arrivée.

Il ne fait pas double emploi avec l'authentification Firebase, et il ne la remplace pas :
le jeton dit **qui** appelle, le code dit qu'on a **le droit** d'appeler. Surtout, sans
identifiants Firebase la vérification de jeton est inactive et `utilisateur_requis`
laisse tout passer — le code d'accès est alors la seule chose qui garde l'API. Toutes
les routes `/api/` sont couvertes, à la seule exception de `/api/firebase`, dont le
navigateur a besoin pour afficher l'écran de connexion.

**3. Vérifier que `DEVIS_PAR_JOUR` et `DEVIS_PAR_MOIS` te conviennent.** C'est le
garde-fou du portefeuille : il couvre ce que le code d'accès ne couvre pas — un code qui
a circulé, un script laissé en boucle, une fausse manœuvre. Deux échelles parce qu'une
seule ne suffit pas : 80 par jour laisse passer 2 400 devis dans le mois.

Les compteurs sont écrits dans `var/compteurs.json` et survivent à un redémarrage — un
plafond qu'on annule en relançant l'application ne protège de rien. **En conteneur, ce
dossier doit être un volume**, sans quoi chaque déploiement les remet à zéro. Leur état
est lisible sur `/health`, une fois la porte franchie.

Le reste est déjà en place et ne demande rien : en-têtes de sécurité et CSP stricte sur
chaque réponse, refus des corps trop volumineux **avant** de les lire, limitation de
débit par adresse, plafond de lignes par devis et de rendus PDF simultanés. Le détail
des variables est dans `.env.example`, et `tests/test_securite.py` les couvre.

Deux points à ne pas oublier au montage :

- **HTTPS est obligatoire**, pas décoratif : le micro du navigateur ne s'ouvre pas sur
  une page non sécurisée. Sans certificat, pas de dictée en rendez-vous.
- Derrière un reverse proxy, lancer uvicorn avec `--proxy-headers`. Sans ça, toutes les
  requêtes semblent venir du proxy et la limitation de débit compte une seule adresse
  pour tout le monde. L'en-tête `X-Forwarded-For` n'est **jamais** lu directement : il
  est écrit par le client, donc n'importe qui pourrait s'inventer une adresse neuve à
  chaque requête.

## Déploiement

```bash
docker compose up --build
```

L'image part de l'image officielle Playwright : Chromium et ses dépendances sont déjà
dedans. Sur Coolify, pointer sur le `Dockerfile`, exposer le port 8000, healthcheck sur
`/health`, et renseigner les variables d'environnement du `.env`.

Pour l'authentification, Coolify injecte des variables et ne dépose pas de fichiers :
utiliser `FIREBASE_CREDENTIALS_JSON` — le JSON du compte de service sur une ligne —
plutôt que `FIREBASE_CREDENTIALS`, qui attend un chemin.

Docker n'est **pas** nécessaire pour développer.
