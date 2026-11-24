"""Credit and field-service templates, each built from its design card."""

from .servicing.claim_documents import CLAIM_DOCUMENTS
from .servicing.claim_payout import CLAIM_PAYOUT
from .servicing.credit_delinquency import DELINQUENCY
from .servicing.insurance_renewal import RENEWAL
from .servicing.damage_revision import DAMAGE_REVISION
from .servicing.loan_disbursement import DISBURSEMENT
from .servicing.loan_documents import APPLICATION
from .servicing.loan_quote_change import QUOTE_CHANGE
from .servicing.repair_parts import REPAIR
from .servicing.visit_noshow import NOSHOW

WORKFLOWS = {
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
}
BY_TEMPLATE = {workflow.template: workflow for workflow in WORKFLOWS.values()}


def workflow_agents(workflow):
    return workflow.agents
