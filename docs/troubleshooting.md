# Troubleshooting

## The app does not start from the build tree

The installed launcher is required. It hard-codes the install prefix and adds
that prefix to `GSETTINGS_SCHEMA_DIR` and `XDG_DATA_DIRS`:

```sh
meson install -C build
./build-install/bin/md_split examples/demo.md
```

Running a source-tree script directly can miss schemas, icons, or the
GResource.

## Missing schemas or icons

Check the install prefix:

```sh
GSETTINGS_SCHEMA_DIR="$PWD/build-install/share/glib-2.0/schemas" \
XDG_DATA_DIRS="$PWD/build-install/share:/usr/local/share:/usr/share" \
  ./build-install/bin/md_split
```

A `GtkBuilder` error mentioning `GtkSourceView` means the GtkSource GTypes were
not registered before `super().__init__`; keep the
`_ensure_gtksource_types()` call in `MdSplitWindow.__init__`.

## A UI change does not appear

Blueprint compiles `src/*.blp` to `build/src/*.ui`. If the generated file is
stale:

```sh
touch src/window.blp
meson compile -C build
ninja -C build -d explain
```

Do not edit `build/src/*.ui`; edit the `.blp` source.

## Tests skip without a display

Display tests exit with status 77 when `Gtk.init_check()` or
`Gdk.Display.get_default()` fails. Run them under Xvfb:

```sh
xvfb-run --auto-servernum python3 scripts/test.py
```

For X11/Xwayland, ibus can crash with a segmentation fault in
`gdk_x11_surface_get_xid`. This is an input-method/environment issue:

```sh
GTK_IM_MODULE=gtk-im-context-simple xvfb-run --auto-servernum python3 scripts/test.py
```

## Noisy GTK and libenchant warnings

These noisy upstream warnings are filtered because they do not indicate a
document error:

- `Trying to snapshot GtkGizmo ... without a current allocation` comes from
  `GtkTextView` updating scrollbar adjustments during snapshot
  ([GNOME/gtk#6057](https://gitlab.gnome.org/GNOME/gtk/-/work_items/6057)). It
  can coincide with temporary scrollbar glitches; update GTK for a fix.
- `gtkimcontextwayland.c:...:notify_surrounding_text: code should not be
  reached` comes from the Wayland text-input protocol's 4 KiB surrounding-text
  window when a large selection is active. It is harmless.
- `libenchant-WARNING ...: Error loading plugin: ...: cannot open shared
  object file` appears while the spelling backend probes provider plugins that
  are not installed. Dictionaries still load through an available provider
  such as hunspell.
- `Duplicate call to gdk_frame_timings_presented()` is a compositor frame-timing
  warning with slightly different timestamps; it does not indicate an editor
  failure.

The filter matches only those exact messages in the `Gtk`, `Gdk`, and
`libenchant` log domains, through both legacy and structured GLib logging;
other warnings still appear. Set `MD_SPLIT_SHOW_WARNINGS=1` to disable the
filter while debugging.

## Collect Python debug logs

Run the app with `MD_SPLIT_DEBUG=1` to enable Python `DEBUG` logging:

```sh
MD_SPLIT_DEBUG=1 ./build-install/bin/md_split examples/demo.md
```

`G_MESSAGES_DEBUG` controls GLib's own log domains and does not change the
app's log level.

## Flatpak build fails with `Invalid cross-device link`

In a container whose `/var/lib/flatpak` is an overlayfs layer, updating a
runtime can fail with `EXDEV`. Mount the Flatpak installation on a named volume:

```yaml
container:
  options: --privileged
  volumes:
    - flatpak-installation:/var/lib/flatpak
```

The CI workflow uses this workaround. On a normal desktop installation, the
system Flatpak directory is already on a normal filesystem.

## Flatpak says `first statement must be a call to project()`

The source archive was flattened by `flatpak-builder`. Archive sources strip
one leading path component. Recreate the archive with a top-level directory:

```sh
git archive --format=tar.gz --prefix="md-split-$VERSION/" \
  -o "dist/md-split-$VERSION.tar.gz" "$TAG"
```

See [releasing.md](releasing.md).

## Flatpak cannot open a command-line file

Flatpak only preserves the working directory when the app can see it. The
manifest shares your home directory, so relative paths work from a directory
under `$HOME`:

```sh
flatpak run io.github.james2doyle.MdSplit README.md
flatpak run io.github.james2doyle.MdSplit ~/notes/README.md
```

For a file outside home, grant access to its folder first with Flatseal
(**Filesystem > Other files**) or:

```sh
flatpak override --user --filesystem=/path/to/folder \
  io.github.james2doyle.MdSplit
```

Then restart the app and pass the absolute path. Without that access Flatpak
cannot see the file even though it exists on the host; the app reports a
sandbox permission error instead of the raw `g-io-error-quark` message.

## PDF export fails

PDF export uses the optional Cairo/Pango bindings. If pycairo is unavailable,
the app still starts, but the export action reports an error. Install the
system package (for example `python3-cairo` on Fedora) or use the Flatpak
runtime, which includes it. A failed write removes its temporary file.

## The AppImage does not start

If the AppImage reports that FUSE is unavailable, run it with the embedded
runtime:

```sh
./dist/md_split-*.AppImage --appimage-extract-and-run
```

The AppImage uses a static type2 runtime with FUSE3, so libfuse2 is not
required; the host still needs `/dev/fuse` and `fusermount3` for the default
mount mode. The release workflow uses `--appimage-extract` and runs
`squashfs-root/AppRun` directly, which avoids FUSE entirely. If GTK reports a
missing display or graphics library, confirm the host has working X11 or
Wayland and GPU drivers; the AppImage intentionally uses host graphics
drivers.

## AppImage build fails

`scripts/build-appimage.sh` requires podman or Docker, network access, and a
working Ubuntu 26.04 container. Remove `packaging/appimage/AppDir` and
`build-appimage` before retrying a failed run. The script pins
`appimage-builder`, `appimagetool`, and the type2 runtime, and verifies their
SHA-256 sums before repacking. See the container output for missing packages,
download failures, or checksum mismatches. The exact pinned versions live in
`scripts/build-appimage.sh`.

## Spell checking is unavailable

MD Split disables spell checking when libspelling, its provider, or a
dictionary is missing. Check the backend directly:

```sh
python3 -c 'import gi; gi.require_version("Spelling", "1"); from gi.repository import Spelling; Spelling.init(); print(Spelling.Checker.get_default().get_provider().list_languages().get_n_items())'
```

The app logs one `Spell checking disabled` warning at startup that names the
missing namespace, provider, or dictionary source. Install a dictionary package
for your locale and reopen the app. The Flatpak image bundles libspelling and
the GNOME runtime ships hunspell dictionaries, so a Flatpak build should not
need the typelib installed by the host. If dictionaries live outside the
standard directories, force detection with `MD_SPLIT_SPELLCHECK=1`.

### Custom dictionaries in Flatpak

The Flatpak shares your home directory, so dictionaries under
`~/.local/share/hunspell` or `~/.local/share/myspell` are visible. If your
dictionaries live elsewhere, grant read-only access to that directory:

- In Flatseal, select MD Split, open **Filesystem > Other files**, and add
  `~/.local/share/hunspell:ro`.
- Or run:

  ```sh
  flatpak override --user \
    --filesystem=~/.local/share/hunspell:ro \
    io.github.james2doyle.MdSplit
  ```

Restart MD Split after changing the permission.

## Recovery drafts or backups

Drafts and backups live under `$XDG_STATE_HOME/md-split`. Inspect them with:

```sh
ls "$XDG_STATE_HOME/md-split/recovery"
ls "$XDG_STATE_HOME/md-split/backups"
```

A draft is removed after a successful save or when you discard it from the
recovery prompt. Set `autosave` to false to stop new drafts.

## Session restore or recent files behave unexpectedly

Session paths and recent files are ordinary list settings:

```sh
gsettings get io.github.james2doyle.MdSplit session-files
gsettings get io.github.james2doyle.MdSplit recent-files
```

Missing session paths are skipped with a warning and removed on the next
restore. Use **Clear Recent** in the recent-files menu to empty the recent list.

## Remote images do not load

Remote images are disabled by default. Enable **Load Remote Images** from the
main menu or Preferences. Only HTTP(S) images are fetched; non-HTTP schemes,
redirects to other schemes, images over 10 MiB, and images over 20 megapixels
are rejected.

## Preview is paused

The preview refuses to build an unsafe widget tree for documents over 250,000
characters or 10,000 lines. The editor remains usable; switch to Source Only or
split the document. Word counting also pauses above 2,000,000 characters.

## The file will not open

Document reads are limited to 64 MiB. Larger files, invalid UTF-8, and missing
permissions produce an error dialog. The current document is left unchanged.

## The demo keeps loading

The bundled demo loads only when the app starts without a file and
`load-demo-on-startup` is enabled. Disable it in Preferences or run:

```sh
gsettings set io.github.james2doyle.MdSplit load-demo-on-startup false
```

For an uninstalled prefix, add
`GSETTINGS_SCHEMA_DIR="$PWD/build/data"` before `gsettings`.

## CLI flags conflict

A source-only flag combined with a preview-only flag exits with status 1, as
does `--light` with `--dark`. The aliases are:

- `--no-source` = `--preview-only`
- `--no-preview` = `--source-only`

Run `md_split --help` for the current list.

## Reset settings

```sh
gsettings reset-recursively io.github.james2doyle.MdSplit
```

Use the same `GSETTINGS_SCHEMA_DIR` as the running app when resetting an
uninstalled prefix.
