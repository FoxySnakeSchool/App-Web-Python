"""Exo 1 : lire mes parties d'échecs. Environ 25 min.

Question : je joue depuis 2023, je stagne, qu'est-ce que je devrais travailler ?
À produire : un DataFrame propre, prêt pour les figures de l'exo 1 et pour l'appli de l'exo 2.
Les colonnes et leurs pièges sont dans data/echecs/LISEZMOI.md : lisez-le d'abord.
Lancer : uv run python fil_rouge/data.py
"""

from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parents[1] / "data" / "echecs"
RESULTATS = {"win": "victoire", "draw": "nulle", "lose": "défaite"}
SCORES = {"victoire": 1.0, "nulle": 0.5, "défaite": 0.0}
COLONNES_NUMERIQUES = ["elo_joueur", "elo_adverse", "gaffes_joueur"]
COLONNES_REQUISES = [
    "partie_id",
    "date_jeu",
    "resultat",
    "cadence",
    "couleur",
    "ouverture_famille",
    *COLONNES_NUMERIQUES,
]


def valider(df: pd.DataFrame) -> pd.DataFrame:
    """Plan B : s'arrêter avec un message clair si le fichier n'a pas la forme attendue.

    Colonnes absentes, colonnes entièrement vides, nombres lus comme du texte,
    identifiant de partie en double, résultat inconnu.
    """
    absentes = [colonne for colonne in COLONNES_REQUISES if colonne not in df]
    if absentes:
        raise ValueError(f"Colonnes absentes dans parties.csv : {', '.join(absentes)}")

    vides = [
        colonne
        for colonne in COLONNES_REQUISES
        if colonne != "cadence" and df[colonne].isna().all()
    ]
    if vides:
        raise ValueError(f"Colonnes entièrement vides dans parties.csv : {', '.join(vides)}")

    non_numeriques = [
        colonne for colonne in COLONNES_NUMERIQUES if not pd.api.types.is_numeric_dtype(df[colonne])
    ]
    if non_numeriques:
        raise ValueError(f"Colonnes non numériques dans parties.csv : {', '.join(non_numeriques)}")

    identifiants = df["partie_id"]
    if identifiants.isna().any() or not identifiants.is_unique:
        raise ValueError("Identifiant de partie manquant ou en double dans parties.csv.")

    inconnus = df.loc[~df["resultat"].isin(RESULTATS), "resultat"]
    if not inconnus.empty:
        raise ValueError(
            f"Résultat inconnu ou manquant dans parties.csv : {inconnus.unique().tolist()}"
        )

    return df


def load_data() -> pd.DataFrame:
    """Une ligne par partie (data/echecs/parties.csv), validée.

    Ajoutez : le résultat de mon point de vue en français, un score (1, 0,5 ou 0),
    l'année, l'heure, et l'écart d'Elo avec l'adversaire. Une cadence vide veut dire
    « aucune » : ne la laissez pas disparaître des filtres.
    Les exos suivants lisent ces colonnes, gardez ces noms : date_jeu (en datetime),
    resultat (victoire, nulle, défaite), score, annee, heure (UTC), ecart_elo
    (Elo de l'adversaire moins le mien), cadence.
    """
    df = valider(pd.read_csv(DATA / "parties.csv"))
    dates = pd.to_datetime(df["date_jeu"], errors="coerce", utc=True)
    if dates.isna().any():
        raise ValueError("Date de jeu invalide ou manquante dans parties.csv.")

    df["date_jeu"] = dates
    df["resultat"] = df["resultat"].map(RESULTATS)
    df["score"] = df["resultat"].map(SCORES)
    df["annee"] = dates.dt.year
    df["heure"] = dates.dt.hour
    df["ecart_elo"] = df["elo_adverse"] - df["elo_joueur"]
    df["cadence"] = df["cadence"].fillna("aucune")
    return df


def load_coups() -> pd.DataFrame:
    """Un de mes coups jugés mauvais par ligne (data/echecs/coups_juges.csv)."""
    return pd.read_csv(DATA / "coups_juges.csv")


def familles(df: pd.DataFrame, min_n: int = 30) -> pd.DataFrame:
    """Score moyen par famille d'ouvertures, la pire d'abord.

    Colonnes : famille, n (parties), score, gaffes (par partie).
    Sous min_n parties le chiffre ne dit rien : écartez la famille.
    """
    stats = df.groupby("ouverture_famille", as_index=False).agg(
        n=("partie_id", "size"),
        score=("score", "mean"),
        gaffes=("gaffes_joueur", "mean"),
    )
    stats = stats.rename(columns={"ouverture_famille": "famille"})
    return stats.loc[stats["n"] >= min_n].sort_values("score", ignore_index=True)


if __name__ == "__main__":
    d = load_data()
    print(d.shape)
