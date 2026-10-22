"""Read-only DTB/key mapping inspection of the verified LX04 backup."""
import hashlib
import json
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "backups/original"
OUT = ROOT / "state/firmware-analysis"


def device_trees(blob):
    cursor = 0
    while (offset := blob.find(bytes.fromhex("d00dfeed"), cursor)) >= 0:
        cursor = offset + 4
        if offset + 40 > len(blob):
            continue
        h = struct.unpack_from(">10I", blob, offset)
        if not (40 <= h[1] <= len(blob) - offset and h[5] in (16, 17)):
            continue
        data = blob[offset:offset + h[1]]
        strings = data[h[3]:h[3] + h[8]]
        pos, stack, nodes = h[2], [], {}
        while pos < h[2] + h[9]:
            tag = struct.unpack_from(">I", data, pos)[0]
            pos += 4
            if tag == 1:
                end = data.index(0, pos)
                stack.append(data[pos:end].decode())
                nodes["/".join(stack)] = {}
                pos = (end + 4) & ~3
            elif tag == 2:
                stack.pop()
            elif tag == 3:
                size, name_offset = struct.unpack_from(">II", data, pos)
                pos += 8
                name = strings[name_offset:strings.index(0, name_offset)].decode()
                nodes["/".join(stack)][name] = data[pos:pos + size]
                pos = (pos + size + 3) & ~3
            elif tag == 9:
                break
            elif tag != 4:
                raise ValueError(f"Invalid FDT token {tag}")
        yield offset, nodes


def cells(value):
    return list(struct.unpack(">" + "I" * (len(value) // 4), value))


def main():
    results = []
    with (BACKUP / "user.bin").open("rb") as image:
        image.seek(1024)
        entries = image.read(128 * 128)
        for i in range(128):
            entry = entries[i * 128:(i + 1) * 128]
            name = entry[56:].decode("utf-16-le").rstrip("\0")
            if name not in ("odmdtbo_a", "odmdtbo_b"):
                continue
            first, last = struct.unpack_from("<QQ", entry, 32)
            image.seek(first * 512)
            blob = image.read((last - first + 1) * 512)
            for offset, nodes in device_trees(blob):
                for node, props in nodes.items():
                    if "mediatek,kpd-hw-init-map" not in props:
                        continue
                    results.append({
                        "partition": name,
                        "partition_sha256": hashlib.sha256(blob).hexdigest(),
                        "dtb_offset": hex(offset), "node": node,
                        "mapping": {k: cells(v) for k, v in props.items()
                                    if k.startswith("mediatek,kpd-")},
                    })
    assert len(results) == 4, "Expected two device trees in each A/B overlay"
    for item in results:
        mapping = item["mapping"]
        assert mapping["mediatek,kpd-hw-init-map"][0] == 115
        assert mapping["mediatek,kpd-hw-rstkey"] == [5]
        assert mapping["mediatek,kpd-sw-rstkey"] == [114]
    report = {
        "evidence": results,
        "linux_key_codes": {"114": "KEY_VOLUMEDOWN", "115": "KEY_VOLUMEUP"},
        "lk_key_5": "PMIC home/reset key -> Linux 114 -> volume down",
        "lk_key_0": "keypad index 0 -> Linux 115 -> volume up",
        "recovery_entry": "LK 0x56003130 checks key 5 AND key 0",
        "limitation": "Static original-firmware evidence; no device key test performed",
    }
    (OUT / "lx04-key-mapping.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
