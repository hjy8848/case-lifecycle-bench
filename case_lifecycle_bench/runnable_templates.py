"""Every template the real-model runner can run: the card templates and the retail ones."""

from types import SimpleNamespace

from .cases.refund_variants import generate_t1_case, generate_t2_case, generate_t9_case
from .cases.t3_variants import generate_t3_case
from .industries.service_cases import WORKFLOWS
from .model_agent import POLICY, TOOLS

# The retail templates share one Policy and one tool set, and have no outbound calls and no
# P7 instances. They run with the simulated customer in chat visits like everything else.
RETAIL = {
    name: SimpleNamespace(template=template, generate=generate, policy=POLICY, tools=TOOLS)
    for name, template, generate in (
        ("refund-silent-failure", "refund_silent_failure", generate_t1_case),
        ("refund-timeout-committed", "refund_timeout_committed", generate_t2_case),
        ("cross-channel-revisit", "cross_channel_revisit", generate_t9_case),
        ("cancel-partial-success", "cancel_partial_success", generate_t3_case),
    )
}
ALL = {**WORKFLOWS, **RETAIL}
BY_TEMPLATE_NAME = {workflow.template: workflow for workflow in ALL.values()}