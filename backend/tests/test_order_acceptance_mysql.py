"""Real MySQL 8 acceptance checks for the existing process_order API.

Run with explicit local DB credentials and NTOU_ACCEPTANCE_ALLOW_CREATE=1.
The script creates a fresh ntou_acceptance_* schema and prints JSON evidence.
The response-race case deletes only its newly created synthetic order. No HTTP
service is started or contacted, and the schema is retained after each run.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from datetime import datetime
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.mysql_support import (  # noqa: E402
    create_fixture, create_local_application, database_snapshot,
)


class OrderAcceptanceMySQLTests(unittest.TestCase):
    evidence: list[dict] = []
    metadata: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.server, cls.settings, cls.metadata = create_local_application("test")
        cls.evidence = []

    @classmethod
    def tearDownClass(cls):
        with cls.server.app.app_context():
            cls.server.db.session.remove()
            cls.server.db.engine.dispose()

    def setUp(self):
        self.fixture = create_fixture(self.server)

    def payload(self, fixture=None, deliverer_id=None):
        fixture = fixture or self.fixture
        return {
            "buyer_id": fixture["buyer_id"],
            "timestamp": fixture["timestamp"],
            "deliverer_id": deliverer_id or fixture["deliverer_ids"][0],
        }

    def post(self, payload):
        # A fresh Flask client/app context gives each request its own scoped session.
        with self.server.app.test_client() as client:
            response = client.post("/api/process_order", json=payload)
            return response.status_code, response.get_json()

    def assert_snapshot(self, fixture, states, deliverer_id, delivery_exists=True):
        snapshot = database_snapshot(self.settings, fixture)
        self.assertEqual([row["state"] for row in snapshot["orders"]], list(states))
        self.assertEqual(snapshot["delivery_exists"], delivery_exists)
        self.assertEqual(snapshot["deliverer_id"], deliverer_id)
        return snapshot

    def record(self, name, fixture, responses, snapshot, **extra):
        self.evidence.append({
            "case": name,
            "order_identity": {
                "buyer_id": fixture["buyer_id"], "timestamp": fixture["timestamp"],
            },
            "responses": responses,
            "committed_database_state": snapshot,
            **extra,
        })

    def test_multi_meal_acceptance_is_committed_and_readable_after_engine_disposal(self):
        code, body = self.post(self.payload())
        self.assertEqual(code, 200, body)
        self.assertTrue(body["success"])
        self.assertEqual({row["order_id"] for row in body["processed_orders"]}, set(self.fixture["order_ids"]))
        self.assertEqual([row["order_state"] for row in body["processed_orders"]], ["processing", "processing"])
        # Dispose the application's pool, then read using a separate MySQL client.
        # This demonstrates committed persistence, not a full OS/server restart.
        with self.server.app.app_context():
            self.server.db.session.remove()
            self.server.db.engine.dispose()
        snapshot = self.assert_snapshot(self.fixture, ["PROCESSING", "PROCESSING"], self.fixture["deliverer_ids"][0])
        self.record(self._testMethodName, self.fixture, [{"status": code, "body": body}], snapshot)

    def test_same_courier_cannot_accept_twice(self):
        first = self.post(self.payload())
        second = self.post(self.payload())
        self.assertEqual(first[0], 200, first[1])
        self.assertEqual(second[0], 409, second[1])
        snapshot = self.assert_snapshot(self.fixture, ["PROCESSING", "PROCESSING"], self.fixture["deliverer_ids"][0])
        self.record(self._testMethodName, self.fixture, [
            {"status": code, "body": body} for code, body in [first, second]
        ], snapshot)

    def test_two_couriers_compete_on_mysql_row_locks_ten_rounds(self):
        from sqlalchemy import event

        with self.server.app.app_context():
            engine = self.server.db.engine
        for round_index in range(10):
            with self.subTest(round=round_index + 1):
                fixture = self.fixture if round_index == 0 else create_fixture(self.server)
                condition = threading.Condition()
                connection_ids = {}
                update_threads = set()

                def observe_sql(connection, _cursor, statement, _parameters, _context, _many):
                    sql = " ".join(statement.upper().split())
                    thread_id = threading.get_ident()
                    with condition:
                        if sql.startswith("SELECT") and "FROM ORDERS" in sql:
                            connection_ids[thread_id] = connection.connection.driver_connection.thread_id()
                        elif sql.startswith("UPDATE ORDERS"):
                            update_threads.add(thread_id)
                        condition.notify_all()

                event.listen(engine, "before_cursor_execute", observe_sql)
                blocker = self.settings.connect()
                try:
                    blocker.begin()
                    with blocker.cursor() as cursor:
                        cursor.execute(
                            "SELECT order_id FROM orders WHERE buyer_id=%s AND timestamp=%s FOR UPDATE",
                            (fixture["buyer_id"], fixture["timestamp"]),
                        )
                        self.assertEqual(len(cursor.fetchall()), 2)
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        futures = [pool.submit(self.post, self.payload(fixture, courier_id))
                                   for courier_id in fixture["deliverer_ids"]]
                        try:
                            with condition:
                                both_reached_orders = condition.wait_for(
                                    lambda: len(connection_ids) == 2, timeout=5,
                                )
                                self.assertTrue(both_reached_orders, "Both requests must reach Orders on separate connections.")
                                self.assertEqual(len(set(connection_ids.values())), 2)
                                # A version missing FOR UPDATE can read stale pending rows,
                                # then wait at UPDATE; let both reach that point before release.
                                # With the correct lock neither can get beyond its SELECT yet.
                                condition.wait_for(lambda: len(update_threads) == 2, timeout=0.2)
                            self.assertTrue(all(not future.done() for future in futures))
                        finally:
                            # Always release before executor shutdown so failed assertions
                            # cannot strand its workers behind our verification lock.
                            blocker.commit()
                        responses = [future.result(timeout=10) for future in futures]
                finally:
                    blocker.rollback()
                    blocker.close()
                    event.remove(engine, "before_cursor_execute", observe_sql)

                self.assertEqual(sorted(code for code, _ in responses), [200, 409], responses)
                winner_index = next(i for i, (code, _) in enumerate(responses) if code == 200)
                winner = fixture["deliverer_ids"][winner_index]
                snapshot = self.assert_snapshot(fixture, ["PROCESSING", "PROCESSING"], winner)
                self.record(self._testMethodName, fixture, [
                    {"courier_id": courier, "status": response[0], "body": response[1]}
                    for courier, response in zip(fixture["deliverer_ids"], responses)
                ], snapshot, round=round_index + 1, distinct_mysql_connections=len(set(connection_ids.values())))

    def test_missing_delivery_rejects_without_updating_any_meal(self):
        fixture = create_fixture(self.server, delivery=False)
        code, body = self.post(self.payload(fixture))
        self.assertEqual(code, 404, body)
        snapshot = self.assert_snapshot(fixture, ["PENDING", "PENDING"], None, delivery_exists=False)
        self.record(self._testMethodName, fixture, [{"status": code, "body": body}], snapshot)

    def test_nonexistent_courier_and_order_return_404_without_changes(self):
        payloads = [
            {**self.payload(), "deliverer_id": 2_000_000_000},
            {**self.payload(), "buyer_id": 2_000_000_000},
            {**self.payload(), "timestamp": "2000-01-01 00:00:00"},
        ]
        responses = []
        for payload in payloads:
            with self.subTest(payload=payload):
                code, body = self.post(payload)
                self.assertEqual(code, 404, body)
                responses.append({"status": code, "body": body})
                self.assert_snapshot(self.fixture, ["PENDING", "PENDING"], None)
        self.record(self._testMethodName, self.fixture, responses, database_snapshot(self.settings, self.fixture))

    def test_non_pending_and_mixed_state_orders_reject_as_a_whole(self):
        for states in [
            ("CANCELLED", "CANCELLED"), ("COMPLETED", "COMPLETED"),
            ("PROCESSING", "PROCESSING"), ("PENDING", "PROCESSING"),
            ("IN_CART", "IN_CART"), ("PENDING", "IN_CART"),
        ]:
            with self.subTest(states=states):
                fixture = create_fixture(self.server, states=states)
                code, body = self.post(self.payload(fixture))
                self.assertEqual(code, 409, body)
                snapshot = self.assert_snapshot(fixture, states, None)
                self.record(self._testMethodName, fixture, [{"status": code, "body": body}], snapshot)

    def test_other_courier_assignment_is_not_overwritten(self):
        with self.server.app.app_context():
            delivery = self.server.db.session.get(self.server.Delivery, (
                self.fixture["buyer_id"], datetime.strptime(self.fixture["timestamp"], "%Y-%m-%d %H:%M:%S"),
            ))
            delivery.deliverer_id = self.fixture["deliverer_ids"][1]
            self.server.db.session.commit()
        code, body = self.post(self.payload())
        self.assertEqual(code, 409, body)
        snapshot = self.assert_snapshot(self.fixture, ["PENDING", "PENDING"], self.fixture["deliverer_ids"][1])
        self.record(self._testMethodName, self.fixture, [{"status": code, "body": body}], snapshot)

    def test_invalid_payloads_return_400_without_changes(self):
        payloads = [[], {}, {**self.payload(), "buyer_id": 0},
                    {**self.payload(), "deliverer_id": True},
                    {**self.payload(), "deliverer_id": "not-an-id"},
                    {**self.payload(), "timestamp": "not-a-time"}]
        responses = []
        for payload in payloads:
            with self.subTest(payload=payload):
                code, body = self.post(payload)
                self.assertEqual(code, 400, body)
                responses.append({"status": code, "body": body})
        snapshot = self.assert_snapshot(self.fixture, ["PENDING", "PENDING"], None)
        self.record(self._testMethodName, self.fixture, responses, snapshot)

    def test_sql_failure_rolls_back_updates_in_both_tables(self):
        from sqlalchemy import event

        with self.server.app.app_context():
            engine = self.server.db.engine
        injected = []
        updated_tables = []

        def fail_after_real_update(_connection, _cursor, statement, _parameters, _context, _many):
            sql = " ".join(statement.upper().split())
            for table in ("ORDERS", "DELIVERY"):
                if sql.startswith(f"UPDATE {table}"):
                    updated_tables.append(table)
            if not injected and set(updated_tables) == {"ORDERS", "DELIVERY"}:
                injected.append(True)
                raise RuntimeError("synthetic acceptance failure after both tables were updated")

        event.listen(engine, "after_cursor_execute", fail_after_real_update)
        try:
            code, body = self.post(self.payload())
        finally:
            event.remove(engine, "after_cursor_execute", fail_after_real_update)
        self.assertTrue(injected, "Both real table UPDATEs must execute before the injected failure.")
        self.assertEqual(set(updated_tables), {"ORDERS", "DELIVERY"})
        self.assertEqual(code, 500, body)
        snapshot = self.assert_snapshot(self.fixture, ["PENDING", "PENDING"], None)
        self.record(self._testMethodName, self.fixture, [{"status": code, "body": body}], snapshot,
                    synthetic_failure_injection=True, updated_tables_before_failure=updated_tables)

    def test_acceptance_keeps_other_buyer_and_other_timestamp_orders_unchanged(self):
        other_fixture = create_fixture(self.server)
        fixed_time = datetime(2033, 2, 3, 4, 5, 6)
        identities = []
        responses = []

        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_time

        for fixture in (self.fixture, other_fixture):
            with self.server.app.test_client() as client:
                with patch.object(self.server, "datetime", FixedClock):
                    created = client.post("/api/add_new_order", json={
                        "buyer_id": fixture["buyer_id"],
                        "meals": [{"meal_id": meal, "quantity": 1} for meal in fixture["meal_ids"]],
                    })
                self.assertEqual(created.status_code, 201, created.get_json())
                body = created.get_json()
                identity = {"buyer_id": fixture["buyer_id"], "timestamp": body["timestamp"]}
                identities.append(identity)
                delivery = client.post("/api/add_new_delivery", json={
                    **identity, "pickup_address": "不同買家的接單聯測地址",
                })
                self.assertEqual(delivery.status_code, 201, delivery.get_json())
                responses.extend([
                    {"path": "/api/add_new_order", "status": created.status_code, "body": body},
                    {"path": "/api/add_new_delivery", "status": delivery.status_code,
                     "body": delivery.get_json()},
                ])
        self.assertEqual(identities[0]["timestamp"], identities[1]["timestamp"])
        self.assertNotEqual(identities[0]["buyer_id"], identities[1]["buyer_id"])
        other_before = self.assert_snapshot(identities[1], ["PENDING", "PENDING"], None)
        same_buyer_before = self.assert_snapshot(self.fixture, ["PENDING", "PENDING"], None)
        self.assertNotEqual(self.fixture["timestamp"], identities[0]["timestamp"])
        courier = self.fixture["deliverer_ids"][0]
        code, body = self.post({**identities[0], "deliverer_id": courier})
        self.assertEqual(code, 200, body)
        responses.append({"path": "/api/process_order", "status": code, "body": body})
        accepted = self.assert_snapshot(identities[0], ["PROCESSING", "PROCESSING"], courier)
        other_after = self.assert_snapshot(identities[1], ["PENDING", "PENDING"], None)
        self.assertEqual(other_before, other_after)
        same_buyer_after = self.assert_snapshot(self.fixture, ["PENDING", "PENDING"], None)
        self.assertEqual(same_buyer_before, same_buyer_after)
        self.record(self._testMethodName, identities[0], responses, accepted,
                    unrelated_order={"identity": identities[1], "before": other_before,
                                     "after": other_after},
                    same_buyer_other_order={
                        "identity": {"buyer_id": self.fixture["buyer_id"],
                                     "timestamp": self.fixture["timestamp"]},
                        "before": same_buyer_before, "after": same_buyer_after,
                    },
                    injected_clock=fixed_time.strftime("%Y-%m-%d %H:%M:%S"))

    def test_failed_order_commit_returns_json_and_leaves_no_partial_order(self):
        fixed_time = datetime(2034, 3, 4, 5, 6, 7)
        identity = {"buyer_id": self.fixture["buyer_id"],
                    "timestamp": fixed_time.strftime("%Y-%m-%d %H:%M:%S")}
        flushed = []

        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_time

        def fail_commit_after_flush():
            self.server.db.session.flush()
            flushed.append(True)
            raise RuntimeError("synthetic order commit failure after INSERT")

        before = self.assert_snapshot(identity, [], None, delivery_exists=False)
        with self.server.app.test_client() as client:
            with patch.object(self.server, "datetime", FixedClock), patch.object(
                self.server.db.session, "commit", side_effect=fail_commit_after_flush,
            ):
                response = client.post("/api/add_new_order", json={
                    "buyer_id": self.fixture["buyer_id"],
                    "meals": [{"meal_id": meal, "quantity": 1} for meal in self.fixture["meal_ids"]],
                })
            self.assertEqual(response.status_code, 500)
            self.assertTrue(response.is_json, response.get_data(as_text=True))
            body = response.get_json()
        self.assertEqual(flushed, [True])
        self.assertIs(body["success"], False)
        self.assertIn("message", body)
        after = self.assert_snapshot(identity, [], None, delivery_exists=False)
        self.assertEqual(before, after)
        self.record(self._testMethodName, identity, [{
            "path": "/api/add_new_order", "status": response.status_code, "body": body,
        }], after, synthetic_failure_injection=True, flushed_before_failure=True)

    def test_same_second_checkout_rejects_before_and_after_acceptance(self):
        fixed_time = datetime(2036, 1, 2, 3, 4, 5)

        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_time

        payload = {
            "buyer_id": self.fixture["buyer_id"],
            "meals": [{"meal_id": meal, "quantity": 1} for meal in self.fixture["meal_ids"]],
        }
        responses = []
        with patch.object(self.server, "datetime", FixedClock):
            with self.server.app.test_client() as client:
                created = client.post("/api/add_new_order", json=payload)
                self.assertEqual(created.status_code, 201, created.get_json())
                identity = {"buyer_id": payload["buyer_id"], "timestamp": created.get_json()["timestamp"]}
                delivery = client.post("/api/add_new_delivery", json={
                    **identity, "pickup_address": "同秒重複下單測試地址",
                })
                self.assertEqual(delivery.status_code, 201, delivery.get_json())
                duplicate = client.post("/api/add_new_order", json=payload)
                self.assertEqual(duplicate.status_code, 409, duplicate.get_json())
            before_accept = self.assert_snapshot(identity, ["PENDING", "PENDING"], None)
            courier = self.fixture["deliverer_ids"][0]
            code, body = self.post({**identity, "deliverer_id": courier})
            self.assertEqual(code, 200, body)
            before_retry = self.assert_snapshot(identity, ["PROCESSING", "PROCESSING"], courier)
            with self.server.app.test_client() as client:
                after_accept = client.post("/api/add_new_order", json=payload)
                self.assertEqual(after_accept.status_code, 409, after_accept.get_json())
            final = self.assert_snapshot(identity, ["PROCESSING", "PROCESSING"], courier)
        self.assertEqual(before_retry, final)
        self.assertEqual({row["order_id"] for row in final["orders"]},
                         {row["order_id"] for row in created.get_json()["orders"]})
        for path, response in (("/api/add_new_order", created),
                               ("/api/add_new_delivery", delivery),
                               ("/api/add_new_order", duplicate)):
            responses.append({"path": path, "status": response.status_code, "body": response.get_json()})
        responses.extend([
            {"path": "/api/process_order", "status": code, "body": body},
            {"path": "/api/add_new_order", "status": after_accept.status_code,
             "body": after_accept.get_json()},
        ])
        self.record(self._testMethodName, identity, responses, final,
                    before_accept=before_accept, injected_clock=identity["timestamp"])

    def test_concurrent_same_buyer_checkout_creates_only_one_order_group(self):
        from sqlalchemy import event

        fixed_time = datetime(2037, 1, 2, 3, 4, 5)

        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_time

        identity = {"buyer_id": self.fixture["buyer_id"],
                    "timestamp": fixed_time.strftime("%Y-%m-%d %H:%M:%S")}
        payload = {
            "buyer_id": self.fixture["buyer_id"],
            "meals": [{"meal_id": meal, "quantity": 1} for meal in self.fixture["meal_ids"]],
        }
        condition = threading.Condition()
        lock_connections = set()

        def observe_parent_lock(connection, _cursor, statement, _parameters, _context, _many):
            sql = " ".join(statement.upper().split())
            if sql.startswith("SELECT") and "FROM USERS" in sql and "FOR UPDATE" in sql:
                with condition:
                    lock_connections.add(connection.connection.driver_connection.thread_id())
                    condition.notify_all()

        def checkout():
            with self.server.app.test_client() as client:
                response = client.post("/api/add_new_order", json=payload)
                return response.status_code, response.get_json()

        with self.server.app.app_context():
            engine = self.server.db.engine
        event.listen(engine, "before_cursor_execute", observe_parent_lock)
        blocker = self.settings.connect()
        try:
            blocker.begin()
            with blocker.cursor() as cursor:
                cursor.execute("SELECT user_id FROM users WHERE user_id=%s FOR UPDATE",
                               (self.fixture["buyer_id"],))
                self.assertIsNotNone(cursor.fetchone())
            with patch.object(self.server, "datetime", FixedClock), ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(checkout) for _ in range(2)]
                try:
                    with condition:
                        reached = condition.wait_for(lambda: len(lock_connections) == 2, timeout=5)
                    self.assertTrue(reached, "Both checkouts must reach the parent lock on separate connections.")
                    self.assertTrue(all(not future.done() for future in futures))
                finally:
                    blocker.commit()
                responses = [future.result(timeout=10) for future in futures]
        finally:
            blocker.rollback()
            blocker.close()
            event.remove(engine, "before_cursor_execute", observe_parent_lock)
        self.assertEqual(sorted(code for code, _ in responses), [201, 409], responses)
        snapshot = self.assert_snapshot(identity, ["PENDING", "PENDING"], None, delivery_exists=False)
        successful = next(body for code, body in responses if code == 201)
        self.assertEqual(successful["timestamp"], identity["timestamp"])
        self.assertEqual({row["order_id"] for row in snapshot["orders"]},
                         {row["order_id"] for row in successful["orders"]})
        self.record(self._testMethodName, identity, [
            {"path": "/api/add_new_order", "status": code, "body": body} for code, body in responses
        ], snapshot, distinct_mysql_connections=len(lock_connections),
                    injected_clock=identity["timestamp"])

    def test_checkout_rejects_existing_delivery_key_even_without_order_rows(self):
        fixed_time = datetime(2038, 1, 2, 3, 4, 5)

        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_time

        identity = {"buyer_id": self.fixture["buyer_id"],
                    "timestamp": fixed_time.strftime("%Y-%m-%d %H:%M:%S")}
        with self.server.app.app_context():
            self.server.db.session.add(self.server.Delivery(
                buyer_id=identity["buyer_id"], timestamp=fixed_time,
                deliverer_id=None, pickup_address="保留的合成外送資料",
            ))
            self.server.db.session.commit()
        before = self.assert_snapshot(identity, [], None)
        with self.server.app.test_client() as client, patch.object(self.server, "datetime", FixedClock):
            response = client.post("/api/add_new_order", json={
                "buyer_id": identity["buyer_id"],
                "meals": [{"meal_id": self.fixture["meal_ids"][0], "quantity": 1}],
            })
        self.assertEqual(response.status_code, 409, response.get_json())
        after = self.assert_snapshot(identity, [], None)
        self.assertEqual(before, after)
        self.record(self._testMethodName, identity, [{
            "path": "/api/add_new_order", "status": response.status_code, "body": response.get_json(),
        }], after, preexisting_delivery_without_order=True)

    def test_quantity_database_limit_rejects_entire_request_before_writing(self):
        fixed_time = datetime(2039, 1, 2, 3, 4, 5)

        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_time

        identity = {"buyer_id": self.fixture["buyer_id"],
                    "timestamp": fixed_time.strftime("%Y-%m-%d %H:%M:%S")}
        with self.server.app.test_client() as client, patch.object(self.server, "datetime", FixedClock):
            rejected = client.post("/api/add_new_order", json={
                "buyer_id": identity["buyer_id"],
                "meals": [
                    {"meal_id": self.fixture["meal_ids"][0], "quantity": 1},
                    {"meal_id": self.fixture["meal_ids"][1], "quantity": 2_147_483_648},
                ],
            })
        self.assertEqual(rejected.status_code, 400, rejected.get_json())
        self.assertIs(rejected.get_json()["success"], False)
        rejected_snapshot = self.assert_snapshot(identity, [], None, delivery_exists=False)
        # This is a storage limit, not a newly invented business quantity cap.
        with self.server.app.test_client() as client, patch.object(self.server, "datetime", FixedClock):
            accepted = client.post("/api/add_new_order", json={
                "buyer_id": identity["buyer_id"],
                "meals": [{"meal_id": self.fixture["meal_ids"][0], "quantity": 2_147_483_647}],
            })
        self.assertEqual(accepted.status_code, 201, accepted.get_json())
        self.assertEqual(accepted.get_json()["orders"][0]["quantity"], 2_147_483_647)
        snapshot = self.assert_snapshot(identity, ["PENDING"], None, delivery_exists=False)
        self.record(self._testMethodName, identity, [
            {"path": "/api/add_new_order", "status": response.status_code, "body": response.get_json()}
            for response in (rejected, accepted)
        ], snapshot, rejected_request_snapshot=rejected_snapshot)

    def test_checkout_response_survives_delete_after_commit(self):
        from sqlalchemy import event
        from sqlalchemy.orm import Session

        fixed_time = datetime(2040, 1, 2, 3, 4, 5)

        class FixedClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixed_time

        identity = {"buyer_id": self.fixture["buyer_id"],
                    "timestamp": fixed_time.strftime("%Y-%m-%d %H:%M:%S")}
        checkout_thread = threading.get_ident()
        committed = []
        deletion_responses = []

        def delete_from_another_request():
            with self.server.app.test_client() as client:
                response = client.delete("/api/delete_order", json=identity)
                return {"path": "/api/delete_order", "status": response.status_code,
                        "body": response.get_json()}

        def delete_after_checkout_commit(_session):
            if threading.get_ident() != checkout_thread or committed:
                return
            committed.append(database_snapshot(self.settings, identity))
            # The create transaction has committed. A different request deletes
            # its rows before the original handler finishes building its response.
            with ThreadPoolExecutor(max_workers=1) as pool:
                deletion_responses.append(pool.submit(delete_from_another_request).result(timeout=10))

        event.listen(Session, "after_commit", delete_after_checkout_commit)
        try:
            with self.server.app.test_client() as client, patch.object(self.server, "datetime", FixedClock):
                response = client.post("/api/add_new_order", json={
                    "buyer_id": identity["buyer_id"],
                    "meals": [{"meal_id": meal, "quantity": 1} for meal in self.fixture["meal_ids"]],
                })
                body = response.get_json()
        finally:
            event.remove(Session, "after_commit", delete_after_checkout_commit)
        final = self.assert_snapshot(identity, [], None, delivery_exists=False)
        self.record(self._testMethodName, identity, [
            {"path": "/api/add_new_order", "status": response.status_code, "body": body},
            *deletion_responses,
        ], final, committed_before_concurrent_delete=committed,
                    scheduling_hook="delete_order request after create commit, before response serialization")
        self.assertEqual(len(committed), 1)
        self.assertEqual([row["state"] for row in committed[0]["orders"]], ["PENDING", "PENDING"])
        self.assertEqual(len(deletion_responses), 1)
        self.assertEqual(deletion_responses[0]["status"], 200, deletion_responses)
        self.assertEqual(deletion_responses[0]["body"]["deleted_count"], 2)
        self.assertEqual(response.status_code, 201, body)
        self.assertTrue(body["success"])
        self.assertEqual(body["timestamp"], identity["timestamp"])
        self.assertEqual({row["order_id"] for row in body["orders"]},
                         {row["order_id"] for row in committed[0]["orders"]})
        self.assertEqual([row["order_state"] for row in body["orders"]], ["pending", "pending"])

    def test_returned_timestamp_connects_order_delivery_and_acceptance_at_fractional_boundaries(self):
        for microsecond in [0, 499_999, 500_000, 999_999]:
            with self.subTest(microsecond=microsecond):
                fixture = create_fixture(self.server)
                fixed_time = datetime(2032, 1, 1, 12, 0, 0, microsecond)

                class FixedClock(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return fixed_time

                responses = []

                def request(path, payload=None, expected_status=200):
                    with self.server.app.test_client() as client:
                        response = (client.get(path) if payload is None
                                    else client.post(path, json=payload))
                        body = response.get_json()
                    self.assertEqual(response.status_code, expected_status, body)
                    responses.append({"path": path, "status": response.status_code, "body": body})
                    return body

                with patch.object(self.server, "datetime", FixedClock):
                    created = request("/api/add_new_order", {
                            "buyer_id": fixture["buyer_id"],
                            "meals": [{"meal_id": meal, "quantity": index + 1}
                                      for index, meal in enumerate(fixture["meal_ids"])],
                        }, 201)
                self.assertTrue(created["success"])
                identity = {"buyer_id": fixture["buyer_id"], "timestamp": created["timestamp"]}
                self.assertEqual(len(created["orders"]), 2)
                self.assertTrue(all(row["timestamp"] == identity["timestamp"] for row in created["orders"]))
                courier = fixture["deliverer_ids"][0]
                request("/api/process_order", {**identity, "deliverer_id": courier}, 404)
                missing_delivery_snapshot = self.assert_snapshot(
                    identity, ["PENDING", "PENDING"], None, delivery_exists=False,
                )
                delivery = request("/api/add_new_delivery", {
                    **identity, "pickup_address": "合成時間精度驗收資料",
                }, 201)
                self.assertTrue(delivery["success"])
                self.assertEqual(delivery["delivery"]["timestamp"], identity["timestamp"])

                # The lobby gets a string buyer_id and reuses the returned timestamp.
                with self.server.app.test_client() as client:
                    listed = client.get("/api/view_order_by_stamp")
                    self.assertEqual(listed.status_code, 200, listed.get_json())
                    matching_stamps = [stamp for stamp in listed.get_json()
                                       if str(stamp["buyer_id"]) == str(identity["buyer_id"])
                                       and stamp["timestamp"] == identity["timestamp"]]
                self.assertEqual(matching_stamps, [{**identity, "buyer_id": str(identity["buyer_id"])}])
                listed_identity = matching_stamps[0]
                responses.append({"path": "/api/view_order_by_stamp", "status": 200,
                                  "matching_stamps": matching_stamps})
                before_meals = request("/api/view_meals_of_order", listed_identity)
                before_delivery = request("/api/view_delivery", listed_identity)
                self.assertEqual([row["order_state"] for row in before_meals], ["pending", "pending"])
                self.assertEqual([row["quantity"] for row in before_meals], [1, 2])
                self.assertEqual(len(before_delivery), 1)
                self.assertIsNone(before_delivery[0]["deliverer_id"])

                accepted = request("/api/process_order", {**listed_identity, "deliverer_id": courier})
                self.assertTrue(accepted["success"])
                self.assertEqual(accepted["buyer_id"], identity["buyer_id"])
                self.assertEqual(accepted["timestamp"], identity["timestamp"])
                self.assertEqual(accepted["deliverer_id"], courier)
                self.assertEqual({row["order_id"] for row in accepted["processed_orders"]},
                                 {row["order_id"] for row in created["orders"]})
                after_meals = request("/api/view_meals_of_order", listed_identity)
                after_delivery = request("/api/view_delivery", listed_identity)
                self.assertEqual([row["order_state"] for row in after_meals], ["processing", "processing"])
                self.assertEqual(len(after_delivery), 1)
                self.assertEqual(after_delivery[0]["deliverer_id"], courier)
                snapshot = self.assert_snapshot(identity, ["PROCESSING", "PROCESSING"], courier)
                self.assertEqual({row["order_id"] for row in snapshot["orders"]},
                                 {row["order_id"] for row in created["orders"]})
                self.record(self._testMethodName, identity, responses, snapshot,
                            before_delivery_created=missing_delivery_snapshot,
                            injected_clock_microsecond=microsecond)


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(OrderAcceptanceMySQLTests)
    # Existing backend error handlers print diagnostics. Keep them on stderr so
    # stdout remains a machine-readable evidence JSON object, without changing
    # any application error-handling behavior.
    with redirect_stdout(sys.stderr):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({
        "suite": "order_acceptance_mysql",
        "successful": result.wasSuccessful(),
        "tests_run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "metadata": OrderAcceptanceMySQLTests.metadata,
        "evidence": OrderAcceptanceMySQLTests.evidence,
    }, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
