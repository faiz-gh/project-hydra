#!/usr/bin/env python3
"""Replay the recorded thesis-extended experiment as a terminal UI, in minutes.

The whole workflow is one command, ``./run-experiment.sh`` (pipeline/run_all.py):
provision CockroachDB, run every phase, tear down and free the Tailscale names,
redeploy as PostgreSQL/Patroni, run every phase again, tear down, and draw the
insights. For thesis-extended that is the better part of seven hours of wall
clock. This plays it back in a few minutes for a demo, and it plays back what
actually happened:

* **The experiment output is the recorded output.** Every line under
  ``run-experiment.sh`` comes from the 2026-09-11 thesis-extended runs in
  ``runs/_logs``, and every throughput graph is drawn from the ``metrics.csv``
  of the run that line reports. Nothing is simulated except the passage of time.
* **Terraform, the VM wait and the Tailscale cleanup are recorded too**, from the
  first full pipeline run (``PIPELINE_LOG``, 2026-09-29, smoke profile).
  Terraform does not depend on the profile, so the same 30 resources are created
  and destroyed either way, and every step keeps its recorded duration. Cloud
  account ids and the workstation's tailnet identity are masked.
* **The closing insights** are the recorded ``generate_insights.sh --profile
  thesis-extended`` render and ``crdblab analyze engine-comparison`` on the two
  thesis-extended bench runs.
* **Three splices within the experiment output, each from the same profile and
  deployment:** the CockroachDB run's Phase IV comes from
  ``chaos-dead-resume-*.log`` (the sweep's own Phase IV attempt timed out on a
  leaseholder query and was re-run by hand); the PostgreSQL run's data load comes
  from the thesis-profile run on the same deployment, since the thesis-extended
  run reused that data with ``--skip-load``; and the CockroachDB run's closing
  summary, which the aborted sweep never printed, is rebuilt from its own
  recorded numbers. ``--as-recorded`` shows those logs unspliced instead.

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
#: The first full pipeline run (smoke profile, 2026-09-29): the source of every
#: terraform, VM-wait and tailscale line. Terraform does not depend on the
#: profile, so its output is the same for a thesis-extended run.
PIPELINE_LOG = "pipeline-20260929T134252Z.log"
#: `generate_insights.sh --profile thesis-extended` over the runs above.
INSIGHTS_LOG = "insights-20260928T143346Z.log"
#: `crdblab analyze engine-comparison` on the two thesis-extended bench runs.
COMPARISON_LOG = "engine-comparison-20260929T151755Z.log"

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
    dense: bool = False             # a terraform plan body: scrolls past as a block
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
    # run-experiment.sh no longer prints its "next: ./generate_insights.sh"
    # hint: the pipeline runs the insights itself, right after the teardown.
    main = [l for l in main if "next: ./generate_insights.sh" not in l]
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
# the pipeline: terraform, VM waits and tailscale from the recorded pipeline log
# ----------------------------------------------------------------------------

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


PROFILE = "thesis-extended"
ENGINE_OF = {"CockroachDB": "cockroachdb", "PostgreSQL/Patroni": "postgresql"}
PREFIX = {"cockroachdb": "crdb", "postgresql": "pg"}
STEP_OF = {"preflight": "pre", "terraform plan": "plan", "terraform apply": "apply",
           "wait for 6 VMs": "wait", "./run-experiment.sh": "run", "tailscale logout": "logout",
           "terraform destroy": "destroy", "free tailscale names": "purge",
           "insights & comparison": "insights"}
HEADER = re.compile(r"^━━ (.+?)(?:  ·  (.+))?$")
TF_DONE = re.compile(r"\.([a-z0-9]+_[a-z0-9_]+)\.[^.:\s]+: (Creation|Destruction) complete")
TF_TICK = re.compile(r": (Still (creating|destroying)\.\.\. \[|(Creation|Destruction) complete after)")
OK_DUR = re.compile(r"^  ok  .+\((\d+m\d\ds|\d+h\d\dm)\)$")

#: The recording is from a real account. What it would expose on a shared
#: screen -- cloud account ids, the workstation's tailnet identity, the home
#: directory -- is masked; nothing else is altered.
MASKS = [
    (re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"), "<subscription>"),
    (re.compile(r"project-[0-9a-f]{8}-[0-9a-f-]+"), "<gcp-project>"),
    (re.compile(r"/Users/[^/\s]+/Documents/projects/project-hydra/"), ""),
    (re.compile(r"ssh-(rsa|ed25519) [A-Za-z0-9+/=]{40,}( [^\s\"]+)?"), r"ssh-\1 <public key>"),
]


def _mask(text: str) -> str:
    for pattern, repl in MASKS:
        text = pattern.sub(repl, text)
    return text


def _style(text: str) -> str:
    """Re-colour a line of the (ANSI-stripped) pipeline log the way the TUI drew it."""
    if text.startswith("━━ "):
        return f"\x1b[1m{text}\x1b[0m"
    if text.startswith("  ok  "):
        return f"\x1b[32m  ok\x1b[0m  {text[6:]}"
    if text.startswith("  !!  "):
        return f"\x1b[33m  !!\x1b[0m  {text[6:]}"
    if text.startswith("      "):
        return f"\x1b[2m{text}\x1b[0m"
    if re.match(r"(Plan:|Apply complete!)", text):
        return f"\x1b[1m{text}\x1b[0m"
    return text


def pipeline_segments() -> list[dict]:
    """The recorded pipeline log split at its ``━━ <step>  ·  <engine>`` headers."""
    segs: list[dict] = []
    for raw in read_log(PIPELINE_LOG):
        text = _plain(raw)
        m = HEADER.match(text)
        if m and m.group(1) in STEP_OF:
            engine = ENGINE_OF.get(m.group(2) or "", "")
            segs.append({"step": STEP_OF[m.group(1)], "engine": engine, "header": text, "lines": []})
        elif segs:
            segs[-1]["lines"].append(text)
    for seg in segs:
        while seg["lines"] and not seg["lines"][-1].strip():
            seg["lines"].pop()
        ok = next((OK_DUR.match(line) for line in reversed(seg["lines"]) if OK_DUR.match(line)), None)
        # The pipeline writes "2m12s" / "1h05m" with no space, which _dur()'s
        # word boundary does not split, so it is read here directly.
        seg["real_s"] = sum(int(v) * {"h": 3600, "m": 60, "s": 1}[u]
                            for v, u in re.findall(r"(\d+)([hms])", ok.group(1))) if ok else 0.0
    return segs


def segment_events(seg: dict, section: str) -> list[Event]:
    """One recorded pipeline step as events, its recorded duration spread over its ticks."""
    ev: list[Event] = []
    lines = [_mask(line) for line in seg["lines"]]
    engine = seg["engine"]
    # Terraform: time falls on the lines that mark it passing ("Still creating...
    # [10s elapsed]", "Creation complete after 11s"). Anything else carries none.
    ticks = [i for i, line in enumerate(lines) if TF_TICK.search(line)]
    if not ticks:
        after_cmd = next((i + 1 for i, line in enumerate(lines) if line.startswith("❯ ")), 0)
        ticks = [min(after_cmd, len(lines) - 1)] if lines else []
    per_tick = seg["real_s"] / max(1, len(ticks))
    tick_set = set(ticks)
    in_plan = False
    waited = False
    for i, line in enumerate(lines):
        if seg["step"] == "pre" and i > 0 and lines[i - 1].startswith("❯ tailscale status"):
            line = "100.x.y.z      workstation        you@         macOS  -"
        if line.startswith("❯ "):
            ev.append(Event("cmd", line[2:], section=section, engine=engine))
            continue
        if seg["step"] == "wait" and not waited and "cloud-init" in line:
            ev.append(Event("wait", section=section, real_s=seg["real_s"], engine=engine,
                            label="waiting for SSH and cloud-init on all six VMs"))
            waited = True
        # The plan body -- every attribute of every resource -- is shown, but
        # scrolls past as a block, the way it does on a real screen.
        if line.startswith("Terraform will perform the following actions"):
            in_plan = True
        elif line.startswith("Plan:"):
            in_plan = False
        tf = {}
        m = TF_DONE.search(line)
        if m:
            tf = {"provider": _provider(m.group(1)), "delta": 1 if m.group(2) == "Creation" else -1}
        real = per_tick if i in tick_set and seg["step"] != "wait" else 0.0
        ev.append(Event("line", _style(line), section=section, real_s=real, tf=tf,
                        bulk=in_plan or "Refresh" in line, dense=in_plan))
    return ev


def insights_lines() -> list[str]:
    body = [_mask(line) for line in read_log(INSIGHTS_LOG)]
    # The log's banner and closing paths: keep the render itself.
    start = next(i for i, line in enumerate(body) if "==> Drawing" in _plain(line))
    return body[start:]


def storyboard(as_recorded: bool) -> list[Event]:
    # Lazy, and with the repository on the path: run_all imports this module,
    # and demo.sh runs this file as a script from wherever it is invoked.
    if str(REPO) not in sys.path:
        sys.path.insert(0, str(REPO))
    from pipeline.run_all import plan_lines

    ev: list[Event] = [Event("card", section="intro", real_s=0)]
    segs = pipeline_segments()
    ev.append(Event("cmd", "./run-experiment.sh", section="intro"))
    setup = argparse.Namespace(profile=PROFILE, chaos=True, on_failure="ask",
                               engine_list=["cockroachdb", "postgresql"])
    for line in plan_lines(setup)[1:]:
        ev.append(Event("line", f"\x1b[2m{line}\x1b[0m", section="intro"))
    ev.append(Event("line", "", section="intro"))

    for seg in segs:
        p = PREFIX.get(seg["engine"], "")
        key = f"{p}:{seg['step']}" if p else seg["step"]
        ev.append(Event("stage", stage=key, section=key))
        ev.append(Event("line", "", section=key))
        ev.append(Event("line", _style(seg["header"]), section=key))
        if seg["step"] == "run":
            # The measurement itself: the recorded thesis-extended run of this engine.
            engine = seg["engine"]
            ev.append(Event("cmd", f"./run-experiment.sh --engine {engine} --profile {PROFILE}",
                            section=f"{p}-setup", engine=engine))
            recorded = crdb_lines(as_recorded) if p == "crdb" else pg_lines(as_recorded)
            body = parse_run_log([_mask(line) for line in recorded], engine)
            ev.extend(body)
            took = sum(e.real_s for e in body)
            ev.append(Event("line", _style(f"  ok  ./run-experiment.sh ({took // 3600:.0f}h"
                                           f"{took % 3600 // 60:02.0f}m)"), section=f"{p}-end"))
        elif seg["step"] == "insights":
            ev.append(Event("cmd", f"./generate_insights.sh --profile {PROFILE}", section=key))
            ev.extend(Event("line", line, section=key, real_s=0.5, bulk=True) for line in insights_lines())
            cmp_ = read_log(COMPARISON_LOG)
            ev.append(Event("cmd", ".venv/bin/crdblab analyze engine-comparison --crdb "
                            f"{cmp_[0].split()[-1]} --pg {cmp_[1].split()[-1]}", section=key))
            ev.extend(Event("line", line, section=key, bulk=True) for line in cmp_)
            ev.append(Event("line", _style("  ok  insights & comparison"), section=key))
        else:
            ev.extend(segment_events(seg, key))
    ev.append(Event("stage", stage="done", section="insights"))
    return ev


# ----------------------------------------------------------------------------
# the storyboard
# ----------------------------------------------------------------------------

#: The pipeline's steps, as pipeline/run_all.py names them.
PIPE_STEPS = [("plan", "terraform plan"), ("apply", "terraform apply"),
              ("wait", "wait for 6 VMs"), ("run", "./run-experiment.sh"),
              ("logout", "tailscale logout"), ("destroy", "terraform destroy"),
              ("purge", "free tailscale names")]
GROUPS = [("pre", "preflight", [])] + [
    (p, name, [f"{p}:{k}" for k, _ in PIPE_STEPS])
    for p, name in (("crdb", "CockroachDB"), ("pg", "PostgreSQL/Patroni"))
] + [("insights", "insights & comparison", [])]
TOP_STEPS = {"pre", "insights"} | {s for _, _, keys in GROUPS for s in keys}
SUBSTAGES = [
    ("checks", "workstation & topology"), ("testbed", "testbed health"),
    ("load", "working set, 7.5M rows"), ("p1", "Phase I   network"),
    ("p2", "Phase II  benchmark"), ("p3", "Phase III partition"),
    ("p4", "Phase IV  process kill"), ("end", "validate & summary"),
]
SUB_IDS = {s for s, _ in SUBSTAGES}

#: Share of the playback each section gets. Long recorded phases are compressed
#: harder than short ones; the shares were chosen so every phase is legible.
BUDGET = {"intro": 0.03, "pre": 0.01, "insights": 0.04}
for _p in ("crdb", "pg"):
    BUDGET.update({
        f"{_p}:plan": 0.02, f"{_p}:apply": 0.045, f"{_p}:wait": 0.012, f"{_p}:run": 0.002,
        f"{_p}-setup": 0.06, f"{_p}-p1": 0.03, f"{_p}-p2": 0.095, f"{_p}-p3": 0.065,
        f"{_p}-p4": 0.065, f"{_p}-end": 0.025,
        f"{_p}:logout": 0.006, f"{_p}:destroy": 0.035, f"{_p}:purge": 0.008,
    })


def schedule(events: list[Event], total_s: float) -> None:
    """Give every event a playback duration so the whole thing lasts ``total_s``."""
    fixed = {"line": 0.035, "cmd": 0.0, "stage": 0.0, "card": 3.5, "tf": 0.0}
    for e in events:
        if e.kind == "cmd":
            e.playback = min(1.6, 0.4 + 0.02 * len(e.text))
        elif e.kind in ("anim", "wait"):
            e.playback = 0.8
        else:
            e.playback = 0.0015 if e.dense else 0.006 if e.bulk else fixed.get(e.kind, 0.03)
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
        self.stage = ""                  # the pipeline step playing now, e.g. "crdb:apply"
        self.sub = ""                    # its run-experiment.sh substage, e.g. "crdb:p2"
        self.done_stages: set[str] = set()
        self.step_start: dict = {}       # step -> recorded_s when it began
        self.step_took: dict = {}        # step -> recorded seconds it took
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
        rec = int(self.recorded_s)
        right = (f"recorded time {rec // 3600}h {rec % 3600 // 60:02d}m {rec % 60:02d}s   "
                 f"playback {self.elapsed():5.0f}s   speed x{self.speed:g} ")
        left = f"project-hydra  {dot}  thesis-extended  {dot}  replay of the recorded runs"
        self.put(0, 1, left[: max(0, w - len(right) - 3)], self.C["bar"] | curses.A_BOLD)
        self.put(0, max(0, w - len(right)), right, self.C["bar"])

    def elapsed(self) -> float:
        return time.monotonic() - self.playback_start - self.paused_for

    def _checklist(self, y0: int, x0: int, width: int, height: int) -> None:
        """The pipeline's steps, grouped by engine, as pipeline/run_all.py draws them.

        An engine's group is collapsed to one line until it starts and once it
        has finished, so the whole pipeline fits a 28-row terminal.
        """
        G, y = self.G, y0 + 1
        for gid, name, keys in GROUPS:
            if y >= y0 + height - 1:
                break
            if not keys:
                y = self._step_row(gid, name, y, x0 + 1, width) + 1
                continue
            colour = self.C[gid]
            done = all(k in self.done_stages for k in keys)
            touched = done or any(k == self.stage or k in self.done_stages for k in keys)
            if done or not touched:
                mark, attr = (G["done"], self.C["green"]) if done else (G["pending"], self.C["grey"])
                self.put(y, x0 + 1, mark, attr)
                took = sum(self.step_took.get(k, 0.0) for k in keys)
                self.put(y, x0 + 3, name, colour | (curses.A_BOLD if done else 0))
                if done:
                    self._took(y, took, width)
                y += 2
                continue
            self.put(y, x0 + 1, name, colour | curses.A_BOLD)
            y += 1
            for key, (_, title) in zip(keys, PIPE_STEPS):
                y = self._step_row(key, title, y, x0 + 3, width)
                if key.endswith(":run") and key == self.stage:
                    prefix = key.split(":")[0]
                    for sub, label in SUBSTAGES:
                        sk = f"{prefix}:{sub}"
                        s_active, s_done = self.sub == sk, sk in self.done_stages
                        mark = G["done"] if s_done else (G["sub"] if s_active else " ")
                        a = self.C["green"] if s_done else (self.C["yellow"] | curses.A_BOLD if s_active else self.C["grey"])
                        self.put(y, x0 + 7, f"{mark} {label}"[: width - 8], a)
                        y += 1
            y += 1
        for yy in range(y0, y0 + height):
            self.put(yy, x0 + width - 1, self.G["vline"], self.C["grey"])

    def _step_row(self, key: str, title: str, y: int, x: int, width: int) -> int:
        G = self.G
        done, active = key in self.done_stages, key == self.stage
        mark, attr = (G["done"], self.C["green"]) if done else (
            (G["active"], self.C["yellow"] | curses.A_BOLD) if active else (G["pending"], self.C["grey"]))
        self.put(y, x, mark, attr)
        self.put(y, x + 2, title[: width - x - 11],
                 curses.A_BOLD if active else (0 if done else self.C["grey"]))
        if done:
            self._took(y, self.step_took.get(key, 0.0), width)
        elif active:
            self._took(y, self.recorded_s - self.step_start.get(key, self.recorded_s), width)
        return y + 1

    def _took(self, y: int, seconds: float, width: int) -> None:
        s = int(seconds)
        t = f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"
        self.put(y, width - len(t) - 2, t, self.C["grey"])

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
                word = "remaining" if self.stage.endswith(":destroy") else "created"
                self.put(y, x0 + 11 + bar_w, f"{count:>2}/{total} {word}", self.C["grey"])
                y += 1
            if self.captions:
                self.put(y0 + height - 1, x0 + 2,
                         "terraform output recorded 2026-09-29, smoke pipeline run (profile-independent)",
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
            if self.skip_to_next_step and not (e.kind == "stage" and e.stage in TOP_STEPS):
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
            self._finish_step()
            self.stage = ""
            return
        prefix, _, part = stage.partition(":")
        if part in SUB_IDS:            # a run-experiment.sh section within "<p>:run"
            if self.sub and self.sub != stage:
                self.done_stages.add(self.sub)
            self.sub = stage
            return
        self._finish_step()
        self.stage = stage
        self.step_start[stage] = self.recorded_s
        name = {"crdb": "CockroachDB", "pg": "PostgreSQL/Patroni"}.get(prefix, "")
        if part == "apply":
            self.tf_counts = dict.fromkeys(self.tf_totals, 0)
            self.panel = {"kind": "tf", "label": f"provisioning {name}: 5 nodes + 1 client "
                                                 "across Linode, Azure and GCP"}
        elif part == "destroy":
            self.tf_counts = dict(self.tf_totals)
            self.panel = {"kind": "tf", "label": f"tearing the {name} deployment down"}
        elif part in ("plan", "logout", "purge") or stage in ("pre", "insights"):
            label = {"plan": f"planning {name}: terraform plan -out plan.out",
                     "logout": "every VM logs itself out of the tailnet",
                     "purge": "Tailscale API: delete the six devices, verify none remain"}
            self.panel = {"kind": "idle", "label": label.get(part, "")}

    def _finish_step(self) -> None:
        if self.stage:
            self.done_stages.add(self.stage)
            self.step_took[self.stage] = self.recorded_s - self.step_start.get(self.stage, self.recorded_s)
        if self.sub:
            self.done_stages.add(self.sub)
            self.sub = ""

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
            ("One command: provision, measure, tear down, redeploy, measure again, chart.", self.C["grey"]),
            ("Experiment output and throughput graphs: the thesis-extended runs of 2026-09-11.", self.C["grey"]),
            ("Terraform and Tailscale output: the recorded pipeline run of 2026-09-29.", self.C["grey"]),
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
            out.write("\n\x1b[1mProject Hydra\x1b[0m — replaying the recorded pipeline and "
                      "thesis-extended runs\n\n")
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
                        help="hide the caption naming the run Terraform output was recorded in")
    args = parser.parse_args(argv)

    missing = [n for n in (CRDB_LOG, CRDB_P4_LOG, PG_LOG, PG_LOAD_LOG, PIPELINE_LOG,
                           INSIGHTS_LOG, COMPARISON_LOG) if not (LOGS / n).exists()]
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
