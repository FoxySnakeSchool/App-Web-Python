"""L'assistant et les outils : noms reconnus, réglages valides, rien d'inventé. Aucun appel réseau."""

import pytest

from projet import assistant, donnees, outils

pytestmark = pytest.mark.skipif(not donnees.CARREAUX.is_file(), reason="carreaux non préparés")

ETAT = {
    "nombre": 6,
    "perimetre": ["FR"],
    "seuil": 10,
    "regles": [],
    "pop_min": 0,
    "exclure": [],
    "espacement": 0,
    "interdites": [],
    "priorite": "tous",
}


def test_territoires_par_leur_nom_ou_leur_numero():
    assert (
        outils.territoire("Loiret")
        == outils.territoire("45")
        == outils.territoire("loiret")
        == "D:45"
    )
    assert outils.territoire("Île-de-France") == outils.territoire("ile de france") == "R:11"
    assert outils.territoires(["Loiret", "France"]) == ["FR"]
    assert outils.territoires(None) == ["FR"]
    with pytest.raises(ValueError, match="Atlantide"):
        outils.territoire("Atlantide")


def test_communes_et_homonymes():
    assert outils.commune("Pithiviers") == outils.commune("45252") == "45252"
    assert outils.commune("Saint-Denis (93)") == "93066"
    with pytest.raises(ValueError, match="Plusieurs communes"):
        outils.commune("Saint-Denis")
    with pytest.raises(ValueError, match="inconnue"):
        outils.commune("Nulle-Part-sur-Loire")


def test_chaque_departement_se_deplie_sur_le_perimetre():
    regles = outils.regles(
        [{"territoire": "chaque département", "regle": "au moins", "nombre": 1}], ["R:24"]
    )
    assert [r["t"] for r in regles] == ["D:18", "D:28", "D:36", "D:37", "D:41", "D:45"]
    assert {(r["type"], r["n"]) for r in regles} == {("min", 1)}
    with pytest.raises(ValueError, match="Règle inconnue"):
        outils.regles([{"territoire": "Loiret", "regle": "environ", "nombre": 1}], ["FR"])


def test_la_reponse_du_modele_devient_des_reglages_valides():
    brut = {
        "nombre": 8,
        "territoires": ["Centre-Val de Loire"],
        "distance_km": 12,
        "repartition": [{"territoire": "chaque département", "regle": "au moins", "nombre": 1}],
        "population_minimale": 1800,
        "ecarter_communes_equipees": True,
        "espacement_km": 22,
        "communes_interdites": ["Orléans", "Atlantide"],
        "priorite": "aines",
        "repartir_de_zero": False,
        "non_pris_en_compte": ["proximité d'une gare"],
    }
    r = assistant.traduire(brut, ETAT)
    reglages = r["reglages"]
    assert (reglages["nombre"], reglages["perimetre"], reglages["seuil"]) == (8, ["R:24"], 12)
    assert len(reglages["regles"]) == 6
    assert reglages["pop_min"] == 3  # 1 800 habitants : palier le plus proche, 2 000
    assert reglages["exclure"] == ["oui"]
    assert reglages["espacement"] == 20  # le panneau avance par pas de 5 km
    assert reglages["interdites"] == ["45234"]
    assert reglages["priorite"] == "aines"
    assert "proximité d'une gare" in r["ecartes"]
    assert any("Atlantide" in e for e in r["ecartes"])  # commune inconnue : écartée et signalée
    assert "8 maisons en tout" in r["compris"]


def test_un_champ_absent_garde_sa_valeur_et_les_exces_sont_bornes():
    etat = {**ETAT, "perimetre": ["D:45"], "regles": [{"t": "D:45", "type": "min", "n": 2}]}
    r = assistant.traduire({"nombre": 500, "distance_km": 2, "priorite": "inconnue"}, etat)[
        "reglages"
    ]
    assert r["nombre"] == outils.NOMBRE_MAX
    assert r["seuil"] == outils.SEUILS[0]
    assert (r["perimetre"], r["regles"], r["priorite"]) == (
        etat["perimetre"],
        etat["regles"],
        "tous",
    )
    territoire_faux = assistant.traduire({"territoires": ["Atlantide"]}, etat)
    assert territoire_faux["reglages"]["perimetre"] == ["D:45"]
    assert any("Atlantide" in e for e in territoire_faux["ecartes"])


def test_les_outils_rendent_un_bilan_lisible_et_un_lien():
    r = outils.proposer(2, ["Loiret"])
    assert len(r["maisons"]) == 2
    assert r["habitants_rapproches"] == r["habitants_loin_avant"] - r["habitants_encore_loin"]
    assert r["preuve"].startswith("Optimum prouvé")
    lu = outils.lire_lien(r["lien_carte"].split("?", 1)[1])
    assert lu == {
        "maisons": [m["code_insee"] for m in r["maisons"]],
        "perimetre": ["D:45"],
        "seuil": 10,
    }
    assert (
        outils.bilan([m["commune"] + " (45)" for m in r["maisons"]], ["45"])["habitants_rapproches"]
        == r["habitants_rapproches"]
    )


def test_un_lien_abime_ne_casse_rien():
    assert outils.lire_lien("") == {}
    assert outils.lire_lien("?maisons=00000,abc&perimetre=Atlantide&seuil=99") == {}
    assert outils.lire_lien("?maisons=45252&seuil=15") == {"maisons": ["45252"], "seuil": 15}


def test_le_chat_remplit_le_panneau_et_lance_le_calcul(monkeypatch):
    from projet import app

    monkeypatch.setattr(
        app.assistant,
        "interpreter",
        lambda message, etat: {
            **assistant.traduire(
                {"nombre": 3, "territoires": ["Loiret"], "repartir_de_zero": True}, etat
            ),
            "modele": "faux",
        },
    )
    sortie = app.demander(
        {"texte": "3 maisons dans le Loiret"}, [], 6, ["FR"], 10, [], 0, [], 0, [], "tous"
    )
    nombre, perimetre, *_, lancer, fil = sortie
    assert (nombre, perimetre) == (3, ["D:45"])
    assert lancer["remplacer"] is True
    assert [m["qui"] for m in fil] == ["vous", "assistant"]

    def refuse(message, etat):
        raise ValueError("Assistant indisponible")

    monkeypatch.setattr(app.assistant, "interpreter", refuse)
    *reglages, fil = app.demander({"texte": "bonjour"}, [], 6, ["FR"], 10, [], 0, [], 0, [], "tous")
    assert fil[-1]["erreur"] == "Assistant indisponible"
    assert all(r is app.no_update for r in reglages)
