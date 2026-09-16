from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / ".agents" / "AGENTS.md"
REQUIRED_FILES = (
    ROOT / "README.md",
    ROOT / "docs" / "project-spec.md",
    ROOT / "docs" / "architecture.md",
    ROOT / "docs" / "development-plan.md",
    ROOT / ".agents" / "rules" / "project.md",
)
ENTRYPOINTS = {
    ROOT / "AGENTS.md": Path(".agents/AGENTS.md"),
    ROOT / "GEMINI.md": Path(".agents/AGENTS.md"),
    ROOT / ".claude" / "CLAUDE.md": Path("../.agents/AGENTS.md"),
}


def main() -> int:
    failures: list[str] = []
    if not SOURCE.is_file():
        failures.append("missing project instruction source: .agents/AGENTS.md")
    for path in REQUIRED_FILES:
        if not path.is_file():
            failures.append(f"missing required file: {path.relative_to(ROOT)}")
    for path, target in ENTRYPOINTS.items():
        if not path.is_symlink():
            failures.append(f"agent entrypoint is not a symlink: {path.relative_to(ROOT)}")
        elif path.readlink() != target:
            failures.append(
                f"unexpected symlink target: {path.relative_to(ROOT)} -> {path.readlink()}"
            )
    if failures:
        for failure in failures:
            print(f"NG: {failure}")
        return 1
    print("OK: repository structure")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
