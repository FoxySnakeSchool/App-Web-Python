"""Exo 1 : trois figures. Environ 25 min.

Question : où ce joueur perd-il des points ?
À produire : trois figures Plotly dont le titre dit ce qu'elles montrent.
Lancer : uv run python fil_rouge/01_figures.py
"""

import plotly.express as px

from data import familles, load_coups, load_data

df, coups = load_data(), load_coups()
print(df.shape, coups.shape)

# 1. Le score par famille d'ouvertures, la pire en haut (px.bar, familles(df))
ouvertures = familles(df)
fig_ouvertures = px.bar(
    ouvertures,
    x="score",
    y="famille",
    orientation="h",
    hover_data=["n", "gaffes"],
    labels={
        "score": "Score moyen",
        "famille": "Famille d’ouvertures",
        "n": "Parties",
        "gaffes": "Gaffes par partie",
    },
    title="Score moyen par famille d’ouvertures (au moins 30 parties)",
)
fig_ouvertures.update_xaxes(tickformat=".0%", range=[0, 1])
fig_ouvertures.update_yaxes(autorange="reversed")
fig_ouvertures.show()

# 2. La phase de la partie où tombent ses gaffes : ouverture, milieu, finale (coups)
phases = {"opening": "Ouverture", "middlegame": "Milieu", "endgame": "Finale"}
gaffes = (
    coups.loc[coups["jugement"] == "blunder", "phase"]
    .value_counts()
    .reindex(list(phases), fill_value=0)
    .rename(index=phases)
    .rename_axis("phase")
    .reset_index(name="gaffes")
)
fig_gaffes = px.bar(
    gaffes,
    x="phase",
    y="gaffes",
    text_auto=True,
    labels={"phase": "Phase de jeu", "gaffes": "Nombre de gaffes"},
    title="Nombre de gaffes du joueur par phase de jeu",
)
fig_gaffes.show()

# 3. Le score selon l'heure de la partie (heures UTC, au moins 30 parties par heure)
heures = df.groupby("heure", as_index=False).agg(
    n=("partie_id", "size"),
    score=("score", "mean"),
)
heures = heures.loc[heures["n"] >= 30]
fig_heures = px.bar(
    heures,
    x="heure",
    y="score",
    hover_data=["n"],
    labels={"heure": "Heure de début (UTC)", "score": "Score moyen", "n": "Parties"},
    title="Score moyen par heure de début en UTC (au moins 30 parties)",
)
fig_heures.update_xaxes(dtick=1, range=[-0.5, 23.5])
fig_heures.update_yaxes(tickformat=".0%", range=[0, 1])
fig_heures.show()
