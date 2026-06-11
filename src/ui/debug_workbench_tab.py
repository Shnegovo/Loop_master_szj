"""Read-only debug workbench UI for Keil project source browsing."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygon, QTextCursor, QTextFormat
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTreeWidget,
    QTreeWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.debug_workbench import (
    BreakpointStore,
    CodeDocument,
    DebugAction,
    DebugCommandPlan,
    LineDecoration,
    SourceTreeNode,
    DebugWorkbenchSession,
    DebugWorkbenchStatus,
    line_decorations,
    load_code_document,
    search_document,
)
from src.core.debug_snapshots import DebugPcLocation
from src.core.debug_sources import (
    SourceManifest,
    source_manifest_from_keil_project,
    source_manifest_missing_path_hints,
)
from src.core.debug_transactions import DebugCommandHistoryEntry, DebugCommandTransaction
from src.core.keil.commands import KeilCommandTransaction
from src.core.keil.project import KeilProject, parse_keil_project
from src.ui.pcl_theme import PclComboBox, polish_combo_popup


ROLE_PATH = Qt.UserRole


class _LineNumberArea(QWidget):
    def __init__(self, editor: "SourceCodeEditor") -> None:
        super().__init__(editor)
        self._editor = editor
        self.setCursor(Qt.PointingHandCursor)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return QSize(self._editor.line_number_area_width(), 0)

    def paintEvent(self, event):  # noqa: N802 - Qt override
        self._editor.line_number_area_paint_event(event)

    def mousePressEvent(self, event):  # noqa: N802 - Qt override
        line = self._editor.line_at_y(event.position().toPoint().y())
        if line > 0:
            self._editor.gutterLineClicked.emit(line)
        event.accept()

    def mouseMoveEvent(self, event):  # noqa: N802 - Qt override
        line = self._editor.line_at_y(event.position().toPoint().y())
        tip = self._editor.gutter_tooltip_for_line(line)
        self.setToolTip(tip)
        super().mouseMoveEvent(event)


class SourceCodeEditor(QPlainTextEdit):
    """A light code preview with a gutter for line numbers and decorations."""

    gutterLineClicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._line_number_area = _LineNumberArea(self)
        self._document_model: CodeDocument | None = None
        self._decorations: tuple[LineDecoration, ...] = ()
        self._decorations_by_line: dict[int, list[LineDecoration]] = {}

        self.setObjectName("debugCodeEditor")
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.setTabStopDistance(36.0)
        self.setCenterOnScroll(True)
        self.setFont(QFont("Cascadia Code", 10))
        self.blockCountChanged.connect(self._update_line_number_area_width)
        self.updateRequest.connect(self._update_line_number_area)
        self.cursorPositionChanged.connect(self._highlight_current_line)
        self._update_line_number_area_width(0)

    def line_number_area_width(self) -> int:
        digits = max(3, len(str(max(1, self.blockCount()))))
        return 34 + self.fontMetrics().horizontalAdvance("9" * digits)

    def set_code_document(
        self,
        document: CodeDocument | None,
        decorations: tuple[LineDecoration, ...] = (),
    ) -> None:
        if document is None:
            if self._document_model is not None or self.toPlainText() != "请选择左侧源码文件。":
                self.setPlainText("请选择左侧源码文件。")
        elif self._document_model != document:
            self.setPlainText("\n".join(line.text for line in document.lines))
        self._document_model = document
        self.set_decorations(decorations)

    def set_decorations(self, decorations: tuple[LineDecoration, ...]) -> None:
        self._decorations = tuple(decorations)
        by_line: dict[int, list[LineDecoration]] = {}
        for decoration in self._decorations:
            by_line.setdefault(int(decoration.line), []).append(decoration)
        self._decorations_by_line = by_line
        self._highlight_current_line()
        self._line_number_area.update()
        self._line_number_area.setToolTip("")

    def gutter_tooltip_for_line(self, line: int) -> str:
        if line <= 0:
            return ""
        decorations = self._decorations_by_line.get(int(line), [])
        parts: list[str] = []
        for decoration in decorations:
            if decoration.kind == "breakpoint":
                state = "启用" if decoration.enabled else "停用"
                condition = f" · 条件: {decoration.label}" if decoration.label else ""
                verify = "已验证" if decoration.verified else "未验证" if decoration.message else "待验证"
                message = f" · {decoration.message}" if decoration.message else ""
                parts.append(f"{state}断点 · {verify}{condition}{message}")
            elif decoration.kind == "pc":
                evidence = "已回读" if decoration.verified else "未验证" if decoration.message else "待回读"
                message = f" · {decoration.message}" if decoration.message else ""
                parts.append(f"当前 PC · {evidence}{message}")
            elif decoration.kind == "run":
                parts.append("运行行")
            elif decoration.kind == "search_active":
                parts.append("当前搜索命中")
            elif decoration.kind == "search":
                parts.append("搜索命中")
        return "；".join(parts)

    def line_at_y(self, y: int) -> int:
        block = self.firstVisibleBlock()
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        while block.isValid():
            height = int(self.blockBoundingRect(block).height())
            if top <= y <= top + height:
                return block.blockNumber() + 1
            top += height
            block = block.next()
        return -1

    def resizeEvent(self, event):  # noqa: N802 - Qt override
        super().resizeEvent(event)
        rect = self.contentsRect()
        self._line_number_area.setGeometry(
            QRect(rect.left(), rect.top(), self.line_number_area_width(), rect.height())
        )

    def line_number_area_paint_event(self, event) -> None:
        painter = QPainter(self._line_number_area)
        painter.fillRect(event.rect(), QColor("#f3f7fb"))
        painter.setPen(QPen(QColor("#d8e4f1")))
        painter.drawLine(event.rect().right() - 1, event.rect().top(), event.rect().right() - 1, event.rect().bottom())

        block = self.firstVisibleBlock()
        line_number = block.blockNumber() + 1
        top = int(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + int(self.blockBoundingRect(block).height())
        width = self._line_number_area.width()

        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                line = str(line_number)
                decorations = self._decorations_by_line.get(line_number, [])
                self._paint_gutter_markers(painter, decorations, top, bottom, width)
                painter.setPen(QColor("#8290a3"))
                painter.drawText(22, top, width - 28, self.fontMetrics().height(), Qt.AlignRight, line)
            block = block.next()
            top = bottom
            bottom = top + int(self.blockBoundingRect(block).height())
            line_number += 1

    def _update_line_number_area_width(self, _block_count: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def _update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self._line_number_area.scroll(0, dy)
        else:
            self._line_number_area.update(0, rect.y(), self._line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_line_number_area_width(0)

    def _highlight_current_line(self) -> None:
        selections: list[QTextEdit.ExtraSelection] = []
        if not self.isReadOnly():
            selections.append(self._line_selection(self.textCursor().blockNumber() + 1, QColor("#f8fbff")))

        for line, decorations in sorted(self._decorations_by_line.items()):
            kinds = {decoration.kind for decoration in decorations}
            if "pc" in kinds:
                selections.append(self._line_selection(line, QColor("#dbeafe")))
            elif "run" in kinds:
                selections.append(self._line_selection(line, QColor("#dcfce7")))
            elif "search_active" in kinds:
                selections.append(self._line_selection(line, QColor("#ffe8a3")))
            elif "search" in kinds:
                selections.append(self._line_selection(line, QColor("#fff7cc")))
            elif "breakpoint" in kinds:
                selections.append(self._line_selection(line, QColor("#fff5f5")))
        self.setExtraSelections(selections)

    def _line_selection(self, line: int, color: QColor) -> QTextEdit.ExtraSelection:
        selection = QTextEdit.ExtraSelection()
        selection.format.setBackground(color)
        selection.format.setProperty(QTextFormat.FullWidthSelection, True)
        block = self.document().findBlockByNumber(max(0, int(line) - 1))
        cursor = QTextCursor(block)
        cursor.clearSelection()
        selection.cursor = cursor
        return selection

    def _paint_gutter_markers(
        self,
        painter: QPainter,
        decorations: list[LineDecoration],
        top: int,
        bottom: int,
        width: int,
    ) -> None:
        if not decorations:
            return
        kinds = {decoration.kind for decoration in decorations}
        center_y = top + max(1, (bottom - top) // 2)
        if "pc" in kinds:
            pc_decorations = [decoration for decoration in decorations if decoration.kind == "pc"]
            verified = any(decoration.verified for decoration in pc_decorations)
            if verified:
                painter.setBrush(QColor("#2563eb"))
                painter.setPen(Qt.NoPen)
            else:
                painter.setBrush(QColor("#bfdbfe"))
                painter.setPen(QPen(QColor("#2563eb"), 1.5, Qt.DashLine))
            points = [
                (8, center_y - 6),
                (8, center_y + 6),
                (17, center_y),
            ]
            painter.drawPolygon(QPolygon([QPoint(x, y) for x, y in points]))
        if "run" in kinds:
            painter.setBrush(QColor("#16a34a"))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(6, center_y - 5, 12, 10, 3, 3)
        if "breakpoint" in kinds:
            breakpoint_decorations = [decoration for decoration in decorations if decoration.kind == "breakpoint"]
            enabled = all(decoration.enabled for decoration in breakpoint_decorations)
            conditional = any(bool(decoration.label) for decoration in breakpoint_decorations)
            if enabled:
                painter.setPen(QPen(QColor("#b91c1c"), 1.8))
                painter.setBrush(QColor("#ef4444"))
            else:
                painter.setPen(QPen(QColor("#94a3b8"), 1.8, Qt.DashLine))
                painter.setBrush(QColor("#ffffff"))
            painter.drawEllipse(6, center_y - 6, 12, 12)
            if conditional:
                painter.setPen(QPen(QColor("#ffffff" if enabled else "#dc2626"), 1.5))
                painter.setBrush(QColor("#f59e0b") if enabled else QColor("#ffffff"))
                diamond = QPolygon([
                    QPoint(12, center_y - 4),
                    QPoint(16, center_y),
                    QPoint(12, center_y + 4),
                    QPoint(8, center_y),
                ])
                painter.drawPolygon(diamond)
        if "search" in kinds:
            painter.setPen(QPen(QColor("#f59e0b"), 2))
            painter.drawLine(width - 7, top + 4, width - 7, bottom - 4)
        if "search_active" in kinds:
            painter.setPen(QPen(QColor("#d97706"), 3))
            painter.drawLine(width - 11, top + 4, width - 11, bottom - 4)

class DebugWorkbenchTab(QWidget):
    """Modern Keil project source browser, disconnected from runtime control."""

    summaryChanged = Signal()
    debugActionRequested = Signal(str)
    backendSelectionChanged = Signal(str)
    sourceProviderSelectionChanged = Signal(str)
    sourceProviderConfigureRequested = Signal(str)
    sourceRemapRequested = Signal()
    variablePresetWriteRequested = Signal(str, str)
    variablePresetWatchRequested = Signal(str, str, str)
    profileSaveRequested = Signal()
    profileLoadRequested = Signal()
    keilProfileConfigureRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project: KeilProject | None = None
        self._source_manifest: SourceManifest | None = None
        self._source_manifest_mode = "empty"
        self._source_tree: SourceTreeNode | None = None
        self._breakpoints = BreakpointStore()
        self._current_document: CodeDocument | None = None
        self._current_pc_line: int | None = None
        self._run_line: int | None = None
        self._pc_evidence: DebugPcLocation | None = None
        self._active_search_line: int | None = None
        self._search_matches = ()
        self._search_index = -1
        self._breakpoint_rows = ()
        self._diagnostics: tuple[tuple[str, str], ...] = ()
        self._plan_rows: tuple[DebugCommandPlan, ...] = ()
        self._command_transactions: tuple[KeilCommandTransaction | DebugCommandTransaction, ...] = ()
        self._command_history_entries: tuple[DebugCommandHistoryEntry, ...] = ()
        self._breakpoint_table_syncing = False
        self._breakpoint_quick_syncing = False
        self._session = DebugWorkbenchSession()
        self._backend_controls_ready = False
        self._backend_options: tuple[tuple[str, str, str], ...] = ()
        self._backend_selection_syncing = False
        self._source_provider_options: tuple[tuple[str, str, str], ...] = ()
        self._source_provider_selection_syncing = False
        self._variable_presets: tuple[tuple[str, str, str, str, str, bool], ...] = ()

        self.setObjectName("debugWorkbenchTab")
        self._build_ui()
        self._apply_style()
        self._apply_debug_status(self._session.status)
        self._refresh_summary()

    @property
    def project_name(self) -> str:
        return self._project.name if self._project is not None else ""

    @property
    def source_count(self) -> int:
        if self._source_manifest is None:
            return 0
        return self._source_manifest.source_count

    @property
    def source_manifest(self) -> SourceManifest | None:
        return self._source_manifest

    @property
    def breakpoint_count(self) -> int:
        return len(self._breakpoints.all())

    @property
    def current_document(self) -> CodeDocument | None:
        return self._current_document

    @property
    def debug_status(self) -> DebugWorkbenchStatus:
        return self._session.status

    def current_cursor_location(self) -> tuple[Path, int] | None:
        if self._current_document is None:
            return None
        line = self._current_editor_line()
        if line < 1 or line > self._current_document.line_count:
            return None
        return self._current_document.path, line

    def show_source_location(self, path: str | Path, line: int) -> None:
        target = Path(path).expanduser().resolve()
        if self._current_document is None or self._current_document.path != target:
            if target.exists():
                self._load_source(target)
        self._scroll_editor_to_line(line)

    def local_breakpoints(self) -> tuple:
        return self._breakpoints.all()

    def local_source_paths(self) -> tuple[Path, ...]:
        if self._source_tree is None:
            return ()
        paths: list[Path] = []
        if self._source_manifest is not None:
            paths.extend(self._source_manifest.paths)
        else:
            for group in self._source_tree.children:
                for child in group.children:
                    if child.path is not None:
                        paths.append(child.path)
        return tuple(paths)

    def hero_summary(self) -> tuple[str, str, str]:
        if self._source_manifest is None:
            return "未加载源码清单", "只读预览", "0 个断点"
        if self._project is None and self._source_manifest_mode == "empty":
            return "未打开 Keil 工程", "只读预览", "0 个断点"
        return (
            self._source_manifest.name,
            f"{self.source_count} 个源码文件",
            f"{self.breakpoint_count} 个本地断点",
        )

    def load_project(self, path: str | Path) -> None:
        project = parse_keil_project(path)
        self.set_project(project)

    def set_project(self, project: KeilProject) -> None:
        self._project = project
        self._source_manifest_mode = "keil"
        self._source_manifest = source_manifest_from_keil_project(project)
        self._session.set_project(project)
        self._breakpoints.clear()
        self._current_document = None
        self._current_pc_line = None
        self._run_line = None
        self.target_combo.blockSignals(True)
        self.target_combo.clear()
        for target in project.targets:
            self.target_combo.addItem(target.name, target.name)
        self.target_combo.blockSignals(False)
        self.target_combo.setMaxVisibleItems(max(1, self.target_combo.count()))
        polish_combo_popup(self.target_combo)
        self._rebuild_source_tree()
        self._load_first_existing_source()
        self._apply_debug_status(self._session.status)
        self._refresh_summary()

    def set_source_manifest(
        self,
        manifest: SourceManifest,
        *,
        mode: str = "external",
        clear_breakpoints: bool = False,
    ) -> None:
        self._source_manifest_mode = str(mode or "external")
        self._source_manifest = manifest
        if clear_breakpoints:
            self._breakpoints.clear()
        self._current_document = None
        self._active_search_line = None
        self._search_index = -1
        self._rebuild_source_tree()
        self._load_first_existing_source()
        self._refresh_diagnostics_table()
        self._refresh_summary()

    def restore_project_source_manifest(self) -> bool:
        self._source_manifest_mode = "keil"
        if self._project is None:
            self._source_manifest = None
            self._current_document = None
            self._rebuild_source_tree()
            self._load_first_existing_source()
            self._refresh_diagnostics_table()
            self._refresh_summary()
            return False
        self._source_manifest = source_manifest_from_keil_project(self._project)
        self._rebuild_source_tree()
        self._load_first_existing_source()
        self._refresh_diagnostics_table()
        self._refresh_summary()
        return True

    def set_runtime_markers(self, current_pc_line: int | None = None, run_line: int | None = None) -> None:
        self._current_pc_line = current_pc_line
        self._run_line = run_line
        self._pc_evidence = None
        self._refresh_decorations()

    def set_pc_evidence(self, pc_location: DebugPcLocation | None) -> None:
        self._pc_evidence = pc_location
        if pc_location is not None and pc_location.line is not None:
            self._current_pc_line = pc_location.line
        self._refresh_decorations()

    def set_debug_status(self, status: DebugWorkbenchStatus, *, controls_ready: bool = False) -> None:
        self._session.apply_status(status)
        self._backend_controls_ready = bool(controls_ready)
        self._current_pc_line = status.current_pc_line
        self._run_line = status.run_line
        self._pc_evidence = None
        self._apply_debug_status(status)
        self._refresh_decorations()
        self._refresh_summary()

    def set_debug_controls_ready(self, ready: bool) -> None:
        self._backend_controls_ready = bool(ready)
        self._apply_debug_status(self._session.status)

    def set_backend_diagnostics(self, items: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> None:
        self._diagnostics = tuple((str(key), str(value)) for key, value in items)
        self._refresh_diagnostics_table()

    def set_variable_presets(
        self,
        presets: tuple[tuple[str, str, str, str, str, bool], ...] | list[tuple[str, str, str, str, str, bool]],
    ) -> None:
        self._variable_presets = tuple(
            (
                str(expression),
                str(label),
                str(value_type),
                str(default_value),
                str(purpose),
                bool(write_allowed),
            )
            for expression, label, value_type, default_value, purpose, write_allowed in presets
        )
        self._refresh_variable_preset_table()

    def set_backend_options(
        self,
        options: tuple[tuple[str, str, str], ...] | list[tuple[str, str, str]],
        selected: str,
    ) -> None:
        self._backend_options = tuple(
            (str(key), str(label), str(note))
            for key, label, note in options
        )
        if not hasattr(self, "backend_combo"):
            return
        self._backend_selection_syncing = True
        try:
            self.backend_combo.clear()
            selected_index = 0
            for index, (key, label, note) in enumerate(self._backend_options):
                self.backend_combo.addItem(label, key)
                self.backend_combo.setItemData(index, note, Qt.ToolTipRole)
                if key == selected:
                    selected_index = index
            if self.backend_combo.count():
                self.backend_combo.setCurrentIndex(selected_index)
        finally:
            self._backend_selection_syncing = False

    def set_source_provider_options(
        self,
        options: tuple[tuple[str, str, str], ...] | list[tuple[str, str, str]],
        selected: str,
    ) -> None:
        self._source_provider_options = tuple(
            (str(key), str(label), str(note))
            for key, label, note in options
        )
        if not hasattr(self, "source_provider_combo"):
            return
        self._source_provider_selection_syncing = True
        try:
            self.source_provider_combo.clear()
            selected_index = 0
            for index, (key, label, note) in enumerate(self._source_provider_options):
                self.source_provider_combo.addItem(label, key)
                self.source_provider_combo.setItemData(index, note, Qt.ToolTipRole)
                if key == selected:
                    selected_index = index
            if self.source_provider_combo.count():
                self.source_provider_combo.setCurrentIndex(selected_index)
        finally:
            self._source_provider_selection_syncing = False

    def set_command_transactions(
        self,
        transactions: tuple[KeilCommandTransaction | DebugCommandTransaction, ...] | list[KeilCommandTransaction | DebugCommandTransaction],
    ) -> None:
        self._command_transactions = tuple(transactions)
        self._refresh_command_plan_preview()

    def set_command_history_entries(
        self,
        entries: tuple[DebugCommandHistoryEntry, ...] | list[DebugCommandHistoryEntry],
    ) -> None:
        self._command_history_entries = tuple(entries)
        self._refresh_command_history_preview()

    def add_breakpoint(
        self,
        line: int,
        *,
        path: str | Path | None = None,
        enabled: bool = True,
        condition: str = "",
    ) -> None:
        breakpoint_path = Path(path) if path is not None else (
            self._current_document.path if self._current_document is not None else None
        )
        if breakpoint_path is None:
            return
        breakpoint = self._breakpoints.add(breakpoint_path, line, enabled=enabled, condition=condition)
        self._refresh_breakpoint_views(select_path=breakpoint.path, select_line=breakpoint.line)

    def set_breakpoint_verification(
        self,
        line: int,
        *,
        path: str | Path | None = None,
        verified: bool,
        message: str = "",
    ) -> None:
        breakpoint_path = Path(path) if path is not None else (
            self._current_document.path if self._current_document is not None else None
        )
        if breakpoint_path is None:
            return
        try:
            breakpoint = self._breakpoints.set_verified(breakpoint_path, line, verified, message)
        except KeyError:
            return
        self._refresh_breakpoint_views(select_path=breakpoint.path, select_line=breakpoint.line)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        root.addWidget(self._build_toolbar())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setObjectName("debugWorkbenchSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(4)
        splitter.setOpaqueResize(False)
        splitter.addWidget(self._build_navigation_panel())
        splitter.addWidget(self._build_editor_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([320, 860])
        root.addWidget(splitter, 1)

    def _build_toolbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("debugCard")
        layout = QGridLayout(bar)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(8)

        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("debugStatusDot")
        self.status_text = QLabel("Keil 只读工作台")
        self.status_text.setObjectName("debugHint")
        status = QHBoxLayout()
        status.setSpacing(6)
        status.addWidget(self.status_dot)
        status.addWidget(self.status_text)
        status.addStretch(1)
        layout.addLayout(status, 0, 0)

        self.backend_combo = PclComboBox()
        self.backend_combo.setObjectName("debugCombo")
        self.backend_combo.setMinimumWidth(170)
        self.backend_combo.setToolTip("选择调试后端")
        self.backend_combo.currentIndexChanged.connect(self._on_backend_combo_changed)
        layout.addWidget(self.backend_combo, 0, 1)

        self.open_project_button = QPushButton("打开 Keil 工程")
        self.open_project_button.setObjectName("debugPrimaryButton")
        self.open_project_button.clicked.connect(self._choose_project)
        layout.addWidget(self.open_project_button, 0, 2)

        self.target_combo = PclComboBox()
        self.target_combo.setObjectName("debugCombo")
        self.target_combo.setMinimumWidth(180)
        self.target_combo.currentTextChanged.connect(self._on_target_changed)
        layout.addWidget(self.target_combo, 0, 3)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("debugSearch")
        self.search_edit.setPlaceholderText("搜索当前文件")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search_changed)
        search_box = QHBoxLayout()
        search_box.setContentsMargins(0, 0, 0, 0)
        search_box.setSpacing(6)
        search_box.addWidget(self.search_edit, 1)
        self.search_prev_button = QPushButton("上一处")
        self.search_prev_button.setObjectName("debugSearchNavButton")
        self.search_prev_button.setToolTip("跳到上一处搜索结果")
        self.search_prev_button.clicked.connect(lambda: self._navigate_search(-1))
        search_box.addWidget(self.search_prev_button)
        self.search_next_button = QPushButton("下一处")
        self.search_next_button.setObjectName("debugSearchNavButton")
        self.search_next_button.setToolTip("跳到下一处搜索结果")
        self.search_next_button.clicked.connect(lambda: self._navigate_search(1))
        search_box.addWidget(self.search_next_button)

        self.summary_label = QLabel("未打开工程")
        self.summary_label.setObjectName("debugSummary")
        layout.addWidget(self.summary_label, 1, 0, 1, 3)
        layout.addLayout(search_box, 1, 3, 1, 2)

        actions = self._build_action_bar()
        layout.addLayout(actions, 2, 0, 1, 5)
        layout.addWidget(self._build_action_plan_strip(), 3, 0, 1, 5)
        layout.setColumnStretch(4, 1)
        return bar

    def _build_action_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self._action_buttons: dict[str, QPushButton] = {}
        groups = (
            (("discover", "发现"), ("build_project", "构建"), ("launch_uvsock", "启动"), ("auto_debug", "调试")),
            (("attach", "连接"), ("disconnect", "断开")),
            (("halt", "暂停"), ("run", "运行"), ("reset", "复位"), ("step", "单步"), ("step_over", "跨过"), ("run_to_cursor", "到光标")),
            (("sync_breakpoints", "断点"), ("write_variables", "写入")),
        )
        for group_index, group in enumerate(groups):
            if group_index:
                layout.addWidget(self._action_separator())
            for key, title in group:
                button = QPushButton(title)
                button.setObjectName("debugActionButton")
                button.setCursor(Qt.PointingHandCursor)
                button.setMinimumWidth(54 if len(title) >= 3 else 46)
                button.setEnabled(False)
                button.clicked.connect(lambda _checked=False, action_key=key: self.debugActionRequested.emit(action_key))
                self._action_buttons[key] = button
                layout.addWidget(button)
        layout.addStretch(1)
        return layout

    @staticmethod
    def _action_separator() -> QFrame:
        separator = QFrame()
        separator.setObjectName("debugActionSeparator")
        separator.setFrameShape(QFrame.VLine)
        separator.setFixedWidth(8)
        return separator

    def _build_action_plan_strip(self) -> QFrame:
        strip = QFrame()
        strip.setObjectName("debugPlanStrip")
        strip.setCursor(Qt.WhatsThisCursor)
        layout = QHBoxLayout(strip)
        layout.setContentsMargins(10, 7, 10, 7)
        layout.setSpacing(8)

        label = QLabel("动作计划")
        label.setObjectName("debugPlanTitle")
        layout.addWidget(label)

        self.plan_focus_label = QLabel("等待状态")
        self.plan_focus_label.setObjectName("debugPlanFocus")
        layout.addWidget(self.plan_focus_label)

        self.plan_state_label = QLabel("等待条件")
        self.plan_state_label.setObjectName("debugPlanState")
        layout.addWidget(self.plan_state_label)

        self.plan_risk_label = QLabel("信息")
        self.plan_risk_label.setObjectName("debugPlanRisk")
        layout.addWidget(self.plan_risk_label)

        self.plan_guard_label = QLabel("所有真实调试动作仍处于预览保护")
        self.plan_guard_label.setObjectName("debugPlanGuard")
        self.plan_guard_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.plan_guard_label, 1)

        self.plan_history_label = QLabel("历史 0")
        self.plan_history_label.setObjectName("debugPlanHistory")
        layout.addWidget(self.plan_history_label)
        return strip

    def _build_navigation_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("debugCard")
        outer_layout = QVBoxLayout(panel)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setObjectName("debugNavigationScroll")
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer_layout.addWidget(scroll)

        content = QFrame()
        content.setObjectName("debugNavigationContent")
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(9)

        source_header = QHBoxLayout()
        source_header.setContentsMargins(0, 0, 0, 0)
        source_header.setSpacing(8)
        title = QLabel("源码树")
        title.setObjectName("debugSectionTitle")
        source_header.addWidget(title)
        source_header.addStretch(1)
        self.source_provider_combo = PclComboBox()
        self.source_provider_combo.setObjectName("debugSourceProviderCombo")
        self.source_provider_combo.setMinimumWidth(118)
        self.source_provider_combo.setToolTip("选择源码来源预览")
        self.source_provider_combo.currentIndexChanged.connect(self._on_source_provider_combo_changed)
        source_header.addWidget(self.source_provider_combo)
        self.source_provider_configure_button = QPushButton("配置")
        self.source_provider_configure_button.setObjectName("debugMiniButton")
        self.source_provider_configure_button.setCursor(Qt.PointingHandCursor)
        self.source_provider_configure_button.setToolTip("配置当前源码来源")
        self.source_provider_configure_button.clicked.connect(self._on_source_provider_configure_clicked)
        source_header.addWidget(self.source_provider_configure_button)
        self.source_provider_remap_button = QPushButton("映射")
        self.source_provider_remap_button.setObjectName("debugMiniButton")
        self.source_provider_remap_button.setCursor(Qt.PointingHandCursor)
        self.source_provider_remap_button.setToolTip("把缺失源码目录映射到本地源码根")
        self.source_provider_remap_button.clicked.connect(self.sourceRemapRequested.emit)
        self.source_provider_remap_button.setEnabled(False)
        source_header.addWidget(self.source_provider_remap_button)
        layout.addLayout(source_header)

        layout.addWidget(self._build_source_manifest_strip())

        self.source_tree = QTreeWidget()
        self.source_tree.setObjectName("debugSourceTree")
        self.source_tree.setHeaderHidden(True)
        self.source_tree.setIndentation(16)
        self.source_tree.setAnimated(True)
        self.source_tree.setUniformRowHeights(True)
        self.source_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.source_tree.itemClicked.connect(self._on_source_item_clicked)
        layout.addWidget(self.source_tree, 3)

        diag_header = QHBoxLayout()
        diag_header.setContentsMargins(0, 0, 0, 0)
        diag_header.setSpacing(7)
        diag_title = QLabel("后端诊断")
        diag_title.setObjectName("debugSectionTitle")
        diag_header.addWidget(diag_title)
        diag_header.addStretch(1)
        self.keil_profile_config_button = QPushButton("Keil配置")
        self.keil_profile_config_button.setObjectName("debugMiniButton")
        self.keil_profile_config_button.setCursor(Qt.PointingHandCursor)
        self.keil_profile_config_button.setToolTip("配置 Keil 根目录和 UVSOCK 端口")
        self.keil_profile_config_button.clicked.connect(self.keilProfileConfigureRequested.emit)
        diag_header.addWidget(self.keil_profile_config_button)
        self.profile_save_button = QPushButton("保存档案")
        self.profile_save_button.setObjectName("debugMiniButton")
        self.profile_save_button.setCursor(Qt.PointingHandCursor)
        self.profile_save_button.setToolTip("保存当前 Keil 工程/Target/端口为默认调试档案")
        self.profile_save_button.clicked.connect(self.profileSaveRequested.emit)
        diag_header.addWidget(self.profile_save_button)
        self.profile_load_button = QPushButton("载入")
        self.profile_load_button.setObjectName("debugMiniButton")
        self.profile_load_button.setCursor(Qt.PointingHandCursor)
        self.profile_load_button.setToolTip("载入默认 Keil 调试档案")
        self.profile_load_button.clicked.connect(self.profileLoadRequested.emit)
        diag_header.addWidget(self.profile_load_button)
        layout.addLayout(diag_header)

        self.diagnostics_table = QTableWidget()
        self.diagnostics_table.setObjectName("debugDiagnosticsTable")
        self.diagnostics_table.setColumnCount(2)
        self.diagnostics_table.setHorizontalHeaderLabels(["项目", "状态"])
        self.diagnostics_table.horizontalHeader().setStretchLastSection(False)
        self.diagnostics_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.diagnostics_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.diagnostics_table.verticalHeader().setVisible(False)
        self.diagnostics_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.diagnostics_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.diagnostics_table.setAlternatingRowColors(True)
        self.diagnostics_table.verticalHeader().setDefaultSectionSize(24)
        self.diagnostics_table.verticalHeader().setMinimumSectionSize(22)
        self.diagnostics_table.setMinimumHeight(138)
        layout.addWidget(self.diagnostics_table, 1)

        preset_title = QLabel("变量预设")
        preset_title.setObjectName("debugSectionTitle")
        layout.addWidget(preset_title)

        self.variable_preset_table = QTableWidget()
        self.variable_preset_table.setObjectName("debugVariablePresetTable")
        self.variable_preset_table.setColumnCount(4)
        self.variable_preset_table.setHorizontalHeaderLabels(["变量", "默认", "类型", "用途"])
        self.variable_preset_table.horizontalHeader().setStretchLastSection(False)
        self.variable_preset_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.variable_preset_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.variable_preset_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.variable_preset_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.variable_preset_table.verticalHeader().setVisible(False)
        self.variable_preset_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.variable_preset_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.variable_preset_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.variable_preset_table.setAlternatingRowColors(True)
        self.variable_preset_table.verticalHeader().setDefaultSectionSize(24)
        self.variable_preset_table.verticalHeader().setMinimumSectionSize(22)
        self.variable_preset_table.setMinimumHeight(92)
        self.variable_preset_table.cellDoubleClicked.connect(self._on_variable_preset_double_clicked)
        layout.addWidget(self.variable_preset_table, 1)

        preset_actions = QHBoxLayout()
        preset_actions.setContentsMargins(0, 0, 0, 0)
        preset_actions.setSpacing(7)
        self.variable_preset_write_button = QPushButton("写入预设")
        self.variable_preset_write_button.setObjectName("debugMiniButton")
        self.variable_preset_write_button.setCursor(Qt.PointingHandCursor)
        self.variable_preset_write_button.setEnabled(False)
        self.variable_preset_write_button.clicked.connect(self._emit_selected_variable_preset)
        preset_actions.addWidget(self.variable_preset_write_button)
        self.variable_preset_watch_button = QPushButton("加入示波")
        self.variable_preset_watch_button.setObjectName("debugMiniButton")
        self.variable_preset_watch_button.setCursor(Qt.PointingHandCursor)
        self.variable_preset_watch_button.setEnabled(False)
        self.variable_preset_watch_button.clicked.connect(self._emit_selected_variable_watch_preset)
        preset_actions.addWidget(self.variable_preset_watch_button)
        preset_actions.addStretch(1)
        layout.addLayout(preset_actions)

        bp_title = QLabel("本地断点")
        bp_title.setObjectName("debugSectionTitle")
        layout.addWidget(bp_title)

        self.breakpoint_table = QTableWidget()
        self.breakpoint_table.setObjectName("debugBreakpointTable")
        self.breakpoint_table.setColumnCount(6)
        self.breakpoint_table.setHorizontalHeaderLabels(["启用", "文件", "行", "条件", "验证", "操作"])
        self.breakpoint_table.horizontalHeader().setStretchLastSection(False)
        self.breakpoint_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.breakpoint_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.breakpoint_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.breakpoint_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.breakpoint_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.breakpoint_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.breakpoint_table.verticalHeader().setVisible(False)
        self.breakpoint_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.breakpoint_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.breakpoint_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.breakpoint_table.setAlternatingRowColors(True)
        self.breakpoint_table.cellClicked.connect(self._on_breakpoint_table_clicked)
        self.breakpoint_table.currentCellChanged.connect(self._on_breakpoint_table_current_changed)
        self.breakpoint_table.itemChanged.connect(self._on_breakpoint_table_item_changed)
        self.breakpoint_table.setMinimumHeight(108)
        layout.addWidget(self.breakpoint_table, 1)
        layout.addWidget(self._build_breakpoint_quick_editor())
        self._refresh_diagnostics_table()
        self._refresh_variable_preset_table()
        return panel

    def _on_backend_combo_changed(self) -> None:
        if self._backend_selection_syncing:
            return
        key = self.backend_combo.currentData()
        if key:
            self.backendSelectionChanged.emit(str(key))

    def _on_source_provider_combo_changed(self) -> None:
        if self._source_provider_selection_syncing:
            return
        key = self.source_provider_combo.currentData()
        if key:
            self.sourceProviderSelectionChanged.emit(str(key))

    def _on_source_provider_configure_clicked(self) -> None:
        key = self.source_provider_combo.currentData() if hasattr(self, "source_provider_combo") else ""
        self.sourceProviderConfigureRequested.emit(str(key or "auto"))

    def _build_source_manifest_strip(self) -> QFrame:
        strip = QFrame()
        strip.setObjectName("debugSourceStrip")
        row = QHBoxLayout(strip)
        row.setContentsMargins(8, 6, 8, 6)
        row.setSpacing(6)
        self.source_provider_state_label = QLabel("来源 --")
        self.source_provider_state_label.setObjectName("debugSourceChip")
        row.addWidget(self.source_provider_state_label)
        self.source_provider_count_label = QLabel("0 文件")
        self.source_provider_count_label.setObjectName("debugSourceChip")
        row.addWidget(self.source_provider_count_label)
        self.source_provider_missing_label = QLabel("路径 --")
        self.source_provider_missing_label.setObjectName("debugSourceChip")
        row.addWidget(self.source_provider_missing_label)
        row.addStretch(1)
        return strip

    def _build_breakpoint_quick_editor(self) -> QFrame:
        editor = QFrame()
        editor.setObjectName("debugBreakpointEditor")
        row = QHBoxLayout(editor)
        row.setContentsMargins(8, 7, 8, 7)
        row.setSpacing(7)

        self.breakpoint_editor_label = QLabel("未选择断点")
        self.breakpoint_editor_label.setObjectName("debugBreakpointEditorLabel")
        row.addWidget(self.breakpoint_editor_label)

        self.breakpoint_editor_enabled = QPushButton("启用")
        self.breakpoint_editor_enabled.setObjectName("debugMiniToggleButton")
        self.breakpoint_editor_enabled.setCheckable(True)
        self.breakpoint_editor_enabled.setCursor(Qt.PointingHandCursor)
        self.breakpoint_editor_enabled.toggled.connect(self._on_breakpoint_quick_enabled_toggled)
        row.addWidget(self.breakpoint_editor_enabled)

        self.breakpoint_editor_condition = QLineEdit()
        self.breakpoint_editor_condition.setObjectName("debugBreakpointConditionEdit")
        self.breakpoint_editor_condition.setPlaceholderText("条件表达式")
        self.breakpoint_editor_condition.editingFinished.connect(self._apply_breakpoint_quick_condition)
        row.addWidget(self.breakpoint_editor_condition, 1)

        self.breakpoint_editor_clear = QPushButton("清空")
        self.breakpoint_editor_clear.setObjectName("debugMiniButton")
        self.breakpoint_editor_clear.setCursor(Qt.PointingHandCursor)
        self.breakpoint_editor_clear.clicked.connect(self._clear_breakpoint_quick_condition)
        row.addWidget(self.breakpoint_editor_clear)

        self.breakpoint_editor_delete = QPushButton("删除")
        self.breakpoint_editor_delete.setObjectName("debugMiniDangerButton")
        self.breakpoint_editor_delete.setCursor(Qt.PointingHandCursor)
        self.breakpoint_editor_delete.clicked.connect(self._remove_selected_breakpoint)
        row.addWidget(self.breakpoint_editor_delete)
        return editor

    def _build_editor_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("debugCard")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(8)
        self.file_label = QLabel("源码预览")
        self.file_label.setObjectName("debugSectionTitle")
        header.addWidget(self.file_label)
        header.addStretch(1)
        self.marker_label = QLabel("未连接运行时")
        self.marker_label.setObjectName("debugHint")
        header.addWidget(self.marker_label)
        self.current_line_condition_button = QPushButton("当前行条件")
        self.current_line_condition_button.setObjectName("debugMiniButton")
        self.current_line_condition_button.setCursor(Qt.PointingHandCursor)
        self.current_line_condition_button.setToolTip("为当前源码行创建或编辑条件断点")
        self.current_line_condition_button.clicked.connect(self._edit_current_line_breakpoint_condition)
        self.current_line_condition_button.setEnabled(False)
        header.addWidget(self.current_line_condition_button)
        layout.addLayout(header)

        self.editor = SourceCodeEditor()
        self.editor.gutterLineClicked.connect(self._toggle_breakpoint)
        layout.addWidget(self.editor, 1)
        self.editor.set_code_document(None)
        return panel

    def _choose_project(self) -> None:
        path, _selected_filter = QFileDialog.getOpenFileName(
            self,
            "打开 Keil 工程",
            "",
            "Keil Project (*.uvprojx *.uvproj);;All Files (*)",
        )
        if path:
            self.load_project(path)

    def _rebuild_source_tree(self) -> None:
        self.source_tree.clear()
        if self._source_manifest_mode == "keil":
            if self._project is None:
                self._source_manifest = None
                self._source_tree = None
                placeholder = QTreeWidgetItem(["未打开工程"])
                placeholder.setDisabled(True)
                self.source_tree.addTopLevelItem(placeholder)
                self._refresh_source_provider_summary()
                return
            target_name = self.target_combo.currentData() or self.target_combo.currentText() or None
            if target_name:
                self._session.set_project(self._project, str(target_name))
                self._apply_debug_status(self._session.status)
            self._source_manifest = source_manifest_from_keil_project(self._project, str(target_name) if target_name else None)
        if self._source_manifest is None:
            self._source_tree = None
            placeholder = QTreeWidgetItem(["未加载源码清单"])
            placeholder.setDisabled(True)
            self.source_tree.addTopLevelItem(placeholder)
            self._refresh_source_provider_summary()
            return
        self._source_tree = self._source_manifest.tree
        if not self._source_tree.children:
            placeholder = QTreeWidgetItem([f"{self._source_manifest.name} 无源码文件"])
            placeholder.setDisabled(True)
            self.source_tree.addTopLevelItem(placeholder)
            self._refresh_source_provider_summary()
            return
        for group in self._source_tree.children:
            group_item = QTreeWidgetItem([group.name])
            group_item.setExpanded(True)
            self.source_tree.addTopLevelItem(group_item)
            for child in group.children:
                file_item = QTreeWidgetItem([child.name])
                if child.path is not None:
                    file_item.setData(0, ROLE_PATH, str(child.path))
                    if not child.path.exists():
                        file_item.setText(0, f"{child.name}  (缺失)")
                        file_item.setDisabled(True)
                group_item.addChild(file_item)
        self.source_tree.expandToDepth(0)
        self._refresh_source_provider_summary()

    def _on_target_changed(self) -> None:
        if self._source_manifest_mode != "keil":
            self._refresh_summary()
            return
        self._rebuild_source_tree()
        self._load_first_existing_source()
        self._refresh_summary()

    def _load_first_existing_source(self) -> None:
        if self._source_tree is None:
            return
        for group in self._source_tree.children:
            for child in group.children:
                if child.path is not None and child.path.exists():
                    self._load_source(child.path)
                    return
        self.editor.set_code_document(None)
        if hasattr(self, "current_line_condition_button"):
            self.current_line_condition_button.setEnabled(False)
        self.file_label.setText("源码预览")

    def _on_source_item_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        path_text = item.data(0, ROLE_PATH)
        if path_text:
            self._load_source(Path(path_text))

    def _load_source(self, path: Path) -> None:
        try:
            document = load_code_document(path)
        except Exception as exc:
            self._current_document = None
            self.editor.set_code_document(None)
            self.file_label.setText(path.name)
            self.summary_label.setText(f"源码读取失败：{exc}")
            if hasattr(self, "current_line_condition_button"):
                self.current_line_condition_button.setEnabled(False)
            return
        self._current_document = document
        if hasattr(self, "current_line_condition_button"):
            self.current_line_condition_button.setEnabled(True)
        self.file_label.setText(f"{document.path.name}  ·  {document.line_count} 行")
        self._select_source_tree_path(document.path)
        self._active_search_line = None
        self._search_index = -1
        self._refresh_decorations()
        self._refresh_summary()

    def _toggle_breakpoint(self, line: int) -> None:
        if self._current_document is None:
            return
        breakpoint = self._breakpoints.toggle(self._current_document.path, line)
        if breakpoint is None:
            self._refresh_breakpoint_views()
        else:
            self._refresh_breakpoint_views(select_path=breakpoint.path, select_line=breakpoint.line)

    def _refresh_breakpoint_views(self, *, select_path: Path | None = None, select_line: int | None = None) -> None:
        self._refresh_decorations()
        self._refresh_summary()
        if select_path is not None and select_line is not None:
            self._select_breakpoint_row(select_path, select_line)

    def _refresh_decorations(self) -> None:
        if self._current_document is None:
            self.editor.set_code_document(None)
            return
        decorations = line_decorations(
            self._current_document,
            self._breakpoints,
            current_pc_line=self._current_pc_line,
            run_line=self._run_line,
            search_query=self.search_edit.text(),
        )
        decorations = self._with_pc_decoration_evidence(decorations)
        self._refresh_search_matches()
        if self._active_search_line is not None:
            decorations = tuple(decorations) + (
                LineDecoration(line=self._active_search_line, kind="search_active", label="当前搜索"),
            )
        self.editor.set_code_document(self._current_document, decorations)
        self.marker_label.setText(self._marker_text(decorations))

    def _with_pc_decoration_evidence(
        self,
        decorations: tuple[LineDecoration, ...],
    ) -> tuple[LineDecoration, ...]:
        if not decorations:
            return decorations
        result: list[LineDecoration] = []
        pc_message = self._pc_decoration_message()
        pc_verified = bool(self._pc_evidence and self._pc_evidence.complete)
        for decoration in decorations:
            if decoration.kind != "pc":
                result.append(decoration)
                continue
            result.append(
                LineDecoration(
                    line=decoration.line,
                    kind=decoration.kind,
                    label="PC 已回读" if pc_verified else "PC 未验证",
                    enabled=decoration.enabled,
                    verified=pc_verified,
                    message=pc_message,
                )
            )
        return tuple(result)

    def _pc_decoration_message(self) -> str:
        if self._pc_evidence is None:
            if self._current_pc_line is None:
                return ""
            return "来源: 本地状态；尚未由调试后端回读验证"
        parts: list[str] = []
        source = self._pc_source_label(self._pc_evidence.source)
        if source:
            parts.append(f"来源: {source}")
        if self._pc_evidence.address is not None:
            parts.append(f"地址: 0x{int(self._pc_evidence.address):08X}")
        if self._pc_evidence.function:
            parts.append(f"函数: {self._pc_evidence.function}")
        if self._pc_evidence.message:
            parts.append(self._pc_evidence.message)
        if not parts and not self._pc_evidence.complete:
            parts.append("尚未由调试后端回读验证")
        return "；".join(parts)

    @staticmethod
    def _pc_source_label(source: str) -> str:
        labels = {
            "status": "本地状态",
            "keil_uvsock": "Keil/UVSOCK",
            "openocd_gdb": "OpenOCD/GDB",
            "pyocd": "pyOCD",
        }
        return labels.get(str(source or ""), str(source or ""))

    def _refresh_breakpoint_table(self) -> None:
        breakpoints = self._breakpoints.all()
        self._breakpoint_rows = breakpoints
        self._breakpoint_table_syncing = True
        self.breakpoint_table.blockSignals(True)
        self.breakpoint_table.setRowCount(len(breakpoints))
        for row, breakpoint in enumerate(breakpoints):
            self._set_table_item(
                row,
                0,
                "",
                checkable=True,
                checked=breakpoint.enabled,
                tooltip="启用或停用这个本地断点",
            )
            self._set_table_item(row, 1, breakpoint.path.name, tooltip=str(breakpoint.path))
            self._set_table_item(row, 2, str(breakpoint.line))
            self._set_table_item(
                row,
                3,
                breakpoint.condition,
                editable=True,
                tooltip="双击编辑条件表达式；留空表示普通断点",
            )
            self._set_table_item(
                row,
                4,
                self._breakpoint_verify_label(breakpoint.verified, breakpoint.message),
                tooltip=self._breakpoint_verify_tooltip(breakpoint.verified, breakpoint.message),
            )
            remove_button = QPushButton("删除")
            remove_button.setObjectName("debugTableDangerButton")
            remove_button.setCursor(Qt.PointingHandCursor)
            remove_button.setToolTip("移除这个本地断点")
            remove_button.clicked.connect(
                lambda _checked=False, path=breakpoint.path, line=breakpoint.line: self._remove_breakpoint(path, line)
            )
            self.breakpoint_table.setCellWidget(row, 5, remove_button)
        self.breakpoint_table.blockSignals(False)
        self._breakpoint_table_syncing = False
        self._refresh_breakpoint_quick_editor()

    def _on_search_changed(self) -> None:
        self._active_search_line = None
        self._search_index = -1
        self._refresh_decorations()

    def _refresh_search_matches(self) -> None:
        if self._current_document is None:
            self._search_matches = ()
        else:
            self._search_matches = search_document(self._current_document, self.search_edit.text())
        if not self._search_matches:
            self._search_index = -1
            self._active_search_line = None
        elif self._search_index >= len(self._search_matches):
            self._search_index = len(self._search_matches) - 1
        self._refresh_search_buttons()

    def _refresh_search_buttons(self) -> None:
        enabled = bool(self._search_matches)
        for button in (getattr(self, "search_prev_button", None), getattr(self, "search_next_button", None)):
            if button is not None:
                button.setEnabled(enabled)

    def _navigate_search(self, delta: int) -> None:
        if self._current_document is None:
            return
        self._refresh_search_matches()
        if not self._search_matches:
            return
        if self._search_index < 0:
            self._search_index = 0 if delta >= 0 else len(self._search_matches) - 1
        else:
            self._search_index = (self._search_index + int(delta)) % len(self._search_matches)
        match = self._search_matches[self._search_index]
        self._active_search_line = match.line
        self._scroll_editor_to_line(match.line)
        self._refresh_decorations()

    def _on_breakpoint_table_clicked(self, row: int, column: int) -> None:
        if not (0 <= int(row) < len(self._breakpoint_rows)):
            return
        if int(column) in {0, 3, 4, 5}:
            return
        breakpoint = self._breakpoint_rows[int(row)]
        self._load_source(breakpoint.path)
        self._scroll_editor_to_line(breakpoint.line)

    def _on_breakpoint_table_current_changed(self, _current_row: int, _current_column: int, _previous_row: int, _previous_column: int) -> None:
        self._refresh_breakpoint_quick_editor()

    def _on_breakpoint_table_item_changed(self, item: QTableWidgetItem) -> None:
        if self._breakpoint_table_syncing:
            return
        row = int(item.row())
        column = int(item.column())
        if not (0 <= row < len(self._breakpoint_rows)):
            return
        breakpoint = self._breakpoint_rows[row]
        try:
            if column == 0:
                self._breakpoints.set_enabled(breakpoint.path, breakpoint.line, item.checkState() == Qt.Checked)
            elif column == 3:
                self._breakpoints.set_condition(breakpoint.path, breakpoint.line, item.text().strip())
            else:
                return
        except KeyError:
            return
        self._refresh_breakpoint_views()

    def _remove_breakpoint(self, path: Path, line: int) -> None:
        if not self._breakpoints.remove(path, line):
            return
        self._refresh_breakpoint_views()

    def _select_breakpoint_row(self, path: Path, line: int) -> None:
        target_path = str(Path(path).expanduser().resolve()).lower()
        target_line = int(line)
        for row, breakpoint in enumerate(self._breakpoint_rows):
            current_path = str(breakpoint.path.expanduser().resolve()).lower()
            if current_path == target_path and int(breakpoint.line) == target_line:
                self.breakpoint_table.setCurrentCell(row, 3)
                self.breakpoint_table.selectRow(row)
                self._refresh_breakpoint_quick_editor()
                return
        self._refresh_breakpoint_quick_editor()

    def _current_editor_line(self) -> int:
        if self._current_document is None:
            return -1
        return self.editor.textCursor().blockNumber() + 1

    def _edit_current_line_breakpoint_condition(self) -> None:
        if self._current_document is None:
            return
        line = self._current_editor_line()
        if line <= 0:
            return
        breakpoint = self._breakpoints.get(self._current_document.path, line)
        if breakpoint is None:
            breakpoint = self._breakpoints.add(self._current_document.path, line)
        self._refresh_breakpoint_views(select_path=breakpoint.path, select_line=breakpoint.line)
        self.breakpoint_editor_condition.setFocus(Qt.OtherFocusReason)
        self.breakpoint_editor_condition.selectAll()

    def _selected_breakpoint(self):
        row = self.breakpoint_table.currentRow() if hasattr(self, "breakpoint_table") else -1
        if not (0 <= int(row) < len(self._breakpoint_rows)):
            return None
        return self._breakpoint_rows[int(row)]

    def _refresh_breakpoint_quick_editor(self) -> None:
        if not hasattr(self, "breakpoint_editor_label"):
            return
        breakpoint = self._selected_breakpoint()
        enabled = breakpoint is not None
        self._breakpoint_quick_syncing = True
        if breakpoint is None:
            self.breakpoint_editor_label.setText("未选择断点")
            self.breakpoint_editor_enabled.setChecked(False)
            self.breakpoint_editor_enabled.setText("启用")
            self.breakpoint_editor_condition.setText("")
        else:
            self.breakpoint_editor_label.setText(f"{breakpoint.path.name}:{breakpoint.line}")
            self.breakpoint_editor_enabled.setChecked(breakpoint.enabled)
            self.breakpoint_editor_enabled.setText("启用" if breakpoint.enabled else "停用")
            self.breakpoint_editor_condition.setText(breakpoint.condition)
        self.breakpoint_editor_enabled.setEnabled(enabled)
        self.breakpoint_editor_condition.setEnabled(enabled)
        self.breakpoint_editor_clear.setEnabled(enabled)
        self.breakpoint_editor_delete.setEnabled(enabled)
        self._breakpoint_quick_syncing = False

    def _on_breakpoint_quick_enabled_toggled(self, checked: bool) -> None:
        self.breakpoint_editor_enabled.setText("启用" if checked else "停用")
        if self._breakpoint_quick_syncing:
            return
        breakpoint = self._selected_breakpoint()
        if breakpoint is None:
            return
        try:
            self._breakpoints.set_enabled(breakpoint.path, breakpoint.line, bool(checked))
        except KeyError:
            return
        self._refresh_breakpoint_views()

    def _apply_breakpoint_quick_condition(self) -> None:
        if self._breakpoint_quick_syncing:
            return
        breakpoint = self._selected_breakpoint()
        if breakpoint is None:
            return
        try:
            self._breakpoints.set_condition(breakpoint.path, breakpoint.line, self.breakpoint_editor_condition.text().strip())
        except KeyError:
            return
        self._refresh_breakpoint_views()

    def _clear_breakpoint_quick_condition(self) -> None:
        if not self.breakpoint_editor_condition.isEnabled():
            return
        self.breakpoint_editor_condition.setText("")
        self._apply_breakpoint_quick_condition()

    def _remove_selected_breakpoint(self) -> None:
        breakpoint = self._selected_breakpoint()
        if breakpoint is None:
            return
        self._remove_breakpoint(breakpoint.path, breakpoint.line)

    def _scroll_editor_to_line(self, line: int) -> None:
        block = self.editor.document().findBlockByNumber(max(0, int(line) - 1))
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        self.editor.setTextCursor(cursor)
        self.editor.centerCursor()

    def _select_source_tree_path(self, path: Path) -> None:
        target = str(path.resolve()).lower()
        found = self._find_source_item(self.source_tree.invisibleRootItem(), target)
        if found is None:
            return
        self.source_tree.blockSignals(True)
        self.source_tree.setCurrentItem(found)
        found.setSelected(True)
        parent = found.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        self.source_tree.blockSignals(False)

    def _find_source_item(self, parent: QTreeWidgetItem, target: str) -> QTreeWidgetItem | None:
        for index in range(parent.childCount()):
            child = parent.child(index)
            path_text = child.data(0, ROLE_PATH)
            if path_text and str(Path(path_text).resolve()).lower() == target:
                return child
            found = self._find_source_item(child, target)
            if found is not None:
                return found
        return None

    def _set_table_item(
        self,
        row: int,
        column: int,
        text: str,
        *,
        editable: bool = False,
        checkable: bool = False,
        checked: bool = False,
        tooltip: str = "",
    ) -> None:
        item = QTableWidgetItem(text)
        flags = item.flags()
        if not editable:
            flags &= ~Qt.ItemIsEditable
        if checkable:
            flags |= Qt.ItemIsUserCheckable
            item.setCheckState(Qt.Checked if checked else Qt.Unchecked)
            item.setTextAlignment(Qt.AlignCenter)
        item.setFlags(flags)
        if tooltip:
            item.setToolTip(tooltip)
        self.breakpoint_table.setItem(row, column, item)

    def _breakpoint_verify_label(self, verified: bool, message: str = "") -> str:
        if verified:
            return "已验证"
        if message:
            return "未验证"
        return "待验证"

    def _breakpoint_verify_tooltip(self, verified: bool, message: str = "") -> str:
        label = self._breakpoint_verify_label(verified, message)
        if message:
            return f"{label}: {message}"
        if verified:
            return "已由调试后端确认"
        return "等待未来 Keil 回读验证"

    def _refresh_diagnostics_table(self) -> None:
        if not hasattr(self, "diagnostics_table"):
            return
        backend_items = self._diagnostics or (
            ("Keil 根目录", "等待发现"),
            ("UVSOCK", "等待发现"),
            ("uVision", "未检测"),
        )
        items = self._source_diagnostic_rows() + tuple(backend_items)
        self.diagnostics_table.setRowCount(len(items))
        for row, (key, value) in enumerate(items):
            self._set_diagnostics_item(row, 0, key)
            self._set_diagnostics_item(row, 1, value)

    def _set_diagnostics_item(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setToolTip(text)
        self.diagnostics_table.setItem(row, column, item)

    def _refresh_variable_preset_table(self) -> None:
        if not hasattr(self, "variable_preset_table"):
            return
        self.variable_preset_table.setRowCount(len(self._variable_presets))
        for row, (expression, label, value_type, default_value, purpose, write_allowed) in enumerate(self._variable_presets):
            tooltip = "\n".join(
                part
                for part in (
                    f"表达式: {expression}",
                    f"名称: {label}",
                    f"类型: {value_type}",
                    f"默认: {default_value or '--'}",
                    purpose,
                    "可写入" if write_allowed else "推荐示波/只读",
                )
                if part
            )
            self._set_variable_preset_item(row, 0, expression, tooltip=tooltip)
            self._set_variable_preset_item(row, 1, default_value or "--", tooltip=tooltip)
            self._set_variable_preset_item(row, 2, value_type or "--", tooltip=tooltip)
            self._set_variable_preset_item(row, 3, label or purpose or "--", tooltip=tooltip)
            for column in range(4):
                item = self.variable_preset_table.item(row, column)
                if item is not None:
                    item.setData(Qt.UserRole, (expression, default_value, write_allowed))
                    if not write_allowed:
                        item.setForeground(QColor("#8290a3"))
        has_write = any(item[-1] for item in self._variable_presets)
        has_watch = bool(self._variable_presets)
        self.variable_preset_write_button.setEnabled(has_write)
        self.variable_preset_write_button.setToolTip(
            "用选中的可写预设打开 Keil 写变量确认流程" if has_write else "当前工程没有可写变量预设"
        )
        self.variable_preset_watch_button.setEnabled(has_watch)
        self.variable_preset_watch_button.setToolTip(
            "把选中的变量加入 Keil Watch 示波列表" if has_watch else "当前工程没有可示波变量预设"
        )

    def _set_variable_preset_item(self, row: int, column: int, text: str, *, tooltip: str = "") -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        if tooltip:
            item.setToolTip(tooltip)
        self.variable_preset_table.setItem(row, column, item)

    def _on_variable_preset_double_clicked(self, row: int, _column: int) -> None:
        if 0 <= row < len(self._variable_presets) and self._variable_presets[row][-1]:
            self._emit_variable_preset_row(row)
        else:
            self._emit_variable_watch_preset_row(row)

    def _emit_selected_variable_preset(self) -> None:
        row = self.variable_preset_table.currentRow() if hasattr(self, "variable_preset_table") else -1
        if row < 0 and self.variable_preset_table.rowCount() > 0:
            row = 0
        self._emit_variable_preset_row(row)

    def _emit_selected_variable_watch_preset(self) -> None:
        row = self.variable_preset_table.currentRow() if hasattr(self, "variable_preset_table") else -1
        if row < 0 and self.variable_preset_table.rowCount() > 0:
            row = 0
        self._emit_variable_watch_preset_row(row)

    def _emit_variable_preset_row(self, row: int) -> None:
        if row < 0 or row >= len(self._variable_presets):
            return
        expression, _label, _value_type, default_value, _purpose, write_allowed = self._variable_presets[row]
        if not write_allowed:
            return
        self.variablePresetWriteRequested.emit(expression, default_value)

    def _emit_variable_watch_preset_row(self, row: int) -> None:
        if row < 0 or row >= len(self._variable_presets):
            return
        expression, label, value_type, _default_value, _purpose, _write_allowed = self._variable_presets[row]
        self.variablePresetWatchRequested.emit(expression, label, value_type)

    def _refresh_source_provider_summary(self) -> None:
        if not hasattr(self, "source_provider_state_label"):
            return
        manifest = self._source_manifest
        if manifest is None:
            values = ("来源 --", "0 文件", "路径 --")
            tooltip = "尚未加载源码清单"
        else:
            diagnostics = dict(manifest.diagnostics)
            missing = diagnostics.get("缺失", "")
            if missing == "":
                missing_count = sum(1 for entry in manifest.entries if not entry.exists)
                missing = str(missing_count) if missing_count else ""
            provider_label = self._source_provider_label(manifest.provider)
            missing_text = f"缺失 {missing}" if missing and missing != "0" else "路径正常" if manifest.source_count else "路径 --"
            values = (
                provider_label,
                f"{manifest.source_count} 文件",
                missing_text,
            )
            tooltip_lines = [
                f"名称: {manifest.name}",
                f"provider: {manifest.provider}",
                f"root: {manifest.root or '--'}",
                f"project: {manifest.project_path or '--'}",
            ]
            if manifest.diagnostics:
                tooltip_lines.append("诊断:")
                tooltip_lines.extend(f"- {key}: {value}" for key, value in manifest.diagnostics)
            mapping_hints = source_manifest_missing_path_hints(manifest)
            if mapping_hints:
                tooltip_lines.append("映射提示:")
                for hint in mapping_hints:
                    examples = ", ".join(hint.raw_examples[:2]) if hint.raw_examples else "--"
                    tooltip_lines.append(f"- {hint.missing_dir}: {hint.count} 个缺失，示例 {examples}")
            tooltip = "\n".join(tooltip_lines)
        labels = (
            self.source_provider_state_label,
            self.source_provider_count_label,
            self.source_provider_missing_label,
        )
        for label, value in zip(labels, values):
            label.setText(value)
            label.setToolTip(tooltip)
        if hasattr(self, "source_provider_remap_button"):
            has_hints = bool(manifest is not None and source_manifest_missing_path_hints(manifest))
            self.source_provider_remap_button.setEnabled(has_hints)
            self.source_provider_remap_button.setToolTip(
                "把缺失源码目录映射到本地源码根" if has_hints else "当前没有可映射的缺失源码"
            )

    def _source_diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        manifest = self._source_manifest
        if manifest is None:
            return (("源码来源", "未加载"),)
        diagnostics = dict(manifest.diagnostics)
        rows: list[tuple[str, str]] = [
            ("源码来源", self._source_provider_label(manifest.provider).replace("来源 ", "")),
            ("源码文件", str(manifest.source_count)),
        ]
        for key in ("缺失", "重复", "过滤", "截断"):
            value = diagnostics.get(key)
            if value not in (None, ""):
                rows.append((f"源码{key}", str(value)))
        for key in ("重映射重放", "重映射", "重映射命中", "重映射跳过"):
            value = diagnostics.get(key)
            if value not in (None, ""):
                rows.append((key, str(value)))
        mapping_hints = source_manifest_missing_path_hints(manifest)
        if mapping_hints:
            hint = mapping_hints[0]
            rows.append(("映射提示", f"{hint.count} 个缺失位于 {hint.missing_dir}"))
            if hint.raw_examples:
                rows.append(("映射示例", ", ".join(hint.raw_examples[:3])))
        if manifest.root is not None:
            rows.append(("源码根", str(manifest.root)))
        return tuple(rows)

    def _source_provider_label(self, provider: str) -> str:
        labels = {
            "keil": "Keil 工程",
            "elf_dwarf": "ELF/DWARF",
            "compile_commands": "编译数据库",
            "compile_commands_missing": "编译数据库",
            "gdb_info_sources": "GDB 源码表",
            "gdb_text_pending": "GDB 文本",
            "manual_roots": "源码根",
            "manual_roots_missing": "源码根",
            "elf_dwarf_pending": "ELF/DWARF",
        }
        provider_text = str(provider)
        if provider_text.endswith("_roots_preview"):
            text = "源码根预览"
        elif provider_text.endswith("_preview"):
            text = "复用预览"
        else:
            text = labels.get(provider_text, provider_text.replace("_", " "))
        return f"来源 {text}"

    def _refresh_command_plan_preview(self) -> None:
        if not hasattr(self, "plan_focus_label"):
            return
        self._plan_rows = self._session.command_plans()
        plan = self._focused_command_plan()
        transaction = self._focused_command_transaction(plan)
        tooltip = self._all_plan_tooltip()
        if transaction is not None:
            tooltip = tooltip + "\n\n" + self._transaction_tooltip(transaction)
        sync_transaction = self._command_transaction_by_key("sync_breakpoints")
        if sync_transaction is not None:
            tooltip = tooltip + "\n\n" + self._transaction_brief(sync_transaction)
        if plan is None:
            self.plan_focus_label.setText("等待状态")
            self.plan_state_label.setText("等待条件")
            self.plan_risk_label.setText("信息")
            self.plan_guard_label.setText("所有真实调试动作仍处于预览保护")
        else:
            self.plan_focus_label.setText(plan.title)
            self.plan_state_label.setText(plan.status)
            self.plan_risk_label.setText(self._risk_label(plan))
            if transaction is not None:
                guard_text = self._transaction_guard_text(transaction)
            elif plan.execution_enabled:
                guard_text = "可执行"
            elif plan.preconditions_met:
                guard_text = "条件满足，等待显式执行"
            else:
                guard_text = plan.disabled_reason or "等待后端条件"
            self.plan_guard_label.setText(guard_text)
        for widget in (
            self.plan_focus_label,
            self.plan_state_label,
            self.plan_risk_label,
            self.plan_guard_label,
        ):
            widget.setToolTip(tooltip)
        self._repolish(self.plan_state_label, self.plan_risk_label)
        self._refresh_command_history_preview()

    def _refresh_command_history_preview(self) -> None:
        if not hasattr(self, "plan_history_label"):
            return
        count = len(self._command_history_entries)
        self.plan_history_label.setText(f"历史 {count}")
        self.plan_history_label.setToolTip(self._history_tooltip())
        self._repolish(self.plan_history_label)

    def _focused_command_plan(self) -> DebugCommandPlan | None:
        priority = (
            "attach",
            "halt",
            "run",
            "step",
            "run_to_cursor",
            "sync_breakpoints",
            "write_variables",
            "disconnect",
            "discover",
        )
        ready = {plan.key: plan for plan in self._plan_rows if plan.preconditions_met}
        for key in priority:
            if key in ready:
                return ready[key]
        return self._plan_rows[0] if self._plan_rows else None

    def _focused_command_transaction(self, plan: DebugCommandPlan | None) -> KeilCommandTransaction | DebugCommandTransaction | None:
        if plan is None:
            return None
        for transaction in self._command_transactions:
            if transaction.kind.value == plan.key:
                return transaction
        return None

    def _command_transaction_by_key(self, key: str) -> KeilCommandTransaction | DebugCommandTransaction | None:
        for transaction in self._command_transactions:
            if transaction.kind.value == key:
                return transaction
        return None

    def _all_plan_tooltip(self) -> str:
        if not self._plan_rows:
            return "等待调试状态"
        return "\n\n".join(self._plan_tooltip(plan) for plan in self._plan_rows)

    def _plan_tooltip(self, plan: DebugCommandPlan) -> str:
        sections = [
            plan.intent,
            f"状态: {plan.status}",
        ]
        if plan.disabled_reason:
            sections.append(f"限制: {plan.disabled_reason}")
        if plan.requirements:
            sections.append("条件: " + "；".join(plan.requirements))
        if plan.safety_notes:
            sections.append("安全: " + "；".join(plan.safety_notes))
        if plan.preview_steps:
            sections.append("步骤: " + " -> ".join(plan.preview_steps))
        return "\n".join(sections)

    def _risk_label(self, plan: DebugCommandPlan) -> str:
        labels = {
            "info": "信息",
            "low": "低",
            "medium": "中",
            "high": "高",
        }
        return labels.get(plan.risk.value, plan.risk.value)

    def _transaction_guard_text(self, transaction: KeilCommandTransaction | DebugCommandTransaction) -> str:
        if transaction.execution_enabled:
            return f"执行: {transaction.title} · 审计: 待记录"
        if transaction.preconditions_met:
            return f"干跑: {transaction.title} · 审计: 未执行"
        blocked = transaction.blocked_reasons[0] if transaction.blocked_reasons else "等待条件"
        return f"干跑: {transaction.title} · 审计: 已阻止 · {blocked}"

    def _transaction_tooltip(self, transaction: KeilCommandTransaction | DebugCommandTransaction) -> str:
        guard_lines = [
            f"- {guard.label}: {guard.state.value} {guard.detail}".rstrip()
            for guard in transaction.guards
        ]
        command_lines = [f"- {line}" for line in transaction.command_preview]
        sections = [
            f"交易 ID: {transaction.transaction_id}",
            f"动作: {transaction.title}",
            f"模式: {'干跑' if transaction.dry_run else '执行'}",
            f"端口: {getattr(transaction, 'port', None) if getattr(transaction, 'port', None) is not None else '--'}",
            f"工程: {transaction.project_path or '--'}",
            f"Target: {transaction.target_name or '--'}",
            "未来命令:",
            *command_lines,
        ]
        breakpoint_diff_summary = getattr(transaction, "breakpoint_diff_summary", None)
        if breakpoint_diff_summary is not None:
            diff = breakpoint_diff_summary.to_record()
            sections.extend(
                [
                    "断点差分:",
                    f"- 快照: {'完成' if diff.get('snapshot_complete') else '等待'}",
                    (
                        "- 统计: "
                        f"add={diff.get('add_count', 0)} remove={diff.get('remove_count', 0)} "
                        f"enable={diff.get('enable_count', 0)} disable={diff.get('disable_count', 0)} "
                        f"update_condition={diff.get('update_condition_count', 0)} noop={diff.get('noop_count', 0)}"
                    ),
                    (
                        "- 本地验证: "
                        f"已验证={diff.get('verified_count', 0)} 未验证={diff.get('unverified_count', 0)} "
                        f"待验证={diff.get('pending_verify_count', 0)}"
                    ),
                ]
            )
        if transaction.backend_snapshot_id:
            snapshot = transaction.backend_snapshot or {}
            sections.extend(
                [
                    "后端快照:",
                    f"- ID: {transaction.backend_snapshot_id}",
                    (
                        "- 状态: "
                        f"{snapshot.get('state', '--')} · "
                        f"连接={'是' if snapshot.get('connection_established') else '否'} · "
                        f"只读={'是' if snapshot.get('read_only', True) else '否'}"
                    ),
                    (
                        "- 远端断点: "
                        f"{snapshot.get('remote_breakpoint_snapshot_id', '--')} · "
                        f"{'完成' if snapshot.get('remote_breakpoint_complete') else '等待'}"
                    ),
                ]
            )
        sections.extend([
            "Guard:",
            *guard_lines,
            f"审计: {transaction.audit_summary}",
        ])
        return "\n".join(sections)

    def _transaction_brief(self, transaction: KeilCommandTransaction | DebugCommandTransaction) -> str:
        if transaction.command_preview:
            preview = transaction.command_preview[0]
        else:
            preview = transaction.audit_summary
        return f"断点预览: {transaction.title} · {preview}"

    def _history_tooltip(self) -> str:
        if not self._command_history_entries:
            return "暂无干跑命令历史"
        lines = ["最近干跑命令历史:"]
        for entry in self._command_history_entries[:5]:
            state = "已阻止" if entry.blocked_reasons else "未执行"
            repeat = f" x{entry.seen_count}" if entry.seen_count > 1 else ""
            blocked = f" · {entry.blocked_reasons[0]}" if entry.blocked_reasons else ""
            backend = f" · {entry.backend}" if getattr(entry, "backend", "") else ""
            diff = ""
            if entry.breakpoint_diff_summary:
                summary = entry.breakpoint_diff_summary
                diff = (
                    f" · 断点差分 add={summary.get('add_count', 0)}"
                    f" remove={summary.get('remove_count', 0)}"
                    f" enable={summary.get('enable_count', 0)}"
                    f" disable={summary.get('disable_count', 0)}"
                    f" update_condition={summary.get('update_condition_count', 0)}"
                    f" noop={summary.get('noop_count', 0)}"
                    f" verified={summary.get('verified_count', 0)}"
                    f" unverified={summary.get('unverified_count', 0)}"
                    f" pending={summary.get('pending_verify_count', 0)}"
                )
            snapshot = f" · snapshot={entry.backend_snapshot_id}" if entry.backend_snapshot_id else ""
            lines.append(
                f"- #{entry.sequence} {entry.title}{backend} · {state}{repeat} · {entry.last_seen_at}{blocked}{diff}{snapshot}"
            )
        return "\n".join(lines)

    def _refresh_summary(self) -> None:
        project, source_count, breakpoints = self.hero_summary()
        status = self._session.status
        self.summary_label.setText(f"{project} · {source_count} · {breakpoints} · {status.label}")
        self._refresh_breakpoint_table()
        self.summaryChanged.emit()

    def _apply_debug_status(self, status: DebugWorkbenchStatus) -> None:
        self.status_text.setText(f"{status.label} · {status.detail}")
        self.status_dot.setProperty("state", status.state.value)
        self._repolish(self.status_dot)
        actions = {action.key: action for action in self._session.actions()}
        for key, button in self._action_buttons.items():
            action = actions.get(key, DebugAction(key, button.text(), False, "等待后端状态"))
            explicit_keil_write = (
                key == "write_variables"
                and self._backend_controls_ready
                and status.backend.value == "keil"
                and status.state.value in {"keil_attached", "paused", "running"}
            )
            explicit_keil_breakpoint_sync = (
                key == "sync_breakpoints"
                and self._backend_controls_ready
                and status.backend.value == "keil"
                and status.state.value in {"keil_attached", "paused", "running"}
            )
            explicit_keil_profile_action = (
                key in {"build_project", "launch_uvsock", "auto_debug"}
                and self._backend_controls_ready
                and status.backend.value == "keil"
                and status.project_path is not None
            )
            explicit_keil_runtime_action = (
                key == "halt"
                and self._backend_controls_ready
                and status.backend.value == "keil"
                and status.state.value == "running"
            ) or (
                key == "run"
                and self._backend_controls_ready
                and status.backend.value == "keil"
                and status.state.value == "paused"
            ) or (
                key == "reset"
                and self._backend_controls_ready
                and status.backend.value == "keil"
                and status.state.value in {"keil_attached", "paused", "running"}
            ) or (
                key in {"step", "step_over", "run_to_cursor"}
                and self._backend_controls_ready
                and status.backend.value == "keil"
                and status.state.value == "paused"
            )
            enabled = bool(
                (
                    action.enabled
                    or explicit_keil_write
                    or explicit_keil_breakpoint_sync
                    or explicit_keil_profile_action
                    or explicit_keil_runtime_action
                )
                and self._backend_controls_ready
            )
            button.setEnabled(enabled)
            if enabled:
                if explicit_keil_runtime_action:
                    if key == "run_to_cursor":
                        button.setToolTip("显式通过 Keil/UVSOCK 运行到当前光标源码行，执行前会再次确认")
                    else:
                        button.setToolTip("显式通过 Keil/UVSOCK 改变目标运行状态，执行前会再次确认")
                elif explicit_keil_breakpoint_sync:
                    button.setToolTip("显式通过 Keil/UVSOCK 同步本地断点，执行前会再次确认")
                elif explicit_keil_profile_action:
                    button.setToolTip("使用当前 Keil 工程/Target 的调试档案执行显式动作")
                elif explicit_keil_write and not action.enabled:
                    button.setToolTip("显式通过 Keil/UVSOCK 写变量，写入前会再次确认")
                else:
                    button.setToolTip(action.title)
            elif action.enabled and not self._backend_controls_ready:
                button.setToolTip("等待调试后端控制器接入")
            else:
                button.setToolTip(action.reason or "当前状态不可用")
        self._refresh_command_plan_preview()

    def _repolish(self, *widgets: QWidget) -> None:
        for widget in widgets:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def _marker_text(self, decorations: tuple[LineDecoration, ...]) -> str:
        counts: dict[str, int] = {}
        for decoration in decorations:
            counts[decoration.kind] = counts.get(decoration.kind, 0) + 1
        parts = []
        if counts.get("pc"):
            pc_decorations = [decoration for decoration in decorations if decoration.kind == "pc"]
            if any(decoration.verified for decoration in pc_decorations):
                parts.append("PC 已回读")
            elif any(decoration.message for decoration in pc_decorations):
                parts.append("PC 未验证")
            else:
                parts.append("PC")
        if counts.get("run"):
            parts.append("运行行")
        if counts.get("search"):
            if self._search_matches and self._search_index >= 0:
                parts.append(f"搜索 {self._search_index + 1}/{len(self._search_matches)}")
            else:
                parts.append(f"{counts['search']} 个搜索命中")
        if counts.get("breakpoint"):
            breakpoint_decorations = [decoration for decoration in decorations if decoration.kind == "breakpoint"]
            enabled_count = sum(1 for decoration in breakpoint_decorations if decoration.enabled)
            disabled_count = len(breakpoint_decorations) - enabled_count
            conditional_count = sum(1 for decoration in breakpoint_decorations if decoration.label)
            verified_count = sum(1 for decoration in breakpoint_decorations if decoration.verified)
            unverified_count = sum(1 for decoration in breakpoint_decorations if decoration.message and not decoration.verified)
            pending_count = len(breakpoint_decorations) - verified_count - unverified_count
            detail = f"{len(breakpoint_decorations)} 个断点"
            subparts = []
            if enabled_count:
                subparts.append(f"启用 {enabled_count}")
            if disabled_count:
                subparts.append(f"停用 {disabled_count}")
            if conditional_count:
                subparts.append(f"条件 {conditional_count}")
            if verified_count:
                subparts.append(f"已验证 {verified_count}")
            if unverified_count:
                subparts.append(f"未验证 {unverified_count}")
            if pending_count:
                subparts.append(f"待验证 {pending_count}")
            if subparts:
                detail += f"（{' / '.join(subparts)}）"
            parts.append(detail)
        return " / ".join(parts) if parts else "未连接运行时"

    def _apply_style(self) -> None:
        self.setStyleSheet("""
            QWidget#debugWorkbenchTab {
                background: #f6f8fb;
                color: #0f172a;
                font-family: "Microsoft YaHei UI", "Segoe UI Variable Text", "Segoe UI", sans-serif;
                font-size: 10pt;
            }
            QFrame#debugCard {
                background: #ffffff;
                border: 1px solid #dce6f0;
                border-radius: 8px;
            }
            QScrollArea#debugNavigationScroll {
                background: transparent;
                border: none;
            }
            QFrame#debugNavigationContent {
                background: transparent;
                border: none;
            }
            QScrollArea#debugNavigationScroll QScrollBar:vertical {
                background: transparent;
                border: none;
                width: 7px;
                margin: 4px 1px 4px 0;
            }
            QScrollArea#debugNavigationScroll QScrollBar::handle:vertical {
                background: #cbd8e6;
                border-radius: 3px;
                min-height: 28px;
            }
            QScrollArea#debugNavigationScroll QScrollBar::add-line:vertical,
            QScrollArea#debugNavigationScroll QScrollBar::sub-line:vertical {
                height: 0;
            }
            QLabel#debugSectionTitle {
                color: #111827;
                font-size: 11pt;
                font-weight: 600;
            }
            QLabel#debugHint, QLabel#debugSummary {
                color: #64748b;
                font-size: 9pt;
            }
            QLabel#debugStatusDot {
                color: #2563eb;
                font-size: 14px;
            }
            QLabel#debugStatusDot[state="running"] {
                color: #16a34a;
            }
            QLabel#debugStatusDot[state="paused"] {
                color: #f97316;
            }
            QLabel#debugStatusDot[state="error"] {
                color: #ef4444;
            }
            QFrame#debugPlanStrip {
                background: #f8fbff;
                border: 1px solid #d2deea;
                border-radius: 7px;
            }
            QLabel#debugPlanTitle {
                color: #64748b;
                font-size: 9pt;
                font-weight: 600;
            }
            QLabel#debugPlanFocus {
                color: #0f172a;
                font-size: 9pt;
                font-weight: 600;
                padding: 2px 7px;
                background: #eef6ff;
                border: 1px solid #c8dbf3;
                border-radius: 5px;
            }
            QLabel#debugPlanState {
                color: #1d4ed8;
                font-size: 9pt;
                font-weight: 600;
                padding: 2px 7px;
                background: #eff6ff;
                border: 1px solid #bfdbfe;
                border-radius: 5px;
            }
            QLabel#debugPlanRisk {
                color: #b45309;
                font-size: 9pt;
                font-weight: 600;
                padding: 2px 7px;
                background: #fffbeb;
                border: 1px solid #fde68a;
                border-radius: 5px;
            }
            QLabel#debugPlanGuard {
                color: #64748b;
                font-size: 9pt;
            }
            QLabel#debugPlanHistory {
                color: #475569;
                font-size: 9pt;
                font-weight: 600;
                padding: 2px 7px;
                background: #f8fafc;
                border: 1px solid #d2deea;
                border-radius: 5px;
            }
            QFrame#debugSourceStrip {
                background: #f8fbff;
                border: 1px solid #d2deea;
                border-radius: 7px;
            }
            QLabel#debugSourceChip {
                color: #475569;
                font-size: 9pt;
                font-weight: 600;
                padding: 2px 6px;
                background: #ffffff;
                border: 1px solid #dce6f0;
                border-radius: 5px;
            }
            QFrame#debugBreakpointEditor {
                background: #f8fbff;
                border: 1px solid #d2deea;
                border-radius: 7px;
            }
            QLabel#debugBreakpointEditorLabel {
                color: #475569;
                font-size: 9pt;
                font-weight: 600;
                min-width: 86px;
            }
            QComboBox#debugCombo, QLineEdit#debugSearch {
                background: #f8fbff;
                border: 1px solid #d2deea;
                border-radius: 7px;
                padding: 7px 9px;
                selection-background-color: #2563eb;
                selection-color: white;
            }
            QComboBox#debugCombo:hover, QLineEdit#debugSearch:hover {
                border-color: #9fb8d4;
            }
            QComboBox#debugCombo:focus, QLineEdit#debugSearch:focus {
                border-color: #2563eb;
                background: #ffffff;
            }
            QComboBox#debugSourceProviderCombo {
                background: #f8fbff;
                border: 1px solid #d2deea;
                border-radius: 6px;
                padding: 4px 7px;
                selection-background-color: #2563eb;
                selection-color: white;
                font-size: 9pt;
            }
            QComboBox#debugSourceProviderCombo:hover {
                border-color: #9fb8d4;
            }
            QComboBox#debugSourceProviderCombo:focus {
                border-color: #2563eb;
                background: #ffffff;
            }
            QLineEdit#debugBreakpointConditionEdit {
                background: #ffffff;
                border: 1px solid #d2deea;
                border-radius: 6px;
                padding: 4px 7px;
                color: #0f172a;
                selection-background-color: #2563eb;
                selection-color: white;
            }
            QLineEdit#debugBreakpointConditionEdit:focus {
                border-color: #2563eb;
            }
            QLineEdit#debugBreakpointConditionEdit:disabled {
                background: #f3f6fa;
                color: #94a3b8;
            }
            QPushButton#debugPrimaryButton {
                min-height: 34px;
                border-radius: 7px;
                padding: 6px 12px;
                font-weight: 560;
                background: #2563eb;
                border: 1px solid #2563eb;
                color: #ffffff;
            }
            QPushButton#debugPrimaryButton:hover {
                background: #1d4ed8;
                border-color: #1d4ed8;
            }
            QPushButton#debugActionButton {
                min-height: 28px;
                border-radius: 7px;
                padding: 4px 9px;
                font-weight: 560;
                background: #f8fbff;
                border: 1px solid #d2deea;
                color: #0f172a;
            }
            QPushButton#debugActionButton:hover {
                background: #eef6ff;
                border-color: #9fb8d4;
            }
            QPushButton#debugActionButton:disabled {
                background: #f3f6fa;
                border-color: #dce6f0;
                color: #9aa6b4;
            }
            QFrame#debugActionSeparator {
                border: none;
                border-left: 1px solid #d8e4f1;
                margin: 5px 3px;
                background: transparent;
            }
            QPushButton#debugSearchNavButton {
                min-height: 34px;
                min-width: 58px;
                border-radius: 7px;
                padding: 4px 8px;
                font-weight: 560;
                background: #f8fbff;
                border: 1px solid #d2deea;
                color: #334155;
            }
            QPushButton#debugSearchNavButton:hover {
                background: #eef6ff;
                border-color: #9fb8d4;
                color: #1d4ed8;
            }
            QPushButton#debugSearchNavButton:disabled {
                background: #f3f6fa;
                border-color: #dce6f0;
                color: #a8b3c0;
            }
            QPushButton#debugMiniButton, QPushButton#debugMiniToggleButton {
                min-height: 24px;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 9pt;
                font-weight: 560;
                background: #ffffff;
                border: 1px solid #d2deea;
                color: #334155;
            }
            QPushButton#debugMiniButton:hover, QPushButton#debugMiniToggleButton:hover {
                background: #eef6ff;
                border-color: #9fb8d4;
                color: #1d4ed8;
            }
            QPushButton#debugMiniToggleButton:checked {
                background: #eff6ff;
                border-color: #93c5fd;
                color: #1d4ed8;
            }
            QPushButton#debugMiniButton:disabled, QPushButton#debugMiniToggleButton:disabled {
                background: #f3f6fa;
                border-color: #dce6f0;
                color: #a8b3c0;
            }
            QPushButton#debugMiniDangerButton {
                min-height: 24px;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 9pt;
                font-weight: 560;
                background: #fff5f5;
                border: 1px solid #fecaca;
                color: #b91c1c;
            }
            QPushButton#debugMiniDangerButton:hover {
                background: #fee2e2;
                border-color: #fca5a5;
                color: #991b1b;
            }
            QPushButton#debugMiniDangerButton:disabled {
                background: #f3f6fa;
                border-color: #dce6f0;
                color: #a8b3c0;
            }
            QPushButton#debugTableDangerButton {
                min-height: 22px;
                border-radius: 6px;
                padding: 2px 8px;
                font-size: 9pt;
                font-weight: 560;
                background: #fff5f5;
                border: 1px solid #fecaca;
                color: #b91c1c;
            }
            QPushButton#debugTableDangerButton:hover {
                background: #fee2e2;
                border-color: #fca5a5;
                color: #991b1b;
            }
            QTreeWidget#debugSourceTree, QTableWidget#debugBreakpointTable,
            QTableWidget#debugDiagnosticsTable, QTableWidget#debugVariablePresetTable,
            QPlainTextEdit#debugCodeEditor {
                background: #fbfdff;
                border: 1px solid #d2deea;
                border-radius: 7px;
                color: #0f172a;
                selection-background-color: #dbeafe;
                selection-color: #123c96;
            }
            QPlainTextEdit#debugCodeEditor {
                padding: 8px 10px;
                font-family: "Cascadia Code", "Consolas", "Microsoft YaHei UI", monospace;
                font-size: 10pt;
            }
            QTreeWidget#debugSourceTree::item {
                min-height: 26px;
                padding: 3px 5px;
                border-radius: 4px;
            }
            QTreeWidget#debugSourceTree::item:hover {
                background: #eef6ff;
                color: #1d4ed8;
            }
            QTreeWidget#debugSourceTree::item:selected {
                background: #dbeafe;
                color: #123c96;
                border-left: 3px solid #2563eb;
            }
            QTableWidget#debugBreakpointTable::item {
                padding: 3px 6px;
            }
            QTableWidget#debugDiagnosticsTable::item,
            QTableWidget#debugVariablePresetTable::item {
                padding: 3px 6px;
            }
            QHeaderView::section {
                background: #f8fafc;
                color: #64748b;
                border: none;
                border-bottom: 1px solid #dce6f0;
                padding: 6px 7px;
                font-weight: 600;
                font-size: 9pt;
            }
            QSplitter#debugWorkbenchSplitter::handle {
                background: transparent;
                border-left: 1px solid #c7d5e4;
                width: 4px;
            }
        """)
