"""Lire les prix de l'artisan dans une matière brute : une dictée, un tableau Excel ou CSV,
le PDF d'un ancien devis, la photo d'un devis papier.

Hors du périmètre d'origine, comme le tarif lui-même (CLAUDE.md, « Exception assumée :
tarif de l'artisan »). Ce module **propose** des lignes ; il n'enregistre rien. L'artisan
les relit à l'écran, les corrige, et seul « Enregistrer mon tarif » les écrit — par
`grilles.enregistrer`, avec la même validation que pour une saisie à la main.

Ce que la lecture garantit, en plus de ce que le modèle promet :

- **Le modèle ne calcule pas.** Un prix lu TTC reste tel quel, signalé : c'est l'artisan
  qui le ramène en HT. Aucune conversion ici, aucune dans le prompt.
- **Chaque prix lu est confronté à la source.** S'il n'apparaît pas parmi les nombres du
  texte d'origine, la ligne porte « prix non retrouvé » : le modèle a pu mal lire, ou
  calculer malgré la consigne. On lit large (`structuration.nombres_dits`) : un nombre
  en trop fait rater un signalement, jamais n'en invente un. Un PDF numérique a un
  texte, extrait par pypdf ; une photo ou un scan n'en a pas, et chaque ligne le dit :
  « lu sur une image, relisez ce prix sur le document ».
- **Rien du client ne sort d'ici** : ni journal, ni log du contenu. Un ancien devis porte
  un nom et une adresse ; ils partent chez le fournisseur du modèle, le temps de la
  lecture, et nulle part ailleurs.
"""

from __future__ import annotations

import io
import logging
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from app.config import get_config
from app.grilles import cle
from app.models import GrillePrix, Unite, arrondi
from app.structuration import Piece, StructurationError, generer, nombres_dits

logger = logging.getLogger("devis-vocal")

PROMPT_PATH = Path(__file__).parent / "prompts" / "grille.md"

MAX_LIGNES_LUES = 150
MAX_LIGNES_TABLEAU = 2000
# Ce qu'on envoie au modèle, au plus : environ 25 000 tokens. Au-delà, ce n'est plus un
# tarif, c'est un export complet de logiciel — il vaut mieux le dire que le payer.
MAX_CARACTERES = 100_000
# Un ancien devis fait deux ou trois pages ; vingt suffisent à un catalogue de prix. Le
# fournisseur facture à la page, et chaque page allonge la lecture.
MAX_PAGES = 20
# Les photos sont réduites par le navigateur (2 000 px, JPEG) avant l'envoi ; celles
# qui arrivent plus lourdes viennent d'ailleurs. Claude refuse une image de plus de
# 5 Mo une fois encodée en base64 : 3,5 Mo bruts laissent la marge.
MAX_IMAGE_OCTETS = 3_500_000

CONSIGNE_DOCUMENT = (
    "Voici un document de l'artisan — un ancien devis, une liste de prix ou une photo. "
    "Relève ses prix unitaires, en suivant tes consignes."
)


class LectureRefusee(Exception):
    """Le fichier n'est pas lisible, ou la lecture n'a pas abouti. Lu par l'artisan."""


# ---------------------------------------------------------------------------
# Ce que le modèle rend, et ce qu'on propose à l'artisan
# ---------------------------------------------------------------------------


class PrixLu(BaseModel):
    """Un prix tel que le modèle l'a lu. `prix`, pas `prix_unitaire_ht` : il peut être TTC."""

    prestation: str
    unite: Unite | None
    prix: float
    precision: str | None
    base: Literal["HT", "TTC", "inconnue"]


class GrilleLue(BaseModel):
    lignes: list[PrixLu]
    remarque: str | None


class Drapeau(BaseModel):
    code: str   # non_retrouve · non_verifiable · ttc · unite · doublon · deja · deja_identique
    texte: str  # ce que lit l'artisan, sous la ligne


class PrixPropose(BaseModel):
    """Une ligne proposée au tableau de l'artisan, avec ce qu'il doit regarder."""

    prestation: str
    unite: Unite | None
    prix_unitaire_ht: Decimal  # le prix lu ; TTC si un drapeau « ttc » le dit
    precision: str | None
    drapeaux: list[Drapeau]


class Lecture(BaseModel):
    lignes: list[PrixPropose]
    remarque: str | None


@lru_cache
def _prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Du fichier au texte
# ---------------------------------------------------------------------------


def _cellule(valeur) -> str:
    """Une cellule de tableur en texte, sans « 45.0 » ni notation scientifique."""
    if valeur is None:
        return ""
    if isinstance(valeur, bool):
        return "oui" if valeur else "non"
    if isinstance(valeur, float):
        if valeur.is_integer():
            return str(int(valeur))
        return format(Decimal(str(valeur)).normalize(), "f")
    if hasattr(valeur, "isoformat"):
        return valeur.isoformat()
    return str(valeur).replace("\t", " ").replace("\n", " ").strip()


def _texte_xlsx(contenu: bytes) -> str:
    from openpyxl import load_workbook

    try:
        classeur = load_workbook(io.BytesIO(contenu), read_only=True, data_only=True)
    except Exception as err:  # noqa: BLE001 — zip abîmé, classeur protégé, autre format
        raise LectureRefusee(
            "Ce fichier Excel n'a pas pu être ouvert. Enregistrez-le à nouveau en .xlsx, "
            "ou exportez-le en .csv."
        ) from err

    morceaux: list[str] = []
    lignes = 0
    try:
        for feuille in classeur.worksheets:
            rangs = []
            for rang in feuille.iter_rows(values_only=True):
                cellules = [_cellule(v) for v in rang]
                if any(cellules):
                    rangs.append("\t".join(cellules).rstrip("\t"))
                    lignes += 1
                if lignes >= MAX_LIGNES_TABLEAU:
                    break
            if rangs:
                morceaux.append(f"## Feuille « {feuille.title} »\n" + "\n".join(rangs))
            if lignes >= MAX_LIGNES_TABLEAU:
                break
    finally:
        classeur.close()
    return "\n\n".join(morceaux)


def _texte_csv(contenu: bytes) -> str:
    # UTF-8 d'abord (avec ou sans BOM, comme l'écrit Excel), puis Windows-1252 : c'est
    # ce que produisent encore beaucoup de logiciels de devis français.
    for codage in ("utf-8-sig", "cp1252"):
        try:
            texte = contenu.decode(codage)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise LectureRefusee("Ce fichier texte n'a pas pu être décodé. Exportez-le en UTF-8.")

    if "\x00" in texte:
        raise LectureRefusee("Ce fichier n'est pas un tableau lisible : utilisez .xlsx ou .csv.")
    # Le séparateur n'est pas analysé : « ; », « , » ou tabulation, le modèle lit les
    # trois. On ne garde que les premières lignes non vides.
    lignes = [l for l in texte.splitlines() if l.strip(" ;,\t")]
    return "\n".join(lignes[:MAX_LIGNES_TABLEAU])


def texte_du_fichier(nom: str, contenu: bytes) -> str:
    """Le texte d'un fichier de prix, reconnu à ses premiers octets plutôt qu'à son nom."""
    if not contenu:
        raise LectureRefusee("Le fichier est vide.")

    if contenu.startswith(b"\xd0\xcf\x11\xe0"):
        raise LectureRefusee(
            "Les anciens fichiers Excel (.xls) ne sont pas lus. Enregistrez-le en .xlsx "
            "ou en .csv depuis votre tableur."
        )
    if contenu.startswith(b"PK\x03\x04"):
        if b"xl/" not in contenu[:4096] and b"[Content_Types].xml" not in contenu[:4096]:
            raise LectureRefusee("Ce fichier n'est pas un tableau Excel (.xlsx).")
        texte = _texte_xlsx(contenu)
    elif type_document(contenu):
        # Un document n'a pas de texte à lui : il passe par `preparer_document`.
        raise LectureRefusee("Ce fichier est un document : il se lit autrement.")
    else:
        texte = _texte_csv(contenu)

    texte = texte.strip()
    if not texte:
        raise LectureRefusee("Aucune donnée lisible dans ce fichier.")
    if len(texte) > MAX_CARACTERES:
        raise LectureRefusee(
            "Ce fichier est trop long pour un tarif. Gardez seulement vos prestations et "
            "leurs prix, puis réessayez."
        )
    return texte


def type_document(contenu: bytes) -> str | None:
    """« application/pdf », « image/jpeg », « image/png », ou None pour un tableau."""
    if contenu.startswith(b"%PDF"):
        return "application/pdf"
    if contenu[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if contenu.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if contenu[4:12] in (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1", b"ftypheim"):
        raise LectureRefusee(
            "Les photos HEIC de l'iPhone ne sont pas lues telles quelles. Prenez la photo "
            "depuis cet écran (« Photographier un devis »), ou enregistrez-la en JPEG."
        )
    return None


def documents_acceptes() -> bool:
    """Seuls Claude et OpenAI lisent un PDF ou une image ; Groq et les autres, non."""
    config = get_config()
    return not config.use_fixtures and config.structuration_provider in ("anthropic", "openai")


def preparer_document(contenu: bytes, media_type: str) -> tuple[Piece, str | None]:
    """Contrôle un document avant de payer sa lecture, et en tire le texte de contrôle.

    Rend la pièce à envoyer au modèle, et le texte du PDF quand il en a un — c'est contre
    lui que chaque prix lu sera vérifié. Une photo, ou un PDF scanné, n'en a pas : None.
    """
    if not documents_acceptes():
        raise LectureRefusee(
            "La lecture des PDF et des photos demande le moteur Claude ou OpenAI. "
            "Importez plutôt un fichier Excel ou CSV, ou dictez vos prix."
        )

    if media_type != "application/pdf":
        if len(contenu) > MAX_IMAGE_OCTETS:
            raise LectureRefusee(
                "Cette photo est trop lourde. Prenez-la depuis cet écran, ou réduisez-la "
                "sous 3,5 Mo."
            )
        return Piece(media_type, contenu, "photo"), None

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        lecteur = PdfReader(io.BytesIO(contenu))
        if lecteur.is_encrypted:
            raise LectureRefusee("Ce PDF est protégé par un mot de passe : il ne peut pas être lu.")
        pages = len(lecteur.pages)
        if pages > MAX_PAGES:
            raise LectureRefusee(
                f"Ce PDF compte {pages} pages : {MAX_PAGES} au plus. Gardez les pages "
                "où figurent vos prix."
            )
        texte = "\n".join((page.extract_text() or "") for page in lecteur.pages)
    except LectureRefusee:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError) as err:
        raise LectureRefusee("Ce PDF n'a pas pu être ouvert. Enregistrez-le à nouveau.") from err

    return Piece(media_type, contenu, "document.pdf"), (texte if texte.strip() else None)


def texte_dicte(texte: str) -> str:
    """La dictée transcrite, telle que l'artisan l'a peut-être retouchée à l'écran."""
    texte = (texte or "").strip()
    if not texte:
        raise LectureRefusee("La dictée est vide.")
    if len(texte) > MAX_CARACTERES:
        raise LectureRefusee("La dictée est trop longue. Découpez-la en plusieurs fois.")
    return texte


# ---------------------------------------------------------------------------
# La lecture
# ---------------------------------------------------------------------------


def _euro(valeur: Decimal) -> str:
    return f"{valeur:.2f}".replace(".", ",") + " €"


def _drapeaux(lus: list[PrixLu], source: str | None,
              tarif: GrillePrix | None) -> list[PrixPropose]:
    """Ce que l'artisan doit regarder, ligne par ligne, avant d'enregistrer.

    `source` : le texte d'où viennent les prix, pour vérifier qu'ils y sont ; None quand
    il n'y en a pas (une photo, un scan), et la ligne le dit.
    """
    dits = nombres_dits(source) if source is not None else None
    existants = {}
    for p in (tarif.lignes if tarif else []):
        existants[cle(p.prestation, p.precision, p.unite)] = p.prix_unitaire_ht

    vus: dict[tuple, list[Decimal]] = {}
    for lu in lus:
        if lu.unite is not None:
            vus.setdefault(cle(lu.prestation, lu.precision, lu.unite), []).append(arrondi(lu.prix))

    proposes: list[PrixPropose] = []
    for lu in lus:
        prix = arrondi(lu.prix)
        drapeaux: list[Drapeau] = []

        if dits is None:
            drapeaux.append(Drapeau(code="non_verifiable",
                                    texte="Lu sur une image : relisez ce prix sur le document."))
        elif round(lu.prix, 2) not in dits:
            drapeaux.append(Drapeau(code="non_retrouve",
                                    texte="Prix introuvable tel quel dans le document : vérifiez-le."))
        if lu.base == "TTC":
            drapeaux.append(Drapeau(code="ttc",
                                    texte="Prix TTC : ramenez-le en HT avant d'enregistrer."))
        if lu.unite is None:
            drapeaux.append(Drapeau(code="unite", texte="Choisissez l'unité."))
        else:
            k = cle(lu.prestation, lu.precision, lu.unite)
            autres = sorted(set(vus.get(k, [])) - {prix})
            if autres:
                drapeaux.append(Drapeau(
                    code="doublon",
                    texte="Lu aussi à " + ", ".join(_euro(a) for a in autres)
                          + " : gardez le bon prix, ou précisez ce qui les distingue.",
                ))
            if k in existants:
                if existants[k] == prix:
                    drapeaux.append(Drapeau(code="deja_identique",
                                            texte="Déjà dans votre tarif, au même prix."))
                else:
                    drapeaux.append(Drapeau(
                        code="deja",
                        texte=f"Déjà dans votre tarif à {_euro(existants[k])} : gardez une "
                              "seule des deux lignes.",
                    ))

        proposes.append(PrixPropose(prestation=lu.prestation.strip(), unite=lu.unite,
                                    prix_unitaire_ht=prix,
                                    precision=(lu.precision or "").strip() or None,
                                    drapeaux=drapeaux))
    return proposes


def _lire(contenu: str, source: str | None, tarif: GrillePrix | None,
          pieces: tuple[Piece, ...] = ()) -> Lecture:
    try:
        lue: GrilleLue = generer(
            GrilleLue, systeme=[_prompt()], contenu=contenu,
            delai=get_config().delai_lecture_s, nom_schema="tarif", pieces=pieces,
        )
    except StructurationError as err:
        raise LectureRefusee(
            "La lecture de vos prix n'a pas abouti. Réessayez dans un instant."
        ) from err

    # Un prix nul ou négatif n'est pas un prix : le modèle a lu une remise ou une
    # quantité. On l'écarte plutôt que de le proposer.
    lus = [l for l in lue.lignes if l.prix > 0 and l.prestation.strip()][:MAX_LIGNES_LUES]
    logger.info("  tarif lu : %d lignes proposées", len(lus))
    return Lecture(lignes=_drapeaux(lus, source, tarif), remarque=lue.remarque)


def lire_texte(texte: str, tarif: GrillePrix | None = None) -> Lecture:
    """Lit les prix d'un texte (dictée transcrite, tableau converti) et les propose."""
    return _lire(texte, texte, tarif)


def lire_document(piece: Piece, source: str | None, tarif: GrillePrix | None = None) -> Lecture:
    """Lit les prix d'un PDF ou d'une photo, préparés par `preparer_document`."""
    return _lire(CONSIGNE_DOCUMENT, source, tarif, (piece,))
