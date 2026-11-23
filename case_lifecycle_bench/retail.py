"""Seed a lifecycle case from a compatible order in the pinned τ-bench Retail DB."""

import re
from decimal import Decimal
from importlib.metadata import version


def load_retail_tools():
    from tau2.domains.retail.data_model import get_db
    from tau2.domains.retail.tools import RetailTools

    if version("tau2") != "1.0.1":
        raise ValueError("Retail fixtures require tau2 v1.0.1")
    return RetailTools(get_db())


def retail_case(case: dict, order_id: str, *, tools=None) -> dict:
    if tools is None:
        tools = load_retail_tools()
    if order_id not in tools.db.orders:
        raise ValueError(f"Unknown τ-bench Retail order: {order_id}")
    order = tools.get_order_details(order_id)
    init = case["init"]
    status = init.get("order_status", "delivered")
    if order.status != status:
        raise ValueError(f"Case requires a {status} Retail order, got {order.status}")
    if not order.payment_history or any(
        payment.transaction_type != "payment" for payment in order.payment_history
    ):
        raise ValueError("Case requires an order with payments and no prior refunds")
    amount = sum(Decimal(str(payment.amount)) for payment in order.payment_history)
    if amount <= 0:
        raise ValueError("Case requires a positive amount paid")

    names = {init["user"]: order.user_id, init["order"]: order.order_id}
    tokens = re.compile(r"(?<!\w)(?:" + "|".join(map(re.escape, names)) + r")(?!\w)")

    def bind(value):
        if isinstance(value, str):
            return tokens.sub(lambda match: names[match[0]], value)
        if isinstance(value, list):
            return [bind(item) for item in value]
        if isinstance(value, dict):
            return {key: bind(item) for key, item in value.items()}
        return value

    bound = bind(case)
    bound["init"]["amount"] = float(amount)
    bound["case_id"] += "_" + order.order_id.removeprefix("#")
    bound["retail_source"] = {"version": "1.0.1", "order_id": order.order_id}
    # The outcome DSL is for inspection. Evaluators read the numeric init amount.
    original_amount = str(Decimal(str(init["amount"])))
    bound["checks"]["outcome"] = [
        re.sub(r"amount == [\d.]+", f"amount == {amount}", predicate)
        if f"amount == {original_amount}" in predicate
        else predicate
        for predicate in bound["checks"]["outcome"]
    ]
    return bound
