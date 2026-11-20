"""Mesure la fiabilité de l'assistant sur un jeu de demandes dont on connaît la bonne traduction.

Lancer depuis la racine du dépôt (appelle le modèle : une vingtaine de requêtes) :
    uv run python -m projet.evaluer_assistant

Chaque demande part des réglages par défaut. Elle est réussie si tous les réglages attendus sont
exacts et si un souhait hors de portée de l'outil est bien signalé comme non pris en compte.
"""

import sys

from . import assistant, outils

DEFAUT = {
    "nombre": 6,
    "perimetre": ["FR"],
    "seuil": 10,
    "regles": [],
    "pop_min": 0,
    "exclure": [],
    "espacement": 0,
    "interdites": [],
    "priorite": "tous",
}
CVL = [f"D:{d}" for d in ("18", "28", "36", "37", "41", "45")]


def regle(territoire, genre, n):
    return {"t": territoire, "type": genre, "n": n}


# (demande, réglages attendus, un souhait doit-il être signalé comme non pris en compte ?)
CAS = [
    ("6 maisons dans le Loiret", {"nombre": 6, "perimetre": ["D:45"]}, False),
    (
        "Je veux implanter 12 maisons de santé en Bretagne",
        {"nombre": 12, "perimetre": ["R:53"]},
        False,
    ),
    (
        "j'ai 6 maisons : 4 dans le Loiret et 2 en Ile-de-France",
        {
            "nombre": 6,
            "perimetre": ["D:45", "R:11"],
            "regles": [regle("D:45", "exact", 4), regle("R:11", "exact", 2)],
        },
        False,
    ),
    (
        "8 maisons en Centre-Val de Loire, au moins une par département",
        {"nombre": 8, "perimetre": ["R:24"], "regles": [regle(t, "min", 1) for t in CVL]},
        False,
    ),
    (
        "10 maisons en Occitanie, pas plus de 2 par département",
        {"nombre": 10, "perimetre": ["R:76"], "regles": "max 2 partout"},
        False,
    ),
    (
        "5 maisons en Normandie en comptant comme éloigné au-delà de 15 km",
        {"nombre": 5, "perimetre": ["R:28"], "seuil": 15},
        False,
    ),
    (
        "3 maisons dans le Cher, seulement dans des communes d'au moins 2000 habitants",
        {"nombre": 3, "perimetre": ["D:18"], "pop_min": 3},
        False,
    ),
    (
        "7 maisons en Bourgogne-Franche-Comté sans en mettre là où il y en a déjà une",
        {"nombre": 7, "perimetre": ["R:27"], "exclure": ["oui"]},
        False,
    ),
    (
        "9 maisons dans le Grand Est, espacées d'au moins 30 km",
        {"nombre": 9, "perimetre": ["R:44"], "espacement": 30},
        False,
    ),
    (
        "4 maisons en Ille-et-Vilaine mais pas à Rennes",
        {"nombre": 4, "perimetre": ["D:35"], "interdites": ["35238"]},
        False,
    ),
    (
        "6 maisons en France, priorité aux personnes âgées",
        {"nombre": 6, "perimetre": ["FR"], "priorite": "aines"},
        False,
    ),
    (
        "6 maisons en Corse uniquement pour les zones qui manquent de médecins généralistes",
        {"nombre": 6, "perimetre": ["R:94"], "priorite": "seuls"},
        False,
    ),
    (
        "5 maisons dans l'Indre à moins de 20 minutes en voiture d'une gare",
        {"nombre": 5, "perimetre": ["D:36"]},
        True,
    ),
    (
        "2 maisons dans le Loiret là où des médecins sont prêts à s'installer",
        {"nombre": 2, "perimetre": ["D:45"]},
        True,
    ),
    (
        "Repars de zéro avec 3 maisons en Eure-et-Loir",
        {"nombre": 3, "perimetre": ["D:28"], "remplacer": True},
        False,
    ),
    (
        "15 maisons en Nouvelle-Aquitaine, exactement 3 en Gironde et au plus 1 dans les Landes, communes de 1000 habitants minimum",
        {
            "nombre": 15,
            "perimetre": ["R:75"],
            "regles": [regle("D:33", "exact", 3), regle("D:40", "max", 1)],
            "pop_min": 2,
        },
        False,
    ),
]


def conforme(reglages, attendus, ecartes, doit_signaler) -> list[str]:
    """Liste des écarts entre ce que l'assistant a rendu et ce qui était attendu."""
    ecarts = []
    for champ, valeur in {**DEFAUT, "remplacer": False, **attendus}.items():
        obtenu = reglages[champ]
        if champ == "regles" and valeur == "max 2 partout":
            ok = bool(obtenu) and all(r["type"] == "max" and r["n"] == 2 for r in obtenu)
        elif champ in ("regles", "perimetre", "interdites"):
            cle = (lambda r: (r["t"], r["type"], r["n"])) if champ == "regles" else (lambda x: x)
            ok = sorted(map(cle, obtenu)) == sorted(map(cle, valeur))
        else:
            ok = obtenu == valeur
        if not ok:
            ecarts.append(f"{champ} : attendu {valeur}, obtenu {obtenu}")
    if doit_signaler != bool(ecartes):
        ecarts.append(
            "souhait hors de portée non signalé"
            if doit_signaler
            else f"signalement inattendu : {ecartes}"
        )
    return ecarts


def evaluer() -> tuple[int, int]:
    reussies = 0
    for demande, attendus, doit_signaler in CAS:
        try:
            r = assistant.interpreter(demande, dict(DEFAUT))
            ecarts = conforme(r["reglages"], attendus, r["ecartes"], doit_signaler)
        except ValueError as erreur:
            ecarts = [str(erreur)]
        reussies += not ecarts
        print(("OK   " if not ecarts else "ECHEC") + " " + demande)
        for e in ecarts:
            print("        " + e)
    return reussies, len(CAS)


if __name__ == "__main__":
    if not assistant.disponible():
        sys.exit("Aucune clé : renseignez CLAUDE_API_KEY dans projet/.env.")
    bonnes, total = evaluer()
    print(
        f"\n{bonnes} demandes sur {total} traduites sans écart avec {assistant.modele()} "
        f"({outils.NOMBRE_MAX} maisons au plus, réglages par défaut au départ)."
    )
