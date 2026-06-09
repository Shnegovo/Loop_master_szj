"""Cockpit-inspired visual theme helpers for the LoopMaster Qt UI."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QParallelAnimationGroup, QPoint, QPropertyAnimation, QRect, QTimer, Qt
from PySide6.QtGui import QColor, QPalette, QFont
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QListView,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


ACCENT = "#1d4ed8"
ACCENT_DARK = "#123c96"
ACCENT_SOFT = "#dbeafe"
TEAL = "#0ea5a5"
TEAL_SOFT = "#d7f3ef"
TEXT = "#0f172a"
MUTED = "#64748b"
SURFACE = "#ffffff"
WINDOW = "#f6f5f2"
BORDER = "#d5dde8"
SOFT_BLUE = "#eef6ff"


class PclComboPopupView(QListView):
    """List used by the custom combo popup."""

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() == Qt.Key_Escape:
            parent = self.parentWidget()
            if parent is not None:
                parent.hide()
            event.accept()
            return
        super().keyPressEvent(event)


class PclComboPopupFrame(QFrame):
    """In-window popup shell that avoids Qt's private combo overhang."""

    def __init__(self, combo: "PclComboBox") -> None:
        super().__init__(combo)
        self._combo = combo
        self.setObjectName("comboPopupWindow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setAutoFillBackground(False)
        self.setFocusPolicy(Qt.StrongFocus)
        self.hide()

        self.view = PclComboPopupView(self)
        self.view.setObjectName("comboPopupView")
        self.view.setFrameShape(QFrame.NoFrame)
        self.view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.view.setUniformItemSizes(True)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.view.setMouseTracking(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(0)
        layout.addWidget(self.view)

    def keyPressEvent(self, event):  # noqa: N802 - Qt override
        if event.key() == Qt.Key_Escape:
            self.hide()
            event.accept()
            return
        super().keyPressEvent(event)


class PclComboBox(QComboBox):
    """Combo box with a custom frameless popup."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._popup_frame = PclComboPopupFrame(self)
        self._popup_frame.installEventFilter(self)
        self._popup_frame.view.clicked.connect(self._choose_popup_index)
        self._popup_frame.view.activated.connect(self._choose_popup_index)
        polish_combo_popup(self)

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        if self.count() <= 0 or not self.isEnabled():
            return

        polish_combo_popup(self)
        self._sync_popup_view()
        self._position_popup()
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        self._popup_frame.show()
        self._popup_frame.raise_()
        self._popup_frame.view.setFocus(Qt.PopupFocusReason)

    def hidePopup(self) -> None:  # noqa: N802 - Qt override
        if self._popup_frame.isVisible():
            self._popup_frame.hide()
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().hidePopup()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt override
        if watched is self._popup_frame and event.type() == QEvent.Hide:
            self.update()
        if not self._popup_frame.isVisible():
            return super().eventFilter(watched, event)

        event_type = event.type()
        owner = self.window()
        if event_type in (QEvent.WindowDeactivate, QEvent.Close):
            self.hidePopup()
            return super().eventFilter(watched, event)
        if watched is owner and event_type in (QEvent.Move, QEvent.Resize):
            QTimer.singleShot(0, self._position_popup)
        if event_type == QEvent.MouseButtonPress:
            pos = self._event_global_pos(event)
            if pos is not None:
                popup_rect = QRect(self._popup_frame.mapToGlobal(QPoint(0, 0)), self._popup_frame.size())
                combo_rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
                if not popup_rect.contains(pos) and not combo_rect.contains(pos):
                    self.hidePopup()
        return super().eventFilter(watched, event)

    def polish_popup(self) -> None:
        view = self._popup_frame.view
        view.setModel(self.model())
        view.setRootIndex(self.rootModelIndex())
        try:
            view.setModelColumn(self.modelColumn())
        except Exception:
            pass
        view.setContentsMargins(0, 0, 0, 0)
        view.setViewportMargins(0, 0, 0, 0)
        view.viewport().setObjectName("comboPopupViewport")

    def _sync_popup_view(self) -> None:
        self.polish_popup()
        row = max(0, self.currentIndex())
        index = self.model().index(row, self.modelColumn(), self.rootModelIndex())
        if index.isValid():
            self._popup_frame.view.setCurrentIndex(index)
            self._popup_frame.view.scrollTo(index, QAbstractItemView.PositionAtCenter)

    def _position_popup(self) -> None:
        owner = self.window()
        if self._popup_frame.parentWidget() is not owner:
            self._popup_frame.setParent(owner)
            self._popup_frame.hide()

        row_count = max(1, self.model().rowCount(self.rootModelIndex()))
        visible_rows = max(1, min(row_count, self.maxVisibleItems()))
        row_height = self._popup_frame.view.sizeHintForRow(max(0, self.currentIndex()))
        if row_height <= 0:
            row_height = self.fontMetrics().height() + 14
        row_height = max(28, row_height)

        frame_margins = self._popup_frame.layout().contentsMargins()
        chrome_height = frame_margins.top() + frame_margins.bottom() + 2
        popup_height = visible_rows * row_height + chrome_height
        popup_width = max(self.width(), self.minimumWidth(), self.sizeHint().width(), 96)

        available = owner.rect().adjusted(4, 4, -4, -4)
        popup_width = min(popup_width, max(96, available.width()))
        popup_height = min(popup_height, max(row_height + chrome_height, available.height()))
        below = self.mapTo(owner, QPoint(0, self.height() + 3))
        above = self.mapTo(owner, QPoint(0, -popup_height - 3))
        y = below.y()
        if y + popup_height > available.bottom() + 1 and above.y() >= available.top():
            y = above.y()
        y = max(available.top(), min(y, available.bottom() - popup_height + 1))
        x = max(available.left(), min(below.x(), available.right() - popup_width + 1))

        self._popup_frame.setGeometry(x, y, popup_width, popup_height)

    def _choose_popup_index(self, index) -> None:
        if not index.isValid() or not (index.flags() & Qt.ItemIsEnabled):
            return
        row = index.row()
        self.setCurrentIndex(row)
        try:
            self.activated.emit(row)
        except Exception:
            pass
        self.hidePopup()

    @staticmethod
    def _event_global_pos(event: QEvent) -> QPoint | None:
        try:
            return event.globalPosition().toPoint()
        except Exception:
            pass
        try:
            return event.globalPos()
        except Exception:
            return None


def polish_combo_popup(combo: QComboBox) -> None:
    """Remove native popup overhang and keep combo menus visually flush."""
    if isinstance(combo, PclComboBox):
        combo.polish_popup()
        return

    if not isinstance(combo.view(), PclComboPopupView):
        combo.setView(PclComboPopupView(combo))
    view = combo.view()
    view.setObjectName("comboPopupView")
    view.setFrameShape(QFrame.NoFrame)
    view.setUniformItemSizes(True)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    view.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
    view.setContentsMargins(0, 0, 0, 0)
    try:
        view.setViewportMargins(0, 0, 0, 0)
        view.viewport().setObjectName("comboPopupViewport")
    except Exception:
        pass

    popup = view.window()
    if popup is not None:
        popup.setObjectName("comboPopupWindow")
        popup.setWindowFlag(Qt.NoDropShadowWindowHint, True)
        popup.setAttribute(Qt.WA_StyledBackground, True)
        popup.setContentsMargins(0, 0, 0, 0)


def apply_pcl_theme(app: QApplication) -> None:
    """Apply a light, card-based cockpit theme."""
    app.setFont(QFont("Microsoft YaHei UI", 10))
    palette = app.palette()
    palette.setColor(QPalette.Window, QColor(WINDOW))
    palette.setColor(QPalette.WindowText, QColor(TEXT))
    palette.setColor(QPalette.Base, QColor(SURFACE))
    palette.setColor(QPalette.AlternateBase, QColor("#f5f8fd"))
    palette.setColor(QPalette.Text, QColor(TEXT))
    palette.setColor(QPalette.Button, QColor(SURFACE))
    palette.setColor(QPalette.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor("#a6a6a6"))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#a6a6a6"))
    app.setPalette(palette)

    app.setStyleSheet(f"""
        QWidget {{
            color: {TEXT};
            font-family: "Microsoft YaHei UI", "Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Microsoft YaHei", sans-serif;
            font-size: 10pt;
            selection-background-color: {ACCENT};
            selection-color: #ffffff;
        }}
        QWidget#root {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #fbfaf7, stop:0.42 #f4f7fb, stop:0.72 #eef6f6, stop:1 #edf2f8);
        }}
        QDialog, QFileDialog {{
            background: {WINDOW};
            color: {TEXT};
            font-family: "Microsoft YaHei UI", "Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Microsoft YaHei", sans-serif;
            font-size: 10pt;
        }}
        QDialog QLabel, QFileDialog QLabel {{
            color: {TEXT};
        }}
        QFileDialog QListView, QFileDialog QTreeView,
        QFileDialog QTableView, QFileDialog QLineEdit,
        QFileDialog QComboBox {{
            font-family: "Microsoft YaHei UI", "Segoe UI Variable Text", "Segoe UI Variable", "Segoe UI", "Microsoft YaHei", sans-serif;
            font-size: 10pt;
        }}
        QDialogButtonBox QPushButton {{
            min-width: 76px;
        }}
        QScrollArea, QStackedWidget {{
            background: transparent;
            border: none;
        }}
        QFrame#pclHero {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                stop:0 #123c96, stop:0.54 {ACCENT}, stop:1 {TEAL});
            border: 1px solid rgba(255, 255, 255, 96);
            border-radius: 12px;
        }}
        QLabel#heroLogo {{
            min-width: 48px;
            min-height: 48px;
            max-width: 48px;
            max-height: 48px;
            border-radius: 10px;
            background: transparent;
            border: 1px solid rgba(255, 255, 255, 104);
            color: white;
        }}
        QLabel#heroMascot {{
            min-width: 58px;
            min-height: 63px;
            max-width: 58px;
            max-height: 63px;
            background: transparent;
            border: none;
        }}
        QLabel#heroTitle {{
            color: white;
            font-size: 16pt;
            font-weight: 700;
        }}
        QLabel#heroSubtitle {{
            color: rgba(255, 255, 255, 210);
            font-size: 9pt;
        }}
        QLabel#heroPill {{
            color: white;
            background: rgba(255, 255, 255, 38);
            border: 1px solid rgba(255, 255, 255, 70);
            border-radius: 9px;
            padding: 5px 10px;
            font-size: 9pt;
        }}
        QPushButton#heroMenuButton {{
            min-height: 28px;
            padding: 5px 14px;
            color: white;
            background: rgba(255, 255, 255, 28);
            border: 1px solid rgba(255, 255, 255, 66);
            border-radius: 8px;
            font-weight: 700;
        }}
        QPushButton#heroMenuButton:hover {{
            background: rgba(255, 255, 255, 54);
            border-color: rgba(255, 255, 255, 95);
            color: white;
        }}
        QPushButton#heroMenuButton:pressed {{
            background: rgba(255, 255, 255, 76);
            color: white;
        }}
        QPushButton#heroMenuButton::menu-indicator {{
            image: none;
            width: 0px;
        }}
        QFrame#connectionBar, QFrame#panel, QFrame#controlBar,
        QFrame#toolbarCard, QFrame#plotPanel, QFrame#plotToolBar,
        QFrame#debugBar {{
            background: rgba(255, 255, 255, 238);
            border: 1px solid rgba(15, 23, 42, 32);
            border-radius: 10px;
        }}
        QFrame#statusCluster {{
            background: rgba(238, 246, 255, 210);
            border: 1px solid rgba(29, 78, 216, 42);
            border-radius: 9px;
        }}
        QLabel#statusLed {{
            font-size: 13px;
            padding: 0px 1px;
        }}
        QLabel#statusText {{
            color: #596579;
            font-size: 9pt;
        }}
        QLabel#statusRate {{
            color: #343d4a;
            font-family: 'Cascadia Code', 'Consolas', monospace;
            font-size: 9pt;
        }}
        QLabel#debugState {{
            color: #343d4a;
            background: #f8fbff;
            border: 1px solid rgba(29, 78, 216, 42);
            border-radius: 8px;
            padding: 6px 10px;
            font-family: 'Cascadia Code', 'Consolas', monospace;
            font-size: 9pt;
        }}
        QFrame#pclDialogCard {{
            background: #ffffff;
            border: 1px solid #d8e4f1;
            border-radius: 10px;
        }}
        QLabel#dialogTitle {{
            color: {TEXT};
            font-size: 12pt;
            font-weight: 700;
        }}
        QLabel#dialogBody {{
            color: #596579;
            font-size: 9pt;
            line-height: 140%;
        }}
        QLabel#dialogIconInfo, QLabel#dialogIconWarning {{
            min-width: 38px;
            min-height: 38px;
            max-width: 38px;
            max-height: 38px;
            border-radius: 10px;
            font-size: 15pt;
            font-weight: 700;
        }}
        QLabel#dialogIconInfo {{
            background: #eaf2fe;
            color: {ACCENT_DARK};
            border: 1px solid {ACCENT_SOFT};
        }}
        QLabel#dialogIconWarning {{
            background: #fff1f0;
            color: #ce2111;
            border: 1px solid #ffd0cc;
        }}
        QPushButton#dialogPrimary {{
            background: {ACCENT};
            border-color: {ACCENT};
            color: #ffffff;
            min-width: 82px;
        }}
        QPushButton#dialogPrimary:hover {{
            background: #4890f5;
            border-color: #4890f5;
        }}
        QPushButton#debugBtn, QPushButton#debugPrimaryBtn {{
            min-height: 30px;
            padding: 5px 12px;
            border-radius: 8px;
            font-weight: 700;
        }}
        QPushButton#debugBtn {{
            background: #f7fbff;
            border: 1px solid #c9d9eb;
            color: {TEXT};
        }}
        QPushButton#debugBtn:hover {{
            background: #eaf2fe;
            border-color: {ACCENT_SOFT};
            color: {ACCENT_DARK};
        }}
        QPushButton#debugPrimaryBtn {{
            background: {ACCENT};
            border: 1px solid {ACCENT};
            color: white;
        }}
        QPushButton#debugPrimaryBtn:hover {{
            background: #4890f5;
            border-color: #4890f5;
        }}
        QPushButton#debugBtn:disabled, QPushButton#debugPrimaryBtn:disabled {{
            background: #f1f4f8;
            border-color: #dbe4ef;
            color: #a6aeb8;
        }}
        QLabel#sectionTitle {{
            color: {TEXT};
            font-size: 10pt;
            font-weight: 700;
        }}
        QLabel#hintLabel {{
            color: {MUTED};
            font-size: 9pt;
            padding: 3px 2px;
        }}
        QLabel#barTitle {{
            color: {TEXT};
            font-weight: 700;
        }}
        QLabel#chipLabel {{
            color: #0f766e;
            background: {TEAL_SOFT};
            border: 1px solid rgba(14, 165, 165, 84);
            border-radius: 8px;
            padding: 4px 8px;
            font-weight: 700;
        }}
        QTabWidget::pane {{
            border: none;
            background: transparent;
            top: -1px;
        }}
        QTabBar::tab {{
            background: rgba(255, 255, 255, 215);
            color: {MUTED};
            padding: 9px 22px;
            border: 1px solid rgba(15, 23, 42, 24);
            border-bottom: 1px solid rgba(15, 23, 42, 24);
            border-top-left-radius: 10px;
            border-top-right-radius: 10px;
            margin-right: 4px;
            font-size: 10pt;
            font-weight: 700;
        }}
        QTabBar::tab:selected {{
            background: #ffffff;
            color: {ACCENT_DARK};
            border-color: rgba(29, 78, 216, 64);
            border-bottom: 2px solid {ACCENT};
        }}
        QTabBar::tab:hover:!selected {{
            background: #f7fbff;
            color: {ACCENT};
        }}
        QTreeWidget, QListWidget, QTableWidget,
        QTreeView, QListView, QTableView {{
            background: #ffffff;
            alternate-background-color: #f7f9fc;
            border: 1px solid rgba(15, 23, 42, 30);
            border-radius: 8px;
            outline: none;
            padding: 2px;
            gridline-color: #eef2f7;
        }}
        QTreeWidget, QTreeView {{
            show-decoration-selected: 0;
        }}
        QTreeWidget::item, QTreeView::item {{
            min-height: 26px;
            padding: 3px 5px;
            border-radius: 0px;
        }}
        QListWidget::item, QListView::item {{
            min-height: 26px;
            padding: 3px 5px;
            border-radius: 5px;
        }}
        QTreeWidget::item:hover, QTreeView::item:hover,
        QListWidget::item:hover, QListView::item:hover {{
            background: #eaf2fe;
            color: {ACCENT_DARK};
        }}
        QTreeWidget::item:selected, QTreeView::item:selected {{
            background: {ACCENT_SOFT};
            color: {ACCENT_DARK};
            border-left: 3px solid {ACCENT};
        }}
        QListWidget::item:selected, QListView::item:selected,
        QTableWidget::item:selected, QTableView::item:selected {{
            background: {ACCENT_SOFT};
            color: {ACCENT_DARK};
        }}
        QTreeWidget::branch, QTreeView::branch {{
            background: transparent;
            border: none;
            margin: 0px;
            padding: 0px;
            width: 18px;
        }}
        QTreeWidget::branch:selected, QTreeView::branch:selected {{
            background: transparent;
            border: none;
        }}
        QTableWidget::item, QTableView::item {{
            padding: 3px 7px;
            color: {TEXT};
        }}
        QHeaderView::section {{
            background: #f8fafc;
            color: {MUTED};
            border: none;
            border-bottom: 1px solid rgba(15, 23, 42, 26);
            padding: 7px 8px;
            font-weight: 700;
            font-size: 9pt;
        }}
        QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
            min-height: 24px;
            padding: 5px 10px;
            background: #ffffff;
            border: 1px solid {BORDER};
            border-radius: 8px;
            color: {TEXT};
            font-size: 9pt;
        }}
        QTextEdit, QPlainTextEdit {{
            padding: 8px;
        }}
        QLineEdit:hover, QSpinBox:hover, QComboBox:hover,
        QTextEdit:hover, QPlainTextEdit:hover {{
            border-color: #aac6e9;
            background: #fbfdff;
        }}
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QComboBox:on,
        QTextEdit:focus, QPlainTextEdit:focus {{
            border-color: {ACCENT};
            background: #ffffff;
        }}
        QComboBox {{
            padding: 5px 24px 5px 10px;
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 22px;
            border: none;
            border-left: 1px solid #e1e8f2;
            border-top-right-radius: 8px;
            border-bottom-right-radius: 8px;
            background: transparent;
        }}
        QComboBox::drop-down:hover, QComboBox::drop-down:on {{
            background: #edf5ff;
        }}
        QComboBox::down-arrow {{
            image: none;
            width: 0px;
            height: 0px;
        }}
        QFrame#comboPopupWindow {{
            background: #ffffff;
            border: 1px solid {BORDER};
            border-radius: 8px;
        }}
        QFrame#comboPopupWindow QListView#comboPopupView,
        QAbstractItemView#comboPopupView {{
            background: transparent;
            border: none;
            border-radius: 0px;
            padding: 0px;
            margin: 0px;
            color: {TEXT};
            selection-background-color: {ACCENT_SOFT};
            selection-color: {ACCENT_DARK};
            outline: 0;
        }}
        QFrame#comboPopupWindow QListView#comboPopupView::item,
        QAbstractItemView#comboPopupView::item {{
            min-height: 22px;
            padding: 4px 8px;
            margin: 0px;
            border-radius: 4px;
        }}
        QFrame#comboPopupWindow QListView#comboPopupView::item:hover,
        QAbstractItemView#comboPopupView::item:hover {{
            background: #eaf3ff;
            color: {ACCENT_DARK};
        }}
        QFrame#comboPopupWindow QListView#comboPopupView::item:selected,
        QAbstractItemView#comboPopupView::item:selected {{
            background: {ACCENT_SOFT};
            color: {ACCENT_DARK};
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            width: 18px;
            border: none;
            background: transparent;
        }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background: #eaf2fe;
        }}
        QPushButton {{
            min-height: 29px;
            padding: 6px 14px;
            background: rgba(255, 255, 255, 238);
            border: 1px solid rgba(15, 23, 42, 36);
            border-radius: 8px;
            color: {TEXT};
            font-size: 9pt;
            font-weight: 700;
        }}
        QPushButton:hover {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #ffffff, stop:1 #eef6ff);
            border-color: rgba(29, 78, 216, 90);
            color: {ACCENT_DARK};
        }}
        QPushButton:pressed {{
            background: #dbeafe;
            border-color: {ACCENT};
            color: {ACCENT_DARK};
            padding: 6px 14px;
        }}
        QPushButton:disabled {{
            background: #f2f4f8;
            color: #a6a6a6;
            border-color: #e1e6ee;
        }}
        QPushButton#importBtn, QPushButton#startBtn, QPushButton#connectBtn {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {ACCENT}, stop:1 {TEAL});
            border-color: rgba(29, 78, 216, 120);
            color: white;
        }}
        QPushButton#importBtn:hover, QPushButton#startBtn:hover, QPushButton#connectBtn:hover {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #2563eb, stop:1 #14b8a6);
            border-color: rgba(14, 165, 165, 150);
        }}
        QPushButton#importBtn:pressed, QPushButton#startBtn:pressed, QPushButton#connectBtn:pressed {{
            background: {ACCENT_DARK};
            border-color: {ACCENT_DARK};
            color: #ffffff;
        }}
        QPushButton#stopBtn, QPushButton#disconnectBtn {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #f97316, stop:1 #ef4444);
            border-color: rgba(239, 68, 68, 140);
            color: white;
        }}
        QPushButton#stopBtn:hover, QPushButton#disconnectBtn:hover {{
            background: #ff4c4c;
            border-color: #ff4c4c;
        }}
        QPushButton#stopBtn:pressed, QPushButton#disconnectBtn:pressed {{
            background: #a9190d;
            border-color: #a9190d;
            color: #ffffff;
        }}
        QPushButton#exportBtn {{
            background: #ffffff;
            color: {ACCENT_DARK};
            border-color: #b4cbe6;
        }}
        QPushButton#smallBtn, QPushButton#scanBtn {{
            background: #ffffff;
            color: {TEXT};
            border-color: #c2d6ef;
        }}
        QPushButton#smallBtn:hover, QPushButton#scanBtn:hover,
        QPushButton#exportBtn:hover {{
            background: {SOFT_BLUE};
            color: {ACCENT_DARK};
            border-color: {ACCENT};
        }}
        QFrame#segmentedControl {{
            background: #f8fafc;
            border: 1px solid rgba(15, 23, 42, 34);
            border-radius: 9px;
        }}
        QFrame#segmentedControl:disabled {{
            background: #f2f4f8;
            border-color: #e1e6ee;
        }}
        QPushButton#segmentButton {{
            min-height: 24px;
            min-width: 46px;
            padding: 3px 8px;
            background: transparent;
            border: none;
            border-radius: 7px;
            color: #596579;
            font-size: 9pt;
            font-weight: 700;
        }}
        QPushButton#segmentButton:hover {{
            background: {SOFT_BLUE};
            color: {ACCENT_DARK};
        }}
        QPushButton#segmentButton:checked {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {ACCENT}, stop:1 {TEAL});
            color: #ffffff;
        }}
        QPushButton#segmentButton:disabled {{
            background: transparent;
            color: #a6a6a6;
        }}
        QPushButton#presetBtn {{
            padding: 4px 8px;
            min-width: 34px;
        }}
        QPushButton#plotToolBtn, QPushButton#plotFloatBtn, QPushButton#yAutoBtn {{
            min-height: 24px;
            padding: 4px 10px;
            background: #ffffff;
            border: 1px solid #c2d6ef;
            border-radius: 8px;
            color: #596579;
            font-size: 8pt;
            font-weight: 700;
        }}
        QPushButton#plotToolBtn:checked, QPushButton#plotFloatBtn:checked, QPushButton#yAutoBtn:checked {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 {ACCENT}, stop:1 {TEAL});
            border-color: rgba(29, 78, 216, 120);
            color: #ffffff;
        }}
        QPushButton#plotToolBtn:hover, QPushButton#plotFloatBtn:hover, QPushButton#yAutoBtn:hover {{
            background: {SOFT_BLUE};
            border-color: {ACCENT};
            color: {ACCENT_DARK};
        }}
        QPushButton#plotToolBtn:checked:hover, QPushButton#plotFloatBtn:checked:hover, QPushButton#yAutoBtn:checked:hover {{
            background: #4890f5;
            border-color: #4890f5;
            color: #ffffff;
        }}
        QToolButton {{
            background: #ffffff;
            border: 1px solid #c2d6ef;
            border-radius: 8px;
            padding: 5px;
            color: {TEXT};
        }}
        QToolButton:hover {{
            background: {SOFT_BLUE};
            border-color: {ACCENT};
            color: {ACCENT_DARK};
        }}
        QGroupBox {{
            background: rgba(255, 255, 255, 246);
            border: 1px solid #d8e4f1;
            border-radius: 9px;
            margin-top: 13px;
            padding: 11px 9px 9px 9px;
            font-weight: 700;
        }}
        QGroupBox::title {{
            subcontrol-origin: margin;
            subcontrol-position: top left;
            left: 10px;
            padding: 0 5px;
            color: {ACCENT_DARK};
            background: {WINDOW};
        }}
        QCheckBox, QRadioButton {{
            spacing: 7px;
            color: {TEXT};
        }}
        QCheckBox::indicator, QRadioButton::indicator {{
            width: 15px;
            height: 15px;
            border: 1px solid #b7cbea;
            background: #ffffff;
        }}
        QCheckBox::indicator {{
            border-radius: 3px;
        }}
        QRadioButton::indicator {{
            border-radius: 8px;
        }}
        QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
            border-color: {ACCENT};
            background: #eaf2fe;
        }}
        QCheckBox::indicator:checked {{
            background: {ACCENT};
            border-color: {ACCENT};
        }}
        QRadioButton::indicator:checked {{
            background: {ACCENT};
            border: 4px solid #ffffff;
        }}
        QStatusBar {{
            background: #ffffff;
            border-top: 1px solid #d9e3f0;
            color: #596579;
            font-size: 9pt;
            padding: 3px 8px;
        }}
        QMenuBar {{
            background: #ffffff;
            border-bottom: 1px solid #d9e3f0;
            color: {TEXT};
            padding: 2px;
        }}
        QMenuBar::item:selected {{
            background: {SOFT_BLUE};
            border-radius: 7px;
        }}
        QMenu {{
            background: #ffffff;
            border: 1px solid {BORDER};
            color: {TEXT};
            padding: 4px;
        }}
        QMenu::separator {{
            height: 1px;
            background: #e1e8f2;
            margin: 4px 8px;
        }}
        QMenu::item:selected {{
            background: {SOFT_BLUE};
            color: {ACCENT_DARK};
            border-radius: 7px;
        }}
        QToolTip {{
            background: rgba(255, 255, 255, 238);
            color: {TEXT};
            border: 1px solid {BORDER};
            padding: 5px 8px;
            border-radius: 8px;
            font-size: 9pt;
        }}
        QDialog#pclDialog {{
            background: #ffffff;
            color: {TEXT};
        }}
        QProgressBar {{
            min-height: 8px;
            border: 1px solid #c2d6ef;
            border-radius: 6px;
            background: #edf3fb;
            text-align: center;
            color: transparent;
        }}
        QProgressBar::chunk {{
            border-radius: 5px;
            background: {ACCENT};
        }}
        QFrame#connSeparator {{
            background: #d8e4f1;
            border: none;
        }}
        QSplitter::handle {{
            background: #d8e4f1;
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 9px;
            margin: 0;
        }}
        QScrollBar::handle:vertical {{
            background: #c5d2e3;
            min-height: 30px;
            border-radius: 5px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: #aebed2;
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            height: 0;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: none;
        }}
        QScrollBar:horizontal {{
            background: transparent;
            height: 9px;
            margin: 0;
        }}
        QScrollBar::handle:horizontal {{
            background: #c5d2e3;
            min-width: 30px;
            border-radius: 5px;
        }}
        QScrollBar::handle:horizontal:hover {{
            background: #aebed2;
        }}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
            width: 0;
        }}
        QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
            background: none;
        }}
    """)

    app.setStyleSheet(app.styleSheet() + f"""
        QWidget#root {{
            background: #f3f7fb;
        }}
        QFrame#pclHero {{
            background: #fbfdff;
            border: 1px solid rgba(15, 23, 42, 18);
            border-radius: 10px;
        }}
        QLabel#heroLogo {{
            min-width: 50px;
            min-height: 50px;
            max-width: 50px;
            max-height: 50px;
            border-radius: 13px;
            background: transparent;
            border: 1px solid rgba(15, 23, 42, 24);
            color: #172033;
        }}
        QLabel#heroMascot {{
            min-width: 58px;
            min-height: 63px;
            max-width: 58px;
            max-height: 63px;
            background: transparent;
            border: none;
        }}
        QLabel#heroTitle {{
            color: #172033;
            font-size: 15pt;
            font-weight: 620;
        }}
        QLabel#heroSubtitle {{
            color: #66758a;
            font-size: 9pt;
        }}
        QLabel#heroPill {{
            color: #304055;
            background: #f7fbff;
            border: 1px solid rgba(15, 23, 42, 18);
            border-radius: 8px;
            padding: 6px 11px;
            font-size: 9pt;
            font-weight: 540;
        }}
        QPushButton#heroMenuButton {{
            min-height: 29px;
            padding: 6px 14px;
            color: #334155;
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #ffffff, stop:1 #f7fbff);
            border: 1px solid rgba(15, 23, 42, 18);
            border-radius: 8px;
            font-weight: 560;
        }}
        QPushButton#heroMenuButton:hover {{
            background: #eef6ff;
            border-color: #a9c8ef;
            color: #1d4ed8;
        }}
        QPushButton#heroMenuButton:pressed {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #dcecff, stop:1 #cfe1fa);
            border-color: #2563eb;
            color: #1d4ed8;
        }}
        QFrame#connectionBar {{
            background: #fbfdff;
            border: 1px solid rgba(15, 23, 42, 16);
            border-radius: 9px;
        }}
        QFrame#workspaceShell {{
            background: #fbfdff;
            border: 1px solid rgba(15, 23, 42, 12);
            border-radius: 8px;
        }}
        QFrame#navRail {{
            background: #f8fafc;
            border: 1px solid rgba(15, 23, 42, 12);
            border-radius: 8px;
        }}
        QLabel#navTitle {{
            color: #172033;
            font-size: 11pt;
            font-weight: 600;
            padding: 2px 4px 0px 4px;
        }}
        QLabel#navHint {{
            color: #718096;
            font-size: 8pt;
            padding: 0px 4px 9px 4px;
        }}
        QLabel#navSection {{
            color: #718096;
            font-size: 8pt;
            font-weight: 540;
            padding: 8px 4px 2px 4px;
        }}
        QLabel#navMetric {{
            color: #475569;
            background: rgba(255, 255, 255, 210);
            border: 1px solid rgba(15, 23, 42, 22);
            border-radius: 8px;
            padding: 8px 10px;
            font-family: 'Cascadia Code', 'Consolas', monospace;
            font-size: 8pt;
        }}
        QPushButton#navDomainButton, QPushButton#navButton {{
            min-height: 34px;
            padding: 7px 9px;
            text-align: left;
            color: #475569;
            background: transparent;
            border: 1px solid transparent;
            border-radius: 7px;
            font-weight: 520;
        }}
        QPushButton#navDomainButton {{
            min-height: 36px;
            background: #ffffff;
            border-color: rgba(15, 23, 42, 20);
            font-weight: 560;
        }}
        QPushButton#navDomainButton:hover, QPushButton#navButton:hover {{
            background: rgba(239, 246, 255, 210);
            border-color: rgba(37, 99, 235, 40);
            color: #1d4ed8;
        }}
        QPushButton#navDomainButton:checked, QPushButton#navButton:checked {{
            background: #eaf2ff;
            border-color: rgba(37, 99, 235, 92);
            color: #1d4ed8;
            font-weight: 600;
        }}
        QTabWidget#workspaceTabs::pane {{
            border: none;
            background: transparent;
        }}
        QFrame#toolbarCard, QFrame#plotToolBar, QFrame#debugBar,
        QFrame#controlBar, QFrame#panel, QFrame#plotPanel {{
            background: #ffffff;
            border: 1px solid rgba(15, 23, 42, 12);
            border-radius: 8px;
        }}
        QFrame#scopePane {{
            background: #ffffff;
            border: 1px solid rgba(15, 23, 42, 8);
            border-radius: 7px;
        }}
        QWidget#scopeSidebar {{
            background: transparent;
        }}
        QWidget#scopePlotArea {{
            background: transparent;
        }}
        QScrollArea#scopeSidebarScroll {{
            background: transparent;
            border: none;
        }}
        QLabel#sectionTitle {{
            color: #172033;
            font-size: 10pt;
            font-weight: 600;
        }}
        QLabel#hintLabel, QLabel#statusText {{
            color: #66758a;
            font-size: 9pt;
        }}
        QLabel#barTitle {{
            color: #2f3d52;
            font-weight: 600;
        }}
        QLabel#scopePaneTitle {{
            color: #172033;
            font-size: 10pt;
            font-weight: 600;
        }}
        QLabel#scopePaneCount {{
            color: #64748b;
            background: #f7fbff;
            border: 1px solid rgba(148, 163, 184, 34);
            border-radius: 7px;
            padding: 4px 9px;
            font-family: 'Cascadia Code', 'Consolas', monospace;
            font-size: 8pt;
        }}
        QLabel#scopePaneLegend {{
            color: #52657f;
            font-size: 8pt;
            padding: 0px 1px 2px 1px;
        }}
        QLabel#chipLabel {{
            color: #1d4ed8;
            background: #eef6ff;
            border: 1px solid #cfe1f7;
            border-radius: 10px;
            padding: 5px 9px;
            font-weight: 700;
        }}
        QLabel#debugState, QLabel#statusRate {{
            color: #334155;
            background: #f8fbff;
            border: 1px solid rgba(15, 23, 42, 24);
            border-radius: 8px;
            padding: 6px 10px;
            font-family: 'Cascadia Code', 'Consolas', monospace;
            font-size: 9pt;
        }}
        QTreeWidget, QListWidget, QTableWidget,
        QTreeView, QListView, QTableView {{
            background: #ffffff;
            alternate-background-color: #f7fbff;
            border: 1px solid rgba(15, 23, 42, 24);
            border-radius: 8px;
            outline: none;
            padding: 2px;
            gridline-color: #edf2f8;
        }}
        QTreeWidget, QTreeView {{
            show-decoration-selected: 0;
        }}
        QTreeWidget::branch, QTreeView::branch {{
            background: transparent;
            border: none;
            margin: 0px;
            padding: 0px;
            width: 18px;
        }}
        QTreeWidget::branch:selected, QTreeView::branch:selected {{
            background: transparent;
            border: none;
        }}
        QTreeWidget::item, QTreeView::item {{
            min-height: 28px;
            padding: 4px 7px;
            border-radius: 7px;
        }}
        QListWidget::item, QListView::item {{
            min-height: 28px;
            padding: 4px 7px;
            border-radius: 7px;
        }}
        QTableWidget::item, QTableView::item {{
            min-height: 28px;
            padding: 4px 8px;
            color: #273449;
        }}
        QTreeWidget::item:hover, QTreeView::item:hover,
        QListWidget::item:hover, QListView::item:hover {{
            background: #eef6ff;
            color: #1d4ed8;
        }}
        QTreeWidget::item:selected, QTreeView::item:selected,
        QListWidget::item:selected, QListView::item:selected,
        QTableWidget::item:selected, QTableView::item:selected {{
            background: #dbeafe;
            color: #1d4ed8;
        }}
        QHeaderView::section {{
            background: #f2f6fb;
            color: #52657f;
            border: none;
            border-bottom: 1px solid #dbe6f2;
            padding: 7px 10px;
            font-weight: 600;
            font-size: 9pt;
        }}
        QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
            min-height: 31px;
            padding: 6px 11px;
            background: #fbfdff;
            border: 1px solid rgba(15, 23, 42, 30);
            border-radius: 8px;
            color: #273449;
            font-size: 10pt;
        }}
        QLineEdit:hover, QSpinBox:hover, QComboBox:hover,
        QTextEdit:hover, QPlainTextEdit:hover {{
            border-color: #abc6ea;
            background: #fbfdff;
        }}
        QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QComboBox:on,
        QTextEdit:focus, QPlainTextEdit:focus {{
            border-color: #2563eb;
            background: #ffffff;
        }}
        QPushButton {{
            min-height: 32px;
            padding: 7px 11px;
            background: #fbfdff;
            border: 1px solid rgba(15, 23, 42, 30);
            border-radius: 8px;
            color: #273449;
            font-size: 10pt;
            font-weight: 560;
        }}
        QPushButton:hover {{
            background: #eef6ff;
            border-color: #8fb9ef;
            color: #1d4ed8;
        }}
        QPushButton:pressed {{
            background: #dcecff;
            border-color: #2563eb;
            color: #1d4ed8;
        }}
        QPushButton:disabled {{
            background: #f2f5f9;
            color: #a2adbb;
            border-color: #e1e8f0;
        }}
        QPushButton#importBtn, QPushButton#debugPrimaryBtn {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #2f6cf6, stop:0.65 #2563eb, stop:1 #0ea5a5);
            border-color: #2563eb;
            color: #ffffff;
        }}
        QPushButton#importBtn:hover, QPushButton#debugPrimaryBtn:hover {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #3b82f6, stop:0.64 #2f6cf6, stop:1 #14b8a6);
            border-color: #3b82f6;
            color: #ffffff;
        }}
        QPushButton#importBtn:pressed, QPushButton#debugPrimaryBtn:pressed {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #1d4ed8, stop:1 #1e40af);
            border-color: #1d4ed8;
            color: #ffffff;
        }}
        QPushButton#startBtn, QPushButton#connectBtn {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #22c55e, stop:0.62 #16a34a, stop:1 #0ea5a5);
            border-color: #16a34a;
            color: #ffffff;
        }}
        QPushButton#startBtn:hover, QPushButton#connectBtn:hover {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #34d399, stop:0.62 #22c55e, stop:1 #14b8a6);
            border-color: #22c55e;
            color: #ffffff;
        }}
        QPushButton#startBtn:pressed, QPushButton#connectBtn:pressed {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #15803d, stop:1 #166534);
            border-color: #15803d;
            color: #ffffff;
        }}
        QPushButton#stopBtn, QPushButton#disconnectBtn {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #ef4444, stop:1 #dc2626);
            border-color: #dc2626;
            color: #ffffff;
        }}
        QPushButton#stopBtn:hover, QPushButton#disconnectBtn:hover {{
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 #f87171, stop:1 #ef4444);
            border-color: #ef4444;
            color: #ffffff;
        }}
        QPushButton#smallBtn, QPushButton#scanBtn, QPushButton#exportBtn,
        QPushButton#debugBtn, QPushButton#plotToolBtn, QPushButton#plotFloatBtn,
        QPushButton#yAutoBtn {{
            background: #fbfdff;
            color: #334155;
            border-color: #d2e0ef;
        }}
        QPushButton#smallBtn:hover, QPushButton#scanBtn:hover, QPushButton#exportBtn:hover,
        QPushButton#debugBtn:hover, QPushButton#plotToolBtn:hover, QPushButton#plotFloatBtn:hover,
        QPushButton#yAutoBtn:hover {{
            background: #eef6ff;
            border-color: #8fb9ef;
            color: #1d4ed8;
        }}
        QPushButton#plotToolBtn:checked, QPushButton#plotFloatBtn:checked,
        QPushButton#yAutoBtn:checked {{
            background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                stop:0 #2f6cf6, stop:0.64 #2563eb, stop:1 #0ea5a5);
            border-color: #2563eb;
            color: #ffffff;
        }}
        QFrame#segmentedControl {{
            background: #f2f6fb;
            border: 1px solid #d2e0ef;
            border-radius: 8px;
        }}
        QPushButton#segmentButton {{
            min-height: 26px;
            min-width: 48px;
            padding: 4px 9px;
            background: transparent;
            border: none;
            border-radius: 7px;
            color: #52657f;
            font-size: 9pt;
            font-weight: 600;
        }}
        QPushButton#segmentButton:hover {{
            background: #e9f3ff;
            color: #1d4ed8;
        }}
        QPushButton#segmentButton:checked {{
            background: #2563eb;
            color: #ffffff;
        }}
        QComboBox {{
            padding: 7px 28px 7px 12px;
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 24px;
            border: none;
            border-left: 1px solid #e1e8f2;
            border-top-right-radius: 10px;
            border-bottom-right-radius: 10px;
            background: rgba(37, 99, 235, 10);
        }}
        QComboBox::drop-down:hover, QComboBox::drop-down:on {{
            background: #eef6ff;
        }}
        QComboBox::down-arrow {{
            image: none;
            width: 0px;
            height: 0px;
        }}
        QFrame#comboPopupWindow {{
            background: #ffffff;
            border: 1px solid #cfddec;
            border-radius: 8px;
        }}
        QFrame#comboPopupWindow QListView#comboPopupView,
        QAbstractItemView#comboPopupView {{
            background: transparent;
            border: none;
            border-radius: 0px;
            padding: 0px;
            margin: 0px;
            color: #273449;
            selection-background-color: #dbeafe;
            selection-color: #1d4ed8;
            outline: 0;
        }}
        QFrame#comboPopupWindow QListView#comboPopupView::item,
        QAbstractItemView#comboPopupView::item {{
            min-height: 24px;
            padding: 4px 8px;
            margin: 0px;
            border-radius: 4px;
        }}
        QFrame#comboPopupWindow QListView#comboPopupView::item:hover,
        QAbstractItemView#comboPopupView::item:hover {{
            background: #eef6ff;
            color: #1d4ed8;
        }}
        QFrame#comboPopupWindow QListView#comboPopupView::item:selected,
        QAbstractItemView#comboPopupView::item:selected {{
            background: #2563eb;
            color: #ffffff;
        }}
        QMenu {{
            background: rgba(255, 255, 255, 252);
            border: 1px solid #cfddec;
            border-radius: 10px;
            color: #273449;
            padding: 5px;
        }}
        QMenu::item {{
            padding: 7px 18px;
            border-radius: 8px;
        }}
        QMenu::item:selected {{
            background: #eef6ff;
            color: #1d4ed8;
        }}
        QSplitter::handle {{
            background: transparent;
        }}
        QSplitter::handle:hover {{
            background: #dce7f3;
        }}
        QSplitter#scopeMainSplitter::handle {{
            background: transparent;
            border: none;
        }}
        QSplitter#scopeMainSplitter::handle:horizontal {{
            width: 2px;
            margin: 16px 0px;
            border-left: 1px solid rgba(148, 163, 184, 34);
        }}
        QSplitter#scopeMainSplitter::handle:hover {{
            background: rgba(37, 99, 235, 18);
        }}
        QSplitter#scopePaneSplitter::handle {{
            background: transparent;
            border: none;
        }}
        QSplitter#scopePaneSplitter::handle:horizontal {{
            width: 3px;
            margin: 14px 1px;
            border-left: 1px solid rgba(148, 163, 184, 42);
        }}
        QSplitter#scopePaneSplitter::handle:vertical {{
            height: 3px;
            margin: 1px 14px;
            border-top: 1px solid rgba(148, 163, 184, 42);
        }}
        QSplitter#scopePaneSplitter::handle:hover {{
            background: rgba(37, 99, 235, 22);
        }}
        QPushButton#scanBtn, QPushButton#debugBtn, QPushButton#plotToolBtn,
        QPushButton#presetBtn, QPushButton#segmentButton {{
            padding-left: 8px;
            padding-right: 8px;
        }}
        QPushButton#presetBtn {{
            min-height: 28px;
            font-weight: 560;
        }}
        QPushButton#startBtn, QPushButton#stopBtn, QPushButton#connectBtn,
        QPushButton#disconnectBtn, QPushButton#importBtn, QPushButton#exportBtn {{
            font-weight: 620;
        }}
        QGraphicsView {{
            border: none;
            background: transparent;
        }}
        QToolTip {{
            background: rgba(255, 255, 255, 244);
            color: #273449;
            border: 1px solid #cfddec;
            padding: 6px 10px;
            border-radius: 10px;
            font-size: 9pt;
        }}
    """)


def install_card_shadow(
    widget: QWidget,
    blur_radius: float = 20.0,
    y_offset: float = 5.0,
    alpha: int = 30,
) -> QGraphicsDropShadowEffect | None:
    if widget.objectName() not in {
        "pclHero",
        "pclDialogCard",
        "navRail",
    }:
        return None
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur_radius)
    effect.setOffset(0, y_offset)
    effect.setColor(QColor(30, 41, 59, min(alpha, 38)))
    widget.setGraphicsEffect(effect)
    return effect


class PclMessageDialog(QDialog):
    """Small frameless PCL-style message dialog."""

    def __init__(self, parent: QWidget | None, title: str, message: str, kind: str = "info"):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setObjectName("pclDialog")
        self.setMinimumWidth(380)
        self.setMaximumWidth(560)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)

        card = QFrame()
        card.setObjectName("pclDialogCard")
        install_card_shadow(card, blur_radius=24, y_offset=7, alpha=48)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(14)

        head = QHBoxLayout()
        head.setSpacing(12)
        icon = QLabel("!" if kind == "warning" else "i")
        icon.setAlignment(Qt.AlignCenter)
        icon.setObjectName("dialogIconWarning" if kind == "warning" else "dialogIconInfo")
        head.addWidget(icon)

        text_stack = QVBoxLayout()
        text_stack.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("dialogTitle")
        body_label = QLabel(message)
        body_label.setObjectName("dialogBody")
        body_label.setWordWrap(True)
        text_stack.addWidget(title_label)
        text_stack.addWidget(body_label)
        head.addLayout(text_stack, stretch=1)
        layout.addLayout(head)

        buttons = QHBoxLayout()
        buttons.addStretch()
        ok = QPushButton("确定")
        ok.setObjectName("dialogPrimary")
        ok.clicked.connect(self.accept)
        buttons.addWidget(ok)
        layout.addLayout(buttons)


class PclLoadingDialog(QDialog):
    """Frameless loading dialog for long synchronous work."""

    def __init__(self, parent: QWidget | None, title: str, message: str):
        super().__init__(parent)
        self.setModal(True)
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setObjectName("pclDialog")
        self.setMinimumWidth(430)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(18, 18, 18, 18)

        card = QFrame()
        card.setObjectName("pclDialogCard")
        install_card_shadow(card, blur_radius=24, y_offset=7, alpha=48)
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title_label = QLabel(title)
        title_label.setObjectName("dialogTitle")
        layout.addWidget(title_label)

        self.message_label = QLabel(message)
        self.message_label.setObjectName("dialogBody")
        self.message_label.setWordWrap(True)
        layout.addWidget(self.message_label)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        layout.addWidget(self.progress)

    def set_message(self, message: str) -> None:
        self.message_label.setText(message)


def show_pcl_message(parent: QWidget | None, title: str, message: str, kind: str = "info") -> int:
    dialog = PclMessageDialog(parent, title, message, kind)
    return dialog.exec()


class PclHoverFilter(QObject):
    """Cursor binding for clickable controls.

    Button-level graphics effects can expand outside dense Qt layouts and make
    controls appear to hang over neighboring cards, so button feedback stays in
    the stylesheet while page/status animations carry the motion language.
    """

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._anims: dict[QWidget, QPropertyAnimation] = {}

    def bind(self, widget: QWidget) -> None:
        if getattr(widget, "_pcl_motion_bound", False):
            return
        widget.setCursor(Qt.PointingHandCursor)
        widget._pcl_motion_bound = True

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        return False


def start_status_pulse(widget: QWidget, parent: QObject) -> None:
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(1.0)
    widget.setGraphicsEffect(effect)
    anim = QPropertyAnimation(effect, b"opacity", parent)
    anim.setDuration(1350)
    anim.setStartValue(0.58)
    anim.setKeyValueAt(0.48, 1.0)
    anim.setEndValue(0.58)
    anim.setLoopCount(-1)
    anim.setEasingCurve(QEasingCurve.InOutSine)
    anim.start()
    return anim


def animate_page_enter(page: QWidget, parent: QObject) -> None:
    """Fade the current page in without disturbing splitter geometry."""
    effect = QGraphicsOpacityEffect(page)
    effect.setOpacity(0.0)
    page.setGraphicsEffect(effect)
    start_pos = page.pos() + QPoint(10, 0)
    end_pos = page.pos()
    page.move(start_pos)

    group = QParallelAnimationGroup(parent)
    opacity = QPropertyAnimation(effect, b"opacity", group)
    opacity.setDuration(210)
    opacity.setStartValue(0.0)
    opacity.setEndValue(1.0)
    opacity.setEasingCurve(QEasingCurve.OutCubic)
    slide = QPropertyAnimation(page, b"pos", group)
    slide.setDuration(230)
    slide.setStartValue(start_pos)
    slide.setEndValue(end_pos)
    slide.setEasingCurve(QEasingCurve.OutCubic)
    group.addAnimation(opacity)
    group.addAnimation(slide)
    if not hasattr(parent, "_pcl_page_anims"):
        parent._pcl_page_anims = []
    parent._pcl_page_anims.append(group)

    def finish() -> None:
        page.setGraphicsEffect(None)
        page.move(end_pos)
        try:
            parent._pcl_page_anims.remove(group)
        except (AttributeError, ValueError):
            pass

    group.finished.connect(finish)
    group.start()
