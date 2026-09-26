"""Application composition, command-line modes and process lifecycle."""

import argparse
import sys

from PySide6.QtCore import QLockFile, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from dipbot.market.chain import profiles
from dipbot.persistence.storage import Store, data_dir
from dipbot.persistence.vault import Vault
from dipbot.ui.theme import STYLE
from dipbot.ui.window import Window


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--position-check", help="Offline synthetic position UI audit directory")
    parser.add_argument("--position-check-resume", action="store_true")
    parser.add_argument(
        "--market-paper-modern",
        action="store_true",
        help="Exercise window DIP, quote exits, activity filter and adaptive RPC in PAPER audit",
    )
    parser.add_argument("--market-paper-token", help="Isolated visible PAPER market audit token")
    parser.add_argument("--market-paper-pool", help="Canonical pool for market audit")
    parser.add_argument("--market-paper-output", help="New directory for market audit")
    parser.add_argument("--smoke-test", action="store_true", help="Offline GUI startup test, temporary state")
    parser.add_argument("--display-check", help="Isolated read-only GUI audit directory")
    parser.add_argument("--display-replay", help="Recorded PAPER report with market_samples for GUI replay")
    parser.add_argument("--paper-acceptance", help="Isolated read-only PAPER check directory")
    parser.add_argument("--acceptance-seconds", type=int, default=600)
    parser.add_argument("--acceptance-resume", action="store_true")
    parser.add_argument("--acceptance-endpoint", default="https://bsc-dataseed.binance.org")
    args = parser.parse_args()
    diagnostics = None
    app = QApplication(sys.argv[:1])
    app.setApplicationName("DipBot Mac")
    app.setStyleSheet(STYLE)
    if args.position_check:
        from dipbot.checks.position_check import run

        return run(app, args.position_check, args.position_check_resume)
    if args.market_paper_token:
        if not args.market_paper_output:
            parser.error("--market-paper-token requires --market-paper-output")
        from pathlib import Path

        from dipbot.checks.token_ui_paper import run

        return run(
            args.market_paper_token,
            Path(args.market_paper_output),
            args.acceptance_seconds,
            args.market_paper_pool,
            exercise_recovery=True,
            close_after=True,
            modern=args.market_paper_modern,
        )
    if args.display_check:
        if not args.display_replay:
            parser.error("--display-check requires --display-replay")
        from dipbot.checks.display_check import run

        return run(app, args.display_check, args.display_replay, args.acceptance_seconds)
    if args.paper_acceptance:
        from dipbot.checks.acceptance import run

        return run(
            app,
            args.paper_acceptance,
            args.acceptance_seconds,
            args.acceptance_resume,
            args.acceptance_endpoint,
        )
    if args.smoke_test:
        import tempfile
        from pathlib import Path

        from eth_account import Account

        # Exercise packaged crypto using a temporary, unfunded key. No provider or broadcast.
        account = Account.create()
        signed = account.sign_transaction(
            {"to": account.address, "value": 0, "chainId": 56, "nonce": 0, "gas": 21000, "gasPrice": 1}
        )
        assert Account.recover_transaction(signed.raw_transaction) == account.address
        assert len(profiles()) == 42
        Vault.backend()  # Instantiate only; do not read/write the real Keychain.
        temp = tempfile.TemporaryDirectory()
        store = Store(Path(temp.name) / "state.json")
        window = Window(store)
        window.show()
        QTimer.singleShot(600, window.close)
        QTimer.singleShot(1500, app.quit)
    else:
        lock = QLockFile(str(data_dir() / "app.lock"))
        if not lock.tryLock(0):
            QMessageBox.warning(None, "DipBot Mac", "Другой экземпляр приложения уже запущен")
            return 1
        from dipbot.observability.diagnostics import Diagnostics

        try:
            diagnostics = Diagnostics(data_dir() / "diagnostics")
        except (OSError, ValueError):
            diagnostics = None
        try:
            window = Window()
        except Exception:
            if diagnostics is not None:
                diagnostics.close(clean=False)
            QMessageBox.critical(
                None,
                "Данные приложения",
                "Не удалось прочитать state.json. Сохраните его копию для сверки; торговля не запущена",
            )
            return 1
        if diagnostics is not None and diagnostics.previous_unclean:
            window.log(
                "Предыдущая сессия завершилась без отметки штатного выхода. Проверьте позиции и журнал восстановления; диагностика сохранена локально."
            )
        window.show()
    try:
        result = app.exec()
    except BaseException:
        if diagnostics is not None:
            diagnostics.exception(*sys.exc_info())
            diagnostics.close(clean=False)
        raise
    if diagnostics is not None:
        diagnostics.close(clean=result == 0)
    return result
