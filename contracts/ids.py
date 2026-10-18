"""ID factory (CON-03). ``new(prefix)`` -> ``f"{prefix}_{26 hex chars}"``.

With a seed (test and demo) IDs repeat exactly across runs. Prefixes: act, prp, apv, tkt,
je, rtx, inv, bil, rcp, cpt, pye, drf, pck, cfm.
"""

from __future__ import annotations

import random
import re
import secrets
from collections.abc import Callable

PREFIXES = frozenset(
    {
        "act",
        "prp",
        "apv",
        "tkt",
        "je",
        "rtx",
        "inv",
        "bil",
        "rcp",
        "cpt",
        "pye",
        "drf",
        "pck",
        "cfm",
    }
)
_PREFIX_RE = re.compile(r"^[a-z][a-z0-9]{1,15}$")


class IdFactory:
    def __init__(self, seed: int | None = None):
        self.seed = seed
        self._rng = random.Random(seed) if seed is not None else None

    def new(self, prefix: str) -> str:
        if not _PREFIX_RE.match(prefix):
            raise ValueError(f"invalid id prefix: {prefix!r}")
        bits = self._rng.getrandbits(104) if self._rng is not None else secrets.randbits(104)
        return f"{prefix}_{bits:026x}"


_factory: IdFactory | None = None
_provider: Callable[[], IdFactory] | None = None


def register_id_provider(provider: Callable[[], IdFactory]) -> None:
    global _provider
    _provider = provider


def set_id_factory(factory: IdFactory) -> None:
    global _factory
    _factory = factory


def reset_id_factory() -> None:
    global _factory
    _factory = None


def get_id_factory() -> IdFactory:
    """Process singleton (seeded in test/demo via the provider registered by infra)."""
    global _factory
    if _factory is None:
        _factory = _provider() if _provider is not None else IdFactory()
    return _factory
