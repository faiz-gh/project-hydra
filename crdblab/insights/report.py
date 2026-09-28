"""The five artefacts a render writes beside its charts.

``insights.md`` and ``dashboard.html`` carry the same content -- every chart with
its caption and numbers, the runs behind them, and every chart that was skipped
and why -- one for a document, one as a single self-contained page with the SVGs
inlined (no external stylesheet, script or image). ``summary.json``,
``summary.csv`` and ``chart_status.csv`` carry the numbers and the drawn/skipped
record in machine-readable form.
"""

from __future__ import annotations

import csv
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ._base import GROUPS, Chart
from .data import Inventory


@dataclass
class Result:
    chart: Chart
    drawn: bool
    stats: dict[str, Any]
    files: list[str]
    skipped: str = ""

    @property
    def png(self) -> str | None:
        return next((f for f in self.files if f.endswith(".png")), None)

    @property
    def svg(self) -> str | None:
        return next((f for f in self.files if f.endswith(".svg")), None)


def fmt(value: Any) -> str:
    """One stat as a report cell: four significant figures, thousands separated.

    Integers are counts and are printed exactly; a float is a measurement and is
    printed to the precision the report can defend. A nested dict becomes
    ``key=value`` pairs, and a list (a series of points) is summarised by its
    length -- the full series is in ``summary.json``.
    """
    if isinstance(value, bool) or value is None:
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:  # NaN
            return "nan"
        return f"{value:,.0f}" if abs(value) >= 1000 else f"{value:.4g}"
    if isinstance(value, dict):
        return ", ".join(f"{k}={fmt(v)}" for k, v in value.items())
    if isinstance(value, (list, tuple)):
        return f"{len(value)} entries"
    return str(value)


def flatten(stats: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Nested stats as ``(metric, value)`` rows: keys join with ``.``, lists index ``[n]``."""
    rows: list[tuple[str, Any]] = []
    if isinstance(stats, dict):
        for key, value in stats.items():
            rows += flatten(value, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(stats, (list, tuple)):
        for index, value in enumerate(stats):
            rows += flatten(value, f"{prefix}[{index}]")
    else:
        rows.append((prefix, stats))
    return rows


def _json_safe(value: Any) -> Any:
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "item"):  # numpy scalar
        return _json_safe(value.item())
    return value


def _scope(profile: str | None) -> str:
    return profile or "all profiles"


def _where(out_dir: Path) -> str:
    return f"{out_dir.parent.name}/{out_dir.name}"


# -- markdown ----------------------------------------------------------------

def write_markdown(out_dir: Path, results: list[Result], inventory: Inventory,
                   generated: datetime) -> Path:
    drawn = sum(r.drawn for r in results)
    lines = [
        "# Measurement insights",
        "",
        f"Generated {generated:%Y-%m-%d %H:%M} UTC from `{_where(out_dir)}`.",
        "",
        f"- Profile: **{_scope(inventory.profile)}**",
        f"- Charts drawn: **{drawn}** of {len(results)}",
        f"- Runs considered: **{len(inventory.entries)}**",
        f"- Engines: {', '.join(inventory.engines) or 'none'}",
        "",
        "Every chart resolves its inputs through the gated run loader, so a run that "
        "failed pre-flight or validation cannot appear in any figure here.",
        "",
        "## Runs behind this report",
        "",
        "| run id | engine | profile | phase |",
        "|---|---|---|---|",
    ]
    lines += [f"| `{e.run_id}` | {e.engine} | {e.profile} | {e.kind} |" for e in inventory.entries]
    lines.append("")

    for letter, heading in GROUPS.items():
        group = [r for r in results if r.chart.group == letter]
        if not group:
            continue
        lines += [f"## {letter} - {heading}", ""]
        for r in group:
            lines += [f"### {r.chart.id}. {r.chart.title}", ""]
            if not r.drawn:
                lines += [f"_Skipped: {r.skipped}_", ""]
                continue
            if r.png:
                lines += [f"![{r.chart.title}]({r.png})", ""]
            lines += [r.chart.caption, ""]
            if r.stats:
                lines += ["| metric | value |", "|---|---|"]
                lines += [f"| `{k}` | {fmt(v)} |" for k, v in r.stats.items()]
                lines.append("")

    skipped = [r for r in results if not r.drawn]
    if skipped:
        lines += ["## Skipped charts", "", "| chart | reason |", "|---|---|"]
        lines += [f"| {r.chart.id}. {r.chart.title} | {r.skipped} |" for r in skipped]
        lines.append("")

    path = out_dir / "insights.md"
    path.write_text("\n".join(lines))
    return path


# -- dashboard ---------------------------------------------------------------

_CSS = """
:root {
  --surface: #fcfcfb; --panel: #ffffff; --ink: #0b0b0b; --ink-2: #52514e;
  --ink-3: #898781; --line: #e1e0d9; --accent: #2a78d6; --warn: #b8860b;
}
@media (prefers-color-scheme: dark) {
  :root {
    --surface: #16161a; --panel: #1e1e23; --ink: #f4f4f2; --ink-2: #c3c2bd;
    --ink-3: #8d8c86; --line: #33333a; --accent: #6da7ec; --warn: #fab219;
  }
  /* The charts are rendered for a light surface, so they keep their own
     background rather than inheriting a dark one they were not designed for. */
  .chart { background: #fcfcfb; border-radius: 6px; padding: 6px; }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--surface); color: var(--ink);
  font: 15px/1.55 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
}
.wrap { max-width: 1100px; margin: 0 auto; padding: 32px 20px 80px; }
h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: -0.01em; }
h2 { font-size: 19px; margin: 44px 0 12px; padding-bottom: 6px; border-bottom: 1px solid var(--line); }
h3 { font-size: 15px; margin: 0 0 8px; }
.sub { color: var(--ink-2); margin: 0 0 22px; }
.cards { display: flex; flex-wrap: wrap; gap: 10px; margin: 18px 0 8px; }
.card {
  flex: 1 1 150px; background: var(--panel); border: 1px solid var(--line);
  border-radius: 8px; padding: 12px 14px;
}
.card .n { font-size: 22px; font-weight: 600; }
.card .l { font-size: 12px; color: var(--ink-3); text-transform: uppercase; letter-spacing: .04em; }
.chartbox { background: var(--panel); border: 1px solid var(--line); border-radius: 8px;
  padding: 16px; margin: 0 0 18px; }
.chart { overflow-x: auto; }
.chart svg { max-width: 100%; height: auto; display: block; }
.cap { color: var(--ink-2); font-size: 13px; margin: 12px 0 0; }
table { border-collapse: collapse; width: 100%; font-size: 12.5px; margin-top: 12px; display: block; overflow-x: auto; }
th, td { text-align: left; padding: 5px 10px 5px 0; border-bottom: 1px solid var(--line); }
th { color: var(--ink-3); font-weight: 600; text-transform: uppercase; font-size: 11px; letter-spacing: .04em; }
code, td.m { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.skip { border-left: 3px solid var(--warn); padding-left: 12px; color: var(--ink-2); font-size: 13.5px; }
.pill { display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 20px;
  background: var(--line); color: var(--ink-2); margin-left: 8px; }
nav { position: sticky; top: 0; background: var(--surface); padding: 10px 0;
  border-bottom: 1px solid var(--line); margin-bottom: 8px; z-index: 5; }
nav a { color: var(--accent); text-decoration: none; margin-right: 14px; font-size: 13px; }
""".strip("\n")


def _inline_svg(path: Path) -> str:
    """An SVG file's ``<svg>`` element, without the XML prolog and doctype."""
    text = path.read_text()
    start = text.find("<svg")
    return text[start:] if start >= 0 else ""


def write_dashboard(out_dir: Path, results: list[Result], inventory: Inventory,
                    generated: datetime) -> Path:
    e = lambda s: html.escape(str(s), quote=True)  # noqa: E731
    drawn = sum(r.drawn for r in results)
    parts = [
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>",
        "<meta name='viewport' content='width=device-width, initial-scale=1'>",
        "<title>crdblab insights</title>",
        f"<style>\n{_CSS}\n</style></head><body><div class='wrap'>",
        "<h1>Measurement insights</h1>",
        f"<p class='sub'>{e(_scope(inventory.profile))} &middot; generated "
        f"{generated:%Y-%m-%d %H:%M} UTC</p>",
        "<div class='cards'>",
        f"<div class='card'><div class='n'>{drawn}/{len(results)}</div><div class='l'>charts drawn</div></div>",
        f"<div class='card'><div class='n'>{len(inventory.entries)}</div><div class='l'>runs</div></div>",
        f"<div class='card'><div class='n'>{len(inventory.engines)}</div><div class='l'>engines</div></div>",
        f"<div class='card'><div class='n'>{len(results) - drawn}</div><div class='l'>skipped</div></div>",
        "</div>",
        "<nav>",
    ]
    groups = [(k, v) for k, v in GROUPS.items() if any(r.chart.group == k for r in results)]
    parts += [f"<a href='#g{k}'>{k} &middot; {e(v)}</a>" for k, v in groups]
    parts += [
        "</nav>",
        "<h2>Runs behind this report</h2>",
        "<table><tr><th>run id</th><th>engine</th><th>profile</th><th>phase</th></tr>",
    ]
    parts += [
        f"<tr><td class='m'>{e(x.run_id)}</td><td>{e(x.engine)}</td>"
        f"<td>{e(x.profile)}</td><td>{e(x.kind)}</td></tr>"
        for x in inventory.entries
    ]
    parts.append("</table>")

    for letter, heading in groups:
        parts.append(f"<h2 id='g{letter}'>{letter} &middot; {e(heading)}</h2>")
        for r in (r for r in results if r.chart.group == letter):
            parts.append("<div class='chartbox'>")
            pill = "" if r.drawn else "<span class='pill'>skipped</span>"
            parts.append(f"<h3>{e(r.chart.id)}. {e(r.chart.title)}{pill}</h3>")
            if not r.drawn:
                parts += [f"<p class='skip'>{e(r.skipped)}</p>", "</div>"]
                continue
            if r.svg and (out_dir / r.svg).exists():
                parts.append(f"<div class='chart'>{_inline_svg(out_dir / r.svg)}</div>")
            parts.append(f"<p class='cap'>{e(r.chart.caption)}</p>")
            if r.stats:
                parts.append("<table><tr><th>metric</th><th>value</th></tr>")
                parts += [
                    f"<tr><td class='m'>{e(k)}</td><td class='m'>{e(fmt(v))}</td></tr>"
                    for k, v in r.stats.items()
                ]
                parts.append("</table>")
            parts.append("</div>")
    parts.append("</div></body></html>")

    path = out_dir / "dashboard.html"
    path.write_text("\n".join(parts))
    return path


# -- machine-readable --------------------------------------------------------

def write_summary(out_dir: Path, results: list[Result]) -> list[Path]:
    summary = {
        r.chart.id: {
            "title": r.chart.title,
            "drawn": r.drawn,
            "skipped": r.skipped,
            "stats": _json_safe(r.stats),
            "files": r.files,
        }
        for r in results
    }
    json_path = out_dir / "summary.json"
    json_path.write_text(json.dumps(summary, indent=2))

    csv_path = out_dir / "summary.csv"
    with open(csv_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["chart_id", "chart_title", "metric", "value"])
        for r in results:
            for metric, value in flatten(_json_safe(r.stats)):
                writer.writerow([r.chart.id, r.chart.title, metric, value])

    status_path = out_dir / "chart_status.csv"
    with open(status_path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["chart_id", "title", "status", "reason", "files"])
        for r in results:
            writer.writerow([
                r.chart.id, r.chart.title, "drawn" if r.drawn else "skipped",
                r.skipped, " ".join(r.files),
            ])
    return [json_path, csv_path, status_path]


def one_line(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()
