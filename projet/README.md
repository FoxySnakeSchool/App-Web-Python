# Phares

Où implanter les prochaines maisons de santé ? Une carte de nuit de la France métropolitaine : les
points orange sont des habitants qui vivent loin d'une maison de santé. On explore, on pose soi-même
de nouvelles maisons pour voir ce que chacune rapporte, seule et avec les autres, et on peut demander
à l'outil de proposer les meilleures communes d'accueil.

- **Pour** : un élu ou un chargé de planification, qui doit décider dans quelles communes soutenir
  l'ouverture de maisons de santé.
- **URL** : https://phares.plotly.app (Plotly Cloud). La première
  version du projet est gardée dans `projet_0/`.

## Mettre en ligne (Plotly Cloud)

```powershell
uv run python -m projet.preparation.publication
```

Le script fabrique un dossier réduit (Phares, ses données, `requirements.txt` aux versions
installées, un `app.py` d'entrée, `gunicorn.conf.py`) et affiche la commande `plotly app publish` à
lancer ensuite. Le `.env` n'y est jamais copié : la clé `CLAUDE_API_KEY` se saisit dans les variables
d'environnement de l'appli, sur cloud.plotly.com, avec `PHARES_URL` (l'adresse publique).
L'identifiant de l'appli est gardé dans `plotly-cloud.toml`. Offre gratuite : 1 Go de mémoire, veille
après 15 minutes sans visite.

## Lancer

Depuis la racine du dépôt :

```powershell
uv run python -m projet.app
```

```powershell
uv run pytest projet/tests -q
```

```powershell
uv run ruff check projet
```

L'appli écoute sur http://localhost:8060 (variable `PORT` pour changer). Au démarrage, le serveur
charge 2,3 millions de carreaux en tâche de fond : la toute première visite peut attendre quelques
secondes, les suivantes non. Le fond de carte (OpenFreeMap) et MapLibre viennent d'internet ; sans
réseau, la carte s'affiche sur fond uni.

## Le parcours

1. **Lire la carte.** Choisir un périmètre dans l'arborescence (France, régions, départements, cases à
   trois états) et la distance au-delà de laquelle on est « loin des soins » (5 à 20 km). Le constat
   se met à jour : combien d'habitants sont loin, quelle part du périmètre. Les habitants s'affichent
   par cases de 5 km en vue France, puis par carreaux de 1 km et de 200 m en zoomant : orange s'ils
   sont loin d'une maison de santé, bleu s'ils sont déjà couverts.
2. **Ouvrir une fiche.** Assez zoomé, le contour de la commune survolée s'allume ; un clic ouvre sa
   fiche (ou la recherche par nom). Elle donne ce qu'une maison y rapporterait, les cinq communes les
   plus rapprochées et les maisons de santé les plus proches. Un clic sur l'icône d'une maison
   existante ouvre la sienne. Un clic dans le vide, ou Échap, referme la fiche.
3. **Poser, retirer, déplacer.** Le bouton « Poser une maison ici » de la fiche ajoute une maison ;
   une croix la retire ; on la déplace en glissant sa balise, elle se recale sur la commune la plus
   proche. Les maisons existantes ne sont pas modifiables.
4. **Lire le bilan.** Pour chaque maison : les habitants qu'elle rapprocherait seule, dont ceux
   qu'elle est la seule à couvrir (perdus si on la retire). Pour l'ensemble : habitants rapprochés,
   comptés une fois, et habitants encore loin.
5. **Proposer automatiquement.** « Compléter mes maisons » garde celles déjà posées et ajoute les
   meilleures pour atteindre le nombre demandé ; « Remplacer tout » repart de zéro. Critères avancés :
   répartition par région ou département (exactement, au moins, au plus), population minimale de la
   commune d'accueil, communes déjà équipées écartées, espacement, communes interdites, et qui compte
   (tous, zones sous-dotées en généralistes, 65 ans et plus).
6. **Regarder une maison.** Un clic sur une maison ouvre une vue inclinée : les communes concernées
   montent en volumes, d'autant plus hauts qu'elles comptent d'habitants. En vert, celles qu'une
   nouvelle maison rapproche ; en violet, celles qu'une maison existante dessert déjà. Les noms du
   fond de carte s'effacent, seuls restent ceux de ces communes.

Drill-down : France, région, département, commune, carreau. La carte se déplace à la souris ; le clic
molette maintenu la fait pivoter et l'incline. Le panneau se replie, la légende est en bas.

## Rapide ou Sûr

Sur un périmètre ordinaire (un département, une région moyenne), le calcul dit toujours s'il a prouvé
l'optimum ou quel écart maximal il garantit. Sur un périmètre vaste (plus de 4 000 communes), un choix
apparaît :

- **Rapide** : réponse immédiate, optimum non prouvé ;
- **Sûr** : quelques secondes de plus, dans un processus à part. La progression prend la place des
  boutons : elle donne le numéro du calcul et l'écart restant à l'optimum, et la barre est pleine
  quand il est prouvé. Le nombre de calculs n'est pas connu d'avance : le mode Sûr s'arrête dès que
  la preuve est faite, au plus tard au 400e.

Mesures sur la France entière, mode Sûr : 6 maisons à 10 km, optimum prouvé en 8 s ; 20 maisons, 20 s ;
6 maisons à 5 km, 14 s ; « 4 dans le Loiret et 2 en Île-de-France », écart garanti de 3 habitants en
22 s. « Prouvé » s'entend à un habitant près.

## Les données

L'appli ne lit que des fichiers préparés. Les sources ne sont jamais modifiées.

| Donnée | Source | Date |
|---|---|---|
| Position des habitants | Insee, Filosofi, carreaux de 200 m | 2021 (paru le 12/02/2026) |
| Population des communes | API Découpage administratif | 2023 |
| Maisons de santé existantes | FINESS, catégorie 603 | 4 mai 2026 |
| Accès aux généralistes (APL) | DREES | 2024 |
| Contours des communes | Etalab | 2026 |

`preparation/carreaux.py` fabrique `data/carreaux.parquet` (2 298 591 carreaux, 44,5 Mo) :

```powershell
uv run --with pyproj python -I projet/preparation/carreaux.py <Filosofi2021_carreaux_200m_csv.zip>
```

Il garde la métropole (Corse comprise), rattache chaque carreau à une commune d'aujourd'hui, puis
**recale** : dans chaque commune, les carreaux sont multipliés pour que leur somme égale la population
2023. La répartition à l'intérieur de la commune reste celle de 2021. Les dix communes sans carreau
reçoivent un point à leur centre.

## Comment c'est fait

| Fichier | Rôle |
|---|---|
| `moteur.py` | Le calcul, sans dépendance à l'interface ni aux fichiers : bilan d'une liste de maisons, potentiel de chaque commune, choix automatique (glouton, échanges, preuve par îlots, encadrement lagrangien). |
| `scenario.py` | Relie la demande aux données et au moteur ; rend des résultats prêts à afficher. |
| `donnees.py` | Lecture des Parquet et des contours, régions et départements. |
| `app.py` | Dash : mise en page, callbacks, cache serveur, tâche de fond du mode Sûr, routes de données de la carte. |
| `assets/carte.js` | MapLibre GL : calques, animations, caméra, balises déplaçables. Les chiffres viennent de Python ; la carte reprend seulement la règle « loin et dans le rayon » pour colorer les carreaux. |
| `assets/style.css`, `theme.py` | Styles et couleurs. |
| `tuiles.py` | Tuiles vectorielles des carreaux, fabriquées et mises en cache par le serveur. |
| `outils.py` | Le moteur en langage courant (noms de territoires et de communes, lien de scénario), partagé par le chat et le serveur MCP. |
| `assistant.py`, `evaluer_assistant.py` | Traduction d'une phrase en critères par Claude, et mesure de sa fiabilité. |
| `mcp_server.py` | Accès au moteur pour un agent extérieur. |

**Framework** : Dash. L'état (liste des maisons, bilan, commune ouverte) vit dans des `dcc.Store`, et
la carte s'anime par des callbacks côté navigateur.

**Une nouvelle maison est placée au centre de sa commune.** La décision porte sur une commune ; les
maisons existantes, elles, sont à leur adresse.

**Le moteur.** Glouton, puis recherche par échanges. Preuve d'optimalité de deux façons : sur un
petit périmètre, le territoire se découpe en îlots indépendants résolus par programme linéaire en
nombres entiers ; sinon, une relaxation lagrangienne donne un majorant de l'optimum, qui tient compte
de la répartition entre territoires disjoints mais ignore l'espacement (le majorant reste valable,
moins serré).

**Affichage des habitants.** En vue France, 155 Ko de cases de 5 km. En zoomant, des tuiles
vectorielles fabriquées par le serveur (`tuiles.py`, route `/tuiles/…`) : carreaux de 1 km, puis de
200 m. Le navigateur ne charge que ce qu'il regarde. Les maisons proposées tombent une à une sur la
vue du périmètre, sans promener la caméra.

## La couche intelligente : un LLM qui traduit, un moteur qui calcule

**Le chat de l'appli** (bulle en bas à droite ; le « ? » rappelle ce qu'on peut demander). On écrit
sa demande en français :
« 8 maisons en Centre-Val de Loire, au moins une par département, priorité aux 65 ans et plus ». Le
modèle (Claude, `claude-opus-5-5`) rend uniquement des critères ; `assistant.py` les valide, les
borne, résout les noms de territoires et de communes, remplit le panneau puis lance le calcul. Le
modèle ne choisit aucune commune et ne produit aucun chiffre : tout vient du moteur. Le fil affiche
ce qui a été compris et ce qui n'a pas pu être pris en compte (« près d'une gare », par exemple).

- Envoyé au modèle : la phrase tapée et les réglages en cours. Aucune donnée de l'appli.
- Clé : `CLAUDE_API_KEY` dans `projet/.env` (jamais commité), ou `ANTHROPIC_API_KEY` dans
  l'environnement. Sans clé, le chat dit qu'il est indisponible et le reste de l'appli fonctionne.
  `LLM_MODEL` change de modèle.
- Fiabilité mesurée : 16 demandes sur 16 traduites sans écart (`uv run python -m
  projet.evaluer_assistant`, 16 appels payants). Réponse en 1 à 2 secondes.

**Le serveur MCP** (`mcp_server.py`) ouvre le même moteur à un agent extérieur : `proposer_maisons`,
`bilan_maisons`, `fiche_commune`, `points_chauds`, `chercher_commune`. Outils en lecture seule,
entrées bornées (60 maisons au plus), transport stdio par défaut, donc rien n'écoute sur le réseau.
Chaque réponse contient un lien qui ouvre la carte sur le scénario
(`?perimetre=D:45&seuil=10&maisons=45252,…`).

```powershell
claude mcp add phares -- uv run python -m projet.mcp_server
```

La référence k-means a été retirée : elle n'apportait rien au choix des communes.

**Dépendances ajoutées au `pyproject.toml` du dépôt** : `diskcache`, `multiprocess`, `psutil` (tâche
de fond du mode Sûr) et `anthropic` (chat).

## Limites

- Distances à vol d'oiseau, pas des temps de trajet.
- La position des habitants date de 2021 ; 78,7 % des carreaux sont estimés par l'Insee (ils portent
  19,7 % des habitants). Le fichier Insee compte les ménages fiscaux (62,6 millions de personnes) :
  le recalage sur 66,2 millions suppose que les autres sont répartis de la même façon.
- Seuls les habitants du périmètre choisi sont comptés.
- « Maison de santé » = catégorie 603 du fichier FINESS, pas l'ensemble de l'offre de soins.
- Rien sur les médecins disponibles, le foncier ou les coûts : c'est une présélection.
- Le serveur garde beaucoup en mémoire (carreaux et matrices de couverture) ; l'occupation n'a pas été
  mesurée en vue d'un déploiement.

## Accessibilité

Les états ne reposent jamais sur la couleur seule : braises sans contour, carreaux rapprochés cerclés
de blanc, balises numérotées, maisons existantes en croix. Les animations
sont coupées si le système demande moins de mouvement. Sous 860 px, le panneau passe en bas.

Assistant de code : Claude Code (conception de l'interface, moteur, préparation des données, tests).
