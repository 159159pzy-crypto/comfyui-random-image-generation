from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anima_webui.persistence import backup_corrupt_file, backup_for_migration


class StorageBackupTests(unittest.TestCase):
    def test_corrupt_backup_never_overwrites_an_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_bytes(b"latest damaged data")
            previous = path.with_name("data.json.corrupt.bak")
            previous.write_bytes(b"older irreplaceable data")
            # Model an exists/open race: a concurrent process reserves the
            # name immediately after an existence check reports it absent.
            with patch.object(Path, "exists", return_value=False):
                result = backup_corrupt_file(path)
            self.assertEqual(previous.read_bytes(), b"older irreplaceable data")
            self.assertEqual(result.read_bytes(), b"latest damaged data")
            self.assertNotEqual(result, previous)

    def test_migration_backup_never_overwrites_an_existing_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_bytes(b"latest data")
            previous = path.with_name("data.json.v1.bak")
            previous.write_bytes(b"older data")
            with patch.object(Path, "exists", return_value=False):
                result = backup_for_migration(path, 1)
            self.assertEqual(previous.read_bytes(), b"older data")
            self.assertEqual(result.read_bytes(), b"latest data")

    def test_failed_backup_copy_removes_only_its_incomplete_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.json"
            path.write_bytes(b"complete data")
            previous = path.with_name("data.json.v1.bak")
            previous.write_bytes(b"older data")

            def partial_copy(source, destination):
                Path(destination).write_bytes(b"partial")
                raise OSError("disk full")

            with patch("anima_webui.persistence.shutil.copy2", side_effect=partial_copy):
                with self.assertRaisesRegex(OSError, "disk full"):
                    backup_for_migration(path, 1)
            self.assertEqual(path.read_bytes(), b"complete data")
            self.assertEqual(previous.read_bytes(), b"older data")
            self.assertEqual(list(Path(directory).glob("*.bak")), [previous])
