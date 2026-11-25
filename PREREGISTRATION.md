# Preregistration: Case Ledger ("Debts, Not Diaries")

**Status: DRAFT.** It becomes binding when it is committed together with the frozen benchmark on 12/6 and the
commit hash is written to `PROVENANCE.json`. No ledger group (E2, E3, A1, A2) has been run on the test seeds
(300-309, 390, 391); the guard in `template_run.py` refuses it unless `LEDGER_FROZEN` is set.
Every later change goes into the change log below and says whether it was made after seeing results.

## Method in one paragraph

A stateless agent wakes up on a case and sees a bill of what it owes. Two rules: **keep every promise** and
**earn every credential** (saying "done", closing, or settling needs evidence read after waking up).
The model judges; the ledger only keeps score. A promise leaves the book through four doors: done (a read
from this session after the object's last write), carried (someone will wake it: a scheduled follow-up, a
message sent to the customer, or a push notification), handed_over (a successful handoff receipt), released
(the customer's words or the policy). Three deterministic checks: A reconcile first, B settle with evidence,
C the books must balance at the end of a session (and at ticket close).

## Groups, seeds, repetitions

| Group | What it is |
| --- | --- |
| E0 | CRM only (today's baseline) |
| E1 | Diary: all earlier sessions appended on every wake (`history.FullHistory`) |
| E2 | Plain to-do list: the two bookkeeping tools, a bill with promises only, no evidence, no journal, no checks, no scorekeeper |
| E3 | Case Ledger, all parts |
| E4 | Single session reference (T3 only) |
| A1 | E3 without "keep every promise": no scorekeeper, no check C, journal not shown (uncertain area still recorded for A) |
| A2 | E3 without "earn every credential": no check A, no check B (settling needs no evidence); the close gate stays |

* Development seeds 400-409 (10 per template, outcomes 5/3/2): the only seeds on which prompts, rejection texts and tool classes may be tuned.
* Test seeds: the frozen 240 (300-309 plus 390, 391 per template). Ablation subset: per template the lowest seed of each outcome (3 per template, 60 total).
* E0-E3: 3 runs on all 240, Poll and Push. A1, A2: 3 runs on the 60-instance subset. E4: 3 runs on T3. Main model: the current Qwen; a second model (E0, E2, E3 only) once chosen.
* Customer: the simulated (LLM) customer for every group.

## Predictions (fixed before any ledger run on the test seeds)

1. **Debts beat diaries.** E3 responsibility rate > E1, and E3 carries fewer than half of E1's memory tokens per wake.
2. **Ledger beats list.** E3 > E2, paired McNemar test, p < 0.05, per instance with the majority of 3 runs.
3. **One column per rule.** A1 is worse than E3 mainly on omissions; A2 mainly on premature actions and false completions. Duplicates sit where the columns cross and are reported on their own. Test: difference of differences, (A1's omission failure increase minus A1's premature-plus-false-completion increase) against the same quantity for A2, opposite signs expected; 95% interval by instance bootstrap.
4. **The gain is where expected.** E3 minus E0 is larger for the "keep waiting" outcome than for the other two, larger in Poll than in Push, and timeout instances get clearly worse under both A1 and A2.
5. **Negative control.** For business-judgement checks the E3 minus E0 difference in failure rate has a 95% interval that contains 0.

Any prediction that does not hold is reported as it is, not reworded. If 2 fails, the finding is that writing the promises down is already enough.

## Numbers to report

Responsibility rate (primary), failure rate of each check column, wrongful-closure rate, harmful and procedural unsafe-action rates, split by terminal outcome and wake mode; memory tokens per wake (bill or history); extra calls and tokens for the scorekeeper and for rejections.

## Check classes

The classes follow the check definitions only, never any ledger-group result. `scripts/check_classes.py` verifies
that every check of the benchmark sits in exactly one class. Principles (fixed by the lead on 11/25):

* Triggered by "the time has come" is **timing**; triggered by a state or a count is **business judgement**;
  contact rules (window, frequency, authorisation) are business judgement.
* A check that can be wrong in both directions (too early or too late; must do but must not overdo) goes to the
  **two-way** class. It is reported on its own and is not part of the separation test of prediction 3.
* False completion is measured mainly by the wrongful-closure rate.

| Class | Checks | Role |
| --- | --- | --- |
| Omission | 21 | A1 should hurt here |
| Omission, told | 21 | reported separately (expected effect small) |
| Premature | 9 | A2 should hurt here |
| Two-way | 8 | reported separately, outside prediction 3 |
| Duplicate | 5 | sits where the columns cross, reported on its own |
| False completion | 2 (plus the wrongful-closure rate) | A2 should hurt here |
| Business judgement | 31 | the negative control (prediction 5) |

The 10 checks of the older stage-based T3 evaluation that were not in the first list of 87 are classified as the lead
decided: `commitments_kept`, `unexpired_followup_at_deadline` omission; `customer_informed_of_failure`,
`customer_informed_of_pending_status`, `notified_after_interception` omission-told; `closed_after_refund_notification`,
`ticket_kept_open` false completion; `single_handoff` duplicate; `escalated_after_failure`, `no_unneeded_escalation`
business judgement (triggered by a state). `no_procedural_violation` and `handed_over_at_most_once` (added by the
pre-freeze revisions) and the P7 check `human_request_honoured` are reported on their own.

Prediction 3 uses omission versus premature plus false completion only.

## Change log

| Date | Change | After seeing ledger results? |
| --- | --- | --- |
| 11/25 | Draft written | no |
| 11/25 | Object ids generalised to every `_id` argument except user, customer, contact and task ids; after-contact work added for every group; `told_ref` optional; blocked calls are not writes; check classes revised by the lead | no |
