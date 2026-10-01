"""Configuration centralisée. Une seule source de vérité, alimentée par le `.env`.

Tous les champs ont une valeur par défaut : le projet démarre et les tests passent
sans aucun `.env` ni aucune clé API.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
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
    # `openai` est le fournisseur par défaut, pour le chiffrage comme pour la
    # transcription : une seule clé, OPENAI_API_KEY, fait tourner tout le pipeline.
    # `anthropic` et `groq` restent sélectionnables. Groq est gratuit pour dégrossir,
    # mais son chiffrage est sensiblement moins juste (voir CONTRIBUTING.md).
    structuration_provider: Literal["anthropic", "groq", "openai"] = "openai"
    model_structuration: str = "claude-opus-5"
    model_structuration_groq: str = "openai/gpt-oss-120b"
    model_structuration_openai: str = "gpt-5"
    transcription_provider: Literal["openai", "groq"] = "openai"
    model_transcription_openai: str = "gpt-4o-transcribe"
    model_transcription: str = "whisper-large-v3-turbo"  # chez Groq
    groq_base_url: str = "https://api.groq.com/openai/v1"

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

    entreprise_settings: EntrepriseSettings = Field(default_factory=EntrepriseSettings)

    @property
    def entreprise(self) -> Entreprise:
        """Le modèle pur, tel que le consomme le devis."""
        return Entreprise(**self.entreprise_settings.model_dump())


@lru_cache
def get_config() -> Config:
    return Config()
