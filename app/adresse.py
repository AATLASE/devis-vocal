"""Adresse du client — complétée depuis la Base Adresse Nationale.

Il n'existe aucun annuaire qui donne l'adresse d'un particulier à partir de son nom, et
il ne doit pas en exister : ce serait un problème de données personnelles, pas une
fonctionnalité. Ce qui existe, c'est l'autocomplétion d'une adresse qu'on commence à
taper — l'artisan écrit « 12 rue des comp », choisit dans la liste, et l'adresse sort
écrite comme La Poste l'écrit, code postal compris.

Pour un client professionnel (syndic, SCI, commerce), l'annuaire des entreprises déjà
branché pour l'identité de l'artisan fait l'affaire : voir `app/entreprise.py`.

Source : le service de géocodage de la Géoplateforme (IGN), qui a repris l'API Adresse
de data.gouv.fr — l'ancienne adresse y redirige. Ouvert, gratuit, sans clé ni compte.
https://data.geopf.fr/geocodage/search

L'appel passe par le serveur et pas par le navigateur : la politique de sécurité de la
page n'autorise les requêtes que vers l'application elle-même.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import BaseModel

logger = logging.getLogger("devis-vocal")

API_URL = "https://data.geopf.fr/geocodage/search"

ENTETES = {"User-Agent": "devis-vocal (demonstrateur artisans)"}

DELAI = 4.0  # secondes : une autocomplétion qui se fait attendre ne complète plus rien

# En dessous, la base propose des rues de toute la France sans rapport avec ce qu'on
# cherche, et chaque frappe consomme une requête du débit autorisé.
LONGUEUR_MIN = 5


class AdresseTrouvee(BaseModel):
    """Une adresse proposée à l'artisan."""

    libelle: str  # ce qui s'écrit sur le devis : « 12 Rue des Compagnons, 49480 Verrières-en-Anjou »
    voie: str
    code_postal: str
    ville: str
    # « 49, Maine-et-Loire, Pays de la Loire » : c'est ce qui distingue deux rues
    # homonymes dans la liste, comme la ville distingue deux entreprises.
    contexte: str = ""


def _mapper(feature: dict) -> AdresseTrouvee | None:
    """Traduit un résultat de la base en adresse de devis. `None` si inexploitable."""
    proprietes = feature.get("properties") or {}

    voie = (proprietes.get("name") or "").strip()
    code_postal = (proprietes.get("postcode") or "").strip()
    ville = (proprietes.get("city") or "").strip()
    if not (voie and code_postal and ville):
        return None

    # Une commune seule n'est pas une adresse où envoyer un devis : le libellé de la
    # base répéterait la ville deux fois (« Lyon, 69001 Lyon »).
    if proprietes.get("type") == "municipality":
        return None

    return AdresseTrouvee(
        libelle=f"{voie}, {code_postal} {ville}",
        voie=voie,
        code_postal=code_postal,
        ville=ville,
        contexte=(proprietes.get("context") or "").strip(),
    )


async def rechercher(requete: str, limite: int = 5) -> list[AdresseTrouvee]:
    """Propose des adresses qui commencent comme `requete`. Liste vide si rien ne colle.

    Ne lève jamais : la base en panne laisse simplement l'artisan taper l'adresse en
    entier, comme il l'aurait fait sans elle.
    """
    requete = (requete or "").strip()
    if len(requete) < LONGUEUR_MIN:
        return []

    parametres = {
        "q": requete[:200],
        "limit": str(min(limite, 10)),
        "autocomplete": "1",
        "index": "address",
    }

    try:
        async with httpx.AsyncClient(timeout=DELAI, headers=ENTETES, follow_redirects=True) as client:
            reponse = await client.get(API_URL, params=parametres)
            reponse.raise_for_status()
            resultats = reponse.json().get("features") or []
    except Exception as err:
        logger.warning("Recherche d'adresse indisponible (%s) : %s", requete, err)
        return []

    trouvees = []
    for feature in resultats:
        mappee = _mapper(feature)
        if mappee and mappee.libelle not in {t.libelle for t in trouvees}:
            trouvees.append(mappee)
    return trouvees[:limite]
