"""Applications found in your mailbox, and AI reading of job alerts. No network."""

import base64
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_job_agent import cloud_ai, gmail, mail_applications as mail, service
from norway_job_agent.web_api import close_resources, create_app
from support import offline_collection


def gmail_message(message_id, subject, sender, body, *, date="Tue, 8 Sep 2026 10:00:00 +0200", sent=False, html=False, to="", headers=()):
    data = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    all_headers = [{"name": "From", "value": sender}, {"name": "Subject", "value": subject}, {"name": "Date", "value": date},
                   *([{"name": "To", "value": to}] if to else []), *({"name": name, "value": value} for name, value in headers)]
    return {"id": message_id, "threadId": "t" + message_id, "labelIds": ["SENT"] if sent else ["INBOX"],
            "payload": {"mimeType": "multipart/alternative", "headers": all_headers,
                        "parts": [{"mimeType": "text/html" if html else "text/plain", "body": {"data": data}}]}}


MAILBOX = {
    "m1": gmail_message("m1", "Thank you for applying to Nordic Fixture", "Nordic Fixture Recruiting <no-reply@greenhouse.io>",
                        "Hi, thank you for applying to the Python Developer position at Nordic Fixture. We will review your application.",
                        date="Mon, 7 Sep 2026 09:00:00 +0200"),
    "m2": gmail_message("m2", "Update on your application", "Talent Team <talent@nordicfixture.com>",
                        "Thank you for your interest. Unfortunately, we have decided to move forward with other candidates for the Python Developer role.",
                        date="Thu, 1 Oct 2026 09:00:00 +0200"),
    "m3": gmail_message("m3", "Interview invitation: Data Engineer", "Jane Smith <jane@berlinfixture.de>",
                        "We would like to invite you to an interview for the Data Engineer position. Please pick a time that suits you.",
                        date="Fri, 2 Oct 2026 09:00:00 +0200"),
    "m4": gmail_message("m4", "Vielen Dank für Ihre Bewerbung", "Kyiv Fixture <jobs@kyivfixture.ua>",
                        "Vielen Dank für Ihre Bewerbung als Backend Engineer bei Kyiv Fixture.", date="Wed, 12 Aug 2026 09:00:00 +0200"),
    "m5": gmail_message("m5", "5 interview mistakes", "Career News <news@careernews.example>", "Top interview tips for this week.",
                        headers=[("List-Unsubscribe", "<mailto:unsubscribe@careernews.example>")]),
    "m6": gmail_message("m6", "Application for Cloud Engineer", "Me <me@gmail.com>", "Dear team, please find my CV attached.",
                        sent=True, to="Kari Nordmann <kari@cloudcorp.no>", date="Sat, 3 Oct 2026 09:00:00 +0200"),
    "m7": gmail_message("m7", "Your application", "Hiring <no-reply@myworkday.com>", "Thank you for applying. We have received your application.",
                        date="Sun, 4 Oct 2026 09:00:00 +0200"),
}


def fake_gmail(data_dir, url):
    if "?q=" in url or url.endswith("messages") or "maxResults" in url:
        return {"messages": [{"id": message_id} for message_id in MAILBOX]}
    message_id = url.rsplit("/", 1)[1].split("?")[0]
    return MAILBOX[message_id]


class FakeEngine:
    is_cloud = True
    label = "fake-model (Fake)"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def generate(self, system, messages, **options):
        self.calls.append(json.loads(messages[0]["content"]))
        return {"text": json.dumps(self.replies.pop(0))}


class RulesTests(unittest.TestCase):
    def test_messages_become_application_records(self):
        records = {message_id: mail.read_message(message) for message_id, message in MAILBOX.items()}
        self.assertEqual((records["m1"]["kind"], records["m1"]["company"], records["m1"]["position"]),
                         ("applied", "Nordic Fixture", "Python Developer"))
        self.assertEqual((records["m2"]["kind"], records["m2"]["company"], records["m2"]["position"]),
                         ("rejected", "Nordicfixture", "Python Developer"))
        # A recruiter's own name is not the company: the domain is.
        self.assertEqual((records["m3"]["kind"], records["m3"]["company"]), ("interview", "Berlinfixture"))
        self.assertEqual((records["m4"]["kind"], records["m4"]["position"]), ("applied", "Backend Engineer"))
        self.assertIsNone(records["m5"])  # A newsletter about interviews.
        self.assertEqual((records["m6"]["kind"], records["m6"]["company"], records["m6"]["position"], records["m6"]["sent"]),
                         ("applied", "Cloudcorp", "Cloud Engineer", True))
        self.assertEqual((records["m7"]["company"], records["m7"]["position"]), ("", ""))  # The AI can read these.
        self.assertEqual(records["m1"]["received_at"][:10], "2026-09-07")
        self.assertNotIn("http", records["m1"]["excerpt"])

    def test_wording_in_four_languages(self):
        cases = [
            ("Søknad mottatt", "Takk for din søknad på stillingen som Systemutvikler hos Statens vegvesen.", "applied", ("Statens vegvesen", "Systemutvikler")),
            ("Ваш відгук", "Дякуємо за відгук на вакансію Python Developer.", "applied", ("", "Python Developer")),
            ("Absage", "Leider müssen wir Ihnen mitteilen, dass wir uns für andere Bewerber entschieden haben.", "rejected", None),
            ("Svar på søknad", "Dessverre har vi valgt å gå videre med andre kandidater.", "rejected", None),
            ("Співбесіда", "Запрошуємо вас на співбесіду щодо вакансії QA Engineer.", "interview", None),
            ("Your application", "Thank you for applying. Unfortunately, due to high volume we cannot reply to everyone individually.", "applied", None),
        ]
        for subject, body, kind, extracted in cases:
            with self.subTest(subject=subject):
                self.assertEqual(mail.classify(subject, body), kind)
                if extracted:
                    self.assertEqual(mail.extract(subject, "Webcruiter <noreply@webcruiter.no>", body), extracted)

    def test_linking_needs_the_same_employer_and_position(self):
        jobs = [{"id": 1, "title": "Python Developer (m/w/d)", "company": "Nordic Fixture AS", "status": "new"},
                {"id": 2, "title": "Office Manager", "company": "Nordic Fixture AS", "status": "new"},
                {"id": 3, "title": "Python Developer", "company": "Other Company", "status": "new"}]
        records = [{"message_id": "a", "company": "Nordic Fixture", "position": "Python Developer"},
                   {"message_id": "b", "company": "Nordic Fixture", "position": "Chief of Staff"},
                   {"message_id": "c", "company": "Unknown Ltd", "position": "Python Developer"},
                   {"message_id": "d", "company": "", "position": "Python Developer"}]
        self.assertEqual(mail.link(records, jobs), {"a": (1, "position"), "b": (None, "company"), "c": (None, ""), "d": (None, "")})

    def test_messages_group_into_applications_with_the_latest_outcome(self):
        records = [{"message_id": "1", "kind": "applied", "company": "Acme", "position": "", "received_at": "2026-09-01", "sender": ""},
                   {"message_id": "2", "kind": "rejected", "company": "Acme AS", "position": "Data Engineer", "received_at": "2026-09-20", "sender": ""},
                   {"message_id": "3", "kind": "applied", "company": "Beta", "position": "QA Engineer", "received_at": "2026-10-02", "sender": ""},
                   {"message_id": "4", "kind": "interview", "company": "Beta", "position": "", "received_at": "2026-10-05", "sender": ""},
                   {"message_id": "5", "kind": "applied", "company": "Gamma", "position": "SRE", "received_at": "2026-10-06", "sender": ""}]
        summary = mail.summary(records)
        self.assertEqual((summary["applications"], summary["waiting"], summary["interview"], summary["rejected"]), (3, 1, 1, 1))
        self.assertEqual(summary["response_rate"], 67)
        acme = next(group for group in summary["groups"] if group["company"].startswith("Acme"))
        self.assertEqual((acme["position"], acme["outcome"], acme["messages"], acme["applied_at"]), ("Data Engineer", "rejected", 2, "2026-09-01"))
        self.assertEqual(summary["months"], [{"month": "2026-09", "count": 1}, {"month": "2026-10", "count": 2}])

    def test_stage_suggestions_only_move_forward(self):
        self.assertEqual(mail.stage_suggestion("new", "applied"), "applied")
        self.assertEqual(mail.stage_suggestion("applied", "rejected"), "rejected")
        self.assertIsNone(mail.stage_suggestion("interview", "applied"))
        self.assertIsNone(mail.stage_suggestion("archived", "interview"))
        self.assertIsNone(mail.stage_suggestion("rejected", "rejected"))

    def test_the_search_finds_application_mail_only(self):
        query = mail.scan_query(180)
        self.assertTrue(query.startswith("newer_than:180d ("))
        self.assertIn("from:greenhouse.io", query)
        self.assertIn("subject:søknad", query)
        with self.assertRaises(ValueError):
            mail.scan_query(5000)

    def test_ai_reading_keeps_only_names_found_in_the_message(self):
        records = [{"message_id": "x", "kind": "applied", "company": "", "position": "", "subject": "Your application",
                    "sender": "Hiring <no-reply@myworkday.com>", "excerpt": "Thank you for applying for Site Reliability Engineer at Contoso."},
                   {"message_id": "y", "kind": "applied", "company": "", "position": "", "subject": "Weekly digest",
                    "sender": "news@x.example", "excerpt": "Ten jobs you may like."}]
        engine = FakeEngine({"emails": [{"id": "0", "kind": "applied", "company": "Contoso", "position": "Site Reliability Engineer"},
                                        {"id": "1", "kind": "other", "company": "Fabrikam", "position": "CEO"}]})
        changes = mail.ai_read(records, engine)
        self.assertEqual(changes["x"], {"kind": "applied", "company": "Contoso", "position": "Site Reliability Engineer"})
        self.assertEqual(changes["y"], {"kind": "other", "company": "", "position": ""})  # Not in the email: dropped.
        self.assertIn("Contoso", engine.calls[0]["emails"][0]["excerpt"])


class MailServiceTests(unittest.TestCase):
    def setUp(self):
        offline_collection()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        service.initialize(self.data)
        self.python = service.import_vacancy(self.data, {"title": "Python Developer", "company": "Nordic Fixture AS", "location": "Oslo",
                                                         "description": "Python.", "source": "manual", "source_url": "https://jobs.example.test/1"})[0]
        self.other = service.import_vacancy(self.data, {"title": "Office Manager", "company": "Nordic Fixture AS", "location": "Oslo",
                                                        "description": "Office.", "source": "manual", "source_url": "https://jobs.example.test/2"})[0]
        reader = patch.object(gmail, "_gmail_get", side_effect=fake_gmail)
        reader.start()
        self.addCleanup(reader.stop)

    def test_a_scan_counts_applications_and_suggests_stages(self):
        result = service.scan_mail(self.data, days=365)
        self.assertEqual(result["scan"]["found"], 6)
        self.assertEqual(result["messages"], 6)
        self.assertEqual(result["applications"], 5)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(result["suggestions"], [{"job_id": self.python, "title": "Python Developer", "company": "Nordic Fixture AS",
                                                  "current": "new", "suggested": "rejected", "date": "2026-10-01T07:00:00+00:00"}])
        # A second scan reads only new mail.
        with patch.object(mail, "read_message", side_effect=AssertionError("already read")):
            self.assertEqual(service.scan_mail(self.data)["scan"]["found"], 0)
        # Lists and the vacancy page show that you applied.
        page = service.list_page(self.data, view="discover")["jobs"]
        marked = {job["id"]: job.get("email") for job in page}
        self.assertEqual(marked[self.python]["kind"], "rejected")
        self.assertEqual(marked[self.python]["applied_at"][:10], "2026-09-07")
        self.assertIsNone(marked[self.other])
        history = service.mail_history(self.data, service.get_vacancy(self.data, self.other))
        self.assertTrue(history and not any(item["this_job"] for item in history))  # Same employer, other job.
        applied = service.apply_mail_stages(self.data, [{"job_id": self.python, "status": "rejected"}])
        self.assertEqual(applied["updated"], 1)
        self.assertEqual(service.get_vacancy(self.data, self.python)["status"], "rejected")
        self.assertEqual(applied["suggestions"], [])
        with self.assertRaisesRegex(ValueError, "no longer suggests"):
            service.apply_mail_stages(self.data, [{"job_id": self.other, "status": "applied"}])

    def test_applications_found_only_in_mail_can_be_added(self):
        overview = service.scan_mail(self.data)
        cloud = next(group for group in overview["groups"] if group["position"] == "Cloud Engineer")
        result = service.add_mail_application(self.data, cloud["message_ids"][0])
        job = service.get_vacancy(self.data, result["id"])
        self.assertEqual((job["title"], job["company"], job["status"], job["source"]), ("Cloud Engineer", "Cloudcorp", "applied", "gmail"))
        self.assertTrue(job["source_url"].startswith("https://mail.google.com/"))
        added = next(group for group in result["groups"] if group["position"] == "Cloud Engineer")
        self.assertEqual(added["job_id"], result["id"])
        self.assertEqual(service.add_mail_application(self.data, cloud["message_ids"][0])["id"], result["id"])
        service.forget_mail(self.data)
        self.assertEqual(service.mail_overview(self.data)["messages"], 0)

    def test_newly_collected_vacancies_are_linked_after_a_refresh(self):
        service.scan_mail(self.data)
        later = service.import_vacancy(self.data, {"title": "Data Engineer", "company": "Berlinfixture GmbH", "location": "Berlin",
                                                   "description": "Data.", "source": "manual", "source_url": "https://jobs.example.test/3"})[0]
        from norway_job_agent.storage import JobStore
        with JobStore(self.data / "vacancies.db") as store:
            service._relink_mail(store)
        self.assertEqual(service.list_page(self.data, view="discover", query="Data Engineer")["jobs"][0]["email"]["kind"], "interview")
        self.assertEqual(service.get_vacancy(self.data, later)["status"], "new")  # Stages change only when you choose.

    def test_unclear_mail_is_read_by_ai(self):
        service.scan_mail(self.data)
        engine = FakeEngine({"emails": [{"id": "0", "kind": "applied", "company": "", "position": ""}]})
        with patch.object(cloud_ai, "engine", return_value=engine):
            result = service.ai_read_mail(self.data)
        self.assertEqual(len(engine.calls[0]["emails"]), 1)
        self.assertEqual(result["unclear"], 1)


class AlertReadingTests(unittest.TestCase):
    ALERT = ('<p>New jobs for you</p><div><a href="https://www.linkedin.com/jobs/view/111">See job</a> Platform Engineer at Fabrikam, Oslo</div>'
             '<div><a href="https://www.linkedin.com/jobs/view/222">See job</a> Data Analyst at Contoso, Bergen</div>'
             '<a href="https://example.com/unsubscribe">Unsubscribe</a>')

    def test_rules_report_alerts_they_could_not_read(self):
        alert = gmail_message("a1", "Jobs for you", "Alerts <alerts@linkedin.com>", self.ALERT, html=True)
        with patch.object(gmail, "_gmail_get", side_effect=lambda data, url: {"messages": [{"id": "a1"}]} if "?q=" in url else alert):
            result = gmail.fetch_candidates(Path("unused"), {"target_roles": ["Chef"]}, query="jobs")
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["unread"], [{"message_id": "a1", "subject": "Jobs for you", "sender": "Alerts <alerts@linkedin.com>"}])

    def test_ai_chooses_links_by_number_and_cannot_invent_jobs(self):
        alert = gmail_message("a1", "Jobs for you", "Alerts <alerts@linkedin.com>", self.ALERT, html=True)
        engine = FakeEngine({"jobs": [{"title": "Platform Engineer", "company": "Fabrikam", "location": "Oslo", "link": 0},
                                      {"title": "Data Analyst", "company": "Contoso", "location": "Bergen", "link": 1},
                                      {"title": "Chief Wizard", "company": "Nowhere", "location": "", "link": 0},
                                      {"title": "Data Analyst", "company": "Contoso", "location": "", "link": 7}]})
        with patch.object(gmail, "_gmail_get", return_value=alert):
            jobs = gmail.ai_candidates(Path("unused"), "a1", engine)
        self.assertEqual([(job["title"], job["company"], job["source_url"]) for job in jobs],
                         [("Platform Engineer", "Fabrikam", "https://www.linkedin.com/jobs/view/111"),
                          ("Data Analyst", "Contoso", "https://www.linkedin.com/jobs/view/222")])
        sent = engine.calls[0]
        self.assertEqual([link["number"] for link in sent["links"]], [0, 1])  # The unsubscribe link is not a job.
        self.assertNotIn("http", json.dumps(sent))
        self.assertTrue(jobs[0]["raw_json"]["email_import"]["ai_read"])


class MailApiTests(unittest.TestCase):
    def setUp(self):
        offline_collection()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        self.app = create_app(self.data, token="desktop-token")
        self.addCleanup(close_resources, self.app)
        self.client = self.app.test_client()
        self.job = service.import_vacancy(self.data, {"title": "Python Developer", "company": "Nordic Fixture", "location": "Oslo",
                                                      "description": "Python.", "source": "manual", "source_url": "https://jobs.example.test/1"})[0]

    def call(self, method, path, **options):
        return self.client.open(path, method=method, headers={"X-Session-Token": "desktop-token"}, **options)

    def test_scan_task_overview_and_stage_updates(self):
        with patch.object(gmail, "_gmail_get", side_effect=fake_gmail):
            task_id = self.call("POST", "/api/tasks", json={"action": "mail-scan", "days": 90}).get_json()["task_id"]
            for _ in range(200):
                state = self.call("GET", f"/api/tasks/{task_id}").get_json()
                if state["status"] != "running":
                    break
                time.sleep(0.02)
        self.assertEqual(state["status"], "done", state)
        overview = self.call("GET", "/api/mail-applications").get_json()
        self.assertEqual(overview["applications"], 5)
        self.assertEqual(self.call("GET", f"/api/jobs/{self.job}").get_json()["mail"][0]["this_job"], True)
        listed = self.call("GET", "/api/jobs?view=discover").get_json()["jobs"]
        self.assertEqual(listed[0]["email"]["kind"], "rejected")
        stage = overview["suggestions"][0]
        updated = self.call("POST", "/api/mail-applications/stages", json={"items": [{"job_id": stage["job_id"], "status": stage["suggested"]}]})
        self.assertEqual(updated.get_json()["updated"], 1)
        self.assertEqual(self.call("POST", "/api/tasks", json={"action": "mail-scan", "days": "all"}).status_code, 400)
        self.assertEqual(self.call("POST", "/api/tasks", json={"action": "gmail-ai-read", "message_ids": ["x"] * 11}).status_code, 400)
        self.assertEqual(self.call("DELETE", "/api/mail-applications").get_json()["messages"], 0)


if __name__ == "__main__":
    unittest.main()
