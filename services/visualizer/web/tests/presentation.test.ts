import assert from "node:assert/strict";
import test from "node:test";
import {
  LatestPacketQueue,
  LatestTargetPresentation,
  type DecodedVisualFrame,
} from "../src/presentation.js";

function candidate(targetId: number, closed: number[] = []): DecodedVisualFrame {
  return {
    frame: {
      metadata: {
        target_id: targetId,
        target_elapsed_s: targetId / 30,
        left_sequence: 1,
        right_sequence: 2,
        alpha: 0.5,
        height_count: 1,
        jpeg_bytes: 1,
        shared: { cycle_index: 0, phase: "filling", target_fill_ratio: 0, surface_fill_ratio: 0, surface_volume_m3: 0, current_inlet_index: null },
      },
      heights: new Float32Array([0]),
      jpeg: new Uint8Array([0]),
    },
    image: {
      close: () => {
        closed.push(targetId);
      },
    } as unknown as ImageBitmap,
    cameraTargetId: targetId,
  };
}

test("latest target replaces queued work without catchup", () => {
  const closed: number[] = [];
  const presentation = new LatestTargetPresentation();
  assert.deepEqual(presentation.offer(candidate(1, closed)), { accepted: true, droppedFrames: 0 });
  assert.deepEqual(presentation.offer(candidate(3, closed)), { accepted: true, droppedFrames: 1 });
  assert.deepEqual(presentation.offer(candidate(2, closed)), { accepted: false, droppedFrames: 1 });
  assert.deepEqual(closed, [1, 2]);
  assert.equal(presentation.size, 1);
  assert.equal(presentation.consume()?.frame.metadata.target_id, 3);
  assert.equal(presentation.size, 0);
  assert.deepEqual(presentation.offer(candidate(2, closed)), { accepted: false, droppedFrames: 1 });
  assert.deepEqual(closed, [1, 2, 2]);
});

test("reset accepts a restarted stream whose target ids begin again", () => {
  const presentation = new LatestTargetPresentation();
  assert.deepEqual(presentation.offer(candidate(20)), { accepted: true, droppedFrames: 0 });
  assert.equal(presentation.consume()?.frame.metadata.target_id, 20);

  assert.equal(presentation.reset(), 0);

  assert.deepEqual(presentation.offer(candidate(0)), { accepted: true, droppedFrames: 0 });
  assert.equal(presentation.consume()?.frame.metadata.target_id, 0);
});

test("reset closes and counts a decoded frame that was not presented", () => {
  const closed: number[] = [];
  const presentation = new LatestTargetPresentation();
  presentation.offer(candidate(4, closed));

  assert.equal(presentation.reset(), 1);
  assert.deepEqual(closed, [4]);
  assert.equal(presentation.size, 0);
});

test("latest packet queue reports replacement and reset drops", () => {
  const queue = new LatestPacketQueue<number>();

  assert.equal(queue.offer(1), 0);
  assert.equal(queue.size, 1);
  assert.equal(queue.offer(2), 1);
  assert.equal(queue.take(), 2);
  assert.equal(queue.size, 0);
  assert.equal(queue.reset(), 0);
  assert.equal(queue.offer(3), 0);
  assert.equal(queue.reset(), 1);
});
