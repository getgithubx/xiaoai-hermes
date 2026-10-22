"""Read-only verification of extracted LX04 images; never accesses USB devices."""
import hashlib
import json
import mmap
import struct
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "state/firmware-analysis"


def verify_image(name, key):
    table = (DIRECTORY / f"{name}.verity-table").read_bytes()
    signature = (DIRECTORY / f"{name}.verity-signature").read_bytes()
    key.verify(signature, table, padding.PKCS1v15(), hashes.SHA256())
    fields = table.decode().split()
    assert fields[0] == "1" and fields[7] == "sha256"
    block, hash_block, blocks, tree_start = map(int, fields[3:7])
    assert block == hash_block == 4096 and tree_start == blocks
    salt = bytes.fromhex(fields[9])
    with (DIRECTORY / f"{name}.img").open("rb") as file:
        with mmap.mmap(file.fileno(), 0, access=mmap.ACCESS_READ) as data:
            # dm-verity v1 hashes salt before each block. Store the root level first.
            level = b"".join(hashlib.sha256(salt + data[i:i + block]).digest()
                             for i in range(0, blocks * block, block))
            levels = []
            while True:
                level += b"\0" * (-len(level) % block)
                levels.append(level)
                if len(level) == block:
                    root_hash = hashlib.sha256(salt + level).hexdigest()
                    break
                level = b"".join(hashlib.sha256(salt + level[i:i + block]).digest()
                                 for i in range(0, len(level), block))
            assert root_hash == fields[8], f"{name}: root hash differs"
            tree = b"".join(reversed(levels))
            start = tree_start * block
            assert data[start:start + len(tree)] == tree, f"{name}: tree differs"
            metadata = start + len(tree)
            magic, version = struct.unpack_from("<II", data, metadata)
            assert magic == 0xB001B001 and version == 0
            assert data[metadata + 8:metadata + 264] == signature
            length = struct.unpack_from("<I", data, metadata + 264)[0]
            assert data[metadata + 268:metadata + 268 + length] == table
            # Android FEC footer: two header copies within the last block.
            footer = data[-block:]
            magic, version, size, roots, fec_size, input_size = struct.unpack_from("<IIIIIQ", footer)
            assert magic == 0xFECFECFE and version == 0 and size == 60
            assert footer[:size] == footer[-size:]
            assert input_size + fec_size + block == len(data)
            fec_hash = hashlib.sha256(data[input_size:input_size + fec_size]).digest()
            assert fec_hash == footer[28:60], f"{name}: FEC digest differs"
            return {"signature_sha256_valid": True, "data_bytes": blocks * block,
                    "tree_bytes": len(tree), "tree_byte_exact": True,
                    "root_hash": root_hash, "metadata_offset": metadata,
                    "fec_header_copies_match": True, "fec_roots": roots,
                    "fec_bytes": fec_size, "fec_digest_valid": True,
                    "fec_parity_recomputed": False}


def main():
    raw = (DIRECTORY / "original-verity-key").read_bytes()
    words = struct.unpack("<131I", raw)
    assert words[0] == 64
    key = rsa.RSAPublicNumbers(words[-1], int.from_bytes(raw[8:264], "little")).public_key()
    cert = x509.load_pem_x509_certificate((DIRECTORY / "aosp-verity.x509.pem").read_bytes())
    result = {"original_key_sha256": hashlib.sha256(raw).hexdigest(),
              "matches_aosp_android_8_1_r1_certificate": key.public_numbers() == cert.public_key().public_numbers(),
              "images": {}}
    for name in ("system_a", "system_b", "vendor_a", "vendor_b"):
        result["images"][name] = verify_image(name, key)
    (DIRECTORY / "verity-integrity-verification.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
