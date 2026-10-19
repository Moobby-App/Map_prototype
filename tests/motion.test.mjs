import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("../web/app.js", import.meta.url), "utf8");
const context = { URLSearchParams, console, setTimeout, clearTimeout };
vm.runInNewContext(`${source}\nglobalThis.__motion = { motionDistance, buildTripLegs, positionAt, distanceMeters };`, context);
const { motionDistance, buildTripLegs, positionAt, distanceMeters } = context.__motion;

const length = 3000;
const movingTime = 270;
const acceleration = Math.min(22, 0.35 * movingTime);
const vmax = length / (movingTime - acceleration);
const endLng = length / 111195;

function station(id, lng, time, name = id) {
  return { id, lng, lat: 0, time, name, modes: ["krl"] };
}

function testTrip(stops) {
  return { train_id: "T1", mode: "krl", line_id: 7, stops };
}

test("trapezoid example has expected speed, endpoints, continuity, and monotonic distance", () => {
  assert.equal(acceleration, 22);
  assert.ok(Math.abs(vmax - 12.0967741935) < 1e-9);
  assert.equal(motionDistance(0, movingTime, length), 0);
  assert.equal(motionDistance(movingTime, movingTime, length), length);
  for (const boundary of [acceleration, movingTime - acceleration]) {
    const left = motionDistance(boundary - 1e-7, movingTime, length);
    const at = motionDistance(boundary, movingTime, length);
    const right = motionDistance(boundary + 1e-7, movingTime, length);
    assert.ok(Math.abs(left - at) < 1e-4);
    assert.ok(Math.abs(right - at) < 1e-4);
  }
  let previous = -1;
  for (let tau = 0; tau <= movingTime; tau += 0.25) {
    const distance = motionDistance(tau, movingTime, length);
    assert.ok(distance >= previous, `distance decreased at tau=${tau}`);
    previous = distance;
  }
});

test("builds ordered track geometry and immediately places a mid-segment train", () => {
  const a = station("A", 0, 0, "Origin");
  const mid = station("M", endLng / 2, 100, "Middle");
  const b = station("B", endLng, movingTime, "Terminus");
  const trip = testTrip([a, b]);
  const segments = [
    { from: b, to: mid, coordinates: [[endLng, 0], [endLng / 2, 0]], modes: ["krl"], lineIds: [7], approximate: false },
    { from: mid, to: a, coordinates: [[endLng / 2, 0], [0, 0]], modes: ["krl"], lineIds: [7], approximate: false },
  ];
  buildTripLegs([trip], segments);
  assert.deepEqual(Array.from(trip.legs[0].coordinates), [[0, 0], [endLng / 2, 0], [endLng, 0]]);
  assert.equal(trip.legs[0].approximate, false);
  const expectedDistance = vmax * (acceleration / 2 + 100 - acceleration);
  const state = positionAt(trip, 100);
  assert.ok(state.coordinate[0] > 0, "train should render away from its origin immediately");
  assert.ok(Math.abs(distanceMeters([0, 0], state.coordinate) - expectedDistance) < 1);
});

test("applies dwell at intermediate departure stops and holds the terminus for 60 seconds", () => {
  const a = station("A", 0, 0);
  const b = station("B", endLng / 2, 300);
  const c = station("C", endLng, 600, "End");
  const trip = testTrip([a, b, c]);
  const trackAtB = endLng / 2 + 0.0002;
  const segments = [
    { from: a, to: b, coordinates: [[0, 0], [trackAtB, 0]], modes: ["krl"], lineIds: [7], approximate: false },
    { from: c, to: b, coordinates: [[endLng, 0], [trackAtB, 0]], modes: ["krl"], lineIds: [7], approximate: false },
  ];
  buildTripLegs([trip], segments);
  assert.equal(trip.legs[0].dwell, 30);
  assert.equal(trip.legs[0].movingTime, 270);
  assert.equal(positionAt(trip, 270).status, "berhenti di B");
  assert.equal(positionAt(trip, 299).status, "berhenti di B");
  assert.equal(positionAt(trip, 300).status, "bergerak ke End");
  for (const time of [270, 299, 300]) assert.ok(Math.abs(positionAt(trip, time).coordinate[0] - trackAtB) < 1e-10);
  assert.equal(positionAt(trip, 600).status, "berhenti di End");
  assert.equal(positionAt(trip, 659).status, "berhenti di End");
  assert.equal(positionAt(trip, 660), null);
});

test("zero duration or missing geometry never divides by zero and resolves at stop B", () => {
  assert.equal(motionDistance(0, 0, 100), 100);
  const sameTimeA = station("A0", 0, 0), sameTimeB = station("B0", endLng, 0);
  const zeroTimeTrip = testTrip([sameTimeA, sameTimeB]);
  buildTripLegs([zeroTimeTrip], [
    { from: sameTimeA, to: sameTimeB, coordinates: [[0, 0], [endLng, 0]], modes: ["krl"], lineIds: [7], approximate: false },
  ]);
  assert.deepEqual(Array.from(positionAt(zeroTimeTrip, 0).coordinate), [endLng, 0]);

  const a = station("A", 0, 0), b = station("B", 1, 60);
  const trip = testTrip([a, b]);
  buildTripLegs([trip], []);
  const state = positionAt(trip, 20);
  assert.deepEqual(Array.from(state.coordinate), [1, 0]);
  assert.equal(state.status, "berhenti di B");
});

test("marks an approved connector as approximate path", () => {
  const a = station("A", 0, 0), b = station("B", endLng, movingTime);
  const trip = testTrip([a, b]);
  buildTripLegs([trip], [
    { from: a, to: b, coordinates: [[0, 0], [endLng, 0]], modes: ["krl"], lineIds: [7], approximate: true },
  ]);
  assert.equal(positionAt(trip, 100).approximate, true);
});
