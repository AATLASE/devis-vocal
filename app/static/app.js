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
let dureeVocal = null;
let chrono = null;
let revelation = null;

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
  arreterDictee();
  devisCourant = null;
  dureeVocal = null;
  $('fichier').value = '';
  $('repli').classList.remove('is-open');
  $('depot-repli').classList.remove('is-open');
  $('carte-transcription').hidden = true;
  $('transcription-directe').textContent = '';
  montrer('accueil');
}

function echouer(message) {
  clearInterval(chrono);
  clearInterval(revelation);
  $('erreur-message').textContent = message;
  montrer('erreur');
}

/* ---- 2. traitement ----------------------------------------------------- */
/* Les trois étapes sont celles du pipeline, pas une décoration. Le libellé du
   chiffrage annonce l'attente pendant qu'elle a lieu : une attente annoncée est
   supportée, une attente muette est subie. */

const ETAPES = [
  { titre: 'Transcription', detail: 'Lecture de la note vocale, mot pour mot' },
  {
    titre: 'Analyse et chiffrage',
    detail: 'Identification des prestations, des quantités et des prix',
    occupe: 'Chiffrage en cours — 20 à 40 secondes',
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

async function poste(url, options) {
  const reponse = await fetch(url, options);
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
    body: JSON.stringify({ transcription }),
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

let enregistreur = null;
let morceaux = [];
let micro = null;
let chronoDictee = null;
let secondesDictee = 0;

const mmss = (s) => Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');

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
  // Sans ça, le navigateur laisse le voyant d'enregistrement allumé.
  if (micro) micro.getTracks().forEach((t) => t.stop());
  micro = null;
}

async function demarrerDictee() {
  const format = formatDisponible();
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !format) {
    refuserDictee(raisonMicro(null));
    return;
  }

  try {
    micro = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (err) {
    refuserDictee(raisonMicro(err));
    return;
  }

  const [type, extension] = format;
  morceaux = [];
  // Référence locale : `arreterDictee()` remet `enregistreur` à null dès l'appel
  // à stop(), or l'événement, lui, n'arrive qu'après.
  const recorder = new MediaRecorder(micro, type ? { mimeType: type } : undefined);
  enregistreur = recorder;

  recorder.addEventListener('dataavailable', (e) => {
    if (e.data && e.data.size) morceaux.push(e.data);
  });

  recorder.addEventListener('stop', () => {
    const blob = new Blob(morceaux, { type: recorder.mimeType || type });
    const secondes = secondesDictee;
    fermerMicro();
    peindreDictee(false);

    if (secondes < DUREE_MIN || !blob.size) {
      $('dictee-aide').textContent = 'Trop court — parlez quelques secondes.';
      return;
    }
    // La durée vient du chronomètre et non du blob : un webm sorti de
    // MediaRecorder annonce presque toujours une durée infinie.
    lancer(new File([blob], `dictee.${extension}`, { type: blob.type }), null, secondes);
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

function arreterDictee() {
  clearInterval(chronoDictee);
  chronoDictee = null;
  if (enregistreur && enregistreur.state !== 'inactive') enregistreur.stop();
  else { fermerMicro(); peindreDictee(false); }
  enregistreur = null;
}

function basculerDictee() {
  if (chronoDictee) arreterDictee();
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
    let transcription = texte;

    if (fichier) {
      // Une dictée connaît sa durée : elle sort du chronomètre, pas du fichier.
      dureeVocal = formaterDuree(dureeSecondes != null ? dureeSecondes : await dureeAudio(fichier));
      transcription = await transcrire(fichier);
    } else {
      dureeVocal = null;
    }

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

async function telechargerPdf() {
  const bouton = $('btn-pdf');
  const libelle = bouton.textContent;
  bouton.disabled = true;
  bouton.textContent = 'Génération…';

  try {
    const r = await poste('/api/pdf', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(devisCourant),
    });
    const url = URL.createObjectURL(await r.blob());
    const lien = document.createElement('a');
    lien.href = url;
    lien.download = `${devisCourant.numero}.pdf`;
    lien.click();
    URL.revokeObjectURL(url);
  } catch (err) {
    echouer(err.message);
  } finally {
    bouton.disabled = false;
    bouton.textContent = libelle;
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
  if (texte) lancer(null, texte);
});

$('btn-pdf').addEventListener('click', telechargerPdf);

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

/* Signale à l'écran quand le chiffrage est rejoué plutôt que calculé. */
fetch('/health')
  .then((r) => r.json())
  .then((info) => {
    if (info.mode !== 'fixtures') return;
    $('bandeau').hidden = false;
    document.body.classList.add('a-bandeau');
  })
  .catch(() => { /* le bandeau reste caché : pas de quoi bloquer la page */ });
