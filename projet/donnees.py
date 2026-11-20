"""Lecture des données préparées : communes, carreaux de population, maisons, contours.

Périmètre : France métropolitaine, Corse comprise.
"""

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

RACINE = Path(__file__).resolve().parent
DATA = RACINE.parent / "data" / "projets"
COMMUNES = DATA / "atlas_france.parquet"
MSP = DATA / "atlas_msp.parquet"
CONTOURS = DATA / "atlas_contours.zip"
CARREAUX = RACINE / "data" / "carreaux.parquet"
DEPARTEMENTS = RACINE / "assets" / "geo" / "departements.geojson"

REGIONS = {
    "11": "Île-de-France",
    "24": "Centre-Val de Loire",
    "27": "Bourgogne-Franche-Comté",
    "28": "Normandie",
    "32": "Hauts-de-France",
    "44": "Grand Est",
    "52": "Pays de la Loire",
    "53": "Bretagne",
    "75": "Nouvelle-Aquitaine",
    "76": "Occitanie",
    "84": "Auvergne-Rhône-Alpes",
    "93": "Provence-Alpes-Côte d'Azur",
    "94": "Corse",
}
FRANCE = "FR"
SEUIL_APL = 2.5  # consultations par an et par habitant : en dessous, zone sous-dotée (DREES)
RAYON_TERRE = 6371.0


def xyz(lat, lon) -> np.ndarray:
    """Points sur la sphère unité : la distance au sol se lit sur la corde entre deux points."""
    la, lo = np.radians(np.asarray(lat, dtype=float)), np.radians(np.asarray(lon, dtype=float))
    return np.column_stack((np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)))


@lru_cache(maxsize=1)
def departements() -> dict[str, dict]:
    """Code du département -> nom et code de sa région (métropole seulement)."""
    contours = json.loads(DEPARTEMENTS.read_text(encoding="utf-8"))
    return {
        f["properties"]["code"]: {
            "nom": f["properties"]["nom"],
            "region": f["properties"]["region"],
        }
        for f in contours["features"]
        if f["properties"]["region"] in REGIONS
    }


@lru_cache(maxsize=1)
def communes() -> pd.DataFrame:
    """Les communes de métropole, triées par code INSEE : la position sert d'identifiant."""
    deps = departements()
    df = pd.read_parquet(COMMUNES)
    df = df[df["dep"].isin(deps)].sort_values("code_insee", ignore_index=True)
    df["region"] = df["dep"].map(lambda d: deps[d]["region"])
    df["sous_dotee"] = df["apl_generalistes_2024"] < SEUIL_APL
    df["idx"] = df.index
    return df


@lru_cache(maxsize=1)
def maisons() -> pd.DataFrame:
    df = pd.read_parquet(MSP, columns=["finess", "nom", "dep", "code_insee", "lat", "lon"])
    return df[df["dep"].isin(departements())].reset_index(drop=True)


@lru_cache(maxsize=1)
def debuts_communes() -> np.ndarray:
    """Rang du premier carreau de chaque commune (les carreaux sont triés par commune)."""
    return np.searchsorted(carreaux().commune, np.arange(len(communes()) + 1))


@lru_cache(maxsize=1)
def _cases_5km():
    c = carreaux()
    cle = (c.x1.astype(np.int64) // 5) * 100_000 + c.y1 // 5
    isoles = c.x1 < 0  # communes sans carreau : chacune garde son point
    cle[isoles] = -np.arange(1, isoles.sum() + 1)
    _, premier, case = np.unique(cle, return_index=True, return_inverse=True)
    return np.ravel(case), communes()["dep"].to_numpy()[c.commune[premier]]


@lru_cache(maxsize=16)
def carreaux_5km(seuil: float) -> pd.DataFrame:
    """Vue France : par case de 5 km, les habitants loin des soins (placés à leur barycentre)
    et les autres. Recalculé pour chaque distance d'accès."""
    c = carreaux()
    case, dep = _cases_5km()
    n = len(dep)
    loin = np.where(c.distance > seuil, c.pop, 0.0)
    pop_loin = np.bincount(case, weights=loin, minlength=n)
    pop = np.bincount(case, weights=c.pop, minlength=n)
    # Position : barycentre des habitants loin s'il y en a, sinon de tous les habitants.
    poids = np.where((pop_loin > 0)[case], loin, c.pop)
    total = np.bincount(case, weights=poids, minlength=n)
    return pd.DataFrame(
        {
            "lon": np.bincount(case, weights=c.lon * poids, minlength=n) / total,
            "lat": np.bincount(case, weights=c.lat * poids, minlength=n) / total,
            "loin": pop_loin,
            "proche": pop - pop_loin,
            "dep": dep,
        }
    )


@dataclass(frozen=True)
class Carreaux:
    """Les habitants par carreau de 200 m, en colonnes alignées."""

    lon: np.ndarray
    lat: np.ndarray
    pop: np.ndarray  # habitants, recalés sur la population communale 2023
    pop65: np.ndarray  # dont 65 ans et plus
    distance: np.ndarray  # km à vol d'oiseau jusqu'à la maison de santé la plus proche
    commune: np.ndarray  # position de la commune dans communes()
    impute: (
        np.ndarray
    )  # 1 : valeur estimée par l'Insee ; 2 : commune sans carreau, placée au centre
    x1: np.ndarray
    y1: np.ndarray
    xyz: np.ndarray


@lru_cache(maxsize=1)
def carreaux() -> Carreaux:
    df = pd.read_parquet(CARREAUX)
    position = pd.Series(communes().index, index=communes()["code_insee"])
    return Carreaux(
        lon=df["lon"].to_numpy(),
        lat=df["lat"].to_numpy(),
        pop=df["pop"].to_numpy(dtype=np.float64),
        pop65=df["pop65"].to_numpy(dtype=np.float64),
        distance=df["distance_soins_km"].to_numpy(),
        commune=position.reindex(df["code_insee"].astype(str)).to_numpy(dtype=np.int32),
        impute=df["impute"].to_numpy(),
        x1=df["x1"].to_numpy(),
        y1=df["y1"].to_numpy(),
        xyz=xyz(df["lat"], df["lon"]),
    )


@lru_cache(maxsize=1)
def carreaux_1km() -> pd.DataFrame:
    """Regroupement par carreau de 1 km, pour l'affichage dézoomé (positions pondérées)."""
    c = carreaux()
    df = pd.DataFrame(
        {
            "cle": c.x1.astype(np.int64) * 100_000 + c.y1,
            "pop": c.pop,
            "lon": c.lon * c.pop,
            "lat": c.lat * c.pop,
            "distance": c.distance * c.pop,
            "dep": communes()["dep"].to_numpy()[c.commune],
        }
    )
    # Les communes sans carreau (x1 = -1) gardent chacune leur point.
    isoles = c.x1 < 0
    df.loc[isoles, "cle"] = -np.arange(1, isoles.sum() + 1)
    groupes = df.groupby("cle", sort=False)
    gros = groupes[["pop", "lon", "lat", "distance"]].sum()
    for colonne in ("lon", "lat", "distance"):
        gros[colonne] /= gros["pop"]
    gros["dep"] = groupes["dep"].first()
    return gros.reset_index(drop=True)


def tranche_departement(dep: str) -> slice:
    """Rangs des carreaux d'un département (communes et carreaux sont triés par code INSEE)."""
    rangs = np.flatnonzero(communes()["dep"].to_numpy() == dep)
    if not len(rangs):
        raise KeyError(dep)
    debut, fin = np.searchsorted(carreaux().commune, [rangs[0], rangs[-1] + 1])
    return slice(int(debut), int(fin))


def deps_de(territoires) -> list[str]:
    """Traduit « FR », « R:24 » ou « D:45 » en liste triée de codes de département."""
    deps = departements()
    retenus = set()
    for t in territoires or []:
        if t == FRANCE:
            return sorted(deps)
        genre, _, code = t.partition(":")
        if genre == "R":
            retenus |= {d for d, infos in deps.items() if infos["region"] == code}
        elif genre == "D" and code in deps:
            retenus.add(code)
    return sorted(retenus)


def nom_territoire(territoire: str) -> str:
    if territoire == FRANCE:
        return "France entière"
    genre, _, code = territoire.partition(":")
    return REGIONS[code] if genre == "R" else departements()[code]["nom"]


def options_territoires(dans=None) -> list[dict]:
    """Options du sélecteur : France, régions, départements (limitées à un périmètre si donné)."""
    deps = departements()
    permis = set(dans) if dans is not None else set(deps)
    regions = sorted(
        (code for code in REGIONS if any(deps[d]["region"] == code for d in permis)),
        key=REGIONS.get,
    )
    options = [] if dans is not None else [{"label": "France entière", "value": FRANCE}]
    options += [{"label": f"Région · {REGIONS[r]}", "value": f"R:{r}"} for r in regions]
    options += [
        {"label": f"{d} · {deps[d]['nom']}", "value": f"D:{d}"} for d in sorted(permis & set(deps))
    ]
    return options


@lru_cache(maxsize=16)
def contours(dep: str) -> dict:
    """Contours communaux d'un département, lus dans l'archive locale."""
    if dep not in departements():
        raise KeyError(dep)
    with ZipFile(CONTOURS) as archive:
        return json.loads(archive.read(f"{dep}.json"))
