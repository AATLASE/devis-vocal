/* Mes prix — le tarif de l'artisan.

   Hors du périmètre d'origine, sur décision explicite (CLAUDE.md, « Exception assumée :
   tarif de l'artisan »). L'artisan connecté y range ses prix ; au chiffrage, une
   prestation dictée qui correspond à une ligne de son tarif prend son prix, sans la
   mention « estimé ».

   Trois façons de remplir le tableau : à la main, en dictant ses prix, en important un
   fichier Excel ou CSV. Les deux dernières ne font que **proposer** des lignes : elles
   arrivent dans le tableau avec ce qu'il faut regarder (prix introuvable, TTC, unité),
   et rien n'est enregistré avant « Enregistrer mon tarif ».

   Script classique, chargé après `app.js` dont il emprunte les outils (`$`, `noeud`,
   `poste`, `montrer`, `champEdition`, `lireNombre`, `UNITES`, `icone`, `transcrire`,
   `formatDisponible`, `raisonMicro`). Sans Firebase, rien ne l'ouvre : le bouton du
   menu n'existe pas, et ce fichier ne fait rien.

   Le navigateur ne décide de rien ici non plus : il relit le tableau et l'envoie, et
   c'est le serveur qui valide, numérote et refuse en nommant la ligne fautive. */

(() => {
  let tarif = null;      // le tarif tel que le serveur l'a renvoyé la dernière fois
  let modifie = false;   // le tableau diffère de ce qui est enregistré
  let retourArme = false;
  let viderArme = null;
  let enLecture = false; // une dictée ou un fichier est en cours de lecture

  const dire = (texte, ton, zone = 'prix-message') => {
    const el = $(zone);
    el.textContent = texte || '';
    el.className = 'template__message' + (ton ? ' template__message--' + ton : '');
    el.hidden = !texte;
  };
  const direImport = (texte, ton) => dire(texte, ton, 'prix-import-message');

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

  /* Une ligne du tableau. `p.drapeaux`, quand la ligne vient d'une lecture, dit ce que
     l'artisan doit regarder ; un prix lu TTC bloque l'enregistrement tant qu'il n'a
     pas été retouché, ou déclaré HT. */
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
    // Une ligne lue sans unité reconnue : l'artisan choisit, on ne devine pas.
    if (p && p.drapeaux && !p.unite) {
      const vide = noeud('option', null, 'Unité ?');
      vide.value = '';
      unite.append(vide);
    }
    UNITES.forEach((u) => {
      const option = noeud('option', null, u);
      option.value = u;
      unite.append(option);
    });
    unite.value = p && p.drapeaux && !p.unite ? ''
      : p && UNITES.includes(p.unite) ? p.unite : 'm²';

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

    const drapeaux = ((p && p.drapeaux) || []).filter((d) => d.code !== 'deja_identique');
    if (p && p.drapeaux) rang.classList.add('est-proposee');
    if (drapeaux.length) {
      const liste = noeud('ul', 'ligne-prix__drapeaux');
      drapeaux.forEach((d) => liste.append(noeud('li', null, d.texte)));
      rang.append(liste);
    }
    if (drapeaux.some((d) => d.code === 'ttc')) {
      rang.dataset.ttc = '1';
      const ht = noeud('input');
      ht.type = 'checkbox';
      ht.dataset.cle = 'est-ht';
      const etiquette = noeud('label', 'ligne-ed__prix');
      etiquette.append(ht, document.createTextNode('Ce prix est déjà HT'));
      rang.append(etiquette);
      // Retouché, le prix est celui que l'artisan a tapé dans la colonne HT.
      prix.addEventListener('input', () => { delete rang.dataset.ttc; etiquette.remove(); },
        { once: true });
    }

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

  const vide = (rang) => ['prestation', 'prix', 'precision']
    .every((cle) => !rang.querySelector(`[data-cle="${cle}"]`).value.trim());

  /* Relit le tableau. Une ligne entièrement vide est ignorée : c'est celle qu'on vient
     d'ajouter sans la remplir. Le reste est dit au champ près, ici ; les règles du
     tarif — unités, plafonds, doublons — sont tenues par le serveur. */
  function lireGrille() {
    const erreurs = [];
    const lignes = [];
    [...$('prix-lignes').children].forEach((rang, i) => {
      if (vide(rang)) return;
      const champ = (cle) => rang.querySelector(`[data-cle="${cle}"]`);
      const prestation = champ('prestation').value.trim();
      const brut = champ('prix').value.trim();
      const precision = champ('precision').value.trim();

      const prix = lireNombre(brut);
      champ('prestation').setAttribute('aria-invalid', String(!prestation));
      champ('prix').setAttribute('aria-invalid', String(prix === null));
      champ('unite').setAttribute('aria-invalid', String(!champ('unite').value));
      if (!prestation) erreurs.push(`Ligne ${i + 1} : indiquez la prestation.`);
      if (prix === null) {
        // Même règle que l'édition du devis : « 1.200 » peut valoir mille deux cents
        // ou un virgule deux. On ne devine pas un prix, on dit comment l'écrire.
        const motif = !brut ? 'est vide'
          : AMBIGU.test(sansEspaces(brut)) ? 'est ambigu : écrivez 1200, ou 1,2 pour une décimale'
          : 'est illisible';
        erreurs.push(`Ligne ${i + 1} : le prix ${motif}.`);
      }
      if (!champ('unite').value) erreurs.push(`Ligne ${i + 1} : choisissez l'unité.`);
      const ht = champ('est-ht');
      if (rang.dataset.ttc && !(ht && ht.checked)) {
        erreurs.push(`Ligne ${i + 1} : ce prix a été lu TTC. Ramenez-le en HT, `
          + 'ou cochez « Ce prix est déjà HT ».');
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

  /* ---- ajouter des prix lus ------------------------------------------------ */

  const normaliser = (t) => String(t || '').normalize('NFKD').replace(/[̀-ͯ]/g, '')
    .replace(/\s+/g, ' ').trim().toLowerCase();

  /* Les lignes lues rejoignent le tableau, sans rien enregistrer. Celles qui y sont
     déjà, au même prix, ne sont pas ajoutées une seconde fois. */
  function ajouterPropositions(lecture, origine) {
    const presentes = new Set([...$('prix-lignes').children].filter((r) => !vide(r)).map((r) => {
      const champ = (cle) => r.querySelector(`[data-cle="${cle}"]`).value;
      return [normaliser(champ('prestation')), normaliser(champ('precision')), champ('unite'),
        lireNombre(champ('prix'))].join('|');
    }));
    // La ligne vide d'un tarif neuf n'a plus de raison d'être.
    [...$('prix-lignes').children].filter(vide).forEach((r) => r.remove());

    let ajoutees = 0;
    let ignorees = 0;
    (lecture.lignes || []).forEach((p) => {
      const cle = [normaliser(p.prestation), normaliser(p.precision), p.unite || '',
        lireNombre(Number(p.prix_unitaire_ht).toFixed(2))].join('|');
      if (presentes.has(cle) || (p.drapeaux || []).some((d) => d.code === 'deja_identique')) {
        ignorees += 1;
        return;
      }
      presentes.add(cle);
      $('prix-lignes').append(rangDePrix(p));
      ajoutees += 1;
    });
    if (ajoutees) noterModification();

    const signalees = [...$('prix-lignes').querySelectorAll('.ligne-prix__drapeaux')].length;
    const morceaux = [];
    morceaux.push(ajoutees
      ? `${ajoutees} prix lu${ajoutees > 1 ? 's' : ''} dans ${origine}, ajouté${ajoutees > 1 ? 's' : ''} en bas du tableau.`
      : `Aucun nouveau prix lu dans ${origine}.`);
    if (ignorees) morceaux.push(`${ignorees} déjà dans votre tarif.`);
    if (lecture.remarque) morceaux.push(lecture.remarque);
    if (ajoutees) {
      morceaux.push(signalees
        ? 'Relisez-les, surtout les lignes signalées, puis enregistrez.'
        : 'Relisez-les, puis enregistrez.');
    }
    return morceaux.join(' ');
  }

  function occupe(actif) {
    enLecture = actif;
    ['btn-prix-dicter', 'btn-prix-fichier', 'btn-prix-lire'].forEach((id) => { $(id).disabled = actif; });
  }

  async function lire(donnees, origine) {
    const reponse = await poste('/api/grille/lire', { method: 'POST', body: donnees });
    return ajouterPropositions(await reponse.json(), origine);
  }

  async function importer(fichiers) {
    if (!fichiers.length || enLecture) return;
    occupe(true);
    const comptes = [];
    try {
      for (const fichier of fichiers) {
        direImport(`Lecture de « ${fichier.name} »… cela peut prendre une minute.`);
        const donnees = new FormData();
        donnees.append('fichier', fichier);
        try {
          comptes.push(await lire(donnees, `« ${fichier.name} »`));
        } catch (err) {
          if (err.porte) return;
          comptes.push(`« ${fichier.name} » : ${err.message}`);
        }
      }
      direImport(comptes.join(' '), 'ok');
    } finally {
      occupe(false);
      $('prix-fichier').value = '';
    }
  }

  /* ---- dicter ses prix ---------------------------------------------------- */
  /* Un petit enregistreur à part : celui de l'accueil est lié à son écran et enchaîne
     sur le devis. Celui-ci s'arrête, transcrit, et laisse l'artisan relire le texte
     avant de le faire lire — une erreur de transcription sur un prix se voit là. */

  let enregistreur = null;
  let flux = null;

  function couperMicro() {
    if (flux) flux.getTracks().forEach((t) => t.stop());
    flux = null;
    enregistreur = null;
    $('btn-prix-dicter').classList.remove('enregistre');
    $('prix-dicter-libelle').textContent = 'Dicter mes prix';
  }

  async function basculerDictee() {
    if (enregistreur) {
      enregistreur.stop();
      return;
    }
    const format = formatDisponible();
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia || !format) {
      direImport(raisonMicro(null), 'alerte');
      return;
    }
    try {
      flux = await navigator.mediaDevices.getUserMedia({ audio: CONTRAINTES_MICRO });
    } catch (err) {
      direImport(raisonMicro(err), 'alerte');
      return;
    }
    const [type, extension] = format;
    const morceaux = [];
    try {
      enregistreur = new MediaRecorder(flux, type ? { mimeType: type, audioBitsPerSecond: DEBIT_DICTEE } : undefined);
    } catch (_) {
      enregistreur = new MediaRecorder(flux);
    }
    enregistreur.addEventListener('dataavailable', (e) => { if (e.data.size) morceaux.push(e.data); });
    enregistreur.addEventListener('stop', async () => {
      const typeReel = enregistreur.mimeType || type || 'audio/webm';
      couperMicro();
      const blob = new Blob(morceaux, { type: typeReel });
      if (blob.size < 2000) {
        direImport('Dictée trop courte. Touchez « Dicter mes prix », parlez, puis touchez à nouveau.', 'alerte');
        return;
      }
      occupe(true);
      direImport('Transcription de votre dictée…');
      try {
        const texte = await transcrire(new File([blob], `prix.${extension}`, { type: typeReel }));
        $('prix-dictee-texte').value = texte;
        $('prix-dictee').hidden = false;
        direImport('Relisez le texte — surtout les prix —, corrigez-le si besoin, puis touchez « Lire mes prix ».');
        $('prix-dictee-texte').focus();
      } catch (err) {
        if (!err.porte) direImport(err.message, 'alerte');
      } finally {
        occupe(false);
      }
    });
    enregistreur.start();
    $('btn-prix-dicter').classList.add('enregistre');
    $('prix-dicter-libelle').textContent = 'Arrêter la dictée';
    $('prix-dictee').hidden = true;
    direImport('Dictez vos prix : « la pose de carrelage, 45 le mètre carré ; un mitigeur posé, 185… ». '
      + 'Touchez à nouveau pour arrêter.');
  }

  async function lireDictee() {
    const texte = $('prix-dictee-texte').value.trim();
    if (!texte || enLecture) return;
    occupe(true);
    direImport('Lecture de vos prix… cela peut prendre une minute.');
    try {
      const donnees = new FormData();
      donnees.append('texte', texte);
      direImport(await lire(donnees, 'votre dictée'), 'ok');
      $('prix-dictee').hidden = true;
      $('prix-dictee-texte').value = '';
    } catch (err) {
      if (!err.porte) direImport(err.message, 'alerte');
    } finally {
      occupe(false);
    }
  }

  /* ---- les actions -------------------------------------------------------- */

  async function ouvrir() {
    montrer('prix');
    dire('');
    direImport('');
    $('prix-dictee').hidden = true;
    $('prix-etat').textContent = 'Lecture…';
    $('prix-meta').textContent = '';
    try {
      peindreGrille(await appel('GET'));
    } catch (err) {
      if (err.porte) return;
      // On ne sait pas ce qui est enregistré : on ne l'affirme pas, et on n'offre pas
      // d'enregistrer un tableau vide par-dessus un tarif qu'on n'a pas su lire.
      tarif = null;
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
      direImport('');
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
    if (enregistreur) enregistreur.stop();
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
    if ((modifie || enregistreur || enLecture) && $('ecran-prix').classList.contains('is-active')) {
      e.preventDefault();
    }
  });

  document.addEventListener('DOMContentLoaded', () => {
    if (!$('ecran-prix')) return;
    $('btn-prix-retour').addEventListener('click', retour);
    $('btn-prix-ajouter').addEventListener('click', ajouter);
    $('btn-prix-enregistrer').addEventListener('click', enregistrer);
    $('btn-prix-vider').addEventListener('click', vider);
    $('btn-prix-dicter').addEventListener('click', basculerDictee);
    $('btn-prix-lire').addEventListener('click', lireDictee);
    $('btn-prix-dictee-annuler').addEventListener('click', () => {
      $('prix-dictee').hidden = true;
      $('prix-dictee-texte').value = '';
      direImport('');
    });
    $('btn-prix-fichier').addEventListener('click', () => $('prix-fichier').click());
    $('prix-fichier').addEventListener('change', (e) => importer([...e.target.files]));
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
      if (enregistreur) enregistreur.stop();
    },
  };
})();
