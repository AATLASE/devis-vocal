"""Lire les prix de l'artisan dans une dictée ou un fichier Excel/CSV.

Ce qui doit être juste à tous les coups :

- **un fichier se reconnaît à ses octets**, pas à son nom, et ce qu'on ne sait pas lire
  est refusé avec une phrase qui dit quoi faire ;
- **chaque prix lu est confronté à la source** : introuvable, TTC, sans unité, en double
  ou déjà au tarif, la ligne le dit — et un TTC n'est jamais converti ;
- **rien n'est enregistré** par la lecture, et elle ne coûte un appel au modèle que si
  le fichier a passé les contrôles.

Le modèle est simulé : aucune clé, aucun réseau.
"""

from __future__ import annotations

import io
from datetime import datetime
from decimal import Decimal

import pytest

from app import grilles, lecture_grille, securite
from app.config import get_config
from app.lecture_grille import GrilleLue, LectureRefusee, PrixLu, texte_du_fichier
from app.models import GrillePrix, PrixArtisan, Unite


@pytest.fixture(autouse=True)
def etat_neuf():
    get_config.cache_clear()
    grilles.vider_la_memoire()
    yield
    get_config.cache_clear()
    grilles.vider_la_memoire()


def lu(prestation="Pose de carrelage de sol", unite="m²", prix=45.0, precision=None,
       base="HT") -> PrixLu:
    return PrixLu(prestation=prestation, unite=unite, prix=prix, precision=precision, base=base)


def codes(propose) -> list[str]:
    return [d.code for d in propose.drapeaux]


# ---------------------------------------------------------------------------
# Du fichier au texte
# ---------------------------------------------------------------------------


def test_un_csv_utf8_est_lu_tel_quel():
    texte = texte_du_fichier("tarif.csv", "Prestation;Unité;Prix HT\nPose carrelage;m²;45,00\n".encode())
    assert "Pose carrelage;m²;45,00" in texte


def test_le_bom_d_excel_est_retire():
    texte = texte_du_fichier("tarif.csv", "﻿Prestation;Prix\nPeinture;20\n".encode("utf-8"))
    assert texte.startswith("Prestation")


def test_un_csv_windows_1252_garde_ses_accents_et_l_euro():
    contenu = "Dépose faïence;m²;18 €\n".encode("cp1252")
    assert texte_du_fichier("tarif.csv", contenu) == "Dépose faïence;m²;18 €"


def test_un_xlsx_est_converti_en_tableau_texte_sans_45_point_0():
    from openpyxl import Workbook

    classeur = Workbook()
    feuille = classeur.active
    feuille.title = "Tarif"
    feuille.append(["Prestation", "Unité", "Prix HT"])
    feuille.append(["Pose de carrelage", "m²", 45.0])
    feuille.append(["Peinture", "m²", 19.5])
    feuille.append([None, None, None])
    autre = classeur.create_sheet("Plomberie")
    autre.append(["Mitigeur", "u", 185])
    tampon = io.BytesIO()
    classeur.save(tampon)

    texte = texte_du_fichier("tarif.xlsx", tampon.getvalue())
    assert "## Feuille « Tarif »" in texte and "## Feuille « Plomberie »" in texte
    assert "Pose de carrelage\tm²\t45" in texte
    assert "45.0" not in texte
    assert "Peinture\tm²\t19.5" in texte
    assert "Mitigeur\tu\t185" in texte


@pytest.mark.parametrize("contenu, attendu", [
    (b"", "vide"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100, ".xls"),
    (b"PK\x03\x04" + b"\x00" * 100 + b"mimetypeapplication/epub", "pas un tableau Excel"),
    # Un document n'a pas de texte à lui : la route le fait lire autrement.
    (b"%PDF-1.7\n", "document"),
    (b"\x89PNG\r\n\x1a\n", "document"),
    (b"abc\x00def", "pas un tableau lisible"),
    (b"a" * 100_001, "trop long"),
], ids=["vide", "xls", "zip", "pdf", "png", "binaire", "trop-long"])
def test_ce_qui_ne_se_lit_pas_est_refuse_en_disant_quoi_faire(contenu, attendu):
    with pytest.raises(LectureRefusee, match=attendu):
        texte_du_fichier("tarif.csv", contenu)


def test_un_faux_xlsx_ne_fait_pas_tomber_le_serveur():
    with pytest.raises(LectureRefusee, match="n'a pas pu être ouvert"):
        texte_du_fichier("tarif.xlsx", b"PK\x03\x04[Content_Types].xml" + b"\x00" * 200)


# ---------------------------------------------------------------------------
# Les drapeaux
# ---------------------------------------------------------------------------


def test_un_prix_present_dans_la_source_n_a_pas_de_drapeau():
    (propose,) = lecture_grille._drapeaux([lu()], "Pose carrelage : 45 € le m²", None)
    assert propose.drapeaux == []
    assert propose.prix_unitaire_ht == Decimal("45.00")


def test_un_prix_absent_de_la_source_est_signale():
    (propose,) = lecture_grille._drapeaux([lu(prix=47.5)], "Pose carrelage : 45 € le m²", None)
    assert codes(propose) == ["non_retrouve"]


def test_les_milliers_a_la_francaise_sont_retrouves():
    (propose,) = lecture_grille._drapeaux(
        [lu("Chauffe-eau 200 L", "u", 1243.9)], "Chauffe-eau 200 L\tu\t1 243,90 €", None)
    assert "non_retrouve" not in codes(propose)


def test_un_prix_ttc_est_signale_et_jamais_converti():
    (propose,) = lecture_grille._drapeaux([lu(prix=55.0, base="TTC")], "55 € TTC", None)
    assert codes(propose) == ["ttc"]
    assert propose.prix_unitaire_ht == Decimal("55.00")


def test_une_unite_inconnue_est_a_choisir():
    (propose,) = lecture_grille._drapeaux([lu("Sac de colle", None, 12.0)], "colle 12 le sac", None)
    assert codes(propose) == ["unite"]


def test_deux_prix_pour_la_meme_prestation_se_signalent_l_un_l_autre():
    a, b = lecture_grille._drapeaux([lu(prix=45.0), lu("pose de CARRELAGE de sol", prix=48.0)],
                                    "45 puis 48", None)
    assert "doublon" in codes(a) and "48,00 €" in a.drapeaux[0].texte
    assert "doublon" in codes(b) and "45,00 €" in b.drapeaux[0].texte


def test_une_ligne_deja_au_tarif_est_signalee():
    tarif = GrillePrix(proprietaire="u", modifie_le=datetime.now(), lignes=[
        PrixArtisan(id="P1", prestation="Pose de carrelage de sol", unite=Unite.M2,
                    prix_unitaire_ht=Decimal("42.00")),
        PrixArtisan(id="P2", prestation="Peinture des murs", unite=Unite.M2,
                    prix_unitaire_ht=Decimal("20.00")),
    ])
    autre, meme = lecture_grille._drapeaux(
        [lu(prix=45.0), lu("Peinture des murs", prix=20.0)], "45 et 20", tarif)
    assert "deja" in codes(autre) and "42,00 €" in autre.drapeaux[0].texte
    assert codes(meme) == ["deja_identique"]


def test_sans_source_la_ligne_est_a_relire_sur_le_document():
    (propose,) = lecture_grille._drapeaux([lu()], None, None)
    assert codes(propose) == ["non_verifiable"]


# ---------------------------------------------------------------------------
# La lecture, modèle simulé
# ---------------------------------------------------------------------------


@pytest.fixture
def modele(monkeypatch):
    """Remplace l'appel au modèle ; garde ce qu'on lui a envoyé."""
    appels = []
    reponse = {"lue": GrilleLue(lignes=[], remarque=None)}

    def faux_generer(modele_sortie, **options):
        appels.append(options)
        assert modele_sortie is GrilleLue
        return reponse["lue"]

    monkeypatch.setattr(lecture_grille, "generer", faux_generer)
    return type("Modele", (), {"appels": appels, "reponse": reponse})


def test_la_lecture_ecarte_les_prix_nuls_et_garde_la_remarque(modele):
    modele.reponse["lue"] = GrilleLue(lignes=[lu(), lu("Remise", "forfait", -50.0)],
                                      remarque="Prix tous HT.")
    lecture = lecture_grille.lire_texte("Pose de carrelage 45 le m², remise 50")
    assert [p.prestation for p in lecture.lignes] == ["Pose de carrelage de sol"]
    assert lecture.remarque == "Prix tous HT."
    assert modele.appels[0]["delai"] == 180
    assert "prix unitaires" in modele.appels[0]["systeme"][0]


def test_une_lecture_en_echec_dit_de_reessayer(monkeypatch):
    from app.structuration import StructurationError

    def en_panne(*_a, **_k):
        raise StructurationError("panne")

    monkeypatch.setattr(lecture_grille, "generer", en_panne)
    with pytest.raises(LectureRefusee, match="Réessayez"):
        lecture_grille.lire_texte("45 le m²")


# ---------------------------------------------------------------------------
# La route
# ---------------------------------------------------------------------------


@pytest.fixture
async def client(monkeypatch, modele):
    import httpx

    from app import authentification
    from app.authentification import Utilisateur
    from app.main import app

    monkeypatch.setenv("FIREBASE_CREDENTIALS_JSON", '{"type": "service_account"}')
    get_config.cache_clear()
    monkeypatch.setattr(authentification, "verifier_jeton",
                        lambda _jeton: Utilisateur(uid="artisan-1"))
    monkeypatch.setattr(grilles, "_actif", lambda: False)
    securite.reinitialiser()

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test",
                                 headers={"Authorization": "Bearer jeton"}) as c:
        yield c
    securite.reinitialiser()


async def test_sans_firebase_la_lecture_n_existe_pas():
    import httpx

    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        reponse = await c.post("/api/grille/lire", data={"texte": "45 le m²"})
    assert reponse.status_code == 404


async def test_une_dictee_est_lue_et_rien_n_est_enregistre(client, modele):
    modele.reponse["lue"] = GrilleLue(lignes=[lu()], remarque=None)
    reponse = await client.post("/api/grille/lire", data={"texte": "La pose de carrelage, 45 le m²."})

    assert reponse.status_code == 200
    corps = reponse.json()
    assert corps["lignes"][0]["prestation"] == "Pose de carrelage de sol"
    assert corps["lignes"][0]["prix_unitaire_ht"] == "45.00"
    assert corps["lignes"][0]["drapeaux"] == []
    # Proposé, pas enregistré : c'est l'artisan qui enregistre, après relecture.
    assert (await client.get("/api/grille")).json()["lignes"] == []
    assert securite.compteurs()["devis_du_jour"] == 1


async def test_un_fichier_csv_est_lu(client, modele):
    modele.reponse["lue"] = GrilleLue(lignes=[lu()], remarque=None)
    fichier = {"fichier": ("tarif.csv", "Pose de carrelage;m²;45\n".encode(), "text/csv")}
    reponse = await client.post("/api/grille/lire", files=fichier)

    assert reponse.status_code == 200
    assert "Pose de carrelage;m²;45" in modele.appels[0]["contenu"]


async def test_un_fichier_refuse_ne_coute_aucun_appel(client, modele):
    fichier = {"fichier": ("tarif.xls", b"\xd0\xcf\x11\xe0" + b"\x00" * 50, "application/vnd.ms-excel")}
    reponse = await client.post("/api/grille/lire", files=fichier)

    assert reponse.status_code == 400
    assert ".xlsx" in reponse.json()["detail"]
    assert modele.appels == []
    assert securite.compteurs()["devis_du_jour"] == 0


async def test_sans_dictee_ni_fichier_la_route_le_dit(client):
    reponse = await client.post("/api/grille/lire", data={"texte": "  "})
    assert reponse.status_code == 400
    assert "Dictez" in reponse.json()["detail"]


async def test_en_mode_fixtures_la_lecture_est_refusee(client, monkeypatch, modele):
    monkeypatch.setenv("USE_FIXTURES", "true")
    get_config.cache_clear()
    reponse = await client.post("/api/grille/lire", data={"texte": "45 le m²"})
    assert reponse.status_code == 400
    assert modele.appels == []


async def test_le_plafond_du_jour_vaut_aussi_pour_la_lecture(client, monkeypatch, modele):
    monkeypatch.setenv("DEVIS_PAR_JOUR", "1")
    get_config.cache_clear()
    assert (await client.post("/api/grille/lire", data={"texte": "45 le m²"})).status_code == 200
    assert (await client.post("/api/grille/lire", data={"texte": "45 le m²"})).status_code == 429


async def test_un_gros_fichier_passe_la_porte_de_la_lecture_mais_pas_celle_du_devis(client):
    """La lecture reçoit des fichiers (10 Mo) ; le reste de /api/ ne reçoit que du JSON."""
    gros = {"fichier": ("tarif.csv", b"a;1\n" * (2 * 1024 * 1024), "text/csv")}  # 8 Mo
    reponse = await client.post("/api/grille/lire", files=gros)
    assert reponse.status_code != 413  # refusé plus loin, comme trop long pour un tarif

    devis = await client.post("/api/devis", content=b"x" * (600 * 1024),
                              headers={"Content-Type": "application/json"})
    assert devis.status_code == 413
