pkgdatadir = get_option('prefix') / get_option('datadir') / meson.project_name()
moduledir = pkgdatadir / 'md_split'
gnome = import('gnome')

blueprints = custom_target('blueprints',
  input: files(
    'preferences-dialog.blp',
    'shortcuts-dialog.blp',
    'window.blp',
  ),
  output: '.',
  command: [find_program('blueprint-compiler'), 'batch-compile', '@OUTPUT@', '@CURRENT_SOURCE_DIR@', '@INPUT@'],
)

md_split_resources = gnome.compile_resources('md_split',
  'md_split.gresource.xml',
  source_dir: '..',
  gresource_bundle: true,
  install: true,
  install_dir: pkgdatadir,
  dependencies: blueprints,
)

python = import('python')

conf = configuration_data()
conf.set('PYTHON', python.find_installation('python3').full_path())
conf.set('VERSION', meson.project_version())
conf.set('localedir', get_option('prefix') / get_option('localedir'))
conf.set('pkgdatadir', pkgdatadir)

configure_file(
  input: 'md_split.in',
  output: 'md_split',
  configuration: conf,
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x'
)

md_split_sources = [
  '__init__.py',
  'document.py',
  'editing.py',
  'exporting.py',
  'filechanges.py',
  'fileio.py',
  'formatting.py',
  'image_insert.py',
  'image_policy.py',
  'launcher.py',
  'links.py',
  'logging_utils.py',
  'main.py',
  'markdown_parser.py',
  'markdown_view.py',
  'paths.py',
  'preferences.py',
  'preview_vim.py',
  'recovery.py',
  'renderer.py',
  'session.py',
  'spellcheck.py',
  'window.py',
]

install_data(md_split_sources, install_dir: moduledir)
install_data('export.css', install_dir: moduledir)
install_subdir('vendor',
  install_dir: moduledir,
  exclude_directories: ['**/__pycache__'],
  exclude_files: ['**/*.pyc'],
)

python3 = python.find_installation('python3')

# Ship bytecode: read-only installs would otherwise recompile on every launch.
meson.add_install_script(
  python3,
  files('../scripts/compile-bytecode.py'),
  get_option('datadir') / meson.project_name(),
)

test_env = [
  'PYTHONPATH=' + meson.project_source_root(),
  'APP_RESOURCE=' + md_split_resources[0].full_path(),
  # Keep tests away from the host session bus, credentials and user dirs.
  'DBUS_SESSION_BUS_ADDRESS=unix:path=/nonexistent',
  'LC_ALL=C',
  'LANG=C',
  'TERM=dumb',
  'NO_AT_BRIDGE=1',
  'PYTHONDONTWRITEBYTECODE=1',
  'GTK_IM_MODULE=gtk-im-context-simple',
  'HOME=' + (meson.current_build_dir() / 'test-home'),
  'XDG_CONFIG_HOME=' + (meson.current_build_dir() / 'test-config'),
  'XDG_STATE_HOME=' + (meson.current_build_dir() / 'test-state'),
  'XDG_RUNTIME_DIR=' + (meson.current_build_dir() / 'test-runtime'),
  'XDG_CACHE_HOME=' + (meson.current_build_dir() / 'test-cache'),
  'XDG_DATA_HOME=' + (meson.current_build_dir() / 'test-data'),
  'GSETTINGS_SCHEMA_DIR=' + (meson.project_build_root() / 'data'),
  'GSETTINGS_BACKEND=memory',
]

test('Document model', python3, args: [files('../tests/test_document.py')],
  env: test_env)
test('Markdown markup', python3, args: [files('../tests/test_markdown.py')],
  env: test_env)
test('File I/O', python3, args: [files('../tests/test_fileio.py')],
  env: test_env)
test('External file changes', python3, args: [files('../tests/test_filechanges.py')],
  env: test_env)
test('Session helpers', python3, args: [files('../tests/test_session.py')],
  env: test_env)
test('Recovery helpers', python3, args: [files('../tests/test_recovery.py')],
  env: test_env)
test('Spell-check helpers', python3, args: [files('../tests/test_spellcheck.py')],
  env: test_env)
test('Markdown formatting', python3, args: [files('../tests/test_formatting.py')],
  env: test_env)
test('Window smoke test', python3, args: [files('../tests/test_window.py')],
  depends: test_schemas,
  env: test_env)
test('Native Markdown renderer', python3, args: [files('../tests/test_renderer.py')],
  env: test_env)
test('Application lifecycle', python3, args: [files('../tests/test_lifecycle.py')],
  depends: test_schemas,
  env: test_env)
test('Preferences dialog', python3, args: [files('../tests/test_preferences.py')],
  depends: test_schemas,
  env: test_env)
test('View modes', python3, args: [files('../tests/test_view_modes.py')],
  depends: test_schemas,
  env: test_env)
test('CLI options', python3, args: [files('../tests/test_cli.py')],
  depends: test_schemas,
  env: test_env)
test('List continuation', python3, args: [files('../tests/test_lists.py')],
  env: test_env)
test('Scroll sync', python3, args: [files('../tests/test_scroll_sync.py')],
  depends: test_schemas,
  env: test_env)
test('Packaged resources', python3, args: [files('../tests/test_resources.py')],
  env: test_env)
test('Lazy startup imports', python3, args: [files('../tests/test_startup.py')],
  env: test_env)
test('Remote image policy', python3, args: [files('../tests/test_images.py')],
  env: test_env)
test('Image insertion helpers', python3, args: [files('../tests/test_image_insert.py')],
  env: test_env)
test('Editing helpers', python3, args: [files('../tests/test_editing.py')],
  env: test_env)
test('Link helpers', python3, args: [files('../tests/test_links.py')],
  env: test_env)
test('Link instance launcher', python3, args: [files('../tests/test_launcher.py')],
  env: test_env)
test('Preview Vim helpers', python3, args: [files('../tests/test_preview_vim.py')],
  env: test_env)
test('HTML and PDF export', python3, args: [files('../tests/test_export.py')],
  env: test_env)
test('Warning filters', python3, args: [files('../tests/test_logging.py')],
  env: test_env)
