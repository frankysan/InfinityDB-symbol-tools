"""Offline stdlib-only regression tests for immutable v3 manifest publication."""

from __future__ import annotations

import json
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from infinity_symbol_tools import snapshot_provenance as provenance

ACQUIRED_AT = datetime(2026, 11, 13, 11, 0, tzinfo=UTC)
SOURCE_URLS = {
    "army": "https://api.corvusbelli.com/army",
    "wiki": "https://infinitythewiki.com/",
    "symbols": "https://assets.corvusbelli.net/army/img/logo/units/",
}


def _pair(
    root: Path, *, snapshot_type: str = "army", source_url: str | None = None,
) -> tuple[Path, Path, dict]:
    archive = root / "data/raw" / f"{snapshot_type}-snapshot.zip"
    archive.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("test/source.txt", "Povél & immutable source")
    directory = root / "data/manifests/snapshots"
    args = {
        "snapshot_type": snapshot_type,
        "acquired_at": ACQUIRED_AT,
        "source_url": source_url or SOURCE_URLS[snapshot_type],
        "document_count": 1,
        "project_root": root,
        "language": "en" if snapshot_type != "symbols" else None,
        "source_data_changed_on": date(2026, 9, 3) if snapshot_type == "army" else None,
    }
    return archive, directory, args


class SnapshotManifestPublicationTests(TestCase):
    def setUp(self) -> None:
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_v3_manifest_format_and_replay_are_byte_identical(self) -> None:
        for kind in ("army", "wiki", "symbols"):
            with self.subTest(snapshot_type=kind):
                archive, directory, args = _pair(self.root / kind, snapshot_type=kind)
                expected = provenance.build_snapshot_manifest(archive, **args)
                self.assertEqual(expected["formatVersion"], 3)
                manifest = provenance.write_snapshot_manifest(archive, directory, **args)
                payload = (
                    json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                ).encode("utf-8")
                self.assertEqual(manifest.read_bytes(), payload)
                self.assertEqual(
                    provenance.load_snapshot_manifest(manifest, archive=archive), expected
                )
                self.assertEqual(
                    provenance.write_snapshot_manifest(archive, directory, **args), manifest
                )
                self.assertEqual(manifest.read_bytes(), payload)
                self.assertFalse(manifest.with_suffix(".json.tmp").exists())

    def test_preexisting_different_provenance_is_untouched(self) -> None:
        archive, directory, args = _pair(self.root)
        manifest = provenance.write_snapshot_manifest(archive, directory, **args)
        original = manifest.read_bytes()
        modified = {**args, "source_url": "https://api.corvusbelli.com/modified"}
        with self.assertRaisesRegex(provenance.SnapshotProvenanceError, "different content"):
            provenance.write_snapshot_manifest(archive, directory, **modified)
        self.assertEqual(manifest.read_bytes(), original)

    def test_crlf_manifest_is_different_even_with_identical_decoded_json(self) -> None:
        archive, directory, args = _pair(self.root)
        manifest = provenance.write_snapshot_manifest(archive, directory, **args)
        previous = manifest.read_bytes().replace(b"\n", b"\r\n")
        manifest.write_bytes(previous)
        with self.assertRaisesRegex(provenance.SnapshotProvenanceError, "different content"):
            provenance.write_snapshot_manifest(archive, directory, **args)
        self.assertEqual(manifest.read_bytes(), previous)

    def test_transaction_requires_new_manifest_even_for_identical_content(self) -> None:
        archive, directory, args = _pair(self.root)
        manifest = provenance.write_snapshot_manifest(archive, directory, **args)
        previous = manifest.read_bytes()
        with self.assertRaisesRegex(provenance.SnapshotProvenanceError, "already exists"):
            provenance.write_snapshot_manifest(archive, directory, require_new=True, **args)
        self.assertEqual(manifest.read_bytes(), previous)

    def test_transaction_rejects_race_winner_without_deleting_it(self) -> None:
        for identical in (True, False):
            with self.subTest(identical=identical):
                archive, directory, args = _pair(self.root / str(identical))
                target = directory / f"{archive.stem}.json"
                original_link = provenance.os.link
                winner = b""

                def competing_link(
                    source: Path, destination: Path, *,
                    same: bool = identical, link=original_link,
                ) -> None:
                    nonlocal winner
                    winner = source.read_bytes() if same else b"other provenance\n"
                    destination.write_bytes(winner)
                    link(source, destination)

                with patch.object(provenance.os, "link", competing_link):
                    with self.assertRaisesRegex(
                        provenance.SnapshotProvenanceError, "already exists"
                    ):
                        provenance.write_snapshot_manifest(
                            archive, directory, require_new=True, **args
                        )
                self.assertEqual(target.read_bytes(), winner)
                self.assertFalse(target.with_suffix(".json.tmp").exists())

    def test_stale_stage_is_never_deleted_or_replaced(self) -> None:
        archive, directory, args = _pair(self.root)
        directory.mkdir(parents=True)
        stage = directory / f"{archive.stem}.json.tmp"
        previous = b"other producer's incomplete source receipt"
        stage.write_bytes(previous)
        with self.assertRaisesRegex(
            provenance.SnapshotProvenanceError, "staging already exists"
        ):
            provenance.write_snapshot_manifest(archive, directory, **args)
        self.assertEqual(stage.read_bytes(), previous)
        self.assertFalse((directory / f"{archive.stem}.json").exists())

    def test_symlink_destination_is_rejected_even_if_dangling(self) -> None:
        archive, directory, args = _pair(self.root)
        target = directory / f"{archive.stem}.json"
        original_is_symlink = Path.is_symlink

        def fake_is_symlink(path: Path) -> bool:
            return path == target or original_is_symlink(path)

        with patch.object(Path, "is_symlink", fake_is_symlink):
            with self.assertRaisesRegex(provenance.SnapshotProvenanceError, "symlink"):
                provenance.write_snapshot_manifest(archive, directory, **args)
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix(".json.tmp").exists())

    def test_concurrent_winner_is_idempotent_or_rejected(self) -> None:
        for identical in (True, False):
            with self.subTest(identical=identical):
                archive, directory, args = _pair(self.root / str(identical))
                target = directory / f"{archive.stem}.json"
                expected = provenance.build_snapshot_manifest(archive, **args)
                canonical = (
                    json.dumps(expected, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
                ).encode("utf-8")
                competitor = canonical if identical else b"conflicting provenance\n"
                original_link = provenance.os.link

                def competing_link(
                    source: Path, destination: Path, *,
                    expected_target: Path = target, expected_payload: bytes = canonical,
                    winner: bytes = competitor, link=original_link,
                ) -> None:
                    self.assertEqual(destination, expected_target)
                    self.assertEqual(source.read_bytes(), expected_payload)
                    destination.write_bytes(winner)
                    link(source, destination)

                with patch.object(provenance.os, "link", competing_link):
                    if identical:
                        self.assertEqual(
                            provenance.write_snapshot_manifest(archive, directory, **args),
                            target,
                        )
                    else:
                        with self.assertRaisesRegex(
                            provenance.SnapshotProvenanceError, "different content"
                        ):
                            provenance.write_snapshot_manifest(archive, directory, **args)
                self.assertEqual(target.read_bytes(), competitor)
                self.assertFalse(target.with_suffix(".json.tmp").exists())

    def test_filesystem_without_hard_links_must_not_replace(self) -> None:
        archive, directory, args = _pair(self.root)
        target = directory / f"{archive.stem}.json"

        def no_links(_source: Path, _destination: Path) -> None:
            raise OSError("hard links unavailable")

        with patch.object(provenance.os, "link", no_links):
            with self.assertRaisesRegex(OSError, "hard links unavailable"):
                provenance.write_snapshot_manifest(archive, directory, **args)
        self.assertFalse(target.exists())
        self.assertFalse(target.with_suffix(".json.tmp").exists())

    def test_external_source_path_does_not_leak_host_identity(self) -> None:
        (self.root / "receiver").mkdir()
        archive, directory, args = _pair(self.root / "producer")
        args["project_root"] = self.root / "receiver"
        manifest = provenance.write_snapshot_manifest(archive, directory, **args)
        verified = provenance.load_snapshot_manifest(manifest, archive=archive)
        self.assertNotIn("path", verified["snapshot"]["archive"])

    def test_symbols_input_artifact_binding_is_unchanged(self) -> None:
        archive, directory, args = _pair(self.root, snapshot_type="symbols")
        army = self.root / "army.zip"
        with zipfile.ZipFile(army, "w") as output:
            output.writestr("metadata.json", "{}")
        args["input_artifact"] = army
        manifest = provenance.write_snapshot_manifest(archive, directory, **args)
        document = provenance.load_snapshot_manifest(manifest, archive=archive)
        self.assertEqual(document["inputArtifact"]["name"], army.name)
        self.assertEqual(document["inputArtifact"]["sha256"], provenance.sha256_file(army))
