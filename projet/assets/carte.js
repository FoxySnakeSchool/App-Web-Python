/* Phares : carte MapLibre, animations et fonctions appelées par les callbacks Dash.
   Tous les chiffres viennent de Python (store « bilan »). Ce fichier affiche et anime : il
   reprend seulement la règle géométrique « loin et dans le rayon » pour colorer les carreaux. */
(function () {
  "use strict";

  const FRANCE = [[-5.3, 41.2], [9.7, 51.2]];
  const VERSION = 4; // à augmenter quand le format des données servies change
  const ZOOM_COMMUNE = 8; // à partir d'ici, on survole et on clique des communes
  const nf = new Intl.NumberFormat("fr-FR");
  const calme = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const $ = (id) => document.getElementById(id);
  const borner = (x, a, b) => Math.max(a, Math.min(b, x));
  const adoucir = (t) => 1 - Math.pow(1 - t, 3);
  const collection = (formes) => ({ type: "FeatureCollection", features: formes || [] });
  const AUCUN_DEP = ["==", ["get", "code"], "aucun"];
  const JAMAIS = ["<", ["zoom"], 0];
  const dc = () => window.dash_clientside;
  const racine = document.documentElement; // Dash réécrit les classes de ses éléments : nos états vivent ici

  const E = {
    carte: null,
    pret: false,
    attente: [],
    theme: {},
    C: null, // communes en colonnes
    M: null, // maisons de santé existantes
    G: null, // cases de 5 km pour la distance en cours
    gros: new Map(), // distance -> cases de 5 km
    grilleGros: null,
    grilleCommunes: null,
    position: null, // code INSEE -> rang de la commune
    rangsDeps: [],
    boites: {}, // département -> [ouest, sud, est, nord]
    polys: new Map(), // département -> contours des communes, pour le survol
    contours: new Map(),
    opacites: {},
    seuil: 10,
    deps: null, // null = France entière
    cle: null,
    bornes: FRANCE,
    bilan: null,
    codes: [],
    cumul: [0],
    etape: 0,
    cadence: 600, // millisecondes entre deux maisons posées à la suite
    jeton: 0,
    marqueurs: new Map(),
    focus: null,
    orbite: 0,
    glisse: 0,
    pivot: null,
    survole: null,
    etiquettes: [], // noms du fond de carte, masqués pendant une plongée
    sansNoms: false,
    travaux: 0,
    envoiPerimetre: 0,
  };

  // ------------------------------------------------------------------ utilitaires

  function quandPret(action) {
    if (E.pret) action();
    else E.attente.push(action);
  }

  /* Un petit témoin « Chargement… » tant qu'une donnée est en route. */
  async function lire(url) {
    racine.classList.toggle("charge", ++E.travaux > 0);
    try {
      // Le numéro de version évite qu'un navigateur réutilise des données d'un ancien format.
      return await (await fetch(url.startsWith("/donnees/") ? `${url}?v=${VERSION}` : url)).json();
    } finally {
      racine.classList.toggle("charge", --E.travaux > 0);
    }
  }

  function marges() {
    if (window.innerWidth <= 860) return { left: 20, top: 20, right: 20, bottom: Math.round(window.innerHeight * 0.5) };
    // La fenêtre de chat ouverte occupe le bas à droite : la carte cadre à côté d'elle.
    // (La fenêtre de chat ne tient que le coin : elle ne décale plus le centre de la carte.)
    const droite = 60;
    return { left: racine.classList.contains("panneau-replie") ? 40 : 400, top: 30, right: droite, bottom: 80 };
  }

  function cadrer() {
    E.carte.setPadding(marges());
  }

  function compter(el, cible, prefixe, suffixe) {
    if (!el) return;
    const depart = Number(el.dataset.v || 0);
    el.dataset.v = cible;
    const ecrire = (v) => (el.textContent = (prefixe || "") + nf.format(Math.round(v)) + (suffixe || ""));
    if (calme || depart === cible || document.hidden) return ecrire(cible);
    const t0 = performance.now();
    el.dataset.t = t0;
    (function image(t) {
      if (Number(el.dataset.t) !== t0) return;
      const a = borner((t - t0) / 650, 0, 1);
      ecrire(depart + (cible - depart) * adoucir(a));
      if (a < 1) requestAnimationFrame(image);
    })(t0);
  }

  function distance(lon1, lat1, lon2, lat2) {
    const rad = Math.PI / 180;
    const a =
      Math.sin(((lat2 - lat1) * rad) / 2) ** 2 +
      Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(((lon2 - lon1) * rad) / 2) ** 2;
    return 12742 * Math.asin(Math.sqrt(a));
  }

  function cercle(lon, lat, km, front) {
    const pts = [];
    const dLat = km / 110.574;
    const dLon = km / (111.32 * Math.cos((lat * Math.PI) / 180));
    for (let i = 0; i <= 72; i++) {
      const a = (i / 72) * 2 * Math.PI;
      pts.push([lon + dLon * Math.cos(a), lat + dLat * Math.sin(a)]);
    }
    return { type: "Feature", properties: { front: front ? 1 : 0 }, geometry: { type: "Polygon", coordinates: [pts] } };
  }

  /* Index spatial simple : des cases de `pas` degrés, pour retrouver vite les points voisins. */
  function grille(lon, lat, pas) {
    const cases = new Map();
    for (let i = 0; i < lon.length; i++) {
      const cle = Math.floor(lon[i] / pas) * 10000 + Math.floor(lat[i] / pas);
      if (!cases.has(cle)) cases.set(cle, []);
      cases.get(cle).push(i);
    }
    return { cases, pas };
  }

  function autour(g, lon, lat, km, action) {
    const dLat = km / 110.574;
    const dLon = km / (111.32 * Math.cos((lat * Math.PI) / 180));
    for (let x = Math.floor((lon - dLon) / g.pas); x <= Math.floor((lon + dLon) / g.pas); x++) {
      for (let y = Math.floor((lat - dLat) / g.pas); y <= Math.floor((lat + dLat) / g.pas); y++) {
        const liste = g.cases.get(x * 10000 + y);
        if (liste) for (const i of liste) action(i);
      }
    }
  }

  function communeProche(lon, lat, km) {
    let meilleur = null;
    let mini = km;
    autour(E.grilleCommunes, lon, lat, km, (i) => {
      const d = distance(lon, lat, E.C.lon[i], E.C.lat[i]);
      if (d < mini) [mini, meilleur] = [d, i];
    });
    return meilleur;
  }

  function zoomSite(lat, part) {
    const hauteur = E.carte.getContainer().clientHeight;
    const mpp = (2 * E.seuil * 1000) / (hauteur * part);
    return Math.log2((78271.5 * Math.cos((lat * Math.PI) / 180)) / mpp);
  }

  function dansPerimetre(dep) {
    return !E.deps || E.deps.has(dep);
  }

  function depsVisibles() {
    const vue = E.carte.getBounds();
    return Object.keys(E.boites).filter((d) => {
      const b = E.boites[d];
      return b[0] <= vue.getEast() && b[2] >= vue.getWest() && b[1] <= vue.getNorth() && b[3] >= vue.getSouth();
    });
  }

  // ------------------------------------------------------------------ démarrage

  async function styleCarte() {
    const secours = {
      version: 8,
      sources: {},
      layers: [{ id: "background", type: "background", paint: { "background-color": E.theme.nuit } }],
    };
    try {
      const style = await (await fetch("https://tiles.openfreemap.org/styles/dark")).json();
      for (const calque of style.layers) {
        if (calque.id === "background") calque.paint["background-color"] = E.theme.nuit;
        if (calque.id === "water") calque.paint["fill-color"] = "#0b1424";
        if (calque.id === "waterway") calque.paint["line-color"] = "#0b1424";
        if (calque.id.startsWith("place_")) {
          calque.paint["text-color"] = "#93a0b8";
          calque.paint["text-halo-color"] = "rgba(5,8,15,0.85)";
        }
        if (calque.id.startsWith("boundary_country")) calque.paint["line-color"] = "#3a465c";
      }
      return style;
    } catch (erreur) {
      console.warn("Fond de carte indisponible, affichage sans tuiles.", erreur);
      return secours;
    }
  }

  async function demarrer(el) {
    E.theme = JSON.parse(el.dataset.theme);
    const [style, C, M, G, deps] = await Promise.all([
      styleCarte(),
      lire("/donnees/communes"),
      lire("/donnees/maisons"),
      lire("/donnees/gros/10.0"),
      lire("/assets/geo/departements.geojson"),
    ]);
    E.C = C;
    E.M = M;
    E.gros.set(10, G);
    E.position = new Map(C.code.map((c, i) => [c, i]));
    E.grilleCommunes = grille(C.lon, C.lat, 0.1);
    for (let i = 0; i < C.lon.length; i++) {
      const b = (E.boites[C.dep[i]] = E.boites[C.dep[i]] || [180, 90, -180, -90]);
      b[0] = Math.min(b[0], C.lon[i]); b[1] = Math.min(b[1], C.lat[i]);
      b[2] = Math.max(b[2], C.lon[i]); b[3] = Math.max(b[3], C.lat[i]);
    }
    E.rangsDeps = Object.keys(E.boites).sort();
    deps.features = deps.features.filter((f) => f.properties.code in C.departements);
    construireArbre();

    // La carte s'ouvre directement cadrée sur la France.
    const carte = new maplibregl.Map({
      container: el, style, bounds: FRANCE, fitBoundsOptions: { padding: marges() }, maxPitch: 70,
      attributionControl: { compact: true },
    });
    E.carte = carte;
    carte.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "bottom-right");
    carte.addControl(boutonEnsemble(), "bottom-right");
    window.pharesCarte = carte;

    carte.on("load", () => {
      // Projection plane : en mode globe, MapLibre 5.7 n'affiche pas ensemble volumes 3D et aplats translucides.
      installerCalques(deps);
      E.pret = true;
      cadrer();
      // Les marges sont maintenant celles de la carte : on recadre, sinon elles compteraient deux fois.
      carte.fitBounds(E.bornes, { padding: 30, duration: 0, maxZoom: 10 });
      afficherGros(10);
      filtrer();
      E.attente.splice(0).forEach((action) => action());
      racine.classList.add("carte-prete");
    });
    carte.on("mousemove", survol);
    carte.on("mouseout", finSurvol);
    carte.on("movestart", finSurvol);
    carte.on("click", clicCarte);
    carte.on("moveend", chargerContours);
    ["mousedown", "touchstart", "wheel"].forEach((evt) => carte.on(evt, () => (E.orbite = 0)));
    window.addEventListener("resize", () => E.pret && cadrer());
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && racine.classList.contains("chat-ouvert")) {
        racine.classList.remove("chat-ouvert");
        return;
      }
      if (e.key === "Escape" && E.focus) dc().set_props("commune-active", { data: null });
    });
    pivoterAuClicMolette(carte);
    // Survoler une commune dans la fiche allume sa tour sur la carte.
    const ligne = (e) => (e.target.closest ? e.target.closest(".communes li[data-code]") : null);
    document.addEventListener("mouseover", (e) => ligne(e) && allumer(ligne(e).dataset.code));
    document.addEventListener("mouseout", (e) => ligne(e) && !ligne(e).contains(e.relatedTarget) && allumer(null));
  }

  function boutonEnsemble() {
    return {
      onAdd() {
        this.el = document.createElement("div");
        this.el.className = "maplibregl-ctrl maplibregl-ctrl-group";
        this.el.innerHTML = `<button type="button" class="ctrl-ensemble" title="Vue d'ensemble" aria-label="Vue d'ensemble">⤢</button>`;
        this.el.firstChild.addEventListener("click", () => {
          if (E.focus) dc().set_props("commune-active", { data: null });
          else ensemble(true);
        });
        return this.el;
      },
      onRemove() {
        this.el.remove();
      },
    };
  }

  /* Clic molette maintenu : glisser à l'horizontale fait pivoter, à la verticale incline. */
  function pivoterAuClicMolette(carte) {
    const zone = carte.getCanvasContainer();
    zone.addEventListener("mousedown", (e) => {
      if (e.button !== 1) return;
      e.preventDefault();
      E.orbite = 0;
      E.pivot = { x: e.clientX, y: e.clientY, cap: carte.getBearing(), pente: carte.getPitch() };
    });
    zone.addEventListener("auxclick", (e) => e.button === 1 && e.preventDefault());
    window.addEventListener("mousemove", (e) => {
      if (!E.pivot) return;
      carte.setBearing(E.pivot.cap + (e.clientX - E.pivot.x) * 0.4);
      carte.setPitch(borner(E.pivot.pente - (e.clientY - E.pivot.y) * 0.3, 0, 70));
    });
    window.addEventListener("mouseup", (e) => {
      if (e.button === 1) E.pivot = null;
    });
  }

  function installerCalques(deps) {
    const carte = E.carte;
    const T = E.theme;
    const symbole = carte.getStyle().layers.find((c) => c.type === "symbol");
    const avant = symbole ? symbole.id : undefined;
    const ajouter = (calque) => carte.addLayer(calque, avant);
    E.etiquettes = carte.getStyle().layers.filter((c) => c.type === "symbol" && (c.layout || {}).visibility !== "none").map((c) => c.id);

    carte.addSource("deps", { type: "geojson", data: deps });
    for (const nom of ["gros", "ondes", "fantome", "selection", "survol", "contours"]) {
      carte.addSource(nom, { type: "geojson", data: collection() });
    }
    // Carreaux de 1 km et de 200 m : des tuiles fabriquées par le serveur, demandées seulement pour l'écran.
    const tuiles = (niveau) => [`${location.origin}/tuiles/${niveau}/{z}/{x}/{y}.pbf?v=${VERSION}`];
    carte.addSource("moyens", { type: "vector", tiles: tuiles("moyens"), minzoom: 7, maxzoom: 8 });
    carte.addSource("fins", { type: "vector", tiles: tuiles("fins"), minzoom: 9, maxzoom: 10 });
    carte.addSource("msp", {
      type: "geojson",
      data: collection(E.M.lon.map((lon, i) => ({ type: "Feature", properties: { i, nom: E.M.nom[i] }, geometry: { type: "Point", coordinates: [lon, E.M.lat[i]] } }))),
    });

    // Tailles : aux niveaux regroupés, un point proportionnel aux habitants ; à 200 m, la taille du carreau.
    const selon = (champ, paliers) => ["interpolate", ["linear"], ["sqrt", ["get", champ]], ...paliers];
    const rayonGros = (champ, f) => {
      const base = selon(champ, [0, 0.5, 20, 1, 60, 1.8, 150, 3, 400, 5]);
      return ["interpolate", ["linear"], ["zoom"], 4, ["*", 0.6 * f, base], 5.4, ["*", f, base], 8, ["*", 2.6 * f, base]];
    };
    const rayonMoyen = (f) => {
      const base = selon("p", [0, 0.7, 30, 1.6, 100, 3]);
      return ["interpolate", ["linear"], ["zoom"], 6.4, ["*", 0.7 * f, base], 8, ["*", 1.5 * f, base], 10.4, ["*", 3.2 * f, base]];
    };
    const rayonFin = (f) => ["interpolate", ["exponential", 2], ["zoom"], 8, 0.42 * f, 18, 430 * f];
    // Chaque niveau apparaît et disparaît en fondu selon le zoom.
    const fondus = {
      gros: (v) => ["interpolate", ["linear"], ["zoom"], 7.2, v, 8, 0],
      moyens: (v) => ["interpolate", ["linear"], ["zoom"], 7, 0, 7.6, v, 9.6, v, 10.4, 0],
      fins: (v) => ["interpolate", ["linear"], ["zoom"], 9, 0, 9.6, v],
    };
    const zooms = { gros: { maxzoom: 8 }, moyens: { minzoom: 7, maxzoom: 10.4 }, fins: { minzoom: 9 } };
    const densite = ["interpolate", ["linear"], ["get", "p"], 0, 0.3, 5, 0.55, 30, 0.85, 150, 1];
    const poids = (champ) => selon(champ, [0, 0.25, 4, 0.55, 12, 0.85, 30, 1]);
    const point = (id, niveau, paint) =>
      ajouter({ id, type: "circle", source: niveau, ...(niveau === "gros" ? {} : { "source-layer": "h" }), ...zooms[niveau], filter: JAMAIS, paint });
    const cerne = { "circle-stroke-color": "#ffffff", "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 5, 0.4, 9, 1.2] };

    ajouter({ id: "deps-traits", type: "line", source: "deps", paint: { "line-color": "#ffffff", "line-opacity": 0.09, "line-width": 0.6 } });
    // Aux trois niveaux : habitants déjà couverts (bleu), loin des soins (orange), rapprochés (vert cerné).
    // Les filtres qui répartissent les points entre ces calques sont posés par filtrer().
    point("gros-couvert", "gros", { "circle-color": T.couvert, "circle-radius": rayonGros("pp", 0.8), "circle-opacity": 0 });
    point("gros-halo", "gros", { "circle-color": T.braise, "circle-blur": 1, "circle-radius": rayonGros("pl", 2.6), "circle-opacity": 0 });
    point("gros-braise", "gros", { "circle-color": T.braise, "circle-radius": rayonGros("pl", 1.1), "circle-opacity": 0 });
    point("moyens-couvert", "moyens", { "circle-color": T.couvert, "circle-radius": rayonMoyen(0.8), "circle-opacity": 0 });
    point("moyens-halo", "moyens", { "circle-color": T.braise, "circle-blur": 1, "circle-radius": rayonMoyen(2.4), "circle-opacity": 0 });
    point("moyens-braise", "moyens", { "circle-color": T.braise, "circle-radius": rayonMoyen(1), "circle-opacity": 0 });
    point("fins-couvert", "fins", { "circle-color": T.couvert, "circle-radius": rayonFin(0.9), "circle-pitch-alignment": "map", "circle-opacity": 0 });
    point("fins-braise", "fins", { "circle-color": T.braise, "circle-radius": rayonFin(0.95), "circle-pitch-alignment": "map", "circle-opacity": 0 });
    // Hors périmètre, les points sont atténués par eux-mêmes (en plus du voile).
    E.opacites = {
      "gros-couvert": (dedans) => fondus.gros(["*", 0.5, poids("pp"), dedans]),
      "gros-halo": (dedans) => fondus.gros(["*", 0.18, poids("pl"), dedans]),
      "gros-braise": (dedans) => fondus.gros(["*", poids("pl"), dedans]),
      "moyens-couvert": (dedans) => fondus.moyens(["*", 0.5, poids("p"), dedans]),
      "moyens-halo": (dedans) => fondus.moyens(["*", 0.16, poids("p"), dedans]),
      "moyens-braise": (dedans) => fondus.moyens(["*", poids("p"), dedans]),
      "fins-couvert": (dedans) => fondus.fins(["*", 0.6, densite, dedans]),
      "fins-braise": (dedans) => fondus.fins(["*", densite, dedans]),
    };

    // Les icônes passent sous le voile : au-dessus d'un aplat translucide, elles l'effacent.
    carte.addImage("msp", iconeMaison(T.existant), { pixelRatio: 2 });
    ajouter({
      id: "msp", type: "symbol", source: "msp", minzoom: 6.4,
      layout: { "icon-image": "msp", "icon-allow-overlap": true, "icon-size": ["interpolate", ["linear"], ["zoom"], 6.4, 0.32, 9, 0.62, 12, 0.95] },
    });
    ajouter({ id: "deps-ombre", type: "fill", source: "deps", filter: AUCUN_DEP, paint: { "fill-color": "#02040a", "fill-opacity": 0.5 } });
    ajouter({ id: "deps-perimetre", type: "line", source: "deps", filter: AUCUN_DEP, paint: { "line-color": "#ffffff", "line-opacity": 0.38, "line-width": 1.1 } });
    ajouter({ id: "ondes-fond", type: "fill", source: "ondes", paint: { "fill-color": T.phare, "fill-opacity": 0.07 } });
    ajouter({
      id: "ondes-trait", type: "line", source: "ondes",
      paint: {
        "line-color": T.phare,
        "line-opacity": ["case", ["==", ["get", "front"], 1], 1, 0.55],
        "line-width": ["case", ["==", ["get", "front"], 1], 2.6, 1.1],
      },
    });
    ajouter({ id: "fantome", type: "line", source: "fantome", paint: { "line-color": "#ffffff", "line-width": 1.6, "line-dasharray": [2, 2], "line-opacity": 0.9 } });
    ajouter({ id: "survol", type: "line", source: "survol", paint: { "line-color": "#ffffff", "line-width": 1.6, "line-opacity": 0.95 } });
    ajouter({ id: "selection", type: "line", source: "selection", paint: { "line-color": "#ffffff", "line-width": 2.6 } });
    point("gros-phare-halo", "gros", { "circle-color": T.phare, "circle-blur": 1, "circle-radius": rayonGros("pl", 3), "circle-opacity": fondus.gros(0.4) });
    point("gros-phare", "gros", { "circle-color": T.phare, "circle-radius": rayonGros("pl", 1.3), "circle-opacity": fondus.gros(1), "circle-stroke-opacity": fondus.gros(1), ...cerne });
    point("moyens-phare", "moyens", { "circle-color": T.phare, "circle-radius": rayonMoyen(1.15), "circle-opacity": fondus.moyens(1), "circle-stroke-opacity": fondus.moyens(1), ...cerne });
    point("fins-phare", "fins", {
      "circle-color": T.phare, "circle-radius": rayonFin(0.95), "circle-pitch-alignment": "map", "circle-opacity": fondus.fins(densite),
      "circle-stroke-color": "#ffffff", "circle-stroke-opacity": fondus.fins(1), "circle-stroke-width": ["interpolate", ["linear"], ["zoom"], 10, 0, 13, 1],
    });
    ajouter({
      id: "contours-3d", type: "fill-extrusion", source: "contours",
      layout: { visibility: "none" }, // activée seulement en plongée sur une maison
      paint: { "fill-extrusion-color": T.phare, "fill-extrusion-height": 0, "fill-extrusion-opacity": 0.72 },
    });
    ajouter({ id: "contours-trait", type: "line", source: "contours", paint: { "line-color": "#ffffff", "line-opacity": 0.5, "line-width": 0.8 } });
    carte.addLayer({
      id: "contours-noms", type: "symbol", source: "contours", minzoom: 8.5,
      layout: { "text-field": ["concat", ["get", "nom"], "\n", ["get", "etiq"]], "text-font": ["Noto Sans Regular"], "text-size": 11, "symbol-sort-key": ["-", 0, ["get", "hab"]] },
      paint: { "text-color": "#ffffff", "text-halo-color": "rgba(5,8,15,0.9)", "text-halo-width": 1.4 },
    });
    appliquerPerimetre();
  }

  /* Une petite maison marquée d'une croix, pour les maisons de santé existantes. */
  function iconeMaison(couleur) {
    const t = 48;
    const toile = document.createElement("canvas");
    toile.width = toile.height = t;
    const c = toile.getContext("2d");
    c.lineJoin = "round";
    c.beginPath();
    c.moveTo(24, 5); c.lineTo(44, 23); c.lineTo(38, 23); c.lineTo(38, 43); c.lineTo(10, 43); c.lineTo(10, 23); c.lineTo(4, 23);
    c.closePath();
    c.fillStyle = couleur;
    c.strokeStyle = "rgba(5,8,15,0.95)";
    c.lineWidth = 3;
    c.fill();
    c.stroke();
    c.strokeStyle = "#05080f";
    c.lineWidth = 4.5;
    c.lineCap = "round";
    c.beginPath();
    c.moveTo(24, 24); c.lineTo(24, 38); c.moveTo(17, 31); c.lineTo(31, 31);
    c.stroke();
    return c.getImageData(0, 0, t, t);
  }

  // ------------------------------------------------------------------ périmètre : France, régions, départements

  /* Arborescence à cases à trois états. Cliquer un parent dont au moins un enfant est coché
     décoche tout ; sinon coche tout. Le résultat part dans le store « perimetre ». */
  function construireArbre() {
    const zone = $("perimetre-zone");
    if (!zone) return;
    const deps = E.C.departements;
    const regions = Object.entries(E.C.regions).sort((a, b) => a[1].localeCompare(b[1], "fr"));
    const enfants = (region) => Object.keys(deps).filter((d) => deps[d].region === region).sort();
    const choix = new Set(Object.keys(deps));
    const ligne = (genre, code, texte, pliable) =>
      `<div class="arbre-ligne">${pliable ? `<button type="button" class="arbre-plier" aria-label="Déplier ${texte}" aria-expanded="false">▸</button>` : `<span class="arbre-vide"></span>`}` +
      `<button type="button" class="arbre-choix" role="checkbox" data-genre="${genre}" data-code="${code}"><span class="case"></span><span class="arbre-nom">${texte}</span></button></div>`;
    zone.innerHTML =
      `<button type="button" class="arbre-bouton" aria-haspopup="tree" aria-expanded="false"><span class="arbre-resume"></span><span>▾</span></button>` +
      `<div class="arbre-menu" hidden><input type="search" class="arbre-filtre" placeholder="Filtrer…" aria-label="Filtrer les territoires">` +
      `<ul class="arbre" role="tree"><li>${ligne("fr", "FR", "France entière", false)}<ul>` +
      regions.map(([r, nom]) => `<li data-region="${r}">${ligne("reg", r, nom, true)}<ul hidden>` +
        enfants(r).map((d) => `<li data-dep="${d}">${ligne("dep", d, `${d} · ${deps[d].nom}`, false)}</li>`).join("") + `</ul></li>`).join("") +
      `</ul></li></ul></div>`;
    const menu = zone.querySelector(".arbre-menu");
    const bouton = zone.querySelector(".arbre-bouton");
    const portee = (genre, code) => (genre === "fr" ? Object.keys(deps) : genre === "reg" ? enfants(code) : [code]);

    function valeur() {
      if (choix.size === Object.keys(deps).length) return ["FR"];
      const liste = [];
      for (const [r] of regions) {
        const e = enfants(r);
        if (e.every((d) => choix.has(d))) liste.push(`R:${r}`);
        else for (const d of e) if (choix.has(d)) liste.push(`D:${d}`);
      }
      return liste;
    }

    function rafraichir(envoyer) {
      zone.querySelectorAll(".arbre-choix").forEach((b) => {
        const e = portee(b.dataset.genre, b.dataset.code);
        const n = e.filter((d) => choix.has(d)).length;
        b.setAttribute("aria-checked", n === 0 ? "false" : n === e.length ? "true" : "mixed");
      });
      const v = valeur();
      const noms = v.map((x) => (x === "FR" ? "France entière" : x.startsWith("R:") ? E.C.regions[x.slice(2)] : deps[x.slice(2)].nom));
      const nr = v.filter((x) => x.startsWith("R:")).length;
      const nd = v.filter((x) => x.startsWith("D:")).length;
      zone.querySelector(".arbre-resume").textContent = !v.length
        ? "Aucun territoire"
        : noms.length <= 2
          ? noms.join(", ")
          : [nr && `${nr} région${nr > 1 ? "s" : ""}`, nd && `${nd} département${nd > 1 ? "s" : ""}`].filter(Boolean).join(", ");
      if (!envoyer) return;
      clearTimeout(E.envoiPerimetre);
      E.envoiPerimetre = setTimeout(() => dc().set_props("perimetre", { data: v }), 350);
    }

    zone.addEventListener("click", (e) => {
      const plier = e.target.closest(".arbre-plier");
      const coche = e.target.closest(".arbre-choix");
      if (e.target.closest(".arbre-bouton")) {
        menu.hidden = !menu.hidden;
        bouton.setAttribute("aria-expanded", String(!menu.hidden));
      } else if (plier) {
        const sous = plier.closest("li").querySelector("ul");
        sous.hidden = !sous.hidden;
        plier.setAttribute("aria-expanded", String(!sous.hidden));
        plier.textContent = sous.hidden ? "▸" : "▾";
      } else if (coche) {
        const concernes = portee(coche.dataset.genre, coche.dataset.code);
        const deja = concernes.some((d) => choix.has(d));
        concernes.forEach((d) => (deja ? choix.delete(d) : choix.add(d)));
        rafraichir(true);
      }
    });
    zone.querySelector(".arbre-filtre").addEventListener("input", (e) => {
      const texte = e.target.value.trim().toLowerCase();
      zone.querySelectorAll("li[data-region]").forEach((li) => {
        const nomRegion = li.querySelector(".arbre-nom").textContent.toLowerCase();
        let trouve = !texte || nomRegion.includes(texte);
        li.querySelectorAll("li[data-dep]").forEach((d) => {
          const vu = !texte || nomRegion.includes(texte) || d.textContent.toLowerCase().includes(texte);
          d.hidden = !vu;
          trouve = trouve || vu;
        });
        li.hidden = !trouve;
        const sous = li.querySelector("ul");
        sous.hidden = !texte;
        li.querySelector(".arbre-plier").textContent = sous.hidden ? "▸" : "▾";
      });
    });
    document.addEventListener("click", (e) => {
      if (menu.hidden || zone.contains(e.target)) return;
      menu.hidden = true;
      bouton.setAttribute("aria-expanded", "false");
    });
    // Quand le périmètre change ailleurs (assistant, lien de scénario), l'arborescence le reflète.
    E.fixerArbre = (valeurs) => {
      const voulus = departementsDe(valeurs);
      const liste = voulus === null ? Object.keys(deps) : [...voulus];
      if (liste.length === choix.size && liste.every((d) => choix.has(d))) return;
      choix.clear();
      liste.forEach((d) => choix.add(d));
      rafraichir(false);
    };
    rafraichir(false);
  }

  function departementsDe(valeurs) {
    if (!valeurs || !valeurs.length) return new Set();
    if (valeurs.includes("FR")) return null;
    const retenus = new Set();
    for (const v of valeurs) {
      const [genre, code] = v.split(":");
      if (genre === "D") retenus.add(code);
      else for (const [d, infos] of Object.entries(E.C.departements)) if (infos.region === code) retenus.add(d);
    }
    return retenus;
  }

  // ------------------------------------------------------------------ contexte : périmètre et distance

  /* Répartit les points entre les calques : déjà couverts, loin des soins, ou rapprochés par les
     maisons posées jusqu'à l'étape en cours (dans leur rayon et à l'intérieur du périmètre).
     Même règle que le moteur Python, qui fait foi pour les chiffres. */
  function filtrer() {
    if (!E.pret) return;
    const carte = E.carte;
    const maisons = E.bilan ? E.bilan.maisons.slice(0, E.etape) : [];
    const rayon = E.bilan ? E.bilan.seuil : E.seuil;
    const disques = maisons.length
      ? ["within", { type: "MultiPolygon", coordinates: maisons.map((m) => cercle(m.lon, m.lat, rayon).geometry.coordinates) }]
      : null;
    const dedans = E.deps ? [["in", ["get", "dep"], ["literal", [...E.deps]]]] : [];
    for (const niveau of ["gros", "moyens", "fins"]) {
      const loin = niveau === "gros" ? [">", ["get", "pl"], 0] : [">=", ["get", "q"], E.seuil];
      const couvert = niveau === "gros" ? [">", ["get", "pp"], 0] : ["<", ["get", "q"], E.seuil];
      const rapproche = disques ? ["all", loin, disques, ...dedans] : JAMAIS;
      const braise = disques ? ["all", loin, ["!", ["all", disques, ...dedans]]] : loin;
      carte.setFilter(`${niveau}-couvert`, couvert);
      carte.setFilter(`${niveau}-braise`, braise);
      carte.setFilter(`${niveau}-phare`, rapproche);
      if (niveau !== "fins") carte.setFilter(`${niveau}-halo`, braise);
      if (niveau === "gros") carte.setFilter("gros-phare-halo", rapproche);
    }
  }

  function appliquerPerimetre() {
    const liste = E.deps ? ["literal", [...E.deps]] : null;
    E.carte.setFilter("deps-ombre", liste ? ["!", ["in", ["get", "code"], liste]] : AUCUN_DEP);
    E.carte.setFilter("deps-perimetre", liste ? ["in", ["get", "code"], liste] : AUCUN_DEP);
    const dedans = liste ? ["case", ["in", ["get", "dep"], liste], 1, 0.5] : 1;
    for (const [calque, opacite] of Object.entries(E.opacites)) E.carte.setPaintProperty(calque, "circle-opacity", opacite(dedans));
    filtrer();
  }

  /* Cases de 5 km : elles dépendent de la distance, le serveur les recalcule pour chacune. */
  async function afficherGros(seuil) {
    if (!E.gros.has(seuil)) E.gros.set(seuil, await lire(`/donnees/gros/${seuil.toFixed(1)}`));
    if (seuil !== E.seuil) return; // une autre distance a été choisie entre-temps
    const G = E.gros.get(seuil);
    const formes = new Array(G.lon.length);
    for (let i = 0; i < formes.length; i++) {
      formes[i] = { type: "Feature", properties: { pl: G.loin[i], pp: G.proche[i], dep: G.dep[i] }, geometry: { type: "Point", coordinates: [G.lon[i], G.lat[i]] } };
    }
    E.carte.getSource("gros").setData(collection(formes));
  }

  function contexte(perimetre, glisse, valeur) {
    const source = dc().callback_context.triggered.map((t) => t.prop_id);
    quandPret(() => {
      if (E.fixerArbre) E.fixerArbre(perimetre);
      const seuil = (source.includes("seuil.value") ? valeur : glisse || valeur) || 10;
      if (seuil !== E.seuil) {
        E.seuil = seuil;
        filtrer();
        afficherGros(seuil);
      }
      const cle = (perimetre || []).slice().sort().join("|");
      if (cle === E.cle) return;
      const premier = E.cle === null;
      E.cle = cle;
      E.deps = departementsDe(perimetre);
      appliquerPerimetre();
      E.bornes = FRANCE;
      if (E.deps && E.deps.size) {
        const b = [180, 90, -180, -90];
        for (const d of E.deps) {
          const x = E.boites[d];
          if (!x) continue;
          b[0] = Math.min(b[0], x[0]); b[1] = Math.min(b[1], x[1]); b[2] = Math.max(b[2], x[2]); b[3] = Math.max(b[3], x[3]);
        }
        E.bornes = [[b[0], b[1]], [b[2], b[3]]];
      }
      cadrer();
      if (!E.focus && !premier) {
        E.carte.fitBounds(E.bornes, { padding: 30, duration: calme ? 0 : 1600, pitch: 0, bearing: 0, maxZoom: 10, essential: true });
      }
    });
  }

  // ------------------------------------------------------------------ contours des communes

  /* Contours des communes à l'écran : ils servent au survol et au clic. */
  async function contoursDe(dep) {
    if (!E.contours.has(dep)) E.contours.set(dep, lire(`/donnees/contours/${dep}`));
    return E.contours.get(dep);
  }

  async function chargerContours() {
    if (!E.pret || E.carte.getZoom() < ZOOM_COMMUNE - 0.4) return;
    const visibles = depsVisibles();
    if (!visibles.length || visibles.length > 14) return;
    for (const dep of visibles.filter((d) => !E.polys.has(d))) {
      E.polys.set(dep, []);
      const lot = await contoursDe(dep);
      E.polys.set(
        dep,
        lot.features.map((f) => {
          const boite = [180, 90, -180, -90];
          const anneaux = f.geometry.type === "Polygon" ? [f.geometry.coordinates] : f.geometry.coordinates;
          for (const poly of anneaux) for (const [x, y] of poly[0]) {
            boite[0] = Math.min(boite[0], x); boite[1] = Math.min(boite[1], y); boite[2] = Math.max(boite[2], x); boite[3] = Math.max(boite[3], y);
          }
          return { code: f.id, boite, anneaux, geometry: f.geometry };
        })
      );
    }
  }

  function dansAnneau(anneau, x, y) {
    let dedans = false;
    for (let i = 0, j = anneau.length - 1; i < anneau.length; j = i++) {
      const [xi, yi] = anneau[i];
      const [xj, yj] = anneau[j];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) dedans = !dedans;
    }
    return dedans;
  }

  /* La commune dont le contour contient le point, parmi celles chargées. */
  function communeSous(lng, lat) {
    for (const [dep, liste] of E.polys) {
      const b = E.boites[dep];
      if (lng < b[0] - 0.3 || lng > b[2] + 0.3 || lat < b[1] - 0.3 || lat > b[3] + 0.3) continue;
      for (const c of liste) {
        if (lng < c.boite[0] || lng > c.boite[2] || lat < c.boite[1] || lat > c.boite[3]) continue;
        for (const poly of c.anneaux) {
          if (dansAnneau(poly[0], lng, lat) && !poly.slice(1).some((trou) => dansAnneau(trou, lng, lat))) return c;
        }
      }
    }
    return null;
  }

  // ------------------------------------------------------------------ rayons des maisons posées

  function dessinerOndes(k, rayonKm) {
    const maisons = E.bilan.maisons;
    const formes = [];
    for (let j = 0; j < k; j++) {
      const dernier = j === k - 1 && rayonKm != null;
      formes.push(cercle(maisons[j].lon, maisons[j].lat, dernier ? Math.max(rayonKm, 0.05) : E.bilan.seuil, dernier));
    }
    E.carte.getSource("ondes").setData(collection(formes));
  }

  // ------------------------------------------------------------------ balises : une par nouvelle maison

  function creerMarqueur(maison) {
    const ancre = document.createElement("div");
    ancre.className = "balise-ancre";
    const bouton = document.createElement("button");
    bouton.className = "balise cachee";
    bouton.type = "button";
    bouton.innerHTML = `<span class="balise-onde"></span><span class="balise-num"></span>`;
    bouton.addEventListener("click", (e) => {
      e.stopPropagation();
      if (performance.now() - E.glisse < 400) return;
      dc().set_props("commune-active", { data: marqueur.code });
    });
    ancre.appendChild(bouton);
    const marqueur = new maplibregl.Marker({ element: ancre, anchor: "center", draggable: true })
      .setLngLat([maison.lon, maison.lat])
      .addTo(E.carte);
    marqueur.bouton = bouton;
    marqueur.code = maison.code;
    marqueur.on("drag", () => {
      const p = marqueur.getLngLat();
      const i = communeProche(p.lng, p.lat, 15);
      const cible = i == null ? p : { lng: E.C.lon[i], lat: E.C.lat[i] };
      E.carte.getSource("fantome").setData(collection([cercle(cible.lng, cible.lat, E.seuil, false)]));
      const pos = E.carte.project(p);
      bulle(i == null ? "<b>Hors de France</b>" : `<b>${E.C.nom[i]} (${E.C.dep[i]})</b><span>Lâchez pour déplacer la maison ici</span>`, pos.x, pos.y);
    });
    marqueur.on("dragend", () => {
      E.glisse = performance.now();
      bulle(null);
      E.carte.getSource("fantome").setData(collection());
      const p = marqueur.getLngLat();
      const i = communeProche(p.lng, p.lat, 15);
      const code = i == null ? null : E.C.code[i];
      if (!code || code === marqueur.code || E.codes.includes(code)) {
        const m = E.bilan.maisons.find((x) => x.code === marqueur.code);
        return marqueur.setLngLat([m.lon, m.lat]);
      }
      marqueur.setLngLat([E.C.lon[i], E.C.lat[i]]);
      if (E.focus === marqueur.code) dc().set_props("commune-active", { data: code });
      dc().set_props("maisons", { data: E.codes.map((c) => (c === marqueur.code ? code : c)) });
    });
    return marqueur;
  }

  function synchroniserMarqueurs(maisons) {
    const codes = new Set(maisons.map((m) => m.code));
    for (const [code, marqueur] of E.marqueurs) {
      if (codes.has(code)) continue;
      marqueur.remove();
      E.marqueurs.delete(code);
    }
    maisons.forEach((m) => {
      if (!E.marqueurs.has(m.code)) E.marqueurs.set(m.code, creerMarqueur(m));
      const marqueur = E.marqueurs.get(m.code);
      marqueur.bouton.querySelector(".balise-num").textContent = m.k;
      marqueur.bouton.title = `${m.k} · ${m.commune} : +${nf.format(m.seule)} habitants seule`;
      marqueur.bouton.setAttribute("aria-label", marqueur.bouton.title);
    });
  }

  // ------------------------------------------------------------------ bilan : nouvel état des maisons

  function chiffres(bilan, gain) {
    compter($("kpi-gain"), gain, "+");
    compter($("kpi-reste"), bilan.loin - gain);
    compter($("kpi-part"), bilan.loin ? (100 * gain) / bilan.loin : 0, "", " %");
  }

  function surBilan(bilan) {
    if (!bilan) return;
    // Les chiffres du panneau ne dépendent pas de la carte : ils s'affichent même si elle tarde.
    compter($("constat-pop"), bilan.loin);
    $("constat-seuil").textContent = bilan.seuil;
    $("constat-part").textContent = bilan.habitants ? ((100 * bilan.loin) / bilan.habitants).toFixed(1).replace(".", ",") : "0";
    if (!E.pret) chiffres(bilan, bilan.rapproches);
    quandPret(() => {
      const avant = E.codes;
      const codes = bilan.maisons.map((m) => m.code);
      const jeton = ++E.jeton;
      E.bilan = bilan;
      E.codes = codes;
      E.cumul = [0];
      bilan.maisons.forEach((m) => E.cumul.push(E.cumul[E.cumul.length - 1] + m.ajout));
      synchroniserMarqueurs(bilan.maisons);

      // Des maisons ajoutées en fin de liste sont posées une à une ; tout autre changement s'applique d'un coup.
      let commun = 0;
      while (commun < avant.length && commun < codes.length && avant[commun] === codes[commun]) commun++;
      const nouvelles = codes.length - commun;
      const ajout = codes.length > avant.length && commun === avant.length;
      appliquerEtape(ajout ? commun : codes.length);
      if (!ajout) return E.focus && plonger(E.focus, false);
      E.cadence = borner(6000 / nouvelles, 250, 700);
      if (E.focus || nouvelles < 2 || calme) return etapeSuivante(jeton);
      // Plusieurs maisons d'un coup : on revient sur tout le périmètre pour les voir se répartir.
      E.carte.fitBounds(E.bornes, { padding: 30, duration: 900, pitch: 0, bearing: 0, maxZoom: 10, essential: true });
      setTimeout(() => etapeSuivante(jeton), 950);
    });
  }

  /* État complet après la k-ième maison, sans animation. */
  function appliquerEtape(k) {
    const b = E.bilan;
    if (!b) return;
    E.etape = k;
    b.maisons.forEach((m, j) => E.marqueurs.get(m.code).bouton.classList.toggle("cachee", j >= k));
    dessinerOndes(k, null);
    filtrer();
    chiffres(b, k >= b.maisons.length ? b.rapproches : E.cumul[k]);
  }

  /* Pose la maison suivante sans bouger la caméra : la balise tombe avec une impulsion, son anneau
     s'étend, puis les carreaux de son rayon changent de couleur. */
  function etapeSuivante(jeton) {
    const b = E.bilan;
    if (!b || jeton !== E.jeton) return;
    const n = b.maisons.length;
    const k = E.etape + 1;
    if (k > n) return;
    const m = b.maisons[k - 1];
    const duree = calme ? 0 : E.cadence * 0.8;
    const bouton = E.marqueurs.get(m.code).bouton;
    bouton.classList.remove("cachee");
    bouton.classList.add("pose");
    chiffres(b, k >= n ? b.rapproches : E.cumul[k]);
    const t0 = performance.now();
    (function image(t) {
      if (jeton !== E.jeton) return;
      const a = duree ? borner((t - t0) / duree, 0, 1) : 1;
      dessinerOndes(k, adoucir(a) * b.seuil);
      if (a < 1) return requestAnimationFrame(image);
      appliquerEtape(k);
      if (k < n) return setTimeout(() => etapeSuivante(jeton), calme ? 0 : E.cadence * 0.2);
      if (E.focus) plonger(E.focus, true);
    })(t0);
  }

  // ------------------------------------------------------------------ fiche d'une commune ou d'une maison existante

  async function plonger(actif, voler) {
    const carte = E.carte;
    const C = E.C;
    const existante = actif.startsWith("msp:") ? Number(actif.slice(4)) : null;
    const i = existante == null ? E.position.get(actif) : null;
    if (existante == null && i == null) return;
    E.focus = actif;
    E.orbite = 0;
    const [lon, lat] = existante == null ? [C.lon[i], C.lat[i]] : [E.M.lon[existante], E.M.lat[existante]];
    const maison = existante == null && E.bilan ? E.bilan.maisons.find((m) => m.code === actif) : null;
    for (const [c, marqueur] of E.marqueurs) {
      marqueur.bouton.classList.toggle("active", c === actif);
      marqueur.bouton.classList.toggle("discrete", c !== actif);
    }
    // Sans nouvelle maison ici : un anneau en pointillé montre le rayon.
    carte.getSource("fantome").setData(collection(maison ? [] : [cercle(lon, lat, E.seuil, false)]));
    if (maison && E.etape < E.bilan.maisons.length) appliquerEtape(E.bilan.maisons.length);
    // Une maison, nouvelle ou existante, se regarde en volumes : ses communes montent autour d'elle.
    const enVolume = Boolean(maison) || existante != null;
    nomsDuFond(!enVolume);
    if (voler !== false) {
      carte.flyTo({
        center: [lon, lat], zoom: zoomSite(lat, enVolume ? 0.64 : 0.42),
        // Inclinaison modérée et pas de rotation : les étiquettes restent près de leur commune.
        pitch: enVolume && !calme ? 45 : 0, bearing: enVolume && !calme ? -12 : 0, duration: calme ? 0 : 2000, essential: true,
      });
    }

    // Nouvelle maison : les communes qu'elle rapproche, en vert. Maison existante : celles qu'elle dessert, en violet.
    let rapprochees = new Map(maison ? maison.communes : []);
    if (existante != null) {
      carte.getSource("selection").setData(collection());
      carte.getSource("contours").setData(collection());
      rapprochees = new Map(await lire(`/donnees/desserte/${existante}/${E.seuil.toFixed(1)}`));
      if (E.focus !== actif) return;
    }
    const deps = new Set(existante == null ? [C.dep[i]] : []);
    for (const c of rapprochees.keys()) deps.add(c.slice(0, 2));
    const lots = await Promise.all([...deps].map(contoursDe));
    if (E.focus !== actif) return;
    const volumes = [];
    let contour = null;
    // Une maison existante peut desservir une grande ville : ses hauteurs sont ramenées à la plus haute.
    const plafond = Math.max(1, ...rapprochees.values());
    const hauteur = (hab) => (existante == null ? 250 + Math.sqrt(hab) * 42 : 250 + 5500 * Math.sqrt(hab / plafond));
    for (const lot of lots) {
      for (const f of lot.features) {
        if (f.id === actif) contour = { type: "Feature", properties: {}, geometry: f.geometry };
        const hab = rapprochees.get(f.id);
        if (hab) volumes.push({ type: "Feature", geometry: f.geometry, properties: { code: f.id, nom: C.nom[E.position.get(f.id)], hab, h: hauteur(hab), etiq: (existante == null ? "+" : "") + nf.format(hab) } });
      }
    }
    carte.getSource("selection").setData(collection(contour ? [contour] : []));
    carte.getSource("contours").setData(collection(volumes));
    E.couleurTours = existante == null ? E.theme.phare : E.theme.desserte;
    E.tour = null;
    carte.setPaintProperty("contours-3d", "fill-extrusion-color", E.couleurTours);
    carte.setLayoutProperty("contours-3d", "visibility", enVolume ? "visible" : "none");
    if (!enVolume) return;
    const t0 = performance.now();
    const attente = calme || voler === false ? 0 : 700;
    (function monter(t) {
      if (E.focus !== actif) return;
      const a = borner((t - t0 - attente) / (calme ? 1 : 1400), 0, 1);
      carte.setPaintProperty("contours-3d", "fill-extrusion-height", ["*", adoucir(a), ["get", "h"]]);
      if (a < 1) requestAnimationFrame(monter);
    })(t0);
  }

  function ensemble(force) {
    if (!E.pret || (!E.focus && !force)) return;
    E.focus = null;
    E.orbite = 0;
    nomsDuFond(true);
    cadrer();
    for (const [, marqueur] of E.marqueurs) marqueur.bouton.classList.remove("active", "discrete");
    E.carte.setLayoutProperty("contours-3d", "visibility", "none");
    for (const nom of ["contours", "selection", "fantome"]) E.carte.getSource(nom).setData(collection());
    E.carte.fitBounds(E.bornes, { padding: 30, duration: calme ? 0 : 1600, pitch: 0, bearing: 0, maxZoom: 10, essential: true });
  }

  // ------------------------------------------------------------------ survol, clic, calques

  function bulle(contenu, x, y) {
    const el = $("bulle");
    if (!contenu) return (el.style.display = "none");
    el.innerHTML = contenu;
    el.style.transform = `translate(${x + 14}px, ${y + 14}px)`;
    el.style.display = "flex";
  }

  /* Une tour et sa ligne dans le panneau s'allument ensemble : on voit quelle commune est laquelle. */
  function allumer(code) {
    if (!E.pret || (E.tour || null) === (code || null)) return;
    E.tour = code || null;
    E.carte.setPaintProperty(
      "contours-3d", "fill-extrusion-color",
      E.tour ? ["case", ["==", ["get", "code"], E.tour], "#ffffff", E.couleurTours] : E.couleurTours
    );
    for (const ligne of document.querySelectorAll(".communes li[data-code]")) ligne.classList.toggle("allumee", ligne.dataset.code === E.tour);
  }

  function tourSous(e) {
    if (!E.focus || E.carte.getLayoutProperty("contours-3d", "visibility") !== "visible") return null;
    const tour = E.carte.queryRenderedFeatures(e.point, { layers: ["contours-3d"] })[0];
    return tour ? tour.properties : null;
  }

  function finSurvol() {
    if (!E.pret) return;
    allumer(null);
    bulle(null);
    if (E.survole) E.carte.getSource("survol").setData(collection());
    E.survole = null;
    E.carte.getCanvas().style.cursor = "";
  }

  /* Ce qui se trouve sous la souris : une maison existante en priorité, sinon une commune. */
  function sous(e) {
    const carte = E.carte;
    if (carte.getLayoutProperty("msp", "visibility") !== "none" && carte.getZoom() >= 6.4) {
      const boite = [[e.point.x - 8, e.point.y - 8], [e.point.x + 8, e.point.y + 8]];
      const maison = carte.queryRenderedFeatures(boite, { layers: ["msp"] })[0];
      if (maison) return { maison: maison.properties.i };
    }
    if (carte.getZoom() < ZOOM_COMMUNE) return null;
    const commune = communeSous(e.lngLat.lng, e.lngLat.lat);
    return commune ? { commune } : null;
  }

  function survol(e) {
    if (!E.pret || E.pivot) return;
    const tour = tourSous(e);
    if (tour) {
      if (E.survole) E.carte.getSource("survol").setData(collection());
      E.survole = null;
      allumer(tour.code);
      E.carte.getCanvas().style.cursor = "pointer";
      const quoi = E.focus.startsWith("msp:") ? "habitants à portée de cette maison" : "habitants rapprochés par cette maison";
      return bulle(`<b>${tour.nom}</b><span>${tour.etiq} ${quoi}</span>`, e.point.x, e.point.y);
    }
    allumer(null);
    const cible = sous(e);
    if (!cible) return finSurvol();
    E.carte.getCanvas().style.cursor = "pointer";
    if (cible.maison != null) {
      if (E.survole) E.carte.getSource("survol").setData(collection());
      E.survole = null;
      return bulle(`<b>${E.M.nom[cible.maison]}</b><span>Maison de santé existante · cliquer pour la fiche</span>`, e.point.x, e.point.y);
    }
    const code = cible.commune.code;
    if (E.survole !== code) {
      E.survole = code;
      E.carte.getSource("survol").setData(collection([{ type: "Feature", properties: {}, geometry: cible.commune.geometry }]));
    }
    const i = E.position.get(code);
    bulle(`<b>${E.C.nom[i]} (${E.C.dep[i]})</b><span>${nf.format(E.C.pop[i])} habitants · cliquer pour la fiche</span>`, e.point.x, e.point.y);
  }

  function clicCarte(e) {
    if (performance.now() - E.glisse < 400) return;
    const cible = sous(e);
    if (cible && cible.maison != null) return dc().set_props("commune-active", { data: `msp:${cible.maison}` });
    if (cible) return dc().set_props("commune-active", { data: cible.commune.code });
    if (E.focus) dc().set_props("commune-active", { data: null }); // clic dans le vide : la fiche se ferme
  }

  function calques(valeurs) {
    quandPret(() => E.carte.setLayoutProperty("msp", "visibility", (valeurs || []).includes("msp") ? "visible" : "none"));
  }

  /* En plongée, seuls restent les noms des communes concernées : ceux du fond de carte s'effacent. */
  function nomsDuFond(visibles) {
    if (E.sansNoms === !visibles) return;
    E.sansNoms = !visibles;
    for (const id of E.etiquettes) E.carte.setLayoutProperty(id, "visibility", visibles ? "visible" : "none");
  }

  // ------------------------------------------------------------------ fonctions appelées par Dash

  window.dash_clientside = window.dash_clientside || {};
  window.dash_clientside.phares = {
    contexte,
    calques,
    bilan: surBilan,
    replier: function () {
      const source = dc().callback_context.triggered[0].prop_id.split(".")[0];
      racine.classList.toggle("panneau-replie", source === "btn-replier");
      quandPret(cadrer);
    },
    vue: function (actif, _maisons, _proposer, _retour, proposition, classe) {
      const sources = dc().callback_context.triggered.map((t) => t.prop_id.split(".")[0]);
      const onglet = (classe || "").includes("onglet-proposer") ? "onglet-proposer" : "onglet-maisons";
      const fermer = () => actif && dc().set_props("commune-active", { data: null });
      if (sources.includes("btn-retour")) {
        fermer();
        return dc().no_update;
      }
      if (sources.includes("onglet-maisons") || sources.includes("onglet-proposer")) {
        fermer();
        return `appli ${sources.includes("onglet-proposer") ? "onglet-proposer" : "onglet-maisons"}`;
      }
      if (sources.includes("proposition") && proposition) {
        fermer();
        return "appli onglet-maisons"; // une proposition vient d'arriver : on montre la liste
      }
      if (actif) {
        racine.classList.remove("panneau-replie");
        quandPret(() => plonger(actif, true));
        return `appli ${onglet} fiche-ouverte`;
      }
      quandPret(() => ensemble(false));
      return `appli ${onglet}`;
    },
    ouvrir: function (_clics, recherche) {
      const declencheur = dc().callback_context.triggered[0];
      if (!declencheur || !declencheur.value) return dc().no_update;
      if (declencheur.prop_id.startsWith("recherche")) return recherche;
      return JSON.parse(declencheur.prop_id.slice(0, declencheur.prop_id.lastIndexOf("."))).code;
    },
    chat: function () {
      const source = dc().callback_context.triggered[0].prop_id.split(".")[0];
      if (source === "chat-aide-btn") {
        racine.classList.toggle("chat-aide-visible");
        return;
      }
      const ouvert = source === "btn-chat" && !racine.classList.contains("chat-ouvert");
      racine.classList.toggle("chat-ouvert", ouvert);
      quandPret(cadrer);
      if (ouvert) setTimeout(() => document.getElementById("chat-texte")?.focus(), 80);
    },
    // À l'envoi, sans attendre le serveur : le champ se vide, la demande et une attente s'affichent.
    chatEnvoyer: function (_clic, _entree, texte) {
      const message = (texte || "").trim();
      if (!message || racine.classList.contains("chat-attend")) return [dc().no_update, dc().no_update];
      document.getElementById("chat-echo").textContent = message;
      racine.classList.add("chat-attend");
      racine.classList.remove("chat-aide-visible");
      clearTimeout(E.chatAttente);
      E.chatAttente = setTimeout(() => racine.classList.remove("chat-attend"), 45000);
      return ["", { texte: message, jeton: Date.now() }];
    },
    chatExemple: function (_clics, textes) {
      const clic = dc().callback_context.triggered[0];
      if (!clic || !clic.value) return dc().no_update;
      racine.classList.remove("chat-aide-visible");
      setTimeout(() => document.getElementById("chat-texte")?.focus(), 30);
      return textes[JSON.parse(clic.prop_id.split(".")[0]).i];
    },
    chatDefiler: function () {
      const fil = document.getElementById("chat-fil");
      clearTimeout(E.chatAttente);
      racine.classList.remove("chat-aide-visible", "chat-attend");
      if (fil) requestAnimationFrame(() => (fil.scrollTop = fil.scrollHeight));
    },
    tiroir: function (_ouvrir, _fermer, classe) {
      const source = dc().callback_context.triggered[0].prop_id.split(".")[0];
      return source === "btn-methode" && !(classe || "").includes("ouvert") ? "tiroir ouvert" : "tiroir";
    },
    compterCriteres: function (regles, popMin, exclure, espacement, interdites, priorite) {
      const n =
        (regles || []).length + (popMin > 0) + ((exclure || []).length > 0) + (espacement > 0) +
        ((interdites || []).length > 0) + (priorite !== "tous");
      return n ? String(n) : "";
    },
  };

  (function attendre() {
    const el = document.getElementById("carte");
    // Une minuterie, pas une image d'animation : la page démarre aussi dans un onglet en arrière-plan.
    if (!el || !window.maplibregl || !document.getElementById("perimetre-zone")) return setTimeout(attendre, 50);
    demarrer(el);
  })();
})();
