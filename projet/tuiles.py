"""Tuiles vectorielles des habitants, fabriquées à la demande (format Mapbox Vector Tile).

La carte ne demande que les tuiles visibles à l'écran : plus de fichier par département à
recharger ni de couche à reconstruire dans le navigateur. Deux niveaux : carreaux de 1 km
(« moyens ») et de 200 m (« fins »). Chaque point porte trois attributs :
- `p` : habitants (arrondis) ;
- `q` : kilomètres entiers jusqu'à la maison de santé la plus proche ;
- `dep` : code du département.
"""

import gzip
from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from . import donnees

ETENDUE = 4096  # côté d'une tuile, en unités de tuile
TAMPON = 96  # débord autour de la tuile, pour que les points des bords ne soient pas coupés
ZOOMS = {"moyens": (7, 8), "fins": (9, 10)}  # zooms servis (au-delà, la carte agrandit la tuile)


@dataclass
class Niveau:
    zmax: int
    cle: np.ndarray  # tuile de zoom zmax de chaque point, triée
    mx: np.ndarray  # position Mercator normalisée, entre 0 et 1
    my: np.ndarray
    p: np.ndarray
    q: np.ndarray
    dep: np.ndarray  # rang du département dans `deps`
    deps: list


def _mercator(lon, lat):
    lat = np.radians(np.asarray(lat, dtype=np.float64))
    return (np.asarray(lon, dtype=np.float64) + 180) / 360, (
        1 - np.arcsinh(np.tan(lat)) / np.pi
    ) / 2


def _arrondir(pop):
    """Habitants arrondis à deux chiffres significatifs : peu de valeurs distinctes par tuile."""
    pop = np.asarray(pop, dtype=np.float64)
    pas = np.where(pop < 100, 1, np.where(pop < 1000, 10, 100))
    return np.clip(np.rint(pop / pas) * pas, 0, 60_000).astype(np.int64)


@lru_cache(maxsize=2)
def niveau(nom: str) -> Niveau:
    zmax = ZOOMS[nom][1]
    if nom == "fins":
        c = donnees.carreaux()
        lon, lat, pop, distance = c.lon, c.lat, c.pop, c.distance
        codes = donnees.communes()["dep"].to_numpy()[c.commune]
    else:
        df = donnees.carreaux_1km()
        lon, lat, pop, distance = (df[k].to_numpy() for k in ("lon", "lat", "pop", "distance"))
        codes = df["dep"].to_numpy()
    deps, rang = np.unique(codes, return_inverse=True)
    mx, my = _mercator(lon, lat)
    cote = 2**zmax
    cle = np.floor(mx * cote).astype(np.int64) * cote + np.floor(my * cote).astype(np.int64)
    ordre = np.argsort(cle, kind="stable")
    return Niveau(
        zmax=zmax,
        cle=cle[ordre],
        mx=mx[ordre],
        my=my[ordre],
        p=_arrondir(pop)[ordre],
        q=np.clip(np.floor(distance), 0, 40).astype(np.int64)[ordre],
        dep=np.ravel(rang).astype(np.int64)[ordre],
        deps=deps.tolist(),
    )


def _points(n: Niveau, z: int, x: int, y: int) -> np.ndarray:
    """Rangs des points de la tuile, débord compris."""
    cote, fin = 2**z, 2**n.zmax
    marge = TAMPON / ETENDUE
    x0, x1, y0, y1 = (
        (x - marge) / cote,
        (x + 1 + marge) / cote,
        (y - marge) / cote,
        (y + 1 + marge) / cote,
    )
    # Les points sont rangés par tuile de zoom zmax : on ne lit que les colonnes concernées.
    colonnes = range(max(int(x0 * fin), 0), min(int(x1 * fin), fin - 1) + 1)
    bas, haut = max(int(y0 * fin), 0), min(int(y1 * fin), fin - 1)
    debuts = np.searchsorted(n.cle, [c * fin + bas for c in colonnes], side="left")
    fins = np.searchsorted(n.cle, [c * fin + haut for c in colonnes], side="right")
    morceaux = [np.arange(a, b) for a, b in zip(debuts, fins, strict=True) if b > a]
    if not morceaux:
        return np.empty(0, dtype=np.int64)
    rangs = np.concatenate(morceaux)
    dedans = (n.mx[rangs] >= x0) & (n.mx[rangs] < x1) & (n.my[rangs] >= y0) & (n.my[rangs] < y1)
    return rangs[dedans]


def _varint(v: int) -> bytes:
    morceaux = bytearray()
    while True:
        octet = v & 0x7F
        v >>= 7
        morceaux.append(octet | (0x80 if v else 0))
        if not v:
            return bytes(morceaux)


def _champ(numero: int, contenu: bytes) -> bytes:
    """Champ protobuf « longueur puis contenu »."""
    return _varint(numero << 3 | 2) + _varint(len(contenu)) + contenu


def _encoder(px, py, valeurs_p, rang_p, valeurs_q, rang_q, deps, rang_dep) -> bytes:
    """Une couche « h » dont chaque point est une entité : 22 octets fixes par point.

    Les entiers sont écrits sur deux octets même quand un suffirait (c'est permis par le format),
    ce qui donne des entités de taille fixe, écrites d'un bloc avec numpy.
    """
    n = len(px)
    entites = np.empty((n, 22), dtype=np.uint8)
    #                entité  attributs : clé 0 = p, clé 1 = q, clé 2 = dep        type point  géométrie
    entites[:] = [0x12, 20, 0x12, 9, 0, 0, 0, 1, 0, 0, 2, 0, 0, 0x18, 1, 0x22, 5, 9, 0, 0, 0, 0]

    def deux_octets(colonne, valeurs):
        entites[:, colonne] = (valeurs & 0x7F) | 0x80
        entites[:, colonne + 1] = valeurs >> 7

    deux_octets(5, rang_p)
    deux_octets(8, rang_q + len(valeurs_p))
    deux_octets(11, rang_dep + len(valeurs_p) + len(valeurs_q))
    deux_octets(18, (px << 1) ^ (px >> 63))  # zigzag : les coordonnées du débord sont négatives
    deux_octets(20, (py << 1) ^ (py >> 63))
    entier = lambda v: _champ(4, b"\x28" + _varint(int(v)))
    texte = lambda s: _champ(4, _champ(1, s.encode()))
    couche = (
        b"\x78\x02"  # version 2
        + _champ(1, b"h")
        + entites.tobytes()
        + b"".join(_champ(3, cle) for cle in (b"p", b"q", b"dep"))
        + b"".join(entier(v) for v in valeurs_p)
        + b"".join(entier(v) for v in valeurs_q)
        + b"".join(texte(d) for d in deps)
        + b"\x28"
        + _varint(ETENDUE)
    )
    return _champ(3, couche)


@lru_cache(maxsize=384)
def tuile(nom: str, z: int, x: int, y: int) -> bytes | None:
    """Tuile compressée, ou None s'il n'y a personne dans cette tuile."""
    zmin, zmax = ZOOMS[nom]
    if not (zmin <= z <= zmax and 0 <= x < 2**z and 0 <= y < 2**z):
        raise KeyError((nom, z, x, y))
    n = niveau(nom)
    rangs = _points(n, z, x, y)
    if not len(rangs):
        return None
    cote = 2**z
    px = np.floor((n.mx[rangs] * cote - x) * ETENDUE).astype(np.int64)
    py = np.floor((n.my[rangs] * cote - y) * ETENDUE).astype(np.int64)
    valeurs_p, rang_p = np.unique(n.p[rangs], return_inverse=True)
    valeurs_q, rang_q = np.unique(n.q[rangs], return_inverse=True)
    presents, rang_dep = np.unique(n.dep[rangs], return_inverse=True)
    corps = _encoder(
        px,
        py,
        valeurs_p,
        np.ravel(rang_p),
        valeurs_q,
        np.ravel(rang_q),
        [n.deps[d] for d in presents],
        np.ravel(rang_dep),
    )
    return gzip.compress(corps, 5)
