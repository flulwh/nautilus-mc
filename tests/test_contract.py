"""W0 contract tests: the frontend must only call routes the backend serves.

Two drift channels are checked:

1. ``frontend/src`` API call literals vs ``app.openapi()`` paths — a renamed
   or deleted endpoint fails here instead of in the browser.
2. ``frontend/src/generated/contract.ts`` vs ``nautilus/authz.py`` constants —
   roles/objects/actions are generated, never hand-copied.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_SRC = ROOT / "frontend" / "src"
CONTRACT_TS = FRONTEND_SRC / "generated" / "contract.ts"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# api.get/post/put/patch/del/upload(<generics>)("path" or `path`) — the path
# literal may start on the next line, and generics may contain spaces.
CALL_RE = re.compile(
    r"api\.(get|post|put|patch|del|upload)\s*(?:<.*?>)?\s*\(\s*(`[^`]*`|\"[^\"]*\")",
    re.S,
)
# Direct fetches that bypass the api helper (login/logout, SSE POSTs).
FETCH_RE = re.compile(r"fetch\(`\$\{BASE\}(/[^`]*)`")
STREAM_RE = re.compile(r"(?:consume|streamPost)\(\s*\"(/[^\"]*)\"")

METHODS = {"get": "GET", "post": "POST", "put": "PUT", "patch": "PATCH", "del": "DELETE", "upload": "POST"}


def _normalize(path: str) -> tuple[str, ...]:
    # Replace template/param holes first: ``${a?.b}`` contains a "?" that the
    # query-string split below would otherwise cut on.
    path = re.sub(r"\$\{[^}]*\}", "*", path)  # frontend template holes
    path = re.sub(r"\{[^}]*\}", "*", path)  # OpenAPI {param} segments
    path = path.split("?", 1)[0].split("#", 1)[0]
    return tuple(seg for seg in path.split("/") if seg)


def _frontend_calls() -> list[tuple[str, tuple[str, ...], str]]:
    """(method, segments, origin) for every API path literal in the frontend."""
    calls: list[tuple[str, tuple[str, ...], str]] = []
    for file in sorted(FRONTEND_SRC.rglob("*.ts*")):
        text = file.read_text(encoding="utf-8")
        origin = str(file.relative_to(ROOT))
        for m in CALL_RE.finditer(text):
            verb, raw = m.group(1), m.group(2)[1:-1]
            if not raw.startswith("/"):
                continue  # relative fragments, not endpoints
            calls.append((METHODS[verb], _normalize("/api" + raw), origin))
        for m in FETCH_RE.finditer(text):
            calls.append(("POST", _normalize("/api" + m.group(1)), origin))
        for m in STREAM_RE.finditer(text):
            calls.append(("POST", _normalize("/api" + m.group(1)), origin))
    return calls


def _spec_routes() -> set[tuple[str, tuple[str, ...]]]:
    from nautilus.main import create_app

    spec = create_app().openapi()
    routes = set()
    for path, ops in spec["paths"].items():
        segs = _normalize(path)
        for method in ops:
            if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                routes.add((method.upper(), segs))
    return routes


def _matches(method: str, segs: tuple[str, ...], routes: set[tuple[str, tuple[str, ...]]]) -> bool:
    for m, spec_segs in routes:
        if m != method or len(spec_segs) != len(segs):
            continue
        if all(a == b or a == "*" or b == "*" for a, b in zip(segs, spec_segs)):
            return True
    return False


def test_frontend_calls_exist_in_openapi():
    routes = _spec_routes()
    assert routes, "OpenAPI spec produced no routes"
    calls = _frontend_calls()
    # Sanity: the scanner really parses call sites (this repo has ~90 today).
    assert len(calls) > 50, f"only {len(calls)} frontend API calls parsed; scanner broken?"
    missing = sorted({f"{m} /{'/'.join(s)}  [{o}]" for m, s, o in calls if not _matches(m, s, routes)})
    assert not missing, "frontend calls routes the backend does not serve:\n" + "\n".join(missing)


def test_generated_contract_is_fresh():
    import scripts.gen_contract as gen

    assert CONTRACT_TS.exists(), "frontend/src/generated/contract.ts missing; run scripts/gen_contract.py"
    assert CONTRACT_TS.read_text(encoding="utf-8") == gen.render(), (
        "contract.ts is stale; run `python scripts/gen_contract.py` and commit the result"
    )
