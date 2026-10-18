"""Registries shared by every package: expected packages, folder ownership, chart of
accounts, settings keys and the control ids of PRD/14 §3."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath

from contracts.dto_books import AccountDTO
from contracts.enums import AccountType


@dataclass(frozen=True)
class PackageSpec:
    wp: str
    entry_module: str  # module that must exist for the package to count as present
    service_names: tuple[str, ...] = ()  # names the entry module must export


# Order = integration order. Layer 2 packages, the sandbox, then the top layers.
EXPECTED_PACKAGES: dict[str, PackageSpec] = {
    "governance": PackageSpec("WP1", "governance.service", ("build_governance",)),
    "sandbox": PackageSpec("WP2", "sandbox.app", ("app",)),
    "adapters": PackageSpec("WP2", "adapters", ("build_ports",)),
    "books": PackageSpec("WP3", "books.service", ("Books",)),
    "planning": PackageSpec("WP4", "planning.service", ("Planner",)),
    "financing": PackageSpec("WP5", "financing.service", ("Financing", "Readiness")),
    "outputs": PackageSpec("WP6", "outputs.service", ("Outputs",)),
    "agent": PackageSpec("WP7", "agent.service", ("Agent",)),
    "datagen": PackageSpec("WP8", "datagen.generate", ()),
    "orchestrator": PackageSpec("WP9", "orchestrator.container", ("build_app",)),
    "ui": PackageSpec("WP10", "ui.app", ()),
    "scripts": PackageSpec("WP11", "scripts.run_demo", ()),
}

# Root files frozen by WP0 and covered by contracts/MANIFEST.sha256 (CON-23).
FROZEN_ROOT_FILES: tuple[str, ...] = (
    "pyproject.toml",
    "requirements.lock",
    "tasks.py",
    ".importlinter",
    ".env.example",
    ".gitattributes",
    "tests/conftest.py",
    "CLAUDE.md",
    "PC_GUIDE.md",
)

# WP -> owned paths (directories end with "/"). The most specific path wins, so
# data/rules.json belongs to WP3 although data/ belongs to WP8.
OWNERSHIP: dict[str, tuple[str, ...]] = {
    "WP0": (
        "contracts/",
        "infra/",
        "tests/contracts/",
        "tests/conftest.py",
        ".github/",
        "pyproject.toml",
        "requirements.lock",
        "tasks.py",
        "Makefile",
        ".importlinter",
        ".env.example",
        ".gitignore",
        ".gitattributes",
        ".editorconfig",
        "CLAUDE.md",
        "PC_GUIDE.md",
    ),
    "WP1": ("governance/", "tests/governance/"),
    "WP2": ("sandbox/", "adapters/", "tests/sandbox/", "tests/adapters/"),
    "WP3": ("books/", "data/rules.json", "tests/books/"),
    "WP4": ("planning/", "tests/planning/"),
    "WP5": (
        "financing/",
        "data/products.json",
        "data/regulatory_params.json",
        "tests/financing/",
    ),
    "WP6": ("outputs/", "tests/outputs/"),
    "WP7": ("agent/", "tests/agent/"),
    "WP8": ("datagen/", "data/", "tests/datagen/"),
    "WP9": ("orchestrator/", "tests/integration/"),
    "WP10": ("ui/", "tests/ui/"),
    "WP11": ("scripts/", "docs/", "evidence/", "tests/e2e/", "README.md", "THIRD_PARTY.md"),
}

# Never owned by a WP and never handed off.
UNOWNED_PATHS: tuple[str, ...] = (
    "PRD/",
    ".git/",
    ".venv/",
    "var/",
    "handoff/",
    ".env",
    ".coverage",
    "htmlcov/",
)
IGNORED_PARTS = frozenset(
    {
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".hypothesis",
        ".mypy_cache",
        ".import_linter_cache",
    }
)
IGNORED_SUFFIXES = (".pyc", ".pyo", ".db", ".db-wal", ".db-shm")


def _matches(path: str, owned: str) -> bool:
    return path.startswith(owned) if owned.endswith("/") else path == owned


def owner_of(path: str) -> str | None:
    """WP owning a repo-relative POSIX path, or None. Longest matching entry wins."""
    p = PurePosixPath(path).as_posix()
    best: tuple[int, str] | None = None
    for wp, paths in OWNERSHIP.items():
        for owned in paths:
            if _matches(p, owned) and (best is None or len(owned) > best[0]):
                best = (len(owned), wp)
    return best[1] if best else None


def is_ignored(path: str) -> bool:
    """Caches, compiled files and databases never travel in hand-offs."""
    parts = PurePosixPath(path).parts
    return bool(IGNORED_PARTS.intersection(parts)) or path.endswith(IGNORED_SUFFIXES)


def is_unowned(path: str) -> bool:
    return any(_matches(path, u) for u in UNOWNED_PATHS)


CHART_OF_ACCOUNTS: tuple[AccountDTO, ...] = (
    AccountDTO(code="1000", name="Cash at bank", type=AccountType.ASSET, is_cash=True),
    AccountDTO(code="1010", name="Cash on hand (till)", type=AccountType.ASSET, is_cash=True),
    AccountDTO(
        code="1020",
        name="Card settlements receivable (clearing)",
        type=AccountType.ASSET,
        is_cash=False,
    ),
    AccountDTO(code="1100", name="Accounts receivable", type=AccountType.ASSET, is_cash=False),
    AccountDTO(
        code="1110",
        name="Refund claims receivable (disputed charges)",
        type=AccountType.ASSET,
        is_cash=False,
    ),
    AccountDTO(
        code="1150",
        name="Allowance for expected credit losses",
        type=AccountType.ASSET,
        is_cash=False,
    ),
    AccountDTO(code="1200", name="Inventory", type=AccountType.ASSET, is_cash=False),
    AccountDTO(code="1500", name="Equipment", type=AccountType.ASSET, is_cash=False),
    AccountDTO(code="2000", name="Accounts payable", type=AccountType.LIABILITY, is_cash=False),
    AccountDTO(code="2100", name="Accrued liabilities", type=AccountType.LIABILITY, is_cash=False),
    AccountDTO(code="2500", name="Loans payable", type=AccountType.LIABILITY, is_cash=False),
    AccountDTO(code="3000", name="Owner's capital", type=AccountType.EQUITY, is_cash=False),
    AccountDTO(code="3100", name="Owner's drawings", type=AccountType.EQUITY, is_cash=False),
    AccountDTO(code="3200", name="Retained earnings", type=AccountType.EQUITY, is_cash=False),
    AccountDTO(code="4000", name="Sales revenue", type=AccountType.INCOME, is_cash=False),
    AccountDTO(
        code="5000",
        name="Cost of goods sold (ingredients, packaging)",
        type=AccountType.EXPENSE,
        is_cash=False,
    ),
    AccountDTO(code="6000", name="Rent", type=AccountType.EXPENSE, is_cash=False),
    AccountDTO(code="6100", name="Wages", type=AccountType.EXPENSE, is_cash=False),
    AccountDTO(code="6200", name="Utilities", type=AccountType.EXPENSE, is_cash=False),
    AccountDTO(code="6300", name="Marketing", type=AccountType.EXPENSE, is_cash=False),
    AccountDTO(code="6400", name="Bank and card fees", type=AccountType.EXPENSE, is_cash=False),
    AccountDTO(
        code="6500", name="Impairment loss on receivables", type=AccountType.EXPENSE, is_cash=False
    ),
    AccountDTO(code="6900", name="Other expenses", type=AccountType.EXPENSE, is_cash=False),
    AccountDTO(code="7000", name="Interest expense", type=AccountType.EXPENSE, is_cash=False),
)
PERSONAL_LABEL = "PERSONAL"  # categorisation label, posts to 3100
CASH_ACCOUNTS = ("1000", "1010")

# Settings table keys (seeded by WP9 from .env defaults). Value = .env variable or a
# literal default when the key has no variable.
SETTINGS_KEYS: tuple[str, ...] = (
    "kill_switch",
    "cap_per_action_cents",
    "cap_per_day_cents",
    "cap_per_payee_week_cents",
    "cap_msgs_per_customer_week",
    "approval_ttl_seconds",
    "confidence_confirm_threshold",
    "receipt_confirm_threshold",
    "framework",
    "safety_buffer_cents",
    "tier3_enabled_types",
)
SETTINGS_ENV: dict[str, str] = {
    "cap_per_action_cents": "CAP_PER_ACTION_CENTS",
    "cap_per_day_cents": "CAP_PER_DAY_CENTS",
    "cap_per_payee_week_cents": "CAP_PER_PAYEE_WEEK_CENTS",
    "cap_msgs_per_customer_week": "CAP_MSGS_PER_CUSTOMER_WEEK",
    "approval_ttl_seconds": "APPROVAL_TTL_SECONDS",
    "confidence_confirm_threshold": "CONFIDENCE_CONFIRM_THRESHOLD",
    "receipt_confirm_threshold": "RECEIPT_CONFIRM_THRESHOLD",
    "framework": "LOANREADY_FRAMEWORK",
}
SETTINGS_LITERAL_DEFAULTS: dict[str, str] = {
    "kill_switch": "false",
    "safety_buffer_cents": "auto",
    "tier3_enabled_types": "[]",
}

# Every control id of PRD/14 §3. governance.controls must list each one (T-GOV-24).
CONTROL_IDS: tuple[str, ...] = (
    "CTL-AC-01",
    "CTL-AC-02",
    "CTL-AC-03",
    "CTL-AC-04",
    "CTL-AC-05",
    "CTL-AC-06",
    "CTL-AC-07",
    "CTL-SD-01",
    "CTL-AU-01",
    "CTL-AU-02",
    "CTL-LIM-01",
    "CTL-LIM-02",
    "CTL-LIM-03",
    "CTL-LIM-04",
    "CTL-LIM-05",
    "CTL-LIM-06",
    "CTL-PAY-01",
    "CTL-PAY-02",
    "CTL-PAY-03",
    "CTL-PAY-04",
    "CTL-PAY-05",
    "CTL-PAY-06",
    "CTL-LOG-01",
    "CTL-LOG-02",
    "CTL-LOG-03",
    "CTL-LOG-04",
    "CTL-LOG-05",
    "CTL-FR-01",
    "CTL-FR-02",
    "CTL-FR-03",
    "CTL-FR-04",
    "CTL-FR-05",
    "CTL-FR-06",
    "CTL-FR-07",
    "CTL-FR-08",
    "CTL-FR-09",
    "CTL-FR-10",
    "CTL-DQ-01",
    "CTL-DQ-02",
    "CTL-AI-01",
    "CTL-AI-02",
    "CTL-AI-03",
    "CTL-AI-04",
    "CTL-AI-05",
    "CTL-AI-06",
    "CTL-AI-07",
    "CTL-AI-08",
    "CTL-AI-09",
    "CTL-FIN-01",
    "CTL-FIN-02",
    "CTL-FIN-03",
    "CTL-FIN-04",
    "CTL-CHG-01",
    "CTL-CHG-02",
    "CTL-CHG-03",
    "CTL-OPS-01",
    "CTL-SEC-01",
)

LENDER_IDS = frozenset({"lender_demo"})
OPERATING_ACCOUNT_ID = "acc_operating"
