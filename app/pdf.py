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
from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from playwright.async_api import Browser, async_playwright

from app.config import RACINE
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

# Ces trois nombres sont mesurés sur le gabarit, pas devinés : l'en-tête d'entreprise
# de la page 1 mange la hauteur de sept lignes, une page de suite en tient quatorze, et
# la clôture (note †, observations, totaux, bon pour accord) occupe l'équivalent de neuf.
# Les changer se vérifie à l'œil : une page blanche de plus dans le PDF que dans le HTML
# signifie que le contenu déborde.
LIGNES_PAGE_1 = 7
LIGNES_PAGE_SUITE = 14
LIGNES_MAX_AVEC_CLOTURE = 5


def paginer(lignes: list[LigneDevis]) -> tuple[list[list[tuple[int, LigneDevis]]], bool, int]:
    """Répartit les prestations sur les pages du devis.

    Renvoie `(pages, cloture_sur_derniere, total_pages)` :

    - `pages` : les lignes numérotées, découpées page par page ;
    - `cloture_sur_derniere` : la clôture tient sous la dernière tranche du tableau ;
    - `total_pages` : tableau + clôture éventuelle + la page des mentions.

    Avec les douze prestations du design livré, on retombe exactement sur ses trois
    pages : 7 + 5 avec la clôture, puis les mentions.
    """
    numerotees = list(enumerate(lignes, start=1))

    pages = [numerotees[:LIGNES_PAGE_1]]
    reste = numerotees[LIGNES_PAGE_1:]
    while reste:
        pages.append(reste[:LIGNES_PAGE_SUITE])
        reste = reste[LIGNES_PAGE_SUITE:]

    # La clôture ne descend jamais sous la page 1 : l'en-tête d'entreprise et le bloc
    # client y prennent déjà le tiers de la hauteur. Mesure faite, elle n'y tiendrait
    # qu'avec trois ou quatre lignes ET peu d'observations — un seuil qui dépend de la
    # longueur des observations, donc que Python ne peut pas prédire honnêtement. On
    # préfère une règle qui ne déborde jamais : sur une page de suite assez courte,
    # sinon sur une page à elle.
    cloture_sur_derniere = len(pages) > 1 and len(pages[-1]) <= LIGNES_MAX_AVEC_CLOTURE

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
def _feuille_de_style() -> Markup:
    """Tokens, polices et feuille A4, dans cet ordre : les variables d'abord."""
    return Markup(
        (STATIQUE / "tokens.css").read_text(encoding="utf-8")
        + _polices_inline()
        + (TEMPLATES / "pdf.css").read_text(encoding="utf-8")
    )


def _environnement() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["euro"] = f_euro
    env.filters["montant"] = f_montant
    env.filters["nombre"] = f_nombre
    env.filters["pourcent"] = f_pourcent
    env.filters["date_fr"] = f_date_fr
    env.filters["date_longue"] = f_date_longue
    return env


_env = _environnement()


def render_html(devis: Devis) -> str:
    """Le devis en HTML. Exposé à part : c'est ce qu'on ouvre dans un onglet pour
    travailler la mise en page sans regénérer un PDF à chaque itération."""
    pages, cloture_sur_derniere, total_pages = paginer(devis.lignes)
    return _env.get_template("devis.html").render(
        devis=devis,
        e=devis.entreprise,
        taux_reduit=TAUX_REDUIT,
        pages=pages,
        cloture_sur_derniere=cloture_sur_derniere,
        total_pages=total_pages,
        css=_feuille_de_style(),
    )


# ---------------------------------------------------------------------------
# Cycle de vie du navigateur
# ---------------------------------------------------------------------------

_playwright = None
_navigateur: Browser | None = None


async def demarrer() -> None:
    """Lance Chromium. Appelé une fois au démarrage de l'application."""
    global _playwright, _navigateur
    if _navigateur is not None:
        return
    _playwright = await async_playwright().start()
    _navigateur = await _playwright.chromium.launch()


async def arreter() -> None:
    global _playwright, _navigateur
    if _navigateur is not None:
        await _navigateur.close()
        _navigateur = None
    if _playwright is not None:
        await _playwright.stop()
        _playwright = None


async def render(devis: Devis) -> bytes:
    """Produit le PDF du devis."""
    await demarrer()  # filet : permet d'appeler render() depuis un test, hors application
    assert _navigateur is not None

    page = await _navigateur.new_page()
    try:
        await page.set_content(render_html(devis), wait_until="load")
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
