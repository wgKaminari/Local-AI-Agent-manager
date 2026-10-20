"""The IT-job check used by "IT jobs only"."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent.it_jobs import is_it

IT_TITLES = [
    "Software Engineer", "Softwareentwickler C# / .NET Backend (m/w/d)", "Senior .NET-utvikler", "Python розробник",
    "Data Scientist (m/w/d)", "IT-Leiter (m/w/d)", "Head of IT", "IT-Support Tekniker", "Fachinformatiker Systemintegration (m/w/d)",
    "DevOps Engineer", "Senior SAP Consultant Finance (m/w/d)", "Bli med og utvikle fremtidens KI-løsninger",
    "Machine Learning Engineer II", "Staff Frontend Engineer", "QA Automation Engineer", "UX/UI Designer", "Scrum Master",
    "Cloud Engineer (m/w/d)", "Member of Technical Staff - Systems", "Системний адміністратор", "Azure-plattformingeniører",
    "Dynamics 365 konsulent", "Salesforce-utvikler/arkitekt", "Netzwerkadministrator (m/w/d)", "Cyber Security Analyst",
    "Datenbankentwickler (m/w/d)", "Systemutvikler", "Junior AI Developer", "Data Engineer - AI Pipelines & DataOps",
]
NOT_IT_TITLES = [
    "Projektentwickler Immobilien (m/w/d)", "Business Developer", "VIL DU HA EN TØMRERJOBB DER DU TRIVES, UTVIKLER DEG – OG BLIR SETT?",
    "CNC-Programmierer (m/w/d)", "Security Officer", "Bauingenieur (m/w/d)", "Sykepleier", "Account Executive", "Audit Consultant",
    "Make it happen: Sales Manager", "Environmental Engineer", "Lagerhelfer", "Produktdesigner Maschinenbau (m/w/d)",
    "Italian speaking customer agent", "Vertriebsingenieur (m/w/d)", "Barnehagelærer", "Senior Commercial Counsel",
]


class ItJobTests(unittest.TestCase):
    def test_clear_titles_in_four_languages(self):
        for title in IT_TITLES:
            with self.subTest(title=title):
                self.assertTrue(is_it({"title": title}))
        for title in NOT_IT_TITLES:
            with self.subTest(title=title):
                self.assertFalse(is_it({"title": title}))

    def test_a_vague_title_needs_three_it_technologies_in_its_advert(self):
        job = {"title": "Senior Systems Engineer"}
        self.assertTrue(is_it({**job, "description": "We run Python services on Kubernetes in AWS with Terraform."}))
        self.assertFalse(is_it({**job, "description": "Mechanical systems design with CAD software and Python scripts."}))
        self.assertFalse(is_it({"title": "Controller", "description": "SAP, SQL, Power BI, Python, Excel"}))

    def test_an_official_category_decides_unless_the_title_clearly_says_otherwise(self):
        self.assertTrue(is_it({"title": "Mitarbeiter Anwendungsbetreuung", "raw_json": {"it_category": True}}))
        self.assertTrue(is_it({"title": "Java Developer", "raw_json": {"it_category": False}}))
        self.assertFalse(is_it({"title": "Tømrer, utvikler deg", "raw_json": {"it_category": False}}))
        self.assertFalse(is_it({"title": "Senior Systems Engineer", "raw_json": '{"it_category": false}',
                                "description": "Python, Kubernetes, AWS, Terraform"}))
        # The Bundesagentur's occupation name also counts as a title.
        self.assertTrue(is_it({"title": "Mitarbeiter (m/w/d)", "raw_json": {"occupation": "Fachinformatiker/in - Anwendungsentwicklung"}}))


if __name__ == "__main__":
    unittest.main()
