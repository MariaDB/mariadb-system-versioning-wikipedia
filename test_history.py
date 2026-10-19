"""Tests for the parts that are easy to break without noticing.

    python -m unittest test_history

The database tests build a table `test_article` from schema.sql, so they need a running MariaDB
(as in common.py / .env), but they never touch the loaded data. Not a temporary table: MariaDB
does not allow CREATE TEMPORARY TABLE with system versioning.
"""
import re, unittest
import history
from common import connect


class CleanWikitext(unittest.TestCase):
    def test_templates_references_and_links(self):
        blocks = history.clean("{{Infobox|a={{b}}}}'''Talvisota''' oli [[sota|sotaa]] [[Suomi]] "
                               "vastaan.<ref>lähde</ref>\n== Tausta ==\n* kohta\n[[Luokka:Sodat]]")
        self.assertEqual(blocks, [("p", "Talvisota oli sotaa Suomi vastaan."),
                                  ("h", "Tausta"), ("li", "kohta")])


class TimeTravel(unittest.TestCase):
    """Load a small history with system_versioning_insert_history, as load.py does, and ask it."""

    @classmethod
    def setUpClass(cls):
        cls.con = connect()
        cls.cur = cls.con.cursor()
        cls.cur.execute("SET time_zone = '+00:00'")
        # The article table exactly as in schema.sql, under another name.
        with open("schema.sql") as f:
            sql = re.sub(r"--[^\n]*", "", f.read()).split(";")[0]
        cls.cur.execute("DROP TABLE IF EXISTS test_article")
        cls.cur.execute(sql.replace("CREATE TABLE IF NOT EXISTS article", "CREATE TABLE test_article"))
        cls.cur.execute("SET system_versioning_insert_history = 1")
        rows = [  # rev_id, user, sha1, start, end
            (1, "Alice",          "aaa", "2005-01-01 10:00:00", "2006-01-01 10:00:00"),
            (2, "10.0.0.1",       "bbb", "2006-01-01 10:00:00", "2006-01-01 10:05:00"),  # anonymous edit
            (3, "Bob",            "aaa", "2006-01-01 10:05:00", "2010-01-01 00:00:00"),  # revert: back to aaa
            (4, "Alice",          "ccc", "2010-01-01 00:00:00", "2010-01-02 00:00:00"),
            (5, "~2026-12345-6",  "ddd", "2010-01-02 00:00:00", "2010-01-03 00:00:00"),  # temporary account
            (6, "Bob",            "ccc", "2010-01-03 00:00:00", None),                   # revert, current
        ]
        for rev, user, sha1, start, end in rows:
            cols = "page_id, title, rev_id, user, sha1, text, row_start" + (", row_end" if end else "")
            values = [1, "T", rev, user, sha1, f"text {sha1}", start] + ([end] if end else [])
            cls.cur.execute(f"INSERT INTO test_article ({cols}) VALUES ({', '.join('?' * len(values))})", values)

    @classmethod
    def tearDownClass(cls):
        cls.cur.execute("DROP TABLE test_article")
        cls.con.close()

    def test_as_of_returns_the_version_current_then(self):
        self.assertEqual(history.as_of(self.cur, 1, "2005-06-01", "test_article")["rev_id"], 1)
        self.assertEqual(history.as_of(self.cur, 1, "2006-01-01 10:02", "test_article")["rev_id"], 2)
        self.assertEqual(history.as_of(self.cur, 1, "2008-01-01", "test_article")["rev_id"], 3)

    def test_nothing_before_the_first_version(self):
        self.assertIsNone(history.as_of(self.cur, 1, "2004-01-01", "test_article"))

    def test_current_version_without_for_system_time(self):
        self.cur.execute("SELECT rev_id FROM test_article WHERE page_id = 1")
        self.assertEqual(self.cur.fetchall(), [(6,)])

    def test_reverts_and_who_they_undid(self):
        versions = history.versions(self.cur, 1, "test_article")
        self.assertEqual([v["rev_id"] for v in versions if v["is_revert"]], [3, 6])
        self.assertTrue(all(v["undid_anonymous"] for v in versions if v["is_revert"]))


if __name__ == "__main__":
    unittest.main()
