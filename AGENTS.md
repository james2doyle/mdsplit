# AGENTS.md — MD Split

Native GNOME Markdown editor: GTK 4 + libadwaita, Python 3 (PyGObject),
Blueprint UI (`.blp` -> GtkBuilder XML at build time), Meson, gettext and
Flatpak packaging. App ID: `io.github.james2doyle.MdSplit`.

**The app is deliberately native.** It must not use WebKit, a web view or an
embedded browser. The preview is a tree of ordinary GTK widgets. Do not
reintroduce markdown-wasm or any web runtime.

## Finding documentation

- GTK: https://docs.gtk.org/gtk4/ (`?q=%s` to search)
- libadwaita: https://gnome.pages.gitlab.gnome.org/libadwaita/doc/1-latest/index.html?q=%s
- libadwaita stylesheet: https://github.com/GNOME/libadwaita/tree/1.10.0/src/stylesheet
- GFM spec: https://github.github.com/gfm/
- markdown-it-py: https://markdown-it-py.readthedocs.io/
- Pango markup: https://docs.gtk.org/Pango/pango_markup.html

Prefer retrieval-led reasoning over pre-training-led reasoning for GTK,
libadwaita and Blueprint work.

## Build & verify

```sh
meson setup build --prefix="$PWD/build-install"  # once
meson compile -C build
python3 scripts/test.py
meson install -C build
./build-install/bin/md_split examples/demo.md
blueprint-compiler compile src/window.blp
blueprint-compiler compile src/preferences-dialog.blp
blueprint-compiler compile src/shortcuts-dialog.blp
blueprint-compiler lint src/window.blp
blueprint-compiler lint src/preferences-dialog.blp
blueprint-compiler lint src/shortcuts-dialog.blp
```

Definition of done for a change: compile and lint touched `.blp` files,
`python3 scripts/test.py` green, and full `meson compile -C build`. Tests do not
launch a separate app process. Headless tests cover the document model,
Markdown -> Pango markup, editing helpers, export and file I/O; in-process GUI
tests skip themselves without a display. Launch
`./build-install/bin/md_split examples/demo.md` only for visual checks after UI
changes, and ask first.

## Desktop automation

Never drive the GUI, synthesize input, or automate the desktop without explicit
permission for that run. Before launching the app, moving or clicking the
pointer, typing, sending keyboard shortcuts, or using tools, accessibility APIs,
or compositor automation, tell the user exactly what will happen and ask whether
they are ready. Wait for an explicit yes. Headless tests and static checks do not
require this; visual checks that launch the app do.

Use `gridhand` CLI tool desktop automation. Run `gridhand --help` for detailed
list of commands.

## Hard boundaries

- No WebKit/WebView/browser. No compiled `.wasm` assets.
- Don't edit generated `.ui` files. Edit `src/*.blp`; `blueprint-compiler`
  writes `build/src/*.ui`.
- Don't add runtime pip dependencies. PyGObject and GI libraries come from the
  system; pure-Python Markdown packages are vendored in `src/vendor/`.
- Don't parse Markdown by hand in the UI layer. Extend the vendored parser or
  `src/renderer.py` tokens; keep the parser behavior GFM-compatible.
- Raw HTML stays disabled (`html: False`). The only promoted inline tag is a
  balanced, correctly nested `<u>`/`</u>` pair; code spans, unbalanced tags and
  pairs that cross emphasis or link boundaries remain literal.
- Resolve document-relative links and images through `src/paths.py`; never
  open or embed a path that resolves outside the document directory.
  Escape every text fragment with `GLib.markup_escape_text` before putting it
  in a `Gtk.Label`.
- PDF export imports pycairo lazily so the editor still starts when the
  optional Cairo binding is unavailable. Keep the import inside the export
  paths and report an error dialog instead of failing application startup.
- `Gtk.TextBuffer` has `select_range()`, not `select()`. Use `place_cursor()`
  for an empty selection when applying a `src/editing.py` edit.
- `Gtk.FileDialog.open()` invokes its callback with `(source_object, result)`
  only; pass user data after the callback if the handler needs it, and declare
  the matching parameter.
- Don't leave user-visible strings unwrapped. Use `_()`/`ngettext()` in Python
  and `_()`/`C_()` in Blueprint, and add source files to `po/POTFILES.in`.
- Call `gi.require_version` before importing any GI namespace.
- Keep Meson source/file lists explicit — no globbing.

## UI logic placement

| Concern | Blueprint (`.blp`) | Python |
| :--- | :---: | :---: |
| Static layout, labels, style classes, menus | Yes | |
| Property bindings between widgets | Yes (`bind`/`as`/`bidirectional`) | |
| Breakpoints / adaptive shells | Yes (`setters`) | |
| Anything that changes at runtime | | Yes |
| Signal handlers, actions, dialog flow | | Yes |
| Markdown AST -> widgets | | Yes (`src/renderer.py`) |

Configure appearance and state in Blueprint; code wires behavior. Only set in
code what changes at runtime.

## Workflow: add a Markdown block type

1. Inspect the token stream: `python3 -c` with `src.renderer.MarkdownRenderer`
   or `markdown_it`.
2. Add a token branch in `MarkdownRenderer._render_blocks`.
3. Build ordinary GTK widgets; add CSS classes to `src/style.css` only when an
   Adwaita class cannot express the style.
4. Escape all text with `GLib.markup_escape_text` before putting it in a
   `Gtk.Label` Pango markup; links go through `_resolve_link`.
5. Add inline cases to `tests/test_markdown.py` (headless) and, for block
   structure, a widget-tree assertion to `tests/test_renderer.py`.
6. Add any new user-visible strings to `po/POTFILES.in` if in a new file.

## Workflow: new action

1. In `MdSplitApplication.__init__`:
   `self.create_action('name', self.on_name_action, ['<control>n'])`.
2. Wire it from `.blp` (menu item `action: "app.name";` or button
   `action-name: "app.name";`).
3. Callbacks use `self.props.active_window` and delegate to the window.
4. Add the shortcut to `src/shortcuts-dialog.blp`.
5. Verify the action string is `"app." + name`.

`app.shortcuts` needs no `create_action`: `Adw.Application` auto-loads
`shortcuts-dialog.ui` and registers it.

## Snippets

Native label from Markdown:

```python
label = Gtk.Label()
label.set_use_markup(True)
label.set_wrap(True)
label.set_selectable(True)
label.set_xalign(0.0)
label.set_markup(self._render_inline(token))
label.connect("activate-link", self._on_activate_link)
```

GSettings access (keys live in `data/io.github.james2doyle.MdSplit.gschema.xml`):

```python
settings = Gio.Settings(schema_id='io.github.james2doyle.MdSplit')
settings.set_string('view-mode', 'source')
```

## File index

```text
[UI] src/window.blp, src/preferences-dialog.blp, src/shortcuts-dialog.blp;
  generated: build/src/*.ui
[App] src/main.py (application/actions/icons, session/recent/recovery),
  src/window.py (window/file I/O, search, autosave, file monitor, spell check),
  src/logging_utils.py (known GTK/libenchant warning filter)
[Model] src/document.py (text, file, dirty state, counts, reading time),
  src/fileio.py (async I/O)
[State] src/session.py (session/recent paths), src/recovery.py (drafts/backups),
  src/filechanges.py (external-change decisions)
[Spell] src/spellcheck.py (optional libspelling backend)
[Paths] src/paths.py (document-directory containment, export extensions)
[Edit] src/editing.py (surround/link completion/formatting/search),
  src/formatting.py (Markdown source normalization and diff summaries),
  src/links.py (source link detection and resolution)
[Preview] src/renderer.py (tokens -> widgets), src/code_highlight.py
  (native code-block highlighting), src/markdown_view.py (scroll/theme),
  src/preview_vim.py (Vim navigation/search for the preview),
  src/markdown_parser.py (shared GFM parser)
[Export] src/exporting.py (standalone HTML and styled PDF),
  src/export.css (HTML export stylesheet, inlined at export)
[Prefs] src/preferences.py (settings-backed preferences dialog)
[Data] data/*.gschema.xml, data/*.desktop.in, data/*.metainfo.xml.in,
  data/icons/hicolor/*/apps/*.svg
[Vendor] src/vendor/markdown_it, mdit_py_plugins, mdurl, LICENSES
[Tests] tests/test_document.py, test_markdown.py, test_code_highlight.py,
  test_fileio.py, test_editing.py, test_links.py, test_lists.py, test_export.py,
  test_logging.py, test_filechanges.py, test_session.py, test_recovery.py, test_spellcheck.py,
  test_formatting.py, test_resources.py, test_preview_vim.py,
  test_startup.py (headless);
  test_renderer.py, test_window.py, test_lifecycle.py, test_preferences.py,
  test_view_modes.py, test_cli.py, test_scroll_sync.py, test_images.py (display)
[Scripts] scripts/test.py (sanitized test runner), dev.py, scripts/init.py
[CI] .github/workflows/ci.yml, .github/workflows/release.yml,
  .github/workflows/publish.yml
[Docs] README.md, AGENTS.md, CONTRIBUTING.md, docs/*.md
[Packaging] meson.build, src/meson.build, io.github.james2doyle.MdSplit.json,
  scripts/compile-bytecode.py (install-time unchecked-hash .pyc),
  packaging/appimage/AppImageBuilder.yml, scripts/build-appimage.sh
```

## Gotchas

- GtkSource GTypes register lazily. `MdSplitWindow.__init__` calls
  `_ensure_gtksource_types()` before `super().__init__`, otherwise GtkBuilder
  fails with `Invalid object type 'GtkSourceView'`. Keep this call.
- `Gtk.Picture` with `can_shrink=True` can be allocated 0 height after loading
  an SVG in a scrolled preview. `_size_picture()` sets an explicit height
  request (capped at 360 px); preserve that when changing image code.
- `blocks, _ = ...` shadows gettext's `_()` in that function and raises
  `UnboundLocalError` on `_("...")`. Use `_ignored` or another name.
- Blueprint object-valued properties end with `;` after the closing brace:
  `content: Adw.ToolbarView { ... };`. Slots such as `[top]` do not.
- Blueprint imports are `using Gtk 4.0;`, `using Adw 1;`,
  `using GtkSource 5;` — never `1.x`.
- `Gtk.Label` has `activate-link` even though `GObject.signal_list_names`
  may not show it; connect it normally and return `True` to suppress the
  default handler.
- Pango markup is not HTML. Escape every text fragment with
  `GLib.markup_escape_text`; use `<b>`, `<i>`, `<s>`, `<a href>`, `<sup>` and
  `<span font_family="monospace" background="#...">` for inline styles.
- Custom CSS is scoped to `md-*` classes in `src/style.css` and uses Adwaita
  named colors (`@borders`, `@card_bg_color`, `@accent_bg_color`,
  `@success_bg_color`, `@warning_bg_color`, `@error_bg_color`).
- `Gio.File.replace_contents_async()` does not keep a Python `bytes` payload
  alive. `src/fileio.py` passes the payload as callback data until the write
  completes; otherwise the file can be filled with freed-memory garbage.
- Under X11/Xwayland, ibus can segfault on `gdk_x11_surface_get_xid` in this
  environment. Use `GTK_IM_MODULE=gtk-im-context-simple` for X11 test runs;
  this is an input-method/environment issue, not app code.
- HTML/PDF export and document decode run on daemon threads. Do not touch GTK
  from those workers; marshal results back with `GLib.idle_add` and start file
  writes on the main loop.
- Export colors and type scale live in one block in `src/exporting.py`; the
  generated CSS and the PDF writer both read it. Add new export colors there
  instead of introducing a second color literal.
- `PangoCairo.show_layout_line()` puts the current point on the line baseline,
  not its top. Offset by `-line.get_pixel_extents()[1].y` when paginating or
  drawing table cells, or text is clipped and rows overlap.
- `Pango.LayoutLine` objects are owned by their `Pango.Layout`. Keep the layout
  referenced while storing lines for later page breaks, or Pango raises
  `LINE_IS_VALID` criticals when the line is finally drawn.
- Python debug logging is enabled with `MD_SPLIT_DEBUG=1`. `G_MESSAGES_DEBUG`
  belongs to GLib and does not change the app's log level.
- Spell checking is optional. Check the provider's language count, not only the
  `Spelling` GI namespace; the host can load the typelib without dictionaries.
- Recovery drafts and backups are state files under
  `$XDG_STATE_HOME/md-split`, not settings. Keep their writes atomic and their
  permissions private.
- The installed launcher resolves `share/md_split` and translations from
  `APPDIR` when it is set. Preserve that relocation path for AppImage builds;
  system and Flatpak installs use the configured prefix.
- AppImage builds repack the `appimage-builder` AppDir with the pinned official
  `appimagetool` and type2 runtime in `scripts/build-appimage.sh`. Bump the
  version constants and SHA-256 sums together; the release smoke test checks
  that the embedded runtime reports `type2-runtime`.
- Vim mode: `GtkSource.VimIMContext` on a second CAPTURE key controller,
  added after the Markdown helper controller so helpers see Insert-mode keys
  first. `VimIMContext.execute_command()` does not emit `write`/`execute-command`;
  tests type commands through `filter_key` instead.
- First-frame startup work is deferred: the bundled demo, Markdown syntax
  highlighting and the first preview render run from idles after `present()`.
  Display tests must drain `GLib.MainContext` before asserting demo,
  highlighting or preview state; headless import tests guard the lazy parser
  and renderer imports.
- If a UI change does not show up, touch the `.blp` or remove stale
  `build/src/*.ui`; `ninja -C build -d explain` shows why.
