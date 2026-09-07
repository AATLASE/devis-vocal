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
let fournisseur = 'anthropic';   // renseigné par /health

/* ---- navigation -------------------------------------------------------- */

function montrer(nom) {
  document.querySelectorAll('.screen').forEach((s) => s.classList.remove('is-active'));
  $('ecran-' + nom).classList.add('is-active');
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
  devisCourant = null;
  pdfPret = null;
  vocalCourant = null;
  transcriptionCourante = null;
  dureeVocal = null;
  $('fichier').value = '';
  $('repli').classList.remove('is-open');
  $('depot-repli').classList.remove('is-open');
  $('carte-transcription').hidden = true;
  $('transcription-directe').textContent = '';
  peindreDictee(false);
  montrer('accueil');
}

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
    occupe: ATTENTE.anthropic,   // ajusté au retour de /health
  },
  { titre: 'Génération du devis', detail: 'Mise en forme du document et des mentions légales' },
];

function peindreEtapes(atteinte) {
  const conteneur = $('etapes');
  conteneur.replaceChildren();
  ETAPES.forEach((etape, i) => {
    const faite = atteinte > i;
    const encours = atteinte === i;

    const bloc = noeud('div', 'step');
    bloc.append(noeud('div', 'step__mark', faite ? '✓' : encours ? '›' : '·'));

    const centre = noeud('div');
    centre.append(
      noeud('div', 'step__title', etape.titre),
      noeud('div', 'step__detail', encours && etape.occupe ? etape.occupe : etape.detail),
    );
    bloc.append(centre);

    bloc.append(noeud('div', 'step__status', faite ? 'Terminé' : encours ? 'En cours' : 'En attente'));
    conteneur.append(bloc);
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

/* Le code d'accès accompagne chaque appel, dans un en-tête et jamais dans l'URL :
   une URL se retrouve dans l'historique, dans les journaux du reverse proxy et dans
   le `Referer` envoyé aux sites tiers. */
async function poste(url, options) {
  let reponse;
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
    // Le code a changé côté serveur, ou celui gardé ici n'a jamais été le bon.
    oublierAcces();
    montrer('porte');
    $('porte-erreur').textContent = "Ce code n'est plus valable. Redemandez-le.";
    throw new Error("Code d'accès refusé.");
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
let dicteeAnnulee = false;

const mmss = (s) => Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');

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
  if (actif) {
    $('dictee-titre').textContent = mmss(0);
    $('dictee-aide').replaceChildren(
      noeud('span', 'dictee__point'),
      document.createTextNode('Enregistrement · touchez pour arrêter'),
    );
    zone.setAttribute('aria-label', "Arrêter l'enregistrement");
  } else {
    $('dictee-titre').textContent = 'Dictez votre devis';
    $('dictee-aide').textContent = 'Touchez pour commencer à parler';
    zone.setAttribute('aria-label', 'Enregistrer une note vocale');
  }
}

/* Le micro peut manquer pour trois raisons, et chacune a sa réponse. On le dit
   sur place et on déplie le dépôt de fichier : l'artisan n'est jamais coincé. */
function refuserDictee(message) {
  fermerMicro();
  peindreDictee(false);
  $('dictee-aide').textContent = message;
  $('depot-repli').classList.add('is-open');
}

function raisonMicro(err) {
  if (!window.isSecureContext) return 'Le micro exige une connexion sécurisée (https). Déposez un fichier.';
  if (err && err.name === 'NotAllowedError') return 'Micro refusé. Autorisez-le dans le navigateur, ou déposez un fichier.';
  if (err && err.name === 'NotFoundError') return 'Aucun micro détecté sur cet appareil. Déposez un fichier.';
  return 'Micro indisponible ici. Déposez un fichier à la place.';
}

function fermerMicro() {
  fermerNiveau();
  // Sans ça, le navigateur laisse le voyant d'enregistrement allumé.
  if (micro) micro.getTracks().forEach((t) => t.stop());
  micro = null;
}

async function demarrerDictee() {
  fermerRelu();

  const format = formatDisponible();
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !format) {
    refuserDictee(raisonMicro(null));
    return;
  }

  try {
    micro = await navigator.mediaDevices.getUserMedia({ audio: CONTRAINTES_MICRO });
  } catch (err) {
    refuserDictee(raisonMicro(err));
    return;
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

  recorder.addEventListener('stop', () => {
    const blob = new Blob(morceaux, { type: recorder.mimeType || type });
    const secondes = secondesDictee;
    const pic = picNiveau;
    const mesure = niveauMesure;
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
      $('depot-repli').classList.add('is-open');
      return;
    }

    // La durée vient du chronomètre et non du blob : un webm sorti de
    // MediaRecorder annonce presque toujours une durée infinie.
    montrerRelu(new File([blob], `dictee.${extension}`, { type: blob.type }), secondes);
  });

  recorder.start();
  secondesDictee = 0;
  peindreDictee(true);
  chronoDictee = setInterval(() => {
    secondesDictee += 1;
    $('dictee-titre').textContent = mmss(secondesDictee);
    if (secondesDictee >= DUREE_MAX) arreterDictee();
  }, 1000);
}

/* `annuler` sépare les deux façons d'arrêter : le doigt qui met fin à la dictée
   pour l'envoyer, et le code qui la coupe pour en effacer la trace. L'événement
   `stop` du MediaRecorder n'arrive qu'après coup et ne saurait pas les distinguer
   tout seul — il enchaînerait sur l'envoi dans les deux cas. */
function arreterDictee(annuler) {
  clearInterval(chronoDictee);
  chronoDictee = null;
  dicteeAnnulee = !!annuler;
  if (enregistreur && enregistreur.state !== 'inactive') enregistreur.stop();
  else { fermerMicro(); peindreDictee(false); dicteeAnnulee = false; }
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
    peindreEtapes(2);

    await devoilement;
    peindreEtapes(3);
    clearInterval(chrono);

    if (devisCourant.lignes.length === 0) {
      peindreVide(devisCourant);
      setTimeout(() => montrer('vide'), 500);
    } else {
      peindreRelecture(devisCourant);
      preparerPdf();   // fabriqué pendant la relecture, prêt avant le premier appui
      setTimeout(() => montrer('relecture'), 500);
    }
  } catch (err) {
    echouer(err.message);
  }
}

/* ---- 3. relecture ------------------------------------------------------ */

function peindreRelecture(d) {
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

  rang.append(
    gauche,
    noeud('div', 'r num', nombre(l.quantite)),
    noeud('div', 'r unit', l.unite),
    noeud('div', 'r num', money(l.prix_unitaire_ht)),
    noeud('div', 'r num total', money(l.total_ht)),
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

  totaux.append(
    rang('totaux__row', 'Total HT', euro(d.total_ht), true),
    rang('totaux__row', `TVA ${pourcent(d.taux_tva)}${NB}%`, euro(d.montant_tva), true),
    rang('totaux__ttc', 'Total TTC', euro(d.total_ttc), false),
    rang('totaux__acompte', `Acompte ${pourcent(d.acompte_pct)}${NB}%`, euro(d.montant_acompte), true),
  );

  if (Number(d.total_ht_estime) > 0) {
    totaux.append(noeud('div', 'totaux__note',
      `Dont ${euro(d.total_ht_estime)} de prix estimés. Relisez ces lignes avant l'envoi.`));
  }
}

/* ---- 4. devis vide ----------------------------------------------------- */

function peindreVide(d) {
  $('vide-source').textContent = 'Note vocale' + (dureeVocal ? ' · ' + dureeVocal : '');
  // Les guillemets français, et la transcription entière : elle prouve que
  // l'écoute a bien eu lieu, ce qui est tout l'objet de cet écran.
  $('vide-transcription').textContent = '« ' + d.transcription + ' »';
  peindreObservations($('vide-observations'), $('vide-titre-observations'), d.notes);
}

/* ---- PDF --------------------------------------------------------------- */

/* L'artisan ne veut pas un fichier, il veut que sa cliente l'ait. Sur un téléphone,
   un PDF téléchargé atterrit dans « Fichiers » et le parcours s'arrête là ; la feuille
   de partage du système, elle, mène à WhatsApp, aux messages, au courrier. Ce n'est
   pas une intégration — c'est le partage de l'OS, et il tient en un appel. */

async function genererPdf() {
  const r = await poste('/api/pdf', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(devisCourant),
  });
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
  promesse.catch(() => {});   // l'échec sera revu au clic ; pas de rejet non traité
  pdfPret = promesse;
}

async function envoyerPdf() {
  const bouton = $('btn-pdf');
  const libelle = bouton.textContent;
  bouton.disabled = true;
  // Le libellé d'attente n'apparaît que si l'attente a lieu : le PDF est en général
  // déjà prêt, et un « Génération… » qui clignote se lit comme un défaut.
  const attente = setTimeout(() => { bouton.textContent = 'Génération…'; }, 150);

  let blob;
  try {
    blob = await (pdfPret || genererPdf());
  } catch (err) {
    pdfPret = null;
    echouer(err.message);
    return;
  } finally {
    clearTimeout(attente);
    bouton.disabled = false;
    bouton.textContent = libelle;
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
    // Sur un téléphone, un téléchargement se signale par une notification qu'on
    // rate. Le bouton, lui, est sous le doigt.
    bouton.textContent = 'Devis enregistré';
    setTimeout(() => { bouton.textContent = libelle; }, 2200);
  }
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

$('depot-bascule').addEventListener('click', () => $('depot-repli').classList.toggle('is-open'));
$('repli-bascule').addEventListener('click', () => $('repli').classList.toggle('is-open'));

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

/* Déplacement dans l'enregistrement. La position se calcule sur la durée du
   chronomètre, pas sur celle du média — qui est fausse. Un webm sans index peut
   refuser le saut : dans ce cas on ne fait rien, plutôt que de casser la lecture. */
$('relu-piste').addEventListener('click', (e) => {
  if (!vocalPret) return;
  const piste = e.currentTarget.getBoundingClientRect();
  const part = Math.min(1, Math.max(0, (e.clientX - piste.left) / piste.width));
  try {
    $('relu-audio').currentTime = part * vocalPret.secondes;
    peindreLecteur();
  } catch (_) { /* saut refusé : la lecture continue là où elle en était */ }
});

$('btn-pdf').addEventListener('click', envoyerPdf);
if (saitPartagerUnPdf()) $('btn-pdf').textContent = 'Envoyer le devis';

document.querySelectorAll('[data-recommencer]').forEach((b) =>
  b.addEventListener('click', reinitialiser));

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

/* La ligne d'accueil. Tant que l'identité manque, elle porte l'accent : un devis
   sans SIRET ni assurance décennale n'est pas un devis, et ça ne doit pas se
   découvrir sur le PDF, devant un client. */
function peindreIdentite() {
  const remplie = estRemplie(entrepriseCourante);
  $('identite').classList.toggle('est-vide', !remplie);
  $('identite-label').textContent = remplie ? 'Devis établis au nom de' : 'Votre entreprise';
  $('identite-nom').textContent = remplie ? entrepriseCourante.nom : 'à renseigner';
  $('identite-action').textContent = remplie ? 'Modifier' : 'Renseigner';
}

function remplirFormulaire(entreprise) {
  CHAMPS_ENTREPRISE.forEach((champ) => {
    const el = $('e-' + champ);
    if (el) el.value = (entreprise && entreprise[champ]) || '';
  });
}

function lireFormulaire() {
  const entreprise = {};
  CHAMPS_ENTREPRISE.forEach((champ) => {
    const el = $('e-' + champ);
    entreprise[champ] = el ? el.value.trim() : '';
  });
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

  const manquant = ['telephone', 'email', 'assurance', 'iban'].find((c) => !actuel[c]);
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

const MOTEURS = { anthropic: 'Anthropic', groq: 'Groq', openai: 'OpenAI' };

function peindreSante(info) {
  fournisseur = info.provider || 'anthropic';
  const rejoue = info.mode === 'fixtures';

  ETAPES[1].occupe = rejoue ? ATTENTE.fixtures : (ATTENTE[fournisseur] || ATTENTE.anthropic);

  const secours = !rejoue && fournisseur !== 'anthropic';
  if (!rejoue && !secours) return;

  $('bandeau-fixtures').hidden = !rejoue;
  $('bandeau-fournisseur').hidden = !secours;
  if (secours) $('bandeau-moteur').textContent = MOTEURS[fournisseur] || fournisseur;
  $('bandeau').hidden = false;
  document.body.classList.add('a-bandeau');
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
  peindreSante(info);
  montrer('accueil');
}

$('btn-porte').addEventListener('click', entrer);
$('porte-code').addEventListener('keydown', (e) => {
  if (e.key === 'Enter') { e.preventDefault(); entrer(); }
});

/* Au chargement : le serveur dit s'il réclame un code, et le reste de ce qu'il
   annonce — moteur, mode — n'est donné qu'une fois la porte franchie. */
(async () => {
  let info = null;
  try {
    info = await fetch('/health', { headers: enteteAcces() }).then((r) => r.json());
  } catch (_) {
    return;  // serveur muet : la page reste debout, l'erreur viendra à l'usage
  }

  if (info.acces === 'requis') {
    montrer('porte');
    $('porte-code').focus({ preventScroll: true });
    return;
  }
  peindreSante(info);
})();
