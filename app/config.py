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
    # `anthropic` est le fournisseur de référence : c'est lui qui doit tourner en démo.
    # `groq` est une option gratuite pour dégrossir sans consommer de crédit — le
    # chiffrage y est sensiblement moins juste (voir CONTRIBUTING.md).
    structuration_provider: Literal["anthropic", "groq", "openai"] = "anthropic"
    model_structuration: str = "claude-opus-5"
    model_structuration_groq: str = "openai/gpt-oss-120b"
    model_structuration_openai: str = "gpt-5"
    model_transcription: str = "whisper-large-v3-turbo"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # Qui transcrit : `groq` (gratuit, la référence) ou `openai`. Avec
    # STRUCTURATION_PROVIDER=openai, c'est ce qui permet de tout faire tourner sur la
    # seule OPENAI_API_KEY. Même SDK, même appel : seuls l'URL, la clé et le modèle
    # changent.
    transcription_provider: Literal["groq", "openai"] = "groq"
    model_transcription_openai: str = "whisper-1"

    @property
    def modele_transcription_actif(self) -> str:
        """Le modèle du fournisseur de transcription retenu — celui qui répond vraiment."""
        if self.transcription_provider == "openai":
            return self.model_transcription_openai
        return self.model_transcription

    # --- Mode hors-ligne : rejoue une extraction enregistrée, zéro appel API, zéro euro ---
    use_fixtures: bool = False

    # --- Journal de démonstration ---
    # Garde sur disque les vocaux réels et ce que le modèle en a tiré. Ce sont eux, et
    # pas des fonctionnalités, qui font progresser le prompt : cinq artisans rencontrés,
    # cinq vocaux dont il ne resterait rien le lendemain. Désactivé par défaut — il
    # enregistre la voix de quelqu'un, et ça se demande avant.
    journal: bool = False
    journal_dir: Path = RACINE / "journal"

    # --- Journalisation d'exécution ---
    # Voir app/suivi.py. Toujours actif : un log qu'il faut penser à armer est un log
    # qu'on n'a pas le jour où quelque chose casse devant un artisan.
    log_dir: Path = RACINE / "logs"
    log_retention_jours: int = 30
    # Recopie la transcription et le JSON d'extraction dans le log. C'est ce qui rend
    # un devis raté compréhensible après coup ; c'est aussi ce qui fait le volume.
    log_contenu: bool = True

    # --- Règles de devis ---
    tva_defaut: Decimal = Decimal("0.10")  # rénovation logement > 2 ans
    validite_jours: int = 30
    acompte_pct: Decimal = Decimal("0.30")

    # --- Garde-fous ---
    max_upload_mo: int = 25

    @property
    def max_upload_octets(self) -> int:
        return self.max_upload_mo * 1024 * 1024

    # --- Authentification Firebase ---
    # Hors du périmètre d'origine du démonstrateur (CLAUDE.md interdit « comptes /
    # auth / multi-tenant » et « base de données »), ajouté sur décision explicite.
    #
    # Tout est facultatif, et c'est la règle qui compte : sans identifiants Firebase,
    # `auth_active` est faux, l'application se comporte exactement comme avant et la
    # suite de tests passe sans le moindre secret. C'est aussi ce qui garde le mode
    # hors-ligne démontrable dans un sous-sol sans réseau.
    #
    # Le compte de service — celui qui vérifie les jetons côté serveur. Deux formes,
    # parce que Coolify injecte des variables et ne dépose pas de fichiers :
    firebase_credentials: Path | None = None        # chemin vers le JSON
    firebase_credentials_json: str = ""             # le même JSON, en clair dans l'env

    # La configuration publique du SDK navigateur. Ces valeurs ne sont pas des secrets :
    # Firebase les publie dans le code de toute page qui l'utilise. Elles sont servies
    # au front par /api/firebase.
    firebase_api_key: str = ""
    firebase_auth_domain: str = ""
    firebase_project_id: str = ""

    @property
    def auth_active(self) -> bool:
        """Vrai seulement si le serveur peut réellement vérifier un jeton.

        On ne se contente pas de la config publique : servir un écran de connexion
        sans vérification côté serveur donnerait une porte peinte sur un mur — le pire
        des deux mondes, puisqu'on croirait l'API protégée.
        """
        return bool(self.firebase_credentials_json or self.firebase_credentials)

    # --- Gabarits de devis ---
    # Le HTML que l'artisan téléverse pour remplacer `templates/devis.html`.
    max_gabarit_ko: int = 512

    @property
    def max_gabarit_octets(self) -> int:
        return self.max_gabarit_ko * 1024

    entreprise_settings: EntrepriseSettings = Field(default_factory=EntrepriseSettings)

    @property
    def entreprise(self) -> Entreprise:
        """Le modèle pur, tel que le consomme le devis."""
        return Entreprise(**self.entreprise_settings.model_dump())


@lru_cache
def get_config() -> Config:
    return Config()
