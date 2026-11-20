"""Atlas des soins, un studio cartographique de planification des implantations."""

import json
import os
from collections import Counter
from pathlib import Path

import dash_leaflet as dl
import numpy as np
from dash import (
    ALL,
    Dash,
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
from flask import abort, jsonify, send_file
from flask_caching import Cache

from .moteur import charger, simuler
from .optimisation import optimiser
from .reference import reference_ml
from .territoires import (
    charger_national,
    contours_nationaux,
    departements,
    emprises_communales,
    msp_existantes,
)
from .theme import COUVERT, GAIN, HORS

DATA = Path(__file__).resolve().parent / "data"
ARR = {"451": "Montargis", "452": "Orléans", "453": "Pithiviers"}
MAX_DEPS = 4
app = Dash(
    __name__,
    title="Atlas des soins · Planification",
    update_title=None,
    suppress_callback_exceptions=True,
)
server = app.server
cache = Cache(server, config={"CACHE_TYPE": "SimpleCache", "CACHE_DEFAULT_TIMEOUT": 3600})


def fr(n):
    return f"{int(n):,}".replace(",", " ")


@cache.memoize()
def catalogue():
    return departements()


@cache.memoize()
def donnees(source, deps):
    if source == "national":
        return charger_national(deps)
    return charger().assign(population_reference=lambda d: d.population_2023, annee_population=2023)


def initial():
    dispos = {d["value"] for d in catalogue()}
    national = {"45", "28"}.issubset(dispos)
    return {
        "source": "national" if national else "cours",
        "deps": ["28", "45"] if national else [],
        "nombre": 4,
        "seuil": 10,
        "quotas": {"45": 2} if national else {},
    }


@server.get("/atlas/contours.geojson")
def contours_loiret():
    return send_file(
        DATA / "risques_communes.geojson", mimetype="application/geo+json", max_age=86400
    )


@server.get("/atlas/france/<codes>.geojson")
def contours_france(codes):
    selection = codes.split(",")
    dispos = {d["value"] for d in catalogue()}
    if not 0 < len(selection) <= MAX_DEPS or not set(selection) <= dispos:
        abort(404)
    communes = set(donnees("national", tuple(sorted(selection))).code_insee)
    features = [
        f
        for code in dict.fromkeys(selection)
        for f in contours_nationaux(code)["features"]
        if f["id"] in communes
    ]
    response = jsonify(type="FeatureCollection", features=features)
    response.cache_control.max_age = 86400
    return response


def parametres(source, deps, nombre, seuil, valeurs, identifiants):
    if (
        source not in {"cours", "national"}
        or nombre not in range(1, 11)
        or seuil not in (5, 10, 15, 20)
    ):
        raise ValueError("Choisissez de 1 à 10 ouvertures et une distance proposée.")
    deps = sorted(set(deps or [])) if source == "national" else []
    if source == "national" and (
        not 1 <= len(deps) <= MAX_DEPS or not set(deps) <= {d["value"] for d in catalogue()}
    ):
        raise ValueError(f"Sélectionnez entre 1 et {MAX_DEPS} départements.")
    territoires = options_quota(source, deps)
    quotas = {}
    for identifiant, valeur in zip(identifiants, valeurs, strict=True):
        code = identifiant["code"]
        if valeur is None or code not in territoires:
            continue
        if (
            not isinstance(valeur, (int, float))
            or int(valeur) != valeur
            or not 0 <= valeur <= nombre
        ):
            raise ValueError("Chaque quota doit être un entier compris entre zéro et le budget.")
        quotas[code] = int(valeur)
    if sum(quotas.values()) > nombre:
        raise ValueError("La somme des quotas dépasse le nombre d’ouvertures.")
    return {
        "source": source,
        "deps": deps,
        "nombre": int(nombre),
        "seuil": int(seuil),
        "quotas": quotas,
    }


def cle(config):
    return json.dumps(config, sort_keys=True)


@cache.memoize(timeout=3600)
def gain_sans_quota(source, deps, nombre, seuil):
    df = donnees(source, deps)
    return optimiser(
        df, nombre, seuil, colonne_territoire="dep" if source == "national" else "arr"
    )["gain_total"]


@cache.memoize(timeout=3600)
def programme(signature):
    p = json.loads(signature)
    df = donnees(p["source"], tuple(p["deps"]))
    colonne = "dep" if p["source"] == "national" else "arr"
    manuels = p.get("sites_manuels")
    if manuels:
        initiale = {nom: valeur for nom, valeur in p.items() if nom != "sites_manuels"}
        recommande, reference, _ = programme(cle(initiale))
        couverture = tableau_couverture(df, p["seuil"], manuels)
        return evaluer_deplacement(couverture, colonne, manuels, recommande), reference, couverture
    resultat = optimiser(df, p["nombre"], p["seuil"], p["quotas"], colonne)
    reference = reference_ml(df, p["nombre"], p["seuil"], p["quotas"], colonne)
    return (
        resultat,
        reference["gain"],
        tableau_couverture(df, p["seuil"], [site["code"] for site in resultat["sites"]]),
    )


def tableau_couverture(df, seuil, codes):
    # Une commune est attribuée au premier site qui la couvre.
    premier = np.full(len(df), -1)
    distances = df.distance_soins_km.to_numpy(copy=True)
    for rang, code in enumerate(codes):
        vue = simuler(df, code, seuil)
        premier[(premier == -1) & vue.nouveaux.to_numpy()] = rang
        distances = np.minimum(distances, vue.distance_apres_km.to_numpy())
    return df.assign(rang_site=premier, distance_apres=distances)


def evaluer_deplacement(couverture, colonne, codes, recommande):
    index = couverture.set_index("code_insee")
    sites = []
    for rang, code in enumerate(codes):
        commune = index.loc[code]
        nouvelles = couverture.loc[couverture.rang_site == rang]
        sites.append(
            {
                "code": code,
                "commune": commune.commune,
                "territoire": str(commune[colonne]),
                "gain_marginal": int(nouvelles.population_reference.sum()),
                "communes_marginales": len(nouvelles),
            }
        )
    return {
        "sites": sites,
        "gain_total": sum(site["gain_marginal"] for site in sites),
        "communes_gagnees": sum(site["communes_marginales"] for site in sites),
        "population_hors_seuil": recommande["population_hors_seuil"],
        "communes_hors_seuil": recommande["communes_hors_seuil"],
        "methode": "Simulation après déplacement manuel",
        "gain_recommande": recommande["gain_total"],
    }


def options_quota(source, deps):
    if source == "cours":
        return ARR
    selection = set(deps or [])
    return {o["value"]: o["label"] for o in catalogue() if o["value"] in selection}


def controles_quota(source, deps, quotas):
    visibles = options_quota(source, deps)
    territoires = {**ARR, **{option["value"]: option["label"] for option in catalogue()}}
    return [
        html.Label(
            [
                html.Span(nom),
                dcc.Input(
                    type="number",
                    min=0,
                    max=10,
                    step=1,
                    placeholder="Libre",
                    value=quotas.get(code),
                    id={"type": "atlas-quota", "code": code},
                    debounce=True,
                ),
            ],
            className="quota-row",
            id={
                "type": "atlas-quota-territoire",
                "code": code,
                "source": "cours" if code in ARR else "national",
            },
            hidden=code not in visibles,
        )
        for code, nom in territoires.items()
    ]


def champ(titre, composant):
    return html.Div([html.Div(titre, className="field-label"), composant], className="field")


def disposition():
    p = initial()
    options = catalogue()
    sources = [{"label": "Loiret · données du cours", "value": "cours"}]
    if options:
        sources.insert(0, {"label": "France · départements", "value": "national"})
    return html.Div(
        [
            html.Header(
                html.Div(
                    [
                        html.A(
                            [
                                html.Span("✚", className="brand-icon", **{"aria-hidden": "true"}),
                                "Atlas des soins",
                            ],
                            href="#planification",
                            className="brand",
                        ),
                        html.Nav(
                            [
                                html.A("Planification", href="#planification"),
                            ],
                            **{"aria-label": "Navigation principale"},
                        ),
                    ],
                    className="shell nav-inner",
                ),
                className="topbar",
            ),
            html.Main(
                [
                    html.Div(
                        [
                            html.H1("Où ouvrir les prochaines maisons de santé ?"),
                            html.P(
                                "Explorez les communes retenues et la population que chaque ouverture rapprocherait d’une maison de santé."
                            ),
                        ],
                        className="intro",
                        id="planification",
                    ),
                    html.Details(
                        [
                            html.Summary(
                                [
                                    html.Div(
                                        [
                                            html.Span(
                                                "Programme étudié", className="summary-label"
                                            ),
                                            html.Strong(id="atlas-scenario-titre"),
                                        ]
                                    ),
                                    html.Span("Modifier les critères", className="ouvrir-criteres"),
                                ],
                                className="scenario-summary",
                            ),
                            html.Div(
                                [
                                    champ(
                                        "Périmètre",
                                        dcc.Dropdown(
                                            id="atlas-source",
                                            options=sources,
                                            value=p["source"],
                                            clearable=False,
                                            searchable=False,
                                        ),
                                    ),
                                    champ(
                                        "Ouvertures",
                                        dcc.Input(
                                            id="atlas-budget",
                                            type="number",
                                            min=1,
                                            max=10,
                                            step=1,
                                            value=4,
                                        ),
                                    ),
                                    champ(
                                        "Distance cible",
                                        dcc.RadioItems(
                                            id="atlas-seuil",
                                            options=[
                                                {"label": f"{km} km", "value": km}
                                                for km in (5, 10, 15, 20)
                                            ],
                                            value=10,
                                            className="segments",
                                        ),
                                    ),
                                    html.Button(
                                        "Calculer le programme",
                                        id="atlas-calculer",
                                        className="primary",
                                        n_clicks=0,
                                    ),
                                ],
                                className="controls",
                            ),
                            html.Div(
                                [
                                    html.Div(
                                        [
                                            html.Label(
                                                f"Départements étudiés (jusqu’à {MAX_DEPS})",
                                                htmlFor="atlas-deps",
                                            ),
                                            dcc.Dropdown(
                                                id="atlas-deps",
                                                options=options,
                                                multi=True,
                                                value=p["deps"],
                                                placeholder="Sélectionner les départements",
                                            ),
                                        ],
                                        id="atlas-choix-deps",
                                        hidden=p["source"] != "national",
                                    ),
                                    html.Details(
                                        [
                                            html.Summary("Répartition territoriale"),
                                            html.P(
                                                "Fixez un nombre exact d’ouvertures par territoire. Une case vide laisse la répartition libre."
                                            ),
                                            html.Div(
                                                controles_quota(
                                                    p["source"], p["deps"], p["quotas"]
                                                ),
                                                id="atlas-quotas",
                                            ),
                                        ],
                                        className="quota",
                                    ),
                                ],
                                className="subcontrols",
                            ),
                            html.Div(id="atlas-etat-form", role="status"),
                            html.Div(id="atlas-erreur", role="alert"),
                        ],
                        className="settings",
                        **{"aria-label": "Paramètres du programme"},
                    ),
                    dcc.Store(id="atlas-programme", data=p),
                    dcc.Store(id="atlas-focus"),
                    dcc.Store(id="atlas-deplacement"),
                    dcc.Loading(
                        html.Section(
                            id="atlas-kpis",
                            className="kpis",
                            **{"aria-label": "Impact du programme", "aria-live": "polite"},
                        ),
                        delay_show=180,
                    ),
                    html.Div(
                        [
                            html.Section(
                                [
                                    html.Div(
                                        [
                                            html.Div(
                                                [
                                                    html.H2("Carte de couverture"),
                                                    html.Span(id="atlas-emprise"),
                                                ]
                                            ),
                                        ],
                                        className="map-head",
                                    ),
                                    html.Div(
                                        [
                                            dl.Map(
                                                [
                                                    dl.LayerGroup(id="atlas-fond"),
                                                    dl.GeoJSON(
                                                        id="atlas-polygones",
                                                        url="/atlas/contours.geojson",
                                                        style={"variable": "atlasCarte.style"},
                                                        onEachFeature={
                                                            "variable": "atlasCarte.infobulle"
                                                        },
                                                        hideout={"communes": {}},
                                                    ),
                                                    dl.LayerGroup(id="atlas-msp"),
                                                    dl.LayerGroup(id="atlas-villes"),
                                                    dl.LayerGroup(id="atlas-sites"),
                                                    dl.ScaleControl(
                                                        position="bottomleft",
                                                        imperial=False,
                                                        maxWidth=100,
                                                    ),
                                                ],
                                                id="atlas-carte",
                                                className="carte",
                                                center=[47.98, 2.34],
                                                zoom=8,
                                                zoomSnap=0.1,
                                                zoomDelta=0.5,
                                                scrollWheelZoom=False,
                                                trackViewport=False,
                                                zoomControl=True,
                                            ),
                                            html.Button(
                                                "Vue d’ensemble",
                                                id="atlas-retour",
                                                n_clicks=0,
                                                className="overview",
                                            ),
                                        ],
                                        className="map-stage",
                                    ),
                                    html.Div(
                                        [
                                            html.Span("Déjà couverte", className="key deja"),
                                            html.Span(
                                                "Nouvellement couverte", className="key nouvelle"
                                            ),
                                            html.Span("Hors seuil", className="key hors"),
                                            dcc.Checklist(
                                                id="atlas-rues",
                                                value=["rues"],
                                            ),
                                        ],
                                        className="legend",
                                    ),
                                ],
                                className="map-panel",
                            ),
                            html.Aside(
                                [
                                    html.Div(
                                        [
                                            html.H2("Implantations retenues"),
                                            html.P(
                                                "Habitants supplémentaires à chaque ouverture, dans l’ordre indiqué."
                                            ),
                                        ],
                                        className="result-head",
                                    ),
                                    html.Div(id="atlas-liste", className="site-list"),
                                    html.Div(
                                        [
                                            html.Button(
                                                "Déplacer le site sélectionné",
                                                id="atlas-deplacer",
                                                disabled=True,
                                            ),
                                            html.Button(
                                                "Annuler",
                                                id="atlas-annuler-deplacement",
                                                hidden=True,
                                            ),
                                            html.Button(
                                                "Rétablir la recommandation",
                                                id="atlas-restaurer",
                                                hidden=True,
                                            ),
                                            html.P(id="atlas-message-deplacement", role="status"),
                                        ],
                                        className="deplacement",
                                    ),
                                    html.Div(
                                        id="atlas-fiche",
                                        className="site-detail",
                                        **{"aria-live": "polite"},
                                    ),
                                    html.Section(id="atlas-repartition", className="repartition"),
                                ],
                                className="results",
                            ),
                        ],
                        className="workspace",
                    ),
                ],
                className="shell",
            ),
        ]
    )


app.layout = disposition


clientside_callback(
    """function(source, deps, territoires) {
        const national = source === "national";
        const codes = new Set(deps || []);
        return [!national, territoires.map(territoire =>
            territoire.source !== source || (national && !codes.has(territoire.code))
        )];
    }""",
    Output("atlas-choix-deps", "hidden"),
    Output({"type": "atlas-quota-territoire", "code": ALL, "source": ALL}, "hidden"),
    Input("atlas-source", "value"),
    Input("atlas-deps", "value"),
    State({"type": "atlas-quota-territoire", "code": ALL, "source": ALL}, "id"),
)


@callback(
    Output("atlas-programme", "data"),
    Output("atlas-erreur", "children"),
    Input("atlas-calculer", "n_clicks"),
    State("atlas-source", "value"),
    State("atlas-deps", "value"),
    State("atlas-budget", "value"),
    State("atlas-seuil", "value"),
    State({"type": "atlas-quota", "code": ALL}, "value"),
    State({"type": "atlas-quota", "code": ALL}, "id"),
    State("atlas-programme", "data"),
    prevent_initial_call=True,
)
def appliquer(clic, source, deps, nombre, seuil, valeurs, ids, actif):
    try:
        p = parametres(source, deps, nombre, seuil, valeurs, ids)
        if p == actif:
            return no_update, ""
        programme(cle(p))
        return p, ""
    except ValueError as erreur:
        return no_update, str(erreur)


@callback(
    Output("atlas-etat-form", "children"),
    Input("atlas-source", "value"),
    Input("atlas-deps", "value"),
    Input("atlas-budget", "value"),
    Input("atlas-seuil", "value"),
    Input({"type": "atlas-quota", "code": ALL}, "value"),
    State({"type": "atlas-quota", "code": ALL}, "id"),
    Input("atlas-programme", "data"),
)
def etat(source, deps, nombre, seuil, valeurs, ids, actif):
    try:
        if parametres(source, deps, nombre, seuil, valeurs, ids) == {
            nom: valeur for nom, valeur in actif.items() if nom != "sites_manuels"
        }:
            return ""
    except ValueError:
        pass
    return "Paramètres modifiés. Calculez le programme pour mettre la carte à jour."


@callback(Output("atlas-rues", "options"), Input("atlas-programme", "data"))
def couches_disponibles(p):
    options = [{"label": "Fond de rues", "value": "rues"}]
    if p["source"] == "national":
        options.append({"label": "MSP recensées (FINESS)", "value": "msp"})
    return options


@callback(Output("atlas-fond", "children"), Input("atlas-rues", "value"))
def changer_fond(valeurs):
    fond = []
    if (Path(__file__).parent / "assets/geo/departements.geojson").is_file():
        fond.append(
            dl.GeoJSON(
                url=app.get_asset_url("geo/departements.geojson"),
                interactive=False,
                style={
                    "interactive": False,
                    "weight": 1.1,
                    "color": "#9ba8ad",
                    "fillColor": "#e7eae5",
                    "fillOpacity": 0.10 if "rues" in valeurs else 0.4,
                    "dashArray": "4 3",
                },
            )
        )
    if "rues" in valeurs:
        fond.append(
            dl.TileLayer(
                url="https://data.geopf.fr/wmts?SERVICE=WMTS&VERSION=1.0.0&REQUEST=GetTile"
                "&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&FORMAT=image/png"
                "&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}",
                attribution='Fond de carte © <a href="https://cartes.gouv.fr/">IGN</a>',
                opacity=0.55,
                maxNativeZoom=18,
                zIndex=1,
            )
        )
    return fond


@callback(
    Output("atlas-msp", "children"), Input("atlas-rues", "value"), Input("atlas-programme", "data")
)
def afficher_msp(couches, p):
    if "msp" not in couches or p["source"] != "national":
        return []
    return [
        dl.CircleMarker(
            center=[site.lat, site.lon],
            radius=4,
            color="#0c536d",
            weight=1.5,
            fill=True,
            fillColor="white",
            fillOpacity=1,
            children=dl.Tooltip(site.nom or "Maison de santé recensée"),
        )
        for site in msp_existantes(p["deps"]).itertuples()
    ]


@callback(Output("atlas-scenario-titre", "children"), Input("atlas-programme", "data"))
def titre_programme(p):
    secteur = " et ".join(nom_territoire(p, d).split(" · ")[-1] for d in p["deps"]) or "Loiret"
    quotas = [
        f"{nom_territoire(p, d).split(' · ')[-1]} ({n} imposée{'s' if n > 1 else ''})"
        for d, n in p["quotas"].items()
    ]
    quota = f" · Quota {', '.join(quotas)}" if quotas else ""
    return f"{p['nombre']} ouverture{'s' if p['nombre'] > 1 else ''} · {secteur} · {p['seuil']} km{quota}"


def nom_territoire(p, code):
    if p["source"] == "cours":
        return f"Arr. de {ARR.get(code, code)}"
    return next((d["label"] for d in catalogue() if d["value"] == code), code)


def site_item(site, rang, p, gain_total):
    return html.Button(
        [
            html.Span(str(rang), className="rank"),
            html.Span(
                [
                    html.Strong(site["commune"]),
                    html.Small(nom_territoire(p, site["territoire"])),
                    html.Progress(
                        value=site["gain_marginal"],
                        max=max(gain_total, 1),
                        title="Part du gain total",
                        **{"aria-label": "Part du gain total"},
                    ),
                ],
                className="site-name",
            ),
            html.Span(
                [html.B(f"+{fr(site['gain_marginal'])}"), html.Small("habitants")],
                className="site-count",
            ),
        ],
        id={"type": "atlas-site", "code": site["code"]},
        n_clicks=0,
        className="site-row",
        title=f"Explorer {site['commune']}",
    )


@callback(
    Output("atlas-kpis", "children"),
    Output("atlas-liste", "children"),
    Output("atlas-repartition", "children"),
    Output("atlas-emprise", "children"),
    Output("atlas-polygones", "url"),
    Input("atlas-programme", "data"),
)
def decrire(p):
    resultat, _, df = programme(cle(p))
    gain = resultat["gain_total"]
    reste = resultat["population_hors_seuil"] - gain
    kpis = [
        ("Habitants nouvellement couverts", f"+{fr(gain)}"),
        ("Communes gagnées", fr(resultat["communes_gagnees"])),
        (f"Habitants au-delà de {p['seuil']} km après", fr(reste)),
    ]
    chiffres = [
        html.Div([html.Span(label), html.Strong(valeur)], className="kpi") for label, valeur in kpis
    ]
    sites = [site_item(s, i, p, gain) for i, s in enumerate(resultat["sites"], 1)]
    effectifs = Counter(site["territoire"] for site in resultat["sites"])
    lignes = []
    for code, territoire in options_quota(p["source"], p["deps"]).items():
        quota = p["quotas"].get(code)
        effectif = effectifs[code]
        lignes.append(
            html.Li(
                [
                    html.Span(territoire.split(" · ")[-1]),
                    html.Strong(f"{effectif} site{'s' if effectif > 1 else ''}"),
                    html.Small(f"Quota de {quota} respecté") if quota is not None else None,
                ]
            )
        )
    repartition = [html.H3("Répartition des ouvertures"), html.Ul(lignes)]
    source_nationale = p["source"] == "national"
    nom = " · ".join(nom_territoire(p, dep) for dep in p["deps"]) if source_nationale else "Loiret"
    if "gain_recommande" in resultat:
        variation = gain - resultat["gain_recommande"]
        signe = "+" if variation > 0 else "−" if variation < 0 else ""
        repartition.insert(
            0,
            html.P(
                [
                    "Écart avec la recommandation",
                    html.Strong(f"{signe}{fr(abs(variation))} habitants"),
                ]
            ),
        )
    if p["quotas"]:
        libre = gain_sans_quota(p["source"], tuple(p["deps"]), p["nombre"], p["seuil"])
        repartition.append(
            html.P(
                [
                    "Gain du programme calculé en répartition libre à budget égal",
                    html.Strong(f"+{fr(libre)} habitants"),
                ]
            )
        )
    url = (
        f"/atlas/france/{','.join(p['deps'])}.geojson"
        if source_nationale
        else "/atlas/contours.geojson"
    )
    return chiffres, sites, repartition, f"{nom} · {fr(len(df))} communes", url


@callback(
    Output("atlas-deplacer", "disabled"),
    Output("atlas-annuler-deplacement", "hidden"),
    Output("atlas-restaurer", "hidden"),
    Input("atlas-focus", "data"),
    Input("atlas-deplacement", "data"),
    Input("atlas-programme", "data"),
)
def commandes_deplacement(code, mouvement, p):
    resultat, _, _ = programme(cle(p))
    actif = mouvement and mouvement["signature"] == cle(p)
    est_site = any(site["code"] == code for site in resultat["sites"])
    return bool(actif or not est_site), not actif, "sites_manuels" not in p


@callback(
    Output("atlas-programme", "data", allow_duplicate=True),
    Output("atlas-deplacement", "data"),
    Output("atlas-message-deplacement", "children"),
    Input("atlas-deplacer", "n_clicks"),
    Input("atlas-annuler-deplacement", "n_clicks"),
    Input("atlas-restaurer", "n_clicks"),
    Input("atlas-polygones", "clickData"),
    Input("atlas-programme", "data"),
    State("atlas-deplacement", "data"),
    State("atlas-focus", "data"),
    prevent_initial_call=True,
)
def deplacer_site(debut, annuler, restaurer, clic, p, mouvement, focus):
    action = ctx.triggered_id
    if action in ("atlas-programme", "atlas-annuler-deplacement"):
        return no_update, None, ""
    if action == "atlas-restaurer" and "sites_manuels" in p:
        return {nom: valeur for nom, valeur in p.items() if nom != "sites_manuels"}, None, ""
    if action == "atlas-deplacer":
        resultat, _, _ = programme(cle(p))
        site = next((site for site in resultat["sites"] if site["code"] == focus), None)
        if site is not None:
            return (
                no_update,
                {"code": focus, "signature": cle(p)},
                f"Choisis sur la carte la nouvelle commune pour remplacer {site['commune']}.",
            )
    if action != "atlas-polygones" or not clic or not mouvement or mouvement["signature"] != cle(p):
        raise PreventUpdate

    resultat, _, df = programme(cle(p))
    code = clic.get("id") or clic.get("properties", {}).get("code_insee")
    index = df.set_index("code_insee")
    origine = mouvement["code"]
    codes = [site["code"] for site in resultat["sites"]]
    if code not in index.index or origine not in codes:
        raise PreventUpdate
    if code in codes:
        return no_update, no_update, "Choisis une commune sans implantation proposée."
    colonne = "dep" if p["source"] == "national" else "arr"
    depart, arrivee = str(index.at[origine, colonne]), str(index.at[code, colonne])
    if depart != arrivee and (depart in p["quotas"] or arrivee in p["quotas"]):
        return (
            no_update,
            no_update,
            "Ce déplacement modifierait un quota. Choisis une autre commune.",
        )
    codes[codes.index(origine)] = code
    return {**p, "sites_manuels": codes}, None, ""


@callback(
    Output("atlas-focus", "data"),
    Input({"type": "atlas-site", "code": ALL}, "n_clicks"),
    Input({"type": "atlas-pin", "code": ALL}, "n_clicks"),
    Input("atlas-polygones", "clickData"),
    Input("atlas-retour", "n_clicks"),
    Input("atlas-programme", "data"),
    State("atlas-focus", "data"),
    State("atlas-deplacement", "data"),
    prevent_initial_call=True,
)
def selectionner(boutons, marqueurs, clic, retour, p, actif, mouvement):
    trigger = ctx.triggered_id
    if trigger in ("atlas-retour", "atlas-programme"):
        return None
    _, _, df = programme(cle(p))
    if isinstance(trigger, dict):
        valeurs = boutons if trigger["type"] == "atlas-site" else marqueurs
        if not any(valeurs):
            raise PreventUpdate
        code = trigger["code"]
    elif trigger == "atlas-polygones" and clic:
        if mouvement and mouvement["signature"] == cle(p):
            raise PreventUpdate
        code = clic.get("id") or clic.get("properties", {}).get("code_insee")
    else:
        raise PreventUpdate
    if code not in set(df.code_insee) or code == actif:
        raise PreventUpdate
    return code


def bounds(df, p):
    codes = set(df.code_insee)
    boites = np.array(
        [
            boite
            for dep in (p["deps"] if p["source"] == "national" else ["cours"])
            for code, boite in emprises_communales(dep).items()
            if code in codes
        ]
    )
    return [boites[:, :2].min(axis=0).tolist(), boites[:, 2:].max(axis=0).tolist()]


@callback(
    Output("atlas-carte", "viewport"),
    Input("atlas-focus", "data"),
    Input("atlas-programme", "data"),
    Input("atlas-deplacement", "data"),
)
def cadrer(code, p, mouvement):
    resultat, _, df = programme(cle(p))
    choix_en_cours = mouvement and mouvement["signature"] == cle(p)
    cible = df.loc[df.code_insee == (None if choix_en_cours else code)]
    if not cible.empty:
        site = next((i for i, s in enumerate(resultat["sites"]) if s["code"] == code), None)
        if site is not None:
            cible = df.loc[(df.rang_site == site) | (df.code_insee == code)]
        return {
            "bounds": bounds(cible, p),
            "transition": "flyToBounds",
            "options": {"padding": [16, 16], "maxZoom": 11, "duration": 0.55},
        }
    return {
        "bounds": bounds(df, p),
        "transition": "fitBounds",
        "options": {"padding": [12, 12], "animate": False},
    }


@callback(
    Output("atlas-polygones", "hideout"),
    Output("atlas-sites", "children"),
    Output("atlas-villes", "children"),
    Output("atlas-fiche", "children"),
    Input("atlas-programme", "data"),
    Input("atlas-focus", "data"),
)
def explorer(p, code):
    resultat, _, df = programme(cle(p))
    signees = {s["code"]: i for i, s in enumerate(resultat["sites"])}
    rang_focus = signees.get(code)
    styles = {}
    for r in df.itertuples():
        distance = r.distance_apres
        gain = r.rang_site >= 0
        statut = (
            "Nouvellement couverte"
            if gain
            else "Hors seuil"
            if distance > p["seuil"]
            else "Déjà couverte"
        )
        styles[r.code_insee] = {
            "c": GAIN if gain else HORS if distance > p["seuil"] else COUVERT,
            "n": r.commune,
            "d": round(distance, 1),
            "s": statut,
            "focus": r.code_insee == code,
            "g": gain and r.rang_site == rang_focus,
        }
    pins = []
    villes = []
    index = df.set_index("code_insee")
    if rang_focus is not None:
        cible = index.loc[code]
        pins.append(
            dl.Circle(
                center=[cible.lat, cible.lon],
                radius=1000 * p["seuil"],
                color="#9a4b16",
                weight=2,
                dashArray="6 5",
                fill=True,
                fillColor=GAIN,
                fillOpacity=0.08,
                interactive=False,
            )
        )
    for i, site in enumerate(resultat["sites"], 1):
        r = index.loc[site["code"]]
        pins.append(
            dl.DivMarker(
                position=[r.lat, r.lon],
                id={"type": "atlas-pin", "code": site["code"]},
                n_clicks=0,
                title=f"{i} · {site['commune']}",
                alt=site["commune"],
                zIndexOffset=400 if site["code"] == code else 200,
                iconOptions={
                    "html": f"<span>{i}</span>",
                    "className": "site-pin active" if site["code"] == code else "site-pin",
                    "iconSize": [32, 32],
                    "iconAnchor": [16, 16],
                },
                children=dl.Tooltip(site["commune"], direction="top", offset=[0, -14]),
            )
        )
    etiquettes = []
    ecart_lon = max(float(df.lon.max() - df.lon.min()) * 0.12, 0.02)
    ecart_lat = max(float(df.lat.max() - df.lat.min()) * 0.065, 0.01)
    for r in df.nlargest(30, "population_reference").itertuples():
        if r.code_insee in signees:
            continue
        if any(
            abs(r.lat - lat) < ecart_lat and abs(r.lon - lon) < ecart_lon for lat, lon in etiquettes
        ):
            continue
        etiquettes.append((r.lat, r.lon))
        villes.append(
            dl.CircleMarker(
                center=[r.lat, r.lon],
                radius=2,
                color="#263b49",
                interactive=False,
                children=dl.Tooltip(
                    r.commune, permanent=True, direction="top", className="nom-ville"
                ),
            )
        )
        if len(etiquettes) == 5:
            break
    fiche = []
    if code in index.index:
        r = index.loc[code]
        fiche = [
            html.Div([html.Span("Fiche communale"), html.H3(r.commune)], className="detail-title"),
            html.P(
                f"{fr(r.population_reference)} habitants en {int(r.annee_population)} · {r.distance_soins_km:.1f} km de la structure recensée la plus proche avant implantation.".replace(
                    ".", ",", 1
                )
            ),
        ]
        apl = r.get("apl_generalistes_2024")
        if apl is not None and np.isfinite(apl):
            fiche.append(
                html.P(
                    f"Accessibilité aux généralistes en 2024 (APL) : {apl:.2f} consultations par habitant standardisé et par an.".replace(
                        ".", ",", 1
                    )
                )
            )
        if rang_focus is not None:
            site = resultat["sites"][rang_focus]
            fiche.append(
                html.P(
                    f"À l’ouverture n°{rang_focus + 1}, {fr(site['communes_marginales'])} communes supplémentaires "
                    f"passent à {p['seuil']} km ou moins d’une maison, soit {fr(site['gain_marginal'])} habitants."
                )
            )
            noms = sorted(df.loc[df.rang_site == rang_focus, "commune"])
            if noms:
                fiche.append(
                    html.Details(
                        [
                            html.Summary("Voir les communes gagnées"),
                            html.Ul([html.Li(n) for n in noms]),
                        ]
                    )
                )
    return {"communes": styles}, pins, villes, fiche


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", "8065")))
