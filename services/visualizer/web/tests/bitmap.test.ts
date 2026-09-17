import assert from "node:assert/strict";
import test from "node:test";
import { cameraBitmapOptions } from "../src/bitmap.js";

test("camera frames decode directly to the backing canvas dimensions", () => {
  assert.deepEqual(cameraBitmapOptions({ width: 960, height: 540 }), {
    resizeWidth: 960,
    resizeHeight: 540,
    resizeQuality: "low",
  });
});

test("camera bitmap dimensions must be positive integers", () => {
  for (const target of (
    [
      { width: 0, height: 540 },
      { width: 960, height: -1 },
      { width: 960.5, height: 540 },
    ] as const
  )) {
    assert.throws(
      () => cameraBitmapOptions(target),
      /camera bitmap target dimensions must be positive integers/,
    );
  }
});
