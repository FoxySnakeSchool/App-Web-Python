"""Prépare le dossier envoyé à Plotly Cloud : Phares seul, ses données, ses dépendances figées.

Le dépôt contient d'autres applis, le cours et des fichiers de travail : on ne publie qu'une copie
réduite, refaite à chaque fois. Le fichier `.env` (clés) n'y est jamais copié.

Depuis la racine du dépôt :
    uv run python -m projet.preparation.publication
puis la commande affichée (`plotly app publish …`). L'identifiant de l'appli publiée est gardé dans
`projet/plotly-cloud.toml`, pour que les publications suivantes mettent à jour la même appli.
"""

import shutil
import sys
import tempfile
import tomllib
from importlib.metadata import version
from pathlib import Path

PAQUET = Path(__file__).resolve().parents[1]
DEPOT = PAQUET.parent
MODULES = (
    "__init__",
    "app",
    "assistant",
    "donnees",
    "moteur",
    "outils",
    "scenario",
    "theme",
    "tuiles",
)
DONNEES = ("atlas_france.parquet", "atlas_msp.parquet", "atlas_contours.zip")
DEPENDANCES = (
    "dash",
    "flask-caching",
    "diskcache",
    "multiprocess",
    "psutil",
    "pandas",
    "pyarrow",
    "numpy",
    "scipy",
    "anthropic",
)
CONFIG = PAQUET / "plotly-cloud.toml"


def preparer(sortie: Path) -> Path:
    """Reconstruit `sortie` avec le paquet, les données et requirements.txt. Rend le dossier."""
    if sortie.exists():
        shutil.rmtree(sortie)
    paquet = sortie / PAQUET.name
    paquet.mkdir(parents=True)
    for module in MODULES:
        shutil.copy2(PAQUET / f"{module}.py", paquet / f"{module}.py")
    shutil.copytree(PAQUET / "assets", paquet / "assets")
    (paquet / "data").mkdir()
    shutil.copy2(PAQUET / "data" / "carreaux.parquet", paquet / "data" / "carreaux.parquet")
    projets = sortie / "data" / "projets"
    projets.mkdir(parents=True)
    for fichier in DONNEES:
        shutil.copy2(DEPOT / "data" / "projets" / fichier, projets / fichier)
    # Plotly Cloud exige un fichier Python à la racine : il ne fait que désigner l'appli du paquet.
    (sortie / "app.py").write_text(
        '"""Point d\'entrée pour Plotly Cloud : l\'appli vit dans le paquet projet."""\n\n'
        f"from {PAQUET.name}.app import app, server  # noqa: F401\n",
        encoding="utf-8",
    )
    # Un seul processus (une seule copie des données en mémoire), mais plusieurs requêtes à la fois :
    # la carte demande ses tuiles en parallèle, et un calcul long ne doit pas bloquer les autres.
    (sortie / "gunicorn.conf.py").write_text(
        "workers = 1\nthreads = 8\ntimeout = 120\n", encoding="utf-8"
    )
    # Les versions installées ici, à l'identique en ligne.
    (sortie / "requirements.txt").write_text(
        "".join(f"{nom}=={version(nom)}\n" for nom in DEPENDANCES), encoding="utf-8"
    )
    return sortie


def poids(dossier: Path) -> float:
    return sum(f.stat().st_size for f in dossier.rglob("*") if f.is_file()) / 2**20


if __name__ == "__main__":
    defaut = Path(tempfile.gettempdir()) / "phares-publication"
    dossier = preparer(Path(sys.argv[1]) if len(sys.argv) > 1 else defaut)
    if CONFIG.exists():
        cible = f"--app-id {tomllib.loads(CONFIG.read_text(encoding='utf-8'))['app_id']}"
    else:
        cible = "--name phares"
    print(f"Dossier prêt : {dossier} ({poids(dossier):.0f} Mio)")
    print(
        f'uv run plotly app publish --project-path "{dossier}" --config "{CONFIG}" '
        f"{cible} --entrypoint-module app --poll-timeout 900"
    )
