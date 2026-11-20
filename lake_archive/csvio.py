"""CSV parsing and rendering for logger exports (stdlib only).

Each data row of a logger CSV is one frame and carries:

    seq         -- integer serial number of the frame
    sampled_at  -- UTC sampling instant
    stored      -- temperature in integer millidegrees Celsius, either an
                   absolute value or a delta relative to an *earlier* frame
                   referenced by ``base``
    base        -- (delta rows only) the seq of the referenced earlier frame

Column names are matched case-insensitively; a small set of Chinese and
English header spellings is accepted.  The layout of the uploaded file (header
names, column order, delimiter, line terminator, time spelling) is retained so
that a download reproduces the same CSV format.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone


class CsvFormatError(ValueError):
    """Raised when an uploaded CSV cannot be interpreted as a frame list."""


HEADER_SEQ = ("seq", "序号", "frame", "帧号")
HEADER_TIME = ("utc", "sampled_at", "sampled_at_utc", "time", "采样时间", "时间")
HEADER_VALUE = ("value", "value_millidegrees_c", "value_mc", "temp", "temperature",
                "温度", "值")
HEADER_BASE = ("base", "base_seq", "基准", "基准帧")
HEADER_KIND = ("kind", "mode", "类型", "方式")

TIME_FORMATS = (
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%d %H:%M:%S%z",
)

_INT_RE = re.compile(r"^[+-]?\d+$")


@dataclass(frozen=True)
class FrameRow:
    seq: int
    sampled_at: str          # normalized UTC, ends with 'Z'
    sampled_at_raw: str      # as written in the file, kept for export
    stored_kind: str         # 'absolute' | 'delta'
    stored_mc: int
    base_seq: int | None
    kind_raw: str = ""             # kind token exactly as written in the file
    extras: tuple[str, ...] = ()   # values of columns the app does not manage


@dataclass(frozen=True)
class CsvLayout:
    header: tuple[str, ...]
    delimiter: str
    lineterminator: str
    col_seq: int
    col_time: int
    col_value: int
    col_base: int | None
    col_kind: int | None
    extra_cols: tuple[int, ...] = ()

    def to_json(self) -> str:
        return json.dumps({
            "header": list(self.header),
            "delimiter": self.delimiter,
            "lineterminator": self.lineterminator,
            "col_seq": self.col_seq,
            "col_time": self.col_time,
            "col_value": self.col_value,
            "col_base": self.col_base,
            "col_kind": self.col_kind,
            "extra_cols": list(self.extra_cols),
        }, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "CsvLayout":
        d = json.loads(raw)
        return cls(
            header=tuple(d["header"]),
            delimiter=d["delimiter"],
            lineterminator=d["lineterminator"],
            col_seq=d["col_seq"],
            col_time=d["col_time"],
            col_value=d["col_value"],
            col_base=d["col_base"],
            col_kind=d["col_kind"],
            extra_cols=tuple(d.get("extra_cols", ())),
        )


def _norm(name: str) -> str:
    return (name or "").strip().lower()


def _pick(header, candidates):
    seen = {_norm(h): i for i, h in enumerate(header)}
    for cand in candidates:
        if _norm(cand) in seen:
            return seen[_norm(cand)]
    return None


def parse_utc(text: str) -> str:
    """Normalize a UTC sampling time to ``YYYY-MM-DDTHH:MM:SS[.ffffff]Z``."""
    raw = (text or "").strip()
    if not raw:
        raise CsvFormatError("missing sampling time")
    body = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    for fmt in TIME_FORMATS:
        try:
            dt = datetime.strptime(body, fmt)
        except ValueError:
            continue
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        # A time without an explicit offset is the logger's UTC stamp.
        out = dt.strftime("%Y-%m-%dT%H:%M:%S")
        if dt.microsecond:
            out += ".%06d" % dt.microsecond
        return out + "Z"
    raise CsvFormatError("sampling time is not a recognized UTC instant: %r" % text)


def _parse_int(raw: str, what: str, line: int) -> int:
    text = (raw or "").strip()
    if not _INT_RE.match(text):
        raise CsvFormatError(
            "line %d: %s must be an integer (millidegrees), got %r" % (line, what, raw))
    return int(text, 10)


def parse_csv(data: bytes) -> tuple[list[FrameRow], CsvLayout]:
    """Parse a logger CSV into validated frames ordered by seq, plus its layout."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CsvFormatError("file is not UTF-8 encoded: %s" % exc) from exc

    lineterminator = "\r\n" if "\r\n" in text else "\n"
    first_line = text.split("\n", 1)[0].rstrip("\r")
    counts = {d: first_line.count(d) for d in (",", ";", "\t")}
    delimiter = max(counts, key=counts.get) if max(counts.values()) else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        raise CsvFormatError("file is empty")

    i_seq = _pick(header, HEADER_SEQ)
    i_time = _pick(header, HEADER_TIME)
    i_value = _pick(header, HEADER_VALUE)
    i_base = _pick(header, HEADER_BASE)
    i_kind = _pick(header, HEADER_KIND)

    if i_seq is None or i_time is None or i_value is None:
        raise CsvFormatError(
            "header must provide seq, sampling time and temperature columns; got %r"
            % (header,))
    # A file with neither a kind nor a base column stores every frame as an
    # absolute value.

    width = max([i for i in (i_seq, i_time, i_value, i_base, i_kind) if i is not None]) + 1
    managed = {i_seq, i_time, i_value}
    if i_base is not None:
        managed.add(i_base)
    if i_kind is not None:
        managed.add(i_kind)
    extra_cols = [i for i in range(len(header)) if i not in managed]

    rows: list[FrameRow] = []
    seen: dict[int, FrameRow] = {}
    for line_no, row in enumerate(reader, start=2):
        if not row or all(not (c or "").strip() for c in row):
            continue
        need = max(width, max(extra_cols) + 1 if extra_cols else 0)
        if len(row) < need:
            raise CsvFormatError(
                "line %d: expected %d columns, got %d" % (line_no, need, len(row)))

        seq = _parse_int(row[i_seq], "seq", line_no)
        if seq < 0:
            raise CsvFormatError("line %d: seq must be non-negative" % line_no)
        if seq in seen:
            raise CsvFormatError("line %d: duplicate seq %d" % (line_no, seq))
        raw_time = row[i_time].strip()
        sampled_at = parse_utc(raw_time)
        stored_mc = _parse_int(row[i_value], "temperature value", line_no)

        kind_raw = (row[i_kind].strip().lower() if i_kind is not None else "")
        base_raw = (row[i_base].strip() if i_base is not None else "")

        if kind_raw in ("absolute", "abs", "value", "绝对", "绝对值"):
            kind = "absolute"
        elif kind_raw in ("delta", "diff", "relative", "相对", "差值"):
            kind = "delta"
        elif kind_raw == "":
            # No explicit kind: a base reference means delta, otherwise absolute.
            kind = "delta" if base_raw and base_raw.upper() not in ("NULL", "NONE", "-") else "absolute"
        else:
            raise CsvFormatError("line %d: unknown kind %r" % (line_no, row[i_kind]))

        if kind == "absolute":
            if base_raw and base_raw.upper() not in ("NULL", "NONE", "-"):
                raise CsvFormatError(
                    "line %d: absolute frame %d must not name a base" % (line_no, seq))
            base_seq = None
        else:
            if not base_raw or base_raw.upper() in ("NULL", "NONE", "-"):
                raise CsvFormatError(
                    "line %d: delta frame %d must name a base seq" % (line_no, seq))
            base_seq = _parse_int(base_raw, "base seq", line_no)
            if base_seq >= seq:
                raise CsvFormatError(
                    "line %d: base seq %d must be earlier than frame %d"
                    % (line_no, base_seq, seq))

        fr = FrameRow(seq=seq, sampled_at=sampled_at, sampled_at_raw=raw_time,
                      stored_kind=kind, stored_mc=stored_mc, base_seq=base_seq,
                      kind_raw=(row[i_kind] if i_kind is not None else ""),
                      extras=tuple(row[i] for i in extra_cols))
        seen[seq] = fr
        rows.append(fr)

    if not rows:
        raise CsvFormatError("file contains no frames")
    for fr in rows:
        if fr.stored_kind == "delta" and fr.base_seq not in seen:
            raise CsvFormatError(
                "frame %d references missing base frame %d" % (fr.seq, fr.base_seq))
    rows.sort(key=lambda f: f.seq)

    layout = CsvLayout(
        header=tuple(header), delimiter=delimiter, lineterminator=lineterminator,
        col_seq=i_seq, col_time=i_time, col_value=i_value,
        col_base=i_base, col_kind=i_kind, extra_cols=tuple(extra_cols),
    )
    return rows, layout


def render_csv(frames, layout: CsvLayout) -> bytes:
    """Render frames (FrameView objects) in the uploaded file's CSV layout.

    Columns this application does not manage are echoed back verbatim.
    """
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=layout.delimiter,
                        lineterminator=layout.lineterminator)
    writer.writerow(list(layout.header))
    for fr in sorted(frames, key=lambda f: f.seq):
        values = [""] * len(layout.header)
        values[layout.col_seq] = str(fr.seq)
        values[layout.col_time] = fr.sampled_at_raw
        values[layout.col_value] = str(fr.stored_mc)
        if layout.col_kind is not None:
            # Echo the token the file used, which may be blank when the kind
            # was inferred from a base reference.  The kind itself never
            # changes, so the token stays valid.
            values[layout.col_kind] = fr.kind_raw
        if layout.col_base is not None:
            values[layout.col_base] = "" if fr.base_seq is None else str(fr.base_seq)
        extras = fr.extras or ()
        for col, text in zip(layout.extra_cols, extras):
            values[col] = text
        writer.writerow(values)
    return buf.getvalue().encode("utf-8")
