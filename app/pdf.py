"""Devis -> PDF, via Chromium headless piloté par Playwright.

Interface unique : `render(devis) -> bytes`.

Pourquoi un navigateur plutôt qu'un moteur de rendu Python : aucune dépendance système à
installer (ni GTK, ni Pango), le même setup sur Windows, Mac et Linux, et le CSS complet de
Chromium. Ce qu'on voit dans l'onglet est exactement ce qui sort en PDF.

Le navigateur est lancé une seule fois au démarrage de l'application et réutilisé : le
démarrer à chaque rendu coûterait environ une seconde par devis.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from jinja2 import Environment, FileSystemLoader, select_autoescape
from playwright.async_api import Browser, async_playwright

from app.config import RACINE
from app.models import Devis

TEMPLATES = RACINE / "templates"
TAUX_REDUIT = Decimal("0.10")

# Espace insécable : en typographie française, il ne doit jamais y avoir de coupure
# entre les milliers, ni avant le symbole €.
INSECABLE = " "


# ---------------------------------------------------------------------------
# Filtres de formatage français
# ---------------------------------------------------------------------------


def f_euro(valeur: Decimal | float) -> str:
    """1234.5 -> « 1 234,50 € »"""
    montant = Decimal(str(valeur)).quantize(Decimal("0.01"))
    entier, _, decimales = f"{abs(montant):.2f}".partition(".")
    groupes = []
    while len(entier) > 3:
        groupes.insert(0, entier[-3:])
        entier = entier[:-3]
    groupes.insert(0, entier)
    signe = "-" if montant < 0 else ""
    return f"{signe}{INSECABLE.join(groupes)},{decimales}{INSECABLE}€"


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


def _environnement() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
    )
    env.filters["euro"] = f_euro
    env.filters["nombre"] = f_nombre
    env.filters["pourcent"] = f_pourcent
    env.filters["date_fr"] = f_date_fr
    return env


_env = _environnement()


def render_html(devis: Devis) -> str:
    """Le devis en HTML. Exposé à part : c'est ce qu'on ouvre dans un onglet pour
    travailler la mise en page sans regénérer un PDF à chaque itération."""
    return _env.get_template("devis.html").render(
        devis=devis,
        e=devis.entreprise,
        taux_reduit=TAUX_REDUIT,
    )


# ---------------------------------------------------------------------------
# Cycle de vie du navigateur
# ---------------------------------------------------------------------------

_playwright = None
_navigateur: Browser | None = None

PIED_DE_PAGE = """
<div style="width:100%;font-size:7pt;color:#949cab;text-align:center;
            font-family:Arial,sans-serif;padding:0 14mm;">
  Page <span class="pageNumber"></span> / <span class="totalPages"></span>
</div>
"""


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
        return await page.pdf(
            format="A4",
            # Sans ça, Chromium n'imprime ni les aplats ni les fonds de tableau :
            # le devis sortirait délavé.
            print_background=True,
            display_header_footer=True,
            header_template="<div></div>",
            footer_template=PIED_DE_PAGE,
            margin={"top": "12mm", "bottom": "16mm", "left": "14mm", "right": "14mm"},
        )
    finally:
        await page.close()
