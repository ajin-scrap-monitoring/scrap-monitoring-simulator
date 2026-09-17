import * as THREE from "./vendor/three.module.js";
import {
  createCameraLayout,
  createHeightScale,
  fitOrthographicCamera,
  guideEndpoint,
  surfaceVertexHeights,
  uniqueTriangleEdges,
  type CameraLayout,
} from "./geometry.js";
import { disposeSceneResources } from "./resources.js";
import type {
  Edge,
  MaterialDescriptor,
  MeshDescriptor,
  SceneModelDescriptor,
  SharedStats,
  SurfaceTopologyDescriptor,
  Triangle,
} from "./protocol.js";

const EDGE_COLOR = 0x454545;
const OVERLAY_COLOR = 0x111827;
const ACTIVE_INLET_COLOR = 0xc62828;
const INACTIVE_INLET_COLOR = 0x2e7d32;

function color(rgb: readonly number[]): any {
  return new THREE.Color(rgb[0] ?? 0, rgb[1] ?? 0, rgb[2] ?? 0);
}

function material(descriptor: MaterialDescriptor, options: Record<string, unknown> = {}): any {
  return new THREE.MeshStandardMaterial({
    color: color(descriptor.color),
    metalness: descriptor.metallic,
    roughness: descriptor.roughness,
    ...options,
  });
}

function faceIndices(faces: readonly Triangle[]): number[] {
  return faces.flatMap((face) => [...face]);
}

function edgeIndices(faces: readonly Triangle[]): number[] {
  return uniqueTriangleEdges(faces).flatMap((edge) => [...edge]);
}

function staticGeometry(mesh: MeshDescriptor): any {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.Float32BufferAttribute(mesh.vertices_m.flatMap((vertex) => [...vertex]), 3),
  );
  geometry.setIndex(faceIndices(mesh.faces));
  geometry.computeVertexNormals();
  return geometry;
}

function meshEdges(position: any, faces: readonly Triangle[]): any {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", position);
  geometry.setIndex(edgeIndices(faces));
  return new THREE.LineSegments(
    geometry,
    new THREE.LineBasicMaterial({ color: EDGE_COLOR, transparent: true, opacity: 0.85 }),
  );
}

interface InletGuide {
  linePosition: any;
  lineMaterial: any;
  topMarker: any;
  surfaceMarker: any;
  x: number;
  y: number;
}

interface VolumeSides {
  geometry: any;
  position: any;
}

export class ScrapScene {
  private readonly renderer: any;
  private readonly scene = new THREE.Scene();
  private readonly camera: any;
  private readonly model: SceneModelDescriptor;
  private readonly cameraLayout: CameraLayout;
  private readonly surfaceGeometry: any;
  private readonly surfacePosition: any;
  private readonly volumeSides: VolumeSides;
  private readonly inletGuides: readonly InletGuide[];
  private lastWidth = 0;
  private lastHeight = 0;

  constructor(canvas: HTMLCanvasElement, model: SceneModelDescriptor) {
    this.model = model;
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.renderer.setClearColor(color(model.background_color), 1);
    this.cameraLayout = createCameraLayout(model.boundary_xy_m, model.floor_z_m, model.top_z_m);
    this.camera = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.1, this.cameraLayout.span * 8);
    this.camera.position.set(...this.cameraLayout.eye);
    this.camera.up.set(...this.cameraLayout.viewUp);
    this.camera.lookAt(...this.cameraLayout.focal);

    this.addLights();
    this.addFloor();
    this.addWalls();
    this.surfaceGeometry = this.createSurface(model.topology.surface);
    this.surfacePosition = this.surfaceGeometry.getAttribute("position");
    this.scene.add(new THREE.Mesh(
      this.surfaceGeometry,
      material(model.materials.scrap, { flatShading: true, side: THREE.DoubleSide }),
    ));
    this.scene.add(meshEdges(this.surfacePosition, model.topology.surface.faces));
    this.volumeSides = this.createVolumeSides(model.topology.surface);
    this.scene.add(new THREE.Mesh(
      this.volumeSides.geometry,
      material(model.materials.scrap, { flatShading: true, side: THREE.DoubleSide }),
    ));
    this.addHeightScale();
    this.inletGuides = this.createInletGuides();
    this.resize();
  }

  updateHeights(heights: Float32Array, stats: SharedStats): void {
    const grid = this.model.surface_grid;
    if (heights.length !== grid.x_coordinates_m.length * grid.y_coordinates_m.length) {
      throw new Error("height count does not match WebGL surface grid");
    }
    const vertexHeights = surfaceVertexHeights(
      this.model.topology.surface.vertices_xy_m,
      grid.x_coordinates_m,
      grid.y_coordinates_m,
      heights,
    );
    for (let index = 0; index < vertexHeights.length; index += 1) {
      this.surfacePosition.setZ(index, vertexHeights[index] ?? this.model.floor_z_m);
    }
    this.surfacePosition.needsUpdate = true;
    this.surfaceGeometry.computeVertexNormals();
    this.updateVolumeSides(vertexHeights);
    this.updateInletGuides(heights, stats);
  }

  render(): void {
    this.resize();
    this.renderer.render(this.scene, this.camera);
  }

  dispose(): void {
    disposeSceneResources(this.scene, this.renderer);
  }

  private addLights(): void {
    this.scene.add(new THREE.HemisphereLight(0xf2eee6, 0x1b2229, 2.1));
    const key = new THREE.DirectionalLight(0xffffff, 1.4);
    key.position.set(
      this.cameraLayout.center[0] - this.cameraLayout.span,
      this.cameraLayout.center[1] - this.cameraLayout.span,
      this.cameraLayout.center[2] + this.cameraLayout.span * 2,
    );
    this.scene.add(key);
  }

  private addFloor(): void {
    const geometry = staticGeometry(this.model.topology.floor);
    this.scene.add(new THREE.Mesh(
      geometry,
      material(this.model.materials.floor, { side: THREE.DoubleSide }),
    ));
  }

  private addWalls(): void {
    const geometry = staticGeometry(this.model.topology.walls);
    const position = geometry.getAttribute("position");
    this.scene.add(new THREE.Mesh(
      geometry,
      material(this.model.materials.wall, {
        side: THREE.DoubleSide,
        transparent: true,
        opacity: 0.3,
        depthWrite: false,
      }),
    ));
    this.scene.add(meshEdges(position, this.model.topology.walls.faces));
  }

  private createSurface(topology: SurfaceTopologyDescriptor): any {
    const positions = topology.vertices_xy_m.flatMap(([x, y]) => [x, y, this.model.floor_z_m]);
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geometry.setIndex(faceIndices(topology.faces));
    geometry.computeVertexNormals();
    return geometry;
  }

  private createVolumeSides(topology: SurfaceTopologyDescriptor): VolumeSides {
    const positions: number[] = [];
    const faces: number[] = [];
    for (let edgeIndex = 0; edgeIndex < topology.boundary_edges.length; edgeIndex += 1) {
      const [startIndex, endIndex] = topology.boundary_edges[edgeIndex] as Edge;
      const start = topology.vertices_xy_m[startIndex] as readonly [number, number];
      const end = topology.vertices_xy_m[endIndex] as readonly [number, number];
      positions.push(
        start[0], start[1], this.model.floor_z_m,
        start[0], start[1], this.model.floor_z_m,
        end[0], end[1], this.model.floor_z_m,
        end[0], end[1], this.model.floor_z_m,
      );
      const offset = edgeIndex * 4;
      faces.push(offset, offset + 1, offset + 2, offset, offset + 2, offset + 3);
    }
    const geometry = new THREE.BufferGeometry();
    const position = new THREE.Float32BufferAttribute(positions, 3);
    geometry.setAttribute("position", position);
    geometry.setIndex(faces);
    geometry.computeVertexNormals();
    return { geometry, position };
  }

  private updateVolumeSides(vertexHeights: Float32Array): void {
    for (let edgeIndex = 0; edgeIndex < this.model.topology.surface.boundary_edges.length; edgeIndex += 1) {
      const [startIndex, endIndex] = this.model.topology.surface.boundary_edges[edgeIndex] as Edge;
      const offset = edgeIndex * 4;
      this.volumeSides.position.setZ(offset, vertexHeights[startIndex] ?? this.model.floor_z_m);
      this.volumeSides.position.setZ(offset + 1, this.model.floor_z_m);
      this.volumeSides.position.setZ(offset + 2, this.model.floor_z_m);
      this.volumeSides.position.setZ(offset + 3, vertexHeights[endIndex] ?? this.model.floor_z_m);
    }
    this.volumeSides.position.needsUpdate = true;
    this.volumeSides.geometry.computeVertexNormals();
  }

  private addHeightScale(): void {
    const scale = createHeightScale(
      this.model.boundary_xy_m,
      this.model.floor_z_m,
      this.model.top_z_m,
      this.cameraLayout,
    );
    const lineGeometry = new THREE.BufferGeometry().setFromPoints(
      scale.lines.flatMap(([start, end]) => [new THREE.Vector3(...start), new THREE.Vector3(...end)]),
    );
    const lines = new THREE.LineSegments(
      lineGeometry,
      new THREE.LineBasicMaterial({ color: OVERLAY_COLOR, depthTest: false }),
    );
    lines.renderOrder = 10;
    this.scene.add(lines);
    for (const label of scale.labels) {
      const sprite = this.createTextSprite(label.text);
      sprite.position.set(...label.position);
      sprite.renderOrder = 11;
      this.scene.add(sprite);
    }
  }

  private createTextSprite(text: string): any {
    const canvas = document.createElement("canvas");
    canvas.width = 160;
    canvas.height = 64;
    const context = canvas.getContext("2d");
    if (context === null) {
      throw new Error("height scale label context is unavailable");
    }
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = "#111827";
    context.font = "32px Arial, sans-serif";
    context.textAlign = "left";
    context.textBaseline = "middle";
    context.fillText(text, 4, canvas.height / 2);
    const texture = new THREE.CanvasTexture(canvas);
    texture.needsUpdate = true;
    const sprite = new THREE.Sprite(new THREE.SpriteMaterial({ map: texture, transparent: true, depthTest: false }));
    sprite.center.set(0, 0.5);
    sprite.scale.set(this.cameraLayout.span * 0.12, this.cameraLayout.span * 0.048, 1);
    return sprite;
  }

  private createInletGuides(): readonly InletGuide[] {
    const guides: InletGuide[] = [];
    const xValues = this.model.boundary_xy_m.map(([x]) => x);
    const yValues = this.model.boundary_xy_m.map(([, y]) => y);
    const markerScale = Math.max(
      Math.max(...xValues) - Math.min(...xValues),
      Math.max(...yValues) - Math.min(...yValues),
      1,
    );
    for (const [x, y] of this.model.inlet_positions_xy_m) {
      const linePosition = new THREE.Float32BufferAttribute([
        x, y, this.model.top_z_m,
        x, y, this.model.floor_z_m,
      ], 3);
      const lineGeometry = new THREE.BufferGeometry();
      lineGeometry.setAttribute("position", linePosition);
      const lineMaterial = new THREE.LineBasicMaterial({ color: INACTIVE_INLET_COLOR });
      this.scene.add(new THREE.Line(lineGeometry, lineMaterial));
      const topMarker = new THREE.Mesh(
        new THREE.SphereGeometry(0.03 * markerScale, 20, 12),
        new THREE.MeshStandardMaterial({ color: INACTIVE_INLET_COLOR, roughness: 0.45 }),
      );
      topMarker.position.set(x, y, this.model.top_z_m);
      this.scene.add(topMarker);
      const surfaceMarker = new THREE.Mesh(
        new THREE.SphereGeometry(0.022 * markerScale, 20, 12),
        new THREE.MeshStandardMaterial({ color: INACTIVE_INLET_COLOR, roughness: 0.45 }),
      );
      surfaceMarker.position.set(x, y, this.model.floor_z_m);
      this.scene.add(surfaceMarker);
      guides.push({ linePosition, lineMaterial, topMarker, surfaceMarker, x, y });
    }
    return guides;
  }

  private updateInletGuides(heights: Float32Array, stats: SharedStats): void {
    const grid = this.model.surface_grid;
    for (let index = 0; index < this.inletGuides.length; index += 1) {
      const guide = this.inletGuides[index] as InletGuide;
      const endpoint = guideEndpoint(
        [guide.x, guide.y],
        grid.x_coordinates_m,
        grid.y_coordinates_m,
        heights,
      );
      guide.linePosition.setZ(1, endpoint[2]);
      guide.linePosition.needsUpdate = true;
      guide.surfaceMarker.position.set(...endpoint);
      const active = stats.phase === "filling" && stats.current_inlet_index === index;
      const markerColor = active ? ACTIVE_INLET_COLOR : INACTIVE_INLET_COLOR;
      guide.lineMaterial.color.setHex(markerColor);
      guide.topMarker.material.color.setHex(markerColor);
      guide.surfaceMarker.material.color.setHex(markerColor);
    }
  }

  private resize(): void {
    const canvas = this.renderer.domElement;
    const width = Math.max(1, Math.round(canvas.clientWidth));
    const height = Math.max(1, Math.round(canvas.clientHeight));
    if (width === this.lastWidth && height === this.lastHeight) {
      return;
    }
    this.lastWidth = width;
    this.lastHeight = height;
    this.renderer.setSize(width, height, false);
    const bounds = fitOrthographicCamera(
      this.cameraLayout,
      this.model.boundary_xy_m,
      this.model.floor_z_m,
      this.model.top_z_m,
      width / height,
    );
    this.camera.left = bounds.left;
    this.camera.right = bounds.right;
    this.camera.bottom = bounds.bottom;
    this.camera.top = bounds.top;
    this.camera.near = bounds.near;
    this.camera.far = bounds.far;
    this.camera.updateProjectionMatrix();
  }
}
