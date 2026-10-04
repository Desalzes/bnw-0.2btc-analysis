#!/usr/bin/env python3
"""Fetch the third-party OpenCL kernels the engine needs (stdlib only).

The 14 kernels below are not redistributed here: their upstream repository has no licence file. This tool
downloads them at a pinned commit, checks every file against kernels/UPSTREAM.sha256, inserts this project's
p2pkh_address_for_public_key() (kernels/address_p2pkh.part) into address.cl and checks the assembled file too.
Nothing is written unless every check passes.

    python3 tools/fetch_kernels.py [--dest DIR] [--base URL]

Afterwards, from puzzles/btc-02-picture/harness:
    uv run --no-project --python 3.12 --with coincurve --with numpy --with pyopencl \
        python -c "import gpu_setperm as g; print(g.engine_hash())"       # expected: 9cb088b6d185f485
"""
import argparse
import hashlib
import os
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
KERNELS = os.path.normpath(os.path.join(HERE, "..", "puzzles", "btc-02-picture", "harness", "kernels"))
COMMIT = "c820aff3e16e80839ce24c53734a8b02daadd138"
BASE = f"https://raw.githubusercontent.com/johncantrell97/bip39-solver-gpu/{COMMIT}/cl"
NAMES = ("address", "common", "int_to_address", "just_address", "just_seed", "mnemonic_constants", "ripemd",
         "secp256k1", "secp256k1_common", "secp256k1_field", "secp256k1_group", "secp256k1_prec",
         "secp256k1_scalar", "sha2")
ANCHOR = "void normal_private_child_from_private("
ASSEMBLED = "assembled/address.cl"


class KernelError(Exception):
    pass


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_hashes(path):
    out = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                digest, key = line.split(None, 1)
                out[key.strip()] = digest
    return out


def assemble_address(upstream: bytes, part: bytes) -> bytes:
    text = upstream.decode("utf-8")
    if text.count(ANCHOR) != 1:
        raise KernelError(f"address.cl: expected exactly one {ANCHOR!r}, found {text.count(ANCHOR)}")
    i = text.index(ANCHOR)
    return (text[:i] + part.decode("utf-8") + text[i:]).encode("utf-8")


def _download(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return r.read()


def fetch_all(hashes, part, fetch=_download, base=BASE):
    """{file name: bytes}, every file verified; raises KernelError on the first mismatch. Writes nothing."""
    files = {}
    for name in NAMES:
        data = fetch(f"{base}/{name}.cl")
        want = hashes.get(f"{name}.cl")
        if want is None:
            raise KernelError(f"{name}.cl has no entry in UPSTREAM.sha256")
        if sha256(data) != want:
            raise KernelError(f"{name}.cl: sha256 {sha256(data)} != recorded {want} (wrong commit, or the file changed)")
        if name == "address":
            data = assemble_address(data, part)
            if sha256(data) != hashes.get(ASSEMBLED):
                raise KernelError(f"assembled address.cl: sha256 {sha256(data)} != recorded {hashes.get(ASSEMBLED)}")
        files[f"{name}.cl"] = data
    return files


def write_all(dest, files):
    os.makedirs(dest, exist_ok=True)
    for name, data in files.items():
        tmp = os.path.join(dest, f".{name}.tmp")
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, os.path.join(dest, name))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dest", default=KERNELS)
    ap.add_argument("--base", default=BASE)
    args = ap.parse_args(argv)
    hashes = load_hashes(os.path.join(args.dest, "UPSTREAM.sha256"))
    with open(os.path.join(args.dest, "address_p2pkh.part"), "rb") as f:
        part = f.read()
    try:
        files = fetch_all(hashes, part, base=args.base)
    except KernelError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"could not download ({e}); nothing written", file=sys.stderr)
        return 2
    write_all(args.dest, files)
    for name in files:
        print(f"ok  {name}")
    print(f"{len(files)} kernels written to {os.path.relpath(args.dest)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
