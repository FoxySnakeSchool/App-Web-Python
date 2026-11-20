"""Lecture locale des sources nationales. Seuls les départements choisis sont transmis."""

import json
from functools import lru_cache
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd

DATA = Path(__file__).resolve().parent / "data"
COMMUNES = DATA / "atlas_france.parquet"
CONTOURS = DATA / "atlas_contours.zip"
MSP = DATA / "atlas_msp.parquet"


def departements():
    if not COMMUNES.is_file() or not CONTOURS.is_file():
        return []
    with ZipFile(CONTOURS) as archive:
        noms = json.loads(archive.read("departements.json"))
    codes = pd.read_parquet(COMMUNES, columns=["dep"])["dep"].unique()
    return [
        {"label": f"{code} · {noms[code]}", "value": code} for code in sorted(codes) if code in noms
    ]


def charger_national(deps):
    if not deps or not COMMUNES.is_file():
        return pd.DataFrame()
    return pd.read_parquet(COMMUNES, filters=[("dep", "in", list(deps))])


def msp_existantes(deps):
    colonnes = ["dep", "lat", "lon", "nom"]
    if not deps or not MSP.is_file():
        return pd.DataFrame(columns=colonnes)
    return pd.read_parquet(MSP, columns=colonnes, filters=[("dep", "in", list(deps))])


@lru_cache(maxsize=8)
def contours_nationaux(dep):
    with ZipFile(CONTOURS) as archive:
        return json.loads(archive.read(f"{dep}.json"))


@lru_cache(maxsize=12)
def emprises_communales(dep):
    """Bornes géographiques réelles des communes, y compris toutes leurs îles."""
    if dep == "cours":
        contours = json.loads((DATA / "risques_communes.geojson").read_text(encoding="utf-8"))
    else:
        contours = contours_nationaux(dep)
    emprises = {}
    for commune in contours["features"]:
        geometrie = commune["geometry"]
        polygones = geometrie["coordinates"]
        if geometrie["type"] == "Polygon":
            polygones = [polygones]
        contour = np.concatenate([polygone[0] for polygone in polygones])
        ouest, sud = contour[:, :2].min(axis=0)
        est, nord = contour[:, :2].max(axis=0)
        emprises[commune["id"]] = (sud, ouest, nord, est)
    return emprises
