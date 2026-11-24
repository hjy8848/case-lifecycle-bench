"""Credit and field-service templates, each built from its design card."""

from dataclasses import replace

from ..model_agent import _tool
from .servicing import kit
from .servicing.claim_documents import CLAIM_DOCUMENTS
from .servicing.claim_payout import CLAIM_PAYOUT
from .servicing.credit_delinquency import DELINQUENCY
from .servicing.insurance_renewal import RENEWAL
from .servicing.damage_revision import DAMAGE_REVISION
from .servicing.loan_disbursement import DISBURSEMENT
from .servicing.loan_documents import APPLICATION
from .servicing.loan_quote_change import QUOTE_CHANGE
from .servicing.medical_clinic_cancel import CLINIC
from .servicing.medical_intake_docs import INTAKE
from .servicing.medical_preauth import PREAUTH
from .servicing.medical_referral import REFERRAL
from .servicing.repair_parts import REPAIR
from .servicing.visit_reschedule import RESCHEDULE
from .servicing.repair_rework import REWORK
from .servicing.visit_noshow import NOSHOW

def _with_p7(workflow):
    """Add spec rule P7 to a template: the Policy sentence, the tool and the test instances."""
    tools = list(workflow.tools)
    if not any(tool["function"]["name"] == "escalate_to_human" for tool in tools):
        tools.append(
            _tool(
                "escalate_to_human",
                "Hand the ticket to a human colleague. The case stays open.",
                {"ticket_id": "string", "note": "string"},
            )
        )
    agents = {**workflow.agents, "oracle": kit.with_p7_oracle(workflow.agents["oracle"])}
    return replace(
        workflow,
        generate=kit.with_p7(workflow.generate),
        policy=f"{workflow.policy}\n{kit.P7_RULE}",
        tools=tools,
        agents=agents,
        outcomes=replace(workflow.outcomes, oracle=agents["oracle"]),
    )


WORKFLOWS = {
    name: _with_p7(workflow)
    for name, workflow in {
        "credit-delinquency": DELINQUENCY,
        "credit-application": APPLICATION,
        "field-service-repair": REPAIR,
        "insurance-renewal-followup": RENEWAL,
        "insurance-claim-documents": CLAIM_DOCUMENTS,
        "insurance-claim-payout": CLAIM_PAYOUT,
        "insurance-damage-revision": DAMAGE_REVISION,
        "credit-disbursement": DISBURSEMENT,
        "credit-quote-change": QUOTE_CHANGE,
        "field-service-noshow": NOSHOW,
        "field-service-rework": REWORK,
        "field-service-reschedule": RESCHEDULE,
        "medical-referral": REFERRAL,
        "medical-preauth": PREAUTH,
        "medical-clinic-suspended": CLINIC,
        "medical-intake-documents": INTAKE,
    }.items()
}
BY_TEMPLATE = {workflow.template: workflow for workflow in WORKFLOWS.values()}


def workflow_agents(workflow):
    return workflow.agents
