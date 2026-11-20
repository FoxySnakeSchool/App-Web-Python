"""Plan de référence par regroupement des populations éloignées sous les mêmes quotas."""

import numpy as np
from scipy.optimize import linear_sum_assignment
from scipy.spatial.distance import cdist
from sklearn.cluster import KMeans
from threadpoolctl import threadpool_limits

from .moteur import simuler


def _centres_representatifs(candidats, nombre, seuil):
    if nombre == 0:
        return []
    if nombre > len(candidats):
        raise ValueError("Le quota dépasse le nombre de sites candidats.")
    # Les coordonnées sur la sphère évitent la discontinuité du méridien 180°.
    lat, lon = np.radians(candidats[["lat", "lon"]].to_numpy()).T
    xyz = np.column_stack([np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)])
    population = candidats["population_reference"].to_numpy()
    cible = (candidats["distance_soins_km"].to_numpy() > seuil) & (population > 0)
    if cible.sum() < nombre:
        # Des groupes supplémentaires doivent recevoir un site même si le besoin local est nul.
        cible = np.ones(len(candidats), dtype=bool)
    poids = np.maximum(population[cible], 1)
    modele = KMeans(n_clusters=nombre, n_init=5, random_state=42)
    with threadpool_limits(limits=1):
        modele.fit(xyz[cible], sample_weight=poids)
    _, indices = linear_sum_assignment(cdist(modele.cluster_centers_, xyz))
    return candidats.iloc[indices]["code_insee"].tolist()


def reference_ml(df, nombre, seuil, quotas=None, colonne_territoire="arr"):
    """Place un site près de chaque groupe, puis mesure sa couverture sur tout le périmètre."""
    quotas = {str(code): n for code, n in (quotas or {}).items()}
    if "population_reference" not in df:
        df = df.assign(population_reference=df["population_2023"])
    candidats = df.sort_values("code_insee")
    territoire = candidats[colonne_territoire].astype(str)
    sites = []
    for code, n in quotas.items():
        sites.extend(_centres_representatifs(candidats.loc[territoire == code], n, seuil))
    libres = candidats.loc[~territoire.isin(quotas)]
    sites.extend(_centres_representatifs(libres, nombre - len(sites), seuil))
    nouvelle_couverture = np.zeros(len(df), dtype=bool)
    for code in sites:
        nouvelle_couverture |= simuler(df, code, seuil)["nouveaux"].to_numpy()
    return {"gain": int(df.loc[nouvelle_couverture, "population_reference"].sum()), "sites": sites}
