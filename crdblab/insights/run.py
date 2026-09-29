"""Render the whole chart catalogue into one directory.

The caller chooses a fresh ``out_dir`` per render (``cli._cmd_insights`` does).
"""

from __future__ import annotations

import traceback
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

import matplotlib.pyplot as plt

# Importing the chart modules registers their charts, in catalogue order.
from . import charts_a, charts_b, charts_c, charts_d, charts_e  # noqa: F401
from ._base import REGISTRY, Context, Skip
from .data import Inventory
from .report import Result, one_line, write_dashboard, write_markdown, write_summary


def generate(
    out_dir: Path,
    runs_dir: Path,
    profile: str | None = None,
    progress: Callable[[Result], None] | None = None,
) -> tuple[list[Result], Inventory]:
    """Draw every chart whose inputs exist, and write the report beside them.

    A chart that cannot draw, or raises, is recorded as skipped with the reason,
    so one broken chart never costs the rest.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    inventory = Inventory.scan(Path(runs_dir), profile=profile)
    ctx = Context(inventory=inventory, out_dir=out_dir)

    results: list[Result] = []
    for spec in REGISTRY:
        try:
            drawn = spec.fn(ctx)
            result = Result(spec, True, drawn.stats, drawn.files)
        except Skip as exc:
            result = Result(spec, False, {}, [], skipped=one_line(str(exc)))
        except Exception as exc:  # last resort; see docstring
            detail = traceback.format_exception_only(type(exc), exc)[-1].strip()
            result = Result(spec, False, {}, [], skipped=f"failed while drawing: {detail}")
        finally:
            plt.close("all")
        results.append(result)
        if progress is not None:
            progress(result)

    generated = datetime.now(timezone.utc)
    write_markdown(out_dir, results, inventory, generated)
    write_dashboard(out_dir, results, inventory, generated)
    write_summary(out_dir, results)
    return results, inventory
