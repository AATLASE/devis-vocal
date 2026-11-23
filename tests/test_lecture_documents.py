"""Lire les prix de l'artisan dans le PDF d'un ancien devis, ou la photo d'un devis papier.

Ce qui doit être juste à tous les coups :

- **un document se reconnaît à ses octets**, et ce qui ne passera pas — PDF protégé,
  trop long, photo HEIC ou trop lourde, moteur qui ne lit pas les images — est refusé
  **avant** l'appel au modèle, donc sans rien coûter ;
- **le texte d'un PDF sert de contrôle** : chaque prix lu y est cherché, comme pour un
  tableau. Un scan ou une photo n'en a pas, et chaque ligne dit de relire le document ;
- **le document part au bon format** chez Claude comme chez OpenAI.

`tests/fixtures/grilles/devis_exemple.pdf` est le devis d'exemple du produit (« Atelier
Exemple », aucune donnée réelle), rendu par `app.pdf`. Le modèle est simulé.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import grilles, lecture_grille, securite
from app.config import get_config
from app.lecture_grille import GrilleLue, LectureRefusee, PrixLu, preparer_document, type_document
from app.structuration import Piece, _blocs_anthropic, _blocs_openai

PDF_EXEMPLE = (Path(__file__).parent / "fixtures" / "grilles" / "devis_exemple.pdf").read_bytes()
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def etat_neuf(monkeypatch):
    # Les documents ne se lisent qu'avec Claude ou OpenAI : Claude par défaut ici.
    monkeypatch.setenv("STRUCTURATION_PROVIDER", "anthropic")
    get_config.cache_clear()
    grilles.vider_la_memoire()
    yield
    get_config.cache_clear()
    grilles.vider_la_memoire()


def pdf(pages: int = 1, mot_de_passe: str | None = None) -> bytes:
    from pypdf import PdfWriter

    ecrivain = PdfWriter()
    for _ in range(pages):
        ecrivain.add_blank_page(width=595, height=842)
    if mot_de_passe:
        ecrivain.encrypt(mot_de_passe)
    tampon = io.BytesIO()
    ecrivain.write(tampon)
    return tampon.getvalue()


# ---------------------------------------------------------------------------
# Reconnaître et préparer
# ---------------------------------------------------------------------------


def test_les_documents_se_reconnaissent_a_leurs_octets():
    assert type_document(PDF_EXEMPLE) == "application/pdf"
    assert type_document(JPEG) == "image/jpeg"
    assert type_document(PNG) == "image/png"
    assert type_document("Prestation;Prix\n".encode()) is None


def test_une_photo_heic_est_refusee_en_disant_quoi_faire():
    with pytest.raises(LectureRefusee, match="HEIC"):
        type_document(b"\x00\x00\x00\x18ftypheic" + b"\x00" * 32)


def test_le_texte_du_pdf_sert_a_verifier_les_prix():
    piece, source = preparer_document(PDF_EXEMPLE, "application/pdf")
    assert piece.media_type == "application/pdf"
    assert piece.donnees == PDF_EXEMPLE
    assert "Atelier Exemple" in source

    lu = PrixLu(prestation="Receveur extra-plat 120×90", unite="u", prix=1243.90,
                precision=None, base="HT")
    invente = PrixLu(prestation="Faïence", unite="m²", prix=81.0, precision=None, base="HT")
    trouve, absent = lecture_grille._drapeaux([lu, invente], source, None)
    assert trouve.drapeaux == []  # « 1 243,90 » est bien dans le PDF
    assert [d.code for d in absent.drapeaux] == ["non_retrouve"]


def test_un_pdf_sans_texte_est_a_relire_sur_le_document():
    _piece, source = preparer_document(pdf(1), "application/pdf")
    assert source is None


def test_un_pdf_trop_long_est_refuse():
    with pytest.raises(LectureRefusee, match="21 pages"):
        preparer_document(pdf(21), "application/pdf")


def test_un_pdf_protege_est_refuse():
    with pytest.raises(LectureRefusee, match="mot de passe"):
        preparer_document(pdf(1, mot_de_passe="secret"), "application/pdf")


def test_un_pdf_abime_est_refuse():
    with pytest.raises(LectureRefusee, match="n'a pas pu être ouvert"):
        preparer_document(b"%PDF-1.7\n" + b"\x00" * 200, "application/pdf")


def test_une_photo_n_a_pas_de_texte_de_controle():
    piece, source = preparer_document(JPEG, "image/jpeg")
    assert piece.media_type == "image/jpeg" and source is None


def test_une_photo_trop_lourde_est_refusee():
    with pytest.raises(LectureRefusee, match="trop lourde"):
        preparer_document(b"\xff\xd8\xff" + b"\x00" * lecture_grille.MAX_IMAGE_OCTETS, "image/jpeg")


@pytest.mark.parametrize("fournisseur", ["groq", "autre"])
def test_un_moteur_qui_ne_lit_pas_les_images_refuse_les_documents(monkeypatch, fournisseur):
    monkeypatch.setenv("STRUCTURATION_PROVIDER", fournisseur)
    get_config.cache_clear()
    assert lecture_grille.documents_acceptes() is False
    with pytest.raises(LectureRefusee, match="Claude ou OpenAI"):
        preparer_document(PDF_EXEMPLE, "application/pdf")


def test_openai_lit_aussi_les_documents(monkeypatch):
    monkeypatch.setenv("STRUCTURATION_PROVIDER", "openai")
    get_config.cache_clear()
    assert lecture_grille.documents_acceptes() is True


# ---------------------------------------------------------------------------
# Le format d'envoi
# ---------------------------------------------------------------------------


def test_les_blocs_claude():
    pdf_bloc, image_bloc = _blocs_anthropic([Piece("application/pdf", b"%PDF"),
                                             Piece("image/jpeg", b"\xff\xd8\xff")])
    assert pdf_bloc["type"] == "document"
    assert pdf_bloc["source"] == {"type": "base64", "media_type": "application/pdf",
                                  "data": base64.b64encode(b"%PDF").decode()}
    assert image_bloc["type"] == "image"
    assert image_bloc["source"]["media_type"] == "image/jpeg"


def test_les_blocs_openai():
    pdf_bloc, image_bloc = _blocs_openai([Piece("application/pdf", b"%PDF", "devis.pdf"),
                                          Piece("image/png", b"\x89PNG")])
    assert pdf_bloc == {"type": "file", "file": {
        "filename": "devis.pdf",
        "file_data": "data:application/pdf;base64," + base64.b64encode(b"%PDF").decode()}}
    assert image_bloc["type"] == "image_url"
    assert image_bloc["image_url"]["url"].startswith("data:image/png;base64,")


def test_le_document_part_avant_la_consigne_chez_claude(monkeypatch):
    import anthropic

    from app.structuration import generer

    appels = []

    class Messages:
        def parse(self, **options):
            appels.append(options)
            return SimpleNamespace(parsed_output=GrilleLue(lignes=[], remarque=None),
                                   usage=SimpleNamespace(input_tokens=1, output_tokens=1))

    class Client:
        def __init__(self, **_options):
            self.messages = Messages()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "cle")
    get_config.cache_clear()
    monkeypatch.setattr(anthropic, "Anthropic", Client)

    generer(GrilleLue, systeme=["prompt"], contenu="Relève les prix.", delai=10,
            pieces=[Piece("application/pdf", b"%PDF")])
    contenu = appels[0]["messages"][0]["content"]
    assert [bloc["type"] for bloc in contenu] == ["document", "text"]
    assert contenu[1]["text"] == "Relève les prix."
    assert appels[0]["system"] == "prompt"


def test_generer_refuse_un_document_a_groq(monkeypatch):
    from app.structuration import StructurationError, generer

    monkeypatch.setenv("STRUCTURATION_PROVIDER", "groq")
    get_config.cache_clear()
    with pytest.raises(StructurationError, match="Claude ou OpenAI"):
        generer(GrilleLue, systeme=["prompt"], contenu="x", delai=10,
                pieces=[Piece("image/png", b"\x89PNG")])


# ---------------------------------------------------------------------------
# La route
# ---------------------------------------------------------------------------


@pytest.fixture
def modele(monkeypatch):
    appels = []

    def faux_generer(modele_sortie, **options):
        appels.append(options)
        return GrilleLue(lignes=[PrixLu(prestation="Receveur extra-plat 120×90", unite="u",
                                        prix=1243.90, precision=None, base="HT")],
                         remarque=None)

    monkeypatch.setattr(lecture_grille, "generer", faux_generer)
    return appels


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


async def test_un_pdf_est_lu_et_ses_prix_verifies(client, modele):
    fichier = {"fichier": ("ancien-devis.pdf", PDF_EXEMPLE, "application/pdf")}
    reponse = await client.post("/api/grille/lire", files=fichier)

    assert reponse.status_code == 200
    ligne = reponse.json()["lignes"][0]
    assert ligne["prix_unitaire_ht"] == "1243.90"
    assert ligne["drapeaux"] == []
    assert modele[0]["pieces"][0].media_type == "application/pdf"
    assert securite.compteurs()["devis_du_jour"] == 1


async def test_une_photo_est_lue_et_chaque_prix_est_a_relire(client, modele):
    fichier = {"fichier": ("photo.jpg", JPEG, "image/jpeg")}
    reponse = await client.post("/api/grille/lire", files=fichier)

    assert reponse.status_code == 200
    assert [d["code"] for d in reponse.json()["lignes"][0]["drapeaux"]] == ["non_verifiable"]


async def test_un_document_refuse_ne_coute_aucun_appel(client, modele, monkeypatch):
    monkeypatch.setenv("STRUCTURATION_PROVIDER", "groq")
    get_config.cache_clear()
    fichier = {"fichier": ("ancien-devis.pdf", PDF_EXEMPLE, "application/pdf")}
    reponse = await client.post("/api/grille/lire", files=fichier)

    assert reponse.status_code == 400
    assert modele == []
    assert securite.compteurs()["devis_du_jour"] == 0


async def test_avec_groq_un_csv_se_lit_toujours(client, modele, monkeypatch):
    monkeypatch.setenv("STRUCTURATION_PROVIDER", "groq")
    get_config.cache_clear()
    fichier = {"fichier": ("tarif.csv", "Receveur;u;1243,90\n".encode(), "text/csv")}
    assert (await client.post("/api/grille/lire", files=fichier)).status_code == 200


async def test_l_ecran_sait_si_les_documents_sont_lus(client, monkeypatch):
    assert (await client.get("/api/grille")).json()["documents_acceptes"] is True
    monkeypatch.setenv("STRUCTURATION_PROVIDER", "groq")
    get_config.cache_clear()
    assert (await client.get("/api/grille")).json()["documents_acceptes"] is False
