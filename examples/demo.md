# MD Split

A native GNOME Markdown editor with a live **GitHub-Flavored Markdown** preview.

> [!NOTE]
> The preview is a real GTK widget tree — headings are labels, tables are
> `Gtk.Grid`s, task lists are check buttons, and images are `Gtk.Picture`s.
> There is no WebKit, no embedded browser and no web runtime.

## Why MD Split?

- [x] Native GTK 4, libadwaita and GtkSourceView
- [x] GFM tables, task lists, alerts, autolinks and footnotes
- [x] Synchronized scrolling and three view modes
- [x] Find, replace and Format Document
- [x] Link insertion plus picker, drop and clipboard images
- [x] Auto-pairing, list indentation, task toggles, line moves and Go to Line
- [x] Optional Vim keybindings for the editor and preview
- [x] Autosave recovery, session restore and external-change warnings
- [x] Standalone HTML and styled PDF export
- [ ] Your next note

## Markdown at a glance

| Feature | Example | Native widget |
|:--------|:-------:|:--------------|
| Tables | this table | `Gtk.Grid` |
| Task lists | above | `Gtk.CheckButton` |
| Code blocks | below | `Gtk.Label` |
| Images | at the end | `Gtk.Picture` |
| Alerts | this page | styled `Gtk.Box` |

> [!TIP]
> Press **F9** to cycle between both panes, source only, and preview only.

## Code

```python
def render(markdown: str) -> Gtk.Widget:
    return MarkdownRenderer().render(markdown)
```

Inline code such as `Ctrl+Shift+R` formats the document or selection.

## Formatting

**Bold**, *italic*, ~~strikethrough~~, <u>underline</u>, and
[a link to the project](https://github.com/james2doyle/mdsplit).

> Markdown is a lightweight markup language for creating formatted text using a
> plain-text editor.

## Try it

1. Open a Markdown file with **Ctrl+O**.
2. Press **F9** to change the view.
3. Format the document with **Ctrl+Shift+R**.
4. Enable **Vim keybindings** in Preferences, then try `j`/`k` and `/` here.
5. Export HTML or a styled PDF from the main menu.

## Editing helpers

**Ctrl+K** inserts a link and uses the selection as its label. Insert images
from the menu or a file drop, or press **Ctrl+Shift+V** for clipboard image
data. Images inside the document directory use relative paths; copied and
clipboard images are saved under `assets/`.

Opening brackets, quotes, backticks, `*`, `_`, and `~` insert a matching
closing delimiter in prose. Typing the closing delimiter steps over it, and
Backspace between an empty pair removes both characters. Pairing stays out of
code spans and fenced blocks; you can turn it off with **Auto-pair delimiters**
in Preferences.

Use **Tab** and **Shift+Tab** to indent and outdent list items by one level;
nested content moves with its parent. **Ctrl+Shift+X** or **Ctrl+D** toggles the
current task between `[ ]` and `[x]` while preserving list markers.

Move the current line or selected lines with **Alt+Up** and **Alt+Down**.
Duplicate a line or the exact selection with **Ctrl+Shift+D**. **Ctrl+L** opens
Go to Line, which validates the entry, clamps large line numbers to the end of
the document, and returns focus to the editor. Each edit is one undo step.

## Keyboard shortcuts

| Shortcut | Action |
|:---------|:-------|
| Ctrl+N | New document |
| Ctrl+O | Open |
| Ctrl+S | Save |
| Ctrl+Shift+S | Save As |
| Ctrl+F | Find |
| Ctrl+H | Replace |
| Ctrl+L | Go to line |
| Ctrl+K | Insert Markdown link |
| Ctrl+Shift+V | Insert image from clipboard |
| Alt+Up / Alt+Down | Move current or selected lines |
| Ctrl+Shift+D | Duplicate current line or selection |
| Tab / Shift+Tab | Indent / outdent current or selected list items |
| Ctrl+Shift+X / Ctrl+D | Toggle current task marker |
| Ctrl+Shift+R | Format document or selection |
| F9 | Cycle view mode |
| Ctrl+, | Preferences |

## Vim mode

Vim keybindings are optional and off by default. Enable **Vim keybindings** in
Preferences (**Ctrl+,**).

**Editor.** The source pane uses GtkSourceView's built-in Vim emulation:
Normal, Insert, Visual and Replace modes, motions, text objects, registers,
`/` search and `:` commands. The status bar shows the current mode and any
pending keys.

| Command | Effect |
|:--------|:-------|
| `:w` / `:w FILE` | Save, or save to a path |
| `:e FILE` / `:e` | Open a file, or reload the current one |
| `:q` / `:q!` / `:wq` / `:x` | Close, discard and close, or save and close |
| `gq{motion}` | Format the covered lines |
| `Ctrl+W w` / `Ctrl+W l` | Move focus to the preview |

**Preview.** The preview becomes a Vim-style viewer, including in preview-only
mode:

| Keys | Action |
|:-----|:-------|
| `j` / `k`, `Ctrl+E` / `Ctrl+Y` | Scroll down / up, with an optional count such as `5j` |
| `Ctrl+D` / `Ctrl+U`, `Ctrl+F` / `Ctrl+B` | Half page, then full page |
| `gg` / `G`, `{n}G` | Top / bottom, or jump to `n` percent |
| `]]` / `[[` | Next / previous heading |
| `/` / `?`, `n` / `N` | Search and step through matches |
| `i`, `e` | Focus the editor at the part you are reading |
| `Ctrl+W w`, `Ctrl+W h` / `Ctrl+W l` | Switch pane / focus editor / focus preview |

> [!TIP]
> While the editor has focus, Vim's Ctrl keys take precedence over MD Split
> shortcuts such as **Ctrl+B** and **Ctrl+F**. Use the menus for those actions,
> or use them from the preview.

## Export and recovery

Export HTML or a styled PDF from the main menu. Autosave recovery drafts,
session restore, recent files and optional timestamped backups keep your work
safe. Spell checking uses libspelling when a system dictionary is available.

## Security

Raw HTML is disabled. A balanced `<u>...</u>` pair is the only inline HTML tag
rendered as formatting. Remote images stay off until you enable **Load Remote
Images**; local images are decoded asynchronously with size limits.

## Learn more

- [Architecture](https://github.com/james2doyle/mdsplit/blob/main/docs/architecture.md)
- [Markdown support](https://github.com/james2doyle/mdsplit#markdown-support)
- [CommonMark spec](https://commonmark.org/)
- [GitHub GFM spec](https://github.github.com/gfm/)

Footnote reference[^native].

[^native]: The parser is markdown-it-py with its composite GFM plugin.

![MD Split icon](../data/icons/hicolor/scalable/apps/io.github.james2doyle.MdSplit.svg)
