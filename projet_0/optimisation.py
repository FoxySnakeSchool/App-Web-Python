"""Implantations maximisant la population nouvellement couverte à vol d'oiseau."""

from math import ceil
from numbers import Integral

import numpy as np
from scipy import sparse
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.spatial import cKDTree


def _entier(valeur):
    return isinstance(valeur, Integral) and not isinstance(valeur, bool) and valeur >= 0


def _groupes(territoires, nombre, quotas):
    """Les territoires sans quota se partagent les places restantes."""
    demandes = {str(t): n for t, n in (quotas or {}).items()}
    if any(not _entier(n) for n in demandes.values()):
        raise ValueError("Les quotas doivent être des entiers positifs ou nuls.")
    if demandes.keys() - set(territoires):
        raise ValueError("Un territoire demandé est absent des données.")
    libres = nombre - sum(demandes.values())
    groupes = [(territoires == t, int(n)) for t, n in sorted(demandes.items())]
    groupes.append((~np.isin(territoires, list(demandes)), libres))
    if any(n < 0 or n > masque.sum() for masque, n in groupes):
        raise ValueError("Les quotas sont incompatibles avec le nombre de sites disponibles.")
    return groupes


def _couverture(df, hors_seuil, seuil):
    """Index sphérique des seuls centres à couvrir, sans matrice de distances dense."""
    lat, lon = np.radians(df[["lat", "lon"]].to_numpy()).T
    xyz = np.column_stack((np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)))
    cibles = xyz[hors_seuil]
    rayon = 2 * np.sin(min(seuil / 6371, np.pi) / 2)
    voisins = cKDTree(cibles).query_ball_point(xyz, np.nextafter(rayon, np.inf))
    longueurs = np.fromiter(map(len, voisins), dtype=int, count=len(df))
    indices = np.fromiter((j for ligne in voisins for j in ligne), dtype=int)
    return sparse.csr_matrix(
        (np.ones(len(indices)), indices, np.r_[0, longueurs.cumsum()]),
        shape=(len(df), len(cibles)),
    )


def _glouton(couverture, population, groupes):
    restants = population.copy()
    eligibles = np.ones(couverture.shape[0], dtype=bool)
    places = [n for _, n in groupes]
    for masque, n in groupes:
        if n == 0:
            eligibles[masque] = False
    selection = []
    for _ in range(sum(places)):
        gains = couverture @ restants
        i = int(np.argmax(np.where(eligibles, gains, -1)))
        selection.append(i)
        eligibles[i] = False
        restants[couverture.indices[couverture.indptr[i] : couverture.indptr[i + 1]]] = 0
        for j, (masque, _) in enumerate(groupes):
            if masque[i]:
                places[j] -= 1
                if places[j] == 0:
                    eligibles[masque] = False
                break
    return selection


def _resoudre(couverture, population, groupes):
    """x sélectionne les sites, y indique la couverture. y <= couverture.T @ x."""
    n, m = couverture.shape
    quotas = sparse.csr_matrix(np.array([masque for masque, _ in groupes], dtype=float))
    places = [p for _, p in groupes]
    matrice = sparse.bmat([[quotas, None], [-couverture.T, sparse.eye(m)]], format="csc")
    contraintes = LinearConstraint(
        matrice, np.r_[places, np.full(m, -np.inf)], np.r_[places, np.zeros(m)]
    )
    return milp(
        np.r_[np.zeros(n), -population],
        integrality=np.r_[np.ones(n), np.zeros(m)],
        bounds=Bounds(0, 1),
        constraints=contraintes,
        options={"time_limit": 1.5, "mip_rel_gap": 0},
    )


def _sites_realisables(solution, n, groupes):
    """Valide les décisions binaires, puis vérifie tous les quotas après arrondi."""
    if getattr(solution, "x", None) is None:
        return None
    x = np.asarray(solution.x)[:n]
    if x.shape != (n,) or not np.isfinite(x).all():
        return None
    arrondis = np.rint(x)
    if not np.allclose(x, arrondis, rtol=0, atol=1e-6) or not np.isin(arrondis, [0, 1]).all():
        return None
    if any(arrondis[masque].sum() != quota for masque, quota in groupes):
        return None
    return np.flatnonzero(arrondis).tolist()


def _gain(couverture, population, sites):
    couvertes = np.asarray(couverture[sites].sum(axis=0)).ravel() > 0
    return int(population[couvertes].sum())


def optimiser(df, nombre, seuil, quotas=None, colonne_territoire="arr"):
    """Choisit exactement nombre centres communaux, avec quotas exacts optionnels."""
    if not _entier(nombre) or nombre > len(df):
        raise ValueError(
            "Le nombre de sites doit être un entier entre zéro et le nombre de communes."
        )
    if not np.isfinite(seuil) or seuil < 0:
        raise ValueError("Le seuil doit être une distance positive ou nulle.")
    if df["code_insee"].isna().any() or not df["code_insee"].is_unique:
        raise ValueError("Chaque commune doit avoir un code INSEE unique.")
    df = df.sort_values("code_insee").reset_index(drop=True)
    territoires = df[colonne_territoire].astype(str).to_numpy()
    groupes = _groupes(territoires, nombre, quotas)
    hors_seuil = df["distance_soins_km"].to_numpy() > seuil
    colonne_population = (
        "population_reference" if "population_reference" in df else "population_2023"
    )
    population = df.loc[hors_seuil, colonne_population].to_numpy(dtype=np.int64)
    couverture = _couverture(df, hors_seuil, seuil)
    selection = _glouton(couverture, population, groupes)
    borne = int(population.sum()) if nombre else 0
    methode = "Glouton sous contraintes"
    gains_uniques = couverture @ population
    # Somme des meilleurs gains de chaque groupe, avant déduction des recouvrements.
    borne = min(
        borne, sum(int(np.sort(gains_uniques[masque])[-k:].sum()) for masque, k in groupes if k)
    )
    meilleur_gain = _gain(couverture, population, selection)
    if meilleur_gain < borne:
        solution = _resoudre(couverture, population, groupes)
        candidat = _sites_realisables(solution, len(df), groupes)
        if candidat is not None:
            gain_candidat = _gain(couverture, population, candidat)
            if gain_candidat > meilleur_gain:
                selection = candidat
                meilleur_gain = gain_candidat
                methode = (
                    "Optimisation exacte (MILP)"
                    if solution.success
                    else "Solution intermédiaire (MILP)"
                )
            if solution.success and gain_candidat == meilleur_gain:
                borne = meilleur_gain
        # La borne inférieure du minimum de -gain est une borne supérieure pour gain.
        duale = getattr(solution, "mip_dual_bound", None)
        if duale is not None and np.isfinite(duale) and -duale >= meilleur_gain - 1e-6:
            borne = min(borne, max(meilleur_gain, ceil(-duale)))

    retenus = np.zeros(len(df), dtype=bool)
    retenus[selection] = True
    restantes = np.ones(len(population), dtype=bool)
    sites = []
    for _ in selection:
        gains = couverture @ (population * restantes)
        i = int(np.argmax(np.where(retenus, gains, -1)))
        retenus[i] = False
        cibles = couverture.indices[couverture.indptr[i] : couverture.indptr[i + 1]]
        sites.append(
            {
                "code": str(df.at[i, "code_insee"]),
                "commune": str(df.at[i, "commune"]),
                "territoire": str(territoires[i]),
                "gain_marginal": int(gains[i]),
                "communes_marginales": int(restantes[cibles].sum()),
            }
        )
        restantes[cibles] = False
    gain = int(population[~restantes].sum())
    return {
        "sites": sites,
        "gain_total": gain,
        "communes_gagnees": int((~restantes).sum()),
        "population_hors_seuil": int(population.sum()),
        "communes_hors_seuil": len(population),
        "optimal": gain == borne,
        "methode": methode,
        "borne_superieure": borne,
        "ecart_max": borne - gain,
    }
