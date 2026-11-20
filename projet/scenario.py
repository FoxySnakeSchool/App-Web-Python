"""Relie la demande de l'utilisateur aux données et au moteur, et rend des résultats prêts à afficher."""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

from . import donnees, moteur

TYPES = {"exact": "exactement", "min": "au moins", "max": "au plus"}
PRIORITES = {
    "tous": "Tous les habitants éloignés comptent autant",
    "priorite": "Les zones sous-dotées en généralistes comptent double",
    "seuls": "Seules les zones sous-dotées en généralistes comptent",
    "aines": "Seuls les 65 ans et plus comptent",
}
PREUVE_RAPIDE = 3.0  # secondes laissées à la preuve d'optimalité en mode Rapide
PREUVE_SURE = 20.0  # et en mode Sûr
TOURS_SURS = 400  # tours d'encadrement en mode Sûr : un tour = un calcul sur tout le périmètre
VASTE = 4_000  # communes : au-delà, la preuve immédiate est peu probable et le mode Sûr est proposé


@dataclass
class Cadre:
    """Un périmètre et une distance : le terrain du moteur et de quoi revenir aux communes."""

    deps: tuple
    terrain: moteur.Terrain
    sites: pd.DataFrame  # communes du périmètre, dans l'ordre des sites du terrain
    cibles: np.ndarray  # rang, dans donnees.carreaux(), de chaque carreau éloigné
    habitants: float  # population totale du périmètre


@lru_cache(maxsize=3)
def cadre(deps: tuple, seuil: float) -> Cadre:
    communes, carreaux = donnees.communes(), donnees.carreaux()
    dans = communes["dep"].isin(deps).to_numpy()
    carreau_dans = dans[carreaux.commune]
    cibles = np.flatnonzero(carreau_dans & (carreaux.distance > seuil))
    sites = communes[dans].reset_index(drop=True)
    terrain = moteur.Terrain(
        seuil=float(seuil),
        sites_xyz=donnees.xyz(sites["lat"], sites["lon"]),
        sites_population=sites["population_reference"].to_numpy(dtype=np.int64),
        cibles_xyz=carreaux.xyz[cibles],
        habitants=carreaux.pop[cibles],
        aines=carreaux.pop65[cibles],
        sous_dotee=communes["sous_dotee"].to_numpy(dtype=bool)[carreaux.commune[cibles]],
    )
    return Cadre(tuple(deps), terrain, sites, cibles, float(carreaux.pop[carreau_dans].sum()))


def _cadre(territoires, seuil) -> Cadre:
    deps = donnees.deps_de(territoires)
    if not deps:
        raise ValueError("Choisissez au moins un territoire.")
    return cadre(tuple(deps), float(seuil))


@lru_cache(maxsize=1)
def _position() -> dict:
    return {code: i for i, code in enumerate(donnees.communes()["code_insee"])}


def _points(codes) -> np.ndarray:
    communes = donnees.communes()
    rangs = [_position()[c] for c in codes]
    return donnees.xyz(communes["lat"].to_numpy()[rangs], communes["lon"].to_numpy()[rangs])


def _par_commune(c: Cadre, zone: np.ndarray, limite: int = 60) -> list:
    """Habitants rapprochés d'une zone, regroupés par commune, des plus nombreux aux moins."""
    rangs = donnees.carreaux().commune[c.cibles[zone]]
    totaux = pd.Series(c.terrain.habitants[zone]).groupby(rangs).sum().sort_values(ascending=False)
    codes = donnees.communes()["code_insee"].to_numpy()
    return [[codes[i], round(float(v))] for i, v in totaux.head(limite).items() if v >= 0.5]


def libelle_regle(regle: dict) -> str:
    return f"{donnees.nom_territoire(regle['t'])} : {TYPES[regle['type']]} {regle['n']}"


# ---------------------------------------------------------------- bilan des maisons posées


def bilan(codes, territoires, seuil=10.0) -> dict:
    """Ce que rapportent les maisons posées dans les communes `codes`, seules et ensemble."""
    c = _cadre(territoires, seuil)
    codes = [code for code in codes or () if code in _position()]
    b = moteur.evaluer(c.terrain, _points(codes))
    communes = donnees.communes()
    noms_dep = donnees.departements()
    maisons = []
    for k, (code, m, zone) in enumerate(zip(codes, b["maisons"], b["zones"], strict=True), start=1):
        ligne = communes.iloc[_position()[code]]
        maisons.append(
            {
                "k": k,
                "code": code,
                "commune": ligne["commune"],
                "dep": ligne["dep"],
                "dep_nom": noms_dep[ligne["dep"]]["nom"],
                "lon": float(ligne["lon"]),
                "lat": float(ligne["lat"]),
                "hors_perimetre": ligne["dep"] not in c.deps,
                "seule": round(m["seule"]),
                "propre": round(m["propre"]),
                "ajout": round(m["ajout"]),
                "communes": _par_commune(c, zone),
            }
        )
    return {
        "seuil": float(seuil),
        "deps": list(c.deps),
        "maisons": maisons,
        "rapproches": round(b["rapproches"]),
        "rapproches_aines": round(b["rapproches_aines"]),
        "rapproches_sous_dotes": round(b["rapproches_sous_dotes"]),
        "loin": round(b["loin"]),
        "carreaux_loin": len(c.cibles),
        "habitants": round(c.habitants),
    }


def fiche(code, codes, territoires, seuil=10.0) -> dict:
    """Portrait d'une commune et ce qu'une maison y rapporte (posée ou non)."""
    if code not in _position():
        raise ValueError("Commune inconnue.")
    c = _cadre(territoires, seuil)
    communes, carreaux = donnees.communes(), donnees.carreaux()
    rang = _position()[code]
    ligne = communes.iloc[rang]
    debut, fin = np.searchsorted(carreaux.commune, [rang, rang + 1])
    pop, distance = carreaux.pop[debut:fin], carreaux.distance[debut:fin]
    codes = [x for x in codes or () if x in _position()]
    posee = code in codes
    b = moteur.evaluer(c.terrain, _points(codes if posee else [*codes, code]))
    k = codes.index(code) if posee else len(codes)
    apl = ligne["apl_generalistes_2024"]
    return {
        "code": code,
        "commune": ligne["commune"],
        "dep": ligne["dep"],
        "dep_nom": donnees.departements()[ligne["dep"]]["nom"],
        "region": donnees.REGIONS[ligne["region"]],
        "lon": float(ligne["lon"]),
        "lat": float(ligne["lat"]),
        "population": int(ligne["population_reference"]),
        "nb_msp": int(ligne["nb_msp"]),
        "apl": None if pd.isna(apl) else round(float(apl), 1),
        "loin": round(float(pop[distance > seuil].sum())),
        "distance_mediane": float(
            np.median(np.repeat(distance, np.maximum(pop.round(), 1).astype(int)))
        )
        if len(pop)
        else None,
        "aines": round(float(carreaux.pop65[debut:fin].sum())),
        "hors_perimetre": ligne["dep"] not in c.deps,
        "posee": posee,
        "k": k + 1 if posee else None,
        "seule": round(b["maisons"][k]["seule"]),
        "propre": round(b["maisons"][k]["propre"]),
        "ajout": round(b["maisons"][k]["ajout"]),
        "communes": _par_commune(c, b["zones"][k], limite=5),
        "maisons_proches": maisons_proches(code),
        "nb_communes": len(_par_commune(c, b["zones"][k], limite=10_000)),
    }


@lru_cache(maxsize=1)
def _arbre_maisons() -> cKDTree:
    m = donnees.maisons()
    return cKDTree(donnees.xyz(m["lat"], m["lon"]))


@lru_cache(maxsize=1)
def _arbre_communes() -> cKDTree:
    communes = donnees.communes()
    return cKDTree(donnees.xyz(communes["lat"], communes["lon"]))


def _nom_commune(code) -> str:
    rang = _position().get(code)
    return "" if rang is None else donnees.communes()["commune"].iloc[rang]


def maisons_proches(code, nombre: int = 3) -> list[dict]:
    """Les maisons de santé de la commune, puis les `nombre` plus proches de son centre."""
    maisons = donnees.maisons()
    point = _points([code])[0]
    cordes, rangs = _arbre_maisons().query(point, k=min(len(maisons), nombre + 12))
    ici = np.flatnonzero((maisons["code_insee"] == code).to_numpy()).tolist()
    autres = [int(i) for i in rangs if int(i) not in ici][:nombre]
    distance = dict(zip(rangs.tolist(), moteur.km(cordes).tolist(), strict=True))
    return [
        {
            "i": i,
            "nom": maisons["nom"].iloc[i].title(),
            "commune": _nom_commune(maisons["code_insee"].iloc[i]),
            "km": distance.get(
                i, float(moteur.km(np.linalg.norm(_arbre_maisons().data[i] - point)))
            ),
            "ici": i in ici,
        }
        for i in [*ici, *autres]
    ]


def _carreaux_autour(point, rayon_km: float) -> np.ndarray:
    """Rangs des carreaux à moins de `rayon_km` d'un point (toute la métropole)."""
    carreaux, debuts = donnees.carreaux(), donnees.debuts_communes()
    # Les carreaux sont rangés par commune : on ne regarde que les communes des environs.
    voisines = _arbre_communes().query_ball_point(point, moteur.corde(rayon_km + 25))
    if not voisines:
        return np.empty(0, dtype=int)
    rangs = np.concatenate([np.arange(debuts[i], debuts[i + 1]) for i in voisines])
    return rangs[np.linalg.norm(carreaux.xyz[rangs] - point, axis=1) <= moteur.corde(rayon_km)]


def habitants_autour(point, rayon_km: float) -> float:
    """Habitants dont le carreau est à moins de `rayon_km` d'un point."""
    return float(donnees.carreaux().pop[_carreaux_autour(point, rayon_km)].sum())


def desserte_maison(rang: int, seuil=10.0, limite: int = 60) -> list:
    """Habitants à portée d'une maison de santé existante, par commune, des plus nombreux aux moins."""
    if not 0 <= rang < len(donnees.maisons()):
        raise ValueError("Maison de santé inconnue.")
    carreaux = donnees.carreaux()
    rangs = _carreaux_autour(_arbre_maisons().data[rang], float(seuil))
    totaux = pd.Series(carreaux.pop[rangs]).groupby(carreaux.commune[rangs]).sum()
    codes = donnees.communes()["code_insee"].to_numpy()
    return [
        [codes[i], round(float(v))]
        for i, v in totaux.sort_values(ascending=False).head(limite).items()
        if v >= 0.5
    ]


def fiche_maison(rang: int, seuil=10.0) -> dict:
    """Portrait d'une maison de santé existante."""
    maisons = donnees.maisons()
    if not 0 <= rang < len(maisons):
        raise ValueError("Maison de santé inconnue.")
    ligne = maisons.iloc[rang]
    point = _arbre_maisons().data[rang]
    cordes, rangs = _arbre_maisons().query(point, k=min(len(maisons), 4))
    voisines = [
        (int(i), float(d)) for i, d in zip(rangs, moteur.km(cordes), strict=True) if int(i) != rang
    ][:3]
    return {
        "nom": ligne["nom"].title(),
        "finess": ligne["finess"],
        "code": ligne["code_insee"],
        "commune": _nom_commune(ligne["code_insee"]),
        "dep": ligne["dep"],
        "dep_nom": donnees.departements()[ligne["dep"]]["nom"],
        "lon": float(ligne["lon"]),
        "lat": float(ligne["lat"]),
        "habitants": round(habitants_autour(point, float(seuil))),
        "voisines": [
            {
                "i": i,
                "nom": maisons["nom"].iloc[i].title(),
                "commune": _nom_commune(maisons["code_insee"].iloc[i]),
                "km": d,
            }
            for i, d in voisines
        ],
    }


def potentiel(codes, territoires, seuil=10.0, priorite="tous") -> dict:
    """Les « points chauds » : communes où une maison de plus rapporterait le plus.

    Des communes voisines ont presque le même potentiel, puisqu'elles couvriraient les mêmes
    habitants. On ne garde donc que la meilleure dans chaque rayon.
    """
    c = _cadre(territoires, seuil)
    codes = [x for x in codes or () if x in _position()]
    couverts = moteur.evaluer(c.terrain, _points(codes))["couverts"]
    valeurs = moteur.potentiel(c.terrain, couverts, priorite)
    arbre = cKDTree(c.terrain.sites_xyz)
    ecarte = valeurs < 1
    retenus = []
    for i in np.argsort(-valeurs):
        if ecarte[i]:
            continue
        retenus.append(int(i))
        ecarte[arbre.query_ball_point(c.terrain.sites_xyz[i], moteur.corde(float(seuil)))] = True
    return {
        "idx": c.sites["idx"].to_numpy()[retenus].tolist(),
        "v": np.rint(valeurs[retenus]).astype(int).tolist(),
    }


# ---------------------------------------------------------------- proposition automatique


def _groupes(regles, c: Cadre, codes_poses) -> list:
    """Masques des sites par règle, minimums et maximums diminués des maisons déjà posées."""
    communes = donnees.communes()
    deps_poses = [communes["dep"].iloc[_position()[code]] for code in codes_poses]
    groupes, deja = [], []
    for regle in regles or ():
        concernes = [d for d in donnees.deps_de([regle["t"]]) if d in c.deps]
        nom = donnees.nom_territoire(regle["t"])
        if not concernes:
            raise ValueError(f"{nom} est en dehors du périmètre choisi.")
        n = int(regle["n"])
        mini = n if regle["type"] in ("exact", "min") else 0
        maxi = n if regle["type"] in ("exact", "max") else 10_000
        groupes.append((c.sites["dep"].isin(concernes).to_numpy(), mini, maxi))
        deja.append(sum(d in concernes for d in deps_poses))
    try:
        return moteur.bornes_groupes(groupes, deja)
    except ValueError as erreur:
        raise ValueError(f"{erreur} Retirez des maisons ou choisissez « Remplacer ».") from None


def proposer(
    nombre,
    territoires,
    codes_poses=(),
    regles=(),
    seuil=10.0,
    pop_min=0,
    exclure_equipees=False,
    espacement=0.0,
    interdites=(),
    priorite="tous",
    sur=False,
    progression=None,
) -> dict:
    """Complète les maisons déjà posées jusqu'à `nombre`, avec les meilleures communes d'accueil."""
    c = _cadre(territoires, seuil)
    if not nombre or nombre < 1:
        raise ValueError("Indiquez combien de maisons de santé implanter.")
    poses = [code for code in codes_poses or () if code in _position()]
    a_ajouter = int(nombre) - len(poses)
    if a_ajouter < 0:
        raise ValueError(
            f"{len(poses)} maisons sont déjà posées pour {nombre} demandées : "
            "augmentez le nombre ou choisissez « Remplacer »."
        )
    t = c.terrain
    points = _points(poses)
    couverts = moteur.evaluer(t, points)["couverts"]

    eligibles = t.sites_population >= int(pop_min or 0)
    if exclure_equipees:
        eligibles &= c.sites["nb_msp"].to_numpy() == 0
    eligibles &= ~c.sites["code_insee"].isin([*poses, *(interdites or ())]).to_numpy()
    espacement = float(espacement or 0)
    if espacement > 0 and len(points):
        ecart = moteur.km(moteur.cKDTree(points).query(t.sites_xyz)[0])
        eligibles &= ecart >= espacement
    groupes = _groupes(regles, c, poses)
    minimum = sum(mini for _, mini, _ in groupes)
    if minimum > a_ajouter and not _imbriquees(groupes):
        raise ValueError(
            f"La répartition demande encore {minimum} maisons pour {a_ajouter} à ajouter : "
            "augmentez le nombre ou allégez la répartition."
        )

    resultat = moteur.optimiser(
        t,
        a_ajouter,
        priorite=priorite,
        couverts=couverts,
        eligibles=eligibles,
        groupes=groupes,
        espacement=espacement,
        limite_preuve=PREUVE_SURE if sur else PREUVE_RAPIDE,
        tours_encadrement=TOURS_SURS if sur else 0,
        progression=progression,
    )
    # Les ajouts sont rangés du plus utile au moins utile, une fois les premiers posés.
    restants, ordre = list(resultat["selection"]), []
    poids = np.where(couverts, 0, t.poids(priorite))
    while restants:
        gains = np.asarray(t.couverture[restants] @ poids)
        i = restants.pop(int(np.argmax(gains)))
        ordre.append(i)
        poids[t.couverture.indices[t.couverture.indptr[i] : t.couverture.indptr[i + 1]]] = 0
    return {
        "codes": [*poses, *c.sites["code_insee"].to_numpy()[ordre].tolist()],
        "ajoutees": a_ajouter,
        "optimal": bool(resultat["optimal"]),
        "methode": resultat["methode"],
        "objectif": round(resultat["objectif"]),
        "ecart_max": None
        if resultat["majorant"] is None
        else round(resultat["majorant"] - resultat["objectif"]),
        "prouvable": not groupes and espacement == 0,
        "priorite": priorite,
    }


def _imbriquees(groupes) -> bool:
    """Deux règles se recouvrent (une région et l'un de ses départements) : la somme ne dit rien."""
    vus = None
    for masque, _, _ in groupes:
        if vus is not None and (vus & masque).any():
            return True
        vus = masque if vus is None else vus | masque
    return False


def vaste(territoires) -> bool:
    """Le périmètre est-il trop grand pour espérer une preuve immédiate ?"""
    deps = donnees.deps_de(territoires)
    return int(donnees.communes()["dep"].isin(deps).sum()) > VASTE


# ---------------------------------------------------------------- recherche de communes


def chercher_communes(texte: str, deps=None, limite: int = 25) -> list[dict]:
    """Communes dont le nom commence par (ou contient) le texte saisi."""
    texte = (texte or "").strip().casefold()
    if len(texte) < 2:
        return []
    df = donnees.communes()
    if deps is not None:
        df = df[df["dep"].isin(deps)]
    noms = df["commune"].str.casefold()
    trouves = pd.concat([df[noms.str.startswith(texte)], df[noms.str.contains(texte, regex=False)]])
    trouves = trouves.drop_duplicates("code_insee").head(limite)
    return libelles_communes(trouves["code_insee"])


def libelles_communes(codes) -> list[dict]:
    df = donnees.communes()
    # `codes` peut être une colonne pandas, qui ne se teste pas avec `or` : on la convertit d'abord.
    df = df[df["code_insee"].isin([] if codes is None else list(codes))]
    return [
        {"label": f"{c} ({d})", "value": code}
        for c, d, code in zip(df["commune"], df["dep"], df["code_insee"], strict=True)
    ]
