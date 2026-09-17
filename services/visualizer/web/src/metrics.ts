export interface QueueDepths {
  pendingDecode: number;
  decodeInflight: number;
  pendingPresent: number;
}

export interface VisualMetricsSnapshot {
  version: 2;
  sampled_at_ms: number;
  window_s: number;
  network: {
    received_frames: number;
    received_fps: number;
    reconnects: number;
  };
  browser: {
    decoded_frames: number;
    decoded_fps: number;
    decode_duration_ms: number;
    decode_duration_max_ms: number;
    presented_frames: number;
    presented_fps: number;
    dropped_before_decode: number;
    dropped_before_present: number;
    decode_errors: number;
    target_mismatches: number;
  };
  queues: {
    pending_decode: number;
    decode_inflight: number;
    pending_present: number;
    max_pending_decode: number;
    max_decode_inflight: number;
    max_pending_present: number;
  };
}

interface DurationSample {
  completedAtMs: number;
  durationMs: number;
}

type Now = () => number;

function assertQueueDepth(value: number, label: string): void {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw new Error(`${label} must be a non-negative safe integer`);
  }
}

function assertDuration(value: number): void {
  if (!Number.isFinite(value) || value < 0) {
    throw new Error("decode duration must be a non-negative finite number");
  }
}

export class VisualMetrics {
  private readonly startedAtMs: number;
  private readonly receivedSamples: number[] = [];
  private readonly decodedSamples: number[] = [];
  private readonly presentedSamples: number[] = [];
  private readonly decodeDurations: DurationSample[] = [];
  private receivedFrames = 0;
  private decodedFrames = 0;
  private presentedFrames = 0;
  private reconnects = 0;
  private droppedBeforeDecode = 0;
  private droppedBeforePresent = 0;
  private decodeErrors = 0;
  private targetMismatches = 0;
  private maxPendingDecode = 0;
  private maxDecodeInflight = 0;
  private maxPendingPresent = 0;

  constructor(
    private readonly now: Now = () => performance.now(),
    private readonly windowMs = 5_000,
  ) {
    if (!Number.isFinite(windowMs) || windowMs <= 0) {
      throw new Error("metric window must be positive");
    }
    this.startedAtMs = now();
  }

  recordReceived(): void {
    this.receivedFrames += 1;
    this.receivedSamples.push(this.now());
  }

  recordDecoded(durationMs: number): void {
    const completedAtMs = this.now();
    assertDuration(durationMs);
    this.decodedFrames += 1;
    this.decodedSamples.push(completedAtMs);
    this.decodeDurations.push({ completedAtMs, durationMs });
  }

  recordPresented(modelTargetId: number, cameraTargetId: number): void {
    this.presentedFrames += 1;
    this.presentedSamples.push(this.now());
    if (modelTargetId !== cameraTargetId) {
      this.targetMismatches += 1;
    }
  }

  recordReconnect(): void {
    this.reconnects += 1;
  }

  recordDroppedBeforeDecode(count = 1): void {
    this.droppedBeforeDecode += count;
  }

  recordDroppedBeforePresent(count = 1): void {
    this.droppedBeforePresent += count;
  }

  recordDecodeError(durationMs: number): void {
    const completedAtMs = this.now();
    assertDuration(durationMs);
    this.decodeErrors += 1;
    this.decodeDurations.push({ completedAtMs, durationMs });
  }

  observeQueues(depths: QueueDepths): void {
    assertQueueDepth(depths.pendingDecode, "pending decode depth");
    assertQueueDepth(depths.decodeInflight, "decode inflight depth");
    assertQueueDepth(depths.pendingPresent, "pending present depth");
    this.maxPendingDecode = Math.max(this.maxPendingDecode, depths.pendingDecode);
    this.maxDecodeInflight = Math.max(this.maxDecodeInflight, depths.decodeInflight);
    this.maxPendingPresent = Math.max(this.maxPendingPresent, depths.pendingPresent);
  }

  snapshot(depths: QueueDepths): VisualMetricsSnapshot {
    this.observeQueues(depths);
    const sampledAtMs = this.now();
    this.trim(sampledAtMs);
    const decodeDurationMs = this.decodeDurations.length === 0
      ? 0
      : this.decodeDurations.reduce((total, sample) => total + sample.durationMs, 0) / this.decodeDurations.length;
    const decodeDurationMaxMs = this.decodeDurations.reduce(
      (maximum, sample) => Math.max(maximum, sample.durationMs),
      0,
    );
    return {
      version: 2,
      sampled_at_ms: sampledAtMs,
      window_s: this.windowMs / 1_000,
      network: {
        received_frames: this.receivedFrames,
        received_fps: this.rate(this.receivedSamples, sampledAtMs),
        reconnects: this.reconnects,
      },
      browser: {
        decoded_frames: this.decodedFrames,
        decoded_fps: this.rate(this.decodedSamples, sampledAtMs),
        decode_duration_ms: decodeDurationMs,
        decode_duration_max_ms: decodeDurationMaxMs,
        presented_frames: this.presentedFrames,
        presented_fps: this.rate(this.presentedSamples, sampledAtMs),
        dropped_before_decode: this.droppedBeforeDecode,
        dropped_before_present: this.droppedBeforePresent,
        decode_errors: this.decodeErrors,
        target_mismatches: this.targetMismatches,
      },
      queues: {
        pending_decode: depths.pendingDecode,
        decode_inflight: depths.decodeInflight,
        pending_present: depths.pendingPresent,
        max_pending_decode: this.maxPendingDecode,
        max_decode_inflight: this.maxDecodeInflight,
        max_pending_present: this.maxPendingPresent,
      },
    };
  }

  private trim(nowMs: number): void {
    const cutoffMs = nowMs - this.windowMs;
    const trimSamples = (samples: number[]): void => {
      while (samples.length > 0 && (samples[0] ?? nowMs) <= cutoffMs) {
        samples.shift();
      }
    };
    trimSamples(this.receivedSamples);
    trimSamples(this.decodedSamples);
    trimSamples(this.presentedSamples);
    while (
      this.decodeDurations.length > 0
      && (this.decodeDurations[0]?.completedAtMs ?? nowMs) <= cutoffMs
    ) {
      this.decodeDurations.shift();
    }
  }

  private rate(samples: readonly number[], nowMs: number): number {
    const elapsedMs = Math.min(this.windowMs, Math.max(0, nowMs - this.startedAtMs));
    return elapsedMs === 0 ? 0 : samples.length * 1_000 / elapsedMs;
  }
}
