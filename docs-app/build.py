"""Regenerate docs-app/data.js (CLI, profiles, run-data schemas, charts) from the code.

Run from the repository root after changing CLI flags, profiles, schemas or charts::

    .venv/bin/python docs-app/build.py
"""

from __future__ import annotations

import argparse
import ast
import io
import json
import sys
import tokenize
from dataclasses import fields
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: Column descriptions for the recorded CSVs; names come from crdblab.core.recorder.
COLUMN_NOTES: dict[str, dict[str, str]] = {
    "COLUMNS": {
        "ts_utc": "Wall-clock time the interval was recorded.",
        "elapsed_s": "The generator's own elapsed time, from when it started issuing operations.",
        "wall_offset_s": "Harness monotonic clock, seconds from the run's epoch. ~5 s ahead of elapsed_s (SSH and process startup). Blank in schema 2.0 runs.",
        "concurrency": "The tier's --concurrency.",
        "repetition": "Which repeat of the tier (1-based).",
        "op": "Operation type, e.g. read or update. Latency is never pooled across ops.",
        "tps": "Operations per second in this interval, for this op.",
        "tps_cum": "Cumulative operations per second since the tier started.",
        "errors_cum": "Cumulative error count since the tier started.",
        "p50_ms": "Median latency in this interval, for this op.",
        "p95_ms": "95th percentile latency.",
        "p99_ms": "99th percentile latency.",
        "pmax_ms": "Maximum latency.",
        "gateway_cpu_pct": "Always blank; per-node utilisation lives in hardware_metrics.csv.",
        "gateway_disk_iops": "Always blank; see hardware_metrics.csv.",
        "gateway_rss_bytes": "Always blank; see hardware_metrics.csv.",
    },
    "NETWORK_COLUMNS": {
        "ts_utc": "When the pair was probed.",
        "source": "Source hostname.",
        "destination": "Destination hostname.",
        "source_region": "Source region.",
        "destination_region": "Destination region.",
        "samples": "Ping replies received.",
        "loss_pct": "Packet loss percentage.",
        "rtt_min_ms": "Minimum RTT, from ping's summary line (3 decimals).",
        "rtt_mean_ms": "Mean RTT, from ping's summary line. The quorum floor is computed from this.",
        "rtt_p50_ms": "Median RTT, from per-packet lines.",
        "rtt_p95_ms": "95th percentile RTT.",
        "rtt_p99_ms": "99th percentile RTT.",
        "rtt_max_ms": "Maximum RTT.",
        "rtt_mdev_ms": "Mean deviation, from ping's summary line.",
        "rtt_resolution_ms": "Precision of the per-packet values (ping prints fewer decimals for slower links).",
    },
    "AUDIT_COLUMNS": {
        "wall_offset_s": "Seconds from the run's epoch when the attempt finished.",
        "seq_id": "Sequence number, never reused or retried.",
        "outcome": "ack (committed), ambiguous (connection failed after sending) or refused (rejected by a reachable database).",
    },
    "PROBE_COLUMNS": {
        "ts_utc": "Wall clock at microsecond resolution.",
        "seq_id": "Attempt sequence number.",
        "dispatch_offset_s": "Seconds from the epoch when the write was sent.",
        "complete_offset_s": "Seconds from the epoch when it returned. Recovery timing uses this edge.",
        "duration_ms": "complete - dispatch, in ms.",
        "outcome": "ok, timeout (the outage signature), conn_error, or refused (a probe bug, not downtime).",
        "worker": "Which worker made the attempt.",
        "detail": "Exception text, often empty.",
    },
    "HARDWARE_METRICS_COLUMNS": {
        "ts_utc": "Wall clock of the poll.",
        "wall_offset_s": "Seconds from the run's epoch.",
        "node": "Short node name (gcp-1, client-1, ...).",
        "host": "Hostname (crdb-gcp-1, ...).",
        "cpu_busy_pct": "100 x (1 - idle/total) since the previous poll. Blank on a node's first poll.",
        "cpu_seconds_idle_cum": "Raw idle CPU-seconds counter.",
        "cpu_seconds_total_cum": "Raw total CPU-seconds counter.",
        "mem_total_bytes": "MemTotal gauge.",
        "mem_available_bytes": "MemAvailable gauge.",
        "disk_read_bytes_per_s": "Read rate since the previous poll, all devices.",
        "disk_write_bytes_per_s": "Write rate since the previous poll, all devices.",
        "disk_busy_pct": "100 x delta(io_time) / delta(t).",
        "disk_read_bytes_cum": "Raw read-bytes counter.",
        "disk_write_bytes_cum": "Raw written-bytes counter.",
        "disk_io_time_seconds_cum": "Raw IO-time counter.",
        "net_rx_bytes_per_s": "Receive rate, all non-loopback interfaces.",
        "net_tx_bytes_per_s": "Transmit rate, all non-loopback interfaces.",
        "net_rx_bytes_cum": "Raw receive counter.",
        "net_tx_bytes_cum": "Raw transmit counter.",
        "load1": "1-minute load average.",
    },
}

SCHEMA_FILES = {
    "COLUMNS": "metrics.csv",
    "NETWORK_COLUMNS": "network.csv",
    "AUDIT_COLUMNS": "audit.csv",
    "PROBE_COLUMNS": "rto_probe.csv",
    "HARDWARE_METRICS_COLUMNS": "hardware_metrics.csv",
}


def _doc_comments(src: str) -> dict[int, str]:
    """Map the line after each ``#:`` comment run to the joined comment text."""
    runs: dict[int, list[str]] = {}
    last = -2
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT and tok.string.startswith("#:"):
            line = tok.start[0]
            text = tok.string[2:].strip()
            if line == last + 1 and line - 1 in runs:
                runs[line] = runs.pop(line - 1) + [text]
            else:
                runs[line] = [text]
            last = line
    return {line + 1: " ".join(t for t in texts if t) for line, texts in runs.items()}


def field_notes(path: Path) -> dict[str, dict[str, str]]:
    """``{class: {field: doc}}`` from the ``#:`` comments above annotated class fields."""
    src = path.read_text()
    notes = _doc_comments(src)
    out: dict[str, dict[str, str]] = {}
    for node in ast.parse(src).body:
        if isinstance(node, ast.ClassDef):
            out[node.name] = {
                item.target.id: notes.get(item.lineno, "")
                for item in node.body
                if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
            }
    return out


def cli_reference() -> dict:
    from crdblab.cli import build_parser

    def describe(parser: argparse.ArgumentParser, path: list[str]) -> list[dict]:
        commands: list[dict] = []
        args: list[dict] = []
        children: list[dict] = []
        for action in parser._actions:
            if isinstance(action, argparse._HelpAction):
                continue
            if isinstance(action, argparse._SubParsersAction):
                helps = {a.dest: a.help for a in action._choices_actions}
                for name, sub in action.choices.items():
                    for entry in describe(sub, path + [name]):
                        if entry["path"] == path + [name]:
                            entry["help"] = helps.get(name, "") or ""
                        children.append(entry)
                continue
            default = action.default
            if default is argparse.SUPPRESS or isinstance(action, argparse._StoreTrueAction):
                default = None
            if isinstance(action, argparse._StoreFalseAction):
                default = None
            args.append({
                "flags": action.option_strings or [action.dest],
                "positional": not action.option_strings,
                "help": action.help or "",
                "default": default if isinstance(default, (str, int, float, bool)) else None,
                "choices": list(action.choices) if action.choices else None,
                "required": bool(action.required),
                "switch": action.nargs == 0,
            })
        commands.append({"path": path, "help": "", "description": parser.description or "", "args": args,
                         "leaf": not children})
        return commands + children

    parser = build_parser()
    return {"prog": parser.prog, "commands": describe(parser, [])}


def profile_reference() -> dict:
    import yaml

    from crdblab.config import ChaosSpec, HardwareMetricsSpec, Profile, WorkloadSpec

    notes = field_notes(ROOT / "crdblab/config.py")

    sections = {}
    for label, spec in (("workload", WorkloadSpec), ("chaos", ChaosSpec), ("hardware_metrics", HardwareMetricsSpec)):
        sections[label] = [
            {"name": f.name, "default": repr(getattr(spec(), f.name)), "doc": notes[spec.__name__].get(f.name, "")}
            for f in fields(spec)
        ]
    profiles = {}
    for path in sorted((ROOT / "profiles").glob("*.yaml")):
        resolved = Profile.load(str(path)).to_dict()
        raw = yaml.safe_load(path.read_text()) or {}
        overrides = {sec: sorted((raw.get(sec) or {}).keys()) for sec in sections}
        profiles[path.stem] = {"resolved": resolved, "overrides": overrides, "file": f"profiles/{path.name}"}
    return {"sections": sections, "profiles": profiles}


def schema_reference() -> dict:
    from crdblab.core import recorder

    out = []
    for const, filename in SCHEMA_FILES.items():
        columns = getattr(recorder, const)
        out.append({
            "constant": const,
            "file": filename,
            "columns": [{"name": c, "doc": COLUMN_NOTES[const].get(c, "")} for c in columns],
        })
    manifest = [
        {"name": f.name, "type": str(f.type)} for f in fields(recorder.Manifest)
    ]
    return {"version": recorder.SCHEMA_VERSION, "csv": out, "manifest": manifest,
            "probe_outcomes": list(recorder.PROBE_OUTCOMES)}


def chart_reference() -> dict:
    from crdblab.insights._base import GROUPS, REGISTRY

    return {
        "groups": GROUPS,
        "charts": [
            {"id": c.id, "title": c.title, "caption": " ".join(c.caption.split()), "stem": c.stem}
            for c in REGISTRY
        ],
    }


def main() -> None:
    data = {
        "cli": cli_reference(),
        "profiles": profile_reference(),
        "schemas": schema_reference(),
        "charts": chart_reference(),
    }
    target = Path(__file__).with_name("data.js")
    target.write_text("window.HYDRA_DOCS = " + json.dumps(data, indent=1) + ";\n")
    commands = sum(1 for c in data["cli"]["commands"] if c["leaf"] and c["path"])
    print(f"wrote {target.relative_to(ROOT)}: {commands} commands, {len(data['charts']['charts'])} charts")


if __name__ == "__main__":
    main()
