from __future__ import annotations

import zipfile
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

_ARCHIVE_DATE_TIME = (1980, 1, 1, 0, 0, 0)
_ARCHIVE_MODE = 0o100644 << 16
_ARCHIVE_COMPRESSION = zipfile.ZIP_DEFLATED
_ARCHIVE_COMPRESSLEVEL = 6


def _archive_info(arcname: str) -> zipfile.ZipInfo:
    """Return metadata-normalized ZIP information for one snapshot member."""
    info = zipfile.ZipInfo(arcname, date_time=_ARCHIVE_DATE_TIME)
    info.compress_type = _ARCHIVE_COMPRESSION
    info.create_system = 3
    info.external_attr = _ARCHIVE_MODE
    info.extra = b""
    info.comment = b""
    return info


def create_timestamped_archive(
    files: Iterable[Path],
    destination: Path,
    *,
    prefix: str,
    root: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Archive exactly ``files`` using deterministic member metadata and ordering."""
    timestamp = (now or datetime.now().astimezone()).strftime("%Y%m%d-%H%M%S")
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"{prefix} {timestamp}.zip"
    sequence = 2
    while archive.exists():
        archive = destination / f"{prefix} {timestamp}-{sequence}.zip"
        sequence += 1

    items: list[tuple[str, Path]] = []
    seen_names: set[str] = set()
    seen_casefolded: dict[str, str] = {}
    for path in files:
        arcname = path.relative_to(root).as_posix() if root is not None else path.name
        if arcname in seen_names:
            raise ValueError(f"Duplicate archive path: {arcname}")
        casefolded = arcname.casefold()
        if previous := seen_casefolded.get(casefolded):
            raise ValueError(
                f"Case-only archive path collision: {previous!r} and {arcname!r}"
            )
        seen_names.add(arcname)
        seen_casefolded[casefolded] = arcname
        items.append((arcname, path))

    with zipfile.ZipFile(
        archive,
        "x",
        compression=_ARCHIVE_COMPRESSION,
        compresslevel=_ARCHIVE_COMPRESSLEVEL,
    ) as output:
        for arcname, path in sorted(items):
            output.writestr(
                _archive_info(arcname),
                path.read_bytes(),
                compress_type=_ARCHIVE_COMPRESSION,
                compresslevel=_ARCHIVE_COMPRESSLEVEL,
            )
    return archive
