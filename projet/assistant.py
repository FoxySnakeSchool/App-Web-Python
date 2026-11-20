"""L'assistant de l'appli : il traduit une demande écrite en critères du calcul, rien de plus.

Le modèle de langage ne choisit aucune commune et ne produit aucun chiffre : il remplit les mêmes
réglages que le panneau, que l'utilisateur voit et peut corriger, puis le moteur calcule.
On ne lui envoie aucune donnée du projet : la phrase de l'utilisateur, ses réglages en cours et la
liste publique des régions et départements.

Modèle : Claude, par le SDK Anthropic. La clé est lue dans projet/.env (CLAUDE_API_KEY) ou dans
l'environnement (ANTHROPIC_API_KEY) ; LLM_MODEL change de modèle.
"""

import json
import os
from functools import lru_cache
from pathlib import Path

import anthropic
import httpx2

from . import donnees, outils

ENV = Path(__file__).resolve().parent / ".env"
PALIERS_POPULATION = [0, 500, 1000, 2000, 5000, 10000]
PRIORITES = ("tous", "priorite", "seuls", "aines")
CHAMPS = (
    "nombre",
    "territoires",
    "distance_km",
    "repartition",
    "population_minimale",
    "ecarter_communes_equipees",
    "espacement_km",
    "communes_interdites",
    "priorite",
)


def _env() -> dict:
    """Variables du fichier .env (jamais versionné), sans dépendance supplémentaire."""
    if not ENV.is_file():
        return {}
    lignes = (ligne.strip() for ligne in ENV.read_text(encoding="utf-8").splitlines())
    paires = (ligne.split("=", 1) for ligne in lignes if "=" in ligne and not ligne.startswith("#"))
    return {nom.strip(): valeur.strip().strip("\"'") for nom, valeur in paires}


def _reglage(*noms, defaut=None):
    fichier = _env()
    for nom in noms:
        valeur = os.environ.get(nom) or fichier.get(nom)
        if valeur:
            return valeur
    return defaut


def modele() -> str:
    return _reglage("LLM_MODEL", defaut="claude-opus-5-5")


def disponible() -> bool:
    return bool(_reglage("CLAUDE_API_KEY", "ANTHROPIC_API_KEY"))


@lru_cache(maxsize=1)
def _consigne() -> str:
    regions = ", ".join(donnees.REGIONS.values())
    return f"""Tu traduis la demande d'un élu en critères pour un outil qui choisit où implanter des maisons de santé en France métropolitaine. Tu ne choisis aucune commune et tu ne donnes aucun chiffre de population.

Réponds par un seul objet JSON, sans texte autour, avec ces champs. Mets null pour un champ dont la demande ne parle pas : il gardera sa valeur actuelle.
- "nombre" : entier de 1 à 60, nombre total de maisons de santé voulues.
- "territoires" : liste de noms de régions ou de départements, ou ["France"]. C'est le périmètre où l'on implante et où l'on compte les habitants.
- "distance_km" : entier de 5 à 20, distance au-delà de laquelle un habitant est loin d'une maison de santé.
- "repartition" : liste de règles {{"territoire": nom, "regle": "exactement" | "au moins" | "au plus", "nombre": entier}}. Le territoire est une région, un département, ou "chaque département" ou "chaque région" pour répéter la règle sur tout le périmètre. Liste vide pour supprimer les règles. N'ajoute une règle que si la demande répartit les maisons entre plusieurs territoires ou fixe un minimum ou un maximum : « 6 maisons dans le Loiret » ne demande aucune règle, seulement nombre 6 et territoires ["Loiret"].
- "population_minimale" : nombre minimal d'habitants de la commune qui accueille une maison.
- "ecarter_communes_equipees" : true pour ne pas implanter dans une commune qui a déjà une maison de santé.
- "espacement_km" : distance minimale entre deux nouvelles maisons, de 0 à 50.
- "communes_interdites" : liste de noms de communes où ne pas implanter.
- "priorite" : "tous" (tous les habitants comptent), "priorite" (les zones sous-dotées en médecins généralistes comptent double), "seuls" (uniquement ces zones : à choisir dès que la demande dit « uniquement », « seulement » ou « rien que » pour les zones qui manquent de médecins), "aines" (seuls les 65 ans et plus comptent).
- "repartir_de_zero" : true si la demande veut remplacer les maisons déjà posées, sinon false.
- "non_pris_en_compte" : liste de courtes phrases, en français, pour chaque souhait de la demande qu'aucun champ ne permet d'exprimer (temps de trajet, coût, présence de médecins, gare, etc.). Liste vide sinon.

Si la demande cite des territoires dans une répartition sans donner de périmètre, le périmètre est la liste de ces territoires. « 4 dans le Loiret et 2 en Île-de-France » veut dire : nombre 6, territoires ["Loiret", "Île-de-France"], deux règles "exactement".

Régions : {regions}.
Les départements se donnent par leur nom ou leur numéro."""


@lru_cache(maxsize=1)
def _client() -> anthropic.Anthropic:
    """Un seul client, réutilisé : la connexion reste ouverte d'une demande à l'autre.

    On force l'IPv4 : sur certaines machines l'IPv6 expire avant le repli, et la première demande
    attend alors de 5 à 40 secondes au lieu de 0,2 (mesuré). LLM_IPV4=0 désactive ce choix.
    """
    reseau = (
        {}
        if os.environ.get("LLM_IPV4") == "0"
        else {"transport": httpx2.HTTPTransport(local_address="0.0.0.0")}
    )
    return anthropic.Anthropic(
        api_key=_reglage("CLAUDE_API_KEY", "ANTHROPIC_API_KEY"),
        timeout=30.0,
        max_retries=1,
        http_client=anthropic.DefaultHttpxClient(**reseau),
    )


@lru_cache(maxsize=256)
def _demander(message: str, etat: str) -> dict:
    """Un appel au modèle, mis en cache : même demande et mêmes réglages, même réponse."""
    reponse = _client().messages.create(
        model=modele(),
        max_tokens=4000,
        # Traduire une phrase en réglages ne demande pas de longue réflexion : effort faible.
        output_config={"effort": "low"},
        system=_consigne(),
        messages=[{"role": "user", "content": f"Réglages actuels : {etat}\n\nDemande : {message}"}],
    )
    if reponse.stop_reason == "refusal":
        raise ValueError("Le modèle a refusé cette demande.")
    texte = "".join(bloc.text for bloc in reponse.content if bloc.type == "text")
    return json.loads(texte[texte.index("{") : texte.rindex("}") + 1])


def _entier(valeur, mini, maxi):
    return None if valeur is None else max(mini, min(maxi, round(float(valeur))))


def traduire(brut: dict, etat: dict) -> dict:
    """Transforme la réponse du modèle en réglages valides du panneau. Fonction pure, testable.

    `etat` : réglages actuels au format du panneau (nombre, perimetre, seuil, regles, pop_min,
    exclure, espacement, interdites, priorite). Tout ce qui est invalide est écarté et signalé.
    """
    reglages = dict(etat)
    ecartes = [str(x) for x in brut.get("non_pris_en_compte") or []]

    def essayer(action, souhait):
        try:
            return action()
        except (ValueError, KeyError, TypeError) as erreur:
            ecartes.append(f"{souhait} : {erreur}" if str(erreur) else souhait)
            return None

    if brut.get("nombre") is not None:
        reglages["nombre"] = _entier(brut["nombre"], 1, outils.NOMBRE_MAX)
    if brut.get("territoires"):
        zone = essayer(lambda: outils.territoires(brut["territoires"]), "périmètre")
        if zone:
            reglages["perimetre"] = zone
    if brut.get("distance_km") is not None:
        reglages["seuil"] = _entier(brut["distance_km"], *outils.SEUILS)
    if brut.get("repartition") is not None:
        regles = essayer(
            lambda: outils.regles(brut["repartition"], reglages["perimetre"]), "répartition"
        )
        if regles is not None:
            reglages["regles"] = regles
    # Une règle « exactement N » sur tout le périmètre, pour N maisons, ne dit rien de plus : on l'ôte.
    partout = set(donnees.deps_de(reglages["perimetre"]))
    reglages["regles"] = [
        x
        for x in reglages["regles"]
        if not (
            x["type"] == "exact"
            and x["n"] == reglages["nombre"]
            and set(donnees.deps_de([x["t"]])) == partout
        )
    ]
    if brut.get("population_minimale") is not None:
        voulu = float(brut["population_minimale"])
        # Le panneau propose des paliers : on prend le plus proche.
        reglages["pop_min"] = min(
            range(len(PALIERS_POPULATION)), key=lambda i: abs(PALIERS_POPULATION[i] - voulu)
        )
    if brut.get("ecarter_communes_equipees") is not None:
        reglages["exclure"] = ["oui"] if brut["ecarter_communes_equipees"] else []
    if brut.get("espacement_km") is not None:
        reglages["espacement"] = 5 * round(_entier(brut["espacement_km"], 0, 50) / 5)
    if brut.get("communes_interdites") is not None:
        codes = [
            essayer(lambda nom=nom: outils.commune(nom), f"commune « {nom} »")
            for nom in brut["communes_interdites"]
        ]
        reglages["interdites"] = [c for c in codes if c]
    if brut.get("priorite") in PRIORITES:
        reglages["priorite"] = brut["priorite"]
    reglages["remplacer"] = bool(brut.get("repartir_de_zero"))
    return {"reglages": reglages, "ecartes": ecartes, "compris": resumer(reglages)}


def resumer(r: dict) -> list[str]:
    """Les réglages en clair, écrits par le code (pas par le modèle)."""
    from . import scenario

    lignes = [
        f"{r['nombre']} maison{'s' if r['nombre'] > 1 else ''} en tout",
        "Périmètre : " + ", ".join(donnees.nom_territoire(t) for t in r["perimetre"]),
        f"Loin des soins au-delà de {r['seuil']} km",
    ]
    if r["regles"]:
        lignes.append(
            "Répartition : " + " ; ".join(scenario.libelle_regle(x) for x in r["regles"][:6])
        )
        if len(r["regles"]) > 6:
            lignes[-1] += f" ; et {len(r['regles']) - 6} autres règles"
    if r["pop_min"]:
        lignes.append(f"Commune d'accueil d'au moins {PALIERS_POPULATION[r['pop_min']]} habitants")
    if r["exclure"]:
        lignes.append("Communes déjà équipées écartées")
    if r["espacement"]:
        lignes.append(f"Au moins {r['espacement']} km entre deux nouvelles maisons")
    if r["interdites"]:
        lignes.append(
            "Communes interdites : "
            + ", ".join(o["label"] for o in scenario.libelles_communes(r["interdites"]))
        )
    if r["priorite"] != "tous":
        lignes.append(scenario.PRIORITES[r["priorite"]])
    if r.get("remplacer"):
        lignes.append("Les maisons déjà posées sont remplacées")
    return lignes


def _en_clair(etat: dict) -> str:
    """Les réglages actuels, dits au modèle avec les mots de sa consigne."""
    from . import scenario

    inverse = {v: k for k, v in outils.REGLES.items()}
    return json.dumps(
        {
            "nombre": etat["nombre"],
            "territoires": [donnees.nom_territoire(t) for t in etat["perimetre"]],
            "distance_km": etat["seuil"],
            "repartition": [
                {
                    "territoire": donnees.nom_territoire(x["t"]),
                    "regle": inverse[x["type"]],
                    "nombre": x["n"],
                }
                for x in etat["regles"]
            ],
            "population_minimale": PALIERS_POPULATION[etat["pop_min"]],
            "ecarter_communes_equipees": bool(etat["exclure"]),
            "espacement_km": etat["espacement"],
            "communes_interdites": [
                o["label"] for o in scenario.libelles_communes(etat["interdites"])
            ],
            "priorite": etat["priorite"],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def interpreter(message: str, etat: dict) -> dict:
    """Demande écrite -> réglages du panneau, ce qui a été compris et ce qui a été écarté."""
    message = (message or "").strip()
    if not message:
        raise ValueError("Écrivez votre demande.")
    if not disponible():
        raise ValueError("Assistant indisponible : aucune clé CLAUDE_API_KEY dans projet/.env.")
    if len(message) > 600:
        raise ValueError("Demande trop longue : 600 caractères au plus.")
    try:
        brut = _demander(message, _en_clair(etat))
    except ValueError:
        raise ValueError(
            "Le modèle n'a pas rendu une réponse exploitable. Reformulez la demande."
        ) from None
    except anthropic.APIError as erreur:  # réseau, quota, clé refusée : on le dit sans la clé
        raise ValueError(
            f"Le modèle n'a pas répondu ({type(erreur).__name__}). Réessayez dans un instant."
        ) from None
    return {**traduire(brut, etat), "modele": modele()}
