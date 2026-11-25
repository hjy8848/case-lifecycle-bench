"""Draft classification of the benchmark's process checks into the Case Ledger's failure columns.

The classes follow the check definitions only, never any ledger-group result. Run this file to verify
that every check in the benchmark sits in exactly one class and that none is missing:

    python scripts/check_classes.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CLASSES = {
    "omission": """initial_followup_scheduled followup_after_each_write followup_pending_while_waiting
        pending_intercept_rechecks_scheduled commitments_monitored commitments_recorded followup_at_agreed_time
        proactive_contact_by_deadline proactive_contact_within_a_day specialist_chased_when_due chased_when_allowed
        recontacted_when_not_confirmed unreached_patient_contacted_once_more reminded_while_photos_missing
        confirmation_asked_after_rework checklist_sent_at_intake secure_link_sent_at_intake payout_started_at_intake
        disbursement_started_at_intake""",
    "omission_told": """customer_informed_while_waiting customer_informed_of_escalation customer_told_of_escalation
        customer_told_of_suspension customer_told_what_is_booked customer_told_while_clearing failure_told_to_customer
        miss_told_to_customer revision_told_to_customer change_told_to_customer patient_notified_after_suspension
        patient_told_of_escalation patient_told_of_handoff rejection_told_to_patient rejection_relayed
        requirement_relayed mismatch_raised_with_customer lapse_warned_before_handoff""",
    "premature": """no_refund_before_interception no_exam_before_approval no_unapproved_booking_or_early_chase
        no_unready_booking_attempt fulfillment_checked_before_notification original_kept_until_confirmed
        rescheduled_only_as_confirmed fee_agreed_before_change not_pressing_a_customer_who_needs_time
        compliant_contact compliant_reminders_and_contact""",
    "timing_both_ways": """escalation_matches_deadline escalation_matches_overdue escalation_matches_silence
        escalation_matches_expiry""",
    "duplicate": """no_duplicate_refund no_duplicate_or_unauthorised_payment no_duplicate_or_unverified_change
        single_ticket_for_the_case reminded_once_per_broken_promise""",
    "false_completion": """closure_matches_state customer_notified_after_verification""",
    "business_judgement": """identity_before_discussion compensation_within_limit options_matched_to_deadline
        reorder_matches_mismatch slots_chosen_by_customer slots_chosen_by_patient appeal_matches_state
        billing_advisor_matches_state complaint_matches_state suspension_matches_state sensitive_info_not_in_ticket
        symptom_report_escalated alternatives_offered_to_the_patient doctor_note_requested free_repair_on_original_order
        retry_matches_failure voucher_matches_second_miss info_collected_before_dispatch compliant_settlement
        compliant_signing compliant_actions renewal_has_current_consent third_change_goes_to_human
        escalation_matches_limit escalation_matches_second_miss escalation_matches_broken_promises
        escalation_matches_confirmation escalation_matches_state""",
}
CLASSES = {name: text.split() for name, text in CLASSES.items()}
# Checks that exist in code but are not process checks of a lifecycle template (reported, not classified).
OUTSIDE = {"human_request_honoured", "no_procedural_violation", "handed_over_at_most_once"}


def checks_in_code():
    from case_lifecycle_bench.environment import run_case
    from case_lifecycle_bench.evaluation import evaluate_case
    from case_lifecycle_bench.template_check import TEMPLATES

    found = {}
    for name, spec in TEMPLATES.items():
        for seed in range(300, 310):
            case = spec.generate(seed)
            evaluation = evaluate_case(case, run_case(case, spec.oracle, wake="poll"))
            for key in evaluation["process"]:
                found.setdefault(key, set()).add(name)
        for key in spec.oracle_process_checks:
            found.setdefault(key, set()).add(name)
    return found


def main():
    flat = [check for names in CLASSES.values() for check in names]
    duplicated = sorted({c for c in flat if flat.count(c) > 1})
    code = checks_in_code()
    in_code = set(code) - OUTSIDE
    print(f"classified: {len(flat)} ({len(set(flat))} distinct); in code: {len(in_code)}")
    print("in two classes:", duplicated or "none")
    print("in code but not classified:", sorted(in_code - set(flat)) or "none")
    print("classified but not in code:", sorted(set(flat) - in_code) or "none")
    for name, names in CLASSES.items():
        print(f"  {name}: {len(names)}")


if __name__ == "__main__":
    main()
