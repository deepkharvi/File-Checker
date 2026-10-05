"""Black CyberTech UI for the File Health Checker.  Made by CyberTech 2026."""
import math
import os
import time

from PySide6.QtCore import (
    Qt, QTimer, QPropertyAnimation, QEasingCurve, QRectF, QPointF, Signal,
)
from PySide6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QLinearGradient, QPainterPath,
    QRadialGradient, QPalette,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QFileDialog, QProgressBar, QRadioButton, QButtonGroup,
    QComboBox, QMessageBox, QTextEdit, QFrame, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QTabWidget, QStyle, QGraphicsDropShadowEffect,
)

from models import FileMode, DuplicatePolicy, EXT_TO_CATEGORY
from scanner import ScannerWorker
from report import export_csv
from file_utils import list_dir_entries, WORKING_DIR_NAME, NON_WORKING_DIR_NAME, RESERVED_DIR_NAMES

APP_NAME = "File Health Checker"
CREDIT = "Made by CyberTech 2026"

# ---- pure-black cyber theme ------------------------------------------------
BG = "#000000"
CARD = "#0a0a0a"
BORDER = "#1f1f1f"
FIELD = "#050505"
TEXT = "#e6edf3"
MUTED = "#7d8590"
ACCENT = "#00d9ff"      # neon cyan
ACCENT2 = "#7c4dff"     # electric violet
GREEN = "#00ff9c"
RED = "#ff3b5c"
AMBER = "#ffc857"

STYLE_SHEET = f"""
QMainWindow, QWidget#root {{ background-color: {BG}; }}
QWidget {{ color: {TEXT}; font-family: 'Segoe UI', 'Inter', sans-serif; font-size: 13px; }}
QFrame#card {{ background-color: {CARD}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel {{ background: transparent; }}
QLabel#appTitle {{ font-size: 22px; font-weight: 800; letter-spacing: 2px; }}
QLabel#subtitle {{ color: {MUTED}; font-size: 12px; }}
QLabel#badge {{
    color: {ACCENT}; border: 1px solid {ACCENT}; border-radius: 10px;
    padding: 4px 12px; font-size: 11px; font-weight: 800; letter-spacing: 2px;
}}
QLabel#cardTitle {{ font-size: 12px; font-weight: 800; color: {ACCENT}; letter-spacing: 2px; }}
QLabel#pathLabel {{ color: {MUTED}; }}
QLabel#statTitle {{ color: {MUTED}; font-size: 10px; font-weight: 600; letter-spacing: 1px; }}
QLabel#statValue {{ font-size: 22px; font-weight: 800; }}
QLabel#credit {{ color: {MUTED}; font-size: 11px; letter-spacing: 2px; }}
QPushButton {{
    background-color: {ACCENT}; color: #000000; border: none; border-radius: 8px;
    padding: 9px 20px; font-weight: 800;
}}
QPushButton:hover {{ background-color: #5eeaff; }}
QPushButton:disabled {{ background-color: #141414; color: #4a4f57; }}
QPushButton#ghost {{
    background-color: transparent; border: 1px solid {BORDER}; color: {TEXT};
    padding: 5px 12px; font-weight: 600;
}}
QPushButton#ghost:hover {{ border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton#ghost:disabled {{ border-color: #141414; color: #4a4f57; background: transparent; }}
QPushButton#stopButton {{ background-color: {RED}; color: white; }}
QPushButton#stopButton:hover {{ background-color: #ff6680; }}
QPushButton#stopButton:disabled {{ background-color: #141414; color: #4a4f57; }}
QProgressBar {{
    border: 1px solid {BORDER}; border-radius: 8px; text-align: center;
    background-color: {FIELD}; height: 16px; color: white; font-weight: 700; font-size: 11px;
}}
QProgressBar::chunk {{
    border-radius: 7px;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {ACCENT}, stop:1 {ACCENT2});
}}
QTreeWidget {{
    background-color: {FIELD}; border: 1px solid {BORDER}; border-radius: 10px;
    padding: 4px; outline: none; alternate-background-color: #0a0a0a;
}}
QTreeWidget::item {{ padding: 3px 4px; border-radius: 5px; }}
QTreeWidget::item:hover {{ background-color: #101820; }}
QTreeWidget::item:selected {{ background-color: #0d2a33; color: {TEXT}; }}
QTreeView::indicator {{
    width: 15px; height: 15px; border: 2px solid #3a3f47; border-radius: 4px; background: {FIELD};
}}
QTreeView::indicator:hover {{ border-color: {ACCENT}; }}
QTreeView::indicator:checked {{ background-color: {ACCENT}; border-color: {ACCENT}; }}
QTreeView::indicator:indeterminate {{
    background-color: #006b7d; border-color: {ACCENT};
}}
QHeaderView::section {{
    background-color: {CARD}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 5px 8px; font-size: 11px; font-weight: 700; letter-spacing: 1px;
}}
QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 10px; background: {FIELD}; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 7px 18px; border: none;
    font-weight: 800; letter-spacing: 2px; font-size: 11px;
}}
QTabBar::tab:selected {{ color: {ACCENT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QRadioButton::indicator {{
    width: 14px; height: 14px; border: 2px solid #3a3f47; border-radius: 9px; background: {FIELD};
}}
QRadioButton::indicator:checked {{
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 {ACCENT}, stop:0.55 {ACCENT}, stop:0.6 {FIELD}, stop:1 {FIELD});
    border-color: {ACCENT};
}}
QRadioButton {{ spacing: 8px; }}
QTextEdit {{
    background-color: {FIELD}; border: none; color: #b6f3ff;
    font-family: 'Consolas', 'Cascadia Mono', monospace; font-size: 12px;
}}
QComboBox {{
    background-color: {FIELD}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 5px 10px; min-width: 150px;
}}
QComboBox:hover {{ border-color: {ACCENT}; }}
QComboBox QAbstractItemView {{ background-color: {FIELD}; selection-background-color: {ACCENT}; selection-color: #000; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: #262626; border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {ACCENT}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #262626; border-radius: 5px; min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QMessageBox {{ background-color: {CARD}; }}
QMessageBox QLabel {{ color: {TEXT}; font-size: 13px; }}
QToolTip {{ background-color: {CARD}; color: {TEXT}; border: 1px solid {ACCENT}; }}
"""

ROLE_PATH = Qt.UserRole          # full path on every item
ROLE_KIND = Qt.UserRole + 1      # "dir" or "file"


# ---------------------------------------------------------------- helpers --
def make_card(title=None):
    frame = QFrame()
    frame.setObjectName("card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(18, 14, 18, 16)
    lay.setSpacing(10)
    if title:
        t = QLabel(title)
        t.setObjectName("cardTitle")
        lay.addWidget(t)
    return frame, lay


def stat_block(title, color=TEXT):
    wrapper = QWidget()
    lay = QVBoxLayout(wrapper)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    t = QLabel(title)
    t.setObjectName("statTitle")
    v = QLabel("0")
    v.setObjectName("statValue")
    v.setStyleSheet(f"color: {color};")
    lay.addWidget(t)
    lay.addWidget(v)
    return wrapper, v


def human_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024


# ------------------------------------------------------------ splash screen --
class SplashScreen(QWidget):
    """Animated opening screen: glowing logo, spinning rings, loading bar."""
    finished = Signal()
    DURATION_MS = 3000

    STEPS = ["Starting engine...", "Loading validators...",
             "Preparing interface...", "Ready"]

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.SplashScreen)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(560, 400)
        self._t0 = time.time()
        self._p = 0.0
        self._closing = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center() - self.rect().center())
        self.setWindowOpacity(0.0)

    def start(self):
        self.show()
        self._fade = QPropertyAnimation(self, b"windowOpacity")
        self._fade.setDuration(500)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.start()
        self._t0 = time.time()
        self._timer.start(16)

    def _tick(self):
        self._p = min(1.0, (time.time() - self._t0) * 1000 / self.DURATION_MS)
        self.update()
        if self._p >= 1.0 and not self._closing:
            self._closing = True
            self._timer.stop()
            QTimer.singleShot(250, self._fade_out)

    def _fade_out(self):
        self._out = QPropertyAnimation(self, b"windowOpacity")
        self._out.setDuration(450)
        self._out.setStartValue(1.0)
        self._out.setEndValue(0.0)
        self._out.finished.connect(self._done)
        self._out.start()
        self.finished.emit()   # main window starts fading in while splash fades out

    def _done(self):
        self.close()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        t = time.time() - self._t0

        # card
        card = QRectF(20, 20, w - 40, h - 40)
        path = QPainterPath()
        path.addRoundedRect(card, 28, 28)
        g = QLinearGradient(0, 0, w, h)
        g.setColorAt(0, QColor("#0b0b0b"))
        g.setColorAt(1, QColor("#000000"))
        p.fillPath(path, QBrush(g))
        p.setPen(QPen(QColor(BORDER), 1.5))
        p.drawPath(path)

        cx, cy = w / 2, 150
        # soft glow
        pulse = 0.5 + 0.5 * math.sin(t * 3)
        rg = QRadialGradient(QPointF(cx, cy), 110)
        c = QColor(ACCENT)
        c.setAlpha(int(60 + 40 * pulse))
        rg.setColorAt(0, c)
        rg.setColorAt(1, QColor(0, 0, 0, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(rg))
        p.drawEllipse(QPointF(cx, cy), 110, 110)

        # spinning rings
        for radius, speed, color, width, span in (
            (74, 140, ACCENT, 4, 110), (62, -200, ACCENT2, 3, 80), (50, 260, GREEN, 3, 60)
        ):
            pen = QPen(QColor(color), width)
            pen.setCapStyle(Qt.RoundCap)
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            rect = QRectF(cx - radius, cy - radius, radius * 2, radius * 2)
            start = int(((t * speed) % 360) * 16)
            p.drawArc(rect, start, span * 16)
            p.drawArc(rect, start + 180 * 16, span * 16)

        # logo: shield with check, scales in
        k = min(1.0, t / 0.8)
        k = 1 - (1 - k) ** 3
        p.save()
        p.translate(cx, cy)
        p.scale(k, k)
        shield = QPainterPath()
        shield.moveTo(0, -30)
        shield.lineTo(26, -20)
        shield.lineTo(26, 4)
        shield.quadTo(26, 26, 0, 36)
        shield.quadTo(-26, 26, -26, 4)
        shield.lineTo(-26, -20)
        shield.closeSubpath()
        sg = QLinearGradient(-26, -30, 26, 36)
        sg.setColorAt(0, QColor(ACCENT))
        sg.setColorAt(1, QColor(ACCENT2))
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(sg))
        p.drawPath(shield)
        # animated check mark
        chk = min(1.0, max(0.0, (t - 0.6) / 0.6))
        pen = QPen(QColor("white"), 5)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        pts = [QPointF(-11, 4), QPointF(-3, 13), QPointF(13, -8)]
        seg1 = min(1.0, chk * 2)
        seg2 = max(0.0, chk * 2 - 1)
        p.drawLine(pts[0], pts[0] + (pts[1] - pts[0]) * seg1)
        if seg2 > 0:
            p.drawLine(pts[1], pts[1] + (pts[2] - pts[1]) * seg2)
        p.restore()

        # title (fades in)
        a = int(255 * min(1.0, max(0.0, (t - 0.4) / 0.7)))
        f = QFont("Segoe UI", 24, QFont.ExtraBold)
        f.setLetterSpacing(QFont.AbsoluteSpacing, 3)
        p.setFont(f)
        p.setPen(QColor(232, 236, 248, a))
        p.drawText(QRectF(0, 235, w, 40), Qt.AlignHCenter, "FILE HEALTH CHECKER")

        # status + progress
        step = self.STEPS[min(len(self.STEPS) - 1, int(self._p * len(self.STEPS)))]
        p.setFont(QFont("Segoe UI", 10))
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(0, 278, w, 20), Qt.AlignHCenter, step)

        bar = QRectF(110, 308, w - 220, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#1a1a1a"))
        p.drawRoundedRect(bar, 3, 3)
        fill = QRectF(bar.x(), bar.y(), bar.width() * self._p, bar.height())
        bg = QLinearGradient(fill.topLeft(), fill.topRight())
        bg.setColorAt(0, QColor(ACCENT))
        bg.setColorAt(1, QColor(ACCENT2))
        p.setBrush(QBrush(bg))
        p.drawRoundedRect(fill, 3, 3)

        f2 = QFont("Segoe UI", 9)
        f2.setLetterSpacing(QFont.AbsoluteSpacing, 1.5)
        p.setFont(f2)
        p.setPen(QColor(MUTED))
        p.drawText(QRectF(0, 336, w, 20), Qt.AlignHCenter, CREDIT.upper())


# -------------------------------------------------------------- main window --
ROLE_COUNT = Qt.UserRole + 2     # scannable files at/below a folder item


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME}  -  {CREDIT}")
        self.resize(980, 990)
        self.setMinimumSize(860, 760)
        self.setStyleSheet(STYLE_SHEET)

        self.selected_folder = ""
        self.worker = None
        self.last_summary = None
        self._busy = False          # guards recursive check-state updates
        self._file_items = []       # checkable file items (supported formats)
        self._dir_items = []        # every folder item, parents before children

        st = self.style()
        self._icon_dir = st.standardIcon(QStyle.SP_DirIcon)
        self._icon_file = st.standardIcon(QStyle.SP_FileIcon)

        self._build_ui()

    # ---------------------------------------------------------------- UI --
    def _build_ui(self):
        central = QWidget()
        central.setObjectName("root")
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(22, 18, 22, 12)
        outer.setSpacing(12)

        # header
        head = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(0)
        title = QLabel("FILE HEALTH CHECKER")
        title.setObjectName("appTitle")
        sub = QLabel("Scan a main folder and sort every file into Working / Non-Working "
                     "inside its own folder")
        sub.setObjectName("subtitle")
        titles.addWidget(title)
        titles.addWidget(sub)
        badge = QLabel("CYBERTECH 2026")
        badge.setObjectName("badge")
        head.addLayout(titles, stretch=1)
        head.addWidget(badge, alignment=Qt.AlignTop)
        outer.addLayout(head)

        # step 1 + 2: main folder and full folder / file tree
        pick_card, pick = make_card("1  SELECT MAIN FOLDER  >  2  TICK FOLDERS / FILES")
        row = QHBoxLayout()
        self.path_label = QLabel("No folder selected")
        self.path_label.setObjectName("pathLabel")
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.on_browse)
        row.addWidget(self.path_label, stretch=1)
        row.addWidget(browse_btn)
        pick.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["NAME", "TYPE", "SIZE / FILES"])
        self.tree.setAlternatingRowColors(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setMinimumHeight(200)
        hdr = self.tree.header()
        hdr.setStretchLastSection(False)
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.itemChanged.connect(self._on_item_changed)
        pick.addWidget(self.tree, stretch=1)

        sel_row = QHBoxLayout()
        all_btn = QPushButton("Select all")
        all_btn.setObjectName("ghost")
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn = QPushButton("Clear")
        none_btn.setObjectName("ghost")
        none_btn.clicked.connect(lambda: self._set_all(False))
        exp_btn = QPushButton("Expand all")
        exp_btn.setObjectName("ghost")
        exp_btn.clicked.connect(self.tree.expandAll)
        col_btn = QPushButton("Collapse")
        col_btn.setObjectName("ghost")
        col_btn.clicked.connect(self._collapse_to_first_level)
        self.sel_info = QLabel("Browse to your main folder, then tick it (or any folder / file inside it).")
        self.sel_info.setObjectName("pathLabel")
        for b in (all_btn, none_btn, exp_btn, col_btn):
            sel_row.addWidget(b)
        sel_row.addSpacing(8)
        sel_row.addWidget(self.sel_info, stretch=1)
        pick.addLayout(sel_row)
        outer.addWidget(pick_card, stretch=3)

        # options
        opt_card, opt = make_card("OPTIONS")
        orow = QHBoxLayout()
        mode_group = QButtonGroup(self)
        self.copy_radio = QRadioButton("Copy files (originals untouched)")
        self.move_radio = QRadioButton("Move files")
        self.copy_radio.setChecked(True)  # copy is the safer default
        mode_group.addButton(self.copy_radio)
        mode_group.addButton(self.move_radio)
        orow.addWidget(self.copy_radio)
        orow.addWidget(self.move_radio)
        orow.addSpacing(24)
        orow.addWidget(QLabel("On duplicate name:"))
        self.duplicate_combo = QComboBox()
        self.duplicate_combo.addItems([p.value for p in DuplicatePolicy])
        orow.addWidget(self.duplicate_combo)
        orow.addStretch()
        opt.addLayout(orow)
        outer.addWidget(opt_card)

        # controls
        controls = QHBoxLayout()
        self.start_btn = QPushButton("START SCAN")
        self.start_btn.clicked.connect(self.on_start)
        self.stop_btn = QPushButton("STOP")
        self.stop_btn.setObjectName("stopButton")
        self.stop_btn.clicked.connect(self.on_stop)
        self.stop_btn.setEnabled(False)
        self.export_btn = QPushButton("Export Report (CSV)")
        self.export_btn.setObjectName("ghost")
        self.export_btn.clicked.connect(self.on_export)
        self.export_btn.setEnabled(False)
        controls.addWidget(self.start_btn)
        controls.addWidget(self.stop_btn)
        controls.addWidget(self.export_btn)
        controls.addStretch()
        outer.addLayout(controls)

        # progress + stats
        stats_card, stats = make_card("PROGRESS")
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        stats.addWidget(self.progress_bar)
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        w1, self.val_total = stat_block("TOTAL FILES")
        w2, self.val_scanned = stat_block("SCANNED")
        w3, self.val_working = stat_block("WORKING", GREEN)
        w4, self.val_nonworking = stat_block("NON-WORKING", RED)
        w5, self.val_unsupported = stat_block("UNSUPPORTED / SKIPPED", AMBER)
        w6, self.val_speed = stat_block("SPEED (files/sec)")
        w7, self.val_eta = stat_block("ETA")
        for i, w in enumerate([w1, w2, w3, w4, w5, w6, w7]):
            grid.addWidget(w, i // 4, i % 4)
        stats.addLayout(grid)
        cur = QHBoxLayout()
        cur.addWidget(QLabel("Current file:"))
        self.current_file_label = QLabel("-")
        self.current_file_label.setObjectName("pathLabel")
        cur.addWidget(self.current_file_label, stretch=1)
        stats.addLayout(cur)
        outer.addWidget(stats_card)

        # log + results tabs
        self.tabs = QTabWidget()
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.results_tree = QTreeWidget()
        self.results_tree.setColumnCount(3)
        self.results_tree.setHeaderLabels(["FOLDER / FILE", "STATUS", "DETAILS"])
        self.results_tree.setUniformRowHeights(True)
        rh = self.results_tree.header()
        rh.setStretchLastSection(True)
        rh.setSectionResizeMode(0, QHeaderView.Interactive)
        self.results_tree.setColumnWidth(0, 420)
        self.tabs.addTab(self.log_box, "LOG")
        self.tabs.addTab(self.results_tree, "RESULTS BY FOLDER")
        self.tabs.setMinimumHeight(170)
        outer.addWidget(self.tabs, stretch=2)

        # footer
        credit = QLabel(CREDIT.upper())
        credit.setObjectName("credit")
        credit.setAlignment(Qt.AlignCenter)
        outer.addWidget(credit)

        for card in (pick_card, opt_card, stats_card):
            sh = QGraphicsDropShadowEffect(self)
            sh.setBlurRadius(24)
            sh.setOffset(0, 4)
            sh.setColor(QColor(0, 229, 255, 28))
            card.setGraphicsEffect(sh)

    # ------------------------------------------------------- folder tree --
    def _new_item(self, parent, name, kind, path):
        item = QTreeWidgetItem(parent) if parent is not None else QTreeWidgetItem(self.tree)
        item.setText(0, name)
        item.setData(0, ROLE_PATH, path)
        item.setData(0, ROLE_KIND, kind)
        item.setIcon(0, self._icon_dir if kind == "dir" else self._icon_file)
        return item

    def _fill_dir(self, dir_item, path, readonly=False):
        """Add every sub-folder and file under `path` to the tree. Returns the
        number of scannable (supported-format) files at or below it.

        readonly=True is used inside 'Working Files' / 'Non-Working Files':
        those items are shown (so you can see where files ended up) but have no
        checkbox, so already-sorted files are never scanned again."""
        scannable = 0
        try:
            dirs, files = list_dir_entries(path, include_output=True)
        except OSError:
            dir_item.setText(1, "No access")
            dir_item.setForeground(0, QBrush(QColor(MUTED)))
            dir_item.setData(0, ROLE_COUNT, 0)
            return 0

        for name, full in dirs:
            child = self._new_item(dir_item, name, "dir", full)
            is_out = name in RESERVED_DIR_NAMES
            if is_out:
                color = GREEN if name == WORKING_DIR_NAME else RED
                child.setText(1, "Sorted output")
                child.setForeground(0, QBrush(QColor(color)))
                n = self._fill_dir(child, full, readonly=True)
                child.setText(2, f"{n} file{'s' if n != 1 else ''}")
                child.setData(0, ROLE_COUNT, 0)
                child.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
                child.setForeground(0, QBrush(QColor(color)))
                continue
            child.setText(1, "Folder")
            self._dir_items.append(child)
            scannable += self._fill_dir(child, full, readonly=readonly)

        for name, full in files:
            f = self._new_item(dir_item, name, "file", full)
            ext = os.path.splitext(name)[1].lower()
            category = EXT_TO_CATEGORY.get(ext)
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            f.setText(2, human_size(size))
            f.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
            if readonly:
                f.setText(1, category or "Other")
                f.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)  # view-only
                scannable += 1
            elif category:
                f.setText(1, category)
                f.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
                f.setCheckState(0, Qt.Unchecked)
                self._file_items.append(f)
                scannable += 1
            else:
                f.setText(1, "Unsupported")
                f.setForeground(0, QBrush(QColor(MUTED)))
                f.setForeground(1, QBrush(QColor(MUTED)))
                f.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)  # no checkbox

        dir_item.setData(0, ROLE_COUNT, scannable)
        dir_item.setText(2, f"{scannable} file{'s' if scannable != 1 else ''}")
        dir_item.setTextAlignment(2, Qt.AlignRight | Qt.AlignVCenter)
        if readonly:
            dir_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            return scannable
        if scannable:
            dir_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable)
            dir_item.setCheckState(0, Qt.Unchecked)
        else:
            dir_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
            dir_item.setForeground(0, QBrush(QColor(MUTED)))
        return scannable

    def _populate_tree(self, restore_paths=None):
        self.tree.blockSignals(True)
        self.tree.setUpdatesEnabled(False)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.tree.clear()
            self._file_items = []
            self._dir_items = []
            root_name = os.path.basename(os.path.normpath(self.selected_folder)) or self.selected_folder
            root = self._new_item(None, f"{root_name}   (main folder)", "dir", self.selected_folder)
            root.setText(1, "Main folder")
            self._dir_items.append(root)
            self._fill_dir(root, self.selected_folder)
            if restore_paths:
                for f in self._file_items:
                    if f.data(0, ROLE_PATH) in restore_paths:
                        f.setCheckState(0, Qt.Checked)
                for d in reversed(self._dir_items):      # children before parents
                    self._recompute(d)
            self._collapse_to_first_level()
        finally:
            QApplication.restoreOverrideCursor()
            self.tree.setUpdatesEnabled(True)
            self.tree.blockSignals(False)
        self._update_selection_info()

    def _collapse_to_first_level(self):
        self.tree.collapseAll()
        root = self.tree.topLevelItem(0)
        if root is not None:
            root.setExpanded(True)

    # ---- tri-state check logic (folder <-> children) ----
    @staticmethod
    def _is_checkable(item):
        return bool(item.flags() & Qt.ItemIsUserCheckable)

    def _set_subtree(self, item, state):
        for i in range(item.childCount()):
            c = item.child(i)
            if not self._is_checkable(c):
                continue
            c.setCheckState(0, state)
            if c.data(0, ROLE_KIND) == "dir":
                self._set_subtree(c, state)

    def _recompute(self, item):
        """Set a folder's check state from its checkable children."""
        if item.data(0, ROLE_KIND) != "dir" or not self._is_checkable(item):
            return
        states = {item.child(i).checkState(0) for i in range(item.childCount())
                  if self._is_checkable(item.child(i))}
        if not states:
            return
        if states == {Qt.Checked}:
            new = Qt.Checked
        elif states == {Qt.Unchecked}:
            new = Qt.Unchecked
        else:
            new = Qt.PartiallyChecked
        if item.checkState(0) != new:
            item.setCheckState(0, new)

    def _on_item_changed(self, item, column):
        if column != 0 or self._busy:
            return
        self._busy = True
        try:
            if item.data(0, ROLE_KIND) == "dir":
                st = item.checkState(0)
                if st != Qt.PartiallyChecked:
                    self._set_subtree(item, st)
            p = item.parent()
            while p is not None:
                self._recompute(p)
                p = p.parent()
        finally:
            self._busy = False
        self._update_selection_info()

    def _set_all(self, checked):
        root = self.tree.topLevelItem(0)
        if root is None:
            return
        state = Qt.Checked if checked else Qt.Unchecked
        self._busy = True
        try:
            self._set_subtree(root, state)
            if self._is_checkable(root):
                root.setCheckState(0, state)
        finally:
            self._busy = False
        self._update_selection_info()

    def _checked_files(self):
        return [f.data(0, ROLE_PATH) for f in self._file_items
                if f.checkState(0) == Qt.Checked]

    def _update_selection_info(self, *_):
        if self.tree.topLevelItemCount() == 0:
            self.sel_info.setText("Browse to your main folder, then tick it "
                                  "(or any folder / file inside it).")
            return
        files = self._checked_files()
        if not files:
            self.sel_info.setText(f"{len(self._file_items)} scannable files found. "
                                  "Tick the main folder, a sub-folder, or single files.")
            return
        folders = {os.path.dirname(p) for p in files}
        self.sel_info.setText(f"{len(files)} of {len(self._file_items)} files selected "
                              f"in {len(folders)} folder{'s' if len(folders) != 1 else ''}")

    # ------------------------------------------------------------ actions --
    def on_browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Select main folder")
        if folder:
            self.selected_folder = folder
            self.path_label.setText(folder)
            self._populate_tree()

    def on_start(self):
        if not self.selected_folder:
            QMessageBox.warning(self, "No folder", "Please select a main folder first.")
            return
        if not os.path.isdir(self.selected_folder):
            QMessageBox.warning(self, "Invalid folder", "The selected folder no longer exists.")
            return
        files = self._checked_files()
        if not files:
            QMessageBox.warning(self, "Nothing selected",
                                "Please tick the main folder, a folder, or at least one file.")
            return

        mode = FileMode.MOVE if self.move_radio.isChecked() else FileMode.COPY
        folders = sorted({os.path.dirname(p) for p in files}, key=str.lower)
        preview = "\n".join(
            f"  - {os.path.relpath(d, self.selected_folder) if d != self.selected_folder else '(main folder)'}"
            for d in folders[:10])
        if len(folders) > 10:
            preview += f"\n  ... and {len(folders) - 10} more"
        confirm_msg = (
            f"This will scan {len(files)} file(s) in {len(folders)} folder(s):\n{preview}\n\n"
            f"Mode: {mode.value}\n"
            "Check type: Thorough (100% full check -- every video frame start "
            "to end, strict image truncation detection, full RAW/PDF/spreadsheet "
            "read)\n"
            "Each file is sorted into the 'Working Files' or 'Non-Working Files' "
            "folder INSIDE the folder it lives in. Nothing else is changed.\n\nContinue?"
        )
        if QMessageBox.question(self, "Confirm scan", confirm_msg) != QMessageBox.Yes:
            return

        policy = DuplicatePolicy(self.duplicate_combo.currentText())

        self.log_box.clear()
        self.results_tree.clear()
        self.tabs.setCurrentIndex(0)
        self.progress_bar.setValue(0)
        self.export_btn.setEnabled(False)
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)

        self.worker = ScannerWorker([], mode, policy, thorough=True, files=files)
        self.worker.progress.connect(self.on_progress)
        self.worker.finished_scan.connect(self.on_finished)
        self.worker.start()

    def on_stop(self):
        if self.worker:
            self.worker.stop()
            self.log_box.append("Stopping... finishing in-flight files.")
        self.stop_btn.setEnabled(False)

    def on_progress(self, payload: dict):
        total = payload["total"]
        scanned = payload["scanned"]
        self.val_total.setText(str(total))
        self.val_scanned.setText(str(scanned))
        self.val_working.setText(str(payload["working"]))
        self.val_nonworking.setText(str(payload["non_working"]))
        self.val_unsupported.setText(str(payload["unsupported"] + payload["errors"]))
        self.val_speed.setText(f"{payload['speed']:.1f}")
        self.val_eta.setText(time.strftime("%M:%S", time.gmtime(payload["eta_seconds"])))
        self.current_file_label.setText(payload["current_file"] or "-")

        pct = int((scanned / total) * 100) if total else 0
        self.progress_bar.setValue(pct)

    def on_finished(self, summary):
        self.last_summary = summary
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.export_btn.setEnabled(True)
        self.progress_bar.setValue(100 if summary.scanned == summary.total else self.progress_bar.value())

        mins, secs = divmod(int(summary.elapsed_seconds), 60)
        self.log_box.append("\n" + "=" * 50)
        self.log_box.append("SCAN COMPLETED")
        self.log_box.append(f"Total Files:        {summary.total}")
        self.log_box.append(f"Working Files:      {summary.working}")
        self.log_box.append(f"Non-Working Files:  {summary.non_working}")
        self.log_box.append(f"Unsupported Files:  {summary.unsupported}  (left in original location -- not moved)")
        self.log_box.append(f"Error Files:        {summary.errors}")
        self.log_box.append(f"Scan Time:          {mins:02d}:{secs:02d}")

        failures = [r for r in summary.results if r.status.value not in ("Working", "Unsupported")]
        if failures:
            self.log_box.append("\nSample issues:")
            for r in failures[:25]:
                self.log_box.append(f"  {r.filename}: {r.status.value} - {r.reason}")

        self._build_results_tree(summary)
        self.tabs.setCurrentIndex(1)

        # refresh the picker (files may have moved) and keep the user's ticks
        if self.selected_folder and os.path.isdir(self.selected_folder):
            self._populate_tree(restore_paths=set(self._checked_files()))

    # ------------------------------------------------------ results view --
    def _build_results_tree(self, summary):
        """Folder -> Working / Non-Working -> files, so it's clear exactly where
        every file of every folder ended up."""
        tree = self.results_tree
        tree.setUpdatesEnabled(False)
        tree.clear()
        try:
            groups = {}
            for r in summary.results:
                folder = os.path.dirname(r.path)
                if r.status.value == "Working":
                    bucket = "w"
                elif r.status.value == "Unsupported":
                    bucket = "u"
                else:
                    bucket = "n"
                groups.setdefault(folder, {"w": [], "n": [], "u": []})[bucket].append(r)

            labels = {"w": (f"{WORKING_DIR_NAME}", GREEN),
                      "n": (f"{NON_WORKING_DIR_NAME}", RED),
                      "u": ("Left in place (unsupported)", AMBER)}
            for folder in sorted(groups, key=str.lower):
                rel = os.path.relpath(folder, self.selected_folder) if self.selected_folder else folder
                name = "(main folder)" if rel == "." else rel
                g = groups[folder]
                fi = QTreeWidgetItem(tree, [name, "", f"{sum(len(v) for v in g.values())} files"])
                fi.setIcon(0, self._icon_dir)
                for key in ("w", "n", "u"):
                    rows = g[key]
                    if not rows:
                        continue
                    text, color = labels[key]
                    bi = QTreeWidgetItem(fi, [f"{text}  ({len(rows)})", "", ""])
                    bi.setIcon(0, self._icon_dir)
                    bi.setForeground(0, QBrush(QColor(color)))
                    for r in sorted(rows, key=lambda x: x.filename.lower()):
                        li = QTreeWidgetItem(bi, [r.filename, r.status.value, r.reason])
                        li.setIcon(0, self._icon_file)
                        li.setForeground(1, QBrush(QColor(color)))
                fi.setExpanded(True)
        finally:
            tree.setUpdatesEnabled(True)

    def on_export(self):
        if not self.last_summary:
            return
        dest, _ = QFileDialog.getSaveFileName(self, "Export report", "scan_report.csv", "CSV Files (*.csv)")
        if not dest:
            return
        try:
            export_csv(self.last_summary, dest)
            QMessageBox.information(self, "Report exported", f"Report saved to:\n{dest}")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Export failed", str(exc))


def _apply_dark_palette(app):
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.Base, QColor(FIELD))
    pal.setColor(QPalette.AlternateBase, QColor(CARD))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(CARD))
    pal.setColor(QPalette.ToolTipBase, QColor(CARD))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("#000000"))
    app.setPalette(pal)


def run_app():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    _apply_dark_palette(app)

    win = MainWindow()
    win.setWindowOpacity(0.0)

    splash = SplashScreen()

    def open_main():
        win.show()
        win.raise_()
        win.activateWindow()
        anim = QPropertyAnimation(win, b"windowOpacity", win)
        anim.setDuration(600)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        win._open_anim = anim  # keep a reference

    splash.finished.connect(open_main)
    splash.start()
    app.exec()
