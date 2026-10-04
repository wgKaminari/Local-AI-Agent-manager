"""Offline catalog integrity checks; live availability is recorded in evidence."""
import fnmatch
from importlib.resources import files
import json
from pathlib import Path
import tomllib
import unittest
from urllib.parse import urlsplit

from norway_job_agent.company_catalog import (
    COMPANY_CATALOG, company_catalog, job_board_catalog, source_for_countries,
)
from norway_job_agent.company_sources import COMPANY_SOURCE_TYPES, validate_company_source
from norway_job_agent.countries import COUNTRIES, COUNTRY_TERMS
from norway_job_agent.sources import _board


class CatalogTests(unittest.TestCase):
    def test_directories_are_packaged_as_json_resources(self):
        data = files("norway_job_agent").joinpath("data")
        expected = {
            "employers_no_ua.json", "employers_us_de.json",
            "job_boards_no_ua.json", "job_boards_us_de.json",
        }
        self.assertTrue(expected <= {item.name for item in data.iterdir()})
        for name in expected:
            with self.subTest(name=name):
                rows = json.loads(data.joinpath(name).read_text(encoding="utf-8"))
                self.assertIsInstance(rows, list)
                self.assertTrue(rows)
        manifest = Path(__file__).resolve().parents[1] / "pyproject.toml"
        patterns = tomllib.loads(manifest.read_text(encoding="utf-8"))["tool"]["setuptools"]["package-data"]["norway_job_agent"]
        for name in expected:
            self.assertTrue(any(fnmatch.fnmatch("data/" + name, pattern) for pattern in patterns), name)

    def assert_public_url(self, url):
        parsed = urlsplit(url)
        self.assertEqual(parsed.scheme, "https", url)
        self.assertTrue(parsed.hostname and "." in parsed.hostname, url)
        self.assertIsNone(parsed.username, url)
        self.assertIsNone(parsed.password, url)
        self.assertNotIn(parsed.hostname, {"example.com", "example.org", "localhost"}, url)

    def test_records_keep_country_scope_and_reviewable_evidence(self):
        statuses = {
            "collector_checked", "collector_verified", "official_page_reviewed",
            "official_link_reviewed", "official_index_reviewed", "official_search_result",
            "access_limited", "page_response_only",
        }
        for catalog in (company_catalog(), job_board_catalog()):
            self.assertEqual(len(catalog), len({row["id"] for row in catalog}))
            for row in catalog:
                with self.subTest(entry=row["id"]):
                    self.assertRegex(row["id"], r"^[a-z0-9][a-z0-9-]*$")
                    self.assertTrue(row["name"].strip())
                    self.assertTrue(row["countries"])
                    self.assertEqual(len(row["countries"]), len(set(row["countries"])))
                    self.assertTrue(set(row["countries"]) <= set(COUNTRIES))
                    self.assertTrue(row["sectors"])
                    self.assertTrue(row["note"].strip())
                    self.assert_public_url(row["careers_url"])
                    self.assertRegex(row["checked_at"], r"^\d{4}-\d{2}-\d{2}$")
                    evidence = row["verification"]
                    self.assertIn(evidence["status"], statuses)
                    self.assertTrue(evidence["detail"].strip())
                    self.assertTrue(evidence["evidence_urls"])
                    for url in evidence["evidence_urls"]:
                        self.assert_public_url(url)

    def test_each_country_has_broad_independent_coverage(self):
        for code in COUNTRIES:
            with self.subTest(country=code):
                employers = company_catalog(code)
                boards = job_board_catalog(code)
                self.assertGreaterEqual(len(employers), 60)
                self.assertGreaterEqual(len(boards), 10)
                self.assertGreaterEqual(sum(bool(row.get("source")) for row in employers), 10)
                self.assertEqual(len(employers), len({row["name"].casefold() for row in employers}))
                self.assertTrue(all(code in row["countries"] for row in employers + boards))

    def test_manual_routes_never_masquerade_as_automatic_sources(self):
        for row in company_catalog() + job_board_catalog():
            with self.subTest(entry=row["id"]):
                verified = row["verification"]["status"] in {"collector_checked", "collector_verified"}
                self.assertEqual(bool(row.get("source")), verified)
                if row.get("access_mode") == "manual":
                    self.assertIsNone(row.get("source"))
                if row.get("access_mode") == "automatic":
                    self.assertTrue(row.get("source"))

    def test_automatic_sources_are_valid_and_not_duplicated(self):
        identities = set()
        for row in company_catalog():
            source = row.get("source")
            if not source:
                continue
            with self.subTest(entry=row["id"]):
                self.assertIn(source["type"], COMPANY_SOURCE_TYPES | {"greenhouse", "lever"})
                if source["type"] in COMPANY_SOURCE_TYPES:
                    validate_company_source(source)
                else:
                    _board(source["board"])
                    self.assertNotIn(source["board"].casefold(), {"example", "demo", "placeholder"})
                    if source["type"] == "lever":
                        self.assertIn(source.get("region", "global"), {"global", "eu"})
                self.assertTrue(source["locations"])
                self.assertTrue(all(isinstance(term, str) and term.strip() for term in source["locations"]))
                if "countries" in source:
                    self.assertEqual(set(source["countries"]), set(row["countries"]))
                identity = (source["type"], source.get("board", ""), source.get("region", "global"), source.get("url", ""))
                self.assertNotIn(identity, identities)
                identities.add(identity)

    def test_shared_boards_are_narrowed_to_selected_country_without_mutation(self):
        shared = [row for row in company_catalog() if row.get("source") and len(row["countries"]) > 1]
        self.assertTrue(shared)
        for row in shared:
            before = json.dumps(row, sort_keys=True)
            for code in row["countries"]:
                with self.subTest(entry=row["id"], country=code):
                    source = source_for_countries(row, [code])
                    self.assertEqual(source["countries"], [code])
                    self.assertEqual(source["locations"], list(dict.fromkeys(COUNTRY_TERMS[code])))
                    source["locations"].clear()
                    self.assertEqual(json.dumps(row, sort_keys=True), before)
            self.assertIsNone(source_for_countries(row, []))
            other = [code for code in COUNTRIES if code not in row["countries"]]
            self.assertIsNone(source_for_countries(row, other))

    def test_country_selection_preserves_legacy_norway_collector_limits(self):
        loaded = {row["id"]: row for row in company_catalog("NO")}
        for original in COMPANY_CATALOG:
            if not original.get("source"):
                continue
            with self.subTest(entry=original["id"]):
                source = source_for_countries(loaded[original["id"]], ["NO"])
                self.assertEqual(source, {**original["source"], "countries": ["NO"]})
                self.assertIsNone(source_for_countries(loaded[original["id"]], ["US", "DE", "UA"]))

    def test_manual_directories_are_independent_copies(self):
        first = job_board_catalog("US")
        first[0]["countries"].clear()
        first[0]["verification"]["evidence_urls"].clear()
        fresh = job_board_catalog("US")
        self.assertTrue(fresh[0]["countries"])
        self.assertTrue(fresh[0]["verification"]["evidence_urls"])


if __name__ == "__main__":
    unittest.main()
