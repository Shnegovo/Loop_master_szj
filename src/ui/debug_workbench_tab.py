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
    source_entries_from_keil_project,
    source_tree_from_entries,
)
from src.core.keil.commands import KeilCommandHistoryEntry, KeilCommandTransaction
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
            painter.setBrush(QColor("#2563eb"))
            painter.setPen(Qt.NoPen)
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
            enabled = all(decoration.enabled for decoration in decorations if decoration.kind == "breakpoint")
            painter.setPen(QPen(QColor("#dc2626"), 2))
            painter.setBrush(QColor("#ef4444") if enabled else QColor("#ffffff"))
            painter.drawEllipse(6, center_y - 6, 12, 12)
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

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project: KeilProject | None = None
        self._source_tree: SourceTreeNode | None = None
        self._breakpoints = BreakpointStore()
        self._current_document: CodeDocument | None = None
        self._current_pc_line: int | None = None
        self._run_line: int | None = None
        self._active_search_line: int | None = None
        self._search_matches = ()
        self._search_index = -1
        self._breakpoint_rows = ()
        self._diagnostics: tuple[tuple[str, str], ...] = ()
        self._plan_rows: tuple[DebugCommandPlan, ...] = ()
        self._command_transactions: tuple[KeilCommandTransaction, ...] = ()
        self._command_history_entries: tuple[KeilCommandHistoryEntry, ...] = ()
        self._breakpoint_table_syncing = False
        self._session = DebugWorkbenchSession()
        self._backend_controls_ready = False

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
        if self._project is None or self._project.default_target is None:
            return 0
        return len(self._project.default_target.source_files) + len(self._project.default_target.header_files)

    @property
    def breakpoint_count(self) -> int:
        return len(self._breakpoints.all())

    @property
    def current_document(self) -> CodeDocument | None:
        return self._current_document

    @property
    def debug_status(self) -> DebugWorkbenchStatus:
        return self._session.status

    def local_breakpoints(self) -> tuple:
        return self._breakpoints.all()

    def local_source_paths(self) -> tuple[Path, ...]:
        if self._source_tree is None:
            return ()
        paths: list[Path] = []
        for group in self._source_tree.children:
            for child in group.children:
                if child.path is not None:
                    paths.append(child.path)
        return tuple(paths)

    def hero_summary(self) -> tuple[str, str, str]:
        if self._project is None:
            return "未打开 Keil 工程", "只读预览", "0 个断点"
        return (
            self._project.path.name,
            f"{self.source_count} 个源码文件",
            f"{self.breakpoint_count} 个本地断点",
        )

    def load_project(self, path: str | Path) -> None:
        project = parse_keil_project(path)
        self.set_project(project)

    def set_project(self, project: KeilProject) -> None:
        self._project = project
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

    def set_runtime_markers(self, current_pc_line: int | None = None, run_line: int | None = None) -> None:
        self._current_pc_line = current_pc_line
        self._run_line = run_line
        self._refresh_decorations()

    def set_debug_status(self, status: DebugWorkbenchStatus, *, controls_ready: bool = False) -> None:
        self._session.apply_status(status)
        self._backend_controls_ready = bool(controls_ready)
        self._current_pc_line = status.current_pc_line
        self._run_line = status.run_line
        self._apply_debug_status(status)
        self._refresh_decorations()
        self._refresh_summary()

    def set_debug_controls_ready(self, ready: bool) -> None:
        self._backend_controls_ready = bool(ready)
        self._apply_debug_status(self._session.status)

    def set_backend_diagnostics(self, items: tuple[tuple[str, str], ...] | list[tuple[str, str]]) -> None:
        self._diagnostics = tuple((str(key), str(value)) for key, value in items)
        self._refresh_diagnostics_table()

    def set_command_transactions(
        self,
        transactions: tuple[KeilCommandTransaction, ...] | list[KeilCommandTransaction],
    ) -> None:
        self._command_transactions = tuple(transactions)
        self._refresh_command_plan_preview()

    def set_command_history_entries(
        self,
        entries: tuple[KeilCommandHistoryEntry, ...] | list[KeilCommandHistoryEntry],
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
        self._breakpoints.add(breakpoint_path, line, enabled=enabled, condition=condition)
        self._refresh_breakpoint_views()

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

        self.open_project_button = QPushButton("打开 Keil 工程")
        self.open_project_button.setObjectName("debugPrimaryButton")
        self.open_project_button.clicked.connect(self._choose_project)
        layout.addWidget(self.open_project_button, 0, 1)

        self.target_combo = PclComboBox()
        self.target_combo.setObjectName("debugCombo")
        self.target_combo.setMinimumWidth(180)
        self.target_combo.currentTextChanged.connect(self._on_target_changed)
        layout.addWidget(self.target_combo, 0, 2)

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
        layout.addLayout(search_box, 0, 3)

        self.summary_label = QLabel("未打开工程")
        self.summary_label.setObjectName("debugSummary")
        layout.addWidget(self.summary_label, 1, 0, 1, 2)

        actions = self._build_action_bar()
        layout.addLayout(actions, 1, 2, 1, 2)
        layout.addWidget(self._build_action_plan_strip(), 2, 0, 1, 4)
        layout.setColumnStretch(3, 1)
        return bar

    def _build_action_bar(self) -> QHBoxLayout:
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self._action_buttons: dict[str, QPushButton] = {}
        for key, title in (
            ("discover", "发现 Keil"),
            ("attach", "连接"),
            ("disconnect", "断开"),
            ("halt", "暂停"),
            ("run", "运行"),
        ):
            button = QPushButton(title)
            button.setObjectName("debugActionButton")
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumWidth(58)
            button.setEnabled(False)
            button.clicked.connect(lambda _checked=False, action_key=key: self.debugActionRequested.emit(action_key))
            self._action_buttons[key] = button
            layout.addWidget(button)
        layout.addStretch(1)
        return layout

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
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(9)

        title = QLabel("源码树")
        title.setObjectName("debugSectionTitle")
        layout.addWidget(title)

        self.source_tree = QTreeWidget()
        self.source_tree.setObjectName("debugSourceTree")
        self.source_tree.setHeaderHidden(True)
        self.source_tree.setIndentation(16)
        self.source_tree.setAnimated(True)
        self.source_tree.setUniformRowHeights(True)
        self.source_tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.source_tree.itemClicked.connect(self._on_source_item_clicked)
        layout.addWidget(self.source_tree, 3)

        diag_title = QLabel("后端诊断")
        diag_title.setObjectName("debugSectionTitle")
        layout.addWidget(diag_title)

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

        bp_title = QLabel("本地断点")
        bp_title.setObjectName("debugSectionTitle")
        layout.addWidget(bp_title)

        self.breakpoint_table = QTableWidget()
        self.breakpoint_table.setObjectName("debugBreakpointTable")
        self.breakpoint_table.setColumnCount(5)
        self.breakpoint_table.setHorizontalHeaderLabels(["启用", "文件", "行", "条件", "操作"])
        self.breakpoint_table.horizontalHeader().setStretchLastSection(False)
        self.breakpoint_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.breakpoint_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.breakpoint_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.breakpoint_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.breakpoint_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.breakpoint_table.verticalHeader().setVisible(False)
        self.breakpoint_table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed)
        self.breakpoint_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.breakpoint_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.breakpoint_table.setAlternatingRowColors(True)
        self.breakpoint_table.cellClicked.connect(self._on_breakpoint_table_clicked)
        self.breakpoint_table.itemChanged.connect(self._on_breakpoint_table_item_changed)
        self.breakpoint_table.setMinimumHeight(108)
        layout.addWidget(self.breakpoint_table, 1)
        self._refresh_diagnostics_table()
        return panel

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
        if self._project is None:
            placeholder = QTreeWidgetItem(["未打开工程"])
            placeholder.setDisabled(True)
            self.source_tree.addTopLevelItem(placeholder)
            return
        target_name = self.target_combo.currentData() or self.target_combo.currentText() or None
        if self._project is not None and target_name:
            self._session.set_project(self._project, str(target_name))
            self._apply_debug_status(self._session.status)
        entries = source_entries_from_keil_project(self._project, str(target_name) if target_name else None)
        self._source_tree = source_tree_from_entries(entries)
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

    def _on_target_changed(self) -> None:
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
            return
        self._current_document = document
        self.file_label.setText(f"{document.path.name}  ·  {document.line_count} 行")
        self._select_source_tree_path(document.path)
        self._active_search_line = None
        self._search_index = -1
        self._refresh_decorations()
        self._refresh_summary()

    def _toggle_breakpoint(self, line: int) -> None:
        if self._current_document is None:
            return
        self._breakpoints.toggle(self._current_document.path, line)
        self._refresh_breakpoint_views()

    def _refresh_breakpoint_views(self) -> None:
        self._refresh_decorations()
        self._refresh_summary()

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
        self._refresh_search_matches()
        if self._active_search_line is not None:
            decorations = tuple(decorations) + (
                LineDecoration(line=self._active_search_line, kind="search_active", label="当前搜索"),
            )
        self.editor.set_code_document(self._current_document, decorations)
        self.marker_label.setText(self._marker_text(decorations))

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
            remove_button = QPushButton("删除")
            remove_button.setObjectName("debugTableDangerButton")
            remove_button.setCursor(Qt.PointingHandCursor)
            remove_button.setToolTip("移除这个本地断点")
            remove_button.clicked.connect(
                lambda _checked=False, path=breakpoint.path, line=breakpoint.line: self._remove_breakpoint(path, line)
            )
            self.breakpoint_table.setCellWidget(row, 4, remove_button)
        self.breakpoint_table.blockSignals(False)
        self._breakpoint_table_syncing = False

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
        if int(column) in {0, 3, 4}:
            return
        breakpoint = self._breakpoint_rows[int(row)]
        self._load_source(breakpoint.path)
        self._scroll_editor_to_line(breakpoint.line)

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

    def _refresh_diagnostics_table(self) -> None:
        if not hasattr(self, "diagnostics_table"):
            return
        items = self._diagnostics or (
            ("Keil 根目录", "等待发现"),
            ("UVSOCK", "等待发现"),
            ("uVision", "未检测"),
        )
        self.diagnostics_table.setRowCount(len(items))
        for row, (key, value) in enumerate(items):
            self._set_diagnostics_item(row, 0, key)
            self._set_diagnostics_item(row, 1, value)

    def _set_diagnostics_item(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        item.setToolTip(text)
        self.diagnostics_table.setItem(row, column, item)

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
                guard_text = "条件满足，但仍等待 UVSOCK 烟测阶段"
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

    def _focused_command_transaction(self, plan: DebugCommandPlan | None) -> KeilCommandTransaction | None:
        if plan is None:
            return None
        for transaction in self._command_transactions:
            if transaction.kind.value == plan.key:
                return transaction
        return None

    def _command_transaction_by_key(self, key: str) -> KeilCommandTransaction | None:
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

    def _transaction_guard_text(self, transaction: KeilCommandTransaction) -> str:
        if transaction.execution_enabled:
            return f"执行: {transaction.title} · 审计: 待记录"
        if transaction.preconditions_met:
            return f"干跑: {transaction.title} · 审计: 未执行"
        blocked = transaction.blocked_reasons[0] if transaction.blocked_reasons else "等待条件"
        return f"干跑: {transaction.title} · 审计: 已阻止 · {blocked}"

    def _transaction_tooltip(self, transaction: KeilCommandTransaction) -> str:
        guard_lines = [
            f"- {guard.label}: {guard.state.value} {guard.detail}".rstrip()
            for guard in transaction.guards
        ]
        command_lines = [f"- {line}" for line in transaction.command_preview]
        sections = [
            f"交易 ID: {transaction.transaction_id}",
            f"动作: {transaction.title}",
            f"模式: {'干跑' if transaction.dry_run else '执行'}",
            f"端口: {transaction.port if transaction.port is not None else '--'}",
            f"工程: {transaction.project_path or '--'}",
            f"Target: {transaction.target_name or '--'}",
            "未来命令:",
            *command_lines,
        ]
        if transaction.breakpoint_diff_summary is not None:
            diff = transaction.breakpoint_diff_summary.to_record()
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
                ]
            )
        sections.extend([
            "Guard:",
            *guard_lines,
            f"审计: {transaction.audit_summary}",
        ])
        return "\n".join(sections)

    def _transaction_brief(self, transaction: KeilCommandTransaction) -> str:
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
                )
            lines.append(
                f"- #{entry.sequence} {entry.title} · {state}{repeat} · {entry.last_seen_at}{blocked}{diff}"
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
            enabled = bool(action.enabled and self._backend_controls_ready)
            button.setEnabled(enabled)
            if enabled:
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
            parts.append("PC")
        if counts.get("run"):
            parts.append("运行行")
        if counts.get("search"):
            if self._search_matches and self._search_index >= 0:
                parts.append(f"搜索 {self._search_index + 1}/{len(self._search_matches)}")
            else:
                parts.append(f"{counts['search']} 个搜索命中")
        if counts.get("breakpoint"):
            parts.append(f"{counts['breakpoint']} 个断点")
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
            QTableWidget#debugDiagnosticsTable, QPlainTextEdit#debugCodeEditor {
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
            QTableWidget#debugDiagnosticsTable::item {
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
