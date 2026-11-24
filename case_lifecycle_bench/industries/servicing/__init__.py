"""Card-based credit and field-service templates on one servicing engine."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ServicingTemplate:
    """Everything the runner, scorer and acceptance check need from one template."""

    industry: str
    template: str
    title: str
    generate: Callable  # seed -> case
    environment: Callable  # (case, wake) -> lifecycle environment
    evaluate: Callable  # (case, run) -> evaluation
    policy: str
    tools: list
    agents: dict  # name -> agent; "oracle" plus the counterexamples
    counterexamples: tuple  # (agent name, section, key) for the acceptance check
    oracle_process_checks: tuple
    outcomes: object  # OutcomeSpec for three-outcome scoring
