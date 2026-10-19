"""Semantic regressions for matching thousands of adverts without rescanning prose."""
import html
import random
import re
import sys
import unicodedata
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent.experience_matching import _clauses, experience_requirements
from norway_job_agent.profile import _search_hint, contains_phrase, match_job


class MatchingPerformanceRegressionTests(unittest.TestCase):
    def test_literal_fast_path_keeps_original_unicode_and_boundary_semantics(self):
        terms = ["Java", "C++", "C#", "SQL", "AI/ML", "_", "+", "a a", "  Python  ",
                 "İstanbul", "istanbul", "ıstanbul", "Straße", "STRASSE", "σ", "Σ", "ς", "K", "ſ", ""]
        texts = ["JavaScript Java", "C++11 C++", "C#Developer C#", "PostgreSQL SQL", "AI/ML Engineer",
                 "_Java Java_ Java2 2Java Java", "xa a a!", "İSTANBUL ıstanbul", "Straße STRASSE",
                 "Σ σ ς", "K K k", "ſ S s", "Python² Python\u0301", "a_a + a+"]
        rng = random.Random(1234)
        alphabet = " a_+012JavaSQLßẞİıſKΣσς\u0301²"
        texts.extend("".join(rng.choices(alphabet, k=80)) for _ in range(150))
        for text in texts:
            for term in terms:
                expected = bool(term.strip()) and re.search(
                    r"(?<!\w)" + re.escape(term.strip()) + r"(?!\w)", text, re.I) is not None
                with self.subTest(text=text, term=term):
                    self.assertEqual(contains_phrase(text, term), expected)
                    self.assertEqual(contains_phrase(text, term, folded_text=_search_hint(text)), expected)

    def test_clause_fast_path_keeps_decimal_degree_html_and_unicode_boundaries(self):
        def reference(value):
            value = re.sub(r"</?(?:p|li|div|br|h[1-6])\b[^>]*>", "\n", value, flags=re.I)
            value = re.sub(r"<[^>]*>", " ", value)
            value = unicodedata.normalize("NFKC", html.unescape(value))[:100000]
            value = re.sub(r"\b(?:Ph\.D|B\.S|M\.S|B\.Sc|M\.Sc)\.?",
                           lambda match: match.group().replace(".", "\x00"), value, flags=re.I)
            return [" ".join(part.replace("\x00", ".").split()).strip(" •*-")
                    for part in re.split(r"[\r\n;]+|(?<!\d)[.!?](?!\d)", value) if part.strip()]

        samples = ["2.5 years required. B.S. plus 3 years or M.S. plus 1 year! Check Ph.D. routes?",
                   "Requirements\r\n• ٣.٥ years required;;\nPreferred\n５ years with Python.",
                   "<h2>Requirements</h2><p>2.5 years</p><br><li>Research &amp; development</li>",
                   "\n • \n---;;!!?A!2?3.A.3! A\x00B", ""]
        rng = random.Random(789)
        alphabet = " .!?;\r\na1\u0662\u00b2-•*"
        samples.extend("".join(rng.choices(alphabet, k=200)) for _ in range(100))
        for text in samples:
            with self.subTest(text=text):
                self.assertEqual(_clauses(text), reference(text))

    def test_unit_prefilter_retains_multilingual_and_entity_encoded_quantities(self):
        for text in ("3 years of experience required", "3 yrs experience required",
                     "3 &#121;ears of experience required", "3 YRS EXPERIENCE REQUIRED",
                     "Minst 3 års erfaring", "3 Jahre Berufserfahrung erforderlich",
                     "3 JÄHRİGE Erfahrung erforderlich", "Досвід роботи від 3 років",
                     "Досвід роботи від 3 роки", "Досвід роботи від 1 рік"):
            with self.subTest(text=text):
                self.assertTrue(experience_requirements(text))
        self.assertEqual(experience_requirements("No prior experience is required.")[0]["years"], 0)

    def test_repeated_matching_uses_current_profile_and_posting_not_stale_results(self):
        job = {"title": "Data Scientist", "location": "USA", "description": "2 years of Python experience required."}
        profile = {"target_roles": ["Data Scientist"], "skills": ["Python"], "years_of_experience": 2.5}
        first = match_job(job, profile)
        self.assertEqual(first["experience"]["status"], "no_conflict")
        job["description"] = "5 years of SQL experience required."
        second = match_job(job, profile)
        self.assertEqual(second["experience"]["status"], "mismatch")
        self.assertEqual(second["matched_skills"], [])
        profile.update(years_of_experience=5, skills=["SQL"], target_roles=["Data Engineer"])
        third = match_job(job, profile)
        self.assertEqual(third["experience"]["status"], "no_conflict")
        self.assertEqual(third["matched_skills"], ["SQL"])
        self.assertEqual(third["track"], "other")

    def test_recommendation_gate_preserves_academic_alias_and_country_evidence(self):
        profile = {
            "target_roles": ["Machine Learning Engineer", "artificial intelligence"],
            "related_roles": ["Research Assistant"], "skills": ["Python", "C++"],
            "years_of_experience": 2.5, "excluded_keywords": ["unpaid"],
            "country_preferences": {"DE": {"target_roles": ["Data Scientist"]}},
        }
        cases = [
            ({"title": "Software Engineer, Machine Learning", "description": "2 years of Python experience required."}, "target"),
            ({"title": "AI/ML Engineer", "description": "C++ unpaid", "deadline": "2000-01-01"}, "target"),
            ({"title": "PhD candidate", "description": "Research in mathematics"}, "horizon"),
            ({"title": "PhD candidate", "description": "Research in ancient history"}, "other"),
            ({"title": "Research Assistant", "description": "Computational statistics"}, "horizon"),
            ({"title": "Research Assistant", "description": "Ancient history"}, "other"),
            ({"title": "Artificial Intelligence Sales Director", "description": "Python"}, "other"),
            ({"title": "Data Scientist", "countries": ["DE"], "description": "Mindestens 3 Jahre Berufserfahrung."}, "target"),
            # Germany targets Data Scientist only: machine learning is a related
            # field there, never a target role.
            ({"title": "Machine Learning Engineer", "countries": ["DE"]}, "horizon"),
            ({"title": "Machine Learning Engineer", "countries": ["DE", "US"]}, "target"),
        ]
        for job, expected_track in cases:
            with self.subTest(job=job):
                full = match_job(job, profile, today=date(2026, 10, 14))
                recommendation = match_job(job, profile, today=date(2026, 10, 14), recommendations_only=True)
                self.assertEqual(full["track"], expected_track)
                self.assertEqual(recommendation, None if expected_track == "other" else full)

    def test_unrelated_titles_skip_expensive_experience_extraction(self):
        with patch("norway_job_agent.experience_matching.assess_experience") as assess:
            result = match_job({"title": "Account Executive", "description": "Python " * 10000},
                               {"target_roles": ["Data Scientist"]}, recommendations_only=True)
        self.assertIsNone(result)
        assess.assert_not_called()


if __name__ == "__main__":
    unittest.main()
