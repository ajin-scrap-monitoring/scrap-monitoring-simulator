from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

REPOSITORY_ROOT = Path("..").resolve()


def _validate(schema_path: str, document_path: str) -> None:
    schema = json.loads((REPOSITORY_ROOT / schema_path).read_text(encoding="utf-8"))
    document = json.loads((REPOSITORY_ROOT / document_path).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(document)


def test_public_configuration_matches_versioned_schemas() -> None:
    for schema_path, document_path in (
        (
            "contracts/config/v1/simulation-server.schema.json",
            "config/simulation-server.v1.json",
        ),
        (
            "contracts/environment/v1/environment.schema.json",
            "config/environment.v1.json",
        ),
        (
            "contracts/quality/v1/quality-profile.schema.json",
            "config/quality-profile.v1.json",
        ),
    ):
        _validate(schema_path, document_path)
