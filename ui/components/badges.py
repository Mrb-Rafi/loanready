"""Tier badges, AI-label badge and status chips (PRD/12 §1, UI-09, UI-13; CTL-AI-06).

Status is always shown as colour plus text, never colour alone (UI-13).
"""

from __future__ import annotations

import streamlit as st

from contracts.enums import Decision, GeneratedBy, ReadinessBand, Tier
from ui.components.money import md

# REG-AI-06 wording for explanations of figures computed by code.
AI_EXPLANATION_LABEL = "AI-generated explanation of figures calculated by LoanReady"
AI_TEXT_LABEL = "AI-generated"

TIER_TEXT: dict[Tier, str] = {
    Tier.T0_OBSERVE: "Tier 0: read only",
    Tier.T1_PROPOSE: "Tier 1: draft only, nothing is sent",
    Tier.T2_APPROVE: "Tier 2: needs your approval",
    Tier.T3_AUTONOMOUS: "Tier 3: automatic within limits",
    Tier.BLOCKED: "Blocked: never allowed",
}
TIER_COLOUR: dict[Tier, str] = {
    Tier.T0_OBSERVE: "gray",
    Tier.T1_PROPOSE: "blue",
    Tier.T2_APPROVE: "orange",
    Tier.T3_AUTONOMOUS: "violet",
    Tier.BLOCKED: "red",
}
DECISION_COLOUR: dict[Decision, str] = {
    Decision.ALLOWED: "gray",
    Decision.DRAFTED: "blue",
    Decision.PENDING_APPROVAL: "orange",
    Decision.APPROVED: "green",
    Decision.REJECTED: "gray",
    Decision.BLOCKED: "red",
    Decision.EXECUTED: "green",
    Decision.FAILED: "red",
    Decision.EVENT: "gray",
}
BAND_COLOUR: dict[ReadinessBand, str] = {
    ReadinessBand.LOW: "red",
    ReadinessBand.FAIR: "orange",
    ReadinessBand.GOOD: "green",
}


def tier_text(tier: Tier | int | str) -> str:
    """Plain text of a tier (accepts the enum, its int value or its name)."""
    return TIER_TEXT[_tier(tier)]


def _tier(tier: Tier | int | str) -> Tier:
    if isinstance(tier, Tier):
        return tier
    if isinstance(tier, str) and not tier.isdigit():
        return Tier[tier]
    return Tier(int(tier))


def chip(text: str, colour: str) -> str:
    """Streamlit markdown chip ``:colour-badge[text]``."""
    safe = text.replace("[", "(").replace("]", ")")
    return f":{colour}-badge[{safe}]"


def tier_badge(tier: Tier | int | str) -> str:
    t = _tier(tier)
    return chip(TIER_TEXT[t], TIER_COLOUR[t])


def decision_badge(decision: Decision | str) -> str:
    d = Decision(decision)
    return chip(d.value.replace("_", " ").title(), DECISION_COLOUR[d])


def band_badge(band: ReadinessBand) -> str:
    return chip(f"Readiness {band.value.title()}", BAND_COLOUR[band])


def on_off_badge(label: str, on: bool, on_is_bad: bool = False) -> str:
    colour = ("red" if on_is_bad else "green") if on else "gray"
    return chip(f"{label}: {'on' if on else 'off'}", colour)


def ai_badge(label: str | None = None) -> str:
    """Badge for LLM-worded text (UI-09). ``label`` is the DTO's ``ai_label`` when present."""
    return chip(label or AI_TEXT_LABEL, "violet")


def show_ai_text(text: str, label: str | None = None) -> None:
    """Show LLM-worded text with its label right above it (REG-AI-04)."""
    st.markdown(ai_badge(label or AI_EXPLANATION_LABEL))
    st.markdown(md(text))


def draft_label(generated_by: GeneratedBy, ai_label: str) -> str:
    """Badge for a message draft: the DTO's own label, marked AI only if the LLM wrote it."""
    colour = "violet" if generated_by == GeneratedBy.LLM else "blue"
    return chip(ai_label or generated_by.value.title(), colour)


def synthetic_banner() -> None:
    st.info(
        "Sandbox only: synthetic data and fake money. No real bank, lender or customer is "
        "contacted.",
        icon=":material/science:",
    )
