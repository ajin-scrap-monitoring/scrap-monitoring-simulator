import type { VisualFrame } from "./protocol.js";

export interface DecodedVisualFrame {
  frame: VisualFrame;
  image: ImageBitmap;
}

export class LatestTargetPresentation {
  private latest: DecodedVisualFrame | undefined;
  private lastPresentedTarget = -1;

  offer(candidate: DecodedVisualFrame): boolean {
    if (
      candidate.frame.metadata.target_id <= this.lastPresentedTarget ||
      candidate.frame.metadata.target_id <= (this.latest?.frame.metadata.target_id ?? -1)
    ) {
      candidate.image.close();
      return false;
    }
    if (this.latest !== undefined) {
      this.latest.image.close();
    }
    this.latest = candidate;
    return true;
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

  reset(): void {
    if (this.latest !== undefined) {
      this.latest.image.close();
    }
    this.latest = undefined;
    this.lastPresentedTarget = -1;
  }

  get pendingTargetId(): number | undefined {
    return this.latest?.frame.metadata.target_id;
  }
}
