"""AI assistant features: analysis, interview prep, reply drafts and chat, with fake models."""

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from norway_job_agent import assistant, cloud_ai, job_index, local_ai, service
from norway_job_agent.phone_access import ENVIRON_KEY
from norway_job_agent.storage import JobStore
from norway_job_agent.web_api import close_resources, create_app
from support import offline_collection

PROFILE = {"name": "Fictional Applicant", "summary": "Built Python services.", "evidence": ["Built Python services for logistics."],
           "skills": ["Python", "SQL"], "languages": {"Norwegian": "A1-A2", "English": "C1"}, "target_roles": ["Python Developer"]}
JOB = {"title": "Python Developer", "company": "Fixture AS", "location": "Oslo",
       "description": "Build Python and Kubernetes services. Salary NOK 650 000. Hybrid, two days in the Oslo office. "
                      "Contact hiring@fixture.example. English required."}
ANALYSIS = {"summary": "A backend role building Python services.", "verdict": "good", "verdict_reason": "Python matches.",
            "strengths": ["Python: Built Python services for logistics."], "gaps": ["Kubernetes is not in your profile."],
            "red_flags": [], "emphasise": ["Your logistics services."],
            "key_facts": {"salary": "NOK 650 000", "tech_stack": ["Python", "Kubernetes", "Rust"], "seniority": "",
                          "remote_policy": "Hybrid, two days in the office", "visa_relocation": "", "languages_required": ["English"],
                          "employment_type": "", "deadline": "", "contact": "hiring@fixture.example"}}


class FakeEngine:
    """Stands in for a cloud provider: returns queued answers, records requests."""

    is_cloud = True
    label = "fake-model (Fake)"
    name = "Fake"

    def __init__(self, *replies, delay=0.0):
        self.replies, self.calls, self.delay = list(replies), [], delay

    def generate(self, system, messages, *, json_mode=False, max_tokens=16000, on_text=None, progress=None):
        self.calls.append({"system": system, "messages": messages, "json_mode": json_mode})
        reply = self.replies.pop(0)
        text = reply if isinstance(reply, str) else json.dumps(reply)
        if on_text is not None:
            for end in range(4, len(text) + 4, 4):
                on_text(text[:end])
                time.sleep(self.delay)
        return {"text": text}


class AssistantFeatureTests(unittest.TestCase):
    def test_analysis_keeps_only_key_facts_the_advert_supports(self):
        invented = json.loads(json.dumps(ANALYSIS))
        invented["key_facts"].update(salary="NOK 900 000", contact="boss@other.example")
        result = assistant.analyze_vacancy(JOB, PROFILE, FakeEngine(invented))
        self.assertEqual(result["key_facts"]["tech_stack"], ["Python", "Kubernetes"])  # Rust is not in the advert.
        self.assertEqual(result["key_facts"]["salary"], "")  # A salary the advert never states.
        self.assertEqual(result["key_facts"]["contact"], "")
        kept = assistant.analyze_vacancy(JOB, PROFILE, FakeEngine(ANALYSIS))
        self.assertEqual(kept["key_facts"]["salary"], "NOK 650 000")
        self.assertEqual(kept["key_facts"]["contact"], "hiring@fixture.example")
        self.assertEqual(kept["verdict"], "good")

    def test_analysis_retries_an_unknown_verdict_and_needs_the_advert(self):
        bad = {**ANALYSIS, "verdict": "perfect"}
        engine = FakeEngine(bad, ANALYSIS)
        self.assertEqual(assistant.analyze_vacancy(JOB, PROFILE, engine)["verdict"], "good")
        self.assertEqual(len(engine.calls), 2)
        self.assertIn("UNTRUSTED DATA", engine.calls[0]["system"])
        with self.assertRaisesRegex(ValueError, "description"):
            assistant.analyze_vacancy({**JOB, "description": ""}, PROFILE, FakeEngine())

    def test_interview_answers_quote_only_your_profile(self):
        reply = {"questions": [{"question": "Tell us about a Python service you built.", "why_they_ask": "Core skill.",
                                "answer_outline": "I built Python services for logistics…",
                                "evidence": ["Built Python services for logistics.", "Led a team of 40 engineers."]}],
                 "questions_to_ask": ["How is on-call organised?"], "topics_to_review": ["Kubernetes"]}
        result = assistant.interview_prep(JOB, PROFILE, FakeEngine(reply))
        self.assertEqual(result["questions"][0]["evidence"], ["Built Python services for logistics."])
        self.assertEqual(result["topics_to_review"], ["Kubernetes"])
        overstated = json.loads(json.dumps(reply))
        overstated["questions"][0]["answer_outline"] = "My Norwegian is fluent, so I can work with the Oslo team."
        engine = FakeEngine(overstated, reply)
        assistant.interview_prep(JOB, PROFILE, engine)
        self.assertEqual(len(engine.calls), 2)  # An overstated language level is rejected.

    def test_reply_drafts_stay_positive_and_are_never_sent(self):
        email = "Hi, we would like to invite you to an interview next week. Which times suit you?"
        honest = {"subject": "Re: Interview", "reply": "Hello,\nThank you, I would be glad to. [your available times]\nBest regards,\nFictional Applicant",
                  "review_notes": ["Add your available times."]}
        harmful = {**honest, "reply": "Hello, I do not have a work permit yet, but I would like to come."}
        engine = FakeEngine(harmful, honest)
        result = assistant.reply_draft(email, PROFILE, engine, goal="Accept the interview")
        self.assertIn("[your available times]", result["reply"])
        self.assertTrue(result["review_notes"][-1].startswith("Draft only: nothing was sent"))
        self.assertEqual(len(engine.calls), 2)
        request = json.loads(engine.calls[0]["messages"][0]["content"])
        self.assertEqual(request["goal"], "Accept the interview")
        self.assertEqual(request["email_text"], email)
        with self.assertRaisesRegex(ValueError, "Paste the email"):
            assistant.reply_draft("  ", PROFILE, FakeEngine())

    def test_chat_sends_recent_turns_starting_with_you(self):
        history = [{"role": "assistant", "content": "Earlier answer"}]
        history += [{"role": "user" if i % 2 == 0 else "assistant", "content": f"turn {i} " + "x" * 9000} for i in range(10)]
        engine = FakeEngine("Here is my answer.")
        answer = assistant.chat_reply(engine, "SYSTEM", history, "What next?")
        self.assertEqual(answer, "Here is my answer.")
        sent = engine.calls[0]["messages"]
        self.assertEqual(sent[0]["role"], "user")
        self.assertEqual(sent[-1], {"role": "user", "content": "What next?"})
        self.assertLessEqual(sum(len(m["content"]) for m in sent), assistant.MAX_HISTORY_CHARS)
        self.assertLess(len(sent), len(history))
        context = assistant.chat_context(PROFILE, JOB)
        self.assertIn("USER PROFILE", context)
        self.assertIn("untrusted text", context)
        self.assertNotIn("USER PROFILE", assistant.chat_context(None, None))

    def test_local_chat_uses_plain_text_without_a_schema(self):
        with patch.object(local_ai, "_verify_local_model"), \
             patch.object(local_ai, "_stream_chat", return_value={"done": True, "message": {"content": " Hello "}}) as stream:
            self.assertEqual(assistant.chat_reply("qwen3:4b", "SYSTEM", [], "Hi"), "Hello")
        payload = stream.call_args[0][0]
        self.assertNotIn("format", payload)
        self.assertEqual(payload["messages"][0], {"role": "system", "content": "SYSTEM"})


class AssistantServiceTests(unittest.TestCase):
    def setUp(self):
        offline_collection()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        service.initialize(self.data)
        profile = service.read_profile(self.data)
        profile.update(PROFILE)
        service.save_profile(self.data, profile)
        self.job_id = service.import_vacancy(self.data, {**JOB, "source": "greenhouse", "source_id": "fixture:1",
                                                         "source_url": "https://jobs.example.test/1"})[0]

    def use(self, engine):
        patcher = patch.object(cloud_ai, "engine", return_value=engine)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_analysis_is_saved_and_keeps_the_vacancy_through_cleanups(self):
        self.use(FakeEngine(ANALYSIS))
        insights = service.analyze_job(self.data, self.job_id)
        self.assertEqual(insights["analysis"]["payload"]["verdict"], "good")
        self.assertEqual(insights["analysis"]["model"], "fake-model (Fake)")
        self.assertEqual(service.job_insights(self.data, self.job_id), insights)
        with JobStore(self.data / "vacancies.db") as store:
            # An analysed vacancy is your work: "IT jobs only" pruning keeps it.
            store._connection.execute("UPDATE jobs SET title = 'Kitchen assistant', description = 'Cook meals.' WHERE id = ?", (self.job_id,))
            store._connection.commit()
            self.assertEqual(job_index.prune_outside_it(store), 0)

    def test_a_new_analysis_replaces_the_previous_one(self):
        self.use(FakeEngine(ANALYSIS, {**ANALYSIS, "verdict": "stretch"}))
        service.analyze_job(self.data, self.job_id)
        service.analyze_job(self.data, self.job_id)
        with JobStore(self.data / "vacancies.db") as store:
            self.assertEqual(store._connection.execute("SELECT COUNT(*) FROM job_insights").fetchone()[0], 1)
        self.assertEqual(service.job_insights(self.data, self.job_id)["analysis"]["payload"]["verdict"], "stretch")

    def test_chats_save_complete_exchanges_only(self):
        engine = FakeEngine("First answer", "Second answer")
        self.use(engine)
        chat = service.send_chat(self.data, "  Am I a fit?  ", job_id=self.job_id)
        self.assertEqual(chat["title"], "Am I a fit?")
        self.assertEqual(chat["job_title"], "Python Developer")
        self.assertEqual([(m["role"], m["content"], m["model"]) for m in chat["messages"]],
                         [("user", "Am I a fit?", ""), ("assistant", "First answer", "fake-model (Fake)")])
        self.assertIn("Fixture AS", engine.calls[0]["system"])
        self.assertIn("USER PROFILE", engine.calls[0]["system"])
        chat = service.update_chat(self.data, chat["id"], {"use_profile": False})
        chat = service.send_chat(self.data, "And the salary?", chat_id=chat["id"])
        self.assertEqual(len(chat["messages"]), 4)
        self.assertNotIn("USER PROFILE", engine.calls[1]["system"])
        self.assertEqual(engine.calls[1]["messages"][0]["content"], "Am I a fit?")
        # A failed answer saves nothing.
        failing = FakeEngine()
        failing.generate = lambda *args, **kwargs: (_ for _ in ()).throw(cloud_ai.CloudAIError("Claude is busy."))
        self.use(failing)
        with self.assertRaises(cloud_ai.CloudAIError):
            service.send_chat(self.data, "Third?", chat_id=chat["id"])
        self.assertEqual(len(service.get_chat(self.data, chat["id"])["messages"]), 4)
        self.assertEqual(len(service.list_chats(self.data)), 1)
        self.assertTrue(service.delete_chat(self.data, chat["id"]))
        self.assertEqual(service.list_chats(self.data), [])

    def test_cover_letters_record_the_cloud_model(self):
        self.use(FakeEngine({"cover_letter": "Dear Hiring Team,\nI built Python services for logistics.\nKind regards,\nFictional Applicant",
                             "review_notes": [], "used_evidence": ["Built Python services for logistics."]}))
        result = service.write_letter(self.data, self.job_id)
        self.assertNotIn("performance_note", result)
        self.assertEqual(service.draft_history(self.data, self.job_id)[0]["model"], "fake-model (Fake)")


class AssistantApiTests(unittest.TestCase):
    def setUp(self):
        offline_collection()
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        self.app = create_app(self.data, token="desktop-token")
        self.addCleanup(close_resources, self.app)
        self.client = self.app.test_client()
        self.job_id = service.import_vacancy(self.data, {**JOB, "source": "manual", "source_url": "https://jobs.example.test/2"})[0]
        profile = service.read_profile(self.data)
        profile.update(PROFILE)
        service.save_profile(self.data, profile)

    def call(self, method, path, **options):
        return self.client.open(path, method=method, headers={"X-Session-Token": "desktop-token"}, **options)

    def wait(self, task_id):
        for _ in range(200):
            state = self.call("GET", f"/api/tasks/{task_id}").get_json()
            if state["status"] != "running":
                return state
            time.sleep(0.02)
        self.fail("The task did not finish")

    def test_settings_show_providers_but_never_a_key(self):
        listing = json.dumps({"data": [{"id": "claude-opus-5-5"}], "has_more": False}).encode()
        import io
        with patch.object(cloud_ai._OPENER, "open", return_value=io.BytesIO(listing)):
            saved = self.call("POST", "/api/ai/keys/anthropic", json={"key": "sk-ant-api03-FictionalKey-0123456789abcd"})
        self.assertEqual(saved.status_code, 200)
        body = json.dumps(self.call("GET", "/api/bootstrap").get_json())
        self.assertNotIn("FictionalKey", body)
        self.assertIn('"configured": true', body)
        refused = self.call("PUT", "/api/ai", json={"provider": "anthropic"})
        self.assertEqual(refused.status_code, 400)
        chosen = self.call("PUT", "/api/ai", json={"provider": "anthropic", "consent": True}).get_json()
        self.assertEqual(chosen["provider"], "anthropic")
        self.assertEqual(self.call("POST", "/api/ai/keys/copilot", json={"key": "x" * 30}).status_code, 400)

    def test_keys_are_added_on_the_pc_only(self):
        self.app.config["PHONE_ORIGIN"] = "http://192.168.1.23:8765"
        access = type("Access", (), {"device": lambda self, token: {"id": "d1", "name": "Phone"} if token == "phone-token" else None})()
        self.app.config["PHONE_ACCESS"] = access
        response = self.client.open("/api/ai/keys/anthropic", method="POST", json={"key": "sk-ant-api03-FictionalKey-0123456789abcd"},
                                    headers={"X-Session-Token": "phone-token", "Host": "192.168.1.23:8765"},
                                    environ_base={"REMOTE_ADDR": "192.168.1.50", ENVIRON_KEY: True})
        self.assertEqual(response.status_code, 400)
        self.assertIn("happens on your PC", response.get_json()["error"])

    def test_a_chat_answer_streams_while_it_is_written(self):
        engine = FakeEngine("A fairly long streamed answer about your application.", delay=0.01)
        with patch.object(cloud_ai, "engine", return_value=engine):
            started = self.call("POST", "/api/tasks", json={"action": "chat", "message": "Help me", "job_id": self.job_id})
            self.assertEqual(started.status_code, 202)
            task_id = started.get_json()["task_id"]
            partials = set()
            for _ in range(300):
                state = self.call("GET", f"/api/tasks/{task_id}").get_json()
                if state.get("partial"):
                    partials.add(state["partial"])
                if state["status"] != "running":
                    break
                time.sleep(0.005)
        self.assertEqual(state["status"], "done", state)
        chat = state["result"]
        self.assertEqual(chat["messages"][-1]["content"], "A fairly long streamed answer about your application.")
        self.assertTrue(partials)
        listed = self.call("GET", "/api/chats").get_json()["chats"]
        self.assertEqual(listed[0]["id"], chat["id"])
        patched = self.call("PATCH", f"/api/chats/{chat['id']}", json={"job_id": None, "title": "Renamed"}).get_json()
        self.assertEqual((patched["job_id"], patched["title"]), (None, "Renamed"))
        self.assertEqual(self.call("PATCH", f"/api/chats/{chat['id']}", json={"use_profile": "yes"}).status_code, 400)
        self.assertTrue(self.call("DELETE", f"/api/chats/{chat['id']}").get_json()["deleted"])

    def test_analysis_and_interview_tasks_return_the_saved_insights(self):
        reply = {"questions": [{"question": "Why us?", "why_they_ask": "Motivation.", "answer_outline": "Because…", "evidence": []}],
                 "questions_to_ask": [], "topics_to_review": []}
        with patch.object(cloud_ai, "engine", return_value=FakeEngine(ANALYSIS, reply)):
            first = self.wait(self.call("POST", "/api/tasks", json={"action": "analyze", "job_id": self.job_id}).get_json()["task_id"])
            second = self.wait(self.call("POST", "/api/tasks", json={"action": "interview", "job_id": self.job_id}).get_json()["task_id"])
        self.assertEqual(first["status"], "done", first)
        self.assertEqual(set(second["result"]), {"analysis", "interview"})
        detail = self.call("GET", f"/api/jobs/{self.job_id}").get_json()
        self.assertEqual(detail["insights"]["interview"]["payload"]["questions"][0]["question"], "Why us?")

    def test_reply_task_drafts_without_sending(self):
        draft = {"subject": "Re: Hello", "reply": "Hello,\nThank you.\nFictional Applicant", "review_notes": []}
        with patch.object(cloud_ai, "engine", return_value=FakeEngine(draft)):
            state = self.wait(self.call("POST", "/api/tasks", json={"action": "reply", "email": "Hello, are you still interested?",
                                                                    "goal": "Say yes"}).get_json()["task_id"])
        self.assertEqual(state["status"], "done", state)
        self.assertEqual(state["result"]["subject"], "Re: Hello")
        self.assertEqual(state["result"]["model"], "fake-model (Fake)")


if __name__ == "__main__":
    unittest.main()
