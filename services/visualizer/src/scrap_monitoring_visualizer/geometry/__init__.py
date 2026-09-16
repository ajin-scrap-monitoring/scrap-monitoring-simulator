"""Deterministic mesh construction."""

from .mesh import (
    Mesh,
    SceneGeometry,
    build_scene_geometry,
    surface_height_at,
    triangulate_polygon,
)

__all__ = [
    "Mesh",
    "SceneGeometry",
    "build_scene_geometry",
    "surface_height_at",
    "triangulate_polygon",
]
