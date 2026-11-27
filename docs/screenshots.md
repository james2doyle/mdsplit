# Screenshots

`docs/screenshot.png` is used by both `README.md` and the AppStream metadata.
It is the public face of the app, so update it deliberately.

## Current file

- Path: `docs/screenshot.png`
- Size: `1150 × 770`
- Format: 8-bit RGB PNG (no alpha)
- Content: MD Split showing the editor and native preview side by side in the
  dark theme

## Capture checklist

Use a normal desktop session and a clean, representative document:

1. Launch the installed app with the bundled demo or a short Markdown file.
2. Use the app’s dark theme (`--dark`) and 100% scaling.
3. Show both panes and enough preview content to demonstrate native rendering.
4. Avoid personal file names, notifications, browser tabs, and unrelated
   windows.
5. Capture the app window at approximately 1150 × 770.
6. Save as PNG and verify the dimensions:

   ```sh
   file docs/screenshot.png
   ```

7. Check that the image is sharp at 1× and still readable when scaled down.

Use `gridhand` CLI tool for taking screenshots: `gridhand screenshot --window "MD Split" --output /tmp/md-split-window.png`.

## AppStream URL

The AppStream screenshot is loaded from the release tag, not from `main`:

```xml
<image type="source" width="1150" height="770">https://raw.githubusercontent.com/james2doyle/mdsplit/v1.8.0/docs/screenshot.png</image>
```

When preparing a new release, update the tag in
`data/io.github.james2doyle.MdSplit.metainfo.xml.in`. The tag must be pushed and
public before AppStream/Flathub can fetch the image. Do not point the URL at a
branch.

## README

`README.md` embeds the screenshot with a relative link:

```md
![MD Split showing the editor and native preview](docs/screenshot.png)
```

Keep the alt text descriptive; it is also useful for accessibility.

## Validate

```sh
file docs/screenshot.png
appstreamcli validate --no-net build/data/io.github.james2doyle.MdSplit.metainfo.xml
```

The AppStream validator runs as part of the test suite. It checks the metadata
structure but does not download the screenshot, so verify the URL manually
after pushing the tag.

Flathub may mirror screenshots for its own pages; the URL in the repository is
still the source of truth for AppStream consumers.
