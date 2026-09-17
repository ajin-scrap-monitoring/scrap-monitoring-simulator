import type { VisualFrame } from "./protocol.js";

export interface DecodedVisualFrame {
  frame: VisualFrame;
  image: ImageBitmap;
  cameraTargetId: number;
}

export interface PresentationOfferResult {
  accepted: boolean;
  droppedFrames: number;
}

export class LatestPacketQueue<T> {
  private pending: T | undefined;

  offer(value: T): number {
    const droppedFrames = this.pending === undefined ? 0 : 1;
    this.pending = value;
    return droppedFrames;
  }

  take(): T | undefined {
    const value = this.pending;
    this.pending = undefined;
    return value;
  }

  reset(): number {
    const droppedFrames = this.pending === undefined ? 0 : 1;
    this.pending = undefined;
    return droppedFrames;
  }

  get size(): number {
    return this.pending === undefined ? 0 : 1;
  }
}

export class LatestTargetPresentation {
  private latest: DecodedVisualFrame | undefined;
  private lastPresentedTarget = -1;

  offer(candidate: DecodedVisualFrame): PresentationOfferResult {
    if (
      candidate.frame.metadata.target_id <= this.lastPresentedTarget ||
      candidate.frame.metadata.target_id <= (this.latest?.frame.metadata.target_id ?? -1)
    ) {
      candidate.image.close();
      return { accepted: false, droppedFrames: 1 };
    }
    const droppedFrames = this.latest === undefined ? 0 : 1;
    if (this.latest !== undefined) {
      this.latest.image.close();
    }
    this.latest = candidate;
    return { accepted: true, droppedFrames };
  }

  consume(): DecodedVisualFrame | undefined {
    const frame = this.latest;
    if (frame === undefined) {
      return undefined;
    }
    this.latest = undefined;
    this.lastPresentedTarget = frame.frame.metadata.target_id;
    return frame;
  }

  reset(): number {
    const droppedFrames = this.latest === undefined ? 0 : 1;
    if (this.latest !== undefined) {
      this.latest.image.close();
    }
    this.latest = undefined;
    this.lastPresentedTarget = -1;
    return droppedFrames;
  }

  get pendingTargetId(): number | undefined {
    return this.latest?.frame.metadata.target_id;
  }

  get size(): number {
    return this.latest === undefined ? 0 : 1;
  }
}
