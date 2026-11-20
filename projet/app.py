"""Phares : où implanter les prochaines maisons de santé ?

Lancer depuis la racine du dépôt : uv run python -m projet.app
"""

import gzip
import json
import os
import tempfile
import threading
import time
from functools import lru_cache
from pathlib import Path

import diskcache
from dash import (
    ALL,
    ClientsideFunction,
    Dash,
    DiskcacheManager,
    Input,
    Output,
    State,
    callback,
    clientside_callback,
    ctx,
    dcc,
    html,
    no_update,
)
from dash.exceptions import PreventUpdate
from flask import Response, abort
from flask_caching import Cache

from . import assistant, donnees, outils, scenario, tuiles
from .theme import CARTE

MAPLIBRE = "https://unpkg.com/maplibre-gl@5.7.0/dist/maplibre-gl"
POLICES = (
    "https://fonts.googleapis.com/css2?family=Bricolage+Grotesque:opsz,wght@12..96,400..800"
    "&family=Figtree:wght@400;500;600;700&display=swap"
)
PALIERS_POPULATION = [0, 500, 1000, 2000, 5000, 10000]
NOMBRE_MAX = 60
MENU = {
    "maxHeight": 300,
    "labels": {
        "select_all": "Tout cocher",
        "deselect_all": "Tout décocher",
        "selected_count": "{num_selected} choisi(s)",
        "search": "Rechercher",
        "clear_search": "Effacer la recherche",
        "clear_selection": "Effacer la sélection",
        "no_options_found": "Aucun résultat",
    },
}
OCCUPE = [(Output("activite", "className"), "activite visible", "activite")]

# Les calculs longs (mode Sûr) tournent dans un processus à part, avec une progression.
taches = DiskcacheManager(diskcache.Cache(str(Path(tempfile.gettempdir()) / "phares-taches")))
app = Dash(
    __name__,
    title="Phares · où implanter les maisons de santé",
    update_title=None,
    external_scripts=[f"{MAPLIBRE}.js"],
    external_stylesheets=[f"{MAPLIBRE}.css", POLICES],
    suppress_callback_exceptions=True,
    background_callback_manager=taches,
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"}],
)
server = app.server
cache = Cache(server, config={"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 3600})


# ---------------------------------------------------------------- données servies à la carte


def _json_compresse(objet) -> bytes:
    return gzip.compress(json.dumps(objet, ensure_ascii=False, separators=(",", ":")).encode(), 5)


def _reponse(corps: bytes) -> Response:
    entetes = {"Content-Encoding": "gzip", "Cache-Control": "public, max-age=3600"}
    return Response(corps, mimetype="application/json", headers=entetes)


@lru_cache(maxsize=1)
def _communes_json() -> bytes:
    df = donnees.communes()
    return _json_compresse(
        {
            "lon": df["lon"].round(4).tolist(),
            "lat": df["lat"].round(4).tolist(),
            "pop": df["population_reference"].tolist(),
            "dep": df["dep"].tolist(),
            "nom": df["commune"].tolist(),
            "code": df["code_insee"].tolist(),
            "departements": donnees.departements(),
            "regions": donnees.REGIONS,
        }
    )


@lru_cache(maxsize=16)
def _gros_json(seuil: float) -> bytes:
    """Vue France : cases de 5 km, habitants loin des soins et autres, pour une distance donnée."""
    df = donnees.carreaux_5km(seuil)
    return _json_compresse(
        {
            "lon": df["lon"].round(3).tolist(),
            "lat": df["lat"].round(3).tolist(),
            "loin": df["loin"].round().astype(int).tolist(),
            "proche": df["proche"].round().astype(int).tolist(),
            "dep": df["dep"].tolist(),
        }
    )


@lru_cache(maxsize=1)
def _maisons_json() -> bytes:
    df = donnees.maisons()
    return _json_compresse(
        {
            "lon": df["lon"].round(5).tolist(),
            "lat": df["lat"].round(5).tolist(),
            "nom": df["nom"].str.title().tolist(),
        }
    )


@server.route("/donnees/communes")
def route_communes():
    return _reponse(_communes_json())


@server.route("/donnees/gros/<float:seuil>")
def route_gros(seuil):
    if not 1 <= seuil <= 50:
        abort(404)
    return _reponse(_gros_json(float(seuil)))


@server.route("/donnees/maisons")
def route_maisons():
    return _reponse(_maisons_json())


def _par_departement(fabrique, dep):
    try:
        return _reponse(fabrique(dep))
    except KeyError:
        abort(404)


@server.route("/tuiles/<niveau>/<int:z>/<int:x>/<int:y>.pbf")
def route_tuile(niveau, z, x, y):
    """Habitants par carreau de 1 km ou de 200 m, pour la seule tuile demandée par la carte."""
    try:
        corps = tuiles.tuile(niveau, z, x, y)
    except KeyError:
        abort(404)
    entetes = {"Cache-Control": "public, max-age=3600"}
    if corps is None:
        return Response(status=204, headers=entetes)
    return Response(
        corps, mimetype="application/x-protobuf", headers={**entetes, "Content-Encoding": "gzip"}
    )


@server.route("/donnees/desserte/<int:rang>/<float:seuil>")
def route_desserte(rang, seuil):
    """Communes à portée d'une maison de santé existante, pour la montrer en volumes sur la carte."""
    if not 1 <= seuil <= 50:
        abort(404)
    try:
        return _reponse(_json_compresse(scenario.desserte_maison(rang, float(seuil))))
    except ValueError:
        abort(404)


@server.route("/donnees/contours/<dep>")
def route_contours(dep):
    return _par_departement(lambda d: _json_compresse(donnees.contours(d)), dep)


def _prechauffer():
    """Charge les habitants et prépare la première vue dès le démarrage du serveur."""
    _communes_json()
    _maisons_json()
    _gros_json(10.0)
    tuiles.niveau("moyens")
    tuiles.niveau("fins")


if not os.environ.get("PHARES_SANS_PRECHAUFFAGE"):
    threading.Thread(target=_prechauffer, daemon=True).start()


@cache.memoize()
def bilan_en_cache(codes, territoires, seuil):
    return scenario.bilan(list(codes), list(territoires), seuil)


# ---------------------------------------------------------------- mise en page


def nombre_fr(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def km_fr(d) -> str:
    return f"{d:.1f} km".replace(".", ",")


def _bloc(titre, aide, *contenu):
    return html.Div([html.H3(titre), html.P(aide, className="aide"), *contenu], className="bloc")


def _cadre():
    return html.Section(
        className="cadre",
        children=[
            html.Span("Périmètre", className="surtitre"),
            # L'arborescence France > régions > départements est construite par carte.js.
            html.Div(id="perimetre-zone", className="perimetre-zone"),
            html.Div(
                className="reglage",
                children=[
                    html.Span("Loin des soins au-delà de"),
                    dcc.Slider(
                        id="seuil",
                        min=5,
                        max=20,
                        step=1,
                        value=10,
                        marks={5: "5 km", 10: "10", 15: "15", 20: "20 km"},
                        allow_direct_input=False,
                    ),
                ],
            ),
            html.Div(
                className="constat",
                children=[
                    html.Strong("—", id="constat-pop"),
                    html.Span(" habitants vivent à plus de "),
                    html.Span("10", id="constat-seuil"),
                    html.Span(" km d'une maison de santé, soit "),
                    html.Span("—", id="constat-part"),
                    html.Span(" % du périmètre."),
                ],
                **{"aria-live": "polite"},
            ),
            html.Div(id="erreur-cadre", className="erreur", role="alert"),
        ],
    )


def _maisons():
    return html.Section(
        id="vue-maisons",
        className="vue",
        children=[
            html.Div(
                className="grand-chiffre",
                children=[
                    html.Span("+0", id="kpi-gain", className="chiffre"),
                    html.Span("habitants rapprochés par mes maisons", className="chiffre-legende"),
                ],
                **{"aria-live": "polite"},
            ),
            html.Div(
                className="mesures",
                children=[
                    html.Div(
                        [html.Strong("0", id="kpi-reste"), html.Span("habitants encore loin")]
                    ),
                    html.Div(
                        [
                            html.Strong("0 %", id="kpi-part"),
                            html.Span("des habitants loin rapprochés"),
                        ]
                    ),
                ],
            ),
            html.Div(id="liste-maisons", className="maisons"),
            dcc.Dropdown(
                id="recherche",
                options=[],
                placeholder="Chercher une commune…",
                className="selecteur",
                **MENU,
            ),
            html.P(
                "Ou zoomez et cliquez sur une commune. Une balise se déplace en la glissant.",
                className="aide",
            ),
            html.Div(id="statut", className="statut"),
        ],
    )


def _proposer():
    return html.Section(
        id="vue-proposer",
        className="vue",
        children=[
            html.Div(
                className="phrase",
                children=[
                    html.Span("Je veux"),
                    dcc.Input(
                        id="nombre",
                        type="number",
                        min=1,
                        max=NOMBRE_MAX,
                        step=1,
                        value=6,
                        className="nombre",
                    ),
                    html.Span("maisons en tout, les miennes comprises."),
                ],
            ),
            html.Details(
                id="avances",
                className="avances",
                children=[
                    html.Summary(
                        [
                            html.Span("Critères avancés"),
                            html.Span(id="avances-compte", className="pastille"),
                        ]
                    ),
                    _bloc(
                        "Répartition",
                        "Imposez un nombre de maisons par région ou par département.",
                        html.Div(
                            className="composeur",
                            children=[
                                dcc.Dropdown(
                                    id="regle-territoire",
                                    placeholder="Territoire",
                                    className="selecteur",
                                    **MENU,
                                ),
                                dcc.Dropdown(
                                    id="regle-type",
                                    options=[
                                        {"label": v, "value": k} for k, v in scenario.TYPES.items()
                                    ],
                                    value="exact",
                                    clearable=False,
                                    searchable=False,
                                    className="selecteur",
                                    **MENU,
                                ),
                                dcc.Input(
                                    id="regle-n",
                                    type="number",
                                    min=0,
                                    max=NOMBRE_MAX,
                                    step=1,
                                    value=1,
                                    className="nombre petit",
                                ),
                                html.Button("Ajouter", id="regle-ajouter", className="secondaire"),
                            ],
                        ),
                        html.Div(id="regles-liste", className="jetons"),
                    ),
                    _bloc(
                        "Commune d'accueil",
                        "Population minimale de la commune qui reçoit une maison.",
                        dcc.Slider(
                            id="pop-min",
                            min=0,
                            max=len(PALIERS_POPULATION) - 1,
                            step=1,
                            value=0,
                            marks={i: nombre_fr(p) for i, p in enumerate(PALIERS_POPULATION)},
                            allow_direct_input=False,
                        ),
                        dcc.Checklist(
                            id="exclure-equipees",
                            options=[
                                {
                                    "label": "Écarter les communes qui ont déjà une maison de santé",
                                    "value": "oui",
                                }
                            ],
                            value=[],
                        ),
                    ),
                    _bloc(
                        "Espacement",
                        "Distance minimale entre deux nouvelles maisons (0 = libre).",
                        dcc.Slider(
                            id="espacement",
                            min=0,
                            max=50,
                            step=5,
                            value=0,
                            marks={0: "0", 10: "10", 20: "20", 30: "30", 40: "40", 50: "50 km"},
                            allow_direct_input=False,
                        ),
                    ),
                    _bloc(
                        "Communes interdites",
                        "Elles ne recevront jamais de maison proposée. Pour en imposer une, posez-la vous-même.",
                        dcc.Dropdown(
                            id="interdites",
                            multi=True,
                            options=[],
                            className="selecteur",
                            placeholder="Tapez un nom de commune…",
                            **MENU,
                        ),
                    ),
                    _bloc(
                        "Qui compte ?",
                        "Zone sous-dotée : moins de 2,5 consultations de généraliste accessibles par "
                        "habitant et par an (DREES 2024).",
                        dcc.RadioItems(
                            id="priorite",
                            options=[
                                {"label": v, "value": k} for k, v in scenario.PRIORITES.items()
                            ],
                            value="tous",
                        ),
                    ),
                ],
            ),
            html.Div(
                id="mode-bloc",
                className="mode",
                children=[
                    html.H3("Périmètre vaste : quel calcul ?"),
                    dcc.RadioItems(
                        id="mode",
                        options=[
                            {
                                "label": "Rapide · réponse immédiate, optimum non prouvé",
                                "value": "rapide",
                            },
                            {"label": "Sûr · plus long, écart à l'optimum garanti", "value": "sur"},
                        ],
                        value="rapide",
                    ),
                ],
            ),
            # Pendant un calcul, la progression prend la place des boutons.
            html.Div(
                id="progression",
                className="progression",
                children=[
                    html.Progress(id="progres", value="0", max="100"),
                    html.Span("Calcul en cours…", id="progres-texte"),
                    html.Button("Annuler", id="btn-annuler", className="secondaire"),
                ],
            ),
            html.Div(
                id="actions",
                className="actions",
                children=[
                    html.Button(
                        [html.Span("Compléter mes maisons"), html.Span(className="cta-onde")],
                        id="btn-completer",
                        className="cta",
                    ),
                    html.Button("Remplacer tout", id="btn-remplacer", className="secondaire"),
                ],
            ),
            html.Div(id="erreur", className="erreur", role="alert"),
        ],
    )


def _fiche():
    return html.Section(
        id="vue-fiche",
        className="vue",
        children=[
            html.Button("← Retour", id="btn-retour", className="retour"),
            html.Div(id="fiche"),
        ],
    )


def _methode():
    return html.Aside(
        id="methode",
        className="tiroir",
        children=[
            html.Button("Fermer", id="btn-methode-fermer", className="secondaire"),
            dcc.Markdown(
                """
## Méthode et limites

**La décision.** Un élu ou un chargé de planification choisit dans quelles communes soutenir
l'ouverture de maisons de santé.

**Les habitants sont là où ils vivent.** La population est connue par carreau de 200 m (Insee,
2021), recalée pour que chaque commune retrouve sa population 2023. Un habitant est « loin » si
son carreau est à plus de la distance choisie d'une maison de santé existante. La carte regroupe
les carreaux par 5 km, puis 1 km, puis 200 m à mesure qu'on zoome.

**Ce que rapporte une maison.** Les habitants loin des soins qui se trouvent dans son rayon.
« Seule » : ce qu'elle rapprocherait sans les autres. « En propre » : ce qu'on perdrait en la
retirant. Un habitant couvert par deux nouvelles maisons est compté une fois dans le total.

**Une nouvelle maison est placée au centre de sa commune** : la décision porte sur une commune,
pas sur une parcelle. Les maisons existantes sont à leur adresse.

**Distances à vol d'oiseau**, pas des temps de trajet.

**Le calcul automatique** choisit les communes d'accueil qui rapprochent le plus d'habitants et
indique s'il a prouvé l'optimum. Sur un périmètre vaste, « Rapide » répond sans preuve ; « Sûr »
prend quelques secondes de plus et garantit l'écart. Seuls les habitants du périmètre comptent.

**Sources.** Population : Insee, Filosofi 2021 au carreau de 200 m (78,7 % des carreaux sont
estimés par l'Insee, ils portent 19,7 % des habitants) ; populations communales 2023. Maisons de
santé : FINESS, catégorie 603, 4 mai 2026. Accès aux généralistes : indicateur APL de la DREES,
2024. Contours : Etalab 2026. Fond de carte : OpenFreeMap, © OpenStreetMap. France
métropolitaine, Corse comprise.

**Ce que l'appli ne sait pas.** Les médecins disponibles, le foncier, les coûts, les temps de
route. Elle aide à présélectionner, pas à décider seule.
"""
            ),
        ],
    )


def _legende():
    def entree(classe, texte):
        return html.Li([html.I(className=classe), texte])

    return html.Div(
        className="legende",
        children=[
            html.Ul(
                [
                    entree("l-braise", "Habitants loin d'une maison de santé"),
                    entree("l-couvert", "Habitants déjà couverts"),
                    entree("l-phare", "Rapprochés par mes maisons"),
                    entree("l-site", "Nouvelle maison"),
                ]
            ),
            dcc.Checklist(
                id="calques",
                # L'icône de cette entrée est dessinée par la feuille de style.
                options=[{"label": "Maisons existantes", "value": "msp"}],
                value=["msp"],
                inline=True,
            ),
        ],
    )


EXEMPLES = (
    "6 maisons dans le Loiret",
    "8 maisons en Bretagne, au moins une par département",
    "10 maisons en Occitanie espacées de 30 km, priorité aux 65 ans et plus",
)
DEMANDES = (
    "le nombre de maisons (1 à 60) et le périmètre : France, régions, départements ;",
    "la distance au-delà de laquelle on est loin des soins (5 à 20 km) ;",
    "une répartition : exactement, au moins ou au plus tant de maisons par région ou département ;",
    "les communes d'accueil : population minimale, déjà équipées écartées, espacement, interdites ;",
    "qui compte : tous les habitants, les zones qui manquent de généralistes, les 65 ans et plus ;",
    "« repars de zéro » pour remplacer vos maisons au lieu de les compléter.",
)


def _chat():
    """La bulle en bas à droite et sa fenêtre : on y écrit sa demande, l'assistant règle les critères."""
    aide = html.Div(
        className="chat-aide",
        children=[
            html.P(
                "Écrivez votre demande : l'assistant règle les critères et lance le calcul. "
                "Il ne choisit aucune commune et ne produit aucun chiffre."
            ),
            html.B("Vous pouvez préciser"),
            html.Ul([html.Li(ligne) for ligne in DEMANDES]),
            html.B("Exemples"),
            html.Div(
                [
                    html.Button(texte, id={"type": "exemple", "i": i}, className="chat-exemple")
                    for i, texte in enumerate(EXEMPLES)
                ],
                className="chat-exemples",
            ),
        ],
    )
    return [
        html.Section(
            id="chat-fenetre",
            className="chat",
            **{"aria-label": "Assistant"},
            children=[
                html.Header(
                    className="chat-tete",
                    children=[
                        html.B("Assistant"),
                        html.Button(
                            "?",
                            id="chat-aide-btn",
                            className="icone",
                            title="Ce que je peux demander",
                        ),
                        html.Button("×", id="chat-fermer", className="icone", title="Fermer"),
                    ],
                ),
                html.Div(id="chat-fil", className="chat-fil", **{"aria-live": "polite"}),
                aide,
                # Affiché par le navigateur dès l'envoi, sans attendre le serveur : la demande et une attente.
                html.Div(
                    className="chat-envoi",
                    children=[
                        html.P(id="chat-echo", className="chat-vous"),
                        html.Div(
                            [html.I(), html.I(), html.I()],
                            className="chat-assistant chat-attente",
                            title="L'assistant lit votre demande",
                        ),
                    ],
                ),
                html.Div(
                    className="chat-saisie",
                    children=[
                        dcc.Input(
                            id="chat-texte",
                            type="text",
                            debounce=True,
                            maxLength=600,
                            className="chat-texte",
                            placeholder="8 maisons en Bretagne, au moins une par département…",
                        ),
                        html.Button("Envoyer", id="chat-envoyer", className="secondaire"),
                    ],
                ),
            ],
        ),
        html.Button(
            html.I(), id="btn-chat", className="chat-bulle", title="Demander à l'assistant"
        ),
    ]


def serve_layout():
    return html.Div(
        id="appli",
        className="appli onglet-maisons",
        children=[
            html.Div(id="carte", className="carte", **{"data-theme": json.dumps(CARTE)}),
            html.Div(id="voile", className="voile"),
            html.Div(
                [html.I(), html.Span("Chargement…")],
                id="activite",
                className="activite",
                role="status",
            ),
            html.Aside(
                id="panneau",
                className="panneau",
                children=[
                    html.Header(
                        className="marque",
                        children=[
                            html.Span(className="marque-logo", **{"aria-hidden": "true"}),
                            html.Div(
                                [
                                    html.H1("Phares"),
                                    html.P("Où implanter les prochaines maisons de santé ?"),
                                ]
                            ),
                            html.Button(
                                "i", id="btn-methode", className="icone", title="Méthode et limites"
                            ),
                            html.Button(
                                "‹", id="btn-replier", className="icone", title="Replier le panneau"
                            ),
                        ],
                    ),
                    _cadre(),
                    html.Nav(
                        className="onglets",
                        children=[
                            html.Button("Mes maisons", id="onglet-maisons", className="onglet"),
                            html.Button(
                                "Proposer automatiquement", id="onglet-proposer", className="onglet"
                            ),
                        ],
                    ),
                    _maisons(),
                    _proposer(),
                    _fiche(),
                ],
            ),
            html.Button(
                "›", id="btn-deplier", className="icone deplier", title="Déplier le panneau"
            ),
            _legende(),
            *_chat(),
            html.Div(id="bulle", className="bulle"),
            _methode(),
            dcc.Store(id="perimetre", data=[donnees.FRANCE]),
            dcc.Store(id="maisons", data=[]),
            dcc.Store(id="bilan"),
            dcc.Store(id="commune-active"),
            dcc.Store(id="regles", data=[]),
            dcc.Store(id="proposition"),
            dcc.Store(id="demande-sure"),
            dcc.Store(id="chat", data=[]),
            dcc.Store(id="chat-demande"),
            dcc.Store(id="lancer"),
            dcc.Location(id="adresse", refresh=False),
        ],
    )


app.layout = serve_layout


# ---------------------------------------------------------------- bilan des maisons posées


@callback(
    Output("bilan", "data"),
    Output("erreur-cadre", "children"),
    Input("maisons", "data"),
    Input("perimetre", "data"),
    Input("seuil", "value"),
    running=OCCUPE,
)
def evaluer(codes, perimetre, seuil):
    """Toute modification de la liste, du périmètre ou de la distance recalcule le bilan."""
    try:
        bilan = bilan_en_cache(tuple(codes or ()), tuple(perimetre or ()), seuil)
    except ValueError as erreur:
        return no_update, str(erreur)
    return {**bilan, "jeton": time.time()}, ""


@callback(
    Output("liste-maisons", "children"),
    Output("statut", "children"),
    Output("onglet-maisons", "children"),
    Input("bilan", "data"),
    Input("proposition", "data"),
)
def afficher_maisons(bilan, proposition):
    if not bilan:
        raise PreventUpdate
    maisons = bilan["maisons"]
    if not maisons:
        vide = html.P(
            "Aucune maison posée. Ouvrez la fiche d'une commune pour en poser une, "
            "ou laissez l'outil en proposer.",
            className="vide",
        )
        return vide, [], "Mes maisons"
    plus_grande = max(m["seule"] for m in maisons) or 1
    cartes = [
        html.Div(
            className="maison",
            children=[
                html.Button(
                    id={"type": "ouvrir", "code": m["code"]},
                    className="maison-corps",
                    title="Ouvrir la fiche",
                    children=[
                        html.Span(str(m["k"]), className="site-num"),
                        html.Span(
                            [
                                html.Strong(m["commune"]),
                                html.Small(
                                    f"{m['dep']} · {m['dep_nom']}"
                                    + (" · hors périmètre" if m["hors_perimetre"] else "")
                                ),
                            ],
                            className="site-nom",
                        ),
                        html.Span(
                            [
                                html.Strong(f"+{nombre_fr(m['seule'])}"),
                                html.Small(f"dont {nombre_fr(m['propre'])} en propre"),
                            ],
                            className="site-gain",
                        ),
                        html.Span(
                            className="site-barre",
                            style={"--part": round(m["seule"] / plus_grande, 3)},
                        ),
                        html.Span(
                            className="site-barre propre",
                            style={"--part": round(m["propre"] / plus_grande, 3)},
                        ),
                    ],
                ),
                html.Button(
                    "×",
                    id={"type": "retirer", "code": m["code"]},
                    className="maison-retirer",
                    title=f"Retirer la maison de {m['commune']}",
                ),
            ],
        )
        for m in maisons
    ]
    return cartes, _statut(bilan, proposition), f"Mes maisons · {len(maisons)}"


def _statut(bilan, proposition):
    """Ce qu'on sait de la qualité de la liste, tant qu'elle est celle que l'outil a proposée."""
    codes = [m["code"] for m in bilan["maisons"]]
    if not proposition or proposition["codes"] != codes:
        return []
    ecart = proposition["ecart_max"]
    if proposition["optimal"]:
        lignes = [
            html.P(
                [
                    html.B("✓ Optimum prouvé. "),
                    "Aucun autre choix ne fait mieux, à un habitant près.",
                ]
            )
        ]
    elif ecart is not None and ecart <= 0.05 * max(proposition["objectif"], 1):
        texte = f"Aucun autre choix ne rapprocherait plus de {nombre_fr(ecart)} habitant{'s' if ecart > 1 else ''} de plus."
        lignes = [html.P([html.B("✓ Écart garanti. "), texte])]
    else:
        lignes = [
            html.P(
                [html.B("≈ Proposition non prouvée. "), "Le mode Sûr donne une garantie chiffrée."]
            )
        ]
    if proposition["priorite"] != "tous":
        lignes.append(
            html.P(f"Objectif du calcul : {scenario.PRIORITES[proposition['priorite']].lower()}.")
        )
    return lignes


@callback(
    Output("maisons", "data"),
    Input({"type": "poser", "code": ALL}, "n_clicks"),
    Input({"type": "retirer", "code": ALL}, "n_clicks"),
    State("maisons", "data"),
    prevent_initial_call=True,
)
def modifier_maisons(_poser, _retirer, codes):
    """Pose ou retire une maison ; la liste est la seule mémoire des nouvelles maisons."""
    if not ctx.triggered or not ctx.triggered[0]["value"]:
        raise PreventUpdate
    codes = codes or []
    code = ctx.triggered_id["code"]
    if ctx.triggered_id["type"] == "poser":
        return codes if code in codes else [*codes, code]
    return [c for c in codes if c != code]


# ---------------------------------------------------------------- fiches


def _lignes_maisons(maisons):
    """Liste cliquable de maisons de santé existantes."""
    return html.Ol(
        className="proches",
        children=[
            html.Li(
                html.Button(
                    id={"type": "ouvrir", "code": f"msp:{m['i']}"},
                    className="proche",
                    title="Ouvrir la fiche de cette maison de santé",
                    children=[
                        html.I(className="l-msp"),
                        html.Span(
                            [
                                html.Strong(m["nom"]),
                                html.Small(
                                    m["commune"] + (" · dans la commune" if m.get("ici") else "")
                                ),
                            ],
                            className="site-nom",
                        ),
                        html.Span(km_fr(m["km"]), className="commune-pop"),
                    ],
                )
            )
            for m in maisons
        ],
    )


def _fiche_commune(f, seuil):
    etiquettes = []
    if f["posee"]:
        etiquettes.append(html.Span(f"Maison n° {f['k']}", className="etiquette forte"))
    if f["hors_perimetre"]:
        etiquettes.append(html.Span("Hors périmètre", className="etiquette"))
    if f["nb_msp"]:
        etiquettes.append(html.Span("A déjà une maison de santé", className="etiquette"))
    if f["posee"]:
        chiffre, legende = f["seule"], "habitants rapprochés par cette maison seule"
        mesures = [
            html.Div(
                [
                    html.Strong(nombre_fr(f["propre"])),
                    html.Span("en propre : perdus si on la retire"),
                ]
            ),
            html.Div(
                [
                    html.Strong(nombre_fr(f["seule"] - f["propre"])),
                    html.Span("aussi couverts par une autre"),
                ]
            ),
        ]
        action = html.Button(
            "Retirer cette maison",
            id={"type": "retirer", "code": f["code"]},
            className="secondaire large",
        )
    else:
        chiffre, legende = f["ajout"], "habitants de plus si on pose une maison ici"
        mesures = [
            html.Div([html.Strong(nombre_fr(f["seule"])), html.Span("si elle était seule")]),
            html.Div(
                [
                    html.Strong(nombre_fr(f["seule"] - f["ajout"])),
                    html.Span("déjà couverts par mes maisons"),
                ]
            ),
        ]
        action = html.Button(
            [html.Span("Poser une maison ici"), html.Span(className="cta-onde")],
            id={"type": "poser", "code": f["code"]},
            className="cta",
        )
    if f["apl"] is None:
        acces = "non disponible"
    else:
        acces = f"{f['apl']:.1f}".replace(".", ",") + (
            " · zone sous-dotée" if f["apl"] < donnees.SEUIL_APL else ""
        )
    mediane = "—" if f["distance_mediane"] is None else km_fr(f["distance_mediane"])
    part_aines = round(100 * f["aines"] / f["population"]) if f["population"] else 0
    noms = donnees.communes().set_index("code_insee")["commune"]
    plus_grande = max((h for _, h in f["communes"]), default=1) or 1
    return [
        html.Div(etiquettes, className="etiquettes"),
        html.H2(f["commune"]),
        html.P(f"{f['dep']} · {f['dep_nom']} · {f['region']}", className="aide"),
        html.Div(
            className="grand-chiffre",
            children=[
                html.Span(f"+{nombre_fr(chiffre)}", className="chiffre"),
                html.Span(legende, className="chiffre-legende"),
            ],
        ),
        html.Div(mesures, className="mesures"),
        action,
        html.H3("La commune"),
        html.Dl(
            className="reperes",
            children=[
                html.Div([html.Dt("Population (2023)"), html.Dd(nombre_fr(f["population"]))]),
                html.Div(
                    [
                        html.Dt(f"Habitants à plus de {seuil:g} km d'une maison"),
                        html.Dd(nombre_fr(f["loin"])),
                    ]
                ),
                html.Div([html.Dt("Distance médiane à une maison"), html.Dd(mediane)]),
                html.Div([html.Dt("65 ans et plus"), html.Dd(f"{part_aines} %")]),
                html.Div(
                    [
                        html.Dt("Consultations de généraliste accessibles par habitant et par an"),
                        html.Dd(acces),
                    ]
                ),
            ],
        ),
        html.H3(
            f"Communes les plus rapprochées ({min(5, f['nb_communes'])} sur {f['nb_communes']})"
        ),
        html.Ol(
            className="communes",
            children=[
                html.Li(
                    [
                        html.Span(noms.get(c, c), className="commune-nom"),
                        html.Span(f"{nombre_fr(h)} hab.", className="commune-pop"),
                        html.Span(
                            className="site-barre", style={"--part": round(h / plus_grande, 3)}
                        ),
                    ],
                    # Le code sert à la carte : survoler la ligne allume la tour de la commune.
                    **{"data-code": c},
                )
                for c, h in f["communes"]
            ],
        ),
        html.H3("Maisons de santé les plus proches"),
        _lignes_maisons(f["maisons_proches"]),
    ]


def _fiche_maison(m, seuil):
    return [
        html.Div(
            html.Span("Maison de santé existante", className="etiquette existante"),
            className="etiquettes",
        ),
        html.H2(m["nom"]),
        html.P(f"{m['commune']} · {m['dep']} · {m['dep_nom']}", className="aide"),
        html.Div(
            className="grand-chiffre existant",
            children=[
                html.Span(nombre_fr(m["habitants"]), className="chiffre"),
                html.Span(
                    f"habitants vivent à moins de {seuil:g} km de cette maison",
                    className="chiffre-legende",
                ),
            ],
        ),
        html.Button(
            f"Ouvrir la fiche de {m['commune']}",
            id={"type": "ouvrir", "code": m["code"]},
            className="secondaire large",
        ),
        html.Dl(
            className="reperes",
            children=[html.Div([html.Dt("Numéro FINESS"), html.Dd(m["finess"])])],
        ),
        html.H3("Maisons de santé voisines"),
        _lignes_maisons(m["voisines"]),
        html.P("Une maison existante ne se déplace pas et ne se retire pas.", className="aide"),
    ]


@callback(
    Output("fiche", "children"),
    Input("commune-active", "data"),
    Input("bilan", "data"),
    State("maisons", "data"),
    State("perimetre", "data"),
    State("seuil", "value"),
    running=OCCUPE,
)
def afficher_fiche(actif, _bilan, codes, perimetre, seuil):
    """La fiche d'une commune, ou d'une maison de santé existante (identifiant « msp:rang »)."""
    if not actif:
        raise PreventUpdate
    try:
        if actif.startswith("msp:"):
            return _fiche_maison(scenario.fiche_maison(int(actif[4:]), seuil), seuil)
        return _fiche_commune(scenario.fiche(actif, codes or [], perimetre or [], seuil), seuil)
    except ValueError as erreur:
        return html.P(str(erreur), className="erreur")


def _recherche(identifiant, dans_le_perimetre):
    @callback(
        Output(identifiant, "options"),
        Input(identifiant, "search_value"),
        State(identifiant, "value"),
        State("perimetre", "data"),
    )
    def chercher(texte, choisies, perimetre):
        if not texte:
            raise PreventUpdate
        choisies = [choisies] if isinstance(choisies, str) else choisies or []
        deps = donnees.deps_de(perimetre) if dans_le_perimetre else None
        trouvees = scenario.chercher_communes(texte, deps)
        return scenario.libelles_communes(choisies) + [
            o for o in trouvees if o["value"] not in choisies
        ]

    return chercher


chercher_interdites = _recherche("interdites", True)
chercher_commune = _recherche("recherche", False)


# ---------------------------------------------------------------- proposition automatique


@callback(
    Output("regle-territoire", "options"),
    Output("regle-territoire", "value"),
    Input("perimetre", "data"),
)
def proposer_territoires(perimetre):
    """Les règles de répartition portent sur les régions et départements du périmètre."""
    return donnees.options_territoires(dans=donnees.deps_de(perimetre)), None


@callback(Output("mode-bloc", "className"), Input("perimetre", "data"))
def proposer_mode(perimetre):
    """Le choix Rapide / Sûr n'apparaît que si le périmètre est trop vaste pour une preuve immédiate."""
    return "mode visible" if scenario.vaste(perimetre) else "mode"


@callback(
    Output("regles", "data"),
    Input("regle-ajouter", "n_clicks"),
    Input({"type": "regle-suppr", "i": ALL}, "n_clicks"),
    State("regle-territoire", "value"),
    State("regle-type", "value"),
    State("regle-n", "value"),
    State("regles", "data"),
    prevent_initial_call=True,
)
def modifier_regles(_ajout, suppressions, territoire, genre, n, regles):
    if ctx.triggered_id == "regle-ajouter":
        if not territoire or n is None:
            raise PreventUpdate
        autres = [r for r in regles if r["t"] != territoire]
        return [*autres, {"t": territoire, "type": genre, "n": int(n)}]
    if not any(suppressions):
        raise PreventUpdate
    return [r for i, r in enumerate(regles) if i != ctx.triggered_id["i"]]


@callback(Output("regles-liste", "children"), Input("regles", "data"))
def afficher_regles(regles):
    return [
        html.Button(
            [scenario.libelle_regle(r), html.Span("×", **{"aria-hidden": "true"})],
            id={"type": "regle-suppr", "i": i},
            className="jeton",
            title="Retirer cette règle",
        )
        for i, r in enumerate(regles)
    ]


@cache.memoize()
def proposition_en_cache(demande: str, sur: bool):
    return scenario.proposer(**json.loads(demande), sur=sur)


EN_CALCUL = [
    (Output("progression", "className"), "progression active", "progression"),
    (Output("actions", "className"), "actions masquees", "actions"),
]


@callback(
    Output("maisons", "data", allow_duplicate=True),
    Output("proposition", "data"),
    Output("erreur", "children"),
    Output("demande-sure", "data"),
    Input("btn-completer", "n_clicks"),
    Input("btn-remplacer", "n_clicks"),
    Input("lancer", "data"),
    State("nombre", "value"),
    State("perimetre", "data"),
    State("maisons", "data"),
    State("regles", "data"),
    State("seuil", "value"),
    State("pop-min", "value"),
    State("exclure-equipees", "value"),
    State("espacement", "value"),
    State("interdites", "value"),
    State("priorite", "value"),
    State("mode", "value"),
    running=EN_CALCUL,
    prevent_initial_call=True,
)
def proposer(
    _c,
    _r,
    lancer,
    nombre,
    perimetre,
    maisons,
    regles,
    seuil,
    pop_min,
    exclure,
    espacement,
    interdites,
    priorite,
    mode,
):
    """Complète ou remplace la liste. Sur un périmètre vaste en mode Sûr, passe la main au calcul long.

    Déclenché par les deux boutons, ou par l'assistant une fois les critères remplis.
    """
    remplacer = ctx.triggered_id == "btn-remplacer" or (
        ctx.triggered_id == "lancer" and bool((lancer or {}).get("remplacer"))
    )
    demande = json.dumps(
        {
            "nombre": nombre,
            "territoires": perimetre or [],
            "codes_poses": [] if remplacer else (maisons or []),
            "regles": regles or [],
            "seuil": seuil,
            "pop_min": PALIERS_POPULATION[pop_min or 0],
            "exclure_equipees": bool(exclure),
            "espacement": espacement,
            "interdites": interdites or [],
            "priorite": priorite,
        },
        sort_keys=True,
    )
    vaste = scenario.vaste(perimetre)
    if vaste and mode == "sur":
        return no_update, no_update, "", {"demande": demande, "jeton": time.time()}
    try:
        proposition = proposition_en_cache(demande, not vaste)
    except ValueError as erreur:
        return no_update, no_update, str(erreur), no_update
    return proposition["codes"], {**proposition, "jeton": time.time()}, "", no_update


@callback(
    Output("maisons", "data", allow_duplicate=True),
    Output("proposition", "data", allow_duplicate=True),
    Output("erreur", "children", allow_duplicate=True),
    Input("demande-sure", "data"),
    background=True,
    manager=taches,
    interval=80,  # la progression est relue 12 fois par seconde
    running=EN_CALCUL,
    progress=[Output("progres", "value"), Output("progres-texte", "children")],
    progress_default=["0", "Calcul en cours…"],
    cancel=[Input("btn-annuler", "n_clicks")],
    prevent_initial_call=True,
)
def proposer_sur(set_progress, demande_sure):
    """Mode Sûr. La barre suit l'écart restant à l'optimum : elle est pleine quand il est prouvé."""
    if not demande_sure:
        raise PreventUpdate
    set_progress(("0", "Chargement des habitants…"))

    def suivre(etat):
        texte = f"Calcul n° {etat['tour']} · écart restant à l'optimum : {nombre_fr(etat['ecart'])} habitants"
        set_progress((str(round(100 * etat["part"])), texte))

    try:
        proposition = scenario.proposer(
            **json.loads(demande_sure["demande"]), sur=True, progression=suivre
        )
    except ValueError as erreur:
        return no_update, no_update, str(erreur)
    return proposition["codes"], {**proposition, "jeton": time.time()}, ""


# ---------------------------------------------------------------- assistant : une demande écrite


@callback(
    Output("nombre", "value"),
    Output("perimetre", "data", allow_duplicate=True),
    Output("seuil", "value", allow_duplicate=True),
    Output("regles", "data", allow_duplicate=True),
    Output("pop-min", "value"),
    Output("exclure-equipees", "value"),
    Output("espacement", "value"),
    Output("interdites", "options", allow_duplicate=True),
    Output("interdites", "value"),
    Output("priorite", "value"),
    Output("lancer", "data"),
    Output("chat", "data"),
    Input("chat-demande", "data"),
    State("chat", "data"),
    State("nombre", "value"),
    State("perimetre", "data"),
    State("seuil", "value"),
    State("regles", "data"),
    State("pop-min", "value"),
    State("exclure-equipees", "value"),
    State("espacement", "value"),
    State("interdites", "value"),
    State("priorite", "value"),
    running=[*OCCUPE, (Output("chat-envoyer", "disabled"), True, False)],
    prevent_initial_call=True,
)
def demander(
    demande,
    fil,
    nombre,
    perimetre,
    seuil,
    regles,
    pop_min,
    exclure,
    espacement,
    interdites,
    priorite,
):
    """L'assistant traduit la demande en critères, les pose dans le panneau, puis lance le calcul."""
    message = ((demande or {}).get("texte") or "").strip()
    if not message:
        raise PreventUpdate
    etat = {
        "nombre": nombre or 1,
        "perimetre": perimetre or [donnees.FRANCE],
        "seuil": seuil,
        "regles": regles or [],
        "pop_min": pop_min or 0,
        "exclure": exclure or [],
        "espacement": espacement or 0,
        "interdites": interdites or [],
        "priorite": priorite,
    }
    fil = [*(fil or []), {"qui": "vous", "texte": message}][-8:]
    try:
        reponse = assistant.interpreter(message, etat)
    except ValueError as erreur:
        return (*[no_update] * 11, [*fil, {"qui": "assistant", "erreur": str(erreur)}])
    r = reponse["reglages"]
    fil.append(
        {
            "qui": "assistant",
            "compris": reponse["compris"],
            "ecartes": reponse["ecartes"],
            "modele": reponse["modele"],
        }
    )
    return (
        r["nombre"],
        r["perimetre"],
        r["seuil"],
        r["regles"],
        r["pop_min"],
        r["exclure"],
        r["espacement"],
        scenario.libelles_communes(r["interdites"]),
        r["interdites"],
        r["priorite"],
        {"remplacer": r["remplacer"], "jeton": time.time()},
        fil,
    )


@callback(Output("chat-fil", "children"), Input("chat", "data"))
def afficher_fil(fil):
    """Le fil montre ce que l'assistant a compris et ce qu'il n'a pas su traduire."""
    bulles = []
    for message in fil or []:
        if message["qui"] == "vous":
            bulles.append(html.P(message["texte"], className="chat-vous"))
        elif "erreur" in message:
            bulles.append(html.P(message["erreur"], className="chat-assistant erreur"))
        else:
            contenu = [html.B("Compris"), html.Ul([html.Li(ligne) for ligne in message["compris"]])]
            if message["ecartes"]:
                contenu += [
                    html.B("Non pris en compte"),
                    html.Ul([html.Li(x) for x in message["ecartes"]], className="chat-ecartes"),
                ]
            contenu.append(
                html.Small(
                    f"Interprétation générée par {message['modele']}. Seule votre phrase lui est envoyée."
                )
            )
            bulles.append(html.Div(contenu, className="chat-assistant"))
    return bulles


@callback(
    Output("maisons", "data", allow_duplicate=True),
    Output("perimetre", "data", allow_duplicate=True),
    Output("seuil", "value", allow_duplicate=True),
    Input("adresse", "search"),
    prevent_initial_call="initial_duplicate",
)
def ouvrir_lien(recherche):
    """Un lien de scénario (donné par le serveur MCP, par exemple) ouvre la carte sur ses maisons."""
    lu = outils.lire_lien(recherche)
    if not lu:
        raise PreventUpdate
    return lu.get("maisons", no_update), lu.get("perimetre", no_update), lu.get("seuil", no_update)


# ---------------------------------------------------------------- carte et panneau (côté navigateur)

clientside_callback(
    ClientsideFunction("phares", "contexte"),
    Input("perimetre", "data"),
    Input("seuil", "drag_value"),
    Input("seuil", "value"),
)
clientside_callback(ClientsideFunction("phares", "bilan"), Input("bilan", "data"))
clientside_callback(
    ClientsideFunction("phares", "vue"),
    Output("appli", "className"),
    Input("commune-active", "data"),
    Input("onglet-maisons", "n_clicks"),
    Input("onglet-proposer", "n_clicks"),
    Input("btn-retour", "n_clicks"),
    Input("proposition", "data"),
    State("appli", "className"),
    prevent_initial_call=True,
)
clientside_callback(
    ClientsideFunction("phares", "ouvrir"),
    Output("commune-active", "data"),
    Input({"type": "ouvrir", "code": ALL}, "n_clicks"),
    Input("recherche", "value"),
    prevent_initial_call=True,
)
clientside_callback(ClientsideFunction("phares", "calques"), Input("calques", "value"))
clientside_callback(
    ClientsideFunction("phares", "replier"),
    Input("btn-replier", "n_clicks"),
    Input("btn-deplier", "n_clicks"),
    prevent_initial_call=True,
)
clientside_callback(
    ClientsideFunction("phares", "chat"),
    Input("btn-chat", "n_clicks"),
    Input("chat-fermer", "n_clicks"),
    Input("chat-aide-btn", "n_clicks"),
    prevent_initial_call=True,
)
clientside_callback(
    ClientsideFunction("phares", "chatEnvoyer"),
    Output("chat-texte", "value"),
    Output("chat-demande", "data"),
    Input("chat-envoyer", "n_clicks"),
    Input("chat-texte", "n_submit"),
    State("chat-texte", "value"),
    prevent_initial_call=True,
)
clientside_callback(
    ClientsideFunction("phares", "chatExemple"),
    Output("chat-texte", "value", allow_duplicate=True),
    Input({"type": "exemple", "i": ALL}, "n_clicks"),
    State({"type": "exemple", "i": ALL}, "children"),
    prevent_initial_call=True,
)
clientside_callback(
    ClientsideFunction("phares", "chatDefiler"),
    Input("chat-fil", "children"),
    prevent_initial_call=True,
)
clientside_callback(
    ClientsideFunction("phares", "tiroir"),
    Output("methode", "className"),
    Input("btn-methode", "n_clicks"),
    Input("btn-methode-fermer", "n_clicks"),
    State("methode", "className"),
    prevent_initial_call=True,
)
clientside_callback(
    ClientsideFunction("phares", "compterCriteres"),
    Output("avances-compte", "children"),
    Input("regles", "data"),
    Input("pop-min", "value"),
    Input("exclure-equipees", "value"),
    Input("espacement", "value"),
    Input("interdites", "value"),
    Input("priorite", "value"),
)


if __name__ == "__main__":
    app.run(debug=bool(os.environ.get("DEBUG")), port=int(os.environ.get("PORT", "8060")))
