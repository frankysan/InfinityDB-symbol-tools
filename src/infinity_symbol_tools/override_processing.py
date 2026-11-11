"""Resolve reviewed SVG overrides against immutable upstream symbol identities.

The caller supplies authoritative URL-to-override paths. These may differ
from raw ZIP archive paths, particularly for static characteristics/orders.
No application identity, HTTP access, or publication logic is present.
"""

from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


@dataclass(frozen=True)
class EffectiveSymbol:
    body: bytes | None
    source_method: str
    upstream_sha256: str | None
    effective_sha256: str | None
    override_path: str | None


@dataclass(frozen=True)
class OverrideResolution:
    symbols: dict[str, EffectiveSymbol]
    unused_overrides: tuple[str, ...]
    direct_override_count: int
    propagated_override_count: int


def _checked_svg(body: bytes, *, source: str) -> bytes:
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid SVG from {source}: {exc}") from exc
    if root.tag.rsplit("}", 1)[-1].casefold() != "svg":
        raise ValueError(f"Invalid SVG from {source}: root element is not <svg>")
    return body


def _portable_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError(f"Invalid override lookup path: {value!r}")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.suffix.casefold() != ".svg"
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"Invalid override lookup path: {value!r}")
    return path


def _override_files(root: Path) -> dict[str, Path]:
    if not root.exists():
        return {}
    if not root.is_dir():
        raise ValueError(f"Override root is not a directory: {root}")
    result: dict[str, Path] = {}
    for file in sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    ):
        if file.suffix.casefold() != ".svg":
            continue
        if file.is_symlink():
            raise ValueError(f"Override file cannot be a symbolic link: {file}")
        name = file.relative_to(root).as_posix()
        key = name.casefold()
        if key in result:
            raise ValueError(
                "Case-insensitive image override collision: "
                f"{result[key].relative_to(root).as_posix()} and {name}"
            )
        result[key] = file
    return result


def resolve_reviewed_overrides(
    upstream: dict[str, bytes | None],
    override_paths: dict[str, str],
    override_root: Path,
) -> OverrideResolution:
    """Apply direct and category-scoped upstream-equivalent SVG overrides.

    Every upstream URL must have exactly one independently reviewed lookup
    path. An override on one member of a byte-identical upstream category
    propagates to its peers. Conflicting overrides for that identity fail.
    """
    if set(upstream) != set(override_paths):
        raise ValueError("Override lookup URLs differ from pinned upstream source URLs")
    for path in override_paths.values():
        _portable_path(path)

    files = _override_files(override_root)
    direct: dict[str, tuple[str, str, bytes, str]] = {}
    for url in sorted(upstream):
        relative = override_paths[url]
        key = relative.casefold()
        file = files.get(key)
        if file is None:
            continue
        body = _checked_svg(file.read_bytes(), source=f"override {file}")
        direct[url] = (
            key,
            file.relative_to(override_root).as_posix(),
            body,
            hashlib.sha256(body).hexdigest(),
        )

    groups: dict[
        tuple[str, str],
        list[tuple[str, str, str, bytes, str]],
    ] = {}
    for url, (key, relative, body, digest) in direct.items():
        raw = upstream[url]
        if raw is None:
            continue
        checked = _checked_svg(raw, source=f"upstream {url}")
        category = _portable_path(override_paths[url]).parent.as_posix().casefold()
        groups.setdefault((category, hashlib.sha256(checked).hexdigest()), []).append(
            (url, key, relative, body, digest)
        )

    selected_groups: dict[tuple[str, str], tuple[str, str, str, bytes, str]] = {}
    used: set[str] = set()
    for group_key, candidates in sorted(groups.items()):
        if len({candidate[4] for candidate in candidates}) > 1:
            details = "; ".join(
                f"{relative} -> {url}"
                for url, _key, relative, _body, _digest in sorted(candidates)
            )
            raise ValueError(
                "Conflicting image overrides target the same upstream symbol "
                f"({group_key[1]}): {details}"
            )
        selected_groups[group_key] = min(
            candidates, key=lambda row: (row[2].casefold(), row[0])
        )
        used.update(row[1] for row in candidates)

    result: dict[str, EffectiveSymbol] = {}
    direct_count = 0
    propagated = 0
    for url in sorted(upstream):
        raw = upstream[url]
        raw_digest: str | None = None
        if raw is not None:
            raw = _checked_svg(raw, source=f"upstream {url}")
            raw_digest = hashlib.sha256(raw).hexdigest()
        category = _portable_path(override_paths[url]).parent.as_posix().casefold()
        selected = selected_groups.get((category, raw_digest)) if raw_digest else None
        matching_direct = direct.get(url)
        if selected is None and matching_direct is not None:
            key, relative, body, digest = matching_direct
            selected = (url, key, relative, body, digest)
            used.add(key)
        if selected is not None:
            body = selected[3]
            if matching_direct is None:
                propagated += 1
            else:
                direct_count += 1
            method = "override"
            override_path = selected[2]
        else:
            body = raw
            method = "network"
            override_path = None
        result[url] = EffectiveSymbol(
            body=body,
            source_method=method,
            upstream_sha256=raw_digest,
            effective_sha256=hashlib.sha256(body).hexdigest() if body is not None else None,
            override_path=override_path,
        )
    return OverrideResolution(
        symbols=result,
        unused_overrides=tuple(sorted(
            file.relative_to(override_root).as_posix()
            for key, file in files.items() if key not in used
        )),
        direct_override_count=direct_count,
        propagated_override_count=propagated,
    )
