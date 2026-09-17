export function shouldMaintainVisualStream(
  visibilityState: string,
  hasFocus: boolean,
): boolean {
  return visibilityState === "visible" && hasFocus;
}

interface RetryScheduler {
  schedule(callback: () => void, delayMs: number): unknown;
  cancel(handle: unknown): void;
}

interface ForegroundVisualStreamCallbacks<Connection extends object> {
  open(): Connection;
  close(connection: Connection): void;
  resetStream(): void;
  recordReconnect(code: number | undefined): void;
}

const INITIAL_RECONNECT_DELAY_MS = 250;
const MAX_RECONNECT_DELAY_MS = 5_000;

export class ForegroundVisualStream<Connection extends object> {
  private foreground = false;
  private active: Connection | undefined;
  private retry: { handle: unknown } | undefined;
  private reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;

  constructor(
    private readonly callbacks: ForegroundVisualStreamCallbacks<Connection>,
    private readonly scheduler: RetryScheduler,
  ) {}

  setForeground(foreground: boolean): void {
    if (foreground) {
      if (!this.foreground) {
        this.foreground = true;
        this.reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
      }
      this.openIfIdle();
      return;
    }

    this.foreground = false;
    this.cancelRetry();
    this.reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
    const connection = this.active;
    this.active = undefined;
    if (connection !== undefined) {
      this.callbacks.resetStream();
      this.callbacks.close(connection);
    }
  }

  isCurrent(connection: Connection): boolean {
    return connection === this.active;
  }

  markAccepted(connection: Connection): void {
    if (this.isCurrent(connection)) {
      this.reconnectDelayMs = INITIAL_RECONNECT_DELAY_MS;
    }
  }

  markClosed(connection: Connection, code: number | undefined): void {
    if (!this.isCurrent(connection)) {
      return;
    }
    this.active = undefined;
    this.callbacks.resetStream();
    this.callbacks.recordReconnect(code);
    this.scheduleReconnect();
  }

  private openIfIdle(): void {
    if (!this.foreground || this.active !== undefined || this.retry !== undefined) {
      return;
    }
    try {
      this.active = this.callbacks.open();
    } catch {
      this.callbacks.recordReconnect(undefined);
      this.scheduleReconnect();
    }
  }

  private scheduleReconnect(): void {
    if (!this.foreground || this.active !== undefined || this.retry !== undefined) {
      return;
    }
    const delayMs = this.reconnectDelayMs;
    this.reconnectDelayMs = Math.min(
      this.reconnectDelayMs * 2,
      MAX_RECONNECT_DELAY_MS,
    );
    const retry = { handle: undefined as unknown };
    this.retry = retry;
    retry.handle = this.scheduler.schedule(() => {
      if (this.retry !== retry) {
        return;
      }
      this.retry = undefined;
      this.openIfIdle();
    }, delayMs);
  }

  private cancelRetry(): void {
    const retry = this.retry;
    this.retry = undefined;
    if (retry !== undefined) {
      this.scheduler.cancel(retry.handle);
    }
  }
}
