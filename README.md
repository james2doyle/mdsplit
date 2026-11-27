# MD Split

A native GNOME Markdown editor with a live, GitHub-Flavored Markdown preview.

![MD Split showing the editor and native preview](docs/screenshot.png)

MD Split is built with GTK 4, libadwaita and GtkSourceView. The preview is a
real GTK widget tree — headings are labels, tables are `Gtk.Grid`s, task lists
are check buttons, code blocks are selectable monospace labels, and images are
`Gtk.Picture`s. There is no WebKit, no embedded browser and no web runtime.

Markdown parsing is done by [markdown-it-py](https://github.com/executablebooks/markdown-it-py)
with its composite GFM plugin. Both are vendored under `src/vendor`, so the app
has no runtime Python dependencies beyond PyGObject.

## Install

```sh
flatpak remote-add --user \
  --signature-lookaside=https://james2doyle.github.io/mdsplit/sigs \
  md-split https://james2doyle.github.io/mdsplit/md-split.flatpakrepo
flatpak install --user md-split io.github.james2doyle.MdSplit
```

Every `v*.*.*` release tag publishes a new stable build, so the normal Flatpak
update picks it up:

```sh
flatpak update --user io.github.james2doyle.MdSplit
```

## Features

- Split view with a GtkSourceView editor and a native live preview
- GitHub-Flavored Markdown: tables, strikethrough, autolinks, task lists,
  alerts and footnotes
- Native rendering for headings, paragraphs, links, lists, block quotes,
  fenced and indented code blocks, tables, images and horizontal rules
- System, light and dark appearance with system as the default
- Optional bundled demo document when opening without a file
- Synchronized editor/preview scrolling
- Three view modes: both panes, source only, and preview only
- Selection wrapping for brackets, braces, backticks and quotes
- Automatic list continuation on Return and `[]` to `[]()` completion
- Ctrl+click links in the source pane, including inline, autolink and
  reference links
- Bold, italic, underline, block quote and code formatting shortcuts
- Format Document normalizes headings, list spacing, block quotes, tables,
  blank lines and code fences, with a preview for large changes
- Format on save is enabled by default and can be disabled in Preferences
- In-document find and replace with case, whole-word, regex and wrap options
- Selection counts and document or selection reading time in the status bar
- Autosave recovery drafts, startup recovery, and optional timestamped backups
- Session restore, recent files, and a clear-recent action
- External file-change warnings with reload or keep-editing choices
- Spell checking with libspelling and a system dictionary when available
- Optional Vim mode: modal editing in the source pane and Vim-style
  scrolling, heading jumps and `/` search in the preview
- Standalone HTML and styled PDF export, with extension enforcement and
  overwrite confirmation
- Command-line view and appearance overrides
- Open, save and save-as with UTF-8 files
- Unsaved-change prompts, window geometry and preview state persistence
- Cursor position in the status bar
- Copy-code buttons in code block headers
- Command-line file opening and `%F` desktop integration
- Raw HTML is disabled by default for safe previewing; a balanced,
  correctly nested `<u>...</u>` pair is the only inline HTML rendered as
  formatting

## Keyboard shortcuts

| Shortcut | Action |
| --- | --- |
| Ctrl+N | New document |
| Ctrl+O | Open |
| Ctrl+S | Save |
| Ctrl+Shift+S | Save As |
| Ctrl+F | Find |
| Ctrl+G | Find next |
| Ctrl+Shift+G | Find previous |
| Ctrl+H | Replace |
| Ctrl+L | Go to line |
| Alt+Up / Alt+Down | Move current or selected lines |
| Ctrl+Shift+D | Duplicate current line or selection |
| Ctrl+Shift+R | Format document or selection |
| F9 | Cycle view mode |
| Ctrl+B | Bold |
| Ctrl+I | Italic |
| Ctrl+U | Underline |
| Ctrl+T | Block quote |
| `` Ctrl+` `` | Code |
| Ctrl+, | Preferences |
| Ctrl+? | Show shortcuts |
| Ctrl+Q | Quit |

## Vim mode

Turn on **Vim keybindings** in Preferences (the `vim-mode` setting). It is off
by default.

**Editor.** The source pane uses GtkSourceView's built-in Vim emulation:
Normal, Insert, Visual and Replace modes, motions, text objects, registers,
`/` search and `:` commands. The current mode and pending command appear in
the status bar. MD Split handles these commands:

| Command | Effect |
| --- | --- |
| `:w` | Save (Save As for an untitled document) |
| `:w FILE` | Save to `FILE`, relative to the document's folder |
| `:e FILE` / `:e` | Open `FILE` / reload the current file |
| `:q`, `:q!` | Close the window (prompting for unsaved changes) / discard changes and close |
| `:wq`, `:x` | Save and close |
| `gq{motion}` | Run Format Document on the lines covered |
| `Ctrl+W w` / `Ctrl+W l` | Move focus to the preview (showing it if hidden) |

Other `:` commands such as `:s`, `:set` and `:{line}` work as in GtkSourceView.
Editor `/` and `?` searches are case-sensitive regular expressions; while the
cursor sits on a match the status bar shows `/pattern [n/total]`.
The Markdown typing helpers (list continuation, selection wrapping, `[]()`
completion) run only in Insert mode. While the editor has focus, Vim's Ctrl
keys take precedence over MD Split shortcuts that use the same keys: `Ctrl+B`,
`Ctrl+U`, `Ctrl+F`, `Ctrl+O`, `Ctrl+R` and `Ctrl+V`. Use the menus, or the same
shortcuts from outside the editor, for those actions.

**Preview.** With Vim mode on, the preview, including preview-only mode,
works as a Vim-style viewer:

| Keys | Action |
| --- | --- |
| `j` / `k`, `Ctrl+E` / `Ctrl+Y` | Scroll down / up (accepts a count, e.g. `5j`) |
| `Ctrl+D` / `Ctrl+U` | Half page down / up |
| `Ctrl+F` / `Ctrl+B`, `Space` / `Shift+Space` | Page down / up |
| `gg` / `G` | Top / bottom |
| `{n}G` | Jump to `n` percent of the document |
| `]]` / `[[` | Next / previous heading |
| `/` / `?` | Search forward / backward (smartcase, Python regex) |
| `n` / `N` | Next / previous match |
| `Esc` | Clear the match highlight and pending keys |
| `Ctrl+W w`, `Ctrl+W h` / `Ctrl+W l` | Switch pane / focus editor / focus preview |
| `i`, `e` | Focus the editor at the part you are reading (showing it if hidden) |

## View modes

The view control in the header and the **View Mode** entries in the main menu
switch between both panes, source only, and preview only. The choice is stored
in `view-mode` and restored on the next launch. F9 cycles through the three
modes.

## Command-line options

```sh
md_split [OPTION…] [FILE…]
md_split --help
```

| Option | Alias | Effect |
| --- | --- | --- |
| `--no-source` | `--preview-only` | Start with the preview pane only |
| `--no-preview` | `--source-only` | Start with the source editor only |
| `--light` | | Force light mode for this run |
| `--dark` | | Force dark mode for this run |
| `--new-instance` | | Start a separate process instead of reusing the running instance |
| `-h`, `--help` | | Show the available options |

Examples:

```sh
md_split notes.md
md_split --preview-only --dark notes.md
md_split --no-preview --light notes.md
```

## Export

**Export HTML…** writes a standalone HTML document with embedded CSS. Local
images are embedded as bounded data URIs, remote images keep their URLs, and
relative links are resolved against the source document.

**Export PDF…** uses
Cairo and Pango to write a styled A4 PDF with headings, lists, quotes, code,
tables, local images and footnotes. Export uses the current editor contents,
including unsaved changes. **Preferences → Export → PDF font size** controls
the PDF text size; headings, code, tables and captions scale with it. Exported
HTML follows the viewer's light or dark preference.

## Third-party licenses

- The app is MIT licensed; see `COPYING`.
- `markdown-it-py`, `mdit-py-plugins` and `mdurl` are MIT licensed; their
  license texts are in `src/vendor/LICENSES/`.
- The HTML export stylesheet is adapted from [new.css](https://github.com/xz/new.css),
  MIT licensed. The copyright and permission notice is retained in
  `src/export.css`.
