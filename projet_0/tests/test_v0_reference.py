import numpy as np

from projet_0.moteur import charger, simuler
from projet_0.reference import reference_ml


def test_reference_compare_les_memes_quotas_et_les_memes_habitants():
    communes = charger()
    communes["population_reference"] = communes["population_2023"] * 2
    plan = reference_ml(communes, 4, 10, {"451": 2})
    selection = communes[communes.code_insee.isin(plan["sites"])]
    assert len(selection) == 4
    assert (selection.arr.astype(str) == "451").sum() == 2
    couvertes = np.logical_or.reduce(
        [simuler(communes, code, 10)["nouveaux"] for code in plan["sites"]]
    )
    assert plan["gain"] == communes.loc[couvertes, "population_reference"].sum()
