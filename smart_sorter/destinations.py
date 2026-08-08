from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath


# Format tokens that may appear inside a destination subpath and are filled in
# per file at planning time. Anything else in braces is a configuration typo.
_FORMAT_TOKENS = {"year", "yyyy", "month", "mm", "day", "dd", "yyyy-mm", "ext", "category", "source"}
_TOKEN_RE = re.compile(r"\{([^}]*)\}")
_ROOT_RE = re.compile(r"^\{([A-Za-z][A-Za-z0-9 _-]*)\}[\\/]?(.*)$", re.DOTALL)
# Characters Windows forbids in a path component (we sort onto Windows).
_ILLEGAL_COMPONENT = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


@dataclass(frozen=True)
class RenderContext:
    when: datetime
    ext: str
    category: str
    source: str


def split_root(template: str) -> tuple[str | None, str]:
    """Return (root_token, subpath). root_token is None for a legacy relative path."""
    match = _ROOT_RE.match(template.strip())
    if match:
        return match.group(1), match.group(2)
    return None, template.strip()


def _components(subpath: str) -> list[str]:
    normalized = subpath.replace("\\", "/")
    return [part for part in PurePosixPath(normalized).parts if part not in ("", ".")]


def validate_template(template: str, *, root_tokens: set[str], default_root: str) -> str:
    """Validate a destination template at config-load time. Raises ValueError."""
    root, subpath = split_root(template)
    if root is not None and root not in root_tokens:
        raise ValueError(
            f"Unknown destination root {{{root}}}; known roots: {sorted(root_tokens)}"
        )
    if root is None and default_root not in root_tokens:
        raise ValueError(f"Default destination root '{default_root}' is not defined")

    pure = PureWindowsPath(subpath)
    if pure.is_absolute() or (pure.drive or pure.root):
        raise ValueError(f"Destination subpath must be relative: {template!r}")
    for part in _components(subpath):
        if part == "..":
            raise ValueError(f"Destination must stay inside its root (no '..'): {template!r}")
        for token in _TOKEN_RE.findall(part):
            if token not in _FORMAT_TOKENS:
                raise ValueError(
                    f"Unknown format token {{{token}}} in {template!r}; "
                    f"allowed: {sorted(_FORMAT_TOKENS)}"
                )
    return template


def _clean_component(value: str) -> str:
    cleaned = _ILLEGAL_COMPONENT.sub("", value).strip().strip(".")
    return cleaned or "Unknown"


def _render_component(part: str, ctx: RenderContext) -> str:
    values = {
        "year": f"{ctx.when.year:04d}",
        "yyyy": f"{ctx.when.year:04d}",
        "month": f"{ctx.when.month:02d}",
        "mm": f"{ctx.when.month:02d}",
        "day": f"{ctx.when.day:02d}",
        "dd": f"{ctx.when.day:02d}",
        "yyyy-mm": f"{ctx.when.year:04d}-{ctx.when.month:02d}",
        "ext": ctx.ext.lstrip(".").casefold() or "none",
        "category": ctx.category,
        "source": ctx.source,
    }
    rendered = _TOKEN_RE.sub(lambda m: values.get(m.group(1), m.group(0)), part)
    return _clean_component(rendered)


def resolve_destination(
    template: str,
    filename: str,
    *,
    root_map: dict[str, Path],
    default_root: str,
    ctx: RenderContext,
) -> Path:
    """Resolve a validated template to an absolute directory/file path for one file."""
    root, subpath = split_root(template)
    root_name = root if root is not None else default_root
    root_dir = root_map[root_name].resolve()
    target_dir = root_dir
    for part in _components(subpath):
        target_dir = target_dir / _render_component(part, ctx)
    final = (target_dir / filename).resolve()
    # Defence in depth: never let a resolved path escape its declared root.
    try:
        final.relative_to(root_dir)
    except ValueError:
        raise ValueError(f"Resolved destination escaped its root: {final}") from None
    return final
