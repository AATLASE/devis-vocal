/* Orchestration des trois étapes. Le devis vit dans le navigateur : le serveur ne garde rien. */

const $ = (id) => document.getElementById(id);

const euro = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR" });
const nombre = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 3 });

let devisCourant = null;
let chronoTimer = null;

/* ---------- Affichage ---------- */

function montrer(id) { $(id).classList.remove("cachee"); }
function cacher(id) { $(id).classList.add("cachee"); }

function pas(id, etat) {
  const li = $(id);
  li.classList.remove("encours", "faite");
  if (etat) li.classList.add(etat);
}

function demarrerChrono() {
  const debut = Date.now();
  chronoTimer = setInterval(() => {
    const s = Math.round((Date.now() - debut) / 1000);
    $("chrono").textContent = `${s} s — le chiffrage prend en général une trentaine de secondes.`;
  }, 1000);
}

function arreterChrono() {
  clearInterval(chronoTimer);
  $("chrono").textContent = "";
}

function reinitialiser() {
  arreterChrono();
  devisCourant = null;
  ["etape-progression", "etape-erreur", "etape-transcription", "etape-devis"].forEach(cacher);
  montrer("etape-depot");
  ["pas-transcription", "pas-analyse", "pas-pdf"].forEach((p) => pas(p, null));
  $("fichier").value = "";
}

function echouer(message) {
  arreterChrono();
  cacher("etape-progression");
  $("erreur-message").textContent = message;
  montrer("etape-erreur");
}

/* ---------- Appels ---------- */

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
  donnees.append("audio", fichier);
  const r = await poste("/api/transcribe", { method: "POST", body: donnees });
  return (await r.json()).transcription;
}

async function chiffrer(transcription) {
  const r = await poste("/api/devis", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transcription }),
  });
  return await r.json();
}

/* ---------- Parcours ---------- */

async function lancer(fichier, texte) {
  cacher("etape-depot");
  cacher("etape-erreur");
  montrer("etape-progression");
  demarrerChrono();

  try {
    let transcription = texte;

    if (fichier) {
      pas("pas-transcription", "encours");
      transcription = await transcrire(fichier);
      pas("pas-transcription", "faite");
    } else {
      pas("pas-transcription", "faite");
    }

    // On affiche la transcription tout de suite : c'est le moment où l'artisan
    // voit que la machine l'a compris, et ça fait patienter pendant le chiffrage.
    $("transcription").textContent = transcription;
    montrer("etape-transcription");

    pas("pas-analyse", "encours");
    devisCourant = await chiffrer(transcription);
    pas("pas-analyse", "faite");

    afficherDevis(devisCourant);
    arreterChrono();
    cacher("etape-progression");
    montrer("etape-devis");
    $("etape-devis").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) {
    echouer(err.message);
  }
}

/* ---------- Rendu du devis ---------- */

function afficherDevis(devis) {
  $("devis-numero").textContent = devis.numero;

  const client = [devis.client.nom, devis.client.adresse].filter(Boolean).join(" — ");
  $("devis-client").textContent = client || "Client à compléter";

  const corps = $("lignes");
  corps.innerHTML = "";

  if (devis.lignes.length === 0) {
    corps.innerHTML = '<tr><td colspan="5" class="vide">Aucune prestation chiffrable — voir les observations.</td></tr>';
  }

  for (const ligne of devis.lignes) {
    const tr = document.createElement("tr");

    const tdDesignation = document.createElement("td");
    tdDesignation.append(ligne.designation);
    if (ligne.a_valider) {
      const etoile = document.createElement("span");
      etoile.className = "etoile";
      etoile.textContent = "*";
      tdDesignation.append(etoile);
    }
    if (ligne.detail) {
      const detail = document.createElement("span");
      detail.className = "detail";
      detail.textContent = ligne.detail;
      tdDesignation.append(detail);
    }
    tr.append(tdDesignation);

    tr.append(cellule(nombre.format(ligne.quantite), "droite"));
    tr.append(cellule(ligne.unite, "centre"));
    tr.append(cellule(euro.format(ligne.prix_unitaire_ht), "droite"));
    tr.append(cellule(euro.format(ligne.total_ht), "droite"));

    corps.append(tr);
  }

  $("legende-estime").classList.toggle("cachee", !devis.lignes.some((l) => l.a_valider));

  const observations = $("observations");
  observations.innerHTML = "";
  if (devis.notes.length) {
    const libelle = document.createElement("div");
    libelle.className = "libelle";
    libelle.textContent = "Observations";
    const ul = document.createElement("ul");
    for (const note of devis.notes) {
      const li = document.createElement("li");
      li.textContent = note;
      ul.append(li);
    }
    observations.append(libelle, ul);
  }

  const tauxPct = nombre.format(Number(devis.taux_tva) * 100);
  const acomptePct = nombre.format(Number(devis.acompte_pct) * 100);
  $("total-ht").textContent = euro.format(devis.total_ht);
  $("libelle-tva").textContent = `TVA ${tauxPct} %`;
  $("montant-tva").textContent = euro.format(devis.montant_tva);
  $("total-ttc").textContent = euro.format(devis.total_ttc);
  $("libelle-acompte").textContent = `Acompte à la commande (${acomptePct} %)`;
  $("montant-acompte").textContent = euro.format(devis.montant_acompte);
}

function cellule(texte, classe) {
  const td = document.createElement("td");
  td.className = classe;
  td.textContent = texte;
  return td;
}

/* ---------- PDF ---------- */

async function telechargerPdf() {
  const bouton = $("btn-pdf");
  const libelle = bouton.textContent;
  bouton.disabled = true;
  bouton.textContent = "Génération…";
  pas("pas-pdf", "encours");

  try {
    const r = await poste("/api/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(devisCourant),
    });
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const lien = document.createElement("a");
    lien.href = url;
    lien.download = `${devisCourant.numero}.pdf`;
    lien.click();
    URL.revokeObjectURL(url);
    pas("pas-pdf", "faite");
  } catch (err) {
    echouer(err.message);
  } finally {
    bouton.disabled = false;
    bouton.textContent = libelle;
  }
}

/* ---------- Branchements ---------- */

$("fichier").addEventListener("change", (e) => {
  if (e.target.files.length) lancer(e.target.files[0], null);
});

const depot = $("depot");
["dragenter", "dragover"].forEach((evt) =>
  depot.addEventListener(evt, (e) => { e.preventDefault(); depot.classList.add("survol"); })
);
["dragleave", "drop"].forEach((evt) =>
  depot.addEventListener(evt, (e) => { e.preventDefault(); depot.classList.remove("survol"); })
);
depot.addEventListener("drop", (e) => {
  if (e.dataTransfer.files.length) lancer(e.dataTransfer.files[0], null);
});

$("btn-texte").addEventListener("click", () => {
  const texte = $("texte").value.trim();
  if (texte) lancer(null, texte);
});

$("btn-pdf").addEventListener("click", telechargerPdf);
$("btn-nouveau").addEventListener("click", reinitialiser);
$("btn-recommencer").addEventListener("click", reinitialiser);
