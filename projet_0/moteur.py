"""Calcul de couverture autour d'une implantation au centre d'une commune."""

from pathlib import Path

import numpy as np
import pandas as pd

DONNEES = Path(__file__).resolve().parent / "data" / "territoire_loiret.parquet"


def charger() -> pd.DataFrame:
    """Charge les 325 communes et leurs distances aux structures recensées."""
    return pd.read_parquet(DONNEES)


def simuler(df: pd.DataFrame, code: str, seuil: float) -> pd.DataFrame:
    """Ajoute les distances simulées et les communes nouvellement dans le seuil inclusif."""
    if not np.isfinite(seuil) or seuil < 0:
        raise ValueError("Le seuil doit être une distance positive ou nulle.")
    site = df.loc[df["code_insee"] == code, ["lat", "lon"]]
    if len(site) != 1:
        raise ValueError(f"Le code INSEE {code!r} doit identifier une commune de la sélection.")

    lat_site, lon_site = np.radians(site.iloc[0].to_numpy())
    lat = np.radians(df["lat"])
    lon = np.radians(df["lon"])
    # Même formule de haversine que dans le script de préparation du Parquet.
    a = (
        np.sin((lat - lat_site) / 2) ** 2
        + np.cos(lat) * np.cos(lat_site) * np.sin((lon - lon_site) / 2) ** 2
    )
    distance_site = 2 * 6371 * np.arcsin(np.sqrt(a.clip(0, 1)))
    avant = df["distance_soins_km"]
    apres = np.minimum(avant, distance_site)
    return df.assign(distance_apres_km=apres, nouveaux=(avant > seuil) & (apres <= seuil))
