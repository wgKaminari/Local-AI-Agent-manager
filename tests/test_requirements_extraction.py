"""Experience and language requirements read from adverts and job-site data."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import job_index, market, service, sources
from norway_job_agent.experience_matching import _extract_experience_requirements, assess_experience
from norway_job_agent.language_requirements import combine, compare_languages, language_requirements, site_languages
from norway_job_agent.profile import assess_languages
from norway_job_agent.storage import JobStore


def years(text):
    return [(item["years"], item["kind"]) for item in _extract_experience_requirements(text)]


def languages(text):
    found = language_requirements(text)
    return ([(item["language"], item["level"]) for item in found["required"]],
            [(item["language"], item["level"]) for item in found["preferred"]])


class ExperienceWordingTests(unittest.TestCase):
    def test_years_followed_by_the_field_are_requirements(self):
        # Real adverts that stated years the earlier rules missed.
        cases = {
            "• 5+ years of UI test automation": 5,
            "Minimum 3+ years in project management": 3,
            "8+ yrs AppSec with genuine offensive depth": 8,
            "3+ years as a Business Analyst": 3,
            "You have 4+ years in ML engineering": 4,
            "5-9 years in strategic account management": 5,
            "Досвід роботи Creatio Developer від 1 року.": 1,
            "Досвід роботи у сфері malware analysis або offensive security від 3 років.": 3,
            "4+ роки на позиції Project Manager": 4,
            "TypeScript, Nest.JS від 5+ років": 5,
            "5+ Jahre E-Mail-Marketing oder CRM": 5,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(years(text), [(expected, "required")])

    def test_a_narrowing_second_number_keeps_the_first_required(self):
        self.assertEqual(years("5+ років досвіду роботи з даними (BI, Data Engineering або суміжні напрями), з яких "
                               "щонайменше 1,5 року — у ролі Data Lead"), [(5, "required"), (1.5, "required")])
        self.assertEqual(years("4+ years of commercial backend development, including 3+ years with Go"),
                         [(4, "required"), (3, "required")])

    def test_or_between_quantities_makes_alternatives(self):
        self.assertEqual(years("Від 1 року досвіду в SecOps, DevSecOps або 3 роки системний адміністратор"),
                         [(1, "alternative"), (3, "alternative")])

    def test_a_preference_aside_does_not_soften_the_requirement(self):
        self.assertEqual(years("Досвід роботи на позиції Product Manager від 3+ років (перевагою буде досвід в E-commerce, Travel)"),
                         [(3, "required")])
        self.assertEqual(years("min 5 Jahre Erfahrung in der Leitung komplexer IT-Projekte – idealerweise mit mehreren Partnern"),
                         [(5, "required")])

    def test_company_age_in_ukrainian_and_english_lists(self):
        for text in ("За 7+ років наш продукт виріс у платформу", "Over 8+ years, we've delivered 100+ CRM projects",
                     "4 роки на ринку — стабільна прибуткова компанія"):
            with self.subTest(text=text):
                self.assertEqual(years(text), [])

    def test_run_on_lists_keep_their_requirement(self):
        # A career page that joined its list items with non-breaking spaces.
        flat = ("Hvem er du? Du har minimum 3–5 års erfaring innen Data Engineering, dataplattform eller datavarehus "
                "Du har erfaring med Microsoft Fabric, Databricks, Snowflake eller tilsvarende plattformer Du har bygget "
                "og videreutviklet datapipelines og datamodeller i skyplattformene Du er komfortabel med SQL og Python "
                "Du forstår hvordan data skal struktureres, transformeres og tilgjengeliggjøres for analyse og innsikt")
        self.assertEqual(years(flat), [(3, "required")])
        self.assertEqual(sources._plain("datavarehus&nbsp; Du har erfaring med Snowflake&nbsp; Du er komfortabel med SQL"),
                         "datavarehus\nDu har erfaring med Snowflake\nDu er komfortabel med SQL")
        self.assertEqual(sources._plain("3&nbsp;years of Python"), "3 years of Python")

    def test_or_routes(self):
        tracks = ("Advanced degree and 4+ years of experience working in X\nor bachelor's degree and 6+ years of experience "
                  "working in X\nor 10+ years of experience working in X")
        self.assertEqual(years(tracks), [(4, "alternative"), (6, "alternative"), (10, "alternative")])
        # Degree tracks only: the Bachelor track is what a Bachelor's holder must meet.
        degrees = "A minimum of 8 years of related experience with a Bachelor's degree\nor 5 years with a Master's degree"
        self.assertEqual(max(y for y, kind in years(degrees) if kind == "required"), 8)
        self.assertEqual(years("Bachelor's degree in Computer Science and 3+ years of experience\nOR 5+ years of hands-on "
                               "experience designing applications in lieu of a degree"), [(3, "required"), (5, "alternative")])
        self.assertEqual(years("7+ years of experience or education in operations research"), [(7, "alternative")])

    def test_ideally_marks_years_as_preferred(self):
        self.assertEqual(years("Mehrjährige Erfahrung als Data Engineer (idealerweise 4+ Jahre)"), [(4, "preferred")])

    def test_company_contract_benefit_and_age_years_stay_out(self):
        for text in ("Ми понад 11 років розробляємо цифрові сервіси.", "For over 30 years we've been making games",
                     "2 роки тому ми почали будувати платформу", "The employment period is 3 years.",
                     "15 days for 0-1 years of employment", "18+ years old, HS diploma",
                     "will remain enrolled for at least another 1.5 years.", "Seit über 20 Jahren fokussieren wir uns",
                     "an automatic annual progression of 3% for a maximum of 4 years"):
            with self.subTest(text=text):
                self.assertEqual(years(text), [])

    def test_experience_without_a_number_is_kept_for_review(self):
        for text in ("Du har flere års erfaring med dataanalyse", "Mehrjährige Berufserfahrung in der Softwareentwicklung",
                     "Several years of experience with Python"):
            with self.subTest(text=text):
                self.assertEqual(years(text), [(None, "unspecified")])
        result = assess_experience({"title": "Data Engineer", "description": "Du har flere års erfaring med Python."}, 2.5)
        self.assertIsNone(result["minimum_years"])
        self.assertTrue(any("without a number" in reason for reason in result["reasons"]))

    def test_site_stated_experience_sets_the_minimum(self):
        job = {"title": "Data Scientist", "description": "Join our team.",
               "raw_json": json.dumps({"site_requirements": {"experience_years": 2.0, "source": "Djinni"}})}
        result = assess_experience(job, 1.5)
        self.assertEqual(result["minimum_years"], 2.0)
        self.assertEqual(result["status"], "stretch")
        self.assertIn("Djinni lists: 2 years", result["requirements"][0]["evidence"])


class LanguageTests(unittest.TestCase):
    def test_required_and_preferred_in_four_languages(self):
        cases = {
            "Fluent English required, German is a plus.": ([("English", "fluent")], [("German", "")]),
            "Requirements:\n• Excellent English, written and spoken\n• Norwegian is an advantage":
                ([("English", "very good")], [("Norwegian", "")]),
            "Du snakker og skriver norsk flytende. Gode engelskkunnskaper er en fordel.":
                ([("Norwegian", "fluent")], [("English", "good")]),
            "Sehr gute Deutsch- und Englischkenntnisse in Wort und Schrift":
                ([("German", "very good"), ("English", "very good")], []),
            "Deutschkenntnisse (C1) erforderlich, Englisch von Vorteil.": ([("German", "C1")], [("English", "")]),
            "Англійська мова на рівні Upper-Intermediate. Знання німецької буде перевагою.":
                ([("English", "upper-intermediate")], [("German", "")]),
            "Рівень англійської — B2+": ([("English", "B2")], []),
            "English B2+ (C1 preferred) — enough to facilitate a workshop": ([("English", "B2")], []),
            "— Upper-Intermediate level of spoken and written English": ([("English", "upper-intermediate")], []),
            "Polish your skills in Python. Polish language (B2) is a must.": ([("Polish", "B2")], []),
            "Gute Deutschkenntnisse sind sehr wünschenswert": ([], [("German", "good")]),
            "Language Proficiency: Strictly C1-C2 English level required": ([("English", "C1")], []),
            "Знання англійської не нижче B2": ([("English", "B2")], []),
            "Applicants who do not master a Scandinavian language and are not native English speakers must provide "
            "evidence of good written and oral proficiency in English": ([("English", "good")], []),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(languages(text), expected)

    def test_or_lists_are_one_choice(self):
        self.assertEqual(languages("Fluency in Norwegian or English"), ([("Norwegian or English", "fluent")], []))
        self.assertEqual(languages("Nice to have:\n- Swedish or Danish language skills"), ([], [("Swedish or Danish", "")]))

    def test_mentions_that_are_not_requirements(self):
        for text in ("We work with Deutsche Bahn and our Norwegian customers in the Ukrainian market.",
                     "Please send your application in English.", "We offer:\nFree German language courses",
                     "Our product is available in English and German.", "Find more English Speaking Jobs in Germany on Arbeitnow",
                     "a minimum of four years in the Norwegian educational system (level)",
                     "applicants must be a EU/Nato/German citizen, and be eligible for German Security Clearance",
                     "Experience with German automotive clients is a must", "We are a Norwegian company with offices in Oslo"):
            with self.subTest(text=text):
                self.assertEqual(languages(text), ([], []))

    def test_profile_levels_decide_meets_check_and_gap(self):
        stated = language_requirements("Fluent English and good Norwegian required. German C1. Swedish is a plus.")
        result = compare_languages(stated, {"English": "C1", "Norwegian": "A1-A2", "Ukrainian": "Native"})
        status = {item["language"]: item["status"] for item in result["required"] + result["preferred"]}
        self.assertEqual(status, {"English": "meets", "Norwegian": "gap", "German": "gap", "Swedish": "gap"})
        self.assertEqual([gap["language"] for gap in result["gaps"]], ["Norwegian", "German"])  # Only required ones.
        # A range meets what its low end reaches and needs a check where only its top does.
        self.assertEqual(compare_languages(language_requirements("English C1 required"), {"English": "B2-C1"})["required"][0]["status"], "check")
        self.assertEqual(compare_languages(language_requirements("Skandinavisk språk er et krav"), {"Norwegian": "C1"})["required"][0]["status"], "meets")

    def test_site_listed_languages_come_first(self):
        listed = site_languages([{"language": "English", "level": "B1 - Intermediate", "evidence": "Djinni lists: English B1"}])
        merged = combine(language_requirements("Fluent English is a plus."), listed)
        self.assertEqual([(item["language"], item["cefr"]) for item in merged["required"]], [("English", "B1")])
        self.assertEqual(merged["preferred"], [])

    def test_matches_carry_languages_and_warn_about_gaps(self):
        job = {"title": "Data Scientist", "company": "Fixture AS", "description": "Du snakker norsk flytende.",
               "location": "Oslo"}
        result = assess_languages(job, {"English": "C1", "Norwegian": "A2"})
        self.assertEqual(result["gaps"][0]["language"], "Norwegian")
        from norway_job_agent.profile import match_job
        match = match_job(job, {"target_roles": ["Data Scientist"], "languages": {"Norwegian": "A2"}})
        self.assertEqual(match["languages"]["gaps"][0]["level"], "fluent")
        self.assertTrue(any(warning.startswith("Requires Norwegian (fluent)") for warning in match["warnings"]))


class SiteDataTests(unittest.TestCase):
    DJINNI_PAGE = ("<html><body><script type='application/ld+json'>{}</script><div>Send application</div>"
                   "<ul><li>Only from 3 years of experience</li><li>Full Remote</li><li>English B2 - Upper Intermediate</li>"
                   "<li>Ukrainian Native</li></ul><a>Apply for the job</a><p>English C1 - Advanced in a footer</p></body></html>")

    def test_djinni_summary_gives_experience_and_languages(self):
        facts = sources._djinni_facts(self.DJINNI_PAGE)
        self.assertEqual(facts["experience_years"], 3.0)
        self.assertEqual([(item["language"], item["level"]) for item in facts["languages"]],
                         [("English", "B2 - Upper Intermediate"), ("Ukrainian", "Native")])

    def test_job_posting_months_of_experience(self):
        self.assertEqual(sources._experience_years({"@type": "OccupationalExperienceRequirements", "monthsOfExperience": 30}), 2.5)
        self.assertIsNone(sources._experience_years("3 years"))

    def test_level_filtered_feeds_state_experience(self):
        self.assertEqual(market._site_level("Djinni", "2y")["experience_years"], 2.0)
        self.assertEqual(market._site_level("Djinni", "no_exp")["experience_years"], 0.0)
        self.assertEqual(market._site_level("DOU", "1-3")["experience_years"], 1.0)
        self.assertEqual(market._site_level("DOU", "5plus")["experience_label"], "5+ years of experience")
        self.assertIsNone(market._site_level("Djinni", None))

    def test_site_facts_survive_later_feeds_and_refresh_the_match(self):
        with tempfile.TemporaryDirectory() as folder, JobStore(Path(folder) / "jobs.db") as store:
            job = {"title": "Python Developer", "company": "Fixture", "source": "djinni", "source_id": "1",
                   "source_url": "https://djinni.co/jobs/1/", "description": "Build services.",
                   "raw_json": {"site_requirements": {"experience_years": 2.0, "source": "Djinni",
                                                      "languages": [{"language": "English", "level": "B1"}]}}}
            job_id, _ = store.upsert_job(job)
            profile = {"target_roles": ["Python Developer"], "years_of_experience": 3}
            job_index.ensure_matches(store, profile)
            # The newest-jobs feed knows no level: the stored facts stay.
            store.upsert_job({**job, "raw_json": {"matched_queries": []}})
            raw = json.loads(store.get_job(job_id)["raw_json"])
            self.assertEqual(raw["site_requirements"]["experience_years"], 2.0)
            self.assertEqual(raw["matched_queries"], [])
            self.assertEqual(store._connection.execute("SELECT COUNT(*) FROM job_matches").fetchone()[0], 1)
            # A level feed adds a level and keeps the page's languages; the match is recomputed.
            store.upsert_job({**job, "raw_json": {"site_requirements": {"experience_years": 5.0, "source": "Djinni"}}})
            raw = json.loads(store.get_job(job_id)["raw_json"])
            self.assertEqual(raw["site_requirements"]["experience_years"], 5.0)
            self.assertEqual(raw["site_requirements"]["languages"][0]["language"], "English")
            self.assertEqual(store._connection.execute("SELECT COUNT(*) FROM job_matches").fetchone()[0], 0)


class BackfillTests(unittest.TestCase):
    def test_refresh_loads_missing_adverts_for_your_roles_only(self):
        with tempfile.TemporaryDirectory() as folder:
            data = Path(folder)
            profile = {"target_roles": ["Data Scientist"], "related_roles": []}
            with JobStore(data / "vacancies.db") as store:
                for number, title in enumerate(("Data Scientist (m/w/d)", "Koch (m/w/d)", "Data Scientist Berlin")):
                    store.upsert_job({"title": title, "company": "Fixture GmbH", "source": "arbeitsagentur",
                                      "source_id": f"ba-{number}", "source_url": f"https://www.arbeitsagentur.de/jobsuche/jobdetail/ba-{number}",
                                      "description": "", "location": "Berlin, Deutschland"})
                job_index.ensure_matches(store, profile)
                details = {"description": "Sie haben 3+ Jahre Erfahrung. Sehr gute Deutschkenntnisse (C1)."}
                settings = {"market": {"enabled": {"arbeitsagentur": True}}}
                with patch.object(market, "fetch_details", return_value=details) as fetch, patch.object(market, "REQUEST_PAUSE", 0):
                    loaded = service._backfill_details(data, store, settings)
                self.assertEqual(loaded, 2)
                self.assertEqual(sorted(call.args[0]["title"] for call in fetch.call_args_list),
                                 ["Data Scientist (m/w/d)", "Data Scientist Berlin"])
                texts = {row[0]: row[1] for row in store._connection.execute("SELECT title, description FROM jobs")}
                self.assertEqual(texts["Koch (m/w/d)"], "")
                self.assertIn("3+ Jahre", texts["Data Scientist Berlin"])
                # A site you switched off is never contacted.
                with patch.object(market, "fetch_details") as fetch:
                    self.assertEqual(service._backfill_details(data, store, {"market": {"enabled": {}}}), 0)
                fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
