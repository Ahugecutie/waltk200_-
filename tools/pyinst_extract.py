"""
Minimal PyInstaller (onefile/onedir) extractor.

Goal: extract the embedded PYZ/PKG contents from a PyInstaller-built executable
into <exe>_extracted/ for inspection.

This is a pragmatic extractor intended for local analysis workflows.
It supports common PyInstaller archive layouts used by modern bootloaders.

Usage:
  python tools/pyinst_extract.py "C:\\path\\to\\app.exe"
"""

from __future__ import annotations

import io
import os
import struct
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple, Literal


MAGIC = b"MEI\014\013\012\013\016"  # PyInstaller archive magic used by bootloader


def _u32(b: bytes, off: int) -> int:
    return struct.unpack_from("<I", b, off)[0]

def _be32(b: bytes, off: int) -> int:
    return struct.unpack_from(">I", b, off)[0]


def _u64(b: bytes, off: int) -> int:
    return struct.unpack_from("<Q", b, off)[0]


@dataclass(frozen=True)
class ArchiveInfo:
    exe_path: Path
    cookie_offset: int
    archive_size: int
    archive_start: int
    toc_offset: int
    toc_size: int
    pyver: int
    pylibname: str
    endian: Literal["<", ">"]


def find_cookie(exe_bytes: bytes) -> int:
    """
    Find the last occurrence of PyInstaller's archive cookie.
    Cookie ends the executable and is located near EOF.
    """
    idx = exe_bytes.rfind(MAGIC)
    if idx < 0:
        raise RuntimeError("PyInstaller cookie magic not found (not a PyInstaller exe?)")
    return idx


def parse_cookie(exe_path: Path, exe_bytes: bytes) -> ArchiveInfo:
    """
    Parse cookie at the given offset.

    Cookie layout differs across bootloader versions; this parser is tolerant and
    tries to interpret fields based on size heuristics.
    """
    cookie_offset = find_cookie(exe_bytes)
    # PyInstaller cookie sits at EOF. Most common layouts:
    #
    # v2x/v3x style (32-bit fields + 64-byte pylibname):
    #   8s magic
    #   I  pkg_length
    #   I  toc_offset
    #   I  toc_length
    #   I  pyvers
    #   64s pylibname (null-terminated)
    #
    # Some newer bootloaders use 64-bit fields for lengths/offsets, but the key
    # invariant remains:
    #   archive_start = cookie_offset - pkg_length
    # and toc_offset is relative to archive_start.
    tail = exe_bytes[cookie_offset:]
    if len(tail) < 8 + 4 * 4:
        raise RuntimeError("Cookie too small; unsupported PyInstaller variant.")

    magic = tail[:8]
    if magic != MAGIC:
        raise RuntimeError("Cookie magic mismatch.")

    def try_cookie(endian: Literal["<", ">"]) -> Optional[Tuple[int, int, int, int, int, str, Literal["<", ">"]]]:
        # 32-bit fields + 64-byte pylibname
        if endian == "<":
            pkg = _u32(tail, 8)
            toc_off = _u32(tail, 12)
            toc_len = _u32(tail, 16)
            pyv = _u32(tail, 20)
        else:
            pkg = _be32(tail, 8)
            toc_off = _be32(tail, 12)
            toc_len = _be32(tail, 16)
            pyv = _be32(tail, 20)

        pylib = ""
        pylibname_raw = tail[24 : 24 + 64] if len(tail) >= 24 + 64 else b""
        if pylibname_raw:
            pylib = pylibname_raw.split(b"\x00", 1)[0].decode("utf-8", errors="replace")

        # Many bootloaders store pkg_length INCLUDING cookie.
        file_len = len(exe_bytes)
        pkg_start = file_len - pkg
        toc_abs = pkg_start + toc_off
        if not (pkg > 0 and toc_len > 0 and 0 <= pkg_start < file_len and 0 <= toc_abs < file_len and toc_abs + toc_len <= file_len):
            return None

        # Additional sanity: first TOC entry size should be plausible in this endian.
        first4 = exe_bytes[toc_abs : toc_abs + 4]
        if len(first4) < 4:
            return None
        entry_size = struct.unpack(endian + "I", first4)[0]
        if not (18 <= entry_size <= 4096):
            return None

        return (pkg, pkg_start, toc_off, toc_len, pyv, pylib, endian)

    chosen = try_cookie(">") or try_cookie("<")
    if chosen is None:
        # Fallback: 64-bit cookie variants (rare). Not implemented here.
        raise RuntimeError("Cookie parsed but offsets/sizes are not sane; unsupported PyInstaller variant.")

    archive_size, archive_start, toc_offset, toc_size, pyver, pylibname, endian = chosen

    return ArchiveInfo(
        exe_path=exe_path,
        cookie_offset=cookie_offset,
        archive_size=archive_size,
        archive_start=archive_start,
        toc_offset=toc_offset,
        toc_size=toc_size,
        pyver=pyver,
        pylibname=pylibname,
        endian=endian,
    )


@dataclass(frozen=True)
class TocEntry:
    name: str
    entry_offset: int
    compressed_size: int
    uncompressed_size: int
    is_compressed: bool
    typecode: str


def parse_toc(exe_bytes: bytes, archive_start: int, toc_offset: int, toc_size: int) -> List[TocEntry]:
    """
    TOC entry format (common):
      I entry_size
      I entry_offset
      I compressed_size
      I uncompressed_size
      B is_compressed
      c typecode
      ... padding ...
      null-terminated name

    We'll parse using entry_size stepping; this is how bootloader stores TOC.
    """
    toc_abs = archive_start + int(toc_offset)
    toc = exe_bytes[toc_abs : toc_abs + int(toc_size)]
    entries: List[TocEntry] = []
    i = 0
    while i < len(toc):
        if i + 4 > len(toc):
            break
        # TOC in some builds is big-endian. Detect per-entry: if LE looks insane but BE is small, use BE.
        le = _u32(toc, i)
        be = _be32(toc, i)
        entry_size = le if (18 <= le <= 4096 and i + le <= len(toc)) else be
        if entry_size <= 0 or i + entry_size > len(toc):
            break
        entry = toc[i : i + entry_size]
        # Same endianness as entry_size decision
        is_be = entry_size == be
        if is_be:
            entry_offset = _be32(entry, 4)
            csize = _be32(entry, 8)
            usize = _be32(entry, 12)
        else:
            entry_offset = _u32(entry, 4)
            csize = _u32(entry, 8)
            usize = _u32(entry, 12)
        is_compressed = entry[16] != 0
        typecode = chr(entry[17])
        # Name starts at 18 until first NUL
        raw_name = entry[18:]
        nul = raw_name.find(b"\x00")
        if nul >= 0:
            raw_name = raw_name[:nul]
        try:
            name = raw_name.decode("utf-8", errors="replace")
        except Exception:
            name = repr(raw_name)
        entries.append(
            TocEntry(
                name=name,
                entry_offset=archive_start + entry_offset,  # TOC offsets are relative to archive_start
                compressed_size=csize,
                uncompressed_size=usize,
                is_compressed=is_compressed,
                typecode=typecode,
            )
        )
        i += entry_size
    return entries


def safe_write(out_dir: Path, rel_name: str, data: bytes) -> Path:
    # Avoid absolute paths / traversal
    rel_name = rel_name.replace("\\", "/")
    rel_name = rel_name.lstrip("/").replace("..", "__")
    target = out_dir / rel_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def extract(exe_path: Path) -> Tuple[ArchiveInfo, List[TocEntry], Path]:
    exe_bytes = exe_path.read_bytes()
    info = parse_cookie(exe_path, exe_bytes)

    out_dir = exe_path.with_name(exe_path.name + "_extracted")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Parse TOC and extract
    entries = parse_toc(exe_bytes, info.archive_start, info.toc_offset, info.toc_size)

    # Write summary
    (out_dir / "_archive_info.txt").write_text(
        "\n".join(
            [
                f"exe_path={exe_path}",
                f"cookie_offset={info.cookie_offset}",
                f"archive_size={info.archive_size}",
                f"archive_start={info.archive_start}",
                f"toc_offset={info.toc_offset}",
                f"toc_size={info.toc_size}",
                f"pyver={info.pyver}",
                f"pylibname={info.pylibname}",
                f"entries={len(entries)}",
            ]
        ),
        encoding="utf-8",
    )

    # Extract payloads
    for e in entries:
        start = e.entry_offset
        end = start + e.compressed_size
        blob = exe_bytes[start:end]
        if e.is_compressed:
            try:
                blob = zlib.decompress(blob)
            except Exception:
                # Some entries might not be zlib despite flag; keep raw
                pass
        # Name can be empty for some internal entries; still keep them
        name = e.name if e.name else f"_noname_{e.typecode}_{e.entry_offset:x}"
        # Add synthetic extension for known blobs
        if e.typecode in {"z", "Z"} and not name.lower().endswith(".pyz"):
            name = name + ".pyz"
        safe_write(out_dir, name, blob)

    # Create a TOC CSV-like dump for quick review
    toc_lines = ["typecode,is_compressed,entry_offset,compressed_size,uncompressed_size,name"]
    for e in entries:
        toc_lines.append(
            ",".join(
                [
                    e.typecode,
                    "1" if e.is_compressed else "0",
                    str(e.entry_offset),
                    str(e.compressed_size),
                    str(e.uncompressed_size),
                    e.name.replace(",", "_"),
                ]
            )
        )
    (out_dir / "_toc.csv").write_text("\n".join(toc_lines), encoding="utf-8")
    return info, entries, out_dir


def main(argv: List[str]) -> int:
    if len(argv) != 2:
        print("Usage: python tools/pyinst_extract.py <path-to-exe>", file=sys.stderr)
        return 2
    exe_path = Path(argv[1]).expanduser().resolve()
    if not exe_path.exists():
        print(f"File not found: {exe_path}", file=sys.stderr)
        return 2
    info, entries, out_dir = extract(exe_path)
    print(f"OK: extracted {len(entries)} TOC entries to: {out_dir}")
    print(f"Hint: check {out_dir / '_toc.csv'} and search for .pyc/.pyd/.dll/.db/.json/.ini")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))


