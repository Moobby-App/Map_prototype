import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';
import { parseTime, measured, sample, segmentPath, position } from '../web/simulation.js';

test('service-day clock supports midnight and rejects invalid input', () => {
  assert.equal(parseTime('00:12'), 720);
  assert.equal(parseTime('24:12'), 87120);
  assert.equal(parseTime('25:30'), 91800);
  assert.ok(Number.isNaN(parseTime('06:70')));
});
test('distance-based interpolation follows a bent railway in both directions', () => {
  const a = [106.8, -6.2], corner = [106.8, -6.19], b = [106.81, -6.19];
  const path = measured([a, corner, b]);
  const route = [{ name: 'Test', path }];
  const forward = segmentPath(a, b, route, 'Test');
  assert.equal(forward.fallback, false);
  const midpoint = sample(path, path.total / 2);
  assert.ok(Math.abs(midpoint.coordinate[0] - a[0]) < .0001);
  const reverse = segmentPath(b, a, route, 'Test');
  const trip = { stops: [{ arrival: 100, departure: 110, lng: b[0], lat: b[1] }, { arrival: 200, departure: 210, lng: a[0], lat: a[1] }], segments: [reverse] };
  assert.equal(position(trip, 99), null);
  assert.deepEqual(position(trip, 105).coordinate, b);
  assert.equal(position(trip, 105).dwelling, true);
  assert.equal(segmentPath(a, b, route, 'Other line').fallback, true);
  assert.ok(Math.abs(position(trip, 100).coordinate[0] - b[0]) < 1e-8);
  assert.ok(Math.abs(position(trip, 200).coordinate[0] - a[0]) < 1e-8);
  assert.equal(position(trip, 211), null);
  assert.equal(segmentPath(a, b, [], '').fallback, true);
});

test('demo map integration: trains, controls, selection and scrubbing', async () => {
  class Element {
    constructor() { this.dataset = {}; this.style = { setProperty() {} }; this.events = {}; this.hidden = false; this.value = ''; }
    addEventListener(name, handler) { this.events[name] = handler; }
    setAttribute(name, value) { this[name] = value; }
    append() {}
  }
  const elements = new Map();
  const get = (id) => { if (!elements.has(id)) elements.set(id, new Element()); return elements.get(id); };
  let frame;
  class Annotation {
    constructor(coord, factory, options) { this.coordinate = coord; Object.assign(this, options); this.element = factory(); }
  }
  class Marker { constructor(coord, options) { this.coordinate = coord; Object.assign(this, options); } }
  const context = vm.createContext({
    console, parseTime, measured, segmentPath, position,
    document: { getElementById: get, createElement: () => new Element(), addEventListener() {}, hidden: false },
    mapkit: { Coordinate: class { constructor(latitude, longitude) { Object.assign(this, { latitude, longitude }); } }, MarkerAnnotation: Marker, Annotation,
      PolylineOverlay: class { constructor(points, options) { Object.assign(this, { points, options }); } }, Style: class { constructor(options) { Object.assign(this, options); } } },
    fetch: async () => ({ status: 404 }), AbortSignal, matchMedia: () => ({ matches: false }),
    requestAnimationFrame: (callback) => { frame = callback; },
  });
  const source = (await readFile(new URL('../web/app.js', import.meta.url), 'utf8')).replace(/^import .*;$/m, '').replace(/start\(\);\s*$/, '');
  vm.runInContext(source, context);
  const map = { annotations: [], overlays: [], rotation: 0,
    addAnnotation(a) { this.annotations.push(a); }, removeAnnotation(a) { this.annotations = this.annotations.filter(x => x !== a); },
    addOverlay(o) { this.overlays.push(o); }, addEventListener(name, handler) { this[name] = handler; } };
  context.map = map;
  await vm.runInContext('buildRailway(map)', context);
  assert.equal(map.overlays.length, 1);
  assert.equal(map.annotations.filter(a => a.data.station).length, 5);
  const trains = () => map.annotations.filter(a => a.data.tripKey);
  assert.equal(trains().length, 3);
  assert.match(get('source').textContent, /DEMO/);
  assert.match(get('warnings').textContent, /belum tersedia/);
  const normalized = vm.runInContext(`normalizeTrips([{train_id:'N',line:'Unknown',stops:[{station_id:'A',arrival:'23:58',departure:'23:59'},{station_id:'B',arrival:'00:05',departure:'00:06'}]}], new Map([['A',{lng:106.8,lat:-6.2}],['B',{lng:106.81,lat:-6.19}]]), []).trips[0]`, context);
  assert.equal(normalized.stops[1].arrival, 86700);
  assert.equal(normalized.stops[1].departure, 86760);
  assert.equal(normalized.segments[0].fallback, true);
  assert.equal(vm.runInContext(`normalizeStations([{sta_id:'A'}], []).size`, context), 0);
  const initial = trains()[0].coordinate;
  frame(0);
  const beforeThrottle = trains()[0].coordinate;
  frame(10); frame(20);
  assert.equal(trains()[0].coordinate, beforeThrottle);
  frame(1000);
  assert.notEqual(trains()[0].coordinate.latitude, initial.latitude);
  get('play').events.click();
  const paused = trains()[0].coordinate.latitude;
  frame(2000);
  assert.equal(trains()[0].coordinate.latitude, paused);
  trains()[0].element.events.click();
  assert.match(get('detail-title').textContent, /DEMO-01/);
  assert.match(get('detail-body').textContent, /Next stop/);
  map.select({ annotation: map.annotations.find(a => a.data.station) });
  assert.equal(get('detail-title').textContent, 'Jakarta Kota');
  get('time').value = 40000; get('time').events.input();
  assert.equal(trains().length, 0);
  get('time').value = 21600; get('time').events.input();
  assert.equal(trains().length, 3);
});
