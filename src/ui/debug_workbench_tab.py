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
    LineDecoration,
    SourceTreeNode,
    DebugWorkbenchSession,
    DebugWorkbenchStatus,
    line_decorations,
    load_code_document,
    source_entries_from_keil_project,
    source_tree_from_entries,
)
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

class DebugWorkbenchTab(QWidget):
    """Modern Keil project source browser, disconnected from runtime control."""

    summaryChanged = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._project: KeilProject | None = None
        self._source_tree: SourceTreeNode | None = None
        self._breakpoints = BreakpointStore()
        self._current_document: CodeDocument | None = None
        self._current_pc_line: int | None = None
        self._run_line: int | None = None
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
        self._refresh_breakpoint_table()
        self._refresh_decorations()
        self._refresh_summary()

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
        self.target_combo.currentTextChanged.connect(self._rebuild_source_tree)
        layout.addWidget(self.target_combo, 0, 2)

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("debugSearch")
        self.search_edit.setPlaceholderText("搜索当前文件")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._refresh_decorations)
        layout.addWidget(self.search_edit, 0, 3)

        self.summary_label = QLabel("未打开工程")
        self.summary_label.setObjectName("debugSummary")
        layout.addWidget(self.summary_label, 1, 0, 1, 2)

        actions = self._build_action_bar()
        layout.addLayout(actions, 1, 2, 1, 2)
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
            self._action_buttons[key] = button
            layout.addWidget(button)
        layout.addStretch(1)
        return layout

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

        bp_title = QLabel("本地断点")
        bp_title.setObjectName("debugSectionTitle")
        layout.addWidget(bp_title)

        self.breakpoint_table = QTableWidget()
        self.breakpoint_table.setObjectName("debugBreakpointTable")
        self.breakpoint_table.setColumnCount(3)
        self.breakpoint_table.setHorizontalHeaderLabels(["状态", "文件", "行"])
        self.breakpoint_table.horizontalHeader().setStretchLastSection(False)
        self.breakpoint_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.breakpoint_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.breakpoint_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.breakpoint_table.verticalHeader().setVisible(False)
        self.breakpoint_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.breakpoint_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.breakpoint_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.breakpoint_table.setAlternatingRowColors(True)
        self.breakpoint_table.setMinimumHeight(118)
        layout.addWidget(self.breakpoint_table, 1)
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
        self._refresh_decorations()
        self._refresh_summary()

    def _toggle_breakpoint(self, line: int) -> None:
        if self._current_document is None:
            return
        self._breakpoints.toggle(self._current_document.path, line)
        self._refresh_breakpoint_table()
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
        self.editor.set_code_document(self._current_document, decorations)
        self.marker_label.setText(self._marker_text(decorations))

    def _refresh_breakpoint_table(self) -> None:
        breakpoints = self._breakpoints.all()
        self.breakpoint_table.setRowCount(len(breakpoints))
        for row, breakpoint in enumerate(breakpoints):
            self._set_table_item(row, 0, "启用" if breakpoint.enabled else "停用")
            self._set_table_item(row, 1, breakpoint.path.name)
            self._set_table_item(row, 2, str(breakpoint.line))

    def _set_table_item(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        self.breakpoint_table.setItem(row, column, item)

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
            QTreeWidget#debugSourceTree, QTableWidget#debugBreakpointTable, QPlainTextEdit#debugCodeEditor {
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
