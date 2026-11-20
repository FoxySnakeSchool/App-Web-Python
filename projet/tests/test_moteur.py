"""Le moteur compte chaque habitant une fois et respecte chaque critère."""

import numpy as np
import pytest

from projet.donnees import xyz
from projet.moteur import Terrain, bornes_groupes, evaluer, optimiser, potentiel

# Sites candidats (communes) : nom, latitude, longitude, population. 0,1° de latitude = 11 km.
SITES = [
    ("A1", 48.00, 2.00, 5000),
    ("A2", 48.05, 2.00, 800),
    ("A3", 48.00, 2.06, 600),
    ("A4", 48.50, 2.00, 900),
    ("B1", 49.50, 3.00, 2000),
    ("B2", 49.55, 3.00, 300),
]
# Carreaux éloignés : latitude, longitude, habitants, dont 65 ans et plus, zone sous-dotée.
CARREAUX = [
    (48.00, 2.00, 5000, 500, False),
    (48.05, 2.00, 800, 400, True),
    (48.00, 2.06, 600, 60, False),
    (48.50, 2.00, 900, 90, False),
    (49.50, 3.00, 2000, 200, True),
    (49.55, 3.00, 300, 250, False),
]
NOMS = [s[0] for s in SITES]
DEP_A = np.array([n.startswith("A") for n in NOMS])
DEP_B = ~DEP_A


@pytest.fixture
def terrain():
    sites, carreaux = np.array([s[1:] for s in SITES], dtype=float), np.array(CARREAUX, dtype=float)
    return Terrain(
        seuil=10.0,
        sites_xyz=xyz(sites[:, 0], sites[:, 1]),
        sites_population=sites[:, 2].astype(np.int64),
        cibles_xyz=xyz(carreaux[:, 0], carreaux[:, 1]),
        habitants=carreaux[:, 2],
        aines=carreaux[:, 3],
        sous_dotee=carreaux[:, 4].astype(bool),
    )


def noms(resultat):
    return {NOMS[i] for i in resultat["selection"]}


def test_bilan_seule_en_propre_et_ensemble(terrain):
    # A1 et A2 se recouvrent : leurs trois carreaux voisins sont dans les deux rayons.
    b = evaluer(terrain, terrain.sites_xyz[[0, 1, 4]])
    a1, a2, b1 = b["maisons"]
    assert a1["seule"] == a2["seule"] == 6400
    assert a1["propre"] == a2["propre"] == 0  # chacune est doublée par l'autre
    assert (a1["ajout"], a2["ajout"]) == (6400, 0)  # la seconde n'ajoute rien
    assert b1["seule"] == b1["propre"] == b1["ajout"] == 2300
    assert b["rapproches"] == 8700 == sum(m["ajout"] for m in b["maisons"])
    assert b["loin"] == 9600
    assert b["rapproches_sous_dotes"] == 2800


def test_retirer_une_maison_fait_perdre_son_apport_propre(terrain):
    toutes = evaluer(terrain, terrain.sites_xyz[[0, 3, 4]])
    sans_a4 = evaluer(terrain, terrain.sites_xyz[[0, 4]])
    assert toutes["rapproches"] - sans_a4["rapproches"] == toutes["maisons"][1]["propre"] == 900


def test_potentiel_tient_compte_des_maisons_deja_posees(terrain):
    libre = potentiel(terrain)
    assert NOMS[int(np.argmax(libre))] in {"A1", "A2", "A3"}
    assert libre.max() == 6400
    apres = potentiel(terrain, evaluer(terrain, terrain.sites_xyz[[0]])["couverts"])
    assert apres[:3].max() == 0
    assert NOMS[int(np.argmax(apres))] in {"B1", "B2"}


def test_optimum_prouve_et_recouvrements_evites(terrain):
    r = optimiser(terrain, 2)
    assert len(noms(r) & {"A1", "A2", "A3"}) == 1
    assert len(noms(r) & {"B1", "B2"}) == 1
    assert r["objectif"] == 8700
    assert r["optimal"]
    assert r["methode"] == "prouvee"


def test_repartition_minimum_et_maximum(terrain):
    interdit_a = optimiser(terrain, 2, groupes=[(DEP_A, 0, 0)], tours_encadrement=50)
    assert noms(interdit_a) == {"B1", "B2"}
    assert interdit_a["optimal"]  # l'encadrement connaît la répartition
    deux_en_a = optimiser(terrain, 2, groupes=[(DEP_A, 2, 2)])
    assert "A4" in noms(deux_en_a)
    assert deux_en_a["objectif"] == 7300
    with pytest.raises(ValueError):
        optimiser(terrain, 3, groupes=[(DEP_B, 3, 3)])


def test_filtres_de_commune_d_accueil(terrain):
    grandes = terrain.sites_population >= 1000
    r = optimiser(terrain, 2, eligibles=grandes)
    assert noms(r) == {"A1", "B1"}
    with pytest.raises(ValueError, match="Pas assez"):
        optimiser(terrain, 3, eligibles=grandes)


def test_espacement_entre_deux_maisons(terrain):
    r = optimiser(terrain, 2, groupes=[(DEP_A, 2, 2)], espacement=8)
    assert "A4" in noms(r)
    sans_a4 = np.array([n != "A4" for n in NOMS])
    with pytest.raises(ValueError, match="incompatibles"):
        optimiser(terrain, 2, eligibles=sans_a4, groupes=[(DEP_A, 2, 2)], espacement=8)


def test_priorites(terrain):
    assert noms(optimiser(terrain, 1, priorite="seuls")) <= {"B1", "B2"}
    assert optimiser(terrain, 1, priorite="aines")["objectif"] == 960  # 500 + 400 + 60
    with pytest.raises(ValueError, match="Priorité"):
        optimiser(terrain, 1, priorite="autre")


def test_completer_ne_recompte_pas_ce_qui_est_couvert(terrain):
    couverts = evaluer(terrain, terrain.sites_xyz[[0]])["couverts"]
    r = optimiser(terrain, 1, couverts=couverts, eligibles=np.arange(len(NOMS)) != 0)
    assert noms(r) <= {"B1", "B2"}
    assert r["objectif"] == 2300


def test_encadrement_borne_et_ameliore(terrain):
    suivi = []
    r = optimiser(
        terrain,
        2,
        limite_preuve=0,
        tours_encadrement=80,
        progression=suivi.append,
    )
    assert r["methode"] == "encadree"
    assert r["objectif"] == 8700
    assert r["majorant"] >= r["objectif"]
    assert suivi[-1]["part"] == 1.0
    assert suivi[-1]["ecart"] < 0.5
    assert all(0 <= etat["part"] <= 1 for etat in suivi)


def test_bornes_diminuees_des_maisons_posees():
    assert bornes_groupes([(DEP_A, 2, 3)], [1]) == [(DEP_A, 1, 2)]
    with pytest.raises(ValueError, match="déjà plus"):
        bornes_groupes([(DEP_A, 0, 1)], [2])
