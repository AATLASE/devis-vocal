"""Devis -> PDF, via Chromium headless piloté par Playwright.

Interface unique : `render(devis) -> bytes`.

Pourquoi un navigateur plutôt qu'un moteur de rendu Python : aucune dépendance système à
installer (ni GTK, ni Pango), le même setup sur Windows, Mac et Linux, et le CSS complet de
Chromium. Ce qu'on voit dans l'onglet est exactement ce qui sort en PDF.

Le navigateur est lancé une seule fois au démarrage de l'application et réutilisé : le
démarrer à chaque rendu coûterait environ une seconde par devis.

Ce module porte aussi la **pagination** du document. Elle est décidée ici, en Python, et
pas laissée au moteur CSS : la page 1 porte l'en-tête d'entreprise, les suivantes un titre
courant, et les mentions ont toujours une page à elles. Aucune règle de saut de page ne sait
faire cette distinction — et `paginer()` étant une fonction pure, elle se teste sans
Chromium, comme le reste de ce qui doit être juste à tous les coups.
"""

from __future__ import annotations

import base64
import re
from datetime import date
from decimal import Decimal
import asyncio
from collections.abc import Sequence
from functools import lru_cache
from math import ceil
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateError, select_autoescape
from jinja2.sandbox import SandboxedEnvironment
from markupsafe import Markup
from playwright.async_api import Browser, async_playwright

from app.config import RACINE, get_config
from app.models import Devis, LigneDevis

TEMPLATES = RACINE / "templates"
STATIQUE = RACINE / "app" / "static"
TAUX_REDUIT = Decimal("0.10")

# Espace fine insécable (U+202F). C'est celle que demande le design, et la bonne en
# typographie française : ni coupure entre les milliers, ni avant le symbole €, et un
# blanc plus étroit qu'une espace mot. Écrite en échappement pour qu'aucun éditeur ne
# la confonde avec l'insécable ordinaire (U+00A0), qui est visiblement plus large.
INSECABLE = "\u202f"

MOIS = (
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
)


# ---------------------------------------------------------------------------
# Pagination — fonction pure, testable sans navigateur
# ---------------------------------------------------------------------------

# Le découpage du tableau se fait au nombre de lignes : l'en-tête d'entreprise de la
# page 1 laisse la place à sept, une page de suite en tient quatorze.
LIGNES_PAGE_1 = 7
LIGNES_PAGE_SUITE = 14
LIGNES_MAX_AVEC_CLOTURE = 5     # sur une page de suite, où seul un titre courant précède

# Savoir si la clôture — note †, observations, totaux, bon pour accord — tient sous le
# tableau de la page 1 demande en revanche de raisonner en hauteur : deux devis de
# quatre lignes n'occupent pas la même place selon la longueur des désignations et des
# observations. Toutes les valeurs ci-dessous sont **mesurées** dans le navigateur sur
# le gabarit réel, jamais estimées à vue. Le jeu reproduit au pixel près la hauteur du
# tableau des quatre fixtures.
HAUTEUR_LIGNE = 38                      # une ligne de tableau sur une ligne de texte
HAUTEUR_LIGNE_REPLI = 16                # chaque repli supplémentaire de la désignation
HAUTEUR_DETAIL = 17                     # la précision sous la désignation
CARACTERES_PAR_DESIGNATION = 61
CARACTERES_PASTILLE_ESTIME = 8          # « estimé † » est en ligne : il pousse au repli

HAUTEUR_OBSERVATION_LIGNE = 17
ESPACE_ENTRE_OBSERVATIONS = 8
HAUTEUR_LIBELLE_OBSERVATIONS = 20       # le libellé « OBSERVATIONS » et sa marge
CARACTERES_PAR_OBSERVATION = 62

HAUTEUR_TOTAUX = 146                    # le cadre des totaux, hauteur fixe
HAUTEUR_NOTE_DAGUE = 25                 # la note « † Prix non dicté… », marge comprise

# Ce que la page 1 peut porter quand la clôture l'accompagne. Frontière relevée sur
# 72 rendus réels balayant 2 à 7 lignes et 0 à 5 observations de deux longueurs : le
# dernier cas qui tient mesure 450 px, le premier qui déborde 452. On se pose à 445.
BUDGET_PAGE_1_AVEC_CLOTURE = 445


def _hauteur_lignes(lignes: list[LigneDevis]) -> int:
    """Hauteur du tableau des prestations, replis et précisions compris."""
    total = 0
    for ligne in lignes:
        largeur = len(ligne.designation) + (CARACTERES_PASTILLE_ESTIME if ligne.a_valider else 0)
        replis = max(1, ceil(largeur / CARACTERES_PAR_DESIGNATION))
        total += HAUTEUR_LIGNE + HAUTEUR_LIGNE_REPLI * (replis - 1)
        if ligne.detail:
            total += HAUTEUR_DETAIL
    return total


def _hauteur_observations(notes: Sequence[str]) -> int:
    if not notes:
        return 0
    total = HAUTEUR_LIBELLE_OBSERVATIONS + ESPACE_ENTRE_OBSERVATIONS * (len(notes) - 1)
    for note in notes:
        replis = max(1, ceil(len(note) / CARACTERES_PAR_OBSERVATION))
        total += HAUTEUR_OBSERVATION_LIGNE * replis
    return total


def _hauteur_cloture(lignes: list[LigneDevis], notes: Sequence[str]) -> int:
    """Hauteur de la clôture. Les observations et les totaux sont côte à côte dans une
    grille : c'est le plus haut des deux qui commande, pas leur somme."""
    note_dague = HAUTEUR_NOTE_DAGUE if any(ligne.a_valider for ligne in lignes) else 0
    return note_dague + max(_hauteur_observations(notes), HAUTEUR_TOTAUX)


def paginer(
    lignes: list[LigneDevis], notes: Sequence[str] = ()
) -> tuple[list[list[tuple[int, LigneDevis]]], bool, int]:
    """Répartit les prestations sur les pages du devis.

    Renvoie `(pages, cloture_sur_derniere, total_pages)` :

    - `pages` : les lignes numérotées, découpées page par page ;
    - `cloture_sur_derniere` : la clôture tient sous la dernière tranche du tableau ;
    - `total_pages` : tableau + clôture éventuelle + la page des mentions.

    Avec les douze prestations du design livré, on retombe exactement sur ses trois
    pages : 7 + 5 avec la clôture, puis les mentions. Un devis court — quatre lignes,
    une observation — tient sur deux : le tableau et sa clôture, puis les mentions.
    """
    numerotees = list(enumerate(lignes, start=1))

    pages = [numerotees[:LIGNES_PAGE_1]]
    reste = numerotees[LIGNES_PAGE_1:]
    while reste:
        pages.append(reste[:LIGNES_PAGE_SUITE])
        reste = reste[LIGNES_PAGE_SUITE:]

    if len(pages) == 1:
        # Tout le tableau tient sur la page 1. Reste à savoir si la clôture y tient
        # aussi : c'est là que l'en-tête d'entreprise pèse. Une page presque vide n'est
        # pas une faute d'impression, mais sur un devis de quatre lignes elle se voit.
        occupe = _hauteur_lignes(lignes) + _hauteur_cloture(lignes, notes)
        cloture_sur_derniere = occupe <= BUDGET_PAGE_1_AVEC_CLOTURE
    else:
        # Sur une page de suite, seul un titre courant précède le tableau : le nombre
        # de lignes suffit à décider.
        cloture_sur_derniere = len(pages[-1]) <= LIGNES_MAX_AVEC_CLOTURE

    total_pages = len(pages) + (0 if cloture_sur_derniere else 1) + 1
    return pages, cloture_sur_derniere, total_pages


# ---------------------------------------------------------------------------
# Filtres de formatage français
# ---------------------------------------------------------------------------


def f_montant(valeur: Decimal | float) -> str:
    """1234.5 -> « 1 234,50 » — le tableau du devis n'affiche pas le symbole."""
    montant = Decimal(str(valeur)).quantize(Decimal("0.01"))
    entier, _, decimales = f"{abs(montant):.2f}".partition(".")
    groupes = []
    while len(entier) > 3:
        groupes.insert(0, entier[-3:])
        entier = entier[:-3]
    groupes.insert(0, entier)
    signe = "-" if montant < 0 else ""
    return f"{signe}{INSECABLE.join(groupes)},{decimales}"


def f_euro(valeur: Decimal | float) -> str:
    """1234.5 -> « 1 234,50 € »"""
    return f"{f_montant(valeur)}{INSECABLE}€"


def f_nombre(valeur: Decimal | float) -> str:
    """15 -> « 15 », 0.5 -> « 0,5 » — on n'affiche pas de décimales inutiles sur une quantité."""
    nombre = Decimal(str(valeur)).normalize()
    if nombre == nombre.to_integral_value():
        return f"{nombre.to_integral_value():.0f}"
    return f"{nombre}".replace(".", ",")


def f_pourcent(valeur: Decimal | float) -> str:
    """0.30 -> « 30 »"""
    return f_nombre(Decimal(str(valeur)) * 100)


def f_date_fr(valeur: date) -> str:
    return valeur.strftime("%d/%m/%Y")


def f_date_longue(valeur: date) -> str:
    """2026-08-27 -> « 27 août 2026 ». C'est la forme du design, et celle d'un vrai devis."""
    return f"{valeur.day} {MOIS[valeur.month - 1]} {valeur.year}"


# ---------------------------------------------------------------------------
# Feuilles de style et polices, inlinées dans le document
# ---------------------------------------------------------------------------


@lru_cache
def _polices_inline() -> str:
    """`app/static/fonts.css`, ses fichiers woff2 remplacés par des URI base64.

    Chromium rend le document depuis `about:blank` : aucune URL relative n'y résoudrait.
    On pourrait pointer vers le CDN Google, mais alors le PDF dépendrait du réseau au
    moment précis où on le montre à un artisan — et sans les polices, le devis retombe
    sur Georgia. Les octets voyagent donc avec le document.
    """
    css = (STATIQUE / "fonts.css").read_text(encoding="utf-8")

    def en_base64(m: re.Match[str]) -> str:
        octets = (STATIQUE / "fonts" / m.group(1)).read_bytes()
        return f"url(data:font/woff2;base64,{base64.b64encode(octets).decode('ascii')})"

    return re.sub(r"url\(/static/fonts/([\w.-]+)\)", en_base64, css)


@lru_cache
def _feuille_de_style(polices_inline: bool = True) -> Markup:
    """Tokens, polices et feuille A4, dans cet ordre : les variables d'abord.

    `polices_inline=False` remplace les 518 Ko de base64 par un `@import` vers
    `/static/fonts.css`. C'est pour l'aperçu affiché dans l'application, où les
    polices sont déjà chargées par la page : renvoyer un demi-mégaoctet à chaque
    devis serait le payer une seconde fois, sur la 4G d'une camionnette. Le PDF,
    lui, garde ses octets — il doit rester juste sans réseau.

    L'`@import` est en tête : la règle exige qu'aucune autre ne le précède.
    """
    polices = _polices_inline() if polices_inline else ""
    amont = "" if polices_inline else '@import url("/static/fonts.css");\n'
    return Markup(
        amont
        + (STATIQUE / "tokens.css").read_text(encoding="utf-8")
        + polices
        + (TEMPLATES / "pdf.css").read_text(encoding="utf-8")
    )


def _poser_les_filtres(env: Environment) -> Environment:
    """Les filtres de formatage français. Les mêmes des deux côtés : un gabarit
    d'artisan doit pouvoir écrire `{{ devis.total_ttc | euro }}` comme le nôtre."""
    env.filters["euro"] = f_euro
    env.filters["montant"] = f_montant
    env.filters["nombre"] = f_nombre
    env.filters["pourcent"] = f_pourcent
    env.filters["date_fr"] = f_date_fr
    env.filters["date_longue"] = f_date_longue
    return env


_env = _poser_les_filtres(
    Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
)

# Le gabarit téléversé par un artisan est du code que nous n'avons pas écrit et que
# nous exécutons sur notre serveur. Deux précautions, et elles ne sont pas de trop :
#
# - `SandboxedEnvironment` refuse l'accès aux attributs internes. Sans lui,
#   `{{ devis.__class__.__init__.__globals__ }}` ouvre l'interpréteur — donc la
#   configuration, donc les clés API.
# - `loader=None` fait échouer `{% include %}` et `{% extends %}` : aucun gabarit ne
#   lira un fichier du serveur.
#
# Ce qui reste possible, et qu'on assume : un gabarit peut écrire n'importe quoi dans
# le document, y compris un devis non conforme. C'est le sujet de `mentions_manquantes`.
_env_gabarit = _poser_les_filtres(
    SandboxedEnvironment(loader=None, autoescape=select_autoescape(["html"]))
)


class GabaritError(Exception):
    """Le gabarit ne compile pas, ou explose au rendu."""


def contexte(devis: Devis, *, polices_inline: bool = True) -> dict:
    """Tout ce qu'un gabarit peut lire. Documenté par `docs/gabarits.md` : c'est le
    contrat offert à l'artisan qui écrit sa propre mise en page."""
    pages, cloture_sur_derniere, total_pages = paginer(devis.lignes, devis.notes)
    return {
        "devis": devis,
        "e": devis.entreprise,
        "taux_reduit": TAUX_REDUIT,
        "pages": pages,
        "cloture_sur_derniere": cloture_sur_derniere,
        "total_pages": total_pages,
        "css": _feuille_de_style(polices_inline),
    }


def render_html(devis: Devis, gabarit: str | None = None, *, polices_inline: bool = True) -> str:
    """Le devis en HTML. Exposé à part : c'est ce qu'on ouvre dans un onglet pour
    travailler la mise en page sans regénérer un PDF à chaque itération, et c'est ce
    que l'application affiche dans son aperçu A4.

    `gabarit` remplace `templates/devis.html` par le HTML d'un artisan. Il est rendu
    dans le bac à sable, avec exactement le même contexte — un gabarit tiers a accès
    au devis, aux filtres et à la feuille de style, à rien d'autre.

    `polices_inline=False` allège le document de ses polices en base64 ; voir
    `_feuille_de_style`. À n'utiliser que pour un rendu affiché dans l'application,
    jamais pour un document qui doit se suffire à lui-même.
    """
    ctx = contexte(devis, polices_inline=polices_inline)
    if gabarit is None:
        return _env.get_template("devis.html").render(**ctx)
    try:
        return _env_gabarit.from_string(gabarit).render(**ctx)
    except TemplateError as err:
        raise GabaritError(f"{type(err).__name__} : {err}") from err


# ---------------------------------------------------------------------------
# Cycle de vie du navigateur
# ---------------------------------------------------------------------------

_playwright = None
_navigateur: Browser | None = None

# Chaque page Chromium coûte de la mémoire, et c'est elle qui manque en premier sur un
# petit serveur — bien avant le processeur. Sans ce verrou, dix requêtes simultanées
# ouvrent dix pages et la machine se met à ramer pour tout le monde, y compris pour
# l'artisan qui attend son PDF. Les requêtes en trop patientent au lieu de s'écrouler.
_places: asyncio.Semaphore | None = None


async def demarrer() -> None:
    """Lance Chromium. Appelé une fois au démarrage de l'application."""
    global _playwright, _navigateur
    if _navigateur is not None:
        return
    _playwright = await async_playwright().start()
    _navigateur = await _playwright.chromium.launch(
        # Dans un conteneur, /dev/shm fait 64 Mo par défaut : Chromium le sature en
        # plein rendu et meurt sans rien dire. L'option le fait écrire sur le disque —
        # un peu plus lent, mais le PDF sort. Sans effet hors conteneur.
        args=["--disable-dev-shm-usage"],
    )


async def arreter() -> None:
    global _playwright, _navigateur, _places
    _places = None
    if _navigateur is not None:
        await _navigateur.close()
        _navigateur = None
    if _playwright is not None:
        await _playwright.stop()
        _playwright = None


async def render(devis: Devis, gabarit: str | None = None) -> bytes:
    """Produit le PDF du devis. `gabarit` : le HTML de l'artisan, sinon celui livré."""
    global _places
    await demarrer()  # filet : permet d'appeler render() depuis un test, hors application
    assert _navigateur is not None

    # Créé ici et pas au chargement du module : un sémaphore se rattache à la boucle
    # asyncio qui tourne, et il n'y en a aucune à l'import.
    if _places is None:
        _places = asyncio.Semaphore(get_config().rendus_simultanes)

    async with _places:
        return await _rendre(devis, gabarit)


async def _rendre(devis: Devis, gabarit: str | None) -> bytes:
    assert _navigateur is not None
    page = await _navigateur.new_page()
    try:
        await page.set_content(render_html(devis, gabarit), wait_until="load")
        # `load` ne dit rien des polices. Sans cette attente, Chromium imprime par
        # intermittence en police de repli — le document est alors juste, mais ce n'est
        # plus le design.
        await page.evaluate("document.fonts.ready")
        return await page.pdf(
            # `@page { size: A4; margin: 0 }` et les pieds de page du gabarit portent
            # désormais les marges et la pagination : le navigateur n'ajoute rien.
            prefer_css_page_size=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            # Sans ça, Chromium n'imprime ni les aplats ni les fonds de tableau : le TTC
            # inversé et la teinte des lignes estimées disparaîtraient.
            print_background=True,
        )
    finally:
        await page.close()
