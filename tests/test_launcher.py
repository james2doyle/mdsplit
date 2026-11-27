"""Headless tests for application and development process launchers."""

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

SOURCE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SOURCE_ROOT)

from src import launcher


def uri_for(path):
    return Path(path).as_uri()


with tempfile.TemporaryDirectory() as tmp:
    doc = Path(tmp) / "notes.md"
    doc.write_text("# Notes\n")
    other = Path(tmp) / "diagram.png"
    other.write_bytes(b"png")
    fragment_uri = uri_for(doc) + "#section"
    query_uri = uri_for(doc) + "?page=2"
    missing_uri = uri_for(Path(tmp) / "missing.md")

    # A local, existing .md file produces a new-instance command.
    with mock.patch.object(
        launcher, "_launcher_program", return_value="/usr/bin/md_split"
    ):
        command = launcher.markdown_instance_command(uri_for(doc))
        assert command == [
            "/usr/bin/md_split",
            "--new-instance",
            str(doc),
        ], command

        # Fragments and queries are stripped; the file must still resolve.
        assert launcher.markdown_instance_command(fragment_uri) == command
        assert launcher.markdown_instance_command(query_uri) == command

        # Spawn reports success without waiting on the child process.
        with mock.patch.object(
            launcher.subprocess, "Popen", autospec=True
        ) as popen:
            assert launcher.spawn_markdown_instance(uri_for(doc)) is True
            popen.assert_called_once()
            assert popen.call_args.kwargs.get("start_new_session") is True

    # Non-markdown targets keep the regular launcher behaviour.
    with mock.patch.object(
        launcher, "_launcher_program", return_value="/usr/bin/md_split"
    ):
        assert launcher.markdown_instance_command(uri_for(other)) is None

    # Missing files, non-file schemes and absent executables fall back.
    assert launcher.markdown_instance_command(missing_uri) is None
    assert launcher.markdown_instance_command("https://example.com/a.md") is None
    assert launcher.markdown_instance_command("mailto:x@example.com") is None
    assert launcher.markdown_instance_command("not a uri at all") is None
    with mock.patch.object(
        launcher, "_launcher_program", return_value=None
    ):
        assert launcher.markdown_instance_command(uri_for(doc)) is None
        assert launcher.spawn_markdown_instance(uri_for(doc)) is False

# Spawn failure falls back to the caller, which uses the file launcher.
with mock.patch.object(
    launcher, "_launcher_program", return_value="/usr/bin/md_split"
), mock.patch.object(
    launcher.subprocess,
    "Popen",
    side_effect=OSError("exec failed"),
):
    assert launcher.spawn_markdown_instance(uri_for(doc)) is False

# The launcher executable lookup finds something usable in a real install.
program = launcher._launcher_program()
assert program is not None and os.path.isfile(program), program

# A terminal or other host AppImage must not redirect the development child
# away from the repository's local install prefix.
import dev

with mock.patch.dict(os.environ, {"APPDIR": "/tmp/host-app"}):
    child_env = dev.app_env()
    assert "APPDIR" not in child_env
    assert os.environ["APPDIR"] == "/tmp/host-app"

# The installed launcher relocates only when APPDIR contains MD Split's
# resource. APPIMAGE alone is not evidence: an AppImage-hosted terminal also
# exports both variables to Flatpak, but its AppDir is unrelated to MD Split.
launcher_source = (Path(SOURCE_ROOT) / "src" / "md_split.in").read_text(
    encoding="utf-8"
)
launcher_source = launcher_source.split("if __name__ == '__main__':")[0]
launcher_source = (
    launcher_source.replace("@VERSION@", "test")
    .replace("@pkgdatadir@", "/configured/share/md_split")
    .replace("@localedir@", "/configured/share/locale")
)


def launcher_pkgdatadir(appdir, appimage=None):
    with mock.patch.dict(os.environ, {}, clear=False):
        os.environ.pop("APPIMAGE", None)
        if appdir is None:
            os.environ.pop("APPDIR", None)
        else:
            os.environ["APPDIR"] = str(appdir)
        if appimage is not None:
            os.environ["APPIMAGE"] = appimage
        namespace = {}
        exec(compile(launcher_source, "md_split.in", "exec"), namespace)
        return namespace["pkgdatadir"]


with tempfile.TemporaryDirectory() as appdir:
    resource = (
        Path(appdir) / "usr" / "share" / "md_split" / "md_split.gresource"
    )

    # An unrelated host AppDir falls back to the configured install path even
    # when both APPDIR and APPIMAGE are exported by the host terminal.
    assert launcher_pkgdatadir(appdir) == "/configured/share/md_split"
    assert (
        launcher_pkgdatadir(appdir, "/tmp/host.AppImage")
        == "/configured/share/md_split"
    )

    resource.parent.mkdir(parents=True)
    resource.write_bytes(b"resource")

    # A valid AppDir relocates both directly from an AppImage and when it has
    # been extracted, in which case APPIMAGE is no longer set.
    assert launcher_pkgdatadir(appdir) == str(resource.parent)
    assert (
        launcher_pkgdatadir(appdir, "/opt/md_split.AppImage")
        == str(resource.parent)
    )

# APPIMAGE without APPDIR cannot identify a relocated resource either.
assert (
    launcher_pkgdatadir(None, "/tmp/host.AppImage")
    == "/configured/share/md_split"
)

print("ok: process launchers")
