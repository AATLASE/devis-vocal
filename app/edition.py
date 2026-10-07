"""Le devis corrigé par l'artisan — recalculé ici, jamais dans le navigateur.

Ajout hors périmètre d'origine : CLAUDE.md rangeait « édition en ligne du devis » dans
ce qu'il ne fallait pas construire. Elle a été ajoutée sur décision explicite, parce que
c'est la première question d'un artisan devant la démonstration : « et si c'est faux,
je corrige comment ? ». Voir CLAUDE.md § Périmètre.

Deux règles la tiennent, et ce sont celles du reste du projet :

- **Le navigateur ne calcule pas.** Il renvoie les lignes telles que l'artisan les a
  corrigées — désignations, quantités, prix unitaires — et c'est `to_devis()` qui refait
  tous les totaux. Les totaux que le navigateur a encore en main sont ignorés : un devis
  dont le total vient d'un champ modifiable côté client n'est pas un devis.
- **Le serveur ne garde rien.** Le devis d'origine fait l'aller-retour avec ses
  corrections, et repart corrigé. Rien n'est stocké.

Un seul chemin de calcul : les corrections sont remises sous la forme d'une extraction
et repassent par `to_devis()`, exactement comme ce qui sort du modèle. Il n'existe donc
pas de seconde arithmétique à garder juste.
"""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models import (
    Client,
    Devis,
    DevisExtraction,
    LigneExtraction,
    TauxTVA,
    Unite,
    to_devis,
)

# Les deux taux que le devis sait justifier : le gabarit imprime l'attestation du taux
# réduit quand il s'applique. Un 5,5 % saisi à la main sortirait sans sa mention.
TAUX_ACCEPTES = {Decimal("0.10"), Decimal("0.20")}

# Au-delà, ce n'est plus une faute de frappe qu'on laisse passer mais un devis absurde.
QUANTITE_MAX = Decimal("100000")
PRIX_MAX = Decimal("1000000")


class EditionRefusee(ValueError):
    """Une correction qu'on ne peut pas chiffrer. Le message est lu par l'artisan."""


class LigneCorrigee(BaseModel):
    """Une ligne telle que l'artisan l'a laissée. Pas de total : il se calcule ici."""

    model_config = ConfigDict(extra="ignore")

    designation: str
    detail: str | None = None
    quantite: Decimal
    unite: Unite
    prix_unitaire_ht: Decimal
    a_valider: bool = False


class Corrections(BaseModel):
    """Ce que l'écran de relecture permet de changer. Le reste — numéro, date,
    entreprise, conditions — appartient au devis d'origine et n'en bouge pas."""

    model_config = ConfigDict(extra="ignore")

    client: Client
    type_travaux: str | None = None
    duree_estimee: str | None = None
    taux_tva: Decimal
    lignes: list[LigneCorrigee]
    notes: list[str] = []


def _texte(valeur: str | None) -> str | None:
    """Un champ vidé par l'artisan est une absence, pas une chaîne vide : le gabarit
    affiche alors « à compléter » au lieu d'un blanc."""
    valeur = (valeur or "").strip()
    return valeur or None


def verifier(corrections: Corrections, *, max_lignes: int) -> None:
    """Refuse ce qui ne peut pas faire un devis, avec la ligne fautive dans le message."""
    if not corrections.lignes:
        raise EditionRefusee("Un devis doit compter au moins une ligne.")
    if len(corrections.lignes) > max_lignes:
        raise EditionRefusee(f"Un devis ne peut pas dépasser {max_lignes} lignes.")
    if corrections.taux_tva not in TAUX_ACCEPTES:
        raise EditionRefusee("Le taux de TVA doit être 10 % ou 20 %.")

    for numero, ligne in enumerate(corrections.lignes, start=1):
        if not ligne.designation.strip():
            raise EditionRefusee(f"Ligne {numero} : la désignation est vide.")
        if not ligne.quantite.is_finite() or not Decimal("0") < ligne.quantite <= QUANTITE_MAX:
            raise EditionRefusee(f"Ligne {numero} : la quantité doit être supérieure à zéro.")
        if not ligne.prix_unitaire_ht.is_finite() or not Decimal("0") <= ligne.prix_unitaire_ht <= PRIX_MAX:
            raise EditionRefusee(f"Ligne {numero} : le prix unitaire doit être positif ou nul.")


def recalculer(devis: Devis, corrections: Corrections, *, max_lignes: int) -> Devis:
    """Le devis d'origine, avec les corrections de l'artisan, entièrement recalculé."""
    verifier(corrections, max_lignes=max_lignes)

    extraction = DevisExtraction(
        client_nom=_texte(corrections.client.nom),
        client_adresse=_texte(corrections.client.adresse),
        client_telephone=_texte(corrections.client.telephone),
        type_travaux=_texte(corrections.type_travaux),
        duree_estimee=_texte(corrections.duree_estimee),
        lignes=[
            LigneExtraction(
                designation=ligne.designation,
                detail=ligne.detail,
                # `to_devis` relit ces valeurs par `str()`, qui rend la forme la plus
                # courte d'un flottant : « 12.5 » redevient exactement Decimal("12.5").
                # Les bornes ci-dessus gardent loin de la précision où ça cesserait d'être vrai.
                quantite=float(ligne.quantite),
                unite=ligne.unite,
                prix_unitaire_ht=float(ligne.prix_unitaire_ht),
                a_valider=ligne.a_valider,
            )
            for ligne in corrections.lignes
        ],
        # Sans effet : le taux choisi est forcé juste en dessous. Le champ est exigé
        # par le schéma de l'extraction, on lui donne la valeur qui correspond.
        taux_tva_suggere=TauxTVA.NORMAL if corrections.taux_tva == Decimal("0.20") else TauxTVA.REDUIT,
        notes=[note.strip() for note in corrections.notes if note.strip()],
    )

    corrige = to_devis(
        extraction,
        entreprise=devis.entreprise,
        transcription=devis.transcription,
        validite_jours=devis.validite_jours,
        acompte_pct=devis.acompte_pct,
        tva_forcee=corrections.taux_tva,
    )

    # Une correction n'est pas un nouveau devis : il garde son numéro et sa date. Un
    # client qui reçoit la version corrigée doit pouvoir la rapprocher de la première.
    return corrige.model_copy(update={
        "numero": devis.numero,
        "date_emission": devis.date_emission,
        "date_validite": devis.date_validite,
    })
