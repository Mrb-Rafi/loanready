"""Capture: sync POS and bank, upload a receipt, confirm queue, ignored-instruction flags
(PRD/12 §2 page 1; CTL-AI-01, CTL-FR-06). Plain layout (cut list item 11)."""

from __future__ import annotations

import streamlit as st

from contracts.enums import EventType
from ui import state
from ui.components import feedback
from ui.components.money import md, money_md

app = state.get_app()
st.title("Capture")
feedback.show_flashes()

st.subheader("Import records")
if st.button("Sync POS and bank", key="lr_sync"):
    result = feedback.attempt(app.sync_all)
    if result is not None:
        state.set_last("lr_sync_result", result)
        state.flash("success", "Synced: " + ", ".join(f"{k} {v}" for k, v in result.items()))
        st.rerun()
last = state.get_last("lr_sync_result")
if last:
    st.caption("Last sync: " + ", ".join(f"{k}: {v}" for k, v in last.items()))

st.subheader("Upload a receipt")
up = st.file_uploader("Receipt image or PDF", type=["png", "jpg", "jpeg", "pdf", "txt"])
if up is not None and st.button("Read receipt", key="lr_receipt"):
    safe = "".join(c for c in up.name if c.isalnum() or c in "._-") or "receipt"
    target = state.uploads_dir() / safe
    target.write_bytes(up.getvalue())
    rec = feedback.attempt(app.capture_receipt, target)
    if rec is not None:
        state.set_last(state.LAST_RECEIPT, rec)
        st.rerun()
rec = state.get_last(state.LAST_RECEIPT)
if rec is not None:
    with st.container(border=True):
        st.markdown(f"**Receipt {rec.id}**: status {rec.status.value}, OCR {rec.ocr_backend.value}")
        st.write(
            f"Vendor: {rec.vendor or 'unknown'}; total: {money_md(rec.total_cents)}; "
            f"date: {rec.date.isoformat() if rec.date else 'unknown'}; confidence {rec.confidence}"
        )
        st.caption("Text read from the receipt (untrusted, never followed as an instruction):")
        st.code(str(rec.ocr_text), language=None)
        for f in rec.flags:
            st.warning(
                "We found text that looks like an instruction in this receipt. It was ignored. "
                f"({f.pattern_id})"
            )
            st.code(f.excerpt, language=None)  # untrusted: never rendered as markdown

st.subheader("Confirm queue")
queue = state.cached(app, "confirm_queue")
if not queue:
    st.caption("Nothing to confirm.")
for item in queue:
    with st.container(border=True):
        st.markdown(f"**{item.question}**")
        conf = "n/a" if item.confidence is None else str(item.confidence)
        st.caption(f"{item.kind.value}; proposed answer: {item.proposed}; confidence {conf}")
        cols = st.columns(max(1, len(item.options)))
        for col, opt in zip(cols, item.options, strict=False):
            label = f"{opt} (proposed)" if opt == item.proposed else opt
            clicked = col.button(label, key=f"lr_cfm_{item.id}_{opt}")
            if clicked and feedback.attempt_ok(app.resolve, item.id, opt):
                state.flash("success", f"Confirmed {item.ref_id} as {opt}.")
                st.rerun()

st.subheader("Ignored instructions found in documents")
flags = [
    r
    for r in state.cached(app, "audit_rows", None)
    if r.action_type == EventType.UNTRUSTED_INSTRUCTION_IGNORED.value
]
if last and last.get("flags"):
    st.warning(
        f"The last sync found {last['flags']} piece(s) of text that look like instructions in bank "
        "memos or POS notes. They were ignored."
    )
if not flags and not (last and last.get("flags")):
    st.caption("No instructions found in documents so far.")
SOURCE_NAMES = {"BANK": "a bank memo", "POS": "a till (POS) note", "BILL": "a bill"}
for r in flags:
    raw_src = str(r.params_masked.get("source", ""))
    src = SOURCE_NAMES.get(raw_src, "a receipt" if raw_src == "RECEIPT" else "a document")
    excerpt = r.params_masked.get("excerpt", "")
    st.warning(
        f"We found text that looks like an instruction in {md(src)}. It was ignored. (log #{r.seq})"
    )
    if excerpt:
        st.code(str(excerpt), language=None)  # untrusted: never rendered as markdown
