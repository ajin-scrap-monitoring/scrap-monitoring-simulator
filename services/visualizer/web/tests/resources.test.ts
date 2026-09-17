import assert from "node:assert/strict";
import test from "node:test";
import { disposeSceneResources } from "../src/resources.js";

function resource(counter: { value: number }): { dispose: () => void } {
  return {
    dispose: () => {
      counter.value += 1;
    },
  };
}

test("scene disposal releases shared geometry, materials and textures once", () => {
  const geometryCount = { value: 0 };
  const materialCount = { value: 0 };
  const textureCount = { value: 0 };
  const rendererCount = { value: 0 };
  const geometry = resource(geometryCount);
  const texture = resource(textureCount);
  const material = { ...resource(materialCount), map: texture };
  const objects = [
    { geometry, material },
    { geometry, material: [material] },
    {},
  ];

  disposeSceneResources(
    { traverse: (visitor) => objects.forEach(visitor) },
    resource(rendererCount),
  );

  assert.deepEqual(
    [geometryCount.value, materialCount.value, textureCount.value, rendererCount.value],
    [1, 1, 1, 1],
  );
});
