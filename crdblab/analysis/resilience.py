"""Phases III-IV analysis: recovery time, recovery point, and their limits.

* **Clock alignment.** ``events.json`` uses the harness clock; ``metrics.csv``
  also has the generator's ``elapsed``. Schema 2.1 runs record both, so the
  offset is measured; for schema 2.0 runs :func:`align` bounds it and every
  derived timing becomes an interval.
* **Two RTOs.** *Availability RTO* (fault to writes resuming, from the audit log
  and the RTO probe) and *performance RTO* (throughput back to a fraction of
  baseline, held). If the cluster settles into a new, lower stable state,
  performance RTO is reported as undefined rather than infinite.
* **RPO** counts only acknowledged writes that went missing; ambiguous writes
  are reported separately.

Everything is re-derived from the run's CSVs rather than copied from events.json.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ..core.preflight import quorum_floor_ms
from ..core.rto_probe import attempts_from_rows, measure_rto, outage_windows

# Shared with the chaos phase so both use one recovery predicate.
from ..phases.p4_chaos import availability_rto, find_recovery
from ..topology import DEFAULT_TOPOLOGY, Topology
from .loader import Run

#: Post-fault interval (failure detection, lease transfer) excluded when describing
#: the settled state. Performance RTO still includes it.
LIVENESS_SETTLE_S = 15.0

#: Coefficient of variation below which a post-fault series counts as settled.
SETTLED_CV = 0.25

#: A settled write latency within this fraction of baseline counts as "returned";
#: beyond it, a structural shift (losing a fast-quorum member is ~2.7x).
LATENCY_SHIFT_TOLERANCE = 0.15


class AlignmentError(RuntimeError):
    """Raised when a run's two timelines cannot be related at all."""


@dataclass(frozen=True)
class Alignment:
    """How the generator's ``elapsed_s`` maps onto the harness clock.

    ``wall_offset_s = elapsed_s + offset_s``. ``method`` is ``"measured"`` when
    both clocks were recorded, or ``"bounded"``, in which case ``offset_s`` is
    ``None`` and the true value lies in ``[lower_s, upper_s]``.
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

    Schema 2.1: the offset is measured per interval and reported with its spread
    (a small spread shows the clocks run at the same rate). Schema 2.0: the
    offset is bounded between 0 and the run's wall-clock envelope minus the
    generator's elapsed span.
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

    Measured alignment gives ``since_fault_s``; bounded alignment gives
    ``since_fault_lower_s`` and ``since_fault_upper_s`` instead.
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


def _observation_end(run: Run) -> float | None:
    """The generator's last tick: a lower bound on when the run's window closed.

    ``None`` if there is no tick series, in which case coverage is not checked.
    """
    metrics = getattr(run, "metrics", None)
    if metrics is None or len(metrics) == 0:
        return None
    for column in ("wall_offset_s", "elapsed_s"):
        if column in metrics.columns:
            values = metrics[column].dropna()
            if len(values):
                return float(values.max())
    return None


def availability(run: Run) -> dict[str, Any]:
    """Availability RTO, re-derived from the audit log where it survives.

    Falls back to the summary in ``events.json`` for runs without ``audit.csv``.
    The returned ``claim`` is the quotable sentence; below the audit cadence it
    contains no number, since the interval is indistinguishable from none.
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
        measured = availability_rto(
            attempts, float(injected), observation_end=_observation_end(run)
        )
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

    if rto is None and measured.get("coverage_truncated"):
        # About the instrument, not the cluster: the outage is unmeasured.
        out["claim"] = (
            "the audit writer stopped observing "
            f"{measured.get('coverage_gap_s')} s before the run ended; the "
            "outage is UNMEASURED, not absent"
        )
        out["quotable_value_s"] = None
    elif rto is None:
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

    Reported beside :func:`availability`, never instead of it: separate clients,
    connections and tables, so agreement is corroboration. Prefer
    ``observed_outage_s`` (probe-to-probe) when the two differ, because the
    probe's own write delay cancels in it.
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
    measured = measure_rto(
        attempts, float(injected), observation_end_s=_observation_end(run)
    )
    windows = outage_windows(attempts)
    out: dict[str, Any] = {
        "available": True,
        "source": "re-derived from rto_probe.csv",
        **measured,
    }
    # The longest gap anywhere in the run, which the fault may not have caused.
    if windows:
        out["longest_gap_between_served_writes"] = windows[0]
    for key in ("achieved_rate_per_s", "served_rate_per_s", "workers", "dispatch_interval_s"):
        if key in recorded:
            out[key] = recorded[key]
    return out


def performance(run: Run, alignment: Alignment) -> dict[str, Any]:
    """Performance RTO, re-derived from the metrics table.

    With a bounded alignment it runs at both ends of the interval and reports a
    range. With no recovery it separates a cluster still degrading from one that
    settled into a new stable state below the threshold (the metric does not apply).
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

    Excludes :data:`LIVENESS_SETTLE_S` after the fault (detection and lease moves).
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
        # Use the later bound so no pre-fault interval leaks in.
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


def write_latency_recovery(
    run: Run, alignment: Alignment, op: str = "update"
) -> dict[str, Any]:
    """Did the write path itself come back, independent of aggregate throughput.

    Reads are most of the workload, so a permanently slower write path can hide
    in aggregate throughput. A run can recover on throughput and still show a
    ``structural_latency_shift`` here. Settling is judged as in
    :func:`post_fault_steady_state`; baseline is the last 20 pre-fault intervals.
    """
    fault = fault_offsets(run, alignment)
    wall = fault.get("wall_offset_s")
    if wall is None:
        return {"available": False, "detail": "no fault was injected"}

    op_rows = run.metrics[run.metrics["op"] == op]
    if op_rows.empty:
        return {"available": False, "detail": f"no {op!r} samples in this run"}

    if alignment.exact and run.records_wall_clock:
        times = op_rows["wall_offset_s"].astype(float)
        fault_at = float(wall)
    else:
        times = op_rows["elapsed_s"].astype(float)
        # Use the later bound so no pre-fault interval leaks in.
        _, fault_at = alignment.to_generator(float(wall))

    pre = op_rows.loc[times < fault_at, "p50_ms"].astype(float)
    if pre.empty:
        return {
            "available": False,
            "detail": f"no pre-fault {op!r} samples to baseline against",
        }
    baseline = float(pre.tail(20).mean())

    window = op_rows.loc[times >= fault_at + LIVENESS_SETTLE_S, "p50_ms"].astype(float)
    if len(window) < 3:
        return {
            "available": True,
            "op": op,
            "settled": None,
            "baseline_p50_ms": round(baseline, 1),
            "detail": (
                f"only {len(window)} {op!r} interval(s) after the "
                f"{LIVENESS_SETTLE_S:.0f}s settling window; too few to characterise "
                "what the write path settled to"
            ),
        }

    mean = float(window.mean())
    sd = float(window.std(ddof=1))
    cv = sd / mean if mean else float("inf")
    settled = bool(cv < SETTLED_CV)
    ratio = (mean / baseline) if baseline else None
    shifted = bool(ratio is not None and abs(ratio - 1.0) > LATENCY_SHIFT_TOLERANCE)

    out: dict[str, Any] = {
        "available": True,
        "op": op,
        "settled": settled,
        "baseline_p50_ms": round(baseline, 1),
        "settled_p50_ms": round(mean, 1),
        "sd_ms": round(sd, 1),
        "coefficient_of_variation": round(cv, 4),
        "ratio_to_baseline": round(ratio, 3) if ratio is not None else None,
        "intervals": len(window),
        "settling_window_excluded_s": LIVENESS_SETTLE_S,
    }

    if not settled:
        out["classification"] = "unsettled_within_run"
        out["claim"] = (
            f"{op} latency had not settled to a stable value by the end of the "
            f"run (CV={cv:.2f}); no latency-floor comparison can be stated"
        )
    elif shifted:
        out["classification"] = "structural_latency_shift"
        direction = "higher" if ratio > 1 else "lower"
        out["claim"] = (
            f"{op} latency settled at a stable {mean:.1f} ms against a "
            f"{baseline:.1f} ms baseline -- {ratio:.2f}x, {direction} -- and held "
            "there for the rest of the run. This is a structural change to the "
            "write path, not a transient effect of the fault, and a throughput "
            "figure recovering alongside it does not contradict it"
        )
    else:
        out["classification"] = "returned_to_baseline"
        out["claim"] = (
            f"{op} latency settled back within {LATENCY_SHIFT_TOLERANCE * 100:.0f}% "
            f"of its pre-fault baseline ({mean:.1f} ms vs {baseline:.1f} ms)"
        )
    return out


def quorum_geometry(
    run: Run,
    network_csv: Path | None,
    topology: Topology = DEFAULT_TOPOLOGY,
) -> dict[str, Any]:
    """Why performance RTO may be undefined, derived from measured round trips.

    The write floor is the RTT to the follower that completes quorum. Losing a
    fast follower raises it; losing the leader (the usual case here, since the
    gateway is the target) means a survivor takes over with its own RTT row, so
    every survivor is evaluated and a range is reported.

    This explains why performance RTO *may* be undefined; it does not predict
    it. The consequence text differs by engine: on PostgreSQL every operation,
    reads included, follows the primary through HAProxy, so a promotion to a
    distant node also moves the read path.
    """
    events = run.events or {}
    target_name = events.get("target")
    if not network_csv or not Path(network_csv).exists() or not target_name:
        return {
            "available": False,
            "detail": "no Phase I network matrix supplied; run `crdblab net probe`",
        }

    # Same geometry on both engines; only the wording differs.
    is_pg = run.engine == "postgresql"
    leader_word = "primary" if is_pg else "leaseholder"
    promoter = "Patroni" if is_pg else "CockroachDB's allocator"

    from ..core.preflight import gateway_rtts

    gateway = topology.gateway
    try:
        target = topology.get(str(target_name))
    except KeyError:
        return {"available": False, "detail": f"unknown chaos target {target_name!r}"}

    voters = len(topology)
    leaseholder_displaced = target.host == gateway.host

    before_rtts = gateway_rtts(network_csv, gateway.host)
    try:
        before = quorum_floor_ms(before_rtts, voters)
    except ValueError as exc:
        return {"available": False, "detail": str(exc)}

    if not leaseholder_displaced:
        # The leader survives and loses one follower.
        surviving = {host: v for host, v in before_rtts.items() if host != target.host}
        try:
            after = quorum_floor_ms(surviving, voters)
        except ValueError as exc:
            return {"available": False, "detail": str(exc)}

        return {
            "available": True,
            "voters": voters,
            "target": target.name,
            "target_region": target.region,
            "leaseholder_displaced": False,
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
                "whether aggregate throughput regains the recovery threshold depends "
                "on how much of the added write latency the offered concurrency can "
                "hide, and on the read share, which is unaffected. A performance RTO "
                "that comes back undefined for a fault on this member is explained by "
                "this geometry; one that comes back defined is not contradicted by it"
                if after > before
                else "a full performance recovery is physically available for a fault "
                "on this member"
            ),
        }

    # The leader is the target: evaluate every survivor as a candidate leader.
    candidates: dict[str, float] = {}
    for candidate in topology.nodes:
        if candidate.host == target.host:
            continue
        candidate_rtts = gateway_rtts(network_csv, candidate.host)
        candidate_rtts.pop(target.host, None)
        try:
            candidates[candidate.name] = quorum_floor_ms(candidate_rtts, voters)
        except ValueError:
            continue

    if not candidates:
        return {
            "available": False,
            "detail": (
                f"{target.name} is both the chaos target and the gateway, and no "
                "surviving node's RTTs could be read from the Phase I matrix to "
                "estimate a replacement leaseholder's quorum floor"
            ),
        }

    after_min = min(candidates.values())
    after_max = max(candidates.values())
    best = min(candidates, key=candidates.get)
    worst = max(candidates, key=candidates.get)
    ratio_min = round(after_min / before, 2) if before else None
    ratio_max = round(after_max / before, 2) if before else None

    return {
        "available": True,
        "voters": voters,
        "target": target.name,
        "target_region": target.region,
        "leaseholder_displaced": True,
        "quorum_floor_ms": round(before, 2),
        "surviving_quorum_floor_range_ms": [round(after_min, 2), round(after_max, 2)],
        "candidate_floors_ms": {name: round(ms, 2) for name, ms in candidates.items()},
        "best_case_leader": best,
        "worst_case_leader": worst,
        "floor_ratio_range_x": [ratio_min, ratio_max],
        "target_in_fast_quorum": bool(after_min > before + 1e-9),
        "detail": (
            f"{target.name} ({target.region}) was the {leader_word}, so its loss "
            "displaces it rather than merely removing a follower. Depending on "
            f"which survivor {promoter} promotes, the write path's floor rises "
            f"from {before:.1f} ms to somewhere between {after_min:.1f} ms "
            f"({best}, best case) and {after_max:.1f} ms ({worst}, worst case) -- "
            f"{ratio_min:.2f}x to {ratio_max:.2f}x. Writes continue -- a quorum "
            "survives among any three of the four remaining voters -- so this is "
            "a latency change, not an outage, in every candidate"
        ),
        "consequence": (
            (
                "whether aggregate throughput regains the recovery threshold "
                "depends on far more than the write floor above. The generator "
                "reaches PostgreSQL through HAProxy, which follows the primary, "
                f"so a promotion onto {worst} or {best} moves the READ path as "
                "well -- and reads are 80% of this workload. A performance RTO "
                "that comes back undefined after the pinned primary was faulted "
                "is expected, and is dominated by client-to-primary distance "
                "rather than by this quorum geometry: compare the post-fault read "
                "p50 against its baseline before attributing any of it to the "
                "write path"
            )
            if is_pg
            else (
                "whether aggregate throughput regains the recovery threshold "
                "depends on how much of the added write latency the offered "
                "concurrency can hide, and on the read share, which is served by "
                "the surviving leaseholder and is unaffected by which candidate "
                "takes the lease. A performance RTO that comes back undefined is "
                "explained by this geometry at every candidate in the range; one "
                "that comes back defined is not contradicted by it"
            )
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
    """Everything Phase III/IV produces, with the limits attached to each figure."""
    alignment = align(run)
    return {
        "run_id": run.run_id,
        "phase": run.phase,
        "schema_version": run.schema_version,
        "mode": (run.events or {}).get("mode"),
        "target": (run.events or {}).get("target"),
        "clock_alignment": alignment.to_dict(),
        # False: the fault was refused, so every figure describes an undisturbed
        # cluster. None: transport died (likely landed) or not recorded.
        "fault_landed": (run.events or {}).get("fault_landed"),
        "fault": fault_offsets(run, alignment),
        "availability_rto": availability(run),
        "probe_rto": probe_availability(run),
        "performance_rto": performance(run, alignment),
        "write_latency_recovery": write_latency_recovery(run, alignment),
        "quorum_geometry": quorum_geometry(run, network_csv, topology),
        "rpo": rpo(run),
    }
