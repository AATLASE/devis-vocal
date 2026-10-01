"""Audio -> texte.

Interface unique : `transcribe(audio, filename) -> str`. Tout le reste du projet ignore
quel fournisseur est derrière — c'est ce qui permet d'en changer sans rien casser.

Par défaut : OpenAI (gpt-4o-transcribe), avec la même clé que le chiffrage. Groq
(whisper-large-v3-turbo) reste disponible via TRANSCRIPTION_PROVIDER=groq : endpoint
compatible OpenAI, très rapide, tier gratuit. Une alternative 100 % locale est décrite
en bas de fichier.
"""

from __future__ import annotations

import io
import logging

from openai import OpenAI, OpenAIError

from app import suivi
from app.config import get_config

logger = logging.getLogger("devis-vocal")

# Ce que produisent les téléphones et WhatsApp.
EXTENSIONS_ACCEPTEES = {".m4a", ".mp3", ".mp4", ".mpga", ".ogg", ".opus", ".wav", ".webm", ".flac"}


class TranscriptionError(RuntimeError):
    pass


def transcribe(audio: bytes, filename: str) -> str:
    """Transcrit une note vocale en français."""
    config = get_config()

    if not audio:
        raise TranscriptionError("Fichier audio vide.")

    if len(audio) > config.max_upload_octets:
        raise TranscriptionError(
            f"Fichier trop lourd ({len(audio) / 1_048_576:.1f} Mo). "
            f"Maximum {config.max_upload_mo} Mo — soit largement de quoi tenir un vocal de 20 minutes."
        )

    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in EXTENSIONS_ACCEPTEES:
        raise TranscriptionError(
            f"Format « {extension or 'inconnu'} » non pris en charge. "
            f"Formats acceptés : {', '.join(sorted(EXTENSIONS_ACCEPTEES))}."
        )

    # Les deux fournisseurs parlent le même protocole : seuls la clé, l'URL et le
    # modèle changent.
    fournisseur = config.transcription_provider
    modele = config.modele_transcription_actif
    if fournisseur == "groq":
        cle, variable, base_url = config.groq_api_key, "GROQ_API_KEY", config.groq_base_url
    else:
        cle, variable, base_url = config.openai_api_key, "OPENAI_API_KEY", None  # api.openai.com

    if not cle:
        raise TranscriptionError(
            f"{variable} absente. Renseigne-la dans le .env, ou colle directement une "
            "transcription dans le champ texte de la page."
        )

    client = OpenAI(api_key=cle, base_url=base_url)

    fichier = io.BytesIO(audio)
    # Le SDK s'appuie sur l'extension pour typer l'envoi, et « .opus » n'est pas dans
    # la liste des fournisseurs. Un vocal WhatsApp est de l'Opus dans un conteneur
    # Ogg : même fichier, autre nom.
    fichier.name = filename[: -len(extension)] + ".ogg" if extension == ".opus" else filename

    try:
        reponse = client.audio.transcriptions.create(
            model=modele,
            file=fichier,
            language="fr",
            # Amorce le vocabulaire : sans ça, « BA13 » ressort en « bat treize ».
            prompt=(
                "Note vocale d'un artisan du bâtiment décrivant un chantier. "
                "Vocabulaire : BA13, placo, cloison, doublage, chape, ragréage, saignée, "
                "point lumineux, prise de courant, faïence, plinthe, PER, VMC, mitigeur, "
                "receveur, dépose, ml, m², HT."
            ),
        )
    except OpenAIError as err:
        # Le message du fournisseur est un dictionnaire Python sérialisé en anglais —
        # « Error code: 500 - {'error': {'message': 'Internal Server Error'...} ». Il
        # a sa place dans le journal, pas sous les yeux d'un artisan. Ce qu'il doit
        # lire, c'est que la panne n'est pas la sienne et que son vocal est gardé.
        # Le format et la taille sont dans la ligne : un 500 sur un envoi de 3 Ko
        # n'a pas la même cause qu'un 500 sur un webm de 200 Ko bien formé.
        logger.warning(
            "Transcription refusée (%s, %.0f Ko, modèle %s) : %s",
            filename, len(audio) / 1024, modele, err,
        )
        raise TranscriptionError(
            "Le service de transcription n'a pas répondu. Votre enregistrement est "
            "conservé — réessayez dans un instant."
        ) from err

    # Groq comme OpenAI facturent la transcription à la durée d'audio, pas au token :
    # la ligne dit qui a répondu et avec quel modèle, le coût ne s'estime pas ici.
    suivi.appel(fournisseur, modele)

    texte = (reponse.text or "").strip()
    if not texte:
        raise TranscriptionError("Aucune parole détectée dans l'enregistrement.")
    return texte


# ---------------------------------------------------------------------------
# Alternative locale, zéro compte et zéro clé — désactivée par défaut.
#
#   uv pip install faster-whisper
#
# Puis remplacer le corps de `transcribe` par :
#
#   from faster_whisper import WhisperModel
#   _modele = WhisperModel("small", device="cpu", compute_type="int8")
#   segments, _ = _modele.transcribe(io.BytesIO(audio), language="fr")
#   return " ".join(s.text for s in segments).strip()
#
# Compter ~30 à 60 s de calcul CPU pour un vocal de 2 minutes (contre 2 à 3 s chez Groq),
# et une qualité en retrait sur le jargon. Utile si tu veux démontrer sans réseau.
# ---------------------------------------------------------------------------
