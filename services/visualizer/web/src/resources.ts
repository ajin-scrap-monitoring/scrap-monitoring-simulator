interface Disposable {
  dispose(): void;
}

interface SceneGraph {
  traverse(visitor: (object: SceneObject) => void): void;
}

interface SceneObject {
  geometry?: unknown;
  material?: unknown | readonly unknown[];
}

interface Renderer {
  dispose(): void;
}

function disposable(value: unknown): Disposable | undefined {
  if (
    typeof value === "object"
    && value !== null
    && "dispose" in value
    && typeof value.dispose === "function"
  ) {
    return value as Disposable;
  }
  return undefined;
}

export function disposeSceneResources(scene: SceneGraph, renderer: Renderer): void {
  const disposed = new Set<Disposable>();
  const dispose = (value: unknown): void => {
    const resource = disposable(value);
    if (resource !== undefined && !disposed.has(resource)) {
      disposed.add(resource);
      resource.dispose();
    }
  };

  scene.traverse((object) => {
    dispose(object.geometry);
    const materials = Array.isArray(object.material)
      ? object.material
      : [object.material];
    for (const material of materials) {
      if (typeof material === "object" && material !== null && "map" in material) {
        dispose(material.map);
      }
      dispose(material);
    }
  });
  renderer.dispose();
}
