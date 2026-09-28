"""The chart catalogue: 31 charts in five groups, drawn from every run on disk.

``crdblab insights`` (or ``./generate_insights.sh``) renders it into
``insights/<stamp>_<profile|all>/``, with ``insights.md``, a self-contained
``dashboard.html``, and ``summary.json``/``summary.csv``/``chart_status.csv``
beside the charts. See ``docs/data-schema.md`` for the output layout.

Groups: A benchmark and saturation, B hardware utilisation, C resilience,
D engine comparison, E network and provenance.
"""

from .run import generate

__all__ = ["generate"]
