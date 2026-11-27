#!/usr/bin/env python3
"""Restart-on-save development runner for MD Split.

Watches src/, data/ and the top-level build files for changes, rebuilds via
meson, and relaunches the app. Enables the GTK Inspector
(GTK_DEBUG=interactive) unless GTK_DEBUG is already set. Dev-only helper:
not installed, not part of the Flatpak build.
"""

import os
import signal
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
gi.require_version("GLibUnix", "2.0")
from gi.repository import Gio, GLib, GLibUnix

ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD_DIR = os.path.join(ROOT, "build")
INSTALL_DIR = os.path.join(ROOT, "build-install")
APP = os.path.join(INSTALL_DIR, "bin", "md_split")
WATCH_DIRS = ("src", "data")
WATCH_SUFFIXES = (".blp", ".py", ".css", ".xml", ".in")
WATCH_NAMES = ("meson.build",)
DEBOUNCE_MS = 150
BUILD_EVENTS = (
    Gio.FileMonitorEvent.CHANGED,
    Gio.FileMonitorEvent.CREATED,
    Gio.FileMonitorEvent.DELETED,
    Gio.FileMonitorEvent.RENAMED,
    Gio.FileMonitorEvent.MOVED,
)

proc = None
pending = None


def log(message):
    print(f"[dev] {message}", flush=True)


def app_env():
    """Child environment for the app: unbuffered logs, inspector by default."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    # A surrounding AppImage (such as the terminal running this script) has its
    # own APPDIR. The development child uses the local Meson prefix instead.
    env.pop("APPDIR", None)
    # GTK_DEBUG=interactive opens the GTK Inspector; a user-set GTK_DEBUG wins.
    env.setdefault("GTK_DEBUG", "interactive")
    return env


def build_and_install():
    """Compile and install into the local prefix. Returns True on success."""
    result = subprocess.run(
        ["meson", "compile", "-C", "build"],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode == 0:
        result = subprocess.run(
            ["meson", "install", "-C", "build"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
    if result.returncode != 0:
        print(result.stdout, end="", file=sys.stderr, flush=True)
        return False
    return True


def bootstrap():
    if not os.path.exists(os.path.join(BUILD_DIR, "build.ninja")):
        log("configuring meson (first run)")
        result = subprocess.run(
            ["meson", "setup", "build", f"--prefix={INSTALL_DIR}"], cwd=ROOT
        )
        if result.returncode != 0:
            return False
    if not build_and_install():
        log("initial build failed")
        return False
    return True


def stop_child():
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def rebuild_and_restart():
    global pending, proc
    pending = None
    if not build_and_install():
        log("build failed — keeping the running instance")
        return False
    log("build ok — restarting app")
    stop_child()
    proc = subprocess.Popen(
        [APP], cwd=ROOT, env=app_env()
    )
    return False


def schedule_restart():
    global pending
    if pending is not None:
        GLib.Source.remove(pending)
    pending = GLib.timeout_add(DEBOUNCE_MS, rebuild_and_restart)


def on_changed(monitor, file, other_file, event_type):
    path = file.get_path()
    if path is None or event_type not in BUILD_EVENTS:
        return
    name = os.path.basename(path)
    if not (name.endswith(WATCH_SUFFIXES) or name in WATCH_NAMES):
        return
    schedule_restart()


def main():
    global loop, proc
    if not bootstrap():
        return 1
    loop = GLib.MainLoop()

    def on_sigint(*args):
        log("shutting down")
        stop_child()
        loop.quit()
        return GLib.SOURCE_REMOVE

    monitors = []
    for watch_dir in WATCH_DIRS:
        for dirpath, dirnames, _ in os.walk(os.path.join(ROOT, watch_dir)):
            dirnames[:] = [d for d in dirnames if not d.startswith(".")]
            monitor = Gio.File.new_for_path(dirpath).monitor_directory(
                Gio.FileMonitorFlags.NONE, None
            )
            monitor.connect("changed", on_changed)
            monitors.append(monitor)
    # Watch the project root without descending into build/ or .venv/.
    monitor = Gio.File.new_for_path(ROOT).monitor_directory(
        Gio.FileMonitorFlags.NONE, None
    )
    monitor.connect("changed", on_changed)
    monitors.append(monitor)
    for signum in (signal.SIGINT, signal.SIGTERM):
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signum, on_sigint)
    log("starting app (watching src/, data/ and meson.build)")
    proc = subprocess.Popen(
        [APP], cwd=ROOT, env=app_env()
    )
    loop.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
