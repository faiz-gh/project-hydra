# New defects: D13–D17

Continues the numbering in `docs/defects.md` (D1–D12). Same format as that
document: mechanism, observable signature, consequence, and the structural
change that prevents recurrence. Each is classified **Class 1** (derived from a
retained, validated run — recomputable right now from a file on disk) or
**Class 2** (a contemporaneous diagnostic measurement of a configuration that
was never retained as a validated run), per the provenance rule
`docs/gaps-resolution.md` §"Class 1 / Class 2" and
`docs/dissertation-verification.md` § 8 already established for this project.
The two classes are not blurred anywhere below.

---

## D13 — The RTO probe's worker pool phase-locked, collapsing its own resolution

**Class 2.** Found via `crdblab probe rto` runs against the live cluster before
Deployment C existed. Those run directories (`20260905T172748Z_p4-probe`,
`20260905T173255Z_p4-probe`, `20260905T173604Z_p4-probe`) were deleted at the
user's request once their diagnostic purpose was served (they were never part
of a validated Deployment). **The exact numeric evidence below cannot be
recomputed from any artefact currently on disk**; it is retained only in the
conversation transcript and in the code comments the fix left behind
(`crdblab/core/rto_probe.py`, `DEFAULT_WORKERS` docstring and `_spacing()`
docstring), which is why this defect is recorded here rather than left as an
unstated assumption behind the current design.

**Mechanism.** The dispatcher fired a canary write the instant a worker
permit was free, with no minimum spacing between dispatches beyond the
(sub-millisecond) `probe_interval_s` tick. Against a link where every write
costs the same duration (dominated by network RTT to the gateway, not by
server-side variance), all eight workers that started together finished
together, released their permits together, and were immediately refilled by
the next eight ticks — so the pool oscillated as a single burst of eight
writes once per round trip, rather than as eight staggered, continuously
spread observations.

**Signature.** A first standalone run (8 workers, 2 ms configured dispatch
interval, against the live gateway) reported: median gap between served writes
0.22 ms, 90th-percentile gap 342 ms, worst gap 918 ms, with 65% of all gaps
under 1 ms. The probe's own `resolution_s` field — at the time defined as the
*median* gap — reported **0.2 ms**, a precision the eight-worker,
370-millisecond-write design could not possibly have achieved.

**Consequence.** An outage genuinely lasting up to ~350 ms could have begun and
ended entirely inside one of the inter-burst holes and been recorded as no
interruption at all, while the probe's own reported resolution claimed
sub-millisecond precision — the exact shape of asserting a false precision
that this project's methodology exists to prevent.

**Prevention.** Two changes, both now load-bearing in
`crdblab/core/rto_probe.py`:

1. The dispatcher now enforces a minimum spacing between dispatches
   (`RtoProbe._spacing()`), derived from a rolling estimate of the *achieved*
   write latency divided by the worker count, rather than firing on every free
   permit. This is what turns a burst-of-eight-then-a-hole pattern into eight
   evenly staggered observations per round trip.
2. `resolution_s` is now the **95th percentile** of the gap distribution
   (`resolution_of()`), not the median — because the relevant question for an
   RTO claim is the size of the gap an outage could hide inside, which is a
   tail property, not a central one. A regression test
   (`tests/test_rto_probe.py::test_the_pool_does_not_phase_lock_into_bursts`,
   `::test_resolution_is_the_tail_of_the_gap_distribution_not_the_median`)
   pins both properties against a synthetic reproduction of this exact shape,
   since the live evidence itself is not retained.

---

## D14 — A longer post-fault observation window inflates a naive "largest gap" outage detector

**Class 1.** Fully recomputable right now from
`runs/20260905T213941Z_p4-chaos-dead/rto_probe.csv`, a retained, validated run
(`crdblab validate` passes on both its workload and probe logs — see
`VALIDATION-STATUS.md`).

**Mechanism.** The first version of the probe's outage detector flagged the
largest post-fault gap between served writes whenever it exceeded a floor
(the largest pre-fault gap, plus one sampling period). This compares a
maximum against a maximum without accounting for how many observations each
side drew from: the fault is injected roughly a third of the way into a
180-second run by design, so the post-fault window always contains roughly
twice as many observations as the pre-fault one. Drawing more samples from an
identical heavy-tailed distribution produces a larger maximum on its own, with
nothing having actually changed about the system's health.

**Signature.** The retained dead-fault run: 710 pre-fault gaps (max 638 ms) and
1,463 post-fault gaps (max 869 ms). The naive detector's floor was
638 ms + one 224 ms sampling period = 862 ms; 869 ms cleared it by 7 ms, and
the detector reported a 41.085-second "RTO" 40.2 seconds after the fault.
Checking the *rate* rather than the maximum: gaps exceeding the pre-fault 95th
percentile occurred 35 times in 710 pre-fault observations and 30 times in
1,463 post-fault observations — an exceedance **rate** of 4.93% before the
fault against 2.05% after it. The post-fault rate is lower, not higher.

**Consequence.** Had this shipped uncorrected, `RESULTS-DATA-DEPLOYMENT-C.md`
§ 6c would have reported a 41-second recovery time for a fault (killing one
follower out of five) that provably left quorum intact and whose own
performance-RTO figure recovered in 26.5 seconds — an RTO larger than the
run's own total duration to first served write after the fault
(`next_write_after_fault_s = 0.151 s`), asserted from a coin-toss in the
probe's own sampling noise.

**Prevention.** `crdblab.core.rto_probe.tail_attribution()` compares
*exceedance rates*, not raw maxima: it counts, on each side of the fault, how
often a gap exceeds the pre-fault 95th percentile, and requires the post-fault
rate to be at least 1.5× the pre-fault rate before calling a gap attributable.
`measure_rto()`'s `outage` field is now split into `fault_attributable` and
`quotable_value_s` (`null` when not attributable), and every printed claim
(`crdblab chaos run`, `crdblab analyze resilience`) states the exceedance
counts rather than only the verdict. Two regression tests pin both directions
of this: `test_a_longer_post_fault_window_does_not_manufacture_an_outage`
(the false positive, reproduced synthetically) and
`test_a_genuine_rate_increase_after_the_fault_is_attributable` (confirming the
fix does not trade the false positive for blindness to a real one).

---

## D15 — `in_flight_fraction` could exceed 1.0

**Class 1.** Recomputable from the same retained run as D14; the specific row
is `seq_id 1169` in `runs/20260905T213941Z_p4-chaos-dead/rto_probe.csv`.

**Mechanism.** The fraction of an outage gap that the closing write spent "in
flight" was computed as that write's own flight time (dispatch to completion)
divided by the gap's duration. With several workers dispatching concurrently,
the closing write can be dispatched *before* the previous served write even
completes — its own flight time then measures a longer interval than the gap
it is being related to, and the ratio is unbounded above.

**Signature.** `seq_id 1169` was dispatched at wall offset 99.762 s and
completed at 101.089 s (flight time 1,326.6 ms), while the gap it closed ran
from 100.220 s to 101.089 s (869.3 ms). `1326.6 / 869.3 = 1.526` —
`in_flight_fraction` read **1.5261**, a value the field's own name and
documented range (a fraction, ∈ [0, 1]) rule out on its face.

**Consequence.** Confined to a single derived diagnostic field
(`in_flight_fraction`) used only to phrase how confidently a gap's closing
write dates the recovery; it did not change `rto_s`, `quotable_value_s`, or any
attribution verdict. Recorded because a value outside its own documented range
is exactly the kind of internally-inconsistent output this project's
`crdblab validate` exists to catch for the workload schema, and the probe's own
derived statistics are not exempt from the same discipline merely because
`validate_probe()` does not yet assert a bound on this specific field.

**Prevention.** The fraction is now the *overlap* between the closing write's
flight window and the gap window, clamped to at most 1.0:
`overlap = max(0, after.complete − max(after.dispatch, before.complete))`,
`in_flight_fraction = min(1.0, overlap / duration)`. Re-run against the same
row: `in_flight_fraction = 1.0` — the write was in flight for the gap's entire
duration, which is the correct and meaningful reading (it was already waiting
when the previous write completed), not the previous unbounded value. A
targeted regression (`test_in_flight_fraction_is_clamped_to_one_when...`) pins
this exact scenario, and a property test
(`test_in_flight_fraction_never_exceeds_one`) checks the invariant across 200
randomised overlap shapes.

---

## D16 — `cockroach start --background` over SSH hangs the calling session for tens of minutes

**Class 1.** Recomputable from `runs/_logs/experiment-20260905T180838Z.log`
(retained on disk) and from the `started_utc`/`finished_utc` timestamps in the
five run manifests that log's sweep produced (all five retained and validated,
though **superseded by Deployment C and not the subject of this documentation
package** — see `VALIDATION-STATUS.md` for why they remain valid artefacts in
their own right).

**Mechanism.** `run-experiment.sh`'s node-restart step ran
`ssh host "... cockroach start ... --background"`. `--background` forks the
CockroachDB process and returns *within the remote shell*, but the forked
process inherits that shell's stdout and stderr file descriptors — which are
the SSH channel itself. OpenSSH does not close a session while any process on
the remote side still holds its pipes open, so the client blocks waiting for
the *database process* to exit, not for the shell command that started it.
`>/dev/null 2>&1` appended on the *local* (client) side only discards what the
client prints once the channel finally closes; it does nothing to the pipes
the remote fork is holding.

**Signature.** The retained log's sweep: `Phase IV — process kill` (the last
timed phase) finished at `2026-09-05T19:23:46.74Z`
(`runs/20260905T192035Z_p4-chaos-dead/manifest.json` → `finished_utc`); the
script itself reported `Done in 126m 5s` from a start of `18:08:38Z`, i.e.
completion at `20:14:43Z` — **a 50-minute-56-second gap** between the last
measurement finishing and the script actually exiting, all of it spent in the
`Restoring crdb-linode-2` step and whatever followed it before the connection
eventually timed out. For contrast, the equivalent gap in the fixed sweep
(Deployment C, `20260905T213941Z_p4-chaos-dead` finishing at
`21:42:54.96Z` against a script start of `20:26:58Z` and total runtime
`76m 30s`, i.e. completion at `21:43:28Z`) is **33 seconds**.

**Consequence.** No measurement was corrupted — every retained run from that
sweep validates and every one of its numbers is sound — but a 75-minute sweep
silently became a 126-minute one with no failing check anywhere to report why,
which is exactly the operational form of "looks fine, isn't" this project's
culture treats as worth fixing on sight rather than shrugging off as
infrastructure flakiness.

**Prevention.** The remote command's own stdio is redirected from `/dev/null`
and to `/dev/null` *before* `--background` forks
(`--background </dev/null >/dev/null 2>&1`), detaching the daemon from the SSH
channel so the session closes the instant the shell command returns. The
rejoin poll, which had been inheriting the hang as an accidental grace period,
now sleeps explicitly between attempts so it does not report "has not
rejoined" on a node that is merely still starting (`CHANGE-DIFF.md` § 1 quotes
the diff in full).

---

## D17 — `report figures --chaos` pins a fault class, it does not filter the others; a stale cross-deployment figure went undetected

**Class 2.** The symptom's direct evidence — a file-modification-time
mismatch — no longer exists on disk: the affected file
(`figures/fig6_resilience_timeline_recover.png`) was subsequently overwritten
with correct data as part of fixing this defect. What follows is a
contemporaneous observation (`ls -lt figures/*.png`, run in this conversation
before the fix) rather than a value recomputable from a currently retained
artefact. The **cause** is independently verifiable right now, in the current
source diff (`CHANGE-DIFF.md` § 1), which is not the same claim as the symptom
being reproducible.

**Mechanism.** `run-experiment.sh`'s figures step passed `--chaos` once, for
the `dead`-mode run only (`FIG_ARGS+=(--chaos "$(basename "$P4D")")`), on the
reasoning that `report figures`' `--chaos` flag is repeatable and each
fault class draws its own figure regardless of how many times the flag
appears. But the script itself supplied it only once, so
`fig6_resilience_timeline_recover.png` was never named as an input on that
invocation and `crdblab report figures` correctly left it untouched — showing
whatever data the *last* invocation that did name a recover-mode run had drawn
from, which after a redeploy is data from an entirely different cluster.

**Signature.** `ls -lt figures/*.png` after the first post-shift sweep
(the one affected by D16) showed five files dated `Sep 6 01:44` and one,
`fig6_resilience_timeline_recover.png`, dated `Sep 4 20:02` — drawn from a
Phase IV recover run measured before the gateway moved off `crdb-linode-1`.
Nothing in `crdblab report figures`' own output flagged this: the command
prints only the runs it was *given*, and correctly said nothing about the one
it was not.

**Consequence.** A figure set that looked complete and internally consistent
(same date stamp, same run in five of six files) silently carried one figure
from a different deployment, with a different gateway, a different quorum
floor, and different CPU hardware behind its numbers — exactly the kind of
mixed-provenance artefact `_finish()`'s footer-stamping exists to make
detectable, except that detection requires a reader to actually check the
stamp against the other five, which nothing forced.

**Prevention.** `run-experiment.sh` now passes `--chaos` for **both** fault
classes whenever both exist (`CHANGE-DIFF.md` § 1 quotes the diff), so a sweep
that ran both Phase IV modes always regenerates both timeline figures. This
is a script-level fix rather than a `crdblab report figures` behaviour change:
the tool's semantics (a pin, not a filter) are arguably correct as documented —
"defaults to the most recent run of *each* fault class" is exactly what
happens when nothing is passed — and the defect was the sweep script
overriding that sensible default with an incomplete explicit list. No
regression test is added for this one: it is a shell-script orchestration
bug with no unit under `tests/`, and the prevention is the diff itself plus
this record.
