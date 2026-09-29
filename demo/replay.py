#!/usr/bin/env python3
"""Replay the recorded thesis-extended experiment as a terminal UI, in minutes.

The whole workflow -- provision CockroachDB, run every phase, destroy, redeploy
as PostgreSQL/Patroni, run every phase again -- took the better part of seven
hours of wall clock on 2026-09-11. This plays it back in a few minutes for a
demo, and it plays back what actually happened:

* **The experiment output is the recorded output.** Every line under
  ``run-experiment.sh`` comes from ``runs/_logs``, and every throughput graph is
  drawn from the ``metrics.csv`` of the run that line reports. Nothing is
  simulated except the passage of time.
* **Three splices, each from the same profile and deployment:** the CockroachDB
  run's Phase IV comes from ``chaos-dead-resume-*.log`` (the sweep's own Phase IV
  attempt timed out on a leaseholder query and was re-run by hand); the
  PostgreSQL run's data load comes from the thesis-profile run on the same
  deployment, since the thesis-extended run reused that data with
  ``--skip-load``; and the CockroachDB run's closing summary, which the aborted
  sweep never printed, is rebuilt from its own recorded numbers. ``--as-recorded``
  shows the logs unspliced instead.
* **Terraform output is reconstructed, not recorded** -- no apply was logged. The
  resource addresses are read from ``terraform/*.tf``, so the plan is this
  project's real plan; the per-resource timings are typical values. The UI says
  so in its caption while Terraform is on screen.

Usage (from the repository root)::

    ./demo.sh                       # ~4 minutes, full-screen
    ./demo.sh --minutes 3           # tighter
    ./demo.sh --plain               # no full-screen UI; plain scrolling output

Keys: space pause - n skip to the next step - + / - speed - q quit.
"""

from __future__ import annotations

import argparse
import csv
import curses
import json
import locale
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNS = REPO / "runs"
LOGS = RUNS / "_logs"
TERRAFORM = REPO / "terraform"

CRDB_LOG = "experiment-20260911T164025Z.log"
CRDB_P4_LOG = "chaos-dead-resume-20260911T182028Z.log"
PG_LOG = "experiment-20260911T084546Z.log"
PG_LOAD_LOG = "experiment-20260911T052424Z.log"

ANSI = re.compile(r"\x1b\[([0-9;]*)m")

# ----------------------------------------------------------------------------
# events
# ----------------------------------------------------------------------------


@dataclass
class Event:
    kind: str                       # line | cmd | stage | anim | wait | tf | card
    text: str = ""
    real_s: float = 0.0             # recorded seconds this event stands for
    section: str = ""
    stage: str = ""                 # checklist id to mark active (kind=stage)
    bulk: bool = False              # a table row: may scroll very fast
    label: str = ""                 # anim / wait caption
    series: list = field(default_factory=list)   # [(x, tps)] for anim
    marks: list = field(default_factory=list)    # [(x, text)] lines emitted mid-anim
    fault_at: float | None = None
    engine: str = ""
    tf: dict = field(default_factory=dict)       # {"provider": ..., "delta": +1/-1}
    playback: float = 0.0


def _dur(text: str) -> float:
    """``1h 2m 3s`` / ``16m 56s`` / ``45.5s`` -> seconds."""
    total = 0.0
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([hms])\b", text):
        total += float(value) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def _plain(text: str) -> str:
    return ANSI.sub("", text)


def read_log(name: str) -> list[str]:
    return (LOGS / name).read_text(errors="replace").splitlines()


# -- throughput series from metrics.csv ---------------------------------------

def _ticks(run_id: str) -> dict:
    """``{(concurrency, repetition): [(elapsed, wall, tps)]}``, tps summed across ops."""
    path = RUNS / run_id / "metrics.csv"
    acc: dict = {}
    if not path.exists():
        return {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            try:
                key = (int(float(row["concurrency"])), int(float(row["repetition"])))
                elapsed = float(row["elapsed_s"])
                wall = float(row["wall_offset_s"]) if row.get("wall_offset_s") else elapsed
                tps = float(row["tps"] or 0)
            except (KeyError, ValueError):
                continue
            slot = acc.setdefault(key, {}).setdefault(elapsed, [wall, 0.0])
            slot[0] = min(slot[0], wall)
            slot[1] += tps
    return {k: sorted((e, w, t) for e, (w, t) in v.items()) for k, v in acc.items()}


def tier_series(run_id: str, concurrency: int, rep: int) -> list:
    rows = _ticks(run_id).get((concurrency, rep), [])
    return [(e, t) for e, _, t in rows]


def chaos_series(run_id: str) -> list:
    rows = [r for series in _ticks(run_id).values() for r in series]
    return sorted((w, t) for _, w, t in rows)


# ----------------------------------------------------------------------------
# run-experiment.sh log -> events
# ----------------------------------------------------------------------------

def _section_for(header: str, engine: str) -> tuple[str, str]:
    h = _plain(header)
    prefix = "crdb" if engine == "cockroachdb" else "pg"
    for key, stage in (("Phase 1/4", "p1"), ("Phase 2/4", "p2"), ("Phase 3/4", "p3"),
                       ("Phase 4/4", "p4")):
        if key in h:
            return f"{prefix}-{stage}", f"{prefix}:{stage}"
    if "Working set" in h:
        return f"{prefix}-setup", f"{prefix}:load"
    if "testbed" in h:
        return f"{prefix}-setup", f"{prefix}:testbed"
    if "workstation" in h or "topology" in h:
        return f"{prefix}-setup", f"{prefix}:checks"
    return f"{prefix}-end", f"{prefix}:end"


def parse_run_log(lines: list[str], engine: str) -> list[Event]:
    """One run-experiment.sh log as timed events, with real data behind every wait."""
    events: list[Event] = []
    section = ("crdb" if engine == "cockroachdb" else "pg") + "-setup"
    last_stamp: float | None = None
    last_elapsed: float | None = None
    bench_run = None
    i = 0

    def next_run_id(after: int) -> str | None:
        for line in lines[after:]:
            m = re.search(r"run: runs/(\S+)", _plain(line))
            if m:
                return m.group(1)
        return None

    while i < len(lines):
        raw = lines[i]
        text = _plain(raw)

        if text.startswith("==> "):
            section, stage = _section_for(text, engine)
            events.append(Event("stage", stage=stage, section=section))
            events.append(Event("line", raw, section=section))
            i += 1
            continue

        # Phase II: each tier line stands for ~65 s of a real tier; draw its ticks.
        m = re.match(r"\s*tier (\d+)/(\d+) C=(\d+) rep=(\d+):.*\((.+)\)\s*$", text)
        if m:
            if bench_run is None:
                bench_run = next_run_id(i)
            conc, rep = int(m.group(3)), int(m.group(4))
            events.append(Event(
                "anim", section=section, real_s=_dur(m.group(5)), engine=engine,
                label=f"Phase II - tier {m.group(1)}/{m.group(2)}  C={conc} rep={rep}",
                series=tier_series(bench_run, conc, rep) if bench_run else [],
            ))
            events.append(Event("line", raw, section=section))
            i += 1
            continue

        # Phases III/IV: one long chaos run, with the fault line placed at its time.
        m = re.match(r"\s*running (\d+)s at C=(\d+), injecting at", text)
        if m:
            events.append(Event("line", raw, section=section))
            marks, j, real = [], i + 1, float(m.group(1))
            while j < len(lines):
                t = _plain(lines[j])
                f = re.search(r"»\s*\[\s*([\d.]+)s\]", t)
                if f:
                    marks.append((float(f.group(1)), lines[j]))
                elif re.search(r"chaos \w+: \d+ intervals observed", t):
                    real = _dur(t.split("(")[-1]) or real
                    break
                elif not re.match(r"\s*chaos \w+\s+tps=", t):  # progress lines: the graph replaces them
                    marks.append((None, lines[j]))
                j += 1
            run_id = next_run_id(j)
            series = chaos_series(run_id) if run_id else []
            fault = next((x for x, _ in marks if x is not None), None)
            mode = "recover" if "Phase 3" in section or section.endswith("p3") else "dead"
            events.append(Event(
                "anim", section=section, real_s=real, engine=engine,
                label=f"chaos {mode} - {run_id or ''}  C={m.group(2)}",
                series=series, marks=[(x if x is not None else 0.0, t) for x, t in marks],
                fault_at=fault,
            ))
            if j < len(lines):
                events.append(Event("line", lines[j], section=section))
            i = j + 1
            continue

        real, bulk = 0.0, False
        stamp = re.match(r"I\d{6} (\d\d):(\d\d):(\d\d\.\d+)", text)
        if stamp:
            now = int(stamp.group(1)) * 3600 + int(stamp.group(2)) * 60 + float(stamp.group(3))
            real = max(0.0, now - last_stamp) if last_stamp is not None else 0.0
            last_stamp, bulk = now, True
        el = re.match(r"\s*(\d+\.\d)s\s+\d+\s", text)
        if el:
            now = float(el.group(1))
            real = max(0.0, now - last_elapsed) if last_elapsed is not None else 0.0
            last_elapsed, bulk = now, True
        if text.startswith("_elapsed_"):
            bulk = True
        if re.search(r"\d/5 nodes live", text):
            real = 10.0
        if re.search(r"sources probed", text):
            real = 12.0
        r = re.search(r"(?:rejoined|REJOIN).*after (\d+)s", text)
        if r:
            real = float(r.group(1))

        if real > 90:
            label = ("bulk import of usertable (workload init)" if stamp
                     else "restoring the faulted node" if r else "waiting")
            events.append(Event("wait", section=section, real_s=real, label=label, engine=engine))
            events.append(Event("line", raw, section=section, bulk=bulk))
        else:
            events.append(Event("line", raw, section=section, real_s=real, bulk=bulk))
        i += 1
    return events


# ----------------------------------------------------------------------------
# splicing the recorded logs into one coherent story
# ----------------------------------------------------------------------------

def _manifest(run_id: str) -> dict:
    try:
        return json.loads((RUNS / run_id / "manifest.json").read_text())
    except (OSError, ValueError):
        return {}


def _utc(stamp: str | None) -> datetime | None:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")) if stamp else None


def _fmt_dur(seconds: float) -> str:
    seconds = int(round(seconds))
    return f"{seconds // 60}m {seconds % 60}s"


def crdb_lines(as_recorded: bool) -> list[str]:
    main = read_log(CRDB_LOG)
    if as_recorded:
        return main + ["", "# phase IV re-run by hand after the timeout above:", ""] + read_log(CRDB_P4_LOG)

    # Cut the aborted Phase IV attempt and splice in the re-run of the same phase.
    cut = next(i for i, l in enumerate(main) if "Phase 4/4" in l)
    resume = read_log(CRDB_P4_LOG)
    body = main[: cut + 1] + resume

    # The sweep never reached its summary; rebuild it from its own numbers.
    stamp = lambda name: datetime.strptime(re.search(r"\d{8}T\d{6}Z", name).group(0), "%Y%m%dT%H%M%SZ")  # noqa: E731
    started, resume_start = stamp(CRDB_LOG), stamp(CRDB_P4_LOG)
    p3 = _utc(_manifest("20260911T174234Z_p4-chaos-recover").get("finished_utc"))
    p4 = _utc(_manifest("20260911T182217Z_p4-chaos-dead").get("finished_utc"))
    p4_s = (p4.replace(tzinfo=None) - resume_start).total_seconds() if p4 else 0
    done_s = ((p3.replace(tzinfo=None) - started).total_seconds() if p3 else 0) + p4_s
    body.append(f"\x1b[32m  ok\x1b[0m  process kill — {_fmt_dur(p4_s)}")
    body.append("")
    body.append("\x1b[1m==> Validating every run\x1b[0m")
    runs = ["20260911T170805Z_bench_cluster", "20260911T174234Z_p4-chaos-recover",
            "20260911T182217Z_p4-chaos-dead"]
    body.append(f"\x1b[32m  ok\x1b[0m  {len(runs)} of {len(runs)} run(s) validated")
    body.append("")
    body.append(f"\x1b[1m==> Done in {_fmt_dur(done_s)}\x1b[0m")
    body += [f"\x1b[2m      {l}\x1b[0m" for l in crdb_headlines(main, resume)]
    body.append("\x1b[2m      next: ./generate_insights.sh   (charts, report, dashboard, summary tables)\x1b[0m")
    return body


def crdb_headlines(main: list[str], resume: list[str]) -> list[str]:
    text = [_plain(l) for l in main + resume]
    out = []
    floor = next((re.search(r"quorum floor: ([\d.]+) ms", l) for l in text if "quorum floor:" in l), None)
    gw = next((l for l in text if l.strip().startswith("crdb-gcp-1 ") and "=" in l), "")
    rtts = sorted((float(v), k) for k, v in re.findall(r"(\S+)=([\d.]+)", gw))
    if floor:
        who = f" (crdb-{rtts[1][1]})" if len(rtts) > 1 else ""
        out.append(f"20260911T170557Z_p1-network  quorum floor {float(floor.group(1)):.1f} ms{who}")
    rows = [re.match(r"\s*(\d+)\s+\d+\s+\d+\s+([\d.]+)\s+read=([\d.]+)\s+update=([\d.]+)", l) for l in text]
    rows = [(int(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))) for m in rows if m]
    if rows:
        by_c: dict = {}
        for c, tps, rd, up in rows:
            by_c.setdefault(c, []).append((tps, rd, up))
        peak_c = max(by_c, key=lambda c: sum(t for t, _, _ in by_c[c]) / len(by_c[c]))
        vals = by_c[peak_c]
        mean = lambda i: sum(v[i] for v in vals) / len(vals)  # noqa: E731
        out.append(f"20260911T170805Z_bench_cluster  peak {mean(0):,.0f} ops/s @ C={peak_c} "
                   f"· read p50 {mean(1):.1f} ms · update p50 {mean(2):.1f} ms")

    def chaos(lines, run):
        rto = next((re.search(r"RTO availability\s+([\d.]+) s", l) for l in lines if "RTO availability" in l), None)
        gap = next((re.search(r"unattributed gap (\d+) ms", l) for l in lines if "unattributed gap" in l), None)
        rpo = next((re.search(r"(\d+) acknowledged write\(s\) lost of (\d+)", l) for l in lines if "lost of" in l), None)
        parts = []
        if rto:
            parts.append(f"RTO {float(rto.group(1)):.1f} s")
        if gap:
            parts.append(f"probe gap {int(gap.group(1)) / 1000:.1f} s (not attributable)")
        if rpo:
            parts.append(f"RPO {rpo.group(1)}/{rpo.group(2)}")
        return f"{run}  " + " · ".join(parts)

    p3 = [_plain(l) for l in main[next(i for i, l in enumerate(main) if "Phase 3/4" in l):]]
    out.append(chaos(p3, "20260911T174234Z_p4-chaos-recover"))
    out.append(chaos([_plain(l) for l in resume], "20260911T182217Z_p4-chaos-dead"))
    return out


def pg_lines(as_recorded: bool) -> list[str]:
    main = read_log(PG_LOG)
    main = [l.replace("./generate-insights.sh", "./generate_insights.sh") for l in main]
    if as_recorded:
        return main
    # The thesis-extended run reused the working set the thesis run had loaded on
    # the same deployment (--skip-load). After a fresh apply the load has to be
    # shown, so the real load from that run takes the place of the skip notice.
    start = next(i for i, l in enumerate(main) if "Working set" in l)
    end = next(i for i in range(start, len(main)) if "ok" in _plain(main[i]) and "database:" in main[i])
    load = read_log(PG_LOAD_LOG)
    ls = next(i for i, l in enumerate(load) if "Working set" in l)
    le = next(i for i in range(ls, len(load)) if "database:" in load[i])
    return main[: start + 1] + load[ls + 1: le + 1] + main[end + 1:]


# ----------------------------------------------------------------------------
# terraform, reconstructed from terraform/*.tf
# ----------------------------------------------------------------------------

#: Typical HCP Terraform durations per resource type, in seconds. Not recorded.
TF_SECONDS = {
    "linode_instance": 38, "linode_firewall": 3,
    "azurerm_resource_group": 12, "azurerm_virtual_network": 6, "azurerm_subnet": 5,
    "azurerm_public_ip": 4, "azurerm_network_security_group": 4,
    "azurerm_network_interface": 3, "azurerm_network_interface_security_group_association": 2,
    "azurerm_linux_virtual_machine": 58,
    "google_compute_network": 23, "google_compute_subnetwork": 14,
    "google_compute_firewall": 12, "google_compute_instance": 19,
}
TF_ORDER = [  # creation waves: networks before the machines that sit on them
    "azurerm_resource_group", "linode_firewall", "google_compute_network",
    "azurerm_virtual_network", "azurerm_public_ip", "azurerm_network_security_group",
    "google_compute_subnetwork", "google_compute_firewall", "azurerm_subnet",
    "azurerm_network_interface", "azurerm_network_interface_security_group_association",
    "linode_instance", "google_compute_instance", "azurerm_linux_virtual_machine",
]


def tf_resources() -> list[tuple[str, str]]:
    """``(address, type)`` for every resource the root module would create."""
    main = (TERRAFORM / "main.tf").read_text()
    out = []
    for name, body in re.findall(r'module\s+"([^"]+)"\s*\{(.*?)\n\}', main, re.S):
        src = re.search(r'source\s*=\s*"\./([^"]+)"', body)
        if not src:
            continue
        mod = (TERRAFORM / src.group(1) / "main.tf").read_text()
        for rtype, rname in re.findall(r'^resource\s+"([^"]+)"\s+"([^"]+)"', mod, re.M):
            out.append((f"module.{name}[0].{rtype}.{rname}", rtype))
    return out


def _provider(rtype: str) -> str:
    return {"linode": "Linode", "azurerm": "Azure", "google": "GCP"}.get(rtype.split("_")[0], "?")


def tf_events(action: str, engine: str) -> list[Event]:
    section = {"apply": f"tf-apply-{engine}", "destroy": "tf-destroy"}[action]
    resources = tf_resources()
    n = len(resources)
    ev: list[Event] = []
    L = lambda t, real=0.0, **kw: ev.append(Event("line", t, section=section, real_s=real, **kw))  # noqa: E731
    remote = "apply" if action == "apply" else "destroy"
    L(f"Running {remote} in HCP Terraform. Output will stream here. Pressing Ctrl-C")
    L(f"will cancel the remote {remote} if it's still pending. If the {remote} started it")
    L(f"will stop streaming the logs, but will not stop the {remote} running remotely.")
    L("")
    L(f"Preparing the remote {remote}...", 4)
    L("")
    L("To view this run in a browser, visit:")
    L("https://app.terraform.io/app/lightygi/hydra/runs")
    L("")
    L("Waiting for the plan to start...", 6)
    L("")
    L("Initializing plugins and modules...", 8)
    if action == "apply":
        L(f"\x1b[2m# var.database_engine = \"{engine}\"\x1b[0m")
    L("")
    verb = "created" if action == "apply" else "destroyed"
    sym = "\x1b[32m+\x1b[0m" if action == "apply" else "\x1b[31m-\x1b[0m"
    L("Terraform will perform the following actions:", 5)
    L("")
    for address, _ in resources:
        L(f"  {sym} {address} will be {verb}", bulk=True)
    L("")
    plan = f"Plan: {n} to add, 0 to change, 0 to destroy." if action == "apply" else \
        f"Plan: 0 to add, 0 to change, {n} to destroy."
    L(f"\x1b[1m{plan}\x1b[0m")
    L("")
    if action == "apply":
        L('\x1b[1mDo you want to perform these actions in workspace "hydra"?\x1b[0m')
    else:
        L('\x1b[1mDo you really want to destroy all resources in workspace "hydra"?\x1b[0m')
        L("  There is no undo. Only 'yes' will be accepted to confirm.")
    ev.append(Event("cmd", "yes", section=section, label="  Enter a value: "))
    L("")

    order = sorted(resources, key=lambda r: TF_ORDER.index(r[1]) if r[1] in TF_ORDER else 99)
    if action == "destroy":
        order = order[::-1]
    word, done = ("Creating...", "Creation complete") if action == "apply" else \
        ("Destroying...", "Destruction complete")
    # Waves of independent resources start together; each wave waits for its slowest.
    waves: dict = {}
    for address, rtype in order:
        waves.setdefault(rtype, []).append((address, rtype))
    for rtype, items in waves.items():
        for address, _ in items:
            L(f"\x1b[1m{address}: {word}\x1b[0m")
        secs = TF_SECONDS.get(rtype, 5) * (0.6 if action == "destroy" else 1)
        if secs >= 30:
            for s in range(10, int(secs), 10):
                L(f"{items[0][0]}: Still {'creating' if action == 'apply' else 'destroying'}... [{s}s elapsed]", 10)
        for k, (address, _) in enumerate(items):
            ev.append(Event("line", f"\x1b[1m{address}: {done} after {int(secs)}s\x1b[0m",
                            section=section, real_s=secs % 10 if k == 0 else 0.5,
                            tf={"provider": _provider(rtype), "delta": 1 if action == "apply" else -1}))
    L("")
    if action == "apply":
        L(f"\x1b[1;32mApply complete! Resources: {n} added, 0 changed, 0 destroyed.\x1b[0m")
    else:
        L(f"\x1b[1;32mDestroy complete! Resources: {n} destroyed.\x1b[0m")
    L("")
    return ev


# ----------------------------------------------------------------------------
# the storyboard
# ----------------------------------------------------------------------------

STEPS = [
    ("tf1", "terraform apply", "database_engine = cockroachdb"),
    ("crdb", "./run-experiment.sh", "CockroachDB, thesis-extended"),
    ("tfd", "terraform destroy", "all 30 resources"),
    ("tf2", "terraform apply", '-var="database_engine=postgresql"'),
    ("pg", "./run-experiment.sh", "PostgreSQL/Patroni, thesis-extended"),
]
SUBSTAGES = [
    ("checks", "workstation & topology"), ("testbed", "testbed health"),
    ("load", "working set, 7.5M rows"), ("p1", "Phase I   network"),
    ("p2", "Phase II  benchmark"), ("p3", "Phase III partition"),
    ("p4", "Phase IV  process kill"), ("end", "validate & summary"),
]

#: Share of the playback each section gets. Long recorded phases are compressed
#: harder than short ones; the shares were chosen so every phase is legible.
BUDGET = {
    "intro": 0.02,
    "tf-apply-cockroachdb": 0.07,
    "crdb-setup": 0.06, "crdb-p1": 0.03, "crdb-p2": 0.10, "crdb-p3": 0.07,
    "crdb-p4": 0.07, "crdb-end": 0.03,
    "tf-destroy": 0.05,
    "tf-apply-postgresql": 0.06,
    "pg-setup": 0.07, "pg-p1": 0.03, "pg-p2": 0.10, "pg-p3": 0.07,
    "pg-p4": 0.07, "pg-end": 0.03,
}


def storyboard(as_recorded: bool) -> list[Event]:
    ev: list[Event] = []
    ev.append(Event("card", section="intro", real_s=0))

    def step(sid, cmd, section, engine=""):
        ev.append(Event("stage", stage=sid, section=section))
        ev.append(Event("cmd", cmd, section=section, engine=engine))

    step("tf1", "cd terraform && terraform apply", "tf-apply-cockroachdb")
    ev.extend(tf_events("apply", "cockroachdb"))
    step("crdb", "cd .. && ./run-experiment.sh --profile thesis-extended", "crdb-setup", "cockroachdb")
    ev.extend(parse_run_log(crdb_lines(as_recorded), "cockroachdb"))
    step("tfd", "cd terraform && terraform destroy", "tf-destroy")
    ev.extend(tf_events("destroy", "cockroachdb"))
    step("tf2", 'terraform apply -var="database_engine=postgresql"', "tf-apply-postgresql")
    ev.extend(tf_events("apply", "postgresql"))
    step("pg", "cd .. && ./run-experiment.sh --profile thesis-extended --engine postgresql",
         "pg-setup", "postgresql")
    ev.extend(parse_run_log(pg_lines(as_recorded), "postgresql"))
    ev.append(Event("stage", stage="done", section="pg-end"))
    return ev


def schedule(events: list[Event], total_s: float) -> None:
    """Give every event a playback duration so the whole thing lasts ``total_s``."""
    fixed = {"line": 0.035, "cmd": 0.0, "stage": 0.0, "card": 3.5, "tf": 0.0}
    for e in events:
        if e.kind == "cmd":
            e.playback = 0.4 + 0.03 * len(e.text)
        elif e.kind in ("anim", "wait"):
            e.playback = 0.8
        else:
            e.playback = 0.006 if e.bulk else fixed.get(e.kind, 0.03)
    by_section: dict = {}
    for e in events:
        by_section.setdefault(e.section, []).append(e)
    scale = total_s / sum(BUDGET.get(s, 0.02) for s in by_section)
    for section, items in by_section.items():
        budget = BUDGET.get(section, 0.02) * scale
        spent = sum(e.playback for e in items)
        weights = [e.real_s ** 0.6 if e.real_s > 0 else 0.0 for e in items]
        left, total_w = budget - spent, sum(weights)
        if left > 0 and total_w > 0:
            for e, w in zip(items, weights):
                e.playback += left * w / total_w
    # Sections whose events carry no recorded time cannot absorb their share, and
    # a very short target can be smaller than the per-line minimums alone; one
    # last uniform scale makes the replay last exactly as long as asked.
    planned = sum(e.playback for e in events)
    if planned > 0:
        for e in events:
            e.playback *= total_s / planned


# ----------------------------------------------------------------------------
# rendering
# ----------------------------------------------------------------------------

SPARK = " ▁▂▃▄▅▆▇█"

#: Every non-ASCII glyph the UI draws itself (recorded log text is shown as-is).
#: Progress bars use none: they are coloured cells of spaces, which render the
#: same in every font. ``--ascii`` swaps the rest for plain ASCII.
GLYPHS = {"done": "✓", "active": "▶", "sub": "▸", "pending": "·", "vline": "│",
          "hline": "─", "prompt": "❯", "fault": "▌"}
ASCII_GLYPHS = {"done": "+", "active": ">", "sub": ">", "pending": "-", "vline": "|",
                "hline": "-", "prompt": "$", "fault": "|"}


def segments(raw: str) -> list[tuple[str, tuple]]:
    """ANSI SGR text -> [(text, (bold, dim, colour))]."""
    out, bold, dim, colour, pos = [], False, False, None, 0
    for m in ANSI.finditer(raw):
        if m.start() > pos:
            out.append((raw[pos:m.start()], (bold, dim, colour)))
        for code in (m.group(1) or "0").split(";"):
            if code in ("", "0"):
                bold, dim, colour = False, False, None
            elif code == "1":
                bold = True
            elif code == "2":
                dim = True
            elif code in ("31", "32", "33"):
                colour = {"31": "red", "32": "green", "33": "yellow"}[code]
        pos = m.end()
    if pos < len(raw):
        out.append((raw[pos:], (bold, dim, colour)))
    return out


class Screen:
    def __init__(self, scr, events: list[Event], total_s: float, captions: bool,
                 ascii_only: bool = False):
        self.scr = scr
        self.G = ASCII_GLYPHS if ascii_only else GLYPHS
        # Without block glyphs the graph falls back to whole cells of '#'.
        self.spark = " ........#" if ascii_only else SPARK
        self.events = events
        self.total_s = total_s
        self.captions = captions
        self.log: list[str] = []
        self.stage = ""
        self.done_stages: set[str] = set()
        self.recorded_s = 0.0
        self.playback_start = time.monotonic()
        self.paused_for = 0.0
        self.speed = 1.0
        self.skip_to_next_step = False
        self.debt = 0.0
        self.vclock = self.vtarget = 0.0
        self.last_tick = self.last_draw = time.monotonic()
        self.panel: dict = {"kind": "idle"}
        self.tf_counts = {"Linode": 0, "Azure": 0, "GCP": 0}
        self.tf_totals = {"Linode": 0, "Azure": 0, "GCP": 0}
        for _, rtype in tf_resources():
            self.tf_totals[_provider(rtype)] = self.tf_totals.get(_provider(rtype), 0) + 1
        self._colours()

    # -- colours ----------------------------------------------------------
    def _colours(self) -> None:
        curses.start_color()
        try:
            curses.use_default_colors()
            bg = -1
        except curses.error:
            bg = curses.COLOR_BLACK
        many = curses.COLORS >= 256
        pairs = {
            1: curses.COLOR_GREEN, 2: curses.COLOR_YELLOW, 3: curses.COLOR_RED,
            4: 33 if many else curses.COLOR_BLUE, 5: 208 if many else curses.COLOR_MAGENTA,
            6: 244 if many else curses.COLOR_WHITE, 7: curses.COLOR_CYAN,
        }
        for n, fg in pairs.items():
            curses.init_pair(n, fg, bg)
        curses.init_pair(8, curses.COLOR_BLACK, curses.COLOR_CYAN)
        # The empty part of a bar: a dim background, so the track is visible
        # without drawing any character in it.
        curses.init_pair(9, curses.COLOR_WHITE, 236 if many else curses.COLOR_BLACK)
        self.C = {
            "green": curses.color_pair(1), "yellow": curses.color_pair(2),
            "red": curses.color_pair(3), "crdb": curses.color_pair(4),
            "pg": curses.color_pair(5), "grey": curses.color_pair(6),
            "cyan": curses.color_pair(7), "bar": curses.color_pair(8),
            "track": curses.color_pair(9) if many else curses.color_pair(6) | curses.A_REVERSE | curses.A_DIM,
        }

    def attr(self, style: tuple) -> int:
        bold, dim, colour = style
        a = self.C[colour] if colour else 0
        if bold:
            a |= curses.A_BOLD
        if dim:
            a |= self.C["grey"]
        return a

    def bar(self, y: int, x: int, width: int, fraction: float, colour: str) -> None:
        """A progress bar drawn as coloured cells, with no glyph a font could lack."""
        filled = int(round(width * max(0.0, min(1.0, fraction))))
        self.put(y, x, " " * filled, self.C[colour] | curses.A_REVERSE)
        self.put(y, x + filled, " " * (width - filled), self.C["track"])

    def put(self, y: int, x: int, text: str, attr: int = 0) -> None:
        h, w = self.scr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        try:
            self.scr.addstr(y, x, text[: max(0, w - x - (1 if y == h - 1 else 0))], attr)
        except curses.error:
            pass

    # -- layout -----------------------------------------------------------
    def draw(self) -> None:
        self.scr.erase()
        h, w = self.scr.getmaxyx()
        side = 42 if w >= 130 else 36 if w >= 110 else 0
        panel_h = 8
        self._title(w)
        if side:
            self._checklist(1, 0, side, h - 2)
        self._terminal(1, side, w - side, h - 2 - panel_h)
        self._panel(h - 1 - panel_h, side, w - side, panel_h)
        self._help(h - 1, w)
        self.scr.refresh()

    def _title(self, w: int) -> None:
        self.put(0, 0, " " * w, self.C["bar"])
        dot = self.G["pending"]
        self.put(0, 1, f"project-hydra  {dot}  thesis-extended  {dot}  replay of the recorded runs", self.C["bar"] | curses.A_BOLD)
        rec = int(self.recorded_s)
        right = (f"recorded time {rec // 3600}h {rec % 3600 // 60:02d}m {rec % 60:02d}s   "
                 f"playback {self.elapsed():5.0f}s   speed x{self.speed:g} ")
        self.put(0, max(0, w - len(right)), right, self.C["bar"])

    def elapsed(self) -> float:
        return time.monotonic() - self.playback_start - self.paused_for

    def _checklist(self, y0: int, x0: int, width: int, height: int) -> None:
        y = y0 + 1
        for n, (sid, name, detail) in enumerate(STEPS, 1):
            active = self.stage == sid or self.stage.startswith(sid + ":")
            done = sid in self.done_stages
            G = self.G
            mark, attr = (G["done"], self.C["green"]) if done else ((G["active"], self.C["yellow"] | curses.A_BOLD) if active else (G["pending"], self.C["grey"]))
            self.put(y, x0 + 1, mark, attr)
            self.put(y, x0 + 3, f"{n}. {name}", curses.A_BOLD if active else (0 if done else self.C["grey"]))
            y += 1
            engine_colour = self.C["crdb"] if sid in ("tf1", "crdb") else self.C["pg"] if sid in ("tf2", "pg") else self.C["grey"]
            self.put(y, x0 + 6, detail[: width - 7], engine_colour)
            y += 1
            if sid in ("crdb", "pg"):
                prefix = "crdb" if sid == "crdb" else "pg"
                for sub, label in SUBSTAGES:
                    key = f"{prefix}:{sub}"
                    s_active, s_done = self.stage == key, key in self.done_stages
                    mark = G["done"] if s_done else (G["sub"] if s_active else " ")
                    a = self.C["green"] if s_done else (self.C["yellow"] | curses.A_BOLD if s_active else self.C["grey"])
                    self.put(y, x0 + 6, f"{mark} {label}"[: width - 7], a)
                    y += 1
            y += 1
            if y >= y0 + height:
                break
        for yy in range(y0, y0 + height):
            self.put(yy, x0 + width - 1, self.G["vline"], self.C["grey"])

    def _terminal(self, y0: int, x0: int, width: int, height: int) -> None:
        width -= 2
        rows: list[list] = []
        for raw in reversed(self.log):
            segs = segments(raw)
            flat, line, used = [], [], 0
            for text, style in segs:
                while text:
                    room = width - used
                    chunk, text = text[:room], text[room:]
                    line.append((chunk, style))
                    used += len(chunk)
                    if used >= width:
                        flat.append(line)
                        line, used = [], 0
            if line or not flat:
                flat.append(line)
            rows = flat + rows
            if len(rows) >= height:
                break
        rows = rows[-height:]
        for i, line in enumerate(rows):
            x = x0 + 1
            for text, style in line:
                self.put(y0 + i, x, text, self.attr(style))
                x += len(text)

    def _panel(self, y0: int, x0: int, width: int, height: int) -> None:
        self.put(y0, x0, self.G["hline"] * width, self.C["grey"])
        p = self.panel
        inner = width - 4
        if p["kind"] == "anim" and p["series"]:
            colour = self.C["crdb"] if p["engine"] == "cockroachdb" else self.C["pg"]
            xs = [x for x, _ in p["series"]]
            lo, hi = xs[0], xs[-1]
            cols = max(10, inner)
            n = len(p["series"])
            if n <= cols:
                values = [p["series"][min(n - 1, c * n // cols)][1] for c in range(cols)]
            else:
                buckets = [[] for _ in range(cols)]
                for x, v in p["series"]:
                    idx = min(cols - 1, int((x - lo) / max(1e-9, hi - lo) * cols))
                    buckets[idx].append(v)
                values = [sum(b) / len(b) if b else 0.0 for b in buckets]
            peak = max(values) or 1.0
            shown = int(cols * p["progress"])
            rows = height - 3
            fault_col = None
            if p.get("fault_at") is not None and hi > lo:
                fault_col = min(cols - 1, int((p["fault_at"] - lo) / (hi - lo) * cols))
            for c in range(shown):
                level = values[c] / peak * rows * 8
                for r in range(rows):
                    fill = max(0, min(8, int(level - r * 8)))
                    ch = self.spark[fill] if fill else (" " if c != fault_col else self.G["vline"])
                    attr = self.C["red"] if c == fault_col and fault_col is not None and fill == 0 else colour
                    if c == fault_col and fill:
                        attr = self.C["red"]
                    self.put(y0 + height - 2 - r, x0 + 2 + c, ch, attr)
            current = values[shown - 1] if shown else 0.0
            self.put(y0 + 1, x0 + 2, p["label"], curses.A_BOLD)
            info = f"{current:,.0f} ops/s   peak {peak:,.0f}"
            if fault_col is not None and shown > fault_col:
                info += f"   {self.G['fault']}fault injected"
            self.put(y0 + 1, x0 + width - len(info) - 2, info, self.C["red"] if "fault" in info else self.C["grey"])
        elif p["kind"] == "wait":
            self.put(y0 + 1, x0 + 2, p["label"], curses.A_BOLD)
            done = int(inner * p["progress"])
            self.bar(y0 + 3, x0 + 2, inner, p["progress"], "crdb" if p["engine"] == "cockroachdb" else "pg")
            real = p["real_s"] * p["progress"]
            self.put(y0 + 4, x0 + 2, f"{int(real) // 60}m {int(real) % 60:02d}s of {int(p['real_s']) // 60}m {int(p['real_s']) % 60:02d}s recorded", self.C["grey"])
        elif p["kind"] == "tf":
            self.put(y0 + 1, x0 + 2, p["label"], curses.A_BOLD)
            y = y0 + 3
            for name in ("Linode", "Azure", "GCP"):
                total = self.tf_totals.get(name, 0) or 1
                count = self.tf_counts.get(name, 0)
                bar_w = max(10, inner - 26)
                fill = int(bar_w * count / total)
                self.put(y, x0 + 2, f"{name:<7}", curses.A_BOLD)
                self.bar(y, x0 + 10, bar_w, count / total, "cyan")
                word = "remaining" if self.stage == "tfd" else "created"
                self.put(y, x0 + 11 + bar_w, f"{count:>2}/{total} {word}", self.C["grey"])
                y += 1
            if self.captions:
                self.put(y0 + height - 1, x0 + 2,
                         "terraform output reconstructed from terraform/*.tf - timings typical, not recorded",
                         self.C["grey"])
        else:
            self.put(y0 + 2, x0 + 2, p.get("label", ""), self.C["grey"])

    def _help(self, y: int, w: int) -> None:
        text = " space pause   n next step   + / - speed   q quit"
        self.put(y, 0, text.ljust(w - 1), self.C["grey"])

    # -- playback -----------------------------------------------------------
    def keys(self) -> None:
        while True:
            k = self.scr.getch()
            if k == -1:
                return
            if k in (ord("q"), ord("Q")):
                raise KeyboardInterrupt
            if k in (ord("+"), ord("=")):
                self.speed = min(16, self.speed * 1.5)
            elif k in (ord("-"), ord("_")):
                self.speed = max(0.25, self.speed / 1.5)
            elif k in (ord("n"), curses.KEY_RIGHT):
                self.skip_to_next_step = True
            elif k == ord(" "):
                t0 = time.monotonic()
                self.put(self.scr.getmaxyx()[0] - 1, 0, " PAUSED - space to resume ", self.C["bar"])
                self.scr.refresh()
                self.scr.nodelay(False)
                while self.scr.getch() != ord(" "):
                    pass
                self.scr.nodelay(True)
                self.paused_for += time.monotonic() - t0
                self.last_tick = time.monotonic()

    def sleep(self, seconds: float, frame=None) -> None:
        """Advance the schedule by ``seconds`` of playback, drawing as it goes.

        Playback runs on a virtual clock that advances at ``speed`` times real
        time. Each call moves the *target* on by its scheduled duration, so time
        spent drawing is absorbed by the next wait instead of adding up across a
        thousand lines -- the replay lasts as long as it was scheduled to.
        """
        start_v = self.vclock
        self.vtarget += seconds
        span = self.vtarget - start_v
        while True:
            self.keys()
            if self.skip_to_next_step:
                self.vclock = self.vtarget
                return
            now = time.monotonic()
            self.vclock += (now - self.last_tick) * self.speed
            self.last_tick = now
            if frame is not None:
                frame(1.0 if span <= 0 else (self.vclock - start_v) / span)
            if frame is not None or now - self.last_draw >= 0.03 or self.vclock >= self.vtarget:
                self.draw()
                self.last_draw = now
            if self.vclock >= self.vtarget:
                return
            time.sleep(min(0.04, (self.vtarget - self.vclock) / self.speed))

    def run(self) -> None:
        for e in self.events:
            if self.skip_to_next_step and not (e.kind == "stage" and "." not in e.stage and ":" not in e.stage and e.stage != "done"):
                self._apply_instant(e)
                continue
            self.skip_to_next_step = False
            getattr(self, f"_play_{e.kind}")(e)
        self.draw()

    def _apply_instant(self, e: Event) -> None:
        if e.kind == "stage":
            self._stage(e.stage)
        elif e.kind in ("line", "tf"):
            self.log.append(e.text)
            self._tf_count(e)
        elif e.kind == "cmd":
            self.log.append(self._prompt(e) + e.text)
        elif e.kind == "anim":
            for _, text in e.marks:
                self.log.append(text)
        self.recorded_s += e.real_s

    def _stage(self, stage: str) -> None:
        if stage == "done":
            self.done_stages.update(s for s, _, _ in STEPS)
            self.done_stages.update(f"{p}:{s}" for p in ("crdb", "pg") for s, _ in SUBSTAGES)
            self.stage = ""
            return
        if self.stage:
            top_old = self.stage.split(":")[0]
            top_new = stage.split(":")[0]
            if ":" in self.stage or top_old != top_new:
                self.done_stages.add(self.stage)
            if top_old != top_new:
                self.done_stages.add(top_old)
        self.stage = stage
        if stage in ("tf1", "tf2", "tfd"):
            label = {"tf1": "provisioning 5 nodes + 1 client across Linode, Azure and GCP",
                     "tf2": "re-provisioning every node with database_engine = postgresql",
                     "tfd": "tearing the CockroachDB deployment down"}[stage]
            self.tf_counts = {k: (self.tf_totals[k] if stage == "tfd" else 0) for k in self.tf_totals}
            self.panel = {"kind": "tf", "label": label}

    def _tf_count(self, e: Event) -> None:
        if e.tf:
            prov = e.tf["provider"]
            self.tf_counts[prov] = max(0, min(self.tf_totals.get(prov, 0), self.tf_counts.get(prov, 0) + e.tf["delta"]))

    def _prompt(self, e: Event) -> str:
        return e.label if e.label else f"\x1b[32m{self.G['prompt']}\x1b[0m "

    def _play_card(self, e: Event) -> None:
        h, w = self.scr.getmaxyx()
        lines = [
            ("Project Hydra", curses.A_BOLD),
            ("CockroachDB vs PostgreSQL/Patroni on a five-node, three-cloud testbed", 0),
            ("", 0),
            ("Replaying the recorded thesis-extended runs of 2026-09-11:", self.C["grey"]),
            ("provision, measure, tear down, redeploy, measure again.", self.C["grey"]),
            ("Every experiment line and throughput graph is from the recorded runs.", self.C["grey"]),
        ]
        end = time.monotonic() + e.playback / self.speed
        while time.monotonic() < end and not self.skip_to_next_step:
            self.keys()
            self.scr.erase()
            for i, (text, attr) in enumerate(lines):
                self.put(h // 2 - 3 + i, max(0, (w - len(text)) // 2), text, attr)
            self.scr.refresh()
            time.sleep(0.05)
        self.vtarget += e.playback
        self.vclock = self.vtarget
        self.last_tick = time.monotonic()

    def _play_stage(self, e: Event) -> None:
        self._stage(e.stage)
        if e.stage.endswith((":p1", ":load", ":checks")):
            self.panel = {"kind": "idle", "label": ""}

    def _play_cmd(self, e: Event) -> None:
        prompt = self._prompt(e)
        self.log.append(prompt)
        self.sleep(0.25)
        typed = ""
        per_char = max(0.0, (e.playback - 0.4)) / max(1, len(e.text))
        for ch in e.text:
            typed += ch
            self.log[-1] = prompt + typed
            self.sleep(per_char)
            if self.skip_to_next_step:
                self.log[-1] = prompt + e.text
                return
        self.sleep(0.15)

    def _play_line(self, e: Event) -> None:
        self.log.append(e.text)
        self._tf_count(e)
        self.recorded_s += e.real_s
        # Table rows scroll faster than a frame; bank their time and pay it in
        # frame-sized sleeps so the section still lasts as long as scheduled.
        self.debt += e.playback
        if self.debt >= 0.03:
            debt, self.debt = self.debt, 0.0
            self.sleep(debt)

    _play_tf = _play_line

    def _play_wait(self, e: Event) -> None:
        start = self.recorded_s
        self.panel = {"kind": "wait", "label": e.label, "progress": 0.0, "real_s": e.real_s,
                      "engine": e.engine}

        def frame(p):
            self.panel["progress"] = min(1.0, p)
            self.recorded_s = start + e.real_s * min(1.0, p)

        self.sleep(e.playback, frame)
        self.recorded_s = start + e.real_s

    def _play_anim(self, e: Event) -> None:
        start = self.recorded_s
        series = e.series or [(0.0, 0.0), (e.real_s, 0.0)]
        lo, hi = series[0][0], series[-1][0]
        pending = sorted(e.marks, key=lambda m: m[0])
        self.panel = {"kind": "anim", "label": e.label, "series": series, "progress": 0.0,
                      "engine": e.engine, "fault_at": e.fault_at}

        def frame(p):
            p = min(1.0, max(0.0, p))
            self.panel["progress"] = p
            self.recorded_s = start + e.real_s * p
            at = lo + (hi - lo) * p
            while pending and (pending[0][0] <= at or p >= 1.0):
                self.log.append(pending.pop(0)[1])

        self.sleep(e.playback, frame)
        for _, text in pending:
            self.log.append(text)
        self.recorded_s = start + e.real_s


# ----------------------------------------------------------------------------
# plain mode (no curses): for recording in a plain terminal or piping
# ----------------------------------------------------------------------------

def play_plain(events: list[Event], speed: float) -> None:
    out = sys.stdout
    for e in events:
        if e.kind == "card":
            out.write("\n\x1b[1mProject Hydra\x1b[0m — replaying the recorded thesis-extended runs\n\n")
        elif e.kind == "cmd":
            out.write(e.label or "\x1b[32m❯\x1b[0m ")
            for ch in e.text:
                out.write(ch)
                out.flush()
                time.sleep(e.playback / max(1, len(e.text)) / speed)
            out.write("\n")
        elif e.kind in ("line", "tf"):
            out.write(e.text + "\n")
            out.flush()
            time.sleep(e.playback / speed)
        elif e.kind in ("anim", "wait"):
            steps = 30
            pending = sorted(e.marks, key=lambda m: m[0])
            lo = e.series[0][0] if e.series else 0.0
            hi = e.series[-1][0] if e.series else e.real_s
            for s in range(1, steps + 1):
                at = lo + (hi - lo) * s / steps
                while pending and pending[0][0] <= at:
                    out.write("\r\x1b[K" + pending.pop(0)[1] + "\n")
                bar = "#" * s + "-" * (steps - s)
                out.write(f"\r\x1b[K  \x1b[2m{e.label[:50]}  {bar}\x1b[0m")
                out.flush()
                time.sleep(e.playback / steps / speed)
            out.write("\r\x1b[K")
            for _, text in pending:
                out.write(text + "\n")
    out.write("\n")


# ----------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--minutes", type=float, default=4.0, help="playback length (default 4)")
    parser.add_argument("--plain", action="store_true", help="plain scrolling output, no full-screen UI")
    parser.add_argument("--as-recorded", action="store_true",
                        help="show the logs unspliced: the CockroachDB Phase IV timeout and its "
                             "re-run, and PostgreSQL's --skip-load")
    parser.add_argument("--ascii", action="store_true",
                        help="draw the UI with plain ASCII only, for fonts missing box and "
                             "block characters")
    parser.add_argument("--no-captions", action="store_true",
                        help="hide the caption marking Terraform output as reconstructed")
    args = parser.parse_args(argv)

    missing = [n for n in (CRDB_LOG, CRDB_P4_LOG, PG_LOG, PG_LOAD_LOG) if not (LOGS / n).exists()]
    if missing:
        print(f"missing recorded logs under {LOGS}: {', '.join(missing)}", file=sys.stderr)
        return 1

    events = storyboard(args.as_recorded)
    schedule(events, args.minutes * 60)

    if args.plain or not sys.stdout.isatty():
        try:
            play_plain(events, 1.0)
        except KeyboardInterrupt:
            pass
        return 0

    locale.setlocale(locale.LC_ALL, "")

    def run(scr):
        curses.curs_set(0)
        scr.nodelay(True)
        h, w = scr.getmaxyx()
        if w < 90 or h < 28:
            raise SystemExit(f"terminal is {w}x{h}; the replay needs at least 90x28 "
                             "(or use --plain)")
        screen = Screen(scr, events, args.minutes * 60, captions=not args.no_captions,
                        ascii_only=args.ascii)
        screen.run()
        screen.panel = {"kind": "idle", "label": "replay complete - press q to exit"}
        screen.draw()
        scr.nodelay(False)
        while scr.getch() not in (ord("q"), ord("Q"), 27):
            pass

    try:
        curses.wrapper(run)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
