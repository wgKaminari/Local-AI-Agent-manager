"""Open the interface with fictional data in a temporary, isolated workspace."""

import sys
import tempfile
import tkinter as tk
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from norway_job_agent import service
from norway_job_agent.desktop import Desktop


def main():
    with tempfile.TemporaryDirectory(prefix="norway-ui-preview-") as directory:
        data = Path(directory)
        service.initialize(data)
        if "--dark" in sys.argv:
            service._write_json(data / "appearance.json", {"theme": "dark", "sidebar_visible": True})
        profile = service.read_profile(data)
        profile.update(name="Alex Example", summary="AI engineer with a background in statistics and applied machine learning.",
                       target_roles=["AI Engineer", "Data Scientist", "ML Engineer"],
                       related_roles=["Data Engineer", "Research Engineer"],
                       skills=["Python", "SQL", "Machine Learning", "Docker", "NLP"],
                       languages={"English": "B2–C1", "Norwegian": "A1–A2"},
                       search_languages={"Norwegian": "B1"}, preferred_locations=["Norway"],
                       work_authorization="Work permission required.",
                       evidence=["Built Python pipelines for document analysis.", "Developed machine learning models for coursework."],
                       cv_text="Alex Example\nAI Engineer\n\nThis is fictional preview data.\n\nExperience\nBuilt Python pipelines for document analysis.\n\nSkills\nPython, SQL, machine learning, Docker, NLP")
        service.save_profile(data, profile)
        settings = service.read_settings(data)
        settings["sources"] = [{"type": "nav"}, {"type": "greenhouse", "board": "example-company"}]
        service.save_settings(data, settings)
        for i, (title, company, location, status) in enumerate([
            ("AI Engineer", "Northstar Labs · Demo", "Oslo, Norway", "new"),
            ("Data Scientist", "Fjord Analytics · Demo", "Bergen, Norway", "saved"),
            ("ML Engineer — Language Technology", "Nordic Research · Demo", "Trondheim, Norway", "preparing"),
            ("Research Engineer, Applied Mathematics", "Aurora Systems · Demo", "Oslo, Norway", "new"),
            ("Data Engineer", "Northstar Labs · Demo", "Stavanger, Norway", "new"),
            ("AI Engineer — Graduate Programme", "Fjord Analytics · Demo", "Oslo, Norway", "interview"),
        ]):
            job_id, _ = service.import_vacancy(data, {
                "title": title, "company": company, "location": location, "source": "manual",
                "source_url": f"https://example.com/demo/{i}",
                "description": "Fictional opportunity for interface preview.\n\n"
                "Help a small team turn research into useful products. We work across machine learning, language technology and software engineering.\n\n"
                "What you’ll work on\n"
                "• Build and evaluate AI agents and document workflows.\n"
                "• Develop Python services and SQL data pipelines.\n"
                "• Bring models into production with Docker.\n"
                "• Work closely with researchers and product teams.\n\n"
                "What you bring\n"
                "A foundation in statistics, curiosity about real-world problems and hands-on experience with Python. Knowledge of NLP is welcome.\n\n"
                "We offer a collaborative team, time to learn and a hybrid workplace in Norway.",
            })
            service.update_workflow(data, job_id, status, "Ask about the team's current projects." if i == 2 else "")
        root = tk.Tk()
        app = Desktop(root, data)
        if "--gmail" in sys.argv:
            from norway_job_agent import gmail
            candidates = gmail.import_eml(Path(__file__).resolve().parents[1] / "examples/demo-job-alert.eml", profile)
            app._show_email_results({"candidates": candidates, "messages_scanned": 1})
            app._navigate("gmail")
        if "--compact" in sys.argv:
            root.geometry("1120x720")
        if "--collapsed" in sys.argv:
            app._show_sidebar(False)
        if "--library" in sys.argv:
            app._navigate("settings")
            app.settings_tabs.select(3)
        root.title("Norway Job Agent — Design preview (fictional data)")
        root.mainloop()


if __name__ == "__main__":
    main()
