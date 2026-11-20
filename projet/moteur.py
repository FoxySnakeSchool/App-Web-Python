"""Combien d'habitants une maison de santé rapproche-t-elle, et où poser les suivantes ?

Les habitants sont des points (carreaux de 200 m). Un habitant est « loin » si son carreau est à
plus de `seuil` km d'une maison de santé existante, et « rapproché » si une nouvelle maison se
trouve dans ce rayon. Distances à vol d'oiseau. Un habitant couvert par plusieurs nouvelles
maisons est compté une fois.

Trois services, sans dépendance à l'interface ni aux fichiers :
- `evaluer` : le bilan d'une liste de maisons posées n'importe où ;
- `potentiel` : ce qu'une maison de plus rapporterait dans chaque commune candidate ;
- `optimiser` : les meilleures communes d'accueil sous contraintes.
"""

import time
from dataclasses import dataclass
from functools import cached_property

import numpy as np
from scipy import sparse
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
from scipy.spatial.distance import cdist

RAYON_TERRE = 6371.0
PRIORITES = ("tous", "priorite", "seuls", "aines")
TAILLE_EXACTE = (
    6_000  # sites + groupes de carreaux : au-delà, un îlot est trop gros pour le solveur
)


def corde(km: float) -> float:
    """Longueur de corde sur la sphère unité pour une distance au sol."""
    return 2 * np.sin(min(km / RAYON_TERRE, np.pi) / 2)


def km(cordes) -> np.ndarray:
    return 2 * RAYON_TERRE * np.arcsin(np.clip(np.asarray(cordes) / 2, 0, 1))


@dataclass
class Terrain:
    """Un périmètre et une distance d'accès : les habitants loin des soins et les sites possibles."""

    seuil: float
    sites_xyz: np.ndarray  # centres des communes candidates, sur la sphère unité
    sites_population: np.ndarray
    cibles_xyz: np.ndarray  # carreaux à plus de `seuil` km d'une maison existante
    habitants: np.ndarray
    aines: np.ndarray  # dont 65 ans et plus
    sous_dotee: np.ndarray  # carreau en zone sous-dotée en généralistes

    @cached_property
    def arbre(self) -> cKDTree:
        return cKDTree(self.cibles_xyz)

    @cached_property
    def couverture(self) -> sparse.csr_matrix:
        """Matrice creuse sites x carreaux : le site i couvre le carreau j."""
        voisins = self.arbre.query_ball_point(
            self.sites_xyz, np.nextafter(corde(self.seuil), np.inf)
        )
        longueurs = np.fromiter(map(len, voisins), dtype=np.int64, count=len(voisins))
        indices = np.fromiter(
            (j for v in voisins for j in v), dtype=np.int32, count=int(longueurs.sum())
        )
        return sparse.csr_matrix(
            (np.ones(len(indices), dtype=np.float32), indices, np.r_[0, longueurs.cumsum()]),
            shape=(len(self.sites_xyz), len(self.habitants)),
        )

    def autour(self, point) -> np.ndarray:
        """Carreaux éloignés dans le rayon d'un point quelconque."""
        proches = self.arbre.query_ball_point(point, np.nextafter(corde(self.seuil), np.inf))
        return np.asarray(sorted(proches), dtype=np.int64)

    def poids(self, priorite: str = "tous") -> np.ndarray:
        """Ce que vaut chaque carreau dans l'objectif du calcul automatique."""
        if priorite not in PRIORITES:
            raise ValueError(f"Priorité inconnue : {priorite!r}.")
        if priorite == "aines":
            return self.aines.astype(float)
        facteur = {"tous": 1, "priorite": 1 + self.sous_dotee, "seuls": self.sous_dotee}[priorite]
        return self.habitants * facteur


# ---------------------------------------------------------------- bilan d'une liste de maisons


def evaluer(terrain: Terrain, points) -> dict:
    """Bilan de maisons posées en `points` (sphère unité), dans l'ordre donné.

    Pour chaque maison : ce qu'elle rapprocherait seule, ce qu'elle apporte en propre (perdu si
    on la retire) et ce qu'elle ajoute au moment où elle arrive dans la liste.
    """
    h = terrain.habitants
    zones = [terrain.autour(p) for p in points]
    fois = np.zeros(len(h), dtype=np.int32)
    for zone in zones:
        fois[zone] += 1
    vus = np.zeros(len(h), dtype=bool)
    maisons, nouveaux = [], []
    for zone in zones:
        ajout = zone[~vus[zone]]
        vus[zone] = True
        nouveaux.append(ajout)
        maisons.append(
            {
                "seule": float(h[zone].sum()),
                "propre": float(h[zone][fois[zone] == 1].sum()),
                "ajout": float(h[ajout].sum()),
            }
        )
    return {
        "maisons": maisons,
        "zones": zones,  # carreaux dans le rayon de chaque maison
        "nouveaux": nouveaux,  # carreaux qu'elle est la première à couvrir, dans l'ordre de la liste
        "couverts": vus,
        "rapproches": float(h[vus].sum()),
        "rapproches_aines": float(terrain.aines[vus].sum()),
        "rapproches_sous_dotes": float(h[vus & terrain.sous_dotee].sum()),
        "loin": float(h.sum()),
    }


def potentiel(terrain: Terrain, couverts=None, priorite: str = "tous") -> np.ndarray:
    """Pour chaque site candidat, ce qu'une maison de plus y rapporterait."""
    poids = terrain.poids(priorite)
    if couverts is not None:
        poids = np.where(couverts, 0, poids)
    return np.asarray(terrain.couverture @ poids)


# ---------------------------------------------------------------- choix automatique


@dataclass
class _Probleme:
    terrain: Terrain
    nombre: int
    poids: np.ndarray
    eligibles: np.ndarray
    groupes: list  # (masque des sites, mini, maxi)
    espacement: float

    @cached_property
    def arbre_sites(self) -> cKDTree:
        return cKDTree(self.terrain.sites_xyz)

    @cached_property
    def groupe_de(self):
        """Rang de la règle de répartition de chaque site (-1 : aucune), ou None si des
        territoires se recouvrent (une région et l'un de ses départements)."""
        rang = np.full(len(self.eligibles), -1)
        for g, (masque, _, _) in enumerate(self.groupes):
            if (rang[masque] >= 0).any():
                return None
            rang[masque] = g
        return rang

    def trop_proches(self, i: int) -> list[int]:
        return self.arbre_sites.query_ball_point(
            self.terrain.sites_xyz[i], corde(self.espacement) * (1 - 1e-12)
        )

    def zone(self, i: int) -> np.ndarray:
        c = self.terrain.couverture
        return c.indices[c.indptr[i] : c.indptr[i + 1]]


def _valide(p: _Probleme, selection) -> bool:
    sel = np.asarray(selection, dtype=int)
    if len(sel) != p.nombre or len(set(sel.tolist())) != p.nombre:
        return False
    if len(sel) and not p.eligibles[sel].all():
        return False
    if any(not mini <= masque[sel].sum() <= maxi for masque, mini, maxi in p.groupes):
        return False
    if p.espacement > 0 and len(sel) > 1:
        distances = km(cdist(p.terrain.sites_xyz[sel], p.terrain.sites_xyz[sel]))
        np.fill_diagonal(distances, np.inf)
        if distances.min() < p.espacement * (1 - 1e-9):
            return False
    return True


def _gain(p: _Probleme, selection) -> float:
    if not len(selection):
        return 0.0
    couverts = np.asarray(p.terrain.couverture[list(selection)].sum(axis=0)).ravel() > 0
    return float(p.poids[couverts].sum())


def _glouton(p: _Probleme) -> list[int]:
    """À chaque étape, le site permis qui rapproche le plus d'habitants encore non couverts."""
    restants = p.poids.astype(float).copy()
    dispo = p.eligibles.copy()
    choisis: list[int] = []
    comptes = [0] * len(p.groupes)
    population = p.terrain.sites_population
    departage = population / (population.max() + 1) / 2  # < 0,5 : ne dépasse jamais un gain

    while len(choisis) < p.nombre:
        permis = dispo.copy()
        manques = np.zeros(len(dispo), dtype=int)
        for g, (masque, mini, maxi) in enumerate(p.groupes):
            if comptes[g] >= maxi:
                permis &= ~masque
            if comptes[g] < mini:
                manques += masque
        dette = sum(max(0, mini - comptes[g]) for g, (_, mini, _) in enumerate(p.groupes))
        if dette >= p.nombre - len(choisis) and permis.any():
            utile = manques[permis].max()  # plus de marge : seuls les sites qui comblent un minimum
            if utile == 0:
                break
            permis &= manques == utile
        if not permis.any():
            break
        scores = p.terrain.couverture @ restants + departage
        i = int(np.argmax(np.where(permis, scores, -1)))
        choisis.append(i)
        restants[p.zone(i)] = 0
        dispo[i] = False
        if p.espacement > 0:
            dispo[p.trop_proches(i)] = False
        for g, (masque, _, _) in enumerate(p.groupes):
            comptes[g] += int(masque[i])
    return choisis


def _echanges(p: _Probleme, selection: list[int]) -> list[int]:
    """Recherche locale : remplace un site par un meilleur tant que le gain augmente."""
    sel = list(selection)
    for _ in range(4 * max(len(sel), 1)):
        progres = False
        for s in sel:
            autres = [x for x in sel if x != s]
            libres = p.poids.astype(float).copy()
            if autres:
                libres[np.asarray(p.terrain.couverture[autres].sum(axis=0)).ravel() > 0] = 0
            gains = np.asarray(p.terrain.couverture @ libres)
            actuel = gains[s]
            gains[~p.eligibles] = -1
            gains[sel] = -1
            for i in np.argsort(-gains)[:6]:
                if gains[i] <= actuel:
                    break
                if _valide(p, [*autres, int(i)]):
                    sel, progres = [*autres, int(i)], True
                    break
            if progres:
                break
        if not progres:
            break
    return sel


# --- preuve d'optimalité : le territoire se découpe en îlots indépendants


@dataclass
class _Ilot:
    sites: np.ndarray  # positions dans le terrain
    acces: sparse.csr_matrix  # sites de l'îlot x groupes de carreaux
    poids: np.ndarray
    exact: dict  # n -> (valeur, sites) prouvés
    plafond: np.ndarray  # majorant de la valeur pour n = 0..N

    def resoudre(self, n: int, limite: float):
        """Meilleur choix de n sites dans l'îlot, prouvé par programme linéaire en nombres entiers."""
        nk, m = self.acces.shape
        if n >= nk:
            return float(self.poids.sum()), self.sites
        if n == 1:
            gains = self.acces @ self.poids
            return float(gains.max()), self.sites[[int(np.argmax(gains))]]
        if nk + m > TAILLE_EXACTE:
            return None
        matrice = sparse.vstack(
            [
                sparse.csr_matrix(np.r_[np.ones(nk), np.zeros(m)]),
                sparse.hstack([-self.acces.T, sparse.eye(m)]),
            ],
            format="csc",
        )
        solution = milp(
            np.r_[np.zeros(nk), -self.poids],
            integrality=np.r_[np.ones(nk), np.zeros(m)],
            bounds=Bounds(0, 1),
            constraints=LinearConstraint(
                matrice, np.r_[n, np.full(m, -np.inf)], np.r_[n, np.zeros(m)]
            ),
            options={"time_limit": limite, "mip_rel_gap": 0},
        )
        if solution.status != 0 or solution.x is None:
            return None
        return float(-solution.fun), self.sites[np.rint(solution.x[:nk]) == 1]


def _ilots(p: _Probleme) -> list[_Ilot]:
    """Regroupe les carreaux couverts par les mêmes sites, retire les sites dominés, puis sépare
    les zones qui n'ont aucun site en commun : chacune se résout seule."""
    c = p.terrain.couverture
    utiles = np.flatnonzero(p.poids > 0)
    sites = np.flatnonzero(p.eligibles & (np.asarray(c @ p.poids) > 0))
    if not len(sites) or not len(utiles):
        return []
    acces = c[sites][:, utiles].tocsc()
    # Deux carreaux vus par exactement les mêmes sites sont interchangeables : on les additionne.
    # Deux empreintes indépendantes, sommes exactes en flottants : une confusion est exclue en pratique.
    cles = np.random.default_rng(0).integers(1, 2**26, size=(len(sites), 2)).astype(np.float64)
    empreinte = acces.T @ cles
    _, premier, groupe = np.unique(empreinte, axis=0, return_index=True, return_inverse=True)
    groupe = np.ravel(groupe)
    poids = np.bincount(groupe, weights=p.poids[utiles])
    acces = acces[:, premier].tocsr()
    # Un site dont un autre couvre au moins les mêmes groupes n'est jamais nécessaire.
    communs = (acces @ acces.T).tocoo()
    taille = np.asarray(acces.sum(axis=1)).ravel()
    i, j = communs.row, communs.col
    inclus = (communs.data == taille[i]) & (i != j)
    inclus &= (taille[j] > taille[i]) | ((taille[j] == taille[i]) & (j < i))
    garde = np.ones(len(sites), dtype=bool)
    garde[np.unique(i[inclus])] = False
    sites, acces = sites[garde], acces[garde]

    ns = len(sites)
    graphe = sparse.bmat([[None, acces], [acces.T, None]], format="csr")
    _, etiquette = connected_components(graphe, directed=False)
    ilots = []
    for e in np.unique(etiquette[:ns]):
        dans, vus = etiquette[:ns] == e, etiquette[ns:] == e
        bloc = acces[dans][:, vus]
        w = poids[vus]
        seuls = np.sort(np.asarray(bloc @ w))[::-1][: p.nombre]
        plafond = np.r_[0.0, np.minimum(np.cumsum(seuls), w.sum())]
        plafond = np.r_[plafond, np.full(p.nombre + 1 - len(plafond), plafond[-1])]
        ilots.append(_Ilot(sites[dans], bloc, w, {0: (0.0, sites[:0])}, plafond))
    # Ajouter un site dans un îlot ne rapporte jamais plus que son meilleur site seul : une
    # solution optimale tient donc toujours dans les `nombre` îlots au meilleur site seul.
    ilots.sort(key=lambda ilot: -ilot.plafond[1])
    return ilots[: p.nombre]


def _repartir(ilots: list[_Ilot], nombre: int):
    """Programmation dynamique : combien de maisons par îlot pour maximiser la somme des valeurs.

    Utilise la valeur prouvée quand elle existe, sinon le plafond. Renvoie (total, répartition).
    """
    valeurs = np.array(
        [
            [ilot.exact[n][0] if n in ilot.exact else ilot.plafond[n] for n in range(nombre + 1)]
            for ilot in ilots
        ]
    )
    meilleur = np.full(nombre + 1, -np.inf)
    meilleur[0] = 0.0
    choix = np.zeros((len(ilots), nombre + 1), dtype=int)
    for a, ligne in enumerate(valeurs):
        suivant = np.full(nombre + 1, -np.inf)
        for total in range(nombre + 1):
            candidats = meilleur[total::-1] + ligne[: total + 1]  # n maisons ici, total - n avant
            choix[a, total] = int(np.argmax(candidats))
            suivant[total] = candidats[choix[a, total]]
        meilleur = suivant
    total = int(np.argmax(meilleur))
    repartition, reste = [0] * len(ilots), total
    for a in range(len(ilots) - 1, -1, -1):
        repartition[a] = int(choix[a, reste])
        reste -= repartition[a]
    return float(meilleur[total]), repartition


def _prouver(p: _Probleme, limite: float, progression=None):
    """Cherche l'optimum prouvé en résolvant, îlot par îlot, seulement ce dont la répartition a
    besoin. Renvoie (sélection ou None, majorant, prouvé)."""
    fin = time.monotonic() + limite
    ilots = _ilots(p)
    if not ilots:
        return [], 0.0, True
    faits = 0
    while True:
        majorant, repartition = _repartir(ilots, p.nombre)
        manquants = [(a, n) for a, n in enumerate(repartition) if n not in ilots[a].exact]
        if progression:
            progression(faits, faits + len(manquants))
        if not manquants:
            selection = [int(s) for a, n in enumerate(repartition) for s in ilots[a].exact[n][1]]
            return selection, majorant, True
        for a, n in manquants:
            reste = fin - time.monotonic()
            resultat = ilots[a].resoudre(n, max(reste, 0.1)) if reste > 0 else None
            if resultat is None:
                return None, majorant, False
            ilots[a].exact[n] = resultat
            # Une valeur prouvée resserre aussi le plafond des effectifs voisins.
            ilots[a].plafond[: n + 1] = np.minimum(ilots[a].plafond[: n + 1], resultat[0])
            faits += 1
            if progression:
                progression(faits, faits + len(manquants))


def _plus_chers(p: _Probleme, valeurs: np.ndarray) -> np.ndarray:
    """Les `nombre` sites de plus forte valeur. Si les territoires de la répartition sont
    disjoints, leurs minimums et maximums sont respectés : on sert d'abord les minimums, puis on
    complète par valeur décroissante (exact pour des territoires disjoints)."""
    if not p.groupes or p.groupe_de is None:
        return np.argpartition(-valeurs, p.nombre - 1)[: p.nombre]
    ordre = np.argsort(-valeurs)
    rang = p.groupe_de[ordre]
    pris = np.zeros(len(valeurs), dtype=bool)
    marge = []
    for g, (_, mini, maxi) in enumerate(p.groupes):
        pris[ordre[rang == g][:mini]] = True
        marge.append(maxi - mini)
    reste = p.nombre - int(pris.sum())
    if reste < 0:
        return np.argpartition(-valeurs, p.nombre - 1)[: p.nombre]
    for i, g in zip(ordre, rang, strict=True):
        if reste == 0:
            break
        if pris[i] or (g >= 0 and marge[g] == 0):
            continue
        pris[i] = True
        reste -= 1
        if g >= 0:
            marge[g] -= 1
    return np.flatnonzero(pris)


def _encadrer(p: _Probleme, selection, iterations: int, progression=None):
    """Relaxation lagrangienne : améliore la sélection et calcule un majorant de l'optimum.

    On « paie » chaque carreau un prix ; un site vaut la somme des prix de sa zone. Les `nombre`
    sites les plus chers donnent à la fois un majorant et une sélection candidate. Le majorant
    tient compte de la répartition entre territoires disjoints ; il ignore l'espacement, ce qui le
    laisse valable mais moins serré. Les prix sont ajustés à chaque tour.
    Renvoie (sélection, objectif, majorant).
    """
    c = p.terrain.couverture
    w = p.poids.astype(float)
    meilleure, objectif = list(selection), _gain(p, selection)
    prix = w / 2
    majorant, pas, stagnation, depart, tour = np.inf, 2.0, 0, None, -1
    for tour in range(iterations):
        valeurs = np.asarray(c @ prix)
        valeurs[~p.eligibles] = -np.inf
        retenus = _plus_chers(p, valeurs)
        borne = float(np.maximum(w - prix, 0).sum() + valeurs[retenus].sum())
        if borne < majorant - 1e-9:
            majorant, stagnation = borne, 0
        else:
            stagnation += 1
            if stagnation >= 12:
                pas, stagnation = pas / 2, 0
        fois = np.asarray(c[retenus].sum(axis=0)).ravel()
        gain = float(w[fois > 0].sum())
        if gain > objectif + 1e-9 and _valide(p, retenus.tolist()):
            meilleure, objectif = retenus.tolist(), gain
        pente = fois - (w > prix)
        norme = float(pente @ pente)
        if progression:
            ecart = max(majorant - objectif, 0.0)
            depart = ecart if depart is None else depart
            # L'écart se referme vite puis lentement : on le lit sur une échelle logarithmique.
            part = 1.0 if ecart < 0.5 else 1 - np.log1p(ecart) / np.log1p(max(depart, 1.0))
            progression(
                {
                    "tour": tour + 1,
                    "tours_max": iterations,
                    "ecart": ecart,
                    "part": float(np.clip(part, 0, 0.99)),
                }
            )
        if norme == 0 or majorant - objectif < 0.5 or pas < 1e-4:
            break
        prix = np.clip(prix - pas * (borne - objectif) / norme * pente, 0, w)
    if progression:
        progression(
            {
                "tour": tour + 1,
                "tours_max": iterations,
                "ecart": max(majorant - objectif, 0.0),
                "part": 1.0,
            }
        )
    return meilleure, objectif, max(majorant, objectif)


def optimiser(
    terrain: Terrain,
    nombre: int,
    *,
    priorite: str = "tous",
    couverts=None,
    eligibles=None,
    groupes=(),
    espacement: float = 0.0,
    limite_preuve: float = 3.0,
    tours_encadrement: int = 0,
    progression=None,
) -> dict:
    """Choisit `nombre` sites parmi les candidats éligibles du terrain.

    `couverts` : carreaux déjà rapprochés par des maisons posées (ils ne comptent plus).
    `groupes` : (masque des sites, mini, maxi) pour chaque règle de répartition.
    La preuve d'optimalité n'est tentée que sans répartition ni espacement, dans la limite de
    `limite_preuve` secondes. Si elle n'aboutit pas et que `tours_encadrement` > 0, la relaxation
    lagrangienne améliore la sélection et garantit un écart maximal à l'optimum.
    `progression(etat)` est appelée à chaque tour d'encadrement : numéro du tour, maximum de
    tours, écart restant à l'optimum et part du chemin parcouru (1 quand c'est terminé).
    """
    n_sites = len(terrain.sites_xyz)
    eligibles = (
        np.ones(n_sites, dtype=bool) if eligibles is None else np.asarray(eligibles, dtype=bool)
    )
    if nombre < 0 or nombre > int(eligibles.sum()):
        raise ValueError("Pas assez de communes d'accueil possibles pour ce nombre de maisons.")
    poids = terrain.poids(priorite)
    if couverts is not None:
        poids = np.where(couverts, 0, poids)
    groupes = [(np.asarray(m, dtype=bool), int(a), int(b)) for m, a, b in groupes]
    for masque, mini, maxi in groupes:
        if mini > maxi or mini > int((masque & eligibles).sum()):
            raise ValueError(
                "La répartition demandée est impossible avec les communes d'accueil permises."
            )
    p = _Probleme(terrain, int(nombre), poids, eligibles, groupes, float(espacement or 0))

    selection = _glouton(p)
    if not _valide(p, selection):
        raise ValueError(
            "Ces critères sont incompatibles entre eux : assouplissez la répartition, "
            "l'espacement ou les filtres de commune d'accueil."
        )
    selection = _echanges(p, selection)
    objectif = _gain(p, selection)
    resultat = {
        "selection": selection,
        "objectif": objectif,
        "optimal": False,
        "majorant": None,
        "methode": "rapide",
    }
    if not nombre:
        return resultat | {"optimal": True, "majorant": 0.0}
    prouvee, majorant, prouve = None, None, False
    if not groupes and p.espacement == 0 and limite_preuve > 0:
        prouvee, majorant, prouve = _prouver(p, limite_preuve)
        resultat["majorant"] = max(majorant, objectif)
    if not prouve and tours_encadrement > 0:
        selection, objectif, borne = _encadrer(p, selection, tours_encadrement, progression)
        selection = _echanges(p, selection)
        objectif = _gain(p, selection)
        majorant = borne if majorant is None else min(majorant, borne)
        resultat.update(
            selection=selection,
            objectif=objectif,
            majorant=max(majorant, objectif),
            methode="encadree",
        )
        resultat["optimal"] = resultat["majorant"] - objectif < 0.5
    if prouve:
        # Complète avec des sites sans gain si la preuve en a retenu moins que demandé.
        libres = [
            int(i)
            for i in np.argsort(-terrain.sites_population)
            if eligibles[i] and i not in set(prouvee)
        ]
        prouvee = prouvee + libres[: nombre - len(prouvee)]
        if _gain(p, prouvee) >= objectif - 1e-6:
            resultat.update(selection=prouvee, objectif=_gain(p, prouvee))
        resultat.update(optimal=True, methode="prouvee", majorant=resultat["objectif"])
    return resultat


def bornes_groupes(groupes, deja) -> list:
    """Retire des minimums et maximums les maisons déjà posées dans chaque territoire."""
    resultat = []
    for (masque, mini, maxi), n in zip(groupes, deja, strict=True):
        if n > maxi:
            raise ValueError(
                "Il y a déjà plus de maisons posées que le maximum demandé dans un territoire."
            )
        resultat.append((masque, max(0, mini - n), maxi - n))
    return resultat
