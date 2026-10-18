"""T-SBX-01, T-SBX-10 (rate part): scoped tokens and the write rate limit."""

from __future__ import annotations

from typing import Any

from sandbox.ratelimit import LIMITER, RateLimiter


def test_tokens_and_scopes(sbx: Any) -> None:
    """T-SBX-01 proves SBX-01, SBX-02, CTL-AC-05: no/wrong token 401; read token on a write
    403; admin endpoints need the admin token; tokens never appear in responses."""
    assert sbx.client.get("/bank/accounts").status_code == 401
    r = sbx.client.get("/bank/accounts", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401 and r.json()["code"] == "UNAUTHENTICATED"
    assert sbx.client.get("/bank/accounts", headers={"Authorization": "Basic x"}).status_code == 401
    assert sbx.get("/bank/accounts").status_code == 200
    r = sbx.client.post(
        "/bank/payments/schedule", json={}, headers={**sbx.read, "Idempotency-Key": "k1"}
    )
    assert r.status_code == 403 and r.json()["code"] == "INSUFFICIENT_SCOPE"
    for headers in (sbx.read, sbx.write):
        assert sbx.client.get("/admin/clock", headers=headers).status_code == 403
    assert sbx.client.get("/admin/clock", headers=sbx.admin).json() == {"today": "2026-10-14"}
    assert sbx.get("/bank/accounts", headers=sbx.admin).status_code == 200  # admin includes read
    assert sbx.client.get("/health").status_code == 200  # no auth
    from sandbox.settings import get_settings

    text = sbx.client.get("/admin/opslog.csv", headers=sbx.admin).text
    s = get_settings()
    assert s.read_token not in text and s.write_token not in text and s.admin_token not in text


def test_write_rate_limit(sbx: Any) -> None:
    """T-SBX-10 proves SBX-08, CTL-LIM-06: more than N writes per token per sub-app per
    rolling minute -> 429 RATE_LIMITED with Retry-After; replays count; the window slides."""
    t = [1000.0]
    LIMITER.time_source = lambda: t[0]
    try:
        LIMITER.reset()
        for _i in range(30):
            assert sbx.pay("rl-same").status_code in (201, 200)  # replays of one key count too
            t[0] += 0.5
        r = sbx.pay("rl-31")
        assert r.status_code == 429 and r.json()["code"] == "RATE_LIMITED"
        assert int(r.headers["Retry-After"]) >= 1
        assert (
            sbx.client.post(
                "/messaging/send",
                json={"channel": "EMAIL", "to_ref": "a@b.test", "subject": "s", "body": "b"},
                headers=sbx.w("m1"),
            ).status_code
            == 201
        )
        t[0] += 60
        assert sbx.pay("rl-32").status_code == 201
    finally:
        import time

        LIMITER.time_source = time.monotonic
        LIMITER.reset()


def test_rate_limiter_unit() -> None:
    """SBX-08: injectable time source; per (token, sub-app) windows."""
    t = [0.0]
    rl = RateLimiter(lambda: t[0])
    for _ in range(2):
        rl.hit("write", "bank", limit=2)
    rl.hit("write", "lender", limit=2)
    rl.hit("admin", "bank", limit=2)
    try:
        rl.hit("write", "bank", limit=2)
        raise AssertionError("expected 429")
    except Exception as exc:  # SandboxHTTPError
        assert getattr(exc, "code", "") == "RATE_LIMITED"
        assert exc.headers["Retry-After"] == "60"  # type: ignore[attr-defined]
