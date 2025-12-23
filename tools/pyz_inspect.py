"""
Inspect a PyInstaller PYZ (ZlibArchive) file.

This script:
  - parses the PYZ header
  - loads the marshalled TOC
  - optionally scans decompressed entries for keyword strings

Usage:
  python tools/pyz_inspect.py <path-to-PYZ.pyz> [--scan]
"""

from __future__ import annotations

import argparse
import marshal
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Tuple, Union, Any, Optional


@dataclass(frozen=True)
class PyzEntry:
    name: str
    pos: int
    length: int
    is_pkg: int


TocType = Union[Dict[str, Tuple[int, int, int]], List[Tuple[str, Tuple[int, int, int]]], List[Any]]


def parse_pyz(pyz_path: Path) -> Tuple[bytes, int, TocType]:
    b = pyz_path.read_bytes()
    if len(b) < 16:
        raise RuntimeError("PYZ too small")
    if b[:4] != b"PYZ\0":
        raise RuntimeError("Not a PYZ archive (missing PYZ\\0 header)")
    pyc_magic = b[4:8]  # matches importlib.util.MAGIC_NUMBER for the build
    # toc offset is typically big-endian u32
    toc_off_be = struct.unpack_from(">I", b, 8)[0]
    toc_off_le = struct.unpack_from("<I", b, 8)[0]

    # pick the offset that looks sane
    toc_off = None
    for cand in (toc_off_be, toc_off_le):
        if 0 < cand < len(b) - 8:
            toc_off = cand
            break
    if toc_off is None:
        raise RuntimeError(f"Cannot determine TOC offset (be={toc_off_be}, le={toc_off_le}, len={len(b)})")

    toc_blob = b[toc_off:]
    try:
        toc = marshal.loads(toc_blob)
    except Exception as e:
        raise RuntimeError(f"Failed to marshal.loads TOC at offset {toc_off}") from e

    # Some PyInstaller builds store TOC as list of (name, (is_pkg, pos, length))
    if not isinstance(toc, (dict, list)):
        raise RuntimeError(f"Unexpected TOC type: {type(toc)}")
    return pyc_magic, toc_off, toc  # toc: dict or list


def _coerce_toc_to_items(toc: TocType) -> Iterable[Tuple[str, Tuple[int, int, int]]]:
    if isinstance(toc, dict):
        return toc.items()
    if isinstance(toc, list):
        # Expect list of (name, (is_pkg, pos, length))
        out: List[Tuple[str, Tuple[int, int, int]]] = []
        for it in toc:
            if isinstance(it, tuple) and len(it) == 2 and isinstance(it[0], (str, bytes)) and isinstance(it[1], tuple):
                name = it[0].decode("utf-8", errors="replace") if isinstance(it[0], bytes) else str(it[0])
                val = it[1]
                out.append((name, val))  # type: ignore[arg-type]
        return out
    return []


def iter_entries(toc: TocType) -> List[PyzEntry]:
    out: List[PyzEntry] = []
    for name, val in _coerce_toc_to_items(toc):
        try:
            is_pkg, pos, length = val
            out.append(PyzEntry(name=str(name), pos=int(pos), length=int(length), is_pkg=int(is_pkg)))
        except Exception:
            continue
    out.sort(key=lambda e: e.name)
    return out


def extract_entry_bytes(pyz_bytes: bytes, entry: PyzEntry) -> bytes:
    raw = pyz_bytes[entry.pos : entry.pos + entry.length]
    try:
        return zlib.decompress(raw)
    except Exception:
        return raw


def _sanitize_ascii(b: bytes) -> str:
    # Replace non-printables with '.'
    return bytes((c if 32 <= c <= 126 else 46) for c in b).decode("ascii", errors="ignore")


def snip_entries(
    pyz_path: Path,
    entries: List[PyzEntry],
    needles: Iterable[str],
    filter_prefix: Optional[str] = None,
    max_modules: int = 80,
    max_snips_per_module: int = 3,
) -> None:
    pyz_bytes = pyz_path.read_bytes()
    needles_b = [n.encode("utf-8", errors="ignore") for n in needles]
    shown_modules = 0
    for e in entries:
        if filter_prefix and not e.name.startswith(filter_prefix):
            continue
        blob = extract_entry_bytes(pyz_bytes, e)
        low = blob.lower()
        hits = [n for n in needles_b if n and n.lower() in low]
        if not hits:
            continue
        shown_modules += 1
        print(f"\n===MODULE {e.name} pkg={e.is_pkg} pos={e.pos} len={e.length} hits={[h.decode('utf-8','ignore') for h in hits]}===")
        snips = 0
        for nb in hits:
            idx = low.find(nb.lower())
            if idx < 0:
                continue
            start = max(0, idx - 140)
            end = min(len(blob), idx + 320)
            print(_sanitize_ascii(blob[start:end]))
            snips += 1
            if snips >= max_snips_per_module:
                break
        if shown_modules >= max_modules:
            break


def scan_entries(pyz_path: Path, entries: List[PyzEntry], needles: Iterable[str]) -> Dict[str, List[str]]:
    pyz_bytes = pyz_path.read_bytes()
    needles_l = [n.lower() for n in needles]
    hits: Dict[str, List[str]] = {}
    for e in entries:
        blob = extract_entry_bytes(pyz_bytes, e)
        # bytecode contains ASCII/UTF-8 constants often; do a cheap scan
        try:
            s = blob.decode("utf-8", errors="ignore").lower()
        except Exception:
            continue
        matched = [needles[i] for i, n in enumerate(needles_l) if n and n in s]
        if matched:
            hits[e.name] = matched
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pyz", type=Path)
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--snip", action="store_true", help="print snippet contexts for matching modules")
    ap.add_argument("--filter-prefix", default=None, help="only consider modules starting with this prefix (e.g. app.)")
    args = ap.parse_args()

    pyz_path: Path = args.pyz
    pyc_magic, toc_off, toc = parse_pyz(pyz_path)
    entries = iter_entries(toc)

    print(f"PYZ={pyz_path}")
    print(f"pyc_magic={pyc_magic.hex().upper()}")
    print(f"toc_offset={toc_off}")
    print(f"entries={len(entries)}")

    # print a quick sample of non-stdlib looking modules
    # heuristic: show modules without a dot (top-level) and not obviously stdlib
    top = [e for e in entries if "." not in e.name][:50]
    if top:
        print("\nTOPLEVEL_MODULES(sample 50):")
        for e in top:
            print(f"- {e.name} (pkg={e.is_pkg} pos={e.pos} len={e.length})")

    if args.scan:
        needles = [
            "fastapi",
            "uvicorn",
            "starlette",
            "sqlalchemy",
            "sqlite",
            "requests",
            "httpx",
            "websocket",
            "socketio",
            "eel",
            "cef",
            "webview",
            "127.0.0.1",
            "localhost",
            "0.0.0.0",
            ":8000",
            ":5000",
            ":3000",
        ]
        print("\nSCANNING...")
        hits = scan_entries(pyz_path, entries, needles)
        print(f"HITS_MODULES={len(hits)}")
        # show top 80 hits (module -> keywords)
        shown = 0
        for name in sorted(hits.keys()):
            print(f"- {name}: {', '.join(sorted(set(hits[name])))}")
            shown += 1
            if shown >= 80:
                break

    if args.snip:
        # tighter needles to find actual server start points
        needles = [
            "uvicorn.run",
            "uvicorn",
            "FastAPI(",
            "app = FastAPI",
            "host=",
            "port=",
            "0.0.0.0",
            "127.0.0.1",
            "localhost",
            ":8000",
            ":5000",
            "eel",
            "eel.start",
            "cef",
            "webview",
        ]
        print("\nSNIPPETS...")
        snip_entries(pyz_path, entries, needles, filter_prefix=args.filter_prefix)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


