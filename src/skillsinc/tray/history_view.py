"""Past syncs: a popup by the tray icon and a full history window."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Final, override

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    Qt,
)
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from skillsinc.history import SyncRecord

COLUMNS: Final = ("Result", "Started", "Trigger", "Profiles", "Time", "Summary")
OK_MARK: Final = "✓"
FAILED_MARK: Final = "✗"
OK_COLOR: Final = "#2F9E6E"
FAILED_COLOR: Final = "#E5484D"
POPUP_ROWS: Final = 8
# Runs the history window reads at a time; Load older runs adds another page
PAGE: Final = 1000
TRIGGER_LABELS: Final = {
    "manual": "Terminal",
    "scheduled": "OS scheduler",
    "tray": "Sync now",
    "tray-scheduled": "Schedule",
    "tray-startup": "Startup",
}

type Index = QModelIndex | QPersistentModelIndex


def when(moment: datetime) -> str:
    """Return ``moment`` in local time, as the history shows it."""
    return moment.astimezone().strftime("%Y-%m-%d %H:%M:%S")


def profiles_label(record: SyncRecord) -> str:
    """Return the record's profiles, or a dash."""
    profiles = record.profiles
    return ", ".join(profiles) if profiles is not None and profiles else "-"


def details(record: SyncRecord) -> str:
    """Return everything a record holds, as readable text."""
    lines = [
        f"Result: {'succeeded' if record.ok else 'FAILED'}",
        f"Started: {when(record.started)}",
        f"Finished: {when(record.finished)} ({record.duration:.1f}s)",
        f"Trigger: {TRIGGER_LABELS.get(record.trigger.value, record.trigger.value)}",
        f"Profiles: {profiles_label(record)}",
        f"Configuration: {record.config}",
        f"SkillSinc: {record.version or 'unknown'}",
        f"Summary: {record.summary()}",
    ]
    if record.warnings:
        lines.append(f"Warnings: {record.warnings}")
    if record.errors:
        lines += ["", "Errors:", *(f"  {error}" for error in record.errors)]
    if record.changes:
        lines += ["", "Changes:"]
        lines += [
            f"  {change.action:<7} {change.destination}: {change.path}"
            for change in record.changes
        ]
        if record.truncated:
            lines.append(f"  … and {record.changed - len(record.changes)} more")
    return "\n".join(lines) + "\n"


class HistoryModel(QAbstractTableModel):
    """Sync records as table rows, newest first."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._records: list[SyncRecord] = []

    @property
    def records(self) -> list[SyncRecord]:
        """Return the rows' records."""
        return list(self._records)

    def set_records(self, records: Sequence[SyncRecord]) -> None:
        """Replace every row."""
        self.beginResetModel()
        self._records = list(records)
        self.endResetModel()

    def record(self, row: int) -> SyncRecord:
        """Return the record shown in ``row``."""
        return self._records[row]

    @override
    def rowCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008 - Qt API
        """Return the number of records."""
        return 0 if parent.isValid() else len(self._records)

    @override
    def columnCount(self, parent: Index = QModelIndex()) -> int:  # noqa: B008 - Qt API
        """Return the number of columns."""
        return 0 if parent.isValid() else len(COLUMNS)

    @override
    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> object:
        """Return the column titles."""
        if (
            orientation == Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
        ):
            return COLUMNS[section]
        return None

    @override
    def data(self, index: Index, role: int = Qt.ItemDataRole.DisplayRole) -> object:
        """Return one cell's text, color, or tooltip."""
        if not index.isValid():
            return None
        record = self._records[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return self._text(record, column)
        if role == Qt.ItemDataRole.ForegroundRole and column == 0:
            return QColor(OK_COLOR if record.ok else FAILED_COLOR)
        if role == Qt.ItemDataRole.ToolTipRole:
            return record.summary()
        return None

    @staticmethod
    def _text(record: SyncRecord, column: int) -> str:
        if column == 0:
            return f"{OK_MARK} ok" if record.ok else f"{FAILED_MARK} failed"
        if column == 1:
            return when(record.started)
        if column == 2:  # noqa: PLR2004 - column index
            return TRIGGER_LABELS.get(record.trigger.value, record.trigger.value)
        if column == 3:  # noqa: PLR2004 - column index
            return profiles_label(record)
        if column == 4:  # noqa: PLR2004 - column index
            return f"{record.duration:.1f}s"
        return record.summary()


class HistoryWindow(QWidget):
    """Every recorded sync, with the selected run's details."""

    def __init__(
        self,
        load: Callable[[int], Sequence[SyncRecord]],
        sync_now: Callable[[], object],
        open_folder: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._load = load
        self.limit = PAGE
        self.setWindowTitle("SkillSinc sync history")
        self.resize(900, 560)
        self.model = HistoryModel(self)
        self.table = QTableView(self)
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.details = QPlainTextEdit(self)
        self.details.setReadOnly(True)
        self.details.setPlaceholderText("Select a sync to see its details.")
        self.empty = QLabel("No syncs recorded yet. Use Sync now to run one.", self)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        splitter = QSplitter(Qt.Orientation.Vertical, self)
        splitter.addWidget(self.table)
        splitter.addWidget(self.details)
        splitter.setSizes([340, 220])
        self.sync_button = QPushButton("Sync now", self)
        self.sync_button.clicked.connect(lambda _checked=False: sync_now())
        self.refresh_button = QPushButton("Refresh", self)
        self.refresh_button.clicked.connect(self.refresh)
        self.more_button = QPushButton("Load older runs", self)
        self.more_button.clicked.connect(lambda _checked=False: self.load_more())
        self.more_button.setVisible(False)
        self.folder_button = QPushButton("Open folder", self)
        self.folder_button.clicked.connect(lambda _checked=False: open_folder())
        buttons = QHBoxLayout()
        buttons.addWidget(self.sync_button)
        buttons.addWidget(self.refresh_button)
        buttons.addStretch(1)
        buttons.addWidget(self.more_button)
        buttons.addWidget(self.folder_button)
        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addWidget(self.empty)
        layout.addWidget(splitter, 1)
        self.table.selectionModel().currentRowChanged.connect(self._show_details)

    def refresh(self) -> None:
        """Reload the records, keeping the selected run selected."""
        selected = self.selected()
        self.model.set_records(self._load(self.limit))
        self.more_button.setVisible(self.model.rowCount() >= self.limit)
        has_rows = self.model.rowCount() > 0
        self.empty.setVisible(not has_rows)
        if not has_rows:
            self.details.clear()
            return
        row = next(
            (
                index
                for index, record in enumerate(self.model.records)
                if selected is not None and record.id == selected.id
            ),
            0,
        )
        self.table.selectRow(row)

    def load_more(self) -> None:
        """Show another page of older runs."""
        self.limit += PAGE
        self.refresh()

    def selected(self) -> SyncRecord | None:
        """Return the selected run, if any."""
        index = self.table.currentIndex()
        return self.model.record(index.row()) if index.isValid() else None

    def present(self) -> None:
        """Refresh, show, and bring the window forward."""
        self.refresh()
        self.show()
        self.raise_()
        self.activateWindow()

    def _show_details(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if current.isValid():
            self.details.setPlainText(details(self.model.record(current.row())))


class HistoryPopup(QFrame):
    """The newest syncs, shown beside the tray icon on a click."""

    def __init__(
        self,
        sync_now: Callable[[], object],
        open_history: Callable[[], None],
        open_settings: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(
            parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint
        )
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setObjectName("SkillSincPopup")
        self.setFixedWidth(380)
        self.title = QLabel("<b>SkillSinc</b>", self)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.list = QListWidget(self)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.list.itemActivated.connect(lambda _item: self._then(open_history))
        self.list.itemClicked.connect(lambda _item: self._then(open_history))
        self.sync_button = QPushButton("Sync now", self)
        self.sync_button.setDefault(True)
        self.sync_button.clicked.connect(lambda: self._then(sync_now))
        self.history_button = QPushButton("History…", self)
        self.history_button.clicked.connect(lambda: self._then(open_history))
        self.settings_button = QPushButton("Settings…", self)
        self.settings_button.clicked.connect(lambda: self._then(open_settings))
        buttons = QHBoxLayout()
        buttons.addWidget(self.sync_button)
        buttons.addStretch(1)
        buttons.addWidget(self.history_button)
        buttons.addWidget(self.settings_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addWidget(self.status)
        layout.addWidget(self.list)
        layout.addLayout(buttons)

    def populate(self, status: str, records: Sequence[SyncRecord]) -> None:
        """Show ``status`` and the newest ``records``."""
        self.status.setText(status)
        self.list.clear()
        for record in records[:POPUP_ROWS]:
            mark = OK_MARK if record.ok else FAILED_MARK
            label = TRIGGER_LABELS.get(record.trigger.value, record.trigger.value)
            item = QListWidgetItem(
                f"{mark}  {when(record.started)}  {label}\n    {record.summary()}"
            )
            item.setForeground(QColor(OK_COLOR if record.ok else FAILED_COLOR))
            item.setToolTip(details(record))
            self.list.addItem(item)
        if not records:
            self.list.addItem(QListWidgetItem("No syncs recorded yet."))
        rows = max(1, min(len(records), POPUP_ROWS))
        self.list.setFixedHeight(rows * 2 * self.fontMetrics().height() + 12)
        self.adjustSize()

    def show_near(self, anchor: QPoint | None = None) -> None:
        """Show the popup beside ``anchor`` (default: the pointer), on screen."""
        point = anchor if anchor is not None else QCursor.pos()
        screen = QGuiApplication.screenAt(point) or QGuiApplication.primaryScreen()
        area = screen.availableGeometry()
        self.adjustSize()
        x = min(
            max(point.x() - self.width() // 2, area.left()), area.right() - self.width()
        )
        y = point.y() - self.height() if point.y() > area.center().y() else point.y()
        y = min(max(y, area.top()), area.bottom() - self.height())
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    @override
    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Close on Escape."""
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def _then(self, action: Callable[[], object]) -> None:
        self.hide()
        action()
