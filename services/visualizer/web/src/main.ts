import { LatestTargetPresentation, type DecodedVisualFrame } from "./presentation.js";
import { decodeVisualFrame, parseVisualStreamDescriptor, type SharedStats, type VisualStreamDescriptor } from "./protocol.js";
import { ScrapScene } from "./scene.js";

interface CameraPainter {
  paint(image: ImageBitmap): void;
}

class BitmapRendererPainter implements CameraPainter {
  constructor(private readonly context: ImageBitmapRenderingContext) {}

  paint(image: ImageBitmap): void {
    this.context.transferFromImageBitmap(image);
  }
}

class CanvasPainter implements CameraPainter {
  constructor(private readonly context: CanvasRenderingContext2D, private readonly canvas: HTMLCanvasElement) {}

  paint(image: ImageBitmap): void {
    this.context.drawImage(image, 0, 0, this.canvas.width, this.canvas.height);
    image.close();
  }
}

function cameraPainter(canvas: HTMLCanvasElement): CameraPainter {
  const bitmap = canvas.getContext("bitmaprenderer");
  if (bitmap !== null) {
    return new BitmapRendererPainter(bitmap);
  }
  const fallback = canvas.getContext("2d");
  if (fallback === null) {
    throw new Error("camera canvas context is unavailable");
  }
  return new CanvasPainter(fallback, canvas);
}

function streamUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/visual/v1/stream`;
}

function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

function displaySharedStats(stats: SharedStats): void {
  const values: Record<string, string> = {
    "cycle-value": String(stats.cycle_index + 1),
    "phase-value": stats.phase === "filling" ? "적재" : "수거",
    "target-fill-value": formatPercent(stats.target_fill_ratio),
    "surface-fill-value": formatPercent(stats.surface_fill_ratio),
    "volume-value": stats.surface_volume_m3.toFixed(1),
    "inlet-value": stats.current_inlet_index === null ? "없음" : String(stats.current_inlet_index + 1),
  };
  for (const [id, value] of Object.entries(values)) {
    const element = document.getElementById(id);
    if (element !== null) {
      element.textContent = value;
    }
  }
}

interface VisualMetrics {
  presentedTarget: number;
  receivedTarget: number;
  reconnects: number;
  receivedFrames: number;
  presentedFrames: number;
  decodeErrors: number;
  targetMismatches: number;
}

interface QueueMetrics {
  pendingDecode: number;
  decodeInflight: number;
  pendingPresent: number;
}

function exposeMetrics(
  queueMetrics: () => QueueMetrics,
): { metrics: VisualMetrics; record: (kind: "received" | "presented") => void } {
  const metrics: VisualMetrics = {
    presentedTarget: -1,
    receivedTarget: -1,
    reconnects: 0,
    receivedFrames: 0,
    presentedFrames: 0,
    decodeErrors: 0,
    targetMismatches: 0,
  };
  const windowMs = 5_000;
  const received: number[] = [];
  const presented: number[] = [];
  const rate = (samples: number[], now: number): number => {
    while (samples.length > 0 && (samples[0] ?? now) < now - windowMs) samples.shift();
    if (samples.length < 2) return 0;
    return (samples.length - 1) * 1_000 / ((samples[samples.length - 1] ?? now) - (samples[0] ?? now));
  };
  const record = (kind: "received" | "presented"): void => {
    const now = performance.now();
    const samples = kind === "received" ? received : presented;
    samples.push(now);
    if (kind === "received") metrics.receivedFrames += 1;
    else metrics.presentedFrames += 1;
  };
  const api = {
    snapshot: () => {
      const now = performance.now();
      const receivedFps = rate(received, now);
      return {
        version: 1,
        sampled_at_ms: now,
        window_s: windowMs / 1_000,
        source: { rendered_fps: receivedFps },
        network: { received_frames: metrics.receivedFrames, received_fps: receivedFps },
        browser: {
          presented_frames: metrics.presentedFrames,
          presented_fps: rate(presented, now),
          dropped_before_decode: 0,
          dropped_before_present: 0,
          decode_errors: metrics.decodeErrors,
          target_mismatches: metrics.targetMismatches,
        },
        queues: {
          pending_decode: queueMetrics().pendingDecode,
          decode_inflight: queueMetrics().decodeInflight,
          pending_present: queueMetrics().pendingPresent,
        },
      };
    },
  };
  Object.defineProperty(window, "__scrapVisualMetrics", { configurable: true, value: api });
  Object.defineProperty(window, "__scrapCameraMetrics", { configurable: true, value: api });
  return { metrics, record };
}

function start(): void {
  const modelCanvas = document.querySelector<HTMLCanvasElement>("#model-canvas");
  const cameraCanvas = document.querySelector<HTMLCanvasElement>("#camera-canvas");
  if (modelCanvas === null || cameraCanvas === null) {
    throw new Error("visual canvas is unavailable");
  }
  const painter = cameraPainter(cameraCanvas);
  const latest = new LatestTargetPresentation();
  let pendingPacket: ArrayBuffer | undefined;
  let decodeInflight = false;
  const exposedMetrics = exposeMetrics(() => ({
    pendingDecode: pendingPacket === undefined ? 0 : 1,
    decodeInflight: decodeInflight ? 1 : 0,
    pendingPresent: latest.pendingTargetId === undefined ? 0 : 1,
  }));
  const metrics = exposedMetrics.metrics;
  let descriptor: VisualStreamDescriptor | undefined;
  let scene: ScrapScene | undefined;
  let renderQueued = false;
  let reconnectDelayMs = 250;
  let streamGeneration = 0;

  const render = (): void => {
    renderQueued = false;
    const paired = latest.consume();
    if (paired === undefined || scene === undefined) {
      return;
    }
    scene.updateHeights(paired.frame.heights);
    scene.render();
    painter.paint(paired.image);
    metrics.presentedTarget = paired.frame.metadata.target_id;
    exposedMetrics.record("presented");
    displaySharedStats(paired.frame.metadata.shared);
  };
  const scheduleRender = (): void => {
    if (!renderQueued) {
      renderQueued = true;
      window.requestAnimationFrame(render);
    }
  };
  const decodeLatest = async (): Promise<void> => {
    if (decodeInflight || pendingPacket === undefined || descriptor === undefined) {
      return;
    }
    const packet = pendingPacket;
    pendingPacket = undefined;
    const generation = streamGeneration;
    decodeInflight = true;
    if (descriptor === undefined) {
      decodeInflight = false;
      return;
    }
    try {
      const heightCount = descriptor.model.surface.x_coordinates_m.length * descriptor.model.surface.y_coordinates_m.length;
      const frame = decodeVisualFrame(packet, heightCount);
      metrics.receivedTarget = Math.max(metrics.receivedTarget, frame.metadata.target_id);
      const image = await createImageBitmap(new Blob([frame.jpeg.buffer as ArrayBuffer], { type: "image/jpeg" }));
      if (generation !== streamGeneration) {
        image.close();
        return;
      }
      const decoded: DecodedVisualFrame = { frame, image };
      if (latest.offer(decoded)) {
        scheduleRender();
      }
    } catch {
      metrics.decodeErrors += 1;
    } finally {
      decodeInflight = false;
      void decodeLatest().catch(() => undefined);
    }
  };
  const connect = (): void => {
    const socket = new WebSocket(streamUrl());
    socket.binaryType = "arraybuffer";
    socket.onmessage = (event: MessageEvent<string | ArrayBuffer>) => {
      if (typeof event.data === "string") {
        try {
          descriptor = parseVisualStreamDescriptor(JSON.parse(event.data));
          scene = new ScrapScene(modelCanvas, descriptor.model);
        } catch (error) {
          socket.close(1002, String(error));
        }
        return;
      }
      pendingPacket = event.data;
      exposedMetrics.record("received");
      void decodeLatest().catch(() => undefined);
    };
    socket.onopen = () => {
      reconnectDelayMs = 250;
    };
    socket.onclose = () => {
      latest.reset();
      pendingPacket = undefined;
      streamGeneration += 1;
      metrics.reconnects += 1;
      const delay = reconnectDelayMs;
      reconnectDelayMs = Math.min(reconnectDelayMs * 2, 5_000);
      window.setTimeout(connect, delay);
    };
    socket.onerror = () => socket.close();
  };
  connect();
}

start();
