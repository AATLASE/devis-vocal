/* Mes prix — le tarif de l'artisan.

   Hors du périmètre d'origine, sur décision explicite (CLAUDE.md, « Exception assumée :
   tarif de l'artisan »). L'artisan connecté y range ses prix ; au chiffrage, une
   prestation dictée qui correspond à une ligne de son tarif prend son prix, sans la
   mention « estimé ».

   Script classique, chargé après `app.js` dont il emprunte les outils (`$`, `noeud`,
   `poste`, `montrer`, `champEdition`, `lireNombre`, `UNITES`, `icone`). Sans Firebase,
   rien ne l'ouvre : le bouton du menu n'existe pas, et ce fichier ne fait rien.

   Le navigateur ne décide de rien ici non plus : il relit le tableau et l'envoie, et
   c'est le serveur qui valide, numérote et refuse en nommant la ligne fautive. */

(() => {
  let tarif = null;      // le tarif tel que le serveur l'a renvoyé la dernière fois
  let modifie = false;   // le tableau diffère de ce qui est enregistré
  let retourArme = false;
  let viderArme = null;

  const dire = (texte, ton) => {
    const zone = $('prix-message');
    zone.textContent = texte || '';
    zone.className = 'template__message' + (ton ? ' template__message--' + ton : '');
    zone.hidden = !texte;
  };

  async function appel(methode, corps) {
    const options = { method: methode };
    if (corps !== undefined) {
      options.headers = { 'Content-Type': 'application/json' };
      options.body = JSON.stringify(corps);
    }
    // `poste` ajoute le jeton du compte et le code d'accès, et traduit les refus.
    return (await poste('/api/grille', options)).json();
  }

  /* ---- le tableau --------------------------------------------------------- */

  function rangDePrix(p) {
    const rang = noeud('div', 'ligne-ed ligne-prix');
    if (p && p.id) rang.dataset.id = p.id;

    const saisie = (cle, valeur, classe) => {
      const el = noeud('input', classe);
      el.dataset.cle = cle;
      el.autocomplete = 'off';
      el.value = valeur;
      return el;
    };
    const prestation = saisie('prestation', (p && p.prestation) || '');
    // « 42,50 » plutôt que « 42,5 » : c'est ainsi qu'un prix s'écrit sur un devis.
    const prix = saisie('prix', p && p.prix_unitaire_ht != null
      ? Number(p.prix_unitaire_ht).toFixed(2).replace('.', ',') : '', 'num');
    const precision = saisie('precision', (p && p.precision) || '');
    prix.inputMode = 'decimal';
    prestation.placeholder = 'Pose de carrelage de sol, fourniture client';
    precision.placeholder = 'Hors dépose, gamme courante…';

    const unite = noeud('select');
    unite.dataset.cle = 'unite';
    UNITES.forEach((u) => {
      const option = noeud('option', null, u);
      option.value = u;
      unite.append(option);
    });
    unite.value = p && UNITES.includes(p.unite) ? p.unite : 'm²';

    const retirer = noeud('button', 'ligne-ed__retirer');
    retirer.type = 'button';
    retirer.setAttribute('aria-label', 'Retirer cette ligne');
    retirer.append(icone('i-croix'));
    retirer.addEventListener('click', () => {
      rang.remove();
      noterModification();
    });

    rang.append(
      champEdition('champ--designation', 'Prestation', prestation),
      champEdition(null, 'Unité', unite),
      champEdition('champ--prix', 'Prix HT (€)', prix),
      retirer,
      champEdition('champ--detail', 'Précision (facultatif)', precision),
    );
    rang.addEventListener('input', noterModification);
    rang.addEventListener('change', noterModification);
    return rang;
  }

  function noterModification() {
    modifie = true;
    retourArme = false;
    compter();
  }

  function compter() {
    const n = $('prix-lignes').children.length;
    const max = (tarif && tarif.max_lignes) || 300;
    $('prix-compte').textContent = `${n} / ${max} lignes`;
  }

  /* Relit le tableau. Une ligne entièrement vide est ignorée : c'est celle qu'on vient
     d'ajouter sans la remplir. Le reste est dit au champ près, ici ; les règles du
     tarif — unités, plafonds, doublons — sont tenues par le serveur. */
  function lireGrille() {
    const erreurs = [];
    const lignes = [];
    [...$('prix-lignes').children].forEach((rang, i) => {
      const champ = (cle) => rang.querySelector(`[data-cle="${cle}"]`);
      const prestation = champ('prestation').value.trim();
      const brut = champ('prix').value.trim();
      const precision = champ('precision').value.trim();
      if (!prestation && !brut && !precision) return;

      const prix = lireNombre(brut);
      champ('prestation').setAttribute('aria-invalid', String(!prestation));
      champ('prix').setAttribute('aria-invalid', String(prix === null));
      if (!prestation) erreurs.push(`Ligne ${i + 1} : indiquez la prestation.`);
      if (prix === null) {
        // Même règle que l'édition du devis : « 1.200 » peut valoir mille deux cents
        // ou un virgule deux. On ne devine pas un prix, on dit comment l'écrire.
        const motif = !brut ? 'est vide'
          : AMBIGU.test(sansEspaces(brut)) ? 'est ambigu : écrivez 1200, ou 1,2 pour une décimale'
          : 'est illisible';
        erreurs.push(`Ligne ${i + 1} : le prix ${motif}.`);
      }
      lignes.push({
        id: rang.dataset.id || null,
        prestation,
        unite: champ('unite').value,
        prix_unitaire_ht: prix,
        precision: precision || null,
      });
    });
    return { lignes, erreurs };
  }

  function peindreGrille(etat) {
    tarif = etat;
    const lignes = etat.lignes || [];
    $('prix-lignes').replaceChildren(...lignes.map(rangDePrix));
    // Un tarif vide s'ouvre sur une ligne à remplir : c'est l'invitation la plus claire.
    if (!lignes.length) $('prix-lignes').append(rangDePrix(null));

    $('prix-etat').textContent = lignes.length
      ? `${lignes.length} prix enregistré${lignes.length > 1 ? 's' : ''}`
      : 'Aucun prix enregistré';
    $('prix-meta').textContent = etat.modifie_le
      ? 'Modifié le ' + new Date(etat.modifie_le).toLocaleDateString('fr-FR',
        { day: 'numeric', month: 'long', year: 'numeric' })
      : 'Vos devis sont chiffrés sur des prix de marché, tous à vérifier.';
    $('btn-prix-vider').hidden = !lignes.length;
    desarmerVider();
    modifie = false;
    retourArme = false;
    compter();
  }

  /* ---- les actions -------------------------------------------------------- */

  async function ouvrir() {
    montrer('prix');
    dire('');
    $('prix-etat').textContent = 'Lecture…';
    $('prix-meta').textContent = '';
    try {
      peindreGrille(await appel('GET'));
    } catch (err) {
      if (err.porte) return;
      // On ne sait pas ce qui est enregistré : on ne l'affirme pas, et on n'offre pas
      // d'enregistrer un tableau vide par-dessus un tarif qu'on n'a pas su lire.
      $('prix-etat').textContent = 'État inconnu';
      $('prix-lignes').replaceChildren();
      dire("Votre tarif n'a pas pu être lu. " + err.message, 'alerte');
    }
  }

  async function enregistrer() {
    if (!tarif) return;
    const { lignes, erreurs } = lireGrille();
    if (erreurs.length) {
      dire(erreurs.join(' '), 'alerte');
      return;
    }
    const bouton = $('btn-prix-enregistrer');
    bouton.disabled = true;
    dire('Enregistrement…');
    try {
      peindreGrille(await appel('PUT', { lignes }));
      dire(lignes.length
        ? 'Tarif enregistré. Il servira à vos prochains devis.'
        : 'Tarif vidé : vos devis seront chiffrés sur des prix de marché.', 'ok');
    } catch (err) {
      if (!err.porte) dire(err.message, 'alerte');
    } finally {
      bouton.disabled = false;
    }
  }

  /* Supprimer tout le tarif se fait en deux touches : pas de boîte de dialogue, mais
     un bouton qui demande confirmation et retombe de lui-même s'il n'est pas repris. */
  function desarmerVider() {
    clearTimeout(viderArme);
    viderArme = null;
    $('btn-prix-vider').textContent = 'Supprimer tout mon tarif';
  }

  async function vider() {
    if (!viderArme) {
      $('btn-prix-vider').textContent = 'Toucher à nouveau pour tout supprimer';
      viderArme = setTimeout(desarmerVider, 4000);
      return;
    }
    desarmerVider();
    try {
      peindreGrille(await appel('DELETE'));
      dire('Tarif supprimé : vos devis seront chiffrés sur des prix de marché.', 'ok');
    } catch (err) {
      if (!err.porte) dire(err.message, 'alerte');
    }
  }

  function retour() {
    if (modifie && !retourArme) {
      retourArme = true;
      dire('Vos modifications ne sont pas enregistrées. Enregistrez-les, ou touchez '
        + 'Retour une seconde fois pour les abandonner.', 'alerte');
      return;
    }
    modifie = false;
    montrer('accueil');
  }

  function ajouter() {
    const rang = rangDePrix(null);
    $('prix-lignes').append(rang);
    noterModification();
    rang.querySelector('[data-cle="prestation"]').focus();
  }

  window.addEventListener('beforeunload', (e) => {
    if (modifie && $('ecran-prix').classList.contains('is-active')) e.preventDefault();
  });

  document.addEventListener('DOMContentLoaded', () => {
    if (!$('ecran-prix')) return;
    $('btn-prix-retour').addEventListener('click', retour);
    $('btn-prix-ajouter').addEventListener('click', ajouter);
    $('btn-prix-enregistrer').addEventListener('click', enregistrer);
    $('btn-prix-vider').addEventListener('click', vider);
  });

  window.Prix = {
    ouvrir,
    /* La prestation du tarif derrière « votre prix », pour l'info-bulle de la relecture.
       Rien si le tarif n'a pas encore été lu : l'étiquette suffit à elle seule. */
    libelle(id) {
      const ligne = tarif && (tarif.lignes || []).find((p) => p.id === id);
      return ligne ? ligne.prestation : '';
    },
    /* À la déconnexion, ou quand un autre compte se connecte sur le même appareil. */
    oublier() {
      tarif = null;
      modifie = false;
    },
  };
})();
