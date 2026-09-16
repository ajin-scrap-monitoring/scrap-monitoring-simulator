import assert from "node:assert/strict";
import test from "node:test";
import { LatestTargetPresentation, type DecodedVisualFrame } from "../src/presentation.js";

function candidate(targetId: number): DecodedVisualFrame {
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
    image: { close: () => undefined } as ImageBitmap,
  };
}

test("latest target replaces queued work without catchup", () => {
  const presentation = new LatestTargetPresentation();
  assert.equal(presentation.offer(candidate(1)), true);
  assert.equal(presentation.offer(candidate(3)), true);
  assert.equal(presentation.offer(candidate(2)), false);
  assert.equal(presentation.consume()?.frame.metadata.target_id, 3);
  assert.equal(presentation.offer(candidate(2)), false);
});
