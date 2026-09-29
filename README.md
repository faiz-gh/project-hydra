# crdblab

Measurement harness for Project Hydra: CockroachDB compared with PostgreSQL
under Patroni on the same five-node, three-cloud testbed (GCP, Linode, Azure).
It measures steady-state throughput and latency, then injects faults and
measures recovery time (RTO) and lost writes (RPO).

**Documentation:** open [`docs-app/index.html`](docs-app/index.html) in a
browser. It covers the testbed, the workflow, every measurement phase, the CLI,
profiles, run data, the insights charts and a full code reference.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m pytest -q

cp .env.example .env        # set TS_API_KEY
./run-experiment.sh         # full two-engine pipeline (terminal UI)
./run-experiment.sh --help  # measure only the engine that is deployed
```

Other entry points:

```bash
.venv/bin/crdblab --help                          # every phase and analysis command
./generate_insights.sh --profile thesis-extended  # chart catalogue from runs/
./demo.sh                                         # replay a recorded experiment
```

## Updating the docs

The handbook's reference pages are generated from the code:

```bash
.venv/bin/python docs-app/build.py
```
