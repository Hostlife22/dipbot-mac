"""Read-only, allowlisted extraction. Never scans licenses or wallet files."""
import argparse
import hashlib
import json
import re
import struct
from pathlib import Path


def inspect(exe: Path) -> dict:
    data = exe.read_bytes()
    pe = struct.unpack_from("<I", data, 60)[0]
    start = data.index(b".pair_profiles\x00")
    end = data.index(b"upair_profiles.py", start)
    section = data[start:end]
    profiles = []
    for match in re.finditer(rb"0x[0-9a-fA-F]{40}", section):
        before = section[max(0, match.start() - 45):match.start()]
        names = re.findall(rb"a([A-Z][A-Za-z0-9]*)\x00", before)
        name = "U" if before.endswith(b"wUu") else names[-1].decode()
        mode = re.search(rb"a(direct_v[23]|via_usdt_v3|via_eth_v3|native_wrap)\x00",
                         section[match.end():match.end()+45])
        profiles.append({"symbol": name, "address": match.group().decode(),
                         "original_converter": mode.group(1).decode() if mode else "unknown",
                         "exe_offset": start + match.start()})
    markers = ["Nuitka", "__nuitka_version__", "CryptProtectData", "CryptUnprotectData",
               "DipBot._on_buy_submitted", "DipBot._on_sell_submitted", "2 down moves",
               "STOP requested: will sell then stop bot", "SL triggered, bot stopped",
               "Developer DIP Core: allowable BUY recovery = Slippage - Dynamic/100 on V2 and V3."]
    evidence = {key: data.find(key.encode()) for key in markers}
    return {"executable": exe.name, "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data), "pe_machine": hex(struct.unpack_from("<H", data, pe+4)[0]),
            "pyinstaller_cookie": data.rfind(b"MEI\014\013\012\013\016"),
            "evidence_offsets": evidence, "profiles": profiles}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("exe", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = inspect(args.exe)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Extracted {len(report['profiles'])} public profiles; report: {args.output}")

