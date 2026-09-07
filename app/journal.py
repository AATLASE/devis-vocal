"""Journal de démonstration — de la matière première, pas du stockage.

Le produit ne garde rien : le devis fait l'aller-retour en JSON et le serveur l'oublie
aussitôt. Ce module est la seule exception, et elle est délibérée.

Ce qui manque pour faire progresser le prompt, ce ne sont pas des fonctionnalités, ce
sont de **vrais vocaux d'artisans** — dictés vite, en camionnette, avec les mots du
métier, les hésitations et le bruit de fond. On en croise cinq dans une semaine de
rendez-vous, et sans trace il n'en reste rien le lendemain. Ce dossier les garde.

Ce n'est pas une base de données, et il ne faut pas qu'elle le devienne : **rien ici
n'est jamais relu par l'application**. C'est une pile de fichiers qu'on ouvre à la main,
entre deux rendez-vous, pour comprendre ce qui est mal sorti et corriger le prompt.

    journal/2026-08-31/103412-a1b2c3d4e5/
        audio.webm          le vocal tel qu'il a été dicté
        transcription.txt   ce que Whisper en a compris
        extraction.json     ce que le modèle en a tiré
        meta.json           quand, par quels modèles

Désactivé par défaut, parce qu'il enregistre la voix de quelqu'un. `JOURNAL=true`
l'arme, et le dossier est ignoré par git. Le dire à l'artisan avant d'enregistrer
n'est pas une option.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from pathlib import Path

from app.config import get_config
from app.models import DevisExtraction
from app.transcription import EXTENSIONS_ACCEPTEES

logger = logging.getLogger("devis-vocal")


def actif() -> bool:
    return get_config().journal


def _cle(transcription: str) -> str:
    """Relie l'audio à son chiffrage sans que le serveur retienne quoi que ce soit.

    Les deux requêtes sont indépendantes et le serveur est sans état — mais elles
    portent toutes deux la même transcription. Son empreinte suffit donc à les
    rapprocher, et on n'a rien inventé qui ressemble à une session.
    """
    return hashlib.sha1(" ".join(transcription.split()).lower().encode("utf-8")).hexdigest()[:10]


def _racine() -> Path:
    return get_config().journal_dir


def _trouver(cle: str) -> Path | None:
    """Le dossier déjà ouvert par la transcription, s'il existe."""
    racine = _racine()
    if not racine.exists():
        return None
    for jour in sorted(racine.iterdir(), reverse=True):
        if not jour.is_dir():
            continue
        for dossier in jour.iterdir():
            if dossier.is_dir() and dossier.name.endswith(f"-{cle}"):
                return dossier
    return None


def _ouvrir(cle: str, maintenant: datetime) -> Path:
    dossier = _trouver(cle)
    if dossier is None:
        dossier = _racine() / f"{maintenant:%Y-%m-%d}" / f"{maintenant:%H%M%S}-{cle}"
        dossier.mkdir(parents=True, exist_ok=True)
    return dossier


def _sans_casser(operation):
    """Le journal est un observateur. Qu'il échoue ne doit jamais coûter un devis à
    l'artisan qui est en face — on note et on continue."""
    try:
        operation()
    except Exception:
        logger.exception("Le journal n'a pas pu écrire")


def noter_vocal(audio: bytes, filename: str, transcription: str,
                maintenant: datetime | None = None) -> None:
    """Appelé après une transcription réussie. L'audio est la pièce qui a de la valeur."""
    if not actif():
        return

    def ecrire():
        instant = maintenant or datetime.now()
        dossier = _ouvrir(_cle(transcription), instant)
        # `filename` vient du client et sert à construire un chemin. Aujourd'hui
        # `transcribe()` a déjà refusé tout ce qui n'est pas une extension connue,
        # donc rien de dangereux n'arrive ici — mais cette sécurité est un effet de
        # bord, pas une intention : elle disparaîtrait au premier appel fait dans un
        # autre ordre. On la rend explicite, sur place.
        extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if extension not in EXTENSIONS_ACCEPTEES:
            extension = ".bin"
        (dossier / f"audio{extension}").write_bytes(audio)
        (dossier / "transcription.txt").write_text(transcription, encoding="utf-8")

        config = get_config()
        (dossier / "meta.json").write_text(json.dumps({
            "horodatage": instant.isoformat(timespec="seconds"),
            "fichier": filename,
            "octets": len(audio),
            "modele_transcription": config.model_transcription,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    _sans_casser(ecrire)


def noter_extraction(transcription: str, extraction: DevisExtraction,
                     maintenant: datetime | None = None) -> None:
    """Appelé après un chiffrage réussi. Rejoint le vocal quand il y en avait un ;
    ouvre un dossier seul quand la transcription a été collée à la main."""
    if not actif():
        return

    def ecrire():
        instant = maintenant or datetime.now()
        dossier = _ouvrir(_cle(transcription), instant)
        (dossier / "extraction.json").write_text(
            extraction.model_dump_json(indent=2), encoding="utf-8")
        # Le texte peut manquer si le chiffrage part d'une transcription collée.
        transcrit = dossier / "transcription.txt"
        if not transcrit.exists():
            transcrit.write_text(transcription, encoding="utf-8")

        config = get_config()
        meta = dossier / "meta.json"
        donnees = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else {}
        donnees["chiffre_le"] = instant.isoformat(timespec="seconds")
        donnees["fournisseur"] = config.structuration_provider
        donnees["modele_structuration"] = {
            "anthropic": config.model_structuration,
            "groq": config.model_structuration_groq,
            "openai": config.model_structuration_openai,
        }.get(config.structuration_provider, "?")
        donnees["lignes"] = len(extraction.lignes)
        donnees["lignes_estimees"] = sum(1 for l in extraction.lignes if l.a_valider)
        meta.write_text(json.dumps(donnees, ensure_ascii=False, indent=2), encoding="utf-8")

    _sans_casser(ecrire)
