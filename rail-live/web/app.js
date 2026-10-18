"use strict";

const $ = (id) => document.getElementById(id);
const DEFAULT_TIME = 6 * 3600;
const MAX_TIME = 27 * 3600;
const FALLBACK_COLORS = ["#26a9a0", "#e48836", "#a46cda"];
const validColor = (value, fallback) => /^#(?:[\da-f]{3}|[\da-f]{6})$/i.test(String(value)) ? value : fallback;
const validCoordinate = (lat, lng) => Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180;
const coordinate = ([lng, lat]) => new mapkit.Coordinate(lat, lng);

function formatTime(seconds, withSeconds = false) {
  seconds = Math.max(0, Math.floor(seconds));
  const hour = Math.floor(seconds / 3600);
  const minute = Math.floor(seconds / 60) % 60;
  const second = seconds % 60;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}${withSeconds ? `:${String(second).padStart(2, "0")}` : ""}`;
}

function setStatus(message, failed = false) {
  $("status").textContent = message;
  $("status").toggleAttribute("data-error", failed);
  $("retry").hidden = !failed;
}

async function loadData(name) {
  const response = await fetch(`/data/${name}`, { cache: "no-store", signal: AbortSignal.timeout(15000) });
  if (response.status === 404) return { missing: true };
  if (!response.ok) throw new Error(`${name}: HTTP ${response.status}`);
  return { value: await response.json() };
}

function normalizeStations(raw, warnings = []) {
  if (!Array.isArray(raw)) {
    warnings.push("stations.json: expected an array.");
    return new Map();
  }
  const stations = new Map();
  raw.forEach((row, index) => {
    if (!row || row.id == null || typeof row.name !== "string" || !validCoordinate(row.lat, row.lng)) {
      warnings.push(`Station row ${index + 1} is invalid and was skipped.`);
      return;
    }
    const id = String(row.id);
    stations.set(id, { id, name: row.name, lat: row.lat, lng: row.lng, mode: String(row.mode || "KRL").toUpperCase() });
  });
  return stations;
}

function normalizeTrips(raw, stationIndex, warnings = []) {
  if (!Array.isArray(raw)) {
    warnings.push("trips.json: expected an array.");
    return { trips: [], missingStationSkips: 0, invalidSkips: 0 };
  }
  const trips = [];
  let missingStationSkips = 0, invalidSkips = 0;
  raw.forEach((row, index) => {
    if (!row || row.train_id == null || !Array.isArray(row.stops) || row.stops.length < 2) {
      invalidSkips++;
      return;
    }
    const stops = [];
    let previous = -Infinity, missingStation = false, invalid = false;
    for (const item of row.stops) {
      const station = stationIndex.get(String(item?.station_id));
      if (!station) { missingStation = true; break; }
      if (!Number.isFinite(item?.t) || item.t < 0 || item.t < previous) { invalid = true; break; }
      previous = item.t;
      stops.push({ ...station, time: item.t });
    }
    if (missingStation) { missingStationSkips++; return; }
    if (invalid || stops.length < 2) { invalidSkips++; return; }
    trips.push({
      key: `${row.train_id}-${index}`,
      train_id: String(row.train_id),
      name: String(row.name || `Train ${row.train_id}`),
      route: String(row.route || "—"),
      color: validColor(row.color, FALLBACK_COLORS[index % FALLBACK_COLORS.length]),
      dest: String(row.dest || "—"),
      stops,
    });
  });
  if (invalidSkips) warnings.push(`${invalidSkips} trips skipped for invalid times or stop data.`);
  return { trips, missingStationSkips, invalidSkips };
}

function normalizeSegments(raw, stationIndex, warnings = []) {
  if (raw?.type !== "FeatureCollection" || !Array.isArray(raw.features)) {
    warnings.push("segments.geojson: expected a FeatureCollection.");
    return [];
  }
  const segments = [];
  raw.features.forEach((feature, index) => {
    const properties = feature?.properties || {};
    const from = stationIndex.get(String(properties.from));
    const to = stationIndex.get(String(properties.to));
    const coordinates = feature?.geometry?.coordinates;
    if (feature?.geometry?.type !== "LineString" || !from || !to || !Array.isArray(coordinates) || coordinates.length !== 2 ||
        !coordinates.every((point) => Array.isArray(point) && validCoordinate(point[1], point[0]))) {
      warnings.push(`Segment ${index + 1} is invalid and was skipped.`);
      return;
    }
    segments.push({
      from,
      to,
      coordinates,
      color: validColor(properties.color, "#4f827c"),
      approximate: properties.approximate === true,
    });
  });
  return segments;
}

function demoData() {
  const stations = [
    { id: "demo:kota", name: "Jakarta Kota", lat: -6.1376, lng: 106.8146, mode: "KRL" },
    { id: "demo:juanda", name: "Juanda", lat: -6.1667, lng: 106.8304, mode: "KRL" },
    { id: "demo:gondangdia", name: "Gondangdia", lat: -6.1859, lng: 106.8326, mode: "KRL" },
    { id: "demo:cikini", name: "Cikini", lat: -6.1989, lng: 106.8413, mode: "KRL" },
    { id: "demo:manggarai", name: "Manggarai", lat: -6.2098, lng: 106.8502, mode: "KRL" },
  ];
  const start = 6 * 3600;
  const trips = [
    { train_id: "DEMO-01", name: "Demo Commuter", route: "KOTA-MANGGARAI", color: FALLBACK_COLORS[0], dest: "Manggarai", order: stations },
    { train_id: "DEMO-02", name: "Demo Commuter", route: "MANGGARAI-KOTA", color: FALLBACK_COLORS[1], dest: "Jakarta Kota", order: [...stations].reverse() },
    { train_id: "DEMO-03", name: "Demo Commuter", route: "KOTA-MANGGARAI", color: FALLBACK_COLORS[2], dest: "Manggarai", order: stations },
  ].map((trip, index) => ({
    train_id: trip.train_id,
    name: trip.name,
    route: trip.route,
    color: trip.color,
    dest: trip.dest,
    stops: trip.order.map((station, stopIndex) => ({ station_id: station.id, t: start + index * 180 + stopIndex * 420 })),
  }));
  const features = [];
  for (const trip of trips) {
    for (let index = 1; index < trip.stops.length; index++) {
      const from = trip.stops[index - 1].station_id, to = trip.stops[index].station_id;
      const pair = [from, to].sort();
      if (features.some((feature) => feature.properties.from === pair[0] && feature.properties.to === pair[1])) continue;
      const startStation = stations.find((station) => station.id === pair[0]);
      const endStation = stations.find((station) => station.id === pair[1]);
      features.push({ type: "Feature", properties: { from: pair[0], to: pair[1], color: trip.color, approximate: true },
        geometry: { type: "LineString", coordinates: [[startStation.lng, startStation.lat], [endStation.lng, endStation.lat]] } });
    }
  }
  return { stations, trips, segments: { type: "FeatureCollection", features } };
}

function bearing(a, b) {
  const phi1 = a.lat * Math.PI / 180, phi2 = b.lat * Math.PI / 180;
  const deltaLambda = (b.lng - a.lng) * Math.PI / 180;
  const y = Math.sin(deltaLambda) * Math.cos(phi2);
  const x = Math.cos(phi1) * Math.sin(phi2) - Math.sin(phi1) * Math.cos(phi2) * Math.cos(deltaLambda);
  return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}

function positionAt(trip, time) {
  const stops = trip.stops;
  if (time < stops[0].time || time > stops.at(-1).time) return null;
  for (let index = 0; index < stops.length - 1; index++) {
    const a = stops[index], b = stops[index + 1];
    if (time < b.time) {
      const duration = b.time - a.time;
      const progress = duration > 0 ? Math.max(0, Math.min(1, (time - a.time) / duration)) : 0;
      return {
        coordinate: [a.lng + (b.lng - a.lng) * progress, a.lat + (b.lat - a.lat) * progress],
        heading: bearing(a, b),
        next: b,
        eta: Math.max(0, b.time - time),
      };
    }
  }
  const last = stops.at(-1);
  return { coordinate: [last.lng, last.lat], heading: 0, next: null, eta: 0 };
}

function appendDetailLine(list, main, meta = "") {
  const item = document.createElement("li");
  const title = document.createElement("strong");
  title.textContent = main;
  item.append(title);
  if (meta) {
    const note = document.createElement("span");
    note.textContent = meta;
    item.append(note);
  }
  list.append(item);
}

function updateTrainCallout(trip, state, time) {
  $("details").hidden = false;
  $("detail-kicker").textContent = "KRL · TRAIN DETAILS";
  $("detail-title").textContent = trip.name;
  $("detail-body").textContent = `Train ${trip.train_id}  ·  ${trip.route}\nDestination: ${trip.dest}`;
  const list = $("detail-list");
  list.replaceChildren();
  if (state?.next) {
    const eta = state.eta <= 60 ? `${Math.ceil(state.eta)} sec` : `${Math.ceil(state.eta / 60)} min`;
    appendDetailLine(list, `Next stop · ${state.next.name}`, `ETA ${eta} · ${formatTime(state.next.time)}`);
  } else {
    appendDetailLine(list, "Service complete", "Final station reached");
  }
  $("details").style.setProperty("--callout-color", trip.color);
  $("details").dataset.type = "train";
}

function updateStationCallout(station, departuresByStation, time) {
  $("details").hidden = false;
  $("detail-kicker").textContent = `${station.mode} · STATION DETAILS`;
  $("detail-title").textContent = station.name;
  $("detail-body").textContent = `Station ${station.id} · Next scheduled departures`;
  const departures = (departuresByStation.get(station.id) || []).filter((item) => item.stop.time >= time);
  const list = $("detail-list");
  list.replaceChildren();
  for (const item of departures.slice(0, 5)) {
    const destination = item.next ? ` · ${item.next.name}` : ` · ${item.trip.dest}`;
    appendDetailLine(list, `${formatTime(item.stop.time)} · ${item.trip.train_id}`, `${item.trip.route}${destination}`);
  }
  if (!list.childElementCount) appendDetailLine(list, "No upcoming departures", "Try an earlier simulation time");
  $("details").style.removeProperty("--callout-color");
  $("details").dataset.type = "station";
}

async function buildMap(map) {
  const results = await Promise.all([loadData("stations.json"), loadData("trips.json"), loadData("segments.geojson")]);
  const names = ["stations.json", "trips.json", "segments.geojson"];
  const missing = results.flatMap((result, index) => result.missing ? [names[index]] : []);
  if (missing.length) {
    const fixture = demoData();
    results[0].value = fixture.stations;
    results[1].value = fixture.trips;
    results[2].value = fixture.segments;
    $("demo-label").hidden = false;
    $("source").textContent = `Demo schedule · ${missing.join(", ")} missing`;
  } else {
    $("source").textContent = "Real KCI schedule data · 88 stations";
  }

  const warnings = [];
  const stationIndex = normalizeStations(results[0].value, warnings);
  const tripResult = normalizeTrips(results[1].value, stationIndex, warnings);
  const segments = normalizeSegments(results[2].value, stationIndex, warnings);
  const trips = tripResult.trips;
  $("warnings").textContent = warnings.join(" ");
  console.info("[Jakarta Railway] Data loaded", {
    stations: stationIndex.size,
    trips: trips.length,
    tripsSkippedMissingStations: tripResult.missingStationSkips,
    tripsSkippedInvalid: tripResult.invalidSkips,
    segments: segments.length,
    demo: missing.length > 0,
  });

  const departuresByStation = new Map();
  for (const station of stationIndex.values()) departuresByStation.set(station.id, []);
  for (const trip of trips) {
    trip.stops.forEach((stop, index) => {
      departuresByStation.get(stop.id)?.push({ trip, stop, next: trip.stops[index + 1] });
    });
  }
  for (const departures of departuresByStation.values()) {
    departures.sort((a, b) => a.stop.time - b.stop.time || a.trip.train_id.localeCompare(b.trip.train_id));
  }

  for (const station of stationIndex.values()) {
    const marker = new mapkit.MarkerAnnotation(new mapkit.Coordinate(station.lat, station.lng), {
      title: station.name,
      subtitle: `${station.mode} · ${station.id}`,
      color: station.mode === "MRT" ? "#557ee8" : station.mode === "LRT" ? "#24a394" : "#e4873b",
      data: { station },
    });
    map.addAnnotation(marker);
  }

  for (const segment of segments) {
    const overlay = new mapkit.PolylineOverlay(segment.coordinates.map((point) => coordinate(point)), {
      style: new mapkit.Style({
        strokeColor: segment.color,
        strokeOpacity: 0.82,
        lineWidth: 3.5,
        lineCap: "round",
        lineDash: segment.approximate ? [7, 6] : [],
      }),
    });
    map.addOverlay(overlay);
  }

  const annotations = new Map();
  let selectedTrip = null;
  let selectedStation = null;
  let now = DEFAULT_TIME;
  map.addEventListener("select", (event) => {
    const data = event.annotation?.data;
    if (data?.station) {
      selectedTrip = null;
      selectedStation = data.station;
      updateStationCallout(selectedStation, departuresByStation, now);
    } else if (data?.trip) {
      selectedStation = null;
      selectedTrip = data.trip.key;
      updateTrainCallout(data.trip, positionAt(data.trip, now), now);
    }
  });
  $("close-details").addEventListener("click", () => {
    selectedTrip = null;
    selectedStation = null;
    $("details").hidden = true;
  });

  let speed = 60;
  let playing = false;
  const slider = $("time");
  const playButton = $("play");
  const speedButtons = [...document.querySelectorAll("[data-speed]")];
  slider.disabled = false;
  playButton.disabled = false;
  speedButtons.forEach((button) => { button.disabled = false; });
  slider.value = now;

  playButton.addEventListener("click", () => {
    if (now >= MAX_TIME) now = 0;
    playing = !playing;
    playButton.querySelector("span:last-child").textContent = playing ? "Pause" : "Play";
    playButton.querySelector(".play-icon").textContent = playing ? "Ⅱ" : "▶";
    playButton.setAttribute("aria-label", playing ? "Pause simulation" : "Play simulation");
  });
  speedButtons.forEach((button) => button.addEventListener("click", () => {
    speed = Number(button.dataset.speed);
    speedButtons.forEach((item) => item.setAttribute("aria-pressed", String(item === button)));
  }));
  slider.addEventListener("input", () => {
    now = Number(slider.value);
    render();
  });

  function activeTrains() {
    const active = [];
    for (const trip of trips) {
      const position = positionAt(trip, now);
      let entry = annotations.get(trip.key);
      if (!position) {
        if (entry) {
          map.removeAnnotation(entry.annotation);
          annotations.delete(trip.key);
        }
        continue;
      }
      active.push({ trip, position });
      if (!entry) {
        const element = document.createElement("button");
        element.type = "button";
        element.className = "train-marker";
        element.style.setProperty("--train-color", trip.color);
        element.setAttribute("aria-label", `${trip.name}, train ${trip.train_id}`);
        const glyph = document.createElement("span");
        glyph.className = "train-glyph";
        glyph.setAttribute("aria-hidden", "true");
        glyph.textContent = "▲";
        element.append(glyph);
        const annotation = new mapkit.Annotation(coordinate(position.coordinate), () => element, {
          title: trip.name,
          subtitle: `${trip.train_id} · ${trip.route}`,
          data: { trip },
          calloutEnabled: false,
          size: { width: 34, height: 34 },
        });
        element.addEventListener("click", () => {
          selectedStation = null;
          selectedTrip = trip.key;
          updateTrainCallout(trip, positionAt(trip, now), now);
        });
        entry = { annotation, element, glyph };
        annotations.set(trip.key, entry);
        map.addAnnotation(annotation);
      }
      entry.annotation.coordinate = coordinate(position.coordinate);
      entry.glyph.style.transform = `rotate(${position.heading}deg)`;
      entry.element.setAttribute("aria-label", `${trip.name}, train ${trip.train_id}, next ${position.next?.name || trip.dest}`);
      if (selectedTrip === trip.key) updateTrainCallout(trip, position, now);
    }
    if (selectedTrip && !annotations.has(selectedTrip)) {
      $("detail-body").textContent = "This train is outside its scheduled service window.";
      $("detail-list").replaceChildren();
    }
    if (selectedStation) updateStationCallout(selectedStation, departuresByStation, now);
    return active.length;
  }

  function render() {
    const active = activeTrains();
    $("clock").textContent = formatTime(now);
    slider.value = now;
    slider.setAttribute("aria-valuetext", formatTime(now));
    $("active-count").textContent = `${active} active`;
    $("counts").textContent = `${stationIndex.size} stations · ${segments.length} segments · ${active} trains active`;
  }

  let previousFrame;
  let previousRender = -Infinity;
  function frame(timestamp) {
    const elapsed = previousFrame === undefined ? 0 : (timestamp - previousFrame) / 1000;
    previousFrame = timestamp;
    if (playing && !document.hidden) {
      now = Math.min(MAX_TIME, now + elapsed * speed);
      if (now >= MAX_TIME) {
        playing = false;
        playButton.querySelector("span:last-child").textContent = "Play";
        playButton.querySelector(".play-icon").textContent = "▶";
        playButton.setAttribute("aria-label", "Play simulation");
      }
    }
    if (timestamp - previousRender >= 1000 / 15) {
      render();
      previousRender = timestamp;
    }
    requestAnimationFrame(frame);
  }
  document.addEventListener("visibilitychange", () => { previousFrame = undefined; });
  render();
  requestAnimationFrame(frame);
}

async function getToken() {
  const response = await fetch("/token", { cache: "no-store", signal: AbortSignal.timeout(10000) });
  const body = await response.json();
  if (!response.ok) throw new Error(body.detail || "Could not obtain a map token.");
  return body.token;
}

async function start() {
  try {
    let initialToken = await getToken();
    window.initMapKit = async () => {
      try {
        mapkit.addEventListener("configuration-error", () => setStatus("Map authorization failed. Check the configured credentials.", true));
        mapkit.addEventListener("configuration-change", (event) => {
          if (["Initialized", "Refreshed"].includes(event.status)) setStatus("Apple Maps connected · Jakarta");
        });
        mapkit.init({ authorizationCallback: async (done) => {
          try {
            const token = initialToken || await getToken();
            initialToken = null;
            done(token);
          } catch (error) { setStatus(error.message, true); }
        } });
        await mapkit.load(["map", "annotations", "overlays"]);
        const map = new mapkit.Map("map", { center: new mapkit.Coordinate(-6.28, 106.78), cameraDistance: 135000 });
        await buildMap(map);
      } catch (error) { setStatus(error.message, true); }
    };
    const script = document.createElement("script");
    script.src = "https://cdn.apple-mapkit.com/mk/6/mapkit.core.js";
    script.crossOrigin = "anonymous";
    script.async = true;
    script.dataset.callback = "initMapKit";
    script.onerror = () => setStatus("Apple Maps SDK could not load. Check your internet connection.", true);
    document.head.append(script);
  } catch (error) { setStatus(error.message, true); }
}

$("retry").addEventListener("click", () => window.location.reload());
start();
