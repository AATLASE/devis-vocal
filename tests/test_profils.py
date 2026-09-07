"""La fiche de l'artisan : nom, prénom, entreprise, rôle.

Ce qui est testé ici, c'est ce qui doit être juste à tous les coups : **ce qu'on
refuse, dans quel ordre on le dit, et ce que la fiche ne touche pas**. Le troisième
point est le moins évident et le plus important — une décision explicite veut que ce
profil n'entre jamais dans le devis, et rien dans le code ne l'empêcherait de le
faire un jour par inadvertance. Un test le tient.

Aucune clé, aucun Firebase, aucun réseau : sans identifiants, `profils` range en
mémoire, exactement comme `gabarits`.
"""

from __future__ import annotations

import pytest

from app import profils
from app.config import get_config
from app.models import Profil
from app.profils import MAX_CHAMP, ProfilRefuse


@pytest.fixture(autouse=True)
def etat_neuf():
    """Ni configuration ni fiche ne doivent survivre d'un cas au suivant."""
    get_config.cache_clear()
    profils.vider_la_memoire()
    yield
    get_config.cache_clear()
    profils.vider_la_memoire()


FICHE = {
    "prenom": "Camille",
    "nom": "Durand",
    "entreprise": "Bâti Rénov",
    "role": "Gérant · Chef d'entreprise",
}


# ---------------------------------------------------------------------------
# Ce qu'on enregistre
# ---------------------------------------------------------------------------


def test_une_fiche_complete_s_enregistre_et_se_relit():
    pose = profils.enregistrer("artisan-1", FICHE)

    assert pose.proprietaire == "artisan-1"
    assert pose.prenom == "Camille"
    assert pose.entreprise == "Bâti Rénov"

    relue = profils.lire("artisan-1")
    assert relue is not None
    assert relue.model_dump() == pose.model_dump()


def test_sans_fiche_on_repond_rien_plutot_qu_une_fiche_vide():
    """La distinction porte l'écran d'inscription : `None` déclenche le formulaire,
    une fiche vide le sauterait en laissant l'artisan sans identité."""
    assert profils.lire("jamais-inscrit") is None


def test_les_fiches_ne_se_melangent_pas_entre_artisans():
    profils.enregistrer("artisan-1", FICHE)
    profils.enregistrer("artisan-2", {**FICHE, "prenom": "Sofia", "entreprise": "Toiture Sud"})

    assert profils.lire("artisan-1").entreprise == "Bâti Rénov"
    assert profils.lire("artisan-2").entreprise == "Toiture Sud"


def test_une_seconde_fiche_remplace_la_premiere():
    profils.enregistrer("artisan-1", FICHE)
    profils.enregistrer("artisan-1", {**FICHE, "role": "Conducteur de travaux"})

    assert profils.lire("artisan-1").role == "Conducteur de travaux"


def test_identite_assemble_le_prenom_et_le_nom():
    """C'est ce que lit la barre de compte, à la place de l'adresse e-mail."""
    assert profils.enregistrer("artisan-1", FICHE).identite == "Camille Durand"


# ---------------------------------------------------------------------------
# Ce qu'on refuse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("champ", ["prenom", "nom", "entreprise", "role"])
def test_chacun_des_quatre_champs_est_obligatoire(champ):
    """L'étape a été voulue bloquante : une fiche à trois champs n'existe pas."""
    with pytest.raises(ProfilRefuse):
        profils.enregistrer("artisan-1", {**FICHE, champ: ""})
    assert profils.lire("artisan-1") is None


def test_un_champ_de_blancs_compte_comme_vide():
    """Sans quoi « Prénom :   » passerait, et la barre de compte afficherait un blanc."""
    with pytest.raises(ProfilRefuse):
        profils.enregistrer("artisan-1", {**FICHE, "prenom": "   \t "})


def test_le_message_de_refus_designe_le_champ_en_francais():
    """Un artisan doit lire lequel manque, pas un nom de champ."""
    with pytest.raises(ProfilRefuse, match="votre prénom"):
        profils.enregistrer("artisan-1", {**FICHE, "prenom": ""})
    with pytest.raises(ProfilRefuse, match="nom de votre entreprise"):
        profils.enregistrer("artisan-1", {**FICHE, "entreprise": ""})


def test_le_premier_champ_manquant_est_celui_qu_on_signale():
    """Dans l'ordre du formulaire. Signaler le rôle alors que le prénom manque aussi
    ferait remonter l'artisan dans le formulaire pour rien."""
    with pytest.raises(ProfilRefuse, match="votre prénom"):
        profils.enregistrer("artisan-1", {"prenom": "", "nom": "", "entreprise": "", "role": ""})


def test_les_espaces_de_bord_sont_retires():
    pose = profils.enregistrer("artisan-1", {**FICHE, "prenom": "  Camille  "})
    assert pose.prenom == "Camille"


def test_un_champ_demesure_est_coupe_et_non_refuse():
    """Un nom de 400 caractères est un copier-coller malheureux, pas une intention :
    refuser la fiche entière ferait tout ressaisir."""
    pose = profils.enregistrer("artisan-1", {**FICHE, "entreprise": "Bâti" * 500})
    assert len(pose.entreprise) == MAX_CHAMP


def test_un_role_libre_est_accepte():
    """La liste proposée à l'écran accélère la saisie, elle n'enferme personne :
    « Autre » renvoie un texte libre, et le serveur n'impose aucune valeur."""
    pose = profils.enregistrer("artisan-1", {**FICHE, "role": "Compagnon couvreur"})
    assert pose.role == "Compagnon couvreur"


def test_les_roles_proposes_ne_sont_pas_vides():
    """C'est le serveur qui tient la liste ; le front la lit et n'en écrit pas une seconde."""
    assert profils.ROLES
    assert all(role.strip() for role in profils.ROLES)


# ---------------------------------------------------------------------------
# Ce que la fiche ne touche pas
# ---------------------------------------------------------------------------


def test_la_fiche_n_entre_pas_dans_le_devis():
    """Le garde-fou de la décision : l'entreprise du devis vient de la configuration,
    avec son SIRET, sa TVA et son assurance. Faire remonter ici la seule raison
    sociale donnerait un document dont le nom ne correspond plus au SIRET — pire que
    de ne rien faire. Si ce test tombe, c'est que quelqu'un a branché les deux.
    """
    profils.enregistrer("artisan-1", {**FICHE, "entreprise": "Toiture Sud"})

    entreprise = get_config().entreprise
    assert entreprise.nom != "Toiture Sud"
    # Et le modèle lui-même ne porte aucun des champs légaux du devis : il n'y a donc
    # rien à brancher, même par mégarde.
    assert not {"siret", "tva_intracom", "assurance", "iban"} & set(Profil.model_fields)


def test_sans_firebase_la_fiche_vit_en_memoire_et_pas_sur_disque():
    """Le repli n'est pas une commodité : c'est ce qui fait passer `uv run pytest`
    sans le moindre secret, et ce qui garde la démonstration hors-ligne possible."""
    assert not get_config().auth_active

    profils.enregistrer("artisan-1", FICHE)
    assert profils.lire("artisan-1") is not None

    profils.vider_la_memoire()
    assert profils.lire("artisan-1") is None


# ---------------------------------------------------------------------------
# Le contrat HTTP
# ---------------------------------------------------------------------------
#
# Sans Firebase, `utilisateur_requis` renvoie l'utilisateur local : les deux routes
# sont donc joignables sans jeton, et c'est exactement l'état dans lequel la suite
# tourne. Ce qu'on vérifie ici n'est pas l'authentification — testée ailleurs — mais
# la forme des réponses, celle dont dépend l'écran d'inscription.


@pytest.fixture
async def client():
    """Un client HTTP sur l'application, sans démarrer Chromium.

    `TestClient` déclenche le `lifespan`, donc le lancement du navigateur : une
    seconde de plus par test, et une dépendance à Playwright là où on ne rend aucun
    PDF. On monte donc le transport à la main.
    """
    import httpx

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def test_get_profil_annonce_une_fiche_absente_et_la_liste_des_roles(client):
    reponse = await client.get("/api/profil")
    assert reponse.status_code == 200

    corps = reponse.json()
    assert corps["complet"] is False
    # La liste vient du serveur : le front la lit et n'en écrit pas une seconde.
    assert corps["roles"] == list(profils.ROLES)


async def test_put_profil_enregistre_et_renvoie_la_fiche(client):
    corps = (await client.put("/api/profil", json=FICHE)).json()

    assert corps["complet"] is True
    assert corps["identite"] == "Camille Durand"
    assert corps["entreprise"] == "Bâti Rénov"

    # Et la lecture suivante la retrouve : c'est ce qui évite de redemander la fiche
    # à chaque connexion.
    assert (await client.get("/api/profil")).json()["complet"] is True


async def test_put_profil_incomplet_repond_400_avec_le_champ_en_francais(client):
    reponse = await client.put("/api/profil", json={**FICHE, "entreprise": ""})

    assert reponse.status_code == 400
    assert "nom de votre entreprise" in reponse.json()["detail"]
    # Rien n'a été enregistré : une fiche partielle n'existe pas.
    assert (await client.get("/api/profil")).json()["complet"] is False


async def test_put_profil_sans_aucun_champ_repond_400_et_non_422(client):
    """Les quatre champs ont une valeur par défaut vide côté modèle, exprès : on veut
    le message français qui dit lequel manque, pas un 422 de Pydantic listant des
    noms de champs en anglais."""
    reponse = await client.put("/api/profil", json={})

    assert reponse.status_code == 400
    assert "prénom" in reponse.json()["detail"]
