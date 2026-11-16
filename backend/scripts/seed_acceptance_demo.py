"""Create a NEW localhost-only database containing synthetic courier-demo data.

Requires explicit DB_USER, DB_PASSWORD and NTOU_ACCEPTANCE_ALLOW_CREATE=1.
DB_HOST defaults to 127.0.0.1 and DB_PORT to 3307. An omitted DB_NAME generates
a unique ntou_acceptance_demo_* name. Existing databases are never reused.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import secrets
import sys
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.mysql_support import create_local_application, database_snapshot  # noqa: E402


def create_demo_order(server, settings, *, delivery, states):
    """Seed a catalog, then let the real order API assign the order timestamp."""
    suffix = uuid.uuid4().hex[:8]
    with server.app.app_context():
        users = [server.Users(
            user_name=f"d{suffix}{role}", password=secrets.token_urlsafe(24), user_state=status,
        ) for role, status in (
            ("buyer", server.UserStatus.NORMAL),
            ("courierA", server.UserStatus.DELIVERER),
            ("courierB", server.UserStatus.DELIVERER),
        )]
        restaurant = server.Restaurants(
            restaurant_name=f"測試餐廳 {suffix}", restaurant_address="海大測試取餐點",
        )
        server.db.session.add_all([*users, restaurant])
        server.db.session.flush()
        meals = [server.Meals(
            meal_name=f"測試餐點 {index + 1}", restaurant_id=restaurant.restaurant_id,
            price=80 + index * 20,
        ) for index in range(len(states))]
        server.db.session.add_all(meals)
        server.db.session.flush()
        fixture = {"buyer_id": users[0].user_id,
                   "deliverer_ids": [users[1].user_id, users[2].user_id],
                   "meal_ids": [meal.meal_id for meal in meals]}
        server.db.session.commit()
        server.db.session.remove()

    started = datetime.now().replace(microsecond=0)
    with server.app.test_client() as client:
        created = client.post("/api/add_new_order", json={
            "buyer_id": fixture["buyer_id"],
            "meals": [{"meal_id": meal_id, "quantity": index + 1, "note": "接單展示用測試餐點"}
                      for index, meal_id in enumerate(fixture["meal_ids"])],
        })
        if created.status_code != 201:
            raise RuntimeError(f"Demo order creation failed: HTTP {created.status_code}")
        body = created.get_json()
        fixture.update(timestamp=body["timestamp"], order_ids=[row["order_id"] for row in body["orders"]])
        finished = datetime.now().replace(microsecond=0)
        order_time = datetime.strptime(fixture["timestamp"], "%Y-%m-%d %H:%M:%S")
        if not started <= order_time <= finished:
            raise RuntimeError("Demo order timestamp is outside the actual API request time.")
        responses = [{"path": "/api/add_new_order", "status": created.status_code, "body": body}]
        if delivery:
            response = client.post("/api/add_new_delivery", json={
                "buyer_id": fixture["buyer_id"], "timestamp": fixture["timestamp"],
                "pickup_address": "海大測試交付點",
            })
            if response.status_code != 201:
                raise RuntimeError(f"Demo Delivery creation failed: HTTP {response.status_code}")
            responses.append({"path": "/api/add_new_delivery", "status": response.status_code,
                              "body": response.get_json()})

    # Hidden-state fixtures test filtering; their states are deliberate setup.
    # Ready orders keep the API-created PENDING state and timestamp unchanged.
    if any(state != "PENDING" for state in states):
        with server.app.app_context():
            for order_id, state in zip(fixture["order_ids"], states):
                server.db.session.get(server.Orders, order_id).order_state = getattr(server.OrderState, state)
            server.db.session.commit()
            server.db.session.remove()
    snapshot = database_snapshot(settings, fixture)
    if ([row["state"] for row in snapshot["orders"]] != list(states)
            or {row["order_id"] for row in snapshot["orders"]} != set(fixture["order_ids"])
            or snapshot["delivery_exists"] != delivery):
        raise RuntimeError("Demo API identity does not match the committed database rows.")
    fixture["creation_evidence"] = {
        "timestamp_source": "add_new_order API response; server datetime.now()",
        "request_started": started.strftime("%Y-%m-%d %H:%M:%S"),
        "request_finished": finished.strftime("%Y-%m-%d %H:%M:%S"),
        "responses": responses,
        "fixture_states": list(states),
        "database_identity_verified": True,
    }
    return fixture


def main() -> int:
    server, settings, metadata = create_local_application("demo")
    cases = [
        ("ready_multi_meal", True, ("PENDING", "PENDING")),
        ("ready_single_meal", True, ("PENDING",)),
        ("hidden_missing_delivery", False, ("PENDING", "PENDING")),
        ("hidden_already_accepted", True, ("PROCESSING", "PROCESSING")),
        ("hidden_cancelled", True, ("CANCELLED", "CANCELLED")),
        ("hidden_completed", True, ("COMPLETED", "COMPLETED")),
        ("hidden_in_cart", True, ("IN_CART", "IN_CART")),
        ("hidden_mixed_cart", True, ("PENDING", "IN_CART")),
    ]
    fixtures = []
    for label, has_delivery, states in cases:
        fixture = create_demo_order(server, settings, delivery=has_delivery, states=states)
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
        "order_timestamp_source": "Actual add_new_order API responses; no fixed or randomized demo dates.",
        "fixtures": fixtures,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
