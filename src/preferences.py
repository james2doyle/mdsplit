# SPDX-License-Identifier: MIT

"""Preferences dialog backed by GSettings."""

from __future__ import annotations

from gettext import gettext as _

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")

from gi.repository import Adw, Gdk, Gio, Gtk

from . import APP_ID, spellcheck

_APPEARANCES = ("system", "light", "dark")


@Gtk.Template(resource_path="/io/github/james2doyle/MdSplit/preferences-dialog.ui")
class MdSplitPreferencesDialog(Adw.PreferencesDialog):
    __gtype_name__ = "MdSplitPreferencesDialog"

    appearance_row = Gtk.Template.Child("appearance_row")
    switch_load_demo = Gtk.Template.Child("switch_load_demo")
    switch_restore_session = Gtk.Template.Child("switch_restore_session")
    switch_autosave = Gtk.Template.Child("switch_autosave")
    spin_autosave_interval = Gtk.Template.Child("spin_autosave_interval")
    switch_backups = Gtk.Template.Child("switch_backups")
    spin_backup_retention = Gtk.Template.Child("spin_backup_retention")
    spin_export_font_size = Gtk.Template.Child("spin_export_font_size")
    switch_spell_check = Gtk.Template.Child("switch_spell_check")
    combo_spell_language = Gtk.Template.Child("combo_spell_language")
    switch_sync_scroll = Gtk.Template.Child("switch_sync_scroll")
    switch_autocomplete_links = Gtk.Template.Child("switch_autocomplete_links")
    switch_auto_pairing = Gtk.Template.Child("switch_auto_pairing")
    switch_format_on_save = Gtk.Template.Child("switch_format_on_save")
    switch_remote_images = Gtk.Template.Child("switch_remote_images")
    switch_vim_mode = Gtk.Template.Child("switch_vim_mode")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._settings = Gio.Settings(schema_id=APP_ID)
        self._appearance_handler = 0
        self._updating_appearance = False
        self._bindings: list[tuple[Gtk.Widget, str]] = []

        self.appearance_row.set_model(
            Gtk.StringList.new([_("System"), _("Light"), _("Dark")])
        )
        self._sync_appearance()
        self._appearance_handler = self._settings.connect(
            "changed::appearance", self._on_appearance_changed
        )
        self.appearance_row.connect(
            "notify::selected", self._on_appearance_selected
        )
        click = Gtk.GestureClick()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        click.connect("pressed", self._on_background_clicked)
        self.add_controller(click)
        self._click_controller = click
        key = Gtk.EventControllerKey()
        key.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key)
        self._key_controller = key

        for setting, row in (
            ("load-demo-on-startup", self.switch_load_demo),
            ("restore-session", self.switch_restore_session),
            ("autosave", self.switch_autosave),
            ("backups", self.switch_backups),
            ("spell-check", self.switch_spell_check),
            ("sync-scroll", self.switch_sync_scroll),
            ("autocomplete-links", self.switch_autocomplete_links),
            ("auto-pairing", self.switch_auto_pairing),
            ("format-on-save", self.switch_format_on_save),
            ("remote-images", self.switch_remote_images),
            ("vim-mode", self.switch_vim_mode),
        ):
            key = "backup-files" if setting == "backups" else setting
            self._settings.bind(
                key, row, "active", Gio.SettingsBindFlags.DEFAULT
            )
            self._bindings.append((row, "active"))

        self._window_handlers: list[int] = []
        self._setup_spin(
            self.spin_autosave_interval, "autosave-interval", 5, 300, 5
        )
        self._setup_spin(
            self.spin_backup_retention, "backup-retention", 1, 50, 1
        )
        self._setup_spin(
            self.spin_export_font_size, "export-font-size", 8, 18, 1
        )
        self._spell_choices = spellcheck.language_choices()
        self._updating_language = False
        if self._spell_choices:
            self.combo_spell_language.set_model(
                Gtk.StringList.new(
                    [name for _code, name in self._spell_choices]
                )
            )
            self._sync_spell_language()
            self.combo_spell_language.connect(
                "notify::selected", self._on_spell_language_selected
            )
            self._window_handlers.append(
                self._settings.connect(
                    "changed::spell-language",
                    self._on_spell_language_changed,
                )
            )
        else:
            self.switch_spell_check.set_sensitive(False)
            self.combo_spell_language.set_sensitive(False)
            self.combo_spell_language.set_subtitle(
                _("Spell checking is not available")
            )
        self.connect("closed", self._on_closed)

    def _spell_language_index(self) -> int:
        preferred = self._settings.get_string("spell-language")
        code = spellcheck.choose_language(self._spell_choices, preferred)
        for index, (candidate, _name) in enumerate(self._spell_choices):
            if candidate == code:
                return index
        return 0

    def _sync_spell_language(self) -> None:
        index = self._spell_language_index()
        if self.combo_spell_language.get_selected() == index:
            return
        self._updating_language = True
        try:
            self.combo_spell_language.set_selected(index)
        finally:
            self._updating_language = False

    def _on_spell_language_selected(self, _row, _pspec) -> None:
        if self._updating_language:
            return
        index = self.combo_spell_language.get_selected()
        if 0 <= index < len(self._spell_choices):
            self._settings.set_string(
                "spell-language", self._spell_choices[index][0]
            )

    def _on_spell_language_changed(self, _settings, _key) -> None:
        self._sync_spell_language()

    def _setup_spin(
        self,
        row: Adw.SpinRow,
        key: str,
        lower: int,
        upper: int,
        step: int,
    ) -> None:
        value = self._settings.get_int(key)
        row.set_adjustment(
            Gtk.Adjustment(
                lower=lower,
                upper=upper,
                step_increment=step,
                page_increment=step,
                value=value,
            )
        )
        row.set_value(value)
        row.connect("notify::value", self._on_spin_changed, key, lower, upper)
        self._window_handlers.append(
            self._settings.connect(
                f"changed::{key}",
                self._on_spin_setting_changed,
                row,
                key,
            )
        )

    def _on_spin_changed(
        self, row: Adw.SpinRow, _pspec, key: str, lower: int, upper: int
    ) -> None:
        value = max(lower, min(upper, int(round(row.get_value()))))
        if self._settings.get_int(key) != value:
            self._settings.set_int(key, value)

    def _on_spin_setting_changed(
        self, _settings, _key, row: Adw.SpinRow, key: str
    ) -> None:
        value = self._settings.get_int(key)
        if int(round(row.get_value())) != value:
            row.set_value(value)

    def _appearance_index(self) -> int:
        value = self._settings.get_string("appearance")
        try:
            return _APPEARANCES.index(value)
        except ValueError:
            return 0

    def _sync_appearance(self) -> None:
        index = self._appearance_index()
        if self.appearance_row.get_selected() == index:
            return
        self._updating_appearance = True
        try:
            self.appearance_row.set_selected(index)
        finally:
            self._updating_appearance = False

    def _on_appearance_changed(self, _settings, _key) -> None:
        self._sync_appearance()

    def _on_appearance_selected(self, _row, _parameter) -> None:
        if self._updating_appearance:
            return
        index = self.appearance_row.get_selected()
        if 0 <= index < len(_APPEARANCES):
            self._settings.set_string("appearance", _APPEARANCES[index])

    def _on_background_clicked(
        self, _gesture, _n_press: int, x: float, y: float
    ) -> None:
        child = self.get_child()
        if child is None:
            return
        success, bounds = child.compute_bounds(self)
        if not success:
            return
        if (
            bounds.get_x() <= x < bounds.get_x() + bounds.get_width()
            and bounds.get_y() <= y < bounds.get_y() + bounds.get_height()
        ):
            return
        self.close()

    def _on_key_pressed(
        self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType
    ) -> bool:
        modifiers = state & Gtk.accelerator_get_default_mod_mask()
        blocked = (
            Gdk.ModifierType.CONTROL_MASK
            | Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.SUPER_MASK
        )
        if keyval == Gdk.KEY_Escape and not (modifiers & blocked):
            self.close()
            return True
        return False

    def _on_closed(self, *_args) -> None:
        if self._appearance_handler:
            self._settings.disconnect(self._appearance_handler)
            self._appearance_handler = 0
        for handler in self._window_handlers:
            self._settings.disconnect(handler)
        self._window_handlers.clear()
        for row, prop in self._bindings:
            self._settings.unbind(row, prop)
        self._bindings.clear()
