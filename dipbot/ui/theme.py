"""Shared desktop palette and control states."""

from pathlib import Path

# Semantic tokens shared by Qt styles and custom painting.
COLORS = {
    "background": "#101419",
    "surface": "#171d25",
    "raised": "#202936",
    "border": "#344252",
    "text": "#e8edf3",
    "muted": "#adbacb",
    "accent": "#8bc4ff",
    "positive": "#71e0bc",
    "danger": "#ffabb5",
    "warning": "#f4c76b",
    "entry": "#b8b2ff",
    "grid": "#293441",
}
METRICS = {"page": 12, "gap": 8, "control_height": 18, "chart_height": 200}

STYLE = """
QWidget { background: @BACKGROUND@; color: @TEXT@; font-size: 13px; }
QLabel { background: transparent; }
QLabel#title { font-size: 17px; font-weight: 700; }
QLabel#muted { color: @MUTED@; font-size: 12px; }
QWidget#metricCard { background: @SURFACE@; border: 1px solid @BORDER@; border-radius: 5px; }
QLabel#metricCaption { color: @MUTED@; font-size: 11px; font-weight: 600; }
QLabel#metric { color: @TEXT@; font-size: 18px; font-weight: 600; }
QLabel#metric[tone="positive"] { color: @POSITIVE@; }
QLabel#metric[tone="danger"] { color: @DANGER@; }
QLabel#banner { background: #172638; color: #b7d9f7; border: 1px solid #2c435c; border-radius: 5px; padding: 5px 8px; }
QLabel#banner[mode="LIVE"] { background: #33202b; color: #ffc1c8; border-color: #71404f; }
QGroupBox { background: @SURFACE@; border: 1px solid @BORDER@; border-radius: 5px; margin-top: 8px; padding: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #b3c2d3; font-size: 11px; font-weight: 600; }
QLineEdit, QComboBox, QDoubleSpinBox { background: #0f1724; color: @TEXT@; border: 1px solid #37465b; border-radius: 6px; padding: 4px 8px; min-height: 18px; selection-background-color: #275f60; }
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus { border-color: @POSITIVE@; }
QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled { background: #131b27; color: #8190a4; border-color: #283445; }
QComboBox { padding-right: 26px; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox::down-arrow { image: url("@CHEVRON@"); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background: #1b2738; selection-background-color: #2a4658; selection-color: #ffffff; border: 1px solid #43536b; padding: 4px; }
QPushButton { background: @RAISED@; color: #dce6f1; border: 1px solid #3b4b61; border-radius: 6px; padding: 5px 10px; min-height: 18px; font-weight: 600; }
QPushButton:hover { background: #293c54; border-color: #6b819c; }
QPushButton:pressed { background: #132135; }
QPushButton:focus { border-color: #91c9fc; }
QPushButton#primary { background: @POSITIVE@; border-color: @POSITIVE@; color: #0c2b25; }
QPushButton#primary:hover { background: #94edce; }
QPushButton#primary:pressed { background: #51c39f; }
QPushButton#danger { background: #34212d; color: #ffb5c0; border-color: #774758; }
QPushButton#danger:hover { background: #4c2c3b; border-color: #b16a7b; }
QPushButton:disabled, QPushButton#primary:disabled, QPushButton#danger:disabled { background: #151e2b; color: #74839a; border-color: #293447; }
QPushButton#journal { text-align: left; background: transparent; border: 0; color: @MUTED@; padding: 2px 0; min-height: 20px; font-size: 12px; }
QPushButton#journal:focus { border: 1px solid #91c9fc; }
QPlainTextEdit, QTableWidget { background: #101927; border: 1px solid @BORDER@; border-radius: 5px; selection-background-color: #294858; }
QPlainTextEdit { padding: 8px; font-size: 12px; }
QTableWidget { alternate-background-color: #162233; gridline-color: #253246; }
QTableWidget::item { padding: 7px; border: 0; }
QTableWidget::item:selected { background: #294858; color: #ffffff; }
QHeaderView::section { background: #1b2738; color: #b8c6d8; padding: 10px; border: 0; border-bottom: 1px solid #344258; font-weight: 600; }
QTabWidget::pane { border: 0; }
QTabWidget::tab-bar { left: 0px; }
QTabBar { background: @BACKGROUND@; }
QTabBar::tab { background: @BACKGROUND@; color: @MUTED@; padding: 7px 14px; margin-right: 4px; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { color: @TEXT@; background: #192436; }
QTabBar::tab:selected { color: @POSITIVE@; border-bottom-color: @POSITIVE@; background: #192436; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #44536b; border-radius: 3px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: #677e9c; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QCheckBox { spacing: 8px; background: transparent; }
QCheckBox::indicator { width: 16px; height: 16px; border: 1px solid #8293ac; border-radius: 3px; background: #101a29; }
QCheckBox::indicator:checked { background: #6cddbf; border-color: #6cddbf; image: url("@CHECKMARK@"); }
QCheckBox::indicator:disabled { border-color: #44536b; }
QCheckBox::indicator:checked:disabled { background: #496a66; }
QToolTip { color: #f0f5fc; background: #25354b; border: 1px solid #576e8b; padding: 7px; }
"""

STYLE = STYLE.replace("@CHEVRON@", (Path(__file__).parent.parent / "assets" / "chevron-down.svg").as_posix())

STYLE = STYLE.replace("@CHECKMARK@", (Path(__file__).parent.parent / "assets" / "checkmark.svg").as_posix())

STYLE += """
QLabel#modeBadge { padding: 4px 9px; border-radius: 4px; font-weight: 600; color: #8bc4ff; background: #202e40; }
QLabel#modeBadge[mode="LIVE"] { color: #ffabb5; background: #402631; border: 1px solid #895060; }
QLabel#modeBadge[mode="DEMO"] { color: #c5bfdc; background: #292535; }
QLabel#banner[tone="warning"] { color: #f4c76b; border-color: #78613b; background: #29261f; }
QLabel#banner[tone="danger"] { color: #ffabb5; border-color: #895060; background: #30212a; }
QLabel#metric[tone="warning"] { color: #f4c76b; }
QLabel#muted[tone="warning"] { color: #f4c76b; }
QPushButton#disclosure { text-align: left; background: transparent; border: 1px solid #344252; font-weight: 500; }
QPushButton#disclosure:hover, QPushButton#disclosure:checked { background: #202936; }
QPushButton#disclosure:focus, QCheckBox:focus, QTabBar::tab:focus { border: 1px solid #8bc4ff; }
QLabel#footer { color: #adbacb; font-size: 12px; padding-top: 4px; }
"""
for name, value in COLORS.items():
    STYLE = STYLE.replace("@" + name.upper() + "@", value)
