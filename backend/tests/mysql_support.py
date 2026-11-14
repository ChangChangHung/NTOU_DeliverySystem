"""Create a fresh, local-only MySQL database for acceptance tests and demos.

No DROP, TRUNCATE or fixture deletion is performed. Each run retains its database
for evidence inspection. The application is imported only after all DB settings
have been validated and explicitly set, so its load_dotenv() cannot replace them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import hashlib
from importlib.metadata import version as installed_version
import importlib.util
import os
from pathlib import Path
import platform
import re
import secrets
import subprocess
import sys
import uuid


BACKEND_ROOT = Path(__file__).resolve().parents[1]
DATABASE_NAME = re.compile(r"ntou_acceptance_[a-z0-9_]{1,48}\Z")


@dataclass(frozen=True)
class LocalSettings:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
    database: str

    @classmethod
    def from_environment(cls, purpose: str) -> "LocalSettings":
        if os.environ.get("NTOU_ACCEPTANCE_ALLOW_CREATE") != "1":
            raise RuntimeError(
                "Set NTOU_ACCEPTANCE_ALLOW_CREATE=1 to create a NEW local test schema."
            )
        host = os.environ.get("DB_HOST", "127.0.0.1")
        if host not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("Acceptance scripts only allow DB_HOST=127.0.0.1 or localhost.")
        try:
            port = int(os.environ.get("DB_PORT", "3307"))
        except ValueError as exc:
            raise RuntimeError("DB_PORT must be an integer.") from exc
        if not 1 <= port <= 65535:
            raise RuntimeError("DB_PORT is out of range.")
        user = os.environ.get("DB_USER")
        password = os.environ.get("DB_PASSWORD")
        if not user or password is None:
            raise RuntimeError("Explicit DB_USER and DB_PASSWORD are required (not printed).")
        name = os.environ.get("DB_NAME") or (
            f"ntou_acceptance_{purpose}_{datetime.now():%Y%m%d}_{uuid.uuid4().hex[:10]}"
        )
        if not DATABASE_NAME.fullmatch(name):
            raise RuntimeError("DB_NAME must be a short ntou_acceptance_ name using a-z, 0-9 and _.")
        return cls(host, port, user, password, name)

    def connect(self, *, use_database: bool = True):
        import pymysql

        options = {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "charset": "utf8mb4",
            "connect_timeout": 5,
            "read_timeout": 15,
            "write_timeout": 15,
            "autocommit": False,
        }
        if use_database:
            options["database"] = self.database
        return pymysql.connect(**options)

    def apply_to_application(self) -> None:
        os.environ.update({
            "DB_HOST": self.host,
            "DB_PORT": str(self.port),
            "DB_USER": self.user,
            "DB_PASSWORD": self.password,
            "DB_NAME": self.database,
        })


def create_local_application(purpose: str):
    """Return (server module, safe settings, MySQL metadata) for a new schema."""
    settings = LocalSettings.from_environment(purpose)
    connection = settings.connect(use_database=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION(), @@version_comment, @@default_storage_engine")
            version, comment, default_engine = cursor.fetchone()
            if not str(version).startswith("8.") or "mariadb" in str(version).lower():
                raise RuntimeError("These acceptance tests require actual MySQL 8.x, not SQLite/MariaDB.")
            if str(default_engine).lower() != "innodb":
                raise RuntimeError("MySQL default_storage_engine must be InnoDB.")
            cursor.execute(
                "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME=%s",
                (settings.database,),
            )
            if cursor.fetchone() is not None:
                raise RuntimeError(
                    f"Refusing existing database {settings.database}; choose a NEW DB_NAME or unset it."
                )
            # Name is strictly validated above; intentionally no IF NOT EXISTS.
            cursor.execute(f"CREATE DATABASE `{settings.database}` CHARACTER SET utf8mb4")
    finally:
        connection.close()

    settings.apply_to_application()
    sys.path.insert(0, str(BACKEND_ROOT))
    module_name = "_ntou_acceptance_server"
    if module_name in sys.modules:
        raise RuntimeError("Run each acceptance/demo setup in a fresh Python process.")
    spec = importlib.util.spec_from_file_location(module_name, BACKEND_ROOT / "server.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot load the local backend/server.py.")
    server = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = server
    spec.loader.exec_module(server)
    server.app.config["TESTING"] = True

    from sqlalchemy import event, text

    with server.app.app_context():
        @event.listens_for(server.db.engine, "connect")
        def set_test_lock_timeout(dbapi_connection, _connection_record):
            with dbapi_connection.cursor() as cursor:
                cursor.execute("SET SESSION innodb_lock_wait_timeout=5")

        server.db.create_all()
        engines = server.db.session.execute(text(
            "SELECT TABLE_NAME, ENGINE FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA=:schema"
        ), {"schema": settings.database}).all()
        if not engines or any(engine.lower() != "innodb" for _, engine in engines):
            raise RuntimeError("Every application table must use InnoDB for row-lock acceptance tests.")
        server.db.session.remove()

    metadata = {
        "database": settings.database,
        "host": settings.host,
        "port": settings.port,
        "mysql_version": version,
        "mysql_distribution": comment,
        "storage_engine": default_engine,
        "tables": [name for name, _ in engines],
        "synthetic_data_only": True,
        "database_retained_for_inspection": True,
        "python_version": platform.python_version(),
        "package_versions": {name: installed_version(name) for name in (
            "Flask", "Flask-SQLAlchemy", "SQLAlchemy", "python-dotenv", "PyMySQL",
        )},
        "source_sha256": {
            path: hashlib.sha256((BACKEND_ROOT / path).read_bytes()).hexdigest()
            for path in ("server.py", "utils.py", "tests/mysql_support.py")
        },
    }
    git_result = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=BACKEND_ROOT,
        capture_output=True, text=True, check=False,
    )
    metadata["git_head"] = git_result.stdout.strip() if git_result.returncode == 0 else None
    metadata["source_note"] = "Source hashes identify tested working-tree files; git_head alone does not include uncommitted changes."
    return server, settings, metadata


def create_fixture(server, *, delivery: bool = True, states: tuple[str, ...] = ("PENDING", "PENDING")):
    """Insert isolated synthetic rows; return plain IDs with no passwords."""
    suffix = uuid.uuid4().hex[:8]
    timestamp = datetime(2030, 1, 1) + timedelta(seconds=secrets.randbelow(20_000_000))
    with server.app.app_context():
        users = [
            server.Users(user_name=f"t{suffix}buyer", password=secrets.token_urlsafe(24),
                         user_state=server.UserStatus.NORMAL),
            server.Users(user_name=f"t{suffix}courierA", password=secrets.token_urlsafe(24),
                         user_state=server.UserStatus.DELIVERER),
            server.Users(user_name=f"t{suffix}courierB", password=secrets.token_urlsafe(24),
                         user_state=server.UserStatus.DELIVERER),
        ]
        restaurant = server.Restaurants(
            restaurant_name=f"測試餐廳 {suffix}", restaurant_address="合成測試地址，不是真實商家"
        )
        server.db.session.add_all([*users, restaurant])
        server.db.session.flush()
        meals = [server.Meals(
            meal_name=f"合成餐點 {index + 1}", restaurant_id=restaurant.restaurant_id,
            price=80 + index * 20,
        ) for index in range(len(states))]
        server.db.session.add_all(meals)
        server.db.session.flush()
        rows = [server.Orders(
            buyer_id=users[0].user_id, meal_id=meal.meal_id,
            quantity=index + 1, timestamp=timestamp, note="合成驗收資料",
            order_state=getattr(server.OrderState, state),
        ) for index, (meal, state) in enumerate(zip(meals, states))]
        server.db.session.add_all(rows)
        if delivery:
            server.db.session.add(server.Delivery(
                buyer_id=users[0].user_id, timestamp=timestamp, deliverer_id=None,
                pickup_address="海大測試交付點（合成資料）",
            ))
        server.db.session.commit()
        fixture = {
            "buyer_id": users[0].user_id,
            "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "deliverer_ids": [users[1].user_id, users[2].user_id],
            "meal_ids": [meal.meal_id for meal in meals],
            "order_ids": [row.order_id for row in rows],
        }
        server.db.session.remove()
        return fixture


def database_snapshot(settings: LocalSettings, fixture: dict) -> dict:
    """Read committed state through a fresh independent MySQL connection."""
    connection = settings.connect()
    try:
        with connection.cursor() as cursor:
            identity = (fixture["buyer_id"], fixture["timestamp"])
            cursor.execute(
                "SELECT order_id, order_state FROM orders WHERE buyer_id=%s AND timestamp=%s "
                "ORDER BY order_id", identity,
            )
            rows = [{"order_id": row_id, "state": state} for row_id, state in cursor.fetchall()]
            cursor.execute(
                "SELECT deliverer_id FROM delivery WHERE buyer_id=%s AND timestamp=%s", identity,
            )
            delivery = cursor.fetchone()
        return {
            "orders": rows,
            "delivery_exists": delivery is not None,
            "deliverer_id": None if delivery is None else delivery[0],
        }
    finally:
        connection.close()
