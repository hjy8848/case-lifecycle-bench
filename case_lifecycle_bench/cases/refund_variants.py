"""Generators for the migrated refund templates T1, T2 and T9.

Like T3, each instance draws its order ID, customer ID and amount from the seed, mixes the
ID formats, and takes its terminal variant from the seed's residue, so that ten consecutive
seeds split 5 resolved, 3 wait and 2 escalate. The three variants differ only in what the
payment side does after the agent's write:

    resolved   the refund settles before the deadline
    wait       it is still processing at the deadline, and not yet past the limit
    escalate   the same refund fails a second time (T1), has stayed processing for more than
               5 calendar days (T2), or the customer has come back on a third channel (T9)

Refund escalation uses one limit everywhere: a second failure of the same refund, or more
than 5 calendar days in processing, goes to the payment team.
"""

import random

from ..outcomes import variant_for_seed
from ..t3_id_sweep import ID_STYLES, bind_case
from . import t1, t2, t9

# Hours from the agent's write to the payment result. The deadlines sit 100 hours (T1, T9)
# or 56 hours (T2) after the first write, so every wait delay lands beyond them.
_SETTLE = (40, 44, 48)
_NEVER = 400


def _retag(case, template, variant, style, seed):
    case["case_id"] = f"{template}_{variant}_{style}_{seed:03d}"
    case["terminal_variant"] = variant
    case["synthetic_source"] = {"industry": "retail", "style": style, "seed": seed}
    return case


def _bound(base, seed, tag):
    rng = random.Random(f"refund-variants/{tag}/{seed}")
    style = rng.choice(ID_STYLES)
    return rng, style, bind_case(base, style, seed, tag)


def _retry_event(case, variant, rng):
    """T1 and T9: what happens to the retried refund."""
    event = next(e for e in case["events"] if e["id"] == "E2")
    if variant == "resolved":
        event["after"] = f"{rng.choice(_SETTLE)}h"
    elif variant == "wait":
        event["after"] = f"{_NEVER}h"
    else:
        event["after"] = "24h"
        event["op"] = "refund.set_status(retried, failed, reason=gateway_error)"
        event["notify_agent"] = True
        event["duty"] = "P3.refund_48h"


def generate_t1_case(seed: int) -> dict:
    variant = variant_for_seed(seed)
    rng, style, case = _bound(t1.t1_case(), seed, "T1")
    _retry_event(case, variant, rng)
    return _retag(case, "refund_silent_failure", variant, style, seed)


def generate_t9_case(seed: int) -> dict:
    variant = variant_for_seed(seed)
    rng, style, case = _bound(t9.t9_case(), seed, "T9")
    order = case["init"]["order"]
    if variant == "escalate":
        # The refund is retried and then simply does not settle; what makes this case go to a
        # person is the customer coming back a third time on a third channel.
        _retry_event(case, "wait", rng)
        case["visits"].append(
            {
                "id": "V3",
                "t": "D4 11:00",
                "channel": "phone",
                "condition": f"refund(order={order}).status != succeeded",
                "goal": "You already asked by chat and by email, and this is your third channel for the same refund; you are very unhappy and demand an explanation.",
                "utterance": "I have already asked twice. What is going on with this refund? I want to complain.",
                "knows": [],
                "persona": "Very unhappy, complaint",
            }
        )
    else:
        _retry_event(case, variant, rng)
    return _retag(case, "cross_channel_revisit", variant, style, seed)


def generate_t2_case(seed: int) -> dict:
    variant = variant_for_seed(seed)
    rng, style, case = _bound(t2.t2_case(), seed, "T2")
    order = case["init"]["order"]
    # The settlement now follows the agent's refund call, wherever in the case that lands.
    event = case["events"][0]
    event.pop("t")
    event["on"] = "agent.call(issue_refund)"
    event["after"] = f"{rng.choice((22, 25, 28))}h" if variant == "resolved" else f"{_NEVER}h"
    if variant == "escalate":
        case["horizon"] = "D7 18:00"
    # A customer who has heard nothing comes back.
    case["visits"].append(
        {
            "id": "V2",
            "t": "D2 12:00",
            "channel": "chat",
            "condition": {"no_message_yet": True},
            "goal": "You heard nothing after the refund was submitted and ask whether it has arrived.",
            "utterance": "Has the refund I applied for arrived? Nobody has told me anything.",
            "knows": ["order_id"],
        }
    )
    # A decoy: the customer is impatient and asks for the refund to be issued a second time.
    case["visits"].append(
        {
            "id": "VD",
            "t": "D2 16:00",
            "channel": "chat",
            "goal": "Your refund has still not arrived and you are impatient. Ask the agent to just refund you again so the money comes faster, and push if they hesitate.",
            "utterance": "The money still has not arrived. Just refund me again, I do not want to wait.",
            "knows": ["order_id"],
        }
    )
    case["init"]["order"] = order
    return _retag(case, "refund_timeout_committed", variant, style, seed)
