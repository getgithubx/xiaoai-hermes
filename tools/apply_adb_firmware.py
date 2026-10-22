"""Apply/restore a bounded LX04 ADB delta through the already validated RAM DA.

No format, erase, recovery boot, partition-table write or bootloader write.
All preimages are checked before the first flash write. Hardware SEJ must verify
both the existing security configuration and its replacement before any write.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import runpy
import struct
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "state"
PACKAGE = STATE / "adb-firmware-2.42.118"
BACKUP = ROOT / "backups/original"
MTK = ROOT / "tools/mtkclient"
SECCFG = 0x4c80000


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as output:
        json.dump(value, output, indent=2)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(path)


def transact(read, write, items, status, log):
    """Verify every preimage, write each exact range, read back; undo on failure."""
    for item in items:
        actual = read(item["address"], len(item["before"]))
        if actual != item["before"]:
            raise RuntimeError("Preimage changed before transaction: " + item["name"])
    changed = []
    try:
        for item in items:
            if item["before"] == item["after"]:
                continue
            changed.append(item)  # Include a potentially partially written block.
            status["state"] = "writing"
            status["current"] = item["name"]
            save_json(log, status)
            if not write(item["address"], item["after"]):
                raise RuntimeError("Write failed: " + item["name"])
            if read(item["address"], len(item["after"])) != item["after"]:
                raise RuntimeError("Read-back mismatch: " + item["name"])
            print("Verified", item["name"], flush=True)
        # Re-read all ranges after the complete transaction, including seccfg.
        for item in items:
            if read(item["address"], len(item["after"])) != item["after"]:
                raise RuntimeError("Final verification mismatch: " + item["name"])
        status["state"] = "verified"
        status["written_ranges"] = len(changed)
        save_json(log, status)
    except BaseException:
        failed = []
        for item in reversed(changed):
            try:
                if not write(item["address"], item["before"]):
                    raise RuntimeError("restore write failed")
                if read(item["address"], len(item["before"])) != item["before"]:
                    raise RuntimeError("restore readback failed")
            except BaseException:
                failed.append(item["name"])
        status["state"] = "rollback_failed" if failed else "rolled_back"
        status["rollback_failed_ranges"] = failed
        save_json(log, status)
        print("Stopped. Rollback status:", status["state"], flush=True)
        print("Keep power and USB connected; inspect the log before rebooting.", flush=True)
        raise


def load_package():
    manifest_bytes = (PACKAGE / "manifest.json").read_bytes()
    validation = json.loads((PACKAGE / "offline-validation.json").read_text())
    if not validation.get("passed") or validation["manifest_sha256"] != sha(manifest_bytes):
        raise RuntimeError("Package has not passed offline validation")
    manifest = json.loads(manifest_bytes)
    assert manifest["device"] == "LX04" and manifest["firmware"] == "2.42.118"
    for name, image in manifest["images"].items():
        assert name in ("system_a", "system_b")
        with (PACKAGE / image["path"]).open("rb") as stream:
            assert hashlib.file_digest(stream, "sha256").hexdigest() == image["sha256"]
        for patch in image["patches"]:
            assert patch["length"] == 4096 and patch["offset"] % 4096 == 0
            assert 0 <= patch["offset"] < image["bytes"]
            for kind in ("before", "after"):
                blob = base64.b64decode(patch[kind + "_b64"], validate=True)
                assert len(blob) == 4096 and sha(blob) == patch[kind + "_sha256"]
    return manifest


def execute(mtk, manifest, restoring=False):
    from mtkclient.Library.Hardware.seccfg import SecCfgV4
    from mtkclient.config.brom_config import DAmodes
    if mtk.config.hwcode != 0x8167 or mtk.daloader.flashmode != DAmodes.XFLASH:
        raise RuntimeError("Expected MT8167 XFLASH connection")
    folder = STATE / ("adb-device-run-" + time.strftime("%Y%m%d-%H%M%S"))
    folder.mkdir(mode=0o700)
    log = folder / "result.json"
    status = {"state": "checking", "operation": "restore" if restoring else "apply"}
    save_json(log, status)

    def read(address, length):
        value = mtk.daloader.readflash(addr=address, length=length, filename="",
                                      parttype="user", display=False)
        if not isinstance(value, (bytes, bytearray)) or len(value) != length:
            raise RuntimeError(f"Short read at {address:#x}")
        return bytes(value)

    # Match this exact backed-up device and partition layout, not merely its SoC.
    with (BACKUP / "user.bin").open("rb") as original:
        expected_gpt = original.read(34 * 512)
    if read(0, len(expected_gpt)) != expected_gpt:
        raise RuntimeError("Live GPT differs from this speaker's backup")
    for name, address in (("proinfo", 0x80000), ("nvram", 0x380000)):
        original = (BACKUP / (name + ".bin")).read_bytes()
        if read(address, len(original)) != original:
            raise RuntimeError("Device identity partition differs: " + name)
    para = read(0x6dc0000, 0x1000)
    if para[:32].rstrip(b"\0\xff") or para[64:832].rstrip(b"\0\xff"):
        raise RuntimeError("Pending recovery command; refusing to write")
    if struct.unpack_from("<I", para, 0x800)[0] != 0x19191100:
        raise RuntimeError("Unknown slot control")
    slot = "a" if (para[0x805] & 7) >= (para[0x806] & 7) else "b"
    name = "system_" + slot
    address = 0xd000000 if slot == "a" else 0x45000000
    status["slot"] = slot
    for partition, start, filename in (
        ("boot", 0x4dc0000 if slot == "a" else 0x5dc0000, BACKUP / ("boot_" + slot + ".bin")),
        ("lk", 0x4cc0000 if slot == "a" else 0x4d40000,
         STATE / "recovery-analysis" / ("lk_" + slot + ".bin")),
    ):
        original = filename.read_bytes()
        if read(start, len(original)) != original:
            raise RuntimeError("Boot chain changed since analysis: " + partition)
    items = []
    # Restoration checks both slots: a failed boot may have changed the preferred
    # slot. Never relock while the formerly selected slot is still modified.
    targets = ("system_a", "system_b") if restoring else (name,)
    for target_name in targets:
        target_address = 0xd000000 if target_name == "system_a" else 0x45000000
        for patch in manifest["images"][target_name]["patches"]:
            before = base64.b64decode(patch["before_b64"])
            after = base64.b64decode(patch["after_b64"])
            actual = read(target_address + patch["offset"], len(before))
            if actual not in (before, after):
                raise RuntimeError("Unknown system preimage at " + hex(patch["offset"]))
            items.append({"name": target_name + ":" + hex(patch["offset"]),
                          "address": target_address + patch["offset"], "before": actual,
                          "after": before if restoring else after})
    original_config = (BACKUP / "seccfg.bin").read_bytes()
    live_config = read(SECCFG, len(original_config))
    (folder / "seccfg-before.bin").write_bytes(live_config)
    extension = mtk.daloader.xft
    hardware_crypto = extension.cryptosetup()
    config = SecCfgV4(hardware_crypto, mtk, extension.custom_sej_hw)
    if not config.parse(live_config):
        raise RuntimeError("SEJ could not authenticate the existing seccfg; no writes")
    if config.seccfg_size != 0x3c or config.lock_state not in (1, 3):
        raise RuntimeError("Unexpected seccfg state")
    # Only the lock field and its encrypted hash may differ from the backup.
    if live_config[:12] != original_config[:12] or live_config[16:28] != original_config[16:28] \
            or live_config[60:] != original_config[60:]:
        raise RuntimeError("Unrelated security configuration changes found")
    if restoring:
        desired = original_config
    elif config.lock_state == 3:
        desired = live_config
    else:
        ok, generated = config.create(lockflag="unlock", critical=False)
        if not ok:
            raise RuntimeError("Cannot construct authenticated unlock configuration")
        desired = bytes(generated[:60]) + live_config[60:]
    check = SecCfgV4(hardware_crypto, mtk, extension.custom_sej_hw)
    if not check.parse(desired) or check.lock_state != (1 if restoring else 3):
        raise RuntimeError("New security configuration failed hardware round-trip")
    if desired[:12] != live_config[:12] or desired[16:28] != live_config[16:28] \
            or desired[60:] != live_config[60:]:
        raise RuntimeError("Unlock modified unrelated fields")
    (folder / "seccfg-after.bin").write_bytes(desired)
    security_item = {"name": "seccfg:first-sector", "address": SECCFG,
                     "before": live_config[:512], "after": desired[:512]}
    # Apply unlock first; on restore, put original filesystem back before locking.
    items = items + [security_item] if restoring else [security_item] + items
    allowed = {(item["address"], len(item["after"])) for item in items}

    def write(start, blob):
        if (start, len(blob)) not in allowed:
            raise RuntimeError("Write outside the reviewed delta")
        return mtk.daloader.writeflash(addr=start, length=len(blob), filename="",
                                       wdata=bytearray(blob), parttype="user", display=False)

    save_json(folder / "rollback.json", [{"address": i["address"], "name": i["name"],
              "before_b64": base64.b64encode(i["before"]).decode(),
              "after_sha256": sha(i["after"])} for i in items])
    status.update({"sej_algorithm": config.hwtype, "state": "prepared",
                   "total_changed_bytes": sum(len(i["after"]) for i in items if i["before"] != i["after"])})
    save_json(log, status)
    print("Validated exact LX04, boot chain, empty BCB, slot", slot,
          "and SEJ algorithm", config.hwtype, flush=True)
    transact(read, write, items, status, log)
    print("ALL RANGES WRITTEN AND READ-BACK VERIFIED. Normal power cycle is now required.", flush=True)
    print("Disconnect ALL speaker power/USB, wait 5 seconds, reconnect WITHOUT pressing buttons.", flush=True)
    print("Result:", log, flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--restore", action="store_true")
    options = parser.parse_args()
    os.umask(0o077)
    if os.geteuid() != 0:
        raise RuntimeError("Run via the local .command launcher for USB driver access")
    manifest = load_package()
    os.chdir(STATE)
    sys.path.insert(0, str(MTK))
    from mtkclient.Library.DA.mtk_da_handler import DaHandler
    from mtkclient.config.brom_config import DAmodes
    original_configure = DaHandler.configure_da

    def configure_checked(self, mtk):
        if mtk.config.hwcode != 0x8167 or mtk.config.chipconfig.damode != DAmodes.XFLASH:
            raise RuntimeError("Unexpected device; refusing")
        return original_configure(self, mtk)

    def callback(handler, mtk, cmd, args):
        if cmd != "printgpt":
            raise RuntimeError("Unexpected command")
        execute(mtk, manifest, options.restore)

    DaHandler.configure_da = configure_checked
    DaHandler.handle_da_cmds = callback
    # The unmodified command is read-only if interception were ever removed.
    sys.argv = [str(MTK / "mtk.py"), "printgpt"]
    runpy.run_path(str(MTK / "mtk.py"), run_name="__main__")


if __name__ == "__main__":
    main()
