const etatsCommunes = new WeakMap();

window.atlasCarte = {
  style(feature, context) {
    const d = context.hideout.communes?.[String(feature.id)];
    etatsCommunes.set(feature, d);
    return {
      weight: d?.focus ? 3 : d?.g ? 1.8 : 0.85,
      color: d?.focus ? "#124962" : d?.g ? "#8e582e" : "#758a85",
      fillColor: d?.c || "#d5e3df",
      fillOpacity: d?.g ? 0.97 : 0.84,
      dashArray: d?.s === "Hors seuil" ? "2 1" : null,
    };
  },
  infobulle(feature, layer) {
    layer.on("add", () => {
      layer.getElement()?.setAttribute("data-code-insee", String(feature.id));
    });
    layer.bindTooltip(() => {
      const d = etatsCommunes.get(feature);
      const label = document.createElement("span");
      label.textContent = d
        ? d.n + " · " + d.s + " · " + d.d.toLocaleString("fr-FR") + " km"
        : feature.properties.commune || feature.properties.nom || feature.id;
      return label;
    });
  },
};

