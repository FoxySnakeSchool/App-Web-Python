"""Le moteur de Phares en langage courant : territoires et communes par leur nom, résultats lisibles.

Deux portes s'appuient sur ce module : le chat de l'appli (assistant.py) et le serveur MCP
(mcp_server.py). Aucune ne calcule elle-même : tout passe par scenario.py. Les outils sont en
lecture seule et leurs entrées sont bornées.
"""

import os
import re
import unicodedata
from functools import lru_cache
from urllib.parse import parse_qs, urlencode

from . import donnees, scenario

ADRESSE = os.environ.get("PHARES_URL", "http://localhost:8060")
NOMBRE_MAX = 60
SEUILS = (5, 20)  # distance d'accès, en km
REGLES = {"exactement": "exact", "au moins": "min", "au plus": "max"}
CHAQUE = {"chaque departement": "D", "chaque region": "R"}
ALIAS = {
    "france": "FR",
    "france entiere": "FR",
    "toute la france": "FR",
    "france metropolitaine": "FR",
    "metropole": "FR",
    "idf": "R:11",
    "ile de france": "R:11",
    "paca": "R:93",
    "centre": "R:24",
    "region centre": "R:24",
}


def cle(texte: str) -> str:
    """Nom ramené à sa forme de comparaison : sans accent, sans casse, sans ponctuation."""
    sans_accent = unicodedata.normalize("NFKD", str(texte)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", sans_accent.casefold()).strip()


@lru_cache(maxsize=1)
def _territoires() -> dict:
    noms = dict(ALIAS)
    for code, nom in donnees.REGIONS.items():
        noms[cle(nom)] = f"R:{code}"
    for code, infos in donnees.departements().items():
        noms[cle(infos["nom"])] = f"D:{code}"
        noms[cle(code)] = f"D:{code}"
    return noms


def territoire(texte: str) -> str:
    """« Loiret », « 45 », « Centre-Val de Loire », « France » -> D:45, R:24, FR."""
    if re.fullmatch(r"FR|R:\d\d|D:\w\w", str(texte)) and (
        texte == "FR" or donnees.deps_de([texte])
    ):
        return texte
    trouve = _territoires().get(cle(texte))
    if not trouve:
        raise ValueError(f"Territoire inconnu : « {texte} ».")
    return trouve


def territoires(liste) -> list[str]:
    valeurs = list(dict.fromkeys(territoire(t) for t in (liste or ["France"])))
    return ["FR"] if "FR" in valeurs else valeurs


@lru_cache(maxsize=1)
def _communes() -> dict:
    index = {}
    df = donnees.communes()
    for code, nom, dep in zip(df["code_insee"], df["commune"], df["dep"], strict=True):
        index.setdefault(cle(nom), []).append((code, nom, dep))
    return index


def commune(texte: str) -> str:
    """Code INSEE d'une commune donnée par son code ou son nom, « Nom (45) » s'il y a des homonymes."""
    texte = str(texte).strip()
    if texte in scenario._position():
        return texte
    precis = re.fullmatch(r"(.+?)\s*[(,]\s*([0-9]{2}|2[ABab])\s*\)?", texte)
    nom, dep = (precis.group(1), precis.group(2).upper()) if precis else (texte, None)
    trouvees = [c for c in _communes().get(cle(nom), []) if dep is None or c[2] == dep]
    if not trouvees:
        raise ValueError(f"Commune inconnue : « {texte} ».")
    if len(trouvees) > 1:
        choix = ", ".join(f"{n} ({d})" for _, n, d in trouvees[:8])
        raise ValueError(f"Plusieurs communes s'appellent « {nom} » : précisez parmi {choix}.")
    return trouvees[0][0]


def regles(repartition, perimetre) -> list[dict]:
    """Règles de répartition. « chaque département » ou « chaque région » se déplie sur le périmètre."""
    resultat = []
    deps = donnees.deps_de(perimetre)
    for regle in repartition or []:
        genre = REGLES.get(cle(regle.get("regle", "")))
        if genre is None:
            raise ValueError(
                f"Règle inconnue : « {regle.get('regle')} » (exactement, au moins, au plus)."
            )
        n = int(regle["nombre"])
        if not 0 <= n <= NOMBRE_MAX:
            raise ValueError(f"Une règle porte sur 0 à {NOMBRE_MAX} maisons.")
        chaque = CHAQUE.get(cle(regle["territoire"]))
        if chaque == "D":
            cibles = [f"D:{d}" for d in deps]
        elif chaque == "R":
            cibles = [f"R:{r}" for r in sorted({donnees.departements()[d]["region"] for d in deps})]
        else:
            cibles = [territoire(regle["territoire"])]
        resultat += [{"t": t, "type": genre, "n": n} for t in cibles]
    return resultat


# ---------------------------------------------------------------- lien vers la carte


def lien(codes, perimetre, seuil) -> str:
    """Adresse qui ouvre l'appli sur ce scénario."""
    parametres = {"perimetre": ",".join(perimetre), "seuil": f"{seuil:g}"}
    if codes:
        parametres["maisons"] = ",".join(codes)
    return f"{ADRESSE}/?{urlencode(parametres, safe=':,')}"


def lire_lien(recherche: str) -> dict:
    """Scénario contenu dans la partie « ?… » d'une adresse ; seules les valeurs valides sont gardées."""
    champs = parse_qs((recherche or "").lstrip("?"))
    scenario_lu = {}
    codes = [c for c in champs.get("maisons", [""])[0].split(",") if c in scenario._position()]
    if codes:
        scenario_lu["maisons"] = list(dict.fromkeys(codes))[:NOMBRE_MAX]
    try:
        zone = territoires([t for t in champs.get("perimetre", [""])[0].split(",") if t])
        if "perimetre" in champs:
            scenario_lu["perimetre"] = zone
        seuil = int(float(champs["seuil"][0]))
        if SEUILS[0] <= seuil <= SEUILS[1]:
            scenario_lu["seuil"] = seuil
    except (KeyError, ValueError):
        pass
    return scenario_lu


# ---------------------------------------------------------------- outils


def _seuil(distance_km) -> int:
    distance = int(distance_km)
    if not SEUILS[0] <= distance <= SEUILS[1]:
        raise ValueError(f"La distance d'accès va de {SEUILS[0]} à {SEUILS[1]} km.")
    return distance


def _lisible(codes, perimetre, seuil) -> dict:
    b = scenario.bilan(codes, perimetre, seuil)
    return {
        "perimetre": [donnees.nom_territoire(t) for t in perimetre],
        "distance_km": seuil,
        "habitants_loin_avant": b["loin"],
        "habitants_rapproches": b["rapproches"],
        "habitants_encore_loin": b["loin"] - b["rapproches"],
        "dont_65_ans_et_plus": b["rapproches_aines"],
        "dont_zone_sous_dotee_en_generalistes": b["rapproches_sous_dotes"],
        "maisons": [
            {
                "commune": m["commune"],
                "code_insee": m["code"],
                "departement": f"{m['dep']} {m['dep_nom']}",
                "habitants_rapproches_seule": m["seule"],
                "dont_en_propre": m["propre"],
            }
            for m in b["maisons"]
        ],
        "lien_carte": lien(codes, perimetre, seuil),
        "limites": "Distances à vol d'oiseau. Seuls les habitants du périmètre sont comptés.",
    }


def bilan(communes_choisies, zone=None, distance_km=10) -> dict:
    """Ce que rapportent des maisons posées dans ces communes, seules et ensemble."""
    if len(communes_choisies or []) > NOMBRE_MAX:
        raise ValueError(f"Au plus {NOMBRE_MAX} maisons.")
    return _lisible(
        [commune(c) for c in communes_choisies or []], territoires(zone), _seuil(distance_km)
    )


def proposer(
    nombre,
    zone=None,
    distance_km=10,
    repartition=None,
    population_minimale=0,
    ecarter_communes_equipees=False,
    espacement_km=0,
    communes_interdites=None,
    communes_deja_choisies=None,
    priorite="tous",
    garantie=True,
) -> dict:
    """Les meilleures communes d'accueil, en gardant celles déjà choisies."""
    if not 1 <= int(nombre) <= NOMBRE_MAX:
        raise ValueError(f"Le nombre de maisons va de 1 à {NOMBRE_MAX}.")
    if not 0 <= float(espacement_km) <= 50:
        raise ValueError("L'espacement va de 0 à 50 km.")
    perimetre, seuil = territoires(zone), _seuil(distance_km)
    p = scenario.proposer(
        int(nombre),
        perimetre,
        codes_poses=[commune(c) for c in communes_deja_choisies or []],
        regles=regles(repartition, perimetre),
        seuil=seuil,
        pop_min=max(int(population_minimale), 0),
        exclure_equipees=bool(ecarter_communes_equipees),
        espacement=float(espacement_km),
        interdites=[commune(c) for c in communes_interdites or []],
        priorite=priorite,
        sur=bool(garantie) or not scenario.vaste(perimetre),
    )
    if p["optimal"]:
        preuve = "Optimum prouvé, à un habitant près."
    elif p["ecart_max"] is not None:
        pluriel = "s" if p["ecart_max"] > 1 else ""
        preuve = f"Aucun autre choix ne rapprocherait plus de {p['ecart_max']} habitant{pluriel} de plus."
    else:
        preuve = "Proposition rapide, non prouvée."
    return {**_lisible(p["codes"], perimetre, seuil), "preuve": preuve}


def fiche(nom, zone=None, distance_km=10, communes_deja_choisies=None) -> dict:
    """Portrait d'une commune et ce qu'une maison de santé y rapporterait."""
    perimetre, seuil = territoires(zone), _seuil(distance_km)
    f = scenario.fiche(
        commune(nom), [commune(c) for c in communes_deja_choisies or []], perimetre, seuil
    )
    noms = donnees.communes().set_index("code_insee")["commune"]
    return {
        "commune": f["commune"],
        "code_insee": f["code"],
        "departement": f"{f['dep']} {f['dep_nom']}",
        "population_2023": f["population"],
        "habitants_loin_d_une_maison": f["loin"],
        "a_deja_une_maison_de_sante": bool(f["nb_msp"]),
        "consultations_de_generaliste_accessibles_par_habitant_et_par_an": f["apl"],
        "habitants_rapproches_par_une_maison_ici": f["ajout"],
        "communes_les_plus_rapprochees": [
            {"commune": noms.get(c, c), "habitants": h} for c, h in f["communes"]
        ],
        "maisons_de_sante_les_plus_proches": [
            {"nom": m["nom"], "commune": m["commune"], "km": round(m["km"], 1)}
            for m in f["maisons_proches"]
        ],
    }


def points_chauds(zone=None, distance_km=10, communes_deja_choisies=None, combien=10) -> list[dict]:
    """Les communes où une maison de plus rapprocherait le plus d'habitants."""
    perimetre, seuil = territoires(zone), _seuil(distance_km)
    p = scenario.potentiel([commune(c) for c in communes_deja_choisies or []], perimetre, seuil)
    df = donnees.communes()
    return [
        {
            "commune": df["commune"].iloc[i],
            "code_insee": df["code_insee"].iloc[i],
            "departement": df["dep"].iloc[i],
            "habitants_rapproches": v,
        }
        for i, v in list(zip(p["idx"], p["v"], strict=True))[: max(1, min(int(combien), 50))]
    ]


def chercher(texte, combien=10) -> list[dict]:
    """Communes dont le nom commence par, ou contient, ce texte."""
    return [
        {"commune": o["label"], "code_insee": o["value"]}
        for o in scenario.chercher_communes(texte, None, limite=max(1, min(int(combien), 25)))
    ]
