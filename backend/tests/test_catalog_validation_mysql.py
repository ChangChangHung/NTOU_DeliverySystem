"""Check catalog input limits and rollback using a fresh local MySQL 8 schema.

Requires explicit DB credentials and NTOU_ACCEPTANCE_ALLOW_CREATE=1, like the
other MySQL suites. It never reuses an existing schema; deletion tests only
remove the catalog rows they just created.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
import hashlib
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
import uuid


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.mysql_support import create_local_application  # noqa: E402


class CatalogValidationMySQLTests(unittest.TestCase):
    metadata: dict = {}
    evidence: list[dict] = []

    @classmethod
    def setUpClass(cls):
        cls.server, cls.settings, cls.metadata = create_local_application("catalog")
        cls.metadata["source_sha256"]["tests/test_catalog_validation_mysql.py"] = (
            hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        )
        cls.evidence = []

    @classmethod
    def tearDownClass(cls):
        with cls.server.app.app_context():
            cls.server.db.session.remove()
            cls.server.db.engine.dispose()

    def post(self, path, payload, status):
        with self.server.app.test_client() as client:
            response = client.post(path, json=payload)
            self.assertEqual(response.status_code, status, response.get_data(as_text=True))
            self.assertTrue(response.is_json, response.get_data(as_text=True))
            return {"path": path, "status": response.status_code, "body": response.get_json()}

    def counts(self):
        connection = self.settings.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM restaurants")
                restaurants = cursor.fetchone()[0]
                cursor.execute("SELECT COUNT(*) FROM meals")
                meals = cursor.fetchone()[0]
            return {"restaurants": restaurants, "meals": meals}
        finally:
            connection.close()

    def restaurant_row(self, restaurant_id):
        connection = self.settings.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT restaurant_id, restaurant_name, restaurant_address "
                    "FROM restaurants WHERE restaurant_id=%s", (restaurant_id,),
                )
                row = cursor.fetchone()
                self.assertIsNotNone(row)
                return dict(zip(("restaurant_id", "restaurant_name", "restaurant_address"), row))
        finally:
            connection.close()

    def meal_row(self, meal_id):
        connection = self.settings.connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT meal_id, meal_name, restaurant_id, price FROM meals WHERE meal_id=%s",
                    (meal_id,),
                )
                row = cursor.fetchone()
                self.assertIsNotNone(row)
                return dict(zip(("meal_id", "meal_name", "restaurant_id", "price"), row))
        finally:
            connection.close()

    def new_restaurant(self):
        response = self.post("/api/add_new_restaurant", {
            "restaurant_name": f"合成餐廳{uuid.uuid4().hex[:12]}",
            "restaurant_address": "合成地址，不是真實商家",
        }, 201)
        return response["body"]["restaurant_id"]

    def record(self, responses, **details):
        self.evidence.append({"case": self._testMethodName, "responses": responses, **details})

    def test_restaurant_limits_count_trimmed_chinese_characters(self):
        before = self.counts()
        payload = {"restaurant_name": " \t" + "餐" * 40 + "\n ",
                   "restaurant_address": "　" + "址" * 80 + "　"}
        response = self.post("/api/add_new_restaurant", payload, 201)
        row = self.restaurant_row(response["body"]["restaurant_id"])
        self.assertEqual(row, response["body"])
        self.assertEqual(row["restaurant_name"], "餐" * 40)
        self.assertEqual(row["restaurant_address"], "址" * 80)
        after = self.counts()
        self.assertEqual(after, {"restaurants": before["restaurants"] + 1, "meals": before["meals"]})
        self.record([response], committed_row=row, counts_before=before, counts_after=after)

    def test_restaurant_overlong_fields_reject_without_writes_and_recover(self):
        responses = []
        checks = []
        for field, limit, character in (("restaurant_name", 40, "館"),
                                         ("restaurant_address", 80, "路")):
            with self.subTest(field=field):
                payload = {"restaurant_name": f"合成餐廳{uuid.uuid4().hex[:12]}",
                           "restaurant_address": "合成測試地址"}
                payload[field] = " " + character * (limit + 1) + " "
                before = self.counts()
                rejected = self.post("/api/add_new_restaurant", payload, 400)
                after_rejection = self.counts()
                self.assertEqual(after_rejection, before)
                payload[field] = " " + character * limit + " "
                recovered = self.post("/api/add_new_restaurant", payload, 201)
                row = self.restaurant_row(recovered["body"]["restaurant_id"])
                self.assertEqual(row[field], character * limit)
                self.assertEqual(self.counts(), {
                    "restaurants": before["restaurants"] + 1, "meals": before["meals"],
                })
                responses.extend([rejected, recovered])
                checks.append({"field": field, "trimmed_rejected_length": limit + 1,
                               "counts_before": before, "counts_after_rejection": after_rejection,
                               "committed_recovery_row": row})
        self.record(responses, checks=checks)

    def test_meal_name_limit_accepts_trimmed_chinese(self):
        restaurant_id = self.new_restaurant()
        before = self.counts()
        response = self.post("/api/add_new_meal", {
            "restaurant_id": restaurant_id, "meal_name": " \t" + "飯" * 40 + "　", "price": 80,
        }, 201)
        row = self.meal_row(response["body"]["meal_id"])
        self.assertEqual(row, response["body"])
        self.assertEqual(row["meal_name"], "飯" * 40)
        after = self.counts()
        self.assertEqual(after, {"restaurants": before["restaurants"], "meals": before["meals"] + 1})
        self.record([response], committed_row=row, counts_before=before, counts_after=after)

    def test_meal_overlong_name_rejects_without_writes_and_recovers(self):
        restaurant_id = self.new_restaurant()
        before = self.counts()
        payload = {"restaurant_id": restaurant_id, "meal_name": " " + "麵" * 41 + " ", "price": 80}
        rejected = self.post("/api/add_new_meal", payload, 400)
        after_rejection = self.counts()
        self.assertEqual(before, after_rejection)
        payload["meal_name"] = " " + "麵" * 40 + " "
        recovered = self.post("/api/add_new_meal", payload, 201)
        row = self.meal_row(recovered["body"]["meal_id"])
        self.assertEqual(row["meal_name"], "麵" * 40)
        self.assertEqual(self.counts(), {"restaurants": before["restaurants"], "meals": before["meals"] + 1})
        self.record([rejected, recovered], counts_before=before, counts_after_rejection=after_rejection,
                    committed_recovery_row=row)

    def test_price_integer_boundaries_are_persisted_exactly(self):
        restaurant_id = self.new_restaurant()
        responses = []
        rows = []
        for price in (1, 2_147_483_647):
            with self.subTest(price=price):
                response = self.post("/api/add_new_meal", {
                    "restaurant_id": restaurant_id, "meal_name": "價格界線合成餐點", "price": price,
                }, 201)
                row = self.meal_row(response["body"]["meal_id"])
                self.assertEqual(row["price"], price)
                responses.append(response)
                rows.append(row)
        self.record(responses, committed_rows=rows)

    def test_invalid_prices_do_not_insert_and_next_valid_price_succeeds(self):
        restaurant_id = self.new_restaurant()
        before = self.counts()
        responses = []
        for price in (2_147_483_648, "2147483648", True, False, 1.5, 0, -1):
            with self.subTest(price=price):
                response = self.post("/api/add_new_meal", {
                    "restaurant_id": restaurant_id, "meal_name": "不應新增的合成餐點", "price": price,
                }, 400)
                self.assertEqual(self.counts(), before)
                responses.append({**response, "input_price": price})
        recovered = self.post("/api/add_new_meal", {
            "restaurant_id": restaurant_id, "meal_name": "有效價格合成餐點", "price": 80,
        }, 201)
        row = self.meal_row(recovered["body"]["meal_id"])
        self.assertEqual(row["price"], 80)
        after = self.counts()
        self.assertEqual(after, {"restaurants": before["restaurants"], "meals": before["meals"] + 1})
        self.record([*responses, recovered], counts_before=before, counts_after=after,
                    committed_recovery_row=row)

    def check_failed_commit_and_recovery(self, path, payload, table):
        before = self.counts()
        flushed = []

        def fail_after_insert():
            self.server.db.session.flush()
            flushed.append(True)
            raise RuntimeError("synthetic catalog commit failure after INSERT")

        with patch.object(self.server.db.session, "commit", side_effect=fail_after_insert):
            failed = self.post(path, payload, 500)
        self.assertEqual(flushed, [True])
        self.assertIs(failed["body"]["success"], False)
        self.assertIn("message", failed["body"])
        after_failure = self.counts()
        self.assertEqual(after_failure, before)
        recovered = self.post(path, payload, 201)
        after = self.counts()
        self.assertEqual(after, {**before, table: before[table] + 1})
        row = (self.restaurant_row(recovered["body"]["restaurant_id"])
               if table == "restaurants" else self.meal_row(recovered["body"]["meal_id"]))
        self.assertEqual(row, recovered["body"])
        self.record([failed, recovered], synthetic_failure_injection=True, flushed_before_failure=True,
                    counts_before=before, counts_after_failure=after_failure, counts_after_recovery=after,
                    committed_recovery_row=row)

    def test_restaurant_commit_failure_returns_json_and_rolls_back_insert(self):
        self.check_failed_commit_and_recovery("/api/add_new_restaurant", {
            "restaurant_name": f"回滾餐廳{uuid.uuid4().hex[:12]}", "restaurant_address": "合成測試地址",
        }, "restaurants")

    def test_meal_commit_failure_returns_json_and_rolls_back_insert(self):
        self.check_failed_commit_and_recovery("/api/add_new_meal", {
            "restaurant_id": self.new_restaurant(), "meal_name": "回滾測試餐點", "price": 80,
        }, "meals")

    def check_response_survives_delete(self, path, delete_path, payload, table):
        from sqlalchemy import event
        from sqlalchemy.orm import Session

        before = self.counts()
        create_thread = threading.get_ident()
        committed = []
        deletion_responses = []
        id_field = "restaurant_id" if table == "restaurants" else "meal_id"

        def delete_from_another_request(row_id):
            with self.server.app.test_client() as client:
                response = client.delete(delete_path, json={id_field: row_id})
                return {"path": delete_path, "status": response.status_code,
                        "body": response.get_json()}

        def delete_after_create_commit(_session):
            if threading.get_ident() != create_thread or committed:
                return
            connection = self.settings.connect()
            try:
                with connection.cursor() as cursor:
                    if table == "restaurants":
                        cursor.execute("SELECT restaurant_id FROM restaurants WHERE restaurant_name=%s",
                                       (payload["restaurant_name"].strip(),))
                    else:
                        cursor.execute("SELECT meal_id FROM meals WHERE restaurant_id=%s AND meal_name=%s",
                                       (payload["restaurant_id"], payload["meal_name"].strip()))
                    rows = cursor.fetchall()
            finally:
                connection.close()
            self.assertEqual(len(rows), 1)
            row_id = rows[0][0]
            committed.append(self.restaurant_row(row_id) if table == "restaurants" else self.meal_row(row_id))
            with ThreadPoolExecutor(max_workers=1) as pool:
                deletion_responses.append(pool.submit(delete_from_another_request, row_id).result(timeout=10))

        event.listen(Session, "after_commit", delete_after_create_commit)
        try:
            with patch.dict(self.server.app.config, {"PROPAGATE_EXCEPTIONS": False}), self.server.app.test_client() as client:
                response = client.post(path, json=payload)
                body = response.get_json(silent=True)
        finally:
            event.remove(Session, "after_commit", delete_after_create_commit)
        after = self.counts()
        self.record([{"path": path, "status": response.status_code, "body": body}, *deletion_responses],
                    committed_before_concurrent_delete=committed, counts_before=before, counts_after=after,
                    scheduling_hook="delete API request after create commit, before response serialization")
        self.assertEqual(len(committed), 1)
        self.assertEqual(len(deletion_responses), 1)
        self.assertEqual(deletion_responses[0]["status"], 200, deletion_responses)
        self.assertEqual(before, after)
        self.assertEqual(response.status_code, 201, body)
        self.assertTrue(response.is_json)
        self.assertEqual(body, committed[0])

    def test_restaurant_response_survives_delete_after_commit(self):
        self.check_response_survives_delete("/api/add_new_restaurant", "/api/delete_restaurant", {
            "restaurant_name": f"刪除競爭餐廳{uuid.uuid4().hex[:12]}",
            "restaurant_address": "合成測試地址",
        }, "restaurants")

    def test_meal_response_survives_delete_after_commit(self):
        self.check_response_survives_delete("/api/add_new_meal", "/api/delete_meal", {
            "restaurant_id": self.new_restaurant(),
            "meal_name": f"刪除競爭餐點{uuid.uuid4().hex[:12]}", "price": 80,
        }, "meals")


def main() -> int:
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CatalogValidationMySQLTests)
    with redirect_stdout(sys.stderr):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps({
        "suite": "catalog_validation_mysql", "successful": result.wasSuccessful(),
        "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
        "metadata": CatalogValidationMySQLTests.metadata, "evidence": CatalogValidationMySQLTests.evidence,
    }, ensure_ascii=False, indent=2))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
