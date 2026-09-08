"""Tests for figure filenames carrying their own provenance.

A figure's filename is the only thing that distinguishes it from its neighbours
in ``figures/``: the run ids stamped into the image's footer are invisible to
anything listing the directory. Three failures of that have happened here.
``fig6_..._recover.png`` once had no code path that produced it (fixed by
keying on fault mode). A CockroachDB run's figures would silently be
overwritten by a PostgreSQL run's, since nothing in a filename recorded which
engine produced it. And a smoke render and a thesis-scale render of the same
engine still collided, because nothing recorded the profile or the run either
-- which is what :func:`_provenance_slug` now does. These pin all of it without
needing to render an actual figure.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from crdblab.report.figures import (
    EXPORT_VECTOR_EXT,
    _provenance_slug,
    _resilience_filename,
    _written_formats,
)


def _run(engine="cockroachdb", profile="thesis", run_id="20260908T053558Z_bench_cluster"):
    """A stand-in for a loaded Run: the slug reads the manifest, not properties,
    because NetworkRun exposes neither .engine nor .profile and both carry one."""
    manifest = {"run_id": run_id, "profile": {"name": profile}}
    if engine is not None:
        manifest["engine"] = engine
    return SimpleNamespace(manifest=manifest, run_id=run_id)


def test_the_slug_names_engine_profile_and_run():
    assert _provenance_slug(_run()) == "_cockroachdb_thesis_20260908T053558Z_bench_cluster"


def test_the_engine_is_named_even_when_it_is_the_default():
    """Unlike the suffix this replaced, cockroachdb is not the blank case any
    more. A filename that names one engine only when it is the unusual one
    still leaves the reader inferring the other from its absence."""
    assert "_cockroachdb_" in _provenance_slug(_run("cockroachdb"))
    assert "_postgresql_" in _provenance_slug(_run("postgresql"))


def test_the_same_engine_at_two_profiles_does_not_collide():
    """The concrete case: a smoke self-test and a thesis-scale sweep both
    rendered into figures/. Before the profile was in the name, the second
    silently replaced the first."""
    smoke = _provenance_slug(_run(profile="smoke", run_id="20260908T014435Z_bench_cluster"))
    thesis = _provenance_slug(_run(profile="thesis"))
    assert smoke != thesis


def test_two_runs_of_the_same_profile_and_engine_do_not_collide():
    """Re-running the same profile is the common case, and the run id is the
    only thing that separates the two renders."""
    first = _provenance_slug(_run(run_id="20260908T035047Z_bench_cluster"))
    second = _provenance_slug(_run(run_id="20260908T053558Z_bench_cluster"))
    assert first != second


def test_runs_that_disagree_get_mixed_rather_than_a_guess():
    """A figure overlaying both engines' runs is a real, if currently unused,
    call shape (`throughput_sweep` takes a sequence). Tagging it with one
    engine's name would misattribute it; every run id is still listed."""
    slug = _provenance_slug(_run("cockroachdb"), _run("postgresql", profile="smoke"))
    assert slug.startswith("_mixed-engine_mixed-profile_")
    assert "20260908T053558Z_bench_cluster" in slug


def test_phase_one_omits_the_engine_it_does_not_have():
    """Phase I measures the network substrate, which is the same regardless of
    which database is deployed on it, and its manifest records no engine."""
    net = SimpleNamespace(
        manifest={"run_id": "20260908T053439Z_p1-network", "profile": {"name": "thesis"}},
        run_id="20260908T053439Z_p1-network",
    )
    assert _provenance_slug(net, engine=False) == "_thesis_20260908T053439Z_p1-network"


def test_a_run_with_no_recorded_engine_defaults_to_cockroachdb():
    """Every run written before Manifest.engine existed was a CockroachDB run --
    the flag that lets --engine postgresql be requested didn't exist either."""
    assert "_cockroachdb_" in _provenance_slug(_run(engine=None))


def test_none_entries_are_ignored_not_treated_as_a_third_run():
    assert _provenance_slug(_run(), None) == _provenance_slug(_run())


def test_slug_components_are_filename_safe():
    """Profile names and run ids reach the filesystem verbatim otherwise."""
    slug = _provenance_slug(_run(profile="thesis extended/v2"))
    assert "/" not in slug and " " not in slug


def test_resilience_filenames_stay_distinct_for_both_fault_classes():
    slug = _provenance_slug(_run(run_id="20260908T060102Z_p4-chaos-dead"))
    assert _resilience_filename("dead", slug).startswith("fig5_resilience_timeline_cockroachdb")
    assert _resilience_filename("recover", slug).startswith(
        "fig6_resilience_timeline_recover_cockroachdb"
    )
    assert _resilience_filename("dead", slug) != _resilience_filename("recover", slug)


def test_an_unnamed_fault_class_still_gets_its_own_name_and_provenance():
    assert (
        _resilience_filename("network partition", "_cockroachdb_thesis_r1")
        == "fig5_resilience_timeline_network_partition_cockroachdb_thesis_r1.png"
    )


def test_the_named_figure_pairs_never_collide_across_engines():
    """Run the thesis sweep against both engines and every figure from one must
    survive the other."""
    crdb = _provenance_slug(_run("cockroachdb", run_id="20260908T060102Z_p4-chaos-dead"))
    pg = _provenance_slug(_run("postgresql", run_id="20260910T010101Z_p4-chaos-dead"))
    names = {
        _resilience_filename(mode, slug)
        for slug in (crdb, pg)
        for mode in ("dead", "recover")
    }
    assert len(names) == 4


def test_every_figure_is_reported_as_both_a_png_and_a_vector_file():
    """`report figures` prints what it wrote, and the vector file is written by
    the same call -- listing only the PNG hid half the output."""
    written = _written_formats(Path("figures/fig2_throughput_sweep_cockroachdb_thesis_r1.png"))
    assert [p.suffix for p in written] == [".png", EXPORT_VECTOR_EXT]


def test_no_pdf_is_written_any_more():
    assert EXPORT_VECTOR_EXT == ".svg"
