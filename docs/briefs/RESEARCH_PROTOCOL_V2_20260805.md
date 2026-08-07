# RESEARCH PROTOCOL v2 — separating discovery from adjudication
**Proposed amendment to STANDING_ANALYSIS_SPEC. Origin: the 2026-08-05 parameter audit, which retracted six findings and reversed two, and the architecture review that followed it. Status: PROPOSED — needs Michael's ratification alongside the M2 report and the M3 ledger amendment.**

## The thesis this protocol encodes

> A model's ability to produce a convincing explanation must never substitute for a statistical protocol that decides whether the explanation is allowed to become a finding.

The 2026-08-03 milestone (M2) was methodologically sound and killed both of its predecessor's headline hypotheses. The 2026-08-05 ad-hoc analyses failed — not because the model was weak, but because exploratory pattern-hunting was reported in the *confirmatory* system's vocabulary ("Suspected", n-counts, ledger references), borrowing credibility earned by a protocol it wasn't following. v2 makes that borrowing structurally impossible.

---

## 1. Status ladder (replaces `Suspected → Proven`)

`Suspected` was doing too much work — it covered both "a model noticed something" and "this passed a real test."

| Status | Meaning | Who may assign it |
|---|---|---|
| **Exploratory** | A model noticed something. No claim of validity. | Any model, freely |
| **Candidate** | Passed mechanical validity screening (§3) — not yet evidence | Automated screen |
| **Frozen** | Test specification locked (§4). Occupies an OOS slot (§6) | Owner, on slot availability |
| **OOS** | Tested on data that did not exist when frozen | Statistical engine |
| **Proven** | Met pre-declared graduation criteria | Protocol only |
| **Rejected** | Failed its own pre-declared criteria | Protocol only |
| **Retracted** | Logical or statistical flaw found after reporting | Anyone, immediately |

**Vocabulary rule:** Exploratory output may say *"repeat-firing may predict better outcomes."* It may never say *"repeat-firing is predictive."* Exploratory findings are reported without n-counts, p-values, or effect sizes in the headline — those belong to tested claims. Numbers may appear in the body, labeled EXPLORATORY.

---

## 2. Seats, not vendors

The failure mode is role-based, not model-based. Assigning vendors to roles adds a dependency without adding rigor.

| Seat | Job | Constraint |
|---|---|---|
| **Generator** | Propose hypotheses, identify confounders, design the test, write the code | May not declare any hypothesis supported |
| **Adjudicator** | Execute frozen tests | Deterministic code only. No model in this seat, ever |
| **Adversary** | Attempt to falsify the surviving result | **Must be a different model than the Generator, and must NOT see the Generator's reasoning** — only the claim, the method, and the data |
| **Interpreter** | Explain what the statistical result means | Runs only after the Adjudicator has produced a result |

**The two hard rules:**
1. The model that generated a hypothesis may never judge it.
2. The Adversary sees claim + method + data, never the Generator's rationale. A persuasive rationale is the contaminant — it is precisely what made R1–R6 sound reasonable.

Cross-vendor diversity is valuable because models sharing training priors share blind spots — *not* because any vendor is inherently better at generating or attacking. Vendor assignment is deliberately unspecified.

---

## 3. Mechanical validity screen (automated gate, runs before interpretation)

**Prerequisite — variable provenance tagging (data-layer change, highest leverage item in this document).** Every logged variable carries two tags at write time:

- `measured_at`: `pre_entry` | `at_entry` | `intra_trade` | `at_exit` | `post_exit`
- `derives_from`: list of source fields

With those two tags, the following become computable checks rather than judgment calls:

```
PREDICTOR + OUTCOME
   ↓
1. Temporal ordering: is predictor.measured_at strictly before the outcome window opens?
   NO → REJECT (predictor is contemporaneous or downstream)
   ↓
2. Derivation overlap: does outcome.derives_from intersect predictor.derives_from?
   YES → REJECT or recompute against the INCREMENTAL outcome
   ↓
3. Consequence test: is predictor.measured_at in {intra_trade, at_exit}?
   YES → predictor is a consequence or intermediate, NOT a predictor. Reclassify.
   ↓
4. Independence unit: declare it (trade / signal / symbol / day / formation-date / regime)
   → compute raw N and cluster N
   ↓
5. Overlap: do observation windows overlap across units?
   YES → report effective N; if uncomputable, report UNKNOWN
   ↓
CANDIDATE
```

**Worked examples from the audit:**
- *"Reached +2% by day 7" → "return at day 21"* fails step 2 — the predictor is contained in the outcome. Correct handling: incremental day7→day21 (which reversed the finding: fast +0.70% vs rest +1.45%).
- *`hold_days` → `pl_pct`* fails step 3 — `hold_days` is `intra_trade`, so it is a consequence of the exit, not a predictor. **This is the unresolved S4 question, and it self-answers under this screen.**
- *Synth band excess return* fails step 5 — 14 formation dates with 5-day overlapping forward windows.

---

## 4. Freeze (the missing bridge between exploration and validation)

Once a hypothesis reaches Candidate and the owner allocates a slot, **freeze** the following. None may change afterward for any reason connected to a result:

predictor · outcome · horizon · population definition (incl. dedup rule) · exclusions · cutoff/threshold · statistical test · comparison group · minimum n · graduation criterion · **falsification criterion** (what result would kill it)

The frozen spec is hashed and stored. Only the frozen version is validated. Any modification restarts the hypothesis at Exploratory.

**Model-cannot-promote-its-own-discovery rule.** A model may say *"prior-fire count might matter."* It may **not** select the cutoff and then test it. The protocol supplies the pre-specified partition (0 / 1 / 2 / 3 / 4 / 5+). A data-derived cutoff (e.g. the "7+" that failed at p=0.068) is a *separate* hypothesis entering the pipeline at Exploratory with its search cost declared.

---

## 5. Counterfactual specification (required before any strategy comparison)

R2 reversed because the counterfactual was never specified. Before comparing strategy A to B, declare:

what happens to the original position · when the alternative action occurs · where the capital goes · whether another signal is guaranteed to exist · transaction costs · what happens if no signal exists · whether simultaneous/correlated positions are permitted · what idle cash earns

Only then compute. R2's original comparison ("ride" vs "take +2%") was invalid because the positions were up **+9.47%** at day 7 — the alternative action was mis-specified. Corrected, take-and-redeploy won by ~2.2pp.

---

## 6. Budgets — the constraints that actually bind at this data volume

### 6a. OOS slot budget (NEW — the binding constraint)
This system produces **~38 new closed trades per milestone**. OOS data is a scarce, non-renewable resource and every frozen hypothesis consumes it.

- **At most k = 3 hypotheses in FROZEN state at any time**, ranked by prior plausibility.
- Every frozen hypothesis carries an **expiry**: unresolved after N milestones (default 3) → **Rejected by default**, not carried.
- Rationale: M1's velocity hypothesis survived on nothing for three months because carrying was free. Carrying must cost a slot.

### 6b. Session-level discovery budget
Per-test corrections do not address reusing the same 125 trades / 193 signals across dozens of tests in one session. Every session ledgers:

```
hypotheses generated · tests run · thresholds searched · variables examined
confirmatory hypotheses · previously-unseen data? YES/NO
```

and the report states plainly: *"This session examined N candidate variables across M tests."* That disclosure accompanies every finding from the session — including survivors.

### 6c. Expected yield (calibration, so the protocol isn't mistaken for broken)
At ~38 new trades per milestone under this discipline, expect **1–2 graduations per year**. That is the correct rate for the available evidence. M2's own line stands as the design goal, not a defect:

> *"The standing analysis is currently better at killing hypotheses than at confirming them — at n≈38 new trades per milestone that is correct and expected behaviour."*

The objective is not to maximize interesting patterns. It is to maximize **hypotheses that survive a genuine attempt to kill them.**

---

## 7. The nine standing rules (consolidated)

1. **Sweep every threshold; report the range, not the best value.**
2. **Permutation-test every search-derived cutoff before reporting it at all.**
3. **Mechanical-overlap check on every predictor→outcome pair** (automated via §3 tags).
4. **Multiple-comparison correction whenever >3 variants are tested.**
5. **Declare the independence unit; report raw N and cluster/effective N.** A model may not describe raw N as a "strong sample" when effective N is unknown.
6. **Freeze the full test specification before validation** (§4).
7. **Session-level discovery budget, disclosed with every finding** (§6b).
8. **The population definition is a parameter — sweep it too.** The 21-day dedup window moved n from 792→129 and the effect size 3×; every finding must report stability across population definitions, not only across thresholds within one.
9. **OOS slot budget with expiry** (§6a).

---

## 8. Enforcement — why instructions are insufficient

A constitution that says *"say UNRESOLVED rather than infer"* will not hold; models infer under pressure. Enforcement is structural:

**The finding output schema requires fields that only a computation can populate** — `effective_n`, `search_count`, `overlap_verdict`, `independence_unit`, `session_test_count`. A finding with any of these empty **cannot be reported**. The gate is the schema, not the instruction.

Recommended standing preamble for any model in the Generator seat:

> You are an exploratory research assistant, not the authority that determines whether a hypothesis is true. You may discover hypotheses; you may not promote an exploratory observation to a finding. Before interpreting any predictor/outcome relationship, establish: temporal ordering · mechanical overlap · whether the predictor is downstream of the outcome · the independent sampling unit · clustering/overlap · raw N and effective N · every search used to reach the result · pre-specified vs data-derived thresholds · multiple-comparison exposure · the counterfactual · what evidence would falsify the hypothesis. If any item cannot be established from available data, output **UNRESOLVED** rather than inferring it.

---

## 9. Adoption path

1. Michael ratifies v2 alongside the M2 report and M3 ledger amendment.
2. Dell brief: variable provenance tags (§3 prerequisite) — this is the enabling data-layer change and should ship first; it also composes with the pending daily-path join.
3. Re-classify all 2026-08-05 findings onto the v2 ladder: the six retractions → **Retracted**; the four survivors → **Exploratory** (none has been frozen or OOS-tested, so none may hold a stronger status — including the four that survived the audit).
4. M3 runs under v2 with a maximum of three frozen hypotheses. Current best candidates for those slots, by prior plausibility: the M2 oversold-bounce signature (closest to graduation, control arm 3 trades short), the repeat-fire effect (survived every population definition), and the consensus-loved-lags effect (survived every binning — but see §3 step 5, its effective N is unresolved and must be computed before it can occupy a slot).
