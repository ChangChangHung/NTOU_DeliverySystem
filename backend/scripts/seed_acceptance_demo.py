"""Create a NEW localhost-only database containing synthetic courier-demo data.

Requires explicit DB_USER, DB_PASSWORD and NTOU_ACCEPTANCE_ALLOW_CREATE=1.
DB_HOST defaults to 127.0.0.1 and DB_PORT to 3307. An omitted DB_NAME generates
a unique ntou_acceptance_demo_* name. Existing databases are never reused.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.mysql_support import create_fixture, create_local_application  # noqa: E402


def main() -> int:
    server, _settings, metadata = create_local_application("demo")
    cases = [
        ("ready_multi_meal", True, ("PENDING", "PENDING")),
        ("ready_single_meal", True, ("PENDING",)),
        ("hidden_missing_delivery", False, ("PENDING", "PENDING")),
        ("hidden_already_accepted", True, ("PROCESSING", "PROCESSING")),
        ("hidden_cancelled", True, ("CANCELLED", "CANCELLED")),
        ("hidden_completed", True, ("COMPLETED", "COMPLETED")),
    ]
    fixtures = []
    for label, has_delivery, states in cases:
        fixture = create_fixture(server, delivery=has_delivery, states=states)
        if label == "hidden_already_accepted":
            with server.app.app_context():
                delivery = server.db.session.get(server.Delivery, (
                    fixture["buyer_id"], datetime.strptime(fixture["timestamp"], "%Y-%m-%d %H:%M:%S"),
                ))
                delivery.deliverer_id = fixture["deliverer_ids"][1]
                server.db.session.commit()
        fixtures.append({"scenario": label, **fixture})
    courier_ids = fixtures[0]["deliverer_ids"]
    with server.app.app_context():
        server.db.session.remove()
        server.db.engine.dispose()
    print(json.dumps({
        "metadata": metadata,
        "synthetic_fixture_description": "All users, meals, addresses and orders are synthetic local demo data.",
        "authentication_note": "The demo uses explicit user IDs; this does not implement or verify login/authorization.",
        "demo_courier_ids": courier_ids,
        "frontend_environment": {"VITE_DELIVERER_ID": str(courier_ids[0])},
        "expected_initial_visible_orders": 2,
        "fixtures": fixtures,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
