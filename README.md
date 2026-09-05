# crdblab

Measurement harness for a five-node, three-provider CockroachDB testbed.

This package replaces a collection of standalone scripts whose independently
maintained copies of the same parsing logic diverged, producing three
compounding defects in the original Phase II/III exports (see `docs/defects.md`).
The design goal is not convenience but defensibility: every figure must be
traceable to a known code revision, a declared profile, and a retained copy of
the raw generator output that produced it.

**Start here:** `instructions.md` is the step-by-step reproduction guide, from
`git clone` to `terraform destroy`; `docs/defects.md` records the twelve
instrumentation defects that shaped this design. This file covers the harness's
own commitments.

A wider briefing — the project's history, settled decisions, results and open
questions — is kept outside the repository in `project-hydra-context/context.md`,
alongside the session log it was distilled from.

## Order of operations

0. `python3 -m venv .venv && .venv/bin/python -m pip install -e ".[dev]"`
1. `terraform apply` in `terraform/`.
2. Load the working set. The bootstrap creates databases but no tables, and no
   generator creates them either:

   ```
   cockroach workload init ycsb --drop --seed=42 --insert-count=125000 \
     'postgresql://root@crdb-gcp-1:26257/ycsb?sslmode=disable'
   ```

   Repeat against `crdb-local-1` for the Phase II baseline, which needs its own
   working set. The seed and row count **must** match the profile you intend to
   sweep with (`profiles/thesis-extended.yaml` for the dissertation's runs). They
   are not incidental — see the seed commitment below.
3. `.venv/bin/crdblab capture --node gcp-1 --pty --duration 15`
   Pins the column layout emitted by the CockroachDB version actually
   installed. Nothing else should be run until the reported operation types and
   latency columns have been inspected.
4. `crdblab net probe` — Phase I substrate validation. Records the all-pairs RTT
   matrix and asserts clock offset and leaseholder placement. Its derived quorum
   floor is what makes the later write-latency check possible, so it must run
   before any benchmark.
5. `crdblab bench single|cluster --profile thesis-extended` — Phases II and III.
   `single` targets the unreplicated baseline node, which needs its own working
   set loaded exactly as in step 2. Pre-flight refuses to start unless a Phase I
   run exists to supply the quorum floor. Run both phases with the *same* profile:
   the analysis layer refuses to compare runs whose workload parameters differ,
   and concurrency is deliberately not one of those parameters.
6. `crdblab chaos run --mode dead|recover` — Phase IV. A `dead` fault leaves the
   target down and the harness does **not** restore it. CockroachDB is launched by
   cloud-init with `--background`, not as a systemd unit, so there is no service
   to start and a reboot will not bring it back; replay the start command, keeping
   `--cache=0.25 --max-sql-memory=0.25` (see `instructions.md`).
   Each chaos run also carries a **high-frequency RTO probe** on a background
   path: a pool of canary writers on their own connections and their own table,
   dispatching every 2 ms, recording when the database stopped and resumed
   serving writes into `rto_probe.csv` and a flushed-as-it-happens
   `rto_probe.log`. It exists because neither of the other two clients can time a
   recovery: the generator samples once a second, and the RPO audit writer is
   serialised at the cost of one quorum write. `crdblab probe rto --duration 60`
   runs it standalone, with no benchmark anywhere.
7. `crdblab validate runs/<run-id>` — gate before any analysis.
8. `crdblab analyze steady-state <run>` — Phase II or III throughput and latency
   by tier, with an interval estimate across repetitions.
   `crdblab analyze raft-overhead --baseline <p2-run> --cluster <p3-run>` —
   replication cost in three framings, each labelled with what it holds fixed:
   unqueued (both phases at one worker), matched throughput, and matched
   utilisation. It refuses to compare two runs configured differently, and
   declines the matched-throughput scalar where the phases' ranges do not overlap
   rather than extrapolating. It no longer needs
   `--accept-hardware-difference`: the gateway is `crdb-gcp-1`, the same machine
   type as the Phase II baseline, so the two phases differ in replication and
   nothing else the harness can see. The flag is retained for re-analysing runs
   measured before that move, whose gateway was a Linode node with a different
   CPU, and it downgrades the refusal to a *recorded warning* rather than
   suppressing it.
   `crdblab analyze resilience <chaos-run>` — all three RTO figures with their
   limits (throughput-based, audit-log-based, and the probe's), the RPO, and the
   clock alignment every timing depends on.
9. `crdblab report figures` — renders the dissertation figures into `figures/`,
   defaulting to the most recent run of each phase. Every figure resolves through
   the analysis loader, so an unvalidated or manifest-less run cannot reach one,
   and each figure stamps its source run ids into its own footer. PNGs are
   exported at >= 4K width with a vector PDF alongside each.

All steps are implemented.

## Design commitments

- **No positional parsing.** Column names are bound from the generator's own
  header line. A data line arriving before a header is an error, not an
  invitation to guess.
- **Operation type is a dimension, not noise.** Throughput is summed across
  operation types; latency distributions are never pooled.
- **Raw output is retained.** Every run keeps the generator's verbatim stdout
  next to the derived CSV.
- **Runs are immutable and self-describing.** A run directory carries a
  manifest with the git revision, profile, and node inventory.
- **Validation and pre-flight both gate analysis, and they ask different
  questions.** `validate` asks whether the recorded numbers are consistent with
  each other; pre-flight asks whether the system was fit to be measured, and asks
  it *before* the measurement. `analysis/loader.py::load_run()` is the only way
  into a run and refuses one that fails either — the run whose pre-flight failed
  is exactly the run whose numbers look fine.
- **The manifest records the machine, not just the process.** Capturing the
  server's start command made a block-cache asymmetry visible (D9); it could not
  answer a later question about a host that no longer existed, so pre-flight now
  also records CPU count, CPU model and total memory (D11). Memory is compared
  even though the cache flags already are: those flags are *fractions* of it, so
  identical flags on unlike machines give unlike caches.
- **An operation that matches no rows is not a measurement.** The generator
  derives its keys from a seed that changes on every invocation by default, so a
  table populated by one process is addressed by a different keyspace than the
  next process queries; every lookup then matches nothing. Load and run must
  share `--seed` and `--insert-count`. This failure is silent and *flattering* —
  it reports roughly twenty times the throughput at a twenty-fifth of the
  latency — so it is asserted in pre-flight rather than trusted.
- **A run carries both of its clocks.** The generator's `elapsed` accounting and
  the harness's monotonic clock have different origins, differing by the SSH and
  process startup cost — measured between 3.9 s and 5.4 s across runs, which is
  why it is recorded rather than assumed. Events the harness schedules, the chaos
  injection above all, are timed on the second; throughput is reported on the
  first. Both are recorded per interval (`wall_offset_s`, schema 2.1) so the
  offset between them is an observation rather than an assumption. Runs recorded
  before this get a *bounded* alignment and every timing derived across the two
  clocks is reported as an interval — the analysis layer does not substitute an
  estimate for a measurement that was not made. In a figure the fault is drawn as
  a **line** where the offset was measured and as a **band** where it was only
  bounded.
- **A comparison is checked, not just its operands.** Two runs can each be
  internally valid while the inference from their difference is wrong: the
  baseline and the cluster once ran with block caches differing fifteen-fold,
  which inflated apparent replication cost by 43% (D9). `analyze raft-overhead`
  asserts the two runs' server flags, workload parameters, version and hardware
  match before it will compare them.
- **Concurrency is not load.** A closed workload's worker count fixes how many
  operations are outstanding, not how much work gets done, so two systems of
  different capacity at the same concurrency sit at different points on their
  own throughput-latency curves. Replication cost is reported as a curve, or at
  matched throughput; the same-concurrency delta is computed only under a label
  saying it is not a result.
- **Internal consistency is necessary, not sufficient.** Four of the defects on
  record (D7, D8, D9, D11) produce data on which every check in `crdblab validate`
  passes, because the arithmetic relating the recorded quantities is sound and it
  is the system, the operation, or the comparison behind them that is wrong. Where
  a physical invariant exists — a committed write cannot outrun the round trip to
  the follower that completes quorum — it is asserted directly.
- **The measurement is not adjusted to suit its own instrumentation.** The
  row-match check differences an in-memory statistics view that CockroachDB
  flushes every ten minutes, and a flush landing after a tier ends leaves nothing
  to assert on. Suppressing the flush would remove the race — and also remove
  background disk I/O on a two-core host under a saturated workload, raising the
  throughput being measured and making runs before and after the change
  incomparable. The race is handled instead: a flushed window is accepted only
  where the quorum-floor check independently covers the same tier, which is a
  corroboration Phase II cannot have (D12).
