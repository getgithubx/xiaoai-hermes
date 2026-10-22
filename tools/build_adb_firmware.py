"""Build LX04 original-version USB-ADB images without accessing a device.

Existing ext4 file sizes, inode metadata, ownership and SELinux xattrs are kept.
Only original file data blocks and the verity-disable marker are changed.
Bootloader unlock is a prerequisite, not something this builder performs.
"""
import base64
import hashlib
import json
import math
import mmap
import re
import shutil
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ANALYSIS = ROOT / "state/firmware-analysis"
OUT = ROOT / "state/adb-firmware-2.42.118"
DEBUGFS = "/opt/homebrew/opt/e2fsprogs/sbin/debugfs"
BLOCK = 4096


def sha(data):
    return hashlib.sha256(data).hexdigest()


def file_sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def debugfs(image, command):
    result = subprocess.run([DEBUGFS, "-R", command, str(image)], capture_output=True, check=True)
    if b"File not found" in result.stderr or b"Command not found" in result.stderr:
        raise RuntimeError(result.stderr.decode())
    return result.stdout


def map_file(image, name):
    stat = debugfs(image, "stat " + name).decode()
    size = int(re.search(r"\bSize: (\d+)", stat)[1])
    blocks = list(map(int, debugfs(image, "blocks " + name).split()))
    assert len(blocks) == math.ceil(size / BLOCK), "Nontrivial extent mapping is not supported"
    assert len(set(blocks)) == len(blocks)
    content = debugfs(image, "cat " + name)
    assert len(content) == size
    with image.open("rb") as stream:
        parts = []
        for block in blocks:
            stream.seek(block * BLOCK)
            parts.append(stream.read(BLOCK))
    assert b"".join(parts)[:size] == content, "debugfs and raw file mapping differ"
    return content, blocks, stat


def patch_init(original):
    text = original.decode()
    command = "    setprop persist.sys.usb.config mtp,adb\n"
    assert text.count("    load_persist_props\n") == 2
    assert text.count("    class_start main\n") == 3
    # Set after persistent values are loaded, and before every framework start.
    text = text.replace("    load_persist_props\n", "    load_persist_props\n" + command)
    text = text.replace("    class_start main\n", command + "    class_start main\n")
    lines = text.splitlines(keepends=True)
    while len("".join(lines).encode()) > len(original):
        index = next(i for i, line in enumerate(lines)
                     if line.lstrip().startswith("#") and "Copyright" not in line)
        del lines[index]
    result = "".join(lines).encode()
    result += b"\n" * (len(original) - len(result))
    # Compare active statements exactly; no old command is removed or changed.
    active = lambda x: [line.strip() for line in x.decode().splitlines()
                        if line.strip() and not line.lstrip().startswith("#")]
    assert [x for x in active(result) if x != command.strip()] == active(original)
    assert active(result).count(command.strip()) == 5
    return result


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    key_path = Path.home() / ".android/adbkey.pub"
    public_key = key_path.read_text().split()[0]
    decoded = base64.b64decode(public_key, validate=True)
    assert len(decoded) == 524 and struct.unpack_from("<I", decoded)[0] == 64
    key = (public_key + " lx04-owner-usb\n").encode()
    verification = json.loads((ANALYSIS / "verity-integrity-verification.json").read_text())
    manifest = {"version": 1, "device": "LX04", "firmware": "2.42.118",
                "prerequisite": "Validated original seccfg transition to unlocked state 3",
                "hardware_verified": False, "usb_adb_authentication": True,
                "tcp_adb_configuration": "Original vendor service.adb.tcp.port=5555 preserved; authenticated",
                "host_public_key_sha256": sha(decoded),
                "images": {}}
    for slot in ("a", "b"):
        name = "system_" + slot
        source = ANALYSIS / (name + ".img")
        target = OUT / (name + "-adb.img")
        shutil.copyfile(source, target)
        files = []
        for filename in ("/init.rc", "/adb_keys"):
            original, blocks, stat = map_file(source, filename)
            replacement = patch_init(original) if filename == "/init.rc" else key
            assert len(replacement) <= len(original)
            replacement += b"\n" * (len(original) - len(replacement))
            with target.open("r+b") as stream:
                for index, block in enumerate(blocks):
                    chunk = replacement[index * BLOCK:(index + 1) * BLOCK]
                    stream.seek(block * BLOCK)
                    stream.write(chunk)
            after, after_blocks, after_stat = map_file(target, filename)
            assert after == replacement and after_blocks == blocks and after_stat == stat
            files.append({"path": filename, "bytes": len(original), "blocks": blocks,
                          "before_sha256": sha(original), "after_sha256": sha(replacement),
                          "inode_and_selinux_metadata_unchanged": True})
            (OUT / (name + filename.replace("/", "-") + ".txt")).write_bytes(replacement)
        metadata = verification["images"][name]["metadata_offset"]
        with target.open("r+b") as stream:
            stream.seek(metadata)
            assert stream.read(4) == struct.pack("<I", 0xb001b001)
            stream.seek(metadata)
            stream.write(struct.pack("<I", 0x46464f56))
        patches = []
        with source.open("rb") as a, target.open("rb") as b:
            old = mmap.mmap(a.fileno(), 0, access=mmap.ACCESS_READ)
            new = mmap.mmap(b.fileno(), 0, access=mmap.ACCESS_READ)
            allowed = {n for f in files for n in f["blocks"]} | {metadata // BLOCK}
            for offset in range(0, len(old), BLOCK):
                before, after = old[offset:offset + BLOCK], new[offset:offset + BLOCK]
                if before == after:
                    continue
                assert offset // BLOCK in allowed, "Unexpected modified block"
                patches.append({"offset": offset, "length": len(after),
                                "before_sha256": sha(before), "after_sha256": sha(after),
                                "before_b64": base64.b64encode(before).decode(),
                                "after_b64": base64.b64encode(after).decode()})
            old.close()
            new.close()
        assert len(patches) <= len(allowed) and sum(p["length"] for p in patches) <= 65536
        manifest["images"][name] = {
            "path": target.name, "bytes": target.stat().st_size,
            "original_sha256": file_sha(source), "sha256": file_sha(target),
            "files": files, "verity_metadata_offset": metadata,
            "verity_marker": "0x46464f56", "patches": patches,
        }
        print(name, "modified blocks", len(patches), "bytes", sum(p["length"] for p in patches))
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("Built", OUT, "(offline candidate, device boot still requires verification)")


if __name__ == "__main__":
    main()
