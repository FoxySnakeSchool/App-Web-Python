"""Fabrique projet/data/carreaux.parquet : les habitants là où ils vivent, par carreau de 200 m.

Les sources ne sont jamais modifiées. Ce script les lit et écrit un fichier neuf, dédié à l'appli.

Sources
- Insee, Filosofi 2021, données au carreau de 200 m (paru le 12/02/2026) :
  https://www.insee.fr/fr/statistiques/8735162, fichier Filosofi2021_carreaux_200m_csv.zip
- data/projets/atlas_france.parquet : communes, centres, population 2023
- data/projets/atlas_msp.parquet : maisons de santé FINESS géocodées

Ce que fait le script
1. Garde la France métropolitaine, Corse comprise.
2. Rattache chaque carreau à une commune d'aujourd'hui : arrondissements de Paris, Lyon et
   Marseille ramenés à leur commune ; communes fusionnées depuis 2021 rattachées au centre de
   commune le plus proche.
3. Recale la population : dans chaque commune, les carreaux sont multipliés pour que leur somme
   égale la population 2023. La répartition interne reste celle de 2021.
4. Ajoute un point au centre des rares communes sans carreau.
5. Calcule la distance à vol d'oiseau de chaque carreau à la maison de santé la plus proche.

Lancer depuis la racine du dépôt (le script est autonome, sans import du projet) :
    uv run --with pyproj python -I projet/preparation/carreaux.py <archive.zip>
"""

import argparse
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer
from scipy.spatial import cKDTree

RACINE = Path(__file__).resolve().parents[2]
DATA = RACINE / "data" / "projets"
SORTIE = Path(__file__).resolve().parents[1] / "data" / "carreaux.parquet"
ARRONDISSEMENTS = {"751": "75056", "693": "69123", "132": "13055"}
RAYON_TERRE = 6371.0


def xyz(lat, lon) -> np.ndarray:
    la, lo = np.radians(np.asarray(lat, dtype=float)), np.radians(np.asarray(lon, dtype=float))
    return np.column_stack((np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)))


def lire_carreaux(archive: Path) -> pd.DataFrame:
    colonnes = ["idcar_200m", "i_est_200", "lcog_geo", "ind", "ind_65_79", "ind_80p"]
    with zipfile.ZipFile(archive) as z, z.open("carreaux_200m_met.csv") as f:
        df = pd.read_csv(f, usecols=colonnes, dtype={"idcar_200m": str, "lcog_geo": str})
    # L'identifiant Inspire donne le coin sud-ouest du carreau, en mètres (EPSG:3035).
    coins = df["idcar_200m"].str.extract(r"N(\d+)E(\d+)").astype(np.int64)
    nord, est = coins[0].to_numpy(), coins[1].to_numpy()
    lon, lat = Transformer.from_crs(3035, 4326, always_xy=True).transform(est + 100, nord + 100)
    return pd.DataFrame(
        {
            "lon": lon,
            "lat": lat,
            "x1": (est // 1000).astype(np.int16),  # carreau de 1 km, pour l'affichage dézoomé
            "y1": (nord // 1000).astype(np.int16),
            "pop": df["ind"].to_numpy(dtype=float),
            "pop65": (df["ind_65_79"] + df["ind_80p"]).to_numpy(dtype=float),
            "impute": df["i_est_200"].to_numpy(dtype=np.int8),
            "code_2021": df["lcog_geo"].str[:5],
        }
    )


def rattacher(carreaux: pd.DataFrame, communes: pd.DataFrame) -> pd.Series:
    """Code de la commune actuelle de chaque carreau."""
    code = carreaux["code_2021"].copy()
    for prefixe, commune in ARRONDISSEMENTS.items():
        code[code.str.startswith(prefixe)] = commune
    inconnus = ~code.isin(communes["code_insee"])
    _, proche = cKDTree(xyz(communes["lat"], communes["lon"])).query(
        xyz(carreaux.loc[inconnus, "lat"], carreaux.loc[inconnus, "lon"])
    )
    code[inconnus] = communes["code_insee"].to_numpy()[proche]
    print(
        f"  {inconnus.sum():,} carreaux de communes fusionnées rattachés au centre le plus proche"
    )
    return code


def construire(archive: Path) -> pd.DataFrame:
    communes = pd.read_parquet(DATA / "atlas_france.parquet")
    communes = communes[communes["dep"].str.len() == 2].reset_index(drop=True)  # métropole et Corse
    maisons = pd.read_parquet(DATA / "atlas_msp.parquet")
    maisons = maisons[maisons["dep"].str.len() == 2]

    carreaux = lire_carreaux(archive)
    print(f"  {len(carreaux):,} carreaux habités, {carreaux['pop'].sum():,.0f} habitants (2021)")
    carreaux["code_insee"] = rattacher(carreaux, communes)

    population = communes.set_index("code_insee")["population_reference"]
    somme = carreaux.groupby("code_insee")["pop"].sum()
    facteur = (population.reindex(somme.index) / somme).fillna(0)
    ratio = carreaux["code_insee"].map(facteur).to_numpy()
    carreaux["pop"] *= ratio
    carreaux["pop65"] *= ratio

    # Communes sans carreau : toute la population au centre, signalée par impute = 2.
    absentes = communes[
        ~communes["code_insee"].isin(somme.index) & (communes["population_reference"] > 0)
    ]
    part_65 = carreaux["pop65"].sum() / carreaux["pop"].sum()
    centres = pd.DataFrame(
        {
            "lon": absentes["lon"].to_numpy(),
            "lat": absentes["lat"].to_numpy(),
            "x1": np.int16(-1),
            "y1": np.int16(-1),
            "pop": absentes["population_reference"].to_numpy(dtype=float),
            "pop65": absentes["population_reference"].to_numpy(dtype=float) * part_65,
            "impute": np.int8(2),
            "code_insee": absentes["code_insee"].to_numpy(),
        }
    )
    print(f"  {len(absentes)} communes sans carreau, placées à leur centre")
    carreaux = pd.concat([carreaux.drop(columns="code_2021"), centres], ignore_index=True)
    carreaux = carreaux[carreaux["pop"] > 0]

    corde, _ = cKDTree(xyz(maisons["lat"], maisons["lon"])).query(
        xyz(carreaux["lat"], carreaux["lon"])
    )
    carreaux["distance_soins_km"] = 2 * RAYON_TERRE * np.arcsin(np.clip(corde / 2, 0, 1))

    ecart = abs(carreaux["pop"].sum() - population.sum())
    if ecart > 1:
        raise ValueError(f"Le recalage ne retombe pas sur la population 2023 (écart {ecart:.0f}).")
    carreaux = carreaux.sort_values(["code_insee", "y1", "x1"], ignore_index=True)
    for colonne in ("lon", "lat", "pop", "pop65", "distance_soins_km"):
        carreaux[colonne] = carreaux[colonne].astype(np.float32)
    carreaux["code_insee"] = carreaux["code_insee"].astype("category")
    return carreaux


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("archive", type=Path, help="Filosofi2021_carreaux_200m_csv.zip")
    parser.add_argument("--sortie", type=Path, default=SORTIE)
    arguments = parser.parse_args()
    tableau = construire(arguments.archive)
    arguments.sortie.parent.mkdir(parents=True, exist_ok=True)
    tableau.to_parquet(arguments.sortie, index=False, compression="zstd")
    loin = tableau["distance_soins_km"] > 10
    print(
        f"{len(tableau):,} carreaux, {tableau['pop'].sum():,.0f} habitants (2023), "
        f"{tableau.loc[loin, 'pop'].sum():,.0f} à plus de 10 km d'une maison de santé, "
        f"{arguments.sortie.stat().st_size / 1e6:.1f} Mo"
    )
