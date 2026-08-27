"""Configuration centralisée. Une seule source de vérité, alimentée par le `.env`.

Tous les champs ont une valeur par défaut : le projet démarre et les tests passent
sans aucun `.env` ni aucune clé API.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.models import Entreprise

RACINE = Path(__file__).resolve().parent.parent


class EntrepriseSettings(Entreprise, BaseSettings):
    """Charge `Entreprise` depuis les variables ENTREPRISE_*. Valeurs factices par défaut."""

    model_config = SettingsConfigDict(env_prefix="ENTREPRISE_", env_file=".env", extra="ignore")

    nom: str = "Bâti Rénov"
    forme_juridique: str = "SARL au capital de 10 000 €"
    adresse: str = "12 rue des Compagnons"
    code_postal_ville: str = "75011 Paris"
    telephone: str = "06 12 34 56 78"
    email: str = "contact@batirenov.fr"
    siret: str = "912 345 678 00019"
    code_ape: str = "4399C"
    tva_intracom: str = "FR45912345678"
    assurance: str = "AXA France IARD"
    assurance_police: str = "10 987 654 321"
    iban: str = "FR76 3000 4000 0100 0012 3456 789"


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Clés API ---
    anthropic_api_key: str = ""
    groq_api_key: str = ""

    # --- Modèles ---
    model_structuration: str = "claude-opus-5"
    model_transcription: str = "whisper-large-v3-turbo"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # --- Mode hors-ligne : rejoue une extraction enregistrée, zéro appel API, zéro euro ---
    use_fixtures: bool = False

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
