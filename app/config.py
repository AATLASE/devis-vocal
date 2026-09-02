"""Configuration centralisée. Une seule source de vérité, alimentée par le `.env`.

Tous les champs ont une valeur par défaut : le projet démarre et les tests passent
sans aucun `.env` ni aucune clé API.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models import Entreprise

RACINE = Path(__file__).resolve().parent.parent


class EntrepriseSettings(Entreprise, BaseSettings):
    """Charge `Entreprise` depuis les variables ENTREPRISE_*. Valeurs factices par défaut."""

    model_config = SettingsConfigDict(env_prefix="ENTREPRISE_", env_file=".env", extra="ignore")

    # Le nom et l'adresse sont manifestement fictifs : personne ne s'y trompe.
    # Les identifiants légaux, eux, sont laissés à zéro plutôt que remplis de valeurs
    # plausibles. Un devis dont l'argument est la conformité aux mentions obligatoires
    # ne peut pas porter un faux SIRET crédible : l'artisan qui le repère cesse
    # d'écouter, et le document n'a rien à faire en circulation. À zéro, ils se lisent
    # pour ce qu'ils sont — des champs à remplir.
    nom: str = "Bâti Rénov"
    forme_juridique: str = "SARL au capital de 10 000 €"
    metier: str = "Rénovation · Plomberie · Second œuvre"
    adresse: str = "12 rue des Compagnons"
    code_postal_ville: str = "75011 Paris"
    telephone: str = "00 00 00 00 00"
    email: str = "contact@example.com"
    siret: str = "000 000 000 00000"
    code_ape: str = "4399C"
    tva_intracom: str = "FR00 000 000 000"
    assurance: str = "Assureur à renseigner"
    assurance_police: str = "000 000 000"
    iban: str = "FR76 0000 0000 0000 0000 0000 000"


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Clés API ---
    anthropic_api_key: str = ""
    groq_api_key: str = ""
    openai_api_key: str = ""

    # --- Modèles ---
    # `anthropic` est le fournisseur de référence : c'est lui qui doit tourner en démo.
    # `groq` est une option gratuite pour dégrossir sans consommer de crédit — le
    # chiffrage y est sensiblement moins juste (voir CONTRIBUTING.md).
    # `autre` accepte n'importe quelle API au format OpenAI — Mistral, DeepSeek,
    # OpenRouter, Together, xAI, ou un modèle local servi par Ollama ou vLLM. Trois
    # variables suffisent : l'URL, la clé, le modèle.
    # Laisse vide et il se déduit de la clé qu'on trouve — voir `_deduire_fournisseur`.
    structuration_provider: Literal["anthropic", "groq", "openai", "autre"] | None = None
    model_structuration: str = "claude-opus-5"
    model_structuration_groq: str = "openai/gpt-oss-120b"
    model_structuration_openai: str = "gpt-5"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # --- Fournisseur libre pour le chiffrage (STRUCTURATION_PROVIDER=autre) ---
    structuration_base_url: str = ""
    structuration_api_key: str = ""
    model_structuration_autre: str = ""

    # --- Transcription ---
    # Groq par défaut : son Whisper est gratuit, rapide, et sans carte bancaire. Les
    # variables TRANSCRIPTION_* ouvrent le même appel à n'importe quel fournisseur au
    # format OpenAI. Laissées vides, elles retombent sur Groq : rien ne change pour
    # les .env existants.
    model_transcription: str = "whisper-large-v3-turbo"
    transcription_base_url: str = ""
    transcription_api_key: str = ""

    @property
    def transcription_url(self) -> str:
        return self.transcription_base_url or self.groq_base_url

    @property
    def transcription_key(self) -> str:
        return self.transcription_api_key or self.groq_api_key

    # --- Mode hors-ligne : rejoue une extraction enregistrée, zéro appel API, zéro euro ---
    use_fixtures: bool = False

    # --- Journal de démonstration ---
    # Garde sur disque les vocaux réels et ce que le modèle en a tiré. Ce sont eux, et
    # pas des fonctionnalités, qui font progresser le prompt : cinq artisans rencontrés,
    # cinq vocaux dont il ne resterait rien le lendemain. Désactivé par défaut — il
    # enregistre la voix de quelqu'un, et ça se demande avant.
    journal: bool = False
    journal_dir: Path = RACINE / "journal"

    # --- Règles de devis ---
    tva_defaut: Decimal = Decimal("0.10")  # rénovation logement > 2 ans
    validite_jours: int = 30
    acompte_pct: Decimal = Decimal("0.30")

    # --- Garde-fous ---
    max_upload_mo: int = 25

    @property
    def max_upload_octets(self) -> int:
        return self.max_upload_mo * 1024 * 1024

    @model_validator(mode="after")
    def _deduire_fournisseur(self) -> "Config":
        """Sans STRUCTURATION_PROVIDER explicite, on chiffre avec la clé qu'on a.

        Renseigner une clé et devoir en plus nommer son fournisseur est une double
        déclaration qui ne sert à rien : la clé désigne déjà le moteur. On ne déduit
        que le silence — un `STRUCTURATION_PROVIDER` écrit dans le .env gagne toujours,
        y compris pour forcer un fournisseur dont la clé est absente et obtenir le
        message d'erreur qui va avec.

        L'ordre suit la qualité du chiffrage, pas la commodité : Anthropic est la
        référence. Groq passe en dernier bien qu'il soit souvent présent, parce qu'une
        GROQ_API_KEY est d'abord là pour la transcription — la trouver ne veut pas dire
        qu'on a choisi Groq pour chiffrer.

        Le moteur retenu reste annoncé par `/health` et affiché à l'écran : déduit ne
        veut pas dire invisible.
        """
        if self.structuration_provider is None:
            if self.anthropic_api_key:
                deduit = "anthropic"
            elif self.openai_api_key:
                deduit = "openai"
            elif self.structuration_base_url:
                deduit = "autre"
            elif self.groq_api_key:
                deduit = "groq"
            else:
                # Aucune clé nulle part : rester sur la référence, dont le message
                # d'absence est celui qui aide le plus (il mentionne USE_FIXTURES).
                deduit = "anthropic"
            self.structuration_provider = deduit
        return self

    entreprise_settings: EntrepriseSettings = Field(default_factory=EntrepriseSettings)

    @property
    def entreprise(self) -> Entreprise:
        """Le modèle pur, tel que le consomme le devis."""
        return Entreprise(**self.entreprise_settings.model_dump())


@lru_cache
def get_config() -> Config:
    config = Config()
    # Une ligne au démarrage : quel moteur chiffre, où part l'audio. C'est la
    # question qu'on se pose toujours en premier quand un devis sort bizarre.
    logging.getLogger("devis-vocal").info(
        "Chiffrage : %s · transcription : %s · mode : %s",
        config.structuration_provider,
        config.transcription_url,
        "fixtures" if config.use_fixtures else "réel",
    )
    return config
