"""Couleurs et libellés partagés entre l'interface et la carte (exposés au JS par /donnees/theme)."""

NUIT = "#05080f"
ENCRE = "#eef3fb"
BRAISE = "#ff9a3d"  # habitants encore loin d'une maison de santé
PHARE = "#4be3c1"  # habitants rapprochés par le programme
COUVERT = "#6f93e8"  # habitants déjà à portée d'une maison existante
EXISTANT = "#8fb4ff"  # maisons de santé déjà ouvertes
DESSERTE = "#a67cff"  # communes à portée d'une maison existante, quand on l'ouvre

CARTE = {
    "nuit": NUIT,
    "braise": BRAISE,
    "phare": PHARE,
    "couvert": COUVERT,
    "existant": EXISTANT,
    "desserte": DESSERTE,
}
