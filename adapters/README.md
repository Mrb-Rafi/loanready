# adapters (WP2)

Client side of the sandbox (PRD/04 §10): `build_ports(settings, verifier, transport=None)`
returns `contracts.ports.Ports` with `HttpBankPort`, `HttpPosPort`, `HttpMessagePort`,
`HttpLenderPort` and `HttpSandboxAdminPort`.

- Every write verifies the `ExecutionTicket` with the gate's verifier **before** a request
  leaves the process: payment binds payee, account, amount, value date and source account;
  messages bind recipient, channel and the hash of the exact body; the lender binds the hash
  of the decoded PDF and the lender id.
- Writes send `Idempotency-Key = ticket.action_id` and `X-Ticket-Id`. Timeout 10 s; one retry
  only on connection errors; never on an HTTP response (429 included).
- Errors map through `ERROR_MAP` to `SandboxError(reason_code, http_status, sandbox_code)`.
- `_secrets.py` is the only reader of the write token. Adapters never import `sandbox`;
  in-process mode receives an ASGI transport from `infra.sandbox_runner`.

Test alone: `python tasks.py test adapters`.
