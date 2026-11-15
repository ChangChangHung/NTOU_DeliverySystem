"""Run the acceptance UI against a fresh local MySQL schema and real API.

Uses the already configured local_acceptance runtime. Does not install packages,
overwrite the Demo config, delete data, or contact the shared team database.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

from local_acceptance import Configuration, ROOT, BACKEND, require_server, validate_demo


API_PORT = 5100
UI_PORTS = (5180, 5181, 5182)


def save_json(path, value):
    with path.open("x") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def wait_http(url, process):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Service exited before ready: {url}")
        try:
            with opener.open(url, timeout=1) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    raise RuntimeError(f"Service did not become ready: {url}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--playwright-module", required=True, type=Path,
                        help="Path to an already installed playwright package directory")
    parser.add_argument("--node", default=shutil.which("node"))
    parser.add_argument("--audit", action="store_true", help="Also run browser response validation and split-read regression cases")
    args = parser.parse_args()
    processes, logs = [], []
    config = None
    output = None
    try:
        config = Configuration.load()
        require_server(config)
        playwright = args.playwright_module.resolve()
        package = json.loads((playwright / "package.json").read_text())
        if package.get("name") != "playwright" or not (playwright / "index.js").is_file():
            raise RuntimeError("--playwright-module must point to an installed playwright package.")
        node = Path(args.node or "")
        if not node.is_file():
            raise RuntimeError("Provide an installed Node executable with --node.")
        for port in (API_PORT, *UI_PORTS):
            with socket.socket() as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                probe.bind(("127.0.0.1", port))
        vite = ROOT / "frontend/node_modules/vite/bin/vite.js"
        if not vite.is_file():
            raise RuntimeError("Frontend dependencies are missing; this runner does not install them.")

        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
        output = ROOT / "artifacts" / f"integration-{run_id}"
        output.mkdir(parents=True, exist_ok=False)
        seeded = subprocess.run(
            [sys.executable, "-B", str(BACKEND / "scripts/seed_acceptance_demo.py")],
            cwd=ROOT, env=config.environment(), capture_output=True, text=True, check=False,
        )
        (output / "seed.stderr.log").write_text(config.redact(seeded.stderr))
        if seeded.returncode:
            raise RuntimeError("Seed failed; see seed.stderr.log.")
        seed = json.loads(seeded.stdout)
        database = validate_demo(config, seed)
        save_json(output / "seed.json", seed)

        def start(command, env, cwd, name):
            log = (output / f"{name}.log").open("x")
            logs.append(log)
            child = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                                     stdout=log, stderr=subprocess.STDOUT)
            processes.append(child)
            return child

        api = f"http://127.0.0.1:{API_PORT}"
        backend = start([
            sys.executable, "-B", "-c",
            "from server import app; app.run(host='127.0.0.1', port=5100, debug=False, use_reloader=False)",
        ], config.environment(database), BACKEND, "backend")
        wait_http(f"{api}/api/view_all_meals", backend)
        urls = []
        for port, courier in zip(UI_PORTS, [*seed["demo_courier_ids"], "invalid"]):
            env = os.environ.copy()
            env.update({"NTOU_API_TARGET": api, "VITE_DELIVERER_ID": str(courier)})
            child = start([str(node), str(vite), "--host", "127.0.0.1", "--port", str(port),
                           "--strictPort"], env, ROOT / "frontend", f"frontend-{port}")
            url = f"http://127.0.0.1:{port}/driver.html"
            wait_http(url, child)
            urls.append(url)
        runner_input = {"apiBase": api, "frontendUrls": urls, "seed": seed,
                        "outputDir": str(output), "playwrightModule": str(playwright), "audit": args.audit}
        save_json(output / "runner-input.json", runner_input)
        browser_process = start([
            str(node), str(ROOT / "frontend/tests/run-acceptance-integration.mjs"),
            str(output / "runner-input.json"),
        ], os.environ.copy(), ROOT, "browser")
        browser_process.wait(timeout=240)
        report_path = output / "browser-results.json"
        if not report_path.is_file():
            raise RuntimeError("Browser runner failed before reporting; see browser.log.")
        report = json.loads(report_path.read_text())

        # Re-read committed data through a separate connection, outside Flask.
        sys.path.insert(0, str(BACKEND))
        from tests.mysql_support import LocalSettings, database_snapshot
        settings = LocalSettings(config.host, config.port, config.user, config.password, database)
        snapshots = []
        for order in report.get("orders", []):
            state = database_snapshot(settings, order)
            ok = (state["delivery_exists"]
                  and [row["state"] for row in state["orders"]] == order["expected_states"]
                  and {row["order_id"] for row in state["orders"]} == set(order["order_ids"])
                  and state["deliverer_id"] == order["expected_deliverer_id"])
            snapshots.append({"order": order, "database": state, "passed": ok})
        source_paths = ["backend/server.py", "backend/utils.py", "frontend/vite.config.ts",
                        "frontend/src/components/DriverLobby.vue", "backend/scripts/seed_acceptance_demo.py",
                        "backend/scripts/local_integration.py", "frontend/tests/acceptance-integration.mjs",
                        "frontend/tests/run-acceptance-integration.mjs"]
        if args.audit:
            source_paths.append("frontend/tests/acceptance-audit.mjs")
        summary = {
            "run_id": run_id, "completed_at": datetime.now(timezone.utc).isoformat(),
            "passed": browser_process.returncode == 0 and report.get("passed") is True
                      and len(snapshots) == 4 and all(item["passed"] for item in snapshots),
            "browser_cases": {"passed": report.get("passed_cases"), "total": report.get("total")},
            "browser_response_audit": report.get("audit"),
            "metadata": seed["metadata"], "playwright_version": package["version"],
            "database_checks": snapshots,
            "source_sha256": {path: hashlib.sha256((ROOT / path).read_bytes()).hexdigest()
                              for path in source_paths},
        }
        save_json(output / "integration-results.json", summary)
        print(json.dumps({"passed": summary["passed"], "browser_cases": summary["browser_cases"],
                          "browser_response_audit": summary["browser_response_audit"],
                          "database_checks": len(snapshots), "output": str(output)}, ensure_ascii=False))
        return 0 if summary["passed"] else 1
    except Exception as exc:
        message = config.redact(str(exc)) if config else "Cannot read the local runtime configuration."
        print(f"{type(exc).__name__}: {message}", file=sys.stderr)
        if output:
            print(f"Run artifacts: {output}", file=sys.stderr)
        return 1
    finally:
        for child in reversed(processes):
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        for log in logs:
            log.close()


if __name__ == "__main__":
    raise SystemExit(main())
