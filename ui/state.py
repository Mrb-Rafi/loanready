"""UI session state (PRD/12 §1, UI-12).

- ``get_app()`` returns the one ``AppFacade`` for this Streamlit process
  (``st.cache_resource``). It is ``FakeAppFacade`` when ``LOANREADY_UI_FAKE == "1"`` or
  when ``orchestrator.container.build_app`` cannot be imported, else ``build_app()``.
  Reading ``LOANREADY_UI_FAKE`` with ``os.environ`` here is the agreed exception to
  "only ``infra.settings`` reads the environment" (``ui`` may not import ``infra``).
- ``cached(app, "method", *args)`` memoises read-only facade calls with
  ``st.cache_data(ttl=None)`` keyed by ``app.data_version()``, so pages recompute only
  after a write.
- The acting principal (owner / bookkeeper) and the approval tokens returned by
  ``AppFacade.approve`` live in ``st.session_state``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import streamlit as st

from contracts.enums import Role
from contracts.errors import LoanReadyError
from contracts.services import AppFacade

FAKE_ENV = "LOANREADY_UI_FAKE"
UI_ROLES: tuple[Role, ...] = (Role.OWNER, Role.BOOKKEEPER)
ROLE_LABELS: dict[Role, str] = {Role.OWNER: "Owner", Role.BOOKKEEPER: "Bookkeeper"}

_K_ROLE = "lr_role"
_K_TOKENS = "lr_approval_tokens"
_K_FLASH = "lr_flash"
_K_PACK = "lr_last_pack"
_K_RECEIPT = "lr_last_receipt"
_K_SUBMIT = "lr_last_submission"

REPO_ROOT = Path(__file__).resolve().parents[1]


def fake_requested() -> bool:
    """True when ``LOANREADY_UI_FAKE`` is ``"1"`` (the one env read the UI is allowed)."""
    return os.environ.get(FAKE_ENV, "") == "1"


def build_facade() -> tuple[AppFacade, bool]:
    """Return ``(facade, is_fake)``. Never raises when the orchestrator is missing."""
    if not fake_requested():
        try:
            from orchestrator.container import build_app  # type: ignore[import-not-found]
        except ImportError:
            pass
        else:
            return build_app(), False
    from contracts.testing.fakes import FakeAppFacade

    return FakeAppFacade(), True


@st.cache_resource(show_spinner="Starting LoanReady ...")
def _app_resource() -> tuple[AppFacade, bool]:
    return build_facade()


def get_app() -> AppFacade:
    """The process-wide facade, with the session principal applied."""
    app, _ = _app_resource()
    ensure_principal(app)
    return app


def is_fake() -> bool:
    return _app_resource()[1]


# --- principal ---------------------------------------------------------------------------------


def session_role() -> Role:
    role = st.session_state.get(_K_ROLE)
    return role if role in UI_ROLES else Role.OWNER


def set_session_role(role: Role) -> None:
    if role not in UI_ROLES:
        raise ValueError("UI principal must be OWNER or BOOKKEEPER")
    st.session_state[_K_ROLE] = role


def ensure_principal(app: AppFacade) -> None:
    """Make the facade act as the principal chosen in this browser session."""
    role = session_role()
    if app.principal().role != role:
        app.set_principal(role)


# --- cached reads (UI-12) ----------------------------------------------------------------------

READ_METHODS = frozenset(
    {
        "health",
        "dashboard",
        "confirm_queue",
        "books_view",
        "receivables",
        "forecast",
        "drafts",
        "open_bills",
        "excluded_bills",
        "forecast_explanation",
        "cash_plan",
        "pending",
        "financing",
        "financing_explanation",
        "readiness",
        "audit_rows",
        "audit_counts",
        "cap_usage",
        "settings",
        "tier_table",
        "controls_table",
    }
)


@st.cache_data(ttl=None, show_spinner=False, max_entries=512)
def _cached_call(_app: Any, app_key: int, version: int, method: str, args: tuple[Any, ...]) -> Any:
    return getattr(_app, method)(*args)


def cached(app: AppFacade, method: str, *args: Any) -> Any:
    """Read-only facade call memoised by ``(app, data_version, method, args)``."""
    if method not in READ_METHODS:
        raise ValueError(f"{method} is not a cached read")
    try:
        return _cached_call(app, id(app), app.data_version(), method, tuple(args))
    except LoanReadyError:
        raise
    except Exception:  # unpicklable return value from a real package: call directly
        return getattr(app, method)(*args)


def clear_caches() -> None:
    """Drop every cached read and the facade itself (used by tests)."""
    _cached_call.clear()
    _app_resource.clear()


# --- approval tokens ---------------------------------------------------------------------------


def remember_token(proposal_id: str, token_id: str) -> None:
    """Keep the token id from ``approve`` for this session only (never shown or logged)."""
    st.session_state.setdefault(_K_TOKENS, {})[proposal_id] = token_id


def forget_token(proposal_id: str) -> None:
    st.session_state.setdefault(_K_TOKENS, {}).pop(proposal_id, None)


def has_token(proposal_id: str) -> bool:
    return proposal_id in st.session_state.get(_K_TOKENS, {})


# --- flash messages and page memory -----------------------------------------------------------


def flash(kind: str, text: str) -> None:
    """Queue a message shown once on the next run (``kind``: success, info, warning, error)."""
    st.session_state.setdefault(_K_FLASH, []).append((kind, text))


def pop_flashes() -> list[tuple[str, str]]:
    return st.session_state.pop(_K_FLASH, [])


def mark_proposed(draft_id: str) -> None:
    st.session_state.setdefault("lr_proposed_drafts", set()).add(draft_id)


def was_proposed(draft_id: str) -> bool:
    return draft_id in st.session_state.get("lr_proposed_drafts", set())


def set_last(key: str, value: Any) -> None:
    st.session_state[key] = value


def get_last(key: str) -> Any:
    return st.session_state.get(key)


LAST_PACK = _K_PACK
LAST_RECEIPT = _K_RECEIPT
LAST_SUBMISSION = _K_SUBMIT


# --- local file locations ----------------------------------------------------------------------


def _local_dir(*parts: str) -> Path:
    """Repo-relative folder for the real app; a temp folder in fake mode (keeps tests and
    fake demos out of the repo). ``ui`` may not import ``infra.paths``."""
    base = Path(tempfile.gettempdir()) / "loanready_ui" if is_fake() else REPO_ROOT
    path = base.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def uploads_dir() -> Path:
    return _local_dir("var", "uploads")


def export_csv_path() -> Path:
    """Runtime output under ``var/`` (never committed); ``evidence/`` is generated by WP11."""
    return _local_dir("var", "exports") / "action_log.csv"
