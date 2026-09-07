"""API du démonstrateur.

Trois étapes, trois endpoints indépendants, aucun état côté serveur : le devis fait
l'aller-retour en JSON. C'est ce qui permet au navigateur d'afficher une vraie progression
(la structuration prend 20 à 40 secondes) et d'afficher la transcription dès qu'elle arrive.

    POST /api/transcribe   audio          -> { transcription }
    POST /api/devis        transcription  -> Devis chiffré
    POST /api/pdf          Devis          -> application/pdf
    GET  /api/entreprise   q              -> [ identités trouvées ]
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import journal
from app import pdf as pdf_module
from app.config import get_config
from app.entreprise import EntrepriseSaisie, EntrepriseTrouvee, rechercher
from app.models import Devis, to_devis
from app.structuration import StructurationError, structure
from app.transcription import TranscriptionError, transcribe

logger = logging.getLogger("devis-vocal")

STATIQUE = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Chromium est lancé une fois pour toutes : le démarrer à chaque devis coûterait
    # une seconde de plus par rendu.
    try:
        await pdf_module.demarrer()
    except NotImplementedError as err:
        # Sous Windows, `--reload` fait basculer uvicorn sur SelectorEventLoop, qui ne
        # sait pas lancer de sous-processus — donc pas de Chromium, donc pas de PDF.
        # Sans ce message, on ne récolte qu'un NotImplementedError nu à trente lignes
        # de pile, et la commande de démarrage documentée a l'air simplement cassée.
        raise RuntimeError(
            "Chromium n'a pas pu démarrer : la boucle asyncio en place ne sait pas lancer "
            "de sous-processus. Sous Windows, c'est ce que provoque `--reload`. Relance "
            "sans cette option — ou, pour garder le rechargement à chaud : "
            'uv run watchfiles "uvicorn app.main:app" app templates'
        ) from err
    yield
    await pdf_module.arreter()


app = FastAPI(title="Devis Vocal", lifespan=lifespan)


class DemandeDevis(BaseModel):
    transcription: str
    taux_tva: Decimal | None = None  # force 0.10 ou 0.20 ; sinon on suit le LLM

    # L'identité de l'artisan voyage avec la demande plutôt que de vivre dans le `.env`.
    # Sans ça, changer d'artisan entre deux rendez-vous impose d'éditer un fichier et de
    # redémarrer le serveur — impossible à faire en montrant l'outil à quelqu'un.
    # Le serveur ne la retient pas : elle est conservée par le navigateur, ce qui laisse
    # intacte la règle « le serveur ne garde rien ». Absente, on retombe sur la config.
    entreprise: EntrepriseSaisie | None = None


@app.get("/health")
async def health() -> dict[str, str]:
    # `mode` et `provider` sont lus par le front pour signaler à l'écran ce qui tourne
    # vraiment. Sans eux, on peut montrer à un artisan un devis rejoué depuis une
    # fixture — ou chiffré par un moteur de secours — en croyant voir le moteur de
    # référence. Dans les deux cas l'erreur est grossière et parfaitement invisible.
    # `provider` sert aussi à annoncer la bonne attente : quarante secondes chez
    # Anthropic, six chez Groq.
    config = get_config()
    return {
        "status": "ok",
        "mode": "fixtures" if config.use_fixtures else "reel",
        "provider": config.structuration_provider,
    }


@app.post("/api/transcribe")
async def api_transcribe(audio: UploadFile) -> dict[str, str]:
    contenu = await audio.read()
    nom = audio.filename or "audio.m4a"
    try:
        transcription = transcribe(contenu, nom)
        # Le vocal d'un vrai artisan ne repasse pas deux fois : on le garde si le
        # journal est armé. Voir app/journal.py — ce n'est pas du stockage produit.
        journal.noter_vocal(contenu, nom, transcription)
        return {"transcription": transcription}
    except TranscriptionError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except Exception:
        logger.exception("Transcription en échec")
        raise HTTPException(status_code=500, detail="La transcription a échoué.") from None


@app.post("/api/devis")
async def api_devis(demande: DemandeDevis) -> Devis:
    config = get_config()
    try:
        extraction = structure(demande.transcription)
    except StructurationError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except Exception:
        logger.exception("Structuration en échec")
        raise HTTPException(status_code=500, detail="La génération du devis a échoué.") from None

    journal.noter_extraction(demande.transcription, extraction)

    entreprise = (
        demande.entreprise.fusionner(config.entreprise)
        if demande.entreprise
        else config.entreprise
    )

    return to_devis(
        extraction,
        entreprise=entreprise,
        transcription=demande.transcription,
        validite_jours=config.validite_jours,
        acompte_pct=config.acompte_pct,
        tva_forcee=demande.taux_tva,
    )


@app.post("/api/pdf")
async def api_pdf(devis: Devis) -> Response:
    try:
        contenu = await pdf_module.render(devis)
    except Exception:
        logger.exception("Rendu PDF en échec")
        raise HTTPException(status_code=500, detail="La génération du PDF a échoué.") from None

    return Response(
        content=contenu,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{devis.numero}.pdf"'},
    )


@app.get("/api/entreprise")
async def api_entreprise(q: str = "") -> list[EntrepriseTrouvee]:
    """Retrouve une entreprise par nom, ville ou numéro, dans la base publique.

    Le travail se fait ici et pas dans le navigateur, pour la même raison que les totaux :
    la clé de TVA, la clé de contrôle du SIRET et la traduction des codes officiels sont
    du calcul, et le calcul ne quitte pas Python. Le front n'a plus qu'à afficher.

    Jamais d'erreur : une recherche infructueuse et un annuaire en panne donnent tous deux
    une liste vide, et le formulaire de saisie prend le relais. Un artisan bloqué parce
    qu'un service tiers ne répond pas, ce serait le comble pour un champ qu'on cherche
    justement à lui épargner.
    """
    return await rechercher(q)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIQUE / "index.html")


app.mount("/static", StaticFiles(directory=STATIQUE), name="static")
