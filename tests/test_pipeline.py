"""Pipeline helpers: which tailnet devices get deleted, and the per-engine DB_URI."""

import pytest

from crdblab.config import default_db_uri
from crdblab.topology import DEFAULT_TOPOLOGY
from pipeline import nodes
from pipeline.tailscale import matching

HOSTS = nodes.hostnames()


def dev(name, hostname=None):
    return {"id": name, "name": f"{name}.tail1234.ts.net", "hostname": hostname or name}


def names(devices):
    return sorted(d["id"] for d in devices)


def test_six_vms_including_the_client():
    assert len(HOSTS) == 6
    assert "crdb-client-1" in HOSTS and "crdb-gcp-1" in HOSTS


def test_exact_and_collision_renamed_devices_match():
    devices = [dev("crdb-gcp-1"), dev("crdb-gcp-1-1", hostname="crdb-gcp-1"),
               dev("crdb-linode-2-3", hostname="localhost")]
    assert names(matching(devices, HOSTS)) == ["crdb-gcp-1", "crdb-gcp-1-1", "crdb-linode-2-3"]


def test_similar_but_different_names_do_not_match():
    devices = [dev("crdb-gcp-10"), dev("crdb-gcp-1x"), dev("my-crdb-gcp-1"),
               dev("faiz-macbook"), dev("crdb-linode-12")]
    assert matching(devices, HOSTS) == []


def test_workstation_is_never_matched():
    me = dev("crdb-client-1")
    assert matching([me], HOSTS, exclude_name="crdb-client-1.tail1234.ts.net") == []


def test_db_uri_cockroachdb_lists_every_member_gateway_first():
    uri = default_db_uri("cockroachdb", DEFAULT_TOPOLOGY, "unused")
    hosts = uri.split("@", 1)[1].split("/", 1)[0].split(",")
    assert hosts[0] == f"{DEFAULT_TOPOLOGY.gateway.host}:26257"
    assert len(hosts) == 5 and all(h.endswith(":26257") for h in hosts)
    assert uri.endswith("/ycsb?sslmode=disable")


def test_db_uri_postgresql_goes_through_haproxy_with_password():
    uri = default_db_uri("postgresql", DEFAULT_TOPOLOGY, "p@ss")
    assert uri == "postgresql://root:p%40ss@127.0.0.1:5000/ycsb?sslmode=disable"


def test_db_uri_rejects_unknown_engine():
    with pytest.raises(ValueError):
        default_db_uri("mysql", DEFAULT_TOPOLOGY, "x")
