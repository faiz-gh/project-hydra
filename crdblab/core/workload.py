"""Header-driven parser for ``cockroach workload run`` output.

Rationale
---------
The legacy tooling identified sample lines positionally: it accepted any line
with exactly nine whitespace-separated fields and read ``fields[2]`` as
throughput, ``fields[5]`` as p50 and ``fields[7]`` as p99. That heuristic is
unsound for three reasons, all of which corrupted the Phase II/III exports:

1. When the workload reports more than one operation type, each interval emits
   one line *per op type* carrying an unheaded trailing label. Positional
   parsing silently treated read and write lines as independent samples of the
   same quantity, so summing versus averaging became ambiguous downstream.
2. That trailing label makes the periodic line nine fields wide while its
   header is only eight columns wide, shifting every latency index by one:
   ``fields[5]`` is p95, not p50, and ``fields[7]`` is pMax, not p99.
3. The terminal cumulative-summary block is also nine fields wide and carries
   an elapsed value equal to the run duration, so it passed the legacy
   ``elapsed <= duration`` guard and was recorded as a one-second sample with a
   throughput of ~185,000 ops/sec.

This module therefore refuses to interpret any data line until it has seen a
header line to bind names to positions, distinguishes periodic from cumulative
blocks by header content rather than by field count, and preserves the op-type
label instead of discarding it. Aggregation policy (sum throughput across op
types, never pool latency distributions) is applied explicitly in
:func:`aggregate_tick`, not implicitly by the parser.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Iterator

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

#: Canonical names of measured quantities. Any *trailing* header token outside
#: this set names the operation-type column rather than a measurement. Observed
#: against CockroachDB v26.3.0, whose periodic and cumulative blocks label that
#: column differently: the periodic header ends at ``pMax(ms)`` and leaves the
#: op-type label unheaded, whereas the cumulative header ends ``..._pMax(ms)__total``
#: and therefore declares one more column than the periodic header does. A field
#: count alone cannot distinguish the two, which is why the binding is made from
#: the header text in both cases.
_METRIC_COLUMNS = frozenset(_COLUMN_ALIASES.values())


class WorkloadParseError(RuntimeError):
    """Raised when output cannot be interpreted without positional guessing."""


@dataclass(frozen=True)
class Sample:
    """One parsed line of generator output.

    ``kind`` distinguishes a genuine per-interval observation (:data:`PERIODIC`)
    from a terminal cumulative total (:data:`SUMMARY`). Only periodic samples
    are measurements; summary samples are retained solely as an independent
    cross-check on the periodic stream.
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

        # A trailing token that names no known measurement is the operation-type
        # column. Removing it here means the remainder of the binding logic sees
        # the same shape for both block types, rather than special-casing the
        # cumulative block by its width -- the conflation that admitted the
        # summary totals as a per-interval sample in the legacy tooling.
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
            # The operation-type label, whether the header named it (cumulative
            # blocks, "__total") or left it unheaded (periodic blocks). Its
            # inconsistent presence in the header is precisely what made
            # field-count heuristics unsafe.
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
                # Reported against the column *name* rather than its index, so a
                # future layout change is legible instead of arriving as a bare
                # "could not convert string to float".
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
    """All operation types observed at a single elapsed offset.

    ``total_tps`` sums throughput across operation types, which is the only
    correct aggregation: read and write rates are components of one offered
    load, not repeated measurements of it. Latency is deliberately *not*
    pooled; per-op quantiles are kept separate because the arithmetic mean of
    a read p99 and a write p99 is not a quantile of anything.
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
    """Split a periodic sample stream at each change of elapsed offset.

    Lazy, so a caller streaming a three-minute run sees each interval as soon as
    the next one begins rather than at the end. Summary blocks are dropped here
    rather than by the caller: a cumulative total is not an interval, and the
    single place that decides which lines constitute one is this function.

    Grouping is implemented once and shared by :func:`group_ticks` and
    :func:`group_timed_ticks`. Phase IV previously carried its own near-identical
    copy; maintaining two implementations of the rule that defines a measurement
    interval is the shape of defect D1 and is not reintroduced for the sake of
    attaching a timestamp.
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

    ``arrivals`` supplies, for every sample, the reading on the *harness's*
    monotonic clock at the moment the line was read from the pipe. The tick is
    stamped with the arrival of its first line.

    This exists because the generator's ``elapsed`` column and the harness's own
    clock are different clocks with different origins. The generator's begins
    when it starts issuing operations; the harness's begins when the phase does,
    which is earlier by the cost of establishing the SSH session and starting the
    process -- about 5.4 s on this testbed. Events scheduled by the harness (the
    chaos injection, above all) are timed on the latter. Recording only the
    former makes the two files in a run directory silently incomparable, so that
    a fault marker drawn against a throughput series is displaced by an interval
    nobody has measured. Both clocks are therefore recorded per tick, and the
    offset between them becomes an observation rather than an assumption.

    The stamp includes SSH transport and the harness's own scheduling delay. It
    is an upper bound on when the generator printed the line, not the instant it
    did so; at a one-second cadence that is immaterial, and it is the correct
    quantity anyway, since what must be aligned is when the *harness* could have
    known a thing against when it did one.
    """
    for group in _grouped_pairs(arrivals):
        arrived = group[0][0]
        assert arrived is not None  # group_timed_ticks is never fed None
        yield arrived, aggregate_tick(sample for _, sample in group)
