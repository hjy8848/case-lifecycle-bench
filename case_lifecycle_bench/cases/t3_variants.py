"""Generate T3 instances whose terminal variant follows from one key event's timing.

The three variants share the same seeded construction and differ only in what the
carrier does after the interception request:

    resolved   the parcel is stopped within the first two days and the refund
               settles 48 hours after it is issued, both before the deadline
    wait       the carrier result arrives after the deadline, so the interception
               is still pending when the evaluation ends
    escalate   the carrier reports the parcel delivered, the interception failed

Order and customer IDs, the amount and the event delay are drawn from the seed.
"""

import random

from ..outcomes import variant_for_seed

_STYLES = ("w7", "numeric", "clb")
# Hours from the interception request to the carrier result. The deadline is
# 104 hours after the request, so every wait delay lands beyond it.
_DELAYS = {
    "resolved": (20, 26, 30, 36, 44),
    "wait": (118, 126, 132, 140, 150),
    "escalate": (20, 26, 30, 36),
}


def generate_t3_case(seed: int) -> dict:
    """Return the instance for one seed, its variant set by the seed's residue."""
    from ..t3_id_sweep import sweep_case

    variant = variant_for_seed(seed)
    rng = random.Random(f"t3-variants/{seed}")
    style = rng.choice(_STYLES)
    delay = rng.choice(_DELAYS[variant])
    case = sweep_case(style, seed)
    order_id = case["init"]["order"]
    case["case_id"] = f"cancel_partial_success_{variant}_{style}_{seed:03d}"
    case["terminal_variant"] = variant
    event = {
        "id": "E1",
        "on": f"agent.call(cancel_fulfillment, {order_id})",
        "after": f"{delay}h",
        "op": f"shipment.set_status({order_id}, "
        f"{'delivered' if variant == 'escalate' else 'intercepted'})",
        "notify_agent": True,
        "duty": "P3.intercept_24h",
    }
    case["events"] = [event]
    if variant == "resolved":
        case["events"].append(
            {
                "id": "E2",
                "on": "agent.call(issue_refund)",
                "after": "48h",
                "op": "refund.set_status(issued, succeeded)",
                "notify_agent": True,
                "duty": "P3.refund_48h",
            }
        )
    return case
