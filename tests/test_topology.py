"""Tests for the declared testbed topology."""

from __future__ import annotations

import pytest

from crdblab.topology import CLIENT_NODE, DEFAULT_TOPOLOGY, Node, Topology


def test_the_gateway_is_the_gcp_node():
    """The gateway is the GCP node; the client node is separate."""
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
    """Leaseholders are pinned to us-east, us-east1 and us-west."""
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
    """Not one URI per cluster member."""
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
    """The generator gets one URL (HAProxy); the audit writer and probe get all five hosts."""
    from crdblab.config import pg_direct_dsn, pg_generator_dsn

    single = pg_generator_dsn("ycsb", "pw")
    # pgbouncer strips the startup parameter PostgreSQL rejects, then forwards
    # to HAProxy.
    assert single.count("@") == 1 and "127.0.0.1:6432" in single
    assert "," not in single

    direct = pg_direct_dsn(DEFAULT_TOPOLOGY, "chaos_audit", "pw")
    assert direct.count(":5432") == len(DEFAULT_TOPOLOGY.nodes)
    assert "127.0.0.1" not in direct
    # Without this libpq would happily settle on a replica, where the audit
    # writer's INSERTs cannot run at all.
    assert "target_session_attrs=read-write" in direct


def test_the_measurement_clients_bound_established_connections_not_just_new_ones():
    """A black-holed socket must fail, not block forever."""
    from crdblab.config import PG_TCP_USER_TIMEOUT_MS, pg_direct_dsn, pg_generator_dsn

    direct = pg_direct_dsn(DEFAULT_TOPOLOGY, "chaos_audit", "pw")
    assert f"tcp_user_timeout={PG_TCP_USER_TIMEOUT_MS}" in direct
    assert "keepalives=1" in direct
    assert PG_TCP_USER_TIMEOUT_MS > 5000, (
        "must be looser than the probe's server-side statement_timeout, or it "
        "would pre-empt the server's own answer"
    )
    # The generator's loopback path has no partition to survive and must stay
    # identical across engines.
    assert "tcp_user_timeout" not in pg_generator_dsn("ycsb", "pw")


def test_dsn_passwords_are_escaped_in_both_builders():
    from crdblab.config import pg_direct_dsn, pg_generator_dsn

    assert "p%40ss" in pg_generator_dsn("ycsb", "p@ss")
    assert "p%40ss" in pg_direct_dsn(DEFAULT_TOPOLOGY, "ycsb", "p@ss")
