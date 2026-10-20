"""Experience evidence, title screening and the reported recommendation failures."""
import copy
import json
import math
import unittest
from unittest.mock import patch

from norway_job_agent import application_answers, application_forms, local_ai
from norway_job_agent.experience_matching import assess_experience
from norway_job_agent.profile import match_job, preparation_brief, validate_profile



def setUpModule():
    # Drafting starts an installed Ollama; tests use mocked generation and
    # must never launch the real local AI server.
    patcher = patch("norway_job_agent.runtime_setup.ensure_ollama")
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)

class ExperienceProfileTests(unittest.TestCase):
    def test_explicit_fractional_zero_and_unknown_experience_round_trip(self):
        for years in (None, 0, 2.5, 60):
            with self.subTest(years=years):
                profile = validate_profile({"years_of_experience": years})
                self.assertEqual(validate_profile(json.loads(json.dumps(profile)))["years_of_experience"], years)

    def test_invalid_experience_is_rejected(self):
        for value in (True, False, "2.5", "", -0.1, 60.1, math.inf, -math.inf, math.nan, [], {}, 10 ** 1000):
            with self.subTest(value=repr(value)[:30]), self.assertRaisesRegex(ValueError, "Years of experience"):
                validate_profile({"years_of_experience": value})

    def test_existing_profiles_are_migrated_to_unknown_not_guessed(self):
        profile = validate_profile({"cv_text": "Engineer 2020–2026. Ten years of experience.", "summary": "Experienced developer"})
        self.assertIsNone(profile["years_of_experience"])

    def test_brief_includes_exact_recorded_total(self):
        brief = preparation_brief({"title": "ML Engineer"}, {"years_of_experience": 2.5})
        self.assertIn("2.5 years of total professional experience.", brief)


class RequirementTests(unittest.TestCase):
    def assess(self, text, title="Machine Learning Engineer", years=2.5):
        return assess_experience({"title": title, "description": text}, years)

    def test_clear_required_minimums_and_ranges(self):
        samples = {
            "At least 5 years of professional experience required.": 5,
            "Minimum five years of experience in machine learning.": 5,
            "3–5 years of experience building production systems.": 3,
            "2.5 years of professional experience required.": 2.5,
            "2,5 Jahre Berufserfahrung erforderlich.": 2.5,
            "Minst tre års erfaring med Python.": 3,
            "Досвід роботи від 3 років.": 3,
            "5+ years building ML systems.": 5,
            "5 or more years of experience required.": 5,
            "Five (5) years of experience required.": 5,
            "At least 5 (five) years of experience required.": 5,
            "Experience:\n5+ years": 5,
        }
        for text, minimum in samples.items():
            with self.subTest(text=text):
                result = self.assess(text)
                self.assertEqual(result["minimum_years"], minimum)
                self.assertEqual(result["status"], "mismatch" if minimum > 2.5 else "no_conflict")
                self.assertTrue(result["requirements"][0]["evidence"])

    def test_required_fields_use_strongest_floor_without_adding_overlapping_years(self):
        result = self.assess("2 years of Python experience. 3 years of SQL experience.")
        self.assertEqual(result["minimum_years"], 3)
        self.assertEqual(result["status"], "mismatch")

    def test_degree_after_experience_does_not_erase_a_real_requirement(self):
        result = self.assess("5 years of work experience and a Master degree required.")
        self.assertEqual(result["minimum_years"], 5)
        self.assertEqual(result["status"], "mismatch")

    def test_conflicting_written_and_numeric_years_remain_reviewable(self):
        result = self.assess("Five (2) years of experience required.")
        self.assertIsNone(result["minimum_years"])
        self.assertEqual(result["status"], "review")

    def test_preferred_experience_does_not_create_a_required_minimum(self):
        for text in ("5 years of experience preferred.", "Ideally 5 years of experience.",
                     "Five years of experience would be a plus.", "5 Jahre Erfahrung wünschenswert."):
            with self.subTest(text=text):
                result = self.assess(text)
                self.assertEqual(result["status"], "review")
                self.assertIsNone(result["minimum_years"])
                self.assertEqual(result["requirements"][0]["kind"], "preferred")

    def test_required_and_preferred_quantities_do_not_contaminate_each_other(self):
        result = self.assess("2 years of experience required, 5 years preferred.")
        self.assertEqual(result["minimum_years"], 2)
        self.assertNotEqual(result["status"], "mismatch")
        self.assertEqual([item["kind"] for item in result["requirements"]], ["required", "preferred"])

    def test_degree_alternatives_are_not_unconditional_exclusions(self):
        for text in ("Bachelor degree and 5 years of experience or Master degree and 2 years of experience.",
                     "B.S. and 5 years of experience or M.S. and 2 years of experience.",
                     "Bachelor degree plus 5 years of experience or a Master degree.",
                     "5 years of experience or equivalent qualifications required."):
            with self.subTest(text=text):
                result = self.assess(text)
                self.assertEqual(result["status"], "review")
                self.assertIsNone(result["minimum_years"])
                self.assertTrue(all(item["kind"] == "alternative" for item in result["requirements"]))

    def test_skill_alternatives_do_not_erase_a_real_minimum(self):
        result = self.assess("5 years of experience with Python or R required.")
        self.assertEqual(result["status"], "mismatch")
        self.assertEqual(result["minimum_years"], 5)

    def test_degree_options_with_the_same_experience_requirement_stay_required(self):
        for text in ("BS/MS and 5 years of experience required.", "Bachelor or Master degree and 5 years of experience required."):
            with self.subTest(text=text):
                result = self.assess(text)
                self.assertEqual(result["minimum_years"], 5)
                self.assertEqual(result["status"], "mismatch")

    def test_business_history_degree_duration_and_contract_are_not_experience(self):
        samples = (
            "Our company has 20 years of experience serving clients.",
            "We have 15 years of experience in machine learning.",
            "Our 20 years of experience make us a leader.",
            "Our company has 20 years of experience and we need an engineer.",
            "Our 20 years of experience means we need engineers.",
            "Founded 10 years ago. Salary 100000. A 4 year degree is required.",
            "A 5 year contract is available. Some experience is desirable.",
            "Experience delivering 5-year projects and roadmaps.",
        )
        for text in samples:
            with self.subTest(text=text):
                result = self.assess(text)
                self.assertEqual(result["requirements"], [])
                self.assertEqual(result["status"], "review")

    def test_company_history_does_not_erase_a_separate_candidate_requirement(self):
        result = self.assess("Our company has 20 years of experience and candidates must have 3 years of experience.")
        self.assertEqual(result["minimum_years"], 3)
        self.assertEqual(result["status"], "mismatch")

    def test_customer_facing_experience_is_a_real_requirement(self):
        result = self.assess("5+ years of experience working with customers.")
        self.assertEqual(result["minimum_years"], 5)
        self.assertEqual(result["status"], "mismatch")

    def test_html_bullets_preserve_preferred_and_required_boundaries(self):
        result = self.assess("<h2>Requirements</h2><ul><li>2 years of experience required</li></ul><h2>Preferred</h2><p>5 years of experience preferred</p>")
        self.assertEqual(result["minimum_years"], 2)
        self.assertEqual([item["kind"] for item in result["requirements"]], ["required", "preferred"])

    def test_preferred_heading_applies_to_its_bullets_until_required_heading(self):
        result = self.assess("<h2>Preferred qualifications</h2><ul><li>5 years of experience</li><li>3 years of Python experience</li></ul><h2>Requirements</h2><p>2 years of experience</p>")
        self.assertEqual(result["minimum_years"], 2)
        self.assertNotEqual(result["status"], "mismatch")
        self.assertEqual([item["kind"] for item in result["requirements"]], ["preferred", "preferred", "required"])

    def test_nonrequirements_and_upper_bounds_do_not_exclude(self):
        for text in ("5 years of experience is not required.", "Up to 5 years of experience.",
                     "A maximum of 5 years of experience."):
            with self.subTest(text=text):
                result = self.assess(text)
                self.assertIsNone(result["minimum_years"])
                self.assertEqual(result["status"], "review")

    def test_explicit_no_experience_required_is_zero(self):
        result = self.assess("No prior experience is required.", years=0)
        self.assertEqual(result["minimum_years"], 0)
        self.assertEqual(result["status"], "no_conflict")

    def test_unknown_experience_stays_review_and_is_never_filled_from_job(self):
        result = self.assess("5 years of experience required.", "Senior Engineer", None)
        self.assertIsNone(result["candidate_years"])
        self.assertEqual(result["minimum_years"], 5)
        self.assertEqual(result["status"], "review")

    def test_meeting_a_field_minimum_is_not_a_claim_of_field_experience(self):
        result = self.assess("2 years of machine learning experience required.")
        self.assertEqual(result["status"], "no_conflict")
        self.assertIn("Check the required field and skills separately", " ".join(result["reasons"]))


class ReportedRecommendationTests(unittest.TestCase):
    def test_reported_senior_staff_manager_jobs_are_flagged_for_two_and_half_years(self):
        titles = (
            "Senior Machine Learning Engineer II, Ads Response Prediction",
            "Senior Machine Learning Engineer",
            "Senior Machine Learning Engineer, Operations Research",
            "Senior Machine Learning Engineer, Economist",
            "Senior Data Scientist, User Growth",
            "Staff Data Scientist, Causal Inference & Experimentation",
            "Staff AI Engineer | US | Remote",
            "Staff AI Engineer - Grafana AI/ML | USA | Remote",
            "Senior Data Scientist, Growth Forecasting",
            "Senior Data Scientist, Causal Inference + Experimentation",
            "Senior Data Science Manager, Developer Platform",
            "Senior Data Scientist II - Ads Optimization",
            "Senior Data Scientist I - Meals",
            "Senior Data Engineer, Ads",
            "Staff AI Research Engineer",
            "Staff Data Engineer - Data Infrastructure",
            "Senior Applied Scientist II, Ads Optimization",
        )
        profile = {"years_of_experience": 2.5, "target_roles": ["Machine Learning Engineer", "Data Scientist", "AI Engineer"], "skills": ["Python"]}
        for title in titles:
            with self.subTest(title=title):
                result = match_job({"title": title, "description": "Python", "location": "United States"}, profile)
                self.assertEqual(result["experience"]["status"], "mismatch")
                self.assertIsNone(result["experience"]["minimum_years"])
                self.assertIn("not an employer-stated minimum", " ".join(result["experience"]["reasons"]))

    def test_title_mismatch_can_coexist_with_high_keyword_overlap(self):
        result = match_job({"title": "Senior ML Engineer", "description": "Python"},
                           {"years_of_experience": 2.5, "target_roles": ["Machine Learning Engineer"], "skills": ["Python"]})
        self.assertEqual(result["score"], 100)
        self.assertEqual(result["experience"]["status"], "mismatch")

    def test_principal_and_leadership_are_not_assumed_junior(self):
        for title in ("Principal Data Scientist", "Engineering Manager", "Head of Data", "Lead AI Engineer"):
            with self.subTest(title=title):
                self.assertEqual(assess_experience({"title": title}, 2.5)["status"], "mismatch")

    def test_absent_level_and_junior_titles_require_review_without_posted_minimum(self):
        for title in ("Data Scientist, Finance", "Data Scientist", "Data Scientist - Client Platform", "Junior ML Engineer", "ML Engineer II"):
            with self.subTest(title=title):
                self.assertEqual(assess_experience({"title": title}, 2.5)["status"], "review")

    def test_senior_audience_and_mixed_levels_do_not_create_false_title_exclusions(self):
        for title in ("Developer, Senior Living Platform", "Junior / Senior Data Scientist", "Member of Technical Staff"):
            with self.subTest(title=title):
                self.assertEqual(assess_experience({"title": title}, 2.5)["status"], "review")

    def test_explicit_employer_minimum_takes_precedence_over_title_guideline(self):
        result = assess_experience({"title": "Senior Data Scientist", "description": "2 years of experience required."}, 2.5)
        self.assertEqual(result["status"], "review")
        self.assertEqual(result["minimum_years"], 2)

    def test_equivalent_role_titles_are_recognized_without_matching_unrelated_disciplines(self):
        for title in ("ML Engineer", "Machine-Learning Engineer", "AI/ML Engineer", "Machine Learning Engineer II",
                      "AI & ML Engineer", "Software Engineer, Machine Learning", "Machine Learning Software Engineer"):
            with self.subTest(title=title):
                self.assertEqual(match_job({"title": title}, {"target_roles": ["Machine Learning Engineer"]})["track"], "target")
        for title in ("Data Analyst", "Data Engineer", "ML Engineering Manager"):
            with self.subTest(title=title):
                self.assertEqual(match_job({"title": title}, {"target_roles": ["Machine Learning Engineer"]})["track"], "other")

    def test_ai_engineer_aliases_cover_reversed_titles_and_stay_specific(self):
        for title in ("Software Engineer, AI", "Engineer: Artificial Intelligence", "AI & ML Engineer"):
            with self.subTest(title=title):
                self.assertEqual(match_job({"title": title}, {"target_roles": ["AI Engineer"]})["track"], "target")
        self.assertEqual(match_job({"title": "Software Engineer, Air Traffic Control"}, {"target_roles": ["AI Engineer"]})["track"], "other")

    def test_matching_never_mutates_the_saved_profile(self):
        profile = validate_profile({"years_of_experience": 2.5})
        original = copy.deepcopy(profile)
        match_job({"title": "Staff Data Scientist", "location": "Germany"}, profile)
        self.assertEqual(profile, original)


class ExperienceDraftTests(unittest.TestCase):
    def test_cover_prompt_and_evidence_preserve_exact_total(self):
        profile = {"summary": "Built Python services.", "years_of_experience": 2.5}
        draft = {"cover_letter": "I have 2.5 years of total professional experience and built Python services.",
                 "review_notes": [], "used_evidence": ["2.5 years of total professional experience."]}
        with patch.object(local_ai, "_chat", return_value=draft) as chat:
            local_ai.generate_cover_letter({"title": "Engineer", "description": "Develop Python software."}, profile)
        facts = chat.call_args.args[2]["candidate_facts"]
        self.assertEqual(facts["years_of_experience"], 2.5)
        self.assertEqual(facts["experience_summary"], "2.5 years of total professional experience.")
        self.assertIn("never round up", chat.call_args.args[1])

    def test_application_can_answer_experience_from_the_explicit_fact_alone(self):
        form = application_forms.parse_manual_questions("How many years of total professional experience do you have?")
        draft = {"field_id": form["fields"][0]["id"], "status": "draft", "answer": "2.5 years", "selected_options": [],
                 "used_evidence": ["2.5 years of total professional experience."], "review_notes": []}
        with patch.object(local_ai, "_chat", return_value={"answers": [draft]}) as chat:
            result = application_answers.generate_application_answers({}, {"years_of_experience": 2.5}, form)
        self.assertEqual(result["answers"][0]["answer"], "2.5 years")
        self.assertEqual(chat.call_args.args[2]["candidate_facts"]["years_of_experience"], 2.5)
        self.assertIn("never round up", chat.call_args.args[1])

    def test_unknown_total_is_not_derived_from_cv_or_required_years(self):
        facts = application_answers._candidate_facts({"cv_text": "Data Scientist 2020–2026."})
        self.assertIsNone(facts["years_of_experience"])
        self.assertEqual(facts["experience_summary"], "")


if __name__ == "__main__":
    unittest.main()
