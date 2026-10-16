// FloodCheck dashboard: map, inputs, model runs and results.
// Talks to the API in backend/server.py.
(() => {
  const KENYA = [33.8, -4.8, 42.0, 5.1];
  let MAX_SIDE = 0.40;
  let REGIONS = [], LIVE_TERRAIN = true;          // prepared terrain regions from the server
  const PREP_BUFFER = 0.03;                       // must match prepared_buffer_deg in floodmodel.py
  const EXAMPLE_BOX = [39.52, -0.60, 39.80, -0.32];   // demo area
  const TERRAIN_TILES = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png";
  const gibsTiles = (date) => [`https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/MODIS_Terra_CorrectedReflectance_TrueColor/default/${date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`];
  const GIBS_ATTR = "Imagery: NASA GIBS / MODIS Terra";

  // ---------- layer configuration (one entry per result layer, like a layers.json) ----------
  const DEPTH_COLOR = ["interpolate", ["linear"], ["get", "depth"], 0.1, "#F2C14E", 1, "#E07A1F", 2, "#B3261E"];
  const LAYERS = [
    { id: "flood", label: "Flood water", swatch: "linear-gradient(90deg,#BDE4F4,#08306B)", kind: "image" },
    { id: "buildings", label: "Homes and buildings in water", swatch: "#E07A1F", kind: "fill",
      paint: { "fill-color": DEPTH_COLOR, "fill-opacity": 0.95, "fill-outline-color": "#5A2A0A" },
      filter: ["==", "$type", "Polygon"], minzoom: 14,
      extra: { suffix: "pts", type: "circle", filter: ["==", "$type", "Point"], maxzoom: 14,
               paint: { "circle-color": DEPTH_COLOR, "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 1.5, 14, 4],
                        "circle-stroke-color": "#5A2A0A", "circle-stroke-width": 0.5 } },
      count: (e) => e.buildings_flooded ? `${fmt(e.buildings_flooded.any, 0)}` : "–",
      popup: (p) => `<strong>Building</strong><br>About ${fmt(p.depth, 2)} m of water` },
    { id: "roads", label: "Roads under water", swatch: "#C4307A", swatchClass: "sw-line", kind: "line",
      paint: { "line-color": "#C4307A", "line-width": ["interpolate", ["linear"], ["zoom"], 9, 2, 15, 6] },
      count: (e) => e.roads_flooded_km != null ? `${fmt(e.roads_flooded_km)} km` : "–",
      popup: (p) => `<strong>${esc(p.name) || "Road"}</strong> (${esc(p.class)})<br>Up to ${fmt(p.max_depth, 2)} m of water` },
    { id: "facilities", label: "Schools and health", swatch: "#1E86B8", swatchClass: "sw-dot", kind: "circle",
      paint: { "circle-radius": ["case", ["get", "flooded"], 8, 6],
               "circle-color": ["match", ["get", "kind"], "health", "#3E9A44", "#1E86B8"],
               "circle-stroke-color": ["case", ["get", "flooded"], "#B3261E", "#FFFFFF"],
               "circle-stroke-width": ["case", ["get", "flooded"], 3, 1.5] },
      count: (e) => e.facilities_flooded ? `${e.facilities_flooded.length} in water` : "–",
      popup: (p) => `<strong>${esc(p.name)}</strong><br>${p.kind === "health" ? "Health facility" : "School"} · ` +
                    (p.flooded ? `about ${fmt(p.depth, 2)} m of water` : "not flooded") + `<br><span style="color:#4A5578">${esc(p.source)}</span>` },
  ];

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const phoneQuery = matchMedia("(max-width: 760px)");
  const isPhone = () => phoneQuery.matches;
  const sheet = { name: "half" };
  const state = { bbox: null, mode: null, firstCorner: null, inflows: [], runId: null, polling: null,
                  result: null, layerOn: {}, profilePts: [], terrain: false, compare: false };

  // ---------- street map providers ----------
  // Street maps that work without an account. Not used: tile.openstreetmap.org (blocks apps, 403
  // "Access blocked") and CARTO (now draws "API KEY REQUIRED" over every tile without a key).
  // If one provider fails, the next is used automatically. A server can put its own provider
  // first with FLOOD_STREET_TILES (see README).
  const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
  const STREET_MAPS = [
    { id: "esri-streets", tiles: [`${ESRI}/World_Street_Map/MapServer/tile/{z}/{y}/{x}`],
      maxzoom: 19, attribution: "Map © Esri, HERE, Garmin, © OpenStreetMap contributors" },
    { id: "esri-topo", tiles: [`${ESRI}/World_Topo_Map/MapServer/tile/{z}/{y}/{x}`],
      maxzoom: 19, attribution: "Map © Esri, HERE, Garmin, USGS, © OpenStreetMap contributors" },
  ];
  let streetIdx = 0, streetErrors = 0;
  const streetSource = (s) => ({ type: "raster", tiles: s.tiles, tileSize: 256, maxzoom: s.maxzoom, attribution: s.attribution });

  // ---------- map ----------
  const baseStyle = (extra = {}) => ({
    version: 8,
    sources: {
      streets: streetSource(STREET_MAPS[0]),
      sat: { type: "raster", tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
             tileSize: 256, maxzoom: 18, attribution: "Imagery © Esri, Maxar, Earthstar Geographics" },
      ...extra,
    },
    layers: [
      { id: "streets", type: "raster", source: "streets" },
      { id: "sat", type: "raster", source: "sat", layout: { visibility: "none" } },
    ],
  });
  const map = new maplibregl.Map({ container: "map", center: [38.2, 0.2], zoom: 5.6, maxBounds: [[30, -9], [46, 9]],
    maxPitch: 75, attributionControl: false, style: baseStyle({ dem: { type: "raster-dem", tiles: [TERRAIN_TILES], encoding: "terrarium", tileSize: 256, maxzoom: 14 } }) });
  map.addControl(new maplibregl.NavigationControl({ showCompass: true, visualizePitch: true }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");
  map.addControl(new maplibregl.AttributionControl({ compact: matchMedia("(max-width: 760px)").matches }), "bottom-right");
  map.once("load", () => setTimeout(() => {          // start the phone credit line collapsed (tap (i) to read it)
    document.querySelectorAll(".maplibregl-ctrl-attrib.maplibregl-compact").forEach(el => {
      el.classList.remove("maplibregl-compact-show"); el.removeAttribute("open");
    });
  }, 0));

  const emptyFC = { type: "FeatureCollection", features: [] };
  const whenReady = (fn) => (map.getSource("box") ? fn() : map.once("load", fn));
  map.on("load", () => {
    map.addSource("box", { type: "geojson", data: emptyFC });
    map.addLayer({ id: "box-fill", type: "fill", source: "box", paint: { "fill-color": "#C4307A", "fill-opacity": 0.06 } });
    map.addLayer({ id: "box-line", type: "line", source: "box", paint: { "line-color": "#C4307A", "line-width": 3, "line-dasharray": [2, 1.5] } });
    map.addSource("profile", { type: "geojson", data: emptyFC });
    map.addLayer({ id: "profile-line", type: "line", source: "profile", paint: { "line-color": "#0B1650", "line-width": 3, "line-dasharray": [1, 1] } });
    map.addLayer({ id: "profile-pts", type: "circle", source: "profile", filter: ["==", "$type", "Point"],
                   paint: { "circle-radius": 5, "circle-color": "#fff", "circle-stroke-color": "#0B1650", "circle-stroke-width": 2 } });
    fetch("api/config").then(r => r.ok ? r.json() : null).then(c => {
      if (!c) return;
      MAX_SIDE = c.max_side_deg;
      $("max-km").textContent = fmt(MAX_SIDE * 111, 0);
      REGIONS = c.prepared_regions || []; LIVE_TERRAIN = c.live_terrain !== false;
      if (REGIONS.length) {
        map.addSource("regions", { type: "geojson", data: { type: "FeatureCollection",
          features: REGIONS.map(r => ({ ...boxFeature(r.bbox).features[0], properties: { name: r.name } })) } });
        map.addLayer({ id: "regions-line", type: "line", source: "regions",
          paint: { "line-color": "#4BB8C2", "line-width": 2, "line-dasharray": [3, 2], "line-opacity": 0.9 } }, "box-fill");
        if (!LIVE_TERRAIN && !state.bbox) {
          const u = REGIONS.reduce((a, r) => [Math.min(a[0], r.bbox[0]), Math.min(a[1], r.bbox[1]), Math.max(a[2], r.bbox[2]), Math.max(a[3], r.bbox[3])], [180, 90, -180, -90]);
          fitBox(u);
        }
        $("regions-note").hidden = false;
      }
      if (state.bbox) setBox(state.bbox);
      if (c.street_map && c.street_map.tiles && c.street_map.tiles.length) {   // server-chosen provider goes first
        STREET_MAPS.unshift({ id: "server", maxzoom: 19, ...c.street_map });
        useStreetMap(0);
      }
    }).catch(() => {});
  });

  function useStreetMap(i) {
    streetIdx = i; streetErrors = 0;
    const visible = map.getLayer("streets") ? map.getLayoutProperty("streets", "visibility") : "visible";
    if (map.getLayer("streets")) map.removeLayer("streets");
    if (map.getSource("streets")) map.removeSource("streets");
    map.addSource("streets", streetSource(STREET_MAPS[i]));
    map.addLayer({ id: "streets", type: "raster", source: "streets", layout: { visibility: visible || "visible" } }, "sat");
  }
  // switch to the next provider if this one keeps failing (blocked, down or over its limit)
  map.on("error", (e) => {
    if (e.sourceId !== "streets") return;
    if (++streetErrors >= 4 && streetIdx < STREET_MAPS.length - 1) {
      console.warn(`Street map "${STREET_MAPS[streetIdx].id}" is failing; switching to "${STREET_MAPS[streetIdx + 1].id}".`);
      useStreetMap(streetIdx + 1);
    }
  });

  function setBasemap(sat) {
    map.setLayoutProperty("streets", "visibility", sat ? "none" : "visible");
    map.setLayoutProperty("sat", "visibility", sat ? "visible" : "none");
    $("bm-map").setAttribute("aria-pressed", String(!sat));
    $("bm-sat").setAttribute("aria-pressed", String(sat));
  }
  $("bm-map").onclick = () => setBasemap(false);
  $("bm-sat").onclick = () => setBasemap(true);

  // ---------- 3D terrain ----------
  function setTerrain(on) {
    state.terrain = on;
    $("terrain-btn").setAttribute("aria-pressed", String(on));
    const t = on ? { source: "dem", exaggeration: 2 } : null;
    map.setTerrain(t);
    if (after && afterReady) after.setTerrain(t);
    map.easeTo(on ? { pitch: 60, duration: reduced() ? 0 : 900 } : { pitch: 0, bearing: 0, duration: reduced() ? 0 : 600 });
  }
  $("terrain-btn").onclick = () => setTerrain(!state.terrain);
  const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

  // ---------- helpers ----------
  function sideKm(b) {
    const midLat = (b[1] + b[3]) / 2;
    return [(b[2] - b[0]) * 111.32 * Math.cos(midLat * Math.PI / 180), (b[3] - b[1]) * 110.57];
  }
  function boxFeature(b) {
    return { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Polygon",
      coordinates: [[[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]], [b[0], b[1]]]] } }] };
  }
  const normBox = (a, b) => [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
  function boxProblem(b) {
    if (b[2] - b[0] > MAX_SIDE || b[3] - b[1] > MAX_SIDE) return `Too big: keep each side under about ${fmt(MAX_SIDE * 111, 0)} km.`;
    if (b[0] < KENYA[0] || b[2] > KENYA[2] || b[1] < KENYA[1] || b[3] > KENYA[3]) return "Outside the area the model covers: draw it inside the country.";
    if (!LIVE_TERRAIN && REGIONS.length && !inPreparedRegion(b)) return "Outside the prepared areas: draw your area inside one of the teal dashed outlines.";
    return null;
  }
  const inPreparedRegion = (b) => REGIONS.some(r => b[0] - PREP_BUFFER >= r.bbox[0] && b[1] - PREP_BUFFER >= r.bbox[1] &&
                                                   b[2] + PREP_BUFFER <= r.bbox[2] && b[3] + PREP_BUFFER <= r.bbox[3]);
  const inBox = (lng, lat, b) => b && lng >= b[0] && lng <= b[2] && lat >= b[1] && lat <= b[3];
  const fmt = (n, d = 1) => Number(n).toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: 0 });

  function setHint(text, action) {
    const h = $("map-hint"); h.textContent = text || ""; h.hidden = !text;
    if (text && action) {
      const b = document.createElement("button"); b.type = "button"; b.className = "hint-action"; b.textContent = action.label;
      b.onclick = action.run; h.appendChild(b);
    }
  }
  function setMode(mode) {
    if (state.mode === "profile" && mode !== "profile") setTimeout(() => map.doubleClickZoom.enable(), 400);
    state.mode = mode; state.firstCorner = null;
    $("draw-btn").setAttribute("aria-pressed", String(mode === "draw"));
    $("inflow-btn").setAttribute("aria-pressed", String(mode === "inflow"));
    $("profile-btn").setAttribute("aria-pressed", String(mode === "profile"));
    map.getCanvas().style.cursor = mode ? "crosshair" : "";
    setHint(mode === "draw" ? "Click the first corner of your area" :
            mode === "inflow" ? "Click the point where the river enters your area" :
            mode === "profile" ? "Tap points along your line, then press Finish." : "");
    if (mode === "profile") { map.doubleClickZoom.disable(); state.profilePts = []; drawProfileLine(); }
    if (mode) sheetTo("peek");                       // on phones, get the panel out of the way of the map
  }
  function finishProfile() {
    const pts = state.profilePts.slice(); setMode(null);
    state.profilePts = pts; drawProfileLine();
    if (pts.length >= 2) runProfile(pts);
    sheetTo("half");
  }

  function setBox(b, placeName) {
    const changed = JSON.stringify(b) !== JSON.stringify(state.bbox);
    state.bbox = b;
    if (changed || placeName) state.placeName = placeName || null;
    whenReady(() => map.getSource("box").setData(b ? boxFeature(b) : emptyFC));
    const r = $("area-readout");
    renderAreaBanner();
    if (!b) {
      r.innerHTML = `<div class="coord-head"><span>Your area (WGS84)</span></div><p class="coord-empty">No area marked yet.</p>`;
      r.classList.remove("is-bad", "is-set");
      $("next-rain").hidden = true;
    } else {
      const [wk, hk] = sideKm(b); const prob = boxProblem(b);
      const c = (v) => v.toFixed(5) + "°";
      r.innerHTML = `<div class="coord-head"><span>Your area (WGS84)</span><button type="button" class="btn-link" id="area-clear">Clear</button></div>
        ${placeName ? `<p class="coord-place">Around ${esc(placeName)}</p>` : ""}
        <p class="coord-val"><span class="coord-sep">NW</span>${c(b[3])}, ${c(b[0])}</p>
        <p class="coord-val"><span class="coord-sep">SE</span>${c(b[1])}, ${c(b[2])}</p>
        <p class="coord-size"><strong>${fmt(wk, 0)} × ${fmt(hk, 0)} km</strong> · latitude, longitude of the corners</p>
        ${prob ? `<p class="coord-prob">${esc(prob)}</p>` : ""}`;
      r.classList.toggle("is-bad", !!prob); r.classList.add("is-set");
      $("area-clear").onclick = () => { setMode(null); if (placeMarker) { placeMarker.remove(); placeMarker = null; } $("place-results").innerHTML = ""; $("place-q").value = ""; setBox(null); };
      $("step-area").classList.toggle("is-done", !prob);
      $("next-rain").hidden = !!prob;
    }
    if (changed) onAreaChanged();
    updateRunButton();
  }

  const areaName = () => state.placeName || (state.bbox ? "the area you marked" : "");
  function renderAreaBanner() {
    const el = $("rain-area");
    if (!state.bbox) { el.hidden = true; return; }
    const [wk, hk] = sideKm(state.bbox);
    el.hidden = false;
    el.innerHTML = `<span>Area: <strong>${esc(state.placeName || "Area you marked")}</strong> · ${fmt(wk, 0)} × ${fmt(hk, 0)} km</span>
      <button type="button" class="btn-link" data-goto="area">Change</button>`;
    el.querySelector("button").onclick = () => showTab("area");
  }
  // a new area means the old results, report and rainfall no longer apply
  function onAreaChanged() {
    if (state.result) clearResults("Your area changed. Press <strong>Check for floods</strong> to see results for the new area.");
    if (state.weather) {
      state.weather = null;
      if (state.bbox && !boxProblem(state.bbox)) {                 // fetch the rainfall again for the new area
        $("wx-result").innerHTML = `<p class="hint">Getting rainfall for the new area…</p>`;
        setTimeout(() => $("wx-btn").click(), 0);
      } else {
        $("wx-result").innerHTML = `<p class="hint">The area changed. Press <strong>Get rainfall</strong> again for the new area.</p>`;
      }
    }
  }
  function clearResults(message) {
    if (state.polling) return;                                     // a check is running: let it finish
    state.result = null;
    whenReady(() => removeResultLayers(map));
    if (typeof after !== "undefined" && after) removeResultLayers(after);
    $("results").hidden = true; $("status").hidden = true;
    $("legend").hidden = true; $("layers-card").hidden = true;
    $("results-badge").hidden = true;
    $("results-empty").hidden = false; $("results-empty-text").innerHTML = message;
    $("report-ready").hidden = true; $("report-empty").hidden = false; $("report-empty-text").innerHTML = message;
    $("tab-results").classList.remove("is-done");
  }

  function updateRunButton() {
    const btn = $("run-btn"), note = $("run-note");
    const running = !!state.polling;
    let msg = "";
    const realMode = document.querySelector('input[name="rainsrc"]:checked').value === "real";
    if (!state.bbox) msg = "Mark your area to start.";
    else if (boxProblem(state.bbox)) msg = boxProblem(state.bbox);
    else if (state.inflows.some(f => !(f.flood > 0))) msg = "Enter the flood flow for each river inflow, or remove it.";
    else if (state.inflows.some(f => !inBox(f.lng, f.lat, state.bbox))) msg = "A river inflow point is outside your area. Move it inside.";
    else if (realMode && !state.weather) msg = "Press “Get rainfall” first, or switch to “Choose myself”.";
    else if (!(+$("rain-mm").value > 0) || !(+$("rain-hours").value > 0)) msg = "Enter the rain amount and how long it falls.";
    btn.disabled = !!msg || running;
    $("tab-area").classList.toggle("is-done", !!state.bbox && !boxProblem(state.bbox));
    $("tab-rain").classList.toggle("is-done", realMode ? !!state.weather : (+$("rain-mm").value > 0 && +$("rain-hours").value > 0));
    note.textContent = running ? "Checking… this can take a few minutes." : (msg || "Ready. A check takes from a few seconds to a few minutes, depending on the area.");
  }

  // ---------- area drawing ----------
  $("draw-btn").onclick = () => setMode(state.mode === "draw" ? null : "draw");
  $("example-btn").onclick = () => {
    setMode(null);
    let ex = EXAMPLE_BOX;
    if (REGIONS.length && !inPreparedRegion(ex)) {         // use the middle of a prepared region instead
      const r = REGIONS[0].bbox, cx = (r[0] + r[2]) / 2, cy = (r[1] + r[3]) / 2, h = Math.min(0.05, MAX_SIDE / 2);
      ex = [cx - h, cy - h, cx + h, cy + h];
    }
    setBox(ex, "Example area");
    fitBox(state.bbox);
  };

  // ---------- place search ----------
  let placeMarker = null, searchSeq = 0;
  function areaAround(r) {
    // use the place's own outline when it fits, otherwise a square around it (~17 km a side)
    const side = 0.15, half = (v) => v / 2;
    if (r.bbox) {
      const w = r.bbox[2] - r.bbox[0], h = r.bbox[3] - r.bbox[1];
      if (w >= 0.05 && h >= 0.05 && w <= MAX_SIDE * 0.95 && h <= MAX_SIDE * 0.95) return r.bbox.slice();
      const s = Math.min(Math.max(w, h, side), MAX_SIDE * 0.95);
      return [r.lon - half(s), r.lat - half(s), r.lon + half(s), r.lat + half(s)];
    }
    return [r.lon - half(side), r.lat - half(side), r.lon + half(side), r.lat + half(side)];
  }
  function choosePlace(r, others) {
    setMode(null);
    $("place-q").value = r.name;
    if (placeMarker) placeMarker.remove();
    const el = document.createElement("div"); el.className = "place-pin"; el.setAttribute("aria-label", r.name);
    placeMarker = new maplibregl.Marker({ element: el, anchor: "bottom" }).setLngLat([r.lon, r.lat])
      .setPopup(new maplibregl.Popup({ offset: 18 }).setHTML(`<strong>${esc(r.name)}</strong>`)).addTo(map);
    const b = areaAround(r);
    setBox(b, r.name);                         // a new area also clears the old flood results
    fitBox(b);                                 // fly there straight away
    renderAlternatives(r, others || []);
    if (!boxProblem(b)) showTab("rain");       // next step
  }
  // other matches stay in the Area tab, in case the first one was the wrong place
  function renderAlternatives(chosen, all) {
    const list = $("place-results");
    const rest = all.filter(p => p !== chosen);
    if (!rest.length) { list.innerHTML = ""; return; }
    list.innerHTML = `<li class="pr-msg">Showing <strong>${esc(chosen.name)}</strong>. Not the right place? Choose another:</li>`
      + rest.map((p, i) => `<li><button type="button" data-i="${i}"><span class="pr-name">${esc(p.name)}</span><span class="pr-label">${esc(p.label)}</span></button></li>`).join("")
      + `<li class="pr-attr">Search © OpenStreetMap contributors (Nominatim)</li>`;
    list.querySelectorAll("button").forEach(btn => btn.onclick = () => choosePlace(rest[+btn.dataset.i], all));
  }
  async function runSearch() {
    const q = $("place-q").value.trim(), list = $("place-results");
    if (q.length < 2) { list.innerHTML = `<li class="pr-msg">Type at least two letters.</li>`; return; }
    const seq = ++searchSeq;
    list.innerHTML = `<li class="pr-msg">Searching…</li>`;           // old matches go straight away
    if (placeMarker) { placeMarker.remove(); placeMarker = null; }
    try {
      const r = await fetch(`api/search?${new URLSearchParams({ q })}`); const j = await r.json();
      if (seq !== searchSeq) return;
      if (!r.ok) throw new Error(j.detail || "The search failed. Try again.");
      const res = j.results || [];
      if (!res.length) { list.innerHTML = `<li class="pr-msg">No places found for “${esc(q)}”. Check the spelling, try a nearby town, or draw the area on the map.</li>`; return; }
      choosePlace(res[0], res);                // best match first; the others are listed as alternatives
    } catch (err) {
      if (seq !== searchSeq) return;
      list.innerHTML = `<li class="pr-msg is-error-text">${esc(err.message.includes("fetch") ? "Can't reach the FloodCheck server." : err.message)}</li>`;
    }
  }
  $("place-btn").onclick = runSearch;
  $("place-q").addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); runSearch(); } });

  map.on("click", (e) => {
    const p = [e.lngLat.lng, e.lngLat.lat];
    if (state.mode === "draw") {
      if (!state.firstCorner) { state.firstCorner = p; setHint("Now click the opposite corner"); }
      else { const b = normBox(state.firstCorner, p); setMode(null); setBox(b); sheetTo("half"); }
      return;
    }
    if (state.mode === "inflow") { addInflow(p[0], p[1]); setMode(null); sheetTo("half"); return; }
    if (state.mode === "profile") {
      state.profilePts.push(p); drawProfileLine();
      if (state.profilePts.length >= 2) setHint(`${state.profilePts.length} points. Tap more, or finish the line.`, { label: "Finish", run: finishProfile });
      return;
    }
    if (!state.result) return;
    const hitLayers = resultLayerIds();
    const hits = hitLayers.length ? map.queryRenderedFeatures(e.point, { layers: hitLayers }) : [];
    if (hits.length) {
      const cfg = LAYERS.find(l => hits[0].layer.id.startsWith(`res-${l.id}`));
      showPopup(e.lngLat, cfg.popup(hits[0].properties));
    } else queryDepth(e.lngLat);
  });
  map.on("dblclick", () => {
    if (state.mode !== "profile") return;
    // the double-click also fired two clicks: drop the duplicate point
    if (state.profilePts.length > 2) state.profilePts.pop();
    finishProfile();
  });
  map.on("mousemove", (e) => {
    if (state.mode === "draw" && state.firstCorner)
      map.getSource("box").setData(boxFeature(normBox(state.firstCorner, [e.lngLat.lng, e.lngLat.lat])));
    else if (!state.mode && state.result) {
      const ids = resultLayerIds();
      const over = ids.length && map.queryRenderedFeatures(e.point, { layers: ids }).length;
      map.getCanvas().style.cursor = over ? "pointer" : "";
    }
  });
  function resultLayerIds() {
    return LAYERS.filter(l => l.popup).flatMap(l => [`res-${l.id}`, `res-${l.id}-pts`]).filter(id => map.getLayer(id));
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && state.mode) { setMode(null); setBox(state.bbox); }
  });

  // ---------- inflows ----------
  let inflowSeq = 0;
  function addInflow(lng, lat) {
    const f = { id: ++inflowSeq, lng, lat, flood: "", normal: "" };
    const el = document.createElement("div");
    el.style.cssText = "width:18px;height:18px;border-radius:50%;background:#08519C;border:3px solid #fff;box-shadow:0 0 0 1.5px #0B1650;cursor:grab";
    el.setAttribute("aria-label", `River inflow ${f.id}`);
    f.marker = new maplibregl.Marker({ element: el, draggable: true }).setLngLat([lng, lat]).addTo(map);
    f.marker.on("dragend", () => { const ll = f.marker.getLngLat(); f.lng = ll.lng; f.lat = ll.lat; renderInflows(); });
    state.inflows.push(f); renderInflows();
  }
  function renderInflows() {
    const list = $("inflow-list"); list.innerHTML = "";
    state.inflows.forEach((f, idx) => {
      const div = document.createElement("div"); div.className = "inflow";
      div.innerHTML = `
        <div class="inflow-top"><strong>River inflow ${idx + 1}</strong>
          <button class="btn-link" data-remove="${f.id}">Remove</button></div>
        <div class="inflow-grid">
          <div class="field"><label for="fl-${f.id}">Flood flow</label>
            <div class="unit-input"><input type="number" min="1" id="fl-${f.id}" value="${f.flood}" placeholder="e.g. 1500"><span>m³/s</span></div></div>
          <div class="field"><label for="nm-${f.id}">Normal flow</label>
            <div class="unit-input"><input type="number" min="0" id="nm-${f.id}" value="${f.normal}" placeholder="e.g. 150"><span>m³/s</span></div></div>
        </div>
        <p class="hint" style="margin:8px 0 0">${f.lat.toFixed(4)}, ${f.lng.toFixed(4)}. Drag the blue dot to move it.</p>
        <button class="btn-link" data-flow="${f.id}" style="margin-top:6px">Get real flow for the storm dates</button>
        <p class="hint flow-note" style="margin:6px 0 0">${esc(f.flowNote || "")}</p>`;
      list.appendChild(div);
      div.querySelector(`#fl-${f.id}`).oninput = (e) => { f.flood = e.target.value === "" ? "" : +e.target.value; updateRunButton(); };
      div.querySelector(`#nm-${f.id}`).oninput = (e) => { f.normal = e.target.value === "" ? "" : +e.target.value; };
      div.querySelector("[data-remove]").onclick = () => { f.marker.remove(); state.inflows = state.inflows.filter(x => x !== f); renderInflows(); };
      div.querySelector("[data-flow]").onclick = () => getRiverFlow(f);
    });
    $("step-inflow").classList.toggle("is-done", state.inflows.length > 0 && state.inflows.every(f => f.flood > 0));
    updateRunButton();
  }
  $("inflow-btn").onclick = () => setMode(state.mode === "inflow" ? null : "inflow");

  // ---------- storm inputs ----------
  $("rain-range").oninput = (e) => { $("rain-mm").value = e.target.value; updateRunButton(); };
  $("rain-mm").oninput = (e) => { $("rain-range").value = Math.min(400, +e.target.value || 10); updateRunButton(); };
  $("rain-hours").oninput = updateRunButton;

  // ---------- real weather for a date ----------
  const todayISO = () => new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  const addDays = (iso, n) => { const d = new Date(iso + "T12:00:00Z"); d.setUTCDate(d.getUTCDate() + n); return d.toISOString().slice(0, 10); };
  const niceDate = (iso) => new Date(iso + "T12:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  $("wx-date").value = todayISO();
  $("wx-date").max = addDays(todayISO(), 15);
  function syncRainSource() {
    const real = document.querySelector('input[name="rainsrc"]:checked').value === "real";
    $("real-weather").hidden = !real; $("own-presets").hidden = real;
    updateRunButton();
  }
  document.querySelectorAll('input[name="rainsrc"]').forEach(r => r.onchange = syncRainSource);
  const presetBtns = document.querySelectorAll(".presets button");
  presetBtns.forEach(b => b.onclick = () => {
    $("rain-mm").value = b.dataset.mm; $("rain-range").value = b.dataset.mm; $("rain-hours").value = b.dataset.h;
    presetBtns.forEach(x => x.classList.toggle("is-on", x === b)); updateRunButton();
  });
  ["rain-mm", "rain-range", "rain-hours"].forEach(id => $(id).addEventListener("input", () => presetBtns.forEach(x => x.classList.remove("is-on"))));
  state.weather = null;
  syncRainSource();
  $("wx-btn").onclick = async () => {
    const out = $("wx-result");
    if (!state.bbox) { out.innerHTML = `<p class="hint is-error-text">Mark your area first (step 1), so the rainfall is for the right place.</p>`; return; }
    out.innerHTML = `<p class="hint">Getting rainfall…</p>`;
    try {
      const q = new URLSearchParams({ bbox: state.bbox.join(","), date: $("wx-date").value, days: $("wx-days").value });
      const r = await fetch(`api/weather?${q}`); const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "The rainfall request failed.");
      state.weather = j;
      $("rain-mm").value = Math.max(1, Math.round(j.rain_mm)); $("rain-range").value = Math.min(400, Math.max(10, j.rain_mm));
      $("rain-hours").value = j.rain_hours;
      document.getElementById(`wet-${j.wetness}`).checked = true;
      out.innerHTML = weatherChart(j) + `<p class="hint" style="margin:6px 0 0">
        <strong style="color:#0B1650">${fmt(j.rain_mm)} mm</strong> ${j.is_forecast ? "expected" : "fell"} over about ${j.rain_hours} wet hours, ${niceDate(j.start)} to ${niceDate(j.end)}${j.is_forecast ? " (forecast)" : ""}.
        The ground was <strong style="color:#0B1650">${j.wetness}</strong>: ${fmt(j.antecedent_mm)} mm fell in the 5 days before.
        ${j.rain_mm < 5 ? "<br>Very little rain fell on these dates; try another date." : ""}<br>
        <span style="font-size:12px">${esc(j.source)}. Values are averaged over your area, so local downpours can be higher.</span></p>`;
      updateRunButton();
    } catch (err) { out.innerHTML = `<p class="hint is-error-text">${esc(err.message)}</p>`; }
  };
  function weatherChart(j) {
    const W = 300, H = 90, pad = 16, n = j.daily.length, bw = (W - 8) / n;
    const mx = Math.max(5, ...j.daily.map(d => d.rain_mm));
    const bars = j.daily.map((d, i) => {
      const h = (d.rain_mm / mx) * (H - pad - 14);
      return `<rect x="${4 + i * bw + 1}" y="${H - pad - h}" width="${Math.max(bw - 2, 1)}" height="${h}" fill="${d.in_storm ? "#1E86B8" : "#C9D2DE"}"><title>${niceDate(d.date)}: ${d.rain_mm} mm</title></rect>`;
    }).join("");
    return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;margin-top:10px;background:#fff;border:1px solid #D5DCE6;border-radius:8px" role="img" aria-label="Daily rainfall">
      ${bars}<text x="4" y="12" font-size="10" fill="#4A5578">${fmt(mx, 0)} mm/day</text>
      <text x="4" y="${H - 4}" font-size="10" fill="#4A5578">${niceDate(j.daily[0].date)}</text>
      <text x="${W - 4}" y="${H - 4}" font-size="10" fill="#4A5578" text-anchor="end">${niceDate(j.daily[n - 1].date)}</text>
      <text x="${W - 4}" y="12" font-size="10" fill="#1E86B8" text-anchor="end">rain days used</text></svg>`;
  }
  async function getRiverFlow(f) {
    f.flowNote = "Getting river flow…"; renderInflows();
    try {
      const q = new URLSearchParams({ lat: f.lat, lon: f.lng, date: $("wx-date").value, days: $("wx-days").value });
      const r = await fetch(`api/river-flow?${q}`); const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "The river flow request failed.");
      f.flood = j.flood_m3s; f.normal = j.normal_m3s;
      f.flowNote = `Peak ${fmt(j.flood_m3s, 0)} m³/s on ${niceDate(j.peak_date)}; typical ${fmt(j.normal_m3s, 0)} m³/s over the year before. ${j.source}. Uses the dates under Real weather.`;
    } catch (err) { f.flowNote = err.message; }
    renderInflows();
  }

  // ---------- running ----------
  $("run-btn").onclick = async () => {
    const body = {
      bbox: state.bbox, rain_mm: +$("rain-mm").value, rain_hours: +$("rain-hours").value,
      wetness: document.querySelector('input[name="wetness"]:checked').value,
      inflows: state.inflows.map(f => ({ lat: f.lat, lon: f.lng, flood_m3s: +f.flood, normal_m3s: +(f.normal || 0) })),
      place_name: state.placeName || null,
    };
    state.runPlace = { name: state.placeName || "Area you marked", bbox: state.bbox.slice() };
    statusFollow.on = true; statusFollow.lastLen = -1;   // follow the progress list until the user scrolls away
    showTab("results");
    $("results-empty").hidden = true; $("results-badge").hidden = true;
    showStatus("Starting the flood check", "", []);
    $("results").hidden = true;
    try {
      const r = await fetch("api/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const j = await r.json();
      if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "The server rejected the request. Check the inputs.");
      state.runId = j.id;
      state.polling = setInterval(poll, 1500);
      updateRunButton(); poll();
    } catch (err) {
      showError(err.message.includes("fetch") ? "Can't reach the FloodCheck server. Check your internet connection and try again." : err.message);
    }
  };

  function showStatus(title, text, log, current) {
    const s = $("status"); s.hidden = false; s.classList.remove("is-error");
    if (sheet.name === "peek") sheetTo("half");
    $("status-title").textContent = title; $("status-text").textContent = text;
    const ul = $("status-log"); ul.innerHTML = "";
    log.forEach((m, i) => {
      const li = document.createElement("li"); li.textContent = m;
      if (current && i === log.length - 1) li.className = "is-current";
      ul.appendChild(li);
    });
    followStatus();
  }

  // ---------- keep the progress list in view while a check runs ----------
  const statusFollow = { on: false, lastLen: -1 };
  function followStatus() {
    if (!statusFollow.on) return;
    const box = $("panel-scroll"), s = $("status");
    const n = $("status-log").children.length;
    if (n === statusFollow.lastLen && s.dataset.followed === "1") return;   // nothing new to show
    statusFollow.lastLen = n; s.dataset.followed = "1";
    requestAnimationFrame(() => {
      const top = s.offsetTop - 8;                                          // panel-scroll is the offset parent
      const bottom = s.offsetTop + s.offsetHeight - box.clientHeight + 12;  // status bottom at the panel's bottom edge
      // show the newest step: the bottom of the status card, without hiding its title when it fits
      const target = bottom <= top ? Math.min(Math.max(box.scrollTop, bottom), top) : bottom;
      if (Math.abs(target - box.scrollTop) > 2) box.scrollTo({ top: target, behavior: reduced() ? "auto" : "smooth" });
    });
  }
  // the user taking over the scroll stops the auto-follow for this check
  ["wheel", "touchmove", "keydown"].forEach(ev => $("panel-scroll").addEventListener(ev, (e) => {
    if (ev === "keydown" && !["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End", " "].includes(e.key)) return;
    statusFollow.on = false;
  }, { passive: true }));
  function showError(msg) {
    stopPolling();
    statusFollow.on = true; statusFollow.lastLen = -1; $("status").dataset.followed = "";
    const s = $("status"); s.hidden = false; s.classList.add("is-error");
    $("status-title").textContent = "The flood check didn't finish";
    $("status-text").textContent = msg; $("status-log").innerHTML = "";
    followStatus();
  }
  function stopPolling() { if (state.polling) clearInterval(state.polling); state.polling = null; updateRunButton(); }

  let pollBusy = false;
  async function poll() {
    if (!state.runId || pollBusy || !state.polling) return;
    pollBusy = true;
    let j;
    try {
      const r = await fetch(`api/runs/${state.runId}`); j = await r.json();
      if (!r.ok) throw new Error(j.detail || "Lost track of the run.");
    } catch (err) { pollBusy = false; showError("Lost contact with the FloodCheck server. It may have restarted (for example if it ran out of memory). Please try again, or try a smaller area."); return; }
    pollBusy = false;
    if (!state.polling) return;
    if (j.status === "queued") showStatus("Waiting for another check to finish", "", [], false);
    else if (j.status === "running") showStatus("Checking for floods", `${fmt(j.elapsed_s, 0)} seconds so far`, j.log, true);
    else if (j.status === "failed") showError(j.error);
    else if (j.status === "done") { stopPolling(); showStatus("Done", `Took ${fmt(j.elapsed_s, 0)} seconds`, j.log, false); showResult(j); }
  }

  // ---------- results on the map ----------
  function removeResultLayers(m) {
    ["res-facilities", "res-roads", "res-buildings-pts", "res-buildings", "flood"].forEach(id => { if (m.getLayer(id)) m.removeLayer(id); });
    ["res-facilities", "res-roads", "res-buildings", "flood"].forEach(id => { if (m.getSource(id)) m.removeSource(id); });
  }
  async function showResult(j) {
    state.result = j;
    removeResultLayers(map);
    const url = j.image_url + "?t=" + Date.now();
    map.addSource("flood", { type: "image", url, coordinates: j.corners });
    map.addLayer({ id: "flood", type: "raster", source: "flood",
                   paint: { "raster-opacity": +$("opacity").value, "raster-resampling": "nearest" } }, "box-fill");
    for (const cfg of LAYERS.filter(l => l.kind !== "image")) {
      if (!j.layers || !j.layers[cfg.id]) continue;
      map.addSource(`res-${cfg.id}`, { type: "geojson", data: j.layers[cfg.id] });
      const vis = { visibility: state.layerOn[cfg.id] === false ? "none" : "visible" };
      const def = { id: `res-${cfg.id}`, type: cfg.kind, source: `res-${cfg.id}`, paint: cfg.paint, layout: vis };
      if (cfg.filter) def.filter = cfg.filter;
      if (cfg.minzoom != null) def.minzoom = cfg.minzoom;
      map.addLayer(def, "profile-line");
      if (cfg.extra) {
        const x = cfg.extra, xdef = { id: `res-${cfg.id}-${x.suffix}`, type: x.type, source: `res-${cfg.id}`, paint: x.paint, layout: vis, filter: x.filter };
        if (x.maxzoom != null) xdef.maxzoom = x.maxzoom;
        map.addLayer(xdef, "profile-line");
      }
    }
    if (state.layerOn.flood === false) map.setLayoutProperty("flood", "visibility", "none");
    $("legend").hidden = false;
    renderLayersCard(j);
    renderSummary(j.summary);
    if (state.compare) { setResultVisibility(true); syncAfterFlood(); }
    if (state.profilePts.length >= 2) runProfile(state.profilePts);
  }
  $("opacity").oninput = (e) => {
    if (map.getLayer("flood")) map.setPaintProperty("flood", "raster-opacity", +e.target.value);
    if (after && after.getLayer("flood")) after.setPaintProperty("flood", "raster-opacity", +e.target.value);
  };

  function renderLayersCard(j) {
    const e = (j.summary && j.summary.exposure) || {};
    const rows = $("layer-rows"); rows.innerHTML = "";
    LAYERS.forEach(cfg => {
      const available = cfg.kind === "image" || (j.layers && j.layers[cfg.id]);
      const row = document.createElement("label"); row.className = "layer-row";
      const on = state.layerOn[cfg.id] !== false && available;
      row.innerHTML = `<input type="checkbox" ${on ? "checked" : ""} ${available ? "" : "disabled"}>
        <span class="sw ${cfg.swatchClass || ""}" style="background:${cfg.swatch}"></span>
        <span>${cfg.label}</span><span class="count">${available ? (cfg.count ? cfg.count(e) : "") : "not available"}</span>`;
      row.querySelector("input").onchange = (ev) => {
        state.layerOn[cfg.id] = ev.target.checked;
        const ids = cfg.kind === "image" ? ["flood"] : [`res-${cfg.id}`, `res-${cfg.id}-pts`];
        ids.forEach(id => { if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", ev.target.checked ? "visible" : "none"); });
      };
      rows.appendChild(row);
    });
    const card = $("layers-card");
    if (card.hidden && isPhone()) card.removeAttribute("open");
    card.hidden = false;
  }

  // ---------- plain-language risk verdict ----------
  const VERDICTS = [
    { title: "Low risk", text: "This rain is not expected to flood much land in your area.",
      todo: ["Keep drains, culverts and furrows clear", "Keep watching the weather forecast"] },
    { title: "Some flooding likely", text: "Low land near rivers and streams may go under water.",
      todo: ["Do not walk or drive through moving water", "Move animals, seed and stored food off low ground", "Check on neighbours who live near the river"] },
    { title: "Serious flooding likely", text: "Homes, roads or important places are in the flood water in this check.",
      todo: ["Agree where your family would go on higher ground", "Keep documents, medicines and a phone charger ready to carry", "Do not cross flooded roads or bridges", "Tell local leaders and share the warning"] },
    { title: "Severe flooding likely", text: "Many homes or important places are in deep water in this check.",
      todo: ["Be ready to leave early, before the water rises", "Move to higher ground when told to", "Never walk, swim or drive through flood water", "Follow instructions from local authorities"] },
  ];
  function riskLevel(s) {
    const e = s.exposure || {}, b = e.buildings_flooded || {}, fac = e.facilities_flooded || [];
    const share = s.flooded_share_pct || 0;
    if (!(s.flooded_area_km2 > 0)) return 0;
    let lv = 0;
    if (share >= 1 || (b.any || 0) > 0 || (e.roads_flooded_km || 0) >= 0.5) lv = 1;
    if (share >= 5 || (b["over_0.5m"] || 0) >= 20 || fac.length > 0 || (e.main_roads_flooded_km || 0) >= 1) lv = 2;
    if (share >= 15 || (b["over_1.0m"] || 0) >= 100 || fac.some(f => f.depth > 1)) lv = 3;
    return lv;
  }
  function renderVerdict(s) {
    const lv = riskLevel(s), v = VERDICTS[lv], e = s.exposure || {};
    $("verdict").dataset.level = lv;
    $("verdict-title").textContent = v.title;
    let text = v.text;
    if (e.buildings_flooded && e.buildings_flooded.any) text += ` About ${fmt(e.buildings_flooded.any, 0)} buildings have water around them.`;
    $("verdict-text").textContent = text;
    $("verdict-do").innerHTML = v.todo.map(t => `<li>${esc(t)}</li>`).join("");
    $("verdict").querySelectorAll(".meter span").forEach((el, i) => el.classList.toggle("is-on", i <= lv));
  }

  function renderSummary(s) {
    renderVerdict(s);
    $("res-area").textContent = `${fmt(s.flooded_area_km2, s.flooded_area_km2 >= 10 ? 0 : 1)} km² under water`;
    $("res-sub").textContent = `That is ${fmt(s.flooded_share_pct)}% of your area, for ${fmt(s.rain_mm, 0)} mm of rain over ${fmt(s.rain_hours)} hours on ${s.wetness} ground.`;
    $("res-max").textContent = `${fmt(s.max_depth_m)} m`;
    $("res-mean").textContent = `${fmt(s.mean_depth_m, 2)} m`;
    $("res-deep").textContent = `${fmt(s.area_over_1m_km2)} km²`;
    $("res-runoff").textContent = `${fmt(s.mean_runoff_pct, 0)}%`;

    const e = s.exposure || {};
    const notes = [];
    (s.inflows || []).forEach((f, i) => notes.push(`River inflow ${i + 1}: added ${fmt(f.added_m3s, 0)} m³/s along ${fmt(f.river_km, 0)} km of river.`));
    if (s.capped_segments) notes.push(`${s.capped_segments} river segment(s) reached the 15 m limit. Check the inflow values.`);
    if (s.soil_note) notes.push(s.soil_note);
    const wxUsed = state.weather && Math.round(state.weather.rain_mm) === Math.round(s.rain_mm);
    if (wxUsed) notes.push(`Rainfall from ${state.weather.source}, ${niceDate(state.weather.start)} to ${niceDate(state.weather.end)}${state.weather.is_forecast ? " (forecast)" : ""}.`);
    (e.notes || []).forEach(n => notes.push(n));
    if (e.buildings_truncated) notes.push("Only the 25,000 most deeply flooded buildings are drawn on the map; the counts include all of them.");
    $("res-notes").innerHTML = notes.map(n => `<li>${esc(n)}</li>`).join("");

    // exposure block
    const hasExpo = e.buildings_flooded || e.roads_flooded_km != null || e.facilities_flooded;
    $("expo").hidden = !hasExpo;
    if (e.buildings_flooded) {
      const b = e.buildings_flooded;
      $("expo-buildings-head").innerHTML = `<strong style="color:#0B1650;font-size:20px">${fmt(b.any, 0)}</strong> of ${fmt(e.buildings_total, 0)} buildings have flood water around them. How deep:`;
      const bands = [["0.1–0.5 m", b["over_0.1m"] - b["over_0.5m"]], ["0.5–1 m", b["over_0.5m"] - b["over_1.0m"]],
                     ["1–2 m", b["over_1.0m"] - b["over_2.0m"]], ["Over 2 m", b["over_2.0m"]]];
      const maxN = Math.max(1, ...bands.map(x => x[1]));
      const cols = ["#F2C14E", "#E8A13A", "#E07A1F", "#B3261E"];
      $("expo-bands").innerHTML = bands.map(([lab, n], i) =>
        `<li><span>${lab}</span><span class="band-bar" style="width:${Math.max(2, 100 * n / maxN)}%;background:${cols[i]}"></span><span class="n">${fmt(n, 0)}</span></li>`).join("");
    } else { $("expo-buildings-head").textContent = ""; $("expo-bands").innerHTML = ""; }
    $("expo-roads").innerHTML = e.roads_flooded_km != null ?
      `<strong style="color:#0B1650">${fmt(e.roads_flooded_km)} km</strong> of road under more than 30 cm of water, including ${fmt(e.main_roads_flooded_km)} km of main roads.` : "";
    const fac = e.facilities_flooded || [];
    if (e.facilities_total) {
      const tot = e.facilities_total;
      $("expo-fac-head").innerHTML = fac.length ?
        `<strong style="color:#0B1650">${fac.length}</strong> of ${tot.school + tot.health} schools and health facilities in flood water:` :
        `None of the ${tot.school + tot.health} schools and health facilities mapped here are flooded.`;
      $("expo-fac").innerHTML = fac.slice(0, 30).map((f, i) =>
        `<li><button data-i="${i}"><span>${esc(f.name)}</span><span>${fmt(f.depth, 1)} m</span></button></li>`).join("");
      $("expo-fac").querySelectorAll("button").forEach(btn => btn.onclick = () => {
        const f = fac[+btn.dataset.i];
        map.flyTo({ center: [f.lon, f.lat], zoom: 15, duration: reduced() ? 0 : 1000 });
        showPopup({ lng: f.lon, lat: f.lat }, LAYERS.find(l => l.id === "facilities").popup(f));
      });
    } else { $("expo-fac-head").textContent = ""; $("expo-fac").innerHTML = ""; }

    $("res-download").href = state.result.geotiff_url;
    $("res-pdf").href = state.result.report_url || "#";
    const rp = state.runPlace || { name: "Area you marked", bbox: state.bbox };
    const c = (v) => v.toFixed(4) + "°";
    $("res-place").innerHTML = `<span class="sec-label" style="margin:0">Results for</span>
      <p class="res-place-name">${esc(rp.name)}</p>
      <p class="res-place-coords">${c(rp.bbox[3])}, ${c(rp.bbox[0])} to ${c(rp.bbox[1])}, ${c(rp.bbox[2])}</p>`;
    $("report-place").textContent = rp.name;
    $("pdf-msg").textContent = "";
    $("report-ready").hidden = false; $("report-empty").hidden = true;
    $("results-empty").hidden = true;
    $("results-badge").hidden = false; $("tab-results").classList.add("is-done");
    $("status").hidden = true;                       // the progress list has done its job; results go to the top
    $("results").hidden = false;
    sheetTo("half");
    statusFollow.on = false; statusFollow.lastLen = -1; $("status").dataset.followed = "";
    $("panel-scroll").scrollTop = 0;
  }

  let popup;
  function showPopup(ll, html) {
    if (popup) popup.remove();
    popup = new maplibregl.Popup({ closeButton: true, maxWidth: "260px" }).setLngLat(ll).setHTML(html).addTo(map);
  }
  async function queryDepth(ll) {
    if (!inBox(ll.lng, ll.lat, state.bbox)) return;
    try {
      const r = await fetch(`api/runs/${state.runId}/depth?lat=${ll.lat}&lon=${ll.lng}`); const j = await r.json();
      showPopup(ll, j.permanent_water ? "River or lake (always wet)" :
                    j.depth_m > 0 ? `About <strong>${fmt(j.depth_m, 2)} m</strong> of water` : "Dry in this check");
    } catch (err) { console.warn("Depth lookup failed:", err); }
  }

  // ---------- cross-section ----------
  $("profile-btn").onclick = () => setMode(state.mode === "profile" ? null : "profile");
  // (the cross-section card stays open while you use it)
  function drawProfileLine() {
    const pts = state.profilePts;
    const feats = pts.map(p => ({ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: p } }));
    if (pts.length >= 2) feats.push({ type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: pts } });
    whenReady(() => map.getSource("profile").setData({ type: "FeatureCollection", features: feats }));
  }
  async function runProfile(pts) {
    $("profile-readout").textContent = "Measuring along the line…";
    try {
      const r = await fetch(`api/runs/${state.runId}/profile`, { method: "POST", headers: { "Content-Type": "application/json" },
                                                                   body: JSON.stringify({ coords: pts }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail || "The cross-section failed.");
      if (!j.ground_m.some(g => g != null)) {
        $("profile-chart").innerHTML = ""; $("profile-readout").textContent = "The line is outside your area. Draw it inside the rectangle.";
        return;
      }
      drawProfileChart(j);
    } catch (err) { $("profile-readout").textContent = err.message; }
  }
  function drawProfileChart(p) {
    const W = 320, H = 190, m = { l: 40, r: 8, t: 10, b: 26 };
    const xs = p.distance_m, g = p.ground_m, w = p.water_m;
    const vals = g.filter(v => v != null).concat(w.filter(v => v != null));
    let lo = Math.min(...vals), hi = Math.max(...vals);
    const pad = Math.max((hi - lo) * 0.12, 0.5); lo -= pad; hi += pad;
    const X = (d) => m.l + (d / p.length_m) * (W - m.l - m.r);
    const Y = (v) => H - m.b - ((v - lo) / (hi - lo)) * (H - m.t - m.b);
    let groundPath = "", started = false;
    xs.forEach((d, i) => { if (g[i] == null) { started = false; return; }
      groundPath += `${started ? "L" : "M"}${X(d).toFixed(1)},${Y(g[i]).toFixed(1)}`; started = true; });
    // ground fill
    const valid = xs.map((d, i) => [d, g[i]]).filter(x => x[1] != null);
    const groundFill = valid.length ? `M${X(valid[0][0])},${H - m.b}` + valid.map(([d, v]) => `L${X(d).toFixed(1)},${Y(v).toFixed(1)}`).join("") +
                       `L${X(valid[valid.length - 1][0])},${H - m.b}Z` : "";
    // water polygons for each wet run
    let water = "";
    let run = [];
    const flush = () => {
      if (run.length >= 1) {
        const top = run.map(i => `${X(xs[i]).toFixed(1)},${Y(w[i]).toFixed(1)}`);
        const bot = run.slice().reverse().map(i => `${X(xs[i]).toFixed(1)},${Y(g[i]).toFixed(1)}`);
        if (run.length === 1) { const i = run[0]; top.push(`${(X(xs[i]) + 1.5).toFixed(1)},${Y(w[i]).toFixed(1)}`); bot.unshift(`${(X(xs[i]) + 1.5).toFixed(1)},${Y(g[i]).toFixed(1)}`); }
        water += `<polygon points="${top.concat(bot).join(" ")}" fill="#3182BD" fill-opacity="0.75"/>`;
      }
      run = [];
    };
    xs.forEach((d, i) => { if (w[i] != null && g[i] != null) run.push(i); else flush(); }); flush();
    const river = xs.map((d, i) => p.permanent_water[i] && g[i] != null ?
      `<line x1="${X(d).toFixed(1)}" x2="${X(d).toFixed(1)}" y1="${Y(g[i]).toFixed(1)}" y2="${(Y(g[i]) + 4).toFixed(1)}" stroke="#5A6E82" stroke-width="2"/>` : "").join("");
    const km = p.length_m / 1000;
    $("profile-chart").innerHTML = `
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Cross-section of ground and flood water along the drawn line">
        <path d="${groundFill}" fill="#D9C7A7"/>
        ${water}${river}
        <path d="${groundPath}" fill="none" stroke="#6B4F2A" stroke-width="1.5"/>
        <line x1="${m.l}" x2="${W - m.r}" y1="${H - m.b}" y2="${H - m.b}" stroke="#4A5578"/>
        <text x="${m.l - 4}" y="${Y(hi - pad) + 4}" text-anchor="end" font-size="11" fill="#4A5578">${fmt(hi - pad, 0)} m</text>
        <text x="${m.l - 4}" y="${Y(lo + pad) + 4}" text-anchor="end" font-size="11" fill="#4A5578">${fmt(lo + pad, 0)} m</text>
        <text x="${m.l}" y="${H - 8}" font-size="11" fill="#4A5578">0 km</text>
        <text x="${W - m.r}" y="${H - 8}" text-anchor="end" font-size="11" fill="#4A5578">${fmt(km)} km</text>
        <line id="pf-cursor" x1="0" x2="0" y1="${m.t}" y2="${H - m.b}" stroke="#0B1650" stroke-dasharray="3 2" visibility="hidden"/>
        <rect x="${m.l}" y="${m.t}" width="${W - m.l - m.r}" height="${H - m.t - m.b}" fill="transparent" id="pf-hit"/>
      </svg>`;
    const maxDepth = Math.max(0, ...xs.map((d, i) => w[i] != null && g[i] != null ? w[i] - g[i] : 0));
    const base = maxDepth > 0 ? `Deepest water along the line: ${fmt(maxDepth, 2)} m. Hover the chart for details.` : "No flood water along this line.";
    $("profile-readout").textContent = base;
    const svg = $("profile-chart").querySelector("svg"), cur = svg.querySelector("#pf-cursor");
    svg.querySelector("#pf-hit").addEventListener("pointermove", (ev) => {
      const r = svg.getBoundingClientRect();
      const sx = (ev.clientX - r.left) * (W / r.width);
      const d = Math.min(Math.max((sx - m.l) / (W - m.l - m.r), 0), 1) * p.length_m;
      let k = 0; while (k < xs.length - 1 && xs[k] < d) k++;
      cur.setAttribute("x1", X(xs[k])); cur.setAttribute("x2", X(xs[k])); cur.setAttribute("visibility", "visible");
      const depth = w[k] != null && g[k] != null ? w[k] - g[k] : 0;
      $("profile-readout").textContent = g[k] == null ? "Outside your area" :
        `${fmt(xs[k] / 1000, 2)} km along: ground ${fmt(g[k], 1)} m` + (p.permanent_water[k] ? ", river or lake" : depth > 0 ? `, water ${fmt(depth, 2)} m deep` : ", dry");
    });
    svg.querySelector("#pf-hit").addEventListener("pointerleave", () => { cur.setAttribute("visibility", "hidden"); $("profile-readout").textContent = base; });
  }

  // ---------- compare two images ----------
  let after = null, afterReady = false, swipeX = 0.5;
  function setResultVisibility(hideAll) {
    LAYERS.forEach(cfg => {
      const ids = cfg.kind === "image" ? ["flood"] : [`res-${cfg.id}`, `res-${cfg.id}-pts`];
      const show = !hideAll && state.layerOn[cfg.id] !== false;
      ids.forEach(id => { if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", show ? "visible" : "none"); });
    });
  }
  function setImageLayer(m, id, src, beforeId) {
    if (m.getLayer(id)) m.removeLayer(id);
    if (m.getSource(id)) m.removeSource(id);
    if (!src) return;
    m.addSource(id, src);
    m.addLayer({ id, type: "raster", source: id }, beforeId && m.getLayer(beforeId) ? beforeId : undefined);
    // make sure newly arrived image tiles are drawn straight away
    m.on("sourcedata", (e) => { if (e.sourceId === id && e.tile) m.triggerRepaint(); });
  }
  function imageArea() {
    if (state.bbox) return state.bbox;
    const b = map.getBounds();
    const c = map.getCenter(), h = 0.2;   // no area drawn: use the middle of the view
    return [Math.max(b.getWest(), c.lng - h), Math.max(b.getSouth(), c.lat - h), Math.min(b.getEast(), c.lng + h), Math.min(b.getNorth(), c.lat + h)];
  }
  const SIDE = { left: { req: 0 }, right: { req: 0 } };
  async function loadSide(side) {
    const m = side === "left" ? map : after;
    if (side === "right" && !afterReady) return;
    const srcType = $(`cmp-${side}-src`).value, date = $(`cmp-${side}`).value, status = $(`cmp-${side}-status`);
    const id = `cmp-${side}`, before = side === "left" ? "box-fill" : undefined;
    const req = ++SIDE[side].req;
    if (!date) { status.textContent = "Choose a date."; return; }
    if (srcType === "modis") {
      setImageLayer(m, id, { type: "raster", tiles: gibsTiles(date), tileSize: 256, maxzoom: 9, attribution: GIBS_ATTR }, before);
      status.textContent = `MODIS image for ${niceDate(date)}.`;
    } else {
      status.textContent = "Finding the nearest image…";
      const area = imageArea();
      try {
        const q = new URLSearchParams({ source: srcType, bbox: area.join(","), date });
        const r = await fetch(`api/imagery/search?${q}`); const j = await r.json();
        if (req !== SIDE[side].req) return;               // a newer choice replaced this one
        if (!r.ok) throw new Error(j.detail || "No image found.");
        const water = srcType === "s1" && $("cmp-water").checked;
        setImageLayer(m, id, { type: "raster", tiles: [j.tiles + (water ? "?water=true" : "")], tileSize: 256, minzoom: 8, maxzoom: 15,
                               bounds: area, attribution: srcType === "s2" ? "Sentinel-2: Copernicus / Element 84" : "Sentinel-1: Copernicus / Microsoft Planetary Computer" }, before);
        const off = j.days_from_target === 0 ? "the same day" : `${Math.abs(j.days_from_target)} day${Math.abs(j.days_from_target) === 1 ? "" : "s"} ${j.days_from_target > 0 ? "after" : "before"}`;
        status.textContent = srcType === "s2" ? `Photo from ${niceDate(j.date)} (${off}), about ${fmt(j.cloud_pct, 0)}% cloud.`
                                              : `Radar image from ${niceDate(j.date)} (${off}).`;
        if (map.getZoom() < 8) status.textContent += " Zoom in to see it.";
      } catch (err) {
        if (req !== SIDE[side].req) return;
        setImageLayer(m, id, null); status.textContent = err.message;
      }
    }
    if (side === "right") syncAfterFlood();
  }
  function syncAfterFlood() {
    if (!after || !afterReady) return;
    removeResultLayers(after);
    if (state.result && $("cmp-flood").checked) {
      after.addSource("flood", { type: "image", url: state.result.image_url, coordinates: state.result.corners });
      after.addLayer({ id: "flood", type: "raster", source: "flood", paint: { "raster-opacity": +$("opacity").value, "raster-resampling": "nearest" } });
    }
  }
  function placeSwipe() {
    const rect = $("map").getBoundingClientRect();
    const px = swipeX * rect.width;
    $("swipe").style.left = `${px}px`;
    $("map-after").style.clipPath = `inset(0 0 0 ${px}px)`;
  }
  function syncView() { if (after) after.jumpTo({ center: map.getCenter(), zoom: map.getZoom(), bearing: map.getBearing(), pitch: map.getPitch() }); }
  function setCompare(on) {
    state.compare = on;
    $("compare-btn").setAttribute("aria-pressed", String(on));
    $("compare-bar").hidden = !on; $("map-after").hidden = !on; $("swipe").hidden = !on;
    if (on) {
      setResultVisibility(true);   // the left side shows the plain earlier image
      loadSide("left");
      if (!after) {
        after = new maplibregl.Map({ container: "map-after", interactive: false, attributionControl: false,
          center: map.getCenter(), zoom: map.getZoom(), maxPitch: 75,
          style: { version: 8, sources: { dem: { type: "raster-dem", tiles: [TERRAIN_TILES], encoding: "terrarium", tileSize: 256, maxzoom: 14 } }, layers: [] } });
        after.on("load", () => {
          afterReady = true;
          if (state.terrain) after.setTerrain({ source: "dem", exaggeration: 2 });
          loadSide("right");
        });
        map.on("move", syncView);
      } else loadSide("right");
      after.resize(); syncView(); placeSwipe();
    } else {
      setImageLayer(map, "cmp-left", null);
      setResultVisibility(false);
    }
  }
  $("compare-btn").onclick = () => setCompare(!state.compare);
  ["left", "right"].forEach(side => {
    $(`cmp-${side}`).onchange = () => loadSide(side);
    $(`cmp-${side}-src`).onchange = () => loadSide(side);
  });
  $("cmp-flood").onchange = syncAfterFlood;
  $("cmp-water").onchange = () => ["left", "right"].forEach(side => { if ($(`cmp-${side}-src`).value === "s1") loadSide(side); });
  (() => {
    const handle = $("swipe-handle");
    let dragging = false;
    const move = (clientX) => {
      const rect = $("map").getBoundingClientRect();
      swipeX = Math.min(Math.max((clientX - rect.left) / rect.width, 0.02), 0.98); placeSwipe();
    };
    handle.addEventListener("pointerdown", (e) => { dragging = true; handle.setPointerCapture(e.pointerId); });
    handle.addEventListener("pointermove", (e) => { if (dragging) move(e.clientX); });
    handle.addEventListener("pointerup", () => { dragging = false; });
    handle.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft" || e.key === "ArrowRight") { swipeX = Math.min(Math.max(swipeX + (e.key === "ArrowLeft" ? -0.05 : 0.05), 0.02), 0.98); placeSwipe(); e.preventDefault(); }
    });
    window.addEventListener("resize", () => { if (state.compare) { after.resize(); placeSwipe(); } });
  })();

  // ---------- phone layout: the panel is a bottom sheet over a full-screen map ----------
  function sheetMetrics() {
    const hdr = document.querySelector(".brandbar").getBoundingClientRect().height;
    const avail = window.innerHeight - hdr;
    const peek = $("sheet-handle").offsetHeight + document.querySelector(".run-bar").offsetHeight;
    return { hdr, peek, half: Math.max(peek, Math.round(avail * 0.52)), full: Math.max(peek, avail - 10) };
  }
  function applySheet(px) {
    const app = document.querySelector(".app");
    if (!isPhone()) { app.style.removeProperty("--sheet-h"); return; }
    const m = sheetMetrics();
    app.style.setProperty("--hdr", `${m.hdr}px`);
    app.style.setProperty("--sheet-h", `${Math.round(px)}px`);
  }
  function sheetTo(name) {
    if (!isPhone()) return;
    sheet.name = name;
    applySheet(sheetMetrics()[name]);
    const open = name !== "peek";
    $("sheet-label").textContent = open ? "Show more map" : "Show the steps";
    $("sheet-handle").setAttribute("aria-expanded", String(open));
    document.querySelector(".panel").dataset.sheet = name;
  }
  function scrollPanelTo(el) {        // scroll only the panel, never the page
    const box = $("panel-scroll");
    const top = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 8;
    box.scrollTo({ top, behavior: reduced() ? "auto" : "smooth" });
  }
  function fitBox(b) {
    if (!b) return;
    const bottom = isPhone() ? sheetMetrics()[sheet.name] : 0;
    map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: { top: 70, left: 24, right: 24, bottom: bottom + 30 },
      duration: reduced() ? 0 : 850, maxZoom: 15 });
  }
  (() => {
    const handle = $("sheet-handle"), panel = document.querySelector(".panel");
    let drag = null;
    handle.addEventListener("pointerdown", (e) => {
      if (!isPhone()) return;
      drag = { y: e.clientY, h: panel.getBoundingClientRect().height, moved: false };
      handle.setPointerCapture(e.pointerId); panel.classList.add("is-dragging");
    });
    handle.addEventListener("pointermove", (e) => {
      if (!drag) return;
      const m = sheetMetrics(), dy = drag.y - e.clientY;
      if (Math.abs(dy) > 6) drag.moved = true;
      if (drag.moved) applySheet(Math.min(Math.max(drag.h + dy, m.peek), m.full));
    });
    const end = () => {
      if (!drag) return;
      panel.classList.remove("is-dragging");
      const m = sheetMetrics(), h = panel.getBoundingClientRect().height, moved = drag.moved; drag = null;
      if (!moved) { sheetTo(sheet.name === "peek" ? "half" : "peek"); return; }
      const nearest = ["peek", "half", "full"].reduce((a, k) => Math.abs(m[k] - h) < Math.abs(m[a] - h) ? k : a, "peek");
      sheetTo(nearest);
    };
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
    handle.addEventListener("keydown", (e) => {
      if (e.key === "ArrowUp") { e.preventDefault(); sheetTo(sheet.name === "peek" ? "half" : "full"); }
      if (e.key === "ArrowDown") { e.preventDefault(); sheetTo(sheet.name === "full" ? "half" : "peek"); }
    });
    handle.addEventListener("click", (e) => { if (e.detail === 0) sheetTo(sheet.name === "peek" ? "half" : "peek"); });  // keyboard
    const relayout = () => { if (isPhone()) sheetTo(sheet.name); else applySheet(0); };
    window.addEventListener("resize", relayout);
    phoneQuery.addEventListener("change", relayout);
    relayout();
  })();

  // ---------- tabs: Area · Rain · Results · Report ----------
  function showTab(name) {
    document.querySelectorAll(".tab").forEach(t => {
      const on = t.dataset.tab === name;
      t.setAttribute("aria-selected", String(on)); t.tabIndex = on ? 0 : -1;
    });
    document.querySelectorAll(".pane").forEach(p => { p.hidden = p.id !== `pane-${name}`; });
    $("panel-scroll").scrollTop = 0;
    if (isPhone() && sheet.name === "peek") sheetTo("half");
  }
  document.querySelectorAll(".tab").forEach(t => {
    t.onclick = () => showTab(t.dataset.tab);
    t.onkeydown = (e) => {                                   // arrow keys move between tabs
      const tabs = [...document.querySelectorAll(".tab")], i = tabs.indexOf(t);
      const k = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (k) { e.preventDefault(); const n = tabs[(i + k + tabs.length) % tabs.length]; n.focus(); showTab(n.dataset.tab); }
    };
  });
  document.querySelectorAll("[data-goto]").forEach(b => b.onclick = () => showTab(b.dataset.goto));
  // the PDF is made on the server when asked for: fetch it, so problems show as a message instead of a silent failure
  $("res-pdf").addEventListener("click", async (e) => {
    e.preventDefault();
    const b = $("res-pdf"), msg = $("pdf-msg"), url = b.getAttribute("href");
    if (!state.result || !url || url === "#") { msg.textContent = "Run a flood check first."; return; }
    if (b.classList.contains("is-busy")) return;
    const label = b.lastChild.textContent;
    b.classList.add("is-busy"); b.lastChild.textContent = "Preparing the PDF…";
    msg.classList.remove("is-error-text"); msg.textContent = "Making the report: this takes a few seconds.";
    try {
      const r = await fetch(url);
      if (!r.ok) {
        let detail = `The report couldn't be made (error ${r.status}).`;
        try { const j = await r.json(); if (j.detail) detail = j.detail; } catch (_) {}
        if (r.status === 404) detail = "The server doesn't know this report. Make sure the server is running the latest backend files, then run the check again.";
        throw new Error(detail);
      }
      const blob = await r.blob();
      const cd = r.headers.get("Content-Disposition") || "";
      const name = (cd.match(/filename="?([^";]+)"?/) || [])[1] || "floodcheck_report.pdf";
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob); a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(a.href), 60000);
      msg.textContent = `Downloaded: ${name}`;
    } catch (err) {
      msg.classList.add("is-error-text");
      msg.textContent = err.message.includes("fetch") ? "Can't reach the FloodCheck server. Check it is still running." : err.message;
    } finally {
      b.classList.remove("is-busy"); b.lastChild.textContent = label;
    }
  });
  setBox(state.bbox);
})();
