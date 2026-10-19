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
            path.write_text(json.dumps({"skills": 5}), encoding="utf-8")
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


PASTED_SKILLS = 'Mathematical Statistics, Probability Theory, Hypothesis Testing, Parametric Tests, Non-Parametric Tests, Linear Regression, Logistic Regression, Bayesian Analysis, PyMC, Stan, NumPyro, Causal Inference, Experimental Design, A/B Testing, Time Series Analysis, ARIMA, STL, ADF, KPSS, Stochastic Processes, Numerical Methods, Optimization, Machine Learning, Deep Learning, Natural Language Processing (NLP), Reinforcement Learning, PPO, A2C, SAC, TD3, Feature Engineering, Model Evaluation, Classification, Regression, Clustering, Gradient Boosting, scikit-learn, XGBoost, PyTorch, Stable-Baselines3, Gymnasium, Unity ML-Agents, Computer Vision, YOLO, Large Language Models (LLMs), AI Agents, Agentic Workflows, RAG, Embeddings, Vector Databases, Vector Search, Structured Outputs, Prompt Engineering, Context Engineering, Dify, AI Workflow Orchestration, AI Agent Architecture, Document Processing, Document Verification, Data Extraction, Data Validation Pipelines, REST APIs, API Integration, Custom Plugins, Microsoft Graph API, Microsoft SharePoint, Python, NumPy, pandas, statsmodels, Matplotlib, seaborn, xlwings, SQL, PostgreSQL, pgAdmin, R, JavaScript, VBA, Excel, Power BI, Alteryx, Pentaho PDI, Jupyter, Docker, Containerization, Linux, Git, GitHub, MLOps, CI/CD, Logging, Observability, AWS, AWS EC2, GCP, Azure, Data Analysis, Data Quality, Data Automation, Process Automation, Statistical Modelling, Predictive Modelling, Time Series Forecasting, Data Visualization, Business Intelligence'


class ListReadingTests(unittest.TestCase):
    def test_a_comma_separated_skill_paste_becomes_separate_skills(self):
        from norway_job_agent.profile import split_terms
        skills = split_terms(PASTED_SKILLS)
        self.assertEqual(len(skills), 106)
        self.assertEqual(skills[:3], ["Mathematical Statistics", "Probability Theory", "Hypothesis Testing"])
        self.assertIn("Natural Language Processing (NLP)", skills)
        self.assertIn("CI/CD", skills)
        self.assertEqual(skills[-1], "Business Intelligence")

    def test_profiles_saved_as_one_long_item_are_read_as_a_list(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.json"
            path.write_text(json.dumps({"skills": [PASTED_SKILLS], "target_roles": ["Data Scientist; ML Engineer"]}), encoding="utf-8")
            profile = load_profile(path)
        self.assertEqual(len(profile["skills"]), 106)
        self.assertEqual(profile["target_roles"], ["Data Scientist", "ML Engineer"])
        match = match_job({"title": "Data Scientist", "description": "We use PyMC, Docker and Power BI."}, profile)
        self.assertEqual(match["matched_skills"], ["PyMC", "Power BI", "Docker"])

    def test_every_list_format_is_read(self):
        from norway_job_agent.profile import parse_languages, split_sentences, split_terms
        self.assertEqual(split_terms("\u2022 Python\n\u2022 SQL (PostgreSQL, SQLite)\n- Docker | Git; Linux\n1. Excel.\nPython"),
                         ["Python", "SQL (PostgreSQL, SQLite)", "Docker", "Git", "Linux", "Excel"])
        self.assertEqual(split_sentences("\u2022 At Acme, built 10 agents, saving time.\n2. Cut costs by 90%, via automation"),
                         ["At Acme, built 10 agents, saving time.", "Cut costs by 90%, via automation"])
        self.assertEqual(parse_languages("English: B2-C1, German - A2; Norwegian (A1-A2)\nRussian Native"),
                         {"English": "B2-C1", "German": "A2", "Norwegian": "A1-A2", "Russian": "Native"})
        with self.assertRaisesRegex(ValueError, "Add a level"):
            parse_languages("English")


if __name__ == "__main__":
    unittest.main()
