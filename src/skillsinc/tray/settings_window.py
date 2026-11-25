"""The settings window: ``settings.json`` and every element of ``config.json``.

The General tab edits the application settings (schedule, notifications,
updates, start at sign-in, history, which configuration). The Sources,
Destinations, Profiles, and Routes tabs edit the configuration one element at
a time. Save validates the configuration with the real loader, then writes
both files, changing only what changed so comments stay.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from skillsinc.config import Config, ConfigurationError
from skillsinc.configedit import (
    ROUTES,
    ConfigDocument,
    ConfigEditError,
    PendingWrite,
    Table,
)
from skillsinc.jsonc import Jsonc
from skillsinc.models import JsonValue
from skillsinc.profiles.registry import ProfileRegistry
from skillsinc.schedule import format_interval, parse_interval
from skillsinc.schema import BOM
from skillsinc.usersettings import (
    HISTORY_KEEP_RANGE,
    HistoryOptions,
    NotificationOptions,
    ScheduleOptions,
    SettingsFileError,
    SettingsStore,
    SettingsWrite,
    StartupOptions,
    UpdateOptions,
    UserSettings,
)

ERROR_STYLE: Final = "color: #E5484D;"
HARNESSES: Final = tuple(profile.name for profile in ProfileRegistry().profiles)


@dataclass(frozen=True, slots=True)
class Field:
    """One editable key of a configuration element."""

    key: str
    label: str
    kind: str = "text"
    """``text``, ``bool``, ``choice``, ``list`` (one per line), or ``names``."""
    choices: tuple[str, ...] = ()
    """For ``choice``: the values; for ``names``: the section listing them."""
    default: bool = False
    """For ``bool``: the value an absent key means."""
    hint: str = ""


@dataclass(frozen=True, slots=True)
class Section:
    """A tab of configuration elements and the fields each one has."""

    name: str
    title: str
    fields: tuple[Field, ...]
    summary: tuple[str, ...] = field(default=())
    """Keys shown as table columns after the name."""


SOURCES: Final = Section(
    "sources",
    "Sources",
    (
        Field("path", "Folder", hint="A library folder; or give GitHub or Git"),
        Field("github", "GitHub", hint="owner/repo"),
        Field("git", "Git URL"),
        Field("ref", "Ref", hint="Branch, tag, or commit (GitHub and Git)"),
        Field("subdirectory", "Subdirectory", hint="Library folder in the repo"),
        Field("refresh", "Refresh", hint="How often to fetch, e.g. 15m"),
    ),
    ("path", "github", "git", "ref"),
)
DESTINATIONS: Final = Section(
    "destinations",
    "Destinations",
    (
        Field("profile", "Harness", "choice", ("", *HARNESSES)),
        Field("path", "Folder", hint="Required for generic"),
        Field("enabled", "Enabled", "bool", default=True),
    ),
    ("profile", "path", "enabled"),
)
PROFILES: Final = Section(
    "profiles",
    "Profiles",
    (
        Field("description", "Description"),
        Field("default", "Active by default", "bool", default=True),
    ),
    ("description", "default", "when"),
)
ROUTE_FIELDS: Final = (
    Field("sources", "Sources", "names", ("sources",)),
    Field("destinations", "Destinations", "names", ("destinations",)),
    Field("include", "Include", "list", hint="kind/name globs, one per line"),
    Field("exclude", "Exclude", "list", hint="kind/name globs, one per line"),
    Field("allow_lossy", "Allow lossy conversions", "bool"),
    Field("profiles", "Profiles", "names", ("profiles",)),
)
ROUTE_COLUMNS: Final = ("sources", "destinations", "include", "profiles")


type Changed = Callable[[str, str, str], None]
"""Called with a section, an element's old name, and its new one ("": none)."""


def cell(value: JsonValue | None) -> str:
    """Return a table cell's text for a JSON value."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if isinstance(value, dict):
        return "conditions" if value else ""
    return str(value)


class ElementDialog(QDialog):
    """Edit one element: its name (for keyed sections) and its fields."""

    def __init__(  # noqa: PLR0913 - one dialog serves every section
        self,
        title: str,
        fields: Sequence[Field],
        table: Table,
        names: Mapping[str, Sequence[str]],
        *,
        name: str | None = None,
        taken: Sequence[str] = (),
        shown: Mapping[str, JsonValue] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """``shown`` gives what an absent key effectively is, to display it."""
        super().__init__(parent)
        self.setWindowTitle(title)
        self._fields = tuple(fields)
        self._table = copy.deepcopy(table)
        self._taken = set(taken)
        self.widgets: dict[str, QWidget] = {}
        form = QFormLayout()
        self.name_edit: QLineEdit | None = None
        if name is not None:
            self.name_edit = QLineEdit(name, self)
            form.addRow("Name", self.name_edit)
        effective = dict(shown or {})
        for item in self._fields:
            value = table.get(item.key, effective.get(item.key))
            widget = self._widget(item, value, names)
            if item.hint:
                widget.setToolTip(item.hint)
            self.widgets[item.key] = widget
            form.addRow(item.label, widget)
        # What each field reads as before any edit: an untouched field keeps
        # the element's value exactly, including an empty list or no key
        self._initial = {
            item.key: self._value(self.widgets[item.key]) for item in self._fields
        }
        self.error = QLabel(self)
        self.error.setStyleSheet(ERROR_STYLE)
        self.error.setWordWrap(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.try_accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.error)
        layout.addWidget(buttons)

    def name(self) -> str:
        """Return the entered name."""
        return self.name_edit.text().strip() if self.name_edit is not None else ""

    def try_accept(self) -> None:
        """Accept unless the name is missing or taken."""
        if self.name_edit is not None:
            name = self.name()
            if not name:
                self.error.setText("Enter a name.")
                return
            if name in self._taken:
                self.error.setText(f"{name!r} already exists.")
                return
        self.accept()

    def table(self) -> Table:
        """Return the element with the entered values; unknown keys are kept."""
        table = copy.deepcopy(self._table)
        for item in self._fields:
            value = self._value(self.widgets[item.key])
            if value == self._initial[item.key]:
                continue
            present = item.key in self._table
            if value is None or (
                item.kind == "bool" and value == item.default and not present
            ):
                table.pop(item.key, None)
            else:
                table[item.key] = value
        return table

    def _widget(
        self,
        item: Field,
        value: JsonValue | None,
        names: Mapping[str, Sequence[str]],
    ) -> QWidget:
        if item.kind == "bool":
            box = QCheckBox(self)
            box.setChecked(value if isinstance(value, bool) else item.default)
            return box
        if item.kind == "choice":
            combo = QComboBox(self)
            combo.addItems(list(item.choices))
            combo.setCurrentText(value if isinstance(value, str) else "")
            return combo
        if item.kind == "names":
            chosen = value if isinstance(value, list) else []
            listing = QListWidget(self)
            available = list(names.get(item.choices[0], ()))
            available += [str(n) for n in chosen if str(n) not in available]
            for option in available:
                entry = QListWidgetItem(option, listing)
                entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                checked = option in chosen
                entry.setCheckState(
                    Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
                )
            listing.setMaximumHeight(110)
            return listing
        if item.kind == "list":
            lines = value if isinstance(value, list) else []
            editor = QPlainTextEdit("\n".join(str(line) for line in lines), self)
            editor.setMaximumHeight(80)
            return editor
        return QLineEdit(cell(value) if value is not None else "", self)

    @staticmethod
    def _value(widget: QWidget) -> JsonValue | None:
        if isinstance(widget, QCheckBox):
            return widget.isChecked()
        if isinstance(widget, QComboBox):
            return widget.currentText() or None
        if isinstance(widget, QListWidget):
            checked: list[JsonValue] = [
                widget.item(row).text()
                for row in range(widget.count())
                if widget.item(row).checkState() == Qt.CheckState.Checked
            ]
            return checked or None
        if isinstance(widget, QPlainTextEdit):
            lines = widget.toPlainText().splitlines()
            parts: list[JsonValue] = [line.strip() for line in lines if line.strip()]
            return parts or None
        assert isinstance(widget, QLineEdit)  # noqa: S101 - built by _widget
        return widget.text().strip() or None


class SectionTab(QWidget):
    """A table of one section's elements with Add, Edit, and Remove."""

    def __init__(
        self,
        document: ConfigDocument,
        section: Section | None,
        names: Callable[[], Mapping[str, Sequence[str]]],
        parent: QWidget | None = None,
        *,
        changed: Changed | None = None,
    ) -> None:
        super().__init__(parent)
        self._document = document
        self._section = section
        self._names = names
        self._changed = changed
        columns = ("Name", *section.summary) if section else ROUTE_COLUMNS
        self.table = QTableWidget(0, len(columns), self)
        self.table.setHorizontalHeaderLabels([c.capitalize() for c in columns])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.doubleClicked.connect(lambda _index: self.edit())
        self.add_button = QPushButton("Add…", self)
        self.add_button.clicked.connect(lambda _checked=False: self.add())
        self.edit_button = QPushButton("Edit…", self)
        self.edit_button.clicked.connect(lambda _checked=False: self.edit())
        self.remove_button = QPushButton("Remove", self)
        self.remove_button.clicked.connect(lambda _checked=False: self.remove())
        buttons = QHBoxLayout()
        for button in (self.add_button, self.edit_button, self.remove_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.table)
        layout.addLayout(buttons)
        self.dialog: ElementDialog | None = None
        self.refresh()

    def refresh(self) -> None:
        """Show the section as the document holds it now."""
        rows = self._rows()
        self.table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, text in enumerate(values):
                self.table.setItem(row, column, QTableWidgetItem(text))

    def add(self) -> None:
        """Add an element through the element dialog."""
        self._open(None)

    def edit(self) -> None:
        """Edit the selected element."""
        row = self.table.currentRow()
        if row >= 0:
            self._open(row)

    def remove(self) -> None:
        """Remove the selected element."""
        row = self.table.currentRow()
        if row < 0:
            return
        if self._section is None:
            routes = self._document.routes()
            del routes[row]
            self._document.set_routes(routes)
        else:
            key = list(self._document.section(self._section.name))[row]
            self._document.remove(self._section.name, key)
            self._tell(key, "")
        self.refresh()

    def _rows(self) -> list[list[str]]:
        if self._section is None:
            return [
                [cell(route.get(column)) for column in ROUTE_COLUMNS]
                for route in self._document.routes()
            ]
        return [
            [key, *(cell(table.get(column)) for column in self._section.summary)]
            for key, table in self._document.section(self._section.name).items()
        ]

    def _open(self, row: int | None) -> None:
        names = self._names()
        if self._section is None:
            routes = self._document.routes()
            table = routes[row] if row is not None else {}
            dialog = ElementDialog(
                "Route", ROUTE_FIELDS, table, names, parent=self.window()
            )
        else:
            entries = self._document.section(self._section.name)
            key = list(entries)[row] if row is not None else ""
            dialog = ElementDialog(
                self._section.title.removesuffix("s"),
                self._section.fields,
                entries.get(key, {}),
                names,
                name=key,
                taken=[existing for existing in entries if existing != key],
                shown=self._effective(key),
                parent=self.window(),
            )
        self.dialog = dialog
        dialog.accepted.connect(lambda: self._store(dialog, row))
        dialog.open()

    def _store(self, dialog: ElementDialog, row: int | None) -> None:
        if self._section is None:
            routes = self._document.routes()
            if row is None:
                routes.append(dialog.table())
            else:
                routes[row] = dialog.table()
            self._document.set_routes(routes)
        else:
            old = (
                list(self._document.section(self._section.name))[row]
                if (row is not None)
                else ""
            )
            new, table = dialog.name(), dialog.table()
            if self._section is DESTINATIONS and old and old != new:
                # The harness was inferred from the old name: keep it
                inferred = self._effective(old).get("profile")
                if inferred is not None and "profile" not in table:
                    table["profile"] = inferred
            self._document.put(self._section.name, new, table, old_key=old)
            self._tell(old, new)
        self.refresh()

    def _effective(self, key: str) -> dict[str, JsonValue]:
        """Return what absent keys of ``key`` mean: a destination's harness."""
        if self._section is DESTINATIONS and key in HARNESSES:
            return {"profile": key}
        return {}

    def _tell(self, old: str, new: str) -> None:
        """Report a keyed element's rename (or removal: ``new`` empty)."""
        assert self._section is not None  # noqa: S101 - keyed sections only
        if self._changed is not None:
            self._changed(self._section.name, old, new)


class SettingsWindow(QDialog):
    """Application settings and the configuration, saved together."""

    def __init__(  # noqa: PLR0913 - collaborators are injected for tests
        self,
        store: SettingsStore,
        default_config: Callable[[UserSettings], Path],
        validate: Callable[[Path, str], Config],
        on_saved: Callable[[], None],
        parent: QWidget | None = None,
        *,
        open_file: Callable[[Path], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("SkillSinc settings")
        self.resize(760, 560)
        self._store = store
        self._config_for = default_config
        self._validate = validate
        self._on_saved = on_saved
        self._open_file = open_file
        self._settings = UserSettings()
        self._snapshot: bytes | None = None
        # False when settings.json cannot be parsed: Save leaves it alone
        self.settings_editable = True
        self.document: ConfigDocument | None = None
        self.tabs = QTabWidget(self)
        self.error = QLabel(self)
        self.error.setStyleSheet(ERROR_STYLE)
        self.error.setWordWrap(True)
        self.config_note = QLabel(self)
        self.config_note.setWordWrap(True)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        self.open_settings_button = QPushButton("Open settings.json", self)
        self.open_settings_button.clicked.connect(
            lambda _checked=False: self.open_settings_file()
        )
        self.open_settings_button.hide()
        footer = QHBoxLayout()
        footer.addWidget(self.open_settings_button)
        footer.addStretch(1)
        footer.addWidget(buttons)
        layout = QVBoxLayout(self)
        layout.addWidget(self.tabs, 1)
        layout.addWidget(self.config_note)
        layout.addWidget(self.error)
        layout.addLayout(footer)
        self._build_general()
        self.section_tabs: dict[str, SectionTab] = {}

    # Loading

    def load(self) -> None:
        """Read both files into the window; problems show above the buttons."""
        problems: list[str] = []
        self.settings_editable = True
        try:
            self._snapshot = self._store.snapshot()
            self._settings = self._store.load()
        except SettingsFileError as err:
            self._settings = UserSettings()
            self.settings_editable = self._is_object(self._snapshot)
            if self.settings_editable:
                problems.append(
                    f"{err}. Showing defaults; Save writes the values shown."
                )
            else:
                problems.append(
                    f"{err}. Fix it in a text editor (Open settings.json);"
                    " Save writes only the configuration."
                )
        self.general_page.setEnabled(self.settings_editable)
        self.open_settings_button.setVisible(not self.settings_editable)
        self._show_settings(self._settings)
        problems.extend(self._open_document(self._config_for(self._settings)))
        self.error.setText("\n".join(problems))

    def present(self) -> None:
        """Show the window, raised; a new editing session reads both files."""
        if not self.isVisible():
            self.load()
        self.show()
        self.raise_()
        self.activateWindow()

    def open_settings_file(self) -> None:
        """Open settings.json in the desktop's editor, to repair it."""
        if self._open_file is not None:
            self._open_file(self._store.path)

    def _is_object(self, snapshot: bytes | None) -> bool:
        """Return True if ``snapshot`` parses as an object Save can edit."""
        try:
            text = (snapshot or b"").decode("utf-8").removeprefix(BOM)
            plain = Jsonc.blank_trailing_commas(Jsonc.blank_comments(text))
            return not plain.strip() or isinstance(json.loads(plain), dict)
        except (ValueError, RecursionError):
            return False

    def _open_document(self, config: Path) -> list[str]:
        """Edit ``config`` in the section tabs; return what went wrong."""
        try:
            self.document = ConfigDocument.open(config)
            problems: list[str] = []
        except ConfigEditError as err:
            self.document = None
            problems = [f"{err}. The configuration tabs are unavailable."]
        self._config_shown = config
        self._build_sections()
        self.config_note.setText(f"Configuration: {config}")
        return problems

    def _target(self) -> Path:
        """Return the configuration the General tab names."""
        text = self.config_path.text().strip()
        return self._config_for(
            replace(self._settings, config=Path(text) if text else None)
        )

    def follow_config(self) -> bool:
        """Edit the configuration the General tab names; False if refused.

        Pending configuration edits must be saved or cancelled first, so they
        never land in a file the user has moved away from.
        """
        target = self._target()
        if target == self._config_shown:
            return True
        if self.document is not None and self.document.changed:
            shown = self._config_shown
            self.config_path.setText(
                ""
                if shown == self._config_for(replace(self._settings, config=None))
                else str(shown)
            )
            self.error.setText(
                "Save or cancel the configuration edits before choosing another"
                " configuration file."
            )
            return False
        self.error.setText("\n".join(self._open_document(target)))
        return True

    # General tab

    def _build_general(self) -> None:
        page = QWidget(self)
        form = QFormLayout(page)
        self.schedule_enabled = QCheckBox("Sync on a schedule", page)
        self.interval = QLineEdit(page)
        self.interval.setToolTip("5m, 1h, 1h30m, 1d, or HH:MM:SS; at least 1m")
        self.run_at_start = QCheckBox("Sync when SkillSinc starts", page)
        self.only_profiles = QCheckBox(
            "Sync only these profiles (otherwise the ones active here)", page
        )
        self.profiles = QListWidget(page)
        self.profiles.setMaximumHeight(110)
        self.only_profiles.toggled.connect(self.profiles.setEnabled)
        self.notify_failures = QCheckBox("Notify when a sync fails", page)
        self.notify_changes = QCheckBox("Notify when a sync changes files", page)
        self.check_updates = QCheckBox("Check for new releases", page)
        self.install_updates = QCheckBox("Install new releases without asking", page)
        self.launch_at_login = QCheckBox("Start SkillSinc when I sign in", page)
        low, high = HISTORY_KEEP_RANGE
        self.keep = QSpinBox(page)
        self.keep.setRange(low, high)
        self.config_path = QLineEdit(page)
        self.config_path.setPlaceholderText("The user configuration")
        browse = QPushButton("Browse…", page)
        browse.clicked.connect(lambda _checked=False: self.browse())
        config_row = QHBoxLayout()
        config_row.addWidget(self.config_path, 1)
        config_row.addWidget(browse)
        self.config_path.editingFinished.connect(self.follow_config)
        form.addRow(self.schedule_enabled)
        form.addRow("Every", self.interval)
        form.addRow(self.run_at_start)
        form.addRow(self.only_profiles)
        form.addRow(self.profiles)
        form.addRow(self.notify_failures)
        form.addRow(self.notify_changes)
        form.addRow(self.check_updates)
        form.addRow(self.install_updates)
        form.addRow(self.launch_at_login)
        form.addRow("History keeps", self.keep)
        form.addRow("Configuration file", config_row)
        self.general_page = page
        self.tabs.addTab(page, "General")

    def browse(self) -> None:
        """Pick the configuration file."""
        chosen, _filter = QFileDialog.getOpenFileName(
            self, "Configuration file", self.config_path.text(), "JSON (*.json)"
        )
        if chosen:
            self.config_path.setText(chosen)
            self.follow_config()

    def _show_settings(self, settings: UserSettings) -> None:
        schedule = settings.schedule
        self.schedule_enabled.setChecked(schedule.enabled)
        self.interval.setText(format_interval(schedule.interval))
        self.run_at_start.setChecked(schedule.run_at_start)
        self.only_profiles.setChecked(schedule.profiles is not None)
        self.profiles.setEnabled(schedule.profiles is not None)
        self.notify_failures.setChecked(settings.notifications.failures)
        self.notify_changes.setChecked(settings.notifications.changes)
        self.check_updates.setChecked(settings.updates.check)
        self.install_updates.setChecked(settings.updates.install)
        self.launch_at_login.setChecked(settings.startup.launch_at_login)
        self.keep.setValue(settings.history.keep)
        self.config_path.setText(str(settings.config) if settings.config else "")

    def _show_profile_choices(self, checked: Sequence[str] | None = None) -> None:
        chosen = list(
            self._settings.schedule.profiles or () if checked is None else checked
        )
        names = list(self._names().get("profiles", ()))
        names += [name for name in chosen if name not in names]
        self.profiles.clear()
        for name in names:
            entry = QListWidgetItem(name, self.profiles)
            entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            entry.setCheckState(
                Qt.CheckState.Checked if name in chosen else Qt.CheckState.Unchecked
            )

    def settings(self) -> UserSettings:
        """Return the settings the General tab shows.

        Raises:
            ValueError: If the interval is invalid.
        """
        profiles: tuple[str, ...] | None = None
        if self.only_profiles.isChecked():
            profiles = tuple(
                self.profiles.item(row).text()
                for row in range(self.profiles.count())
                if self.profiles.item(row).checkState() == Qt.CheckState.Checked
            )
            if not profiles:
                msg = "tick at least one profile, or sync the ones active here"
                raise ValueError(msg)
        config_text = self.config_path.text().strip()
        return UserSettings(
            config=Path(config_text) if config_text else None,
            schedule=ScheduleOptions(
                enabled=self.schedule_enabled.isChecked(),
                interval=parse_interval(self.interval.text()),
                profiles=profiles,
                run_at_start=self.run_at_start.isChecked(),
            ),
            notifications=NotificationOptions(
                failures=self.notify_failures.isChecked(),
                changes=self.notify_changes.isChecked(),
            ),
            updates=UpdateOptions(
                check=self.check_updates.isChecked(),
                install=self.install_updates.isChecked(),
            ),
            startup=StartupOptions(launch_at_login=self.launch_at_login.isChecked()),
            history=HistoryOptions(keep=self.keep.value()),
        )

    # Configuration tabs

    def _names(self) -> dict[str, list[str]]:
        document = self.document
        if document is None:
            return {}
        return {
            section: list(document.section(section))
            for section in ("sources", "destinations", "profiles")
        }

    def _checked_profiles(self) -> list[str]:
        return [
            self.profiles.item(row).text()
            for row in range(self.profiles.count())
            if self.profiles.item(row).checkState() == Qt.CheckState.Checked
        ]

    def _section_changed(self, section: str, old: str, new: str) -> None:
        """Carry a profile's rename or removal into the scheduled selection."""
        if section != "profiles":
            return
        checked = self._checked_profiles()
        if old and old in checked:
            checked = [
                new if name == old else name for name in checked if new or name != old
            ]
        self._show_profile_choices(checked)

    def _build_sections(self) -> None:
        checked = self._checked_profiles() if self.section_tabs else None
        while self.tabs.count() > 1:
            page = self.tabs.widget(1)
            self.tabs.removeTab(1)
            if page is not None:  # pragma: no branch - Qt's stubs allow None
                page.deleteLater()
        self.section_tabs = {}
        document = self.document
        if document is not None:
            for section in (SOURCES, DESTINATIONS, PROFILES, None):
                tab = SectionTab(
                    document, section, self._names, self, changed=self._section_changed
                )
                name = section.name if section else ROUTES
                title = section.title if section else "Routes"
                self.section_tabs[name] = tab
                self.tabs.addTab(tab, title)
        self._show_profile_choices(checked)

    # Saving

    def save(self) -> bool:
        """Check both files, then write them; keep the window open on an error.

        Nothing is written unless both writes are ready: the configuration is
        valid and unchanged on disk, and settings.json is as it was read.
        """
        try:
            settings = self.settings() if self.settings_editable else None
        except ValueError as err:
            self.error.setText(f"Settings: {err}")
            return False
        if not self.follow_config():
            return False
        if self.document is None:
            self.error.setText(
                f"Cannot save: {self._config_shown} cannot be read as a configuration."
            )
            return False
        try:
            config_write = self.document.plan(self._validate)
            settings_write = (
                self._store.plan(settings, self._snapshot)
                if settings is not None
                else None
            )
        except ConfigurationError as err:
            self.error.setText(str(err))
            return False
        return self._commit(config_write, settings_write, settings)

    def _commit(
        self,
        config_write: PendingWrite,
        settings_write: SettingsWrite | None,
        settings: UserSettings | None,
    ) -> bool:
        """Write the checked configuration, then settings.json."""
        document = config_write.document
        try:
            config_write.commit()
        except (ConfigurationError, OSError) as err:
            self.error.setText(f"Nothing saved: {err}")
            return False
        try:
            if settings_write is not None:
                settings_write.commit()
                self._snapshot = self._store.snapshot()
        except (ConfigurationError, OSError) as err:
            self.error.setText(f"Saved {document.path}, but not settings.json: {err}")
            self._on_saved()
            return False
        if settings is not None:
            self._settings = settings
        self.error.clear()
        self._on_saved()
        self.accept()
        return True
