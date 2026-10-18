# LoanReady

FinTechathon 2026, International Track, Topic E (SME Finance Copilot).

LoanReady turns a young small business's messy records into clean books and a 13-week
cash forecast, closes cash gaps in order of cost (collect, then delay, then borrow), and
prepares a lender-ready application pack. Every action passes a permission gate written in
plain code and lands in a hash-chained audit log.

> Stub written by WP0. WP11 writes the final README (quick start, architecture, tests,
> limitations, licences). The full specification is in [`PRD/`](PRD/README.md).

## Quick start for developers (any PC)

Use a folder whose path has no spaces, and Python 3.12.

```
# Windows:  py -3.12 tasks.py setup        Linux:  python3.12 tasks.py setup
python tasks.py doctor          # all MUST rows must be OK
python tasks.py test contracts
```

Then build only your work package. Development PCs: read [`PC_GUIDE.md`](PC_GUIDE.md) first
(roles, Windows rules, hand-off); hand off with `python tasks.py handoff WPn --device <name>`.
