"""Le scénario complet, sur les données réelles : poser, évaluer, compléter."""

import pytest

from projet import donnees, scenario

pytestmark = pytest.mark.skipif(not donnees.CARREAUX.is_file(), reason="carreaux non préparés")

LOIRET = ["D:45"]
LOIRET_IDF = ["D:45", "R:11"]
REPARTITION = [{"t": "D:45", "type": "exact", "n": 4}, {"t": "R:11", "type": "exact", "n": 2}]
PITHIVIERS = "45252"


def test_les_carreaux_retombent_sur_la_population_2023():
    communes, carreaux = donnees.communes(), donnees.carreaux()
    assert carreaux.pop.sum() == pytest.approx(communes["population_reference"].sum(), abs=10)
    assert set(communes["dep"]) == set(donnees.departements())
    assert all(len(d) == 2 for d in donnees.departements())  # pas d'outre-mer


def test_bilan_vide_puis_une_maison():
    vide = scenario.bilan([], LOIRET)
    assert vide["rapproches"] == 0
    assert 0 < vide["loin"] < vide["habitants"]
    un = scenario.bilan([PITHIVIERS], LOIRET)
    maison = un["maisons"][0]
    assert maison["seule"] == maison["propre"] == maison["ajout"] == un["rapproches"]
    assert sum(h for _, h in maison["communes"]) <= maison["seule"] + len(maison["communes"])


def test_quatre_maisons_dans_le_loiret_et_deux_en_ile_de_france():
    p = scenario.proposer(6, LOIRET_IDF, regles=REPARTITION, sur=True)
    deps = [donnees.communes().set_index("code_insee").loc[c, "dep"] for c in p["codes"]]
    assert deps.count("45") == 4
    assert len(deps) == 6
    assert p["optimal"]
    b = scenario.bilan(p["codes"], LOIRET_IDF)
    assert b["rapproches"] == pytest.approx(p["objectif"], abs=2)
    assert sum(m["ajout"] for m in b["maisons"]) == pytest.approx(b["rapproches"], abs=6)
    libre = scenario.proposer(6, LOIRET_IDF)
    assert libre["objectif"] >= p["objectif"]


def test_completer_garde_les_maisons_posees():
    p = scenario.proposer(3, LOIRET, codes_poses=[PITHIVIERS])
    assert p["codes"][0] == PITHIVIERS
    assert len(set(p["codes"])) == 3
    assert p["ajoutees"] == 2
    with pytest.raises(ValueError, match="déjà posées"):
        scenario.proposer(1, LOIRET, codes_poses=p["codes"])


def test_le_mode_sur_ne_fait_jamais_moins_bien():
    rapide = scenario.proposer(4, ["R:24"])
    sur = scenario.proposer(4, ["R:24"], sur=True)
    assert sur["objectif"] >= rapide["objectif"]
    assert sur["ecart_max"] is not None
    assert sur["ecart_max"] <= 0.01 * sur["objectif"]


def test_un_rayon_plus_court_laisse_plus_d_habitants_loin():
    assert scenario.bilan([], LOIRET, 5)["loin"] > scenario.bilan([], LOIRET, 15)["loin"]


def test_fiche_d_une_commune():
    fiche = scenario.fiche(PITHIVIERS, [], LOIRET)
    assert fiche["commune"] == "Pithiviers"
    assert not fiche["posee"]
    assert len(fiche["communes"]) <= 5
    proches = fiche["maisons_proches"]
    assert proches[0]["ici"]  # Pithiviers a sa maison de santé : elle vient en premier
    assert [m["km"] for m in proches[1:]] == sorted(m["km"] for m in proches[1:])
    assert sum(not m["ici"] for m in proches) == 3
    posee = scenario.fiche(PITHIVIERS, [PITHIVIERS], LOIRET)
    assert posee["posee"]
    assert posee["k"] == 1


def test_le_potentiel_ne_garde_que_les_points_chauds():
    potentiel = scenario.potentiel([], LOIRET)
    assert potentiel["v"] == sorted(potentiel["v"], reverse=True)
    communes = donnees.communes()
    meilleure = communes["code_insee"].iloc[potentiel["idx"][0]]
    assert potentiel["v"][0] == pytest.approx(scenario.fiche(meilleure, [], LOIRET)["ajout"], abs=1)
    # Deux points chauds ne sont jamais dans le rayon l'un de l'autre.
    points = donnees.xyz(
        communes["lat"].iloc[potentiel["idx"]], communes["lon"].iloc[potentiel["idx"]]
    )
    ecarts = scenario.moteur.km(scenario.moteur.cdist(points, points))
    assert ecarts[ecarts > 0].min() > 10
    # Une maison posée au meilleur endroit fait disparaître ce point chaud.
    apres = scenario.potentiel([meilleure], LOIRET)
    assert potentiel["idx"][0] not in apres["idx"]


def test_fiche_d_une_maison_existante():
    fiche = scenario.fiche_maison(0, 10)
    assert fiche["finess"]
    assert fiche["habitants"] > 0
    assert len(fiche["voisines"]) == 3
    assert scenario.fiche_maison(0, 5)["habitants"] < fiche["habitants"]
    with pytest.raises(ValueError):
        scenario.fiche_maison(10**6)


def test_la_recherche_de_commune_rend_des_choix():
    trouvees = scenario.chercher_communes("pithiv")
    assert {"label": "Pithiviers (45)", "value": "45252"} in trouvees
    assert all("pithiv" in o["label"].casefold() for o in trouvees)
    assert scenario.chercher_communes("p") == []  # une lettre ne suffit pas
    assert scenario.chercher_communes("pithiv", deps=["18"]) == []
    assert scenario.libelles_communes(None) == []
    assert scenario.libelles_communes(["45252"]) == [{"label": "Pithiviers (45)", "value": "45252"}]


def test_la_desserte_d_une_maison_existante_se_repartit_par_commune():
    desserte = scenario.desserte_maison(0, 10)
    habitants = [h for _, h in desserte]
    assert habitants == sorted(habitants, reverse=True)
    assert len({code for code, _ in desserte}) == len(desserte)
    # Les 60 premières communes : au plus le total de la fiche, aux arrondis près.
    assert 0 < sum(habitants) <= scenario.fiche_maison(0, 10)["habitants"] + len(desserte)
    with pytest.raises(ValueError):
        scenario.desserte_maison(-1)


def test_demande_impossible_expliquee():
    with pytest.raises(ValueError, match="en dehors du périmètre"):
        scenario.proposer(2, LOIRET, regles=[{"t": "D:75", "type": "min", "n": 1}])
    with pytest.raises(ValueError, match="encore 5 maisons"):
        scenario.proposer(3, LOIRET_IDF, regles=[{"t": "D:45", "type": "min", "n": 5}])
    with pytest.raises(ValueError, match="territoire"):
        scenario.bilan([], [])
