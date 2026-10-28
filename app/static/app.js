/* Devis Vocal — parcours artisan. JS vanilla, pas d'étape de construction.

   Le devis vit dans le navigateur : le serveur ne garde rien. Trois appels, dans
   l'ordre, et l'écran suit le pipeline réel plutôt qu'une minuterie de démo :

     POST /api/transcribe   le fichier audio  -> la transcription
     POST /api/devis        la transcription  -> le devis chiffré
     POST /api/pdf          le devis          -> le fichier

   Le moment qui compte est le deuxième : le chiffrage prend vingt à quarante
   secondes, et c'est la transcription qui se dévoile pendant ce temps-là qui fait
   patienter. C'est aussi là que l'artisan voit que la machine l'a compris. */

/* ---- formatage français ------------------------------------------------ */
/* U+202F, espace fine insécable : c'est ce qui donne « 5 369,00 € ». `money()`
   est la seule façon d'écrire un montant dans cette page. */

const NB = '\u202F';
const money = (n) => {
  const [entier, decimales] = Number(n).toFixed(2).split('.');
  return entier.replace(/\B(?=(\d{3})+(?!\d))/g, NB) + ',' + decimales;
};
const euro = (n) => money(n) + NB + '€';
const nombre = (n) => String(Number(n)).replace('.', ',');
// Les taux arrivent en Decimal sérialisé ; « 0.1 × 100 » vaut 10,000000000000002 en
// virgule flottante, et ce sont des chiffres que l'artisan lit. On arrondit au dixième.
const pourcent = (n) => nombre(Math.round(Number(n) * 1000) / 10);

const MOUVEMENT_REDUIT = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

const $ = (id) => document.getElementById(id);

function noeud(balise, classe, texte) {
  const el = document.createElement(balise);
  if (classe) el.className = classe;
  if (texte !== undefined) el.textContent = texte;
  return el;
}

/* ---- état -------------------------------------------------------------- */

let devisCourant = null;
// Le vocal et la transcription survivent à l'échec de l'étape qui les suit. C'est ce
// qui permet de rejouer la seule étape qui a raté, au lieu de renvoyer l'artisan
// dicter — et il n'y a aucune raison de le faire reparler quand le fournisseur de
// transcription a simplement renvoyé un 500 passager.
let vocalCourant = null;            // { fichier, secondes }
let transcriptionCourante = null;
let dureeVocal = null;
let chrono = null;
let revelation = null;
let fournisseur = 'openai';   // renseigné par /health
// Vrai une fois le PDF partagé ou enregistré : le devis est alors entre les mains de
// l'artisan, et le quitter ne demande plus de confirmation.
let devisEnvoye = false;

/* ---- navigation -------------------------------------------------------- */

/* Le fil de l'ossature : un mot qui dit où l'on est. Vide sur l'accueil et sur la
   connexion, où la question ne se pose pas — l'écran est le produit lui-même. */
const FIL = {
  profil:     'Inscription',
  traitement: 'Établissement',
  relecture:  'Relecture',
  vide:       'Devis non établi',
  erreur:     'Interruption',
  template:    'Mon template',
};

function montrer(nom) {
  document.querySelectorAll('.screen').forEach((s) => s.classList.remove('is-active'));
  $('ecran-' + nom).classList.add('is-active');

  const fil = $('entete-fil');
  fil.textContent = FIL[nom] || '';
  fil.hidden = !FIL[nom];

  window.scrollTo(0, 0);
}

function reinitialiser() {
  clearInterval(chrono);
  clearInterval(revelation);
  chrono = revelation = null;
  // `true` : on annule, on n'envoie pas. Sans cet argument, arrêter une dictée en
  // cours déclencherait son gestionnaire `stop`, qui enchaînerait sur l'envoi — et
  // « Recommencer » lancerait un devis au lieu d'en effacer un.
  arreterDictee(true);
  fermerRelu();
  fermerEdition();
  leverGardeHistorique();
  devisCourant = null;
  devisEnvoye = false;
  pdfPret = null;
  vocalCourant = null;
  transcriptionCourante = null;
  dureeVocal = null;
  $('fichier').value = '';
  fermerRepli('repli', 'repli-bascule');
  fermerRepli('depot-repli', 'depot-bascule');
  $('carte-transcription').hidden = true;
  $('transcription-directe').textContent = '';
  viderApercu();
  peindreDictee(false);
  montrer('accueil');
}

/* ---- ne pas perdre un devis -------------------------------------------- */
/* Le devis et le vocal ne vivent que dans cette page : le serveur n'en garde rien,
   c'est voulu. Un geste retour, un tirer-pour-rafraîchir ou un toucher malheureux
   sur « Nouveau devis » ne doivent donc pas les effacer sans le demander — le perdre,
   c'est redicter, et payer un chiffrage de plus. */

const devisAPerdre = () => !!(devisCourant && devisCourant.lignes.length && !devisEnvoye);

function confirmerAbandon() {
  return !devisAPerdre() || window.confirm(
    "Abandonner ce devis ? Il n'est enregistré nulle part : il faudrait le dicter de nouveau.");
}

/* Ce que déclenchent « Nouveau devis », « Recommencer » et la marque. `reinitialiser`
   reste sans question : le code qui l'appelle sait déjà ce qu'il fait. */
function recommencer() {
  if (confirmerAbandon()) reinitialiser();
}

window.addEventListener('beforeunload', (e) => {
  if (!devisAPerdre() && !vocalPret && !enregistreur) return;
  e.preventDefault();
  e.returnValue = '';
});

/* Le geste retour d'Android quitte la page. Une entrée d'historique posée à l'entrée
   en relecture l'absorbe : le retour demande alors s'il faut abandonner le devis. */
let gardeHistorique = false;

function poserGardeHistorique() {
  if (gardeHistorique) return;
  history.pushState({ devisVocal: true }, '');
  gardeHistorique = true;
}

/* Le devis est abandonné par un bouton : l'entrée posée pour lui n'a plus d'objet, et
   la laisser obligerait à un retour de plus pour quitter la page. */
function leverGardeHistorique() {
  if (!gardeHistorique) return;
  gardeHistorique = false;
  history.back();   // le `popstate` qui suit trouve la garde levée et ne fait rien
}

window.addEventListener('popstate', () => {
  if (!gardeHistorique) return;
  gardeHistorique = false;   // le geste vient de consommer l'entrée
  if (!devisCourant) return;
  if (confirmerAbandon()) reinitialiser();
  else poserGardeHistorique();
});

/* Un repli et son lien vont par deux : le panneau se ferme, et le lien doit le
   dire — `aria-expanded` est ce qui l'annonce au lecteur d'écran, et ce qui fait
   pivoter le signe « + ». */
function ouvrirRepli(panneau, lien, ouvert) {
  $(panneau).classList.toggle('is-open', ouvert);
  $(lien).setAttribute('aria-expanded', String(ouvert));
}
function fermerRepli(panneau, lien) { ouvrirRepli(panneau, lien, false); }

function echouer(message) {
  clearInterval(chrono);
  clearInterval(revelation);
  $('erreur-message').textContent = message;
  // Chaque étape peut échouer après que la précédente a réussi — un 500 passager du
  // fournisseur suffit. Ce qui a déjà été obtenu est gardé, et on ne propose de
  // rejouer que ce qui a raté. Renvoyer l'artisan redicter quinze secondes parce que
  // Groq a hoqueté, ce serait lui faire payer une panne qui n'est pas la sienne.
  const bouton = $('btn-reprendre');
  if (transcriptionCourante) {
    bouton.textContent = 'Réessayer le chiffrage';
    bouton.hidden = false;
  } else if (vocalCourant) {
    bouton.textContent = 'Réessayer la transcription';
    bouton.hidden = false;
  } else {
    bouton.hidden = true;
  }
  montrer('erreur');
}

/* Reprend au dernier point acquis : le chiffrage si la transcription est en main,
   la transcription si on n'a plus que l'audio. */
function reprendre() {
  if (transcriptionCourante) lancer(null, transcriptionCourante);
  else if (vocalCourant) lancer(vocalCourant.fichier, null, vocalCourant.secondes);
}

/* ---- 2. traitement ----------------------------------------------------- */
/* Les trois étapes sont celles du pipeline, pas une décoration. Le libellé du
   chiffrage annonce l'attente pendant qu'elle a lieu : une attente annoncée est
   supportée, une attente muette est subie. */

/* Le libellé d'attente est celui du moteur qui tourne réellement. Anthropic met
   vingt à quarante secondes, Groq en met six, une fixture répond tout de suite.
   Annoncer quarante secondes pour six est aussi trompeur que de ne rien annoncer :
   l'artisan repose son téléphone et rate le moment où la machine le comprend. */
const ATTENTE = {
  anthropic: 'Chiffrage en cours — 20 à 40 secondes',
  openai:    'Chiffrage en cours — 20 à 40 secondes',
  groq:      'Chiffrage en cours — quelques secondes',
  fixtures:  'Lecture du chiffrage enregistré',
};

const ETAPES = [
  { titre: 'Transcription', detail: 'Lecture de la note vocale, mot pour mot' },
  {
    titre: 'Analyse et chiffrage',
    detail: 'Identification des prestations, des quantités et des prix',
    occupe: ATTENTE.openai,   // ajusté au retour de /health
  },
  { titre: 'Génération du devis', detail: 'Mise en forme du document et des mentions légales' },
];

/* Une icône du jeu défini en tête d'`index.html`. Aucun réseau, aucune image :
   un `<use>` sur un `<symbol>`, qui hérite de la couleur du texte qui le porte. */
function icone(nom) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS('http://www.w3.org/2000/svg', 'use');
  use.setAttribute('href', '#' + nom);
  svg.append(use);
  return svg;
}

function peindreEtapes(atteinte) {
  const conteneur = $('etapes');
  conteneur.replaceChildren();
  ETAPES.forEach((etape, i) => {
    const faite = atteinte > i;
    const encours = atteinte === i;

    const bloc = noeud('div', 'step step--' + (faite ? 'faite' : encours ? 'encours' : 'attente'));

    // La pastille : une coche quand c'est fait, le rang tant que ça ne l'est pas.
    // Le chiffre garde sa place — sans lui, la colonne se viderait à mi-parcours.
    const marque = noeud('div', 'step__mark');
    if (faite) marque.append(icone('i-coche'));
    else marque.textContent = String(i + 1);
    bloc.append(marque);

    const centre = noeud('div');
    centre.append(
      noeud('div', 'step__title', etape.titre),
      noeud('div', 'step__detail', encours && etape.occupe ? etape.occupe : etape.detail),
    );
    bloc.append(centre);

    bloc.append(noeud('div', 'step__status', faite ? 'Terminé' : encours ? 'En cours' : 'En attente'));
    conteneur.append(bloc);
  });

  peindreJauge(atteinte);
}

/* La jauge suit les mêmes étapes que la liste, mais se lit à un mètre : c'est
   elle qu'on voit quand le téléphone est posé sur le tableau de bord. Le segment
   en cours fait la navette — le pipeline ne rend aucune progression fine, et
   inventer un pourcentage serait mentir. */
function peindreJauge(atteinte) {
  const jauge = $('jauge');
  jauge.setAttribute('aria-valuenow', String(Math.min(atteinte, ETAPES.length)));
  [...jauge.children].forEach((seg, i) => {
    seg.classList.toggle('est-faite', atteinte > i);
    seg.classList.toggle('est-encours', atteinte === i);
  });
}

function demarrerChrono() {
  const debut = Date.now();
  $('chrono').textContent = '0 s';
  chrono = setInterval(() => {
    $('chrono').textContent = Math.round((Date.now() - debut) / 1000) + ' s';
  }, 500);
}

/* Deux mots toutes les 42 ms, une ellipse tant que le flux continue. Résolue
   quand tout le texte est là : on ne bascule jamais d'écran au milieu d'un mot. */
function devoiler(texte, cible) {
  return new Promise((resolve) => {
    if (MOUVEMENT_REDUIT) {
      cible.textContent = texte;
      resolve();
      return;
    }
    const mots = texte.split(' ');
    let n = 0;
    revelation = setInterval(() => {
      n += 2;
      cible.textContent = mots.slice(0, n).join(' ') + (n < mots.length ? ' …' : '');
      if (n >= mots.length) {
        clearInterval(revelation);
        revelation = null;
        resolve();
      }
    }, 42);
  });
}

/* ---- appels ------------------------------------------------------------ */

/* Deux en-têtes peuvent accompagner un appel, et ils ne disent pas la même chose :
   le jeton Firebase dit QUI appelle, le code d'accès dit qu'on a le droit d'appeler.
   Le second reste utile même avec le premier — sans identifiants Firebase, la
   vérification de jeton est inactive et n'importe qui passerait.

   Ni l'un ni l'autre ne voyage dans l'URL : une URL se retrouve dans l'historique,
   dans les journaux du reverse proxy et dans le `Referer` envoyé aux sites tiers. */
/* Le jeton Firebase, quand il y en a un. `compte.js` est un module chargé après ce
   fichier : tant qu'il n'a pas tourné — ou si Firebase n'est pas configuré — on part
   sans en-tête, et l'API se comporte exactement comme avant. Le compte n'est jamais
   une condition pour dicter un devis. */
async function avecJeton(options = {}) {
  if (!window.Compte || !window.Compte.actif) return options;
  let jeton = null;
  try {
    jeton = await window.Compte.jeton();
  } catch (_) { /* session illisible : on continue sans, le serveur retombera sur le défaut */ }
  if (!jeton) return options;
  return { ...options, headers: { ...(options.headers || {}), Authorization: 'Bearer ' + jeton } };
}

async function poste(url, options) {
  let reponse;
  options = await avecJeton(options);
  try {
    reponse = await fetch(url, {
      ...options,
      headers: { ...(options.headers || {}), ...enteteAcces() },
    });
  } catch (_) {
    // `fetch` ne rejette que sur un échec réseau — coupure, serveur arrêté, tunnel
    // tombé. Le navigateur donne « Failed to fetch », en anglais et sans sujet :
    // illisible pour un artisan, et surtout muet sur ce qu'il peut faire.
    throw new Error('La connexion au serveur a été perdue. Vérifiez le réseau, puis réessayez.');
  }
  if (reponse.status === 401) {
    // Deux refus portent désormais ce code : le code d'accès, et la session Firebase
    // expirée. Les confondre enverrait l'artisan retaper un code juste — qui serait
    // refusé à nouveau, sans qu'il comprenne pourquoi. On lit donc le motif.
    let motif = '';
    try { motif = (await reponse.json()).detail || ''; } catch (_) { /* corps vide */ }

    if (/code d'accès/i.test(motif)) {
      // Pas de code du tout : /health n'avait pas répondu au chargement, la porte
      // n'a jamais été montrée. Un code refusé : il a changé en cours de session.
      const message = codeAcces ? "Ce code n'est plus valable. Redemandez-le." : '';
      oublierAcces();
      ouvrirPorte(message);
      // Erreur marquée : l'appelant ne doit pas la traiter comme une panne. La porte
      // est à l'écran, et l'étape refusée sera rejouée une fois le code saisi.
      const err = new Error("Code d'accès refusé.");
      err.porte = true;
      throw err;
    }
    throw new Error(motif || 'Votre session a expiré. Reconnectez-vous.');
  }
  if (!reponse.ok) {
    let detail = `Erreur ${reponse.status}.`;
    try {
      const corps = await reponse.json();
      if (corps.detail) detail = corps.detail;
    } catch (_) { /* réponse non JSON : on garde le message générique */ }
    throw new Error(detail);
  }
  return reponse;
}

async function transcrire(fichier) {
  const donnees = new FormData();
  donnees.append('audio', fichier);
  const r = await poste('/api/transcribe', { method: 'POST', body: donnees });
  return (await r.json()).transcription;
}

async function chiffrer(transcription) {
  const r = await poste('/api/devis', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    // L'identité part avec la demande : c'est le navigateur qui la retient, pas le
    // serveur. Absente, l'API retombe sur la configuration du `.env`.
    body: JSON.stringify({ transcription, entreprise: entrepriseCourante || undefined }),
  });
  return await r.json();
}

/* La durée du vocal se lit dans le navigateur, sur le fichier lui-même : l'API
   ne la renvoie pas et ça ne vaut pas un aller-retour. Une seconde d'attente au
   maximum — un fichier illisible ne doit pas retarder la transcription. */
function dureeAudio(fichier) {
  return new Promise((resolve) => {
    const url = URL.createObjectURL(fichier);
    const audio = new Audio();
    let fini = false;
    const rendre = (secondes) => {
      if (fini) return;
      fini = true;
      URL.revokeObjectURL(url);
      resolve(secondes);
    };
    audio.addEventListener('loadedmetadata', () =>
      rendre(Number.isFinite(audio.duration) ? audio.duration : null));
    audio.addEventListener('error', () => rendre(null));
    setTimeout(() => rendre(null), 1000);
    audio.src = url;
  });
}

function formaterDuree(secondes) {
  if (secondes === null) return null;
  const total = Math.round(secondes);
  return `${Math.floor(total / 60)} min ${String(total % 60).padStart(2, '0')}`;
}

/* ---- 1. dictée --------------------------------------------------------- */
/* On enregistre dans la page et on envoie le blob au même `POST /api/transcribe`
   que n'importe quel fichier : l'API ne sait pas d'où vient l'audio, et n'a pas
   à le savoir. Le format suit ce que le navigateur sait produire — webm/opus
   partout, mp4 sur Safari — et les deux sont dans les extensions acceptées par
   `app/transcription.py`. */

const FORMATS = [
  ['audio/webm;codecs=opus', 'webm'],
  ['audio/webm', 'webm'],
  ['audio/mp4', 'mp4'],            // Safari, iOS
  ['audio/ogg;codecs=opus', 'ogg'],
];
const DUREE_MAX = 600;   // 10 min : un garde-fou, pas une contrainte de produit
const DUREE_MIN = 1;     // en deçà, c'est un double appui, pas une dictée

// Chrome enregistre par défaut à 112 kbit/s en stéréo — 280 Ko pour vingt secondes —
// alors que Whisper ramène de toute façon tout en 16 kHz mono avant de transcrire.
// À 32 kbit/s le même vocal pèse 71 Ko et se transcrit à l'identique : c'est quatre
// fois moins à téléverser depuis une camionnette en 4G, là où la démonstration a lieu.
// Les contraintes sont en `ideal` et non en valeurs exigées : un appareil qui ne sait
// pas les tenir doit dégrader, jamais refuser le micro.
const DEBIT_DICTEE = 32000;
const CONTRAINTES_MICRO = {
  channelCount: { ideal: 1 },
  sampleRate: { ideal: 16000 },
  echoCancellation: true,
  noiseSuppression: true,
};

// RMS en dessous duquel on considère que le micro n'a rien entendu du tout.
// Calibré à voix normale, téléphone à bout de bras : un micro coupé reste sous
// 0,002, une pièce silencieuse sous 0,006, une phrase dictée dépasse 0,05.
const SEUIL_SILENCE = 0.012;

let enregistreur = null;
let morceaux = [];
let micro = null;
let chronoDictee = null;
let secondesDictee = 0;
// Le temps réellement enregistré : ce qui est acquis avant la dernière pause, et le
// début du segment en cours. Compter des tics d'une seconde en perdait jusqu'à une
// par pause, or c'est cette durée qui sert de référence au lecteur de réécoute.
let msAcquis = 0;
let debutSegment = 0;
let dicteeAnnulee = false;
let dicteeDirecte = false;
// Le micro est en train de s'ouvrir. Entre l'appui et l'autorisation, il se passe
// jusqu'à une demi-seconde sans rien à l'écran : un second appui — des gants, un doigt
// qui insiste — ouvrait un second micro qu'aucun arrêt ne refermait.
let demarrage = false;
// Pourquoi la dictée s'est arrêtée sans qu'on le demande ; dit sous la réécoute.
let interruption = '';

const mmss = (s) => Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');

/* ---- l'écran reste allumé ---------------------------------------------- */
/* Une dictée dure une à deux minutes, téléphone à la main, sans toucher l'écran : le
   verrouillage automatique tombe en plein milieu, et sur iPhone la capture d'une page
   masquée est coupée ou rendue muette. On demande donc à garder l'écran allumé le temps
   de la dictée. Refusé — batterie faible, navigateur ancien —, on dicte sans. */

let verrouEcran = null;

async function garderEcranAllume() {
  if (!navigator.wakeLock || (verrouEcran && !verrouEcran.released)) return;
  try {
    const verrou = await navigator.wakeLock.request('screen');
    // La dictée a pu s'arrêter pendant la demande : on ne garde pas l'écran pour rien.
    if (enregistreur) verrouEcran = verrou;
    else verrou.release().catch(() => {});
  } catch (_) { /* refusé : la dictée continue sans */ }
}

function libererEcran() {
  if (verrouEcran) verrouEcran.release().catch(() => {});
  verrouEcran = null;
}

/* L'écran s'éteint quand même — l'artisan l'a verrouillé, ou il est passé à une autre
   application. La capture d'une page masquée n'est pas garantie : on met en pause, ce
   qui garde tout ce qui a été dit, et on le dit au retour. Reprendre est un geste. */
function suspendreDictee(raison) {
  if (!enregistreur || enregistreur.state !== 'recording') return;
  basculerPause();
  $('dictee-aide').replaceChildren(noeud('span', 'dictee__point'), document.createTextNode(raison));
}

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    suspendreDictee('En pause : l’écran s’est éteint ou vous avez changé d’application. Touchez « Reprendre ».');
  }
});

/* ---- ce que le micro entend -------------------------------------------- */
/* Le chronomètre dit que l'enregistrement tourne ; il ne dit pas que le micro
   entend. Un micro coupé produit quatre-vingt-dix secondes de silence, un
   aller-retour chez Whisper, et un « Aucune parole détectée » — après coup, quand
   l'artisan a déjà parlé pour rien. L'analyseur répond pendant, et le pic retenu
   permet de refuser l'envoi avant de faire recommencer. */

let contexteAudio = null;
let analyseur = null;
let trameNiveau = null;
let picNiveau = 0;
let niveauMesure = false;

function ouvrirNiveau(flux) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  if (!Ctx) return false;             // sans analyseur, `dvpulse` reprend la main
  try {
    contexteAudio = new Ctx();
    analyseur = contexteAudio.createAnalyser();
    analyseur.fftSize = 512;
    contexteAudio.createMediaStreamSource(flux).connect(analyseur);
  } catch (_) {
    fermerNiveau();
    return false;
  }

  const echantillons = new Uint8Array(analyseur.fftSize);
  const zone = $('dictee');
  picNiveau = 0;
  zone.classList.add('a-niveau');

  const mesurer = () => {
    if (!analyseur) return;
    // En pause le micro reste ouvert, donc l'analyseur entend toujours : ni les
    // barres ni le pic ne doivent suivre une conversation qu'on n'enregistre pas.
    if (enregistreur && enregistreur.state === 'paused') {
      zone.style.setProperty('--dv-niveau', '0');
      trameNiveau = requestAnimationFrame(mesurer);
      return;
    }
    analyseur.getByteTimeDomainData(echantillons);
    let somme = 0;
    for (const v of echantillons) {
      const ecart = (v - 128) / 128;
      somme += ecart * ecart;
    }
    const rms = Math.sqrt(somme / echantillons.length);
    if (rms > picNiveau) picNiveau = rms;
    // Racine puis plafond : l'oreille est logarithmique. Une échelle linéaire
    // laisserait le point presque éteint à voix normale, donc muette elle aussi.
    zone.style.setProperty('--dv-niveau', Math.min(1, Math.sqrt(rms * 6)).toFixed(3));
    trameNiveau = requestAnimationFrame(mesurer);
  };
  mesurer();
  return true;
}

function fermerNiveau() {
  cancelAnimationFrame(trameNiveau);
  trameNiveau = null;
  analyseur = null;
  // Sans fermeture explicite, le contexte reste ouvert et le micro chaud.
  if (contexteAudio) contexteAudio.close().catch(() => {});
  contexteAudio = null;
  const zone = $('dictee');
  zone.classList.remove('a-niveau');
  zone.style.removeProperty('--dv-niveau');
}

/* ---- réécoute avant envoi ---------------------------------------------- */

let vocalPret = null;   // { fichier, secondes, url } entre l'arrêt et l'envoi

function montrerRelu(fichier, secondes) {
  fermerRelu();
  vocalPret = { fichier, secondes, url: URL.createObjectURL(fichier) };
  $('relu-audio').src = vocalPret.url;
  $('lecteur').classList.remove('joue');
  peindreLecteur();
  $('relu').hidden = false;
  $('ecran-accueil').classList.add('a-relu');

  const zone = $('dictee');
  zone.classList.add('relu');
  zone.setAttribute('aria-disabled', 'true');
  zone.setAttribute('aria-label', 'Note vocale enregistrée, ' + mmss(secondes));
  $('dictee-titre').textContent = mmss(secondes);
  $('dictee-aide').textContent = 'Réécoutez avant d’envoyer';
}

/* Le lecteur. Un webm sorti de MediaRecorder n'a pas de durée dans son en-tête : le
   flux est écrit au fil de l'enregistrement, personne ne revient inscrire la longueur
   au début. Le lecteur natif en invente alors une — « 32:04 » pour treize secondes de
   dictée, observé sur Android. D'où celui-ci : la durée vient de `vocalPret.secondes`,
   c'est-à-dire du chronomètre, la même source que le titre de la carte. La seule
   valeur qu'on lit du média est `currentTime`, qui, elle, est fiable. */

function peindreLecteur() {
  if (!vocalPret) return;
  const ecoule = Math.min($('relu-audio').currentTime || 0, vocalPret.secondes);
  const part = vocalPret.secondes ? Math.min(1, ecoule / vocalPret.secondes) : 0;
  $('relu-avance').style.width = (part * 100).toFixed(1) + '%';
  $('relu-temps').textContent = mmss(Math.floor(ecoule)) + ' / ' + mmss(vocalPret.secondes);
  // La piste s'annonce comme un curseur : elle doit donc dire où elle en est.
  const piste = $('relu-piste');
  piste.setAttribute('aria-valuenow', String(Math.round(part * 100)));
  piste.setAttribute('aria-valuetext', mmss(Math.floor(ecoule)) + ' sur ' + mmss(vocalPret.secondes));
}

/* Déplacement dans l'enregistrement, à la position donnée entre 0 et 1. La durée
   de référence est celle du chronomètre et non celle du média, qui est fausse sur
   un webm sans index. Un tel fichier peut refuser le saut : on le laisse alors
   continuer là où il en était plutôt que de casser la lecture. */
function deplacerLecture(part) {
  if (!vocalPret) return;
  try {
    $('relu-audio').currentTime = Math.min(1, Math.max(0, part)) * vocalPret.secondes;
    peindreLecteur();
  } catch (_) { /* saut refusé */ }
}

function basculerLecture() {
  const audio = $('relu-audio');
  if (audio.paused) audio.play().catch(() => {});
  else audio.pause();
}

function fermerRelu() {
  if (vocalPret) URL.revokeObjectURL(vocalPret.url);
  vocalPret = null;

  const audio = $('relu-audio');
  audio.pause();
  audio.removeAttribute('src');
  audio.load();                       // sans quoi Chrome garde le flux précédent
  $('lecteur').classList.remove('joue');
  $('relu-avance').style.width = '0';
  $('relu').hidden = true;
  $('ecran-accueil').classList.remove('a-relu');

  const zone = $('dictee');
  zone.classList.remove('relu');
  zone.removeAttribute('aria-disabled');
}

function formatDisponible() {
  if (typeof MediaRecorder === 'undefined') return null;
  return FORMATS.find(([type]) => MediaRecorder.isTypeSupported(type)) || ['', 'webm'];
}

function peindreDictee(actif) {
  const zone = $('dictee');
  zone.classList.toggle('enregistre', actif);
  $('dictee-actions').hidden = !actif;
  if (actif) {
    $('dictee-titre').textContent = mmss(0);
    peindrePause(false);
    zone.setAttribute('aria-label', "Arrêter l'enregistrement");
  } else {
    zone.classList.remove('en-pause');
    $('dictee-titre').textContent = 'Dictez votre devis';
    $('dictee-aide').textContent = 'Touchez pour commencer à parler';
    zone.setAttribute('aria-label', 'Enregistrer une note vocale');
  }
}

/* La pause. `MediaRecorder` sait suspendre sans fermer le fichier : à la reprise il
   écrit à la suite, et c'est un seul vocal qui part. Le micro, lui, reste ouvert —
   le fermer mettrait fin à l'enregistrement. Toucher la carte arrête toujours, en
   pause comme en cours : on peut finir sa dictée sans avoir à reprendre d'abord. */
function peindrePause(enPause) {
  $('dictee').classList.toggle('en-pause', enPause);
  $('btn-pause-dictee').textContent = enPause ? 'Reprendre' : 'Pause';
  $('dictee-aide').replaceChildren(
    noeud('span', 'dictee__point'),
    document.createTextNode(enPause
      ? 'En pause · touchez pour terminer'
      : 'Enregistrement · touchez pour arrêter'),
  );
}

function basculerPause() {
  if (!enregistreur) return;
  if (enregistreur.state === 'recording') {
    enregistreur.pause();
    msAcquis += Date.now() - debutSegment;
    peindrePause(true);
  } else if (enregistreur.state === 'paused') {
    enregistreur.resume();
    debutSegment = Date.now();
    peindrePause(false);
    garderEcranAllume();   // le verrou tombe de lui-même quand la page est masquée
  }
}

/* Le micro peut manquer pour trois raisons, et chacune a sa réponse. On le dit
   sur place et on déplie le dépôt de fichier : l'artisan n'est jamais coincé. */
function refuserDictee(message) {
  fermerMicro();
  peindreDictee(false);
  $('dictee-aide').textContent = message;
  ouvrirRepli('depot-repli', 'depot-bascule', true);
}

function raisonMicro(err) {
  if (!window.isSecureContext) return 'Le micro exige une connexion sécurisée (https). Déposez un fichier.';
  if (err && err.name === 'NotAllowedError') return 'Micro refusé. Autorisez-le dans le navigateur, ou déposez un fichier.';
  if (err && err.name === 'NotFoundError') return 'Aucun micro détecté sur cet appareil. Déposez un fichier.';
  return 'Micro indisponible ici. Déposez un fichier à la place.';
}

function fermerMicro() {
  fermerNiveau();
  libererEcran();
  // Sans ça, le navigateur laisse le voyant d'enregistrement allumé.
  if (micro) micro.getTracks().forEach((t) => t.stop());
  micro = null;
}

async function demarrerDictee() {
  if (demarrage || enregistreur) return;
  fermerRelu();

  const format = formatDisponible();
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !format) {
    refuserDictee(raisonMicro(null));
    return;
  }

  demarrage = true;
  $('dictee-aide').textContent = 'Ouverture du micro…';
  try {
    micro = await navigator.mediaDevices.getUserMedia({ audio: CONTRAINTES_MICRO });
  } catch (err) {
    refuserDictee(raisonMicro(err));
    return;
  } finally {
    demarrage = false;
  }

  niveauMesure = ouvrirNiveau(micro);

  const [type, extension] = format;
  morceaux = [];
  // Référence locale : `arreterDictee()` remet `enregistreur` à null dès l'appel
  // à stop(), or l'événement, lui, n'arrive qu'après.
  const reglages = { audioBitsPerSecond: DEBIT_DICTEE };
  if (type) reglages.mimeType = type;
  let recorder;
  try {
    recorder = new MediaRecorder(micro, reglages);
  } catch (_) {
    // Débit refusé par ce navigateur : mieux vaut un vocal lourd que pas de vocal.
    recorder = new MediaRecorder(micro, type ? { mimeType: type } : undefined);
  }
  enregistreur = recorder;

  recorder.addEventListener('dataavailable', (e) => {
    if (e.data && e.data.size) morceaux.push(e.data);
  });

  // Le micro peut lâcher sans qu'on le demande : iOS qui le reprend, un appel qui
  // arrive. Muet, on met en pause ; coupé, l'enregistrement s'arrête là, et on le dit.
  micro.getAudioTracks().forEach((piste) => {
    piste.addEventListener('mute', () =>
      suspendreDictee('En pause : le micro s’est interrompu. Touchez « Reprendre ».'));
    piste.addEventListener('ended', () => {
      if (enregistreur !== recorder) return;
      interruption = 'Le micro s’est coupé : l’enregistrement s’arrête là. Réécoutez avant d’envoyer.';
      arreterDictee(false);
    });
  });

  recorder.addEventListener('stop', () => {
    // Arrêté par le navigateur et non par un geste : le chronomètre tourne encore, et
    // il réécrirait le titre de la carte pendant la réécoute.
    if (enregistreur === recorder) {
      clearInterval(chronoDictee);
      chronoDictee = null;
      enregistreur = null;
      interruption = interruption ||
        'L’enregistrement s’est arrêté de lui-même. Réécoutez avant d’envoyer.';
    }
    const raison = interruption;
    interruption = '';
    const blob = new Blob(morceaux, { type: recorder.mimeType || type });
    const secondes = secondesDictee;
    const pic = picNiveau;
    const mesure = niveauMesure;
    const directe = dicteeDirecte;
    dicteeDirecte = false;
    fermerMicro();
    peindreDictee(false);

    // Arrêt demandé pour annuler, pas pour envoyer : « Recommencer » et « Refaire »
    // passent par ici, et ne doivent surtout pas déclencher un devis.
    if (dicteeAnnulee) {
      dicteeAnnulee = false;
      return;
    }

    if (secondes < DUREE_MIN || !blob.size) {
      $('dictee-aide').textContent = 'Trop court — parlez quelques secondes.';
      return;
    }

    // Le micro était ouvert mais n'a jamais rien capté : le dire ici épargne à
    // l'artisan un aller-retour chez Whisper et un écran d'erreur pour l'apprendre.
    if (mesure && pic < SEUIL_SILENCE) {
      $('dictee-aide').textContent =
        "Le micro n’a rien capté — vérifiez qu’il n’est pas coupé, ou déposez un fichier.";
      ouvrirRepli('depot-repli', 'depot-bascule', true);
      return;
    }

    // La durée vient du chronomètre et non du blob : un webm sorti de
    // MediaRecorder annonce presque toujours une durée infinie.
    const fichier = new File([blob], `dictee.${extension}`, { type: blob.type });
    // « Générer le devis » pendant la dictée : l'artisan a choisi d'envoyer sans
    // réécouter. Les deux refus ci-dessus s'appliquent quand même.
    if (directe) lancer(fichier, null, secondes);
    else {
      montrerRelu(fichier, secondes);
      if (raison) $('dictee-aide').textContent = raison;
    }
  });

  recorder.start();
  secondesDictee = 0;
  msAcquis = 0;
  debutSegment = Date.now();
  peindreDictee(true);
  garderEcranAllume();
  chronoDictee = setInterval(() => {
    // L'intervalle tourne pendant la pause — c'est lui qui dit à `basculerDictee`
    // qu'une dictée est en cours — mais il ne compte que ce qui s'enregistre.
    if (recorder.state === 'paused') return;
    secondesDictee = Math.floor((msAcquis + Date.now() - debutSegment) / 1000);
    $('dictee-titre').textContent = mmss(secondesDictee);
    if (secondesDictee >= DUREE_MAX) arreterDictee();
  }, 250);
}

/* `annuler` sépare les deux façons d'arrêter : le doigt qui met fin à la dictée
   pour l'envoyer, et le code qui la coupe pour en effacer la trace. L'événement
   `stop` du MediaRecorder n'arrive qu'après coup et ne saurait pas les distinguer
   tout seul — il enchaînerait sur l'envoi dans les deux cas. `directe` est le
   troisième cas : envoyer, mais sans passer par la réécoute. */
function arreterDictee(annuler, directe) {
  clearInterval(chronoDictee);
  chronoDictee = null;
  dicteeAnnulee = !!annuler;
  dicteeDirecte = !!directe;
  if (enregistreur && enregistreur.state !== 'inactive') {
    // Le dernier tic date d'un quart de seconde au plus : on solde le segment en
    // cours pour que la durée annoncée soit celle du fichier.
    if (enregistreur.state === 'recording') msAcquis += Date.now() - debutSegment;
    secondesDictee = Math.floor(msAcquis / 1000);
    enregistreur.stop();
  } else { fermerMicro(); peindreDictee(false); dicteeAnnulee = dicteeDirecte = false; }
  enregistreur = null;
}

function basculerDictee() {
  if (vocalPret) return;   // en réécoute, ce sont les deux boutons qui décident
  if (chronoDictee) arreterDictee(false);
  else demarrerDictee();
}

/* ---- parcours ---------------------------------------------------------- */

async function lancer(fichier, texte, dureeSecondes) {
  clearInterval(revelation);
  revelation = null;
  $('carte-transcription').hidden = true;
  $('transcription-directe').textContent = '';
  montrer('traitement');
  peindreEtapes(0);
  demarrerChrono();

  try {
    if (fichier) {
      // Gardé avant l'appel, pas après : c'est justement quand la transcription
      // échoue qu'on a besoin de retrouver l'audio.
      vocalCourant = { fichier, secondes: dureeSecondes };
      // Un vocal neuf : une transcription d'avant ne le concerne pas, et `reprendre()`
      // la rejouerait à sa place.
      transcriptionCourante = null;
      // Une dictée connaît sa durée : elle sort du chronomètre, pas du fichier.
      dureeVocal = formaterDuree(dureeSecondes != null ? dureeSecondes : await dureeAudio(fichier));
      transcriptionCourante = await transcrire(fichier);
    } else {
      // Sans fichier, le texte est déjà là. `dureeVocal` n'est pas touchée ici :
      // c'est l'appelant qui sait s'il colle un texte neuf (durée inconnue) ou s'il
      // rejoue le chiffrage d'une dictée dont on connaît déjà la durée.
      transcriptionCourante = texte;
    }
    const transcription = transcriptionCourante;

    peindreEtapes(1);
    $('carte-transcription').hidden = false;

    // Le texte collé, l'artisan vient de l'écrire : le dévoiler serait du théâtre.
    const devoilement = fichier
      ? devoiler(transcription, $('transcription-directe'))
      : Promise.resolve(($('transcription-directe').textContent = transcription));

    devisCourant = await chiffrer(transcription);
    devisEnvoye = false;
    peindreEtapes(2);

    await devoilement;
    peindreEtapes(3);
    clearInterval(chrono);

    if (devisCourant.lignes.length === 0) {
      peindreVide(devisCourant);
      setTimeout(() => montrer('vide'), 500);
    } else {
      peindreRelecture(devisCourant);
      preparerPdf();     // fabriqué pendant la relecture, prêt avant le premier appui
      // L'aperçu part en même temps que le PDF et non après : il ne coûte pas de
      // Chromium, il sera donc à l'image avant que l'artisan ait fini de lire son
      // en-tête. On ne l'attend pas — la relecture ne dépend pas de lui.
      vueRelecture = 'detail';
      appliquerVue();
      peindreApercu();
      poserGardeHistorique();
      setTimeout(() => montrer('relecture'), 500);
    }
  } catch (err) {
    if (err.porte) {
      // Le vocal et la transcription sont gardés ; l'étape refusée repartira d'elle-même
      // une fois le code saisi. `echouer()` recouvrirait la porte d'une impasse.
      clearInterval(chrono);
      clearInterval(revelation);
      aRejouer.add(reprendre);
      return;
    }
    echouer(err.message);
  }
}

/* ---- 3. relecture ------------------------------------------------------ */

function peindreRelecture(d) {
  direErreurPdf('');
  $('rel-numero').textContent = 'Devis n° ' + d.numero;
  $('rel-objet').textContent = d.type_travaux || 'Travaux à préciser';
  $('rel-client').textContent =
    [d.client.nom, d.client.adresse, d.client.telephone].filter(Boolean).join(' · ') ||
    'Client à compléter';

  $('rel-duree').textContent =
    'Transcription de la note vocale' + (dureeVocal ? ' · ' + dureeVocal : '');
  $('rel-transcription').textContent = d.transcription;

  const aValider = d.lignes.filter((l) => l.a_valider).length;
  $('rel-compte').textContent = aValider === 0
    ? 'Tous les prix ont été dictés'
    : `${aValider} ligne${aValider > 1 ? 's' : ''} sur ${d.lignes.length} ${aValider > 1 ? 'sont' : 'est'} à valider`;

  const lignes = $('rel-lignes');
  lignes.replaceChildren(...d.lignes.map(rangDeLigne));

  peindreObservations($('rel-observations'), $('rel-titre-observations'), d.notes);
  peindreTotaux(d);
}

function rangDeLigne(l) {
  const rang = noeud('div', 'table__row table__line');

  const gauche = noeud('div');
  gauche.append(noeud('div', 'table__desc', l.designation));
  if (l.detail) gauche.append(noeud('div', 'table__detail', l.detail));
  // Le signal d'honnêteté. Discret volontairement : c'est une information, pas une
  // alarme. Ce qui le rend repérable, c'est sa répétition dans la colonne de gauche.
  if (l.a_valider) gauche.append(noeud('div', 'tag-estime', 'Prix estimé · à valider'));

  // Les libellés voyagent avec les cellules plutôt que dans l'en-tête du tableau :
  // sous 640px celui-ci disparaît, les lignes s'empilent, et un chiffre nu ne dit
  // plus s'il est une quantité ou un prix. C'est le CSS qui les révèle, par
  // `content: attr(data-l)` — le balisage reste le même aux deux largeurs.
  const cellule = (classe, texte, libelle) => {
    const el = noeud('div', classe, texte);
    el.dataset.l = libelle;
    return el;
  };

  rang.append(
    gauche,
    cellule('r num', nombre(l.quantite), 'Qté'),
    cellule('r unit', l.unite, 'Unité'),
    cellule('r num', money(l.prix_unitaire_ht), 'P.U. HT'),
    cellule('r num total', money(l.total_ht), 'Total HT'),
  );
  return rang;
}

function peindreObservations(conteneur, titre, notes) {
  titre.hidden = notes.length === 0;
  conteneur.replaceChildren(...notes.map((note) => {
    const obs = noeud('div', 'observation');
    obs.append(noeud('em', null, '—'), noeud('p', null, note));
    return obs;
  }));
}

function peindreTotaux(d) {
  const totaux = $('rel-totaux');
  totaux.replaceChildren();

  const rang = (classe, libelle, montant, mono) => {
    const el = noeud('div', classe);
    el.append(noeud('span', null, libelle), noeud('span', mono ? 'num' : null, montant));
    return el;
  };

  const franchise = !!(d.entreprise && d.entreprise.franchise_tva);
  totaux.append(
    rang('totaux__row', 'Total HT', euro(d.total_ht), true),
    franchise
      ? rang('totaux__row', 'TVA non applicable · art. 293 B', '—', false)
      : rang('totaux__row', `TVA ${pourcent(d.taux_tva)}${NB}%`, euro(d.montant_tva), true),
    rang('totaux__ttc', franchise ? 'Net à payer' : 'Total TTC', euro(d.total_ttc), false),
    rang('totaux__acompte', `Acompte ${pourcent(d.acompte_pct)}${NB}%`, euro(d.montant_acompte), true),
  );

  if (Number(d.total_ht_estime) > 0) {
    totaux.append(noeud('div', 'totaux__note',
      `Dont ${euro(d.total_ht_estime)} de prix estimés. Relisez ces lignes avant l'envoi.`));
  }
}

/* ---- 3 bis. édition ---------------------------------------------------- */
/* Hors périmètre d'origine, ajoutée sur décision explicite — voir CLAUDE.md
   § Périmètre. C'est la réponse à « et si c'est faux ? », la première question
   d'un artisan devant la démonstration.

   Le navigateur ne calcule rien, ici moins qu'ailleurs : il relit ce que
   l'artisan a saisi, l'envoie avec le devis d'origine, et c'est le serveur qui
   renvoie le devis recalculé. Aucun total n'est affiché pendant la saisie — un
   total calculé ici serait une seconde arithmétique à garder juste. */

const UNITES = ['m²', 'ml', 'm³', 'u', 'forfait', 'h', 'j'];
let numeroChampEdition = 0;

/* « 1 200,50 » comme « 1200.5 » : ce que l'artisan tape, rendu lisible par le
   serveur. Rendu en chaîne et pas en nombre : le Decimal se construit de l'autre
   côté, sans passer par la virgule flottante. `null` quand c'est illisible. */
function lireNombre(texte) {
  const brut = String(texte).replace(/[\s  ]/g, '');
  if (AMBIGU.test(brut)) return null;
  const nombre = brut.replace(',', '.');
  return /^\d+(\.\d+)?$/.test(nombre) ? nombre : null;
}

/* « 1.200 » : à la française, un point suivi de trois chiffres sépare les milliers ;
   sur un clavier anglais, il sépare les décimales. Deviner, c'est risquer un prix
   divisé par mille sans aucun message : on refuse, et on dit comment l'écrire. */
const AMBIGU = /^\d{1,3}(\.\d{3})+$/;
const sansEspaces = (texte) => String(texte).replace(/\s/g, '');

function champEdition(classe, libelle, el) {
  const champ = noeud('div', 'champ' + (classe ? ' ' + classe : ''));
  el.id = 'ed-champ-' + (++numeroChampEdition);
  const etiquette = noeud('label', null, libelle);
  etiquette.htmlFor = el.id;
  champ.append(etiquette, el);
  return champ;
}

function editeurDeLigne(l) {
  const rang = noeud('div', 'ligne-ed');

  const saisie = (cle, valeur, classe) => {
    const el = noeud('input', classe);
    el.dataset.cle = cle;
    el.autocomplete = 'off';
    el.value = valeur;
    return el;
  };

  const designation = saisie('designation', l.designation || '');
  const quantite = saisie('quantite', l.quantite != null ? nombre(l.quantite) : '', 'num');
  const prix = saisie('prix', l.prix_unitaire_ht != null ? nombre(l.prix_unitaire_ht) : '', 'num');
  const detail = saisie('detail', l.detail || '');
  quantite.inputMode = prix.inputMode = 'decimal';
  detail.placeholder = 'Fourniture client, marque, finition…';

  const unite = noeud('select');
  unite.dataset.cle = 'unite';
  UNITES.forEach((u) => {
    const option = noeud('option', null, u);
    option.value = u;
    unite.append(option);
  });
  unite.value = UNITES.includes(l.unite) ? l.unite : 'u';

  const retirer = noeud('button', 'ligne-ed__retirer');
  retirer.type = 'button';
  retirer.setAttribute('aria-label', 'Retirer cette ligne');
  retirer.append(icone('i-croix'));
  retirer.addEventListener('click', () => rang.remove());

  rang.append(
    champEdition('champ--designation', 'Désignation', designation),
    champEdition(null, 'Qté', quantite),
    champEdition(null, 'Unité', unite),
    champEdition('champ--prix', 'P.U. HT (€)', prix),
    retirer,
    champEdition('champ--detail', 'Détail (facultatif)', detail),
  );

  // Un prix estimé le reste tant que l'artisan ne l'a pas pris à son compte. Taper
  // un prix, c'est le prendre : la case se coche d'elle-même. La laisser vide,
  // c'est garder la mention « estimé » sur le PDF — elle ne disparaît jamais seule.
  if (l.a_valider) {
    rang.classList.add('est-estimee');
    const verifie = noeud('input');
    verifie.type = 'checkbox';
    verifie.dataset.cle = 'verifie';
    const etiquette = noeud('label', 'ligne-ed__prix');
    etiquette.append(verifie, document.createTextNode('Prix estimé · cocher une fois vérifié'));
    rang.append(etiquette);
    prix.addEventListener('input', () => { verifie.checked = true; });
  }
  return rang;
}

/* Relit les lignes de l'écran. Les erreurs de lecture — un « 12,5,3 » dans la
   quantité — sont dites ici, au champ près ; les règles du devis, elles, sont
   tenues par le serveur, qui renvoie le numéro de la ligne fautive. */
function lireLignes() {
  const erreurs = [];
  const lignes = [...$('ed-lignes').children].map((rang, i) => {
    const champ = (cle) => rang.querySelector(`[data-cle="${cle}"]`);
    const quantite = lireNombre(champ('quantite').value);
    const prix = lireNombre(champ('prix').value);
    const designation = champ('designation').value.trim();

    champ('designation').setAttribute('aria-invalid', String(!designation));
    champ('quantite').setAttribute('aria-invalid', String(quantite === null));
    champ('prix').setAttribute('aria-invalid', String(prix === null));
    const motif = (el) => {
      if (!el.value.trim()) return 'est vide';
      if (AMBIGU.test(sansEspaces(el.value))) return 'est ambigu : écrivez 1200, ou 1,2 pour une décimale';
      return 'est illisible';
    };
    if (!designation) erreurs.push(`Ligne ${i + 1} : la désignation est vide.`);
    if (quantite === null) erreurs.push(`Ligne ${i + 1} : la quantité ${motif(champ('quantite'))}.`);
    if (prix === null) erreurs.push(`Ligne ${i + 1} : le prix unitaire ${motif(champ('prix'))}.`);

    const verifie = champ('verifie');
    return {
      designation,
      detail: champ('detail').value.trim() || null,
      quantite,
      unite: champ('unite').value,
      prix_unitaire_ht: prix,
      a_valider: verifie ? !verifie.checked : false,
    };
  });
  return { lignes, erreurs };
}

function basculerEdition(actif) {
  $('rel-lecture').hidden = actif;
  $('rel-edition').hidden = !actif;
  $('ecran-relecture').classList.toggle('en-edition', actif);
  // Sur un téléphone, l'édition vit dans la vue « Détail » : si l'artisan était
  // sur l'aperçu, on l'y ramène plutôt que de lui ouvrir un formulaire invisible.
  if (actif && vueRelecture !== 'detail') {
    vueRelecture = 'detail';
    appliquerVue();
  }
}

function ouvrirEdition() {
  const d = devisCourant;
  if (!d) return;
  $('ed-client-nom').value = d.client.nom || '';
  $('ed-client-adresse').value = d.client.adresse || '';
  $('ed-client-telephone').value = d.client.telephone || '';
  $('ed-objet').value = d.type_travaux || '';
  $('ed-duree').value = d.duree_estimee || '';
  // En franchise, le taux n'est pas un choix : le sélecteur montre le régime et se
  // fige. Le serveur l'applique de toute façon, quel que soit le taux envoyé.
  const franchise = !!(d.entreprise && d.entreprise.franchise_tva);
  $('ed-tva-franchise').hidden = $('ed-tva-franchise').disabled = !franchise;
  $('ed-tva').disabled = franchise;
  $('ed-tva').value = franchise ? '0'
    : Number(d.taux_tva) > 0.15 ? '0.20'
    : Number(d.taux_tva) < 0.08 ? '0.055' : '0.10';
  $('ed-lignes').replaceChildren(...d.lignes.map(editeurDeLigne));
  $('ed-notes').value = d.notes.join('\n');
  $('ed-erreur').textContent = '';
  $('ed-pro-recherche').value = '';
  $('ed-pro-etat').textContent = '';
  $('ed-pro-resultats').hidden = true;
  fermerRepli('ed-pro', 'ed-pro-bascule');
  viderSuggestionsAdresse();
  basculerEdition(true);
  $('rel-edition').scrollIntoView({ behavior: MOUVEMENT_REDUIT ? 'auto' : 'smooth', block: 'start' });
}

function fermerEdition() {
  viderSuggestionsAdresse();
  basculerEdition(false);
}

async function validerEdition(e) {
  if (e) e.preventDefault();   // sans événement : rejouée après la porte
  const { lignes, erreurs } = lireLignes();
  if (!lignes.length) erreurs.push('Un devis doit compter au moins une ligne.');
  if (erreurs.length) {
    $('ed-erreur').textContent = erreurs[0];
    return;
  }

  const corrections = {
    client: {
      nom: $('ed-client-nom').value,
      adresse: $('ed-client-adresse').value,
      telephone: $('ed-client-telephone').value,
    },
    type_travaux: $('ed-objet').value,
    duree_estimee: $('ed-duree').value,
    taux_tva: $('ed-tva').value,
    lignes,
    notes: $('ed-notes').value.split('\n').map((n) => n.trim()).filter(Boolean),
  };

  const bouton = $('ed-valider');
  bouton.disabled = true;
  $('ed-erreur').textContent = '';
  try {
    const r = await poste('/api/devis/corriger', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ devis: devisCourant, corrections }),
    });
    devisCourant = await r.json();
  } catch (err) {
    // Code refusé : le formulaire attend derrière la porte, et la validation repart
    // telle quelle une fois le code saisi.
    if (err.porte) aRejouer.add(validerEdition);
    // Le formulaire reste tel quel : l'artisan corrige la ligne citée et revalide,
    // sans rien perdre de ce qu'il a déjà saisi.
    else $('ed-erreur').textContent = err.message;
    return;
  } finally {
    bouton.disabled = false;
  }

  devisEnvoye = false;   // le devis a changé : celui qui est parti n'est plus celui-ci
  fermerEdition();
  peindreRelecture(devisCourant);
  // Le PDF déjà fabriqué et l'aperçu sont ceux d'avant la correction.
  preparerPdf();
  peindreApercu();
  window.scrollTo(0, 0);
}

/* ---- l'adresse du client ----------------------------------------------- */
/* Autocomplétée depuis la Base Adresse Nationale, par le serveur. Il n'existe pas
   d'annuaire des particuliers, et c'est très bien ainsi : on complète l'adresse
   qu'on tape, on ne la devine pas à partir d'un nom. */

let minuterieAdresse = null;
let numeroAdresse = 0;
let suggestionsAdresse = [];
let indexAdresse = -1;

function viderSuggestionsAdresse() {
  clearTimeout(minuterieAdresse);
  numeroAdresse++;   // une réponse encore en route ne rouvrira pas la liste
  suggestionsAdresse = [];
  indexAdresse = -1;
  $('ed-adresse-resultats').replaceChildren();
  $('ed-adresse-resultats').hidden = true;
  $('ed-client-adresse').setAttribute('aria-expanded', 'false');
  $('ed-client-adresse').removeAttribute('aria-activedescendant');
}

function choisirAdresse(adresse) {
  $('ed-client-adresse').value = adresse.libelle;
  viderSuggestionsAdresse();
}

async function chercherAdresse(requete) {
  const q = requete.trim();
  if (q.length < 5) { viderSuggestionsAdresse(); return; }

  const moi = ++numeroAdresse;
  let trouvees = [];
  try {
    const r = await fetch('/api/adresse?q=' + encodeURIComponent(q), { headers: enteteAcces() });
    if (r.ok) trouvees = await r.json();
  } catch (_) { /* hors réseau : l'artisan tape l'adresse en entier */ }
  if (moi !== numeroAdresse) return;

  suggestionsAdresse = trouvees;
  indexAdresse = -1;
  const boite = $('ed-adresse-resultats');
  boite.replaceChildren(...trouvees.map((a, i) => {
    const option = noeud('button', 'resultat');
    option.type = 'button';
    option.id = 'ed-adresse-' + i;
    option.setAttribute('role', 'option');
    option.append(noeud('div', 'resultat__nom', a.libelle));
    if (a.contexte) option.append(noeud('div', 'resultat__meta', a.contexte));
    option.addEventListener('click', () => choisirAdresse(a));
    return option;
  }));
  boite.hidden = trouvees.length === 0;
  $('ed-client-adresse').setAttribute('aria-expanded', String(trouvees.length > 0));
}

function surlignerAdresse(index) {
  indexAdresse = index;
  [...$('ed-adresse-resultats').children].forEach((option, i) =>
    option.setAttribute('aria-selected', String(i === index)));
  $('ed-client-adresse').setAttribute('aria-activedescendant', 'ed-adresse-' + index);
}

/* ---- le client professionnel ------------------------------------------- */
/* Un syndic, une SCI, un commerce : ceux-là sont dans l'annuaire des entreprises,
   le même que pour l'identité de l'artisan. Le nom et l'adresse s'y retrouvent ;
   le téléphone reste à saisir, la base ne le connaît pas. */

let minuteriePro = null;
let numeroPro = 0;

async function chercherClientPro(requete) {
  const q = requete.trim();
  const boite = $('ed-pro-resultats');
  if (q.length < 3) {
    boite.hidden = true;
    $('ed-pro-etat').textContent = '';
    return;
  }

  const moi = ++numeroPro;
  $('ed-pro-etat').textContent = 'Recherche…';
  let trouvees = [];
  try {
    const r = await fetch('/api/entreprise?q=' + encodeURIComponent(q), { headers: enteteAcces() });
    if (r.ok) trouvees = await r.json();
  } catch (_) { /* annuaire injoignable : la saisie à la main reste ouverte */ }
  if (moi !== numeroPro) return;

  boite.replaceChildren(...trouvees.map((e) => {
    const bouton = noeud('button', 'resultat');
    bouton.type = 'button';
    bouton.append(
      noeud('div', 'resultat__nom', e.nom),
      noeud('div', 'resultat__meta', [e.adresse, e.code_postal_ville].filter(Boolean).join(', ')),
    );
    bouton.addEventListener('click', () => choisirClientPro(e));
    return bouton;
  }));
  boite.hidden = trouvees.length === 0;
  $('ed-pro-etat').textContent = trouvees.length
    ? ''
    : 'Aucune entreprise trouvée. Complétez le nom et l’adresse à la main.';
}

function choisirClientPro(e) {
  $('ed-client-nom').value = e.nom;
  $('ed-client-adresse').value = [e.adresse, e.code_postal_ville].filter(Boolean).join(', ');
  $('ed-pro-recherche').value = '';
  $('ed-pro-resultats').hidden = true;
  $('ed-pro-etat').textContent = 'Rempli depuis l’annuaire. Ajoutez le téléphone si vous l’avez.';
  $('ed-client-telephone').focus({ preventScroll: true });
}

/* ---- branchements de l'édition ----------------------------------------- */

$('btn-modifier').addEventListener('click', ouvrirEdition);
$('ed-annuler').addEventListener('click', fermerEdition);
$('rel-edition').addEventListener('submit', validerEdition);

$('ed-ajouter').addEventListener('click', () => {
  const rang = editeurDeLigne({ quantite: 1, unite: 'u' });
  $('ed-lignes').append(rang);
  rang.querySelector('[data-cle="designation"]').focus();
});

$('ed-pro-bascule').addEventListener('click', () => {
  const ouvert = !$('ed-pro').classList.contains('is-open');
  ouvrirRepli('ed-pro', 'ed-pro-bascule', ouvert);
  if (ouvert) $('ed-pro-recherche').focus({ preventScroll: true });
});

$('ed-pro-recherche').addEventListener('input', (e) => {
  clearTimeout(minuteriePro);
  const valeur = e.target.value;
  minuteriePro = setTimeout(() => chercherClientPro(valeur), 320);
});
// Entrée lance la recherche, et surtout pas la validation du formulaire entier.
$('ed-pro-recherche').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  e.preventDefault();
  clearTimeout(minuteriePro);
  chercherClientPro(e.target.value);
});

/* Le débit autorisé est partagé par toutes les routes : on attend une vraie pause
   dans la frappe avant de consommer une requête. */
$('ed-client-adresse').addEventListener('input', (e) => {
  clearTimeout(minuterieAdresse);
  const valeur = e.target.value;
  minuterieAdresse = setTimeout(() => chercherAdresse(valeur), 350);
});

$('ed-client-adresse').addEventListener('keydown', (e) => {
  const n = suggestionsAdresse.length;
  if (!n || $('ed-adresse-resultats').hidden) return;
  if (e.key === 'ArrowDown') { e.preventDefault(); surlignerAdresse((indexAdresse + 1) % n); }
  else if (e.key === 'ArrowUp') { e.preventDefault(); surlignerAdresse((indexAdresse - 1 + n) % n); }
  else if (e.key === 'Escape') { e.preventDefault(); viderSuggestionsAdresse(); }
  else if (e.key === 'Enter' && indexAdresse >= 0) {
    e.preventDefault();
    choisirAdresse(suggestionsAdresse[indexAdresse]);
  }
});

/* La liste se ferme quand on quitte le champ — mais après coup : un clic sur une
   suggestion fait d'abord perdre le focus au champ, et fermer tout de suite
   retirerait la suggestion sous le doigt avant que le clic n'arrive. */
$('ed-client-adresse').addEventListener('blur', () => {
  setTimeout(viderSuggestionsAdresse, 180);
});

/* ---- 4. devis vide ----------------------------------------------------- */

function peindreVide(d) {
  $('vide-source').textContent = 'Note vocale' + (dureeVocal ? ' · ' + dureeVocal : '');
  // Les guillemets français, et la transcription entière : elle prouve que
  // l'écoute a bien eu lieu, ce qui est tout l'objet de cet écran.
  $('vide-transcription').textContent = '« ' + d.transcription + ' »';
  peindreObservations($('vide-observations'), $('vide-titre-observations'), d.notes);
}

/* ---- aperçu du document ------------------------------------------------ */
/* La feuille A4 telle qu'elle partira, à côté du tableau qu'on relit. Elle sort du
   même template et de la même feuille de style que le PDF — c'est `/api/apercu`, qui
   rend le document en HTML au lieu de le passer par Chromium. Deux conséquences
   utiles : elle est là tout de suite, et elle se redimensionne.

   Le document est rendu à sa largeur vraie — 794px, soit 210mm à 96 ppp, exactement
   ce que Chromium donne à la feuille — puis mis à l'échelle. Le réduire en changeant
   sa largeur, lui, changerait ses retours à la ligne : l'aperçu ne montrerait plus
   le même document que le PDF, ce qui est précisément ce qu'on veut éviter. */

const LARGEUR_A4 = 794;
const ETROIT = window.matchMedia('(max-width: 1180px)');
/* Le menu latéral du compte, ouvert, pousse la page de 272px : dans cette plage de
   fenêtre il ne reste plus la place des deux colonnes. Même règle, mêmes bornes,
   dans la section « Adaptation » d'app.css. Sans compte, la classe n'est jamais
   posée et seul `ETROIT` compte, comme avant. */
const ETROIT_AVEC_MENU = window.matchMedia('(min-width: 1181px) and (max-width: 1452px)');
const estEtroit = () => ETROIT.matches ||
  (ETROIT_AVEC_MENU.matches && document.body.classList.contains('menu-ouvert'));
let vueRelecture = 'detail';

function viderApercu() {
  $('apercu-plan').hidden = true;
  $('apercu-attente').hidden = false;
  $('apercu-pages').textContent = '';
  // `onload` en propriété et non en écouteur : vider puis remplir le cadre produit
  // deux chargements — celui de la page blanche, puis celui du devis. Un écouteur
  // `once` serait consommé par le premier, et le devis arriverait sans que
  // personne n'écoute. Une propriété, elle, se remplace.
  const cadre = $('apercu-cadre');
  cadre.onload = null;
  cadre.srcdoc = '';
}

async function peindreApercu() {
  viderApercu();

  let html;
  try {
    const r = await poste('/api/apercu', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(devisCourant),
    });
    // Le template de l'artisan a échoué et le serveur a rejoué sur celui livré. On
    // le sait donc **avant** le téléchargement, au moment où il regarde le
    // document : c'est là que le dire sert à quelque chose.
    if (r.headers.get('X-Template-Repli') === '1') {
      pdfSurTemplateDeRepli = true;
      signalerRepliTemplate();
    }
    html = await r.text();
  } catch (err) {
    if (err.porte) aRejouer.add(peindreApercu);
    // L'aperçu est un confort ; le devis, lui, est intact et téléchargeable. Un
    // aperçu qui rate ne doit surtout pas interrompre la relecture.
    $('apercu-attente').hidden = true;
    $('apercu-pages').textContent = 'Aperçu indisponible';
    return;
  }

  const cadre = $('apercu-cadre');
  cadre.onload = () => {
    const doc = cadre.contentDocument;
    // La page blanche qui précède le devis passe aussi par ici : elle n'a pas de
    // contenu, et il n'y a rien à mesurer dessus.
    if (!doc || !doc.body || !doc.body.firstElementChild) return;
    let pose = false;
    const poser = () => {
      if (pose) return;
      pose = true;
      // Le template livré découpe le document en `.page` ; celui d'un artisan n'y
      // est pas tenu. Sans repère, on n'annonce pas de pagination plutôt que d'en
      // inventer une.
      const pages = doc.querySelectorAll('.page').length;
      $('apercu-pages').textContent = pages ? pages + (pages > 1 ? ' pages' : ' page') : '';
      $('apercu-attente').hidden = true;
      $('apercu-plan').hidden = false;
      ajusterApercu();
    };
    // Sans attendre les polices, la hauteur se mesure sur la police de repli et
    // l'aperçu se coupe de quelques lignes. Elles sont déjà en cache — la page les
    // a chargées — donc l'attente est de l'ordre de la milliseconde. Le garde-fou
    // à 1,5 s existe parce qu'un cadre encore masqué ne demande aucune police, et
    // que `fonts.ready` peut alors ne jamais se résoudre : mieux vaut un aperçu
    // mesuré d'un cheveu trop court qu'un aperçu qui n'arrive jamais.
    // `.then(poser).catch(poser)` aurait été plus court, et aurait avalé toute
    // exception de `poser` lui-même en la rattrapant comme un échec de police —
    // c'est exactement ce qui a masqué un aperçu resté à sa taille par défaut.
    // Le `catch` ne couvre donc que l'attente, et rien d'autre.
    const polices = doc.fonts && doc.fonts.ready ? doc.fonts.ready : Promise.resolve();
    polices.catch(() => {}).then(poser);
    setTimeout(poser, 1500);
  };

  // `srcdoc` plutôt qu'une URL : le document hérite alors de l'origine de la page,
  // donc `/static/fonts.css` s'y charge normalement. Le bac à sable posé dans le
  // balisage n'accorde pas `allow-scripts` — un template d'artisan s'affiche, il ne
  // s'exécute pas.
  cadre.srcdoc = html;
}

function ajusterApercu() {
  const plan = $('apercu-plan');
  const cadre = $('apercu-cadre');
  if (plan.hidden) return;
  const doc = cadre.contentDocument;
  if (!doc) return;

  const bureau = $('apercu-bureau');
  const style = getComputedStyle(bureau);
  const dispo = bureau.clientWidth
    - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  if (dispo <= 0) return;

  const echelle = Math.min(1, dispo / LARGEUR_A4);
  const hauteur = Math.max(doc.documentElement.scrollHeight, doc.body.scrollHeight);

  cadre.style.height = hauteur + 'px';
  cadre.style.transform = 'scale(' + echelle.toFixed(4) + ')';
  // Un élément mis à l'échelle occupe toujours sa taille d'origine dans la mise en
  // page : c'est le plan qui porte la taille visible, sans quoi le bureau
  // défilerait sur du vide.
  plan.style.width = Math.round(LARGEUR_A4 * echelle) + 'px';
  plan.style.height = Math.round(hauteur * echelle) + 'px';
}

/* Les deux vues de la relecture. Côte à côte dès qu'il y a la place ; en dessous,
   elles se relaient sous le commutateur. Le `hidden` n'est posé que dans le cas
   étroit — sur un large écran, cacher la moitié du contenu au lecteur d'écran
   alors qu'elle est à l'image serait un mensonge. */
function appliquerVue() {
  const etroit = estEtroit();
  const surApercu = vueRelecture === 'apercu';

  $('vue-detail').hidden = etroit && surApercu;
  $('vue-apercu').hidden = etroit && !surApercu;

  document.querySelectorAll('.bascule__onglet').forEach((onglet) => {
    const actif = onglet.dataset.vue === vueRelecture;
    onglet.classList.toggle('is-active', actif);
    onglet.setAttribute('aria-selected', String(actif));
  });

  if (!etroit || surApercu) ajusterApercu();
}

/* ---- PDF --------------------------------------------------------------- */

/* L'artisan ne veut pas un fichier, il veut que sa cliente l'ait. Sur un téléphone,
   un PDF téléchargé atterrit dans « Fichiers » et le parcours s'arrête là ; la feuille
   de partage du système, elle, mène à WhatsApp, aux messages, au courrier. Ce n'est
   pas une intégration — c'est le partage de l'OS, et il tient en un appel. */

/* Vrai quand le serveur a dû rejouer le PDF sur le template livré parce que celui de
   l'artisan a échoué. Sorti du corps de `genererPdf` parce que le blob voyage seul
   jusqu'au bouton, et que l'avertissement, lui, doit survivre au trajet. */
let pdfSurTemplateDeRepli = false;

async function genererPdf() {
  const r = await poste('/api/pdf', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(devisCourant),
  });
  // Un devis sorti sur une autre mise en page que celle qu'on croit est exactement le
  // genre de substitution silencieuse que ce produit refuse ailleurs — voir le bandeau
  // du mode hors-ligne. Même principe : on le dit.
  pdfSurTemplateDeRepli = r.headers.get('X-Template-Repli') === '1';
  return await r.blob();
}

/* Le partage de fichiers ne se déduit pas de la présence de `navigator.share` :
   Chrome sur bureau l'expose et refuse les fichiers. On lui soumet un PDF vide. */
function saitPartagerUnPdf() {
  try {
    return !!navigator.canShare &&
      navigator.canShare({ files: [new File([], 'devis.pdf', { type: 'application/pdf' })] });
  } catch (_) {
    return false;
  }
}

/* Deux précautions que le chemin naïf oublie, et qui se paient précisément sur
   l'appareil de l'artisan : l'ancre doit être dans le document — Safari ignore les
   ancres détachées — et l'URL du blob ne doit pas être révoquée dans la foulée du
   clic, sinon le téléchargement est annulé avant d'avoir commencé. */
function telecharger(blob, nom) {
  const url = URL.createObjectURL(blob);
  const lien = document.createElement('a');
  lien.href = url;
  lien.download = nom;
  lien.style.display = 'none';
  document.body.append(lien);
  lien.click();
  setTimeout(() => { lien.remove(); URL.revokeObjectURL(url); }, 60_000);
}

/* Le PDF est fabriqué dès que le devis s'affiche, pendant que l'artisan le relit.
   Deux raisons, et la seconde est la vraie : le bouton devient instantané, et surtout
   `navigator.share()` doit partir dans la fenêtre d'activation du geste. Deux secondes
   de Chromium entre l'appui et l'appel la laissaient expirer — le partage était refusé
   sans qu'aucune feuille ne s'ouvre, et l'écran restait muet. */
let pdfPret = null;

function preparerPdf() {
  const promesse = genererPdf();
  // L'échec sera revu au clic ; pas de rejet non traité. Un code refusé, lui, se
  // rattrape dès la porte franchie.
  promesse.catch((err) => { if (err.porte) aRejouer.add(preparerPdf); });
  pdfPret = promesse;
}

/* Le PDF préparé pendant la relecture peut avoir échoué — coupure 4G, 429, Chromium
   qui hoquette — alors que le devis, lui, est intact en mémoire. Au geste, on refait
   donc le fichier une fois au lieu de rendre l'échec d'il y a deux minutes. */
async function obtenirPdf() {
  if (pdfPret) {
    try {
      return await pdfPret;
    } catch (_) { /* refait ci-dessous */ }
  }
  pdfPret = genererPdf();
  pdfPret.catch(() => {});
  return await pdfPret;
}

function direErreurPdf(texte) {
  const zone = $('pdf-erreur');
  zone.textContent = texte;
  zone.hidden = !texte;
}

async function envoyerPdf() {
  const bouton = $('btn-pdf');
  // Le libellé est un élément à part, et pas le contenu du bouton : celui-ci porte
  // aussi une icône, qu'un `textContent = …` effacerait au premier changement d'état.
  const libelle = $('btn-pdf-libelle');
  const dit = libelle.textContent;
  bouton.disabled = true;
  // Le libellé d'attente n'apparaît que si l'attente a lieu : le PDF est en général
  // déjà prêt, et un « Génération… » qui clignote se lit comme un défaut.
  const attente = setTimeout(() => { libelle.textContent = 'Génération…'; }, 150);

  let blob;
  direErreurPdf('');
  try {
    blob = await obtenirPdf();
  } catch (err) {
    // On reste sur la relecture, devis corrigé compris : l'appui suivant refera le
    // fichier. L'écran d'erreur, lui, ne proposait que de rejouer le chiffrage — un
    // appel payant de plus, et un autre devis à la place de celui-ci.
    if (!err.porte) {
      direErreurPdf(`Le PDF n'a pas pu être produit. ${err.message} ` +
        'Votre devis est intact : touchez de nouveau le bouton pour réessayer.');
    }
    return;
  } finally {
    clearTimeout(attente);
    bouton.disabled = false;
    libelle.textContent = dit;
  }

  const nom = `${devisCourant.numero}.pdf`;
  let partage = false;

  if (saitPartagerUnPdf()) {
    const debut = Date.now();
    try {
      await navigator.share({
        files: [new File([blob], nom, { type: 'application/pdf' })],
        title: `Devis ${devisCourant.numero}`,
      });
      partage = true;
      devisEnvoye = true;
    } catch (err) {
      // Une feuille de partage vraiment ouverte, puis refermée par l'artisan, prend
      // au moins quelques centaines de millisecondes. Un refus instantané veut dire
      // que rien ne s'est affiché : dans ce cas on enregistre le fichier, parce que
      // laisser quelqu'un devant un bouton qui ne fait rien est le pire des deux.
      partage = err && err.name === 'AbortError' && Date.now() - debut > 250;
    }
  }

  if (!partage) {
    telecharger(blob, nom);
    devisEnvoye = true;
    // Sur un téléphone, un téléchargement se signale par une notification qu'on
    // rate. Le bouton, lui, est sous le doigt.
    libelle.textContent = 'Devis enregistré';
    setTimeout(() => { libelle.textContent = dit; }, 2200);
  }

  if (pdfSurTemplateDeRepli) signalerRepliTemplate();
}

/* Le template de l'artisan a échoué sur ce devis-là et le serveur a rejoué sur celui
   livré. Le document est bon — il est simplement sur l'autre mise en page. Le dire au
   moment où l'artisan l'a en main, pas dans un log qu'il ne lira jamais. */
function signalerRepliTemplate() {
  const zone = $('bandeau-template');
  if (!zone) return;
  zone.hidden = false;
  $('bandeau').hidden = false;
  zone.scrollIntoView({ behavior: MOUVEMENT_REDUIT ? 'auto' : 'smooth', block: 'nearest' });
}

/* ---- branchements ------------------------------------------------------ */

const dictee = $('dictee');
dictee.addEventListener('click', basculerDictee);
dictee.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    basculerDictee();
  }
});
$('btn-pause-dictee').addEventListener('click', basculerPause);
$('btn-generer-dictee').addEventListener('click', () => {
  if (chronoDictee) arreterDictee(false, true);
});
$('btn-annuler-dictee').addEventListener('click', () => {
  if (chronoDictee) arreterDictee(true);
});

const depot = $('depot');

depot.addEventListener('click', () => $('fichier').click());
depot.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault();
    $('fichier').click();
  }
});

['dragenter', 'dragover'].forEach((evt) =>
  depot.addEventListener(evt, (e) => { e.preventDefault(); depot.classList.add('is-over'); }));
['dragleave', 'drop'].forEach((evt) =>
  depot.addEventListener(evt, (e) => { e.preventDefault(); depot.classList.remove('is-over'); }));
depot.addEventListener('drop', (e) => {
  if (e.dataTransfer.files.length) lancer(e.dataTransfer.files[0], null);
});

$('fichier').addEventListener('change', (e) => {
  if (e.target.files.length) lancer(e.target.files[0], null);
});

$('depot-bascule').addEventListener('click', () =>
  ouvrirRepli('depot-repli', 'depot-bascule', !$('depot-repli').classList.contains('is-open')));
$('repli-bascule').addEventListener('click', () =>
  ouvrirRepli('repli', 'repli-bascule', !$('repli').classList.contains('is-open')));

$('btn-texte').addEventListener('click', () => {
  const texte = $('texte').value.trim();
  if (!texte) return;
  // Texte collé : il n'y a pas de vocal, ni à dater ni à retranscrire.
  dureeVocal = null;
  vocalCourant = null;
  lancer(null, texte);
});

/* Les deux issues de la réécoute. « Établir le devis » envoie exactement le blob
   qu'on vient d'entendre ; « Refaire » le jette et rouvre le micro. */
$('btn-envoyer-dictee').addEventListener('click', () => {
  if (!vocalPret) return;
  const { fichier, secondes } = vocalPret;
  fermerRelu();
  peindreDictee(false);
  lancer(fichier, null, secondes);
});

$('btn-refaire-dictee').addEventListener('click', () => {
  fermerRelu();
  peindreDictee(false);
  demarrerDictee();
});

/* `dureeVocal` n'est pas touchée : c'est toujours la même note vocale, elle a
   toujours la même durée. */
$('btn-reprendre').addEventListener('click', reprendre);

// Le lecteur de réécoute. Écouteurs posés une fois pour toutes, pas à chaque dictée.
$('relu-jouer').addEventListener('click', basculerLecture);
$('relu-audio').addEventListener('timeupdate', peindreLecteur);
$('relu-audio').addEventListener('play', () => {
  $('lecteur').classList.add('joue');
  $('relu-jouer').setAttribute('aria-label', 'Mettre en pause');
});
$('relu-audio').addEventListener('pause', () => {
  $('lecteur').classList.remove('joue');
  $('relu-jouer').setAttribute('aria-label', "Écouter l'enregistrement");
});
$('relu-audio').addEventListener('ended', () => {
  $('relu-audio').currentTime = 0;
  peindreLecteur();
});

$('relu-piste').addEventListener('click', (e) => {
  const boite = e.currentTarget.getBoundingClientRect();
  deplacerLecture((e.clientX - boite.left) / boite.width);
});

/* La piste porte `role="slider"` : elle doit répondre au clavier, sinon le rôle
   promet une manipulation qui n'existe pas. Cinq secondes par flèche, les bornes
   sur Origine et Fin — les conventions d'un curseur audio. */
$('relu-piste').addEventListener('keydown', (e) => {
  if (!vocalPret) return;
  const pas = 5 / vocalPret.secondes;
  const actuelle = ($('relu-audio').currentTime || 0) / vocalPret.secondes;
  const bonds = {
    ArrowRight: actuelle + pas, ArrowUp: actuelle + pas,
    ArrowLeft: actuelle - pas,  ArrowDown: actuelle - pas,
    Home: 0, End: 1,
  };
  if (!(e.key in bonds)) {
    if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); basculerLecture(); }
    return;
  }
  e.preventDefault();
  deplacerLecture(bonds[e.key]);
});

$('btn-pdf').addEventListener('click', envoyerPdf);
// Là où le système sait partager un fichier, le bouton ne promet plus un
// téléchargement mais un envoi — et son icône dit la même chose que son libellé.
if (saitPartagerUnPdf()) {
  $('btn-pdf-libelle').textContent = 'Envoyer le devis';
  $('icone-pdf').setAttribute('href', '#i-partage');
}

document.querySelectorAll('[data-recommencer]').forEach((b) =>
  b.addEventListener('click', recommencer));

/* ---- ossature et relecture --------------------------------------------- */

/* La marque ramène à l'accueil. En cours d'établissement, elle abandonnerait un
   devis à mi-chemin sans le dire : on la laisse alors inerte plutôt que
   destructrice. Ailleurs, `reinitialiser()` fait le ménage complet.
   `compte.js` pose son propre retour depuis l'écran du template. */
$('marque').addEventListener('click', () => {
  if ($('ecran-traitement').classList.contains('is-active')) return;
  // Devant la porte, l'accueil ne s'atteint qu'avec le code.
  if ($('ecran-porte').classList.contains('is-active')) return;
  recommencer();
});

/* Le commutateur des deux vues du devis. */
document.querySelectorAll('.bascule__onglet').forEach((onglet) =>
  onglet.addEventListener('click', () => {
    vueRelecture = onglet.dataset.vue;
    appliquerVue();
  }));

/* L'échelle de l'aperçu dépend de la largeur du bureau, et cette largeur change
   pour trois raisons : la fenêtre est redimensionnée ou pivotée, on passe d'un
   onglet à l'autre, ou l'écran de relecture vient seulement d'être affiché.

   Ce troisième cas est le plus traître, et c'est celui qui a été observé : le
   document arrive avant l'écran qui doit le porter — les fixtures répondent en
   quelques millisecondes, la relecture s'affiche une demi-seconde plus tard. La
   largeur disponible valait alors zéro, et l'aperçu restait à sa taille par
   défaut, sans que rien ne le signale.

   Un observateur de redimensionnement couvre les trois d'un coup : il se
   déclenche aussi au passage de zéro à une largeur réelle. Il remplace l'écoute
   de `resize`, qu'il englobe — et il est déjà groupé par trame. */
new ResizeObserver(() => ajusterApercu()).observe($('apercu-bureau'));

// Franchir le seuil des deux colonnes change ce qui est affiché, pas seulement
// la taille : les deux vues se séparent ou se rejoignent.
ETROIT.addEventListener('change', appliquerVue);
ETROIT_AVEC_MENU.addEventListener('change', appliquerVue);
appliquerVue();

/* Le devis n'a pas pu être établi, mais les observations, elles, valent le
   déplacement : l'artisan les recolle dans son carnet ou dans un SMS. Un bouton
   qui ne ferait que revenir à l'accueil serait un bouton pour rien. */
$('btn-copier-observations').addEventListener('click', async (e) => {
  if (!devisCourant) return;
  const bouton = e.currentTarget;
  const libelle = bouton.textContent;
  try {
    await navigator.clipboard.writeText(devisCourant.notes.map((n) => '— ' + n).join('\n'));
    bouton.textContent = 'Copiées';
  } catch (_) {
    bouton.textContent = 'Copie impossible';
  }
  setTimeout(() => { bouton.textContent = libelle; }, 1800);
});

/* ---- 6. identité de l'entreprise --------------------------------------- */
/* Le SIRET est le geste le plus coûteux du parcours : quatorze chiffres sans
   signification, tapés sur un téléphone. On ne le demande donc pas, on le retrouve
   dans l'annuaire public — l'artisan tape le nom de sa boîte et sept champs se
   remplissent, numéro de TVA compris (il se calcule depuis le SIREN).

   L'identité est conservée par le navigateur, pas par le serveur : elle est
   renseignée une fois, elle voyage avec chaque demande de devis, et la règle
   « le serveur ne garde rien » reste intacte. */

const CHAMPS_ENTREPRISE = [
  'nom', 'forme_juridique', 'metier', 'adresse', 'code_postal_ville',
  'telephone', 'email', 'siret', 'code_ape', 'tva_intracom',
  'assurance', 'assurance_police', 'iban',
];

const CLE_ENTREPRISE = 'devis-vocal.entreprise';

let entrepriseCourante = null;

/* localStorage jette dans un onglet privé ou quand les données de site sont
   bloquées. Une identité qu'on ne peut pas relire n'est pas une raison de ne pas
   pouvoir faire un devis : on retombe simplement sur la configuration du serveur. */
function lireEntreprise() {
  try {
    const brut = localStorage.getItem(CLE_ENTREPRISE);
    return brut ? JSON.parse(brut) : null;
  } catch (_) {
    return null;
  }
}

function ecrireEntreprise(entreprise) {
  try {
    if (entreprise) localStorage.setItem(CLE_ENTREPRISE, JSON.stringify(entreprise));
    else localStorage.removeItem(CLE_ENTREPRISE);
    return true;
  } catch (_) {
    return false;
  }
}

function estRemplie(e) {
  return !!(e && typeof e.nom === 'string' && e.nom.trim());
}

/* Les mentions que le devis imprime « à compléter » quand elles manquent. Le serveur
   ne les emprunte plus à sa configuration — c'était l'identité d'une autre entreprise,
   imprimée sous le nom de l'artisan. Ce qui manque doit donc se voir ici, avant la
   dictée, et pas sur le PDF devant un client. */
const MENTIONS_REQUISES = [
  ['forme_juridique', 'forme juridique'],
  ['adresse', 'adresse'],
  ['code_postal_ville', 'code postal et ville'],
  ['siret', 'SIRET'],
  ['tva_intracom', 'n° de TVA'],
  ['telephone', 'téléphone'],
  ['email', 'e-mail'],
  ['assurance', 'assurance décennale'],
  ['assurance_police', 'n° de police'],
];

/* En franchise de TVA, le devis n'imprime pas de numéro de TVA : le réclamer
   enverrait l'artisan chercher un numéro qu'il n'a pas à porter. */
const manquantes = (e) => MENTIONS_REQUISES.filter(([champ]) =>
  !(champ === 'tva_intracom' && e.franchise_tva) &&
  !(typeof e[champ] === 'string' && e[champ].trim()));

/* La ligne d'accueil. Tant que l'identité manque ou reste incomplète, elle porte
   l'accent et dit ce qui manque : un devis sans SIRET ni assurance décennale n'est pas
   un devis, et ça ne doit pas se découvrir sur le PDF, devant un client. */
function peindreIdentite() {
  const remplie = estRemplie(entrepriseCourante);
  const manque = remplie ? manquantes(entrepriseCourante).map(([, libelle]) => libelle) : [];
  const complete = remplie && manque.length === 0;
  $('identite').classList.toggle('est-vide', !complete);
  $('identite-label').textContent = remplie ? 'Devis établis au nom de' : 'Votre entreprise';
  $('identite-nom').textContent = remplie ? entrepriseCourante.nom : 'à renseigner';
  $('identite-manque').textContent = manque.length ? 'À compléter : ' + manque.join(', ') : '';
  $('identite-manque').hidden = manque.length === 0;
  $('identite-action').textContent = complete ? 'Modifier' : remplie ? 'Compléter' : 'Renseigner';
}

function remplirFormulaire(entreprise) {
  CHAMPS_ENTREPRISE.forEach((champ) => {
    const el = $('e-' + champ);
    if (el) el.value = (entreprise && entreprise[champ]) || '';
  });
  // Le régime de TVA n'est pas un texte : un oui ou un non, absent valant non.
  $('e-franchise_tva').value = entreprise && entreprise.franchise_tva ? 'oui' : 'non';
}

function lireFormulaire() {
  const entreprise = {};
  CHAMPS_ENTREPRISE.forEach((champ) => {
    const el = $('e-' + champ);
    entreprise[champ] = el ? el.value.trim() : '';
  });
  entreprise.franchise_tva = $('e-franchise_tva').value === 'oui';
  return entreprise;
}

/* ---- recherche --------------------------------------------------------- */

let minuterieRecherche = null;
let numeroRecherche = 0;

function etatRecherche(texte) {
  $('entreprise-etat').textContent = texte;
}

function viderResultats() {
  $('entreprise-resultats').replaceChildren();
  $('entreprise-resultats').hidden = true;
}

function peindreResultats(trouvees) {
  const boite = $('entreprise-resultats');
  boite.replaceChildren();

  trouvees.forEach((e) => {
    const bouton = noeud('button', 'resultat');
    bouton.type = 'button';
    bouton.appendChild(noeud('div', 'resultat__nom', e.nom));

    // La ville et la date distinguent deux homonymes — c'est à ça que l'artisan
    // reconnaît la sienne. Le SIRET seul ne dit rien à personne.
    const meta = noeud('div', 'resultat__meta');
    const details = [e.code_postal_ville, e.metier, e.date_creation && 'depuis ' + e.date_creation]
      .filter(Boolean).join(' · ');
    meta.appendChild(document.createTextNode(details));
    bouton.appendChild(meta);

    const numero = noeud('div', 'resultat__meta');
    numero.appendChild(noeud('span', 'num', 'SIRET ' + e.siret));
    bouton.appendChild(numero);

    bouton.addEventListener('click', () => choisirEntreprise(e));
    boite.appendChild(bouton);
  });

  boite.hidden = trouvees.length === 0;
}

async function chercherEntreprise(requete) {
  const q = requete.trim();
  if (q.length < 3) {
    viderResultats();
    etatRecherche('Trois lettres suffisent. Ajoutez la ville si le nom est courant.');
    return;
  }

  // Chaque frappe peut lancer une requête, et rien ne garantit qu'elles reviennent
  // dans l'ordre. Sans ce compteur, une réponse lente à « dup » écrase la réponse
  // rapide à « dupont plomberie » : l'artisan voit une liste qui ne correspond plus
  // à ce qu'il a tapé.
  const moi = ++numeroRecherche;
  etatRecherche('Recherche…');

  let trouvees = [];
  try {
    const reponse = await fetch('/api/entreprise?q=' + encodeURIComponent(q),
                                { headers: enteteAcces() });
    if (reponse.ok) trouvees = await reponse.json();
  } catch (_) {
    // Réseau coupé : on le dit et le formulaire prend le relais.
  }

  if (moi !== numeroRecherche) return;

  peindreResultats(trouvees);
  if (trouvees.length) {
    etatRecherche(trouvees.length === 1 ? '1 entreprise trouvée.' : trouvees.length + ' entreprises trouvées.');
  } else if (/^[\d\s]+$/.test(q)) {
    etatRecherche("Ce numéro ne correspond à aucune entreprise ouverte. Vérifiez les chiffres, ou complétez à la main.");
  } else {
    etatRecherche("Aucune entreprise trouvée. Essayez avec la ville, ou complétez à la main — certaines entreprises ne sont pas diffusées dans l'annuaire.");
  }
}

/* Une entreprise choisie remplit ce que la base connaît, et rien d'autre : le
   téléphone, l'assurance et l'IBAN restent ce que l'artisan avait déjà saisi.
   Écraser ses champs à lui parce qu'il reprend la recherche serait une punition. */
function choisirEntreprise(trouvee) {
  const actuel = lireFormulaire();
  CHAMPS_ENTREPRISE.forEach((champ) => {
    if (typeof trouvee[champ] === 'string' && trouvee[champ]) actuel[champ] = trouvee[champ];
  });
  remplirFormulaire(actuel);

  viderResultats();
  $('entreprise-recherche').value = '';
  etatRecherche('Rempli depuis l’annuaire. Vérifiez, puis complétez ce qui manque.');

  // Le formulaire s'ouvre : l'artisan voit ce qui vient d'être rempli — c'est
  // l'argument — et surtout les quelques champs qui restent à sa charge.
  $('entreprise-formulaire').classList.add('is-open');

  signalerCapital();

  const [manquant] = manquantes(actuel)[0] || [];
  if (manquant) $('e-' + manquant).focus({ preventScroll: true });
}

/* L'annuaire donne la forme juridique — « SARL » — mais pas le capital social, qui
   n'est publié nulle part. Or il fait partie des mentions obligatoires d'une société
   sur ses documents commerciaux. On ne l'invente pas : on le demande, une fois, à
   l'endroit où l'artisan a le champ sous les yeux. */
const SOCIETES = ['SARL', 'EURL', 'SAS', 'SASU', 'SA', 'SNC'];

function signalerCapital() {
  const aide = $('forme-aide');
  const forme = $('e-forme_juridique').value.trim();
  const societe = SOCIETES.includes(forme.toUpperCase());
  const capitalAbsent = !/capital/i.test(forme);

  if (societe && capitalAbsent) {
    aide.textContent = 'Ajoutez le capital social : mention obligatoire pour une société.';
    aide.classList.add('est-faux');
  } else if (/individuel/i.test(forme) && $('e-franchise_tva').value !== 'oui') {
    // L'annuaire calcule un n° de TVA pour toute entreprise : rien n'y dit qu'une
    // micro-entreprise ne la facture pas. On pose la question, sans trancher.
    aide.textContent = 'Micro-entreprise sans TVA ? Choisissez « Franchise en base » plus bas.';
    aide.classList.remove('est-faux');
  } else {
    aide.textContent = '';
    aide.classList.remove('est-faux');
  }
}

/* ---- écran ------------------------------------------------------------- */

function ouvrirEntreprise() {
  remplirFormulaire(entrepriseCourante);
  $('entreprise-recherche').value = '';
  viderResultats();
  etatRecherche('Trois lettres suffisent. Ajoutez la ville si le nom est courant.');
  // Une identité déjà renseignée n'a plus rien à chercher : on montre le formulaire.
  $('entreprise-formulaire').classList.toggle('is-open', estRemplie(entrepriseCourante));
  signalerCapital();
  montrer('entreprise');
  if (!estRemplie(entrepriseCourante)) $('entreprise-recherche').focus({ preventScroll: true });
}

function enregistrerEntreprise() {
  const saisie = lireFormulaire();
  entrepriseCourante = estRemplie(saisie) ? saisie : null;

  const bouton = $('btn-entreprise-enregistrer');
  if (!ecrireEntreprise(entrepriseCourante)) {
    // Navigation privée, stockage bloqué : l'identité vaut pour la session en cours
    // mais ne survivra pas à la fermeture. Le taire serait pire que le dire.
    bouton.textContent = 'Gardé pour cette session seulement';
    setTimeout(() => { bouton.textContent = 'Enregistrer'; }, 2600);
  }

  peindreIdentite();
  montrer('accueil');
}

$('identite').addEventListener('click', ouvrirEntreprise);
$('btn-entreprise-retour').addEventListener('click', () => montrer('accueil'));
$('btn-entreprise-enregistrer').addEventListener('click', enregistrerEntreprise);

$('btn-entreprise-oublier').addEventListener('click', () => {
  entrepriseCourante = null;
  ecrireEntreprise(null);
  remplirFormulaire(null);
  peindreIdentite();
  etatRecherche('Identité effacée de cet appareil.');
});

$('e-forme_juridique').addEventListener('blur', signalerCapital);
$('e-franchise_tva').addEventListener('change', signalerCapital);

$('entreprise-bascule').addEventListener('click', () =>
  $('entreprise-formulaire').classList.toggle('is-open'));

/* On attend une pause dans la frappe : l'API publique est limitée en débit, et
   une requête par caractère la gaspillerait pour un résultat qu'on n'a pas le
   temps de lire. */
$('entreprise-recherche').addEventListener('input', (e) => {
  clearTimeout(minuterieRecherche);
  const valeur = e.target.value;
  minuterieRecherche = setTimeout(() => chercherEntreprise(valeur), 320);
});

$('entreprise-recherche').addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  e.preventDefault();
  clearTimeout(minuterieRecherche);
  chercherEntreprise(e.target.value);
});

/* Le numéro de TVA se déduit du SIRET : si l'artisan corrige l'un à la main,
   l'autre doit suivre, sinon le devis porte deux identités qui se contredisent. */
$('e-siret').addEventListener('blur', async () => {
  const siret = $('e-siret').value.trim();
  const aide = $('siret-controle');
  if (!siret) { aide.textContent = ''; aide.classList.remove('est-faux'); return; }

  const trouvees = await (async () => {
    try {
      const r = await fetch('/api/entreprise?q=' + encodeURIComponent(siret),
                            { headers: enteteAcces() });
      return r.ok ? await r.json() : [];
    } catch (_) { return []; }
  })();

  const exact = trouvees.find((t) => t.siret.replace(/\D/g, '') === siret.replace(/\D/g, ''));
  if (exact) {
    aide.classList.remove('est-faux');
    aide.textContent = exact.nom;
    if (!$('e-tva_intracom').value.trim()) $('e-tva_intracom').value = exact.tva_intracom;
  } else {
    aide.classList.add('est-faux');
    aide.textContent = "Ce numéro ne correspond à aucune entreprise ouverte.";
  }
});

entrepriseCourante = lireEntreprise();
peindreIdentite();

/* Deux façons de montrer autre chose que ce qu'on croit montrer : un chiffrage
   rejoué depuis une fixture, et un chiffrage calculé par un moteur de secours. La
   première était signalée, la seconde ne l'était pas — or Groq reprend fidèlement
   les prix dictés mais sous-estime les prix estimés de 6 à 46 %. Les deux méritent
   la même bande. Au passage, l'attente annoncée s'aligne sur le moteur réel. */

const MOTEURS = { anthropic: 'Anthropic', groq: 'Groq', openai: 'OpenAI', autre: 'fournisseur libre' };

function peindreSante(info) {
  fournisseur = info.provider || 'openai';
  const rejoue = info.mode === 'fixtures';

  ETAPES[1].occupe = rejoue ? ATTENTE.fixtures : (ATTENTE[fournisseur] || ATTENTE.openai);

  // Groq est le moteur de secours : c'est sur lui que l'écart a été mesuré. Un
  // fournisseur libre n'a été mesuré nulle part — il porte la même bande, par prudence.
  const secours = !rejoue && (fournisseur === 'groq' || fournisseur === 'autre');
  if (!rejoue && !secours) return;

  $('bandeau-fixtures').hidden = !rejoue;
  $('bandeau-fournisseur').hidden = !secours;
  if (secours) $('bandeau-moteur').textContent = MOTEURS[fournisseur] || fournisseur;
  $('bandeau').hidden = false;
}

/* ---- 7. porte d'entrée -------------------------------------------------- */
/* Le démonstrateur tourne sur les clés API de ses auteurs : sans porte, quiconque
   trouve l'URL chiffre à leurs frais. Le code est unique et donné de vive voix ;
   ce n'est pas une authentification, c'est un verrou en attendant les comptes.

   Il est gardé par le navigateur pour ne pas être redemandé à chaque devis — mais
   c'est le serveur qui décide, à chaque appel. Ce qui est stocké ici n'est qu'une
   commodité : le retirer ne donne accès à rien. */

const CLE_ACCES = 'devis-vocal.acces';

let codeAcces = '';
try { codeAcces = localStorage.getItem(CLE_ACCES) || ''; } catch (_) { /* stockage bloqué */ }

function enteteAcces() {
  return codeAcces ? { 'X-Acces': codeAcces } : {};
}

function oublierAcces() {
  codeAcces = '';
  try { localStorage.removeItem(CLE_ACCES); } catch (_) { /* rien à retirer */ }
}

async function entrer() {
  const saisi = $('porte-code').value.trim();
  if (!saisi) return;

  const bouton = $('btn-porte');
  bouton.disabled = true;
  $('porte-erreur').textContent = '';

  codeAcces = saisi;
  let info = null;
  try {
    info = await fetch('/health', { headers: enteteAcces() }).then((r) => r.json());
  } catch (_) {
    $('porte-erreur').textContent = 'Serveur injoignable. Vérifiez le réseau.';
  }

  bouton.disabled = false;

  if (!info) return;

  if (info.acces === 'requis') {
    oublierAcces();
    $('porte-erreur').textContent = 'Code incorrect.';
    $('porte-code').select();
    return;
  }

  try { localStorage.setItem(CLE_ACCES, saisi); } catch (_) { /* vaut pour la session */ }
  $('porte-code').value = '';
  $('porte-erreur').textContent = '';
  peindreSante(info);
  franchirPorte();
}

/* La porte peut aussi tomber en cours de route : le code a changé pendant la session,
   ou /health n'a pas répondu au chargement et le code n'a jamais été demandé. Ce qui
   était à l'écran reste en mémoire — vocal, transcription, devis corrigé. Une fois le
   code saisi, on revient où l'on était et on rejoue ce qui a été refusé : jamais un
   vocal à redicter pour un code. */
let ecranAvantPorte = null;
const aRejouer = new Set();

function ouvrirPorte(message) {
  if ($('ecran-porte').classList.contains('is-active')) return;
  const actif = document.querySelector('.screen.is-active');
  ecranAvantPorte = actif ? actif.id.replace('ecran-', '') : null;
  $('porte-erreur').textContent = message || '';
  montrer('porte');
  $('porte-code').focus({ preventScroll: true });
}

function franchirPorte() {
  montrer(ecranAvantPorte || 'accueil');
  ecranAvantPorte = null;
  const etapes = [...aRejouer];
  aRejouer.clear();
  etapes.forEach((etape) => etape());
  // `compte.js` a pu retenir son écran de connexion le temps de la porte : à lui.
  document.dispatchEvent(new Event('porte-franchie'));
}

$('btn-porte').addEventListener('click', entrer);
$('porte-code').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); entrer(); }
});

/* Au chargement : le serveur dit s'il réclame un code, et le reste de ce qu'il
   annonce — moteur, mode — n'est donné qu'une fois la porte franchie.

   `compte.js` attend cette réponse avant de choisir son écran : la porte passe avant
   la connexion. Sans cette attente, la connexion recouvrait la porte, et l'artisan
   dictait sans code jusqu'au premier refus. Résolue dans tous les cas, même serveur
   muet — elle ne dit pas que tout va bien, seulement que la question est tranchée. */
window.santeLue = (async () => {
  let info = null;
  try {
    info = await fetch('/health', { headers: enteteAcces() }).then((r) => r.json());
  } catch (_) {
    return;  // serveur muet : la page reste debout, la porte viendra au premier refus
  }

  if (info.acces === 'requis') {
    ouvrirPorte('');
    return;
  }
  peindreSante(info);
})();
