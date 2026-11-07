"""Exercise packaging against isolated commits, not developer working files."""

from io import BytesIO
import json
from pathlib import Path
import stat
import subprocess
import tarfile
import zipfile

from PIL import Image, PngImagePlugin
import pytest

from scripts import build_release as release


def git(repo: Path, *args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(repo), *args], stderr=subprocess.STDOUT)


def commit(repo: Path) -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "fixture")
    return git(repo, "rev-parse", "HEAD").decode().strip()


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / "repository"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Package Test")
    git(repo, "config", "user.email", "package@example.invalid")
    git(repo, "config", "core.autocrlf", "false")
    for name in release.REQUIRED_FILES | {"static/styles.css", "anima_webui/extra.py"}:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".png"):
            Image.new("RGB", (2, 2), "blue").save(path)
        else:
            path.write_text("fixture\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text('[project]\nname="anima-random-webui"\nversion="0.1.1"\n', encoding="utf-8")
    (repo / "static/index.html").write_text('<link href="/static/styles.css?v=1"><script src="/static/app.js"></script>', encoding="utf-8")
    (repo / "README.md").write_text('![Product](docs/readme/random-workbench.png)\n', encoding="utf-8")
    (repo / ".gitattributes").write_bytes((release.ROOT / ".gitattributes").read_bytes())
    for name in ("tests/fixture.json", "scripts/check.py", "data/history.sqlite3", "docs/acceptance-2026-11-07.md", ".github/workflows/ci.yml"):
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("private test data\n", encoding="utf-8")
    commit(repo)
    return repo


def make_zip(files, prefix="package/"):
    target = BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        for name, data in files.items():
            archive.writestr(prefix + name, data)
    return target.getvalue()


def exported_files(repo):
    archive = git(repo, "archive", "--format=zip", "HEAD")
    return release.archive_files(archive)


def test_build_is_reproducible_and_excludes_development_and_working_data(repository, tmp_path):
    original_commit = git(repository, "rev-parse", "HEAD").decode().strip()
    (repository / "data/local-token.txt").write_text("untracked user data")
    (repository / "static/app.js").write_text("uncommitted work")
    first = release.build_release(repository, "HEAD", tmp_path / "first")
    second = release.build_release(repository, "HEAD", tmp_path / "second")
    package = Path(first["artifact"])
    assert package.read_bytes() == Path(second["artifact"]).read_bytes()
    assert first["commit"] == original_commit
    contents = release.archive_files(package.read_bytes())
    assert len(contents) == len(release.REQUIRED_FILES) + 2
    assert contents["static/app.js"] == b"fixture\n"
    assert not any(name.startswith(("data/", "tests/", ".github/", "scripts/")) for name in contents)
    assert "docs/acceptance-2026-11-07.md" not in contents
    info = json.loads((package.parent / "build-info.json").read_text())
    assert set(info) == {"version", "commit", "artifact", "sha256"}
    assert (package.parent / "SHA256SUMS.txt").read_text() == f"{first['sha256']}  {package.name}\n"


def test_github_style_zip_and_tar_roots_are_accepted(repository):
    files = exported_files(repository)
    assert release.validate_bytes(make_zip(files, "owner-repo-abcd123/"), repository, "HEAD")["files"] == len(files)
    assert release.validate_bytes(git(repository, "archive", "--format=tar.gz", "--prefix=owner-repo-abcd123/", "HEAD"), repository, "HEAD")["files"] == len(files)


@pytest.mark.parametrize("name", ["data/history.sqlite3", "tests/example.json", "docs/acceptance.md", ".env"])
def test_extra_files_are_rejected(repository, name):
    files = exported_files(repository)
    files[name] = b"not for users"
    with pytest.raises(release.ReleaseError, match="manifest mismatch"):
        release.validate_bytes(make_zip(files), repository, "HEAD")


def test_missing_template_and_changed_content_are_rejected(repository):
    files = exported_files(repository)
    files.pop("templates/workflow_api_v2.json")
    with pytest.raises(release.ReleaseError, match="missing="):
        release.validate_bytes(make_zip(files), repository, "HEAD")
    files = exported_files(repository)
    files["static/app.js"] += b"tampered"
    with pytest.raises(release.ReleaseError, match="differs from source"):
        release.validate_bytes(make_zip(files), repository, "HEAD")


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/private", "root/../private", "root\\private", "root//private"])
def test_unsafe_archive_paths_are_rejected(name):
    # Windows ZipInfo normalizes backslashes on write; restore raw names in
    # both ZIP headers to exercise a downloaded archive with that input.
    archive = make_zip({name.replace("\\", "_"): b"bad"}, prefix="")
    archive = archive.replace(name.replace("\\", "_").encode(), name.encode())
    with pytest.raises(release.ReleaseError, match="Unsafe archive path"):
        release.archive_files(archive)


def test_duplicate_and_empty_unrelated_directories_are_rejected():
    target = BytesIO()
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("README.md", b"one")
        with pytest.warns(UserWarning, match="Duplicate"):
            archive.writestr("README.md", b"two")
    with pytest.raises(release.ReleaseError, match="Duplicate"):
        release.archive_files(target.getvalue())
    with pytest.raises(release.ReleaseError, match="Unexpected archive directories"):
        release.archive_files(make_zip({"README.md": b"readme", "data/": b""}, prefix=""))


@pytest.mark.parametrize("kind", ["zip", "tar"])
def test_links_are_rejected(kind):
    output = BytesIO()
    if kind == "zip":
        with zipfile.ZipFile(output, "w") as archive:
            item = zipfile.ZipInfo("README.md")
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(item, "private")
    else:
        with tarfile.open(fileobj=output, mode="w:gz") as archive:
            item = tarfile.TarInfo("README.md")
            item.type = tarfile.SYMTYPE
            item.linkname = "private"
            archive.addfile(item)
    with pytest.raises(release.ReleaseError, match="link or special"):
        release.archive_files(output.getvalue())


def test_missing_export_rules_fail_closed(repository, tmp_path):
    (repository / ".gitattributes").write_text("* text=auto\n", encoding="utf-8")
    commit(repository)
    with pytest.raises(release.ReleaseError, match="manifest mismatch"):
        release.build_release(repository, "HEAD", tmp_path / "output")
    assert not (tmp_path / "output").exists()


@pytest.mark.parametrize("text", ["ghp_" + "A" * 36, "C:\\Users\\real-person\\secret", "-----BEGIN PRIVATE KEY-----"])
def test_private_text_is_rejected_but_installation_examples_are_allowed(text):
    with pytest.raises(release.ReleaseError, match="credential or personal"):
        release.inspect_content("README.md", text.encode())
    release.inspect_content("README.md", b"Install at D:\\ComfyUI and F:\\animarandom")


def test_image_text_metadata_is_rejected():
    output = BytesIO()
    info = PngImagePlugin.PngInfo()
    info.add_text("parameters", "private prompt and seed")
    Image.new("RGB", (2, 2)).save(output, format="PNG", pnginfo=info)
    with pytest.raises(release.ReleaseError, match="image metadata"):
        release.inspect_content("docs/readme/random-workbench.png", output.getvalue())


def test_missing_html_resource_is_rejected(repository):
    (repository / "static/index.html").write_text('<script src="/static/missing.js"></script>', encoding="utf-8")
    commit(repository)
    with pytest.raises(release.ReleaseError, match="Missing HTML runtime asset"):
        release.validate_bytes(git(repository, "archive", "--format=zip", "HEAD"), repository, "HEAD")
