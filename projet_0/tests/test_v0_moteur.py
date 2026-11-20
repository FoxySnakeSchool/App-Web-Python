import pandas as pd
import pytest

from projet_0.moteur import charger, simuler


def test_charmont_a_dix_kilometres():
    communes = charger()
    code = communes.loc[communes["commune"] == "Charmont-en-Beauce", "code_insee"].item()
    resultat = simuler(communes, code, 10)
    gains = resultat.loc[resultat["nouveaux"]]
    assert len(gains) == 14
    assert gains["population_2023"].sum() == 8306
    assert (resultat["distance_apres_km"] <= communes["distance_soins_km"]).all()
    assert "nouveaux" not in communes


def test_seuil_inclusif_et_distance_nulle():
    communes = pd.DataFrame(
        {
            "code_insee": ["a", "b"],
            "lat": [48.0, 48.0],
            "lon": [2.0, 2.0],
            "distance_soins_km": [10.0, 10.1],
        }
    )
    resultat = simuler(communes, "a", 10)
    assert resultat["nouveaux"].tolist() == [False, True]
    assert resultat["distance_apres_km"].tolist() == [0.0, 0.0]
    with pytest.raises(ValueError, match="INSEE"):
        simuler(communes, "inconnu", 10)
