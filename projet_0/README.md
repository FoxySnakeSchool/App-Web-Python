# Atlas des soins — V0

Version de l’Atlas validée avant l’ajout de la maquette d’architecture. Cette copie embarque ses propres données et utilise par défaut le port 8065.

Où ouvrir plusieurs maisons de santé avec une enveloppe limitée, et que change une obligation de répartition entre territoires ? L’application s’adresse à un élu ou à un chargé de planification qui prépare une présélection de communes.

## Essayer le scénario de démonstration

1. Ouvrir le programme proposé pour le Loiret et l’Eure-et-Loir. Le budget autorise quatre maisons, dont exactement deux dans le Loiret. La distance cible est de 10 km à vol d’oiseau.
2. Lire le gain total et repérer les communes nouvellement couvertes sur la carte. Les habitants couverts par plusieurs maisons sont comptés une fois.
3. Cliquer sur un site de la liste ou son numéro sur la carte pour cadrer sa zone, ouvrir sa fiche et consulter les communes nouvellement desservies. Le bouton « Vue d’ensemble » retrouve le programme complet.
4. Ouvrir les critères, modifier le budget, la distance ou un quota et recalculer. Une case de quota vide laisse le choix au solveur. Zéro interdit toute implantation dans ce territoire. Le périmètre détaillé accepte un à quatre départements parmi les 101 disponibles.
5. Si un quota est fixé, lire dans le panneau de répartition le gain calculé avec une répartition libre au même budget.
6. Pour essayer un autre emplacement, sélectionner un site, cliquer sur « Déplacer le site sélectionné », puis choisir une commune sur la carte. Le scénario conserve les quotas et indique l’écart avec la recommandation. « Rétablir la recommandation » revient au programme calculé.

Sur les données livrées, le scénario initial fait passer **76 625 habitants de 65 communes** dans le rayon de 10 km. Sur les 186 191 habitants initialement hors seuil, 109 566 y restent. Le programme comporte Cepoy, Oison, Frazé et Ermenonville-la-Grande. Le scénario sans quota atteint 78 432 habitants supplémentaires. Dans ce cas précis, les deux résultats sont certifiés optimaux et l’écart vaut 1 807 habitants.

## Ce que le calcul décide

Chaque centre communal du périmètre est un site candidat. L’objectif maximise la somme des populations des communes dont le centre est initialement au-delà du seuil et que couvrirait au moins une des nouvelles implantations. Une maison peut bénéficier à des communes de plusieurs départements sélectionnés. Les effets hors du périmètre d’étude ne sont pas comptabilisés. La distance initiale du mode national tient compte de toutes les maisons FINESS géocodées, même en dehors du périmètre.

Le moteur construit un index spatial, obtient une première combinaison réalisable en choisissant les gains marginaux les plus élevés et calcule une borne supérieure. Si nécessaire, il résout ensuite un problème de programmation linéaire mixte en nombres entiers. Si le temps alloué au solveur expire, il garde la meilleure combinaison réalisable trouvée et indique la marge d’amélioration encore possible. Un optimum peut admettre plusieurs combinaisons équivalentes. Les sites sont présentés par contribution marginale au sein du programme, ce qui fournit un ordre de lecture reproductible pour une combinaison donnée.

Le ML fournit une référence indépendante. Un k-means pondéré regroupe géographiquement la population éloignée, puis associe les groupes à des centres communaux distincts sous les mêmes quotas. Les deux stratégies sont évaluées en habitants nouvellement couverts. Le résultat reste calculable et explicable sans clé API. Dans le scénario de démonstration, cette référence couvre 53 797 habitants supplémentaires.

## Données et portée

| Données | Périmètre et usage |
|---|---|
| Communes, centres et population | 34 875 communes, 101 départements. Population de référence 2023, sauf les 17 communes de Mayotte pour lesquelles la source livre 2017. La fiche indique le millésime de la commune. [API Découpage administratif](https://geo.api.gouv.fr/decoupage-administratif/communes) |
| Maisons de santé | Extraction historique FINESS arrêtée au 4 mai 2026, catégorie 603, 3 112 enregistrements dont 3 107 géocodés. Les points représentent des positions de précision variable, de l’adresse au chef-lieu. [FINESS](https://www.data.gouv.fr/datasets/finess-extraction-du-fichier-des-etablissements) |
| APL des généralistes | Indicateur DREES 2024 disponible sur 34 845 communes après jointure. La fiche l’affiche pour contextualiser la recommandation, en consultations accessibles par habitant standardisé et par an. Une donnée absente reste absente. [DREES](https://data.drees.solidarites-sante.gouv.fr/explore/dataset/530_l-accessibilite-potentielle-localisee-apl/information/) |
| Cartographie | Contours communaux Etalab 2026 généralisés à 100 m. Les géométries sont stockées localement, regroupées par département. Le fond de rues optionnel utilise le Plan IGN diffusé par la Géoplateforme. [Contours administratifs](https://www.data.gouv.fr/datasets/contours-administratifs) · [IGN](https://cartes.gouv.fr/) |
| Mode Loiret du cours | Les 325 communes du cas 8 et les 45 structures du fichier départemental, octobre 2025. Cette définition inclut plusieurs types de structures. [Fiche pédagogique](../projet/cas/08-territoire-loiret.md) |

Ces sources publiques françaises sont réutilisées sous Licence Ouverte. L’attribution des tuiles externes apparaît sur la carte lorsqu’elles sont activées. Les scripts de préparation documentent les sources et la transformation. Les fichiers de travail livrés restent sous 50 Mo.

Le gain décrit une **couverture géographique théorique**. Toute la population municipale est représentée à son centre. Une grande commune peut donc faire varier fortement le classement. L’étude d’une implantation réelle exigerait les trajets routiers, les effectifs médicaux disponibles, le foncier et les coûts. Le nombre de maisons est un budget en unités, avec le même coût supposé pour chaque ouverture.

## Lancer et vérifier

Depuis la racine du dépôt :

~~~powershell
uv sync
uv run python -m projet_0.app
uv run pytest -q projet_0/tests
uv run ruff check projet_0
~~~

Le terminal donne l’URL locale. Les données préparées sont incluses et la carte de base fonctionne hors ligne. Les scripts dans `sources/` à la racine reconstruisent les jeux de données de référence du dépôt. La V0 utilise les fichiers figés dans son dossier `data/`.

Dash relie le calcul Python au formulaire et au parcours cartographique. Leaflet assure le zoom, le déplacement et la sélection des communes dans un cadre rectangulaire. Le cache serveur conserve les résultats par jeu de données et paramètres.

## Depuis la démo intermédiaire

Le retour sur la comparaison manuelle a conduit au choix automatique de plusieurs sites sous contraintes. La rédaction générique par IA a été remplacée par une référence quantitative vérifiable. Le parcours passe d’une recommandation d’ensemble au détail d’un site, avec lecture progressive des ouvertures et vérification de la répartition. Codex a assisté la recherche documentaire, la préparation des données, la conception, les calculs et les essais.
