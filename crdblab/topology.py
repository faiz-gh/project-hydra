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
#: linode-2, with round trips from the gateway of 0, 24.7 and 70.6 ms. The two
#: Azure members sit at 191 and 200 ms and are deliberately outside it. Editing a
#: region string here without editing the bootstrap breaks leaseholder pinning
#: without any error being raised -- that is precisely defect D7.
#:
#: NOTE: Chapter 3 of the dissertation describes the Azure and GCP members as
#: uk-south, eu-west and us-central. The deployed testbed is centralindia,
#: eastasia and us-east1, which changes the WAN latency argument materially. The
#: prose, not this file, is what needs correcting (Stage 7).
DEFAULT_TOPOLOGY = Topology(
    nodes=(
        Node("linode-1", "crdb-linode-1", "root", "linode", "us-east",
             "cloud=linode,region=us-east", gateway=True),
        Node("linode-2", "crdb-linode-2", "root", "linode", "us-west",
             "cloud=linode,region=us-west"),
        Node("azure-1", "crdb-azure-1", "ubuntu", "azure", "centralindia",
             "cloud=azure,region=centralindia"),
        Node("azure-2", "crdb-azure-2", "ubuntu", "azure", "eastasia",
             "cloud=azure,region=eastasia"),
        Node("gcp-1", "crdb-gcp-1", "ubuntu", "gcp", "us-east1",
             "cloud=gcp,region=us-east1"),
    )
)

#: Phase II baseline: a separate ``cockroach start-single-node`` instance, not a
#: member of :data:`DEFAULT_TOPOLOGY`. It is deliberately outside the cluster
#: because Phase II establishes the cost of the workload *without* replication,
#: against which Phase III measures Raft overhead. Including it in the topology
#: would corrupt ``len(topology)`` where that is used as the voter count.
#:
#: CAVEAT for the Raft-overhead comparison: this host has 7 GB of RAM against the
#: cluster members' 3 GB, though both have 2 vCPUs. The Phase II/Phase III
#: difference therefore confounds replication with available page cache and is
#: not a clean single-variable comparison. Either normalise the instance sizes
#: before Stage 6 or state the asymmetry explicitly in the results chapter --
#: do not present the delta as replication cost alone.
BASELINE_NODE = Node(
    "local-1", "crdb-local-1", "ubuntu", "local", "self-hosted",
    "cloud=local,region=self-hosted",
)
