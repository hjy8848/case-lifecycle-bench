"""Credit and field-service workflows built from domain milestones."""

from functools import partial

from .service import Milestone, Workflow, run_oracle
from .servicing.credit_delinquency import DELINQUENCY
from .servicing.repair_parts import REPAIR

APPLICATION = Workflow(
    "credit",
    "credit_application_documents",
    "贷款审批与补件",
    "read_application",
    (
        Milestone(
            "documents_required",
            "request_missing_documents",
            "Notify the customer of the required document through the simulated secure inbox.",
        ),
        Milestone(
            "document_received",
            "acknowledge_document_received",
            "Acknowledge the submission after the application system confirms receipt.",
        ),
        Milestone(
            "decision_ready",
            "notify_application_decision",
            "Send the verified external decision. Never make or change an approval decision. Then close the ticket.",
        ),
    ),
    "我的材料已经补交了，想知道申请办到哪一步了。",
)

WORKFLOWS = {
    "credit-delinquency": DELINQUENCY,
    "credit-application": APPLICATION,
    "field-service-repair": REPAIR,
}
BY_TEMPLATE = {workflow.template: workflow for workflow in WORKFLOWS.values()}


def workflow_agents(workflow):
    if hasattr(workflow, "agents"):  # a card-based servicing template
        return workflow.agents
    agents = {"oracle": partial(run_oracle, workflow)}
    for name, options in (
        ("missed-initial-review", {"skip_initial": True}),
        ("premature-action", {"early": True}),
        ("missed-final-notice", {"skip_notice": True}),
    ):
        agent = partial(run_oracle, workflow, **options)
        agent.__name__ = name.replace("-", "_")
        agents[name] = agent
    return agents
