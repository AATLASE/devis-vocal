"""Fumigation du rendu PDF.

Ce qui est protégé ici : la pagination, le formatage français, et le fait que le template
se rende sur chacune des fixtures. Un template cassé se voit dans un test rouge, pas devant
un client. Aucun appel API, aucun accès réseau — les polices voyagent avec le document.
"""

from __future__ import annotations

import asyncio
import re
import struct
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.async_api import Page

from app import pdf as pdf_module
from app import templates
from app.config import get_config
from app.models import DevisExtraction, LigneDevis, Unite, to_devis
from app.pdf import (
    arreter,
    f_date_longue,
    f_euro,
    f_montant,
    f_nombre,
    f_pourcent,
    _hauteur_lignes,
    paginer,
    render,
    render_html,
)
from tests.test_structuration import ENTREPRISE, INSTANT, noms_fixtures

FIXTURES = Path(__file__).parent / "fixtures"


def devis_de(nom: str):
    extraction = DevisExtraction.model_validate_json((FIXTURES / f"{nom}.json").read_text(encoding="utf-8"))
    return to_devis(extraction, entreprise=ENTREPRISE, maintenant=INSTANT)


@pytest.mark.parametrize("nom", noms_fixtures())
def test_le_html_se_rend(nom):
    html = render_html(devis_de(nom))
    assert "DEV-20260827-1432" in html
    assert "Bon pour accord" in html
    assert "279-0 bis" in html  # la mention de TVA réduite doit être là
    assert ENTREPRISE.metier in html  # la ligne de métier sous la raison sociale


@pytest.mark.parametrize("nom", noms_fixtures())
def test_le_html_a_exactement_le_nombre_de_pages_annonce(nom):
    """Une page de plus dans le PDF que dans le HTML signifierait un débordement ;
    ici on vérifie déjà que le template produit ce que `paginer()` a décidé."""
    devis = devis_de(nom)
    assert render_html(devis).count('<article class="page">') == paginer(devis).total_pages


def test_en_franchise_la_mention_293_b_remplace_la_tva():
    """Ni ligne de TVA, ni numéro de TVA, ni attestation du taux réduit : la mention
    du régime à leur place, en tête, dans les totaux et dans les conditions."""
    extraction = DevisExtraction.model_validate_json((FIXTURES / "sdb.json").read_text(encoding="utf-8"))
    franchise = ENTREPRISE.model_copy(update={"franchise_tva": True})
    html = render_html(to_devis(extraction, entreprise=franchise, maintenant=INSTANT))

    assert html.count("TVA non applicable, art. 293 B du CGI") == 3
    assert ENTREPRISE.tva_intracom not in html
    assert "279-0 bis" not in html
    assert "TVA 10" not in html and "Total TTC" not in html


def test_a_5_5_pourcent_le_devis_cite_l_article_278_0_bis_a():
    """Un 5,5 % sous la mention du taux normal ou du 279-0 bis imprimerait un fondement
    faux sous la signature du client."""
    from decimal import Decimal

    extraction = DevisExtraction.model_validate_json((FIXTURES / "sdb.json").read_text(encoding="utf-8"))
    html = render_html(to_devis(extraction, entreprise=ENTREPRISE, maintenant=INSTANT,
                                tva_forcee=Decimal("0.055")))

    assert "TVA 5,5" in html
    assert "278-0 bis A" in html
    assert "279-0 bis" not in html and "taux normal" not in html


def _texte_imprime(devis) -> str:
    """Le texte que le PDF imprime : sans `<title>`, styles ni commentaires, blancs unifiés."""
    return templates._texte_rendu(render_html(devis))


def test_un_particulier_recoit_le_formulaire_et_un_acompte_differe():
    """Le parcours visé est une vente hors établissement : formulaire type (L221-5),
    aucun paiement avant sept jours (L221-10), demande expresse d'exécution anticipée
    (L221-25). Particulier est la valeur par défaut d'un client."""
    texte = _texte_imprime(devis_de("elec"))

    assert "formulaire de rétractation" in texte
    assert "je/nous (*) vous notifie/notifions (*)" in texte
    assert "(*) rayez la mention inutile." in texte
    assert "exigible après 7 jours" in texte
    assert "je demande que les travaux commencent avant la fin du délai de rétractation" in texte
    assert "à la commande" not in texte


def test_un_client_professionnel_n_a_ni_formulaire_ni_acompte_differe():
    devis = devis_de("elec").model_copy(deep=True)
    devis.client.professionnel = True
    texte = _texte_imprime(devis)

    assert "formulaire de rétractation" not in texte
    assert "exigible après 7 jours" not in texte
    assert "avant la fin du délai de rétractation" not in texte
    assert "à la commande" in texte
    assert "l221-3" in texte


def test_le_solde_n_a_qu_une_echeance_et_les_penalites_suivent_le_client():
    """« Solde à la réception » puis « à 30 jours » : deux échéances pour le même
    solde. Et le taux BCE + 10 et les 40 € (L441-10, D441-5) ne valent qu'entre pros."""
    particulier = _texte_imprime(devis_de("elec"))
    assert "solde à la réception des travaux" in particulier
    assert "chèque à 30 jours" not in particulier
    assert "taux légal" in particulier
    assert "40 € pour frais de recouvrement" not in particulier
    assert "banque centrale européenne" not in particulier

    devis = devis_de("elec").model_copy(deep=True)
    devis.client.professionnel = True
    pro = _texte_imprime(devis)
    assert "chèque à 30 jours" not in pro
    assert "40 € pour frais de recouvrement" in pro and "l441-10" in pro


def test_le_debut_des_travaux_est_toujours_imprime_sans_rien_inventer():
    """L111-1 3° : la date ou le délai d'exécution. Non dicté, une ligne à remplir ;
    saisi à l'écran, la valeur saisie."""
    sans = _texte_imprime(devis_de("elec"))
    assert "début des travaux …………………………" in sans

    devis = devis_de("elec").model_copy(deep=True)
    devis.debut_travaux = "Semaine du 16 novembre"
    devis.client.adresse_chantier = "5 rue Xavier Privas, 69002 Lyon"
    avec = _texte_imprime(devis)
    assert "début des travaux semaine du 16 novembre" in avec
    assert "chantier : 5 rue xavier privas, 69002 lyon" in avec


def test_l_immatriculation_est_imprimee_a_cote_du_siret():
    """RCS et ville du greffe (art. R123-237 du Code de commerce), ou répertoire des
    métiers : à côté du SIRET, et dans le pied de la page des conditions."""
    assert "immatriculation à compléter" in _texte_imprime(devis_de("elec"))

    devis = devis_de("elec").model_copy(deep=True)
    devis.entreprise.immatriculation = "RCS Lyon"
    texte = _texte_imprime(devis)
    assert f"siret {ENTREPRISE.siret} · rcs lyon" in texte
    assert f"{ENTREPRISE.forme_juridique.lower()} · rcs lyon · siret" in texte


def test_le_mediateur_est_nomme_ou_a_completer():
    """R616-1 : ses coordonnées et son site. Sans eux, « à compléter » plutôt qu'une
    phrase qui paraît conforme."""
    sans = _texte_imprime(devis_de("elec"))
    assert "médiateur à compléter, site internet à compléter" in sans

    devis = devis_de("elec").model_copy(deep=True)
    devis.entreprise.mediateur = "Médiateur Exemple, 3 rue des Arbitres, 75009 Paris"
    devis.entreprise.mediateur_site = "mediateur.example"
    avec = _texte_imprime(devis)
    assert "médiateur exemple, 3 rue des arbitres, 75009 paris, mediateur.example" in avec
    assert "à compléter" not in avec.split("médiation de la consommation")[1].split("réserves")[0]


def test_les_prix_estimes_sont_signales_dans_le_html():
    # Sur le papier, le signal est le dague : c'est ce qui survit à une imprimante
    # noir et blanc, là où la teinte brique disparaît.
    assert "estimé †" in render_html(devis_de("sdb"))
    assert "estimé †" not in render_html(devis_de("peinture"))


def test_la_note_dit_que_la_quantite_aussi_peut_etre_estimee():
    """Le modèle estime aussi un métrage non dicté (« deux murs », sans surface) et
    marque la ligne. Une note qui ne parle que du prix enverrait l'artisan, et le
    client, vérifier le mauvais champ."""
    texte = _texte_imprime(devis_de("sdb"))

    assert "ligne estimée : quantité ou prix unitaire non dicté" in texte
    assert "prix non dicté" not in texte


async def test_le_pdf_se_genere():
    contenu = await render(devis_de("sdb"))
    try:
        assert contenu[:4] == b"%PDF"
        assert len(contenu) > 10_000
    finally:
        await arreter()


DESIGNATION = ("Fourniture et pose d'un receveur de douche extra-plat en résine avec bonde et "
               "siphon, y compris reprise de l'étanchéité sous carrelage et raccordement à "
               "l'évacuation existante")
PRECISION = ("Fourniture et pose comprises, finition satinée, teinte au choix du client, deux "
             "couches sur impression, protection des sols et nettoyage du chantier en fin "
             "d'intervention, évacuation des déchets en déchetterie agréée.")
OBSERVATION = ("Le support n'a pas été précisé, un doublage hydrofuge peut être nécessaire et "
               "n'est pas compté au présent devis. L'accès au chantier se fait par un escalier "
               "étroit, sans ascenseur, ce qui allonge la dépose et l'évacuation des gravats. Le "
               "client fournit la faïence, livrée avant le démarrage.")


def _cas_de_pagination():
    """Les fixtures, et des devis placés de part et d'autre de chaque frontière : ceux du
    constat de la revue (précisions et désignations longues, observations démesurées),
    pour un particulier et pour un professionnel, dont le bon pour accord diffère."""
    cas = [devis_de(nom) for nom in noms_fixtures()]
    observation = ("Le support n'a pas été précisé, un doublage hydrofuge peut "
                   "être nécessaire et n'est pas compté au présent devis.")
    peinture = devis_de("peinture").lignes
    for professionnel in (False, True):
        cas += [
            _devis(peinture, ["Une seule remarque, courte."], professionnel=professionnel),
            _devis(peinture, [observation] * 5, professionnel=professionnel),
            # (a) cinq lignes, désignation de 50 caractères, précision de 150, deux observations
            _devis(_lignes(5, DESIGNATION[:50], PRECISION[:150]), [observation] * 2,
                   professionnel=professionnel),
            # (b) 21 lignes, désignation de 70 caractères, précision courte
            _devis(_lignes(21, DESIGNATION[:70], "Fourniture client"), professionnel=professionnel),
            # (c) sept lignes de 180 caractères, précision de 80
            _devis(_lignes(7, DESIGNATION[:180], PRECISION[:80]), professionnel=professionnel),
            # (d) une ligne et quinze observations de 250 caractères
            _devis(_lignes(1), [OBSERVATION[:250]] * 15, professionnel=professionnel),
            # sept lignes simples : la page 1 pleine, clôture comprise
            _devis(_lignes(7), professionnel=professionnel),
            # la clôture sur une page de suite, sous des précisions longues
            _devis(_lignes(12, DESIGNATION[:60], PRECISION[:140]), professionnel=professionnel),
        ]
    # Un en-tête qui grandit : raison sociale sur deux lignes, client sur trois, chantier.
    haut = _devis(_lignes(7))
    haut.entreprise.nom = "ETABLISSEMENTS DUPONT ET FILS (DUPONT PLOMBERIE)"
    haut.client = haut.client.model_copy(update={
        "nom": "Mme Hélène Durand", "telephone": "06 12 34 56 78",
        "adresse_chantier": "Résidence Les Tilleuls, bâtiment B, 8 allée des Tilleuls, 69300 Caluire"})
    cas.append(haut)
    return cas


async def test_le_pdf_a_exactement_le_nombre_de_pages_annonce():
    """Le filet de la pagination. `paginer()` raisonne sur des hauteurs mesurées ; si un
    jour le template change et que ces mesures se périment, le contenu déborde et Chromium
    ajoute une page — invisible au HTML, bien visible chez le client, et tous les « Page
    n / N » qui suivent sont faux. Ce test rend réellement le PDF et compare."""
    try:
        for devis in _cas_de_pagination():
            attendu = paginer(devis).total_pages
            contenu = await render(devis)
            pages = int(re.search(rb"/Count (\d+)", contenu).group(1))
            assert pages == attendu, (
                f"{len(devis.lignes)} lignes, {len(devis.notes)} observations, "
                f"{'pro' if devis.client.professionnel else 'particulier'} : "
                f"{pages} pages rendues pour {attendu} annoncées"
            )
    finally:
        await arreter()


async def test_le_pdf_embarque_ses_polices_de_texte():
    """Chromium n'embarque pas les polices variables : il en dessine les lettres en
    courbes. Le document reste juste à l'œil, mais son texte cesse d'être du texte —
    plus de sélection, plus de recherche, sur l'adresse du client comme sur les
    mentions légales. Le sans et le mono sont donc aplatis en graisses statiques
    (`scripts/aplatir_polices.py`), et ce test empêche qu'on les y remette.

    Source Serif 4 est volontairement absent de cette liste : il garde son axe
    optique, et ses six titres sortent en courbes. C'est assumé."""
    contenu = await render(devis_de("sdb"))
    try:
        familles = {m.decode().split("+")[-1] for m in re.findall(rb"/BaseFont\s*/([\w+#\-,]+)", contenu)}
        assert any(f.startswith("IBMPlexSans") for f in familles), familles
        assert any(f.startswith("IBMPlexMono") for f in familles), familles
    finally:
        await arreter()


# --- Un template téléversé ne fait rien d'autre que s'imprimer ---


def _png(largeur: int, hauteur: int) -> bytes:
    """Une image unie, aux dimensions reconnaissables dans le PDF."""
    lignes = b"".join(b"\x00" + b"\xc0\x40\x20" * largeur for _ in range(hauteur))

    def bloc(nature: bytes, donnees: bytes) -> bytes:
        return (struct.pack(">I", len(donnees)) + nature + donnees
                + struct.pack(">I", zlib.crc32(nature + donnees)))

    return (b"\x89PNG\r\n\x1a\n"
            + bloc(b"IHDR", struct.pack(">IIBBBBB", largeur, hauteur, 8, 2, 0, 0, 0))
            + bloc(b"IDAT", zlib.compress(lignes))
            + bloc(b"IEND", b""))


@pytest.fixture
def service_interne():
    """Un service HTTP sur la boucle locale, comme ceux que le conteneur joint : réseau
    Docker de Coolify, adresse de métadonnées de l'hébergeur. Il note chaque requête."""
    recues: list[str] = []

    class Service(BaseHTTPRequestHandler):
        def do_GET(self):
            recues.append(self.path)
            if self.path.startswith("/image"):
                corps, nature = _png(37, 23), "image/png"
            else:
                corps = b'<p>SECRET</p><a href="https://secret-interne.invalid/">lien</a>'
                nature = "text/html"
            self.send_response(200)
            self.send_header("Content-Type", nature)
            self.send_header("Content-Length", str(len(corps)))
            self.end_headers()
            self.wfile.write(corps)

        def log_message(self, *_):
            pass

    serveur = ThreadingHTTPServer(("127.0.0.1", 0), Service)  # port choisi par le système
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{serveur.server_address[1]}", recues
    serveur.shutdown()
    serveur.server_close()


async def test_un_template_ne_sort_ni_sur_le_reseau_ni_en_javascript(service_interne):
    """Le Chromium du serveur joint tout ce que le conteneur joint. Une iframe vers un
    service interne s'imprimait dans le PDF d'aperçu, renvoyé à qui avait téléversé le
    template ; et un script reconstitué par Jinja passait le nettoyage du source.

    Témoins, sur le code d'avant : le service reçoit toutes les requêtes, le lien du
    service et l'image de 37 × 23 sont dans le PDF, et le script y ajoute quatre pages.
    Le préchargement, lui, échappe à l'interception de Playwright : c'est le proxy
    sans issue posé au lancement de Chromium qui l'arrête."""
    base, recues = service_interne
    script = (
        "{{ ('<scr' ~ 'ipt>fetch(\"" + base + "/depuis-js\");"
        "document.body.insertAdjacentHTML(\"beforeend\", \"<a href=https://js-a-tourne.invalid/>js</a>\""
        " + \"<div style=break-before:page>page</div>\".repeat(4))</scr' ~ 'ipt>') | safe }}"
    )
    template = (
        f'<!doctype html><html><head><link rel="prefetch" href="{base}/prefetch"></head>'
        "<body><p>Devis {{ devis.numero }}</p>"
        f'<iframe src="{base}/meta-data" width="600" height="200"></iframe>'
        f'<img src="{base}/image.png" width="100" height="100">'
        f"{script}</body></html>"
    )
    propre, rendu, _ = templates.valider(template)
    assert "<script" not in propre and "<script" in rendu, "le témoin n'exerce pas le cas"

    try:
        # Le rendu se termine : rien n'attend une réponse qui ne viendra pas.
        contenu = await asyncio.wait_for(render(templates.devis_exemple(), propre), 30)
        # Le préchargement part après coup : on lui laisse le temps d'arriver.
        await asyncio.sleep(1)
    finally:
        await arreter()

    assert recues == []
    assert b"secret-interne" not in contenu
    assert b"/Width 37" not in contenu
    assert b"js-a-tourne" not in contenu
    assert re.search(rb"/Count (\d+)", contenu).group(1) == b"1"


async def test_un_rendu_qui_ne_finit_pas_est_coupe_et_rend_sa_place(monkeypatch):
    """Sans délai, une page qui ne finissait pas gardait sa place jusqu'au redémarrage :
    avec deux places, deux requêtes suffisaient pour que plus personne n'ait de PDF.

    Ici une seule place, et une impression qui n'aboutit jamais."""
    monkeypatch.setenv("RENDUS_SIMULTANES", "1")
    get_config.cache_clear()
    monkeypatch.setattr(pdf_module, "DELAI_RENDU_S", 1)
    await arreter()  # un sémaphore neuf, créé avec cette configuration

    async def interminable(self, **_options):
        await asyncio.Event().wait()

    devis = devis_de("peinture")
    try:
        with monkeypatch.context() as m:
            m.setattr(Page, "pdf", interminable)
            debut = time.monotonic()
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(render(devis), 20)
            assert time.monotonic() - debut < 10, "coupé par le filet du test, pas par le délai"

        assert not pdf_module._places.locked()
        assert pdf_module._navigateur.contexts == [], "la page n'a pas été refermée"
        # La place est revenue : avec une seule, ce rendu attendrait sinon pour toujours.
        contenu = await asyncio.wait_for(render(devis), 30)
        assert contenu[:4] == b"%PDF"
    finally:
        await arreter()


# --- Pagination : fonction pure, aucun navigateur ---


def _lignes(n: int, designation: str = "Prestation {i}", detail: str | None = None) -> list[LigneDevis]:
    from decimal import Decimal

    return [
        LigneDevis(
            designation=designation.format(i=i),
            detail=detail,
            quantite=Decimal("1"),
            unite=Unite.U,
            prix_unitaire_ht=Decimal("100"),
            total_ht=Decimal("100"),
        )
        for i in range(n)
    ]


def _devis(lignes: list[LigneDevis], notes=(), *, professionnel: bool = False):
    """Le devis peinture, ses lignes et ses observations remplacées : `paginer` lit aussi
    l'en-tête, le client et le bon pour accord, qui comptent dans la hauteur."""
    devis = devis_de("peinture").model_copy(deep=True)
    devis.lignes = lignes
    devis.notes = list(notes)
    devis.client.professionnel = professionnel
    return devis


@pytest.mark.parametrize("professionnel", [False, True])
@pytest.mark.parametrize(
    "nb, tranches, cloture_sous_le_tableau, total_pages",
    [
        (0, [0], True, 2),    # devis vide : le template ne doit pas casser pour autant
        (4, [4], True, 2),
        (7, [7], True, 2),    # la page 1 est pleine, la clôture y tient encore
        (8, [7, 1], True, 3),
        (12, [7, 5], True, 3),  # les douze prestations du design livré, sur ses trois pages
        (13, [7, 6], False, 4),
        (25, [7, 14, 4], True, 4),
    ],
)
def test_la_pagination(nb, tranches, cloture_sous_le_tableau, total_pages, professionnel):
    pagination = paginer(_devis(_lignes(nb), professionnel=professionnel))
    assert [len(p) for p in pagination.pages] == tranches
    assert pagination.cloture_sur_derniere is cloture_sous_le_tableau
    assert pagination.total_pages == total_pages
    assert pagination.pages_observations == []


def test_des_observations_volumineuses_chassent_la_cloture_de_la_page_1():
    """Deux devis de quatre lignes n'occupent pas la même place : c'est la hauteur qui
    décide, pas le nombre de lignes. Sans ça, la clôture déborderait sur une page
    fantôme — une page blanche de plus dans le PDF que dans le HTML."""
    longue = "Le support n'a pas été précisé, un doublage hydrofuge peut être nécessaire. " * 3
    peu = paginer(_devis(_lignes(4), ["Une remarque courte."]))
    beaucoup = paginer(_devis(_lignes(4), [longue] * 5))
    assert (peu.cloture_sur_derniere, peu.total_pages) == (True, 2)
    assert (beaucoup.cloture_sur_derniere, beaucoup.total_pages) == (False, 3)


def test_la_pastille_estime_pousse_la_designation_au_repli():
    """« estimé † » est en ligne dans la désignation : il la fait se replier plus tôt,
    et donc grandir. Une ligne estimée est plus haute qu'une ligne dictée."""
    from decimal import Decimal

    def ligne(a_valider):
        return LigneDevis(
            designation="Fourniture et pose de plaques de plâtre hydrofuges BA13",
            quantite=Decimal("1"), unite=Unite.U, a_valider=a_valider,
            prix_unitaire_ht=Decimal("100"), total_ht=Decimal("100"),
        )

    assert _hauteur_lignes([ligne(True)]) > _hauteur_lignes([ligne(False)])


def test_une_precision_longue_compte_toutes_ses_lignes():
    """Une précision de 150 caractères s'imprime sur trois lignes, pas sur une."""
    courte = _lignes(1, detail="Fourniture client")
    longue = _lignes(1, detail=PRECISION[:150])
    assert _hauteur_lignes(longue) - _hauteur_lignes(courte) == 30


def test_les_lignes_sont_numerotees_en_continu_d_une_page_a_l_autre():
    pages = paginer(_devis(_lignes(12))).pages
    numeros = [numero for page in pages for numero, _ in page]
    assert numeros == list(range(1, 13))


def test_sept_lignes_hautes_ne_tiennent_pas_sur_la_page_1():
    """Le plafond de sept lignes ne suffit pas : sept désignations de trois lignes avec
    leur précision débordaient de la page 1."""
    pagination = paginer(_devis(_lignes(7, DESIGNATION[:180], PRECISION[:80])))
    assert len(pagination.pages[0]) < 7
    assert sum(len(page) for page in pagination.pages) == 7


def test_une_page_de_suite_se_coupe_aussi_en_hauteur():
    pagination = paginer(_devis(_lignes(21, DESIGNATION[:70] + " {i}", "Fourniture client")))
    assert all(len(page) < 14 for page in pagination.pages[1:])


def test_des_observations_trop_longues_pour_la_cloture_ont_leurs_pages():
    """Quinze observations de 250 caractères ne tiennent même pas sur une page de clôture :
    elles partent sur des pages à elles, avant la clôture, qui ne garde que les totaux."""
    notes = [OBSERVATION[:250]] * 15
    pagination = paginer(_devis(_lignes(1), notes))

    assert not pagination.cloture_sur_derniere
    assert pagination.pages_observations
    assert [n for page in pagination.pages_observations for n in page] == notes
    assert pagination.notes_cloture == []
    assert pagination.total_pages == 1 + len(pagination.pages_observations) + 1 + 1


# --- Formatage français : c'est ce que le client lit sur le devis ---


def test_les_montants_sont_formates_a_la_francaise():
    assert f_euro(1234.5) == "1\u202f234,50\u202f€"
    assert f_euro(0) == "0,00\u202f€"
    assert f_euro(1234567.89) == "1\u202f234\u202f567,89\u202f€"


def test_le_tableau_du_devis_n_affiche_pas_le_symbole():
    assert f_montant(1234.5) == "1\u202f234,50"


def test_les_quantites_n_affichent_pas_de_decimales_inutiles():
    assert f_nombre(15) == "15"
    assert f_nombre(0.5) == "0,5"
    assert f_nombre(12.50) == "12,5"


def test_les_pourcentages():
    assert f_pourcent(0.30) == "30"
    assert f_pourcent(0.10) == "10"


def test_les_dates_s_ecrivent_en_toutes_lettres_sur_le_devis():
    from datetime import date

    assert f_date_longue(date(2026, 8, 27)) == "27 août 2026"
    assert f_date_longue(date(2026, 12, 1)) == "1er décembre 2026"
    assert f_date_longue(date(2027, 1, 11)) == "11 janvier 2027"


def test_la_ligne_de_signature_n_imprime_pas_l_annee_d_emission():
    """Émis le 15 décembre, valable jusqu'au 14 janvier : signé le 5 janvier, une année
    pré-imprimée daterait l'acceptation d'avant l'émission."""
    from datetime import datetime

    extraction = DevisExtraction.model_validate_json((FIXTURES / "elec.json").read_text(encoding="utf-8"))
    devis = to_devis(extraction, entreprise=ENTREPRISE, maintenant=datetime(2026, 12, 15, 10, 0))
    texte = _texte_imprime(devis)

    assert "le ……… / ……… / ………" in texte
    assert "/ 2026" not in texte
