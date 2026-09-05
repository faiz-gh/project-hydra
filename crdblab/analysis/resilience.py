"""Phase IV analysis: recovery time, recovery point, and their limits.

This module replaces ``evaluate_resilience.py``. Three things it does are
corrections rather than refinements, and each is load-bearing.

**The two clocks are reconciled before anything is drawn against anything.**
A run directory contains two timelines with different origins. ``events.json``
records offsets on the harness's monotonic clock, which starts when the phase
does; ``metrics.csv`` carries the generator's own ``elapsed`` accounting, which
starts only once the generator is running -- later by the cost of opening the
SSH session and starting the process, about 5.4 s on this testbed. Plotting a
fault time from the first against a throughput series from the second displaces
the fault by an interval nobody measured, and on a 9.3 s recovery that is not a
rounding error. Schema 2.1 runs record both clocks per interval, so the offset
is measured; schema 2.0 runs do not, so :func:`align` reports it as a **bounded
interval** and every derived timing becomes an interval too. It does not
substitute an estimate for the missing measurement.

**Two quantities are called RTO and both are reported, each with its limit.**
*Availability RTO* is the interval from the fault to the next acknowledged
write, taken from the audit client's own attempt log. It is the headline for a
failover claim, and it is bounded below by the audit cadence -- which is itself
bounded by the cost of a quorum write, ~70 ms here, not by the nominal
``audit_interval_s``. A value below that cadence is indistinguishable from no
interruption at all and must be reported as such rather than as a number.
*Performance RTO* is the interval until throughput returns to a stated fraction
of baseline and holds. Where the cluster instead settles into a *new stable
state* below the threshold, this reports the metric as **undefined**, not as an
infinite recovery time: losing a member of the fast quorum triangle raises the
write path's floor from ~66.8 ms to ~190 ms, and a level the cluster will not
return to while the node is down is not a recovery the metric can time. Whether
that happens is not fixed by the geometry alone -- the two Phase IV runs here
share a target and disagree, the ``dead`` run at C=50 settling at 0.67 of
baseline while the ``recover`` run at C=100 regained the threshold in about 9 s
-- so the classification is made from the observed post-fault series and the
geometry is reported alongside as the explanation. Reporting only availability
would conceal a degraded cluster; reporting only performance would describe a
cluster that never stopped accepting writes as having never recovered.

**RPO counts acknowledged writes, and only those.** A gap in the audit table
establishes nothing on its own; a gap in the writes the client was *told* had
committed is data loss. Ambiguous writes are neither, and are reported
separately rather than collapsed into either.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..core.preflight import quorum_floor_ms
from ..core.rto_probe import attempts_from_rows, measure_rto, outage_windows

# Recovery detection is imported from the phase that performs it rather than
# reimplemented. The legacy pipeline used one recovery threshold in the runner
# and a different one in the evaluator (D5); a second implementation of the
# predicate is how two artefacts come to disagree about the same run.
from ..phases.p4_chaos import availability_rto, find_recovery
from ..topology import DEFAULT_TOPOLOGY, Topology
from .loader import Run

#: Interval after a fault during which the cluster is still detecting it and
#: transferring leases, excluded when characterising the *settled* post-fault
#: state. CockroachDB's liveness detection alone was observed at ~6 s on this
#: testbed -- the lag between injection and any throughput impact -- and lease
#: transfer follows it. Performance RTO deliberately *includes* this interval,
#: because it is part of the outage; only the question "what did the cluster
#: settle to" excludes it.
LIVENESS_SETTLE_S = 15.0

#: A post-fault throughput series whose standard deviation is within this
#: fraction of its mean is treated as having settled, rather than as still
#: recovering. Loose, because the quantity being distinguished -- a new stable
#: level versus a trend -- is coarse, and a tight threshold here would assert
#: more precision than a 1 Hz sample stream supports.
SETTLED_CV = 0.25


class AlignmentError(RuntimeError):
    """Raised when a run's two timelines cannot be related at all."""


@dataclass(frozen=True)
class Alignment:
    """How the generator's ``elapsed_s`` maps onto the harness's clock.

    ``offset_s`` is where the generator's zero falls on the harness clock, so
    ``wall_offset_s = elapsed_s + offset_s``. ``method`` is ``"measured"`` when
    both clocks were recorded per interval and ``"bounded"`` when only the
    generator's was, in which case ``offset_s`` is ``None`` and the true value
    lies in ``[lower_s, upper_s]``.
    """

    method: str
    offset_s: float | None
    lower_s: float
    upper_s: float
    spread_s: float | None
    detail: str

    @property
    def exact(self) -> bool:
        return self.method == "measured"

    @property
    def uncertainty_s(self) -> float:
        return 0.0 if self.exact else self.upper_s - self.lower_s

    def to_wall(self, elapsed_s: float) -> float | tuple[float, float]:
        """Place a generator-clock offset on the harness clock."""
        if self.exact:
            return elapsed_s + float(self.offset_s or 0.0)
        return (elapsed_s + self.lower_s, elapsed_s + self.upper_s)

    def to_generator(self, wall_offset_s: float) -> float | tuple[float, float]:
        """Place a harness-clock offset on the generator's clock."""
        if self.exact:
            return wall_offset_s - float(self.offset_s or 0.0)
        return (wall_offset_s - self.upper_s, wall_offset_s - self.lower_s)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "generator_start_offset_s": self.offset_s,
            "lower_s": round(self.lower_s, 3),
            "upper_s": round(self.upper_s, 3),
            "spread_s": self.spread_s,
            "uncertainty_s": round(self.uncertainty_s, 3),
            "detail": self.detail,
        }


def _parse_utc(stamp: str | None) -> datetime | None:
    if not stamp:
        return None
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


def align(run: Run) -> Alignment:
    """Relate the run's two timelines, measuring the offset where possible.

    For a schema 2.1 run the offset is observed per interval and reported with
    its spread: a constant offset means the two clocks differ only in origin,
    which is what licenses converting between them, while a drifting one would
    mean they run at different *rates* and that no single conversion exists. The
    legacy pipeline's failure was exactly a clock running at the wrong rate (D4),
    so rate agreement is checked rather than assumed.

    For a schema 2.0 run the offset was never recorded and is bounded instead.
    The generator cannot have started before the run's epoch, so the offset is at
    least zero; and the run's wall-clock envelope must contain the generator's
    whole ``elapsed`` span, so it is at most the difference between them. Every
    timing derived through this alignment is then an interval, which is the
    honest representation of a quantity that was not measured.
    """
    events = run.events or {}
    last_elapsed = float(run.metrics["elapsed_s"].max())

    if run.records_wall_clock:
        paired = run.metrics.dropna(subset=["wall_offset_s"])
        offsets = (paired["wall_offset_s"] - paired["elapsed_s"]).astype(float)
        median = float(offsets.median())
        spread = float(offsets.max() - offsets.min())
        return Alignment(
            method="measured",
            offset_s=round(median, 3),
            lower_s=float(offsets.min()),
            upper_s=float(offsets.max()),
            spread_s=round(spread, 3),
            detail=(
                f"both clocks recorded per interval; the generator's zero falls "
                f"{median:.3f} s after the run's epoch, constant to within "
                f"{spread:.3f} s across {len(offsets)} intervals"
            ),
        )

    started = _parse_utc(events.get("t_start_utc"))
    finished = _parse_utc(events.get("t_end_utc"))
    if started is None or finished is None:
        raise AlignmentError(
            f"{run.run_id} records neither per-interval wall offsets nor a run "
            "envelope, so its throughput series cannot be placed on the same axis "
            "as its event timeline at all"
        )
    envelope = (finished - started).total_seconds()
    upper = max(0.0, envelope - last_elapsed)
    return Alignment(
        method="bounded",
        offset_s=None,
        lower_s=0.0,
        upper_s=round(upper, 3),
        spread_s=None,
        detail=(
            f"schema {run.schema_version} run: only the generator's clock was "
            f"recorded, so the offset between the two timelines is bounded rather "
            f"than measured. The run occupied {envelope:.2f} s of wall clock and "
            f"the generator reported {last_elapsed:.0f} s of intervals, so its zero "
            f"falls between 0 and {upper:.2f} s after the run's epoch. Every timing "
            "crossing the two clocks is therefore reported as an interval of that "
            "width; re-run under schema 2.1 to measure it"
        ),
    )


def fault_offsets(run: Run, alignment: Alignment) -> dict[str, Any]:
    """Where the fault landed, on both clocks."""
    events = run.events or {}
    injected = events.get("injected") or {}
    wall = injected.get("at_offset_s")
    if wall is None:
        return {"wall_offset_s": None, "detail": "no fault was injected"}

    generator = alignment.to_generator(float(wall))
    out: dict[str, Any] = {
        "wall_offset_s": float(wall),
        "target": events.get("target"),
        "mode": events.get("mode"),
        "requested_at_s": (run.profile.get("chaos", {}) or {}).get("inject_at_s"),
    }
    if isinstance(generator, tuple):
        out["generator_elapsed_s"] = None
        out["generator_elapsed_bounds_s"] = [round(generator[0], 3), round(generator[1], 3)]
        out["caveat"] = (
            "the fault's position on the throughput axis is an interval, not a "
            "point; a figure must draw it as a band of this width or use the "
            "harness clock for both series"
        )
    else:
        out["generator_elapsed_s"] = round(generator, 3)
        out["generator_elapsed_bounds_s"] = None
    return out


def degradation_profile(run: Run, alignment: Alignment) -> pd.DataFrame:
    """Throughput against time since the fault, on one clock.

    When the alignment is measured, ``since_fault_s`` is a single column and the
    series can be plotted directly against the event timeline. When it is only
    bounded, that column is absent and ``since_fault_lower_s`` /
    ``since_fault_upper_s`` are given instead, so a figure cannot silently
    collapse an interval into a point.
    """
    ticks = run.ticks()
    fault = fault_offsets(run, alignment)
    wall = fault.get("wall_offset_s")
    if wall is None:
        return ticks

    if alignment.exact:
        if "wall_offset_s" not in ticks.columns:
            ticks = ticks.assign(
                wall_offset_s=ticks["elapsed_s"] + float(alignment.offset_s or 0.0)
            )
        ticks = ticks.assign(since_fault_s=ticks["wall_offset_s"] - float(wall))
    else:
        lower, upper = alignment.to_generator(float(wall))
        ticks = ticks.assign(
            since_fault_lower_s=ticks["elapsed_s"] - upper,
            since_fault_upper_s=ticks["elapsed_s"] - lower,
        )
    return ticks


def availability(run: Run) -> dict[str, Any]:
    """Availability RTO, re-derived from the audit log where it survives.

    ``audit.csv`` is written from schema 2.1 onward precisely so this figure can
    be recomputed from its underlying observations rather than taken on trust.
    Where it is absent the summary recorded at measurement time is reported and
    labelled as not re-derivable.

    The returned ``claim`` is the sentence that may be quoted. Where the measured
    interval is below the audit cadence, that sentence contains no number: the
    two are indistinguishable, and quoting the smaller of them as a result would
    assert a precision the sampling never had.
    """
    events = run.events or {}
    audit_csv = run.path / "audit.csv"

    if audit_csv.exists():
        attempts_df = pd.read_csv(audit_csv)
        injected = (events.get("injected") or {}).get("at_offset_s")
        if injected is None:
            return {"available": False, "detail": "no fault was injected"}
        attempts = [
            (float(r.wall_offset_s), int(r.seq_id), str(r.outcome))
            for r in attempts_df.itertuples()
        ]
        measured = availability_rto(attempts, float(injected))
        measured["source"] = "re-derived from audit.csv"
    else:
        measured = dict(events.get("availability") or {})
        if not measured:
            return {
                "available": False,
                "detail": (
                    f"{run.run_id} predates both the audit attempt log and the "
                    "availability RTO measurement, so the interval from the fault "
                    "to the next acknowledged write cannot be recovered from this "
                    "run. Its throughput-based performance RTO is unaffected"
                ),
            }
        measured["source"] = "events.json, as recorded at measurement time"

    rto = measured.get("availability_rto_s")
    resolution = measured.get("resolution_s")
    out: dict[str, Any] = {"available": True, **measured}

    if rto is None:
        out["claim"] = "no write was acknowledged after the fault within the run"
        out["quotable_value_s"] = None
    elif resolution and rto < resolution:
        out["below_resolution"] = True
        out["quotable_value_s"] = None
        out["claim"] = (
            f"no write interruption detectable at {resolution:.2f} s resolution"
        )
        out["caveat"] = (
            f"the measured interval is {rto:.3f} s, which is shorter than the gap "
            f"between consecutive audit writes ({resolution:.2f} s) and is therefore "
            "indistinguishable from no interruption. Do not quote it as a recovery "
            "time. The cadence is bounded by the cost of a quorum write on this "
            "topology (~70 ms), not by the profile's audit_interval_s"
        )
    else:
        out["below_resolution"] = False
        out["quotable_value_s"] = rto
        out["claim"] = (
            f"writes resumed {rto:.2f} s after the fault"
            + (f", measured at {resolution:.2f} s resolution" if resolution else "")
        )
    return out


def probe_availability(run: Run) -> dict[str, Any]:
    """Availability RTO re-derived from the high-frequency probe, if one ran.

    A third reading of the same quantity :func:`availability` reports, at a
    resolution the RPO audit writer's serialised single connection cannot reach.
    It is reported *beside* that one and never in place of it: they are separate
    clients over separate connections writing separate tables, so agreement
    between them is corroboration and disagreement is a fact about the run that a
    single figure would have hidden.

    Like :func:`availability`, it recomputes from ``rto_probe.csv`` rather than
    reading back the summary the phase wrote, so the published number can be
    disputed against the observations behind it.

    ``observed_outage_s`` is the quantity to prefer when the two differ. The
    probe writes from the workstation, 376 ms round trip from the gateway, so
    every completion timestamp carries about half of that as a systematic offset;
    it appears identically on the last write before the fault and the first after
    it, and therefore cancels in their difference but not in the interval from
    the fault, which is timestamped by the injector rather than by the probe.
    """
    probe_csv = run.path / "rto_probe.csv"
    events = run.events or {}
    recorded = (events.get("probe") or {})

    if not probe_csv.exists():
        if recorded.get("enabled") is False:
            return {
                "available": False,
                "detail": "the high-frequency probe was disabled for this run",
            }
        return {
            "available": False,
            "detail": (
                f"{run.run_id} predates the high-frequency RTO probe. Its "
                "availability RTO comes from the RPO audit log alone and is "
                "bounded by that client's cadence, not by the probe's"
            ),
        }

    injected = (events.get("injected") or {}).get("at_offset_s")
    if injected is None:
        return {"available": False, "detail": "no fault was injected"}

    attempts = attempts_from_rows(pd.read_csv(probe_csv).to_dict("records"))
    measured = measure_rto(attempts, float(injected))
    windows = outage_windows(attempts)
    out: dict[str, Any] = {
        "available": True,
        "source": "re-derived from rto_probe.csv",
        **measured,
    }
    # The longest gap between served writes anywhere in the run, which is not
    # necessarily the one the fault caused. Reported so that an outage the fault
    # did not produce -- a stall on the client, a lease moving for its own
    # reasons -- is visible rather than absorbed into the headline number.
    if windows:
        out["longest_gap_between_served_writes"] = windows[0]
    for key in ("achieved_rate_per_s", "served_rate_per_s", "workers", "dispatch_interval_s"):
        if key in recorded:
            out[key] = recorded[key]
    return out


def performance(run: Run, alignment: Alignment) -> dict[str, Any]:
    """Performance RTO, re-derived from the metrics table.

    Recomputed here rather than copied from ``events.json``, so the recorded
    figure has an independent check against the artefact it was derived from.
    Where the alignment is only bounded the recomputation is run at both ends of
    the interval and reported as a range.

    Where no recovery is found, this distinguishes two cases the legacy evaluator
    conflated under "NOT RECOVERED": a cluster still degrading or oscillating, and
    a cluster that has settled into a *new stable state* below the threshold. The
    second is not a slow recovery. It is the correct behaviour of a quorum system
    that has lost a fast replica, and the metric, not the cluster, is what fails
    to apply.
    """
    events = run.events or {}
    chaos = run.profile.get("chaos", {}) or {}
    threshold = float(events.get("recovery_threshold", chaos.get("recovery_threshold", 0.8)))
    hold = float(events.get("recovery_hold_s", chaos.get("recovery_hold_s", 10)))

    fault = fault_offsets(run, alignment)
    wall = fault.get("wall_offset_s")
    if wall is None:
        return {"defined": False, "detail": "no fault was injected"}

    ticks = run.ticks()
    if alignment.exact and "wall_offset_s" in ticks.columns:
        series = list(zip(ticks["wall_offset_s"].astype(float), ticks["total_tps"].astype(float)))
        fault_points = [float(wall)]
    else:
        series = list(zip(ticks["elapsed_s"].astype(float), ticks["total_tps"].astype(float)))
        lower, upper = alignment.to_generator(float(wall))
        fault_points = sorted({round(lower, 3), round(upper, 3)})

    results: list[dict[str, Any]] = []
    for fault_at in fault_points:
        pre = [v for t, v in series if t < fault_at]
        baseline = sum(pre[-20:]) / len(pre[-20:]) if pre else 0.0
        recovered = (
            find_recovery(series, fault_at, baseline, threshold, hold)
            if baseline > 0
            else None
        )
        results.append(
            {
                "fault_at_s": fault_at,
                "baseline_tps": round(baseline, 2),
                "floor_tps": round(baseline * threshold, 2),
                "recovered_at_s": round(recovered, 3) if recovered is not None else None,
                "rto_s": round(recovered - fault_at, 3) if recovered is not None else None,
            }
        )

    rtos = [r["rto_s"] for r in results]
    out: dict[str, Any] = {
        "recovery_threshold": threshold,
        "recovery_hold_s": hold,
        "recomputed": results,
        "clock": alignment.method,
    }

    recorded = events.get("performance_rto_s", events.get("rto_s"))
    out["recorded_at_measurement_time_s"] = recorded

    if all(r is not None for r in rtos) and rtos:
        out["defined"] = True
        if len(set(rtos)) == 1:
            out["rto_s"] = rtos[0]
            out["claim"] = f"throughput was sustainably back within {rtos[0]:.1f} s"
        else:
            out["rto_s"] = None
            out["rto_bounds_s"] = [min(rtos), max(rtos)]
            out["claim"] = (
                f"throughput was sustainably back within "
                f"{min(rtos):.1f}-{max(rtos):.1f} s; the range is the unmeasured "
                "clock offset, not variability in the system"
            )
        if recorded is not None and out.get("rto_s") is not None:
            delta = abs(float(recorded) - out["rto_s"])
            out["agrees_with_recorded"] = delta < 1.0
            out["recompute_delta_s"] = round(delta, 3)
        return out

    out["defined"] = False
    out["rto_s"] = None
    settled = post_fault_steady_state(run, alignment)
    out["post_fault_state"] = settled
    floor = results[0]["floor_tps"]
    if settled.get("settled") and settled.get("mean_tps") is not None:
        if settled["mean_tps"] < floor:
            out["classification"] = "degraded_steady_state"
            out["claim"] = (
                f"performance RTO is undefined for this fault: throughput settled at "
                f"a stable {settled['mean_tps']:.0f} ops/s, below the "
                f"{floor:.0f} ops/s floor, and stayed there. This is a new stable "
                "state, not a slow recovery -- the metric does not apply while the "
                "node is down"
            )
        else:
            out["classification"] = "recovered_without_holding"
            out["claim"] = (
                "throughput returned above the floor but did not hold it for the "
                "required window"
            )
    else:
        out["classification"] = "unsettled_within_run"
        out["claim"] = (
            "throughput had neither recovered nor settled by the end of the run, so "
            "no recovery time can be stated"
        )
    return out


def post_fault_steady_state(run: Run, alignment: Alignment) -> dict[str, Any]:
    """What the cluster settled to after the fault, once detection had completed.

    Excludes :data:`LIVENESS_SETTLE_S` after the fault, during which the cluster
    is still detecting the failure and moving leases. That interval belongs in
    the recovery time and is deliberately part of performance RTO; it does not
    belong in a description of the state the cluster reached.
    """
    fault = fault_offsets(run, alignment)
    wall = fault.get("wall_offset_s")
    if wall is None:
        return {"settled": None, "detail": "no fault was injected"}

    ticks = run.ticks()
    if alignment.exact and "wall_offset_s" in ticks.columns:
        times = ticks["wall_offset_s"].astype(float)
        fault_at = float(wall)
    else:
        times = ticks["elapsed_s"].astype(float)
        _, upper = alignment.to_generator(float(wall))
        # The later bound, so the window cannot accidentally include pre-fault
        # intervals: an alignment that is uncertain must err towards excluding
        # data, never towards including data from the wrong side of the fault.
        fault_at = upper

    window = ticks[times >= fault_at + LIVENESS_SETTLE_S]
    if len(window) < 3:
        return {
            "settled": None,
            "detail": f"only {len(window)} interval(s) after the settling window",
        }

    values = window["total_tps"].astype(float)
    mean = float(values.mean())
    sd = float(values.std(ddof=1))
    cv = sd / mean if mean else float("inf")
    pre = ticks[times < fault_at]["total_tps"].astype(float)
    baseline = float(pre.tail(20).mean()) if len(pre) else None
    return {
        "settled": bool(cv < SETTLED_CV),
        "mean_tps": round(mean, 1),
        "sd_tps": round(sd, 1),
        "max_tps": round(float(values.max()), 1),
        "coefficient_of_variation": round(cv, 4),
        "intervals": len(values),
        "fraction_of_baseline": round(mean / baseline, 4) if baseline else None,
        "settling_window_excluded_s": LIVENESS_SETTLE_S,
    }


def quorum_geometry(
    run: Run,
    network_csv: Path | None,
    topology: Topology = DEFAULT_TOPOLOGY,
) -> dict[str, Any]:
    """Why performance RTO may be undefined, derived from measured round trips.

    A write commits when a majority of the five voting replicas has acknowledged
    it, so the binding constraint is the round trip to the second-fastest
    *available* follower. Removing a follower from the fast triangle does not stop
    writes -- a quorum survives -- but it raises that floor, on this testbed from
    ~66.8 ms to ~190 ms, because the next-fastest replica is in South Asia.

    What this does **not** establish is that total throughput must stay below the
    recovery threshold. The two Phase IV runs in this project disagree on that
    while sharing a target: the ``dead`` run at C=50 settled at 0.67 of baseline
    and never recovered, while the ``recover`` run at C=100 regained the threshold
    in about 9 s with the same node partitioned. A closed workload can absorb
    higher per-operation latency by keeping more operations outstanding, and 80%
    of this workload's operations are reads served by the local leaseholder and
    unaffected by the change. So the floor is a hard statement about the write
    path and a soft one about aggregate throughput, and it is reported that way:
    the ratio below explains why a performance RTO *may* be undefined for a fault
    on this member, and is not on its own a prediction that it will be.

    Computed from the Phase I matrix rather than asserted, so "the target was in
    the fast quorum" is a measurement.
    """
    events = run.events or {}
    target_name = events.get("target")
    if not network_csv or not Path(network_csv).exists() or not target_name:
        return {
            "available": False,
            "detail": "no Phase I network matrix supplied; run `crdblab net probe`",
        }

    from ..core.preflight import gateway_rtts

    gateway = topology.gateway
    try:
        target = topology.get(str(target_name))
    except KeyError:
        return {"available": False, "detail": f"unknown chaos target {target_name!r}"}

    rtts = gateway_rtts(network_csv, gateway.host)
    rtts.pop(gateway.host, None)
    voters = len(topology)
    surviving = {host: v for host, v in rtts.items() if host != target.host}

    try:
        before = quorum_floor_ms(rtts, voters)
        after = quorum_floor_ms(surviving, voters)
    except ValueError as exc:
        return {"available": False, "detail": str(exc)}

    return {
        "available": True,
        "voters": voters,
        "target": target.name,
        "target_region": target.region,
        "quorum_floor_ms": round(before, 2),
        "surviving_quorum_floor_ms": round(after, 2),
        "floor_ratio_x": round(after / before, 2) if before else None,
        "target_in_fast_quorum": bool(after > before + 1e-9),
        "detail": (
            f"with {target.name} ({target.region}) unavailable, the write path's "
            f"floor rises from {before:.1f} ms to {after:.1f} ms, a factor of "
            f"{after / before:.2f}. Writes continue -- a quorum survives -- so this "
            "is a latency change, not an outage"
            if after > before
            else f"{target.name} is not a member of the fast quorum, so its loss "
            f"leaves the write floor at {before:.1f} ms and the write path is "
            "unaffected"
        ),
        "consequence": (
            "whether aggregate throughput regains the recovery threshold depends on "
            "how much of the added write latency the offered concurrency can hide, "
            "and on the read share, which is unaffected. A performance RTO that "
            "comes back undefined for a fault on this member is explained by this "
            "geometry; one that comes back defined is not contradicted by it"
            if after > before
            else "a full performance recovery is physically available for a fault "
            "on this member"
        ),
    }


def rpo(run: Run) -> dict[str, Any]:
    """Recovery point, preserving the three-way classification of every write."""
    events = run.events or {}
    recorded = dict(events.get("rpo") or {})
    if not recorded:
        return {"available": False, "detail": "no RPO audit was recorded"}

    acknowledged = recorded.get("acknowledged", 0)
    lost = recorded.get("rpo_violations", 0)
    ambiguous = recorded.get("ambiguous", 0)
    out: dict[str, Any] = {"available": True, **recorded}
    out["claim"] = (
        f"{lost} acknowledged write(s) lost of {acknowledged} acknowledged"
        + (
            f"; {ambiguous} further write(s) were ambiguous, of which "
            f"{recorded.get('ambiguous_but_committed', 0)} are present in the table"
            if ambiguous
            else ""
        )
    )
    out["interpretation"] = (
        "RPO = 0 for a quorum-replicated database is the expected result, and is "
        "meaningful here only because the measurement could have shown otherwise: "
        "the audit client records what it was told committed, advances past "
        "ambiguous writes instead of retrying them, and compares its own record "
        "against the table afterwards"
        if lost == 0
        else "a write the client was told had committed is absent; for a "
        "quorum-replicated database this should not occur and must be "
        "investigated before it is reported"
    )
    if acknowledged and events.get("injected"):
        out["sampling_note"] = (
            "the audit cadence is bounded by the cost of a quorum write (~70 ms on "
            "this topology), not by the profile's audit_interval_s, so the series "
            "is coarser than the nominal interval implies"
        )
    return out


def summarise(
    run: Run,
    network_csv: Path | None = None,
    topology: Topology = DEFAULT_TOPOLOGY,
) -> dict[str, Any]:
    """Everything Phase IV produces, with the limits attached to each figure."""
    alignment = align(run)
    return {
        "run_id": run.run_id,
        "phase": run.phase,
        "schema_version": run.schema_version,
        "mode": (run.events or {}).get("mode"),
        "target": (run.events or {}).get("target"),
        "clock_alignment": alignment.to_dict(),
        "fault": fault_offsets(run, alignment),
        "availability_rto": availability(run),
        # The same quantity at a finer resolution, from an independent client.
        # Added as a sibling key rather than folded into `availability_rto`
        # because a consumer that predates the probe must keep reading the audit
        # figure it was written against, and because two readings that disagree
        # should be visible as two readings.
        "probe_rto": probe_availability(run),
        "performance_rto": performance(run, alignment),
        "quorum_geometry": quorum_geometry(run, network_csv, topology),
        "rpo": rpo(run),
    }
