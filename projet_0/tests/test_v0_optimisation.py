from itertools import combinations
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from projet_0 import optimisation
from projet_0.moteur import charger, simuler
from projet_0.optimisation import optimiser


@pytest.fixture
def territoire():
    return pd.DataFrame(
        {
            "code_insee": list("abcdefg"),
            "commune": list("ABCDEFG"),
            "arr": [1, 1, 1, 2, 2, 2, 3],
            "lat": np.zeros(7),
            "lon": [0, 0.01, 0.021, 0.032, 0.04, 0.056, 0.065],
            "distance_soins_km": [0, 4, 4, 4, 4, 4, 4],
            "population_2023": [15, 7, 8, 9, 11, 8, 10],
        }
    )


@pytest.mark.parametrize("quotas", [None, {1: 1}, {"1": 0, 2: 1}, {3: 1}])
def test_optimum_par_enumeration(territoire, quotas):
    k, seuil = 2, 1.5
    reference = []
    for sites in combinations(territoire.itertuples(), k):
        if any(sum(str(s.arr) == str(t) for s in sites) != n for t, n in (quotas or {}).items()):
            continue
        gagnantes = np.logical_or.reduce(
            [simuler(territoire, s.code_insee, seuil)["nouveaux"] for s in sites]
        )
        reference.append(int(territoire.loc[gagnantes, "population_2023"].sum()))
    resultat = optimiser(territoire, k, seuil, quotas)
    assert resultat == optimiser(territoire.sample(frac=1, random_state=2), k, seuil, quotas)
    assert resultat["optimal"]
    assert resultat["gain_total"] == max(reference) == resultat["borne_superieure"]
    assert len({s["code"] for s in resultat["sites"]}) == k
    assert sum(s["gain_marginal"] for s in resultat["sites"]) == max(reference)
    for t, n in (quotas or {}).items():
        assert sum(s["territoire"] == str(t) for s in resultat["sites"]) == n


def test_loiret_un_site():
    resultat = optimiser(charger(), 1, 10)
    assert resultat["sites"][0]["commune"] == "Charmont-en-Beauce"
    assert (resultat["gain_total"], resultat["communes_gagnees"]) == (8306, 14)


def test_population_reference_multimillesime(territoire):
    national = territoire.assign(
        population_reference=territoire["population_2023"] * 2, population_2023=None
    )
    reference = optimiser(territoire, 2, 1.5)
    resultat = optimiser(national, 2, 1.5)
    assert resultat["population_hors_seuil"] == 2 * reference["population_hors_seuil"]
    assert resultat["gain_total"] == 2 * reference["gain_total"]


def test_repli_et_borne(territoire, monkeypatch):
    appels = []

    def interrompre(*args):
        appels.append(True)
        return SimpleNamespace(success=False)

    monkeypatch.setattr(optimisation, "_resoudre", interrompre)
    resultat = optimiser(territoire, 2, 1.5, {1: 1, 2: 1})
    assert appels and not resultat["optimal"]
    assert sorted(s["territoire"] for s in resultat["sites"]) == ["1", "2"]
    assert resultat["borne_superieure"] >= resultat["gain_total"]


def test_aucun_gain_ou_aucun_site(territoire):
    assert optimiser(territoire, 0, 1)["sites"] == []
    r = optimiser(territoire, 2, 20, {3: 1})
    assert r["gain_total"] == 0 and r["optimal"]
    assert len(r["sites"]) == 2


@pytest.mark.parametrize(
    ("variables", "duale", "attendu", "borne"),
    [
        ([0, 0, 0.99999999, 0, 0, 1, 0], -44.01, 42, 45),
        ([0, 0, 1, 0, 0, 1, 0], -42, 42, 42),
        ([0, 0, 1, 1, 0, 0, 0], -45, 35, 45),
        ([1, 0, 0, 0, 0, 1, 0], -45, 35, 45),
        ([0, 0, 0.8, 0, 0, 1, 0], -45, 35, 45),
        ([0, 0, 1, 0, 0, 1, 1], -45, 35, 45),
        ([0, 0, 1, 0, 0, 0, 1], -45, 35, 45),
        ([0, 0, 1, 0, 0, 1, 0], -10, 42, 52),
    ],
)
def test_solution_intermediaire(territoire, monkeypatch, variables, duale, attendu, borne):
    def interrompre(*args):
        return SimpleNamespace(success=False, status=1, x=variables, mip_dual_bound=duale)

    monkeypatch.setattr(optimisation, "_resoudre", interrompre)
    resultat = optimiser(territoire, 2, 1.5, {1: 1, 2: 1})
    assert resultat["gain_total"] == attendu
    assert resultat["borne_superieure"] == borne
    assert resultat["optimal"] == (attendu == borne)
    assert resultat["methode"] == (
        "Solution intermédiaire (MILP)" if attendu == 42 else "Glouton sous contraintes"
    )


@pytest.mark.parametrize("quotas", [{1: 3}, {1: -1}, {3: 2}, {4: 0}, {1: 0, 2: 0}])
def test_contraintes_impossibles(territoire, quotas):
    with pytest.raises(ValueError):
        optimiser(territoire, 2, 1.5, quotas)
