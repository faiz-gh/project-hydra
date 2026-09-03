A working register of everything the supplied measurement material does not answer, cannot answer, or answers inconsistently. Nothing here has been resolved by inference.

Now organised by the chapter each gap affects, so that issues can be linked to the section they block. Gap identifiers from the first version are preserved, so any note you have already made against A1, D3 and so on still resolves. A gap that touches more than one chapter is written out under the chapter where it must be resolved, and cross-referenced from the others.

---

# Index

| ID | Gap | Chapter | Severity |
| --- | --- | --- | --- |
| C1 | Section 3.6 has almost no source material | 3 | Constrains |
| C3 | Four phases must be presented as five | 4 | Constraint, handled |
| D6 | Audit resolution differs between chaos runs | 4 | Reconcile |
| D7 | Phase I run records no database version | 4 | Reconcile |
| A3 | Chapter 5 figures are not in the results data | 5 | Blocking |
| B5 | Row-match corroboration does not cover reads | 5 | Constrains |
| D1 | Three different values for the quorum floor | 6 | Blocking |
| D2 | Little's law agreement reported twice, differently | 6 | Blocking |
| D3 | Unqueued write-latency ratio reported three ways | 6 | Blocking |
| D4 | Matched-utilisation table disagrees with tier table | 6 | Blocking |
| D5 | Deployment A cluster peak differs across files | 6 | Reconcile |
| D8 | Matched-throughput values are interpolated | 6 | Carry qualification |
| B1 | Availability RTO below instrument resolution | 6 | Constrains |
| B2 | No resource-utilisation data exists | 6 | Constrains |
| C2 | No figure for the healed-partition chaos run | 6 | Constrains |
| A1 | The "failed to recover" verdict has no source | 7 | Blocking |
| B3 | Deployment A baseline hardware never captured | 7 | Constrains |
| B4 | Window length confounded with time of day | 7 | Constrains |
| B6 | Two deployments bound the variance loosely | 7 | Constrains |
| B7 | Compared phases ran on different CPU architectures | 7 | Constrains |
| A2 | RQ1 has no quantitative evidence | 8 | Blocking |
| C4 | No cost, scale or alternative-topology data | 8 | Constrains |
| E1 | Three citations cannot be sourced academically | References | Outstanding |
| E2 | Bibliographic detail outstanding on every reference | References | Outstanding |
| E3 | Referencing style not fully confirmed | References | Outstanding |

Severity means: *blocking*, the chapter or section cannot be written correctly without an answer; *reconcile*, two recorded values disagree and one must be chosen; *constrains*, the chapter can be written but a limitation must be carried into the prose; *outstanding*, needed before submission but not before drafting.

---

# Chapter 3. System Design and Architecture

Chapter 3 is drafted. One gap shaped it.

## C1. Section 3.6 has almost no source material

Only three facts about security exist in the supplied material: the cluster runs insecure because the encrypted overlay is its sole network path, replication ports were never exposed publicly, and the repository holds no credentials. No threat model was constructed, no security testing was performed, and no attack surface analysis exists.

**How it was handled:** 3.6 was written at 130 words as a bounded statement of the deployed posture and its rationale, ending with an explicit statement that no evaluation was performed.

**Decision needed:** whether that is acceptable to your supervisor. If a substantive security section is expected, the only honest expansion is external literature on overlay-network threat models, which would need its own citation shopping list. It cannot be expanded with claims about this system.

---

# Chapter 4. Methodology

Chapter 4 is drafted. Two items need reconciliation before Chapter 6 quotes anything related to them.

## C3. Four phases must be presented as five

The target structure separates fault injection from recovery evaluation, but the harness has four phases and the fourth both injects the fault and derives the recovery metrics from the same runs. Five run directories exist: one network, one baseline, one cluster and two chaos runs, the last two being two fault classes of the same phase.

**How it was handled:** 4.1 states plainly that five methodological stages were executed as four measurement phases, and 4.6 states that its quantities derive from the Phase IV runs rather than from further execution. No text implies a fifth execution or a fifth run directory. Recorded here as a standing constraint so it is not reintroduced in Chapters 6 or 7.

## D6. Audit resolution differs between the two chaos runs without explanation

The healed-partition run reports 0.40 s and the abrupt-termination run 0.47 s. Both are described as bounded by the cost of a quorum write, but no reason is recorded for the difference.

**Why it matters here:** 4.6 asserts that the audit cadence is bounded by quorum write cost rather than by a nominal sampling interval. If the two runs differ for another reason, that sentence is wrong and Chapter 6 will inherit the error.

## D7. The Phase I run records no database version

The provenance table lists the CockroachDB version as absent for the network phase while every other phase records v26.3.0.

**Question:** was this because the network probe does not query the database at all? If so, 4.2 should say it rather than leaving a blank in a provenance table.

---

# Chapter 5. Implementation and Engineering Challenges

## A3. The figures Chapter 5 needs are not in the results data — blocking

This gap did not exist when the register was first written. It surfaced while drafting Chapter 3, where the lease-preference bootstrap failure had to be described qualitatively because its magnitudes are recorded in the defect record rather than in the measurement data pack.

Chapter 5 is the second contribution and carries a 1,150-word budget, of which roughly 700 words are the defect argument. That argument depends on showing how flattering the wrong numbers looked, which requires magnitudes. Roughly forty distinct figures appear in the defect record and nowhere in the results data, including: the throughput and latency ratio between the misconfigured and corrected cluster; the twentyfold overstatement of write throughput and twenty-fivefold understatement of write latency under a mismatched key seed; the fifteen-fold block-cache asymmetry between the two phases; the within-sweep drift regression coefficients that ruled out cumulative degradation; the statement-execution and row-match counts; and the row counts in the parser defect signatures.

The standing instruction is that every quantitative claim comes from the measurement data pack. Under that rule, none of the above can be used.

**Three options.**

1. Treat the defect record as a second authoritative source, for Chapter 5 only, on the grounds that its figures were generated from the same run artefacts by the same analysis layer.
2. Write Chapter 5 qualitatively, describing each defect's mechanism and signature without magnitudes. This is possible but weakens the chapter considerably, because "the defective configuration looked better than the correct one" is far less convincing than the ratio that shows it.
3. Confirm a specific subset of figures for use, and only those will appear.

**Recommendation:** option 1. The defect record is not a secondary or remembered source; it is a derived artefact of the same runs. But this is your call, and it is the reason Chapter 5 has not been started.

## B5. The row-match corroboration does not cover reads

Where the statement-statistics view was flushed after a tier ended, the tier was accepted on the strength of the write-latency floor. That evidence is specific to writes, which are twenty per cent of the mix. Reads in those windows were corroborated rather than measured. An unreplicated baseline has no quorum floor at all, so the corroboration is unavailable in Phase II and an uncorroborated flush stays fatal there.

**How it will be handled:** stated as a limit of the validation regime within 5.3, and referenced again in 7.4.

---

# Chapter 6. Results

Chapter 6 has the largest concentration of gaps. Items D1 to D5 must be settled before it is written, because the dissertation must quote one figure consistently, and given what Chapter 5 argues, a reader who catches the paper contradicting itself on a number is a reader who stops trusting the rest of it.

## D1. Three different values for the quorum floor — blocking

The derived floor is stated as 67.054 ms. The network table gives the second-fastest follower a mean round trip of 67.1 ms and a median of 66.9 ms. Elsewhere in the project material the floor appears as both 66.9 ms and 67.1 ms, and two of the figures are captioned 67.1 ms and 67 ms respectively.

**Question:** is 67.054 ms the authoritative derived figure? The Abstract, Chapter 2 and Chapter 4 currently use it. If yes, the figure captions should be regenerated to match.

## D2. Little's law agreement for Phase II at one worker is reported twice, differently — blocking

The per-tier table gives 2.0 per cent. The unqueued comparison section gives 3.0 per cent for what appears to be the same quantity. Both are quoted in support of the headline unqueued comparison, so one must be chosen.

## D3. The unqueued write-latency ratio is reported three times, differently — blocking

The equal-concurrency table gives 50.38x, the unqueued section gives 50.37x, and the reproducibility section gives 50.4x. These presumably differ only by rounding, but the headline range currently reads 33.4x to 50.4x and the Abstract already commits to it.

## D4. The matched-utilisation table disagrees with the tier table — blocking

At 84 per cent utilisation the matched-utilisation table gives Phase II a write median of 1.49 ms at 3,004 ops/s. The Phase II tier table gives 1.48 ms at 3,004.5 ops/s for the two-worker tier. Presumably interpolation, but it should be confirmed, since one of the two will be quoted.

## D5. Deployment A's cluster peak differs across files

The reproducibility table gives 1,791 ops/s. Other project material gives 1,792 ops/s for the same quantity.

## D8. Matched-throughput values are interpolated, not measured

Values at a throughput not measured in a phase are linearly interpolated between bracketing tiers. This qualification appears once in the source but must travel with every matched-throughput figure quoted. Chapter 4 already states it; Chapter 6 must repeat it at the point of use.

## B1. Availability RTO is below the instrument's resolution

Both fault classes produced a measured availability interval shorter than the gap between consecutive audit writes: 0.272 s against 0.40 s resolution, and 0.113 s against 0.47 s. Neither is distinguishable from no interruption at all.

**How it will be handled:** reported as a bound throughout and never as a value. The Abstract and Chapter 4 already reflect this. See also D6.

## B2. No resource-utilisation data exists

Memory utilisation was recorded as a constant zero in every row of the legacy exports, and the disk I/O counter in Phase III is a gateway-local figure rather than a cluster aggregate. Resident-set figures observed around the redeployment were judged inadmissible because they were still climbing within a tier and non-monotonic across tiers.

**Effect:** Chapter 6 will contain no CPU, memory or I/O series. Any claim that the baseline is CPU-bound rests on the shape of the throughput curve and the hardware description, not on a utilisation measurement.

## C2. No figure exists for the healed-partition chaos run

There is a figure for the abrupt-termination run but none for the partition run, although both are reported. Chapter 6 will present the partition case in text and table only, unless a figure can be regenerated from the retained run directory.

---

# Chapter 7. Discussion

## A1. The "failed to recover" verdict has no source — blocking

Section 7.3 of the required structure is titled *Reconciling the "Failed to Recover" Verdict*. Nothing in the supplied material contains such a verdict. Three candidate referents exist, and they would produce three completely different subsections:

- the legacy pipeline's original chaos conclusion, whose reported recovery times were artefacts of a counter advancing at roughly twice wall-clock and were bounded by a detection guard rather than measured;
- the fact that an abrupt-termination fault leaves the node down by design and the harness does not restore it, so an automated verdict of non-recovery would be a property of the protocol rather than of the cluster;
- text in an earlier draft, a supervisor report or a proposal that has not been shared.

**Needed:** which of these, or the verbatim text of the verdict. This subsection will not be drafted from a reconstruction.

## B3. Deployment A's baseline hardware was never captured

The host-hardware capture was added in response to this gap and therefore postdates Deployment A's Phase II run. A live probe of that host before destruction returned matching processor model and total memory, but that observation lives in a session record rather than in a run artefact.

**Effect:** any claim that the two baseline hosts were identical must be qualified. This is the weakest link in the reproducibility argument and 7.4 will say so.

## B4. Measurement-window length is confounded with time of day

The fifteen-second and sixty-second tiers were run in that order, so the further decline observed across the afternoon cannot be attributed to either window length or elapsed time. Recorded as unresolved. Not separable from the retained data.

## B6. Two deployments bound the variance loosely

The replication-cost range rests on two draws. Two observations bound between-deployment variance in the baseline term only loosely, and the underlying cause, why two identically provisioned instances deliver different processor throughput, is a property of the provider's scheduling and is not observable from inside the guest.

## B7. The two compared phases ran on different processor architectures

The baseline and the cluster gateway ran on different CPU models throughout every measurement. The comparison was accepted explicitly rather than silently. The write-latency ratio is the least exposed quantity, because the write path is bounded by a network round trip; throughput and read latency are the most exposed.

**Effect:** a Phase II baseline re-measured on hardware matched to the cluster gateway is the single most valuable measurement that no longer exists. Chapter 8 names it as future work.

---

# Chapter 8. Conclusion and Future Work

## A2. Research question 1 has no quantitative evidence — blocking

RQ1 asks whether the cluster can be provisioned reproducibly and idempotently from a single Terraform codebase. No provisioning time, re-apply result, idempotency check, drift detection or teardown time was recorded anywhere. The only evidence bearing on RQ1 is that the whole protocol was executed twice across a full teardown and redeployment.

**Consequence if unresolved:** RQ1 is answered qualitatively in Chapters 7 and 8 and carries no figure in Chapter 6. Defensible, but weak for a question posed first.

**Needed:** any retained apply logs, timings, or a record of a repeated apply producing no changes. If none exist, confirm and the limitation will be stated explicitly.

## C4. No cost, scale or alternative-topology data

One topology, one workload, one working-set size, one replication factor. No pricing, no scaling beyond five voters, no comparison against a different consensus configuration. All are future work rather than omissions, but Chapter 8 should not promise more than the data supports.

---

# References and front matter

## E1. Three citations cannot be sourced from an academic index

- Terraform primary documentation covering state management and resource lifecycle. Needs URL and access date.
- Tailscale primary documentation describing the coordination server and MagicDNS. No peer-reviewed publication exists; will be cited as vendor documentation and flagged as such in the text. Needs URL and access date.
- A standards definition of Recovery Time Objective and Recovery Point Objective, most likely NIST Special Publication 800-34 Revision 1. Revision number and year require confirmation.

## E2. Bibliographic detail outstanding on every verified reference

Every cited work has been confirmed to exist with its authors, title, venue and DOI, cross-checked against a peer-reviewed academic index. Volume, issue and page numbers were deliberately not filled in, because guessing them is exactly the failure mode this dissertation is about. They should be completed from the PDFs.

One year is genuinely ambiguous: the infrastructure-as-code mapping study carries both a 2018 online-first date and a 2019 issue date.

## E3. Referencing style not fully confirmed

Harvard has been used throughout as instructed. Still to confirm: whether your institution requires access dates on vendor documentation, and whether DOIs should be rendered as bare identifiers or as resolvable links.

---

# What to resolve first

1. **A3**, the Chapter 5 sourcing question. Blocks the next chapter to be written.
2. **A1**, the recovery verdict. Blocks Chapter 7.
3. **D1 to D4**, the numeric inconsistencies. Block Chapter 6.
4. **A2**, provisioning evidence. Determines whether RQ1 gets a result or a limitation.
5. **C1**, the scope of the security section. Determines whether 3.6 is revisited.
6. **E1 to E3**, the outstanding citations. Needed before the final pass, not before drafting.