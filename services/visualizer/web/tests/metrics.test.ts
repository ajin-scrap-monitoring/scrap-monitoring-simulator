import assert from "node:assert/strict";
import test from "node:test";
import { VisualMetrics, type QueueDepths } from "../src/metrics.js";

const emptyQueues = (): QueueDepths => ({
  pendingDecode: 0,
  decodeInflight: 0,
  pendingPresent: 0,
});

test("snapshot separates receive, decode and presentation rates", () => {
  let nowMs = 0;
  const metrics = new VisualMetrics(() => nowMs, 5_000);

  metrics.recordReceived();
  nowMs = 100;
  metrics.recordDecoded(12);
  nowMs = 200;
  metrics.recordPresented(0, 0);
  nowMs = 1_000;
  metrics.recordReceived();
  nowMs = 1_100;
  metrics.recordDecoded(8);
  nowMs = 2_000;

  const snapshot = metrics.snapshot(emptyQueues());
  assert.equal("source" in snapshot, false);
  assert.equal(snapshot.network.received_frames, 2);
  assert.equal(snapshot.network.received_fps, 1);
  assert.equal(snapshot.browser.decoded_frames, 2);
  assert.equal(snapshot.browser.decoded_fps, 1);
  assert.equal(snapshot.browser.presented_frames, 1);
  assert.equal(snapshot.browser.presented_fps, 0.5);
  assert.equal(snapshot.browser.decode_duration_ms, 10);
  assert.equal(snapshot.browser.decode_duration_max_ms, 12);
});

test("rates and decode durations use the same bounded window", () => {
  let nowMs = 0;
  const metrics = new VisualMetrics(() => nowMs, 1_000);

  metrics.recordReceived();
  metrics.recordDecoded(40);
  nowMs = 900;
  metrics.recordReceived();
  metrics.recordDecoded(10);
  nowMs = 1_100;

  const snapshot = metrics.snapshot(emptyQueues());
  assert.equal(snapshot.network.received_fps, 1);
  assert.equal(snapshot.browser.decoded_fps, 1);
  assert.equal(snapshot.browser.decode_duration_ms, 10);
  assert.equal(snapshot.browser.decode_duration_max_ms, 10);
});

test("snapshot reports actual drops, errors, reconnects and target mismatches", () => {
  let nowMs = 0;
  const metrics = new VisualMetrics(() => nowMs);

  metrics.recordDroppedBeforeDecode(2);
  metrics.recordDroppedBeforePresent(3);
  nowMs = 5;
  metrics.recordDecodeError(5);
  metrics.recordReconnect();
  nowMs = 10;
  metrics.recordPresented(7, 8);

  const snapshot = metrics.snapshot(emptyQueues());
  assert.equal(snapshot.network.reconnects, 1);
  assert.equal(snapshot.browser.dropped_before_decode, 2);
  assert.equal(snapshot.browser.dropped_before_present, 3);
  assert.equal(snapshot.browser.decode_errors, 1);
  assert.equal(snapshot.browser.target_mismatches, 1);
  assert.equal(snapshot.browser.decode_duration_ms, 5);
});

test("queue maxima retain transient depths while snapshot exposes current depths", () => {
  let nowMs = 0;
  const metrics = new VisualMetrics(() => nowMs);
  metrics.observeQueues({ pendingDecode: 1, decodeInflight: 0, pendingPresent: 0 });
  metrics.observeQueues({ pendingDecode: 0, decodeInflight: 1, pendingPresent: 1 });
  nowMs = 1_000;

  const snapshot = metrics.snapshot(emptyQueues());
  assert.deepEqual(snapshot.queues, {
    pending_decode: 0,
    decode_inflight: 0,
    pending_present: 0,
    max_pending_decode: 1,
    max_decode_inflight: 1,
    max_pending_present: 1,
  });
});

test("queue depths and decode durations reject invalid measurements", () => {
  const metrics = new VisualMetrics(() => 0);

  assert.throws(
    () => metrics.observeQueues({ pendingDecode: -1, decodeInflight: 0, pendingPresent: 0 }),
    /pending decode depth/,
  );
  assert.throws(() => metrics.recordDecoded(Number.NaN), /decode duration/);
  assert.throws(() => metrics.recordDecodeError(-1), /decode duration/);
});
