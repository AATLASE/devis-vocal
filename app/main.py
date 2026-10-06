"""API du démonstrateur.

Trois étapes, trois endpoints indépendants, aucun état côté serveur : le devis fait
l'aller-retour en JSON. C'est ce qui permet au navigateur d'afficher une vraie progression
(la structuration prend 20 à 40 secondes) et d'afficher la transcription dès qu'elle arrive.

    POST /api/transcribe   audio          -> { transcription }
    POST /api/devis        transcription  -> Devis chiffré
    POST /api/pdf          Devis          -> application/pdf

S'y ajoute `POST /api/apercu`, qui rend le même document que `/api/pdf` mais en HTML,
pour l'aperçu A4 affiché pendant la relecture.

Depuis l'ajout des comptes — hors périmètre d'origine, voir CLAUDE.md § Périmètre —
s'y ajoutent les routes de la fiche et du template personnels de l'artisan :

    GET    /api/firebase         -> la config publique du SDK navigateur
    GET    /api/profil           -> la fiche de l'artisan connecté, et `complet`
    PUT    /api/profil           -> l'enregistrer (les quatre champs sont requis)
    GET    /api/template         -> le template de l'artisan connecté
    PUT    /api/template         -> en poser un (validé avant enregistrement)
    DELETE /api/template         -> revenir au template livré
    POST   /api/template/apercu  -> le PDF d'un template, avant de l'enregistrer
    GET    /api/template/modele  -> le template livré, comme point de départ

La fiche ne descend jamais dans le devis : voir `Profil` dans `app/models.py`.

Le pipeline du devis, lui, reste sans état : l'authentification n'y est qu'un en-tête
facultatif, et un artisan non connecté dicte son devis exactement comme avant.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import journal
from app import pdf as pdf_module
from app import profils as profils_module
from app import suivi
from app import templates as templates_module
from app.authentification import Utilisateur, utilisateur_optionnel, utilisateur_requis
from app.config import get_config
from app.models import Devis, to_devis
from app.profils import ProfilRefuse
from app.structuration import StructurationError, structure
from app.templates import TemplateRefuse
from app.transcription import TranscriptionError, transcribe

logger = logging.getLogger("devis-vocal")

STATIQUE = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Avant Chromium : si le navigateur ne démarre pas, on veut que la raison soit
    # dans le fichier de log et pas seulement dans un terminal qu'on aura fermé.
    suivi.configurer()

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
        with suivi.etape("transcription", attendu=TranscriptionError,
                         fichier=nom, ko=len(contenu) / 1024) as detail:
            transcription = transcribe(contenu, nom)
            detail["caracteres"] = len(transcription)
        suivi.bloc("transcription", transcription)
        # Le vocal d'un vrai artisan ne repasse pas deux fois : on le garde si le
        # journal est armé. Voir app/journal.py — ce n'est pas du stockage produit.
        journal.noter_vocal(contenu, nom, transcription)
        return {"transcription": transcription}
    except TranscriptionError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except Exception:
        raise HTTPException(status_code=500, detail="La transcription a échoué.") from None


@app.post("/api/devis")
async def api_devis(demande: DemandeDevis) -> Devis:
    config = get_config()
    try:
        with suivi.etape("structuration", attendu=StructurationError,
                         caracteres=len(demande.transcription)) as detail:
            extraction = structure(demande.transcription)
            detail["lignes"] = len(extraction.lignes)
            detail["estimees"] = sum(1 for ligne in extraction.lignes if ligne.a_valider)
    except StructurationError as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except Exception:
        raise HTTPException(status_code=500, detail="La génération du devis a échoué.") from None

    suivi.bloc("extraction", extraction.model_dump_json(indent=2))
    journal.noter_extraction(demande.transcription, extraction)

    devis = to_devis(
        extraction,
        entreprise=config.entreprise,
        transcription=demande.transcription,
        validite_jours=config.validite_jours,
        acompte_pct=config.acompte_pct,
        tva_forcee=demande.taux_tva,
    )

    # Les totaux sont la seule partie qui doit être juste à tous les coups : les avoir
    # sous les yeux pendant la démo permet de comparer au PDF sans rouvrir le devis.
    logger.info(
        "  devis  numero=%s lignes=%d tva=%s total_ht=%s montant_tva=%s total_ttc=%s "
        "acompte=%s dont_estime=%s",
        devis.numero, len(devis.lignes), devis.taux_tva, devis.total_ht,
        devis.montant_tva, devis.total_ttc, devis.montant_acompte, devis.total_ht_estime,
    )
    return devis


@app.post("/api/pdf")
async def api_pdf(
    devis: Devis,
    utilisateur: Utilisateur = Depends(utilisateur_optionnel),
) -> Response:
    # L'authentification est facultative ici, et c'est délibéré : un artisan qui n'est
    # pas connecté doit pouvoir dicter et sortir son devis exactement comme avant. Le
    # compte ne sert qu'à retrouver son template.
    template = templates_module.html_pour(utilisateur.uid)

    try:
        with suivi.etape("pdf", numero=devis.numero, template=bool(template)) as detail:
            contenu = await pdf_module.render(devis, template)
            detail["ko"] = len(contenu) / 1024
    except Exception:
        if template:
            # Le template a été validé à l'enregistrement, mais sur le devis d'exemple.
            # Un vrai devis peut le mettre en défaut — vingt lignes au lieu de cinq, un
            # champ nul. Plutôt que de renvoyer l'artisan sans document au moment où il
            # le montre à son client, on rejoue sur le template livré et on le signale.
            logger.warning(
                "  pdf    template de %s en échec, repli sur le template livré", utilisateur.uid
            )
            try:
                with suivi.etape("pdf-repli", numero=devis.numero) as detail:
                    contenu = await pdf_module.render(devis)
                    detail["ko"] = len(contenu) / 1024
                return _reponse_pdf(contenu, f"{devis.numero}.pdf", repli=True)
            except Exception:
                pass
        raise HTTPException(status_code=500, detail="La génération du PDF a échoué.") from None

    return _reponse_pdf(contenu, f"{devis.numero}.pdf")


def _reponse_pdf(contenu: bytes, nom: str, *, repli: bool = False, inline: bool = False) -> Response:
    disposition = "inline" if inline else "attachment"
    entetes = {"Content-Disposition": f'{disposition}; filename="{nom}"'}
    if repli:
        # Lu par le navigateur, qui affiche un avertissement. Un devis sorti sur le
        # template livré alors que l'artisan croit voir le sien est le genre de
        # substitution silencieuse que ce projet refuse ailleurs — voir le bandeau
        # « mode fixtures » : même principe, même raison.
        entetes["X-Template-Repli"] = "1"
        entetes["Access-Control-Expose-Headers"] = "X-Template-Repli"
    return Response(content=contenu, media_type="application/pdf", headers=entetes)


@app.post("/api/apercu")
async def api_apercu(
    devis: Devis,
    utilisateur: Utilisateur = Depends(utilisateur_optionnel),
) -> Response:
    """La feuille A4 en HTML, pour l'aperçu affiché pendant la relecture.

    Le même document que `/api/pdf`, sur le même template, mais rendu par le
    navigateur de l'artisan au lieu de Chromium. Trois raisons de ne pas se
    contenter d'afficher le PDF dans un cadre :

    - il est prêt tout de suite, là où le PDF coûte une à deux secondes de
      Chromium — or l'aperçu s'ouvre au moment même où le devis s'affiche ;
    - les visionneuses PDF intégrées aux navigateurs mobiles sont inégales, et
      certaines refusent purement et simplement de s'afficher dans un cadre ;
    - le document s'y redimensionne, ce qu'un PDF encadré ne fait pas.

    Ce que l'artisan voit ici et ce qu'il télécharge sortent du même template et
    de la même feuille de style : l'aperçu ne peut pas mentir sur le fond. Il
    peut différer d'un cheveu sur le rendu des polices, le PDF étant composé par
    Chromium — c'est la seule liberté qu'il prend.
    """
    template = templates_module.html_pour(utilisateur.uid)

    # Même repli que pour le PDF, et pour la même raison : le template a été validé
    # sur le devis d'exemple, pas sur celui-là. Mieux vaut un aperçu sur la mise en
    # page livrée qu'un cadre vide au moment de relire son devis.
    repli = False
    try:
        html = pdf_module.render_html(devis, template, polices_inline=False)
    except Exception:
        if not template:
            raise HTTPException(status_code=500, detail="L'aperçu n'a pas pu être produit.") from None
        logger.warning("  apercu template de %s en échec, repli sur le template livré", utilisateur.uid)
        try:
            html = pdf_module.render_html(devis, polices_inline=False)
            repli = True
        except Exception:
            raise HTTPException(status_code=500, detail="L'aperçu n'a pas pu être produit.") from None

    entetes = {}
    if repli:
        entetes["X-Template-Repli"] = "1"
        entetes["Access-Control-Expose-Headers"] = "X-Template-Repli"
    return Response(content=html, media_type="text/html; charset=utf-8", headers=entetes)


# ---------------------------------------------------------------------------
# Le compte — configuration du SDK navigateur
# ---------------------------------------------------------------------------


@app.get("/api/firebase")
async def api_firebase() -> dict:
    """La configuration publique du SDK navigateur, et l'état de l'authentification.

    Servie plutôt qu'écrite en dur dans `app.js` : le front est un fichier statique, et
    on ne veut pas d'un projet Firebase codé dans le dépôt. Ces valeurs ne sont pas des
    secrets — Firebase les publie dans le code de toute page qui l'utilise.

    `active` est faux tant que le serveur ne peut pas vérifier de jeton : le front sait
    alors qu'il ne doit pas proposer d'écran de connexion. Un bouton « Se connecter »
    devant une API non protégée serait une porte peinte sur un mur.
    """
    config = get_config()
    return {
        "active": config.auth_active,
        "apiKey": config.firebase_api_key,
        "authDomain": config.firebase_auth_domain,
        "projectId": config.firebase_project_id,
    }


# ---------------------------------------------------------------------------
# La fiche de l'artisan
# ---------------------------------------------------------------------------


class DemandeProfil(BaseModel):
    """Les quatre champs de l'inscription. Validés par `profils.valider`, pas ici :
    on veut le message français qui dit lequel manque, pas un 422 de Pydantic listant
    des noms de champs en anglais."""

    prenom: str = ""
    nom: str = ""
    entreprise: str = ""
    role: str = ""


def _profil_json(profil) -> dict:
    """`complet` est ce que le navigateur regarde pour décider s'il faut demander la
    fiche. Un booléen explicite plutôt qu'un `null` à interpréter : c'est lui qui
    décide d'un écran de plus à l'inscription."""
    if profil is None:
        return {"complet": False, "roles": list(profils_module.ROLES)}
    return {
        "complet": True,
        "prenom": profil.prenom,
        "nom": profil.nom,
        "entreprise": profil.entreprise,
        "role": profil.role,
        "identite": profil.identite,
        "modifie_le": profil.modifie_le.isoformat(),
        "roles": list(profils_module.ROLES),
    }


@app.get("/api/profil")
async def api_profil_lire(utilisateur: Utilisateur = Depends(utilisateur_requis)) -> dict:
    return _profil_json(profils_module.lire(utilisateur.uid))


@app.put("/api/profil")
async def api_profil_poser(
    demande: DemandeProfil,
    utilisateur: Utilisateur = Depends(utilisateur_requis),
) -> dict:
    try:
        profil = profils_module.enregistrer(utilisateur.uid, demande.model_dump())
    except ProfilRefuse as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    logger.info("  profil enregistré par %s : %s · %s", utilisateur.uid,
                profil.identite, profil.entreprise)
    return _profil_json(profil)


# ---------------------------------------------------------------------------
# Le template de l'artisan
# ---------------------------------------------------------------------------


def _template_json(template) -> dict:
    if template is None:
        return {"pose": False}
    return {
        "pose": True,
        "nom": template.nom,
        "modifie_le": template.modifie_le.isoformat(),
        "conforme": template.conforme,
        "mentions_manquantes": template.mentions_manquantes,
        "html": template.html,
    }


@app.get("/api/template")
async def api_template_lire(utilisateur: Utilisateur = Depends(utilisateur_requis)) -> dict:
    return _template_json(templates_module.lire(utilisateur.uid))


@app.put("/api/template")
async def api_template_poser(
    template: UploadFile,
    utilisateur: Utilisateur = Depends(utilisateur_requis),
) -> dict:
    """Valide et enregistre le template. Un template qui ne rend pas est refusé ici,
    pas au moment où l'artisan veut son PDF devant un client."""
    html = await _lire_le_html(template)
    try:
        pose = templates_module.enregistrer(utilisateur.uid, template.filename or "", html)
    except TemplateRefuse as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    logger.info(
        "  template posé par %s : %s (%d mentions manquantes)",
        utilisateur.uid, pose.nom, len(pose.mentions_manquantes),
    )
    return _template_json(pose)


@app.delete("/api/template")
async def api_template_retirer(utilisateur: Utilisateur = Depends(utilisateur_requis)) -> dict:
    try:
        templates_module.supprimer(utilisateur.uid)
    except TemplateRefuse as err:
        raise HTTPException(status_code=502, detail=str(err)) from err
    return {"pose": False}


@app.post("/api/template/apercu")
async def api_template_apercu(
    template: UploadFile,
    utilisateur: Utilisateur = Depends(utilisateur_requis),
) -> Response:
    """Le PDF d'un template sur un devis d'exemple, **avant** de l'enregistrer.

    C'est la moitié utile de la fonctionnalité : personne ne pose une mise en page
    qu'il n'a pas vue sortir en A4.
    """
    html = await _lire_le_html(template)
    try:
        propre, _, _ = templates_module.valider(html)
    except TemplateRefuse as err:
        raise HTTPException(status_code=400, detail=str(err)) from err

    try:
        contenu = await pdf_module.render(templates_module.devis_exemple(), propre)
    except Exception:
        raise HTTPException(status_code=500, detail="L'aperçu n'a pas pu être produit.") from None

    return _reponse_pdf(contenu, "apercu-template.pdf", inline=True)


@app.get("/api/template/modele")
async def api_template_modele() -> FileResponse:
    """Le template livré, à télécharger comme point de départ.

    Partir d'une page blanche pour écrire un devis conforme n'a aucun sens : celui-ci
    passe déjà les quatorze contrôles de mentions obligatoires."""
    return FileResponse(
        pdf_module.TEMPLATES / "devis.html",
        media_type="text/html",
        filename="devis-modele.html",
    )


async def _lire_le_html(fichier: UploadFile) -> str:
    """Le contenu du fichier téléversé, décodé. Le garde-fou de taille est appliqué
    ici aussi : lire deux cents mégaoctets en mémoire avant de les refuser serait absurde."""
    config = get_config()
    contenu = await fichier.read(config.max_template_octets + 1)
    if len(contenu) > config.max_template_octets:
        raise HTTPException(
            status_code=400,
            detail=f"Le fichier dépasse {config.max_template_ko} Ko.",
        )
    try:
        return contenu.decode("utf-8")
    except UnicodeDecodeError as err:
        raise HTTPException(
            status_code=400,
            detail="Le fichier n'est pas lisible en UTF-8. Enregistrez-le dans cet encodage.",
        ) from err


# Le navigateur doit revalider avant de réutiliser une page ou une feuille de style.
#
# Starlette envoie déjà `ETag` et `Last-Modified`, mais aucun `Cache-Control` : sans
# directive, le navigateur applique sa mise en cache heuristique et peut resservir un
# fichier périmé sans même demander au serveur s'il a changé. Observé, et coûteux des
# deux côtés : en développement on croit qu'une modification n'a pas pris ; en
# démonstration, un artisan peut se retrouver devant la version d'avant le déploiement.
#
# `no-cache` ne veut pas dire « ne garde rien » mais « demande avant de réutiliser ».
# Les fichiers restent en cache et la revalidation coûte un 304 sans corps — les
# 388 Ko de polices ne repassent donc pas sur le réseau à chaque chargement.
REVALIDER = "no-cache"


class StatiquesRevalidees(StaticFiles):
    """`StaticFiles`, en demandant au navigateur de revalider. Voir ci-dessus."""

    def file_response(self, *args, **kwargs) -> Response:
        reponse = super().file_response(*args, **kwargs)
        reponse.headers["Cache-Control"] = REVALIDER
        return reponse


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIQUE / "index.html", headers={"Cache-Control": REVALIDER})


app.mount("/static", StatiquesRevalidees(directory=STATIQUE), name="static")
