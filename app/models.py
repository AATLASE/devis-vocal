"""Modèles du domaine — LE CONTRAT entre les deux moitiés du projet.

Deux modèles distincts, et c'est volontaire :

- `DevisExtraction` est ce que le LLM produit. Il comprend le vocal, rien de plus :
  des flottants, aucun total, aucune date. **Il ne fait jamais d'arithmétique.**
- `Devis` est le modèle métier. Decimal, totaux calculés, TVA, mentions. C'est le seul
  objet que consomme le rendu PDF.

Entre les deux, `to_devis()` : une fonction pure, déterministe, testable sans appel API.
C'est elle qui calcule. Un total faux devant un artisan, c'est la démo morte — donc
le calcul ne quitte jamais Python.

Ce module ne dépend d'aucune configuration ni d'aucune clé : il s'importe et se teste nu.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum

from pydantic import BaseModel, Field

CENT = Decimal("0.01")


def arrondi(valeur: Decimal | float | str) -> Decimal:
    """Arrondi monétaire au centime, ROUND_HALF_UP (la règle commerciale française)."""
    return Decimal(str(valeur)).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# Ce que le LLM produit
# ---------------------------------------------------------------------------


class Unite(str, Enum):
    """Unités de vente du bâtiment. L'enum contraint le LLM via le JSON schema."""

    M2 = "m²"
    ML = "ml"
    M3 = "m³"
    U = "u"
    FORFAIT = "forfait"
    H = "h"
    J = "j"


class TauxTVA(str, Enum):
    REDUIT = "10"  # rénovation d'un logement achevé depuis plus de 2 ans
    NORMAL = "20"  # neuf, local professionnel, ou cas non qualifié


class LigneExtraction(BaseModel):
    """Une prestation facturable, telle que le LLM l'a comprise du vocal."""

    designation: str
    detail: str | None  # "fourniture client", marque, finition — null si rien à préciser
    quantite: float
    unite: Unite
    prix_unitaire_ht: float
    a_valider: bool  # True dès que le prix est une estimation et non un prix dicté


class DevisExtraction(BaseModel):
    """Sortie brute du LLM. Aucun champ n'a de valeur par défaut : le schéma les exige
    tous, et l'absence d'information se dit `null` — jamais par une invention plausible."""

    client_nom: str | None
    client_adresse: str | None
    client_telephone: str | None
    type_travaux: str | None
    duree_estimee: str | None  # « 8 jours ouvrés » — seulement si l'artisan l'a dictée
    lignes: list[LigneExtraction]
    taux_tva_suggere: TauxTVA
    notes: list[str]  # ce que l'artisan a dit et qui n'est pas chiffrable en l'état


# ---------------------------------------------------------------------------
# Le modèle métier
# ---------------------------------------------------------------------------


class Entreprise(BaseModel):
    """En-tête du devis. Peuplé depuis la config, jamais depuis le LLM."""

    nom: str
    forme_juridique: str
    metier: str  # « Plomberie · Chauffage · Sanitaire » — la ligne d'accent du haut de page
    adresse: str
    code_postal_ville: str
    telephone: str
    email: str
    siret: str
    code_ape: str
    tva_intracom: str
    assurance: str
    assurance_police: str
    iban: str


class Client(BaseModel):
    nom: str | None = None
    adresse: str | None = None
    telephone: str | None = None


class LigneDevis(BaseModel):
    designation: str
    detail: str | None = None
    quantite: Decimal
    unite: Unite
    prix_unitaire_ht: Decimal
    total_ht: Decimal
    a_valider: bool = False


class Devis(BaseModel):
    numero: str
    date_emission: date
    validite_jours: int
    date_validite: date

    entreprise: Entreprise
    client: Client
    type_travaux: str | None = None
    duree_estimee: str | None = None

    lignes: list[LigneDevis]
    notes: list[str] = Field(default_factory=list)

    taux_tva: Decimal
    total_ht: Decimal
    montant_tva: Decimal
    total_ttc: Decimal
    acompte_pct: Decimal
    montant_acompte: Decimal

    # Part du HT reposant sur des prix estimés. C'est un total : il se calcule ici,
    # jamais dans le navigateur. L'écran de relecture l'affiche sous les totaux.
    total_ht_estime: Decimal = Decimal("0")

    transcription: str = ""  # affichée dans le navigateur, jamais dans le PDF

    @property
    def a_des_prix_estimes(self) -> bool:
        """Pilote l'astérisque et la note de bas de tableau dans le PDF."""
        return any(ligne.a_valider for ligne in self.lignes)

    @property
    def taux_tva_libelle(self) -> str:
        """Le taux tel qu'il s'écrit sur un devis français : « 10 », « 5,5 »."""
        pct = (self.taux_tva * 100).quantize(Decimal("0.1"))
        entier = pct.to_integral_value()
        return f"{entier:.0f}" if pct == entier else f"{pct}".replace(".", ",")


# ---------------------------------------------------------------------------
# Le gabarit de l'artisan
# ---------------------------------------------------------------------------


class Gabarit(BaseModel):
    """La mise en page A4 que l'artisan a téléversée, à la place de `templates/devis.html`.

    Ajout hors périmètre d'origine (voir CLAUDE.md § Périmètre), assumé. Le modèle vit
    ici parce qu'il traverse la frontière entre les deux moitiés du projet : la moitié
    « données » le stocke et le valide, la moitié « rendu » le consomme dans `pdf.py`.

    `mentions_manquantes` est le prix à payer de cette fonctionnalité, et on le rend
    visible plutôt que de le taire. Le gabarit livré garantit les mentions obligatoires
    d'un devis français ; un gabarit téléversé ne garantit rien. On vérifie donc, à
    l'enregistrement, que les valeurs qui doivent figurer sur un devis apparaissent
    bien dans le document rendu, et on liste celles qui manquent. Le gabarit est
    accepté quand même — c'est le document de l'artisan — mais l'écran le dit.
    """

    proprietaire: str  # l'uid Firebase, ou « local » quand l'auth est inactive
    nom: str  # le nom du fichier téléversé, pour que l'artisan reconnaisse le sien
    html: str
    modifie_le: datetime
    mentions_manquantes: list[str] = Field(default_factory=list)

    @property
    def conforme(self) -> bool:
        return not self.mentions_manquantes


# ---------------------------------------------------------------------------
# La fiche de l'artisan
# ---------------------------------------------------------------------------


class Profil(BaseModel):
    """Qui est l'artisan : son nom, son entreprise, son rôle. Demandé à l'inscription.

    Ajout hors périmètre d'origine, comme le gabarit, et sur la même décision explicite.
    Une chose doit rester claire, parce qu'elle a été tranchée et qu'elle est
    contre-intuitive : **ce profil n'entre jamais dans le devis.**

    L'`Entreprise` du haut du devis porte treize champs légalement obligatoires — SIRET,
    TVA intracommunautaire, assurance décennale, IBAN. Faire remonter ici la seule
    raison sociale produirait un document affichant un nom d'entreprise qui ne
    correspond plus à son SIRET : non seulement inutile, mais moins bon que de ne rien
    faire. Le devis continue donc de sortir sur l'`Entreprise` de la configuration, et
    ce profil ne sert qu'à savoir qui est connecté.

    Le jour où le devis devra vraiment porter l'entreprise de l'artisan, c'est
    `Entreprise` qu'il faudra collecter en entier — pas ce modèle qu'il faudra brancher.
    """

    proprietaire: str  # l'uid Firebase, ou « local » quand l'auth est inactive
    prenom: str
    nom: str
    entreprise: str
    role: str
    modifie_le: datetime

    @property
    def identite(self) -> str:
        """« Camille Durand », pour la barre de compte."""
        return f"{self.prenom} {self.nom}".strip()


# ---------------------------------------------------------------------------
# La couture entre les deux
# ---------------------------------------------------------------------------


def to_devis(
    extraction: DevisExtraction,
    *,
    entreprise: Entreprise,
    transcription: str = "",
    validite_jours: int = 30,
    acompte_pct: Decimal = Decimal("0.30"),
    tva_forcee: Decimal | None = None,
    maintenant: datetime | None = None,
) -> Devis:
    """Transforme une extraction LLM en devis chiffré.

    Fonction pure : mêmes entrées, même sortie. `maintenant` est injectable pour que
    les tests soient déterministes.
    """
    maintenant = maintenant or datetime.now()
    emission = maintenant.date()

    taux_tva = tva_forcee if tva_forcee is not None else Decimal(extraction.taux_tva_suggere.value) / 100

    lignes: list[LigneDevis] = []
    for source in extraction.lignes:
        quantite = Decimal(str(source.quantite))
        prix_unitaire = arrondi(source.prix_unitaire_ht)
        lignes.append(
            LigneDevis(
                designation=source.designation.strip(),
                detail=(source.detail or "").strip() or None,
                quantite=quantite,
                unite=source.unite,
                prix_unitaire_ht=prix_unitaire,
                total_ht=arrondi(quantite * prix_unitaire),
                a_valider=source.a_valider,
            )
        )

    # Le total est la somme des lignes déjà arrondies : c'est ce que le client
    # additionnera lui-même en relisant le PDF. Sommer avant d'arrondir donnerait
    # un total qui ne tombe pas juste à l'œil.
    total_ht = arrondi(sum((ligne.total_ht for ligne in lignes), Decimal("0")))
    montant_tva = arrondi(total_ht * taux_tva)
    total_ht_estime = arrondi(sum((l.total_ht for l in lignes if l.a_valider), Decimal("0")))

    return Devis(
        numero=f"DEV-{maintenant:%Y%m%d-%H%M}",
        date_emission=emission,
        validite_jours=validite_jours,
        date_validite=emission + timedelta(days=validite_jours),
        entreprise=entreprise,
        client=Client(
            nom=extraction.client_nom,
            adresse=extraction.client_adresse,
            telephone=extraction.client_telephone,
        ),
        type_travaux=extraction.type_travaux,
        duree_estimee=extraction.duree_estimee,
        lignes=lignes,
        notes=extraction.notes,
        taux_tva=taux_tva,
        total_ht=total_ht,
        montant_tva=montant_tva,
        total_ttc=arrondi(total_ht + montant_tva),
        acompte_pct=acompte_pct,
        montant_acompte=arrondi((total_ht + montant_tva) * acompte_pct),
        total_ht_estime=total_ht_estime,
        transcription=transcription,
    )
