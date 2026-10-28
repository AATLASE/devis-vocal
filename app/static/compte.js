/* Devis Vocal — compte et template personnel.

   Ajout hors périmètre d'origine : CLAUDE.md exclut « comptes / auth / multi-tenant ».
   Assumé sur décision explicite, et écrit pour rester amovible — supprimer ce fichier
   et sa balise <script> rend au produit son comportement d'avant, sans toucher au
   reste du front.

   Module ESM, chargé après `app.js`, et dans cet ordre pour une raison : le SDK
   Firebase n'existe qu'en ESM, alors qu'`app.js` est un script classique dont les
   fonctions (`montrer`, `$`, `telecharger`, `enteteAcces`) sont des globales. La
   dépendance ne va que dans ce sens — `app.js` ne connaît de ce fichier que
   `window.Compte`, n'émet pour lui que l'événement `porte-franchie`, et se passe très
   bien de son absence.

   Deux règles tiennent tout le reste :

   - **Sans vérification côté serveur, pas d'écran de connexion.** C'est /api/firebase
     qui tranche, jamais le front. Un bouton « Se connecter » devant une API non
     protégée serait une porte peinte sur un mur — pire que rien, puisqu'on croirait
     l'API gardée.
   - **Le compte ne conditionne jamais la dictée.** Il ne sert qu'à retrouver son
     template. Un artisan non connecté sort son devis exactement comme avant. */

import { initializeApp } from 'https://www.gstatic.com/firebasejs/12.8.0/firebase-app.js';
import {
  GoogleAuthProvider,
  createUserWithEmailAndPassword,
  getAuth,
  onAuthStateChanged,
  sendPasswordResetEmail,
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

/* L'artisan qui a choisi « Continuer sans compte » ne doit plus retomber sur la
   connexion à chaque chargement : ce serait remettre le mur qu'on vient d'ouvrir.
   Le choix est gardé sur l'appareil ; « Se connecter », dans le menu, le défait.
   Stockage bloqué : le choix vaut pour la page en cours, ce qui suffit à dicter. */
const CLE_SANS_COMPTE = 'devis-vocal.sans-compte';

let sansCompte = false;
try { sansCompte = localStorage.getItem(CLE_SANS_COMPTE) === '1'; } catch (_) { /* bloqué */ }

function choisirSansCompte(valeur) {
  sansCompte = valeur;
  try {
    if (valeur) localStorage.setItem(CLE_SANS_COMPTE, '1');
    else localStorage.removeItem(CLE_SANS_COMPTE);
  } catch (_) { /* vaut pour la page en cours */ }
}

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

const message = (err, defaut = "La connexion a échoué. Réessayez dans un instant.") =>
  (err && MESSAGES[err.code]) || defaut;

function direErreur(texte) {
  const zone = $('connexion-erreur');
  zone.textContent = texte;
  zone.hidden = !texte;
}

/* Ce qui s'est bien passé — l'e-mail de réinitialisation est parti. Une zone à
   part de l'erreur : `role="status"` et non `alert`, et une autre couleur. */
function direInfo(texte) {
  const zone = $('connexion-info');
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

  // Les e-mails que Firebase envoie lui-même — la réinitialisation du mot de passe —
  // et la page où l'on en choisit un nouveau sortent dans cette langue.
  auth.languageCode = 'fr';

  window.Compte.actif = true;
  // `getIdToken()` renouvelle tout seul le jeton quand il approche de l'expiration :
  // on le redemande à chaque appel plutôt que d'en garder un qui périmerait pendant
  // les quarante secondes de structuration.
  window.Compte.jeton = async () => (utilisateur ? await utilisateur.getIdToken() : null);

  onAuthStateChanged(auth, async (compte) => {
    utilisateur = compte;
    try {
      // La porte d'abord : `app.js` doit savoir si le serveur réclame un code avant
      // qu'on choisisse l'écran. Voir `porteFermee`.
      await window.santeLue;
      await peindreCompte();
    } finally {
      // `finally` : une fiche illisible ne doit jamais laisser la page masquée.
      reveler();
    }
  });

  // L'écran retenu le temps de la porte, peint une fois le code saisi.
  document.addEventListener('porte-franchie', () => {
    if (!attendLaPorte) return;
    attendLaPorte = false;
    peindreCompte();
  });

  brancher();
}

/* ---- la porte avant le compte ------------------------------------------- */
/* Quand le serveur réclame un code d'accès, rien du compte ne doit recouvrir la porte.
   La connexion la remplaçait : l'artisan touchait « Continuer sans compte », dictait,
   et ne découvrait le code qu'au premier refus, sur un écran d'erreur sans issue. Tant
   que la porte est à l'écran, on retient donc l'écran du compte — et la lecture de la
   fiche, qui serait refusée faute de code. `app.js` annonce le franchissement. */

let attendLaPorte = false;
const porteFermee = () => $('ecran-porte').classList.contains('is-active');

/* Les en-têtes d'un appel à l'API du compte. Le jeton dit qui appelle ; le code
   d'accès, tenu par `app.js`, dit qu'on a le droit d'appeler — le serveur l'exige sur
   tout /api/, compte compris. */
async function entetes(autres = {}) {
  return {
    ...autres,
    ...enteteAcces(),
    Authorization: 'Bearer ' + (await window.Compte.jeton()),
  };
}

/* Le motif d'un refus, dans la forme où le serveur le donne. */
async function motif(reponse) {
  try {
    const corps = await reponse.json();
    if (corps.detail) return corps.detail;
  } catch (_) { /* réponse non JSON : le message générique fera l'affaire */ }
  return 'Erreur ' + reponse.status + '.';
}

/* ---- le menu latéral ---------------------------------------------------- */
/* Il dit en haut qui est connecté et pour quelle entreprise, et range en bas ce
   qu'on peut y faire. Une icône le masque, la même le rouvre.

   Sur un grand écran il pousse la page et reste ouvert tant qu'on ne le masque pas ;
   ce choix-là est gardé sur l'appareil. En dessous de 1100px il passe par-dessus la
   page : il s'ouvre le temps d'un geste et ne se retient pas. Les deux seuils sont
   ceux d'app.css. */

const CLE_MENU = 'devis-vocal.menu-masque';
const GRAND_ECRAN = window.matchMedia('(min-width: 1100px)');

let menuMasque = false;
try { menuMasque = localStorage.getItem(CLE_MENU) === '1'; } catch (_) { /* bloqué */ }

let menuOuvert = GRAND_ECRAN.matches && !menuMasque;

function appliquerMenu() {
  const ouvert = menuOuvert && !$('menu').hidden;
  document.body.classList.toggle('menu-ouvert', ouvert);
  // L'icône de la barre n'existe que menu fermé : ouvert, c'est celle du menu.
  $('btn-menu').hidden = $('menu').hidden || ouvert;
  // La relecture tient en deux colonnes ou en une selon la place que le menu laisse.
  appliquerVue();
}

function basculerMenu(ouvert) {
  // Le glissement ne vaut que pour un geste : voir `body.menu-anime` dans app.css.
  document.body.classList.add('menu-anime');
  menuOuvert = ouvert;
  if (GRAND_ECRAN.matches) {
    menuMasque = !ouvert;
    try {
      if (menuMasque) localStorage.setItem(CLE_MENU, '1');
      else localStorage.removeItem(CLE_MENU);
    } catch (_) { /* vaut pour la page en cours */ }
  }
  appliquerMenu();
  // Le focus suit le geste : sans ça, il resterait sur une icône qui vient de
  // disparaître, et la touche suivante repartirait du haut de la page.
  $(ouvert ? 'btn-menu-fermer' : 'btn-menu').focus({ preventScroll: true });
}

/* Par-dessus la page, le menu s'efface dès qu'on y a choisi quelque chose : il
   cacherait sinon l'écran qu'on vient de demander. */
function refermerSiPardessus() {
  if (GRAND_ECRAN.matches || !menuOuvert) return;
  document.body.classList.add('menu-anime');
  menuOuvert = false;
  appliquerMenu();
}

/* Le menu a trois visages : la fiche complète, le compte dont l'inscription n'est
   pas finie — une adresse et la déconnexion, rien d'autre —, et le seul lien
   « Se connecter » pour qui a choisi de se passer de compte. */
function peindreMenu() {
  const connecte = !!utilisateur;
  const fiche = connecte && profil && profil.complet ? profil : null;
  const enInscription = connecte && !!profil && !profil.complet;

  $('menu').hidden = !(connecte || sansCompte);

  const email = connecte ? (utilisateur.email || '') : '';
  const nom = fiche ? fiche.identite
    : connecte ? (utilisateur.displayName || email || 'Connecté')
    : 'Sans compte';
  // Sans fiche, l'adresse tient déjà lieu de nom : la redire dessous ne dirait rien.
  const sousNom = connecte ? (email === nom ? '' : email)
    : 'Vous dictez sans être connecté.';

  $('menu-nom').textContent = nom;
  $('menu-role').textContent = fiche ? fiche.role : '';
  $('menu-role').hidden = !fiche;
  $('menu-email').textContent = sousNom;
  $('menu-email').hidden = !sousNom;
  $('menu-entreprise').textContent = fiche ? fiche.entreprise : '';
  $('menu-entreprise-bloc').hidden = !fiche;

  // « Paramètres » rouvre la fiche : sans fiche lue, il n'y a rien à rouvrir. Le
  // template, lui, n'a pas de sens tant que l'inscription n'est pas finie ; la
  // déconnexion reste — c'est la sortie de secours de cet écran.
  $('btn-parametres').hidden = !fiche;
  $('btn-template').hidden = !connecte || enInscription;
  $('btn-deconnexion').hidden = !connecte;
  $('btn-connexion').hidden = connecte;

  appliquerMenu();
}

async function peindreCompte() {
  if (porteFermee()) {
    attendLaPorte = true;
    return;
  }

  if (!utilisateur) {
    profil = null;
    peindreMenu();
    if (sansCompte) {
      // Pas de retour forcé à l'accueil : l'artisan est peut-être en pleine relecture.
      if (document.querySelector('#ecran-connexion.is-active, #ecran-profil.is-active')) {
        montrer('accueil');
      }
      return;
    }
    montrer('connexion');
    return;
  }

  // Connecté : le choix « sans compte » n'a plus d'objet, et ne doit pas survivre à
  // une déconnexion ultérieure.
  choisirSansCompte(false);
  // Le menu d'abord, avec l'adresse seule : la fiche met un aller-retour à arriver.
  peindreMenu();

  // La fiche décide de l'écran : tant qu'elle manque, l'inscription n'est pas finie.
  // C'est le même chemin pour les deux façons de s'inscrire — on regarde ce que
  // l'artisan a, pas comment il est arrivé. Un compte Google passe donc ici aussi,
  // à sa première connexion, avec son nom déjà rempli.
  profil = await lireProfil();
  // La fiche remplace l'adresse : « Camille Durand », son rôle et son entreprise
  // disent qui est connecté mieux qu'une adresse, qui passe en dessous.
  peindreMenu();

  if (profil && !profil.complet) {
    preparerProfil();
    montrer('profil');
    return;
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
    headers: await entetes(options.headers),
  });
  if (!reponse.ok) throw new Error(await motif(reponse));
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

/* Le même formulaire sert deux fois : à l'inscription, où il est la dernière étape,
   et rouvert depuis « Paramètres », où il ne fait que corriger une fiche qui existe.
   Seuls les mots autour changent, et la présence d'un retour. */
function modeProfil(edition) {
  $('profil-label').textContent = edition ? 'Paramètres' : 'Dernière étape';
  $('profil-titre').textContent = edition ? 'Votre fiche' : 'Qui établit ces devis ?';
  $('btn-profil').textContent = edition ? 'Enregistrer' : "Terminer l'inscription";
  $('btn-profil-retour').hidden = !edition;
}

function ouvrirParametres() {
  if (!profil || !profil.complet) return;
  preparerProfil(profil);
  montrer('profil');
  // `montrer` écrit « Inscription » au fil de la barre : ce n'en est plus une.
  $('entete-fil').textContent = 'Paramètres';
}

/* `existante` : la fiche à corriger. Absente, c'est une inscription, et le
   formulaire part de ce que Google sait du nom. */
function preparerProfil(existante = null) {
  direProfil('');
  modeProfil(!!existante);

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

  if (existante) {
    // Un rôle saisi en texte libre n'est pas dans la liste : il revient par la
    // porte par laquelle il est entré, « Autre » et son champ.
    const connu = roles.includes(existante.role);
    liste.value = connu ? existante.role : AUTRE;
    $('profil-prenom').value = existante.prenom;
    $('profil-nom').value = existante.nom;
    $('profil-entreprise').value = existante.entreprise;
    $('profil-role-autre').value = connu ? '' : existante.role;
  } else {
    const [prenom, nom] = couperLeNom(utilisateur && utilisateur.displayName);
    $('profil-prenom').value = prenom;
    $('profil-nom').value = nom;
    $('profil-entreprise').value = '';
    $('profil-role-autre').value = '';
  }
  $('profil-autre').hidden = liste.value !== AUTRE;
  marquerValides();
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

  peindreMenu();
  montrer('accueil');
}

/* ---- connexion ---------------------------------------------------------- */

async function tenter(action) {
  direErreur('');
  direInfo('');
  try {
    await action();
  } catch (err) {
    direErreur(message(err));
  }
}

/* ---- mot de passe oublié ------------------------------------------------ */
/* Tout le travail est fait par Firebase : c'est lui qui envoie l'e-mail, et c'est
   sur sa page que l'artisan choisit son nouveau mot de passe. Le serveur n'envoie
   rien et ne voit jamais passer un mot de passe — ni l'ancien, ni le nouveau. */

async function envoyerLeLien(email) {
  try {
    // L'adresse de retour ajoute, sur la page de Firebase, un bouton qui ramène ici
    // une fois le mot de passe changé.
    await sendPasswordResetEmail(auth, email, { url: window.location.origin + '/' });
  } catch (err) {
    // Firebase n'accepte une adresse de retour que sur un domaine autorisé du projet,
    // et `127.0.0.1` n'en fait pas partie d'origine. On envoie alors le lien sans
    // bouton de retour plutôt que de ne rien envoyer du tout.
    if (err.code !== 'auth/unauthorized-continue-uri' && err.code !== 'auth/invalid-continue-uri') {
      throw err;
    }
    await sendPasswordResetEmail(auth, email);
  }
}

async function motDePasseOublie() {
  const email = $('champ-email').value.trim();
  direErreur('');
  direInfo('');

  if (!email) {
    direErreur('Renseignez votre adresse e-mail ci-dessus, puis touchez de nouveau « Mot de passe oublié ».');
    $('champ-email').focus();
    return;
  }

  const bouton = $('btn-oubli');
  bouton.disabled = true;
  try {
    await envoyerLeLien(email);
  } catch (err) {
    direErreur(message(err, "L'e-mail n'a pas pu être envoyé. Réessayez dans un instant."));
    return;
  } finally {
    bouton.disabled = false;
  }

  // « Si un compte existe » et non « un e-mail est parti » : Firebase ne dit pas si
  // l'adresse est connue, exprès — sinon ce lien servirait à tester des adresses.
  direInfo('Si un compte existe à l’adresse ' + email + ', un e-mail vient de partir avec un '
    + 'lien pour choisir un nouveau mot de passe. Pensez à regarder dans les indésirables.');
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

  $('btn-oubli').addEventListener('click', motDePasseOublie);

  $('btn-deconnexion').addEventListener('click', () => signOut(auth));

  $('btn-sans-compte').addEventListener('click', () => {
    choisirSansCompte(true);
    direErreur('');
    peindreCompte();
  });

  $('btn-connexion').addEventListener('click', () => {
    choisirSansCompte(false);
    peindreCompte();
  });

  $('form-profil').addEventListener('submit', (e) => {
    e.preventDefault();
    envoyerProfil();
  });
  $('profil-role').addEventListener('change', basculerAutre);
  $('btn-profil-retour').addEventListener('click', () => montrer('accueil'));

  $('btn-parametres').addEventListener('click', () => {
    refermerSiPardessus();
    ouvrirParametres();
  });
  $('btn-template').addEventListener('click', () => {
    refermerSiPardessus();
    ouvrirTemplate();
  });
  $('btn-template-retour').addEventListener('click', () => montrer('accueil'));

  $('btn-menu').addEventListener('click', () => basculerMenu(true));
  $('btn-menu-fermer').addEventListener('click', () => basculerMenu(false));
  $('menu-voile').addEventListener('click', () => basculerMenu(false));
  // Échap ne referme que le menu posé par-dessus la page : sur un grand écran il
  // fait partie de la page, et rien n'est à fermer.
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && menuOuvert && !GRAND_ECRAN.matches && !$('menu').hidden) {
      basculerMenu(false);
    }
  });
  // Franchir le seuil change la nature du menu, pas seulement sa taille : on repart
  // de l'état voulu de ce côté-là — ouvert sur grand écran sauf choix contraire,
  // fermé en dessous.
  GRAND_ECRAN.addEventListener('change', () => {
    menuOuvert = GRAND_ECRAN.matches && !menuMasque;
    appliquerMenu();
  });

  brancherTemplate();
}

/* ---- template ----------------------------------------------------------- */

let fichierChoisi = null;

function direTemplate(texte, ton = '') {
  const zone = $('template-message');
  zone.textContent = texte;
  zone.className = 'template__message' + (ton ? ' template__message--' + ton : '');
  zone.hidden = !texte;
}

async function ouvrirTemplate() {
  montrer('template');
  direTemplate('');
  choisir(null);
  try {
    const reponse = await fetch('/api/template', { headers: await entetes() });
    // Un refus n'est pas un état : peint tel quel, il affichait « le template livré,
    // toutes les mentions garanties » par-dessus un template non conforme.
    if (!reponse.ok) throw new Error(await motif(reponse));
    peindreTemplate(await reponse.json());
  } catch (err) {
    // On ne sait pas ce qui est posé : on ne l'affirme pas.
    $('template-nom').textContent = 'État inconnu';
    $('template-meta').textContent = '';
    $('template-alerte').hidden = true;
    $('btn-template-retirer').hidden = true;
    direTemplate("L'état du template n'a pas pu être lu. " + err.message, 'alerte');
  }
}

function peindreTemplate(etat) {
  $('template-nom').textContent = etat.pose ? etat.nom : 'Le template livré';
  $('btn-template-retirer').hidden = !etat.pose;

  if (etat.pose) {
    const pose = new Date(etat.modifie_le);
    $('template-meta').textContent = 'Posé le ' + pose.toLocaleDateString('fr-FR') +
      ' à ' + pose.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
  } else {
    $('template-meta').textContent = 'Toutes les mentions obligatoires sont garanties.';
  }

  const manquantes = etat.pose ? (etat.mentions_manquantes || []) : [];
  const alerte = $('template-alerte');
  alerte.hidden = manquantes.length === 0;
  const liste = $('template-manquantes');
  liste.textContent = '';
  for (const mention of manquantes) {
    const li = document.createElement('li');
    li.textContent = mention;
    liste.append(li);
  }
}

function choisir(fichier) {
  fichierChoisi = fichier;
  $('template-depot-titre').textContent = fichier ? fichier.name : 'Déposez votre fichier HTML';
  $('template-depot-aide').textContent = fichier
    ? Math.max(1, Math.round(fichier.size / 1024)) + ' Ko · prêt à être vérifié'
    : 'ou touchez pour le choisir · un template Jinja, 512 Ko au plus';
  $('btn-template-apercu').disabled = !fichier;
  $('btn-template-poser').disabled = !fichier;
}

async function envoyerTemplate(url, methode) {
  const donnees = new FormData();
  donnees.append('template', fichierChoisi);
  const reponse = await fetch(url, {
    method: methode,
    headers: await entetes(),
    body: donnees,
  });
  if (!reponse.ok) throw new Error(await motif(reponse));
  return reponse;
}

/* Personne ne pose une mise en page qu'il n'a pas vue sortir en A4. L'aperçu est
   rendu sur un devis d'exemple complet — cinq lignes, deux observations, des prix
   estimés — donc sur un document qui exerce vraiment le template. */
async function apercu(bouton) {
  const libelle = bouton.textContent;
  bouton.disabled = true;
  bouton.textContent = 'Rendu…';
  direTemplate('');
  try {
    const blob = await (await envoyerTemplate('/api/template/apercu', 'POST')).blob();
    const url = URL.createObjectURL(blob);
    // Le rendu prend une seconde ou deux : le geste est consommé, et le navigateur
    // peut refuser l'onglet. Dans ce cas on enregistre le fichier plutôt que de
    // laisser quelqu'un devant un bouton qui n'a rien fait.
    if (!window.open(url, '_blank')) {
      telecharger(blob, 'apercu-template.pdf');
      direTemplate("L'onglet a été bloqué : l'aperçu a été enregistré à la place.");
    }
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (err) {
    direTemplate(err.message, 'alerte');
  } finally {
    bouton.disabled = false;
    bouton.textContent = libelle;
  }
}

async function poser(bouton) {
  const libelle = bouton.textContent;
  bouton.disabled = true;
  bouton.textContent = 'Vérification…';
  direTemplate('');
  try {
    const etat = await (await envoyerTemplate('/api/template', 'PUT')).json();
    peindreTemplate(etat);
    choisir(null);
    direTemplate(etat.conforme
      ? 'Template enregistré. Vos prochains devis sortiront dessus.'
      : 'Template enregistré, mais il lui manque des mentions obligatoires — voir ci-dessus.',
      etat.conforme ? 'ok' : 'alerte');
  } catch (err) {
    direTemplate(err.message, 'alerte');
  } finally {
    bouton.disabled = false;
    bouton.textContent = libelle;
  }
}

async function retirer() {
  direTemplate('');
  try {
    const reponse = await fetch('/api/template', {
      method: 'DELETE',
      headers: await entetes(),
    });
    if (!reponse.ok) throw new Error('La suppression a échoué. ' + await motif(reponse));
    peindreTemplate({ pose: false });
    direTemplate('Vos devis sortent de nouveau sur la mise en page livrée.', 'ok');
  } catch (err) {
    direTemplate(err.message, 'alerte');
  }
}

/* Le template livré, comme point de départ. Un lien nu ne peut porter aucun en-tête :
   derrière la porte, le navigateur affichait le refus en JSON brut. On le récupère donc
   comme les autres appels, puis on le remet en fichier. */
async function telechargerModele(e) {
  e.preventDefault();
  direTemplate('');
  try {
    const reponse = await fetch('/api/template/modele', { headers: enteteAcces() });
    if (!reponse.ok) throw new Error(await motif(reponse));
    telecharger(await reponse.blob(), 'devis-modele.html');
  } catch (err) {
    direTemplate('Le template livré n’a pas pu être téléchargé. ' + err.message, 'alerte');
  }
}

function brancherTemplate() {
  const depot = $('template-depot');
  const champ = $('template-fichier');

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

  $('btn-template-apercu').addEventListener('click', (e) => apercu(e.currentTarget));
  $('btn-template-poser').addEventListener('click', (e) => poser(e.currentTarget));
  $('btn-template-retirer').addEventListener('click', retirer);
  $('lien-modele').addEventListener('click', telechargerModele);
}

demarrer();
