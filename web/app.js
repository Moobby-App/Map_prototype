"use strict";

const $ = (id) => document.getElementById(id);
const ENABLED_SERVICES = ["krl"];
const LIVE_DELAYS = false;
const DEBUG_MODE = new URLSearchParams(globalThis.location?.search || "").get("debug") === "1";
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

function wibClockParts(date = new Date()) {
  const parts = new Intl.DateTimeFormat("en-GB", { timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }).formatToParts(date);
  return Object.fromEntries(parts.filter((part) => part.type !== "literal").map((part) => [part.type, Number(part.value)]));
}

function serviceClockSeconds(date = new Date()) {
  const parts = wibClockParts(date);
  const seconds = parts.hour * 3600 + parts.minute * 60 + parts.second + date.getMilliseconds() / 1000;
  return seconds + (parts.hour < 4 ? 86400 : 0);
}

function setStatus(message, failed = false) {
  $("status").textContent = message;
  $("status").toggleAttribute("data-error", failed);
  $("retry").hidden = !failed;
}

async function loadData(name) {
  for (const base of ["/output", "/data"]) {
    const response = await fetch(`${base}/${name}`, { cache: "no-store", signal: AbortSignal.timeout(15000) });
    if (response.status === 404) continue;
    if (!response.ok) throw new Error(`${base}/${name}: HTTP ${response.status}`);
    return { value: await response.json(), source: base };
  }
  return { missing: true };
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
    const modes = (Array.isArray(row.modes) ? row.modes : [row.mode || "krl"])
      .map((mode) => String(mode).toLowerCase()).filter((mode) => ENABLED_SERVICES.includes(mode));
    if (!modes.length) return;
    stations.set(id, { id, name: row.name, lat: row.lat, lng: row.lng, modes });
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
    const mode = String(row?.mode || "krl").toLowerCase();
    if (!ENABLED_SERVICES.includes(mode)) return;
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
      mode,
      line_id: Number.isInteger(row.line_id) ? row.line_id : null,
      day_type: ["daily", "weekday", "sunday"].includes(row.day_type) ? row.day_type : "daily",
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
    const modes = (Array.isArray(properties.modes) ? properties.modes : ["krl"])
      .map((mode) => String(mode).toLowerCase()).filter((mode) => ENABLED_SERVICES.includes(mode));
    if (!modes.length) return;
    const from = stationIndex.get(String(properties.from));
    const to = stationIndex.get(String(properties.to));
    const coordinates = feature?.geometry?.coordinates;
    if (feature?.geometry?.type !== "LineString" || !from || !to || !Array.isArray(coordinates) || coordinates.length < 2 ||
        !coordinates.every((point) => Array.isArray(point) && validCoordinate(point[1], point[0]))) {
      warnings.push(`Segment ${index + 1} is invalid and was skipped.`);
      return;
    }
    segments.push({
      from,
      to,
      coordinates,
      modes,
      lineIds: Array.isArray(properties.line_ids) ? properties.line_ids.map(Number) : [],
      color: validColor(properties.color, "#4f827c"),
      approximate: properties.approximate === true,
    });
  });
  return segments;
}

function demoData() {
  const stations = [
    { id: "demo:kota", name: "Jakarta Kota", lat: -6.1376, lng: 106.8146, modes: ["krl"] },
    { id: "demo:juanda", name: "Juanda", lat: -6.1667, lng: 106.8304, modes: ["krl"] },
    { id: "demo:gondangdia", name: "Gondangdia", lat: -6.1859, lng: 106.8326, modes: ["krl"] },
    { id: "demo:cikini", name: "Cikini", lat: -6.1989, lng: 106.8413, modes: ["krl"] },
    { id: "demo:manggarai", name: "Manggarai", lat: -6.2098, lng: 106.8502, modes: ["krl"] },
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
    mode: "krl",
    day_type: "daily",
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

const EARTH_RADIUS_M = 6371008.8;
const toRadians = (degrees) => degrees * Math.PI / 180;

function distanceMeters(a, b) {
  const lat1 = toRadians(a[1]), lat2 = toRadians(b[1]);
  const dLat = lat2 - lat1, dLng = toRadians(b[0] - a[0]);
  const haversine = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1, Math.sqrt(haversine)));
}

function lineDistance(coordinates) {
  let total = 0;
  for (let index = 1; index < coordinates.length; index++) total += distanceMeters(coordinates[index - 1], coordinates[index]);
  return total;
}

function samePair(a, b) {
  return a < b ? `${a}\u0000${b}` : `${b}\u0000${a}`;
}

function buildRailNetwork(segments) {
  const direct = new Map(), graphs = new Map();
  for (const segment of segments) {
    const pairKey = samePair(segment.from.id, segment.to.id);
    if (!direct.has(pairKey)) direct.set(pairKey, []);
    const edge = { ...segment, length: lineDistance(segment.coordinates) };
    direct.get(pairKey).push(edge);
    if (edge.approximate) continue;
    for (const mode of edge.modes) for (const lineId of edge.lineIds) {
      const graphKey = `${mode}:${lineId}`;
      if (!graphs.has(graphKey)) graphs.set(graphKey, new Map());
      const graph = graphs.get(graphKey);
      for (const [from, to] of [[edge.from.id, edge.to.id], [edge.to.id, edge.from.id]]) {
        if (!graph.has(from)) graph.set(from, []);
        graph.get(from).push({ to, edge });
      }
    }
  }
  return { direct, graphs, pathCache: new Map() };
}

function orientedCoordinates(edge, fromId) {
  return edge.from.id === fromId ? edge.coordinates : [...edge.coordinates].reverse();
}

function shortestTrackPath(graph, fromId, toId) {
  if (!graph?.has(fromId) || !graph?.has(toId)) return null;
  const distances = new Map([[fromId, 0]]), previous = new Map(), pending = new Set([fromId]);
  while (pending.size) {
    let current = null, currentDistance = Infinity;
    for (const node of pending) {
      const distance = distances.get(node);
      if (distance < currentDistance) { current = node; currentDistance = distance; }
    }
    pending.delete(current);
    if (current === toId) break;
    for (const item of graph.get(current) || []) {
      const candidate = currentDistance + item.edge.length;
      if (candidate < (distances.get(item.to) ?? Infinity)) {
        distances.set(item.to, candidate);
        previous.set(item.to, { from: current, edge: item.edge });
        pending.add(item.to);
      }
    }
  }
  if (!previous.has(toId)) return null;
  const edges = [];
  for (let cursor = toId; cursor !== fromId;) {
    const step = previous.get(cursor);
    if (!step) return null;
    edges.push({ edge: step.edge, from: step.from });
    cursor = step.from;
  }
  edges.reverse();
  return edges;
}

function flattenTrackPieces(pieces) {
  const coordinates = [];
  for (const points of pieces) {
    if (!coordinates.length) coordinates.push(...points);
    else if (points.length && distanceMeters(coordinates.at(-1), points[0]) < 0.5) coordinates.push(...points.slice(1));
    else coordinates.push(...points);
  }
  return coordinates;
}

function tripPairGeometry(network, trip, fromId, toId) {
  const cacheKey = `${trip.mode}:${trip.line_id}:${fromId}>${toId}`;
  if (network.pathCache.has(cacheKey)) return network.pathCache.get(cacheKey);
  const direct = (network.direct.get(samePair(fromId, toId)) || []).filter((edge) =>
    edge.modes.includes(trip.mode) && (trip.line_id == null || edge.lineIds.length === 0 || edge.lineIds.includes(trip.line_id))
  );
  const exact = direct.filter((edge) => !edge.approximate).sort((a, b) => a.length - b.length)[0];
  if (exact) {
    const pieces = [orientedCoordinates(exact, fromId)];
    const result = { pieces, coordinates: pieces[0], approximate: false };
    network.pathCache.set(cacheKey, result);
    return result;
  }
  const connector = direct.find((edge) => edge.approximate);
  if (connector) {
    const pieces = [orientedCoordinates(connector, fromId)];
    const result = { pieces, coordinates: pieces[0], approximate: true };
    network.pathCache.set(cacheKey, result);
    return result;
  }
  const graph = trip.line_id == null ? null : network.graphs.get(`${trip.mode}:${trip.line_id}`);
  const path = shortestTrackPath(graph, fromId, toId);
  const pieces = path ? path.map(({ edge, from }) => orientedCoordinates(edge, from)) : [];
  const result = { pieces, coordinates: flattenTrackPieces(pieces), approximate: false };
  network.pathCache.set(cacheKey, result);
  return result;
}

function buildTripLegs(trips, segments) {
  const network = buildRailNetwork(segments);
  for (const trip of trips) {
    trip.legs = trip.stops.slice(0, -1).map((a, index) => {
      const b = trip.stops[index + 1];
      const gap = b.time - a.time;
      const terminus = index + 1 === trip.stops.length - 1;
      const dwell = terminus ? 0 : Math.min(30, Math.max(0, gap - 10));
      const movingTime = gap - dwell;
      const geometry = tripPairGeometry(network, trip, a.id, b.id);
      if (!geometry.cumulative) {
        geometry.cumulative = [0];
        geometry.parts = [];
        for (const piece of geometry.pieces) for (let point = 1; point < piece.length; point++) {
          const start = geometry.cumulative.at(-1);
          const end = start + distanceMeters(piece[point - 1], piece[point]);
          if (end > start) {
            geometry.parts.push({ a: piece[point - 1], b: piece[point], start, end });
            geometry.cumulative.push(end);
          }
        }
        geometry.length = geometry.cumulative.at(-1) || 0;
      }
      return { a, b, gap, dwell, movingTime, coordinates: geometry.coordinates,
        parts: geometry.parts, cumulative: geometry.cumulative, length: geometry.length, approximate: geometry.approximate };
    });
  }
  return trips;
}

function pointAtDistance(leg, distance) {
  const { parts, cumulative, length } = leg;
  if (!parts.length) return [leg.b.lng, leg.b.lat];
  if (distance <= 0 || length <= 0) return parts[0].a;
  if (distance >= length) return parts.at(-1).b;
  let low = 0, high = parts.length - 1;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (parts[middle].end < distance) low = middle + 1;
    else high = middle;
  }
  const { a, b, start, end } = parts[low];
  const progress = end > start ? (distance - start) / (end - start) : 0;
  return [a[0] + (b[0] - a[0]) * progress, a[1] + (b[1] - a[1]) * progress];
}

function smoothPolylineHeading(leg, distance) {
  if (leg.cumulative.length < 2 || leg.length <= 0) return 0;
  const before = pointAtDistance(leg, Math.max(0, distance - 8));
  const after = pointAtDistance(leg, Math.min(leg.length, distance + 8));
  return bearing({ lng: before[0], lat: before[1] }, { lng: after[0], lat: after[1] });
}

function motionDistance(tau, movingTime, length) {
  if (movingTime <= 0 || length <= 0) return length;
  const acceleration = Math.min(22, 0.35 * movingTime);
  const vmax = length / (movingTime - acceleration);
  let distance;
  if (tau < acceleration) distance = vmax * tau ** 2 / (2 * acceleration);
  else if (tau <= movingTime - acceleration) distance = vmax * acceleration / 2 + vmax * (tau - acceleration);
  else distance = length - vmax * (movingTime - tau) ** 2 / (2 * acceleration);
  return Math.max(0, Math.min(length, distance));
}

function positionAt(trip, time) {
  const stops = trip.stops, last = stops.at(-1);
  if (!stops.length || time < stops[0].time || time >= last.time + 60) return null;
  if (time >= last.time) {
    const finalLeg = trip.legs?.at(-1);
    const coordinate = finalLeg?.length > 0
      ? pointAtDistance(finalLeg, finalLeg.length)
      : [last.lng, last.lat];
    return { coordinate, heading: 0, next: null, eta: 0,
      status: `berhenti di ${last.name}`, stoppedAt: last, approximate: finalLeg?.approximate === true };
  }
  for (const leg of trip.legs || []) {
    const { a, b, movingTime, length } = leg;
    if (time >= b.time) continue;
    const eta = Math.max(0, b.time - time);
    if (movingTime <= 0 || length <= 0) {
      return { coordinate: [b.lng, b.lat], heading: 0, next: b, eta,
        status: `berhenti di ${b.name}`, stoppedAt: b, approximate: leg.approximate };
    }
    const moveEnd = a.time + movingTime;
    if (time >= moveEnd) {
      return { coordinate: pointAtDistance(leg, length), heading: 0, next: b, eta,
        status: `berhenti di ${b.name}`, stoppedAt: b, approximate: leg.approximate };
    }
    const distance = motionDistance(time - a.time, movingTime, length);
    return { coordinate: pointAtDistance(leg, distance), heading: smoothPolylineHeading(leg, distance),
      next: b, eta, status: `bergerak ke ${b.name}`, stoppedAt: null, approximate: leg.approximate };
  }
  return null;
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
  $("detail-kicker").textContent = `${trip.mode.toUpperCase()} · TRAIN DETAILS`;
  $("detail-title").textContent = trip.name;
  $("detail-body").textContent = `Train ${trip.train_id}  ·  ${trip.route}\nDestination: ${trip.dest}`;
  const list = $("detail-list");
  list.replaceChildren();
  if (state?.status) appendDetailLine(list, state.status, state.approximate ? "approx. path" : "");
  if (state?.next) {
    const eta = state.eta <= 60 ? `${Math.ceil(state.eta)} sec` : `${Math.ceil(state.eta / 60)} min`;
    appendDetailLine(list, `Next stop · ${state.next.name}`, `ETA ${eta} · ${formatTime(state.next.time)}`);
  } else {
    appendDetailLine(list, "Service complete", `Final station · ${state?.stoppedAt?.name || trip.dest}`);
  }
  $("details").style.setProperty("--callout-color", trip.color);
  $("details").dataset.type = "train";
}

function updateStationCallout(station, departuresByStation, time) {
  $("details").hidden = false;
  $("detail-kicker").textContent = `${station.modes.map((mode) => mode.toUpperCase()).join(" / ")} · STATION DETAILS`;
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
  const results = await Promise.all([
    loadData("stations.json"), loadData("trips.json"), loadData("segments.geojson"),
  ]);
  const names = ["stations.json", "trips.json", "segments.geojson"];
  const missing = results.slice(0, 3).flatMap((result, index) => result.missing ? [names[index]] : []);
  if (missing.length) {
    const fixture = demoData();
    results[0].value = fixture.stations;
    results[1].value = fixture.trips;
    results[2].value = fixture.segments;
    $("demo-label").hidden = false;
    $("source").textContent = `Demo schedule · ${missing.join(", ")} missing`;
  }
  if (!missing.length) $("source").textContent = "Jadwal KRL";

  const warnings = [];
  const stationIndex = normalizeStations(results[0].value, warnings);
  const tripResult = normalizeTrips(results[1].value, stationIndex, warnings);
  const segments = normalizeSegments(results[2].value, stationIndex, warnings);
  const trips = buildTripLegs(tripResult.trips, segments);
  $("warnings").textContent = warnings.join(" ");
  console.info("[Jakarta Railway] Data loaded", {
    stations: stationIndex.size,
    trips: trips.length,
    tripsSkippedMissingStations: tripResult.missingStationSkips,
    tripsSkippedInvalid: tripResult.invalidSkips,
    segments: segments.length,
    demo: missing.length > 0,
    dataFiles: results.slice(0, 3).map((result) => result.source || "demo"),
  });

  const annotations = new Map();
  let networkAnnotations = [];
  let networkOverlays = [];
  let departuresByStation = new Map();
  let visibleTrips = [];
  let selectedTrip = null;
  let selectedStation = null;
  let now = serviceClockSeconds();
  let liveData = null;
  const liveDelays = new Map();
  const daySelect = $("day-type");
  const weekdayName = new Intl.DateTimeFormat("en-US", { weekday: "short", timeZone: "Asia/Jakarta" }).format(new Date());
  let isSaturday = weekdayName === "Sat";
  let selectedDay = isSaturday ? null : weekdayName === "Sun" ? "sunday" : "weekday";
  if (isSaturday) daySelect.insertBefore(new Option("Pilih jadwal…", ""), daySelect.firstChild);
  daySelect.value = selectedDay || "";
  const saturdayNote = $("saturday-note");
  function updateSaturdayNote() {
    saturdayNote.hidden = !isSaturday;
    if (!isSaturday) return;
    if (!selectedDay) saturdayNote.textContent = "Jadwal Sabtu tidak tersedia. Pilih Hari kerja atau Minggu.";
    else saturdayNote.textContent = `Jadwal Sabtu tidak tersedia. Menampilkan jadwal ${selectedDay === "weekday" ? "Hari kerja" : "Minggu"}.`;
  }
  updateSaturdayNote();

  const debugPanel = $("debug-panel");
  debugPanel.hidden = !DEBUG_MODE;
  let activeDateKey = "";

  function rebuildTripSelection() {
    visibleTrips = selectedDay ? trips.filter((trip) =>
      ENABLED_SERVICES.includes(trip.mode) && (trip.day_type === "daily" || trip.day_type === selectedDay)
    ) : [];
    departuresByStation = new Map();
    for (const station of stationIndex.values()) departuresByStation.set(station.id, []);
    for (const trip of visibleTrips) {
      trip.stops.forEach((stop, index) => {
        departuresByStation.get(stop.id)?.push({ trip, stop, next: trip.stops[index + 1] });
      });
    }
    for (const departures of departuresByStation.values()) {
      departures.sort((a, b) => a.stop.time - b.stop.time || a.trip.train_id.localeCompare(b.trip.train_id));
    }
  }

  function refreshNetwork() {
    networkAnnotations.forEach((annotation) => map.removeAnnotation(annotation));
    networkOverlays.forEach((overlay) => map.removeOverlay(overlay));
    networkAnnotations = [];
    networkOverlays = [];
    for (const station of stationIndex.values()) {
      if (!station.modes.some((mode) => ENABLED_SERVICES.includes(mode))) continue;
      const marker = new mapkit.MarkerAnnotation(new mapkit.Coordinate(station.lat, station.lng), {
        title: station.name,
        subtitle: `KRL · ${station.id}`,
        color: "#e4873b",
        data: { station },
      });
      map.addAnnotation(marker);
      networkAnnotations.push(marker);
    }
    for (const segment of segments) {
      if (!segment.modes.some((mode) => ENABLED_SERVICES.includes(mode))) continue;
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
      networkOverlays.push(overlay);
    }
  }
  rebuildTripSelection();
  refreshNetwork();

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

  daySelect.addEventListener("change", () => {
    selectedDay = daySelect.value || null;
    updateSaturdayNote();
    selectedTrip = null;
    $("details").hidden = true;
    rebuildTripSelection();
    render();
    refreshLiveStatus();
  });
  function activeTrains() {
    const active = [];
    const visibleKeys = new Set(visibleTrips.map((trip) => trip.key));
    for (const [key, entry] of annotations) {
      if (!visibleKeys.has(key)) {
        map.removeAnnotation(entry.annotation);
        annotations.delete(key);
      }
    }
    for (const trip of visibleTrips) {
      const positionTime = now - (LIVE_DELAYS ? liveDelays.get(trip.train_id) || 0 : 0);
      const position = positionAt(trip, positionTime);
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
          const detailTime = now - (LIVE_DELAYS ? liveDelays.get(trip.train_id) || 0 : 0);
          updateTrainCallout(trip, positionAt(trip, detailTime), detailTime);
        });
        entry = { annotation, element, glyph };
        annotations.set(trip.key, entry);
        map.addAnnotation(annotation);
      }
      entry.annotation.coordinate = coordinate(position.coordinate);
      entry.glyph.style.transform = `rotate(${position.heading}deg)`;
      entry.element.setAttribute("aria-label", `${trip.name}, train ${trip.train_id}, next ${position.next?.name || trip.dest}`);
      if (selectedTrip === trip.key) updateTrainCallout(trip, position, positionTime);
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
    $("active-count").textContent = `${active} active`;
    $("counts").textContent = `${networkAnnotations.length} stations · ${networkOverlays.length} track segments · ${active} trains active`;
  }

  let previousRender = -Infinity;
  let lastClockEpochSecond = -1;
  let wibBaseSeconds = now;
  function syncWibClock() {
    const date = new Date();
    const epochSecond = Math.floor(date.getTime() / 1000);
    if (epochSecond !== lastClockEpochSecond) {
      lastClockEpochSecond = epochSecond;
      const parts = wibClockParts(date);
      wibBaseSeconds = parts.hour * 3600 + parts.minute * 60 + parts.second + (parts.hour < 4 ? 86400 : 0);
      const dateKey = new Intl.DateTimeFormat("en-CA", {
        timeZone: "Asia/Jakarta", year: "numeric", month: "2-digit", day: "2-digit",
      }).format(date);
      if (dateKey !== activeDateKey) {
        activeDateKey = dateKey;
        const weekday = new Intl.DateTimeFormat("en-US", { weekday: "short", timeZone: "Asia/Jakarta" }).format(date);
        isSaturday = weekday === "Sat";
        if (isSaturday && !daySelect.querySelector('option[value=""]')) {
          daySelect.insertBefore(new Option("Pilih jadwal…", ""), daySelect.firstChild);
        }
        if (!isSaturday) daySelect.querySelector('option[value=""]')?.remove();
        selectedDay = isSaturday ? null : weekday === "Sun" ? "sunday" : "weekday";
        daySelect.value = selectedDay || "";
        updateSaturdayNote();
        selectedTrip = null;
        $("details").hidden = true;
        rebuildTripSelection();
        refreshLiveStatus();
      }
    }
    now = wibBaseSeconds + date.getMilliseconds() / 1000;
  }

  async function refreshLiveStatus() {
    try {
      const day = selectedDay || "weekday";
      const response = await fetch(`/api/live?day_type=${encodeURIComponent(day)}`, {
        cache: "no-store", signal: AbortSignal.timeout(10000),
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      liveData = await response.json();
      liveDelays.clear();
      if (LIVE_DELAYS && liveData.status === "available") {
        const values = new Map();
        for (const item of liveData.delays || []) {
          if (item.field !== "time_est" || !Number.isFinite(item.delay_seconds)) continue;
          if (!values.has(item.train_id)) values.set(item.train_id, []);
          values.get(item.train_id).push(item.delay_seconds);
        }
        for (const [trainId, samples] of values) {
          samples.sort((a, b) => a - b);
          liveDelays.set(trainId, samples[Math.floor(samples.length / 2)]);
        }
      }
      const status = $("service-status");
      const unreachable = liveData.status === "unreachable";
      const adjusted = liveData.status === "available" && (liveData.delays || []).length > 0;
      status.textContent = unreachable ? "KCI tidak terjangkau" : adjusted ? "Disesuaikan estimasi KCI" : "Terjadwal";
      status.dataset.state = unreachable ? "error" : adjusted ? "adjusted" : "scheduled";
      if (DEBUG_MODE) {
        $("debug-info").textContent = JSON.stringify({
          WIB: formatTime(now, true), LIVE_DELAYS, status: liveData.status,
          updated_at: liveData.updated_at, stations_polled: liveData.stations_polled,
          delays: (liveData.delays || []).length, missing: (liveData.missing || []).length,
          extra: (liveData.extra || []).length,
        }, null, 2);
      }
      render();
    } catch (_error) {
      liveData = { status: "unreachable", delays: [], missing: [], extra: [] };
      liveDelays.clear();
      const status = $("service-status");
      status.textContent = "KCI tidak terjangkau";
      status.dataset.state = "error";
      if (DEBUG_MODE) $("debug-info").textContent = JSON.stringify({ status: "unreachable", LIVE_DELAYS }, null, 2);
    }
  }

  function frame(timestamp) {
    syncWibClock();
    if (timestamp - previousRender >= 1000 / 15) {
      render();
      previousRender = timestamp;
    }
    requestAnimationFrame(frame);
  }
  render();
  refreshLiveStatus();
  window.setInterval(refreshLiveStatus, 30000);
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

if (typeof document !== "undefined") {
  $("retry").addEventListener("click", () => window.location.reload());
  start();
}
