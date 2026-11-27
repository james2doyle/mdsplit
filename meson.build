project('md_split',
          version: '1.8.0',
          license: 'MIT',
          license_files: 'COPYING',
    meson_version: '>= 1.1.0',
  default_options: [ 'warning_level=2', 'werror=false', ],
)

i18n = import('i18n')
gnome = import('gnome')

install_data(
  meson.project_license_files(),
  install_dir: get_option('datadir') / 'licenses' / 'io.github.james2doyle.MdSplit',
)

subdir('data')
subdir('src')
subdir('po')

gnome.post_install(
     glib_compile_schemas: true,
    gtk_update_icon_cache: true,
  update_desktop_database: true,
)
