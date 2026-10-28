"""Le template de devis propre à chaque artisan : stockage, validation, conformité.

Hors du périmètre d'origine (`CLAUDE.md` exclut « base de données » et « comptes /
auth / multi-tenant »), ajouté sur décision explicite. Le module tient trois rôles :

1. **Stocker** le HTML téléversé, dans Firestore. Sans Firebase, aucun template
   n'existe : les routes répondent 404 et `html_pour` renvoie toujours None, comme
   l'exige `CLAUDE.md`. Le dictionnaire en mémoire qui reste ne sert qu'aux tests, qui
   passent sans le moindre secret.
2. **Valider** avant d'enregistrer, jamais au moment du rendu. Un template qui explose
   pendant la démonstration, devant l'artisan, est exactement ce qu'on ne peut pas se
   permettre : on refuse donc à l'enregistrement ce qui ne rend pas.
3. **Contrôler les mentions obligatoires**. Le template livré garantit la conformité du
   devis ; un template téléversé ne garantit rien, et c'est le risque assumé de cette
   fonctionnalité. Plutôt que de le taire, on rend un devis d'exemple avec le template
   et on vérifie que les valeurs qui doivent figurer sur un devis français y sont. Ce
   qui manque est listé, montré à l'écran, et conservé sur le template.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import date, datetime
from decimal import Decimal
from html.parser import HTMLParser

from app.authentification import UID_LOCAL, application
from app.config import get_config
from app.models import Client, Devis, Entreprise, LigneDevis, Template, Unite, arrondi
from app.pdf import TemplateError, f_date_fr, f_date_longue, f_montant, render_html

logger = logging.getLogger("devis-vocal")

# L'ancien nom, gardé tel quel : c'est sous lui que sont rangés les templates déjà
# posés. Le renommer ici les rendrait introuvables sans migration de la base.
COLLECTION = "gabarits"

# Les deux blancs que produit le formatage français du devis : l'espace fine insécable
# du séparateur de milliers et l'insécable ordinaire. Écrites en échappement comme
# dans `pdf.py` — à l'œil, dans un fichier source, elles sont indistinguables d'une
# espace normale, et une comparaison de chaînes qui échoue sur ce point est indébogable.
FINE_INSECABLE = "\u202f"
INSECABLE = "\u00a0"


class TemplateRefuse(Exception):
    """Le template ne peut pas être enregistré. Le message part vers le navigateur :
    il doit dire à l'artisan quoi corriger, pas décrire une pile d'appels."""


# ---------------------------------------------------------------------------
# Le devis d'exemple — sert à valider un template et à en montrer l'aperçu
# ---------------------------------------------------------------------------

# Les montants sont volontairement peu ronds. Le contrôle des mentions cherche des
# valeurs dans le document rendu : avec « 1 000,00 », une coïncidence typographique
# suffirait à faire croire qu'un total est imprimé alors qu'il ne l'est pas.
_LIGNES_EXEMPLE = [
    ("Dépose de l'ancienne salle de bain et évacuation", None, "1", Unite.FORFAIT, "687.40", False),
    ("Fourniture et pose d'un receveur extra-plat 120×90", "Bac résine, coloris au choix",
     "1", Unite.U, "1243.90", False),
    ("Faïence murale toute hauteur", "Pose droite, joints époxy", "18.5", Unite.M2, "78.60", True),
    ("Alimentation et évacuation, reprise complète", None, "1", Unite.FORFAIT, "946.30", False),
    ("Pose d'un sèche-serviettes électrique", "Fourniture client", "1", Unite.U, "214.75", True),
]


# Une identité à elle, plutôt que celle de la configuration. Le contrôle des mentions
# cherche des valeurs : il ne vaut que si aucune n'est contenue dans une autre. Avec
# les valeurs par défaut, la police « 000 000 000 » se lisait dans le SIRET
# « 000 000 000 00000 », et tout template qui imprimait le SIRET passait pour assuré.
# Le SIREN 123 456 789 ne passe pas la clé de Luhn : aucune entreprise ne le porte.
ENTREPRISE_EXEMPLE = Entreprise(
    nom="Atelier Exemple",
    forme_juridique="SARL au capital de 8 000 €",
    metier="Plomberie · Carrelage · Second œuvre",
    adresse="27 quai des Tanneurs",
    code_postal_ville="38000 Grenoble",
    telephone="00 00 00 00 00",
    email="contact@example.com",
    siret="123 456 789 00012",
    code_ape="4322A",
    tva_intracom="FR00 123 456 789",
    assurance="Mutuelle Exemple Assurances",
    assurance_police="PE-778 412",
    iban="FR76 0000 0000 0000 0000 0000 000",
)


def devis_exemple(entreprise: Entreprise | None = None) -> Devis:
    """Un devis complet et figé, pour valider un template et en produire l'aperçu.

    Figé au sens strict — dates et identité comprises. Un aperçu qui changerait d'un
    appel à l'autre empêcherait de comparer deux versions d'un template côte à côte,
    et un contrôle qui dépendrait de la configuration du serveur ne dirait pas la même
    chose en local et en production.
    """
    entreprise = entreprise or ENTREPRISE_EXEMPLE

    lignes = [
        LigneDevis(
            designation=designation,
            detail=detail,
            quantite=Decimal(quantite),
            unite=unite,
            prix_unitaire_ht=Decimal(prix),
            total_ht=arrondi(Decimal(quantite) * Decimal(prix)),
            a_valider=estime,
        )
        for designation, detail, quantite, unite, prix, estime in _LIGNES_EXEMPLE
    ]
    # `arrondi()` et pas `quantize()` nu : la règle commerciale française est
    # ROUND_HALF_UP, alors que le défaut de Decimal arrondit au pair le plus proche.
    # Sur ce devis-là, la TVA tombe pile sur la demi-unité — 454,645 — et les deux
    # règles ne donnent pas le même centime. Ce document part en aperçu sous les yeux
    # d'un artisan : il doit s'additionner comme le vrai.
    total_ht = arrondi(sum((ligne.total_ht for ligne in lignes), Decimal("0")))
    montant_tva = arrondi(total_ht * Decimal("0.10"))
    total_ttc = arrondi(total_ht + montant_tva)

    return Devis(
        numero="DEV-EXEMPLE-0001",
        date_emission=date(2026, 3, 12),
        validite_jours=30,
        date_validite=date(2026, 4, 11),
        entreprise=entreprise,
        client=Client(
            nom="Madame Hélène Vasseur",
            adresse="8 allée des Tilleuls, 69300 Caluire-et-Cuire",
            telephone="06 12 34 56 78",
        ),
        type_travaux="Rénovation complète d'une salle de bain",
        duree_estimee="8 jours ouvrés",
        lignes=lignes,
        notes=[
            "Le carrelage est à la charge du client, livré avant le démarrage.",
            "Coupure d'eau d'une demi-journée à prévoir le premier jour.",
        ],
        taux_tva=Decimal("0.10"),
        total_ht=total_ht,
        montant_tva=montant_tva,
        total_ttc=total_ttc,
        acompte_pct=Decimal("0.30"),
        montant_acompte=arrondi(total_ttc * Decimal("0.30")),
        total_ht_estime=arrondi(
            sum((ligne.total_ht for ligne in lignes if ligne.a_valider), Decimal("0"))
        ),
    )


# ---------------------------------------------------------------------------
# Nettoyage
# ---------------------------------------------------------------------------

# Une balise `<script>` sans fermeture emporte tout ce qui la suit, comme dans un
# navigateur, qui lit alors la fin du document comme du script. C'est aussi ce qui
# garde le filtre linéaire : chaque passage s'arrête à une fermeture ou à la fin du
# texte, et ne recommence jamais le même parcours. L'ancienne expression repartait à
# chaque ouverture non refermée vers la fin du fichier — deux minutes de calcul pour
# un fichier de 512 Ko fait de `<script>` répétés.
_SCRIPT = re.compile(r"<script\b[^>]*(?:>|\Z).*?(?:</script\s*>|\Z)", re.IGNORECASE | re.DOTALL)
_ATTRIBUT_EVENEMENT = re.compile(r"""\son[a-z]+\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)""", re.IGNORECASE)


def nettoyer(html: str) -> str:
    """Retire le JavaScript du template.

    Un devis est un document, pas une application : rien de ce qu'un script pourrait
    faire n'a sa place dans un PDF.

    Le filtre travaille sur le source Jinja, avant le rendu : il ne voit ni ce qu'une
    expression reconstitue, ni ce que les expressions régulières ne savent pas lire. Ce
    n'est donc pas la frontière de sécurité. Celle-ci est dans `pdf._rendre`, où
    Chromium imprime sans JavaScript et sans réseau, et dans le bac à sable Jinja. Ce
    filtre-ci est une mesure d'hygiène : le template enregistré, et l'aperçu HTML que
    l'artisan affiche dans son propre navigateur, n'emportent pas de script visible.
    """
    html = _SCRIPT.sub("", html)
    return _ATTRIBUT_EVENEMENT.sub("", html)


# ---------------------------------------------------------------------------
# Contrôle des mentions obligatoires
# ---------------------------------------------------------------------------


def _mentions_attendues(devis: Devis) -> list[tuple[str, list[str | tuple[str, ...]]]]:
    """Le libellé montré à l'artisan, et les valeurs dont au moins une doit figurer.
    Une valeur donnée en tuple exige toutes ses chaînes.

    On cherche les **valeurs**, pas des mots-clés : vérifier que le template contient
    le mot « SIRET » ne dit pas qu'il imprime le SIRET.
    """
    e = devis.entreprise
    return [
        ("Le numéro du devis", [devis.numero]),
        ("La date d'émission", [f_date_fr(devis.date_emission), f_date_longue(devis.date_emission)]),
        ("La date de fin de validité",
         [f_date_fr(devis.date_validite), f_date_longue(devis.date_validite)]),
        ("Le nom de l'entreprise", [e.nom]),
        ("L'adresse de l'entreprise", [e.adresse, e.code_postal_ville]),
        ("Le numéro SIRET", [e.siret]),
        ("Le numéro de TVA intracommunautaire", [e.tva_intracom]),
        # L'assureur et la police : l'un sans l'autre ne permet pas au client de
        # vérifier la couverture.
        ("L'assurance professionnelle", [(e.assurance, e.assurance_police)]),
        ("Le nom du client", [devis.client.nom or ""]),
        ("Le détail des prestations", [devis.lignes[0].designation]),
        ("Le total HT", [f_montant(devis.total_ht)]),
        ("Le montant de la TVA", [f_montant(devis.montant_tva)]),
        ("Le total TTC", [f_montant(devis.total_ttc)]),
        ("La mention « bon pour accord »", ["bon pour accord"]),
    ]


_BLANCS = re.compile(r"\s+")


def _normaliser(texte: str) -> str:
    """Blancs unifiés, casse abaissée.

    Les insécables du formatage français deviennent des espaces simples : sans ça,
    « 4 251,30 » cherché dans un document qui l'affiche correctement — avec une fine
    insécable entre les milliers — ne se trouverait jamais.
    """
    texte = texte.replace(FINE_INSECABLE, " ").replace(INSECABLE, " ")
    return _BLANCS.sub(" ", texte).strip().lower()


class _TexteImprime(HTMLParser):
    """Le texte que le PDF imprime, et pas celui que le fichier contient.

    `page.pdf()` n'imprime ni le titre du document, ni les styles, ni les scripts, ni
    le contenu d'un `<template>`, ni les commentaires. Le template livré met le numéro du
    devis dans `<title>` : un artisan qui le retirait du corps restait « conforme ».
    Le parcours est linéaire, là où l'ancienne expression régulière repartait vers la
    fin du document à chaque `<` resté sans `>`.

    Ce que la lecture du HTML ne peut pas voir, c'est le CSS : un texte en
    `display: none` compte encore. Il faudrait pour cela lire le rendu de Chromium.
    """

    # Ce que `<head>` contient de texte vit dans ces éléments-là. Un texte posé nu dans
    # `<head>`, lui, est déplacé dans le corps par le navigateur, et s'imprime : il
    # compte donc, comme dans Chromium.
    MASQUES = frozenset({"title", "style", "script", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.morceaux: list[str] = []
        self._masques = 0

    def handle_starttag(self, balise, attributs) -> None:
        if balise in self.MASQUES:
            self._masques += 1

    def handle_endtag(self, balise) -> None:
        if balise in self.MASQUES and self._masques:
            self._masques -= 1

    def handle_data(self, texte) -> None:
        if not self._masques:
            self.morceaux.append(texte)


def _texte_rendu(html: str) -> str:
    """Le document réduit au texte qu'il imprime."""
    lecteur = _TexteImprime()
    lecteur.feed(html)
    lecteur.close()
    return _normaliser(" ".join(lecteur.morceaux))


def controler_les_mentions(html_rendu: str, devis: Devis) -> list[str]:
    """Les mentions obligatoires absentes du document rendu."""
    texte = _texte_rendu(html_rendu)

    def presente(valeurs: list[str | tuple[str, ...]]) -> bool:
        for valeur in valeurs:
            parties = valeur if isinstance(valeur, tuple) else (valeur,)
            attendues = [_normaliser(partie) for partie in parties if partie]
            if attendues and all(attendu in texte for attendu in attendues):
                return True
        return False

    return [libelle for libelle, valeurs in _mentions_attendues(devis) if not presente(valeurs)]


# ---------------------------------------------------------------------------
# Validation complète — ce qui décide si un template peut être enregistré
# ---------------------------------------------------------------------------


def valider(html: str) -> tuple[str, str, list[str]]:
    """Valide un template. Renvoie `(html nettoyé, html rendu, mentions manquantes)`.

    Lève `TemplateRefuse` sur ce qui ne peut pas être enregistré : template vide, trop
    gros, syntaxe Jinja fautive, ou explosion au rendu. Les mentions manquantes, elles,
    ne bloquent pas — elles sont signalées.
    """
    config = get_config()

    if not html.strip():
        raise TemplateRefuse("Le fichier est vide.")

    if len(html.encode("utf-8")) > config.max_template_octets:
        raise TemplateRefuse(
            f"Le fichier dépasse {config.max_template_ko} Ko. Les images doivent être "
            "des URI `data:` compactes, ou rester hors du template."
        )

    propre = nettoyer(html)
    exemple = devis_exemple()

    # Le rendu d'essai a deux fonctions : refuser tout de suite un template cassé, et
    # produire le texte sur lequel on contrôle les mentions.
    try:
        rendu = render_html(exemple, template=propre)
    except TemplateError as err:
        raise TemplateRefuse(f"Le template n'a pas pu être rendu. {err}") from err
    except Exception as err:  # noqa: BLE001 — un template tiers échoue de mille façons
        logger.warning("  template rendu d'essai échoué : %r", err)
        raise TemplateRefuse(
            f"Le template n'a pas pu être rendu. {type(err).__name__} : {err}"
        ) from err

    return propre, rendu, controler_les_mentions(rendu, exemple)


# ---------------------------------------------------------------------------
# Stockage
# ---------------------------------------------------------------------------

# Le repli hors-Firebase, pour les tests seulement : sans Firebase, les routes du
# template répondent 404 et `html_pour` n'en lit aucun. Volontairement en mémoire et
# non sur disque : le projet n'a pas de base de données, et n'en gagne pas une par la
# porte de derrière.
_memoire: dict[str, Template] = {}
_verrou = threading.Lock()


def _firestore():
    from firebase_admin import firestore

    return firestore.client(app=application())


def _actif() -> bool:
    """Firestore n'est sollicité que si Firebase est réellement configuré."""
    return get_config().auth_active


def lire(uid: str) -> Template | None:
    """Le template de cet artisan, ou None s'il utilise celui livré."""
    if not _actif():
        return _memoire.get(uid)
    try:
        document = _firestore().collection(COLLECTION).document(uid).get()
    except Exception as err:  # noqa: BLE001
        # Firestore injoignable ne doit pas empêcher de sortir un devis : on retombe
        # sur le template livré, qui est de toute façon celui qu'on sait conforme.
        logger.warning("  template lecture Firestore impossible (%s) : %r", uid, err)
        return None
    if not document.exists:
        return None
    try:
        return Template(**(document.to_dict() or {}))
    except Exception as err:  # noqa: BLE001
        logger.warning("  template document illisible (%s) : %r", uid, err)
        return None


def enregistrer(uid: str, nom: str, html: str) -> Template:
    """Valide puis enregistre le template de cet artisan. Lève `TemplateRefuse`."""
    propre, _, manquantes = valider(html)

    template = Template(
        proprietaire=uid,
        nom=(nom or "template.html").strip()[:120],
        html=propre,
        modifie_le=datetime.now(),
        mentions_manquantes=manquantes,
    )

    if not _actif():
        with _verrou:
            _memoire[uid] = template
        return template

    donnees = template.model_dump()
    donnees["modifie_le"] = template.modifie_le.isoformat()
    try:
        _firestore().collection(COLLECTION).document(uid).set(donnees)
    except Exception as err:  # noqa: BLE001
        logger.error("  template écriture Firestore impossible (%s) : %r", uid, err)
        raise TemplateRefuse(
            "Le template n'a pas pu être enregistré : la base est injoignable. Réessayez."
        ) from err
    return template


def supprimer(uid: str) -> None:
    """Revient au template livré. Sans effet si l'artisan n'en avait pas posé."""
    if not _actif():
        with _verrou:
            _memoire.pop(uid, None)
        return
    try:
        _firestore().collection(COLLECTION).document(uid).delete()
    except Exception as err:  # noqa: BLE001
        logger.error("  template suppression Firestore impossible (%s) : %r", uid, err)
        raise TemplateRefuse(
            "Le template n'a pas pu être supprimé : la base est injoignable. Réessayez."
        ) from err


def html_pour(uid: str | None) -> str | None:
    """Le HTML à passer à `pdf.render()` pour cet artisan, ou None pour celui livré.

    Sans Firebase, toujours None : « pas de gabarit », dit `CLAUDE.md`. Tout le monde
    y est le même utilisateur local, et un template posé par l'un s'imprimait sur les
    devis de tous les autres.

    Appelée à chaque PDF, y compris pour les visiteurs non connectés — ce sont eux la
    majorité des rendus en démonstration. D'où le court-circuit : quand Firebase est
    actif, `UID_LOCAL` désigne quelqu'un qui n'est pas connecté, donc qui ne peut rien
    posséder. Interroger Firestore pour un document qui n'existera jamais coûterait un
    aller-retour réseau sur le chemin le plus chaud du produit.
    """
    if not uid or uid == UID_LOCAL or not get_config().auth_active:
        return None
    template = lire(uid)
    return template.html if template else None


def vider_la_memoire() -> None:
    """Pour les tests, qui ne doivent pas se transmettre de template d'un cas à l'autre."""
    with _verrou:
        _memoire.clear()
