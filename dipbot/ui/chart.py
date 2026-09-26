"""Price chart rendering; no trading or startup responsibilities."""
from collections import deque
import time

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import QWidget

from dipbot.domain.usd import price_text


from dipbot.ui.theme import COLORS, METRICS


class Chart(QWidget):
    def __init__(self):
        super().__init__()
        self.values = deque(maxlen=180)
        self.times = deque(maxlen=180)
        self.levels = {}
        self.reference_base = None
        self.usd_rate = None
        self.markers = deque(maxlen=180)
        self.hover = None
        self.setToolTip("Вход* — опорная цена стратегии, не средняя цена сделки. BUY/SELL отмечают завершённые операции по цене сигнала/наблюдения, не цене исполнения LIVE. Наведите курсор для просмотра цены и относительного времени.")
        self.setMouseTracking(True)
        self.setMinimumHeight(METRICS["chart_height"])

    def mark(self, label, price):
        if self.times:
            self.markers.append((self.times[-1], label, float(price)))
            self.update()

    def mouseMoveEvent(self, event):
        self.hover = event.position()
        self.update()

    def leaveEvent(self, event):
        self.hover = None
        self.update()

    def add(self, value):
        self.values.append(float(value))
        self.times.append(time.monotonic())
        self.update()

    def clear(self):
        self.values.clear()
        self.times.clear()
        self.levels.clear()
        self.reference_base = None
        self.markers.clear()
        self.hover = None
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        painter.setPen(QPen(QColor(COLORS['border']), 1))
        painter.setBrush(QColor(COLORS['surface']))
        painter.drawRoundedRect(self.rect().adjusted(0, 0, -1, -1), 9, 9)
        painter.setBrush(Qt.NoBrush)
        if len(self.values) < 2:
            painter.setPen(QColor("#93a6bb"))
            painter.drawText(self.rect(), Qt.AlignCenter, "График появится после START / выбора пула")
            return
        levels = dict(self.levels)
        if self.reference_base is not None and 'DIP' in levels:
            levels['BASE'] = self.reference_base
        levels['PRICE'] = self.values[-1]
        bounds = list(self.values) + [float(v) for v in levels.values() if float(v) > 0]
        bounds += [v for stamp, _, v in self.markers if stamp >= self.times[0]]
        lo, hi = min(bounds), max(bounds)
        padding = (hi-lo)*.1 if hi != lo else max(abs(hi)*.01, 1e-30)
        lo, hi = max(0, lo-padding), hi+padding
        spread = hi-lo
        font = painter.font()
        font.setPixelSize(11)
        painter.setFont(font)
        fm = painter.fontMetrics()
        axis_width = min(145, max(100, fm.horizontalAdvance(price_text(hi, self.usd_rate, 6)) + 20))
        left, right, top, bottom = axis_width, max(axis_width+40, w-175), 25, h-28
        def y(value):
            return bottom-(bottom-top)*(value-lo)/spread
        elapsed = max(self.times[-1]-self.times[0], .001)
        painter.setPen(QColor('#93a6bb'))
        for index in range(4):
            value = lo + spread*index/3
            py = y(value)
            painter.setPen(QPen(QColor(COLORS['grid']), 1))
            painter.drawLine(QPointF(left, py), QPointF(right, py))
            painter.setPen(QColor(COLORS['muted']))
            painter.drawText(10, int(py)+4, price_text(value, self.usd_rate, 6))
        painter.drawText(left, h-5, f'−{elapsed:.1f} с')
        painter.drawText(int(right)-110, h-5, 'последняя цена')
        colors = {'BASE': COLORS['muted'], 'PRICE': COLORS['accent'],
                  'DIP': COLORS['warning'], 'ENTRY': COLORS['entry'],
                  'TP': COLORS['positive'], 'SL': COLORS['danger'], 'TRAIL': COLORS['warning']}
        names = {'BASE': 'База DIP', 'PRICE': 'Цена', 'DIP': 'Вход DIP',
                 'ENTRY': 'Вход*', 'TP': 'Take Profit', 'SL': 'Stop Loss', 'TRAIL': 'Trailing'}
        rows = [(label, float(value)) for label, value in sorted(levels.items(), key=lambda item: -float(item[1])) if float(value)>0]
        gap = min(28, (bottom-top)/max(1, len(rows)-1))
        positions = []
        for label, value in rows:
            positions.append(max(y(value), positions[-1]+gap if positions else top))
        if positions:
            positions[-1] = min(positions[-1], bottom-8)
            for i in range(len(positions)-2, -1, -1):
                positions[i] = min(positions[i], positions[i+1]-gap)
        for (label, value), py_label in zip(rows, positions):
            color = QColor(colors.get(label, COLORS['entry']))
            style = Qt.DotLine if label in ('BASE', 'PRICE') else Qt.DashLine
            painter.setPen(QPen(color, 1, style))
            py = y(value)
            painter.drawLine(QPointF(left, py), QPointF(right, py))
            painter.setPen(color)
            name = names.get(label, label)
            text = f'{name}  {price_text(value, self.usd_rate, 6)}'
            text = fm.elidedText(text, Qt.ElideMiddle, w-int(right)-14)
            painter.drawText(int(right)+8, int(py_label)+4, text)
        path = QPainterPath()
        for i, value in enumerate(self.values):
            point = QPointF(left+(right-left)*(self.times[i]-self.times[0])/elapsed, y(value))
            if i == 0 or self.times[i]-self.times[i-1] > .55:
                path.moveTo(point)
            else:
                path.lineTo(point)
        painter.setPen(QPen(QColor(COLORS["positive"]), 2))
        painter.drawPath(path)
        # Isolated quotes remain visible without inventing a line across RPC gaps.
        for i, value in enumerate(self.values):
            if i == 0 or self.times[i]-self.times[i-1] > .55:
                painter.drawEllipse(QPointF(left+(right-left)*(self.times[i]-self.times[0])/elapsed, y(value)), 1.5, 1.5)
        captions = []
        fm = painter.fontMetrics()
        for stamp, label, value in reversed(self.markers):
            if stamp < self.times[0]:
                continue
            px = left+(right-left)*(stamp-self.times[0])/elapsed
            py = y(value)
            painter.setPen(QColor('#60e1bb' if label == 'BUY' else '#f4c76b'))
            painter.drawEllipse(QPointF(px, py), 4, 4)
            caption = label + ' · рынок'
            width = fm.horizontalAdvance(caption)
            tx = max(left, min(px+6, right-width))
            direction = -1 if label == 'BUY' else 1
            for lane in (direction, -direction, 2*direction, -2*direction):
                baseline = py + lane*(fm.height()+4)
                box = QRectF(tx, baseline-fm.ascent(), width, fm.height())
                if box.top() < top or box.bottom() > bottom:
                    continue
                if any(box.adjusted(-3, -2, 3, 2).intersects(other) for other in captions):
                    continue
                captions.append(box)
                painter.drawText(int(tx), int(baseline), caption)
                break
            # All execution markers remain visible; crowded captions yield to recent ones.
        if self.hover is not None and left <= self.hover.x() <= right and top <= self.hover.y() <= bottom:
            stamp = self.times[0]+elapsed*(self.hover.x()-left)/(right-left)
            index = min(range(len(self.times)), key=lambda i: abs(self.times[i]-stamp))
            px = left+(right-left)*(self.times[index]-self.times[0])/elapsed
            painter.setPen(QPen(QColor(COLORS['muted']), 1, Qt.DotLine))
            painter.drawLine(QPointF(px, top), QPointF(px, bottom))
            painter.drawEllipse(QPointF(px, y(self.values[index])), 4, 4)
            painter.setPen(QColor('#e7eef7'))
            painter.drawText(left, 17, f'{price_text(self.values[index], self.usd_rate)} · {self.times[index]-self.times[-1]:.1f} с от последней котировки')
