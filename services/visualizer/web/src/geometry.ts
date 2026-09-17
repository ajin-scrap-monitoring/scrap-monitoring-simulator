import type { Edge, Point2, Point3, Triangle } from "./protocol.js";

const EPSILON = 1e-10;
export const CAMERA_EYE_FACTORS = Object.freeze({ x: 1.7, y: -1.7, z: 1.3 });

export interface CameraLayout {
  center: Point3;
  eye: Point3;
  focal: Point3;
  viewUp: Point3;
  forward: Point3;
  right: Point3;
  screenUp: Point3;
  span: number;
}

export interface OrthographicBounds {
  left: number;
  right: number;
  bottom: number;
  top: number;
  near: number;
  far: number;
}

export interface HeightScaleLayout {
  lines: readonly [Point3, Point3][];
  labels: readonly { position: Point3; text: string }[];
}

function subtract(left: Point3, right: Point3): Point3 {
  return [left[0] - right[0], left[1] - right[1], left[2] - right[2]];
}

function dot(left: Point3, right: Point3): number {
  return left[0] * right[0] + left[1] * right[1] + left[2] * right[2];
}

function cross(left: Point3, right: Point3): Point3 {
  return [
    left[1] * right[2] - left[2] * right[1],
    left[2] * right[0] - left[0] * right[2],
    left[0] * right[1] - left[1] * right[0],
  ];
}

function normalize(value: Point3): Point3 {
  const length = Math.hypot(value[0], value[1], value[2]);
  if (length <= EPSILON) {
    throw new Error("vector length must be positive");
  }
  return [value[0] / length, value[1] / length, value[2] / length];
}

export function polygonArea(points: readonly Point2[]): number {
  if (points.length < 3) {
    throw new Error("polygon must contain at least three points");
  }
  let twiceArea = 0;
  for (let index = 0; index < points.length; index += 1) {
    const left = points[index] as Point2;
    const right = points[(index + 1) % points.length] as Point2;
    twiceArea += left[0] * right[1] - right[0] * left[1];
  }
  return Math.abs(twiceArea) / 2;
}

export function indexedMeshAreaXY(vertices: readonly Point3[], faces: readonly Triangle[]): number {
  return faces.reduce((area, face) => {
    const a = vertices[face[0]];
    const b = vertices[face[1]];
    const c = vertices[face[2]];
    if (a === undefined || b === undefined || c === undefined) {
      throw new Error("mesh face references a missing vertex");
    }
    return area + Math.abs(
      (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]),
    ) / 2;
  }, 0);
}

export function uniqueTriangleEdges(faces: readonly Triangle[]): Edge[] {
  const edges = new Map<string, Edge>();
  for (const face of faces) {
    const faceEdges: readonly Edge[] = [
      [face[0], face[1]],
      [face[1], face[2]],
      [face[2], face[0]],
    ];
    for (const [left, right] of faceEdges) {
      const ordered: Edge = left < right ? [left, right] : [right, left];
      edges.set(`${ordered[0]}:${ordered[1]}`, ordered);
    }
  }
  return [...edges.values()];
}

function intervalIndex(coordinates: readonly number[], value: number): number {
  if (coordinates.length < 2 || value < (coordinates[0] ?? 0) - EPSILON || value > (coordinates.at(-1) ?? 0) + EPSILON) {
    throw new Error("surface point lies outside the grid extent");
  }
  let low = 0;
  let high = coordinates.length - 1;
  while (low + 1 < high) {
    const middle = Math.floor((low + high) / 2);
    if ((coordinates[middle] ?? value) <= value) low = middle;
    else high = middle;
  }
  return Math.min(low, coordinates.length - 2);
}

export function interpolateSurfaceHeight(
  xCoordinates: readonly number[],
  yCoordinates: readonly number[],
  heights: Float32Array,
  x: number,
  y: number,
): number {
  const columns = xCoordinates.length;
  const rows = yCoordinates.length;
  if (columns < 2 || rows < 2 || heights.length !== columns * rows) {
    throw new Error("surface grid dimensions do not match its heights");
  }
  const xIndex = intervalIndex(xCoordinates, x);
  const yIndex = intervalIndex(yCoordinates, y);
  const x0 = xCoordinates[xIndex] as number;
  const x1 = xCoordinates[xIndex + 1] as number;
  const y0 = yCoordinates[yIndex] as number;
  const y1 = yCoordinates[yIndex + 1] as number;
  const u = (x - x0) / (x1 - x0);
  const v = (y - y0) / (y1 - y0);
  const lowerLeft = heights[yIndex * columns + xIndex] as number;
  const lowerRight = heights[yIndex * columns + xIndex + 1] as number;
  const upperLeft = heights[(yIndex + 1) * columns + xIndex] as number;
  const upperRight = heights[(yIndex + 1) * columns + xIndex + 1] as number;
  if (v <= u) {
    return lowerLeft + u * (lowerRight - lowerLeft) + v * (upperRight - lowerRight);
  }
  return lowerLeft + u * (upperRight - upperLeft) + v * (upperLeft - lowerLeft);
}

export function surfaceVertexHeights(
  vertices: readonly Point2[],
  xCoordinates: readonly number[],
  yCoordinates: readonly number[],
  heights: Float32Array,
): Float32Array {
  return Float32Array.from(vertices, ([x, y]) => interpolateSurfaceHeight(xCoordinates, yCoordinates, heights, x, y));
}

export function guideEndpoint(
  inlet: Point2,
  xCoordinates: readonly number[],
  yCoordinates: readonly number[],
  heights: Float32Array,
): Point3 {
  return [
    inlet[0],
    inlet[1],
    interpolateSurfaceHeight(xCoordinates, yCoordinates, heights, inlet[0], inlet[1]),
  ];
}

export function createCameraLayout(
  boundary: readonly Point2[],
  floorZ: number,
  topZ: number,
): CameraLayout {
  if (boundary.length < 3 || topZ <= floorZ) {
    throw new Error("camera scene bounds are invalid");
  }
  const xs = boundary.map(([x]) => x);
  const ys = boundary.map(([, y]) => y);
  const center: Point3 = [
    (Math.min(...xs) + Math.max(...xs)) / 2,
    (Math.min(...ys) + Math.max(...ys)) / 2,
    (floorZ + topZ) / 2,
  ];
  const span = Math.max(
    Math.max(...xs) - Math.min(...xs),
    Math.max(...ys) - Math.min(...ys),
    topZ - floorZ,
    1,
  );
  const eye: Point3 = [
    center[0] + CAMERA_EYE_FACTORS.x * span,
    center[1] + CAMERA_EYE_FACTORS.y * span,
    center[2] + CAMERA_EYE_FACTORS.z * span,
  ];
  const viewUp: Point3 = [0, 0, 1];
  const forward = normalize(subtract(center, eye));
  const right = normalize(cross(forward, viewUp));
  const screenUp = normalize(cross(right, forward));
  return { center, eye, focal: center, viewUp, forward, right, screenUp, span };
}

export function fitOrthographicCamera(
  layout: CameraLayout,
  boundary: readonly Point2[],
  floorZ: number,
  topZ: number,
  aspect: number,
): OrthographicBounds {
  if (!Number.isFinite(aspect) || aspect <= 0) {
    throw new Error("camera aspect must be positive");
  }
  const corners = boundary.flatMap(([x, y]) => [
    [x, y, floorZ] as Point3,
    [x, y, topZ] as Point3,
  ]);
  let halfWidth = 0;
  let halfHeight = 0;
  let near = Number.POSITIVE_INFINITY;
  let far = 0;
  for (const point of corners) {
    const focalOffset = subtract(point, layout.focal);
    halfWidth = Math.max(halfWidth, Math.abs(dot(focalOffset, layout.right)));
    halfHeight = Math.max(halfHeight, Math.abs(dot(focalOffset, layout.screenUp)));
    const eyeOffset = subtract(point, layout.eye);
    const depth = dot(eyeOffset, layout.forward);
    near = Math.min(near, depth);
    far = Math.max(far, depth);
  }
  const padding = 1.1;
  halfWidth = Math.max(halfWidth * padding, EPSILON);
  halfHeight = Math.max(halfHeight * padding, EPSILON);
  if (halfWidth / halfHeight < aspect) halfWidth = halfHeight * aspect;
  else halfHeight = halfWidth / aspect;
  const depthPadding = layout.span * 0.25;
  return {
    left: -halfWidth,
    right: halfWidth,
    bottom: -halfHeight,
    top: halfHeight,
    near: Math.max(0.01, near - depthPadding),
    far: far + depthPadding,
  };
}

export function heightTickLevels(floorZ: number, topZ: number, interval = 2): number[] {
  if (!Number.isFinite(floorZ) || !Number.isFinite(topZ) || topZ <= floorZ || interval <= 0) {
    throw new Error("height scale bounds are invalid");
  }
  const levels = [floorZ];
  let level = Math.ceil(floorZ / interval) * interval;
  if (Math.abs(level - floorZ) <= EPSILON) level += interval;
  while (level < topZ - EPSILON) {
    levels.push(level);
    level += interval;
  }
  if (Math.abs((levels.at(-1) ?? floorZ) - topZ) > EPSILON) levels.push(topZ);
  return levels;
}

export function createHeightScale(
  boundary: readonly Point2[],
  floorZ: number,
  topZ: number,
  layout: CameraLayout,
): HeightScaleLayout {
  const rightX = layout.right[0];
  const rightY = layout.right[1];
  const edge = [...boundary].sort((left, right) => {
    const delta = right[0] * rightX + right[1] * rightY - (left[0] * rightX + left[1] * rightY);
    return Math.abs(delta) > EPSILON ? delta : right[0] - left[0] || right[1] - left[1];
  })[0] as Point2;
  const tickLength = 0.025 * layout.span;
  const labelOffset = 2.2 * tickLength;
  const levels = heightTickLevels(floorZ, topZ);
  const lines: [Point3, Point3][] = [
    [[edge[0], edge[1], floorZ], [edge[0], edge[1], topZ]],
  ];
  const labels: { position: Point3; text: string }[] = [];
  for (const level of levels) {
    lines.push([
      [edge[0], edge[1], level],
      [edge[0] + rightX * tickLength, edge[1] + rightY * tickLength, level],
    ]);
    labels.push({
      position: [edge[0] + rightX * labelOffset, edge[1] + rightY * labelOffset, level],
      text: `${Number((Math.abs(level) <= EPSILON ? 0 : level).toFixed(6))} m`,
    });
  }
  return { lines, labels };
}
