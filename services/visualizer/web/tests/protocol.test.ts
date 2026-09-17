import assert from "node:assert/strict";
import test from "node:test";
import { decodeVisualFrame, parseVisualStreamDescriptor } from "../src/protocol.js";

const descriptor = {
  type: "visual_stream_descriptor",
  version: 1,
  fps: 30,
  model: {
    boundary_xy_m: [[0, 0], [2, 0], [2, 2], [0, 2]],
    floor_z_m: 0,
    top_z_m: 4,
    inlet_positions_xy_m: [[1, 1]],
    surface_grid: { x_coordinates_m: [0, 2], y_coordinates_m: [0, 2] },
    topology: {
      floor: {
        vertices_m: [[0, 0, 0], [2, 0, 0], [2, 2, 0], [0, 2, 0]],
        faces: [[0, 1, 2], [0, 2, 3]],
      },
      walls: {
        vertices_m: [[0, 0, 0], [2, 0, 0], [2, 0, 4], [0, 0, 4]],
        faces: [[0, 1, 2], [0, 2, 3]],
      },
      surface: {
        vertices_xy_m: [[0, 0], [2, 0], [2, 2], [0, 2]],
        faces: [[0, 1, 2], [0, 2, 3]],
        boundary_edges: [[0, 1], [1, 2], [2, 3], [3, 0]],
      },
    },
    background_color: [0.84, 0.85, 0.84],
    materials: {
      floor: { color: [0.25, 0.27, 0.28], metallic: 0.15, roughness: 0.82 },
      wall: { color: [0.52, 0.31, 0.16], metallic: 0.25, roughness: 0.72 },
      scrap: { color: [0.5, 0.52, 0.54], metallic: 0.65, roughness: 0.38 },
    },
  },
};

function packet(metadata: Record<string, unknown>, heights: readonly number[], jpeg: readonly number[]): ArrayBuffer {
  const header = new TextEncoder().encode(JSON.stringify(metadata));
  const result = new ArrayBuffer(4 + header.length + heights.length * 4 + jpeg.length);
  const view = new DataView(result);
  view.setUint32(0, header.length, false);
  new Uint8Array(result, 4, header.length).set(header);
  heights.forEach((height, index) => view.setFloat32(4 + header.length + index * 4, height, true));
  new Uint8Array(result, 4 + header.length + heights.length * 4).set(jpeg);
  return result;
}

test("descriptor defines static model topology", () => {
  const parsed = parseVisualStreamDescriptor(descriptor);
  assert.equal(parsed.model.surface_grid.x_coordinates_m.length * parsed.model.surface_grid.y_coordinates_m.length, 4);
  assert.deepEqual(parsed.model.topology.surface.boundary_edges, [[0, 1], [1, 2], [2, 3], [3, 0]]);
  assert.deepEqual(parsed.model.materials.scrap, {
    color: [0.5, 0.52, 0.54],
    metallic: 0.65,
    roughness: 0.38,
  });
});

test("descriptor rejects material factors outside the PBR unit interval", () => {
  const invalid = structuredClone(descriptor);
  invalid.model.materials.scrap.metallic = 1.1;
  assert.throws(() => parseVisualStreamDescriptor(invalid), /metallic must be in \[0, 1\]/);
});

test("binary visual frame has little-endian heights and exact payload lengths", () => {
  const metadata = {
    target_id: 3,
    target_elapsed_s: 0.1,
    left_sequence: 1,
    right_sequence: 2,
    alpha: 0.5,
    height_count: 4,
    jpeg_bytes: 3,
    shared: {
      cycle_index: 0,
      phase: "filling",
      target_fill_ratio: 0.4,
      surface_fill_ratio: 0.3,
      surface_volume_m3: 1.2,
      current_inlet_index: 1,
    },
  };
  const frame = decodeVisualFrame(packet(metadata, [0.1, 0.2, 0.3, 0.4], [1, 2, 3]), 4);
  assert.deepEqual([...frame.heights].map((height) => height.toFixed(1)), ["0.1", "0.2", "0.3", "0.4"]);
  assert.deepEqual([...frame.jpeg], [1, 2, 3]);
  assert.equal(frame.metadata.target_id, 3);
});

test("binary visual frame rejects a mismatched JPEG length", () => {
  const metadata = {
    target_id: 1,
    target_elapsed_s: 0,
    left_sequence: 0,
    right_sequence: 0,
    alpha: 0,
    height_count: 4,
    jpeg_bytes: 4,
    shared: { cycle_index: 0, phase: "filling", target_fill_ratio: 0, surface_fill_ratio: 0, surface_volume_m3: 0, current_inlet_index: null },
  };
  assert.throws(() => decodeVisualFrame(packet(metadata, [0, 0, 0, 0], [1, 2, 3]), 4), /payload lengths/);
});
