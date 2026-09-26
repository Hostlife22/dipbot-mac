"""Sanitized application errors; never expose provider URLs or credentials."""
import re
from requests.exceptions import HTTPError
from web3.exceptions import Web3RPCError
from dipbot.persistence.storage import SaveAfterReplaceError
from dipbot.execution.errors import UncertainTransaction
from dipbot.domain.entry_guard import EntryRejected

def safe_error(exc):
    if type(exc) is SaveAfterReplaceError:
        return "Файл заменён, но надёжность сохранения не подтверждена; проверьте сохранённое состояние"
    if isinstance(exc, HTTPError):
        code = getattr(exc.response, 'status_code', None)
        if type(code) is int and 100 <= code <= 599:
            detail = 'лимит запросов RPC' if code == 429 else 'ошибка HTTP при обращении к RPC'
            return f'HTTP {code}: {detail}. Операция прервана'
    if isinstance(exc, Web3RPCError):
        response = getattr(exc, 'rpc_response', None)
        error = response.get('error') if isinstance(response, dict) else None
        code = error.get('code') if isinstance(error, dict) else None
        if type(code) is int:
            return f'RPC {code}: операция прервана. Проверьте доступность узла'
    # Provider exceptions can contain RPC credentials. Do not log arbitrary text.
    if type(exc) in (ValueError, RuntimeError, UncertainTransaction, EntryRejected):
        message = str(exc)
        if type(exc) is ValueError:
            message = re.sub(r"(?:0x)?[0-9a-fA-F]{64}", "[REDACTED]", message)
        if "http" not in message.lower() and len(message) < 250:
            return message
    return f"{type(exc).__name__}: операция прервана. Проверьте RPC, баланс и доступность пула"
