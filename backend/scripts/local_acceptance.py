"""Rerun this repository's approved local MySQL acceptance environment.

Use backend/.venv/bin/python -B backend/scripts/local_acceptance.py COMMAND.
This helper never downloads, initializes, deletes files, or drops databases.
Credentials stay in .local-runtime/mysql-local.json and child environments.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
import uuid


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
RUNTIME = ROOT / ".local-runtime"
ARTIFACTS = ROOT / "artifacts"
DATA = RUNTIME / "mysql-data"
MYSQL_HOME = RUNTIME / "mysql-8.0.46-macos15-arm64"
MYSQL_VERSION = "8.0.46"
SCHEMA_NAME = re.compile(r"ntou_acceptance_[a-z0-9_]{1,48}\Z")


@dataclass(frozen=True)
class Configuration:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    socket_path: Path

    @classmethod
    def load(cls):
        data = json.loads((RUNTIME / "mysql-local.json").read_text())
        if data.get("host") not in {"127.0.0.1", "localhost"} or data.get("port") != 3307:
            raise RuntimeError("This helper only supports the approved localhost:3307 runtime.")
        if not isinstance(data.get("user"), str) or not data["user"]:
            raise RuntimeError("The local runtime config must contain a user.")
        if not isinstance(data.get("password"), str):
            raise RuntimeError("The local runtime config must contain a password string.")
        socket_path = Path(data["socket"])
        if not socket_path.is_absolute() or socket_path.name != "mysql.sock":
            raise RuntimeError("The local runtime socket must be an absolute mysql.sock path.")
        return cls(data["host"], data["port"], data["user"], data["password"], socket_path)

    def redact(self, text):
        return text.replace(self.password, "<redacted>") if self.password else text

    def environment(self, database=None):
        env = os.environ.copy()
        env.update({
            "DB_HOST": self.host, "DB_PORT": str(self.port), "DB_USER": self.user,
            "DB_PASSWORD": self.password, "NTOU_ACCEPTANCE_ALLOW_CREATE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        # A previous demo DB must never be reused by test/seed.
        env.pop("DB_NAME", None)
        if database is not None:
            if not SCHEMA_NAME.fullmatch(database):
                raise RuntimeError("Refusing a database outside the ntou_acceptance_ namespace.")
            env["DB_NAME"] = database
        return env

    def connect(self):
        import pymysql

        return pymysql.connect(
            host=self.host, port=self.port, user=self.user, password=self.password,
            charset="utf8mb4", connect_timeout=3, read_timeout=5, write_timeout=5,
        )


def port_is_open(config):
    try:
        with socket.create_connection((config.host, config.port), timeout=1):
            return True
    except ConnectionRefusedError:
        return False


def verify_server(config):
    """Identify the dedicated server before allowing schema work or SHUTDOWN."""
    connection = config.connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION(), @@port, @@datadir, @@socket, @@bind_address")
            version, port, datadir, unix_socket, bind_address = cursor.fetchone()
        if version != MYSQL_VERSION or port != 3307:
            raise RuntimeError("Refusing a different MySQL version or port.")
        if Path(datadir).resolve() != DATA.resolve():
            raise RuntimeError("Refusing a MySQL server outside this repository's dedicated datadir.")
        if Path(unix_socket).resolve() != config.socket_path.resolve():
            raise RuntimeError("The server socket does not match the approved local config.")
        if bind_address != "127.0.0.1":
            raise RuntimeError("The dedicated server must bind only to 127.0.0.1.")
        return {
            "running": True, "host": config.host, "port": port,
            "mysql_version": version, "datadir": str(DATA), "socket": str(config.socket_path),
        }
    finally:
        connection.close()


def require_server(config):
    if not port_is_open(config):
        raise RuntimeError("Local MySQL is stopped; run mysql-start first.")
    return verify_server(config)


def start_mysql(config):
    if port_is_open(config):
        return {"action": "already_running", **verify_server(config)}
    executable = MYSQL_HOME / "bin" / "mysqld"
    if not executable.is_file() or not (DATA / "auto.cnf").is_file() or not (DATA / "mysql").is_dir():
        raise RuntimeError("The approved binary/datadir is missing; this helper does not install or initialize it.")
    config.socket_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    command = [
        str(executable), "--no-defaults", f"--basedir={MYSQL_HOME}", f"--datadir={DATA}",
        "--bind-address=127.0.0.1", "--port=3307", "--mysqlx=0",
        f"--socket={config.socket_path}", f"--pid-file={RUNTIME / 'mysql.pid'}",
        f"--log-error={RUNTIME / 'mysql.log'}",
    ]
    with (RUNTIME / "mysql-stdout.log").open("a") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(30):
        if process.poll() is not None:
            raise RuntimeError(f"MySQL exited during startup; inspect {RUNTIME / 'mysql.log'}.")
        if port_is_open(config):
            return {"action": "started", **verify_server(config)}
        time.sleep(0.5)
    raise RuntimeError(f"MySQL has not become ready; inspect {RUNTIME / 'mysql.log'}.")


def stop_mysql(config):
    if not port_is_open(config):
        return {"action": "already_stopped", "running": False, "host": config.host, "port": config.port}
    verified = verify_server(config)
    connection = config.connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHUTDOWN")
    finally:
        connection.close()
    for _ in range(30):
        if not port_is_open(config):
            return {**verified, "action": "stopped", "running": False}
        time.sleep(0.5)
    raise RuntimeError("Shutdown was requested, but port 3307 is still open; no process was killed.")


def write_json(path, value, *, exclusive=False):
    with path.open("x" if exclusive else "w") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def run_job(config, action):
    require_server(config)
    script = (BACKEND / "tests" / "test_order_acceptance_mysql.py" if action == "test"
              else BACKEND / "scripts" / "seed_acceptance_demo.py")
    result = subprocess.run(
        [sys.executable, "-B", str(script)], cwd=ROOT, env=config.environment(),
        capture_output=True, text=True, check=False,
    )
    ARTIFACTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "-" + uuid.uuid4().hex[:6]
    prefix = "acceptance-test" if action == "test" else "freshseed"
    artifact = ARTIFACTS / f"{prefix}-{stamp}.json"
    stdout = config.redact(result.stdout)
    stderr = config.redact(result.stderr)
    stderr_path = ARTIFACTS / f"{prefix}-{stamp}.stderr.log"
    if stderr:
        with stderr_path.open("x") as handle:
            handle.write(stderr)
    try:
        payload = json.loads(stdout)
    except json.JSONDecodeError:
        failed = ARTIFACTS / f"{prefix}-{stamp}.stdout.log"
        with failed.open("x") as handle:
            handle.write(stdout)
        raise RuntimeError(f"The {action} command did not return JSON. See {failed} and {stderr_path}.")
    write_json(artifact, payload, exclusive=True)
    success = result.returncode == 0 and (action != "test" or payload.get("successful") is True)
    if success and action == "seed":
        validate_demo(config, payload)
        write_json(ARTIFACTS / "freshseed.json", payload)
        write_json(RUNTIME / "demo.json", payload)
    print(json.dumps({
        "command": action, "successful": success, "artifact": str(artifact),
        "stderr": str(stderr_path) if stderr else None,
        "database": payload.get("metadata", {}).get("database"),
        **({"tests_run": payload.get("tests_run")} if action == "test" else {
            "demo_courier_ids": payload.get("demo_courier_ids"),
            "frontend_environment": payload.get("frontend_environment"),
        }),
    }, ensure_ascii=False, indent=2))
    return 0 if success else 1


def validate_demo(config, demo):
    metadata = demo.get("metadata", {})
    database = metadata.get("database", "")
    if not isinstance(database, str) or not SCHEMA_NAME.fullmatch(database):
        raise RuntimeError("Demo metadata must identify an ntou_acceptance_ schema.")
    if metadata.get("host") not in {"127.0.0.1", "localhost"} or metadata.get("port") != config.port:
        raise RuntimeError("Demo metadata must target the approved local server.")
    if metadata.get("synthetic_data_only") is not True:
        raise RuntimeError("Demo metadata must mark its data as synthetic.")
    return database


def serve(config):
    require_server(config)
    demo = json.loads((RUNTIME / "demo.json").read_text())
    database = validate_demo(config, demo)
    connection = config.connect()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME=%s", (database,))
            if cursor.fetchone() is None:
                raise RuntimeError("The recorded demo schema no longer exists; run seed first.")
    finally:
        connection.close()
    os.environ.update(config.environment(database))
    sys.path.insert(0, str(BACKEND))
    spec = importlib.util.spec_from_file_location("_ntou_local_demo_server", BACKEND / "server.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot import the backend server.")
    server = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = server
    spec.loader.exec_module(server)
    print(f"Serving synthetic demo schema {database} at http://127.0.0.1:5000", flush=True)
    server.app.run(host="127.0.0.1", port=5000, debug=False, use_reloader=False)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "mysql-start", "mysql-stop", "test", "seed", "serve"))
    args = parser.parse_args()
    config = None
    try:
        config = Configuration.load()
        if args.command in {"test", "seed"}:
            return run_job(config, args.command)
        if args.command == "serve":
            return serve(config)
        if args.command == "mysql-start":
            result = start_mysql(config)
        elif args.command == "mysql-stop":
            result = stop_mysql(config)
        else:
            result = (verify_server(config) if port_is_open(config) else {
                "running": False, "host": config.host, "port": config.port,
            })
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        message = config.redact(str(exc)) if config else "Cannot read a valid local runtime configuration."
        print(f"{type(exc).__name__}: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
