/* Devis Vocal — compte et gabarit personnel.

   Ajout hors périmètre d'origine : CLAUDE.md exclut « comptes / auth / multi-tenant ».
   Assumé sur décision explicite, et écrit pour rester amovible — supprimer ce fichier
   et sa balise <script> rend au produit son comportement d'avant, sans toucher au
   reste du front.

   Module ESM, chargé après `app.js`, et dans cet ordre pour une raison : le SDK
   Firebase n'existe qu'en ESM, alors qu'`app.js` est un script classique dont les
   fonctions (`montrer`, `$`, `telecharger`) sont des globales. La dépendance ne va
   que dans ce sens — `app.js` ne connaît de ce fichier que `window.Compte`, et se
   passe très bien de son absence.

   Deux règles tiennent tout le reste :

   - **Sans vérification côté serveur, pas d'écran de connexion.** C'est /api/firebase
     qui tranche, jamais le front. Un bouton « Se connecter » devant une API non
     protégée serait une porte peinte sur un mur — pire que rien, puisqu'on croirait
     l'API gardée.
   - **Le compte ne conditionne jamais la dictée.** Il ne sert qu'à retrouver son
     gabarit. Un artisan non connecté sort son devis exactement comme avant. */

import { initializeApp } from 'https://www.gstatic.com/firebasejs/12.8.0/firebase-app.js';
import {
  GoogleAuthProvider,
  createUserWithEmailAndPassword,
  getAuth,
  onAuthStateChanged,
  signInWithEmailAndPassword,
  signInWithPopup,
  signOut,
} from 'https://www.gstatic.com/firebasejs/12.8.0/firebase-auth.js';

const $ = (id) => document.getElementById(id);

/* Ce qu'`app.js` consomme. Posé tout de suite, dans son état inerte : le premier
   appel réseau peut partir avant que Firebase ait répondu, et il doit partir sans
   en-tête plutôt que d'attendre. */
window.Compte = {
  actif: false,
  jeton: async () => null,
};

let auth = null;
let utilisateur = null;

/* ---- l'écran à montrer -------------------------------------------------- */

/* Tant qu'on ignore si quelqu'un est connecté, on ne sait pas quel écran est le bon.
   Les masquer le temps de le savoir évite de faire clignoter l'accueil devant un
   artisan qui va se retrouver sur la connexion. `visibility` et non `display` : la
   page garde sa hauteur, donc rien ne saute quand elle réapparaît. */
document.body.classList.add('compte-inconnu');

let revele = false;
function reveler() {
  if (revele) return;
  revele = true;
  document.body.classList.remove('compte-inconnu');
}

/* Filet : si /api/firebase ne répond pas, la page ne doit pas rester masquée. Une
   démonstration hors-ligne, réseau coupé, doit s'afficher quand même. */
setTimeout(reveler, 1500);

/* ---- messages ----------------------------------------------------------- */

/* Les codes Firebase sont en anglais et décrivent l'implémentation. Un artisan a
   besoin de savoir quoi faire, pas ce que le SDK a constaté. */
const MESSAGES = {
  'auth/invalid-email': "Cette adresse e-mail n'est pas valide.",
  'auth/invalid-credential': 'Adresse e-mail ou mot de passe incorrect.',
  'auth/wrong-password': 'Adresse e-mail ou mot de passe incorrect.',
  'auth/user-not-found': "Aucun compte à cette adresse. Utilisez « Créer un compte ».",
  'auth/email-already-in-use': 'Un compte existe déjà à cette adresse. Connectez-vous.',
  'auth/weak-password': 'Le mot de passe doit faire au moins six caractères.',
  'auth/popup-closed-by-user': 'La fenêtre Google a été fermée avant la fin.',
  'auth/popup-blocked': 'Le navigateur a bloqué la fenêtre Google. Autorisez-la, puis réessayez.',
  'auth/network-request-failed': 'Connexion au serveur impossible. Vérifiez le réseau.',
  'auth/too-many-requests': 'Trop de tentatives. Patientez une minute avant de réessayer.',
  'auth/operation-not-allowed':
    "Ce mode de connexion n'est pas activé sur le projet Firebase.",
};

const message = (err) =>
  (err && MESSAGES[err.code]) || "La connexion a échoué. Réessayez dans un instant.";

function direErreur(texte) {
  const zone = $('connexion-erreur');
  zone.textContent = texte;
  zone.hidden = !texte;
}

/* ---- démarrage ---------------------------------------------------------- */

async function demarrer() {
  let config;
  try {
    config = await (await fetch('/api/firebase')).json();
  } catch (_) {
    // Pas de config lisible : on ne propose rien et le produit tourne comme avant.
    reveler();
    return;
  }

  if (!config.active) {
    reveler();
    return;
  }

  try {
    auth = getAuth(initializeApp({
      apiKey: config.apiKey,
      authDomain: config.authDomain,
      projectId: config.projectId,
    }));
  } catch (err) {
    // Une config publique incomplète côté serveur ne doit pas emporter la page :
    // sans authentification, le parcours du devis reste entier.
    console.warn('Firebase non initialisable', err);
    reveler();
    return;
  }

  window.Compte.actif = true;
  // `getIdToken()` renouvelle tout seul le jeton quand il approche de l'expiration :
  // on le redemande à chaque appel plutôt que d'en garder un qui périmerait pendant
  // les quarante secondes de structuration.
  window.Compte.jeton = async () => (utilisateur ? await utilisateur.getIdToken() : null);

  onAuthStateChanged(auth, async (compte) => {
    utilisateur = compte;
    try {
      await peindreCompte();
    } finally {
      // `finally` : une fiche illisible ne doit jamais laisser la page masquée.
      reveler();
    }
  });

  brancher();
}

async function peindreCompte() {
  const barre = $('compte');
  if (!utilisateur) {
    barre.hidden = true;
    profil = null;
    // Le compte suivant repart d'une barre entière : sans ça, un artisan qui se
    // déconnecte depuis l'écran de la fiche laisserait « Mon gabarit » caché.
    $('btn-gabarit').hidden = false;
    montrer('connexion');
    return;
  }

  barre.hidden = false;
  $('compte-qui').textContent = utilisateur.email || utilisateur.displayName || 'Connecté';

  // La fiche décide de l'écran : tant qu'elle manque, l'inscription n'est pas finie.
  // C'est le même chemin pour les deux façons de s'inscrire — on regarde ce que
  // l'artisan a, pas comment il est arrivé. Un compte Google passe donc ici aussi,
  // à sa première connexion, avec son nom déjà rempli.
  profil = await lireProfil();

  if (profil && !profil.complet) {
    preparerProfil();
    montrer('profil');
    return;
  }

  if (profil) {
    // La fiche remplace l'adresse e-mail : « Camille Durand · Bâti Rénov » dit qui
    // est connecté mieux qu'une adresse, et l'adresse reste en infobulle.
    $('compte-qui').textContent = profil.identite + ' · ' + profil.entreprise;
    $('compte-qui').title = utilisateur.email || '';
  }

  // On ne ramène pas de force à l'accueil : un rafraîchissement du jeton en plein
  // écran de relecture renverrait l'artisan au début pour rien.
  if (document.querySelector('#ecran-connexion.is-active, #ecran-profil.is-active')) {
    montrer('accueil');
  }
}

/* ---- la fiche de l'artisan ---------------------------------------------- */
/* Nom, prénom, entreprise, rôle — demandés une fois, à l'inscription. Rien de tout
   cela n'entre dans le devis : l'en-tête du document porte treize mentions légales,
   et n'y faire remonter que la raison sociale donnerait un devis dont le nom ne
   correspond plus au SIRET. Voir `Profil` dans app/models.py. */

let profil = null;

async function appelProfil(options = {}) {
  const reponse = await fetch('/api/profil', {
    ...options,
    headers: {
      ...(options.headers || {}),
      Authorization: 'Bearer ' + (await window.Compte.jeton()),
    },
  });
  if (!reponse.ok) {
    let detail = 'Erreur ' + reponse.status + '.';
    try {
      const corps = await reponse.json();
      if (corps.detail) detail = corps.detail;
    } catch (_) { /* réponse non JSON : le message générique fera l'affaire */ }
    throw new Error(detail);
  }
  return await reponse.json();
}

async function lireProfil() {
  try {
    return await appelProfil();
  } catch (err) {
    // L'API est injoignable. On laisse passer à l'accueil plutôt que d'enfermer
    // quelqu'un devant un formulaire qu'il ne pourra pas envoyer : le compte ne doit
    // jamais devenir la condition pour établir un devis. La fiche sera redemandée à
    // la prochaine connexion.
    console.warn('Fiche illisible, on continue sans', err);
    return null;
  }
}

/* Le nom que Google nous donne, coupé en deux. « Camille Durand » -> prénom, nom ;
   un nom composé reste entier du bon côté. Ce n'est qu'une pré-saisie : l'artisan a
   les deux champs sous les yeux et corrige en une frappe si on s'est trompé. */
function couperLeNom(complet) {
  const mots = (complet || '').trim().split(/\s+/).filter(Boolean);
  if (mots.length === 0) return ['', ''];
  if (mots.length === 1) return [mots[0], ''];
  return [mots[0], mots.slice(1).join(' ')];
}

function preparerProfil() {
  direProfil('');

  // La liste des rôles vient du serveur : c'est lui qui en tient la référence, et
  // elle n'a pas à être écrite deux fois. « Autre » n'y est pas — c'est la porte de
  // sortie du front, pas une valeur du domaine.
  const liste = $('profil-role');
  const roles = (profil && profil.roles) || [];
  liste.replaceChildren();
  const invite = document.createElement('option');
  invite.value = '';
  invite.textContent = 'Choisissez…';
  invite.disabled = true;
  invite.selected = true;
  liste.append(invite);
  for (const role of [...roles, AUTRE]) {
    const option = document.createElement('option');
    option.value = role;
    option.textContent = role;
    liste.append(option);
  }
  basculerAutre();

  const [prenom, nom] = couperLeNom(utilisateur && utilisateur.displayName);
  $('profil-prenom').value = prenom;
  $('profil-nom').value = nom;
  $('profil-entreprise').value = '';
  $('profil-role-autre').value = '';
  marquerValides();

  // Le gabarit n'a pas de sens tant que l'inscription n'est pas finie ; la
  // déconnexion, elle, reste — c'est la sortie de secours de cet écran.
  $('btn-gabarit').hidden = true;
}

const AUTRE = 'Autre';

function basculerAutre() {
  const autre = $('profil-role').value === AUTRE;
  $('profil-autre').hidden = !autre;
  if (autre) $('profil-role-autre').focus();
}

function direProfil(texte) {
  const zone = $('profil-erreur');
  zone.textContent = texte;
  zone.hidden = !texte;
}

function marquerValides() {
  for (const id of ['profil-prenom', 'profil-nom', 'profil-entreprise', 'profil-role',
                    'profil-role-autre']) {
    $(id).removeAttribute('aria-invalid');
  }
}

/* Les quatre champs saisis. Le rôle est celui de la liste, sauf « Autre » où c'est
   le texte libre qui compte — la liste n'est là que pour aller vite. */
function fiche() {
  const role = $('profil-role').value;
  return {
    prenom: $('profil-prenom').value.trim(),
    nom: $('profil-nom').value.trim(),
    entreprise: $('profil-entreprise').value.trim(),
    role: role === AUTRE ? $('profil-role-autre').value.trim() : role,
  };
}

/* Le premier champ vide, dans l'ordre du formulaire — signaler le dernier alors que
   le premier manque ferait remonter l'artisan pour rien. Le serveur applique la même
   règle et dans le même ordre : c'est lui qui tranche, ceci ne fait qu'éviter
   l'aller-retour. */
const REQUIS = [
  ['prenom', 'profil-prenom', 'Renseignez votre prénom.'],
  ['nom', 'profil-nom', 'Renseignez votre nom.'],
  ['entreprise', 'profil-entreprise', "Renseignez le nom de votre entreprise."],
  ['role', 'profil-role', 'Choisissez votre rôle dans l’entreprise.'],
];

function premierManquant(donnees) {
  for (const [champ, id, message] of REQUIS) {
    if (!donnees[champ]) {
      // Le rôle « Autre » sans précision : c'est le champ libre qu'il faut désigner,
      // pas la liste, qui elle est bien renseignée.
      const cible = (champ === 'role' && $('profil-role').value === AUTRE)
        ? 'profil-role-autre' : id;
      return { cible, message: champ === 'role' && $('profil-role').value === AUTRE
        ? 'Précisez votre rôle dans l’entreprise.' : message };
    }
  }
  return null;
}

async function envoyerProfil() {
  const donnees = fiche();
  marquerValides();

  const manque = premierManquant(donnees);
  if (manque) {
    direProfil(manque.message);
    // Le champ fautif est signalé et reçoit le focus : lire un message sans savoir
    // où corriger fait relire tout le formulaire.
    $(manque.cible).setAttribute('aria-invalid', 'true');
    $(manque.cible).focus();
    return;
  }

  const bouton = $('btn-profil');
  const libelle = bouton.textContent;
  bouton.disabled = true;
  bouton.textContent = 'Enregistrement…';
  direProfil('');

  try {
    profil = await appelProfil({
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(donnees),
    });
  } catch (err) {
    direProfil(err.message);
    return;
  } finally {
    bouton.disabled = false;
    bouton.textContent = libelle;
  }

  $('btn-gabarit').hidden = false;
  $('compte-qui').textContent = profil.identite + ' · ' + profil.entreprise;
  $('compte-qui').title = utilisateur.email || '';
  montrer('accueil');
}

/* ---- connexion ---------------------------------------------------------- */

async function tenter(action) {
  direErreur('');
  try {
    await action();
  } catch (err) {
    direErreur(message(err));
  }
}

function brancher() {
  $('btn-google').addEventListener('click', () =>
    tenter(() => signInWithPopup(auth, new GoogleAuthProvider())));

  $('form-email').addEventListener('submit', (e) => {
    e.preventDefault();
    tenter(() => signInWithEmailAndPassword(
      auth, $('champ-email').value.trim(), $('champ-motdepasse').value));
  });

  $('btn-creer').addEventListener('click', () => {
    const email = $('champ-email').value.trim();
    const motdepasse = $('champ-motdepasse').value;
    if (!email || motdepasse.length < 6) {
      direErreur('Renseignez une adresse e-mail et un mot de passe de six caractères au moins.');
      return;
    }
    tenter(() => createUserWithEmailAndPassword(auth, email, motdepasse));
  });

  $('btn-deconnexion').addEventListener('click', () => signOut(auth));

  $('form-profil').addEventListener('submit', (e) => {
    e.preventDefault();
    envoyerProfil();
  });
  $('profil-role').addEventListener('change', basculerAutre);

  $('btn-gabarit').addEventListener('click', ouvrirGabarit);
  $('btn-gabarit-retour').addEventListener('click', () => montrer('accueil'));

  brancherGabarit();
}

/* ---- gabarit ------------------------------------------------------------ */

let fichierChoisi = null;

function direGabarit(texte, ton = '') {
  const zone = $('gabarit-message');
  zone.textContent = texte;
  zone.className = 'gabarit__message' + (ton ? ' gabarit__message--' + ton : '');
  zone.hidden = !texte;
}

async function ouvrirGabarit() {
  montrer('gabarit');
  direGabarit('');
  choisir(null);
  try {
    peindreGabarit(await (await fetch('/api/gabarit', {
      headers: { Authorization: 'Bearer ' + (await window.Compte.jeton()) },
    })).json());
  } catch (_) {
    direGabarit("L'état du gabarit n'a pas pu être lu. Le devis sort sur la mise en page livrée.", 'alerte');
  }
}

function peindreGabarit(etat) {
  $('gabarit-nom').textContent = etat.pose ? etat.nom : 'Le gabarit livré';
  $('btn-gabarit-retirer').hidden = !etat.pose;

  if (etat.pose) {
    const pose = new Date(etat.modifie_le);
    $('gabarit-meta').textContent = 'Posé le ' + pose.toLocaleDateString('fr-FR') +
      ' à ' + pose.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  } else {
    $('gabarit-meta').textContent = 'Toutes les mentions obligatoires sont garanties.';
  }

  const manquantes = etat.pose ? (etat.mentions_manquantes || []) : [];
  const alerte = $('gabarit-alerte');
  alerte.hidden = manquantes.length === 0;
  const liste = $('gabarit-manquantes');
  liste.textContent = '';
  for (const mention of manquantes) {
    const li = document.createElement('li');
    li.textContent = mention;
    liste.append(li);
  }
}

function choisir(fichier) {
  fichierChoisi = fichier;
  $('gabarit-depot-titre').textContent = fichier ? fichier.name : 'Déposez votre fichier HTML';
  $('gabarit-depot-aide').textContent = fichier
    ? Math.max(1, Math.round(fichier.size / 1024)) + ' Ko · prêt à être vérifié'
    : 'ou touchez pour le choisir · un gabarit Jinja, 512 Ko au plus';
  $('btn-gabarit-apercu').disabled = !fichier;
  $('btn-gabarit-poser').disabled = !fichier;
}

async function envoyerGabarit(url, methode) {
  const donnees = new FormData();
  donnees.append('gabarit', fichierChoisi);
  const reponse = await fetch(url, {
    method: methode,
    headers: { Authorization: 'Bearer ' + (await window.Compte.jeton()) },
    body: donnees,
  });
  if (!reponse.ok) {
    let detail = 'Erreur ' + reponse.status + '.';
    try {
      const corps = await reponse.json();
      if (corps.detail) detail = corps.detail;
    } catch (_) { /* réponse non JSON : le message générique fera l'affaire */ }
    throw new Error(detail);
  }
  return reponse;
}

/* Personne ne pose une mise en page qu'il n'a pas vue sortir en A4. L'aperçu est
   rendu sur un devis d'exemple complet — cinq lignes, deux observations, des prix
   estimés — donc sur un document qui exerce vraiment le gabarit. */
async function apercu(bouton) {
  const libelle = bouton.textContent;
  bouton.disabled = true;
  bouton.textContent = 'Rendu…';
  direGabarit('');
  try {
    const blob = await (await envoyerGabarit('/api/gabarit/apercu', 'POST')).blob();
    const url = URL.createObjectURL(blob);
    // Le rendu prend une seconde ou deux : le geste est consommé, et le navigateur
    // peut refuser l'onglet. Dans ce cas on enregistre le fichier plutôt que de
    // laisser quelqu'un devant un bouton qui n'a rien fait.
    if (!window.open(url, '_blank')) {
      telecharger(blob, 'apercu-gabarit.pdf');
      direGabarit("L'onglet a été bloqué : l'aperçu a été enregistré à la place.");
    }
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (err) {
    direGabarit(err.message, 'alerte');
  } finally {
    bouton.disabled = false;
    bouton.textContent = libelle;
  }
}

async function poser(bouton) {
  const libelle = bouton.textContent;
  bouton.disabled = true;
  bouton.textContent = 'Vérification…';
  direGabarit('');
  try {
    const etat = await (await envoyerGabarit('/api/gabarit', 'PUT')).json();
    peindreGabarit(etat);
    choisir(null);
    direGabarit(etat.conforme
      ? 'Gabarit enregistré. Vos prochains devis sortiront dessus.'
      : 'Gabarit enregistré, mais il lui manque des mentions obligatoires — voir ci-dessus.',
      etat.conforme ? 'ok' : 'alerte');
  } catch (err) {
    direGabarit(err.message, 'alerte');
  } finally {
    bouton.disabled = false;
    bouton.textContent = libelle;
  }
}

async function retirer() {
  direGabarit('');
  try {
    const reponse = await fetch('/api/gabarit', {
      method: 'DELETE',
      headers: { Authorization: 'Bearer ' + (await window.Compte.jeton()) },
    });
    if (!reponse.ok) throw new Error('La suppression a échoué.');
    peindreGabarit({ pose: false });
    direGabarit('Vos devis sortent de nouveau sur la mise en page livrée.', 'ok');
  } catch (err) {
    direGabarit(err.message, 'alerte');
  }
}

function brancherGabarit() {
  const depot = $('gabarit-depot');
  const champ = $('gabarit-fichier');

  depot.addEventListener('click', () => champ.click());
  depot.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      champ.click();
    }
  });
  champ.addEventListener('change', () => choisir(champ.files[0] || null));

  depot.addEventListener('dragover', (e) => {
    e.preventDefault();
    depot.classList.add('is-over');
  });
  depot.addEventListener('dragleave', () => depot.classList.remove('is-over'));
  depot.addEventListener('drop', (e) => {
    e.preventDefault();
    depot.classList.remove('is-over');
    choisir(e.dataTransfer.files[0] || null);
  });

  $('btn-gabarit-apercu').addEventListener('click', (e) => apercu(e.currentTarget));
  $('btn-gabarit-poser').addEventListener('click', (e) => poser(e.currentTarget));
  $('btn-gabarit-retirer').addEventListener('click', retirer);
}

demarrer();
