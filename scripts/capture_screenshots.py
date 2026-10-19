"""Screenshots of the real app for the usage guide: ``python -m scripts.capture_screenshots``.

Seeds a fresh copy of Rina's Bakery in a temporary data folder (your own ``var/`` is never
touched), starts the UI on a spare port, drives headless Chromium through the demo story over
the Chrome DevTools protocol and saves PNGs to ``docs/images/``. The AI mode comes from
``.env`` (with ``LLM_BACKEND=hybrid`` and an OpenAI key the drafts and explanations show real
AI wording; a few calls, well under one cent).

Needs Chromium (or Chrome) on PATH. Uses only packages already in the lock (httpx,
websockets). Clicks are real mouse events at the element's centre, so Streamlit widgets
(dropdowns, dialogs) behave exactly as for a person.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any

import httpx
from websockets.sync.client import connect

from infra.paths import REPO_ROOT

OUT_DIR = REPO_ROOT / "docs" / "images"
UI_PORT = 8503
CDP_PORT = 9333
WIDTH, HEIGHT = 1360, 880


def _free(port: int) -> bool:
    with closing(socket.socket()) as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


class Browser:
    """Minimal Chrome DevTools client: evaluate JS, real mouse clicks, typing, screenshots."""

    def __init__(self, ws_url: str) -> None:
        self.ws = connect(ws_url, max_size=64 * 1024 * 1024).__enter__()
        self._id = 0

    def call(self, method: str, **params: Any) -> dict[str, Any]:
        self._id += 1
        self.ws.send(json.dumps({"id": self._id, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return dict(msg.get("result", {}))

    def js(self, expr: str) -> Any:
        r = self.call("Runtime.evaluate", expression=expr, returnByValue=True, awaitPromise=True)
        return r.get("result", {}).get("value")

    def goto(self, url: str) -> None:
        self.call("Page.navigate", url=url)
        time.sleep(1.0)
        self.settle(min_wait=3.0)

    def settle(self, timeout: float = 90.0, min_wait: float = 1.5) -> None:
        """Wait until Streamlit finished running the page (no running indicator, no stale
        elements) and the main area has content."""
        time.sleep(min_wait)
        end = time.time() + timeout
        quiet = 0
        while time.time() < end:
            busy = self.js(
                "!!(document.querySelector('[data-testid=\"stStatusWidget\"]') && "
                "document.querySelector('[data-testid=\"stStatusWidget\"]').innerText"
                ".toLowerCase().includes('running')) || "
                "!!document.querySelector('[data-stale=\"true\"]') || "
                "!document.querySelector('[data-testid=\"stMain\"], section.main')"
            )
            quiet = 0 if busy else quiet + 1
            if quiet >= 3:
                time.sleep(1.0)
                return
            time.sleep(0.5)
        raise TimeoutError("page did not settle")

    def wait_text(self, text: str, timeout: float = 90.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.js(f"document.body.innerText.includes({json.dumps(text)})"):
                return
            time.sleep(0.5)
        raise TimeoutError(f"text not found: {text!r}")

    def _center(self, finder: str) -> tuple[float, float]:
        r = self.js(
            f"(() => {{ const el = {finder}; if (!el) return null;"
            " el.scrollIntoView({block: 'center'});"
            " const b = el.getBoundingClientRect();"
            " return [b.left + b.width / 2, b.top + b.height / 2]; })()"
        )
        if not r:
            raise LookupError(f"element not found: {finder}")
        time.sleep(0.4)
        r = self.js(
            f"(() => {{ const b = ({finder}).getBoundingClientRect();"
            " return [b.left + b.width / 2, b.top + b.height / 2]; })()"
        )
        return float(r[0]), float(r[1])

    def click_at(self, x: float, y: float) -> None:
        for kind in ("mousePressed", "mouseReleased"):
            self.call("Input.dispatchMouseEvent", type=kind, x=x, y=y, button="left", clickCount=1)

    def click(self, finder: str, settle: bool = True) -> None:
        self.click_at(*self._center(finder))
        if settle:
            self.settle()

    def type(self, text: str) -> None:
        self.call("Input.insertText", text=text)

    def scroll_to(self, finder: str, block: str = "start") -> None:
        ok = self.js(
            f"(() => {{ const el = {finder}; if (!el) return false;"
            f" el.scrollIntoView({{block: '{block}'}}); return true; }})()"
        )
        if not ok:
            raise LookupError(f"element not found: {finder}")
        time.sleep(0.8)

    def shot(self, path: Path) -> None:
        data = self.call("Page.captureScreenshot", format="png")["data"]
        path.write_bytes(base64.b64decode(data))
        print(f"  saved {path.name}")


def button(label: str, nth: int = 0) -> str:
    """JS expression for the nth visible button whose text is exactly ``label``."""
    return (
        "[...document.querySelectorAll('button')].filter(b => b.offsetParent !== null && "
        f"b.innerText.trim() === {json.dumps(label)})[{nth}]"
    )


def by_text(selector: str, text: str) -> str:
    return (
        f"[...document.querySelectorAll({json.dumps(selector)})]"
        f".find(e => e.offsetParent !== null && e.innerText.includes({json.dumps(text)}))"
    )


def heading(text: str) -> str:
    return by_text("h1, h2, h3", text)


def top() -> str:
    return "document.querySelector('[data-testid=\"stMain\"] h1, section.main h1')"


@contextmanager
def running_app(var: Path) -> Any:
    env = {**os.environ, "LOANREADY_VAR_DIR": str(var), "LOANREADY_ENV": "dev"}
    seed = subprocess.run(
        [sys.executable, "-m", "orchestrator.seed"], cwd=REPO_ROOT, env=env, check=False
    )
    if seed.returncode:
        raise SystemExit("seeding failed")
    ui = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", "ui/app.py",
            "--server.address", "127.0.0.1", "--server.port", str(UI_PORT),
            "--server.headless", "true", "--browser.gatherUsageStats", "false",
        ],
        cwd=REPO_ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    chrome_bin = next(
        (p for n in ("chromium", "chromium-browser", "google-chrome") if (p := shutil.which(n))),
        None,
    )
    if chrome_bin is None:
        ui.terminate()
        raise SystemExit("Chromium or Chrome is required (not found on PATH)")
    profile = var / "chrome"
    chrome = subprocess.Popen(
        [
            chrome_bin, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
            f"--remote-debugging-port={CDP_PORT}", f"--user-data-dir={profile}",
            f"--window-size={WIDTH},{HEIGHT}", "about:blank",
        ],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )  # fmt: skip
    try:
        for _ in range(120):
            try:
                if httpx.get(f"http://127.0.0.1:{UI_PORT}", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        pages = []
        for _ in range(60):
            try:
                pages = httpx.get(f"http://127.0.0.1:{CDP_PORT}/json", timeout=1).json()
                if pages:
                    break
            except httpx.HTTPError:
                time.sleep(0.5)
        page = next(p for p in pages if p.get("type") == "page")
        browser = Browser(page["webSocketDebuggerUrl"])
        browser.call("Page.enable")
        browser.call(
            "Emulation.setDeviceMetricsOverride",
            width=WIDTH, height=HEIGHT, deviceScaleFactor=1, mobile=False,
        )  # fmt: skip
        yield browser
    finally:
        chrome.terminate()
        ui.terminate()
        for p in (chrome, ui):
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def story(b: Browser, out: Path) -> None:
    base = f"http://127.0.0.1:{UI_PORT}"

    print("Home")
    b.goto(base + "/")
    b.wait_text("LoanReady dashboard")
    b.shot(out / "01_home.png")
    b.click(by_text("details summary", "AI status"))
    b.scroll_to(by_text("details summary", "AI status"), "center")
    b.shot(out / "02_sidebar_ai_status.png")
    b.click(by_text("details summary", "AI status"))  # close it again for the other shots

    print("Capture")
    b.click(by_text("[data-testid='stSidebarNav'] a", "Capture"))
    b.wait_text("Confirm queue")
    b.scroll_to(heading("Confirm queue"))
    b.shot(out / "03_capture_confirm_queue.png")
    b.scroll_to(heading("Ignored instructions"))
    b.shot(out / "04_capture_ignored_instructions.png")
    for label in (
        "PERSONAL (proposed)",
        "PERSONAL (proposed)",
        "duplicate_claim_refund (proposed)",
        "6900 (proposed)",
        "PERSONAL (proposed)",
    ):
        b.click(button(label))

    print("Books")
    b.click(by_text("[data-testid='stSidebarNav'] a", "Books"))
    b.wait_text("Journal")
    b.click(by_text("[role='tab']", "Reconciliation"))
    b.scroll_to(top())
    b.shot(out / "05_books_reconciliation.png")
    b.click(by_text("[role='tab']", "P&L"))
    b.scroll_to(top())
    b.shot(out / "06_books_pnl.png")

    print("Forecast")
    b.click(by_text("[data-testid='stSidebarNav'] a", "Forecast"))
    b.wait_text("AI-generated explanation", timeout=120)
    b.scroll_to(top())
    time.sleep(2.0)  # chart animation
    b.shot(out / "07_forecast_chart.png")
    b.scroll_to(heading("Why"))
    b.shot(out / "08_forecast_why_excluded.png")

    print("Action plan")
    b.click(by_text("[data-testid='stSidebarNav'] a", "Action plan"))
    b.wait_text("Closing the cash gap")
    b.scroll_to(top())
    b.shot(out / "09_action_plan_levers.png")
    b.click(button("Draft payment reminders"))
    b.wait_text("Preview message", timeout=120)
    b.click(by_text("details summary", "Preview message"), settle=False)
    time.sleep(1.0)
    b.scroll_to(heading("Drafts"))
    b.shot(out / "10_action_plan_drafts.png")
    b.click(button("Propose sending"))
    b.click(button("Review and approve"), settle=False)
    b.wait_text("Approval needed")
    time.sleep(1.5)
    b.shot(out / "11_approval_dialog_reminder.png")
    b.click(button("Approve and execute"))
    b.wait_text("Executed in the sandbox")
    b.scroll_to(top())
    b.shot(out / "12_reminder_sent.png")

    print("Supplier payments")
    combo = 'document.querySelector(\'[data-testid="stSelectbox"] input[aria-label="Open bill"]\')'
    option = "[...document.querySelectorAll('[role=option]')].find(o => o.innerText.startsWith({}))"
    b.click(combo, settle=False)
    time.sleep(0.8)
    b.click(option.format(json.dumps("BILL-2211")), settle=False)
    time.sleep(0.8)
    b.click(button("Propose payment"))
    b.wait_text("ACCOUNT_CHANGED")
    b.scroll_to(top())
    b.shot(out / "13_blocked_fraud_payment.png")
    b.click(combo, settle=False)
    time.sleep(0.8)
    b.click(option.format(json.dumps("BILL-2190")), settle=False)
    time.sleep(0.8)
    b.click(button("Propose payment"))
    b.click(button("Review and approve"), settle=False)
    b.wait_text("Approval needed")
    time.sleep(1.5)
    b.shot(out / "14_approval_dialog_payment.png")
    b.click(button("Approve and execute"))
    b.wait_text("pay_")

    print("Financing")
    b.click(by_text("[data-testid='stSidebarNav'] a", "Financing"))
    b.wait_text("Most expensive by EAR", timeout=120)
    b.scroll_to(top())
    b.shot(out / "15_financing_comparison.png")
    b.wait_text("AI-generated explanation", timeout=120)
    b.scroll_to(heading("Explanation"))
    b.shot(out / "16_financing_explanation.png")
    b.scroll_to(heading("Loan readiness"))
    time.sleep(1.5)
    b.shot(out / "17_readiness_score.png")

    print("Loan pack")
    b.click(by_text("[data-testid='stSidebarNav'] a", "Loan pack"))
    b.wait_text("Generate pack")
    b.click(button("Generate pack"))
    b.wait_text("Download loan pack PDF")
    b.click(button("Submit to lender"))
    b.click(button("Review and approve"), settle=False)
    b.wait_text("Approval needed")
    time.sleep(1.0)
    b.click(button("Approve and execute"))
    b.wait_text("Receipt ID")
    b.scroll_to(heading("Summary"))
    b.shot(out / "18_loan_pack_receipt.png")

    print("Log and security")
    b.click(by_text("[data-testid='stSidebarNav'] a", "Log and security"))
    b.wait_text("Verify chain")
    b.click(button("Try it"))
    b.click(button("Verify chain"))
    b.wait_text("Chain intact")
    b.scroll_to(top())
    b.shot(out / "19_log_verify_chain.png")
    b.scroll_to(heading("Limits and kill switch"))
    time.sleep(1.5)
    b.shot(out / "20_limits_and_blocked_actions.png")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m scripts.capture_screenshots", description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args(argv)
    for port in (UI_PORT, CDP_PORT):
        if not _free(port):
            raise SystemExit(f"port {port} is busy; stop whatever uses it first")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="loanready_shots_") as tmp:
        var = Path(tmp) / "var"
        bundle = REPO_ROOT / "var" / "seed" / "bundle.json"
        if bundle.exists():  # reuse the generated bundle instead of regenerating it
            (var / "seed").mkdir(parents=True)
            shutil.copy(bundle, var / "seed" / "bundle.json")
        with running_app(var) as browser:
            story(browser, args.out_dir)
    print(f"screenshots in {args.out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
