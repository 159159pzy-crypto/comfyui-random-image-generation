"""The source installer must share Atelier's receipt and preserve user changes."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from urllib.error import HTTPError, URLError

import pytest

from anima_webui import extension_install
from anima_webui.extension_install import DIRECTORY, RECEIPT, REQUIRED_FILES, inspect, install, manifest


@pytest.fixture
def environment(tmp_path):
    root, source = tmp_path / "ComfyUI", tmp_path / "source"
    root.mkdir()
    source.mkdir()
    (root / "main.py").write_text("# fixture")
    for filename in REQUIRED_FILES:
        (source / filename).write_text("# version one")
    return root, source


def test_check_is_read_only_and_install_is_idempotent(environment):
    root, source = environment
    assert inspect(root, source)["status"] == "missing"
    assert not (root / "custom_nodes").exists()
    first = install(root, source)
    assert first["changed"]
    assert json.loads((Path(first["target"]) / RECEIPT).read_text())["files"] == manifest(source)
    assert install(root, source, running=True)["changed"] is False


def test_update_keeps_backup_outside_custom_nodes(environment):
    root, source = environment
    install(root, source)
    (source / "__init__.py").write_text("# version two")
    assert inspect(root, source)["status"] == "outdated"
    result = install(root, source)
    backup = Path(result["backup"])
    assert backup.parent == root / ".anima-extension-backups"
    assert (backup / "__init__.py").read_text() == "# version one"
    assert manifest(Path(result["target"])) == manifest(source)


def test_running_service_blocks_new_install_and_update(environment):
    root, source = environment
    with pytest.raises(ValueError, match="停止"):
        install(root, source, running=True)
    assert not (root / "custom_nodes").exists()
    install(root, source)
    (source / "__init__.py").write_text("# version two")
    with pytest.raises(ValueError, match="停止"):
        install(root, source, running=True)


def test_user_modified_installation_is_preserved(environment):
    root, source = environment
    install(root, source)
    target = root / "custom_nodes" / DIRECTORY
    (target / "__init__.py").write_text("# my changes")
    with pytest.raises(ValueError, match="已被修改"):
        install(root, source)
    assert (target / "__init__.py").read_text() == "# my changes"


def test_identical_unmanaged_extension_is_reused(environment):
    root, source = environment
    install(root, source)
    receipt = root / "custom_nodes" / DIRECTORY / RECEIPT
    receipt.unlink()
    assert install(root, source)["changed"] is False
    assert not receipt.exists()


def test_failed_promotion_restores_previous_installation(environment, monkeypatch):
    root, source = environment
    install(root, source)
    (source / "__init__.py").write_text("# version two")
    original = Path.rename

    def fail_staging(self, target):
        if self.name.endswith(".disabled"):
            raise OSError("simulated disk failure")
        return original(self, target)

    monkeypatch.setattr(Path, "rename", fail_staging)
    with pytest.raises(OSError, match="disk failure"):
        install(root, source)
    assert (root / "custom_nodes" / DIRECTORY / "__init__.py").read_text() == "# version one"
    assert list((root / "custom_nodes").iterdir()) == [root / "custom_nodes" / DIRECTORY]


@pytest.mark.parametrize("missing", sorted(REQUIRED_FILES))
def test_incomplete_source_cannot_replace_shared_extension(environment, missing):
    root, source = environment
    install(root, source)
    original = manifest(root / "custom_nodes" / DIRECTORY)
    (source / missing).unlink()
    with pytest.raises(ValueError, match="缺少配套扩展文件"):
        install(root, source)
    assert manifest(root / "custom_nodes" / DIRECTORY) == original
    assert not (root / ".anima-extension-backups").exists()


def test_copy_failure_leaves_original_and_no_staging_tree(environment, monkeypatch):
    root, source = environment
    install(root, source)
    (source / "__init__.py").write_text("# version two")

    def fail_copy(*args):
        raise OSError("copy failed")

    monkeypatch.setattr(shutil, "copyfile", fail_copy)
    with pytest.raises(OSError, match="copy failed"):
        install(root, source)
    assert (root / "custom_nodes" / DIRECTORY / "__init__.py").read_text() == "# version one"
    assert list((root / "custom_nodes").iterdir()) == [root / "custom_nodes" / DIRECTORY]


@pytest.mark.parametrize("mutated_tree", ["source", "installed"])
def test_changes_during_staging_cancel_replacement(environment, monkeypatch, mutated_tree):
    root, source = environment
    install(root, source)
    target = root / "custom_nodes" / DIRECTORY
    (source / "__init__.py").write_text("# version two")
    original_copy = shutil.copyfile

    def copy_and_modify(input_path, output_path):
        result = original_copy(input_path, output_path)
        # After this file was staged, a user or another updater changes it.
        if input_path.name == "__init__.py":
            changed = source if mutated_tree == "source" else target
            (changed / "__init__.py").write_text("# concurrent change")
        return result

    monkeypatch.setattr(shutil, "copyfile", copy_and_modify)
    with pytest.raises((OSError, ValueError), match="变化|已被修改"):
        install(root, source)
    expected = "# version one" if mutated_tree == "source" else "# concurrent change"
    assert (target / "__init__.py").read_text() == expected
    assert not (root / ".anima-extension-backups").exists()
    assert list((root / "custom_nodes").iterdir()) == [target]


def test_failed_rollback_retains_backup_and_reports_recovery_path(environment, monkeypatch):
    root, source = environment
    install(root, source)
    (source / "__init__.py").write_text("# version two")
    original_rename = Path.rename

    def fail_promotion_and_recovery(self, target):
        if self.name.endswith(".disabled") or self.parent.name == ".anima-extension-backups":
            raise OSError("simulated disk failure")
        return original_rename(self, target)

    monkeypatch.setattr(Path, "rename", fail_promotion_and_recovery)
    with pytest.raises(OSError, match="无法自动恢复") as caught:
        install(root, source)
    backup, = (root / ".anima-extension-backups").iterdir()
    assert str(backup) in str(caught.value)
    assert (backup / "__init__.py").read_text() == "# version one"
    assert not list((root / "custom_nodes").iterdir())


def directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        # Directory junctions do not require Windows developer mode/admin rights.
        quote = lambda value: "'" + str(value).replace("'", "''") + "'"  # noqa: E731
        subprocess.run([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
            f"$ErrorActionPreference='Stop'; New-Item -ItemType Junction -Path {quote(link)} -Target {quote(target)} | Out-Null",
        ], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


@pytest.mark.parametrize("location", ["custom_nodes", "target", "backup", "nested"])
def test_windows_junctions_and_directory_links_never_change_external_tree(environment, tmp_path, location):
    root, source = environment
    external = tmp_path / "external"
    external.mkdir()
    (external / "keep.txt").write_text("user data")
    if location == "custom_nodes":
        link = root / "custom_nodes"
    else:
        install(root, source)
        (source / "__init__.py").write_text("# version two")
        if location == "target":
            target = root / "custom_nodes" / DIRECTORY
            # Preserve the real installation inside the test's temporary root.
            target.rename(root / "saved-extension")
            link = target
        elif location == "backup":
            link = root / ".anima-extension-backups"
        else:
            link = root / "custom_nodes" / DIRECTORY / "user-link"
    directory_link(link, external)
    with pytest.raises(ValueError, match="链接"):
        install(root, source)
    assert list(external.iterdir()) == [external / "keep.txt"]
    assert (external / "keep.txt").read_text() == "user data"


@pytest.mark.parametrize("reason", [TimeoutError("timeout"), OSError("network unavailable"), "unknown failure"])
def test_uncertain_service_probe_never_allows_installation(monkeypatch, reason):
    def failed_probe(*args, **kwargs):
        raise URLError(reason)

    monkeypatch.setattr(extension_install, "urlopen", failed_probe)
    with pytest.raises(OSError, match="无法确认 ComfyUI"):
        extension_install.running_at("http://127.0.0.1:8188")


@pytest.mark.parametrize("error, expected", [
    (URLError(ConnectionRefusedError("not listening")), False),
    (HTTPError("http://127.0.0.1:8188/system_stats", 503, "busy", {}, None), True),
])
def test_service_probe_distinguishes_refused_connection_from_http_response(monkeypatch, error, expected):
    def failed_probe(*args, **kwargs):
        raise error

    monkeypatch.setattr(extension_install, "urlopen", failed_probe)
    assert extension_install.running_at("http://127.0.0.1:8188") is expected


@pytest.mark.parametrize("url", ["https://localhost", "http://example.com", "http://user:pw@localhost", "http://localhost/path"])
def test_nonlocal_or_ambiguous_service_addresses_are_rejected_without_request(monkeypatch, url):
    def forbidden_probe(*args, **kwargs):
        pytest.fail("invalid address must not make a request")

    monkeypatch.setattr(extension_install, "urlopen", forbidden_probe)
    with pytest.raises(ValueError, match="本机回环 HTTP"):
        extension_install.running_at(url)
