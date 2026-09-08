"""Tests for the declared testbed topology.

The topology is the one fact every phase reads and no phase re-derives, so an
error here is silently inherited by every measurement rather than caught by one.
These tests pin the properties that other code assumes without checking.
"""

from __future__ import annotations

import pytest

from crdblab.topology import CLIENT_NODE, DEFAULT_TOPOLOGY, Node, Topology


def test_the_gateway_is_the_gcp_node():
    """The gateway is the gcp node, which was previously the baseline node.
    """
    gateway = DEFAULT_TOPOLOGY.gateway
    assert gateway.name == "gcp-1"
    assert gateway.host == "crdb-gcp-1"
    assert gateway.provider == "gcp"

    assert CLIENT_NODE.provider == "gcp"
    assert CLIENT_NODE.name not in DEFAULT_TOPOLOGY.names


def test_exactly_one_node_is_the_gateway():
    """``Topology.gateway`` raises rather than picking one, and every phase calls
    it. A second gateway flag would fail the whole harness at the first phase,
    which is the intended behaviour, but it is cheaper to fail here."""
    assert sum(1 for node in DEFAULT_TOPOLOGY if node.gateway) == 1
    two = Topology(
        nodes=(
            Node("a", "a", "root", "x", "r", "l", gateway=True),
            Node("b", "b", "root", "x", "r", "l", gateway=True),
        )
    )
    with pytest.raises(ValueError, match="exactly one gateway"):
        _ = two.gateway


def test_the_gateway_is_inside_the_lease_preference_triangle():
    """Leaseholders are pinned to us-east, us-east1 and us-west.

    A gateway outside that set would put a wide-area hop on every operation while
    the cluster reported full health -- D7's shape. The bootstrap's list is the
    other half of this and lives outside the repository, so ``run-experiment.sh``
    asserts the ordering against the live cluster; this asserts the membership,
    which is the part declared here.
    """
    assert DEFAULT_TOPOLOGY.gateway.region in {"us-east", "us-east1", "us-west"}


def test_the_client_node_is_not_a_member_of_the_cluster():
    """``len(topology)`` is used as the voter count, so admitting the
    client node into it would corrupt the quorum arithmetic."""
    assert CLIENT_NODE.name not in DEFAULT_TOPOLOGY.names
    assert len(DEFAULT_TOPOLOGY) == 5


def test_the_chaos_target_default_is_not_the_gateway():
    """Failing the node the generator and both audit clients run on would remove
    the measurement apparatus along with the node under test. ``p4_chaos.run``
    refuses that at run time; this catches it in a profile review instead."""
    from crdblab.config import Profile

    for name in ("thesis", "thesis-extended", "smoke"):
        target = Profile.load(name).chaos.target
        assert target in DEFAULT_TOPOLOGY.names
        assert target != CLIENT_NODE.name


def test_cluster_target_generates_a_single_gateway_uri_for_cockroachdb():
    """Not one URI per cluster member.

    `cockroach workload run`, given more than one URL, dials its
    --concurrency connections *serially* against the list rather than in
    parallel -- ~2.65s each, measured on this topology, turning a sub-second
    connect into minutes at any real concurrency (and once desynchronised a
    chaos run's fault-injection timer from the generator ever starting).
    """
    from crdblab.config import Settings
    from crdblab.phases.bench import cluster_target

    settings = Settings(db_uri="postgresql://root@crdb-gcp-1:26257/ycsb", topology=DEFAULT_TOPOLOGY)
    target = cluster_target(settings, database="ycsb", engine="cockroachdb")
    assert target.db_uri == "postgresql://root@crdb-gcp-1:26257/ycsb?sslmode=disable"


def test_cluster_target_generates_a_single_local_uri_for_postgresql():
    """One host (the client node's pgbouncer, which forwards to the HAProxy that
    follows Patroni's leader) and a password: Patroni's pg_hba is md5 for every host connection, so an
    uncredentialed DSN is refused before the generator sends an operation."""
    from crdblab.config import Settings
    from crdblab.phases.bench import cluster_target

    settings = Settings(
        db_uri="postgresql://root@crdb-gcp-1:26257/ycsb",
        topology=DEFAULT_TOPOLOGY,
        pg_password="s3cret",
    )
    target = cluster_target(settings, database="ycsb", engine="postgresql")
    assert target.db_uri == "postgresql://root:s3cret@127.0.0.1:6432/ycsb?sslmode=disable"


def test_cockroachdb_target_stays_uncredentialed():
    """CockroachDB runs --insecure here and takes root with no password; adding
    one to that DSN would change what the CockroachDB half of the comparison
    connects as."""
    from crdblab.config import Settings
    from crdblab.phases.bench import cluster_target

    settings = Settings(topology=DEFAULT_TOPOLOGY, pg_password="s3cret")
    assert "s3cret" not in cluster_target(settings, engine="cockroachdb").db_uri


def test_a_password_with_url_metacharacters_is_escaped():
    """A password is copied into a URL, so `@` or `/` in one would otherwise
    re-parse the DSN into a different host."""
    from crdblab.config import Settings
    from crdblab.phases.bench import cluster_target

    settings = Settings(topology=DEFAULT_TOPOLOGY, pg_password="p@ss/word")
    uri = cluster_target(settings, engine="postgresql").db_uri
    assert uri == "postgresql://root:p%40ss%2Fword@127.0.0.1:6432/ycsb?sslmode=disable"



# --- PostgreSQL connection strings ------------------------------------------

def test_the_generator_gets_one_host_and_the_measurement_clients_get_all_five():
    """Two different jobs. `cockroach workload run` must be given exactly one
    URL (more than one and it dials its connections serially), so the generator
    goes through the client node's HAProxy. The RPO audit writer and the RTO
    probe must survive the fault they are measuring, so they use libpq's own
    multi-host resolution instead of depending on that one proxy."""
    from crdblab.config import pg_direct_dsn, pg_generator_dsn

    single = pg_generator_dsn("ycsb", "pw")
    # pgbouncer, not HAProxy directly: `cockroach workload` sends
    # allow_unsafe_internals as a startup parameter and PostgreSQL rejects
    # unknown ones with a FATAL, so the generator cannot reach the cluster
    # without something that drops it. pgbouncer forwards to HAProxy.
    assert single.count("@") == 1 and "127.0.0.1:6432" in single
    assert "," not in single

    direct = pg_direct_dsn(DEFAULT_TOPOLOGY, "chaos_audit", "pw")
    assert direct.count(":5432") == len(DEFAULT_TOPOLOGY.nodes)
    assert "127.0.0.1" not in direct
    # Without this libpq would happily settle on a replica, where the audit
    # writer's INSERTs cannot run at all.
    assert "target_session_attrs=read-write" in direct


def test_dsn_passwords_are_escaped_in_both_builders():
    from crdblab.config import pg_direct_dsn, pg_generator_dsn

    assert "p%40ss" in pg_generator_dsn("ycsb", "p@ss")
    assert "p%40ss" in pg_direct_dsn(DEFAULT_TOPOLOGY, "ycsb", "p@ss")
