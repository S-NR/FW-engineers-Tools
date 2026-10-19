"""Dark / light themes and shared colours."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class ThemeColors:
    name: str
    window: str
    base: str
    alt_base: str
    panel: str
    border: str
    text: str
    muted: str
    accent: str
    ok: str
    warn: str
    error: str
    undefined: str
    diff: str
    field_colors: tuple[str, ...]


DARK = ThemeColors(
    "dark", window="#1e2127", base="#16181d", alt_base="#1b1e24", panel="#252931", border="#3a3f4b",
    text="#d7dae0", muted="#8b93a1", accent="#4fa3f7", ok="#3fb950", warn="#d29922", error="#f85149",
    undefined="#5a4b4b", diff="#8a3b12",
    field_colors=("#1f4e79", "#2e5e3a", "#5b3f7a", "#6b4a1f", "#205f63", "#6a2f45", "#47566b", "#4f5e1f"),
)

LIGHT = ThemeColors(
    "light", window="#f3f4f6", base="#ffffff", alt_base="#f7f8fa", panel="#e8eaee", border="#c5cad3",
    text="#1f2328", muted="#5f6772", accent="#0969da", ok="#1a7f37", warn="#9a6700", error="#cf222e",
    undefined="#f2d7d5", diff="#ffd8a8",
    field_colors=("#cfe3f7", "#d4edda", "#e5dcf3", "#f6e3c6", "#cdeeee", "#f5d5df", "#dde3ec", "#e6edc9"),
)

_current: ThemeColors = DARK


def colors() -> ThemeColors:
    return _current


def field_color(index: int) -> QColor:
    palette = _current.field_colors
    return QColor(palette[index % len(palette)]) if index >= 0 else QColor(_current.undefined)


def mono_font(size: int = 11) -> QFont:
    font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
    for family in ("JetBrains Mono", "Cascadia Mono", "Consolas", "DejaVu Sans Mono", "Menlo", "Courier New"):
        if family in QFontDatabase.families():
            font = QFont(family)
            break
    font.setStyleHint(QFont.StyleHint.Monospace)
    font.setPointSize(size)
    return font


def _qss(c: ThemeColors) -> str:
    return f"""
    QWidget {{ color: {c.text}; }}
    QMainWindow, QDialog {{ background: {c.window}; }}
    QToolTip {{ background: {c.panel}; color: {c.text}; border: 1px solid {c.border}; padding: 4px; }}
    QLabel#SectionHeader {{
        font-weight: 600; letter-spacing: 1px; color: {c.muted};
        padding: 4px 2px 2px 2px; border-bottom: 1px solid {c.border};
    }}
    QLabel#StatusIndicator {{ padding: 4px 10px; border-radius: 4px; font-weight: 600; }}
    QFrame#Panel {{ background: {c.panel}; border: 1px solid {c.border}; border-radius: 4px; }}
    QPlainTextEdit, QTextEdit, QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
        background: {c.base}; border: 1px solid {c.border}; border-radius: 3px;
        selection-background-color: {c.accent}; selection-color: #ffffff; padding: 2px;
    }}
    QPlainTextEdit:focus, QTextEdit:focus, QLineEdit:focus {{ border: 1px solid {c.accent}; }}
    QTableView, QTableWidget, QListWidget, QTreeWidget {{
        background: {c.base}; alternate-background-color: {c.alt_base};
        gridline-color: {c.border}; border: 1px solid {c.border};
        selection-background-color: {c.accent}; selection-color: #ffffff;
    }}
    QHeaderView::section {{
        background: {c.panel}; color: {c.muted}; border: none; border-right: 1px solid {c.border};
        border-bottom: 1px solid {c.border}; padding: 4px 6px; font-weight: 600;
    }}
    QPushButton, QToolButton {{
        background: {c.panel}; border: 1px solid {c.border}; border-radius: 3px; padding: 4px 12px;
    }}
    QPushButton:hover, QToolButton:hover {{ border-color: {c.accent}; }}
    QPushButton:pressed, QToolButton:pressed {{ background: {c.border}; }}
    QPushButton:disabled {{ color: {c.muted}; }}
    QPushButton#Primary {{ background: {c.accent}; color: #ffffff; border-color: {c.accent}; font-weight: 600; }}
    QTabWidget::pane {{ border: 1px solid {c.border}; top: -1px; }}
    QTabBar::tab {{
        background: {c.window}; border: 1px solid {c.border}; padding: 6px 14px; margin-right: 2px;
        border-top-left-radius: 3px; border-top-right-radius: 3px; color: {c.muted};
    }}
    QTabBar::tab:selected {{ background: {c.panel}; color: {c.text}; border-bottom-color: {c.panel}; }}
    QSplitter::handle {{ background: {c.border}; }}
    QSplitter::handle:horizontal {{ width: 3px; }}
    QSplitter::handle:vertical {{ height: 3px; }}
    QGroupBox {{ border: 1px solid {c.border}; border-radius: 4px; margin-top: 14px; padding-top: 6px; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px; color: {c.muted}; }}
    QStatusBar {{ background: {c.panel}; border-top: 1px solid {c.border}; }}
    QMenuBar {{ background: {c.window}; }}
    QMenuBar::item:selected, QMenu::item:selected {{ background: {c.accent}; color: #ffffff; }}
    QMenu {{ background: {c.panel}; border: 1px solid {c.border}; }}
    QScrollArea {{ border: none; }}
    """


def apply_theme(app: QApplication, name: str) -> ThemeColors:
    global _current
    _current = LIGHT if name == "light" else DARK
    c = _current
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, QColor(c.window))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(c.text))
    pal.setColor(QPalette.ColorRole.Base, QColor(c.base))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(c.alt_base))
    pal.setColor(QPalette.ColorRole.Text, QColor(c.text))
    pal.setColor(QPalette.ColorRole.Button, QColor(c.panel))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(c.text))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(c.accent))
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(c.panel))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(c.text))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(c.muted))
    pal.setColor(QPalette.ColorRole.Link, QColor(c.accent))
    app.setPalette(pal)
    app.setStyleSheet(_qss(c))
    return c
