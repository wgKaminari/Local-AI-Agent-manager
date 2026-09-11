import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent.profile import load_profile, match_job, preparation_brief


class ProfileTests(unittest.TestCase):
    def test_score_is_unavailable_without_preferences(self):
        self.assertIsNone(match_job({"title": "Engineer"}, {})["score"])

    def test_partial_words_do_not_create_false_skill_claims(self):
        result = match_job({"title": "Developer", "description": "JavaScript, C++ and Python"},
                           {"skills": ["Java", "C++", "Python"]})
        self.assertEqual(result["matched_skills"], ["C++", "Python"])
        self.assertEqual(result["score"], 67)

    def test_expired_and_excluded_vacancies_stay_visible_with_warnings(self):
        result = match_job({"title": "Unpaid trainee", "deadline": "2025-01-01"},
                           {"excluded_keywords": ["unpaid"]}, today=date(2026, 9, 9))
        self.assertTrue(result["expired"])
        self.assertEqual(result["excluded_terms"], ["unpaid"])

    def test_profile_rejects_wrong_types(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.json"
            path.write_text(json.dumps({"skills": "Python"}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "list of strings"):
                load_profile(path)

    def test_brief_does_not_invent_experience(self):
        brief = preparation_brief({"title": "Welder", "description": "Five years required"}, {})
        self.assertIn("No achievement examples supplied", brief)
        self.assertIn("not a generated cover letter", brief)

    def test_generic_research_titles_need_relevant_field_evidence(self):
        profile = {"related_roles": ["PhD"]}
        unrelated = match_job({"title": "PhD in Theatre", "description": "Dramatic arts"}, profile)
        related = match_job({"title": "PhD in Applied Mathematics", "description": "Statistical modelling"}, profile)
        self.assertEqual(unrelated["track"], "other")
        self.assertEqual(related["track"], "horizon")

    def test_ai_topic_does_not_classify_a_lawyer_as_an_ai_engineer(self):
        profile = {"target_roles": ["AI Engineer", "kunstig intelligens"]}
        result = match_job({"title": "Jurist innen kunstig intelligens"}, profile)
        self.assertEqual(result["track"], "other")
        self.assertEqual(result["score"], 0)


if __name__ == "__main__":
    unittest.main()
