# Settings

Settings use the GSettings schema `io.github.james2doyle.MdSplit` with path
`/io/github/james2doyle/MdSplit/`. The schema is defined in
`data/io.github.james2doyle.MdSplit.gschema.xml` and installed under
`share/glib-2.0/schemas`.

## Keys

| Key | Type | Default | Effect |
| --- | --- | --- | --- |
| `window-width` | int | `1100` | Last window width; range 600–4000 |
| `window-height` | int | `720` | Last window height; range 400–3000 |
| `paned-position` | int | `540` | Editor/preview split; range 0–4000 |
| `view-mode` | string | `both` | `both`, `source`, or `preview` |
| `remote-images` | bool | `false` | Fetch HTTP(S) images in the preview |
| `appearance` | string | `system` | `system`, `light`, or `dark` |
| `load-demo-on-startup` | bool | `true` | Load bundled demo when opened without a file |
| `sync-scroll` | bool | `true` | Keep editor and preview scroll positions aligned |
| `autocomplete-links` | bool | `true` | Expand a typed `[]` to `[]()` |
| `auto-pairing` | bool | `true` | Pair, skip, and delete matching editor delimiters |
| `format-on-save` | bool | `true` | Normalize Markdown source before writing a file |
| `last-folder` | string | `""` | Last file-chooser directory |
| `restore-session` | bool | `true` | Reopen file windows from the last session |
| `session-files` | string list | `[]` | Paths of open file windows from the last run |
| `session-active` | string | `""` | File that had focus in the last run |
| `recent-files` | string list | `[]` | Recent paths, newest first |
| `autosave` | bool | `true` | Write recovery drafts while editing |
| `autosave-interval` | int | `30` | Seconds between drafts; range 5–300 |
| `backup-files` | bool | `false` | Keep timestamped backups on save |
| `backup-retention` | int | `5` | Backups per file; range 1–50 |
| `export-font-size` | int | `11` | Base PDF font size in points; range 8–18 |
| `vim-mode` | bool | `false` | Vim keybindings in the editor and Vim navigation in the preview |
| `spell-check` | bool | `true` | Check spelling when a backend and dictionary exist |
| `spell-language` | string | `""` | Dictionary code; empty uses the provider default |

## When values are written

- `view-mode` is written when the user picks a mode or cycles with F9.
- `remote-images` is written by the menu action and preferences switch.
- `sync-scroll`, `autocomplete-links`, `auto-pairing`, `format-on-save`,
  `load-demo-on-startup`, and `appearance` are bound in the preferences
  dialog.
- `format-on-save` applies the same formatter as the manual Format Document
  command before a save writes its snapshot. It formats the whole document,
  never only a selection, and runs as one undoable edit.
- `window-width`, `window-height`, and `paned-position` are written when the
  window receives a close request. Values are clamped to the ranges above
  before they are stored, so a display larger than the schema maximum still
  persists the maximum. `paned-position` is only stored while both panes are
  visible.
- `last-folder` is updated after a file is opened or saved.
- `session-files` and `session-active` update when a file opens, saves, gains
  focus, or closes. Missing paths are removed on the next session restore.
- `recent-files` updates after a file opens or saves. **Clear Recent** empties
  the list.
- `restore-session`, `autosave`, `autosave-interval`, `backup-files`,
  `backup-retention`, `export-font-size`, `spell-check`, and `spell-language`
  are bound in the preferences dialog. Numeric rows clamp to the schema ranges.
  Spell settings have no effect when no dictionary provider is available.
- `export-font-size` is the base text size for PDF export only. Headings, code,
  tables, captions and footer sizes are derived from it. HTML export uses the
  fixed web type scale in `src/export.css` and follows the viewer's light or
  dark preference.
- Recovery drafts live under
  `$XDG_STATE_HOME/md-split/recovery`. Timestamped backups live under
  `$XDG_STATE_HOME/md-split/backups`.

## Command-line overrides

`--source-only`, `--preview-only`, `--no-source`, and `--no-preview` set the
initial view mode for the windows created by that invocation. `--light` and
`--dark` force the appearance for that run. These overrides are held on the
application and are not written to GSettings. The aliases are exact synonyms.
Any source-only flag combined with any preview-only flag is rejected, as is
`--light` with `--dark`; conflicts exit with status 1 before a window opens.

`FILE` arguments open as documents; multiple files open in separate windows.
View and appearance flags apply to every window opened by that invocation; they
do not overwrite the saved settings.

By default MD Split keeps one process per session, and a later launch hands its
files to the running process. `--new-instance` starts an independent process
instead. Secondary processes start with a fresh session: they do not restore or
update the shared session, and they do not prompt for recovery drafts. Avoid
editing the same file in two processes at once; file monitoring and autosave
will report external changes.

## Inspecting and resetting

For an installed app:

```sh
gsettings get io.github.james2doyle.MdSplit view-mode
gsettings set io.github.james2doyle.MdSplit remote-images false
gsettings reset io.github.james2doyle.MdSplit paned-position
```

For an uninstalled prefix, add the build schema directory:

```sh
GSETTINGS_SCHEMA_DIR="$PWD/build/data" \
  gsettings get io.github.james2doyle.MdSplit view-mode
```

Reset everything:

```sh
gsettings reset-recursively io.github.james2doyle.MdSplit
```

## Implementation notes

- Preferences bind booleans with `Gio.Settings.bind()`. `bind()` returns
  `None`; cleanup uses `Gio.Settings.unbind(row, "active")`.
- The appearance row is an `Adw.ComboRow`; changing it writes `appearance` and
  the app listens for `changed::appearance` to update `Adw.StyleManager`.
- Invalid `view-mode` values fall back to `both` in the application and window.
- The CLI overrides are deliberately not persisted so a one-off launch does not
  change the user's saved view or theme.
