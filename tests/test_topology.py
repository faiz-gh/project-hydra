"""Tests for the declared testbed topology.

The topology is the one fact every phase reads and no phase re-derives, so an
error here is silently inherited by every measurement rather than caught by one.
These tests pin the properties that other code assumes without checking.
"""

from __future__ import annotations

import pytest

from crdblab.topology import BASELINE_NODE, DEFAULT_TOPOLOGY, Node, Topology


def test_the_gateway_is_the_gcp_node():
    """The gateway shares a machine type with the Phase II baseline, by design.

    The whole of the Raft-overhead result is Phase III against Phase II, and
    while the gateway was ``crdb-linode-1`` those two ran on different processors
    -- Intel Xeon against AMD EPYC (D11a) -- so the difference between them
    confounded replication with the CPU it was measured on and the comparison
    could only be computed by passing ``--accept-hardware-difference``.

    Moving the gateway back to a node at another provider reintroduces that
    confound without changing any number that looks wrong, which is why it is
    asserted here and not left to a comment.
    """
    gateway = DEFAULT_TOPOLOGY.gateway
    assert gateway.name == "gcp-1"
    assert gateway.host == "crdb-gcp-1"
    assert gateway.provider == "gcp"

    # BASELINE_NODE.provider reads "local", which is a *role* label and not a
    # location: it says the node is an isolated single-node server rather than a
    # cluster member. It is provisioned by `local_config` in the same GCP project
    # and at the same machine type as the gateway (instructions.md, Appendix A),
    # which is the fact this test is really about. That fact cannot be asserted
    # from this file, so it is asserted where it can be -- against the hardware
    # each run actually captured, by `validation.check_run_comparability`, which
    # `run-experiment.sh` now invokes without --accept-hardware-difference for
    # exactly this reason.
    assert BASELINE_NODE.provider == "local"
    assert BASELINE_NODE.name not in DEFAULT_TOPOLOGY.names


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


def test_the_baseline_is_not_a_member_of_the_cluster():
    """``len(topology)`` is used as the voter count, so admitting the
    unreplicated baseline into it would corrupt the quorum arithmetic."""
    assert BASELINE_NODE.name not in DEFAULT_TOPOLOGY.names
    assert len(DEFAULT_TOPOLOGY) == 5


def test_the_chaos_target_default_is_not_the_gateway():
    """Failing the node the generator and both audit clients run on would remove
    the measurement apparatus along with the node under test. ``p4_chaos.run``
    refuses that at run time; this catches it in a profile review instead."""
    from crdblab.config import Profile

    for name in ("thesis", "thesis-extended", "smoke"):
        target = Profile.load(name).chaos.target
        assert target in DEFAULT_TOPOLOGY.names
        assert target != DEFAULT_TOPOLOGY.gateway.name
