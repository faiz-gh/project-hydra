"""Header-driven parser for ``cockroach workload run`` output.

Column positions are bound from the generator's own header line, never guessed
from field counts. Each periodic interval emits one line per operation type,
with an unheaded trailing op label; the cumulative summary block has a
different header and is kept apart from per-interval samples. Aggregation
(sum throughput across ops, never pool latency) happens in :func:`aggregate_tick`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

# A header line is a run of column names padded with underscores, e.g.
#   _elapsed___errors__ops/sec(inst)___ops/sec(cum)__p50(ms)__p95(ms)__p99(ms)_pMax(ms)
_HEADER_RE = re.compile(r"^_+elapsed")
_UNDERSCORE_RUN_RE = re.compile(r"_+")
_ELAPSED_TOKEN_RE = re.compile(r"^\d+(?:\.\d+)?s$")
_NUMERIC_TOKEN_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")

#: Column names as printed by cockroach, mapped to canonical field names.
_COLUMN_ALIASES = {
    "elapsed": "elapsed_s",
    "errors": "errors_cum",
    "ops/sec(inst)": "tps",
    "ops/sec(cum)": "tps_cum",
    "ops(total)": "ops_total",
    "avg(ms)": "avg_ms",
    "p50(ms)": "p50_ms",
    "p95(ms)": "p95_ms",
    "p99(ms)": "p99_ms",
    "pMax(ms)": "pmax_ms",
    "pmax(ms)": "pmax_ms",
}

PERIODIC = "periodic"
SUMMARY = "summary"

#: Columns that identify a cumulative summary block rather than a periodic one.
_SUMMARY_MARKERS = frozenset({"ops_total", "avg_ms"})

#: Measured quantities. A trailing header token outside this set names the
#: operation-type column, which periodic and summary headers label differently.
_METRIC_COLUMNS = frozenset(_COLUMN_ALIASES.values())


class WorkloadParseError(RuntimeError):
    """Raised when output cannot be interpreted without positional guessing."""


@dataclass(frozen=True)
class Sample:
    """One parsed line of generator output.

    Only :data:`PERIODIC` samples are measurements; :data:`SUMMARY` samples are
    the generator's cumulative totals, kept as a cross-check.
    """

    kind: str
    elapsed_s: float
    op: str
    errors_cum: int
    values: dict[str, float]

    @property
    def tps(self) -> float:
        return self.values.get("tps", float("nan"))

    def latency_ms(self, quantile: str) -> float:
        return self.values.get(f"{quantile}_ms", float("nan"))


@dataclass
class _Header:
    columns: list[str]
    kind: str
    #: Name of the trailing header token that labels the operation type, when
    #: the block declares one. ``None`` when the label is emitted unheaded.
    op_column: str | None = None


@dataclass
class WorkloadParser:
    """Incremental, header-bound parser.

    Feed it lines in arrival order. It yields a :class:`Sample` for every data
    line and ``None`` for headers, blank lines and generator chatter. State is
    reset whenever a new header appears, so a run containing several blocks
    (init, run, summary) is handled without special-casing.
    """

    strict: bool = True
    _header: _Header | None = field(default=None, init=False, repr=False)
    unparsed: list[str] = field(default_factory=list, init=False, repr=False)

    # -- header handling ---------------------------------------------------
    @staticmethod
    def _parse_header(line: str) -> _Header:
        raw = [tok for tok in _UNDERSCORE_RUN_RE.split(line.strip()) if tok]
        columns = [_COLUMN_ALIASES.get(tok, tok) for tok in raw]

        # Strip a trailing op-type column so both block types bind the same way.
        op_column: str | None = None
        if columns and columns[-1] not in _METRIC_COLUMNS:
            op_column = columns.pop()

        kind = SUMMARY if _SUMMARY_MARKERS & set(columns) else PERIODIC
        return _Header(columns=columns, kind=kind, op_column=op_column)

    # -- line handling -----------------------------------------------------
    def feed(self, line: str) -> Sample | None:
        text = line.rstrip("\n")
        stripped = text.strip()
        if not stripped:
            return None

        if _HEADER_RE.match(stripped):
            self._header = self._parse_header(stripped)
            return None

        fields = stripped.split()
        if not _ELAPSED_TOKEN_RE.match(fields[0]):
            self.unparsed.append(stripped)
            return None

        if self._header is None:
            if self.strict:
                raise WorkloadParseError(
                    "encountered a data line before any header line; refusing to "
                    f"infer column positions: {stripped!r}"
                )
            self.unparsed.append(stripped)
            return None

        return self._bind(fields, self._header)

    def _bind(self, fields: list[str], header: _Header) -> Sample:
        ncols = len(header.columns)
        if len(fields) == ncols + 1:
            # Trailing operation-type label, headed or not.
            op = fields[-1].strip("_") or "all"
            payload = fields[:-1]
            if _NUMERIC_TOKEN_RE.match(op):
                raise WorkloadParseError(
                    f"trailing field {op!r} is numeric, so it is a measurement rather "
                    f"than an operation-type label; the header "
                    f"{header.op_column or '(unheaded)'!r} no longer describes this "
                    "block and _COLUMN_ALIASES needs a new entry"
                )
        elif len(fields) == ncols:
            op = "all"
            payload = fields
        else:
            raise WorkloadParseError(
                f"line has {len(fields)} fields but the active header declares "
                f"{ncols} columns: {' '.join(fields)!r}"
            )

        values: dict[str, float] = {}
        elapsed_s = 0.0
        errors_cum = 0
        for name, token in zip(header.columns, payload):
            try:
                if name == "elapsed_s":
                    elapsed_s = float(token.rstrip("s"))
                elif name == "errors_cum":
                    errors_cum = int(float(token))
                else:
                    values[name] = float(token)
            except ValueError as exc:
                raise WorkloadParseError(
                    f"column {name!r} received non-numeric token {token!r}; the "
                    f"active header does not describe this line: {' '.join(fields)!r}"
                ) from exc

        return Sample(
            kind=header.kind,
            elapsed_s=elapsed_s,
            op=op,
            errors_cum=errors_cum,
            values=values,
        )

    # -- convenience -------------------------------------------------------
    def parse_stream(self, lines: Iterable[str]) -> Iterator[Sample]:
        for line in lines:
            sample = self.feed(line)
            if sample is not None:
                yield sample


@dataclass(frozen=True)
class Tick:
    """All operation types observed at one elapsed offset.

    ``total_tps`` sums throughput across ops. Latency stays per op: averaging a
    read p99 and a write p99 is not a quantile of anything.
    """

    elapsed_s: float
    total_tps: float
    errors_cum: int
    by_op: dict[str, Sample]

    def latency_ms(self, op: str, quantile: str) -> float:
        sample = self.by_op.get(op)
        return float("nan") if sample is None else sample.latency_ms(quantile)


def aggregate_tick(samples: Iterable[Sample]) -> Tick:
    """Fold the samples sharing one elapsed offset into a single tick."""
    by_op: dict[str, Sample] = {}
    elapsed: float | None = None
    for sample in samples:
        if sample.kind != PERIODIC:
            raise WorkloadParseError("refusing to aggregate a cumulative summary sample")
        if elapsed is None:
            elapsed = sample.elapsed_s
        elif abs(sample.elapsed_s - elapsed) > 1e-9:
            raise WorkloadParseError("samples do not share an elapsed offset")
        by_op[sample.op] = sample

    if elapsed is None:
        raise WorkloadParseError("no samples to aggregate")

    # A "__total" line, when the generator emits one, is already the sum; using
    # it alongside the component lines would double-count.
    components = {op: s for op, s in by_op.items() if op not in {"total", "all"}}
    if components:
        total_tps = sum(s.tps for s in components.values())
        errors_cum = max(s.errors_cum for s in components.values())
    else:
        only = next(iter(by_op.values()))
        total_tps = only.tps
        errors_cum = only.errors_cum

    return Tick(
        elapsed_s=elapsed,
        total_tps=total_tps,
        errors_cum=errors_cum,
        by_op=by_op,
    )


def _grouped_pairs(
    arrivals: Iterable[tuple[float | None, Sample]],
) -> Iterator[list[tuple[float | None, Sample]]]:
    """Lazily split a periodic sample stream at each change of elapsed offset.

    Summary blocks are dropped. Shared by :func:`group_ticks` and
    :func:`group_timed_ticks` so the interval rule lives in one place.
    """
    buffer: list[tuple[float | None, Sample]] = []
    current: float | None = None
    for arrived, sample in arrivals:
        if sample.kind != PERIODIC:
            continue
        if current is not None and abs(sample.elapsed_s - current) > 1e-9:
            yield buffer
            buffer = []
        current = sample.elapsed_s
        buffer.append((arrived, sample))
    if buffer:
        yield buffer


def group_ticks(samples: Iterable[Sample]) -> Iterator[Tick]:
    """Group a periodic sample stream into ticks, discarding summary blocks."""
    for group in _grouped_pairs((None, sample) for sample in samples):
        yield aggregate_tick(sample for _, sample in group)


def group_timed_ticks(
    arrivals: Iterable[tuple[float, Sample]],
) -> Iterator[tuple[float, Tick]]:
    """As :func:`group_ticks`, but pairing each tick with when it was observed.

    Each sample comes with the harness's monotonic clock reading when its line
    was read; a tick is stamped with its first line's arrival. The generator's
    ``elapsed`` clock starts ~5 s later (SSH and process startup), so both are
    recorded and the offset between them is observed rather than assumed.
    """
    for group in _grouped_pairs(arrivals):
        arrived = group[0][0]
        assert arrived is not None  # group_timed_ticks is never fed None
        yield arrived, aggregate_tick(sample for _, sample in group)
