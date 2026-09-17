from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from scrap_monitoring_visualizer.contracts import (
    ContractParser,
    SceneDefinition,
    SceneFrame,
    SceneSegment,
    materialize_keyframe,
)
from scrap_monitoring_visualizer.geometry import (
    build_scene_geometry,
    build_scene_geometry_topology,
    surface_height_at,
)

CONTRACT_ROOT = Path("../contracts/scene/v2")


@pytest.fixture(scope="module")
def records() -> tuple[SceneDefinition, SceneFrame]:
    parser = ContractParser(CONTRACT_ROOT)
    lines = (
        (CONTRACT_ROOT / "fixtures/scene.v2.jsonl")
        .read_bytes()
        .splitlines(keepends=True)
    )
    values = tuple(parser.parse_line(line).value for line in lines)
    assert isinstance(values[0], SceneDefinition)
    assert isinstance(values[1], SceneSegment)
    return values[0], materialize_keyframe(
        values[0], values[1].right, values[1].right_sequence, values[1].run_id
    )


def _projected_area(
    vertices: tuple[tuple[float, float, float], ...],
    faces: tuple[tuple[int, int, int], ...],
) -> float:
    area = 0.0
    for face in faces:
        left, middle, right = (vertices[index] for index in face)
        area += (
            abs(
                (middle[0] - left[0]) * (right[1] - left[1])
                - (middle[1] - left[1]) * (right[0] - left[0])
            )
            / 2
        )
    return area


def test_fixture_mesh_covers_boundary(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    geometry = build_scene_geometry(*records)

    assert _projected_area(
        geometry.floor.vertices, geometry.floor.faces
    ) == pytest.approx(1.0)
    assert _projected_area(
        geometry.surface.vertices, geometry.surface.faces
    ) == pytest.approx(1.0)
    assert len(geometry.walls.faces) == 8


def test_y_major_height_mapping(records: tuple[SceneDefinition, SceneFrame]) -> None:
    header, frame = records
    surface = replace(
        frame.surface,
        x_coordinates_m=(0.0, 0.5, 1.0),
        y_coordinates_m=(0.0, 1.0),
        heights_m=((0.0, 0.1, 0.2), (0.3, 0.4, 0.5)),
    )
    static_surface = replace(
        header.scene.surface,
        x_coordinates_m=surface.x_coordinates_m,
        y_coordinates_m=surface.y_coordinates_m,
    )
    geometry = build_scene_geometry(
        replace(header, scene=replace(header.scene, surface=static_surface)),
        replace(frame, surface=surface),
    )

    assert (0.5, 0.0, 0.1) in geometry.surface.vertices
    assert (0.5, 1.0, 0.4) in geometry.surface.vertices


def test_surface_height_uses_rendered_triangle_interpolation(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    _, frame = records
    surface = replace(
        frame.surface,
        heights_m=((0.0, 2.0), (4.0, 10.0)),
    )
    replaced = replace(frame, surface=surface)

    assert surface_height_at(replaced, 0.75, 0.25) == pytest.approx(3.5)
    assert surface_height_at(replaced, 0.25, 0.75) == pytest.approx(4.5)
    assert surface_height_at(replaced, 1.0, 1.0) == pytest.approx(10.0)
    with pytest.raises(ValueError, match="outside"):
        surface_height_at(replaced, -0.01, 0.5)


def test_concave_boundary_clips_crossing_cells(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    header, frame = records
    boundary = ((0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (1.0, 1.0), (1.0, 2.0), (0.0, 2.0))
    scene = replace(
        header.scene,
        boundary_xy_m=boundary,
        inlet_positions_xy_m=((0.5, 0.5),),
        top_z_m=5.0,
        surface=replace(
            header.scene.surface,
            x_coordinates_m=(0.0, 1.0, 2.0),
            y_coordinates_m=(0.0, 1.0, 2.0),
        ),
    )
    surface = replace(
        frame.surface,
        x_coordinates_m=(0.0, 1.0, 2.0),
        y_coordinates_m=(0.0, 1.0, 2.0),
        heights_m=((0.0, 1.0, 2.0), (2.0, 3.0, 4.0), (4.0, 5.0, 5.0)),
    )
    geometry = build_scene_geometry(
        replace(header, scene=scene), replace(frame, surface=surface)
    )

    assert _projected_area(
        geometry.surface.vertices, geometry.surface.faces
    ) == pytest.approx(3.0)
    assert all(not (x > 1.0 and y > 1.0) for x, y, _ in geometry.surface.vertices)


def test_static_topology_clips_concave_boundary_without_a_frame(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    header, _ = records
    scene = replace(
        header.scene,
        boundary_xy_m=(
            (0.0, 0.0),
            (2.0, 0.0),
            (2.0, 1.0),
            (1.0, 1.0),
            (1.0, 2.0),
            (0.0, 2.0),
        ),
        inlet_positions_xy_m=((0.5, 0.5),),
        top_z_m=5.0,
        surface=replace(
            header.scene.surface,
            x_coordinates_m=(0.0, 1.0, 2.0),
            y_coordinates_m=(0.0, 1.0, 2.0),
        ),
    )

    topology = build_scene_geometry_topology(replace(header, scene=scene))
    surface_vertices = tuple((x, y, 0.0) for x, y in topology.surface_xy)

    assert _projected_area(surface_vertices, topology.surface_faces) == pytest.approx(
        3.0
    )
    assert topology.surface_xy == (
        (0.0, 0.0),
        (1.0, 0.0),
        (1.0, 0.5),
        (1.0, 1.0),
        (0.0, 1.0),
        (2.0, 0.0),
        (2.0, 1.0),
        (0.5, 1.5),
        (1.0, 2.0),
        (0.0, 2.0),
    )
    assert topology.surface_faces == (
        (0, 1, 2),
        (0, 2, 3),
        (0, 3, 4),
        (1, 5, 6),
        (1, 6, 2),
        (2, 6, 3),
        (4, 3, 7),
        (7, 3, 8),
        (4, 7, 9),
        (9, 7, 8),
    )
    assert topology.surface_boundary_edges == (
        (0, 1),
        (4, 0),
        (1, 5),
        (6, 3),
        (3, 8),
        (9, 4),
        (5, 6),
        (8, 9),
    )


def test_clipped_vertices_interpolate_original_surface(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    header, frame = records
    scene = replace(
        header.scene,
        boundary_xy_m=((0.0, 0.0), (1.0, 0.0), (0.0, 0.5)),
        inlet_positions_xy_m=((0.1, 0.1),),
        top_z_m=4.0,
    )
    surface = replace(
        frame.surface,
        heights_m=((0.0, 1.0), (2.0, 3.0)),
    )
    geometry = build_scene_geometry(
        replace(header, scene=scene), replace(frame, surface=surface)
    )

    assert _projected_area(
        geometry.surface.vertices, geometry.surface.faces
    ) == pytest.approx(0.25)
    assert all(z == pytest.approx(x + 2 * y) for x, y, z in geometry.surface.vertices)


def test_polygon_order_normalization_is_deterministic(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    header, frame = records
    first = build_scene_geometry(header, frame)
    boundary = header.scene.boundary_xy_m
    reordered = tuple(reversed(boundary[2:] + boundary[:2]))
    second = build_scene_geometry(
        replace(header, scene=replace(header.scene, boundary_xy_m=reordered)),
        frame,
    )

    assert second == first


def test_nonzero_floor_is_used_for_floor_and_walls(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    header, frame = records
    shifted_header = replace(
        header,
        scene=replace(header.scene, floor_z_m=-2.0, top_z_m=2.0),
    )
    geometry = build_scene_geometry(shifted_header, frame)

    assert {vertex[2] for vertex in geometry.floor.vertices} == {-2.0}
    assert {vertex[2] for vertex in geometry.walls.vertices} == {-2.0, 2.0}


def test_surface_volume_sides_extend_to_floor(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    header, frame = records
    surface = replace(
        frame.surface,
        heights_m=((1.0, 1.0), (1.0, 1.0)),
    )

    geometry = build_scene_geometry(header, replace(frame, surface=surface))

    assert len(geometry.volume_sides.faces) == 8
    assert {vertex[2] for vertex in geometry.volume_sides.vertices} == {0.0, 1.0}
    assert {(vertex[0], vertex[1]) for vertex in geometry.volume_sides.vertices} == set(
        header.scene.boundary_xy_m
    )


def test_empty_surface_has_no_volume_sides(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    header, frame = records
    surface = replace(
        frame.surface,
        heights_m=((0.0, 0.0), (0.0, 0.0)),
    )

    geometry = build_scene_geometry(header, replace(frame, surface=surface))

    assert geometry.volume_sides.vertices == ()
    assert geometry.volume_sides.faces == ()


def test_each_frame_rebuilds_the_complete_surface(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    header, frame = records
    replacement = replace(
        frame.surface,
        heights_m=((0.5, 0.5), (0.5, 0.5)),
    )

    geometry = build_scene_geometry(header, replace(frame, surface=replacement))

    assert {vertex[2] for vertex in geometry.surface.vertices} == {0.5}


def test_cached_topology_updates_heights_without_reclipping(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    header, frame = records
    topology = build_scene_geometry_topology(header)
    updated = replace(
        frame,
        surface=replace(
            frame.surface,
            heights_m=((0.2, 0.4), (0.6, 0.8)),
        ),
    )

    cached = topology.materialize(updated)
    rebuilt = build_scene_geometry(header, updated)

    assert cached.surface == rebuilt.surface
    assert cached.floor == rebuilt.floor
    assert cached.walls == rebuilt.walls
    assert len(cached.volume_sides.faces) == 8


def test_cached_topology_rejects_grid_changes(
    records: tuple[SceneDefinition, SceneFrame],
) -> None:
    header, frame = records
    topology = build_scene_geometry_topology(header)
    changed = replace(
        frame,
        surface=replace(frame.surface, cell_size_m=0.5),
    )

    with pytest.raises(ValueError, match="grid"):
        topology.materialize(changed)
