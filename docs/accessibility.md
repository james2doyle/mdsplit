# Accessibility

MD Split uses standard GTK 4 and libadwaita widgets, so it inherits platform
focus handling, text scaling, and high-contrast behavior. This page is a
checklist, not a certification or a completed audit.

## Keyboard

- `Ctrl+N`, `Ctrl+O`, `Ctrl+S`, and `Ctrl+Shift+S` cover document actions.
- `F9` cycles both/source/preview; the View Mode menu exposes the same choices.
- Formatting shortcuts: `Ctrl+B`, `Ctrl+I`, `Ctrl+U`, `Ctrl+T`, and
  `` Ctrl+` ``.
- `Ctrl+F`, `Ctrl+G`, `Ctrl+Shift+G`, and `Ctrl+H` open find, find next,
  find previous, and replace. Escape closes the search bar and returns focus to
  the editor.
- `Ctrl+L` opens Go to Line with numeric validation and returns focus to the
  editor. Alt+Up/Down moves selected lines; Ctrl+Shift+D duplicates the line or
  selection. All edits are available as single undo steps.
- `Ctrl+Shift+R` formats the selection or the whole document. Large changes
  open a dialog with a diff preview; Cancel leaves the source untouched.
- `Ctrl+,` opens Preferences; `Ctrl+?` opens the shortcuts dialog.
- `Ctrl+Q` quits with the normal dirty-document confirmation.
- Preferences closes with Escape, the close button, or a click outside.
- The editor receives focus on launch; preview-only mode focuses the preview.
- Opening find from preview-only mode shows the source pane until the search
  bar closes.

## Focus and names

- Icon-only header buttons have tooltips and action names: Open, Save, Recent
  Files, View Mode, Main Menu.
- Search option buttons use text labels and tooltips; the search entry has a
  placeholder and the match count is a read-only label.
- The preferences dialog's close button has a tooltip and the standard
  window-close icon.
- Preview images use their Markdown alt text as a caption, and image fallbacks
  include the alt text in the placeholder.
- Code-block copy buttons have a “Copy code” tooltip.
- The editor and preview panes are separate focus targets; the status bar is
  not interactive.

## Preview semantics

The preview is a native widget tree, not a web view:

- Text labels are selectable.
- Links are real Pango links and are pointer-activatable; keyboard activation
  of preview links is not yet audited.
- Images are `Gtk.Picture` widgets with alt-text captions.
- Tables are `Gtk.Grid` widgets built from labels, not an HTML table.
- Task-list checkboxes are display-only (`tasklists_editable=False`).

## Known gaps

- Headings are styled `Gtk.Label`s, not semantic heading roles. Screen readers
  may not announce heading levels or provide heading navigation.
- Tables are grids of labels without row/column header semantics.
- Task checkboxes are not interactive, so they are not keyboard-editable.
- There is no formal Orca, high-contrast, or 200% text-scaling audit yet.
- Focus mode and other future layout modes need a keyboard-only review when
  added.

## Manual checks

Before a release with UI changes, test:

1. **Keyboard only:** open, edit, save, find and replace, switch view mode,
   open preferences, dismiss recovery and external-change dialogs, and quit
   without a pointer.
2. **Screen reader:** run Orca and check the window title, header buttons,
   editor, preview links, status bar, and preferences labels.
3. **Text scaling:** set a large text scale or font size and confirm labels do
   not clip or overlap.
4. **High contrast:** enable the high-contrast theme and check preview tables,
   code blocks, alerts, block quotes, and selection colors.
5. **Reduced motion:** if animations are added, respect the desktop setting.
6. **Zoom:** check the app at 100%, 150%, and 200% scale on a HiDPI display.

## Reporting

Include the GTK/libadwaita version, distribution, display server (Wayland or
X11), theme, text scale, and the exact keyboard/screen-reader steps. Screenshots
of focus rings and clipped labels are helpful.
