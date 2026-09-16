"""Deterministic floor, wall and clipped surface mesh calculation."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from scrap_monitoring_visualizer.contracts.models import SceneDefinition, SceneFrame
from scrap_monitoring_visualizer.limits import (
    MAX_CLIP_EDGE_TESTS,
    MAX_SURFACE_TRIANGLES,
)

type Point2 = tuple[float, float]
type Point3 = tuple[float, float, float]
type Triangle = tuple[int, int, int]

_EPSILON = 1e-10
_KEY_DIGITS = 12


@dataclass(frozen=True, slots=True)
class Mesh:
    vertices: tuple[Point3, ...]
    faces: tuple[Triangle, ...]


@dataclass(frozen=True, slots=True)
class SceneGeometry:
    floor: Mesh
    walls: Mesh
    surface: Mesh
    volume_sides: Mesh


@dataclass(frozen=True, slots=True)
class SceneGeometryTopology:
    """Static clipping result that materializes only changing vertex heights."""

    run_id: str
    floor_z_m: float
    cell_size_m: float
    x_coordinates_m: tuple[float, ...]
    y_coordinates_m: tuple[float, ...]
    floor: Mesh
    walls: Mesh
    surface_xy: tuple[Point2, ...]
    surface_faces: tuple[Triangle, ...]
    surface_boundary_edges: tuple[tuple[int, int], ...]

    def materialize(self, frame: SceneFrame) -> SceneGeometry:
        if frame.run_id != self.run_id:
            raise ValueError("frame run_id does not match geometry topology")
        surface = frame.surface
        if (
            surface.cell_size_m != self.cell_size_m
            or surface.x_coordinates_m != self.x_coordinates_m
            or surface.y_coordinates_m != self.y_coordinates_m
        ):
            raise ValueError("frame grid does not match geometry topology")

        surface_vertices = tuple(
            (x_m, y_m, surface_height_at(frame, x_m, y_m))
            for x_m, y_m in self.surface_xy
        )
        volume_vertices: list[Point3] = []
        volume_faces: list[Triangle] = []
        for start_index, end_index in self.surface_boundary_edges:
            top_start = surface_vertices[start_index]
            top_end = surface_vertices[end_index]
            offset = len(volume_vertices)
            volume_vertices.extend(
                (
                    top_start,
                    (top_start[0], top_start[1], self.floor_z_m),
                    (top_end[0], top_end[1], self.floor_z_m),
                    top_end,
                )
            )
            volume_faces.extend(
                (
                    (offset, offset + 1, offset + 2),
                    (offset, offset + 2, offset + 3),
                )
            )
        return SceneGeometry(
            floor=self.floor,
            walls=self.walls,
            surface=Mesh(surface_vertices, self.surface_faces),
            volume_sides=Mesh(tuple(volume_vertices), tuple(volume_faces)),
        )


class _MeshBuilder:
    def __init__(self) -> None:
        self.vertices: list[Point3] = []
        self.faces: list[Triangle] = []
        self._indices: dict[Point3, int] = {}

    def _index(self, point: Point3) -> int:
        key: Point3 = (
            round(point[0], _KEY_DIGITS),
            round(point[1], _KEY_DIGITS),
            round(point[2], _KEY_DIGITS),
        )
        index = self._indices.get(key)
        if index is None:
            index = len(self.vertices)
            self._indices[key] = index
            self.vertices.append(key)
        return index

    def triangle(self, left: Point3, middle: Point3, right: Point3) -> None:
        first = tuple(middle[index] - left[index] for index in range(3))
        second = tuple(right[index] - left[index] for index in range(3))
        cross = (
            first[1] * second[2] - first[2] * second[1],
            first[2] * second[0] - first[0] * second[2],
            first[0] * second[1] - first[1] * second[0],
        )
        if sum(component * component for component in cross) <= _EPSILON * _EPSILON:
            return
        self.faces.append((self._index(left), self._index(middle), self._index(right)))

    def build(self) -> Mesh:
        return Mesh(tuple(self.vertices), tuple(self.faces))


def _cross2(
    left: Point2 | Point3, middle: Point2 | Point3, right: Point2 | Point3
) -> float:
    return (middle[0] - left[0]) * (right[1] - left[1]) - (middle[1] - left[1]) * (
        right[0] - left[0]
    )


def _signed_area(points: tuple[Point2, ...]) -> float:
    return 0.5 * sum(
        left[0] * right[1] - right[0] * left[1]
        for left, right in zip(points, points[1:] + points[:1], strict=True)
    )


def _normalized_polygon(points: tuple[Point2, ...]) -> tuple[Point2, ...]:
    normalized = points if _signed_area(points) > 0 else tuple(reversed(points))
    changed = True
    while changed and len(normalized) > 3:
        changed = False
        retained: list[Point2] = []
        for index, point in enumerate(normalized):
            previous = normalized[index - 1]
            following = normalized[(index + 1) % len(normalized)]
            if abs(_cross2(previous, point, following)) <= _EPSILON:
                changed = True
            else:
                retained.append(point)
        normalized = tuple(retained)
    start = min(range(len(normalized)), key=lambda index: normalized[index])
    return normalized[start:] + normalized[:start]


def _point_in_triangle(point: Point2, a: Point2, b: Point2, c: Point2) -> bool:
    return (
        _cross2(a, b, point) >= -_EPSILON
        and _cross2(b, c, point) >= -_EPSILON
        and _cross2(c, a, point) >= -_EPSILON
    )


def triangulate_polygon(
    points: tuple[Point2, ...],
) -> tuple[tuple[Point2, Point2, Point2], ...]:
    polygon = _normalized_polygon(points)
    indices = list(range(len(polygon)))
    triangles: list[tuple[Point2, Point2, Point2]] = []
    while len(indices) > 3:
        ear_found = False
        for offset, current in enumerate(indices):
            previous = indices[offset - 1]
            following = indices[(offset + 1) % len(indices)]
            a, b, c = polygon[previous], polygon[current], polygon[following]
            if _cross2(a, b, c) <= _EPSILON:
                continue
            if any(
                _point_in_triangle(polygon[candidate], a, b, c)
                for candidate in indices
                if candidate not in {previous, current, following}
            ):
                continue
            triangles.append((a, b, c))
            del indices[offset]
            ear_found = True
            break
        if not ear_found:
            raise ValueError("boundary cannot be triangulated")
    triangles.append((polygon[indices[0]], polygon[indices[1]], polygon[indices[2]]))
    return tuple(triangles)


def _inside(point: Point3, edge_start: Point2, edge_end: Point2) -> bool:
    return _cross2(edge_start, edge_end, point) >= -_EPSILON


def _intersection(
    start: Point3, end: Point3, edge_start: Point2, edge_end: Point2
) -> Point3:
    edge_x = edge_end[0] - edge_start[0]
    edge_y = edge_end[1] - edge_start[1]
    segment_x = end[0] - start[0]
    segment_y = end[1] - start[1]
    denominator = edge_x * segment_y - edge_y * segment_x
    if abs(denominator) <= _EPSILON:
        return start
    numerator = edge_x * (start[1] - edge_start[1]) - edge_y * (
        start[0] - edge_start[0]
    )
    ratio = min(1.0, max(0.0, -numerator / denominator))
    return (
        start[0] + ratio * (end[0] - start[0]),
        start[1] + ratio * (end[1] - start[1]),
        start[2] + ratio * (end[2] - start[2]),
    )


def _clip_triangle(
    surface_triangle: tuple[Point3, Point3, Point3],
    clip_triangle: tuple[Point2, Point2, Point2],
    operation_count: list[int],
) -> tuple[Point3, ...]:
    output = list(surface_triangle)
    for edge_start, edge_end in zip(
        clip_triangle, clip_triangle[1:] + clip_triangle[:1], strict=True
    ):
        source = output
        output = []
        if not source:
            break
        previous = source[-1]
        previous_inside = _inside(previous, edge_start, edge_end)
        for current in source:
            operation_count[0] += 1
            if operation_count[0] > MAX_CLIP_EDGE_TESTS:
                raise ValueError("clipping operation limit exceeded")
            current_inside = _inside(current, edge_start, edge_end)
            if current_inside:
                if not previous_inside:
                    output.append(
                        _intersection(previous, current, edge_start, edge_end)
                    )
                output.append(current)
            elif previous_inside:
                output.append(_intersection(previous, current, edge_start, edge_end))
            previous = current
            previous_inside = current_inside
    cleaned: list[Point3] = []
    for point in output:
        if not cleaned or any(
            abs(left - right) > _EPSILON
            for left, right in zip(point, cleaned[-1], strict=True)
        ):
            cleaned.append(point)
    if len(cleaned) > 1 and all(
        abs(left - right) <= _EPSILON
        for left, right in zip(cleaned[0], cleaned[-1], strict=True)
    ):
        cleaned.pop()
    return tuple(cleaned)


def _add_polygon(builder: _MeshBuilder, points: tuple[Point3, ...]) -> None:
    if len(points) < 3:
        return
    polygon = points
    if _cross2(polygon[0], polygon[1], polygon[2]) < 0:
        polygon = tuple(reversed(polygon))
    start = min(range(len(polygon)), key=lambda index: polygon[index])
    polygon = polygon[start:] + polygon[:start]
    for index in range(1, len(polygon) - 1):
        builder.triangle(polygon[0], polygon[index], polygon[index + 1])


def _surface_triangles(
    frame: SceneFrame,
) -> tuple[tuple[Point3, Point3, Point3], ...]:
    surface = frame.surface
    triangles: list[tuple[Point3, Point3, Point3]] = []
    for y_index in range(len(surface.y_coordinates_m) - 1):
        for x_index in range(len(surface.x_coordinates_m) - 1):
            x0, x1 = surface.x_coordinates_m[x_index : x_index + 2]
            y0, y1 = surface.y_coordinates_m[y_index : y_index + 2]
            lower_left = (x0, y0, surface.heights_m[y_index][x_index])
            lower_right = (x1, y0, surface.heights_m[y_index][x_index + 1])
            upper_left = (x0, y1, surface.heights_m[y_index + 1][x_index])
            upper_right = (x1, y1, surface.heights_m[y_index + 1][x_index + 1])
            triangles.extend(
                (
                    (lower_left, lower_right, upper_right),
                    (lower_left, upper_right, upper_left),
                )
            )
    if len(triangles) > MAX_SURFACE_TRIANGLES:
        raise ValueError("surface triangle limit exceeded")
    return tuple(triangles)


def surface_height_at(frame: SceneFrame, x_m: float, y_m: float) -> float:
    """Interpolate the height on the same triangle split used by the surface mesh."""
    surface = frame.surface
    x_coordinates = surface.x_coordinates_m
    y_coordinates = surface.y_coordinates_m
    if not (x_coordinates[0] <= x_m <= x_coordinates[-1]) or not (
        y_coordinates[0] <= y_m <= y_coordinates[-1]
    ):
        raise ValueError("surface point lies outside the grid extent")

    x_index = min(bisect_right(x_coordinates, x_m) - 1, len(x_coordinates) - 2)
    y_index = min(bisect_right(y_coordinates, y_m) - 1, len(y_coordinates) - 2)
    x0, x1 = x_coordinates[x_index : x_index + 2]
    y0, y1 = y_coordinates[y_index : y_index + 2]
    u = (x_m - x0) / (x1 - x0)
    v = (y_m - y0) / (y1 - y0)
    lower_left = surface.heights_m[y_index][x_index]
    lower_right = surface.heights_m[y_index][x_index + 1]
    upper_left = surface.heights_m[y_index + 1][x_index]
    upper_right = surface.heights_m[y_index + 1][x_index + 1]
    if v <= u:
        return (
            lower_left
            + u * (lower_right - lower_left)
            + v * (upper_right - lower_right)
        )
    return lower_left + u * (upper_right - upper_left) + v * (upper_left - lower_left)


def _build_volume_sides(surface: Mesh, floor_z_m: float) -> Mesh:
    edge_counts: dict[tuple[int, int], int] = {}
    oriented_edges: dict[tuple[int, int], tuple[int, int]] = {}
    for face in surface.faces:
        for edge_start, edge_end in zip(face, face[1:] + face[:1], strict=True):
            key = (min(edge_start, edge_end), max(edge_start, edge_end))
            edge_counts[key] = edge_counts.get(key, 0) + 1
            oriented_edges.setdefault(key, (edge_start, edge_end))

    builder = _MeshBuilder()
    for key in sorted(edge_counts):
        if edge_counts[key] != 1:
            continue
        start_index, end_index = oriented_edges[key]
        top_start = surface.vertices[start_index]
        top_end = surface.vertices[end_index]
        lower_start = (top_start[0], top_start[1], floor_z_m)
        lower_end = (top_end[0], top_end[1], floor_z_m)
        builder.triangle(top_start, lower_start, lower_end)
        builder.triangle(top_start, lower_end, top_end)
    return builder.build()


def _surface_boundary_edges(surface: Mesh) -> tuple[tuple[int, int], ...]:
    edge_counts: dict[tuple[int, int], int] = {}
    oriented_edges: dict[tuple[int, int], tuple[int, int]] = {}
    for face in surface.faces:
        for edge_start, edge_end in zip(face, face[1:] + face[:1], strict=True):
            key = (min(edge_start, edge_end), max(edge_start, edge_end))
            edge_counts[key] = edge_counts.get(key, 0) + 1
            oriented_edges.setdefault(key, (edge_start, edge_end))
    return tuple(
        oriented_edges[key] for key in sorted(edge_counts) if edge_counts[key] == 1
    )


def build_scene_geometry(header: SceneDefinition, frame: SceneFrame) -> SceneGeometry:
    if frame.run_id != header.run_id:
        raise ValueError("frame run_id does not match header")
    boundary = _normalized_polygon(header.scene.boundary_xy_m)
    boundary_triangles = triangulate_polygon(boundary)

    floor_builder = _MeshBuilder()
    for triangle in boundary_triangles:
        floor_builder.triangle(
            (triangle[0][0], triangle[0][1], header.scene.floor_z_m),
            (triangle[1][0], triangle[1][1], header.scene.floor_z_m),
            (triangle[2][0], triangle[2][1], header.scene.floor_z_m),
        )

    wall_builder = _MeshBuilder()
    for left, right in zip(boundary, boundary[1:] + boundary[:1], strict=True):
        lower_left = (left[0], left[1], header.scene.floor_z_m)
        lower_right = (right[0], right[1], header.scene.floor_z_m)
        upper_left = (left[0], left[1], header.scene.top_z_m)
        upper_right = (right[0], right[1], header.scene.top_z_m)
        wall_builder.triangle(lower_left, lower_right, upper_right)
        wall_builder.triangle(lower_left, upper_right, upper_left)

    source_triangles = _surface_triangles(frame)
    if len(source_triangles) * len(boundary_triangles) * 3 > MAX_CLIP_EDGE_TESTS:
        raise ValueError("clipping operation limit exceeded")
    surface_builder = _MeshBuilder()
    operation_count = [0]
    for source_triangle in source_triangles:
        for boundary_triangle in boundary_triangles:
            _add_polygon(
                surface_builder,
                _clip_triangle(source_triangle, boundary_triangle, operation_count),
            )
    surface = surface_builder.build()
    return SceneGeometry(
        floor=floor_builder.build(),
        walls=wall_builder.build(),
        surface=surface,
        volume_sides=_build_volume_sides(surface, header.scene.floor_z_m),
    )


def build_scene_geometry_topology(
    header: SceneDefinition,
    frame: SceneFrame,
) -> SceneGeometryTopology:
    geometry = build_scene_geometry(header, frame)
    surface = frame.surface
    return SceneGeometryTopology(
        run_id=header.run_id,
        floor_z_m=header.scene.floor_z_m,
        cell_size_m=surface.cell_size_m,
        x_coordinates_m=surface.x_coordinates_m,
        y_coordinates_m=surface.y_coordinates_m,
        floor=geometry.floor,
        walls=geometry.walls,
        surface_xy=tuple((x_m, y_m) for x_m, y_m, _ in geometry.surface.vertices),
        surface_faces=geometry.surface.faces,
        surface_boundary_edges=_surface_boundary_edges(geometry.surface),
    )
