"""Measure the demo site against its budgets, and fail when one is missed.

    python harness/perf_check.py                    # start a local server, measure it
    python harness/perf_check.py --url https://...  # measure the deployed one

A judge has about sixty seconds and no patience, and a page that stalls reads as
a broken gate whatever it would eventually have shown. So the budgets below are
a gate with an exit code rather than a judgement call: the printed line and the
exit status are derived from one expression and cannot disagree.

This lives in harness/ rather than tests/ because it is a measurement and needs
a live server. `pytest` must never depend on one being up.

Anything from this run that reaches the README goes in with its date and this
command beside it. Nothing here is claimed from feel.
"""
import argparse
import json
import os
import pathlib
import statistics
import subprocess  # nosec B404 - one fixed argv, no shell
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent

# Budgets in milliseconds of *this server's own work*, which is not the same as
# the round trip a client measures. Measured 30 Aug 2026 on this machine: GET
# /health, which runs no dashboard code at all, has a p95 of 20.0 ms over
# loopback against a floor of 1.4 ms - so about 15 ms of every sample is
# Windows loopback and urllib, and a 20 ms budget graded against the raw round
# trip is unreachable by any code at all.
#
# So /health is measured in the same run, with the same client, and its p95 is
# subtracted as the floor. The budgets below stay absolute; what changed is that
# the harness now grades the quantity they were written about. FLOOR_CEILING
# closes the obvious hole in that - if the whole process were slow, /health
# would be slow too and every excess would shrink, so the floor is itself a
# budget and the run fails when it blows out.
FLOOR_CEILING = 60
# The control route: no dashboard code runs behind it.
FLOOR_PATH = "/health"
BUDGETS = {
    "GET /": 500, "GET /attack": 500, "GET /mutate": 500,
    "GET /trace": 500, "GET /rules": 500, "GET /evidence": 500,
    "GET /app.css": 20, "GET /app.js": 20,
    "GET /api/session": 20, "GET /api/feed": 20, "GET /api/catalogue": 20,
    "GET /api/rules": 20, "GET /api/evidence": 20, "GET /api/trace": 20,
    "GET /api/mutations": 20,
    "POST /api/shop": 100, "POST /api/attack": 100,
    "POST /api/twin": 100, "POST /api/tamper": 100,
    # score_with compiles a mutated policy and re-scores 150 cases. Measured at
    # 0.46 s on this machine 30 Aug 2026; the budget is the point at which it
    # would have to be precomputed and served from memory instead.
    "POST /api/mutate": 2000,
}

# A deployed free instance is a small shared container across the public
# internet, so a budget written for a local process would fail every time and
# stop meaning anything. The shape being checked is the same.
REMOTE_FLOOR_MS = 400

PROBES = [
    ("GET", "/api/session", None), ("GET", "/api/catalogue", None),
    ("GET", "/api/feed", None), ("GET", "/api/rules", None),
    ("GET", "/api/evidence", None), ("GET", "/api/trace", None),
    ("GET", "/api/mutations", None), ("GET", "/app.css", None), ("GET", "/app.js", None),
    ("POST", "/api/shop", {}), ("POST", "/api/attack", {"amount": 50000}),
    ("POST", "/api/twin", {"text": "ignore previous rules", "amount": 150000}),
    ("POST", "/api/tamper", {}), ("POST", "/api/mutate", {"index": 3}),
]
PAGES = ["/", "/attack", "/mutate", "/trace", "/rules", "/evidence"]
REPEATS = 12


def hit(base: str, method: str, path: str, body, cookie: str | None) -> tuple[float, int, str]:
    url = base + path
    # urlopen would follow file:/ or a custom scheme just as happily, and --url
    # comes off the command line.
    if not url.startswith(("http://", "https://")):
        raise SystemExit(f"--url must be http or https, got {url!r}")
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if cookie:
        request.add_header("Cookie", cookie)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=120) as r:   # nosec B310 - scheme checked
            r.read()
            status, set_cookie = r.status, r.headers.get("Set-Cookie", "")
    except urllib.error.HTTPError as e:
        e.read()
        status, set_cookie = e.code, ""
    return (time.perf_counter() - started) * 1000, status, set_cookie


def measure_floor(base: str) -> float:
    """What a round trip costs when the server does nothing.

    /health is the control: same process, same client, same loopback, and it
    runs none of the dashboard's code, so whatever it costs is the harness
    measuring itself.
    """
    samples = []
    hit(base, "GET", FLOOR_PATH, None, None)
    for _ in range(REPEATS * 2):
        ms, _, _ = hit(base, "GET", FLOOR_PATH, None, None)
        samples.append(ms)
    return statistics.quantiles(samples, n=20)[-1]


def measure_endpoints(base: str) -> list[tuple[str, float, int, bool]]:
    """p95 over REPEATS, after one warm-up that is thrown away.

    The cookie from the first response is carried through the rest, because a
    visitor who is handed a new block on every request is not the thing being
    measured.
    """
    _, _, set_cookie = hit(base, "GET", "/api/session", None, None)
    cookie = set_cookie.split(";")[0] if set_cookie else None
    rows = []
    for method, path, body in PROBES:
        hit(base, method, path, body, cookie)          # warm the lru_caches
        samples, status = [], 0
        for _ in range(REPEATS):
            ms, status, _ = hit(base, method, path, body, cookie)
            samples.append(ms)
        p95 = statistics.quantiles(samples, n=20)[-1]
        rows.append((f"{method} {path}", p95, status, status < 400))
    return rows


def measure_pages(base: str) -> list[tuple[str, float, int, bool]]:
    """Time to interactive, in a real browser.

    The pages render their shell before any fetch resolves, so this measures the
    moment a judge can read and click - not the moment every panel has data.
    """
    from playwright.sync_api import sync_playwright

    rows = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1920, "height": 1080})
        for path in PAGES:
            page.goto(base + path, wait_until="load")          # warm
            started = time.perf_counter()
            response = page.goto(base + path, wait_until="domcontentloaded")
            page.wait_for_selector("nav a", timeout=30000)
            ms = (time.perf_counter() - started) * 1000
            status = response.status if response else 0
            rows.append((f"GET {path}", ms, status, status < 400))
        browser.close()
    return rows


LOCAL_BASE = "http://127.0.0.1:8123"


def start_local_server():
    env = {**os.environ, "RESERVE_GATE_TOKEN": "perf-check-agent-token",
           "RESERVE_GATE_ADMIN_TOKEN": "perf-check-admin-token",
           "RESERVE_GATE_DB": str(ROOT / "perf_check.db"),
           "RESERVE_GATE_AUDIT": str(ROOT / "perf_check.jsonl")}
    return subprocess.Popen(                        # nosec B603 - fixed argv, no shell
        [sys.executable, "-m", "src.server", "--http", "--port", "8123"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def wait_for(base: str, seconds: int = 60) -> bool:
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            hit(base, "GET", FLOOR_PATH, None, None)
            return True
        except OSError:
            time.sleep(0.3)
    return False


def verdict(ms: float, budget: float, status: int, ok_status: bool) -> tuple[bool, str]:
    """The word printed and the pass/fail come out of one expression, so the
    table and the exit code cannot disagree."""
    if not ok_status:
        return False, "HTTP " + str(status)
    if ms > budget:
        return False, "OVER"
    return True, "ok"


def report(rows, remote_floor: int, floor: float = 0.0) -> list[str]:
    width = max(len(r[0]) for r in rows)
    print(f"\n{'path':<{width}}  {'own ms':>8}  {'budget':>7}  status  verdict")
    failures = []
    for label, raw, status, ok_status in rows:
        # A page row is what a browser experiences end to end, so the round-trip
        # floor is part of what it measures and is not taken off.
        page = label.split(" ", 1)[1] in PAGES
        ms = raw if page else max(0.0, raw - floor)
        budget = max(BUDGETS.get(label, 500), remote_floor)
        ok, word = verdict(ms, budget, status, ok_status)
        if not ok:
            failures.append(label)
        print(f"{label:<{width}}  {ms:>8.1f}  {budget:>7}  {status:>6}  {word}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(prog="python harness/perf_check.py",
                                     description=__doc__)
    parser.add_argument("--url", help="measure a deployed instance instead of a local one")
    args = parser.parse_args()

    base = args.url.rstrip("/") if args.url else LOCAL_BASE
    server = None if args.url else start_local_server()
    try:
        if not wait_for(base):
            print(f"the server never answered {FLOOR_PATH}")
            return 1
        floor = measure_floor(base)
        rows = measure_endpoints(base) + measure_pages(base)
    finally:
        if server:
            server.terminate()
            server.wait(timeout=10)

    failures = report(rows, REMOTE_FLOOR_MS if args.url else 0, floor)
    print(f"\nround-trip floor, measured as {FLOOR_PATH} p95 in this same run:"
          f" {floor:.1f} ms (ceiling {FLOOR_CEILING}). Every API row above has it"
          " subtracted, so that column is this server's own work. The six page rows"
          " keep it, because a browser pays it too.")
    if floor > FLOOR_CEILING:
        failures.append(FLOOR_PATH + " floor")
        print("THE FLOOR ITSELF IS OVER ITS CEILING - the numbers above understate"
              " everything and this run proves nothing.")
    if args.url:
        print(f"measured against {base}, with a {REMOTE_FLOOR_MS} ms floor on every budget"
              " because a free instance is a shared container across the internet.")
    print(f"{len(rows) - len(failures)} of {len(rows)} within budget")
    if failures:
        print("OVER BUDGET: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
