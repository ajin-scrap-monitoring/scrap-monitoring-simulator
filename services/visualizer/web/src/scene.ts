import * as THREE from "./vendor/three.module.js";
import type { SceneModelDescriptor } from "./protocol.js";

function color(rgb: readonly number[]): any {
  return new THREE.Color(rgb[0] ?? 0, rgb[1] ?? 0, rgb[2] ?? 0);
}

interface InletGuide {
  geometry: any;
  marker: any;
  x: number;
  y: number;
}

export class ScrapScene {
  private readonly renderer: any;
  private readonly scene = new THREE.Scene();
  private readonly camera: any;
  private readonly surface: any;
  private readonly position: any;
  private readonly xCoordinates: readonly number[];
  private readonly yCoordinates: readonly number[];
  private readonly inletGuides: readonly InletGuide[];

  constructor(canvas: HTMLCanvasElement, model: SceneModelDescriptor) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(color(model.palette.background), 1);
    this.xCoordinates = model.surface.x_coordinates_m;
    this.yCoordinates = model.surface.y_coordinates_m;
    this.camera = this.createCamera(model);
    this.scene.add(new THREE.HemisphereLight(0xf2eee6, 0x1b2229, 2.1));
    this.scene.add(new THREE.DirectionalLight(0xffffff, 1.4));
    this.surface = this.createSurface(model);
    this.position = this.surface.getAttribute("position");
    this.scene.add(new THREE.Mesh(this.surface, new THREE.MeshStandardMaterial({ color: color(model.palette.scrap), roughness: 0.93, metalness: 0.18, flatShading: true })));
    this.scene.add(this.createWalls(model));
    this.inletGuides = this.createInletGuides(model);
    this.resize();
  }

  updateHeights(heights: Float32Array): void {
    if (heights.length !== this.xCoordinates.length * this.yCoordinates.length) {
      throw new Error("height count does not match WebGL surface topology");
    }
    let index = 0;
    for (let y = 0; y < this.yCoordinates.length; y += 1) {
      for (let x = 0; x < this.xCoordinates.length; x += 1) {
        this.position.setZ(index, heights[index] ?? 0);
        index += 1;
      }
    }
    this.position.needsUpdate = true;
    this.surface.computeVertexNormals();
    for (const guide of this.inletGuides) {
      const surfaceZ = this.surfaceHeightAt(guide.x, guide.y);
      const endpoint = guide.geometry.getAttribute("position");
      endpoint.setZ(1, surfaceZ);
      endpoint.needsUpdate = true;
      guide.marker.position.z = surfaceZ;
    }
  }

  render(): void {
    this.resize();
    this.renderer.render(this.scene, this.camera);
  }

  private createCamera(model: SceneModelDescriptor): any {
    const xs = model.boundary_xy_m.map(([x]) => x);
    const ys = model.boundary_xy_m.map(([, y]) => y);
    const centerX = (Math.min(...xs) + Math.max(...xs)) / 2;
    const centerY = (Math.min(...ys) + Math.max(...ys)) / 2;
    const span = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys), model.top_z_m - model.floor_z_m);
    const camera = new THREE.OrthographicCamera(-span, span, span, -span, 0.1, span * 8);
    camera.position.set(centerX + span * 1.45, centerY - span * 1.65, model.top_z_m + span * 1.7);
    camera.lookAt(centerX, centerY, model.floor_z_m + (model.top_z_m - model.floor_z_m) * 0.42);
    return camera;
  }

  private createSurface(model: SceneModelDescriptor): any {
    const positions: number[] = [];
    for (const y of this.yCoordinates) {
      for (const x of this.xCoordinates) {
        positions.push(x, y, model.floor_z_m);
      }
    }
    const indices: number[] = [];
    const columns = this.xCoordinates.length;
    for (let y = 0; y < this.yCoordinates.length - 1; y += 1) {
      for (let x = 0; x < columns - 1; x += 1) {
        const a = y * columns + x;
        indices.push(a, a + 1, a + columns, a + 1, a + columns + 1, a + columns);
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geometry.setIndex(indices);
    geometry.computeVertexNormals();
    return geometry;
  }

  private createWalls(model: SceneModelDescriptor): any {
    const positions: number[] = [];
    for (let index = 0; index < model.boundary_xy_m.length; index += 1) {
      const [ax, ay] = model.boundary_xy_m[index] ?? [0, 0];
      const [bx, by] = model.boundary_xy_m[(index + 1) % model.boundary_xy_m.length] ?? [0, 0];
      positions.push(ax, ay, model.floor_z_m, bx, by, model.floor_z_m, bx, by, model.top_z_m);
      positions.push(ax, ay, model.floor_z_m, bx, by, model.top_z_m, ax, ay, model.top_z_m);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geometry.computeVertexNormals();
    return new THREE.Mesh(geometry, new THREE.MeshStandardMaterial({ color: color(model.palette.wall), roughness: 0.82, metalness: 0.05, side: THREE.DoubleSide }));
  }

  private createInletGuides(model: SceneModelDescriptor): readonly InletGuide[] {
    const guides: InletGuide[] = [];
    for (const [x, y] of model.inlet_positions_xy_m) {
      const geometry = new THREE.BufferGeometry().setFromPoints([
        new THREE.Vector3(x, y, model.top_z_m),
        new THREE.Vector3(x, y, model.floor_z_m),
      ]);
      this.scene.add(new THREE.Line(geometry, new THREE.LineBasicMaterial({ color: color(model.palette.guide) })));
      const marker = new THREE.Mesh(
        new THREE.SphereGeometry(0.07, 12, 8),
        new THREE.MeshStandardMaterial({ color: color(model.palette.guide), roughness: 0.45 }),
      );
      marker.position.set(x, y, model.floor_z_m);
      this.scene.add(marker);
      guides.push({ geometry, marker, x, y });
    }
    return guides;
  }

  private surfaceHeightAt(x: number, y: number): number {
    const nearestIndex = (coordinates: readonly number[], value: number): number => {
      let selected = 0;
      let distance = Number.POSITIVE_INFINITY;
      for (let index = 0; index < coordinates.length; index += 1) {
        const candidateDistance = Math.abs((coordinates[index] ?? 0) - value);
        if (candidateDistance < distance) {
          selected = index;
          distance = candidateDistance;
        }
      }
      return selected;
    };
    const xIndex = nearestIndex(this.xCoordinates, x);
    const yIndex = nearestIndex(this.yCoordinates, y);
    return this.position.getZ(yIndex * this.xCoordinates.length + xIndex);
  }

  private resize(): void {
    const canvas = this.renderer.domElement;
    const width = Math.max(1, canvas.clientWidth);
    const height = Math.max(1, canvas.clientHeight);
    if (canvas.width === width && canvas.height === height) {
      return;
    }
    this.renderer.setSize(width, height, false);
    const span = Math.max(this.camera.top - this.camera.bottom, 1);
    this.camera.left = -span * width / height;
    this.camera.right = span * width / height;
    this.camera.updateProjectionMatrix();
  }
}
