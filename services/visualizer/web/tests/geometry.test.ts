import assert from "node:assert/strict";
import test from "node:test";
import {
  CAMERA_EYE_FACTORS,
  createCameraLayout,
  fitOrthographicCamera,
  guideEndpoint,
  heightTickLevels,
  indexedMeshAreaXY,
  interpolateSurfaceHeight,
  polygonArea,
} from "../src/geometry.js";
import type { Point2, Point3, Triangle } from "../src/protocol.js";

const closeTo = (actual: number, expected: number): void => {
  assert.ok(Math.abs(actual - expected) < 1e-9, `${actual} is not close to ${expected}`);
};

test("clipped concave floor triangles preserve the boundary area", () => {
  const boundary: Point2[] = [[0, 0], [3, 0], [3, 1], [1, 1], [1, 3], [0, 3]];
  const vertices: Point3[] = boundary.map(([x, y]) => [x, y, 0]);
  const faces: Triangle[] = [[0, 1, 2], [0, 2, 3], [0, 3, 4], [0, 4, 5]];
  assert.equal(polygonArea(boundary), 5);
  assert.equal(indexedMeshAreaXY(vertices, faces), 5);
});

test("surface height uses the v <= u diagonal split from the clipped model", () => {
  const x = [0, 2];
  const y = [0, 2];
  const heights = new Float32Array([0, 10, 20, 30]);
  closeTo(interpolateSurfaceHeight(x, y, heights, 1.5, 0.5), 12.5);
  closeTo(interpolateSurfaceHeight(x, y, heights, 0.5, 1.5), 17.5);
  closeTo(interpolateSurfaceHeight(x, y, heights, 1, 1), 15);
});

test("inlet guide endpoint is triangle-interpolated instead of nearest sampled", () => {
  const endpoint = guideEndpoint(
    [0.5, 1.5],
    [0, 2],
    [0, 2],
    new Float32Array([0, 10, 20, 30]),
  );
  assert.deepEqual(endpoint.slice(0, 2), [0.5, 1.5]);
  closeTo(endpoint[2], 17.5);
});

test("oblique camera keeps the fixed eye factors and fits the canvas aspect", () => {
  const boundary: Point2[] = [[0, 0], [4, 0], [4, 5.3], [2.7, 5.3], [1.9, 2.5], [0, 2.5]];
  const layout = createCameraLayout(boundary, 0, 10);
  assert.deepEqual(CAMERA_EYE_FACTORS, { x: 1.7, y: -1.7, z: 1.3 });
  assert.deepEqual(layout.center, [2, 2.65, 5]);
  assert.deepEqual(layout.eye, [19, -14.35, 18]);
  assert.deepEqual(layout.focal, layout.center);
  const wide = fitOrthographicCamera(layout, boundary, 0, 10, 16 / 9);
  const tall = fitOrthographicCamera(layout, boundary, 0, 10, 9 / 16);
  closeTo((wide.right - wide.left) / (wide.top - wide.bottom), 16 / 9);
  closeTo((tall.right - tall.left) / (tall.top - tall.bottom), 9 / 16);
  assert.ok(wide.near > 0 && wide.far > wide.near);
});

test("height scale uses two metre intervals and includes both bounds", () => {
  assert.deepEqual(heightTickLevels(0, 10), [0, 2, 4, 6, 8, 10]);
  assert.deepEqual(heightTickLevels(1, 6), [1, 2, 4, 6]);
});
