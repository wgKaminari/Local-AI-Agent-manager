"""Country isolation, migration, and reviewed-application service regressions.

All candidate data is synthetic. These tests never contact an employer or model.
"""

import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import service
from norway_job_agent.application_forms import parse_manual_questions
from norway_job_agent.countries import (
    job_countries, profile_for_job, validate_countries, validate_country_preferences,
)
from norway_job_agent.profile import match_job, validate_profile
from norway_job_agent.storage import JobStore


class CountryProfileTests(unittest.TestCase):
    def setUp(self):
        self.profile = validate_profile({
            "name": "Fixture Person", "summary": "Built Python tools.",
            "target_roles": ["Data Scientist"], "related_roles": ["Data Engineer"],
            "skills": ["Python"], "evidence": ["Built Python tools."],
            "preferred_locations": ["Remote"],
            "languages": {"English": "B2", "Norwegian": "A1"},
            "work_authorization": "Norway permission has not been obtained.",
        })

    def test_country_detection_does_not_confuse_remote_or_foreign_city_names(self):
        cases = [
            ({"location": "Oslo, Norway"}, ["NO"]),
            ({"location": "Berlin, Germany"}, ["DE"]),
            ({"location": "Київ"}, ["UA"]),
            ({"location": "New York, NY"}, ["US"]),
            ({"location": "Berlin, NH"}, ["US"]),
            ({"location": "Oslo, United States"}, ["US"]),
            ({"location": "Remote", "description": "We have offices in Norway"}, []),
            ({"location": "Worldwide remote"}, []),
            ({"location": "Unknown"}, []),
            ({"location": "Berlin", "countries": ["UA"]}, ["UA"]),
            ({"location": "Remote", "countries": ["US", "DE", "US"]}, ["US", "DE"]),
        ]
        for job, expected in cases:
            with self.subTest(job=job):
                self.assertEqual(job_countries(job), expected)

    def test_legacy_authorization_is_only_available_for_norway(self):
        for countries in (["NO"], ["US"], ["DE"], ["UA"], [], ["NO", "US"]):
            with self.subTest(countries=countries):
                result = profile_for_job(self.profile, {"countries": countries})
                expected = self.profile["work_authorization"] if countries == ["NO"] else ""
                self.assertEqual(result["work_authorization"], expected)
                for key in ("name", "summary", "skills", "evidence", "languages", "target_roles", "preferred_locations"):
                    self.assertEqual(result[key], self.profile[key])

    def test_country_overrides_are_independent_copies_of_shared_profile(self):
        self.profile["country_preferences"] = {
            "DE": {"work_authorization": "Requires separate review in Germany.",
                   "cover_letter_language": "German", "preferred_locations": ["Berlin"],
                   "relocation_preference": "Discuss relocation first"},
        }
        baseline = copy.deepcopy(self.profile)
        german = profile_for_job(self.profile, {"location": "Berlin, Germany"})
        self.assertEqual(german["work_authorization"], "Requires separate review in Germany.")
        self.assertEqual(german["cover_letter_language"], "German")
        self.assertEqual(german["preferred_locations"], ["Berlin"])
        self.assertEqual(german["target_roles"], baseline["target_roles"])
        german["target_roles"].append("Unrelated mutation")
        german["preferred_locations"].append("Unrelated city")
        german["languages"]["English"] = "Unrelated mutation"
        self.assertEqual(self.profile, baseline)
        american = profile_for_job(self.profile, {"countries": ["US"]})
        self.assertEqual(american["preferred_locations"], ["Remote"])
        self.assertEqual(american["cover_letter_language"], "English")
        self.assertEqual(american["work_authorization"], "")

    def test_explicit_empty_norway_authorization_clears_legacy_claim(self):
        self.profile["country_preferences"] = {"NO": {"work_authorization": ""}}
        self.assertEqual(profile_for_job(self.profile, {"countries": ["NO"]})["work_authorization"], "")

    def test_matching_warns_about_unknown_country_without_inventing_eligibility(self):
        job = {"title": "Data Scientist", "location": "Remote", "description": "Python"}
        result = match_job(job, self.profile)
        self.assertEqual(result["track"], "target")
        self.assertTrue(any("Country is unknown" in item for item in result["warnings"]))
        self.assertFalse(any(self.profile["work_authorization"] in item for item in result["warnings"]))
        self.profile["target_countries"] = ["DE"]
        result = match_job({**job, "location": "New York, USA"}, self.profile)
        self.assertTrue(any("outside your selected search countries" in item for item in result["warnings"]))

    def test_invalid_country_settings_fail_before_becoming_search_preferences(self):
        for value in ("DE", ["FR"], [True], None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_countries(value)
        for value in ({"FR": {}}, {"DE": {"skills": ["Invented"]}},
                      {"DE": {"target_roles": "Engineer"}}, {"UA": {"work_authorization": True}}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_country_preferences(value)


class CountryServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="country-service-test-")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        service.initialize(self.folder)
        profile = service.read_profile(self.folder)
        profile.update(name="Fixture Person", target_roles=["Data Scientist"], skills=["Python"],
                       summary="Built Python tools.", evidence=["Built Python tools."])
        service.save_profile(self.folder, profile)

    def add_job(self, code="DE", **changes):
        job = {"source": "manual", "source_id": code,
               "source_url": f"https://jobs.example.com/jobs/{code}",
               "title": "Data Scientist", "company": "Fixture Company",
               "location": "Remote" if not code else code, "description": "Python tools",
               "countries": [code] if code else [], **changes}
        return service.import_vacancy(self.folder, job)[0]

    def test_switching_country_filters_preserves_shared_profile_and_vacancies(self):
        expected = {code: self.add_job(code) for code in ("NO", "US", "DE", "UA", "")}
        profile_before = (self.folder / "profile.json").read_bytes()
        for code in ("NO", "DE", "US", "UA", ""):
            settings = service.read_settings(self.folder)
            service.save_settings(self.folder, {**settings, "active_country": code})
            jobs = service.vacancies(self.folder, country=code)
            self.assertEqual({job["id"] for job in jobs}, {expected[code]} if code else set(expected.values()))
            self.assertEqual((self.folder / "profile.json").read_bytes(), profile_before)
        self.assertEqual([job["id"] for job in service.vacancies(self.folder, country="unknown")], [expected[""]])
        self.assertEqual(len(service.vacancies(self.folder)), 5)

    def test_multicountry_vacancy_is_visible_in_each_explicit_country(self):
        job_id = self.add_job("US", countries=["US", "DE"])
        self.assertEqual([job["id"] for job in service.vacancies(self.folder, country="US")], [job_id])
        self.assertEqual([job["id"] for job in service.vacancies(self.folder, country="DE")], [job_id])
        self.assertEqual(service.vacancies(self.folder, country="NO"), [])

    def test_collection_uses_only_active_country_sources_without_mutating_configuration(self):
        settings = {"active_country": "DE", "model": "qwen3:4b", "sources": [
            {"type": "nav"},
            {"type": "greenhouse", "board": "norway-fixture", "countries": ["NO"]},
            {"type": "greenhouse", "board": "german-fixture", "countries": ["DE", "US"]},
            {"type": "lever", "board": "global-fixture"},
        ]}
        baseline = copy.deepcopy(settings)
        profile_before = (self.folder / "profile.json").read_bytes()
        service.save_settings(self.folder, settings)
        settings_before = (self.folder / "sources.json").read_bytes()
        with patch("norway_job_agent.nav.fetch_nav_batch") as nav, patch(
            "norway_job_agent.cli.collect_sources",
            return_value={"created": 0, "updated": 0, "sources": []},
        ) as collect:
            service.collect(self.folder, settings_override=settings)
        nav.assert_not_called()
        sources = [call.args[0]["sources"][0] for call in collect.call_args_list]
        self.assertEqual([item["board"] for item in sources], ["german-fixture", "global-fixture"])
        self.assertTrue(all(item["countries"] == ["DE"] for item in sources))
        self.assertEqual(settings, baseline)
        self.assertEqual((self.folder / "profile.json").read_bytes(), profile_before)
        self.assertEqual((self.folder / "sources.json").read_bytes(), settings_before)

    def test_invalid_country_does_not_replace_saved_settings(self):
        before = (self.folder / "sources.json").read_bytes()
        with self.assertRaises(ValueError):
            service.save_settings(self.folder, {"sources": [], "active_country": "FR"})
        self.assertEqual((self.folder / "sources.json").read_bytes(), before)
        with self.assertRaises(ValueError):
            service.vacancies(self.folder, country="FR")

    def test_letter_generation_receives_country_specific_authorization_only(self):
        job_id = self.add_job("DE")
        before = service.read_profile(self.folder)
        with patch("norway_job_agent.local_ai.generate_cover_letter", return_value={"cover_letter": "A draft"}) as generate:
            service.write_letter(self.folder, job_id)
        self.assertEqual(generate.call_args.args[1]["work_authorization"], "")
        self.assertEqual(generate.call_args.args[1]["skills"], before["skills"])
        self.assertEqual(service.read_profile(self.folder), before)
        self.assertEqual(service.get_vacancy(self.folder, job_id)["status"], "new")

    def test_prepared_and_edited_answers_persist_as_distinct_private_versions(self):
        job_id = self.add_job("DE")
        other_id = self.add_job("US")
        form = parse_manual_questions("Why this role?")
        field = form["fields"][0]
        generated = {"form": form, "answers": [{"field_id": field["id"], "label": field["label"],
            "answer": "Built Python tools.", "status": "draft", "selected_options": [],
            "used_evidence": ["Built Python tools."], "review_notes": []}], "review_notes": []}
        profile = service.read_profile(self.folder)
        profile["writing_style"] = {"tone": "brief and direct", "max_words": 80}
        profile["country_preferences"] = {"DE": {"cover_letter_language": "German"}}
        service.save_profile(self.folder, profile)
        before = (self.folder / "profile.json").read_bytes()
        with patch("norway_job_agent.application_answers.generate_application_answers", return_value=generated) as generate:
            prepared = service.prepare_application(self.folder, job_id, form)
        effective = generate.call_args.args[1]
        self.assertEqual(effective["work_authorization"], "")
        self.assertEqual(effective["cover_letter_language"], "German")
        self.assertEqual(generate.call_args.kwargs["writing_style"]["tone"], "brief and direct")
        edited = copy.deepcopy(prepared)
        edited["answers"][0].update(answer="My edited response.", status="user_edited")
        service.save_application(self.folder, job_id, edited)
        # Neither later caller mutation nor re-opening the store changes history.
        edited["answers"][0]["answer"] = "Unsaved mutation"
        history = service.application_history(self.folder, job_id)
        self.assertEqual([row["payload"]["answers"][0]["answer"] for row in history],
                         ["My edited response.", "Built Python tools."])
        self.assertEqual(history[1]["payload"]["job_id"], job_id)
        self.assertTrue(history[1]["payload"]["profile_hash"])
        self.assertEqual(service.application_history(self.folder, other_id), [])
        self.assertEqual(service.get_vacancy(self.folder, job_id)["status"], "new")
        self.assertEqual((self.folder / "profile.json").read_bytes(), before)

    def test_application_for_another_job_or_invalid_form_does_not_write_history(self):
        first = self.add_job("DE")
        second = self.add_job("US")
        payload = {"job_id": first, "form": parse_manual_questions("Why?"), "answers": []}
        with self.assertRaisesRegex(ValueError, "different vacancy"):
            service.save_application(self.folder, second, payload)
        invalid = {**payload, "form": {"fields": [{"id": "missing-required-metadata"}]}}
        with self.assertRaises(ValueError):
            service.save_application(self.folder, first, invalid)
        self.assertEqual(service.application_history(self.folder, first), [])
        self.assertEqual(service.application_history(self.folder, second), [])

    def preview(self, **changes):
        return {"destination": "https://jobs.example.com/applications", "token": "review-token",
                "fields": [{"name": "motivation", "value": "My reviewed response"}], "files": [], **changes}

    def test_delivery_intent_is_durable_before_send_and_suppresses_inflight_duplicate(self):
        job_id = self.add_job("DE")
        preview = self.preview()
        duplicate_session = Mock()
        def send(token):
            self.assertEqual(token, preview["token"])
            with JobStore(self.folder / "vacancies.db") as store:
                rows = store.deliveries(job_id)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["status"], "pending")
            self.assertEqual(rows[0]["destination"], preview["destination"])
            with self.assertRaisesRegex(ValueError, "already has a delivery attempt"):
                service.send_reviewed_application(self.folder, job_id, duplicate_session, self.preview(token="new-token"))
            return {"status": "submitted_unconfirmed", "message": "Click sent; confirmation needs review."}
        session = Mock()
        session.send.side_effect = send
        result = service.send_reviewed_application(self.folder, job_id, session, preview)
        session.send.assert_called_once()
        duplicate_session.send.assert_not_called()
        self.assertEqual(result["status"], "submitted_unconfirmed")
        with self.assertRaisesRegex(ValueError, "already has a delivery attempt"):
            service.send_reviewed_application(self.folder, job_id, duplicate_session, self.preview(token="another-token"))
        self.assertEqual(service.get_vacancy(self.folder, job_id)["status"], "new")

    def test_network_exception_remains_uncertain_and_cannot_silently_resend(self):
        job_id = self.add_job("DE")
        service.update_workflow(self.folder, job_id, "ready", "Reviewed by me")
        session = Mock()
        session.send.side_effect = OSError("Connection interrupted after the send")
        result = service.send_reviewed_application(self.folder, job_id, session, self.preview())
        self.assertEqual(result["status"], "uncertain")
        with JobStore(self.folder / "vacancies.db") as store:
            self.assertEqual(store.deliveries(job_id)[0]["status"], "uncertain")
        with self.assertRaises(ValueError):
            service.send_reviewed_application(self.folder, job_id, session, self.preview(token="fresh-preview"))
        session.send.assert_called_once()
        job = service.get_vacancy(self.folder, job_id)
        self.assertEqual((job["status"], job["notes"]), ("ready", "Reviewed by me"))

    def test_recording_failure_retains_pending_intent_and_prevents_second_send(self):
        job_id = self.add_job("DE")
        session = Mock()
        session.send.return_value = {"status": "submitted_unconfirmed", "message": "No confirmation"}
        with patch.object(JobStore, "finish_delivery", side_effect=sqlite3.OperationalError("Disk failure")):
            with self.assertRaises(sqlite3.OperationalError):
                service.send_reviewed_application(self.folder, job_id, session, self.preview())
        with JobStore(self.folder / "vacancies.db") as store:
            self.assertEqual(store.deliveries(job_id)[0]["status"], "pending")
        with self.assertRaises(ValueError):
            service.send_reviewed_application(self.folder, job_id, session, self.preview(token="retry"))
        session.send.assert_called_once()

    def test_withdrawn_job_blocks_preparation_and_delivery_without_network(self):
        job_id = self.add_job("DE")
        with JobStore(self.folder / "vacancies.db") as store:
            self.assertTrue(store.withdraw_source_job("manual", "DE"))
        form = parse_manual_questions("Why?")
        session = Mock()
        with patch("norway_job_agent.application_answers.generate_application_answers") as generate:
            with self.assertRaisesRegex(ValueError, "withdrawn"):
                service.prepare_application(self.folder, job_id, form)
        with self.assertRaisesRegex(ValueError, "withdrawn"):
            service.send_reviewed_application(self.folder, job_id, session, self.preview())
        generate.assert_not_called()
        session.send.assert_not_called()
        with JobStore(self.folder / "vacancies.db") as store:
            self.assertEqual(store.deliveries(job_id), [])


class CountryMigrationTests(unittest.TestCase):
    def test_upgrade_from_legacy_v1_and_v2_preserves_notes_letters_and_identity(self):
        with tempfile.TemporaryDirectory(prefix="country-migration-test-") as folder:
            for version in (1, 2):
                with self.subTest(version=version):
                    path = Path(folder) / f"legacy-v{version}.db"
                    # An independent legacy schema fixture, not one produced by
                    # the current JobStore initializer under test.
                    with closing(sqlite3.connect(path)) as connection:
                        connection.executescript("""
                            CREATE TABLE jobs (
                                id INTEGER PRIMARY KEY, source TEXT NOT NULL DEFAULT '',
                                source_id TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '',
                                apply_url TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
                                company TEXT NOT NULL DEFAULT '', location TEXT NOT NULL DEFAULT '',
                                description TEXT NOT NULL DEFAULT '', employment_type TEXT NOT NULL DEFAULT '',
                                published_at TEXT NOT NULL DEFAULT '', deadline TEXT NOT NULL DEFAULT '',
                                discovered_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
                                status TEXT NOT NULL DEFAULT 'new', notes TEXT NOT NULL DEFAULT '',
                                raw_json TEXT NOT NULL DEFAULT '{}', canonical_source_url TEXT NOT NULL DEFAULT ''
                            );
                            CREATE TABLE drafts (
                                id INTEGER PRIMARY KEY, job_id INTEGER NOT NULL REFERENCES jobs(id),
                                content TEXT NOT NULL, model TEXT NOT NULL DEFAULT '',
                                profile_hash TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
                            );
                            CREATE TABLE job_identities (
                                kind TEXT NOT NULL, value TEXT NOT NULL, job_id INTEGER NOT NULL REFERENCES jobs(id),
                                PRIMARY KEY (kind, value)
                            );
                            INSERT INTO jobs (id, source, source_id, source_url, title, location,
                                discovered_at, last_seen_at, status, notes, canonical_source_url)
                            VALUES (7, 'manual', 'legacy', 'https://example.com/jobs/legacy', 'Data Scientist',
                                'Oslo, Norway', '2026-09-01T10:00:00Z', '2026-09-02T10:00:00Z', 'ready',
                                'Preserve my reviewed notes.', 'https://example.com/jobs/legacy');
                            INSERT INTO drafts VALUES (11, 7, 'My reviewed cover letter.', 'user-edited',
                                'legacy-profile-hash', '2026-09-02T09:00:00Z');
                        """)
                        connection.execute("INSERT INTO job_identities VALUES (?, ?, ?)",
                                           ("source_id", json.dumps(["manual", "legacy"]), 7))
                        connection.execute("INSERT INTO job_identities VALUES (?, ?, ?)",
                                           ("source_url", "https://example.com/jobs/legacy", 7))
                        if version == 2:
                            connection.execute("ALTER TABLE jobs ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
                            connection.execute("UPDATE jobs SET is_active=0")
                        connection.execute(f"PRAGMA user_version={version}")
                        connection.commit()
                    with JobStore(path) as store:
                        job = store.get_job(7)
                        self.assertEqual((job["status"], job["notes"]), ("ready", "Preserve my reviewed notes."))
                        self.assertEqual(job["discovered_at"], "2026-09-01T10:00:00Z")
                        self.assertEqual(job["is_active"], int(version == 1))
                        self.assertEqual(job_countries(job), ["NO"])
                        self.assertEqual(store.list_drafts(7)[0]["content"], "My reviewed cover letter.")
                        self.assertEqual(store.list_drafts(7)[0]["id"], 11)
                        self.assertEqual(store.preparations(7), [])
                        self.assertEqual(store.deliveries(7), [])
                        updated_id, created = store.upsert_job({"source": "manual", "source_id": "legacy",
                            "source_url": "https://example.com/jobs/legacy", "title": "Updated Data Scientist",
                            "location": "Oslo, Norway"})
                        self.assertEqual(updated_id, 7)
                        self.assertFalse(created)
                        self.assertEqual(store.get_job(7)["notes"], "Preserve my reviewed notes.")
                    with closing(sqlite3.connect(path)) as connection:
                        self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 3)
                    # The upgrade is repeatable and does not create more jobs.
                    with JobStore(path) as store:
                        self.assertEqual(len(store.list_jobs()), 1)
                        self.assertEqual(len(store.list_drafts(7)), 1)


if __name__ == "__main__":
    unittest.main()
