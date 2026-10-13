"""Open the native desktop with fictional data; never touch the user's workspace."""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from norway_job_agent import service
from norway_job_agent.application_forms import parse_manual_questions
from norway_job_agent.modern_desktop import launch


def seed(data):
    service.initialize(data)
    profile = service.read_profile(data)
    profile.update(name="Alex Morgan · Demo", summary="Engineer building useful machine learning tools.", years_of_experience=2.5,
                   skills=["Python", "SQL", "Machine Learning", "NLP"],
                   target_roles=["AI Engineer", "Data Scientist", "ML Engineer"],
                   related_roles=["Data Engineer", "Research Engineer"], preferred_locations=[],
                   evidence=["Built a Python pipeline for document analysis."],
                   cv_text="Fictional design preview. Built a Python pipeline for document analysis.")
    service.save_profile(data, profile)
    service.save_settings(data, {"model": "qwen3:4b", "sources": [], "active_country": "", "country_presets_added": []})
    for i, (title, company, location, stage) in enumerate([
        ("AI Engineer, Applied Intelligence", "Northstar Labs", "Oslo, Norway", "saved"),
        ("Data Scientist — Product Insights", "Forma Analytics", "Berlin, Germany", "new"),
        ("Machine Learning Engineer", "Brightfield", "Boston, MA, USA", "preparing"),
        ("Research Engineer, Language AI", "Atlas Research", "Kyiv, Ukraine", "new"),
        ("AI Engineer — Graduate Programme", "Fjord Systems", "Bergen, Norway", "interview"),
        ("Data Engineer, Platform", "Nordic Intelligence", "Hamburg, Germany", "ready"),
    ]):
        job_id, _ = service.import_vacancy(data, {
            "title": title, "company": company, "location": location, "source": "manual",
            "source_url": f"https://example.com/fictional-opportunities/{i}",
            "employment_type": "Full-time · Hybrid", "published_at": "2026-10-05",
            "description": "Fictional opportunity for design preview.\n\nHelp a thoughtful team turn research into everyday tools. We build products that make complex information easier to understand.\n\nWhat you’ll work on\n\n• Develop reliable Python services for language and document workflows.\n• Experiment with machine learning models and measure their impact.\n• Collaborate with researchers, designers and product engineers.\n• Build clear, maintainable SQL data pipelines.\n\nWhat you bring\n\nHands-on experience with Python, curiosity about real-world problems and a solid foundation in statistics. Experience with NLP is a plus.\n\nLife here\n\nA small, collaborative team. Flexible working hours. Time to learn and space to do your best work.",
        })
        service.update_workflow(data, job_id, stage, "Ask about the team’s current projects." if stage == "preparing" else "")
        service.save_edited_letter(data, job_id, "Dear hiring team,\n\nI’m interested in bringing my experience with Python and document analysis to your team. I built a Python pipeline for document analysis and would welcome the chance to discuss the role.\n\nKind regards,\nAlex Morgan\n\n[Fictional preview draft]")
        form = parse_manual_questions("Describe a relevant project\nPreferred work arrangement | Remote | Hybrid | On-site\nWhy are you interested in this team?")
        service.save_application(data, job_id, {"job_id": job_id, "form": form, "answers": [{"field_id": form["fields"][0]["id"], "label": form["fields"][0]["label"], "answer": "I built a Python pipeline for document analysis.", "selected_options": [], "used_evidence": ["Built a Python pipeline for document analysis."], "review_notes": ["Fictional draft: review before use."], "status": "draft"}]})


if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="job-agent-desktop-preview-") as directory:
        data = Path(directory)
        seed(data)
        launch(data, preview=True)
