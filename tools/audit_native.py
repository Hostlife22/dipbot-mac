"""Read-only evidence extractor for one known Nuitka release, never an EXE loader.

Only bot/trader/pair_profiles constant streams and selected .text ranges are read.
The optional disassembly uses Capstone; JSON extraction uses the standard library.
This is a release-specific decoder, not a general-purpose Nuitka unpacker.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

SHA256 = "0f9da36f8a9f0b9908d69e00a7c7c3501efb8d9823246078063267dababd0f72"
TABLES = {"bot": 0x1429D77D0, "trader": 0x142A5F6D0, "pair_profiles": 0x142A137E0}
SELECTED = {"bot": [25, 28, 30, 31, 35, 36, 37, 56, 57, 112, 134, 140, 147,
                    165, 166, 167, 169, 171],
            "trader": [128, 130, 139, 140, 141, 142, 165, 237, 259, 262, 354, 355, 356, 358, 414],
            "pair_profiles": [129, 131, 135, 137]}
# Virtual addresses refer to preferred image base 0x140000000, not file offsets.
RANGES = {
    "converter_limits": (0x141E840F5, 0x141E8413E),
    "bot_init_gap_and_poll": (0x14088CA6D, 0x14088CB14),
    "buy_guard": (0x14088EEC0, 0x14088F2D6),
    "monitoring_gap_and_dip": (0x14089817B, 0x140898F80),
    "growth_and_two_down_moves": (0x14089AF00, 0x14089B509),
    "tp_sl": (0x14089B689, 0x14089BE5E),
    "buy_receipt_reference": (0x14088FEC0, 0x140892D00),
    "sell_submission": (0x1408965A1, 0x1408965E0),
    "buy_snapshot_formula": (0x141E63DC0, 0x141E65576),
}


class Decoder:
    def __init__(self, data):
        self.data, self.pos, self.previous = data, 0, None

    def take(self, size):
        if size < 0 or self.pos + size > len(self.data):
            raise ValueError("Truncated constant stream")
        result = self.data[self.pos:self.pos + size]
        self.pos += size
        return result

    def varint(self):
        value = 0
        for shift in range(0, 128, 7):
            byte = self.take(1)[0]
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
        raise ValueError("Oversized integer")

    def read(self, depth=0):
        if depth > 100:
            raise ValueError("Excessive nesting")
        tag = chr(self.take(1)[0])
        if tag == "p":
            value = self.previous
        elif tag in "auOcE":
            end = self.data.index(0, self.pos)
            value = self.take(end - self.pos).decode("utf-8")
            self.take(1)
            if tag in "OE":
                value = {"builtin": value}
        elif tag in "wd":
            value = self.take(1).decode("latin1")
        elif tag in "vbBX":
            raw = self.take(self.varint())
            value = raw.decode("utf-8") if tag == "v" else {"bytes": raw.hex()}
        elif tag in "liqI":
            value = self.varint() * (-1 if tag in "qI" else 1)
        elif tag == "f":
            value = struct.unpack("<d", self.take(8))[0]
        elif tag == "Z":
            value = [0.0, -0.0, "NaN", "-NaN", "Infinity", "-Infinity"][self.take(1)[0]]
        elif tag in "ntFs":
            value = {"n": None, "t": True, "F": False, "s": ""}[tag]
        elif tag in "TLDSP":
            count = self.varint()
            if count > len(self.data):
                raise ValueError("Invalid container length")
            self.previous = None
            value = [self.read(depth + 1) for _ in range(count)]
            if tag == "D":
                self.previous = None
                values = [self.read(depth + 1) for _ in range(count)]
                value = {"dict": list(zip(value, values))}
        elif tag in ":;JA":
            self.previous = None
            value = {"type": tag, "values": [self.read(depth + 1)
                     for _ in range(3 if tag in ":;" else 2)]}
        elif tag in "MQ":
            value = {"type": tag, "value": self.take(1)[0]}
        else:
            raise ValueError(f"Unsupported constant tag {tag!r} at {self.pos - 1}")
        self.previous = value
        return value


def constants(data, module):
    # The dot terminates the preceding stream; the name is followed by size/count.
    offset = data.index(b"." + module.encode() + b"\0", 0x2A0C400) + len(module) + 2
    size, count = struct.unpack_from("<IH", data, offset)
    decoder = Decoder(data[offset + 6:offset + 4 + size])
    result = []
    for index in range(count):
        start = offset + 6 + decoder.pos
        value = decoder.read()
        result.append({"index": index, "file_offset": start, "value": value})
    if decoder.data[decoder.pos:] != b".":
        raise ValueError("Constant stream did not end at expected boundary")
    return result


def inspect(exe, disassembly=None):
    data = exe.read_bytes()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("Different release: native addresses must be established again")
    modules = {name: constants(data, name) for name in TABLES}
    report = {"sha256": SHA256, "image_base": "0x140000000",
              "decoded_counts": {k: len(v) for k, v in modules.items()},
              "selected_constants": {name: [rows[i] for i in SELECTED[name]]
                                     for name, rows in modules.items()}, "native_ranges": {}}
    if disassembly:
        import capstone
        disassembly.mkdir(parents=True, exist_ok=True)
        engine = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
        engine.detail = True
        slots = {TABLES[name] + row["index"] * 8: f'{name}[{row["index"]}]={row["value"]!r}'
                 for name, rows in modules.items() for row in rows}
    for name, (start, end) in RANGES.items():
        offset = start - 0x140001000 + 0x400  # .text of this hash-locked release
        code = data[offset:offset + end - start]
        report["native_ranges"][name] = {"va_start": hex(start), "va_end": hex(end),
            "file_offset": offset, "sha256": hashlib.sha256(code).hexdigest()}
        if disassembly:
            lines = []
            for ins in engine.disasm(code, start):
                refs = [slots[ins.address + ins.size + op.mem.disp] for op in ins.operands
                        if op.type == capstone.CS_OP_MEM and op.mem.base == capstone.x86.X86_REG_RIP
                        and ins.address + ins.size + op.mem.disp in slots]
                lines.append(f'{ins.address:#x} {ins.mnemonic} {ins.op_str}' +
                             (" ; " + " | ".join(refs) if refs else ""))
            (disassembly / (name + ".asm")).write_text("\n".join(lines) + "\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("exe", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--disassembly", type=Path)
    args = parser.parse_args()
    args.output.write_text(json.dumps(inspect(args.exe, args.disassembly), indent=2,
                                     ensure_ascii=False) + "\n")
    print("Wrote selected evidence:", args.output)
