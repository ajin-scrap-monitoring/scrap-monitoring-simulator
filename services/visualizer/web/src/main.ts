import { VisualMetrics, type QueueDepths } from "./metrics.js";
import { LatestPacketQueue, LatestTargetPresentation, type DecodedVisualFrame } from "./presentation.js";
import { ForegroundVisualStream, shouldMaintainVisualStream } from "./foreground.js";
import { cameraBitmapOptions } from "./bitmap.js";
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

function exposeMetrics(metrics: VisualMetrics, queueDepths: () => QueueDepths): void {
  const api = {
    snapshot: () => metrics.snapshot(queueDepths()),
  };
  Object.defineProperty(window, "__scrapVisualMetrics", { configurable: true, value: api });
  Object.defineProperty(window, "__scrapCameraMetrics", { configurable: true, value: api });
}

function start(): void {
  const modelCanvas = document.querySelector<HTMLCanvasElement>("#model-canvas");
  const cameraCanvas = document.querySelector<HTMLCanvasElement>("#camera-canvas");
  if (modelCanvas === null || cameraCanvas === null) {
    throw new Error("visual canvas is unavailable");
  }
  const painter = cameraPainter(cameraCanvas);
  const pendingPackets = new LatestPacketQueue<ArrayBuffer>();
  const latest = new LatestTargetPresentation();
  let decodeInflight = false;
  const queueDepths = (): QueueDepths => ({
    pendingDecode: pendingPackets.size,
    decodeInflight: decodeInflight ? 1 : 0,
    pendingPresent: latest.size,
  });
  const metrics = new VisualMetrics();
  const observeQueues = (): void => metrics.observeQueues(queueDepths());
  exposeMetrics(metrics, queueDepths);
  observeQueues();
  let descriptor: VisualStreamDescriptor | undefined;
  let scene: ScrapScene | undefined;
  let renderQueued = false;
  let streamGeneration = 0;

  const render = (): void => {
    renderQueued = false;
    const paired = latest.consume();
    if (paired === undefined || scene === undefined) {
      return;
    }
    scene.updateHeights(paired.frame.heights, paired.frame.metadata.shared);
    scene.render();
    painter.paint(paired.image);
    metrics.recordPresented(paired.frame.metadata.target_id, paired.cameraTargetId);
    observeQueues();
    displaySharedStats(paired.frame.metadata.shared);
  };
  const scheduleRender = (): void => {
    if (!renderQueued) {
      renderQueued = true;
      window.requestAnimationFrame(render);
    }
  };
  const decodeLatest = async (): Promise<void> => {
    if (decodeInflight || pendingPackets.size === 0 || descriptor === undefined) {
      return;
    }
    const packet = pendingPackets.take();
    if (packet === undefined) {
      return;
    }
    const generation = streamGeneration;
    const decodeStartedAtMs = performance.now();
    decodeInflight = true;
    observeQueues();
    try {
      const heightCount = descriptor.model.surface_grid.x_coordinates_m.length * descriptor.model.surface_grid.y_coordinates_m.length;
      const frame = decodeVisualFrame(packet, heightCount);
      const image = await createImageBitmap(
        new Blob([frame.jpeg.buffer as ArrayBuffer], { type: "image/jpeg" }),
        cameraBitmapOptions(cameraCanvas),
      );
      metrics.recordDecoded(performance.now() - decodeStartedAtMs);
      if (generation !== streamGeneration) {
        image.close();
        metrics.recordDroppedBeforePresent();
        return;
      }
      const decoded: DecodedVisualFrame = {
        frame,
        image,
        cameraTargetId: frame.metadata.target_id,
      };
      const result = latest.offer(decoded);
      metrics.recordDroppedBeforePresent(result.droppedFrames);
      observeQueues();
      if (result.accepted) {
        scheduleRender();
      }
    } catch {
      metrics.recordDecodeError(performance.now() - decodeStartedAtMs);
    } finally {
      decodeInflight = false;
      observeQueues();
      void decodeLatest().catch(() => undefined);
    }
  };
  const isForeground = (): boolean => shouldMaintainVisualStream(
    document.visibilityState,
    document.hasFocus(),
  );
  const resetStream = (): void => {
    metrics.recordDroppedBeforeDecode(pendingPackets.reset());
    metrics.recordDroppedBeforePresent(latest.reset());
    descriptor = undefined;
    scene?.dispose();
    scene = undefined;
    streamGeneration += 1;
    observeQueues();
  };
  let foregroundStream: ForegroundVisualStream<WebSocket>;
  const openSocket = (): WebSocket => {
    const socket = new WebSocket(streamUrl());
    socket.binaryType = "arraybuffer";
    socket.onmessage = (event: MessageEvent<string | ArrayBuffer>) => {
      if (!foregroundStream.isCurrent(socket)) {
        return;
      }
      if (typeof event.data === "string") {
        try {
          const nextDescriptor = parseVisualStreamDescriptor(JSON.parse(event.data));
          const nextScene = new ScrapScene(modelCanvas, nextDescriptor.model);
          scene?.dispose();
          descriptor = nextDescriptor;
          scene = nextScene;
          foregroundStream.markAccepted(socket);
        } catch (error) {
          socket.close(1002, String(error));
        }
        return;
      }
      metrics.recordReceived();
      metrics.recordDroppedBeforeDecode(pendingPackets.offer(event.data));
      observeQueues();
      void decodeLatest().catch(() => undefined);
    };
    socket.onclose = (event: CloseEvent) => {
      foregroundStream.markClosed(socket, event.code);
    };
    socket.onerror = () => {
      if (foregroundStream.isCurrent(socket)) {
        socket.close();
      }
    };
    return socket;
  };
  foregroundStream = new ForegroundVisualStream(
    {
      open: openSocket,
      close: (socket) => socket.close(1000, "visual page left the foreground"),
      resetStream,
      recordReconnect: () => {
        metrics.recordReconnect();
      },
    },
    {
      schedule: (callback, delayMs) => window.setTimeout(callback, delayMs),
      cancel: (handle) => window.clearTimeout(handle as number),
    },
  );
  const syncForeground = (): void => {
    foregroundStream.setForeground(isForeground());
  };
  const leaveForeground = (): void => foregroundStream.setForeground(false);
  document.addEventListener("visibilitychange", syncForeground);
  window.addEventListener("focus", syncForeground);
  window.addEventListener("blur", leaveForeground);
  window.addEventListener("pageshow", syncForeground);
  window.addEventListener("pagehide", leaveForeground);
  syncForeground();
}

start();
