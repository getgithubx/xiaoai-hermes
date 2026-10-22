"""Offline inspection of the verified LX04 LK and para backups, no device access."""
import hashlib
import json
import struct
from pathlib import Path
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG, ARM_REG_PC

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "state/firmware-analysis"
raw = (ROOT / "state/recovery-analysis/lk_a.bin").read_bytes()
size = struct.unpack_from("<I", raw, 4)[0]
data = raw[512:512 + size]
base = 0x56000000
assert struct.unpack_from("<I", data, 0x74)[0] == base
needles = [b"MT65XX_FACTORY_KEY", b"MT65XX_BOOT_MENU_KEY", b"MT65XX_RECOVERY_KEY",
           b"Enable recovery mode", b"[FASTBOOT] reboot to boot loader",
           b"boot-recovery", b"Boot command:", b"boot-upgrade", b"set g_boot_from_adb",
           b"[LK] boot_ctrl magic number", b"[LK] unknow slot"]
targets = {}
for needle in needles:
    start = 0
    while (found := data.find(needle, start)) >= 0:
        string_start = data.rfind(b"\0", 0, found) + 1
        text = data[string_start:data.index(0, found)].decode("ascii", "replace")
        targets[base + string_start] = text
        start = found + len(needle)

# Resolve Thumb literal loads and PC-relative address construction, then retain
# only exact matches to known strings. This is a candidate-xref finder, not a CFG.
decoder = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
decoder.detail = True
decoder.skipdata = True
registers = {}
matches = []
for instruction in decoder.disasm(data[:0x32000], base):
    if not instruction.id:
        registers.clear()
        continue
    ops, name = instruction.operands, instruction.mnemonic
    pc = instruction.address + 4
    value = destination = None
    if name.startswith("ldr") and len(ops) == 2 and ops[1].type == ARM_OP_MEM and ops[1].mem.base == ARM_REG_PC:
        offset = (pc & ~3) + ops[1].mem.disp - base
        if 0 <= offset <= len(data) - 4:
            destination, value = ops[0].reg, struct.unpack_from("<I", data, offset)[0]
    elif name.startswith("movw"):
        destination, value = ops[0].reg, ops[1].imm
    elif name.startswith("movt"):
        destination = ops[0].reg
        value = (registers.get(destination, 0) & 65535) | (ops[1].imm << 16)
    elif name.startswith("add") and len(ops) >= 2:
        destination = ops[0].reg
        values = [pc if o.type == ARM_OP_REG and o.reg == ARM_REG_PC else
                  registers.get(o.reg) if o.type == ARM_OP_REG else
                  o.imm if o.type == ARM_OP_IMM else None
                  for o in (ops[1:] if len(ops) == 3 else ops)]
        if len(values) == 2 and all(v is not None for v in values):
            value = sum(values) & 0xffffffff
    if destination and value is not None:
        registers[destination] = value
        if value in targets:
            matches.append({"instruction": hex(instruction.address), "string": targets[value],
                            "string_address": hex(value)})
    elif ops and ops[0].type == ARM_OP_REG and not name.startswith(("str", "cmp", "tst", "b")):
        registers.pop(ops[0].reg, None)
    if name in ("bx", "pop", "b"):
        registers.clear()

para = (ANALYSIS / "para.img").read_bytes()
control = para[0x800:0x808]
assert struct.unpack_from("<I", control)[0] == 0x19191100
slots = [{"suffix": suffix, "raw": hex(value), "priority": value & 7,
          "retry_count": (value >> 3) & 7, "successful_boot_bit": (value >> 6) & 1,
          "bit7": (value >> 7) & 1}
         for suffix, value in zip(("_a", "_b"), control[5:7])]
selected = slots[0] if slots[0]["priority"] >= slots[1]["priority"] else slots[1]
result = {"lk_sha256": hashlib.sha256(raw).hexdigest(), "relocation_base": hex(base),
          "candidate_string_xrefs": matches, "bootctrl_offset_in_para": "0x800",
          "bootctrl_raw": control.hex(), "slots": slots,
          "stored_preferred_slot": selected["suffix"],
          "scope": "Backup state and static LK selection, not live boot-slot attestation",
          "selection_function": "0x56029b88"}
(ANALYSIS / "lk-bootctrl-analysis.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
