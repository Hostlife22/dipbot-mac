"""Reconstructed Windows envelope codec with an explicitly supplied protector.

No file access, DPAPI emulation, portable encryption or automatic Mac import.
The callbacks accept (bytes, purpose); native Windows protection must be provided
by the caller. Synthetic callbacks test only the serialization contract.
"""

import base64
import json


class ProtectedFormatError(ValueError):
    pass


def encode(payload, purpose, protect):
    if not isinstance(payload, dict):
        raise ProtectedFormatError("Protected payload must be an object")
    try:
        plain = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        encrypted = protect(plain, purpose)
        envelope = {
            "format": "NRNF-DPAPI",
            "version": 1,
            "payload": base64.b64encode(encrypted).decode("ascii"),
        }
        return json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("ascii")
    except Exception:
        raise ProtectedFormatError("Protected local data is damaged or unavailable") from None


def decode(raw, purpose, unprotect):
    try:
        envelope = json.loads(raw.decode("ascii"))
        if envelope.get("format") != "NRNF-DPAPI" or int(envelope.get("version", 0)) != 1:
            raise ValueError("envelope")
        encrypted = base64.b64decode(envelope["payload"], validate=True)
        plain = unprotect(encrypted, purpose)
        payload = json.loads(plain.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("payload")
        return payload
    except Exception:
        raise ProtectedFormatError("Protected local data is damaged or unavailable") from None
