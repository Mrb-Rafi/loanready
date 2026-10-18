"""Write the frozen API schema: ``python -m sandbox.dump_openapi`` (SBX-12).

``sandbox/openapi.json`` = ``app.openapi()`` pretty-printed with sorted keys. (Plain
``json`` rather than ``contracts.canonical``: the schema contains JSON numbers such as
float bounds, which canonical JSON rejects by design; sorted keys keep it deterministic.)
``tests/sandbox/test_openapi_frozen.py`` fails if the live schema differs, so an API change
needs a deliberate regeneration and shows up as a diff."""

from __future__ import annotations

import json
import sys

from sandbox.app import OPENAPI_PATH, app


def render() -> str:
    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    OPENAPI_PATH.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {OPENAPI_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
