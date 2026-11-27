# Releasing MD Split

Releases are built from an annotated tag, not from an arbitrary working tree.
The version comes from `meson.build`; keep `pyproject.toml` and
`data/io.github.james2doyle.MdSplit.metainfo.xml.in` in sync.

## Prepare

1. Set the new version in `meson.build`, `pyproject.toml`, and the AppStream
   release entry. Update the AppStream screenshot URL to the new tag.
2. Run the checks:

   ```sh
   meson compile -C build
   python3 scripts/test.py
   uvx pyright src/*.py tests/*.py
   uvx ruff check --select E9,F63,F7,F82,F401,I001 \
     --exclude src/vendor src tests scripts dev.py
   for file in src/*.blp; do
     blueprint-compiler compile "$file" >/dev/null
     blueprint-compiler lint "$file"
   done
   ```

3. Commit and push the release, then create and push the tag:

   ```sh
   export VERSION=1.0.0
   export TAG="v$VERSION"

   git tag -a "$TAG" -m "MD Split $VERSION"
   git push origin main
   git push origin "$TAG"
   ```

## Native optimized build

```sh
meson setup build-release --buildtype=release \
  --prefix="$PWD/build-release-install"
meson compile -C build-release
python3 scripts/test.py -C build-release --print-errorlogs
meson install -C build-release
./build-release-install/bin/md_split
```

This build uses the system GTK, libadwaita and PyGObject stack. Use the
Flatpak build for a self-contained artifact.

## Flatpak release bundle

The development manifest (`io.github.james2doyle.MdSplit.json`) builds the working
tree and does not set a release build type. For a release, create an archive
from the tag and a derived manifest with a SHA-256-pinned `archive` source and
`--buildtype=release`. The manifest step uses `jq`.

```sh
export VERSION=1.0.0
export TAG="v$VERSION"

rm -rf dist build-flatpak .flatpak-builder
mkdir -p dist

git archive --format=tar.gz --prefix="md-split-$VERSION/" \
  -o "dist/md-split-$VERSION.tar.gz" "$TAG"

sha=$(sha256sum "dist/md-split-$VERSION.tar.gz" | cut -d' ' -f1)
jq --arg sha "$sha" --arg archive "md-split-$VERSION.tar.gz" '
  .modules |= map(
    if .name == "md_split" then
      .sources = [{
        "type": "archive",
        "path": $archive,
        "sha256": $sha
      }]
      | .["config-opts"] = ["--libdir=lib", "--buildtype=release"]
    elif .name == "libspelling" then
      .["config-opts"] += ["--buildtype=release"]
    else . end
  )
' io.github.james2doyle.MdSplit.json > "dist/io.github.james2doyle.MdSplit.release.json"

flatpak-builder --force-clean --repo=dist/repo --state-dir=.flatpak-builder \
  --default-branch=stable --subject="MD Split $VERSION" \
  build-flatpak "dist/io.github.james2doyle.MdSplit.release.json"

flatpak build-bundle dist/repo "dist/md_split-$VERSION.flatpak" \
  io.github.james2doyle.MdSplit stable
```

On a machine without the GNOME 51 runtime and SDK, add
`--user --install-deps-from=flathub` to the `flatpak-builder` command.

The archive must have a top-level directory (`--prefix=...`). Flatpak strips
one leading path component when extracting; without it the source tree is
flattened and Meson fails with `first statement must be a call to project()`.

## Published Flatpak repository

`.github/workflows/publish.yml` publishes the stable channel with the
[AetherPak Flatpak registry publisher](https://github.com/aetherpak/actions).
It runs on every `v*.*.*` tag push. The reusable workflow builds the manifest
for `x86_64` and `aarch64`, pushes the application images to GHCR, and deploys
the repository index and landing page to GitHub Pages. Users add the
`md-split` remote and receive later releases through `flatpak update`; see the
[README](../README.md#install) for the install and update commands.

Verified installs need Flatpak 1.17 or newer. On older clients, use the unsigned
remote instead:

```sh
flatpak remote-add --if-not-exists --user --no-gpg-verify \
  md-split oci+https://james2doyle.github.io/mdsplit
flatpak install --user md-split io.github.james2doyle.MdSplit
```

A manual run builds the selected ref, so re-publish a channel from its release
tag, for example after rotating the signing key. The tag must contain this
workflow file:

```sh
gh workflow run publish.yml --ref v1.5.0  # replace with the release tag
```

The caller workflow is pinned to AetherPak `v3.14.0` by commit SHA.

### One-time setup

1. Enable Pages with **Settings → Pages → Build and deployment → Source:
   GitHub Actions**. If the `github-pages` environment uses custom deployment
   branch policies, add a `v*` tag policy; otherwise deployments from a release
   tag are rejected.
2. Create a release signing key and add it as the `GPG_PRIVATE_KEY` repository
   secret. Add `GPG_PRIVATE_KEY_PASSPHRASE` as well when the key is protected.
3. After the first successful publish, make the `mdsplit` container package
   public: your profile or organization, then **Packages** → `mdsplit` →
   **Package settings** → **Change visibility** → **Public**. Installs fail
   while the package is private.

A passphrase-less key is enough for CI:

```sh
gpg --batch --gen-key <<EOF
%no-protection
Key-Type: RSA
Key-Length: 4096
Name-Real: MD Split Releases
Name-Email: releases@example.org
Expire-Date: 0
%commit
EOF
gpg --armor --export-secret-keys releases@example.org
```

Store the armored output as the secret and keep it out of the repository:

```sh
gpg --armor --export-secret-keys releases@example.org |
  gh secret set GPG_PRIVATE_KEY --repo james2doyle/mdsplit
```

Signing is requested with `signing: gpg` in the workflow. AetherPak's
[signing documentation](https://github.com/aetherpak/actions#signing-optional)
covers key rotation; after rotating, run the publish workflow once so every
image listed in the stable index is signed by the new key.

## AppImage release build

The AppImage is built from the tagged source in an Ubuntu 26.04 container. The
build script reads the version from `pyproject.toml`; set `VERSION` only when
testing a tag checkout whose version matches. It pins `appimagetool` and the
official type2 runtime, verifies their SHA-256 sums, and repacks the AppDir
with that runtime.

```sh
export VERSION=1.2.0

./scripts/build-appimage.sh
sha256sum "dist/md_split-$VERSION-x86_64.AppImage"
```

The artifact is written to `dist/md_split-$VERSION-x86_64.AppImage`. It bundles
the app, Python, GTK, libadwaita, GtkSourceView, Pycairo, GSettings schemas,
icons, translations, libspelling and an English dictionary. See
[appimage.md](appimage.md) for the runtime behavior, FUSE fallback and limits.

Smoke-test the artifact without installing it:

```sh
APPIMAGE="dist/md_split-$VERSION-x86_64.AppImage"
"$APPIMAGE" --appimage-version
"$APPIMAGE" --appimage-extract
./squashfs-root/AppRun --help
```

## Verify and install

```sh
export VERSION=1.0.0

flatpak install --user --reinstall "dist/md_split-$VERSION.flatpak"
flatpak info --user io.github.james2doyle.MdSplit
flatpak run io.github.james2doyle.MdSplit
```

`flatpak build-bundle` produces an unsigned local bundle. Flathub builds the
app from a manifest, so use a URL-backed archive (for example the GitHub tag
tarball) and let Flathub sign and build it. See [flathub.md](flathub.md) for
the submission checklist. For a local install test without touching your
normal Flatpak installation:

```sh
tmp=$(mktemp -d)
FLATPAK_USER_DIR="$tmp" flatpak install --user --noninteractive \
  "dist/md_split-$VERSION.flatpak"
FLATPAK_USER_DIR="$tmp" flatpak run io.github.james2doyle.MdSplit --help
rm -rf "$tmp"
```

The release workflow runs the same install-and-help check on the bundle it
publishes.

## Automated GitHub release

`.github/workflows/release.yml` runs after the **CI** workflow succeeds for a
`v*.*.*` tag push. Its `workflow_run.branches` filter prevents no-op Release
runs after ordinary `main` pushes. It is deliberately separate from `ci.yml`
so CI stays read-only and releases cannot be published from pull requests or
non-release branch pushes. CI runs on pull requests, `main` pushes, and
`v*.*.*` tag pushes. The same tag push also runs `.github/workflows/publish.yml`,
which updates the [published Flatpak repository](#published-flatpak-repository).

The workflow:

- triggers only for completed CI runs on `v*.*.*` tags, avoiding no-op runs
  after `main` pushes;
- independently verifies that the triggering ref is a release tag before any
  build starts;
- checks out the tagged commit and creates the same prefixed source archive and
  pinned release manifest shown above;
- builds the Flatpak with `--buildtype=release`;
- installs the built bundle into a temporary `FLATPAK_USER_DIR` and smoke-runs
  `flatpak run io.github.james2doyle.MdSplit --help`;
- builds the AppImage in an Ubuntu 26.04 container with the pinned
  `appimagetool` and type2 runtime, extracts it, checks
  `--appimage-version`, and smoke-runs `AppRun --help` plus an Xvfb launch;
- uploads the source archive, release manifest, Flatpak, AppImage, and
  `SHA256SUMS` to a GitHub Release;
- updates assets with `--clobber` if the release already exists, making
  re-runs safe.

The release is published, not drafted. Add `--draft` to the `gh release create`
command in the workflow if you prefer a manual approval step.

## Artifacts

`dist/` is gitignored. A successful release build produces:

- `dist/md-split-$VERSION.tar.gz` — tagged source archive
- `dist/io.github.james2doyle.MdSplit.release.json` — pinned release manifest
- `dist/md_split-$VERSION.flatpak` — installable bundle
- `dist/repo/` — local OSTree repository
