"""Garde-fous — ce qui protège le crédit API et la machine.

Le démonstrateur est destiné à vivre sur une URL publique, avec les clés API de ses
auteurs derrière. Trois choses peuvent mal tourner, et une seule est une attaque :

- **quelqu'un trouve l'URL** et fait tourner le chiffrage en boucle. C'est le risque
  réel, et il ne demande aucune compétence — il suffit du lien.
- **une requête énorme** arrive et fait tomber la machine avant même d'être validée.
- **la démonstration elle-même dérape** : un artisan qui reclique dix fois, dix
  Chromium qui démarrent, un VPS qui s'étouffe.

Rien ici n'est un système d'authentification. C'est une porte avec un code, posée en
attendant celle que construit l'autre moitié du projet. Elle se retire en une ligne.

Pas de base, pas de Redis. La limitation de débit vit en mémoire — elle protège d'une
rafale, et une rafale ne survit pas à un redémarrage. Les compteurs de dépense, eux,
sont écrits sur disque : un plafond qu'on annule en relançant l'application ne protège
de rien, et c'est précisément au redéploiement qu'on aurait envie de tricher.
"""

from __future__ import annotations

import hmac
import json
import logging
import time
from collections import defaultdict, deque
from datetime import date

from fastapi import HTTPException, Request

from app.config import get_config

logger = logging.getLogger("devis-vocal")

# Tout `/api/` est gardé, sauf la courte liste ci-dessous. C'est fermé par défaut, et
# c'est délibéré : une route ajoutée demain est protégée sans que personne y pense.
# L'inverse — une liste de routes à garder — laisse tôt ou tard passer celle qu'on a
# oublié d'inscrire, et on ne s'en aperçoit pas puisque tout fonctionne.
PREFIXE_PROTEGE = "/api/"

# Ce qui doit rester joignable sans le code, sinon la page ne peut pas le demander :
# la configuration publique du SDK Firebase, dont le navigateur a besoin pour afficher
# l'écran de connexion. Ce ne sont pas des secrets — Firebase les publie dans le code
# de toute page qui l'utilise. `/health` et les fichiers statiques ne commencent pas
# par `/api/` et ne sont donc pas concernés.
ROUTES_LIBRES = frozenset({"/api/firebase"})

# Le code voyage dans un en-tête, jamais dans l'URL : une URL se retrouve dans
# l'historique du navigateur, dans les journaux du reverse proxy et dans le
# `Referer` envoyé aux sites tiers.
ENTETE_ACCES = "X-Acces"


# ---------------------------------------------------------------------------
# La porte
# ---------------------------------------------------------------------------


def acces_requis() -> bool:
    """Sans `ACCES_CODE` dans le `.env`, tout est ouvert.

    C'est volontaire : en développement et pendant les tests, exiger un code
    n'apporterait rien et compliquerait tout. La protection s'arme le jour où on
    renseigne la variable, c'est-à-dire au moment de la mise en ligne.
    """
    return bool(get_config().acces_code)


def verifier_acces(request: Request) -> None:
    """Refuse la requête si le code est absent ou faux."""
    if not acces_requis():
        return

    fourni = request.headers.get(ENTETE_ACCES, "")
    attendu = get_config().acces_code

    # `compare_digest` compare en temps constant. Un `==` classique s'arrête au
    # premier caractère différent, et le temps de réponse trahit alors combien de
    # caractères sont justes — de quoi retrouver le code lettre par lettre.
    if not hmac.compare_digest(fourni, attendu):
        raise HTTPException(status_code=401, detail="Code d'accès invalide.")


# ---------------------------------------------------------------------------
# Le débit
# ---------------------------------------------------------------------------


_fenetres: dict[str, deque[float]] = defaultdict(deque)

# Les compteurs de dépense, gardés sur disque. En mémoire seule, un redéploiement les
# remettrait à zéro — et un plafond qu'on annule en relançant l'application ne protège
# de rien. C'est un fichier de quatre lignes, pas une base de données : le projet n'en
# veut pas, et il n'en a pas besoin pour compter jusqu'à quatre cents.
_etat: dict | None = None


def _client(request: Request) -> str:
    """L'adresse de l'appelant.

    Derrière un reverse proxy, uvicorn doit tourner avec `--proxy-headers` pour que
    ce soit l'adresse réelle et non celle du proxy. On ne lit pas `X-Forwarded-For`
    nous-mêmes : cet en-tête est écrit par le client, donc n'importe qui pourrait
    s'inventer une adresse neuve à chaque requête et contourner toute limite.
    """
    return request.client.host if request.client else "inconnu"


def verifier_debit(request: Request) -> None:
    """Fenêtre glissante d'une minute, par adresse."""
    config = get_config()
    limite = config.limite_par_minute
    if limite <= 0:
        return

    maintenant = time.monotonic()
    fenetre = _fenetres[_client(request)]

    while fenetre and maintenant - fenetre[0] > 60:
        fenetre.popleft()

    # Les adresses qui ne reviennent pas laisseraient une entrée vide chacune. Sur un
    # service exposé, c'est une fuite de mémoire lente mais réelle.
    if len(_fenetres) > 2048:
        for adresse in [a for a, f in _fenetres.items() if not f]:
            del _fenetres[adresse]

    if len(fenetre) >= limite:
        logger.warning("Débit dépassé pour %s (%d requêtes/min)", _client(request), len(fenetre))
        raise HTTPException(
            status_code=429,
            detail="Trop de requêtes en peu de temps. Patientez quelques instants.",
        )

    fenetre.append(maintenant)


def _neuf() -> dict:
    return {"jour": "", "devis_jour": 0, "mois": "", "devis_mois": 0}


def _charger() -> dict:
    """Relit les compteurs sur disque. Un fichier absent ou abîmé repart à zéro.

    Repartir à zéro est le mauvais côté du compromis, mais c'est le seul tenable :
    refuser de servir parce qu'un fichier de compteurs est illisible transformerait
    un garde-fou en panne. Le plafond de dépense chez le fournisseur reste dessous.
    """
    global _etat
    if _etat is not None:
        return _etat

    chemin = get_config().compteurs_fichier
    try:
        charge = json.loads(chemin.read_text(encoding="utf-8"))
        _etat = {**_neuf(), **{c: charge[c] for c in _neuf() if c in charge}}
    except FileNotFoundError:
        _etat = _neuf()
    except Exception as err:
        logger.warning("Compteurs illisibles (%s) : on repart de zéro.", err)
        _etat = _neuf()
    return _etat


def _sauver() -> None:
    """Écrit les compteurs. Passe par un fichier temporaire puis un remplacement.

    Une écriture directe interrompue au mauvais moment laisserait un JSON tronqué,
    donc illisible, donc un compteur remis à zéro — exactement ce qu'on cherche à
    éviter. Le remplacement, lui, est atomique : soit l'ancien fichier, soit le neuf.
    """
    chemin = get_config().compteurs_fichier
    try:
        chemin.parent.mkdir(parents=True, exist_ok=True)
        provisoire = chemin.with_suffix(".tmp")
        provisoire.write_text(json.dumps(_etat, indent=2), encoding="utf-8")
        provisoire.replace(chemin)
    except Exception as err:
        # Ne pas bloquer le devis : le plafond continue de s'appliquer en mémoire
        # pour ce processus. On perd la survie au redémarrage, pas la protection.
        logger.warning("Compteurs non enregistrés (%s)", err)


def consommer_devis() -> None:
    """Plafonds de dépense. C'est le garde-fou du portefeuille.

    Le code d'accès protège du passant ; celui-ci protège du code qui a circulé, du
    script laissé en boucle et de l'erreur de manipulation. Il compte les devis parce
    que c'est l'opération qui coûte de l'argent — une transcription chez Groq est
    gratuite, un chiffrage ne l'est pas.

    Deux échelles : le jour attrape l'emballement, le mois attrape la fuite lente
    qu'un plafond journalier laisse passer sans jamais broncher.
    """
    config = get_config()
    etat = _charger()

    aujourd_hui = date.today()
    jour = aujourd_hui.isoformat()
    mois = aujourd_hui.strftime("%Y-%m")

    if etat["jour"] != jour:
        etat["jour"], etat["devis_jour"] = jour, 0
    if etat["mois"] != mois:
        etat["mois"], etat["devis_mois"] = mois, 0

    for plafond, consomme, echelle, reprise in (
        (config.devis_par_jour, etat["devis_jour"], "aujourd'hui", "demain"),
        (config.devis_par_mois, etat["devis_mois"], "ce mois-ci", "le mois prochain"),
    ):
        if plafond > 0 and consomme >= plafond:
            logger.error(
                "Plafond atteint (%d devis %s). Aucun appel payant ne partira avant %s.",
                plafond, echelle, reprise,
            )
            raise HTTPException(
                status_code=429,
                detail=f"Le nombre de devis autorisés {echelle} est atteint. "
                       f"Réessayez {reprise}.",
            )

    etat["devis_jour"] += 1
    etat["devis_mois"] += 1
    _sauver()


def compteurs() -> dict[str, int]:
    """Pour /health : savoir où on en est sans fouiller les journaux ni la facture."""
    config = get_config()
    etat = _charger()
    aujourd_hui = date.today()
    return {
        "devis_du_jour": etat["devis_jour"] if etat["jour"] == aujourd_hui.isoformat() else 0,
        "plafond_jour": config.devis_par_jour,
        "devis_du_mois": etat["devis_mois"] if etat["mois"] == aujourd_hui.strftime("%Y-%m") else 0,
        "plafond_mois": config.devis_par_mois,
        "adresses_suivies": len(_fenetres),
    }


def reinitialiser() -> None:
    """Remet les compteurs à zéro. Utilisé par les tests, jamais par l'application."""
    global _etat
    _fenetres.clear()
    _etat = None


# ---------------------------------------------------------------------------
# Les middlewares
# ---------------------------------------------------------------------------


async def limiter_la_taille(request: Request, call_next):
    """Refuse les corps trop gros **avant** de les lire.

    Sans ça, `await audio.read()` charge d'abord tout en mémoire et ne vérifie la
    taille qu'ensuite : un envoi de deux gigaoctets fait tomber la machine avant
    d'atteindre la moindre validation. Une seule requête suffit.

    On se fie à `Content-Length`. Un client qui l'omet passe au travers — mais il
    retombe alors sur le plafond de `transcribe()`, qui reste en place. Les deux
    contrôles se complètent au lieu de se remplacer.
    """
    from starlette.responses import JSONResponse

    config = get_config()
    chemin = request.url.path

    if chemin.startswith(PREFIXE_PROTEGE):
        # La transcription reçoit de l'audio, le reste ne reçoit que du JSON. Un devis
        # de plus d'un demi-mégaoctet n'est pas un devis, c'est une charge.
        plafond = (
            config.max_upload_octets
            if chemin == "/api/transcribe"
            else config.max_json_ko * 1024
        )
        declaree = request.headers.get("content-length")
        if declaree and declaree.isdigit() and int(declaree) > plafond:
            logger.warning("Corps refusé sur %s : %s octets", chemin, declaree)
            return JSONResponse(
                status_code=413,
                content={"detail": f"Requête trop volumineuse (maximum {plafond // 1024} Ko)."},
            )

    return await call_next(request)


async def garder_les_routes(request: Request, call_next):
    """La porte et le débit, appliqués à toutes les routes coûteuses.

    En middleware plutôt qu'en dépendance sur chaque route, pour une raison apprise à
    la fusion : le second développeur a ajouté huit routes — profil, gabarit, aperçu —
    dont une qui lance Chromium sur du HTML fourni par l'appelant. Aucune n'était
    gardée, et rien ne le signalait. Une garde qu'il faut penser à poser est une garde
    qu'on oublie.
    """
    from starlette.responses import JSONResponse

    chemin = request.url.path
    if chemin.startswith(PREFIXE_PROTEGE) and chemin not in ROUTES_LIBRES:
        try:
            verifier_acces(request)
            verifier_debit(request)
        except HTTPException as err:
            # Levée hors du cycle des routes, l'exception ne rencontrerait aucun
            # gestionnaire : on rend la réponse nous-mêmes, dans la forme que le
            # front sait déjà lire.
            return JSONResponse(status_code=err.status_code, content={"detail": err.detail})

    return await call_next(request)


# Ce que le navigateur doit refuser de faire avec cette page. Le socle n'autorise que
# l'application elle-même : polices, styles et scripts sont servis par elle, donc
# `'self'` suffit et il n'y a aucun `unsafe-inline` à concéder.
#
# Les origines Firebase ne s'y ajoutent QUE lorsque l'authentification est armée. Une
# CSP qui les autoriserait en permanence élargirait la surface d'un démonstrateur qui,
# la plupart du temps, tourne sans comptes.
SOCLE = {
    "default-src": ["'self'"],
    "img-src": ["'self'", "data:"],
    "media-src": ["'self'", "blob:"],   # la relecture du vocal passe par un blob:
    "font-src": ["'self'"],
    "style-src": ["'self'"],
    "script-src": ["'self'"],
    "connect-src": ["'self'"],
    "object-src": ["'none'"],
    "base-uri": ["'none'"],
    "frame-ancestors": ["'none'"],      # la page ne s'intègre dans aucune iframe
    "form-action": ["'none'"],
}

# Le SDK Firebase pour navigateur n'existe qu'en ESM, servi depuis gstatic. Le module
# `compte.js` l'importe de là : sans cette origine, il ne se charge pas, `window.Compte`
# reste indéfini et il n'y a pas d'écran de connexion — en silence, puisqu'une CSP
# refuse sans rien casser d'autre. C'est précisément ce qui est arrivé.
SDK_FIREBASE = "https://www.gstatic.com"

# Les points d'entrée que le SDK appelle : vérification des identifiants, puis
# rafraîchissement du jeton toutes les heures.
API_FIREBASE = [
    "https://identitytoolkit.googleapis.com",
    "https://securetoken.googleapis.com",
]


def politique() -> str:
    """Construit la CSP selon ce qui est réellement armé."""
    directives = {nom: list(valeurs) for nom, valeurs in SOCLE.items()}

    config = get_config()
    if config.auth_active:
        directives["script-src"].append(SDK_FIREBASE)
        directives["connect-src"].extend(API_FIREBASE)
        # La connexion Google passe par une page d'aide hébergée sur le domaine
        # d'authentification du projet.
        if config.firebase_auth_domain:
            directives["frame-src"] = ["'self'", f"https://{config.firebase_auth_domain}"]

    return "; ".join(f"{nom} {' '.join(valeurs)}" for nom, valeurs in directives.items())


ENTETES_SECURITE = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    # Le micro est le seul matériel dont la page a besoin ; tout le reste est refusé,
    # y compris aux éventuelles iframes.
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=(), payment=()",
}


async def poser_les_entetes(request: Request, call_next):
    reponse = await call_next(request)
    for nom, valeur in ENTETES_SECURITE.items():
        reponse.headers.setdefault(nom, valeur)
    reponse.headers.setdefault("Content-Security-Policy", politique())
    return reponse
