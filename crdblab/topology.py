"""Single source of truth for the testbed topology: nodes, logins and regions."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Node:
    """One testbed machine.

    ``locality`` mirrors ``cockroach start --locality`` and is recorded in each
    run manifest.
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
    #: Default port of the Ubuntu-packaged prometheus-node-exporter.
    node_exporter_port: int = 9100

    @property
    def http_base(self) -> str:
        return f"http://{self.host}:{self.http_port}"

    @property
    def node_exporter_url(self) -> str:
        return f"http://{self.host}:{self.node_exporter_port}/metrics"


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
    def from_mapping(cls, raw: Mapping) -> Topology:
        return cls(nodes=tuple(Node(name=name, **spec) for name, spec in raw.items()))


#: The five-node, three-provider cluster. gcp-1 is the gateway: the CockroachDB
#: leaseholder and Patroni primary are pinned there. Localities must match terraform/scripts/.
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

#: Dedicated workload-generator node; not a cluster member.
CLIENT_NODE = Node(
    "client-1", "crdb-client-1", "ubuntu", "gcp", "us-east1",
    "cloud=gcp,region=us-east1",
)
