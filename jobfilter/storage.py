from __future__ import annotations

from pathlib import Path


EXCLUDED_NAMES = {"Job App Docs", "Bewerbungen"}


def _size(path: Path) -> int:
    if path.is_symlink():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        children = list(path.iterdir())
    except OSError:
        return 0
    for child in children:
        total += _size(child)
    return total


def project_size(root: str | Path) -> dict[str, object]:
    base = Path(root).resolve()
    items = []
    try:
        paths = list(base.iterdir())
    except OSError:
        paths = []
    for path in paths:
        if path.name in EXCLUDED_NAMES:
            continue
        items.append({"name": path.name, "bytes": _size(path)})
    items.sort(key=lambda item: int(item["bytes"]), reverse=True)
    return {"root": str(base), "bytes": sum(int(item["bytes"]) for item in items), "largest": items[:5]}


def human_size(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} GB"


def format_size(report: dict[str, object]) -> str:
    lines = [f"Project: {human_size(int(report['bytes']))} (excluding Job App Docs and Bewerbungen)"]
    lines.extend(f"  {item['name']}: {human_size(int(item['bytes']))}" for item in report["largest"])
    return "\n".join(lines)
