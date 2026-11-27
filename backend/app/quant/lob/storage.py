"""Append-only hourly gzip-CSV store for the order-book recorder, with a manifest, retention and a disk budget.

Layout: {root}/{stream}/{SYMBOL}/{YYYY-MM-DD}/{HH}.csv.gz (UTC, partitioned by each row's own timestamp).

Each flush appends one gzip *member* to the hour's file (a multi-member gzip is still one valid .gz), so a crash loses
at most the rows buffered since the last flush. If the process dies mid-write, the reader drops the truncated tail
member. Once an hour is over, the file is rewritten as a single member at level 9 (better ratio, same rows).

The manifest is one small JSON per UTC day ({root}/_manifest/{YYYY-MM-DD}.json): rows, bytes, first/last ts per file.
"""
from __future__ import annotations

import gzip
import io
import json
import os
import shutil
import zlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd

LEVELS = 20
BOOK_COLS = (["ts", "u", "src"] + [f"bp{i}" for i in range(1, LEVELS + 1)] + [f"bq{i}" for i in range(1, LEVELS + 1)]
             + [f"ap{i}" for i in range(1, LEVELS + 1)] + [f"aq{i}" for i in range(1, LEVELS + 1)])
# Per-second trades. vwap covers both sides; a_last is the second's last aggregate id, so a stretch is complete when
# a_last[i] - a_last[i-1] == n[i]. (Separate buy/sell quote sums and a_first cost 38% more bytes; dropped.)
TRADES_1S_COLS = ["ts", "n", "n_buy", "buy_qty", "sell_qty", "vwap", "high", "low", "last", "a_last"]
TRADES_RAW_COLS = ["ts", "a", "price", "qty", "is_buyer_maker"]
SCHEMAS: dict[str, list[str]] = {"book": BOOK_COLS, "trades_1s": TRADES_1S_COLS, "trades_raw": TRADES_RAW_COLS}
HOUR_MS = 3_600_000
MANIFEST_DIR = "_manifest"


def hour_parts(ts_ms: int) -> tuple[str, str]:
    d = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    return d.strftime("%Y-%m-%d"), d.strftime("%H")


def rel_path(stream: str, symbol: str, ts_ms: int) -> str:
    day, hh = hour_parts(ts_ms)
    return f"{stream}/{symbol}/{day}/{hh}.csv.gz"


def path_hour_ms(rel: str) -> int | None:
    """Start of the hour a file covers, parsed from its path (stream/SYMBOL/YYYY-MM-DD/HH.csv.gz)."""
    parts = rel.replace(os.sep, "/").split("/")
    try:
        d = datetime.strptime(f"{parts[-2]} {parts[-1][:2]}", "%Y-%m-%d %H").replace(tzinfo=UTC)
    except (ValueError, IndexError):
        return None
    return int(d.timestamp() * 1000)


def read_gz_tolerant(path: Path) -> bytes:
    """Decompress every complete gzip member; drop a truncated or corrupt tail (crash mid-flush)."""
    data = path.read_bytes()
    out: list[bytes] = []
    while data:
        d = zlib.decompressobj(31)
        try:
            chunk = d.decompress(data)
        except zlib.error:
            break
        if not d.eof:
            break
        out.append(chunk)
        data = d.unused_data
    return b"".join(out)


def read_file(path: Path, stream: str) -> pd.DataFrame:
    cols = SCHEMAS[stream]
    raw = read_gz_tolerant(path)
    header = ",".join(cols).encode()
    if not raw.startswith(header):  # first member lost in a crash: later members carry no header
        raw = header + b"\n" + raw
    return pd.read_csv(io.BytesIO(raw), dtype={"src": "string"} if stream == "book" else None)


# ---------------------------------------------------------------- manifest
class Manifest:
    def __init__(self, root: Path):
        self.root = Path(root)
        self._days: dict[str, dict[str, dict]] = {}
        self._dirty: set[str] = set()

    def _file(self, day: str) -> Path:
        return self.root / MANIFEST_DIR / f"{day}.json"

    def day(self, day: str) -> dict[str, dict]:
        if day not in self._days:
            f = self._file(day)
            try:
                self._days[day] = json.loads(f.read_text()) if f.exists() else {}
            except (OSError, ValueError):
                self._days[day] = {}
        return self._days[day]

    def record(self, rel: str, rows: int, size: int, first_ts: int, last_ts: int) -> None:
        day = rel.split("/")[2]
        e = self.day(day).setdefault(rel, {"rows": 0, "bytes": 0, "first_ts": first_ts, "last_ts": last_ts})
        e["rows"] += rows
        e["bytes"] = size
        e["first_ts"] = min(e["first_ts"], first_ts)
        e["last_ts"] = max(e["last_ts"], last_ts)
        self._dirty.add(day)

    def set_bytes(self, rel: str, size: int, compact: bool = False) -> None:
        e = self.day(rel.split("/")[2]).get(rel)
        if e is not None:
            e["bytes"] = size
            if compact:
                e["compact"] = True
            self._dirty.add(rel.split("/")[2])

    def remove(self, rel: str) -> None:
        day = rel.split("/")[2]
        if self.day(day).pop(rel, None) is not None:
            self._dirty.add(day)
        if not self.day(day):
            self._days.pop(day, None)
            self._dirty.discard(day)
            try:
                self._file(day).unlink()
            except FileNotFoundError:
                pass

    def save(self) -> None:
        for day in list(self._dirty):
            f = self._file(day)
            f.parent.mkdir(parents=True, exist_ok=True)
            tmp = f.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._days.get(day, {}), sort_keys=True))
            os.replace(tmp, f)
        self._dirty.clear()

    def rows_by_day(self, day: str) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = defaultdict(int)
        for rel, e in self.day(day).items():
            stream, sym = rel.split("/")[:2]
            out[(stream, sym)] += e["rows"]
        return dict(out)

    def last_ts(self, days: list[str]) -> dict[tuple[str, str], int]:
        out: dict[tuple[str, str], int] = {}
        for day in days:
            for rel, e in self.day(day).items():
                k = tuple(rel.split("/")[:2])
                out[k] = max(out.get(k, 0), e["last_ts"])
        return out

    def is_compact(self, rel: str) -> bool:
        return bool(self.day(rel.split("/")[2]).get(rel, {}).get("compact"))


# ---------------------------------------------------------------- budget
@dataclass
class Budget:
    """Hard disk cap for the store plus a floor on free disk for the whole machine."""

    root: Path
    cap_bytes: int
    min_free_bytes: int = 0
    used_bytes: int = 0
    refusing: bool = False
    reason: str | None = None
    refused_rows: int = 0
    _free_bytes: int | None = field(default=None, repr=False)

    def refresh(self) -> int:
        self.used_bytes = dir_size(self.root)
        try:
            self._free_bytes = shutil.disk_usage(self.root if self.root.exists() else self.root.parent).free
        except OSError:
            self._free_bytes = None
        return self.used_bytes

    @property
    def free_bytes(self) -> int | None:
        return self._free_bytes

    def can_write(self, nbytes: int) -> bool:
        if self.used_bytes + nbytes > self.cap_bytes:
            self.refusing, self.reason = True, f"store at cap ({self.used_bytes / 1e9:.2f} of {self.cap_bytes / 1e9:.2f} GB)"
            return False
        if self.min_free_bytes and self._free_bytes is not None and self._free_bytes - nbytes < self.min_free_bytes:
            self.refusing, self.reason = True, f"machine free disk below {self.min_free_bytes / 1e9:.1f} GB"
            return False
        self.refusing, self.reason = False, None
        return True

    def note(self, delta: int) -> None:
        self.used_bytes += delta
        if self._free_bytes is not None:
            self._free_bytes -= delta


def dir_size(root: Path) -> int:
    total = 0
    for dp, _, files in os.walk(root):
        for f in files:
            try:
                total += os.stat(os.path.join(dp, f)).st_size
            except FileNotFoundError:
                pass
    return total


# ---------------------------------------------------------------- writer
class HourlyStore:
    """Buffers rows per (stream, symbol, hour) and appends them as one gzip member per file per flush."""

    def __init__(self, root: Path, budget: Budget, manifest: Manifest | None = None, compact_grace_ms: int = 180_000):
        self.root = Path(root)
        self.budget = budget
        self.manifest = manifest or Manifest(self.root)
        self.compact_grace_ms = compact_grace_ms
        self._buf: dict[str, list[str]] = defaultdict(list)
        self._span: dict[str, tuple[int, int]] = {}
        self._touched: set[str] = set()  # files written by this process, candidates for compaction
        self.bytes_written = 0
        self.rows_written = 0
        self.last_flush_ms: int | None = None

    def add(self, stream: str, symbol: str, ts_ms: int, fields: list) -> None:
        rel = rel_path(stream, symbol, ts_ms)
        self._buf[rel].append(",".join([str(ts_ms)] + ["" if v is None else str(v) for v in fields]))
        lo, hi = self._span.get(rel, (ts_ms, ts_ms))
        self._span[rel] = (min(lo, ts_ms), max(hi, ts_ms))

    def pending_rows(self) -> int:
        return sum(len(v) for v in self._buf.values())

    def flush(self, now_ms: int) -> dict:
        """Write every buffer. Returns {"rows": n, "bytes": n, "refused": n}. Rows refused by the budget are dropped."""
        out = {"rows": 0, "bytes": 0, "refused": 0}
        bufs, spans = self._buf, self._span
        self._buf, self._span = defaultdict(list), {}
        for rel, lines in bufs.items():
            if not lines:
                continue
            stream = rel.split("/")[0]
            path = self.root / rel
            new = not path.exists()
            body = ("\n".join(lines) + "\n").encode()
            if new:
                body = (",".join(SCHEMAS[stream]) + "\n").encode() + body
            member = gzip.compress(body, compresslevel=6, mtime=0)
            if not self.budget.can_write(len(member)):
                out["refused"] += len(lines)
                self.budget.refused_rows += len(lines)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "ab") as fh:
                fh.write(member)
                fh.flush()
                os.fsync(fh.fileno())
            self.budget.note(len(member))
            lo, hi = spans[rel]
            self.manifest.record(rel, len(lines), path.stat().st_size, lo, hi)
            self._touched.add(rel)
            out["rows"] += len(lines)
            out["bytes"] += len(member)
        self.manifest.save()
        self.bytes_written += out["bytes"]
        self.rows_written += out["rows"]
        self.last_flush_ms = now_ms
        return out

    def compact_closed(self, now_ms: int) -> int:
        """Rewrite finished hours as one level-9 member. Returns bytes saved."""
        saved, done = 0, 0
        for rel in sorted(self._touched):
            start = path_hour_ms(rel)
            if start is None or now_ms < start + HOUR_MS + self.compact_grace_ms or rel in self._buf:
                continue
            self._touched.discard(rel)
            saved += self.compact(rel)
            done += 1
        if done:
            self.manifest.save()
        return saved

    def compact(self, rel: str) -> int:
        path = self.root / rel
        if not path.exists():
            return 0
        before = path.stat().st_size
        raw = read_gz_tolerant(path)
        tmp = path.with_suffix(".gz.tmp")
        tmp.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
        os.replace(tmp, path)
        after = path.stat().st_size
        self.budget.note(after - before)
        self.manifest.set_bytes(rel, after, compact=True)
        return before - after


# ---------------------------------------------------------------- retention
def list_files(root: Path) -> list[tuple[int, str]]:
    """(hour start ms, relative path) for every data file, oldest first."""
    root = Path(root)
    out = []
    for p in root.glob("*/*/*/*.csv.gz"):
        rel = p.relative_to(root).as_posix()
        if rel.startswith(MANIFEST_DIR):
            continue
        h = path_hour_ms(rel)
        if h is not None:
            out.append((h, rel))
    out.sort()
    return out


def apply_retention(root: Path, now_ms: int, max_age_days: float | None, cap_bytes: int | None = None,
                    evict_to_fit: bool = False, low_water: float = 0.9, protect_ms: int = 2 * HOUR_MS,
                    manifest: Manifest | None = None) -> list[str]:
    """Delete files past the age limit; if `evict_to_fit`, also delete oldest-first until the store is under
    `low_water * cap_bytes`. Files whose hour began within `protect_ms` of now are never deleted. Returns the deleted
    relative paths in deletion order."""
    root = Path(root)
    files = list_files(root)
    deleted: list[str] = []
    sizes = {rel: (root / rel).stat().st_size for _, rel in files}
    total = dir_size(root)
    cutoff = now_ms - max_age_days * 86_400_000 if max_age_days else None
    for hour, rel in files:
        if now_ms - hour < protect_ms:
            break
        too_old = cutoff is not None and hour + HOUR_MS <= cutoff
        over = evict_to_fit and cap_bytes is not None and total > low_water * cap_bytes
        if not (too_old or over):
            break  # oldest first: if this file may stay, every newer one may too
        try:
            (root / rel).unlink()
        except FileNotFoundError:
            continue
        total -= sizes[rel]
        deleted.append(rel)
        if manifest is not None:
            manifest.remove(rel)
        d = (root / rel).parent
        for _ in range(2):  # prune empty day and symbol directories
            try:
                d.rmdir()
            except OSError:
                break
            d = d.parent
    if manifest is not None:
        manifest.save()
    return deleted


# ---------------------------------------------------------------- reading
def _to_ms(x) -> int:
    if isinstance(x, int | float):
        return int(x)
    t = pd.Timestamp(x)
    if t.tzinfo is None:
        t = t.tz_localize("UTC")
    return int(t.timestamp() * 1000)


def merge_trades_1s(df: pd.DataFrame) -> pd.DataFrame:
    """Rows for the same second can appear twice (live part + REST backfill of a dropped stretch); combine them."""
    if df.empty or not df["ts"].duplicated().any():
        return df
    df = df.sort_values(["ts", "a_last"]).assign(_quote=lambda d: d["vwap"] * (d["buy_qty"] + d["sell_qty"]))
    g = df.groupby("ts", sort=True)
    out = g[["n", "n_buy", "buy_qty", "sell_qty", "_quote"]].sum()
    out["high"], out["low"], out["last"], out["a_last"] = g["high"].max(), g["low"].min(), g["last"].last(), g["a_last"].max()
    out["vwap"] = out["_quote"] / (out["buy_qty"] + out["sell_qty"])
    return out.reset_index()[TRADES_1S_COLS]


def load(symbol: str, stream: str, start, end, root: Path) -> pd.DataFrame:
    """Rows with start <= ts < end. `start`/`end`: epoch ms, datetime, pandas Timestamp or ISO string (UTC)."""
    if stream not in SCHEMAS:
        raise ValueError(f"unknown stream {stream!r}; one of {sorted(SCHEMAS)}")
    a, b = _to_ms(start), _to_ms(end)
    symbol = symbol.upper()
    frames = []
    a_day, b_day = hour_parts(max(a, 0))[0], hour_parts(min(b, 253_402_300_799_000))[0]
    sym_dir = Path(root) / stream / symbol
    days = sorted(d.name for d in sym_dir.iterdir() if d.is_dir()) if sym_dir.is_dir() else []
    for day in (d for d in days if a_day <= d <= b_day):
        for p in sorted((sym_dir / day).glob("*.csv.gz")):
            h = path_hour_ms(f"{day}/{p.name}")
            if h is not None and h + HOUR_MS > a and h < b:
                frames.append(read_file(p, stream))
    cols = SCHEMAS[stream]
    if not frames:
        df = pd.DataFrame(columns=cols)
    else:
        df = pd.concat([f for f in frames if not f.empty] or [frames[0]], ignore_index=True)
    df = df[(df["ts"] >= a) & (df["ts"] < b)] if len(df) else df
    if stream == "trades_1s":
        df = merge_trades_1s(df)
    df = df.sort_values("ts", kind="stable").reset_index(drop=True)
    if stream == "trades_raw" and len(df):
        df = df.drop_duplicates("a")
    df.insert(1, "time", pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True))
    return df


def today_str(now_ms: int) -> str:
    return datetime.fromtimestamp(now_ms / 1000, tz=UTC).strftime("%Y-%m-%d")


def yesterday_str(now_ms: int) -> str:
    return (datetime.fromtimestamp(now_ms / 1000, tz=UTC) - timedelta(days=1)).strftime("%Y-%m-%d")
