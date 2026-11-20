"""Serveur MCP de Phares : un assistant extérieur interroge le moteur, sans passer par la carte.

Brancher dans Claude Code, depuis la racine du dépôt :
    claude mcp add --transport stdio phares -- uv run --directory <chemin du dépôt> python -m projet.mcp_server
Tester à la main :
    npx @modelcontextprotocol/inspector uv run python -m projet.mcp_server
Lancé seul, il n'affiche rien et attend un client : c'est normal.

Choix de sécurité
- Lecture seule : les outils calculent, ils n'écrivent ni fichier ni état, et ne modifient pas
  la session d'un utilisateur de l'appli. Chaque réponse donne un lien qui ouvre la carte sur
  le scénario ; c'est l'utilisateur qui choisit de l'ouvrir.
- Entrées bornées et vérifiées : 60 maisons au plus, distance de 5 à 20 km, territoires et
  communes reconnus par leur nom ou leur code, sinon l'outil répond par un message d'erreur clair.
- Calcul borné : la preuve d'optimalité s'arrête d'elle-même (400 tours au plus).
- Données publiques seulement (Insee, FINESS, DREES) : aucun secret, aucune donnée personnelle.
- Par défaut en stdio : le serveur n'écoute pas le réseau, c'est le client qui le lance sur la
  même machine. MCP_TRANSPORT=http ouvre un port sur 127.0.0.1 seulement.
"""

import os
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import outils

HTTP = os.environ.get("MCP_TRANSPORT") == "http"
mcp = FastMCP(
    "phares",
    instructions=(
        "Outils pour décider où implanter des maisons de santé en France métropolitaine. "
        "Un habitant est « loin » s'il vit à plus de la distance choisie (10 km par défaut, à vol "
        "d'oiseau) d'une maison de santé existante. Les chiffres viennent d'un calcul exact sur "
        "les habitants par carreau de 200 m : cite-les tels quels et donne le lien de la carte."
    ),
    host="127.0.0.1",
    port=int(os.environ.get("MCP_PORT", "8062")),
)

Zone = Annotated[
    list[str] | None,
    Field(
        description='Régions ou départements par leur nom ou leur code, par exemple ["Loiret", "Île-de-France"]. Vide : France entière.'
    ),
]
Distance = Annotated[
    int,
    Field(
        ge=5,
        le=20,
        description="Distance en km au-delà de laquelle un habitant est loin d'une maison de santé.",
    ),
]
Communes = Annotated[
    list[str] | None,
    Field(
        description="Communes par leur nom ou leur code INSEE ; « Nom (45) » s'il existe des homonymes."
    ),
]
Priorite = Literal["tous", "priorite", "seuls", "aines"]


def _outil(action):
    """Les erreurs de saisie reviennent au client comme un message, pas comme une panne."""
    try:
        return action()
    except ValueError as erreur:
        return {"erreur": str(erreur)}


@mcp.tool()
def proposer_maisons(
    nombre: Annotated[
        int,
        Field(
            ge=1,
            le=60,
            description="Nombre total de maisons de santé, celles déjà choisies comprises.",
        ),
    ],
    zone: Zone = None,
    distance_km: Distance = 10,
    repartition: Annotated[
        list[dict] | None,
        Field(
            description='Règles {"territoire": nom, "regle": "exactement" | "au moins" | "au plus", "nombre": n}. Le territoire peut être "chaque département" ou "chaque région".'
        ),
    ] = None,
    population_minimale: Annotated[
        int, Field(ge=0, description="Population minimale de la commune d'accueil.")
    ] = 0,
    ecarter_communes_equipees: Annotated[
        bool, Field(description="Ne pas implanter là où une maison de santé existe déjà.")
    ] = False,
    espacement_km: Annotated[
        int, Field(ge=0, le=50, description="Distance minimale entre deux nouvelles maisons.")
    ] = 0,
    communes_interdites: Communes = None,
    communes_deja_choisies: Communes = None,
    priorite: Annotated[
        Priorite,
        Field(
            description="tous : tous les habitants comptent ; priorite : les zones sous-dotées en généralistes comptent double ; seuls : elles seules comptent ; aines : seuls les 65 ans et plus comptent."
        ),
    ] = "tous",
) -> dict:
    """Choisit les communes où implanter des maisons de santé pour rapprocher le plus d'habitants.

    Garde les communes déjà choisies et complète jusqu'au nombre demandé. Répond avec la liste des
    communes, les habitants rapprochés par chacune, le total, la garantie d'optimalité et un lien
    vers la carte.
    """
    return _outil(
        lambda: outils.proposer(
            nombre,
            zone,
            distance_km,
            repartition,
            population_minimale,
            ecarter_communes_equipees,
            espacement_km,
            communes_interdites,
            communes_deja_choisies,
            priorite,
        )
    )


@mcp.tool()
def bilan_maisons(communes: Communes, zone: Zone = None, distance_km: Distance = 10) -> dict:
    """Mesure ce que rapportent des maisons de santé posées dans des communes données.

    Pour chaque maison : les habitants qu'elle rapprocherait seule, dont ceux qu'elle est la seule
    à couvrir. Pour l'ensemble : habitants rapprochés (comptés une fois) et habitants encore loin.
    """
    return _outil(lambda: outils.bilan(communes, zone, distance_km))


@mcp.tool()
def fiche_commune(
    commune: Annotated[str, Field(description="Nom ou code INSEE de la commune.")],
    zone: Zone = None,
    distance_km: Distance = 10,
    communes_deja_choisies: Communes = None,
) -> dict:
    """Portrait d'une commune : population, habitants loin des soins, maisons de santé les plus
    proches, et ce qu'une nouvelle maison y rapporterait compte tenu de celles déjà choisies."""
    return _outil(lambda: outils.fiche(commune, zone, distance_km, communes_deja_choisies))


@mcp.tool()
def points_chauds(
    zone: Zone = None,
    distance_km: Distance = 10,
    communes_deja_choisies: Communes = None,
    combien: Annotated[int, Field(ge=1, le=50, description="Nombre de communes à renvoyer.")] = 10,
) -> list[dict] | dict:
    """Les communes où une maison de santé de plus rapprocherait le plus d'habitants. Deux points
    chauds ne sont jamais dans le rayon l'un de l'autre."""
    return _outil(lambda: outils.points_chauds(zone, distance_km, communes_deja_choisies, combien))


@mcp.tool()
def chercher_commune(
    texte: Annotated[str, Field(min_length=2, description="Début ou partie du nom.")],
    combien: Annotated[int, Field(ge=1, le=25)] = 10,
) -> list[dict]:
    """Retrouve une commune et son code INSEE à partir d'un bout de son nom."""
    return outils.chercher(texte, combien)


if __name__ == "__main__":
    mcp.run(transport="streamable-http" if HTTP else "stdio")
