import unittest
from unittest.mock import patch

import psycopg
from fastapi.testclient import TestClient

from app.main import app


class FakeCursor:
    def __init__(self, count=0, rows=None):
        self.count = count
        self.rows = rows or []

    def fetchone(self):
        return (self.count,)

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, count=0, rows=None):
        self.count = count
        self.rows = rows or []
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, params))
        return FakeCursor(self.count, self.rows)


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_search_returns_bilingual_context_and_uses_bound_filters(self):
        row = (
            "segment-id", "tbbt", "生活大爆炸", "The Big Bang Theory", 1, 1, "Pilot", 2,
            1500, 2500, "Sheldon says hello", "谢尔顿说你好", "Previous English", "前一句",
            "Next English", "后一句", True, False,
        )
        db = FakeConnection(count=1, rows=[row])
        with patch("app.api.search.connect", return_value=db):
            response = self.client.get("/api/v1/search", params={"q": "SHELDON", "season": 1, "episode": 1, "page": 2})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["page"], 2)
        self.assertEqual(body["results"][0]["text_zh"], "谢尔顿说你好")
        self.assertEqual(body["results"][0]["previous_en"], "Previous English")
        self.assertEqual(body["results"][0]["next_zh"], "后一句")
        self.assertEqual(body["results"][0]["matched_languages"], ["en"])

        sql, params = db.calls[0]
        self.assertIn("v.status = 'published'", sql)
        self.assertIn("sr.status = 'published'", sql)
        self.assertIn("rights.allow_preview IS TRUE", sql)
        self.assertIn("s.review_status = 'approved'", sql)
        self.assertIn("%sheldon%", params)
        self.assertIn(1, params)
        self.assertNotIn("SHELDON", sql)

    def test_empty_search_result_is_not_database_error(self):
        db = FakeConnection(count=0)
        with patch("app.api.search.connect", return_value=db):
            response = self.client.get("/api/v1/search", params={"q": "unknown phrase"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total"], 0)
        self.assertEqual(response.json()["results"], [])

    def test_database_failure_returns_service_unavailable_not_empty_results(self):
        with patch("app.api.search.connect", side_effect=psycopg.OperationalError("private connection details")):
            response = self.client.get("/api/v1/search", params={"q": "known phrase"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "database_unavailable")
        self.assertNotIn("private connection details", response.text)

    def test_like_metacharacters_are_escaped(self):
        db = FakeConnection(count=0)
        with patch("app.api.search.connect", return_value=db):
            response = self.client.get("/api/v1/search", params={"q": "100%_done"})
        self.assertEqual(response.status_code, 200)
        self.assertIn(r"%100\%\_done%", db.calls[0][1])


if __name__ == "__main__":
    unittest.main()
