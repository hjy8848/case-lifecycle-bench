import unittest

from case_lifecycle_bench.cases import t3
from case_lifecycle_bench.cases.t9 import oracle as t9_oracle
from case_lifecycle_bench.cases.t9 import t9_case
from case_lifecycle_bench.environment import run_case
from case_lifecycle_bench.evaluation import evaluate_case


def read_fulfillment_first(session, call):
    """Run the T3 Oracle but cancel in OMS only after reading fulfillment."""
    held = []

    def reordered(tool, **args):
        if tool == "cancel_pending_order":
            held.append(args)
            return None
        result = call(tool, **args)
        if tool == "get_fulfillment_status":
            for pending in held:
                call("cancel_pending_order", **pending)
            held.clear()
        return result

    return t3.oracle(session, reordered)


def refund_half(session, call):
    """Run the T3 Oracle but refund only half of the order amount."""

    def short(tool, **args):
        if tool == "issue_refund":
            args["amount"] = args["amount"] / 2
        return call(tool, **args)

    return t3.oracle(session, short)


def continue_by_search_and_note(session, call):
    """Handle the T9 revisit with a customer search and a note on the same ticket."""
    if session["trigger_type"] != "user_visit" or session["trigger_id"] != "V2":
        return t9_oracle(session, call)

    original = call("search_tickets", user_id=session["user_id"])[0]
    call(
        "add_ticket_note",
        ticket_id=original["ticket_id"],
        text="The customer asked by email about the refund progress.",
    )
    call("say", text="The refund is still processing; I updated the original ticket.")
    return "LEFT"


class T3ScoringTest(unittest.TestCase):
    def test_reading_fulfillment_before_the_oms_cancellation_passes_stage_one(self):
        for wake in ("poll", "push"):
            with self.subTest(wake=wake):
                case = t3.t3_case()
                result = evaluate_case(
                    case, run_case(case, read_fulfillment_first, wake=wake)
                )

                first = next(
                    stage
                    for stage in result["stages"].values()
                    if stage["trigger_id"] == "V1"
                )
                self.assertTrue(first["passed"])
                self.assertTrue(result["metrics"]["case_resolution"])

    def test_oracle_refunds_the_order_amount(self):
        case = t3.t3_case()
        result = evaluate_case(case, run_case(case, t3.oracle))

        self.assertTrue(result["outcome"]["refund_matches_order_amount"])
        self.assertTrue(result["metrics"]["case_resolution"])

    def test_partial_refund_does_not_resolve_the_case(self):
        case = t3.t3_case()
        result = evaluate_case(case, run_case(case, refund_half))

        self.assertTrue(result["outcome"]["single_processing_or_successful_refund"])
        self.assertFalse(result["outcome"]["refund_matches_order_amount"])
        self.assertFalse(result["metrics"]["case_resolution"])


class T9ReuseScoringTest(unittest.TestCase):
    def test_noting_the_original_ticket_found_by_search_counts_as_reuse(self):
        for wake in ("poll", "push"):
            with self.subTest(wake=wake):
                case = t9_case()
                result = evaluate_case(
                    case, run_case(case, continue_by_search_and_note, wake=wake)
                )

                self.assertTrue(result["process"]["reused_original_ticket_on_revisit"])
                self.assertTrue(result["process"]["no_duplicate_ticket_on_revisit"])
                self.assertEqual(result["metrics"]["duplicate_ticket_rate"], 0)


if __name__ == "__main__":
    unittest.main()
