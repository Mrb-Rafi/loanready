# LoanReady: Usage Guide

**Everything you need to install, run and use LoanReady, step by step.**

This guide assumes no prior knowledge of the project. Follow the parts in order the first
time; after that, jump to what you need from the contents. Every button name below is written
exactly as it appears on screen, in **bold**.

> **Everything in LoanReady is pretend.** The business (Rina's Bakery), its customers,
> suppliers, invoices and bank account are made up. Payments, messages and loan applications
> go to a built-in simulator, never to a real bank, person or lender. You cannot lose real
> money with it.


> **About the screenshots.** They were taken from the real app (Rina's Bakery, AS_OF
> 2026-10-14) with `LLM_BACKEND=hybrid` and OpenAI on, by `python -m scripts.capture_screenshots`.
> AI-written wording differs slightly from run to run; every figure is calculated by
> LoanReady and is the same every time.

---

## Contents

1. [What LoanReady is, in two minutes](#1-what-loanready-is-in-two-minutes)
2. [Before you start](#2-before-you-start)
3. [Install (one time)](#3-install-one-time)
4. [Start LoanReady](#4-start-loanready)
5. [Find your way around the screen](#5-find-your-way-around-the-screen)
6. [The pages, one by one](#6-the-pages-one-by-one)
7. [The approval dialog](#7-the-approval-dialog)
8. [Walk through the whole story (the demo script)](#8-walk-through-the-whole-story-the-demo-script)
9. [Run the story without the screen (headless demo, evidence, documents)](#9-run-the-story-without-the-screen)
10. [Turn on AI: local model and OpenAI](#10-turn-on-ai-local-model-and-openai)
11. [Everyday tasks and settings](#11-everyday-tasks-and-settings)
12. [Command reference](#12-command-reference)
13. [Troubleshooting](#13-troubleshooting)
14. [Frequently asked questions](#14-frequently-asked-questions)
15. [Glossary](#15-glossary)

---

## 1. What LoanReady is, in two minutes

LoanReady is a finance assistant for a small business owner. It does five things:

| # | It… | So the owner… |
|---|---|---|
| 1 | Reads the till (POS), the bank account and receipts, and turns them into proper books | sees profit, cash and what is owed, without a bookkeeper |
| 2 | Forecasts cash for the next 13 weeks, with a low, a likely and a high case | sees a cash shortfall weeks before it happens |
| 3 | Plans how to close the shortfall, cheapest option first: chase customers who owe money, then delay a bill that allows it, then borrow only what is left | does not borrow more, or more expensively, than needed |
| 4 | Compares borrowing options by their true yearly cost | is not fooled by expensive offers that look cheap |
| 5 | Prepares a lender-ready application pack (a PDF) | can apply for proper credit |

**Safety in one sentence:** LoanReady may suggest and prepare anything, but nothing leaves it
(no payment, no message, no application) until **you** approve it in a dialog that shows
exactly what will happen, and every attempt, allowed or blocked, is written to a log that
cannot be edited.

The demo business is **Rina's Bakery**, and the business date is **14 October 2026** (shown
as **AS_OF 2026-10-14**). LoanReady always works "as of" that date, so the story is the same
every time you run it.

---

## 2. Before you start

### 2.1 What your computer needs

| Need | Minimum | Notes |
|---|---|---|
| Operating system | Windows 10/11, Linux or macOS | Tested on Windows 11 and Linux Mint |
| Python | **3.12** exactly | 3.11 or 3.13 will be refused by the setup check |
| Disk space | about 1 GB | 620 MB is the Python packages LoanReady installs for itself |
| Memory | 4 GB free | 8 GB+ if you also run a local AI model |
| Internet | only for the first install | and for OpenAI, if you choose to use it |
| Browser | any modern browser | Chrome, Edge, Firefox, Safari |

You do **not** need: an AI model, an OpenAI account, OCR software, a database server or
any bank account. LoanReady runs fully without them.

### 2.2 Check Python 3.12

Open a terminal (Windows: **PowerShell**; Linux/macOS: **Terminal**) and type:

Windows:
```
py -3.12 --version
```
Linux / macOS:
```
python3.12 --version
```

You should see `Python 3.12.x`. If you see "not found", install Python 3.12 from
python.org (Windows: tick **Add python.exe to PATH** during installation) or with your
Linux package manager, then try again.

### 2.3 Where to put the project folder

Put the `loanready` folder at a short path **without spaces**, for example:

- Windows: `C:\dev\loanready`
- Linux: `/opt/loanready` or `~/loanready`

On Windows, **do not** put it inside a OneDrive-synced folder (Desktop or Documents are
often synced): OneDrive locks files and breaks the database.

---

## 3. Install (one time)

All commands are typed in a terminal **inside the project folder** (the folder that contains
`tasks.py`). Change into it first:

Windows:
```
cd C:\dev\loanready
```
Linux / macOS:
```
cd /opt/loanready
```

### Step 1. Create LoanReady's own Python environment

Windows:
```
py -3.12 tasks.py setup
```
Linux / macOS:
```
python3.12 tasks.py setup
```

This creates a folder called `.venv` and installs exactly the packages LoanReady needs. It
takes 2-5 minutes and prints a long list of packages. It ends with a line like
`var: …/var`.

> From now on you can type `python tasks.py …` (Windows: `py -3.12 tasks.py …` also works).
> LoanReady automatically switches into its own environment; you never need to "activate"
> anything.

### Step 2. Check the installation

```
python tasks.py doctor
```

You should see a list where every row marked **MUST** says **OK**, ending with
`doctor: all MUST checks OK`. Rows marked INFO (Tesseract, OpenAI key) are optional.

### Step 3. Create your settings file

Windows (PowerShell):
```
copy .env.example .env
```
Linux / macOS:
```
cp .env.example .env
```

`.env` holds your local settings. The defaults work as they are: AI in "mock" mode (no model
needed) and the simulator running inside LoanReady.

### Step 4 (optional). Run the tests

```
python tasks.py test
```

About 2 minutes. It should end with something like `719 passed, 4 skipped`. The skipped
tests are features that are deliberately not built; that is normal.

**Installation is done.**

---

## 4. Start LoanReady

### Step 1. Load the bakery's data

```
python tasks.py seed
```

The first time, this also generates the bakery's six months of records (about 10 s). It then
loads everything into the simulator and the books (about 25 s) and prints a summary like:

```
  cash (1000+1010)   $2,400.40
  confirm queue      6
  untrusted flags    3
  receipt receipt_blurry.png: NEEDS_CONFIRMATION
  ...
```

### Step 2. Start the screen

```
python tasks.py ui
```

The first time, Streamlit (the screen toolkit) may ask for an e-mail address in the terminal:
just press **Enter** to skip. When you see `Local URL: http://127.0.0.1:8501`, open that
address in your browser. Keep the terminal open: closing it stops LoanReady.

### Step 3. Stop LoanReady

Click in the terminal and press **Ctrl + C**.

> **Tip: one command for both steps.** `python tasks.py run` seeds the data if there is none
> yet and then starts the screen.

> **Important:** if you run `tasks.py seed` again while the screen is open, restart the
> screen afterwards (Ctrl + C, then `python tasks.py ui`). Seeding replaces the database
> underneath the running screen.

---

## 5. Find your way around the screen

### 5.1 The layout

```
┌──────────────────────────┬──────────────────────────────────────────────────┐
│  Home                    │                                                  │
│  Capture                 │                                                  │
│  Books                   │              The page you selected               │
│  Forecast                │                                                  │
│  Action plan             │                                                  │
│  Financing               │                                                  │
│  Loan pack               │                                                  │
│  Log and security        │                                                  │
│ ──────────────────────── │                                                  │
│  LoanReady               │                                                  │
│  Acting as  [Owner ▾]    │                                                  │
│  Principal: Rina (owner) │                                                  │
│  Kill switch: off        │                                                  │
│  Remote AI: off / on     │                                                  │
│  AS_OF 2026-10-14        │                                                  │
│  > AI status             │                                                  │
│  SYNTHETIC DATA banner   │                                                  │
└──────────────────────────┴──────────────────────────────────────────────────┘
```

The **left sidebar** is the same on every page. Click a page name at the top to open it.

![The sidebar with AI status open: hybrid mode, the models in use, today's OpenAI calls and spend, and the backend each task uses](images/02_sidebar_ai_status.png)
*The sidebar with AI status open: hybrid mode, the models in use, today's OpenAI calls and spend, and the backend each task uses*

### 5.2 The sidebar items

| Item | What it means | What you can do |
|---|---|---|
| **Acting as** | Who you are pretending to be: **Owner** (Rina) or **Bookkeeper** | Switch roles to see what each may do. The bookkeeper cannot approve payments or change limits |
| **Principal** | The name the audit log records for your actions | — |
| **Kill switch: off / ON** | Emergency stop. When ON, nothing can be sent or paid | The owner changes it on **Log and security** |
| **Remote AI: off / on** | Whether some tasks may use OpenAI | Set in `.env` (see [§10](#10-turn-on-ai-local-model-and-openai)) |
| **AS_OF 2026-10-14** | The business date everything is calculated for | — |
| **AI status** (click to open) | AI mode, which AI backends are available, models, remote calls today and the budget, and which backend each task uses | — |
| **Synthetic data** banner | Reminder that all data is made up | — |

### 5.3 Things that behave the same everywhere

- **Wait after clicking.** After any button that changes something, the page greys out for
  5-10 seconds while LoanReady recalculates the books, forecast and plan. Wait until it is
  clear again before clicking the next thing.
- **Green, yellow and red messages** appear at the top of the page after an action: green =
  done, yellow = warning or blocked, red = error. They explain what happened in plain
  language and give the log number (for example `log #46`).
- **Money** is always shown as `$1,234.56`. Every number is calculated by LoanReady's code;
  the AI never calculates money.
- **"Tier" badges**: *Tier 1: draft only, nothing is sent* means the button only prepares
  something; *Tier 2: needs your approval* means the button creates a request you must
  approve before anything leaves LoanReady.
- **Use the sidebar to change pages.** Typing an address in the browser bar reloads
  LoanReady and forgets screen-only details such as the last generated loan pack (your data
  is kept).

---

## 6. The pages, one by one

### 6.1 Home (the dashboard)

**What you see:** eight tiles.

![The Home dashboard at the start of the demo: cash $2,400.40, negative in week 3, 6 items to confirm](images/01_home.png)
*The Home dashboard at the start of the demo: cash $2,400.40, negative in week 3, 6 items to confirm*

| Tile | Meaning | At the start of the demo |
|---|---|---|
| Cash today | Money in the bank plus the till, according to the books | $2,400.40 |
| Base case first negative week | The week the likely forecast drops below zero | week 3 |
| Recommended gap to cover | How much cash to find, including a safety buffer | about $6,000 |
| Readiness score | How ready the business looks to a lender, 0-100 | 72 / 100 |
| Pending approvals | Requests waiting for you | 0 |
| Confirm queue | Items LoanReady needs you to decide | 6 |
| Blocked attempts | Actions the safety gate refused | 0 |
| Audit chain | Whether the log is untampered | intact |

If there are pending approvals, a **Go to the approval queue** link appears. The **Run Rina
story** button is greyed out on purpose: the guided demo is not built; use the pages instead
(or the headless demo, [§9](#9-run-the-story-without-the-screen)).

### 6.2 Capture

This page brings records in and asks you about anything unclear.

**Import records → Sync POS and bank.** Pulls new till sales and bank transactions from the
simulator, files them and posts the books. After seeding everything is already imported, so
a sync finds nothing new; it is useful after new activity (for example after a payment you
approved). The result line says how many lines were read, posted and flagged.

**Upload a receipt.** Click **Upload** (or drag a file onto the box), then click **Read
receipt**. LoanReady shows the vendor, total, date, a confidence score and the text it read.
If the receipt contains text that looks like an instruction (for example "ignore previous
instructions"), a yellow box says it was **ignored**, and shows the text in a grey code box,
never as a command.

> In this version receipts are read from a text file stored next to each demo image
> (`data/receipts/*.ocr.json`). The four demo receipts work; a new photo without such a file
> cannot be read and will ask you to confirm it.

**Confirm queue.** Each card is one question for you. The suggested answer is marked
**(proposed)**; click the answer you want.

![The confirm queue: three personal expenses, a duplicate print-shop charge and an unclear deposit, each with the proposed answer marked](images/03_capture_confirm_queue.png)
*The confirm queue: three personal expenses, a duplicate print-shop charge and an unclear deposit, each with the proposed answer marked*

| Card | Example | What the buttons do |
|---|---|---|
| Personal expense? | *Is 'FRESHMART SUPERSTORE' ($86.40) a personal expense?* | **PERSONAL (proposed)** files it as the owner's own spending (not a business cost); **6900** files it as a business "other expense" |
| Duplicate? | *'PRINT SHOP PROMO' ($120.00) looks like a repeat of …* | **duplicate_claim_refund (proposed)**: you were charged twice, and the money is recorded as owed back to you; **genuine_second_purchase**: it really was two purchases |
| Which account? | *Which account is 'CUSTOMER DEPOSIT …' ($45.00)?* | pick the account; the proposed one is LoanReady's best guess |
| Receipt check | *Please check this receipt: GRAIN DEPOT WHOLESALE, total $31.60* | **confirm** accepts what was read; **reject** discards it |

Each click takes 5-10 seconds, then the card disappears and a green "Confirmed …" message
appears. Rina's queue starts with six cards: three personal expenses, one duplicate, one
unclear deposit and one blurry receipt.

**Ignored instructions found in documents.** A list of every place where LoanReady found text
that tried to give it orders (in a bank memo, a till note, a bill and a receipt). They were
all ignored and logged; nothing was done because of them.

![Four planted instructions found and ignored: in a till note, a bank memo, a bill and a receipt](images/04_capture_ignored_instructions.png)
*Four planted instructions found and ignored: in a till note, a bank memo, a bill and a receipt*

### 6.3 Books

Six tabs. Every tab says which accounting framework is used (IFRS for SMEs by default).

| Tab | What it shows | How to read it |
|---|---|---|
| **Journal** | Every bookkeeping entry (5,000+ for Rina) | Type in **Filter** to search by memo, account number or entry id |
| **P&L** | Profit and loss by month: sales, cost of goods, gross profit, expenses, net profit | The last line under the table gives the net profit for the period |
| **Cash flow** | Where cash came from and went, by month, split into operating, investing and financing | Opening cash + the three totals = closing cash |
| **Balance sheet** | What the business owns and owes at AS_OF | Total assets = total liabilities + total equity |
| **Reconciliation** | Whether the books agree with the bank | Before you clear the confirm queue the books are $200.40 above the bank, and every difference is listed; after clearing it you see **Reconciled ✓**, 100% matched, ledger $1,900.00 = bank $1,900.00 |
| **Receivables** | Who owes money and for how long (buckets: current, 1-30, 31-60, 61-90, 90+ days) | Cafe Lumen's $600.00 invoice sits in 61-90 days |

![Reconciliation after the confirm queue is cleared: 100% of bank lines matched, ledger cash equals the bank](images/05_books_reconciliation.png)
*Reconciliation after the confirm queue is cleared: 100% of bank lines matched, ledger cash equals the bank*

![Profit and loss by month](images/06_books_pnl.png)
*Profit and loss by month*

### 6.4 Forecast

**What you see, from top to bottom:**

1. Three tiles: **Low case**, **Base case**, **High case**, each saying the week cash first
   goes below zero (or "stays above zero"). Hover for the lowest amount.
2. A chart: the base line with a shaded band from low to high, a zero line and a marker at the
   week the base case goes negative. Open **Chart as a table** for the numbers.
3. **Why**: a short explanation of the shortfall. When AI is on it is marked *AI-generated
   explanation of figures calculated by LoanReady*; every figure in it is checked against
   LoanReady's own numbers before it is shown.
4. **Largest outflows** in the critical week (for Rina: BILL-2207 Golden Mills $6,500.00, rent,
   ingredients) and **Customer payments expected late** (INV-1043, INV-1051 and others).
5. The gap to cover and for how long (Rina: $6,200.00 recommended, about 49 days).
6. **Based on assumptions** (click to open): every assumption behind each case, in words.
7. **Excluded pending verification**: bills left out of the forecast because they look
   fraudulent. For Rina: **BILL-2211**, which asks to be paid to a different bank account than
   the one on file.
8. **Weekly detail**: choose **Base**, **Low** or **High** to see all 13 weeks line by line.


![The 13-week forecast: the base line inside the low-high band, below zero in week 3](images/07_forecast_chart.png)
*The 13-week forecast: the base line inside the low-high band, below zero in week 3*

![The AI explanation, the largest outflows, the late customer payments and the fraudulent bill excluded pending verification](images/08_forecast_why_excluded.png)
*The AI explanation, the largest outflows, the late customer payments and the fraudulent bill excluded pending verification*

> The forecast is a range, not a promise. The low and high cases show how bad or good it
> could reasonably get.

### 6.5 Action plan

This is where you act on the forecast.

**Closing the cash gap, cheapest lever first.** Three boxes:

| Box | Meaning | Rina |
|---|---|---|
| **1. COLLECT what customers owe** | Overdue invoices to chase (free) | closes $3,200.00 (INV-1043, INV-1051) |
| **2. DELAY bills that allow it** | Bills with no late fee and a grace period, moved within it (free) | closes $600.00 (BoxCo BILL-2215) |
| **3. BORROW the rest, cheapest first** | What is left goes to the Financing page | $1,000.00 |

![The three levers: collect closes $3,200.00, delay closes $600.00, $1,000.00 is left to borrow](images/09_action_plan_levers.png)
*The three levers: collect closes $3,200.00, delay closes $600.00, $1,000.00 is left to borrow*

**Drafts (Tier 1: nothing is sent until you approve).**

1. Click **Draft payment reminders**. One draft appears per overdue invoice. Each shows the
   recipient (partly hidden, for example `a***@bright-events.test`), the channel (EMAIL or
   SMS), a label saying whether AI helped write it, and the subject.
2. Click **Preview message** to read the full text. LoanReady has already checked it: the
   invoice number, amount and due date are correct, and it contains no other amounts, links,
   bank details, phone numbers or threats. If an AI wording failed that check, the standard
   template is used instead.
3. Click **Propose sending** under a draft. This does **not** send it: it creates an approval
   request in the queue below.
4. **Draft delay requests** works the same way for bills you can delay.

![An AI-drafted reminder with its preview, labelled as drafted with AI assistance; nothing is sent yet](images/10_action_plan_drafts.png)
*An AI-drafted reminder with its preview, labelled as drafted with AI assistance; nothing is sent yet*

After sending, the draft shows **Sent. Sandbox reference: msg_…**.

![After approval: executed in the messaging sandbox, with its reference and log number](images/12_reminder_sent.png)
*After approval: executed in the messaging sandbox, with its reference and log number*

**Approval queue (Tier 2).** Every request waiting for approval, with a **Review and
approve** button that opens the [approval dialog](#7-the-approval-dialog).

**Pay a supplier bill.**

1. In **Open bill**, choose a bill. Each entry shows the bill number, the amount still owed
   and the due date (for example *BILL-2190, $900.00 due 2026-10-16*). To pay a bill that is
   not in the list, choose **Type a bill id** and type its id in **Bill id (if not in the
   list)**.
2. Choose a **Value date** (the day the payment should go out; not before AS_OF).
3. Click **Propose payment**. LoanReady checks the bill and the supplier's bank account on
   file. If everything is fine, a request appears in the approval queue; if not, a yellow
   **Blocked** message explains why.

> You never type an amount or a bank account. LoanReady always pays the amount still owed, to
> the account it already has on file for that supplier. This is what stops fake invoices:
> BILL-2211 asks for payment to a new account, so it is blocked with *"The bill asks for
> payment to a different bank account than the one we have on file for this supplier. Call
> the supplier on a known number to verify."*

![The fraudulent bill BILL-2211 is blocked (ACCOUNT_CHANGED) and logged](images/13_blocked_fraud_payment.png)
*The fraudulent bill BILL-2211 is blocked (ACCOUNT_CHANGED) and logged*

### 6.6 Financing

A red-bordered note at the top: *LoanReady compares costs. It does not lend or arrange loans.
Not financial advice.*

1. **Amount needed** and **Term (days)** are pre-filled from the action plan (Rina: $1,000.00
   for 49 days). Change them and click **Compare** to try other amounts.
2. **The table** lists seven example products (all fictional) sorted by true yearly cost
   (**EAR**), with the nominal rate (**APR**) beside it, total cost in dollars, term and
   eligibility. Products the request does not qualify for are marked ineligible with the
   reason (for example "below the product minimum").
3. A green line names the **cheapest** option and a red line the **most expensive**. For
   Rina: Golden Mills 30-day trade credit at 0.00%, and the merchant cash advance at about
   1,750% a year.
4. A blue note explains the merchant cash advance: it charges a fixed fee (a "factor" of
   1.20 means repaying 1.20 times the amount) taken from daily card sales, which over a short
   time is extremely expensive per year.
5. **Explanation**: a plain-language summary (AI-labelled when AI wrote it; numbers checked).

![The financing comparison for $1,000.00 over 49 days, ranked by EAR: supplier credit cheapest, the merchant cash advance most expensive](images/15_financing_comparison.png)
*The financing comparison for $1,000.00 over 49 days, ranked by EAR: supplier credit cheapest, the merchant cash advance most expensive*

![The AI explanation of the comparison and the per-product disclosures](images/16_financing_explanation.png)
*The AI explanation of the comparison and the per-product disclosures*
6. **Disclosures**: click any product to see funding provided, finance charge, total
   repayment, APR and EAR, number and size of payments, prepayment and collateral notes.
7. **Loan readiness**: a gauge and a score out of 100 with a band (LOW, FAIR, GOOD), a table
   of the seven parts of the score, and **How to improve**: concrete steps with the points
   each would add (for example "Collect $3,750.00 of overdue invoices: +5.6 points"). The
   score uses business data only and is not a credit score.


![The readiness score: 86 / 100 (Good), its seven parts and how to improve it](images/17_readiness_score.png)
*The readiness score: 86 / 100 (Good), its seven parts and how to improve it*

> **EAR vs APR.** APR is the daily cost × 365. EAR also counts the effect of repaying and
> re-borrowing ("compounding"), so it is the fairer way to compare short, expensive credit.
> LoanReady ranks by EAR.

### 6.7 Loan pack

1. Check **Requested amount**, **Term (days)** and **Purpose** (pre-filled), then click
   **Generate pack**. This is Tier 1: it only creates a PDF on your computer.
2. The **Summary** shows the requested amount, accounting framework, AS_OF, business name, the
   13 sections in the pack, the pack id and its fingerprint (SHA-256).
3. Click **Download loan pack PDF** to save and open it. Every page is marked
   *SYNTHETIC DATA — PROTOTYPE — NOT A REAL APPLICATION*.
4. Under **Submit to the pretend lender** (Tier 2), click **Submit to lender**, then **Review
   and approve**, then **Approve and execute** in the dialog.
5. **Lender receipt** appears: a receipt id (`rcv_…`), the lender's status
   (*received_under_review*) and any missing sections (*none*).

![The generated pack's summary and the lender receipt after an approved submission](images/18_loan_pack_receipt.png)
*The generated pack's summary and the lender receipt after an approved submission*

> If the PDF is changed after you generate it, the submission is blocked
> (`PACK_HASH_MISMATCH`). Generate it again. LoanReady never accepts a loan; that action is
> permanently blocked.

### 6.8 Log and security

**Audit log (hash-chained, append only).** Every decision LoanReady has made, newest first:
allowed, drafted, pending, approved, rejected, blocked, executed, failed and security events.

- **Filter by decision**: choose one or more types (for example BLOCKED) to narrow the list.
- **Verify chain**: recomputes every row's fingerprint. Expected: *Chain intact: every row hash
  checks out (N rows).* If anyone had edited a row, this would say where.
- **Export CSV**, then **Download action_log.csv**: the full log as a spreadsheet file.


![The audit log with decision counts, after Verify chain: chain intact, including the blocked ACCEPT_LOAN attempt](images/19_log_verify_chain.png)
*The audit log with decision counts, after Verify chain: chain intact, including the blocked ACCEPT_LOAN attempt*

**Limits and kill switch.**

- A gauge and bar show **Paid today vs per-day cap**, and the four limits: per action
  ($5,000.00), per day ($10,000.00), per payee per week ($8,000.00), messages per customer per
  week (2).
- **Kill switch (stops every send and payment)**: owner only. Turn it on and every send,
  payment and submission is refused until you turn it off.
- **Edit limits (owner only)**: change a limit and click **Save limits**. Each change is
  logged.

**Try a blocked action.** Four buttons ask LoanReady to do things it must never do:
**ACCEPT_LOAN** (accept a loan offer), **CHANGE_LIMITS** (raise the daily payment limit
without the owner's form), **PAY_NEW_PAYEE** (pay someone never seen before) and
**DISABLE_GATE** (turn off the safety gate). Click **Try it**: each one is refused with a
yellow message and appears in the log as BLOCKED. This proves the rules hold.


![Limits, the kill switch and the four blocked-action tests](images/20_limits_and_blocked_actions.png)
*Limits, the kill switch and the four blocked-action tests*


**Tier table / Controls.** Two tabs: every action type with its tier and safeguards, and every
security control with the rules it is designed around. Both are produced from the code.

---

## 7. The approval dialog

Every Tier 2 action (sending a reminder or delay request, paying a supplier, cancelling a
payment, submitting the loan pack) opens this window when you click **Review and approve**.

| Field | What to check |
|---|---|
| Action type and tier | for example SEND_REMINDER or SCHEDULE_SUPPLIER_PAYMENT, *Tier 2: needs your approval* |
| **Amount** | the exact amount (invoice amount for a reminder; amount owed for a payment) |
| **Payee / recipient** | who receives it |
| **Account** | where it goes, partly hidden (for example `****2277` or `a***@…`) |
| **Reason** | for example *Pay BILL-2190 to Golden Mills* |
| **Sandbox target** | which simulated service will receive it (for example *MockBank POST /bank/payments/schedule*) |
| **Cap usage today** and **Per-action cap** | how much of today's limit is used |
| Warnings (yellow) | for example *First payment to this payee in 30 days* |
| Validity (blue) | *This approval is valid for 15 minutes and only for exactly these details.* |

![Approving a reminder: amount, recipient (masked), reason, target and the 15-minute validity](images/11_approval_dialog_reminder.png)
*Approving a reminder: amount, recipient (masked), reason, target and the 15-minute validity*

![Approving a supplier payment: $900.00 to Golden Mills, account masked as ****2277, with a first-payment warning](images/14_approval_dialog_payment.png)
*Approving a supplier payment: $900.00 to Golden Mills, account masked as ****2277, with a first-payment warning*

Then either:

- **Approve and execute**: LoanReady re-checks everything and performs the action in the
  simulator. A green message gives the reference (for example *Executed in the sandbox.
  Reference: pay_…*).
- **Reject** (optionally type a **Rejection note** first): nothing happens; the rejection is
  logged.

To change anything (amount, recipient, text), reject and create a new request: an approval
covers exactly the details shown and nothing else. If you are acting as **Bookkeeper**, the
**Approve and execute** button is disabled for payments (only the owner may approve them), and
a red line explains why.

---

## 8. Walk through the whole story (the demo script)

About 15 minutes. Start from fresh data: stop the screen, run `python tasks.py seed`, then
`python tasks.py ui`. Stay as **Owner**.

| # | Page | Do this | You should see |
|---|---|---|---|
| 1 | Home | Look at the tiles | Cash $2,400.40, negative in week 3, confirm queue 6, chain intact |
| 2 | Capture | In **Confirm queue**, click **PERSONAL (proposed)** on the three personal-expense cards (Freshmart, Cineplex, mobile top-up), one at a time, waiting for each | Each card disappears with a green "Confirmed … as PERSONAL" |
| 3 | Capture | Click **duplicate_claim_refund (proposed)** on the print-shop card | Card disappears |
| 4 | Capture | Click **6900 (proposed)** on the customer-deposit card | Card disappears; only the blurry receipt is left (leave it, as in the story) |
| 5 | Capture | Scroll to **Ignored instructions found in documents** | Four warnings: POS, bank, bill, receipt |
| 6 | Books → Reconciliation | Open the tab | **Reconciled ✓**, 100% matched, ledger $1,900.00 = bank $1,900.00 |
| 7 | Forecast | Read the page | Base case below zero in week 3; largest outflow BILL-2207 (Golden Mills) $6,500.00; BILL-2211 listed under *Excluded pending verification* |
| 8 | Action plan | Click **Draft payment reminders** | "2 reminder draft(s) created": INV-1043 and INV-1051 |
| 9 | Action plan | Under the first draft click **Propose sending**, then **Review and approve**, check the dialog, click **Approve and execute** | "Executed in the sandbox. Reference: msg_…"; the draft shows **Sent** |
| 10 | Action plan | Repeat step 9 for the second reminder | Sent |
| 11 | Action plan | Click **Draft delay requests**, then propose, review and approve it | The BoxCo delay request is sent |
| 12 | Action plan | **Pay a supplier bill**: choose **BILL-2211**, click **Propose payment** | Yellow **Blocked** with the *different bank account* explanation (ACCOUNT_CHANGED) |
| 13 | Action plan | Choose **BILL-2190, $900.00**, click **Propose payment**, then **Review and approve** | Dialog: $900.00 to Golden Mills, account ****2277, warning *First payment to this payee in 30 days* |
| 14 | Action plan | Click **Approve and execute** | "Executed in the sandbox. Reference: pay_…" |
| 15 | Financing | Read the comparison | $1,000.00 for 49 days; cheapest Golden Mills 30-day trade credit (0.00%); most expensive merchant cash advance; readiness 86 / 100 GOOD |
| 16 | Loan pack | Click **Generate pack**, then **Download loan pack PDF** and open it | A multi-page PDF with 13 sections and the synthetic-data banner |
| 17 | Loan pack | **Submit to lender** → **Review and approve** → **Approve and execute** | Lender receipt `rcv_…`, status *received_under_review*, missing sections *none* |
| 18 | Log and security | Under **Try a blocked action**, click **Try it** under **ACCEPT_LOAN** | Yellow refusal: this kind of action is never allowed |
| 19 | Log and security | Click **Verify chain** | *Chain intact: every row hash checks out* |
| 20 | Log and security | Click **Export CSV**, then **Download action_log.csv** | A spreadsheet of every decision, including the blocked ones |

Optional extras: switch **Acting as** to **Bookkeeper** and open a payment approval (the
approve button is disabled); turn the **Kill switch** on and try to send a reminder (blocked),
then turn it off again.

---

## 9. Run the story without the screen

These commands are how the evidence for judges is produced. They always use the built-in
mock AI (free and identical every run), whatever `.env` says.

### 9.1 The headless demo

```
python tasks.py demo
```

Plays all ten steps of the story automatically (under a minute) and prints a report:

```
 1. Capture            6 items to confirm
    S1   OK   3 personal expenses found
    ...
10. Evidence           47 log rows; chain OK
ALL CHECKS PASSED; summary: …/evidence/demo_summary.json
```

If any check fails it says **SOME CHECKS FAILED** and exits with an error code. It resets the
demo database first, so restart the screen afterwards if it was open.

### 9.2 Export the evidence

```
python tasks.py evidence
```

Run it after the demo. It writes into the `evidence/` folder (about 2 minutes, because it also
runs the test suite):

| File | What it is |
|---|---|
| `demo_summary.json` | Every scenario check, decision counts, chain status and headline figures |
| `action_log.csv` | The full audit log |
| `chain_verification.txt` | The audit chain re-checked, with its final fingerprint |
| `sandbox_ops.csv` | The simulator's own independent log of every request it received |
| `sandbox_chain_verification.txt` | The simulator's four logs re-checked |
| `test_report.txt` | Summary of the automated tests |
| `README.md` | What each file proves |

Add `--no-tests` to skip the test run: `python -m scripts.export_evidence --no-tests`.

### 9.3 Regenerate the documents

```
python tasks.py docs
```

Rebuilds the tier table, the compliance matrix and the third-party licence list from the
code. (It also prints a note that the traceability table is not built; that is expected.)

---

## 10. Turn on AI: local model and OpenAI

By default LoanReady uses a **mock** AI: keyword rules and fixed message templates. It needs
nothing installed, costs nothing and always gives the same answers. You can switch on real AI
for three jobs: suggesting categories, wording messages and explaining figures. Even with real
AI on, **the AI never decides anything**: its suggestions are checked by code, its category
guesses from a local model always wait for your confirmation, and nothing it writes is sent
without your approval.

### 10.1 The four modes

Set `LLM_BACKEND` in `.env`:

| Value | What happens |
|---|---|
| `mock` | Built-in rules and templates only (default) |
| `ollama` | A free AI model running on your own computer, then mock if it fails |
| `openai` | OpenAI's service for the jobs that may use it, then mock |
| `hybrid` | Best of both: OpenAI writes messages and explanations, the local model suggests categories, and anything involving documents stays on your computer |

### 10.2 A local model with Ollama (free, private)

1. Install Ollama from ollama.com and start it.
2. Download a model in a terminal:
   ```
   ollama pull phi3:mini
   ```
3. In `.env` set `LLM_BACKEND=ollama` (or `hybrid`) and `OLLAMA_MODEL=phi3:mini`.
4. Restart the screen (`Ctrl + C`, then `python tasks.py ui`).

On an ordinary laptop a local model is slow (about 10 seconds per category suggestion and over
a minute per message). If messages time out, raise `OLLAMA_TIMEOUT_S` to `120`.

### 10.3 OpenAI (fast, costs a little)

1. **Get a key.** Sign in at platform.openai.com, open **API keys**, click **Create new secret
   key** and copy it (it starts with `sk-`; it is shown only once). Make sure your account has
   credit on the **Billing** page.
2. **Add it to `.env`**, on the line `OPENAI_API_KEY=`:
   ```
   OPENAI_API_KEY=sk-...your key...
   ```
   No quotes, no spaces, no comment on that line. `.env` is never committed or shared.
3. **See which models your key can use:**
   ```
   python -m agent.health
   ```
   The last line lists small models. Choose a plain chat model such as **`gpt-4o-mini`**.
   Avoid "reasoning" models (`gpt-5-nano`, `o4-mini`, …) for LoanReady: they spend part of a
   reply on hidden reasoning and may run out before answering.
4. **Set the model and its prices** in `.env`:
   ```
   OPENAI_MODEL=gpt-4o-mini
   OPENAI_PRICE_IN_PER_MTOK=0.25
   OPENAI_PRICE_OUT_PER_MTOK=1.50
   ```
   The prices are dollars per million tokens and are used to estimate spending. Copy the real
   ones from OpenAI's pricing page, or keep slightly higher numbers so the estimate errs on the
   safe side.
5. **Set your spending limits** (LoanReady stops using OpenAI when any is reached):
   ```
   OPENAI_MAX_CALLS=300           # calls per day
   OPENAI_MAX_USD=2.00            # dollars per day
   OPENAI_TOTAL_MAX_USD=4.50      # dollars in total, ever (set below your prepaid credit)
   ```
6. **Switch it on:** `LLM_BACKEND=hybrid`.
7. **Check:** run `python -m agent.health` again. You should see
   `available: ['OLLAMA', 'OPENAI', 'MOCK']` (OLLAMA only if it is running), the reminder
   route starting with `OPENAI`, and `remote_budget` with `usd_total: 0`.
8. Restart the screen. The sidebar shows **Remote AI: on**; open **AI status** to see the
   routes and the budget.

**How much does it cost?** With `gpt-4o-mini`, a reminder or an explanation costs a small
fraction of a cent (about $0.0002 at the prices above). Repeated identical questions are
answered from a cache for free. Check spending at any time with `python -m agent.health`
(`usd_today`, `usd_total`); OpenAI's own billing page is the final authority.

**What is sent to OpenAI?** Only what each job needs, with customer and supplier names
replaced by placeholders and long numbers, e-mails and phone numbers removed. Bank details,
documents and receipt text are never sent; that is enforced in code and tested. Every call is
logged as `LLM_REMOTE_CALL` (task and model only, never the content).

**If OpenAI fails** (no credit, no internet, a limit reached), LoanReady quietly falls back to
the local model or the templates and keeps working; the log records `LLM_FALLBACK_USED`.

---

## 11. Everyday tasks and settings

### 11.1 Start again from fresh data

1. Stop the screen (Ctrl + C).
2. Run `python tasks.py seed`.
3. Start the screen again: `python tasks.py ui`.

To wipe all runtime data (databases, generated packs, logs) first, run
`python tasks.py clean`; it refuses to run while the screen or simulator is still running.

### 11.2 Switch accounting framework

In `.env`, set `LOANREADY_FRAMEWORK=US_GAAP` (default `IFRS_SME`) and restart the screen.
Statement titles and the loan pack follow the setting.

### 11.3 Run the bank simulator as a separate service

To show the bank as an independent system (for example for a video):

1. Stop the screen. In `.env` set `SANDBOX_MODE=external`.
2. Terminal 1: `python tasks.py sandbox`. Open http://127.0.0.1:8100/docs to see the bank's
   API.
3. Terminal 2: `python tasks.py ui`.

Never run an external simulator and a normal (`inprocess`) screen on the same data at the same
time. Set `SANDBOX_MODE=inprocess` again when you are done.

### 11.4 Change limits

Use **Log and security → Edit limits (owner only)**, or set the `CAP_…` values in `.env`
(amounts in cents, for example `CAP_PER_DAY_CENTS=1000000` for $10,000.00).

### 11.5 Work as the bookkeeper

Choose **Bookkeeper** in **Acting as**. A bookkeeper can confirm items, draft messages, propose
payments and approve messages and the loan-pack submission, but cannot approve payments,
change limits or use the kill switch. Switch back to **Owner** to approve payments.

### 11.6 Try the screen with canned data only

`LOANREADY_UI_FAKE=1 python tasks.py ui` (Windows PowerShell:
`$env:LOANREADY_UI_FAKE="1"; python tasks.py ui`) runs the screen on built-in sample data
without the real engine. The sidebar then shows a yellow *Demo data* warning.

---

## 12. Command reference

Type each command in the project folder. On Windows, `py -3.12 tasks.py …` works the same as
`python tasks.py …`.

| Command | What it does | Time |
|---|---|---|
| `python tasks.py setup` | Create `.venv` and install the exact packages (first time) | 2-5 min |
| `python tasks.py doctor` | Check Python, packages, files and disk | seconds |
| `python tasks.py gen` | Generate Rina's Bakery's data | ~10 s |
| `python tasks.py seed` | Load the data into the simulator and the books (generates it if missing) | ~25 s |
| `python tasks.py ui` | Start the screen at http://127.0.0.1:8501 | — |
| `python tasks.py run` | Seed if needed, then start the screen | — |
| `python tasks.py demo` | Play the whole story headlessly and check S1-S10 | < 1 min |
| `python tasks.py evidence` | Export logs, chain checks and the test report to `evidence/` | ~2 min |
| `python tasks.py docs` | Regenerate the tier table, compliance matrix and licence list | seconds |
| `python tasks.py test` | Run the automated tests (`--all` adds slow ones; `test books` runs one package) | ~2 min |
| `python tasks.py lint` | Code style and import-rule checks | seconds |
| `python tasks.py integrate` | The full integration check (layers, static rules, every package's tests) | ~3 min |
| `python tasks.py sandbox` | Run the simulator as a separate server on port 8100 | — |
| `python tasks.py clean` | Delete runtime data in `var/` (refuses while running) | seconds |
| `python -m agent.health` | Show AI mode, available backends, routes and OpenAI budget | seconds |
| `python -m scripts.capture_screenshots` | Re-take this guide's screenshots from the real app on a temporary copy of the data (needs Chromium; uses OpenAI if it is on) | ~5 min |
| `python -m scripts.build_pdfs` | Rebuild the PDF editions of this guide and the technical documentation (needs pandoc and Chromium) | ~30 s |

---

## 13. Troubleshooting

### 13.1 Installation and start-up

| Problem | Fix |
|---|---|
| `py` or `python3.12` not found | Install Python 3.12 (Windows: tick *Add python.exe to PATH*), open a new terminal |
| `doctor` says *Python version* is wrong | Use Python 3.12 exactly: `py -3.12 tasks.py setup` |
| `doctor` says *lock mismatch* or extra packages | Someone installed packages by hand. Delete the `.venv` folder and run `setup` again |
| `doctor` says *manifest mismatch* | A protected file was changed. Restore it from the original copy |
| Windows: the terminal crashes printing `doctor` | Run `$env:PYTHONIOENCODING="utf-8"` first, then `doctor` |
| Windows: "database is locked" or files cannot be deleted | Move the project out of OneDrive to a short path like `C:\dev\loanready`; stop the screen before `clean` |
| `Port 8501 is not available` | LoanReady is already running in another terminal: use that one, or stop it with Ctrl + C |
| The browser shows nothing | Wait 10-20 s on the first start; make sure the address is `http://127.0.0.1:8501` |
| Streamlit asks for an e-mail in the terminal | Press Enter |

### 13.2 Using the screen

| Problem | Fix |
|---|---|
| The page is grey and does nothing | It is recalculating; wait 5-10 s |
| Numbers look wrong or old after `seed` or `demo` | Restart the screen (Ctrl + C, `python tasks.py ui`) |
| Tiles show "…" instead of full numbers | The browser window is narrow; widen it or zoom out |
| The confirm-queue card did not go away | Wait for the page to finish; click only once |
| *No pack generated in this session yet* after changing page | You reloaded the browser address; use the sidebar to move between pages, or generate the pack again |
| The approve button is greyed out | You are acting as Bookkeeper on a payment, or the kill switch is on |
| *The approval expired* | Approvals last 15 minutes; propose again |
| A new receipt photo is not read | This version reads the demo receipts' text files only; confirm or reject the receipt manually |

### 13.3 What a message means

These are the plain-language messages LoanReady shows when it refuses or cannot do something,
with the code you will see in the log:

| Message (short) | Code | What to do |
|---|---|---|
| The kill switch is on, so nothing can be sent or paid | `KILL_SWITCH` | Owner: turn it off on Log and security |
| This kind of action is never allowed in LoanReady | `BLOCKED_TYPE` | Nothing; it is a safety rule |
| The request came from text inside a document or message | `UNTRUSTED_ORIGIN` | Nothing; document text can never start an action |
| The person you are acting as may not do this | `ROLE_NOT_ALLOWED` | Switch **Acting as** to Owner |
| Some details are missing or invalid | `INVALID_PARAMS` | Check the form fields |
| We could not find that record | `UNKNOWN_ENTITY` | Check the bill or invoice id |
| This payee has never been paid before and is not pre-registered | `NEW_PAYEE` | Payments go only to known payees |
| The bill asks for payment to a different bank account than the one on file | `ACCOUNT_CHANGED` | Do not pay; call the supplier on a known number |
| The amount is more than what is still owed | `AMOUNT_EXCEEDS_OUTSTANDING` | Nothing; LoanReady pays the amount owed |
| The invoice is not overdue yet | `NOT_OVERDUE` | Reminders are for overdue invoices only |
| The message changed after it was drafted | `DRAFT_MISMATCH` | Draft it again |
| The loan pack changed after it was generated | `PACK_HASH_MISMATCH` | Generate the pack again |
| The amount is above the per-action limit | `CAP_PER_ACTION` | Owner: raise the limit, or pay in parts |
| This would go over today's payment limit | `CAP_PER_DAY` | Wait until tomorrow, or owner raises the limit |
| This would go over the weekly limit for this payee | `CAP_PER_PAYEE_WEEK` | Wait, or owner raises the limit |
| This customer already got the maximum number of messages this week | `CAP_MSGS_PER_CUSTOMER` | Wait until next week |
| A reminder was sent to this customer too recently | `RATE_LIMIT_REMINDER` | Wait 7 days between reminders |
| The approval expired | `APPROVAL_EXPIRED` | Propose again |
| That approval was already used | `APPROVAL_REPLAY` | Propose again |
| The details changed after approval | `APPROVAL_PARAMS_CHANGED` | Propose again and review the new details |
| You cannot approve your own proposal | `SELF_APPROVAL_FORBIDDEN` | Another person must approve |
| The bank's own limit was exceeded | `BANK_LIMIT_EXCEEDED` | The simulator's bank refused; pay less |
| Too many requests; try again in a minute | `RATE_LIMITED` | Wait a minute |
| Not enough money in the account | `INSUFFICIENT_FUNDS` | Nothing to fix; the simulated account is short |
| The sandbox service returned an error | `SANDBOX_ERROR` | Restart the screen; with an external simulator, check it is running |
| This data may not be sent to a remote AI service | `REMOTE_NOT_ALLOWED` | Nothing; the privacy rule worked and a local answer was used |

### 13.4 AI problems

| Problem | Fix |
|---|---|
| Sidebar says **Remote AI: off** after adding a key | `OPENAI_MODEL` is empty or `LLM_BACKEND` is not `openai`/`hybrid`; restart the screen after editing `.env` |
| `agent.health` says *cannot list models (AuthenticationError)* | The key was pasted wrong; paste it again |
| OPENAI missing from `available` | No model set, or a spending limit is reached (see `remote_budget`) |
| Messages are always the standard template | The AI's wording failed LoanReady's check, the AI was unavailable, or you are in `mock` mode; that is safe behaviour |
| Category suggestions from the local model always ask for confirmation | By design: local-model guesses are suggestions only |
| Everything is slow with Ollama | Normal on a laptop; use `hybrid` with OpenAI for messages, or a smaller model |

---

## 14. Frequently asked questions

**Is any of this real money or a real bank?** No. Everything goes to the built-in simulator,
and every name, account and amount is made up.

**Can the AI pay someone or send a message by itself?** No. The AI can only suggest a
category, word a message from facts LoanReady gives it, or explain figures. Every payment,
message and submission needs your approval in the dialog, and some actions (accepting a loan,
paying a new payee, changing a payee's bank account, editing the log) can never happen at all.

**What stops a fake invoice from being paid?** LoanReady pays only the account it already has
on file for that supplier. If a bill asks for a different account, it is blocked, and the
simulated bank checks the account again on its side.

**What happens to text that tries to trick the AI?** It is treated as data, never as an
instruction. It is shown to you in grey boxes and logged, and it can never start or change an
action.

**Can someone change the log afterwards?** Each row contains a fingerprint of the previous
row, so changing anything breaks every later fingerprint. **Verify chain** detects it. There is
no way in LoanReady to edit or delete log rows.

**Why is the merchant cash advance so expensive when the factor is only 1.20?** A factor of
1.20 means paying back 20% more, however quickly you repay. Repaid in about six weeks from card
sales, that 20% is equivalent to well over 1,000% a year. LoanReady shows this as the EAR.

**Is the readiness score a credit score?** No. It is a self-assessment from the business's own
books to help prepare an application. It uses no personal information.

**Why does the date never change?** LoanReady works "as of" 14 October 2026 so the demo story
is the same every time.

**Do I need internet?** Only to install, and for OpenAI if you choose to use it.

**Where is my data stored?** In the `var/` folder inside the project: `app.db` (the books and
the log), `sbx_*.db` (the simulator), `llm_cache.db` (AI answers and spending), `packs/`
(loan-pack PDFs).

---

## 15. Glossary

| Term | Meaning |
|---|---|
| AS_OF | The business date LoanReady works from (14 October 2026) |
| POS | Point of sale: the till that records each sale |
| Books / journal | The accounting records; every entry has equal debits and credits |
| P&L | Profit and loss statement: income minus costs |
| Balance sheet | What the business owns and owes on a date |
| Cash-flow statement | Where cash came from and went |
| Reconciliation | Checking that the books agree with the bank |
| Receivables | Money customers owe the business |
| Confirm queue | Questions LoanReady needs the owner to answer |
| Forecast cases | Low (cautious), base (most likely) and high (optimistic) |
| Cash gap | The amount of cash needed to stay above zero |
| COLLECT / DELAY / BORROW | The three ways to close a gap, from cheapest to most expensive |
| APR | Yearly rate without compounding (daily cost × 365) |
| EAR | Effective yearly rate including compounding; the fair way to compare credit |
| Factor rate | The way merchant cash advances are priced: repay factor × amount |
| Readiness score | A 0-100 self-assessment of how ready the business looks to a lender |
| Loan pack | The PDF application LoanReady prepares |
| Tier 1 / Tier 2 | Draft only / needs your approval before anything leaves LoanReady |
| Approval dialog | The window where you check and approve a Tier 2 action |
| Kill switch | Emergency stop for all sends and payments |
| Audit log / hash chain | The tamper-evident record of every decision |
| Sandbox / simulator | The built-in pretend bank, till, messaging service and lender |
| Mock AI | LoanReady's built-in, rule-based stand-in for an AI model |
| Ollama | Free software for running an AI model on your own computer |
| `.env` | The settings file in the project folder |

---

*More detail on how everything works: [Technical documentation](technical_doc.md). Security
analysis: [Security self-assessment](security_self_assessment.md).*
