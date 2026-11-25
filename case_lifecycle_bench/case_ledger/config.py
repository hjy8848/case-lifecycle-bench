"""Which parts of the Case Ledger are on. One config per experimental group."""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class LedgerConfig:
    name: str = "full"
    statement_journal: bool = True  # the bill lists the journal of writes
    show_uncertain: bool = True  # the bill lists results that are not known (the check-A area)
    check_a: bool = True  # reconcile before writing again
    check_b: bool = True  # settle only with fresh evidence
    check_c: bool = True  # the books must balance when the session ends
    close_gate: bool = True  # no close_ticket while promises are open
    scorekeeper: bool = True
    require_evidence: bool = True
    record_only: bool = False  # compute every check, log what it would return, never reject

    @classmethod
    def full(cls):  # E3
        return cls()

    @classmethod
    def todo(cls):  # E2: a plain to-do list
        return cls(
            name="todo", statement_journal=False, show_uncertain=False, check_a=False, check_b=False,
            check_c=False, close_gate=False, scorekeeper=False, require_evidence=False,
        )

    @classmethod
    def no_keep(cls):  # A1: without "keep every promise"
        return cls(name="no_keep", statement_journal=False, check_c=False, close_gate=False, scorekeeper=False)

    @classmethod
    def no_earn(cls):  # A2: without "earn every credential"
        return cls(name="no_earn", show_uncertain=False, check_a=False, check_b=False, require_evidence=False)

    def recording(self):
        return replace(self, record_only=True)
