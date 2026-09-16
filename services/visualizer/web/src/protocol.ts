export interface SceneModelDescriptor {
  boundary_xy_m: readonly [number, number][];
  floor_z_m: number;
  top_z_m: number;
  inlet_positions_xy_m: readonly [number, number][];
  surface: {
    x_coordinates_m: readonly number[];
    y_coordinates_m: readonly number[];
  };
  palette: {
    background: readonly number[];
    wall: readonly number[];
    scrap: readonly number[];
    guide: readonly number[];
  };
}

export interface VisualStreamDescriptor {
  type: "visual_stream_descriptor";
  version: 1;
  fps: 30;
  model: SceneModelDescriptor;
}

export interface SharedStats {
  cycle_index: number;
  phase: string;
  target_fill_ratio: number;
  surface_fill_ratio: number;
  surface_volume_m3: number;
  current_inlet_index: number | null;
}

export interface VisualFrameMetadata {
  target_id: number;
  target_elapsed_s: number;
  left_sequence: number;
  right_sequence: number;
  alpha: number;
  height_count: number;
  jpeg_bytes: number;
  shared: SharedStats;
}

export interface VisualFrame {
  metadata: VisualFrameMetadata;
  heights: Float32Array;
  jpeg: Uint8Array;
}

const decoder = new TextDecoder("utf-8", { fatal: true });

function asRecord(value: unknown, label: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function finiteNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new Error(`${label} must be a finite number`);
  }
  return value;
}

function nonNegativeInteger(value: unknown, label: string): number {
  const result = finiteNumber(value, label);
  if (!Number.isSafeInteger(result) || result < 0) {
    throw new Error(`${label} must be a non-negative safe integer`);
  }
  return result;
}

function unitInterval(value: unknown, label: string): number {
  const result = finiteNumber(value, label);
  if (result < 0 || result > 1) {
    throw new Error(`${label} must be in [0, 1]`);
  }
  return result;
}

function sharedStats(value: unknown): SharedStats {
  const source = asRecord(value, "shared");
  const inlet = source.current_inlet_index;
  if (inlet !== null && inlet !== undefined) {
    nonNegativeInteger(inlet, "shared.current_inlet_index");
  }
  if (typeof source.phase !== "string" || source.phase.length === 0) {
    throw new Error("shared.phase must be a non-empty string");
  }
  return {
    cycle_index: nonNegativeInteger(source.cycle_index, "shared.cycle_index"),
    phase: source.phase,
    target_fill_ratio: unitInterval(source.target_fill_ratio, "shared.target_fill_ratio"),
    surface_fill_ratio: unitInterval(source.surface_fill_ratio, "shared.surface_fill_ratio"),
    surface_volume_m3: (() => {
      const volume = finiteNumber(source.surface_volume_m3, "shared.surface_volume_m3");
      if (volume < 0) {
        throw new Error("shared.surface_volume_m3 must not be negative");
      }
      return volume;
    })(),
    current_inlet_index: inlet === null || inlet === undefined
      ? null
      : nonNegativeInteger(inlet, "shared.current_inlet_index"),
  };
}

export function parseVisualStreamDescriptor(value: unknown): VisualStreamDescriptor {
  const source = asRecord(value, "descriptor");
  if (source.type !== "visual_stream_descriptor" || source.version !== 1 || source.fps !== 30) {
    throw new Error("unsupported visual stream descriptor");
  }
  const model = asRecord(source.model, "descriptor.model");
  const surface = asRecord(model.surface, "descriptor.model.surface");
  const coordinates = (key: string): number[] => {
    const value = surface[key];
    if (!Array.isArray(value) || value.length < 2) {
      throw new Error(`descriptor.model.surface.${key} must have at least two entries`);
    }
    return value.map((entry, index) => finiteNumber(entry, `descriptor.model.surface.${key}[${index}]`));
  };
  const pairs = (key: string, minLength: number): [number, number][] => {
    const value = model[key];
    if (!Array.isArray(value) || value.length < minLength) {
      throw new Error(`descriptor.model.${key} is invalid`);
    }
    return value.map((entry, index) => {
      if (!Array.isArray(entry) || entry.length !== 2) {
        throw new Error(`descriptor.model.${key}[${index}] is invalid`);
      }
      return [finiteNumber(entry[0], `${key}[${index}][0]`), finiteNumber(entry[1], `${key}[${index}][1]`)] as [number, number];
    });
  };
  const floorZ = finiteNumber(model.floor_z_m, "descriptor.model.floor_z_m");
  const topZ = finiteNumber(model.top_z_m, "descriptor.model.top_z_m");
  if (topZ <= floorZ) {
    throw new Error("descriptor.model.top_z_m must be above floor_z_m");
  }
  const paletteSource = asRecord(model.palette, "descriptor.model.palette");
  const color = (key: string): number[] => {
    const value = paletteSource[key];
    if (!Array.isArray(value) || value.length !== 3) {
      throw new Error(`descriptor.model.palette.${key} must be an RGB triplet`);
    }
    return value.map((entry, index) => {
      const component = finiteNumber(entry, `descriptor.model.palette.${key}[${index}]`);
      if (component < 0 || component > 1) {
        throw new Error(`descriptor.model.palette.${key}[${index}] must be in [0, 1]`);
      }
      return component;
    });
  };
  return {
    type: "visual_stream_descriptor",
    version: 1,
    fps: 30,
    model: {
      boundary_xy_m: pairs("boundary_xy_m", 3),
      floor_z_m: floorZ,
      top_z_m: topZ,
      inlet_positions_xy_m: pairs("inlet_positions_xy_m", 1),
      surface: {
        x_coordinates_m: coordinates("x_coordinates_m"),
        y_coordinates_m: coordinates("y_coordinates_m"),
      },
      palette: {
        background: color("background"),
        wall: color("wall"),
        scrap: color("scrap"),
        guide: color("guide"),
      },
    },
  };
}

export function decodeVisualFrame(packet: ArrayBuffer, expectedHeightCount: number): VisualFrame {
  if (!Number.isSafeInteger(expectedHeightCount) || expectedHeightCount < 1) {
    throw new Error("expectedHeightCount must be a positive safe integer");
  }
  if (packet.byteLength < 4) {
    throw new Error("visual frame is shorter than the header length");
  }
  const view = new DataView(packet);
  const metadataLength = view.getUint32(0, false);
  const metadataStart = 4;
  const metadataEnd = metadataStart + metadataLength;
  if (metadataEnd > packet.byteLength) {
    throw new Error("visual frame metadata exceeds packet length");
  }
  let rawMetadata: unknown;
  try {
    rawMetadata = JSON.parse(decoder.decode(new Uint8Array(packet, metadataStart, metadataLength)));
  } catch (error) {
    throw new Error(`visual frame metadata is invalid: ${String(error)}`);
  }
  const source = asRecord(rawMetadata, "metadata");
  const metadata: VisualFrameMetadata = {
    target_id: nonNegativeInteger(source.target_id, "metadata.target_id"),
    target_elapsed_s: finiteNumber(source.target_elapsed_s, "metadata.target_elapsed_s"),
    left_sequence: nonNegativeInteger(source.left_sequence, "metadata.left_sequence"),
    right_sequence: nonNegativeInteger(source.right_sequence, "metadata.right_sequence"),
    alpha: unitInterval(source.alpha, "metadata.alpha"),
    height_count: nonNegativeInteger(source.height_count, "metadata.height_count"),
    jpeg_bytes: nonNegativeInteger(source.jpeg_bytes, "metadata.jpeg_bytes"),
    shared: sharedStats(source.shared),
  };
  if (metadata.target_elapsed_s < 0) {
    throw new Error("metadata.target_elapsed_s must not be negative");
  }
  if (metadata.right_sequence < metadata.left_sequence) {
    throw new Error("metadata right sequence precedes left sequence");
  }
  if (metadata.height_count !== expectedHeightCount) {
    throw new Error("visual frame height count does not match the descriptor");
  }
  const heightBytes = metadata.height_count * Float32Array.BYTES_PER_ELEMENT;
  const heightStart = metadataEnd;
  const jpegStart = heightStart + heightBytes;
  if (jpegStart + metadata.jpeg_bytes !== packet.byteLength) {
    throw new Error("visual frame payload lengths do not match packet length");
  }
  const heights = new Float32Array(metadata.height_count);
  for (let index = 0; index < metadata.height_count; index += 1) {
    heights[index] = view.getFloat32(heightStart + index * Float32Array.BYTES_PER_ELEMENT, true);
    if (!Number.isFinite(heights[index])) {
      throw new Error("visual frame contains a non-finite surface height");
    }
  }
  return {
    metadata,
    heights,
    jpeg: new Uint8Array(packet.slice(jpegStart)),
  };
}
