# Project history

The dated narrative log of this project's incidents, fixes and status
updates, kept in full for the same defensibility reason every other
artefact here is retained: a claim about what was true on a given date
should be checkable against what was actually observed, not just
asserted. This file used to be the top of `CLAUDE.md` (the section
titled "What this is") until it grew to ~900 lines and started
crowding out the guidance a coding assistant actually needs on every
session; it was split out here on 2026-09-11, unedited except for this
note, so nothing in the history below was rewritten to make room.

**For the current, stable picture of the infrastructure and workflow —
not a chronological log — see `docs/testbed.md`.** This file is an
archive: read it for *why* a design decision was made or *when* a
defect was found and fixed, not for what is true right now.

---

`crdblab` is a measurement harness for a dissertation experiment: a five-node,
three-provider (GCP/Azure/Linode) database testbed used to compare
CockroachDB against PostgreSQL under Patroni for HA, on identical topology.
It replaces a collection of standalone scripts whose independently maintained
parsing logic diverged and produced silent, "flattering" measurement defects
(referred to by ID, e.g. D6, D8, D9, throughout the code and docs — the
catalogue they were originally recorded in, `docs/defects.md`, has since been
removed from the repo; treat a `DN` citation as a stable label for a known
failure mode, not a working link).
The design goal throughout is **defensibility**: every figure must trace back
to a known git revision, a declared profile (`profiles/*.yaml`), and the
generator's retained raw output.

**Status as of 2026-09-09 (this note goes stale the moment a run completes —
update or delete it then):** **the PostgreSQL arm has completed a full
end-to-end run for the first time in this project** —
`runs/_logs/experiment-20260909T040237Z.log`, smoke profile, all four phases
plus validate, analyse and figures, 13 m 51 s. Every previously unexercised
PostgreSQL path ran: the strengthened switchover-candidate check (it waited out
a `/replica answered 503`, then passed on `streaming on timeline 2`), the
`patronictl switchover` that follows it, **Phase IV following Phase III**, and
`restore_target`'s `dead`-mode PostgreSQL branch (`rejoined 5/5 live after
57s`). Both chaos phases produced real numbers with **untruncated coverage on
both instruments**: recover RTO 34.1 s observed outage (probe) / 33.9 s (audit
writer), dead 28.3 s / 28.5 s, RPO 0 in both.

**Update — the thesis-scale PostgreSQL run happened next, and it is the new
status.** `./run-experiment.sh --engine postgresql` against `profiles/thesis`
(`runs/_logs/experiment-20260909T043036Z.log`): the load, Phase I, Phase II and
Phase III all completed with real numbers for the first time at disk-bound
scale; Phase IV failed on a **ninth** distinct failure mode, one step past the
eighth. All three predictions the previous version of this note made were
confirmed. (1) Performance RTO came back `not regained within the run (needs
>=3810 tps)` on the recover phase, as expected from the primary-relocation
gotcha. (2) The Phase IV candidate wait did have to sit and re-clone — see
below, though it was a different check that ultimately failed it. (3) The load
dominated the wall clock: **4467 s (74.5 min)** of the run's total, loading
3,750,063 rows at 814-873 ops/s instantaneous, settling around 815-865 ops/s
cumulative — noticeably slower than the 125k-row smoke load's rate as the
index outgrew cache, exactly as expected.

**Phase I** recorded a 70.311 ms quorum floor (`linode-2`, unchanged ranking
from prior deployments) and passed every clock-offset check. **Phase II**
swept all 12 tiers clean — the first disk-bound PostgreSQL benchmark this
project has ever produced: C=200 read p50 10.3-12.4 ms / update p50 152.5-
159.4 ms, all 27 pre-flight checks passed. **Phase III** (`recover`,
`runs/20260909T060600Z_p4-chaos-recover`) is the first disk-bound resilience
number on either engine: baseline 4762.3 tps, recovery floor 3809.8 tps (80%
held for 10s), **RTO availability 35.62 s**, write outage 31.68 s between
acknowledged writes, RTO probe outage **31506 ms** (21.7 ms resolution,
detection lag 14170 ms), **RPO 0 acknowledged writes lost of 360** (3
ambiguous, 0 of which committed). Both instruments agree closely and neither
flagged truncated coverage.

**Phase IV failed at the placement-repair switchover, and this is a genuinely
new defect, not a repeat of the eighth.** After Phase III's fault, Patroni
promoted `linode-1`; the repair waited out `gcp-1` answering `/replica` 503,
then declared it ready on `streaming on timeline 6, lag within bounds` — the
exact condition the 2026-09-09 03:13 fix (see cause 7 above) checks for.
`patronictl switchover --leader crdb-linode-1 --candidate crdb-gcp-1 --force`
was issued anyway and still answered `503, Switchover failed`. The cluster
table the failure printed shows why: `crdb-gcp-1` was `Role: Replica` — not
`Quorum Standby`, unlike the other three survivors — despite being on the
correct timeline with **zero lag on both Receive and Replay LSN**. Patroni
tracks `synchronous_standby_names` membership separately from streaming state,
on its own `loop_wait` cadence, so a node can hold the leader's timeline with
no lag for one poll before the leader admits it to the synchronous set — and
`synchronous_mode: quorum` refuses a switchover to a candidate that is not yet
in that set, `--force` notwithstanding. Confirmed against Patroni's own REST
API docs: `GET :8008/quorum` "returns HTTP status code 200 only when this node
is listed as a quorum node in `synchronous_standby_names` on the primary" —
the same fact `patronictl list`'s Role column renders, and the one thing
neither `/replica` nor the member's own `/patroni` document exposes.
**Fixed, and verified live the same day.** `patroni_candidate_ready` now adds
`GET :8008/quorum` as a third gate after streaming+timeline, so the
settle-window wait spans the full convergence rather than ending one poll
early. Two regression tests added
(`test_a_streaming_correctly_timelined_replica_can_still_not_be_a_quorum_member`,
`test_the_wait_holds_for_quorum_membership_after_streaming_is_already_true`),
245 tests passing at the time. **Confirmed against a real cluster on
`experiment-20260909T151320Z.log`** (smoke, 2026-09-09 15:13, 13 m 15 s,
against a redeployed testbed — see below): the log line reads `gcp-1 is a
candidate now: streaming on timeline 2, lag within bounds, quorum member`,
the switchover took, and **Phase IV completed end to end** — fault injected,
RTO measured (availability 30.08 s, probe outage 26.07 s, RPO 0/87), target
restored (`rejoined 5/5 live after 30s`), primary switched back to the
gateway, all four runs validated, all figures rendered. This is the first
smoke run in the project's history to complete all four phases without any
manual intervention.

**The deployment that failed was rebuilt before this smoke run** — the SSH
host key for `crdb-gcp-1` changed between sessions, consistent with a fresh
`terraform apply` rather than a fault (this is expected and already disclosed:
`run-experiment.sh`'s `SSH_OPTS` comment states `StrictHostKeyChecking=no` is
deliberate because "the testbed is destroyed and rebuilt repeatedly and
addresses get reused"). Confirmed live 2026-09-09 ~15:40: `patronictl list`
shows Leader on `gcp-1`,
all four others `Quorum Standby / streaming`, timeline 5, zero lag everywhere;
`shared_buffers` 978 MB, `effective_cache_size` 2934 MB; `synchronous_mode:
quorum` / `synchronous_node_count: 2` / `synchronous_mode_strict: true`
producing `ANY 2 (...)` naming all four standbys; `wal_keep_size: 4GB` and
`remove_data_directory_on_diverged_timelines: true` both present; 45 GB free
of 49 GB on `gcp-1`'s root volume. **The one thing this redeploy has NOT yet
had is the thesis-scale load** — the smoke run above reloaded only 125,063
rows (smoke's own `insert_count`), confirmed live by `SELECT count(*) FROM
usertable` returning `125063`. The thesis run will reload the full 3.75M rows
itself (~70-110 min) the same way every prior thesis run has; this is not a
blocker, just not yet done.

**Superseded below (2026-09-10): the PostgreSQL `dead` phase at disk-bound
scale did run, the same day this note was written, and completed.** See the
2026-09-10 update after the "runs/ was decluttered" paragraph for the numbers
and why it took until the *next* session to get written down here at all.
Only CockroachDB's `dead` phase at thesis scale remains unexercised.

**Still unexercised (superseded above): Phase IV (`dead`) at disk-bound scale, on either engine.**
Phase I-III have run for real on the PostgreSQL arm at the current profile;
the `dead` fault has not, on either arm, because CockroachDB's thesis-scale
sweep predates the profile's move to 3.75M rows (see below) and PostgreSQL's
first attempt failed before reaching it. The fix above is what stood between
the project and this, and it is now cleared.

**Every recorded run predating this one was historical or incomparable, and
as of 2026-09-10 none of them exist as run directories any more.** They were
taken at `insert_count: 125000` (~205 MB, fully memory-resident);
`profiles/thesis.yaml` and `thesis-extended.yaml` are now **3,750,000**
(~6.15 GB, ~1.5x node RAM), so nothing recorded before that change was
comparable against anything produced at the current profile in the first
place. That included the CockroachDB thesis-scale sweep of 2026-09-08
(`20260908T053558Z_bench_cluster`, ~33 min wall clock, bench 18.5 min against
an 885 s floor) which had been the reference the PostgreSQL arm was held
against. On 2026-09-10, all 58 run directories recorded between 2026-09-05
and 2026-09-09 (this one included) were consolidated into per-engine/phase
CSVs and deleted to declutter `runs/` — see `docs/data-schema.md` for the
full schema and `runs/legacy-runs/*.csv` (plus `runs-index-data.csv` and
`raw-output-archive.tar.gz` for the generator/ping stdout) for the data
itself. A run-id cited anywhere in this file from here on is a historical
label, the same way a `DN` defect citation is — not a path that still
resolves to a directory on disk. Both arms still have to be re-taken at the
current profile, including CockroachDB's `dead` phase, before `analyze
engine-comparison` means anything — that was true before the consolidation
and is unchanged by it.

**It took seven attempts to complete one PostgreSQL run.** The seventh
(2026-09-09 04:02, smoke) is the one that finished; the six before it each
failed later than the last, for eight causes — the fifth attempt produced two,
one that stopped it and one that would have corrupted its result had it
continued, and the sixth did the same. All eight are fixed in code, and the
history is kept because every one of them is a failure mode that produced
*plausible* output rather than an obvious crash:
(1) 2026-09-08 — the deployment never came up: `bootstrap-patroni.tftpl` wrote
its config to `/etc/patroni/patroni.yml` while Ubuntu's packaged
`patroni.service` reads only `/etc/patroni/config.yml`, so every node
provisioned "successfully" with no database process anywhere.
(2) 2026-09-08 19:18 — the cluster came up healthy and the **data load** died
after 6 m 10 s in "creating load generator" with `server conn crashed?
(SQLSTATE 08P01)`. Not a database fault: HAProxy's `timeout server 300s` is an
*idle* timeout and was killing connections opened early in a slow serial
warm-up before the generator ever used them.
(3) The primary was never pinned, so it landed on `crdb-azure-2` (eastasia,
199 ms from the client) — the reason that warm-up was slow enough to trip the
timeout, and a confound in its own right (see the pinning gotcha).
(4) 2026-09-08 22:57 — the sweep aborted at the health gate with "1 healthy
member(s), expected 5" while four replicas answered 503. Nothing was wrong:
503 is what a Patroni member returns while it is still taking its basebackup.
The gate read once; it now waits (see the gotcha).
(5) 2026-09-09 01:16 — Phases 0-III all passed and **Phase IV aborted at the
placement check**: after Phase III's partition the demoted primary `gcp-1`
diverged, `pg_rewind` could not run because `gcp-1` had already recycled the
WAL it needed, and Patroni refused the switchover with "no good candidates have
been found". Structural rather than unlucky — it happens after every Phase III
run against the primary. See the WAL divergence gotcha.
(6) The same run (5) also produced a **Phase III result that was wrong in the
flattering direction**, which nothing would have caught:
both RTO instruments blocked on connections the partition had black-holed,
stopped observing 3.6 s after the fault, and the harness reported their silence
as a 0.082 s RTO for an outage the generator recorded as two consecutive ticks
of zero throughput. See the instrument-coverage gotcha. **This run's artefacts
were retained deliberately as the regression case both fixes are tested
against, and still are** — its `metrics.csv`/`audit.csv`/`rto_probe.csv` rows
survive the 2026-09-10 consolidation inside `runs/legacy-runs/*-data.csv`,
filterable by `run_id`, even though the run's own directory is gone (see the
note above).
(7) 2026-09-09 03:13 — Phases 0-III passed again and **Phase IV aborted at the
placement check again, on a different condition**: the demoted primary did come
back up this time (so (5)'s fix worked as far as it went), but as a `Replica` on
**timeline 1**, unattached, while the rest of the cluster streamed on timeline
2. `/replica` answered 200 for it — a member attached to nothing reports no lag
— so the candidate wait ended after one reading and `patronictl switchover`
answered `503, Switchover failed`. See the candidate-eligibility gotcha.
(8) The same run (7) also made the Phase III measurement **impossible rather
than merely wrong**: the smoke run was 45 s long with the fault at 15 s, and
both instruments were still inside the outage when observation ended. They
reported the RTO as UNMEASURED, correctly. The next run measured that same
outage at 37.6 s. See the recover-run-length gotcha.

**What has never run against a real cluster, as of the smoke run above: only
the disk-bound `dead` fault on PostgreSQL, and CockroachDB's `dead` fault at
the current 3.75M-row profile.** Everything else — including the `/quorum`
gate — has now executed live at least once, smoke-scale on this redeployment
and thesis-scale on the one before it.

Order of work from here: **the deployment is up and confirmed healthy**
(`patronictl list`, `SHOW shared_buffers`/`synchronous_standby_names`,
`wal_keep_size`, disk space all re-checked live 2026-09-09 ~15:40, see above)
— no redeploy needed. (1) **`./run-experiment.sh --engine postgresql`** —
budget **~2.5-3 h**, mostly the 3.75M-row load, since this deployment only has
the smoke-scale 125,063 rows loaded so far. Watch that Phase IV completes:
`gcp-1` should reach `Role: Quorum Standby / streaming` (the log should print
`... quorum member` on the candidate check) before the switchover is
attempted — the smoke run confirms the code path, not the thesis-scale timing
of it, since a re-clone at 6 GB behaves differently from one at ~200 MB. (2)
Re-take the CockroachDB arm at the current profile, including its `dead`
phase, which is no longer optional. (3) `crdblab analyze engine-comparison
--crdb <run> --pg <run>` — which **currently refuses every cross-engine pair**
and needs the flag-gate decision first (see the gotcha).

**Timing at the current profile is dominated by the load, not the sweep.** The
bench sweep is a fixed 12 tiers x 60 s plus cooldowns and is unchanged. The
PostgreSQL load is not: it runs through the generator insert-only at
`LOAD_CONCURRENCY=64` against a ~70 ms synchronous-replication commit, so
~900 rows/s and falling as the index outgrows cache — **70-110 min** for
3.75M rows, against ~2 min at 125k. The load is latency-bound rather than
CPU-bound, so raising `LOAD_CONCURRENCY` scales it nearly linearly (192 would
put it near ~23 min, and `max_connections` is 500), but that path has broken
three times historically, so treat the change as deliberate rather than
routine.

Check `tailscale status` before assuming the cluster is reachable — this
development machine has intermittently been unable to resolve `crdb-gcp-1`.

**Update — 2026-09-10, code-only changes, none of it exercised against a live
cluster yet.** Three things landed, prompted by the recover/dead throughput
settling far below baseline (~9%/~4%) on the 2026-09-09 thesis-scale
PostgreSQL run and not being classifiable as recovered, degraded, or still
trending within the recorded window.

(1) **Chaos runs observe longer after the fault, so
`resilience.post_fault_steady_state`'s CV<0.25 settling test can actually
resolve which of those three the tail is**, rather than running out of
recorded series first. `profiles/thesis.yaml`'s `chaos.min_post_fault_s` is
now 450 (was 60; `recover` runs ~9.25 min, `dead` ~8.5 min);
`profiles/thesis-extended.yaml`'s is 900 (`recover` ~16.75 min, `dead`
~16 min) — deliberately different, thesis-extended's chosen to clear the
~800-850-post-settle-tick point where the CV crossed 0.25 when the 2026-09-09
run's own recorded series was replayed with a longer synthetic tail,
thesis.yaml's set to half that by request since it is the canonical
cross-engine comparability profile and its own run budget matters more.
Neither value has been verified live yet — the smallest real check is a
standalone `crdblab --engine postgresql chaos run --profile thesis-extended
--mode recover` (and `--mode dead`) against the already-loaded thesis-scale
table, ~35-40 min for both, no need to repeat the load or Phase II first. See
the profiles' own `chaos:` comments for the full derivation.

(2) **Per-node CPU/memory/disk/network utilisation is now collected during
Phase II-IV**, polled directly from each node's node_exporter
(`crdblab/core/hardware_metrics.py`, new), across all 5 cluster nodes plus
the client node, written to a new `hardware_metrics.csv` per run (schema in
`docs/data-schema.md`). `terraform/scripts/bootstrap-{cockroachdb,patroni,
client}.tftpl` now install/verify `prometheus-node-exporter` on every node —
**this needs a redeploy to take effect** (cloud-init only runs at first
boot), so it will not appear on the currently-running deployment until the
next `terraform apply`. No firewall changes were needed anywhere: like
Patroni's `:8008` REST API, node_exporter's `:9100` rides the Tailscale mesh
that already carries every other cross-node service.

(3) **`runs/` was decluttered.** All 58 run directories recorded before this
date were consolidated into per-engine/phase CSVs and deleted — see the note
under "Every recorded run predating this one..." above and
`docs/data-schema.md` for what moved where. `figures/` was cleared too
(everything under it deleted by hand); the next `report figures` invocation
regenerates from whatever runs exist at that time. Nothing about how a *new*
run is recorded changed — `crdblab bench`/`chaos run`/`net probe` still write
a full `runs/<run_id>/` directory exactly as described in
`docs/data-schema.md`'s "Where the data lives" section, and that document is
the reference for artifact layout going forward, superseding any ad-hoc
description in this file.

**Update — 2026-09-10, the PostgreSQL `dead` phase at thesis scale turned out
to have already happened, and three CockroachDB-only defects were found and
fixed while getting the CockroachDB arm running again for the first time
since the engine-flag refactor.**

The thesis-scale PostgreSQL run this file's last status note was waiting on
(`./run-experiment.sh --engine postgresql`, profile thesis) had in fact
already completed the day before, end to end, all four phases —
`runs/_logs/experiment-20260909T153820Z.log`, 117 m 37 s. Nobody wrote it
down here before the 2026-09-10 `runs/` consolidation deleted its run
directories, so it read as "still unexercised" above until this session
checked the logs against that claim and found the mismatch. The data
survived correctly (`runs/legacy-runs/`, run ids `20260909T165504Z_p1-network`
through `20260909T172425Z_p4-chaos-dead`), just undocumented. Headline
numbers: Phase III (`recover`) RPO 0/331 acknowledged writes lost; Phase IV
(`dead`) RPO 0/430, but its performance RTO did **not** settle within the run
— throughput at 194 ops/s (4% of baseline, CV within tolerance) and update
latency CV 0.60, both past the point `resilience.post_fault_steady_state`
could call recovered, degraded, or still trending. That is a real result to
carry into the write-up, not a defect: report it as "did not settle," not as
a missing number.

Getting the CockroachDB arm running again (idle since before the PostgreSQL
work started) surfaced three defects, all in code that had never been
exercised against a truly fresh redeploy since `run-experiment.sh` gained
`--engine` and `crdblab/core/preflight.py` gained its CockroachDB-specific
checks. All three are CockroachDB-only; a research pass confirmed every
affected call site sits behind `engine == "cockroachdb"`, and both a
CockroachDB and a PostgreSQL smoke run were taken in the same session to
prove neither engine regressed the other (below).

(1) **`run-experiment.sh`'s CockroachDB node-status check was a single
reading, not a bounded wait**, unlike the Patroni health gate right below it
in the same file (fixed 2026-09-08 for exactly this reason — see that
gotcha). A fresh `terraform apply` boots five nodes across three clouds on
their own schedules, so a cluster mid-bootstrap legitimately reports fewer
than 5 nodes for a while; read once, that looks identical to a fault.
`experiment-20260909T202501Z.log` died with "cluster reports 0 node(s),
expected 5" against a testbed that was, a few minutes later, entirely
healthy. Fixed the same way as the Patroni gate: polls every 10 s for up to
600 s, breaks immediately once already healthy.

(2) **The wait in (1) was necessary but not sufficient — the real defect was
a self-referential DNS resolution bug, and it was still there after (1)
shipped.** `experiment-20260909T204104Z.log` waited the full 600 s and never
saw more than 0/5, on a cluster that was live and fully healthy the entire
time (confirmed by hand: `cockroach node status` against the gateway's own
Tailscale IP returned all 5 nodes `is_live=true` at the exact moment the
script's own check was failing). The command that fails is `cockroach ...
--host=$GW_HOST:26257`, issued *by SSHing onto the gateway and asking it to
resolve its own hostname*. `cockroach` binds only its Tailscale IPv4 address
(`bootstrap-cockroachdb.tftpl`'s `--listen-addr=$TS_IP:26257`), but on the
gateway's own OS, `/etc/resolv.conf`'s search-domain order puts GCP's
project-internal DNS zone (`*.c.<project>.internal`) ahead of the tailnet's
own (`*.ts.net`) — confirmed live: `getent hosts crdb-gcp-1` run *on*
`crdb-gcp-1` answers `10.5.0.2` (the node's internal RFC1918 address, which
nothing listens on), while `tailscale ip -4` on the same node correctly
answers `100.79.193.22`. Nothing was wrong with the cluster; the check was
asking the node to dial an address it never bound. The same bug then
reappeared one layer down: `experiment-20260909T205041Z.log` got past both
of `run-experiment.sh`'s own checks (fixed by this point) and failed inside
`crdblab` itself, at `preflight.check_leaseholder_placement` — the harness
has its own copy of the identical self-referential pattern.

Fixed in five places, all by resolving the gateway's own `tailscale ip -4`
inline rather than trusting bare-hostname resolution (the same idiom
`p4_chaos.py`'s dead-mode restore payload already used):
`run-experiment.sh`'s node-status and lease-preference checks (`GW_TS_IP`,
resolved once); `crdblab/core/preflight.py::_read_leaseholder_placement`
(D7's detector) and `RowMatchProbe._sample` (D8's detector, CockroachDB
side); `crdblab/cli.py::_cmd_probe`'s canary-table bootstrap. One existing
test (`tests/test_rto_probe.py::test_the_standalone_probe_command_produces_a_normal_run_directory`)
hardcoded the literal hostname in its assertion and was updated to assert
the `tailscale ip -4` idiom instead; full suite (257 tests) and `ruff check`
both clean afterward. **Deliberately left unfixed**, because neither is
reproduced as broken under the current profile (`chaos.target` is always
`gcp-1`, never the node these touch): `p4_chaos.py`'s dead-mode rejoin poll
(SSHes onto a *survivor*, not `gcp-1` — untested whether Azure/Linode have
the same internal-DNS precedence GCP does) and its RPO-audit-table admin
connection (already resolves a *different* node than the one it SSHes onto,
which is why it was never broken in the first place). Revisit if either
provider is ever seen to reproduce the same class of failure.

(3) Both fixes were verified against the same live redeployment, not just by
inspection: a CockroachDB smoke run (`experiment-20260909T210023Z.log`,
11 m 16 s) and, on the same testbed after a redeploy back to PostgreSQL, a
PostgreSQL smoke run (`experiment-20260909T212843Z.log`, 17 m 5 s) both
completed all four phases end to end with no manual intervention. CockroachDB
run: 7/7 pre-flight checks passed, row-match rate 1.0000 on both tiers, both
chaos faults `landed: true`, RPO 0/279 and 0/158, `coverage_truncated: false`
throughout, `crdblab validate` PASS on all three measured runs. PostgreSQL
run: same shape (7/7 checks, RPO 0/209 and 0/96, both faults landed, both
switchovers — including the `/quorum` candidate gate from 2026-09-09 —
printed `quorum member` and took cleanly), and its RTO figures closely
reproduce the historical 2026-09-09 PostgreSQL smoke numbers cited earlier in
this file (34.1 s/33.9 s recover, 28.3 s/28.5 s dead, vs. 34.44 s/34.30 s and
28.82 s/28.87 s here) — strong evidence the fixes changed nothing about
PostgreSQL's own path, since it doesn't touch any of the code that changed.

**Also surfaced, not fixed: `bench.py::HostSampler`'s
`gateway_cpu_pct`/`gateway_disk_iops`/`gateway_rss_bytes` columns in
`metrics.csv` have silently failed on every scrape since this code was
written**, because `Target.metrics_url` (the base property `HostSampler` is
constructed from) unconditionally returns `""`, so every scrape attempt
during every past bench run raised inside `urllib.request.urlopen("")` and
was swallowed by `HostSampler`'s own per-tick failure counter — the smoke
run above reported "host metric scrape failed 65 time(s)" for what is a
~65-70 s phase at a 1 s poll interval, i.e. effectively every tick. This is a
different, older sampler than the 2026-09-10 node_exporter-based
`hardware_metrics.csv` above, which does work (confirmed: 0 unexpected
failures, only the documented one-blank-row-per-node on each node's first
poll). Nothing downstream reads `gateway_cpu_pct` et al. — not `validate`,
not `analysis/` — so this has never corrupted a result, only silently
produced three empty `metrics.csv` columns on every run in the project's
history. Same "flagged, not chased" status as the `generator_totals` gotcha
below: worth fixing (either wire `Target.metrics_url` to CockroachDB's
`:8080/_status/vars`, or delete the dead columns now that
`hardware_metrics.csv` supersedes them), not yet done.

**Update — 2026-09-10: both engines now have complete thesis-scale data,
including `dead`, for the first time in the project.** Two full
`./run-experiment.sh` invocations ran back to back against redeployed
testbeds, closing the gap the previous note called the only one left.

`./run-experiment.sh --engine cockroachdb` at `thesis`
(`runs/_logs/experiment-20260909T222326Z.log`, 64 m 50 s — the bulk `IMPORT`
loads 3.75M rows in 19 m 31 s, nothing like PostgreSQL's serial-insert load)
completed all four phases, `dead` included, for the first time at this
profile on either engine's original attempt: quorum floor 72.404 ms
(`linode-1`, unchanged ranking); Phase II clean across all four concurrency
tiers; Phase III (`recover`) availability RTO 11.92 s, probe outage 5290 ms,
RPO 0/1578; Phase IV (`dead`) availability RTO 9.95 s, probe outage 4881 ms,
RPO 0/1189, performance RTO undefined — throughput settled at 776 ops/s, 74%
of baseline, just under the 80% recovery floor, correctly reported as a new
stable state rather than a slow recovery. Run ids
`20260909T224417Z_p1-network` through `20260909T231745Z_p4-chaos-dead`.

The testbed was then redeployed to PostgreSQL and
`./run-experiment.sh --engine postgresql` at `thesis`
(`runs/_logs/experiment-20260909T235804Z.log`, 127 m 22 s) also completed all
four phases end to end on the fresh deployment — the second complete
PostgreSQL thesis run in the project's history, reproducing the shape of the
first (2026-09-09 15:38) rather than superseding it. Phase III RPO 0/1126 (3
ambiguous, 1 present in the table); Phase IV RPO 0/1421 (11 ambiguous, 0
present); both phases' performance RTO came back undefined — recover
settled at 819 ops/s (20% of baseline, latency CV 0.30, not settled), dead at
325 ops/s (8% of baseline) — consistent with the primary-relocation
read-path penalty documented above, not a new defect. Run ids
`20260910T011709Z_p1-network` through `20260910T014707Z_p4-chaos-dead`.

**`analyze engine-comparison` was attempted between the two `dead` runs above
and, at the time, still refused, unchanged.** `_MATCHED_SERVER_FLAGS` still
errored with "different --cache (0.25 vs unset, i.e. the 128 MiB default),
--max-sql-memory (0.25 vs unset, i.e. the 128 MiB default)" — the same
message documented in that gotcha below, confirmed still current against
live data rather than only against the flag-comparison code. Full suite
still 257 passing, no regressions since the code-only update above.

**Update — the flag-gate decision above was made and implemented the same
session, and `analyze engine-comparison` now succeeds against these same two
runs. Committed as `6c1fdb4`.** The gate is now engine-aware, mirroring the pattern already used for
the version check just below it in the same function: a same-engine
`--cache`/`--max-sql-memory` mismatch is still a hard error, unchanged.
Cross-engine, the two flags are no longer compared literally (meaningless —
PostgreSQL's postmaster argv has neither), and only `--cache` gets a real
check: `core/preflight.py::capture_pg_memory_config` now probes PostgreSQL's
actual `shared_buffers`/`effective_cache_size` (via `sudo -u postgres psql`
against the local trust-authenticated socket, the same idiom
`bootstrap-patroni.tftpl` already uses), recorded as a new `pg memory:`
manifest note; `analysis/validation.py::check_run_comparability` then
compares CockroachDB's implied cache (`--cache` fraction × measured RAM)
against PostgreSQL's recorded `shared_buffers` within a 5%
`CACHE_EQUIVALENCE_TOLERANCE`, erroring on D9 if they diverge. `--max-sql-memory`
is deliberately never compared cross-engine at all (no PostgreSQL
counterpart — `work_mem` is per-query, not a global pool). Both existing
`dead` runs above predate the new probe, so neither has a `pg memory:` note;
this is handled as a **warning**, not an error ("PostgreSQL's cache budget
was not recorded... equivalence could not be verified"), which is what
actually unblocked them — confirmed live: `crdblab analyze
engine-comparison --crdb 20260909T231745Z_p4-chaos-dead --pg
20260910T014707Z_p4-chaos-dead` now exits 0 and prints a real
throughput-latency comparison instead of refusing. Six new tests cover the
same-engine-unchanged, cross-engine-unrecorded, cross-engine-within-tolerance,
cross-engine-outside-tolerance and `--max-sql-memory`-never-compared cases;
full suite 266 passing, `ruff check` shows the same 33 pre-existing findings
as before this change (zero introduced by it, confirmed by diffing against
the pre-change baseline on the same file set) — no regressions.
**Known limitation, not yet closed:** the real fraction-comparison arithmetic
is covered only by synthetic unit-test fixtures so far, not by a live probe
against a running PostgreSQL node — `capture_pg_memory_config`'s exact
`sudo -u postgres psql` invocation has not been exercised against a real
cluster. It will run automatically the next time any PostgreSQL phase
executes (`bench`, `chaos run`, or the between-phase repair in
`run-experiment.sh` all call `capture_server_config`); worth a glance at that
run's manifest for a `pg memory:` note the first time it happens, but nothing
about it should require a dedicated run.

**Superseded below (2026-09-10, hardware reshape): every run and run id named
in the next two paragraphs was taken on the OLD 2 vCPU / 4 GB nodes at
insert_count 3.75M, and none of those run directories exists any more -- see
the reshape update after the insights section. Their run ids are historical
labels now, like a `DN` citation. The engine-comparison reasoning about which
pairs are worth running still holds; the specific pairs do not.**

**Order of work, updated: the cross-engine comparability gate is fixed; only
a live confirmation of the new probe (not a full re-run) remains
optional.** Every phase on both engines has run at least once at the current
3.75M-row thesis profile, `dead` included, and `analyze engine-comparison`
now runs against them end to end. The next PostgreSQL run of any kind will
naturally confirm the new `capture_pg_memory_config` probe against a live
cluster; nothing forces that to be a thesis-scale run. The testbed is
currently deployed as PostgreSQL (the 2026-09-09 23:58 run's redeploy, not
since changed).

**Only the `dead`-phase pair has actually been run through
`engine-comparison` so far; the other two are untried, not blocked.** The
other two thesis-scale pairs available on disk right now, not yet attempted:
`--crdb 20260909T224600Z_bench_cluster --pg 20260910T011819Z_bench_cluster`
(Phase II, the only pair with a full 4-tier throughput-latency curve on both
sides -- the other two chaos-run pairs carry a single pre-fault tier each,
so their "matched throughput"/"matched utilisation" sections degrade to the
lightest-load median and the NOT-A-RESULT same-concurrency delta rather than
a real curve comparison) and
`--crdb 20260909T230544Z_p4-chaos-recover --pg 20260910T013631Z_p4-chaos-recover`
(Phase III). Running all three and writing up the results is the actual
dissertation deliverable this fix exists to unblock; nothing about it
requires new data collection or code.

**Update — 2026-09-10: `run-experiment.sh` no longer analyses or draws
figures, the phases report progress properly, and there is a separate insights
generator.** Four changes, all code-only and all exercised against the runs
already on disk (no testbed needed).

(1) **The `Analysis` and `Figures` steps are gone from `run-experiment.sh`.**
Both ran unconditionally at the end of every sweep: `analyze` printed several
screens of multi-paragraph prose after a run that had already taken two hours,
and `report figures` re-rendered the same five numbered slots whether or not
that run was one anyone wanted figures from. Both commands are unchanged and
still invocable by hand; the script now ends with a validation count and one
line per run from the new **`crdblab headline <run>`**, which picks its numbers
by phase (network -> quorum floor and the node that sets it; bench -> peak
ops/s, its concurrency and read/update p50; chaos -> availability RTO, probe
RTO, RPO, and a loud note if the fault did not land or an instrument stopped
observing). Its numbers come from the same analysis functions `analyze` uses,
so a headline and a full report cannot drift apart.

(2) **`crdblab/core/console.py` is the single output surface**, replacing bare
`print()` in `bench.py`, `p4_chaos.py`, `p1_network.py` (which printed *nothing*
for its whole duration) and `preflight.py`'s settle waits. It carries the same
visual language `run-experiment.sh` already used, plus a `ProgressBar`.
**The non-TTY branch is load-bearing**: the script tees stdout to its log, so a
carriage-return bar would write one kilometre-long unreadable line into the
permanent record of every experiment. On a pipe the bar degrades to a
newline-terminated line at most every 15 s -- the idiom `cli.py`'s standalone
probe command already followed by hand, now generalised and pinned by tests.
`ProgressBar.interrupt()` exists so the fault instant survives in the log
rather than being overwritten by the next frame. No new dependency: `rich` and
`tqdm` were deliberately not added.

(3) **`crdblab/insights/` + `./generate-insights.sh`** draw the wider catalogue
-- 29 charts in five groups -- from every run on disk, with `insights.md`, a
self-contained `dashboard.html` and `summary.json`/`summary.csv`/
`chart_status.csv` beside them. Two groups are genuinely new evidence rather
than re-cuts: **group B is the first consumer of `hardware_metrics.csv`
anywhere in the project** (it had been collected since 2026-09-10 and read by
nothing -- no loader property, no analysis function, no figure), and **group D
is the first time `engine_comparison.compare()` is plotted rather than
printed**. A chart whose inputs are absent skips with a stated reason and is
listed as skipped in both the report and the dashboard, because a missing
artefact and an absent effect must not look alike; the runner catches an
exception from a chart as a last resort so one failure cannot cost the other
28. Everything resolves through `loader.load_run()`, so the existing gate still
holds. See `docs/data-schema.md` for the output layout and `instructions.md`
§9b for the group-by-group table.

(4) **`crdblab/report/style.py`** now holds the palette, the rcParams, the
provenance slug and the PNG+SVG writer, extracted from `figures.py` so both it
and `insights/` draw with one house style rather than two copies of it.
`figures.py` re-exports the names it used to define, so nothing that imported
them from there broke.

Two findings from the first real run of the catalogue, both worth carrying into
the write-up. **B7 contradicts the intuitive story about load distribution**:
CockroachDB is *more* CPU-imbalanced than PostgreSQL on this testbed (CV 0.833
against 0.509, `gcp-1` at 69% against ~15% elsewhere), because `lease_preferences`
pins its leaseholders to the gateway and the testbed pins Patroni's primary to
the same node. Neither arm is free to spread work, so that chart measures this
*configuration*, not the engines' architectures -- its caption says so. And
**E1 independently re-derives the quorum floor** from `network.csv` (72.404 ms)
and gets exactly the value `preflight.json` recorded, which is a useful check
that the floor every write-path claim rests on is not an artefact of how it was
computed.

**Update — 2026-09-10: the testbed was reshaped from 2 vCPU / 4 GB to 4 vCPU /
8 GB on all six machines, the thesis working set doubled to hold the disk-bound
ratio, and a CockroachDB smoke run has already validated the new deployment end
to end. This supersedes every "currently deployed as" and "available on disk"
claim above.**

**The hardware.** All six machines (five cluster nodes plus `crdb-client-1`)
now report `nproc=4` and `MemTotal` ~8.13 GB, confirmed live rather than
inferred: 8,132,188 kB on `crdb-azure-1`, 8,128,936 kB on `crdb-gcp-1`,
8,127,820 kB on `crdb-linode-1` -- a 0.05% spread, well inside
`validation.MEMORY_TOLERANCE` (5%). The shapes are Linode `g6-dedicated-4`,
Azure `Standard_B4als_v2`, GCP `c2d-highcpu-4` (AMD EPYC 7B13, as recorded in
the new runs' `host:` notes). `c2d` was chosen over the newer `c3-`/`c4-`/`n4-
highcpu-4` shapes, which are also 4/8, because C2D definitively supports
`pd-ssd` and `modules/gcp_node/main.tf` hardcodes it; the newer families steer
toward Hyperdisk. Boot disks went 50 GB -> **100 GB** on GCP, Azure and the
client (Linode's plan ships 160 GB): pd-ssd sells IOPS by capacity at ~30/GB,
so this is ~3000 IOPS instead of ~1500, and doubling RAM while leaving storage
at the old ceiling would only relocate the bottleneck.

**The Azure nodes are burstable, and this is a disclosed limitation of the
study rather than an oversight.** `Standard_B4als_v2` is a B-series SKU, which
throttles to a baseline once its CPU credits drain -- over a sustained 60 s
bench tier that can present as the downward drift `validate`'s steady-state CV
check reads as an unsettled tier. It was chosen because **there is no
alternative on this subscription**: `standardDLSv5Family` has a hard quota
limit of **0** in both regions (a `terraform apply` with `Standard_D4ls_v5`
failed on exactly that, and that is what forced this decision), and every other
non-burstable 4 vCPU / 8 GB SKU -- the `D4ls`/`D4lds`/`D4als`/`D4alds` v5 and
v6 families and `F4als_v6` -- reports `NotAvailableForSubscription`. Only
`standardBasv2Family`/`standardBsv2Family` have headroom (limit 10 per region);
the regional `cores` cap is 6, so a second Azure node in either region would
not fit whatever family it used. It is defensible as well as forced, and this
is the framing to carry into the write-up: `crdb-azure-1` (centralindia) and
`crdb-azure-2` (eastasia) are the two most distant members at 230 ms and 204 ms
from the gateway, so their contribution to every measured quantity is dominated
by wide-area round trips rather than local CPU, and neither is ever the chaos
target, the leaseholder, or the Patroni primary. **Disclose it beside the RTT
matrix and do not rest a CPU-bound claim on those two nodes.** One subtlety
worth keeping: the B-series restriction in CentralIndia is of type `Zone` and
names **zone 2 only**, so zones 1 and 3 are unrestricted;
`modules/azure_node/main.tf` sets no `zone`, so placement is regional and the
restriction never binds. Do not add a `zone` argument without re-checking
`az vm list-skus` restrictions first.

**The working set doubled with the RAM, and the ratio is what is being held
fixed.** `profiles/thesis.yaml` and `thesis-extended.yaml` moved
`insert_count` 3,750,000 -> **7,500,000** (~12.3 GB at ~1.64 kB/row), because
6.15 GB against 8 GB nodes would be ~0.77x RAM -- fully memory-resident, and
the disk-bound operating point the setting exists to reach would have been
silently undone by a hardware upgrade. At 12.3 GB it is ~1.5x RAM again, and
~6.3x either engine's cache, exactly as before: both engines derive their cache
as a fraction of measured RAM (`--cache=0.25`, `shared_buffers` = MemTotal/4,
now ~1.96 GB rather than ~978 MB), so the two terms scale together by
construction and no cache literal needed editing. **The cost is load time**:
the PostgreSQL serial-insert load goes from 70-110 min to an estimated
**2.5-4 h**, making a full pg thesis sweep ~4-5.5 h; CockroachDB's bulk
`IMPORT` goes ~19.5 min -> ~40. `LOAD_CONCURRENCY` is still 64 and raising it
is the obvious mitigation (the load is latency-bound, `max_connections` is
500), but that path has broken three times historically, so it stays a
deliberate change rather than a default.

**Every run recorded before the reshape is incomparable with anything recorded
after it, and the run directories are gone.** `check_run_comparability`
compares `cpus` and `cpu_model` exactly and `mem_total_kb` within 5%, so the
hardware change is a hard D9 error against any older run -- and the
`insert_count` change would *not* have been caught, since that lives in the
profile rather than the server config. The `runs/` directories and `figures/`
were therefore deleted by hand; **`runs/_logs/` was deliberately kept**, so
every historical run id cited anywhere in this file is now a label backed by a
log rather than a path that resolves. `runs/legacy-runs/*.csv` (the 2026-09-10
consolidation) also survives, and is likewise pre-reshape data.

**A CockroachDB smoke run has already validated the new deployment end to end**
-- `runs/_logs/experiment-20260910T065444Z.log`, profile `smoke`, engine
`cockroachdb`, **11 m 17 s**, all four phases plus validate, 3 of 3 runs
validated, no manual intervention. Run ids `20260910T065632Z_p1-network`
through `20260910T070410Z_p4-chaos-dead`. The numbers, and why each is
reassuring:

* **Phase I** quorum floor **70.2 ms** (`crdb-linode-2`), against 70.311 ms
  from `linode-2` and 72.404 ms from `linode-1` on the old hardware. The
  network geometry is unchanged by the reshape, which is exactly what should
  happen and is a useful control: it says the reshape moved compute and storage
  and nothing else.
* **Phase II** peak **2,923 ops/s @ C=50**, read p50 1.2 ms, update p50
  75.5 ms. The update p50 sits just above the 70.2 ms quorum floor, as it must.
* **Phase III** (`recover`) availability RTO **10.8 s**, probe outage 5.5 s,
  **RPO 0/281**.
* **Phase IV** (`dead`) availability RTO **8.51 s**, probe outage 4397 ms,
  **RPO 0 of 159** acknowledged (0 ambiguous, 1 refused), performance RTO
  **9.0 s** -- defined rather than undefined, with throughput regaining the 80%
  floor. Both faults recorded `landed: true`, and `restore_target` brought
  `gcp-1` back in 15 s.

Two things about that run not to misread. Its probe reports **10.98 writes/s
achieved of 500/s dispatched at 63.9 ms resolution**, which is not a
regression: `profiles/smoke.yaml` sets `probe_workers: 2` deliberately (the
thesis profile uses eight), and the ~59/s and ~21 ms figures quoted elsewhere
in this file are eight-worker numbers. And it is a **smoke** run --
`insert_count` 125,000, ~205 MB, fully memory-resident -- so it exercises every
code path on the new hardware but says nothing about the disk-bound 7.5M-row
profile, which no run has yet touched on either engine.

**Order of work from here.** (1) `./run-experiment.sh --engine cockroachdb` at
`thesis` -- budget ~1.5 h, of which ~40 min is the load. (2) Redeploy to
PostgreSQL and repeat -- budget ~4-5.5 h, dominated by the 2.5-4 h load, and
this is also the run that will finally exercise `capture_pg_memory_config`
against a live cluster for the first time (glance at its manifest for a
`pg memory:` note; `shared_buffers` should read ~1.96 GB now, not 978 MB).
(3) `crdblab analyze engine-comparison` across all three pairs, then
`./generate-insights.sh`. Nothing in the harness code is known to block any of
this; the reshape needed no code change, only profile and terraform edits.

**Also confirmed by that smoke run, closing an open item: node_exporter
collection works on the reshaped deployment.** The 2026-09-10 change that added
`prometheus-node-exporter` to all three bootstrap templates was flagged above
as needing a redeploy before it could take effect, since cloud-init only runs
at first boot. The reshape *was* that redeploy.
`runs/20260910T065753Z_bench_cluster/hardware_metrics.csv` holds 84 rows -- 14
polls x **all six** machines (five cluster nodes plus `client-1`) -- with
exactly one blank `cpu_busy_pct` per node, which is the documented
first-poll-has-no-prior-scrape-to-diff behaviour (D5) and not a fault. So
`crdblab/core/hardware_metrics.py` and `insights/` group B both have live data
on the current hardware. `figures/` and the insights outputs were deleted with
the runs, so `./generate-insights.sh` has not been re-run since; there is
nothing to fix, it just needs invoking once there are thesis-scale runs worth
charting.

**Operational notes about the terraform side, learned the hard way this
session and easy to lose.** (1) **There is no `terraform.tfvars` in the
repository** -- only `terraform.tfvars.example`, and `terraform/*.tfvars` does
not match anything. The real variable values live outside the checkout, so an
edit to the example changes documentation and nothing else; a shape change has
to be mirrored by hand into whatever actually feeds `terraform apply`.
(2) The example was **not applyable** until this session: it declared no
`client_config` block at all, while `terraform/variables.tf` declares that
variable with no default, so the sixth machine was simply absent from the
template. It now has one, pinned to the same shape and to the same
region/zone as the GCP cluster node -- the ~1 ms client-to-gateway hop is part
of the measured geometry, so those two must stay co-located.
(3) The example's GCP shape had also drifted from reality: it said
`n2-standard-2` while the deployed machines were `n2-custom-2-4096`, which is
how the 4 GB `MemTotal` recorded in every old run came about despite
`n2-standard-2` being an 8 GB type. Treat the example as documentation to be
checked against `az vm list` / `gcloud compute instances describe`, not as a
record of what is deployed.

**The commands that answer "can this subscription actually create this shape",
so the next session does not rediscover them.** Availability and per-SKU
restrictions, which is what catches `NotAvailableForSubscription` and its
zone-scoped variant:
`az vm list-skus --location <region> --resource-type virtualMachines -o json`,
then filter on `capabilities` for `vCPUs`/`MemoryGB`/`CpuArchitectureType`/
`PremiumIO` and read `restrictions[].type` (`Zone` vs `Location`) plus
`restrictions[].restrictionInfo.zones`. Quota, which is a *separate* gate and
the one that actually failed:
`az vm list-usage --location <region> -o json` -- note `limit` comes back as a
**string**, so a JMESPath ``[?limit>`0`]`` filter crashes the CLI and the
filtering has to happen outside it. The regional `cores` entry is a total cap
that binds independently of any family. For the other two providers:
`gcloud compute machine-types list --filter="guestCpus=4 AND memoryMb=8192"
--zones=<zone>` (zone-scoped, so it answers availability directly), and
Linode's unauthenticated `https://api.linode.com/v4/linode/types`, whose
`class` field distinguishes `dedicated` from `standard` (shared-CPU) -- Linode
dedicated plans are fixed at 2 GB per vCPU, which is why 8 GB dedicated implies
4 vCPU there and why the vCPU count, not the RAM, was the binding constraint on
matching all three providers.

**This project was rearchitected from a different design.** It originally
compared the five-node CockroachDB cluster against a separate *unreplicated*
single-node baseline to isolate replication cost, with the generator running
directly from the CockroachDB gateway (`crdb-gcp-1`). That has been fully
replaced by a same-topology, cross-engine comparison: the identical five-node
cluster is stood up once per engine (CockroachDB, then PostgreSQL/Patroni —
two separate `terraform apply`s, selected by `var.database_engine`) and driven
from a dedicated **client node** (`CLIENT_NODE`, `crdb-client-1`) that is not
itself a cluster member. The old unreplicated baseline node, the
`raft-overhead` analysis command, and the `bench single`/`bench cluster`
CLI split are gone; `crdblab analyze engine-comparison --crdb <run> --pg <run>`
is the only cross-run comparison now, and `--engine {cockroachdb,postgresql}`
(default `cockroachdb`) is a **top-level** flag that must precede the
subcommand, e.g. `crdblab --engine postgresql bench --profile ...`.

`README.md` documents the harness's design commitments (why validation and
pre-flight are separate gates, why concurrency isn't load, why seeds must
match, etc.) — read it before changing any analysis or pre-flight code, since
several of these constraints exist specifically to prevent a defect that
already happened once. `instructions.md` is the full operator runbook
(provisioning through teardown) and is the place to check for **why** a given
CLI flag or ordering constraint exists.

**Phase numbering (current, post-rearchitecture):** Phase I = `net probe`
(network substrate); Phase II = `bench` (the benchmark, one run per engine);
Phase III = `chaos run --mode recover`; Phase IV = `chaos run --mode dead`.
This is consistent throughout `README.md`, `instructions.md`,
`run-experiment.sh`'s step labels, and `crdblab/cli.py`'s help text. It is
**not** consistent with on-disk naming: run directories are still suffixed
`_bench_cluster`, `_p4-chaos-recover`, `_p4-chaos-dead` (i.e. `recover` is
still a "p4" run id even though it's Phase III in prose), and
`p4_chaos.py`/`phase="p4_chaos"` in the manifest cover both Phase III and IV.
Renaming those would touch glob patterns in `cli.py` and `run-experiment.sh`
and invalidate existing run directories, so it was deliberately left alone —
don't assume the run-id suffix tells you the current phase number. Historical
prose describing the *removed* pre-rearchitecture design (an unreplicated
single-node "Phase II baseline" compared against a "Phase III cluster") was
deliberately left unrenumbered where it's describing an incident that
happened under the old scheme (mostly in `preflight.py`, `validation.py`,
`workload.py` docstrings) — a `DN`-cited defect story about the old baseline
is not the same claim as "Phase II" today, and renumbering it would misstate
history rather than clarify it.

**Update — 2026-09-10: the five numbered dissertation figures were folded into
`insights` and `crdblab report figures` was removed; `insights` writes each
render into its own subdirectory; and `crdblab/core/console.py` got a visual
pass.** Three code-only changes, none needing a live cluster.

(1) **`crdblab report figures` (`crdblab/report/figures.py`, writing
`fig1_network_matrix`/`fig2_throughput_sweep`/`fig3_latency_by_operation`/
`fig5_resilience_timeline`/`fig6_resilience_timeline_recover` into
`figures/`) is gone.** Checked against the 31-chart `insights` catalogue
before removing anything: fig3 (per-op latency, one run) was already strictly
superseded by chart A2 (p50/p95/p99/pmax, per engine, log-scaled), and
fig5/fig6 (throughput through the fault, with a fault line and a recovery
floor) were already superseded by chart C5 (the same throughput-through-fault
view, plus the settled-mean band and CV-based recovered/degraded/still-moving
verdict) together with the rest of the C-group's decomposition (C1-C4, C6-C8).
fig1 (the raw all-pairs RTT heatmap) and fig2 (the raw throughput-vs-
concurrency curve) had no equivalent — E1/E2 only ever showed a
gateway-relative bar chart and a per-link spread plot, and A1 plots
throughput against the *latency it cost*, never against concurrency directly —
so those two were ported rather than dropped, as new charts **E4 (Round-trip
matrix)** and **A7 (Throughput by concurrency)**, both adapted line-for-line
from the retired module and drawn through the same `_base.py` house style as
every other chart. The catalogue is 31 charts now, not 29. `_resilience_filename`
(the fig5/fig6 naming logic) went with the module that used it; the
provenance-slug machinery it shared with `insights` (`_provenance_slug`,
`_written_formats`, `EXPORT_VECTOR_EXT`) still lives in `crdblab/report/style.py`
and is unaffected — `tests/test_figures.py`'s still-relevant cases moved to
`tests/test_report_style.py`. `crdblab cli.py` lost its `report`/`figures`
subparser and `_cmd_report`; `report figures` is no longer a valid invocation.

(2) **`crdblab insights` now writes each invocation into its own subdirectory**
(`<out>/<run_id>/`, `run_id` = a timestamp plus the `--profile` filter or
`all`, the same idiom a measured run directory uses) instead of writing
straight into `--out`. Before this, a second `crdblab insights` (or a second
`./generate-insights.sh`) silently overwrote every PNG, the report and the
dashboard from the first — there was no way to keep yesterday's render next to
today's. `crdblab/insights/run.py::generate()` itself is unchanged and still
writes exactly to the `out_dir` it is given (existing tests call it directly
with an exact path); the subdirectory policy lives one layer up, in
`cli.py::_cmd_insights`, the same layer that already decides run ids elsewhere
in this codebase. `generate-insights.sh`'s closing summary now globs for the
newest subdirectory under `$OUT` to report on, the same "sort by name, take
the last" idiom `run-experiment.sh`'s own `latest()` uses for `runs/`.

(3) **`crdblab/core/console.py` got a colour and progress-bar pass**, at the
user's request that the harness's terminal output "look good" rather than
merely be readable. See the module's own docstring and the code for what
changed; the non-TTY degrade-to-periodic-plain-lines behaviour (load-bearing
for `run-experiment.sh`'s `tee`d log file) is unchanged.

**Update — 2026-09-10, same day: the claim just above ("unchanged") stopped
being true a few hours later, on user feedback that Phase II/III/IV's bars
still didn't behave like `./generate-insights.sh`'s.** Two more fixes to
`console.py`, both requested after the pass above shipped.

First, `ProgressBar._render()` put the bar glyph *between* the label and the
rest of the line (`label  BAR  pct  suffix  (elapsed)`) -- reported as "the
progress bar in the middle of the message." All descriptive text (label,
live suffix, elapsed/eta) is now assembled first as one block, with the
single indicator (the bar+percentage, or the spinner for a total-less bar)
appended at the very end, uniformly across both branches.

Second, and the bigger one: **the reason Phase II/III/IV's bars looked like
"multiple progress bars" is that `run-experiment.sh` pipes its entire stdout
through `tee` for the whole run** (`exec > >(tee -a "$LOG") 2>&1`, present
since before this session), so every `crdblab` subprocess it spawns sees
`sys.stdout.isatty()` as `False` -- not because nobody is watching, but
because the terminal is on the other end of a pipe rather than directly
attached. That forced every bar into the periodic-full-line fallback for the
bar's *entire* duration, which over a multi-hour phase is dozens of separate
lines, each looking like its own bar. `./generate-insights.sh` never pipes
its own stdout anywhere, so its "drawing charts" bar never hit this path and
animated normally -- which is what made the two look different and is
exactly what was asked to be fixed: make every bar behave like that one.
`ProgressBar.__init__` now looks past `sys.stdout` to the process's
*controlling terminal* (`/dev/tty`, opened once, lazily, and reused) whenever
the caller did not pass an explicit `stream=`: if `/dev/tty` opens
successfully, the bar animates there in place instead, exactly like
`generate-insights.sh`'s, and the periodic-line fallback is skipped entirely
while doing so. Only a genuinely headless invocation (no controlling terminal
at all -- true `cron`/CI/detached-session use, verified with
`start_new_session=True`) still falls back to periodic lines, unchanged from
before. An explicitly passed `stream=` -- what the whole test suite always
does -- is never second-guessed by any of this, so `tests/test_console.py`
needed no changes.

**The trade-off, stated plainly because the module's own docstring said the
opposite until today: while a bar is animating on `/dev/tty`, its per-tick
progress no longer lands in `run-experiment.sh`'s log file** -- only each
bar's final `close()`/`interrupt()` summary does, since those still print to
real `sys.stdout` regardless of where the bar itself draws. That is not a
regression; it is `generate-insights.sh`'s own behaviour (no periodic trace,
just the live bar and a final line), reproduced everywhere on request. A
truly headless run keeps its full periodic trail, unchanged.

Verified with Python's `pty` module rather than by inspection: forked a child
onto a real pty *and* redirected its fd 1 to a separate plain pipe (the exact
shape `exec > >(tee ...)` produces -- a real controlling terminal exists, but
`sys.stdout` is not it). The animated, coloured bar landed on the pty side
only; the plain pipe received exactly one line, the `close()` summary. Also
caught and fixed in the same pass: a freshly created pty with no window size
configured reports `TIOCGWINSZ` as `0x0` rather than failing, which made
`_terminal_width()` truncate every line to empty (a bare `\r` and nothing
else) -- it now treats a non-positive column count as "unknown" and falls
back the same way a genuine `OSError` does.

**Update — 2026-09-11: the CockroachDB generator now goes through a local
HAProxy on `crdb-client-1`, pinned to gcp-1, mirroring Patroni's own
single-active-primary shape. Code-complete; not yet exercised against a live
cluster.**

Reviewing the latest CockroachDB thesis-scale run
(`runs/_logs/experiment-20260910T095237Z.log`, the first on the reshaped 4
vCPU/8 GB hardware) surfaced a new finding: Phase III/IV's chaos-run baseline
throughput at C=100 (~1,400 tps) was only ~38% of Phase II's bench throughput
at the same concurrency (~3,666 tps), verified directly from each run's
`metrics.csv`. The cause was `p4_chaos.py`'s hardcoded
`next(n for n in topo.nodes if n.name != fault_target.name)` -- a one-off hack
that picked whichever cluster node wasn't the chaos fault target (always
`linode-1`, since `chaos.target` is always `gcp-1`) as the generator's sole
connection target, so that node paid an internal CockroachDB RPC hop to reach
the pinned leaseholder on `gcp-1` for every query -- for the *entire* chaos
run, not just the post-fault window, unlike Phase II's direct-to-gateway
connection. `hardware_metrics.csv` corroborated this: `gcp-1` (the
leaseholder) sat at only 40% mean CPU during the chaos baseline versus 54%
mean/98% peak during bench, while `linode-1` (now acting as SQL gateway) was
the busy node at 64% mean/93% peak -- the bottleneck had moved off the
leaseholder entirely. On the pre-reshape hardware this gap was invisible
(CPU was the binding constraint for both paths, so bench and chaos-baseline
throughput matched almost exactly, ~1030 vs ~1050 tps); the reshape relieved
Phase II's CPU bottleneck far more than it relieved the network-hop cost
baked into every CockroachDB chaos run, widening what used to be negligible
into ~2.6x.

The first fix (an HAProxy backend load-balancing across
`{gcp-1, linode-1, linode-2}`, `leastconn`, with `{azure-1, azure-2}` as
HAProxy `backup` servers, mirrored onto PostgreSQL's existing HAProxy the same
way for "as similarly configured as possible" symmetry between the two
engines) closed the comparability gap but introduced a new one: since
CockroachDB's `/health?ready=1` answers 200 for *any* live node (unlike
Patroni's `/primary`, which only the actual primary ever answers), true load
balancing meant paying the internal RPC hop on 2/3 of connections *even while
gcp-1 was perfectly healthy* -- worse than the original problem for Phase II,
which used to dial the gateway directly with zero hop cost.

**The actual fix, at the user's suggestion ("why not just pin to gcp-1 like we
did for PostgreSQL"), was to stop load-balancing and pin instead.**
`crdb_gateway`'s HAProxy backend now has `gcp-1` as the only regular server
and all four other nodes (`linode-1`, `linode-2`, `azure-1`, `azure-2`) as
flat-tier `backup` servers -- not linode-preferred-over-azure, matching
Patroni's own undifferentiated `failover_priority` (1 for all four non-gcp-1
nodes) rather than inventing a CockroachDB-specific priority PostgreSQL
doesn't share. This makes Phase II's *entire* duration and Phase III/IV's
*pre-fault* baseline both hit `gcp-1` exclusively -- identical to
direct-to-gateway behaviour and comparable with every historical Phase II
run -- and the extra hop only appears during a chaos run's genuine post-fault
failover window, which is exactly when service should look different.

Three considered-and-rejected alternatives, in case they resurface. (1)
Connecting the generator directly to the fault target itself (the user's
first instinct): investigated and rejected -- in `dead` mode the target
doesn't restart until `restore_target()` runs, well after the measurement
window closes, so a target-pinned generator would report zero recovery for
the whole run; in `recover` mode the target is network-partitioned, so a
client dialling it directly is simply unreachable for the full 45 s heal
window regardless of how fast the surviving nodes actually restore
availability. Both would conflate "is this one socket back" with "is the
cluster serving writes again," which is the wrong question. (2) Spreading
CockroachDB's actual leaseholder placement (not just the SQL entry point)
across the three fast nodes, to genuinely distribute write-coordination work:
rejected as out of scope -- `lease_preferences` is a priority-fallback chain,
not a spreading primitive, so this would require partitioning `usertable`
into three region-pinned key ranges, a schema-level change; and it would be
asymmetric with PostgreSQL regardless, since Patroni is structurally
single-primary and can never match it. (3) A three-tier HAProxy priority
(gcp-1, then linode-1/linode-2, then azure-1/azure-2 only as a last resort):
rejected as unneeded complexity -- this project's fault model only ever kills
one node at a time, so the flat four-way backup tier can never actually
distinguish itself from a tiered one in any run this harness performs, and
Patroni's own failover priority doesn't make that distinction either.

Files touched: `terraform/scripts/bootstrap-client.tftpl` (new `crdb_front`/
`crdb_gateway` HAProxy stanza, health-checked via CockroachDB's
`:8080/health?ready=1` -- new plumbing, nothing in this codebase used that
endpoint before; caught and fixed a self-inflicted bug mid-edit where
appending both backends' `server` lines after one combined heredoc would have
silently attributed every line to whichever backend was declared last,
leaving `patroni_primary` with zero servers -- fixed by splitting into two
heredocs so each backend's servers land directly after its own stanza);
`crdblab/config.py` (`crdb_generator_dsn()`, mirroring `pg_generator_dsn`);
`crdblab/phases/bench.py` (`Target.db_uri`'s CockroachDB branch now
symmetric with the PostgreSQL one); `crdblab/phases/p4_chaos.py` (the
`admin_node`/fault-target-avoidance hack deleted entirely -- HAProxy's own
health checks make it unnecessary); `crdblab/core/preflight.py`
(`check_write_latency_floor`'s docstring updated to explain that observed
write p50 is now expected to shift only during a chaos run's post-fault
window, not during ordinary bench tiers); `tests/test_topology.py` (updated
the one test that asserted the old literal gateway URI, added a new test for
`crdb_generator_dsn`). Verified locally: full test suite (310 passed), `ruff
check` (96 pre-existing findings, zero new -- confirmed by diffing against a
stash of the pre-change tree), `terraform validate`, a rendered-template
`bash -n` syntax check, and a standalone execution of the peer-loop logic
confirming the generated `server` lines are exactly `gcp-1` regular / four
backups. **Not yet applied to live infrastructure** -- `bootstrap-client.tftpl`
only runs at first boot, so this needs a `terraform apply` (client-node
redeploy) before any of it takes effect, followed by smoke runs on both
engines to confirm the throughput-comparability fix holds and nothing
regressed. See `docs/testbed.md` for the resulting architecture once deployed.

**Update -- 2026-09-11, later the same day: the pin above was redeployed,
found defective by its own first smoke pair, fixed, and redeployed again --
all confirmed live before the day was out.** The redeploy applied cleanly, but
a `recover`/`dead` smoke pair run immediately afterward showed CockroachDB's
post-fault read p50 stuck at ~436 ms and update p50 at ~839 ms -- both flat for
the *entire remainder* of each run, including well after `recover`'s partition
healed -- against a ~1 ms/~76 ms pre-fault baseline, with throughput pinned at
~3% of baseline throughout. Two compounding causes in the first version of
`crdb_gateway`: the four non-gcp-1 backups were a flat, unordered tier, so a
fault could fail the client over to *any* of them independent of which node
CockroachDB's own `lease_preferences` would actually promote next -- often
landing the client and the new leaseholder on two different, independently-far
survivors and paying two long WAN hops instead of one; and HAProxy only ever
prefers the regular server (gcp-1) for *new* connections, so once a fault
forced the generator's long-lived connections onto a backup, they never moved
back even after gcp-1 rejoined and the leaseholder returned to it.

Both were fixed by making the emergent behaviour explicit: the backup `server`
lines in `crdb_gateway` became hardcoded, strict-priority-ordered (`linode-1`,
then `linode-2`, then `azure-1`/`azure-2`), mirroring `lease_preferences`'s own
tiers exactly rather than a flat pool; and `default-server` gained
`on-marked-up shutdown-backup-sessions` alongside the existing
`on-marked-down shutdown-sessions`, forcing sessions parked on a backup to
drop and reconnect the instant gcp-1's health check passes again (`/primary`
already gives `patroni_primary` this for free, since only the actual primary
ever answers it -- CockroachDB's `/health?ready=1` answers 200 for any ready
node, so gcp-1 recovering does not on its own make a backup's check fail).
A second redeploy plus a second smoke pair confirmed the fix the same day:
post-fault read p50 dropped to ~18.9-22 ms (matching the client-to-linode-1 RTT
from Phase I's own network matrix -- the client now lands on the same node
`lease_preferences` also promotes) and post-fault throughput recovered to
~150-300 tps (~25-45% of a ~600-700 tps baseline), still climbing at the end of
the `recover` run's 105 s window instead of flat at ~15-24 tps for the whole
run. Availability RTO (~11 s) was essentially unchanged, as expected -- that
number is dominated by HAProxy's own health-check detection delay, which
neither fix touched; what changed is the quality of service once it resumes,
not how fast it resumes at all. See the `crdb_gateway` entry in `CLAUDE.md`'s
"Known gotchas" for the short, operational form of this fix, and
`docs/testbed.md` for the resulting architecture.

**Update -- 2026-09-11, that evening: a full `thesis-extended` CockroachDB
sweep (all four phases) confirmed the pin at scale, surfaced a second,
unrelated pre-flight defect, and finished the day with both engines complete
across all three profiles.** `experiment-20260911T164025Z.log` ran Phase I
through Phase III cleanly -- Phase II's C=200 tiers held ~3800-3900 tps with
read p50 ~32-34 ms, and Phase III's `recover` run regained its 80% throughput
floor -- but Phase IV died immediately, before injecting anything:

```
refusing to measure: Command [...ssh... crdb-gcp-1 'TS_IP=$(tailscale ip -4);
cockroach sql ... SHOW RANGES FROM DATABASE ycsb WITH DETAILS ...']
timed out after 60 seconds
```

`core/preflight.py::_read_leaseholder_placement` SSHes to the gateway and runs
`SHOW RANGES FROM DATABASE ycsb WITH DETAILS`, and that query scales with
range count, not row count: it read under 20 s at the 38 ranges Phase I's own
probe had seen fresh off the load, but re-run by hand after Phase II's 34-minute
sweep and Phase III's 16.75-minute `recover` run had churned the table with
splits and lease transfers, the identical query took 1 m 13.8 s against 122
ranges -- comfortably over `ssh.run`'s hardcoded 60 s ceiling. That is exactly
the kind of "not settled yet" condition `check_leaseholder_placement`'s
`settle_timeout_s` poll loop exists to tolerate (900 s for `thesis-extended`'s
chaos phases) -- but `_read_leaseholder_placement` let a slow reading raise
`subprocess.TimeoutExpired` instead of returning a normal not-yet-passed
result, so the very first poll crashed out of the loop entirely, propagated
through `p4_chaos.run()` uncaught, and was only caught by `cli.py`'s blanket
`except Exception`, which aborted the whole phase before the fault was ever
injected and before the 900 s settle budget was ever used. `crdb-gcp-1` itself
was untouched throughout -- `check_leaseholder_placement` is read-only and runs
before any fault payload, confirmed live afterward (5/5 nodes up, 7.5M rows
intact, the same `cockroach` process still running since the original
deployment). Fixed by wrapping the `ssh.run()` call in
`_read_leaseholder_placement` with a `try`/`except subprocess.TimeoutExpired`
that returns `(False, "...timed out after 60s", {})` -- the same
catch-locally-and-return-a-result pattern already used in
`p1_network.py::probe_node` and `core/remote_probe.py`, rather than a new one.
Two unit tests added (`tests/test_preflight.py`): a first-reading timeout that
still converges once a later poll succeeds, and an always-timing-out reading
that still fails cleanly rather than crashing. Full suite 312 passing (was
310), zero new `ruff` findings.

Re-running `crdblab chaos run --mode dead --profile thesis-extended` directly
against the still-live, still-loaded cluster (no reload needed -- the fault
never landed on the first attempt, so nothing was left mid-fault) completed
cleanly: fault injected at 71.6 s, ran the full 960 s
(`min_post_fault_s: 900`), gcp-1 killed and then restored and rejoined (5/5
live within 16 s), RPO clean at 0 writes lost of 2185 acknowledged, `validate`
PASS. `runs/20260911T182217Z_p4-chaos-dead`. Its `performance_rto_s` reads
"not regained within the run," which is the expected, structural asymmetry
already on record for `dead` mode against the pinned leaseholder (throughput
cannot fully recover until gcp-1 itself rejoins and reclaims the lease, which
only happens via the restore step *after* the measurement window closes), not
a new defect.

With that run, both engines now have a complete run matrix: `smoke`, `thesis`
and `thesis-extended`, all four phases each (`net probe`, `bench`,
`chaos run --mode recover`, `chaos run --mode dead`), for both `cockroachdb`
and `postgresql` -- twenty-four measured runs across the three profiles, plus
`insights/` renders already generated for all three
(`insights/20260911T034313Z_smoke`, `insights/20260911T084414Z_thesis`,
`insights/20260911T184408Z_thesis-extended`). This is the first point in the
project where every profile has been swept end to end on both engines with the
current hardware, HAProxy topology and pre-flight gate set.
