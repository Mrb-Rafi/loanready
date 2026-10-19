# agent (WP7)

LLM layer for LoanReady (spec: `PRD/09_wp7_agent.md`). The model may only (a) pick a label
from a fixed list, (b) word text from facts computed by code, (c) pick an intent from a fixed
menu. It never computes money, checks permissions or chooses a payee, amount or account.

Status: every **MUST** module is built and tested with `LLM_BACKEND=mock`. PC A added the
backends on 2026-10-19: `backends/ollama_backend.py`, `routing.py`, `health.py`, `cache.py`,
`budget.py` (SHOULD) and `backends/openai_backend.py` (OPTIONAL). Not built: `router.py`.

| File (PC A) | What it does |
|---|---|
| `backends/__init__.py` | `BackendError`, per-task output allowance, `strict_schema` for structured outputs |
| `backends/ollama_backend.py` | local model: JSON-schema format, temperature 0, seed 42, 2,048 context, 10-minute keep-alive |
| `backends/openai_backend.py` | structured outputs (strict), own policy check before any network call, budget reserve/record, drops parameters a model rejects |
| `routing.py` | `DEFAULT_ROUTES`, `LLM_ROUTES_FILE` overrides (validated), `resolve()` (forbidden remote removed, mock last) |
| `health.py` | availability (Ollama `/api/tags`, OpenAI key/model/budget), 60 s cache, circuit breaker; `python -m agent.health` |
| `cache.py` | `var/llm_cache.db`, key includes backend and model |
| `budget.py` | calls/day, USD/day, USD all-time (`OPENAI_TOTAL_MAX_USD`), worst-case reservation, micro-dollars |

Classifier change: candidate labels go to the model with their chart-of-accounts names, and a
local model's confidence is capped at 0.75 (below the 0.80 threshold) because `phi3:mini`
was confidently wrong in live tests on PC A.

## Modules

| File | What it does | Priority |
|---|---|---|
| `policy.py` | `TASK_POLICY` (PRD/09 §3), `check_request`, `remote_allowed`, `assert_remote_allowed` (CTL-AI-02) | MUST |
| `masking.py` | `Masker` (names → `{CUSTOMER_n}`/`{SUPPLIER_n}`/`{BUSINESS}`, `{NUM}`, `{EMAIL}`, `{PHONE}`), `assert_no_identifiers` (CTL-AI-03) | MUST |
| `prompts.py` | `SYSTEM_PREFIX`, per-task instructions, output schemas, `data_block`, `assert_untrusted_enclosed`, `build_messages`, templates (CTL-AI-04) | MUST |
| `llm.py` | `LLM` (the `LLMClient`): policy → prompt → resolve → remote guard → mask → call → validate (one retry) → unmask → fallback, ending in mock (CTL-AI-05) | MUST |
| `backends/mock.py` | `MockBackend`: keyword classification (0.60 / 0), templates, intent UNKNOWN; deterministic | MUST |
| `classifier.py` | `LLMClassifier` (`Classifier`): digits masked, label must be a candidate, confidence capped 0.95 | MUST |
| `wording.py` | `LLMWording` (`WordingProvider`): reminder and delay request from facts only | MUST |
| `explain.py` | `explain_forecast`, `explain_financing`, `postcheck` (CTL-AI-01) | MUST |
| `service.py` | `Agent` (`AgentService`): classifier, wording, explanations, `scan`, `status`, `route` stub | MUST |

## Public API

```python
from agent.llm import LLM
from agent.service import Agent

llm = LLM(settings, events=sink)  # sink: Callable[[EventType, dict], None]
agent = Agent(llm, events=sink)  # contracts.services.AgentService

agent.classifier().classify(
    untrusted_memo, candidates, LLMTask.CATEGORISE_TXN
)  # CategorisationResult
agent.wording().word_reminder(facts)  # (subject, body, GeneratedBy)
agent.wording().word_delay_request(facts)
agent.explain_forecast(forecast)  # str
agent.explain_financing(comparison)  # str
agent.scan(untrusted)  # list[ScanFlag]; emits UNTRUSTED_INSTRUCTION_IGNORED
agent.status()  # {mode, available, models, remote_calls_today, remote_ai, remote_tasks}
agent.route(message)  # RouterResult(UNKNOWN, method="fallback") until the router is built
```

`LLM(..., mode="ollama", backends={LLMBackend.OLLAMA: backend})` injects real or stub backends.
A backend has `name: LLMBackend`, `model: str`, `remote: bool` and
`generate(req, messages) -> str` (raw JSON text); it raises `agent.llm.BackendError(reason)`
on timeout, connection, budget or unavailability.

Events emitted (payloads never hold content or secrets):

| Event | Payload |
|---|---|
| `LLM_FALLBACK_USED` | `{task, from, to, reason}`; reason `invalid_json`, `timeout`, `connection`, `policy`, `postcheck`, ... |
| `LLM_REMOTE_CALL` | `{task, data_class, model, cached}` |
| `REMOTE_NOT_ALLOWED` | `{task, data_class, backend, reason}` |
| `UNTRUSTED_INSTRUCTION_IGNORED` | `{source, source_ref, pattern_ids}` |

## Test alone

```
py -3.12 tasks.py test agent      # 104 tests, LLM_BACKEND=mock, no network; coverage 92%
py -3.12 tasks.py lint
```

Test ids: T-AGT-01 `test_policy.py`, T-AGT-02 `test_masking.py` and `test_wording.py`,
T-AGT-03 `test_prompts.py`, T-AGT-04 `test_validation.py`, T-AGT-07 `test_classifier.py`,
T-AGT-09 `test_explain_postcheck.py`, T-AGT-10 `test_service.py`. T-AGT-05 (cache), T-AGT-06
(budget), T-AGT-08 (router) and most of T-AGT-11/12 (hybrid routing, health) belong to SHOULD
modules not built yet; `test_validation.py` covers the parts of T-AGT-11/12 that the MUST
`llm.py` already implements (every mode ends in mock; fallback events with from/to/reason;
`attempts` recorded).

## Assumptions and choices (PRD ambiguities)

1. **Mock wording reports `GeneratedBy.TEMPLATE`.** The mock fills templates, so calling its
   text "LLM" would put the wrong AI label on it (REG-AI-04). Any real backend's text is
   `GeneratedBy.LLM`.
2. **Wording bodies end at the business name, without the AI label line.** PRD/08 §3 step 4
   says WP6 adds the label to the body; the agent's templates are WP6's minus `{ai_label}`.
3. **`unmask` restores the registered spelling.** Names are matched case-insensitively, so
   `unmask(mask(x)) == x` holds when names are written as registered (tested with a
   hypothesis property); "cafe aurora" comes back as "Cafe Aurora".
4. **Name masking for remote calls** uses the payload keys `customer_name`, `supplier_name`,
   `counterparty_name` and `business_name`; callers may pass `complete(req, masker=...)`.
5. **Phones** are masked only with a `+country` or `(area)` prefix, so ISO dates such as
   `2026-10-14` survive; any other digit run of 6+ becomes `{NUM}`.
6. **Classifier data class**: the merchant string is sent as C2 after every digit run and
   email is masked and it is cut to 80 characters (PRD/09 §3 "effectively C2"). The method
   is `LLM` for every valid answer, `FALLBACK` (label 6900, confidence 0) for a label outside
   the candidates (BKS-06); PERSONAL always needs confirmation.
7. **Explanation post-check is stricter than AGT-10**: every number in the model's text
   (amounts, percentages, counts, weeks, dates) must appear in the facts, not only currency
   amounts and percentages. Mock text is a template and is not post-checked.
8. **A request above its task's max class** raises `InvalidParams` (no specific error class
   exists for it).
9. **`hybrid` mode before `routing.py`**: tries a configured local backend, then a
   policy-allowed remote one, then mock. With no real backend configured (this build), every
   mode answers from mock, which PRD/09 §1 allows ("with only mock available it degrades to
   mock").
10. **`route()`** returns `UNKNOWN` with method `fallback` because the router is SHOULD;
    `AgentService` still needs the method.
11. **`latency_ms`** is 0 for mock (keeps evidence byte-identical) and measured with
    `time.perf_counter` for other backends (a duration, not a clock reading).
12. **Secrets**: backend error text is redacted (`sk-...`, `Bearer ...`, `api_key...`)
    before logging; events never carry error text.

## Proposed contract changes

None needed. (Optional, backward compatible: an `EventType.LLM_POSTCHECK_FAILED` would make
explanation post-check rejections easier to tell apart from backend fallbacks; today they
are `LLM_FALLBACK_USED` with `to="TEMPLATE"`, `reason="postcheck"`.)

## Not done here (SHOULD / OPTIONAL, waiting for the team lead's go-ahead)

`router.py` (rule-based intents); OPTIONAL:
LLM intent fallback.
