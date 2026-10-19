// Pure geometry and service-day helpers, shared with offline Node tests.
export function parseTime(value) {
  const m = /^(\d{1,2}):([0-5]\d)(?::([0-5]\d))?$/.exec(String(value));
  if (!m || +m[1] > 47) return NaN;
  const seconds = +m[1] * 3600 + +m[2] * 60 + +(m[3] || 0);
  return seconds;
}
export const distance = (a, b) => Math.hypot((a[0] - b[0]) * Math.cos((a[1] + b[1]) * Math.PI / 360), a[1] - b[1]) * 111195;
export function measured(points) {
  const lengths = [0];
  for (let i = 1; i < points.length; i++) lengths.push(lengths[i - 1] + distance(points[i - 1], points[i]));
  return { points, lengths, total: lengths.at(-1) };
}
export function sample(path, offset) {
  const { points, lengths, total } = path;
  offset = Math.max(0, Math.min(total, offset));
  let i = 1;
  while (i < lengths.length - 1 && lengths[i] < offset) i++;
  const a = points[i - 1], b = points[i];
  const p = (offset - lengths[i - 1]) / (lengths[i] - lengths[i - 1] || 1);
  const coordinate = [a[0] + (b[0] - a[0]) * p, a[1] + (b[1] - a[1]) * p];
  const heading = Math.atan2((b[0] - a[0]) * Math.cos(a[1] * Math.PI / 180), b[1] - a[1]) * 180 / Math.PI;
  return { coordinate, heading };
}
function project(path, point) {
  let best = { error: Infinity, offset: 0 };
  const scale = Math.cos(point[1] * Math.PI / 180);
  for (let i = 1; i < path.points.length; i++) {
    const a = path.points[i - 1], b = path.points[i];
    const dx = (b[0] - a[0]) * scale, dy = b[1] - a[1];
    const p = Math.max(0, Math.min(1, (((point[0] - a[0]) * scale) * dx + (point[1] - a[1]) * dy) / (dx * dx + dy * dy || 1)));
    const q = [a[0] + (b[0] - a[0]) * p, a[1] + (b[1] - a[1]) * p];
    const error = distance(point, q);
    if (error < best.error) best = { error, offset: path.lengths[i - 1] + p * (path.lengths[i] - path.lengths[i - 1]) };
  }
  return best;
}
export function segmentPath(a, b, routes, line) {
  const direct = distance(a, b);
  let best;
  const key = String(line || "").trim().toLowerCase();
  for (const route of routes) {
    const keys = route.keys || [String(route.name).trim().toLowerCase()];
    if (!key || !keys.includes(key)) continue;
    const start = project(route.path, a), end = project(route.path, b);
    const length = Math.abs(end.offset - start.offset);
    if (start.error > 750 || end.error > 750 || length < direct * .7 || length > direct * 4 + 1000) continue;
    const match = line && String(route.name).toLowerCase().includes(String(line).toLowerCase());
    const score = start.error + end.error + length * .015 - (match ? 300 : 0);
    if (!best || score < best.score) best = { path: route.path, start: start.offset, end: end.offset, score, fallback: false };
  }
  return best || { path: measured([a, b]), start: 0, end: direct, fallback: true };
}
export function position(trip, t) {
  const stops = trip.stops;
  if (t < stops[0].arrival || t > stops.at(-1).departure) return null;
  for (let i = 0; i < stops.length; i++) {
    const a = stops[i];
    if (t >= a.arrival && t <= a.departure) {
      const segment = trip.segments[Math.min(i, trip.segments.length - 1)];
      const offset = i === stops.length - 1 ? segment.end : segment.start;
      const tangent = sample(segment.path, offset);
      return { coordinate: [a.lng, a.lat], heading: tangent.heading + (segment.end < segment.start ? 180 : 0), next: stops[i + 1] || a, fallback: segment.fallback, dwelling: true };
    }
    const b = stops[i + 1];
    if (b && t > a.departure && t < b.arrival) {
      const segment = trip.segments[i];
      const p = (t - a.departure) / (b.arrival - a.departure);
      const result = sample(segment.path, segment.start + (segment.end - segment.start) * p);
      return { ...result, heading: result.heading + (segment.end < segment.start ? 180 : 0), next: b, fallback: segment.fallback, dwelling: false };
    }
  }
  return null;
}
