"""Initial state and synthetic orders for the Retail lifecycle cases."""

import re


def initial_state(init: dict, timestamp: str) -> dict:
    """Create the Retail world from the fixture's order and fulfillment state."""
    user_id = init["user"]
    order_id = init["order"]
    order = {
        "order_id": order_id,
        "user_id": user_id,
        "amount": init["amount"],
        "status": init.get("order_status", "delivered"),
    }
    if "refund_eligibility" in init:
        order["refund_eligibility"] = init["refund_eligibility"]
    state = {
        "users": {user_id: {"user_id": user_id}},
        "orders": {order_id: order},
        "products": {},
        "tickets": {},
        "refunds": {},
        "shipments": {},
        "fulfillment": {},
        "payment_ledger": {},
        "followups": {},
        "outbox": {},
        "handoffs": {},
    }
    for patch in init.get("db_patch", []):
        _patch_fulfillment(state, patch, timestamp)
    if state["fulfillment"] and "order_status" not in init:
        order["status"] = "pending"
    return state


def _patch_fulfillment(state: dict, patch: str, timestamp: str) -> None:
    match = re.fullmatch(
        r"fulfillment\[([^\[\]]+)\]\.status\s*=\s*(pending|picking|shipped|cancelled)",
        patch,
    )
    if not match or match[1] not in state["orders"]:
        raise ValueError(f"Unsupported initial DB patch: {patch!r}")
    order_id, status = match.groups()
    state["fulfillment"][order_id] = {
        "order_id": order_id,
        "status": status,
        "cancel_synced": status == "cancelled",
    }
    if status == "shipped":
        shipment_id = f"SH{len(state['shipments']) + 1}"
        state["shipments"][shipment_id] = {
            "shipment_id": shipment_id,
            "order_id": order_id,
            "status": "in_transit",
            "events": [{"t": timestamp, "status": "in_transit"}],
        }


def retail_t3_case(order_index: int = 1) -> dict:
    """Build a fresh, locally synthesized T3 case without an upstream order."""
    from ..cases.t3 import t3_case

    if type(order_index) is not int or order_index < 1:
        raise ValueError("Synthetic Retail order index must be a positive integer")
    case = t3_case()
    order_id = f"#CLB-T3-{order_index:03d}"
    user_id = f"clb_t3_customer_{order_index:03d}"
    names = {case["init"]["order"]: order_id, case["init"]["user"]: user_id}
    tokens = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, names)) + r")(?!\w)")

    def bind(value):
        if isinstance(value, str):
            return tokens.sub(lambda match: names[match[0]], value)
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        return value

    case = bind(case)
    case["case_id"] = f"{case['template']}_synthetic_{order_index:03d}"
    case["synthetic_source"] = {"industry": "retail", "order_id": order_id}
    return case
