"""Explicit user-provided career facts and transparent preliminary matching."""

import hashlib
import json
import re
from datetime import date
from pathlib import Path


LIST_FIELDS = ("target_roles", "related_roles", "skills", "preferred_locations", "excluded_keywords", "evidence")
TEXT_FIELDS = ("name", "summary", "work_authorization", "cv_text", "cover_letter_language")


def load_profile(path: str | Path) -> dict:
    return validate_profile(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def validate_profile(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise ValueError("Profile must be a JSON object.")
    for key in LIST_FIELDS:
        value = profile.setdefault(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"Profile '{key}' must be a list of strings.")
    for key in TEXT_FIELDS:
        value = profile.setdefault(key, "English" if key == "cover_letter_language" else "")
        if not isinstance(value, str):
            raise ValueError(f"Profile '{key}' must be text.")
    for field in ("languages", "search_languages"):
        languages = profile.setdefault(field, {})
        if not isinstance(languages, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in languages.items()):
            raise ValueError(f"Profile '{field}' must map language names to levels as text.")
    return profile


def profile_hash(profile: dict) -> str:
    return hashlib.sha256(json.dumps(profile, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def contains_phrase(text: str, phrase: str) -> bool:
    """Match literal terms without treating e.g. 'Java' as a match for 'JavaScript'."""
    if not phrase.strip():
        return False
    return re.search(r"(?<!\w)" + re.escape(phrase.strip()) + r"(?!\w)", text, re.IGNORECASE) is not None


def match_job(job: dict, profile: dict, today: date | None = None) -> dict:
    """Return keyword evidence, never an AI assessment or eligibility verdict."""
    title = str(job.get("title", ""))
    location = str(job.get("location", ""))
    body = "\n".join(str(job.get(key, "")) for key in ("title", "company", "location", "description"))
    configured = []
    matched_roles = [x for x in profile.get("target_roles", []) if contains_phrase(title, x)]
    broad_topics = {"artificial intelligence", "data science", "maskinlæring", "kunstig intelligens"}
    technical_title = re.search(r"engineer|ingeniør|scientist|forsker|research|utvikler|developer|matemati|mathemati", title, re.IGNORECASE)
    matched_roles = [role for role in matched_roles if role.casefold() not in broad_topics or technical_title]
    related_roles = [x for x in profile.get("related_roles", []) if contains_phrase(title, x)]
    research_labels = {"phd", "stipendiat", "research assistant", "research scientist", "research engineer"}
    research_overlap = any(term in body.casefold() for term in ("mathemat", "matemat", "statistic", "statistikk", "data science", "machine learning", "maskinlæring", "quantitative", "reinforcement learning", "kunstig intelligens"))
    # Broad academic titles alone should not recommend an unrelated discipline.
    related_roles = [role for role in related_roles if role.casefold() not in research_labels or research_overlap]
    academic_title = re.search(r"\bphd\b|stipendiat|postdoc|postdoktor", title, re.IGNORECASE)
    if academic_title and research_overlap:
        related_roles = list(dict.fromkeys([*related_roles, "Research in a related field"]))
        matched_roles = []
    matched_skills = [x for x in profile.get("skills", []) if contains_phrase(body, x)]
    matched_locations = [x for x in profile.get("preferred_locations", []) if contains_phrase(location, x)]
    exclusions = [x for x in profile.get("excluded_keywords", []) if contains_phrase(body, x)]
    if profile.get("target_roles"):
        configured.append((50, 1.0 if matched_roles else 0.0))
    if profile.get("skills"):
        configured.append((35, len(matched_skills) / len(profile["skills"])))
    if profile.get("preferred_locations"):
        configured.append((15, 1.0 if matched_locations else 0.0))
    score = round(100 * sum(weight * ratio for weight, ratio in configured) / sum(weight for weight, _ in configured)) if configured else None
    warnings = []
    if not configured:
        warnings.append("Add target roles, skills or locations to get a keyword score.")
    if exclusions:
        warnings.append("Contains excluded terms: " + ", ".join(exclusions))
    if academic_title:
        warnings.append("Academic role: check the required degree and research qualifications against your CV.")
    deadline = str(job.get("deadline") or "")
    expired = False
    if deadline:
        try:
            expired = date.fromisoformat(deadline[:10]) < (today or date.today())
            if expired:
                warnings.append("The stated application deadline has passed.")
        except ValueError:
            warnings.append("Deadline needs manual review: " + deadline)
    warnings.append("Check language requirements, work authorization and remote eligibility in the original vacancy.")
    if profile.get("work_authorization"):
        warnings.append("Your recorded work authorization: " + profile["work_authorization"])
    actual_norwegian = profile.get("languages", {}).get("Norwegian", "")
    search_norwegian = profile.get("search_languages", {}).get("Norwegian", "")
    if search_norwegian and actual_norwegian and search_norwegian != actual_norwegian:
        warnings.append(f"Search includes Norwegian {search_norwegian} roles; your stated level is {actual_norwegian}.")
    track = "target" if matched_roles else "horizon" if related_roles else "other"
    reasons = []
    if matched_roles:
        reasons.append("Target title: " + ", ".join(matched_roles))
    if related_roles and not matched_roles:
        reasons.append("Related to your target fields: " + ", ".join(related_roles))
    if matched_skills:
        reasons.append("Skill terms: " + ", ".join(matched_skills))
    return {"score": score, "method": "keyword_overlap", "matched_roles": matched_roles,
            "matched_skills": matched_skills, "matched_locations": matched_locations,
            "excluded_terms": exclusions, "expired": expired, "warnings": warnings,
            "track": track, "reasons": reasons, "related_roles": related_roles}


def preparation_brief(job: dict, profile: dict) -> str:
    """Produce a factual writing brief while the AI provider is unconfigured."""
    match = match_job(job, profile)
    evidence = profile.get("evidence", [])
    evidence_text = "\n".join("- " + fact for fact in evidence) or "No achievement examples supplied yet."
    return "\n\n".join([
        f"# Application preparation: {job.get('title', '')} — {job.get('company', '')}",
        "This is a preparation brief, not a generated cover letter or a submitted application.",
        f"Source: {job.get('source_url', '')}\nApply: {job.get('apply_url', '')}\nDeadline: {job.get('deadline', '') or 'Not provided'}",
        "## Candidate facts\n" + (profile.get("summary") or "No summary supplied yet."),
        "## Supplied experience examples\n" + evidence_text,
        "## Matching skill terms\n" + (", ".join(match["matched_skills"]) or "No matching terms identified."),
        "## Review before applying\n" + "\n".join("- " + warning for warning in match["warnings"]),
        "## Vacancy description\n" + str(job.get("description", "")),
    ]) + "\n"
