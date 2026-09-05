"""Single source of truth for testbed topology.

The legacy scripts each carried their own copy of a ``get_ssh_user`` helper
that inferred the login account from a hostname substring, and the chaos
injector carried a separate hard-coded dictionary of the same facts. Divergence
between those copies is a latent source of experimental error, so the topology
is declared once here and consumed everywhere else.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Mapping


@dataclass(frozen=True)
class Node:
    """One cluster member.

    ``locality`` mirrors the string passed to ``cockroach start --locality`` and
    is recorded in the run manifest so that a result can always be tied back to
    the replica placement in force when it was measured.
    """

    name: str
    host: str
    user: str
    provider: str
    region: str
    locality: str
    gateway: bool = False
    sql_port: int = 26257
    http_port: int = 8080

    @property
    def http_base(self) -> str:
        return f"http://{self.host}:{self.http_port}"


@dataclass(frozen=True)
class Topology:
    nodes: tuple[Node, ...]

    def __iter__(self) -> Iterator[Node]:
        return iter(self.nodes)

    def __len__(self) -> int:
        return len(self.nodes)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(n.name for n in self.nodes)

    def get(self, name: str) -> Node:
        for node in self.nodes:
            if node.name == name:
                return node
        raise KeyError(f"unknown node {name!r}; known nodes: {', '.join(self.names)}")

    @property
    def gateway(self) -> Node:
        gateways = [n for n in self.nodes if n.gateway]
        if len(gateways) != 1:
            raise ValueError(f"exactly one gateway node must be declared, found {len(gateways)}")
        return gateways[0]

    @classmethod
    def from_mapping(cls, raw: Mapping) -> "Topology":
        return cls(nodes=tuple(Node(name=name, **spec) for name, spec in raw.items()))


#: Default five-node, three-provider testbed.
#:
#: These locality strings must equal the ones ``scripts/bootstrap.tftpl`` passes
#: to ``cockroach start --locality``; they are verified against the deployed
#: cluster rather than asserted, and are copied into every run manifest, so a
#: divergence here silently misattributes results to regions that were never
#: measured. The values below were read back from each node's own process
#: arguments on 2026-09-02.
#:
#: The bootstrap's ``lease_preferences`` names ``us-east``, ``us-east1`` and
#: ``us-west``, which makes the low-latency triangle linode-1, gcp-1 and
#: linode-2. The two Azure members sit at 198 and 218 ms from the gateway and are
#: deliberately outside it. Editing a region string here without editing the
#: bootstrap breaks leaseholder pinning without any error being raised -- that is
#: precisely defect D7.
#:
#: **The gateway is gcp-1, not linode-1.** The generator and the audit clients run
#: on whichever node carries ``gateway=True``, and Phase III is compared against
#: the Phase II baseline in :data:`BASELINE_NODE`, which is a GCP instance. While
#: the gateway was linode-1 the two phases ran on different CPU models -- Intel
#: Xeon @ 2.80 GHz against AMD EPYC 7713 -- so their throughput difference
#: confounded replication with the processor it was measured on, and
#: ``raft-overhead`` could only be computed by passing
#: ``--accept-hardware-difference`` (D11a). gcp-1 is the same GCP machine type as
#: the baseline (``n2-custom-2-4096``, 2 vCPU / ~4 GiB, Intel Xeon), so the
#: comparison is now single-variable and that override is no longer required.
#: Nothing else about the topology changed: gcp-1 was already a member, already
#: inside the fast triangle, and is still not the chaos target.
#:
#: Two consequences of the move are load-bearing and are asserted, not assumed:
#:
#: * **The write-latency floor rises, and by more than rounding.** Quorum over
#:   five voters needs the leader plus two follower acks, so the floor is the
#:   round trip to the second-fastest follower -- which from gcp-1 is linode-2 in
#:   us-west. Across the three Phase I matrices in ``runs/`` that is 68.8, 72.7
#:   and 72.8 ms, against 66.8, 66.9 and 67.1 ms from linode-1: a 3-9% increase,
#:   because gcp-1 sits marginally further from us-west than linode-1 does. It
#:   does not disturb any argument that rests on the floor -- the 3.1 ms write
#:   latency of D8 is impossible against either -- but a *measured* write median
#:   from this gateway is expected to be a few ms higher than one recorded before
#:   the move, and comparing the two across the change would attribute that to
#:   whatever else changed. Phase I recomputes the floor per run and every check
#:   bounds against the recomputed value, so nothing here is hardcoded; the
#:   numbers above are quoted so the shift is on the record.
#: * ``lease_preferences`` must name ``us-east1`` *first*, or the leaseholder
#:   stays on linode-1 and every operation pays an extra 23.7 ms hop that no
#:   health check reports. ``run-experiment.sh`` refuses to proceed unless the
#:   gateway's region heads the list, and ``preflight.check_leaseholder_placement``
#:   asserts the placement that actually resulted. See instructions.md § 2.
#:
#: NOTE: Chapter 3 of the dissertation describes the Azure and GCP members as
#: uk-south, eu-west and us-central. The deployed testbed is centralindia,
#: eastasia and us-east1, which changes the WAN latency argument materially. The
#: prose, not this file, is what needs correcting (Stage 7).
DEFAULT_TOPOLOGY = Topology(
    nodes=(
        Node("linode-1", "crdb-linode-1", "root", "linode", "us-east",
             "cloud=linode,region=us-east"),
        Node("linode-2", "crdb-linode-2", "root", "linode", "us-west",
             "cloud=linode,region=us-west"),
        Node("azure-1", "crdb-azure-1", "ubuntu", "azure", "centralindia",
             "cloud=azure,region=centralindia"),
        Node("azure-2", "crdb-azure-2", "ubuntu", "azure", "eastasia",
             "cloud=azure,region=eastasia"),
        Node("gcp-1", "crdb-gcp-1", "ubuntu", "gcp", "us-east1",
             "cloud=gcp,region=us-east1", gateway=True),
    )
)

#: Phase II baseline: a separate ``cockroach start-single-node`` instance, not a
#: member of :data:`DEFAULT_TOPOLOGY`. It is deliberately outside the cluster
#: because Phase II establishes the cost of the workload *without* replication,
#: against which Phase III measures Raft overhead. Including it in the topology
#: would corrupt ``len(topology)`` where that is used as the voter count.
#:
#: It is a GCP instance despite the ``region=self-hosted`` locality label: the
#: label says it is an isolated single-node server rather than a cluster member,
#: and does not describe where it runs (instructions.md, Appendix A).
#:
#: HISTORY of the Raft-overhead comparison, because two successive asymmetries
#: were retired here and the stale text describing them outlived both:
#:
#: * Memory. The comment this replaces read "7 GB of RAM against the cluster
#:   members' 3 GB". That was D9, and it was fixed in the provisioning: every
#:   node is now 2 vCPU / ~4 GiB (``n2-custom-2-4096``, ``g6-dedicated-2``,
#:   ``Standard_B2ls_v2``), and the captured manifests read 4,007,012 kB here
#:   against 4,005,704 kB on the gateway -- 0.03%, within
#:   ``validation.MEMORY_TOLERANCE``. The claim of a 7 GB/3 GB split has been
#:   false since that reprovisioning and is corrected rather than merely deleted,
#:   since docs/dissertation-verification.md cites this comment as a stale fact a
#:   write-up would otherwise inherit.
#: * CPU model. Until the gateway moved to gcp-1 this host was an Intel Xeon
#:   @ 2.80 GHz and the gateway an AMD EPYC 7713 (D11a), which confounded
#:   replication cost with the processor. Both phases now run on the same GCP
#:   machine type, so ``check_run_comparability`` should find nothing to accept.
#:
#: The comparison is therefore single-variable on the two axes the harness can
#: see. It still is not free of confounds it cannot see -- utilisation is not
#: matched by matching throughput, and ``raft_overhead`` reports the gap per
#: point for that reason -- so do not read "same hardware" as "same conditions".
BASELINE_NODE = Node(
    "local-1", "crdb-local-1", "ubuntu", "local", "self-hosted",
    "cloud=local,region=self-hosted",
)
