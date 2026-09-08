"""Tests for figure filenames staying distinct across engines and fault classes.

A figure's filename is part of its provenance in this project -- a caption
cites it, and a re-render has to produce the same name for the same figure.
Two failures of that guarantee have happened here: ``fig6_..._recover.png``
once had no code path that produced it (fixed by keying on fault mode), and a
CockroachDB run's figures would silently be overwritten by a PostgreSQL run's,
since nothing in a filename recorded which engine produced it. These pin the
fix for the second one without needing to render an actual figure.
"""

from __future__ import annotations

from types import SimpleNamespace

from crdblab.report.figures import _engine_suffix, _resilience_filename


def _run(engine="cockroachdb"):
    return SimpleNamespace(engine=engine)


def test_cockroachdb_gets_no_suffix():
    """So every filename and caption written before Postgres existed keeps
    meaning the same figure -- the default engine's name does not move."""
    assert _engine_suffix(_run("cockroachdb")) == ""


def test_postgresql_gets_a_distinguishing_suffix():
    assert _engine_suffix(_run("postgresql")) == "_postgresql"


def test_runs_that_disagree_on_engine_get_no_suffix_rather_than_a_guess():
    """A figure overlaying both engines' runs is a real, if currently unused,
    call shape (`throughput_sweep` takes a sequence). Tagging it with one
    engine's name would misattribute it; blank is the honest answer."""
    assert _engine_suffix(_run("cockroachdb"), _run("postgresql")) == ""


def test_no_runs_gets_no_suffix():
    assert _engine_suffix() == ""


def test_none_entries_are_ignored_not_treated_as_a_third_engine():
    assert _engine_suffix(_run("cockroachdb"), None) == ""


def test_a_run_with_no_recorded_engine_defaults_to_cockroachdb():
    """Every run written before Manifest.engine existed was a CockroachDB run --
    the flag that lets --engine postgresql be requested didn't exist either."""
    legacy = SimpleNamespace()  # no .engine attribute at all
    assert _engine_suffix(legacy) == ""


def test_resilience_filenames_stay_engine_distinct_for_both_fault_classes():
    assert _resilience_filename("dead", "") == "fig5_resilience_timeline.png"
    assert (
        _resilience_filename("dead", "_postgresql")
        == "fig5_resilience_timeline_postgresql.png"
    )
    assert (
        _resilience_filename("recover", "")
        == "fig6_resilience_timeline_recover.png"
    )
    assert (
        _resilience_filename("recover", "_postgresql")
        == "fig6_resilience_timeline_recover_postgresql.png"
    )


def test_an_unnamed_fault_class_still_gets_an_engine_suffix():
    assert (
        _resilience_filename("network_partition", "_postgresql")
        == "fig5_resilience_timeline_network_partition_postgresql.png"
    )


def test_the_four_named_figure_pairs_never_collide_across_engines():
    """The concrete case this whole fix exists for: run the thesis sweep
    against both engines and every figure from one must survive the other."""
    crdb = [
        _resilience_filename("dead", _engine_suffix(_run("cockroachdb"))),
        _resilience_filename("recover", _engine_suffix(_run("cockroachdb"))),
    ]
    pg = [
        _resilience_filename("dead", _engine_suffix(_run("postgresql"))),
        _resilience_filename("recover", _engine_suffix(_run("postgresql"))),
    ]
    assert set(crdb).isdisjoint(pg)
