# Project Hydra: Multi-Cloud CockroachDB Resiliency & Chaos Testbed

**Project Hydra** is an applied empirical research testbed designed to evaluate the real-world trade-offs of running a synchronously replicated, multi-cloud NewSQL database across distinct cloud providers.

Named after the mythical multi-headed beast, this project proves that even if an entire cloud provider's region is completely severed or terminated (cutting off two heads of the cluster), the data infrastructure survives seamlessly on the remaining cloud infrastructure without a single byte of data loss ($RPO = 0$).

## 📌 Overview & Architecture

Traditional disaster recovery relies on asynchronous replication, introducing non-zero Recovery Point Objectives ($RPO > 0$) and sluggish, manual failovers ($RTO$). **Project Hydra** solves this by deploying a 3-node CockroachDB cluster spanned across two cloud giants:

* **Primary Region (AWS):** Hosts 2 nodes in `us-east-1`.


* **Failover Region (GCP):** Hosts 1 node in `us-central1`.



The clouds are bridged securely over a WAN tunnel to maintain active Raft consensus replication across the provider boundary.

```
       [ Client Traffic Generator (200+ TPS) ]
                         │
         ┌───────────────┴───────────────┐
         ▼                               ▼
  ┌──────────────┐                ┌──────────────┐
  │  AWS Region  │                │  GCP Region  │
  │ (us-east-1)  │                │(us-central1) │
  │ ──────────── │   WAN Tunnel   │ ──────────── │
  │ 🖥️ Node 1    │◄──────────────►│ 🖥️ Node 3    │
  │ 🖥️ Node 2    │ (Raft Quorum)  │              │
  └──────────────┘                └──────────────┘
         │                               ▲
         └─── [ 💥 CHAOS AGENT FIRES ] ──┘
              (Simulated AWS Blackout)

```

## 🛠️ Repository Structure & Deliverables

This repository functions as a fully automated, single-afternoon experiment ecosystem:

* **`/terraform` (D1):** Idempotent Infrastructure as Code (IaC) configuration to provision cross-cloud VMs, subnets, firewall routing, and encrypted WAN tunnels across AWS and GCP in under 60 minutes.


* **`/automation` (D2):** The Python Chaos and Orchestration Suite:


* `hydra_traffic.py`: Sustains a high-throughput workload of $\ge 200$ write transactions per second (TPS) with millisecond-resolution logging.


* `hydra_chaos.py`: Simulates a catastrophic regional cloud blackout by abruptly terminating all AWS instances within a tight 5-second window mid-load.


* `hydra_analyzer.py`: Parses raw CSV transaction logs to compute explicit RTO metrics, calculate throughput recovery curves, and mathematically verify $RPO = 0$ via transaction sequence continuity.




* **`/dataset` (D3):** Contains empirical CSV logs and generated visual charts evaluating database performance across local baselines, single-cloud environments, and multi-cloud environments.



## 🔬 Research Focus

This testbed directly investigates the core trade-offs dictated by the **CAP / PACELC theorems**:

1. **The WAN Latency Penalty:** Quantifying the exact write-throughput (TPS) and latency costs of enforcing synchronous Raft consensus across cross-provider networks.


2. **Automated Resiliency:** Measuring the precise duration ($RTO < 30$ sec target) it takes for the lone surviving GCP node to achieve quorum, execute a leadership transfer, and successfully accept writes following an AWS failure.



---

This repository constitutes the practical experimental framework for an MSc dissertation in Network Management and Cloud Computing at Middlesex University.