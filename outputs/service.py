"""``Outputs``: the WP6 implementation of ``contracts.services.OutputsService`` (PRD/08).

Construction (PRD/11 §2): ``Outputs(engine, clock, ids, settings)``. Optional keyword
arguments: ``business_name`` (else ``settings.business_name`` if present, else the demo
business "Rina's Bakery") and ``packs_dir`` (else ``infra.paths.var_dir() / "packs"``).

After each draft, ``last_fallback`` / ``last_problems`` tell the orchestrator whether the
post-check replaced the wording with the template (log ``LLM_FALLBACK_USED``).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from contracts.canonical import file_sha256
from contracts.clock import Clock
from contracts.dto_books import BillDTO, CounterpartyDTO, InvoiceDTO
from contracts.dto_outputs import (
    DelayFacts,
    LoanPackContent,
    LoanPackDTO,
    MessageDraftDTO,
    ReminderFacts,
)
from contracts.errors import InvalidParams
from contracts.ids import IdFactory
from contracts.services import WordingProvider
from outputs import drafts as drafting
from outputs import facts as facts_mod
from outputs.loanpack.builder import build_pdf
from outputs.repo import OutputsRepo
from outputs.templates import TemplateWording

DEFAULT_BUSINESS = "Rina's Bakery"


def stored_path(path: Path) -> str:
    """Repo-relative POSIX path when ``path`` is inside the repo, else absolute POSIX."""
    from infra.paths import REPO_ROOT

    resolved = path.resolve()
    try:
        return resolved.relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def pack_file(stored: str) -> Path:
    """The file a stored pack path points at (relative paths are from the repo root)."""
    from infra.paths import REPO_ROOT

    p = Path(stored)
    return p if p.is_absolute() else REPO_ROOT / p


class Outputs:
    def __init__(
        self,
        engine: Engine,
        clock: Clock,
        ids: IdFactory,
        settings: Any,
        business_name: str | None = None,
        packs_dir: Path | None = None,
    ):
        self.repo = OutputsRepo(engine)
        self.clock = clock
        self.ids = ids
        self.settings = settings
        self.business_name = (
            business_name or getattr(settings, "business_name", None) or DEFAULT_BUSINESS
        )
        self._packs_dir = packs_dir
        self.last_fallback = False
        self.last_problems: list[str] = []

    @property
    def packs_dir(self) -> Path:
        if self._packs_dir is not None:
            return Path(self._packs_dir)
        from infra.paths import var_dir

        return var_dir() / "packs"

    def template_wording(self) -> WordingProvider:
        return TemplateWording()

    # --- facts ---------------------------------------------------------------------------
    def reminder_facts(
        self, invoice: InvoiceDTO, customer: CounterpartyDTO, as_of: date
    ) -> ReminderFacts:
        return facts_mod.reminder_facts(invoice, customer, as_of, self.business_name)

    def delay_facts(self, bill: BillDTO, supplier: CounterpartyDTO, proposed: date) -> DelayFacts:
        return facts_mod.delay_facts(bill, supplier, proposed, self.business_name)

    # --- drafts --------------------------------------------------------------------------
    def _keep(self, result: drafting.DraftResult) -> MessageDraftDTO:
        self.last_fallback, self.last_problems = result.fallback_used, result.problems
        self.repo.add_draft(result.draft)
        return result.draft

    def draft_reminder(self, facts: ReminderFacts, wording: WordingProvider) -> MessageDraftDTO:
        return self._keep(
            drafting.reminder_draft(facts, wording, self.ids.new("drf"), self.clock.now())
        )

    def draft_delay_request(self, facts: DelayFacts, wording: WordingProvider) -> MessageDraftDTO:
        return self._keep(
            drafting.delay_draft(facts, wording, self.ids.new("drf"), self.clock.now())
        )

    def draft(self, draft_id: str) -> MessageDraftDTO | None:
        return self.repo.draft(draft_id)

    def drafts(self) -> list[MessageDraftDTO]:
        """Every draft, sent or not, oldest first."""
        return self.repo.drafts()

    def mark_sent(self, draft_id: str, sbx_ref: str) -> None:
        """OUT-03: called after a successful Tier 2 send; content is never changed."""
        d = self.repo.draft(draft_id)
        if d is None:
            raise InvalidParams(f"unknown draft {draft_id}")
        if d.status == "sent":
            raise InvalidParams(f"draft {draft_id} was already sent")
        self.repo.mark_sent(draft_id, sbx_ref)

    # --- loan pack -----------------------------------------------------------------------
    def build_loan_pack(self, content: LoanPackContent) -> LoanPackDTO:
        """Render the PDF under ``packs_dir`` and store its file hash (OUT-04). ``path`` is
        repo-relative POSIX (cross-platform path rule, so a moved or fresh clone still resolves
        it); absolute only when ``packs_dir`` lies outside the repo (test temp folders).
        Readers resolve it with ``pack_file()`` (the gate: ``REPO_ROOT / path``)."""
        pack_id = self.ids.new("pck")
        built = build_pdf(content, pack_id, self.packs_dir)
        dto = LoanPackDTO(
            pack_id=pack_id,
            path=stored_path(built.path),
            sha256=built.sha256,
            sections=built.sections,
            created_at=self.clock.now(),
            metadata={**built.metadata, "sha256": built.sha256, "pack_id": pack_id},
        )
        self.repo.add_pack(dto)
        return dto

    def loan_pack(self, pack_id: str) -> LoanPackDTO | None:
        return self.repo.pack(pack_id)

    def verify_pack(self, pack_id: str) -> bool:
        """OUT-06: the file still exists and its SHA-256 equals the stored one."""
        pack = self.repo.pack(pack_id)
        if pack is None or not pack_file(pack.path).is_file():
            return False
        return file_sha256(pack_file(pack.path)) == pack.sha256

    def record_submission(self, pack_id: str, lender_id: str, receipt_id: str) -> None:
        """OUT-07: one lender_submissions row per successful submission."""
        pack = self.repo.pack(pack_id)
        if pack is None:
            raise InvalidParams(f"unknown loan pack {pack_id}")
        self.repo.add_submission(
            self.ids.new("sub"), pack_id, pack.sha256, lender_id, receipt_id, self.clock.now()
        )

    def submissions(self, pack_id: str) -> list[tuple[str, str]]:
        return self.repo.submissions(pack_id)
