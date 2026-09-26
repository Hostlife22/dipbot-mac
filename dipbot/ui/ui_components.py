"""Small native Qt presentation helpers; no trading state or side effects."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetricsF, QPainter
from PySide6.QtWidgets import QLabel, QSizePolicy


def set_tone(widget, tone):
    if widget.property("tone") != tone:
        widget.setProperty("tone", tone)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


class MetricLabel(QLabel):
    """Keep full accessible text while fitting narrow financial readouts."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)

    def paintEvent(self, event):
        painter = QPainter(self)
        font = self.font()
        size = font.pixelSize() if font.pixelSize() > 0 else QFontMetricsF(font).height()
        width = self.contentsRect().width()
        while QFontMetricsF(font).horizontalAdvance(self.text()) > width and size > 12:
            size -= 1
            font.setPixelSize(int(size))
        painter.setFont(font)
        painter.setPen(self.palette().color(self.foregroundRole()))
        text = QFontMetricsF(font).elidedText(self.text(), Qt.ElideMiddle, width)
        painter.drawText(self.contentsRect(), Qt.AlignLeft | Qt.AlignVCenter, text)
