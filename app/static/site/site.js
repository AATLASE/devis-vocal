/* Devis Vocal — la vitrine.

   Tout ce qui suit ajoute du mouvement ou du choix ; rien n'est nécessaire pour
   lire la page. Sans script, elle s'affiche entière, l'exemple d'électricien en
   place. Pas de dépendance, pas de script en ligne : la CSP n'autorise que les
   fichiers servis par l'application. */

document.documentElement.classList.add('js');

const mouvementReduit = matchMedia('(prefers-reduced-motion: reduce)');
const pointeurFin = matchMedia('(hover: hover) and (pointer: fine)');

/* ---- menu sur écran étroit ---------------------------------------------- */

const entete = document.querySelector('.entete');
const boutonMenu = document.querySelector('.menu-bouton');
if (entete && boutonMenu) {
  const fermer = () => {
    entete.classList.remove('est-ouvert');
    boutonMenu.setAttribute('aria-expanded', 'false');
    boutonMenu.setAttribute('aria-label', 'Ouvrir le menu');
  };
  boutonMenu.addEventListener('click', () => {
    const ouvert = entete.classList.toggle('est-ouvert');
    boutonMenu.setAttribute('aria-expanded', String(ouvert));
    boutonMenu.setAttribute('aria-label', ouvert ? 'Fermer le menu' : 'Ouvrir le menu');
  });
  // Une ancre de la même page referme le menu, sinon il masque la section visée.
  entete.querySelectorAll('.nav a').forEach((lien) => lien.addEventListener('click', fermer));
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') fermer(); });
}

/* ---- liens de compte ---------------------------------------------------- */

/* Sans identifiants Firebase, l'application n'a pas d'écran de connexion
   (CLAUDE.md § Exception assumée). La vitrine remplace alors « Se connecter » et
   « S'inscrire » par un seul « Ouvrir l'application ». En cas de doute (réseau,
   réponse illisible), on laisse les liens : ils ouvrent l'application de toute façon. */
fetch('/api/firebase')
  .then((r) => r.json())
  .then((config) => {
    if (config && config.active === false) document.body.classList.add('sans-compte');
  })
  .catch(() => {});

/* ---- chiffres qui roulent ----------------------------------------------- */

/* Chaque chiffre devient une colonne 0-9 qui monte jusqu'à sa valeur. Le montant
   exact reste dans le texte, pour les lecteurs d'écran. Le montant ne vient jamais
   d'un calcul ici : il est écrit dans la page, tel que `to_devis()` l'a produit. */
function fairerouler(el) {
  const valeur = el.dataset.valeur || el.textContent.trim();
  el.dataset.valeur = valeur;
  const dit = Object.assign(document.createElement('span'), { className: 'sr-only', textContent: valeur });
  const vu = Object.assign(document.createElement('span'), { className: 'roule' });
  vu.setAttribute('aria-hidden', 'true');
  let i = 0;
  for (const car of valeur) {
    // Les espaces (dont l'espace fine des milliers) se perdraient dans le conteneur
    // flex : chaque caractère non chiffré garde sa boîte.
    if (!/\d/.test(car)) {
      vu.append(Object.assign(document.createElement('span'), { className: 'roule__sep', textContent: car }));
      continue;
    }
    const col = Object.assign(document.createElement('span'), { className: 'roule__col' });
    const bande = Object.assign(document.createElement('span'), { className: 'roule__bande' });
    bande.style.setProperty('--c', car);
    bande.style.setProperty('--i', i++);
    for (let n = 0; n < 10; n++) bande.append(Object.assign(document.createElement('span'), { textContent: n }));
    col.append(bande);
    vu.append(col);
  }
  el.replaceChildren(dit, vu);
  // Deux images plus tard : le navigateur a posé l'état de départ, la transition part.
  requestAnimationFrame(() => requestAnimationFrame(() => vu.classList.add('est-pret')));
}

/* ---- les trois exemples ------------------------------------------------- */

/* Choisir un métier change le ticket de la une et la feuille de la section
   « Le devis » : c'est le même devis, vu deux fois. */
const FEUILLES = {
  elec: {
    src: '/static/site/img/devis-elec.jpg',
    alt: "Première page du devis généré pour la mise aux normes électriques chez M. Bertrand : quatre lignes, dont les saignées marquées estimées, total TTC 2 948,00 €.",
    legende: "Le devis réellement produit par Devis Vocal depuis le vocal de l'électricien, ci-dessus. Entreprise fictive.",
  },
  plomberie: {
    src: '/static/site/img/devis-plomberie.jpg',
    alt: "Première page du devis généré pour le remplacement d'un ballon d'eau chaude : client laissé à compléter, quatre lignes estimées, total TTC 1 853,50 €.",
    legende: "Le devis réellement produit par Devis Vocal depuis le vocal du plombier, ci-dessus. Le nom de la cliente n'ayant pas été dicté, il reste à compléter. Entreprise fictive.",
  },
  peinture: {
    src: '/static/site/img/devis-peinture.jpg',
    alt: "Première page du devis généré pour la peinture d'un deux-pièces rue de Charonne : quatre lignes, total TTC 3 938,00 €.",
    legende: "Le devis réellement produit par Devis Vocal depuis le vocal du peintre, ci-dessus. Entreprise fictive.",
  },
};

const choix = [...document.querySelectorAll('[data-exemple-choix]')];
const exemples = [...document.querySelectorAll('[data-exemple]')];
const feuille = document.querySelector('[data-feuille]');
const lienFeuille = document.querySelector('[data-loupe]');
const legende = document.querySelector('[data-feuille-legende]');

function montrerExemple(cle, { animer = true } = {}) {
  choix.forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.exempleChoix === cle)));
  exemples.forEach((ex) => {
    const actif = ex.dataset.exemple === cle;
    ex.hidden = !actif;
    ex.classList.toggle('vient-d-arriver', actif && animer);
    if (actif) ex.querySelectorAll('[data-roule]').forEach(fairerouler);
  });
  const f = FEUILLES[cle];
  if (feuille && f && feuille.getAttribute('src') !== f.src) {
    const changer = () => {
      feuille.src = f.src;
      feuille.alt = f.alt;
      if (lienFeuille) lienFeuille.href = f.src;
      if (legende) legende.textContent = f.legende;
      feuille.classList.remove('change');
    };
    if (mouvementReduit.matches) changer();
    else { feuille.classList.add('change'); setTimeout(changer, 280); }
  }
}

choix.forEach((b) => b.addEventListener('click', () => montrerExemple(b.dataset.exempleChoix)));
if (exemples.length) {
  // Le premier total roule une fois le ticket arrivé (fin de la séquence d'ouverture).
  setTimeout(() => montrerExemple('elec', { animer: false }), mouvementReduit.matches ? 0 : 900);
}

/* ---- la loupe ----------------------------------------------------------- */

/* Au survol, la feuille grossit sous le pointeur pour qu'on lise les mentions.
   Au clavier et au doigt, le lien ouvre l'image en grand. */
if (lienFeuille && feuille) {
  lienFeuille.addEventListener('pointermove', (e) => {
    if (!pointeurFin.matches || e.pointerType !== 'mouse') return;
    const r = lienFeuille.getBoundingClientRect();
    feuille.style.setProperty('--x', `${((e.clientX - r.left) / r.width) * 100}%`);
    feuille.style.setProperty('--y', `${((e.clientY - r.top) / r.height) * 100}%`);
    lienFeuille.classList.add('est-zoome');
  });
  lienFeuille.addEventListener('pointerleave', () => lienFeuille.classList.remove('est-zoome'));
  lienFeuille.addEventListener('click', (e) => {
    // À la souris, la loupe suffit ; le lien reste pour le clavier et le toucher.
    if (pointeurFin.matches && e.pointerType === 'mouse') e.preventDefault();
  });
}

/* ---- apparitions -------------------------------------------------------- */

/* Les enfants d'un bloc arrivent dans l'ordre de lecture, une fois, quand le haut
   du bloc atteint 88 % de l'écran. L'ordre est posé ici plutôt qu'à la main. */
document.querySelectorAll('[data-suite], [data-rangs]').forEach((bloc) => {
  [...bloc.children].forEach((enfant, i) => enfant.style.setProperty('--i', Math.min(i, 5)));
});

const aReveler = document.querySelectorAll('[data-suite], [data-rangs], [data-volet]');
if ('IntersectionObserver' in window && aReveler.length) {
  const observateur = new IntersectionObserver((entrees) => {
    for (const entree of entrees) {
      if (!entree.isIntersecting) continue;
      entree.target.classList.add('est-visible');
      observateur.unobserve(entree.target);
    }
  }, { rootMargin: '0px 0px -12% 0px' });
  aReveler.forEach((el) => observateur.observe(el));
} else {
  aReveler.forEach((el) => el.classList.add('est-visible'));
}

/* ---- ce qui boucle s'arrête hors de l'écran ----------------------------- */

const film = document.querySelector('[data-film]');
if (film && mouvementReduit.matches) {
  film.removeAttribute('autoplay');
  film.pause();
}
const boucles = document.querySelectorAll('[data-film], [data-ecoute]');
if ('IntersectionObserver' in window && boucles.length) {
  const veille = new IntersectionObserver((entrees) => {
    for (const { target, isIntersecting } of entrees) {
      target.classList.toggle('hors-champ', !isIntersecting);
      if (target.tagName === 'VIDEO' && !mouvementReduit.matches) {
        if (isIntersecting) target.play().catch(() => {});
        else target.pause();
      }
    }
  });
  boucles.forEach((el) => veille.observe(el));
}

/* ---- formulaire de contact ---------------------------------------------- */

/* Le serveur ne garde rien et n'envoie aucun e-mail (CLAUDE.md § Périmètre) :
   le formulaire prépare le message dans la messagerie de l'artisan, qui
   l'envoie lui-même. La CSP interdit toute soumission de formulaire
   (`form-action 'none'`) ; on compose donc un lien `mailto:`. */
const formulaire = document.getElementById('formulaire-contact');
if (formulaire) {
  const erreur = document.getElementById('formulaire-erreur');
  formulaire.addEventListener('submit', (e) => {
    e.preventDefault();
    const donnees = new FormData(formulaire);
    const valeur = (nom) => String(donnees.get(nom) || '').trim();

    const manquants = [];
    if (!valeur('nom')) manquants.push('votre nom');
    if (!valeur('joindre')) manquants.push('un e-mail ou un téléphone');
    if (!valeur('message')) manquants.push('votre message');
    if (manquants.length) {
      erreur.textContent = `Il manque ${manquants.join(', ')}.`;
      erreur.hidden = false;
      formulaire.querySelector(`[name="${
        !valeur('nom') ? 'nom' : !valeur('joindre') ? 'joindre' : 'message'
      }"]`).focus();
      return;
    }
    erreur.hidden = true;

    const metier = valeur('metier');
    const sujet = `Devis Vocal : message de ${valeur('nom')}${metier ? ` (${metier})` : ''}`;
    const corps = [
      valeur('message'),
      '',
      `Nom : ${valeur('nom')}`,
      valeur('entreprise') ? `Entreprise : ${valeur('entreprise')}` : null,
      metier ? `Métier : ${metier}` : null,
      `Me joindre : ${valeur('joindre')}`,
    ].filter((ligne) => ligne !== null).join('\n');

    window.location.href = `mailto:${formulaire.dataset.destinataire}?subject=${encodeURIComponent(sujet)}&body=${encodeURIComponent(corps)}`;
  });
}
