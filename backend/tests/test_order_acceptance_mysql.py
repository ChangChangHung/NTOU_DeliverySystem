"""Real MySQL 8 acceptance checks for the existing process_order API.

Run with explicit local DB credentials and NTOU_ACCEPTANCE_ALLOW_CREATE=1.
The script creates a fresh ntou_acceptance_* schema, never deletes data, and
prints a JSON evidence summary. It does not start or contact an HTTP server.
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

    def test_returned_order_timestamp_can_create_delivery_at_fractional_boundaries(self):
        for microsecond in [0, 499_999, 500_000, 999_999]:
            with self.subTest(microsecond=microsecond):
                fixture = create_fixture(self.server)
                fixed_time = datetime(2032, 1, 1, 12, 0, 0, microsecond)

                class FixedClock(datetime):
                    @classmethod
                    def now(cls, tz=None):
                        return fixed_time

                with self.server.app.test_client() as client:
                    with patch.object(self.server, "datetime", FixedClock):
                        created = client.post("/api/add_new_order", json={
                            "buyer_id": fixture["buyer_id"],
                            "meals": [{"meal_id": meal, "quantity": 1} for meal in fixture["meal_ids"]],
                        })
                    self.assertEqual(created.status_code, 201, created.get_json())
                    body = created.get_json()
                    identity = {"buyer_id": fixture["buyer_id"], "timestamp": body["timestamp"]}
                    delivery = client.post("/api/add_new_delivery", json={
                        **identity, "pickup_address": "合成時間精度驗收資料",
                    })
                    self.assertEqual(delivery.status_code, 201, delivery.get_json())
                snapshot = self.assert_snapshot(identity, ["PENDING", "PENDING"], None)
                self.record(self._testMethodName, identity, [
                    {"status": created.status_code, "body": body},
                    {"status": delivery.status_code, "body": delivery.get_json()},
                ], snapshot, injected_clock_microsecond=microsecond)


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
