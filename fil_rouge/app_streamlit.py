"""Exo 2 : l'appli Streamlit du fil rouge. Environ 1 h.

Question : l'appli que ce joueur ouvrirait chaque semaine pour savoir quoi travailler.
À produire : une barre latérale (cadence, période, couleur), trois st.metric, deux figures
en colonnes. Le titre de la page dit la question, pas « Dashboard ».
Colonnes utilisées : cadence (dont « aucune »), date_jeu (datetime), couleur (white ou black),
score, gaffes_joueur. Si votre load_data() ne les donne pas ainsi, corrigez-la.
Lancer : uv run streamlit run fil_rouge/app_streamlit.py --server.address 127.0.0.1
"""

import pandas as pd
import plotly.express as px
import streamlit as st

from data import DATA, familles, load_coups, load_data

st.set_page_config(page_title="Carnet d’échecs", layout="wide")
print("relance")  # regardez le terminal à chaque clic

VERT = "#375dbe"
TERRE = "#9a4d17"
PHASES = {"opening": "Ouverture", "middlegame": "Milieu", "endgame": "Finale"}

st.html("""
<style>
  [data-testid="stMainBlockContainer"] { max-width: 88rem; padding-top: 3.7rem; }
  .chess-header {
    padding: clamp(1.2rem, 3vw, 2.3rem);
    border-radius: 1rem;
    color: #ffffff;
    background-color: #172b56;
    background-image: linear-gradient(90deg, #172b56 42%, transparent),
      conic-gradient(from 90deg, #ffffff08 25%, transparent 0 50%, #ffffff08 0 75%, transparent 0);
    background-size: auto, 80px 80px;
    margin-bottom: 0.6rem;
  }
  .chess-header p { color: #dce7ff; margin: 0; }
  .chess-header h1 {
    font-family: Georgia, serif; font-weight: 500; color: #ffffff;
    font-size: clamp(1.9rem, 4vw, 3.25rem); line-height: 1.1; margin: .4rem 0 .6rem;
    padding: 0;
  }
  [data-testid="stMetric"] {
    background: var(--secondary-background-color); border: 1px solid #91a2c04d;
    border-radius: .75rem; padding: .85rem 1rem;
  }
  .st-key-ouvertures, .st-key-phases {
    background: var(--secondary-background-color); border: 1px solid #91a2c04d;
    border-radius: .75rem; padding: 1rem;
  }
</style>
<header class="chess-header">
  <p>Carnet d’échecs</p>
  <h1>Que devrais-je travailler aux échecs ?</h1>
  <p>Repère les ouvertures à revoir, situe tes gaffes et explore tes parties.</p>
</header>
""")


@st.cache_data
def charger_donnees():
    return load_data(), load_coups()


@st.cache_data
def charger_archive(fichier):
    return pd.read_csv(DATA / fichier)


parties, coups = charger_donnees()
jours = parties["date_jeu"].dt.date
premier_jour, dernier_jour = jours.min(), jours.max()

with st.sidebar:
    st.header("Périmètre d’analyse")
    options_cadence = sorted(parties["cadence"].unique())
    cadences = st.multiselect(
        "Cadences", options_cadence, placeholder="Toutes les cadences", key="cadences"
    )
    st.caption("Sans sélection, toutes les cadences sont incluses.")
    periode = st.date_input(
        "Période (UTC)",
        value=(premier_jour, dernier_jour),
        min_value=premier_jour,
        max_value=dernier_jour,
        format="DD/MM/YYYY",
        key="periode",
    )
    couleurs = {"Toutes": None, "Blancs": "white", "Noirs": "black"}
    couleur = couleurs[st.radio("Couleur jouée", list(couleurs), key="couleur")]

if len(periode) != 2:
    st.info("Choisis les deux dates de la période.")
    st.stop()

debut, fin = periode
masque = jours.between(debut, fin)
if cadences:
    masque &= parties["cadence"].isin(cadences)
if couleur is not None:
    masque &= parties["couleur"].eq(couleur)
vue = parties.loc[masque]

if vue.empty:
    st.warning("Aucune partie pour ces filtres. Élargis la période ou modifie la sélection.")
    st.stop()

st.caption(
    f"Du {debut:%d/%m/%Y} au {fin:%d/%m/%Y} · UTC · "
    f"{len(vue):,} parties sur {len(parties):,}".replace(",", "\u202f")
)
col_parties, col_score, col_gaffes = st.columns(3)
col_parties.metric("Parties", len(vue))
col_score.metric("Score moyen", f"{vue['score'].mean():.1%}")
col_gaffes.metric("Gaffes par partie", f"{vue['gaffes_joueur'].mean():.2f}")

resultats = vue["resultat"].value_counts()
st.caption(
    f"{resultats.get('victoire', 0)} victoires · {resultats.get('nulle', 0)} nulles · "
    f"{resultats.get('défaite', 0)} défaites. Une nulle vaut un demi-point dans le score."
)

st.header("Pistes de travail")
col_ouvertures, col_phases = st.columns(2, gap="medium")
ouvertures = familles(vue)
with col_ouvertures.container(key="ouvertures"):
    st.subheader("Ouvertures à revoir")
    if ouvertures.empty:
        st.info("Aucune famille d’ouvertures n’atteint 30 parties avec ces filtres.")
    else:
        premiere = ouvertures.iloc[0]
        st.write(
            f"**{premiere['famille']}** ouvre le classement des scores les plus faibles "
            f"avec **{premiere['score']:.1%}** sur {premiere['n']} parties."
        )
        fig_ouvertures = px.bar(
            ouvertures.head(6),
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
            color_discrete_sequence=[VERT],
        )
        fig_ouvertures.update_xaxes(tickformat=".0%", range=[0, 1])
        fig_ouvertures.update_yaxes(autorange="reversed", title=None, automargin=True)
        fig_ouvertures.update_layout(
            height=290,
            margin={"l": 8, "r": 12, "t": 8, "b": 42},
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_ouvertures, key="figure_ouvertures")
        st.caption("Les six scores les plus faibles, parmi les familles jouées au moins 30 fois.")
        with st.expander(f"Classement complet · {len(ouvertures)} familles"):
            st.dataframe(
                ouvertures,
                hide_index=True,
                column_config={
                    "famille": "Famille",
                    "n": "Parties",
                    "score": st.column_config.NumberColumn("Score", format="percent"),
                    "gaffes": st.column_config.NumberColumn("Gaffes / partie", format="%.2f"),
                },
            )

coups_filtres = coups[coups["partie_id"].isin(vue["partie_id"])]
phases_jouees = charger_archive("phases.csv")
phases_jouees = phases_jouees.loc[phases_jouees["phase"].isin(PHASES)].merge(
    vue[["partie_id", "couleur"]], on="partie_id", validate="many_to_one"
)
# Les Blancs jouent les demi-coups pairs. Les bornes de phase sont inclusives.
decalage = phases_jouees["couleur"].eq("black").astype(int)
phases_jouees["coups_joues"] = (phases_jouees["ply_fin"] - decalage) // 2 - (
    phases_jouees["ply_debut"] - 1 - decalage
) // 2
gaffes = phases_jouees.groupby("phase")["coups_joues"].sum().reindex(PHASES, fill_value=0)
gaffes = gaffes.to_frame().join(
    coups_filtres.loc[coups_filtres["jugement"].eq("blunder"), "phase"]
    .value_counts()
    .rename("gaffes")
)
gaffes["gaffes"] = gaffes["gaffes"].fillna(0).astype(int)
gaffes["taux"] = 100 * gaffes["gaffes"] / gaffes["coups_joues"].where(gaffes["coups_joues"] > 0)
gaffes = gaffes.rename(index=PHASES).reset_index()
with col_phases.container(key="phases"):
    st.subheader("Où les gaffes sont-elles fréquentes ?")
    phases_observees = gaffes.loc[gaffes["coups_joues"].gt(0)]
    if not phases_observees.empty:
        phase_principale = phases_observees.loc[phases_observees["taux"].idxmax()]
        st.write(
            f"**{phase_principale['phase']}** · {phase_principale['taux']:.1f} gaffes "
            f"pour 100 coups joués ({phase_principale['gaffes']} au total)."
        )
    else:
        st.write("Aucune phase exploitable dans cette sélection.")
    fig_gaffes = px.bar(
        gaffes,
        x="phase",
        y="taux",
        text_auto=".1f",
        hover_data={"gaffes": True, "coups_joues": True, "taux": ":.1f"},
        labels={
            "phase": "Phase",
            "taux": "Gaffes pour 100 coups",
            "gaffes": "Gaffes",
            "coups_joues": "Coups joués",
        },
        color_discrete_sequence=[TERRE],
    )
    fig_gaffes.update_xaxes(title=None)
    fig_gaffes.update_layout(
        height=290,
        margin={"l": 8, "r": 12, "t": 16, "b": 42},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_gaffes, key="figure_phases")
    st.caption("Le taux tient compte du nombre de coups joués. Les totaux sont visibles au survol.")

st.header("Approfondir l’analyse")
onglet_progression, onglet_relecture, onglet_entrainement = st.tabs(
    ["Progression", "Coups à rejouer", "Entraînement"]
)

with onglet_progression:
    st.subheader("Score mois par mois")
    mensuel = (
        vue.set_index("date_jeu")
        .resample("MS")
        .agg(parties=("score", "size"), score=("score", "mean"))
        .reset_index()
    )
    fig_mensuel = px.line(
        mensuel,
        x="date_jeu",
        y="score",
        markers=True,
        hover_data=["parties"],
        labels={"date_jeu": "Mois", "score": "Score moyen", "parties": "Parties"},
        color_discrete_sequence=[VERT],
    )
    fig_mensuel.update_yaxes(tickformat=".0%", range=[0, 1])
    fig_mensuel.update_layout(height=320, margin={"l": 10, "r": 12, "t": 12, "b": 35})
    st.plotly_chart(fig_mensuel, key="figure_mensuelle")
    st.caption("Survole un mois pour voir son effectif. Les mois sans partie restent vides.")

    st.subheader("Évolution du classement Elo")
    elo = charger_archive("elo.csv")
    elo["date"] = pd.to_datetime(elo["date"], format="mixed", errors="coerce", utc=True)
    elo = elo.loc[
        elo["date"].dt.date.between(debut, fin)
        & (elo["partie_id"].isna() | elo["partie_id"].isin(vue["partie_id"]))
        & elo["source"].isin(["lichess", "chesscom"])
    ].dropna(subset=["elo"])
    elo = elo.loc[elo["cadence"].fillna("aucune").isin(cadences)] if cadences else elo
    if elo.empty:
        st.info("Aucun point Elo en ligne sur cette sélection.")
    else:
        elo["serie"] = elo["source"] + " · " + elo["cadence"].fillna("sans cadence")
        series = elo["serie"].value_counts().index.tolist()
        serie = st.selectbox("Plateforme et cadence", series, key="serie_elo")
        points = elo.loc[elo["serie"].eq(serie)].sort_values("date")
        fig_elo = px.line(
            points,
            x="date",
            y="elo",
            markers=True,
            hover_data=["origine"],
            labels={"date": "Date (UTC)", "elo": "Elo", "origine": "Source du point"},
            color_discrete_sequence=[VERT],
        )
        fig_elo.update_layout(height=300, margin={"l": 12, "r": 12, "t": 16, "b": 35})
        st.plotly_chart(fig_elo, key="figure_elo")
        st.caption(
            "Une série par plateforme et cadence. Les points historiques sans partie sont inclus."
        )

with onglet_relecture:
    graves = coups_filtres.loc[coups_filtres["jugement"].isin(["blunder", "mistake"])]
    st.subheader("Quelles erreurs reviennent ?")
    mecanismes = {
        "material": "Matériel",
        "positional": "Position",
        "mate": "Mat",
        "promotion": "Promotion",
    }
    repartition = (
        graves["mecanisme"]
        .map(mecanismes)
        .fillna("Autre")
        .value_counts()
        .rename_axis("mecanisme")
        .reset_index(name="coups")
    )
    if repartition.empty:
        st.info("Aucune erreur ou gaffe recensée pour ces parties.")
    else:
        fig_mecanismes = px.bar(
            repartition,
            x="coups",
            y="mecanisme",
            orientation="h",
            text_auto=True,
            labels={"coups": "Erreurs et gaffes", "mecanisme": "Mécanisme"},
            color_discrete_sequence=[TERRE],
        )
        fig_mecanismes.update_yaxes(autorange="reversed", title=None)
        fig_mecanismes.update_layout(height=245, margin={"l": 12, "r": 20, "t": 12, "b": 35})
        st.plotly_chart(fig_mecanismes, key="figure_mecanismes")

    st.subheader("Positions qui ont coûté le plus")
    moments = charger_archive("moments_critiques.csv")
    moments = moments.loc[moments["partie_id"].isin(vue["partie_id"])]
    if moments.empty:
        st.info("Aucun moment critique dans cette sélection.")
    else:
        colonnes = [
            "partie_id",
            "numero_coup",
            "phase",
            "coup",
            "meilleur_coup",
            "perte_eval_cp",
        ]
        detail = moments.nlargest(10, "perte_eval_cp")[colonnes].copy()
        detail["phase"] = detail["phase"].map(PHASES).fillna(detail["phase"])
        st.dataframe(
            detail,
            hide_index=True,
            column_config={
                "partie_id": "Partie",
                "numero_coup": "N° du coup",
                "phase": "Phase",
                "coup": "Coup joué",
                "meilleur_coup": "Préféré par le moteur",
                "perte_eval_cp": "Perte d’évaluation (cp)",
            },
        )
        st.caption(
            "Dix coups triés par perte d’évaluation du joueur. 100 cp valent environ un pion."
        )

    revues = charger_archive("revues.csv")
    revues = revues.loc[revues["partie_id"].isin(vue["partie_id"])]
    if not revues.empty:
        st.subheader("Revues du coach")
        for revue in revues.itertuples(index=False):
            with st.expander(f"Partie {revue.partie_id} · {revue.date_revue}"):
                st.write(revue.resume)

with onglet_entrainement:
    st.caption(
        "Historique d’entraînement complet. Ces séances sont indépendantes des filtres des parties."
    )
    puzzles = charger_archive("puzzles.csv")
    valides = puzzles.loc[puzzles["resolu"].isin(["oui", "non"])].copy()
    valides["reussi"] = valides["resolu"].eq("oui")
    sessions = charger_archive("entrainement_ouvertures.csv")
    plans = charger_archive("plans_du_jour.csv").sort_values("date_plan", ascending=False)
    col_tentatives, col_reussite, col_sessions = st.columns(3)
    col_tentatives.metric("Tentatives de puzzles", len(puzzles))
    col_reussite.metric("Puzzles résolus", f"{valides['reussi'].mean():.1%}")
    col_sessions.metric("Séances d’ouvertures", sessions["session"].nunique())

    gauche, droite = st.columns(2, gap="medium")
    with gauche:
        st.subheader("Réussite par thème tactique")
        themes = valides.assign(theme=valides["themes"].str.split(", ")).explode("theme")
        tactiques = [
            "fork",
            "pin",
            "skewer",
            "discoveredAttack",
            "deflection",
            "sacrifice",
            "hangingPiece",
            "mateIn1",
            "mateIn2",
            "backRankMate",
        ]
        libelles = dict(
            zip(
                tactiques,
                [
                    "Fourchette",
                    "Clouage",
                    "Enfilade",
                    "Attaque à la découverte",
                    "Déviation",
                    "Sacrifice",
                    "Pièce en prise",
                    "Mat en un",
                    "Mat en deux",
                    "Mat du couloir",
                ],
                strict=True,
            )
        )
        themes = themes.loc[themes["theme"].isin(libelles)]
        reussite = themes.groupby("theme", as_index=False).agg(
            essais=("reussi", "size"), taux=("reussi", "mean")
        )
        reussite = reussite.loc[reussite["essais"].ge(20)].sort_values("taux")
        reussite["theme"] = reussite["theme"].map(libelles)
        fig_themes = px.bar(
            reussite,
            x="taux",
            y="theme",
            orientation="h",
            hover_data=["essais"],
            labels={"taux": "Résolution", "theme": "Thème", "essais": "Tentatives"},
            color_discrete_sequence=[VERT],
        )
        fig_themes.update_xaxes(range=[0, 1], tickformat=".0%")
        fig_themes.update_yaxes(autorange="reversed", title=None)
        fig_themes.update_layout(height=310, margin={"l": 12, "r": 12, "t": 12, "b": 35})
        st.plotly_chart(fig_themes, key="figure_themes")
        st.caption(
            "Au moins 20 tentatives par thème. Un puzzle peut compter dans plusieurs thèmes."
        )
    with droite:
        st.subheader("Classement aux puzzles")
        puzzle_elo = charger_archive("puzzle_elo.csv").sort_values("date")
        fig_puzzles = px.line(
            puzzle_elo,
            x="date",
            y="elo_apres",
            labels={"date": "Date", "elo_apres": "Elo puzzles"},
            color_discrete_sequence=[VERT],
        )
        fig_puzzles.update_layout(height=310, margin={"l": 12, "r": 12, "t": 12, "b": 35})
        st.plotly_chart(fig_puzzles, key="figure_puzzles")

    with st.expander("Voir un plan du coach"):
        date_plan = st.selectbox("Date du plan", plans["date_plan"], key="date_plan")
        plan = plans.loc[plans["date_plan"].eq(date_plan), "taches"].iloc[0]
        if pd.notna(plan):
            for tache in plan.split(" | "):
                champs = dict(champ.split("=", 1) for champ in tache.split(", ") if "=" in champ)
                sujet = champs.get("label", champs.get("theme"))
                if sujet:
                    detail = {"white": "Blancs", "black": "Noirs"}.get(champs.get("color"), "")
                    theme = libelles.get(champs.get("theme"), champs.get("theme"))
                    st.write(" · ".join(filter(None, [sujet, detail, theme])))

st.header("Explorer les données")
archives = {
    "Parties": ("parties.csv", "Une ligne par partie jouée.", "partie"),
    "Coups jugés": ("coups_juges.csv", "Un coup jugé imprécis, erroné ou gaffé.", "partie"),
    "Phases de jeu": ("phases.csv", "Une ligne par phase atteinte et par partie.", "partie"),
    "Moments critiques": ("moments_critiques.csv", "Les coups ayant coûté le plus.", "partie"),
    "Revues de parties": ("revues.csv", "Les revues rédigées par le coach.", "partie"),
    "Classement Elo": ("elo.csv", "Les points du classement par plateforme et cadence.", "mixte"),
    "Puzzles": ("puzzles.csv", "Les tentatives de problèmes tactiques.", "mixte"),
    "Classement aux puzzles": (
        "puzzle_elo.csv",
        "L’évolution du classement aux puzzles.",
        "archive",
    ),
    "Entraînement d’ouvertures": (
        "entrainement_ouvertures.csv",
        "Les coups travaillés en entraînement.",
        "archive",
    ),
    "Plans du jour": ("plans_du_jour.csv", "Les exercices proposés par le coach.", "archive"),
    "Carnet de conseils": ("conseils.csv", "Le carnet de coaching exporté.", "archive"),
}
choix = st.selectbox("Jeu de données", list(archives), key="archive")
fichier, description, lien = archives[choix]
if fichier == "parties.csv":
    tableau = vue.sort_values("date_jeu", ascending=False)
elif fichier == "coups_juges.csv":
    tableau = coups_filtres
else:
    tableau = charger_archive(fichier)
    if lien == "partie":
        tableau = tableau.loc[tableau["partie_id"].isin(vue["partie_id"])]
    elif lien == "mixte":
        tableau = tableau.loc[
            tableau["partie_id"].isna() | tableau["partie_id"].isin(vue["partie_id"])
        ]

perimetres = {
    "partie": "Suit les filtres des parties.",
    "mixte": "Les entrées sans partie associée restent visibles.",
    "archive": "Archive indépendante des filtres des parties.",
}
st.caption(f"{description} {perimetres[lien]}")
if tableau.empty:
    st.info("Aucune ligne disponible dans ce jeu de données pour ce périmètre.")
else:
    st.dataframe(
        tableau,
        hide_index=True,
        height=330,
        column_config={
            "partie_id": "Partie",
            "date_jeu": st.column_config.DatetimeColumn("Date de jeu (UTC)", timezone="UTC"),
            "ouverture_famille": "Famille d’ouvertures",
            "gaffes_joueur": "Gaffes",
            "resultat": "Résultat",
        },
    )
    st.download_button(
        "Télécharger ces données en CSV",
        data=tableau.to_csv(index=False).encode("utf-8-sig"),
        file_name=fichier,
        mime="text/csv",
        on_click="ignore",
    )
