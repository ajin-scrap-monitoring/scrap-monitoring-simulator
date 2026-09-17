import assert from "node:assert/strict";
import test from "node:test";
import { ForegroundVisualStream, shouldMaintainVisualStream } from "../src/foreground.js";

interface Connection {
  id: number;
}

class FakeScheduler {
  private nextHandle = 1;
  readonly tasks = new Map<number, { callback: () => void; delayMs: number }>();

  schedule = (callback: () => void, delayMs: number): number => {
    const handle = this.nextHandle;
    this.nextHandle += 1;
    this.tasks.set(handle, { callback, delayMs });
    return handle;
  };

  cancel = (handle: unknown): void => {
    this.tasks.delete(handle as number);
  };

  runNext(): void {
    const entry = this.tasks.entries().next().value as
      | [number, { callback: () => void; delayMs: number }]
      | undefined;
    assert.notEqual(entry, undefined);
    if (entry === undefined) {
      return;
    }
    this.tasks.delete(entry[0]);
    entry[1].callback();
  }

  get delays(): number[] {
    return [...this.tasks.values()].map((task) => task.delayMs);
  }
}

function harness(options: { failOpen?: () => boolean } = {}) {
  const scheduler = new FakeScheduler();
  const opened: Connection[] = [];
  const closed: Connection[] = [];
  const reconnectCodes: Array<number | undefined> = [];
  let resets = 0;
  let nextId = 1;
  const stream = new ForegroundVisualStream<Connection>(
    {
      open: () => {
        if (options.failOpen?.() === true) {
          throw new Error("open failed");
        }
        const connection = { id: nextId };
        nextId += 1;
        opened.push(connection);
        return connection;
      },
      close: (connection) => closed.push(connection),
      resetStream: () => {
        resets += 1;
      },
      recordReconnect: (code) => reconnectCodes.push(code),
    },
    scheduler,
  );
  return {
    stream,
    scheduler,
    opened,
    closed,
    reconnectCodes,
    resets: () => resets,
  };
}

test("only the visible focused page maintains the visual stream", () => {
  assert.equal(shouldMaintainVisualStream("visible", true), true);
  assert.equal(shouldMaintainVisualStream("visible", false), false);
  assert.equal(shouldMaintainVisualStream("hidden", true), false);
  assert.equal(shouldMaintainVisualStream("hidden", false), false);
});

test("foreground events maintain exactly one connection and blur releases it", () => {
  const state = harness();

  state.stream.setForeground(true);
  state.stream.setForeground(true);
  assert.equal(state.opened.length, 1);

  const first = state.opened[0];
  assert.notEqual(first, undefined);
  if (first === undefined) {
    return;
  }
  state.stream.setForeground(false);
  assert.deepEqual(state.closed, [first]);
  assert.equal(state.resets(), 1);
  assert.equal(state.stream.isCurrent(first), false);

  state.stream.setForeground(true);
  assert.equal(state.opened.length, 2);
  const second = state.opened[1];
  assert.notEqual(second, undefined);
  if (second === undefined) {
    return;
  }
  state.stream.markClosed(first, 1000);
  assert.equal(state.scheduler.tasks.size, 0);
  assert.deepEqual(state.reconnectCodes, []);
  assert.equal(state.stream.isCurrent(second), true);
});

test("1013 closes use bounded backoff without foreground event bypass", () => {
  const state = harness();
  state.stream.setForeground(true);

  const first = state.opened[0];
  assert.notEqual(first, undefined);
  if (first === undefined) {
    return;
  }
  state.stream.markClosed(first, 1013);
  assert.deepEqual(state.scheduler.delays, [250]);
  assert.deepEqual(state.reconnectCodes, [1013]);

  state.stream.setForeground(true);
  state.stream.setForeground(true);
  assert.equal(state.opened.length, 1);
  assert.deepEqual(state.scheduler.delays, [250]);

  state.scheduler.runNext();
  const second = state.opened[1];
  assert.notEqual(second, undefined);
  if (second === undefined) {
    return;
  }
  state.stream.markClosed(second, 1013);
  assert.deepEqual(state.scheduler.delays, [500]);

  state.scheduler.runNext();
  const accepted = state.opened[2];
  assert.notEqual(accepted, undefined);
  if (accepted === undefined) {
    return;
  }
  state.stream.markAccepted(accepted);
  state.stream.markClosed(accepted, 1006);
  assert.deepEqual(state.scheduler.delays, [250]);
});

test("reconnect delay stops growing at five seconds", () => {
  const state = harness();
  state.stream.setForeground(true);
  const expectedDelays = [250, 500, 1_000, 2_000, 4_000, 5_000, 5_000];

  for (const expectedDelay of expectedDelays) {
    const connection = state.opened.at(-1);
    assert.notEqual(connection, undefined);
    if (connection === undefined) {
      return;
    }
    state.stream.markClosed(connection, 1013);
    assert.deepEqual(state.scheduler.delays, [expectedDelay]);
    state.scheduler.runNext();
  }
});

test("leaving foreground cancels a pending retry", () => {
  const state = harness();
  state.stream.setForeground(true);

  const first = state.opened[0];
  assert.notEqual(first, undefined);
  if (first === undefined) {
    return;
  }
  state.stream.markClosed(first, 1006);
  assert.equal(state.scheduler.tasks.size, 1);

  state.stream.setForeground(false);
  assert.equal(state.scheduler.tasks.size, 0);
  state.stream.setForeground(false);
  assert.equal(state.opened.length, 1);
});

test("synchronous open failures retry through the timer instead of recursion", () => {
  let failures = 2;
  const state = harness({
    failOpen: () => {
      const fail = failures > 0;
      failures -= 1;
      return fail;
    },
  });

  state.stream.setForeground(true);
  assert.deepEqual(state.scheduler.delays, [250]);
  assert.deepEqual(state.reconnectCodes, [undefined]);

  state.scheduler.runNext();
  assert.deepEqual(state.scheduler.delays, [500]);
  assert.equal(state.opened.length, 0);

  state.scheduler.runNext();
  assert.equal(state.scheduler.tasks.size, 0);
  assert.equal(state.opened.length, 1);
});
