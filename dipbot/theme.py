"""Shared desktop palette and control states."""
from pathlib import Path

STYLE = """
QWidget { background: #0e1420; color: #e6edf5; font-size: 13px; }
QLabel { background: transparent; }
QLabel#title { font-size: 23px; font-weight: 700; }
QLabel#muted { color: #a5b3c5; font-size: 12px; }
QWidget#metricCard { background: #161f2e; border: 1px solid #2a3546; border-radius: 10px; }
QLabel#metricCaption { color: #a5b3c5; font-size: 11px; font-weight: 600; }
QLabel#metric { color: #e6edf5; font-size: 22px; font-weight: 600; }
QLabel#metric[tone="positive"] { color: #71e0bc; }
QLabel#metric[tone="danger"] { color: #ffabb5; }
QLabel#banner { background: #172638; color: #b7d9f7; border: 1px solid #2c435c; border-radius: 8px; padding: 8px 12px; }
QLabel#banner[mode="LIVE"] { background: #33202b; color: #ffc1c8; border-color: #71404f; }
QGroupBox { background: #121b29; border: 1px solid #2a3546; border-radius: 9px; margin-top: 10px; padding: 12px; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #b3c2d3; font-size: 11px; font-weight: 600; }
QLineEdit, QComboBox, QDoubleSpinBox { background: #0f1724; color: #e6edf5; border: 1px solid #37465b; border-radius: 6px; padding: 6px 9px; min-height: 20px; selection-background-color: #275f60; }
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus { border-color: #71e0bc; }
QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled { background: #131b27; color: #8190a4; border-color: #283445; }
QComboBox { padding-right: 26px; }
QComboBox::drop-down { border: 0; width: 24px; }
QComboBox::down-arrow { image: url("@CHEVRON@"); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background: #1b2738; selection-background-color: #2a4658; selection-color: #ffffff; border: 1px solid #43536b; padding: 4px; }
QPushButton { background: #1c2a3d; color: #dce6f1; border: 1px solid #3b4b61; border-radius: 6px; padding: 7px 12px; min-height: 20px; font-weight: 600; }
QPushButton:hover { background: #293c54; border-color: #6b819c; }
QPushButton:pressed { background: #132135; }
QPushButton:focus { border-color: #91c9fc; }
QPushButton#primary { background: #71e0bc; border-color: #71e0bc; color: #0c2b25; }
QPushButton#primary:hover { background: #94edce; }
QPushButton#primary:pressed { background: #51c39f; }
QPushButton#danger { background: #34212d; color: #ffb5c0; border-color: #774758; }
QPushButton#danger:hover { background: #4c2c3b; border-color: #b16a7b; }
QPushButton:disabled, QPushButton#primary:disabled, QPushButton#danger:disabled { background: #151e2b; color: #74839a; border-color: #293447; }
QPushButton#journal { text-align: left; background: transparent; border: 0; color: #a5b3c5; padding: 2px 0; min-height: 20px; font-size: 12px; }
QPushButton#journal:focus { border: 1px solid #91c9fc; }
QPlainTextEdit, QTableWidget { background: #101927; border: 1px solid #2a3546; border-radius: 8px; selection-background-color: #294858; }
QPlainTextEdit { padding: 8px; font-size: 12px; }
QTableWidget { alternate-background-color: #162233; gridline-color: #253246; }
QTableWidget::item { padding: 7px; border: 0; }
QTableWidget::item:selected { background: #294858; color: #ffffff; }
QHeaderView::section { background: #1b2738; color: #b8c6d8; padding: 10px; border: 0; border-bottom: 1px solid #344258; font-weight: 600; }
QTabWidget::pane { border: 0; }
QTabWidget::tab-bar { left: 0px; }
QTabBar { background: #0e1420; }
QTabBar::tab { background: #0e1420; color: #a5b3c5; padding: 10px 18px; margin-right: 4px; border-bottom: 2px solid transparent; }
QTabBar::tab:hover { color: #e6edf5; background: #192436; }
QTabBar::tab:selected { color: #71e0bc; border-bottom-color: #71e0bc; background: #192436; }
QScrollArea { border: 0; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #44536b; border-radius: 3px; min-height: 32px; }
QScrollBar::handle:vertical:hover { background: #677e9c; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QCheckBox { spacing: 8px; background: transparent; }
QToolTip { color: #f0f5fc; background: #25354b; border: 1px solid #576e8b; padding: 7px; }
"""

STYLE = STYLE.replace("@CHEVRON@", (Path(__file__).parent / "assets" / "chevron-down.svg").as_posix())
