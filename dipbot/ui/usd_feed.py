"""Asynchronous indicative USD feed owned by the Qt UI."""

import json
import re
import time

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from dipbot.domain.usd import select_rate

MAX_AGE = 90


class UsdRate(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.manager = QNetworkAccessManager(self)
        self.timer = QTimer(self)
        self.timer.setInterval(30000)
        self.timer.timeout.connect(self.refresh)
        self.token = ""
        self.rate = None
        self.received_at = None
        self.reply = None

    def current(self):
        if self.received_at is None or time.monotonic() - self.received_at > MAX_AGE:
            return None
        return self.rate

    def set_token(self, token):
        token = token.lower()
        if token == self.token:
            return
        self.token = token
        self.rate = self.received_at = None
        old, self.reply = self.reply, None
        if old is not None:
            old.abort()
        self.timer.stop()
        self.changed.emit()
        if re.fullmatch(r"0x[0-9a-f]{40}", token):
            self.timer.start()
            self.refresh()

    def refresh(self):
        if not self.token or self.reply is not None:
            return
        token = self.token
        request = QNetworkRequest(QUrl("https://api.dexscreener.com/tokens/v1/bsc/" + token))
        request.setTransferTimeout(5000)
        self.reply = reply = self.manager.get(request)

        def finished():
            if self.reply is reply:
                self.reply = None
            try:
                if token != self.token or reply.error() != QNetworkReply.NoError:
                    return
                rows = json.loads(bytes(reply.readAll()))
                self.rate = select_rate(rows, token)
                self.received_at = time.monotonic()
            except (ValueError, TypeError, AttributeError):
                pass
            finally:
                reply.deleteLater()
                self.changed.emit()

        reply.finished.connect(finished)
