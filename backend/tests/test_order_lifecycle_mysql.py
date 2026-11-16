"""Check order lifecycle transactions on a fresh, isolated MySQL 8 schema.

Use explicit local DB settings and NTOU_ACCEPTANCE_ALLOW_CREATE=1. The script
retains its synthetic rows, starts no HTTP service, and prints a JSON report.
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import sys
import threading
import unittest

from sqlalchemy import event
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.mysql_support import create_fixture, create_local_application, database_snapshot


class OrderLifecycleMySQLTests(unittest.TestCase):
    metadata = {}
    evidence = []

    @classmethod
    def setUpClass(cls):
        cls.server, cls.settings, cls.metadata = create_local_application("lifecycle")
        cls.evidence = []
        cls.metadata["test_source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        with cls.server.app.app_context():
            cls.engine = cls.server.db.engine

    @classmethod
    def tearDownClass(cls):
        with cls.server.app.app_context():
            cls.server.db.session.remove()
            cls.engine.dispose()

    def request(self, operation, fixture, **extra):
        payload = {"buyer_id": fixture["buyer_id"], "timestamp": fixture["timestamp"], **extra}
        if operation == "process_order":
            payload.setdefault("deliverer_id", fixture["deliverer_ids"][0])
        if operation == "add_new_delivery":
            payload.setdefault("pickup_address", "生命週期測試地址（合成資料）")
        with self.server.app.test_client() as client:
            response = client.open("/api/" + operation, json=payload,
                                   method="DELETE" if operation == "delete_order" else "POST")
            return {"operation": operation, "status": response.status_code,
                    "body": response.get_json(silent=True)}

    def processing_fixture(self):
        fixture = create_fixture(self.server)
        accepted = self.request("process_order", fixture)
        self.assertEqual(accepted["status"], 200, accepted)
        return fixture

    def assert_database(self, fixture, state, courier, delivery_exists=True):
        snapshot = database_snapshot(self.settings, fixture)
        expected_ids = [] if state is None else fixture["order_ids"]
        self.assertEqual([item["order_id"] for item in snapshot["orders"]], expected_ids)
        self.assertEqual([item["state"] for item in snapshot["orders"]],
                         [state] * len(expected_ids))
        self.assertEqual(snapshot["delivery_exists"], delivery_exists)
        self.assertEqual(snapshot["deliverer_id"], courier)
        return snapshot

    def record(self, fixture, responses, snapshot, **details):
        self.evidence.append({"case": self._testMethodName,
                              "identity": {key: fixture[key] for key in ("buyer_id", "timestamp")},
                              "responses": responses, "committed_database_state": snapshot,
                              **details})

    def overlap(self, first, second, fixture, expected_codes, state, courier, delivery_exists=True):
        """Keep the first transaction open after validation while the second reads."""
        first_ready, second_read, release_first = (threading.Event() for _ in range(3))
        threads, mysql_connections = {}, {}
        paused = []

        def before_flush(_session, _flush_context, _instances):
            if threading.get_ident() == threads.get("first") and not paused:
                paused.append(True)
                first_ready.set()
                if not release_first.wait(10):
                    raise RuntimeError("First transaction was not released")

        def observe_order_read(connection, _cursor, statement, _parameters, _context, _many):
            sql = " ".join(statement.upper().split())
            if sql.startswith("SELECT") and "FROM ORDERS" in sql:
                thread = threading.get_ident()
                mysql_connections.setdefault(thread, connection.connection.driver_connection.thread_id())
                if thread == threads.get("second"):
                    second_read.set()

        def run(label, operation):
            threads[label] = threading.get_ident()
            return self.request(operation, fixture)

        event.listen(Session, "before_flush", before_flush)
        event.listen(self.engine, "before_cursor_execute", observe_order_read)
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                first_future = pool.submit(run, "first", first)
                try:
                    self.assertTrue(first_ready.wait(5), "First request did not pass its state checks")
                    second_future = pool.submit(run, "second", second)
                    self.assertTrue(second_read.wait(5), "Second request did not attempt its order read")
                    # A plain SELECT would permit the second operation to commit here.
                    with self.assertRaises(FutureTimeout, msg="Second operation must wait for the order lock"):
                        second_future.result(timeout=0.15)
                finally:
                    release_first.set()
                responses = [first_future.result(timeout=10), second_future.result(timeout=10)]
        finally:
            release_first.set()
            event.remove(Session, "before_flush", before_flush)
            event.remove(self.engine, "before_cursor_execute", observe_order_read)
        self.assertEqual([item["status"] for item in responses], list(expected_codes), responses)
        self.assertEqual(len(set(mysql_connections.values())), 2)
        snapshot = self.assert_database(fixture, state, courier, delivery_exists)
        self.record(fixture, responses, snapshot, first_operation=first, second_operation=second,
                    distinct_mysql_connections=2, second_waited_for_first_transaction=True)

    def test_complete_then_abandon_rejects_stale_transition(self):
        fixture = self.processing_fixture()
        self.overlap("complete_order", "abandon_order", fixture, (200, 409),
                     "COMPLETED", fixture["deliverer_ids"][0])

    def test_abandon_then_complete_rejects_stale_transition(self):
        fixture = self.processing_fixture()
        self.overlap("abandon_order", "complete_order", fixture, (200, 409), "PENDING", None)

    def test_cancel_then_abandon_does_not_resurrect_cancelled_order(self):
        fixture = self.processing_fixture()
        self.overlap("cancel_order", "abandon_order", fixture, (200, 409),
                     "CANCELLED", fixture["deliverer_ids"][0])

    def test_abandon_then_cancel_preserves_existing_pending_cancel_rule(self):
        fixture = self.processing_fixture()
        self.overlap("abandon_order", "cancel_order", fixture, (200, 200), "CANCELLED", None)

    def test_complete_then_cancel_rejects_stale_transition(self):
        fixture = self.processing_fixture()
        self.overlap("complete_order", "cancel_order", fixture, (200, 409),
                     "COMPLETED", fixture["deliverer_ids"][0])

    def test_cancel_then_complete_rejects_stale_transition(self):
        fixture = self.processing_fixture()
        self.overlap("cancel_order", "complete_order", fixture, (200, 409),
                     "CANCELLED", fixture["deliverer_ids"][0])

    def test_delivery_creation_then_acceptance_uses_current_delivery(self):
        fixture = create_fixture(self.server, delivery=False)
        self.overlap("add_new_delivery", "process_order", fixture, (201, 200),
                     "PROCESSING", fixture["deliverer_ids"][0])

    def test_duplicate_delivery_creation_is_serialized(self):
        fixture = create_fixture(self.server, delivery=False)
        self.overlap("add_new_delivery", "add_new_delivery", fixture, (201, 409), "PENDING", None)

    def test_reaccept_reads_current_delivery_after_earlier_user_snapshot(self):
        fixture = self.processing_fixture()
        user_read, release_process = threading.Event(), threading.Event()
        worker = {}
        paused = []

        def pause_after_user_read(_connection, _cursor, statement, _parameters, _context, _many):
            sql = " ".join(statement.upper().split())
            if (threading.get_ident() == worker.get("thread") and not paused
                    and sql.startswith("SELECT") and "FROM USERS" in sql):
                paused.append(True)
                user_read.set()
                if not release_process.wait(10):
                    raise RuntimeError("Waiting acceptance was not released")

        def reaccept():
            worker["thread"] = threading.get_ident()
            return self.request("process_order", fixture, deliverer_id=fixture["deliverer_ids"][1])

        event.listen(self.engine, "after_cursor_execute", pause_after_user_read)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(reaccept)
                try:
                    self.assertTrue(user_read.wait(5), "Acceptance must establish a pre-abandon read snapshot")
                    abandoned = self.request("abandon_order", fixture)
                    self.assertEqual(abandoned["status"], 200, abandoned)
                    self.assert_database(fixture, "PENDING", None)
                finally:
                    release_process.set()
                accepted = future.result(timeout=10)
        finally:
            release_process.set()
            event.remove(self.engine, "after_cursor_execute", pause_after_user_read)
        self.assertEqual(accepted["status"], 200, accepted)
        snapshot = self.assert_database(fixture, "PROCESSING", fixture["deliverer_ids"][1])
        self.record(fixture, [abandoned, accepted], snapshot,
                    user_snapshot_created_before_abandon_commit=True)

    def assert_rollback(self, operation, expected_updated_tables):
        fixture = self.processing_fixture()
        updates, injected = [], []

        def fail_after_update(_connection, _cursor, statement, _parameters, _context, _many):
            sql = " ".join(statement.upper().split())
            for table in ("ORDERS", "DELIVERY"):
                if sql.startswith("UPDATE " + table):
                    updates.append(table)
            if not injected and set(updates) == set(expected_updated_tables):
                injected.append(True)
                raise RuntimeError("synthetic lifecycle failure after real UPDATE")

        event.listen(self.engine, "after_cursor_execute", fail_after_update)
        try:
            response = self.request(operation, fixture)
        finally:
            event.remove(self.engine, "after_cursor_execute", fail_after_update)
        self.assertTrue(injected, "Failure must follow real database updates")
        self.assertEqual(response["status"], 500, response)
        self.assertIsInstance(response["body"], dict, "Failure must return JSON")
        self.assertFalse(response["body"]["success"])
        snapshot = self.assert_database(fixture, "PROCESSING", fixture["deliverer_ids"][0])
        self.record(fixture, [response], snapshot, synthetic_failure_injection=True,
                    updated_tables_before_failure=updates)

    def test_cancel_failure_returns_json_and_rolls_back_all_meals(self):
        self.assert_rollback("cancel_order", ["ORDERS"])

    def test_complete_failure_rolls_back_all_meals(self):
        self.assert_rollback("complete_order", ["ORDERS"])

    def test_abandon_failure_rolls_back_both_tables(self):
        self.assert_rollback("abandon_order", ["ORDERS", "DELIVERY"])

    def assert_paired_deletion(self, state):
        fixture = create_fixture(self.server) if state == "PENDING" else self.processing_fixture()
        if state == "COMPLETED":
            completed = self.request("complete_order", fixture)
            self.assertEqual(completed["status"], 200, completed)
        before = database_snapshot(self.settings, fixture)
        deleted = self.request("delete_order", fixture)
        self.assertEqual(deleted["status"], 200, deleted)
        self.assertEqual(deleted["body"], {"success": True, "message": "訂單刪除成功",
                                         "deleted_count": len(fixture["order_ids"])})
        snapshot = self.assert_database(fixture, None, None, delivery_exists=False)
        repeated = self.request("delete_order", fixture)
        self.assertEqual(repeated["status"], 200, repeated)
        self.assertEqual(repeated["body"], {"success": True, "message": "訂單刪除成功",
                                          "deleted_count": 0})
        self.record(fixture, [deleted, repeated], snapshot, initial_state=state, before=before,
                    note="Preserves existing deletion eligibility; this does not approve a business rule.")

    def test_delete_pending_removes_orders_and_delivery_together(self):
        self.assert_paired_deletion("PENDING")

    def test_delete_processing_preserves_existing_rule_and_removes_both_tables(self):
        self.assert_paired_deletion("PROCESSING")

    def test_delete_completed_preserves_existing_rule_and_removes_both_tables(self):
        self.assert_paired_deletion("COMPLETED")

    def test_delete_failure_after_flush_rolls_back_both_tables(self):
        fixture = self.processing_fixture()
        deleted_tables, injected = [], []

        def observe_delete(_connection, _cursor, statement, _parameters, _context, _many):
            sql = " ".join(statement.upper().split())
            for table in ("ORDERS", "DELIVERY"):
                if sql.startswith("DELETE FROM " + table):
                    deleted_tables.append(table)

        def fail_before_commit(_connection):
            if set(deleted_tables) == {"ORDERS", "DELIVERY"} and not injected:
                injected.append(True)
                raise RuntimeError("synthetic deletion commit failure after both tables were flushed")

        event.listen(self.engine, "after_cursor_execute", observe_delete)
        event.listen(self.engine, "commit", fail_before_commit)
        try:
            response = self.request("delete_order", fixture)
        finally:
            event.remove(self.engine, "after_cursor_execute", observe_delete)
            event.remove(self.engine, "commit", fail_before_commit)
        self.assertTrue(injected, "Both real DELETEs must be flushed before commit fails")
        self.assertEqual(set(deleted_tables), {"ORDERS", "DELIVERY"})
        self.assertEqual(response["status"], 500, response)
        self.assertFalse(response["body"]["success"])
        snapshot = self.assert_database(fixture, "PROCESSING", fixture["deliverer_ids"][0])
        self.record(fixture, [response], snapshot, synthetic_failure_injection=True,
                    deleted_tables_before_commit_failure=deleted_tables)

    def test_delete_then_delivery_creation_cannot_leave_orphan_delivery(self):
        fixture = create_fixture(self.server, delivery=False)
        self.overlap("delete_order", "add_new_delivery", fixture, (200, 404),
                     None, None, delivery_exists=False)

    def test_delivery_creation_then_delete_removes_current_delivery(self):
        fixture = create_fixture(self.server, delivery=False)
        self.overlap("add_new_delivery", "delete_order", fixture, (201, 200),
                     None, None, delivery_exists=False)

    def assert_committed_response_survives_delete(self, operation, state, result_key=None):
        fixture = create_fixture(self.server, delivery=operation != "add_new_delivery")
        if operation in {"cancel_order", "complete_order", "abandon_order"}:
            accepted = self.request("process_order", fixture)
            self.assertEqual(accepted["status"], 200, accepted)
        committed, release_response = threading.Event(), threading.Event()
        worker, paused = {}, []

        def pause_after_commit(_session):
            if threading.get_ident() == worker.get("thread") and not paused:
                paused.append(True)
                committed.set()
                if not release_response.wait(10):
                    raise RuntimeError("Committed response was not released")

        def first_request():
            worker["thread"] = threading.get_ident()
            return self.request(operation, fixture)

        event.listen(Session, "after_commit", pause_after_commit)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(first_request)
                try:
                    self.assertTrue(committed.wait(5), "First transaction must really commit")
                    courier = (None if operation in {"abandon_order", "add_new_delivery"}
                               else fixture["deliverer_ids"][0])
                    first_committed = self.assert_database(fixture, state, courier)
                    deleted = self.request("delete_order", fixture)
                    self.assertEqual(deleted["status"], 200, deleted)
                    self.assertEqual(deleted["body"]["deleted_count"], len(fixture["order_ids"]))
                    final = self.assert_database(fixture, None, None, delivery_exists=False)
                finally:
                    release_response.set()
                response = future.result(timeout=10)
        finally:
            release_response.set()
            event.remove(Session, "after_commit", pause_after_commit)
        self.assertEqual(response["status"], 201 if operation == "add_new_delivery" else 200, response)
        body = response["body"]
        self.assertTrue(body["success"])
        if operation == "add_new_delivery":
            self.assertEqual(body, {
                "success": True, "message": "外送需求建立成功", "delivery": {
                    "buyer_id": fixture["buyer_id"], "timestamp": fixture["timestamp"],
                    "deliverer_id": None, "pickup_address": "生命週期測試地址（合成資料）",
                },
            })
        else:
            self.assertEqual(body["buyer_id"], fixture["buyer_id"])
            self.assertEqual(body["timestamp"], fixture["timestamp"])
            self.assertEqual(body[result_key], [
                {"order_id": order_id, "meal_id": meal_id, "quantity": index + 1,
                 "order_state": state.lower()}
                for index, (order_id, meal_id) in enumerate(zip(fixture["order_ids"], fixture["meal_ids"]))
            ])
            if operation in {"process_order", "abandon_order"}:
                self.assertEqual(body["deliverer_id"], courier)
        self.record(fixture, [response, deleted], final,
                    committed_before_delete=first_committed,
                    scheduling="First transaction commits, second request deletes both tables, first response then resumes.")

    def test_acceptance_response_survives_delete_after_commit(self):
        self.assert_committed_response_survives_delete("process_order", "PROCESSING", "processed_orders")

    def test_cancellation_response_survives_delete_after_commit(self):
        self.assert_committed_response_survives_delete("cancel_order", "CANCELLED", "cancelled_orders")

    def test_completion_response_survives_delete_after_commit(self):
        self.assert_committed_response_survives_delete("complete_order", "COMPLETED", "completed_orders")

    def test_abandon_response_survives_delete_after_commit(self):
        self.assert_committed_response_survives_delete("abandon_order", "PENDING", "updated_orders")

    def test_delivery_creation_response_survives_delete_after_commit(self):
        self.assert_committed_response_survives_delete("add_new_delivery", "PENDING")


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(OrderLifecycleMySQLTests)
    with redirect_stdout(sys.stderr):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({"suite": "order_lifecycle_mysql", "successful": result.wasSuccessful(),
                      "tests_run": result.testsRun, "failures": len(result.failures),
                      "errors": len(result.errors), "metadata": OrderLifecycleMySQLTests.metadata,
                      "evidence": OrderLifecycleMySQLTests.evidence}, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
