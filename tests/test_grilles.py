"""Le tarif de l'artisan : validation, stockage, routes, et son usage au chiffrage.

Ce qui doit être juste à tous les coups, et que ces tests tiennent :

- **un tarif enregistré est propre** : unités connues, prix bornés au centime,
  identifiants courts et stables, jamais deux prix pour la même prestation ;
- **la double clé du chiffrage** : une ligne n'est « votre prix » que si l'identifiant
  existe, que l'unité est la même et que le prix recopié est exactement celui du tarif.
  Python vérifie, il n'impose jamais un prix ;
- **les garde-fous du CLAUDE.md** : sans Firebase, rien n'existe ; un visiteur chiffre
  exactement comme avant ; sans tarif, l'appel au modèle est celui d'avant.

Aucune clé, aucun Firebase, aucun réseau : les fournisseurs sont simulés.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import grilles
from app.config import get_config
from app.grilles import MAX_LIGNES, GrilleRefusee
from app.models import DevisExtraction, GrillePrix, LigneExtraction, PrixArtisan, Unite

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def etat_neuf():
    get_config.cache_clear()
    grilles.vider_la_memoire()
    yield
    get_config.cache_clear()
    grilles.vider_la_memoire()


def ligne(prestation="Pose de carrelage de sol", unite="m²", prix="42", **autres) -> dict:
    return {"prestation": prestation, "unite": unite, "prix_unitaire_ht": prix, **autres}


def tarif(*lignes: PrixArtisan) -> GrillePrix:
    return GrillePrix(proprietaire="artisan-1", lignes=list(lignes), modifie_le=datetime.now())


P1 = PrixArtisan(id="P1", prestation="Pose de carrelage de sol, fourniture client",
                 unite=Unite.M2, prix_unitaire_ht=Decimal("42.00"))
P2 = PrixArtisan(id="P2", prestation="Fourniture et pose d'un receveur extra-plat",
                 unite=Unite.U, prix_unitaire_ht=Decimal("650.00"))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_un_tarif_valide_est_nettoye_et_numerote():
    propres = grilles.valider([
        ligne("  Pose   de carrelage | de sol\n", "m2", "42,5"),
        ligne("Peinture des murs", "m²", "1 200", precision="deux couches"),
    ])
    assert [p.id for p in propres] == ["P1", "P2"]
    # La barre verticale sépare les colonnes du bloc envoyé au modèle : elle ne passe pas.
    assert propres[0].prestation == "Pose de carrelage de sol"
    assert propres[0].unite is Unite.M2  # « m2 » tapé au clavier
    assert propres[0].prix_unitaire_ht == Decimal("42.50")
    assert propres[1].prix_unitaire_ht == Decimal("1200.00")
    assert propres[1].precision == "deux couches"


@pytest.mark.parametrize("fautive, attendu", [
    (ligne(prestation="  "), "Ligne 2 : indiquez la prestation"),
    (ligne(unite="kg"), "choisissez une unité"),
    (ligne(prix=""), "indiquez un prix HT"),
    (ligne(prix="0"), "indiquez un prix HT"),
    (ligne(prix="-5"), "indiquez un prix HT"),
    (ligne(prix="abc"), "indiquez un prix HT"),
    (ligne(prix="NaN"), "indiquez un prix HT"),
    (ligne(prix="150000"), "ne peut pas dépasser"),
])
def test_une_ligne_fautive_est_refusee_en_nommant_son_rang(fautive, attendu):
    with pytest.raises(GrilleRefusee, match=attendu):
        grilles.valider([ligne("Dépose de carrelage", "m²", "20"), fautive])


def test_un_tarif_trop_long_est_refuse():
    lignes = [ligne(f"Prestation {i}") for i in range(MAX_LIGNES + 1)]
    with pytest.raises(GrilleRefusee, match=str(MAX_LIGNES)):
        grilles.valider(lignes)


def test_deux_prix_pour_la_meme_prestation_sont_refuses_en_la_nommant():
    """Accents, casse et espaces ne font pas deux prestations différentes."""
    with pytest.raises(GrilleRefusee, match="apparaît deux fois"):
        grilles.valider([ligne("Pose de carrelage", prix="42"),
                         ligne("pose de  CARRELAGE", prix="45")])


def test_le_meme_prix_saisi_deux_fois_est_fusionne_sans_bruit():
    propres = grilles.valider([ligne("Pose de carrelage"), ligne("Pose de carrelage")])
    assert len(propres) == 1


def test_une_precision_differente_fait_une_autre_prestation():
    propres = grilles.valider([ligne("Faïence", prix="60", precision="gamme courante"),
                               ligne("Faïence", prix="95", precision="haut de gamme")])
    assert len(propres) == 2


def test_les_identifiants_existants_sont_gardes_et_les_autres_numerotes():
    """Un identifiant qui change à chaque enregistrement rendrait illisibles les
    références d'un devis en cours de relecture."""
    propres = grilles.valider([
        ligne("A", id="P7"),
        ligne("B", id="P7"),          # en double : renuméroté
        ligne("C", id="ma-ligne"),    # forgé : renuméroté
        ligne("D"),                   # absent : numéroté
    ])
    ids = [p.id for p in propres]
    assert ids[0] == "P7"
    assert len(set(ids)) == 4
    assert all(i.startswith("P") and i[1:].isdigit() for i in ids)


# ---------------------------------------------------------------------------
# Stockage
# ---------------------------------------------------------------------------


def test_enregistrer_puis_lire_par_compte(monkeypatch):
    grilles.enregistrer("artisan-1", [ligne()])
    grilles.enregistrer("artisan-2", [ligne(prix="50")])

    assert grilles.lire("artisan-1").lignes[0].prix_unitaire_ht == Decimal("42.00")
    assert grilles.lire("artisan-2").lignes[0].prix_unitaire_ht == Decimal("50.00")


def test_un_tarif_vide_revient_a_le_supprimer():
    grilles.enregistrer("artisan-1", [ligne()])
    assert grilles.enregistrer("artisan-1", []) is None
    assert grilles.lire("artisan-1") is None


def test_sans_firebase_le_chiffrage_ne_recoit_jamais_de_tarif():
    """« Sans identifiants Firebase, tout est inactif. » Même un tarif en mémoire ne sert
    pas : sans Firebase, tout le monde est l'utilisateur local."""
    grilles.enregistrer("artisan-1", [ligne()])
    assert grilles.pour("artisan-1") is None


def test_un_visiteur_non_connecte_n_a_pas_de_tarif(monkeypatch):
    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", '{"type": "service_account"}')
    get_config.cache_clear()
    monkeypatch.setattr(grilles, "_actif", lambda: False)
    grilles.enregistrer("local", [ligne()])

    assert grilles.pour("local") is None
    assert grilles.pour(None) is None


def test_avec_firebase_le_tarif_de_l_artisan_connecte_sert_au_chiffrage(monkeypatch):
    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", '{"type": "service_account"}')
    get_config.cache_clear()
    monkeypatch.setattr(grilles, "_actif", lambda: False)
    grilles.enregistrer("artisan-1", [ligne()])

    assert grilles.pour("artisan-1").lignes[0].prestation == "Pose de carrelage de sol"
    assert grilles.pour("artisan-2") is None


def test_firestore_recoit_des_valeurs_qu_il_sait_ranger(monkeypatch):
    """Firestore ne range pas un Decimal : le document part en JSON, et se relit."""
    ecrit = {}

    class Document:
        def set(self, donnees):
            ecrit.update(donnees)

    class Collection:
        def document(self, uid):
            return Document()

    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", '{"type": "service_account"}')
    get_config.cache_clear()
    monkeypatch.setattr(grilles, "_firestore",
                        lambda: SimpleNamespace(collection=lambda nom: Collection()))
    grilles.enregistrer("artisan-1", [ligne(prix="42,5")])

    assert ecrit["lignes"][0]["prix_unitaire_ht"] == "42.50"
    assert isinstance(ecrit["modifie_le"], str)
    assert GrillePrix(**ecrit).lignes[0].prix_unitaire_ht == Decimal("42.50")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(monkeypatch):
    """Un client HTTP avec un Firebase simulé, sans Chromium ni Firestore."""
    import httpx

    from app import authentification
    from app.authentification import Utilisateur
    from app.main import app

    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", '{"type": "service_account"}')
    get_config.cache_clear()
    monkeypatch.setattr(authentification, "verifier_jeton",
                        lambda _jeton: Utilisateur(uid="artisan-1"))
    monkeypatch.setattr(grilles, "_actif", lambda: False)

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                 headers={"Authorization": "Bearer jeton"}) as c:
        yield c


async def test_get_sans_tarif_annonce_les_unites_et_la_limite(client):
    corps = (await client.get("/api/grille")).json()
    assert corps["lignes"] == []
    assert corps["max_lignes"] == MAX_LIGNES
    assert corps["unites"] == [u.value for u in Unite]


async def test_put_enregistre_et_get_le_retrouve(client):
    corps = (await client.put("/api/grille", json={"lignes": [ligne(prix="42,5")]})).json()
    assert corps["lignes"][0]["id"] == "P1"
    assert corps["lignes"][0]["prix_unitaire_ht"] == "42.50"

    relu = (await client.get("/api/grille")).json()
    assert relu["lignes"] == corps["lignes"]
    assert relu["modifie_le"]


async def test_put_fautif_repond_400_en_francais_et_n_enregistre_rien(client):
    reponse = await client.put("/api/grille", json={"lignes": [ligne(unite="")]})
    assert reponse.status_code == 400
    assert "unité" in reponse.json()["detail"]
    assert (await client.get("/api/grille")).json()["lignes"] == []


async def test_delete_retire_le_tarif(client):
    await client.put("/api/grille", json={"lignes": [ligne()]})
    assert (await client.delete("/api/grille")).status_code == 200
    assert (await client.get("/api/grille")).json()["lignes"] == []


async def test_le_devis_de_l_artisan_connecte_est_chiffre_avec_son_tarif(client, monkeypatch):
    from app import main

    recu = {}
    extraction = DevisExtraction.model_validate_json(
        (FIXTURES / "plomberie.json").read_text(encoding="utf-8"))

    def chiffrage(transcription, grille=None):
        recu["grille"] = grille
        return extraction

    monkeypatch.setattr(main, "structure", chiffrage)
    await client.put("/api/grille", json={"lignes": [ligne()]})

    assert (await client.post("/api/devis", json={"transcription": "x"})).status_code == 200
    assert recu["grille"].lignes[0].prestation == "Pose de carrelage de sol"


async def test_un_visiteur_chiffre_sans_tarif(monkeypatch):
    """« Le compte ne conditionne jamais la dictée » : pas de jeton, pas de tarif."""
    import httpx

    from app import main

    recu = {}
    extraction = DevisExtraction.model_validate_json(
        (FIXTURES / "plomberie.json").read_text(encoding="utf-8"))

    def chiffrage(transcription, grille=None):
        recu["grille"] = grille
        return extraction

    monkeypatch.setattr(main, "structure", chiffrage)
    transport = httpx.ASGITransport(app=main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        assert (await c.post("/api/devis", json={"transcription": "x"})).status_code == 200
    assert recu["grille"] is None


# ---------------------------------------------------------------------------
# Le chiffrage : la double clé
# ---------------------------------------------------------------------------


def extraction_avec(*lignes: LigneExtraction) -> DevisExtraction:
    return DevisExtraction(client_nom=None, client_adresse=None, client_telephone=None,
                           type_travaux=None, duree_estimee=None, lignes=list(lignes),
                           taux_tva_suggere="10", notes=[])


def ligne_extraite(prix=42.0, unite="m²", ref="P1", a_valider=False) -> LigneExtraction:
    return LigneExtraction(designation="Pose de carrelage de sol", detail=None, quantite=15,
                           unite=unite, prix_unitaire_ht=prix, a_valider=a_valider,
                           ref_grille=ref)


@pytest.fixture
def anthropic_simule(monkeypatch):
    """Un faux client Anthropic : garde ce qu'on lui envoie, renvoie l'extraction voulue."""
    import anthropic

    appels = []
    reponse = {"extraction": None}

    class Messages:
        def parse(self, **options):
            appels.append(options)
            return SimpleNamespace(parsed_output=reponse["extraction"],
                                   usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    class Client:
        def __init__(self, **_options):
            self.messages = Messages()

    monkeypatch.setenv("STRUCTURATION_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle")
    get_config.cache_clear()
    monkeypatch.setattr(anthropic, "Anthropic", Client)
    return SimpleNamespace(appels=appels, reponse=reponse)


def test_le_prix_du_tarif_recopie_exactement_devient_votre_prix(anthropic_simule):
    from app.structuration import structure

    anthropic_simule.reponse["extraction"] = extraction_avec(ligne_extraite())
    sortie = structure("Pose du carrelage au sol, quinze mètres carrés.", tarif(P1, P2))

    assert sortie.lignes[0].ref_grille == "P1"
    # Le prix n'a pas été dicté, mais le garde-fou l'épargne : il vient du tarif.
    assert sortie.lignes[0].a_valider is False


@pytest.mark.parametrize("prix, unite, ref, pourquoi", [
    (42.0, "m²", "P9", "identifiant inconnu"),
    (42.0, "u", "P1", "unité différente"),
    (45.0, "m²", "P1", "prix différent du tarif"),
    (650.0, "m²", "P2", "l'identifiant d'une autre prestation"),
])
def test_une_reference_non_verifiee_est_retiree_et_la_ligne_se_relit(
        anthropic_simule, prix, unite, ref, pourquoi):
    """Python vérifie, il n'impose pas : une référence douteuse ne donne jamais un prix
    « confirmé ». La ligne redevient ordinaire, et son prix, non dicté, se relit."""
    from app.structuration import structure

    anthropic_simule.reponse["extraction"] = extraction_avec(
        ligne_extraite(prix=prix, unite=unite, ref=ref))
    sortie = structure("Pose du carrelage au sol, quinze mètres carrés.", tarif(P1, P2))

    assert sortie.lignes[0].ref_grille is None, pourquoi
    assert sortie.lignes[0].a_valider is True, pourquoi


def test_un_prix_dicte_autre_que_le_tarif_reste_le_prix_dicte(anthropic_simule):
    from app.structuration import structure

    anthropic_simule.reponse["extraction"] = extraction_avec(ligne_extraite(prix=45.0, ref=None))
    sortie = structure("Pose du carrelage au sol, quinze mètres carrés à 45.", tarif(P1))

    assert sortie.lignes[0].ref_grille is None
    assert sortie.lignes[0].a_valider is False
    assert sortie.lignes[0].prix_unitaire_ht == 45.0


def test_une_quantite_estimee_reste_a_valider_meme_au_prix_du_tarif(anthropic_simule):
    from app.structuration import structure

    anthropic_simule.reponse["extraction"] = extraction_avec(ligne_extraite(a_valider=True))
    sortie = structure("Pose du carrelage au sol.", tarif(P1))

    assert sortie.lignes[0].ref_grille == "P1"
    assert sortie.lignes[0].a_valider is True


def test_sans_tarif_toute_reference_est_retiree(anthropic_simule):
    from app.structuration import structure

    anthropic_simule.reponse["extraction"] = extraction_avec(ligne_extraite())
    sortie = structure("Pose du carrelage au sol, quinze mètres carrés.")

    assert sortie.lignes[0].ref_grille is None
    assert sortie.lignes[0].a_valider is True


def test_sans_tarif_l_appel_anthropic_est_celui_d_avant(anthropic_simule):
    from app.structuration import _prompt_systeme, structure

    anthropic_simule.reponse["extraction"] = extraction_avec()
    structure("Pose d'un lavabo.")

    assert anthropic_simule.appels[0]["system"] == _prompt_systeme()


def test_avec_tarif_le_bloc_part_a_cote_du_prompt(anthropic_simule):
    from app.structuration import _prompt_systeme, structure

    anthropic_simule.reponse["extraction"] = extraction_avec()
    structure("Pose d'un lavabo.", tarif(P1))

    systeme = anthropic_simule.appels[0]["system"]
    assert systeme[0]["text"] == _prompt_systeme()
    assert "<tarif_artisan>" in systeme[1]["text"]
    assert "P1 | Pose de carrelage de sol, fourniture client | m² | 42.00" in systeme[1]["text"]


def test_avec_tarif_openai_recoit_un_message_systeme_de_plus(monkeypatch):
    import openai

    from app.structuration import _prompt_systeme, structure

    envoyes = []
    contenu = extraction_avec().model_dump_json()

    class Completions:
        def create(self, **options):
            envoyes.append(options["messages"])
            message = SimpleNamespace(content=contenu)
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

    def faux_client(**_options):
        return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    monkeypatch.setenv("STRUCTURATION_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "cle")
    get_config.cache_clear()
    monkeypatch.setattr(openai, "OpenAI", faux_client)

    structure("Pose d'un lavabo.")
    structure("Pose d'un lavabo.", tarif(P1))

    sans, avec = envoyes
    assert [m["role"] for m in sans] == ["system", "user"]
    assert [m["role"] for m in avec] == ["system", "system", "user"]
    assert avec[0]["content"] == _prompt_systeme()
    assert "<tarif_artisan>" in avec[1]["content"]


def test_les_fixtures_ignorent_le_tarif(monkeypatch):
    """La démonstration hors-ligne rejoue un enregistrement : aucun « votre prix »."""
    from app.structuration import structure

    monkeypatch.setenv("USE_FIXTURES", "true")
    transcription = (FIXTURES / "plomberie.txt").read_text(encoding="utf-8")
    sortie = structure(transcription, tarif(P1, P2))
    assert all(l.ref_grille is None for l in sortie.lignes)


def test_ref_grille_est_exige_par_les_deux_schemas():
    """Facultatif, le champ sortirait du `required` : Claude pourrait l'omettre, et le
    tarif ne servirait jamais, sans que rien ne le dise."""
    from app.structuration import _schema_strict

    assert "ref_grille" in LigneExtraction.model_json_schema()["required"]
    strict = _schema_strict()["$defs"]["LigneExtraction"]
    assert "ref_grille" in strict["required"]
    assert "default" not in strict["properties"]["ref_grille"]


def test_les_extractions_d_avant_le_tarif_se_relisent():
    """Fixtures et journal ont été écrits sans `ref_grille` : ils restent valides."""
    for chemin in FIXTURES.glob("*.json"):
        extraction = DevisExtraction.model_validate_json(chemin.read_text(encoding="utf-8"))
        assert all(l.ref_grille is None for l in extraction.lignes)


# ---------------------------------------------------------------------------
# La correction garde « votre prix »
# ---------------------------------------------------------------------------


def test_la_reference_traverse_la_correction():
    from app.edition import Corrections, recalculer
    from app.models import Entreprise, to_devis

    entreprise = Entreprise(
        nom="Bâti Rénov", forme_juridique="SARL", metier="Rénovation", adresse="1 rue",
        code_postal_ville="75011 Paris", telephone="0", email="a@b.c", siret="1",
        code_ape="4322A", tva_intracom="FR0", assurance="A", assurance_police="1", iban="FR",
    )
    devis = to_devis(extraction_avec(ligne_extraite()), entreprise=entreprise)
    assert devis.lignes[0].ref_grille == "P1"

    brut = json.loads(devis.model_dump_json())
    corrections = Corrections(client=brut["client"], taux_tva=brut["taux_tva"],
                              lignes=brut["lignes"], notes=[])
    corrige = recalculer(devis, corrections, max_lignes=60)
    assert corrige.lignes[0].ref_grille == "P1"
