"""Explicit user-provided career facts and transparent preliminary matching."""

import hashlib
import json
import re
import unicodedata
from datetime import date
from functools import lru_cache
from pathlib import Path


LIST_FIELDS = ("target_roles", "related_roles", "skills", "preferred_locations", "excluded_keywords", "evidence")
TEXT_FIELDS = ("name", "summary", "work_authorization", "cv_text", "cover_letter_language")

# Equivalent job-title spellings, not an inference that the candidate has these
# qualifications. Unrelated disciplines must still be explicitly configured.
_ROLE_ALIASES = (
    ("data scientist", "dataviter", "datenwissenschaftler", "datenwissenschaftlerin", "data scientist in"),
    ("ai engineer", "artificial intelligence engineer", "engineer ai", "engineer artificial intelligence", "ki ingeniør", "ki utvikler",
     "ki entwickler", "ki ingenieur", "ki engineer", "ai developer", "ai software engineer", "ai інженер",
     "ai/ml engineer", "ai / ml engineer", "ai and ml engineer", "ai & ml engineer"),
    ("machine learning engineer", "ml engineer", "engineer machine learning", "machine learning software engineer", "maskinlæringsingeniør",
     "maskinlærings ingeniør", "machine learning ingenieur", "ml ingenieur", "machine learning developer", "ml developer",
     "ml інженер", "інженер з машинного навчання", "ai/ml engineer", "ai / ml engineer", "ai and ml engineer", "ai & ml engineer"),
    ("applied scientist", "applied ai scientist", "applied machine learning scientist"),
    ("data engineer", "dataingeniør", "data ingeniør", "dateningenieur", "інженер даних"),
    ("data analyst", "dataanalytiker", "dataanalytikar", "data analytiker", "datenanalyst", "datenanalystin", "аналітик даних"),
    ("mathematician", "matematiker", "matematikar", "mathematiker", "mathematikerin", "математик"),
    ("statistician", "statistiker", "statistikar", "statistikerin", "статистик"),
)
# Titles close to a target field: recommended as related roles, never claimed
# as the target itself. Each group applies only when the profile targets one
# of its fields, so an unrelated discipline is still never inferred.
_ROLE_NEIGHBOURS = (
    (("machine learning engineer", "ml engineer", "ai engineer", "artificial intelligence", "maskinlæring", "kunstig intelligens"),
     ("mlops engineer", "ml ops engineer", "deep learning engineer", "nlp engineer", "llm engineer", "computer vision engineer",
      "machine learning scientist", "machine learning researcher", "ml scientist", "ml researcher", "ai researcher",
      "ai research scientist", "research scientist machine learning", "genai engineer", "generative ai engineer",
      "ai specialist", "machine learning specialist", "ml platform engineer", "ai architect", "prompt engineer",
      "ki spezialist", "ki berater", "machine learning consultant", "ai consultant"),
     "Machine learning (close role)"),
    (("data scientist", "data science", "dataviter"),
     ("decision scientist", "analytics scientist", "product scientist", "research data scientist", "data science manager",
      "data science lead", "head of data science", "data science consultant", "data science specialist", "data science intern",
      "quantitative researcher", "machine learning scientist"),
     "Data science (close role)"),
    (("mathematician", "matematiker", "statistician"),
     ("quantitative analyst", "quantitative researcher", "quant researcher", "quant analyst", "actuary", "aktuar", "aktuarin",
      "biostatistician", "operations research", "optimization engineer"),
     "Mathematics (close role)"),
)
# A skill score counts at most this many skills, so a long skill list does not
# make every advert look like a weak fit.
_SKILL_SCORE_CAP = 8
# Your field, from your target roles. A title naming it ("Software Engineer,
# Applied AI") or an advert centred on it counts as a related role even when
# no target title matches. Acronyms match only as written, in capitals.
_FIELDS = (
    (("machine learning engineer", "ml engineer", "ai engineer", "artificial intelligence", "maskinlæring",
      "kunstig intelligens", "data scientist", "data science"),
     ("machine learning", "maskinlæring", "maschinelles lernen", "machine-learning", "deep learning", "dyp læring",
      "artificial intelligence", "kunstig intelligens", "künstliche intelligenz", "штучний інтелект", "машинне навчання",
      "generative ai", "large language model", "large language models", "natural language processing",
      "computer vision", "reinforcement learning", "neural network", "neural networks", "nevrale nettverk",
      "ai agents", "ai agent", "agentic", "retrieval-augmented", "pytorch", "tensorflow", "scikit-learn", "mlops"),
     ("AI", "KI", "ML", "LLM", "LLMs", "NLP", "RAG", "GenAI", "ШІ"), "machine learning and AI"),
    (("data scientist", "data science", "dataviter", "data analyst", "statistician", "quantitative analyst"),
     ("data science", "data scientist", "dataviter", "datenwissenschaft", "data analysis", "dataanalyse", "datenanalyse",
      "statistical modelling", "statistical modeling", "statistisk modellering", "predictive modelling",
      "predictive modeling", "statistics", "statistikk", "statistik", "статистика", "forecasting", "prognoser",
      "econometrics", "causal inference", "a/b testing", "experimentation", "bayesian", "analytics", "dataanalytiker",
      "аналіз даних", "time series"),
     (), "data science and statistics"),
    (("mathematician", "matematiker", "statistician"),
     ("mathematical modelling", "mathematical modeling", "matematisk modellering", "mathematik", "mathematics",
      "matematikk", "математика", "operations research", "numerical", "stochastic", "stokastisk", "quantitative",
      "kvantitativ", "actuarial", "aktuar", "optimization", "optimisation", "optimering"),
     (), "mathematics"),
)
# Words that make a title a hands-on technical role, and titles that are not
# one even when they mention AI or data (product, sales, legal, management…).
_TECHNICAL_TITLE = re.compile(
    r"engineer|ingeniør|ingenieur|scientist|forsker|forskar|research|utvikler|utviklar|developer|entwickler|programmer|programmerer|"
    r"analyst|analytiker|analytikar|specialist|spesialist|spezialist|architect|arkitekt|consultant|konsulent|berater|rådgiver|rådgjevar|"
    r"expert|ekspert|statistician|statistiker|statistikar|mathematician|matematiker|matematikar|modell?er|stipendiat|\bphd\b|postdoc|fellow|"
    r"werkstudent|trainee|graduate|intern\b|praktikant|інженер|розробник|аналітик|науковець|дослідник|"
    r"инженер|разработчик|аналитик", re.I)
_NON_TECHNICAL_TITLE = re.compile(
    r"product (?:manager|owner|lead|marketing)|project manager|program(?:me)? manager|\bsales\b|account (?:manager|executive)|"
    r"marketing|recruit|talent|\bhr\b|people partner|legal|jurist|lawyer|counsel|advokat|finance|controller|accountant|"
    r"regnskap|designer|\bdesign\b|customer success|support|community|communications|kommunikasjon|journalist|"
    r"\bmanager\b|director|\bhead of\b|\bvp\b|vice president|chief|leder\b|teacher|lærer|nurse|sykepleier", re.I)
# Skills whose words mean something else in many adverts ("PPO plan",
# "regression testing", a stray "R"): they count only with supporting context.
_SKILL_CONTEXT = {
    "ppo": ("reinforcement", "policy optimization"), "sac": ("reinforcement", "actor-critic", "actor critic"),
    "td3": ("reinforcement",), "a2c": ("reinforcement", "actor-critic", "actor critic"),
    "r": ("rstudio", "tidyverse", "cran", "ggplot", "statistic", "programming", "python", " sas", "spss"),
    "stan": ("bayesian", "probabilistic", "mcmc", "pymc"),
    "regression": ("model", "statistic", "machine learning", "linear", "logistic", "forecast"),
    "classification": ("model", "machine learning", "classifier", "supervised", "neural"),
    "optimization": ("mathematical", "numerical", "convex", "algorithm", "solver", "operations research", "model"),
    "clustering": ("model", "machine learning", "unsupervised", "k-means", "statistic"),
    "logging": ("software", "observability", "monitoring", "devops", "metrics"),
}
_ROLE_FIT = {"target": 1.0, "close": 0.8, "related": 0.7, "title_field": 0.6, "advert_field": 0.45}
_EXPERIENCE_FIT = {"no_conflict": 1.0, "review": 0.8, "stretch": 0.6, "mismatch": 0.1}


_ROLE_SEPARATORS = re.compile(r"[-‐‑‒–—_,:]+")


def _role_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    # Employers mix hyphens (including Unicode dashes), spaces and line breaks.
    # Keep +/# intact so language and specialist titles do not become broader.
    return " ".join(_ROLE_SEPARATORS.sub(" ", value).split())


@lru_cache(maxsize=1024)
def _role_terms(role: str) -> tuple[str, ...]:
    normalized = _role_text(role)
    return tuple(dict.fromkeys([normalized, *(alias for aliases in _ROLE_ALIASES
                              if normalized in aliases for alias in aliases)]))


def _role_matches_normalized(title: str, role: str, *, folded_title: str | None = None) -> bool:
    return any(contains_phrase(title, term, folded_text=folded_title) for term in _role_terms(role))


def role_matches(title: str, role: str) -> bool:
    """Match a configured title or an explicit equivalent, with word boundaries."""
    return _role_matches_normalized(_role_text(title), role)


# How pasted lists are read. Terms (skills, roles, places, keywords) are
# separated by new lines, commas, semicolons, "|", tabs or bullets; a comma
# inside brackets stays, so "Python (pandas, NumPy)" is one skill. Sentences
# (achievements, writing examples) contain commas, so only new lines and
# bullets separate them.
_TERM_SEPARATORS = "\n\r,;|\t\u2022\u00b7\u25cf\u25aa\u25e6\u2023\u2043"
_LINE_SEPARATORS = "\n\r\u2022\u25cf\u25aa\u25e6\u2023\u2043"
_LIST_MARKER = re.compile(r"^(?:[-*+\u2013\u2014]\s+|\(?\d{1,3}[.)]\s+|[a-z][.)]\s+(?=\S))", re.I)
TERM_FIELDS = ("target_roles", "related_roles", "skills", "preferred_locations", "excluded_keywords")
SENTENCE_FIELDS = ("evidence",)
# Proficiency written after a language name without a separator ("English C1").
_LEVEL = re.compile(r"(?:[ABC][12](?:\s*[-/+]\s*[ABC]?[12])?\+?|native|fluent|basic|beginner|intermediate|advanced|"
                    r"professional|conversational|business|working|morsm\u00e5l|flytende|muttersprache|\u0440\u0456\u0434\u043d\u0430)$", re.I)


def _split(text: str, separators: str) -> list[str]:
    parts, current, depth = [], [], 0
    for char in text:
        if char in "([{":
            depth += 1
        elif char in ")]}" and depth:
            depth -= 1
        if char in separators and (depth == 0 or char in "\n\r"):
            parts.append("".join(current))
            current, depth = [], 0 if char in "\n\r" else depth
            continue
        current.append(char)
    parts.append("".join(current))
    return parts


def _clean_items(values, separators: str, *, terms: bool) -> list[str]:
    if isinstance(values, str):
        values = [values]
    result, seen = [], set()
    for value in values:
        for part in _split(value, separators):
            item = _LIST_MARKER.sub("", " ".join(part.split()))
            if terms:
                item = item.rstrip(".:").strip()
            key = item.casefold()
            if item and key not in seen:
                seen.add(key)
                result.append(item)
    return result


def split_terms(value) -> list[str]:
    """Skills, roles, places or keywords, however they were pasted."""
    return _clean_items(value, _TERM_SEPARATORS, terms=True)


def split_sentences(value) -> list[str]:
    """Achievements or examples: one per line or bullet; commas stay."""
    return _clean_items(value, _LINE_SEPARATORS, terms=False)


def parse_languages(value) -> dict[str, str]:
    """Languages with levels: "English: C1", "English - C1", "English (C1)" or "English C1".

    Entries may be on separate lines or separated by commas or semicolons.
    """
    if isinstance(value, dict):
        entries = [f"{name}: {level}" for name, level in value.items()]
    elif isinstance(value, str):
        entries = _split(value, "\n\r,;")
    else:
        raise ValueError("Write languages as Language: level, for example English: C1.")
    languages = {}
    for entry in entries:
        entry = _LIST_MARKER.sub("", " ".join(str(entry).split()))
        if not entry:
            continue
        name = level = ""
        for pattern in (r"^(.+?)\s*[:=]\s*(.+)$", r"^(.+?)\s+[-\u2013\u2014]\s+(.+)$", r"^(.+?)\s*\((.+)\)$"):
            match = re.match(pattern, entry)
            if match:
                name, level = match.group(1).strip(), match.group(2).strip()
                break
        else:
            words = entry.rsplit(" ", 2)
            for size in (2, 1):
                tail = " ".join(words[-size:]) if len(words) > size else ""
                if tail and _LEVEL.match(tail):
                    name, level = entry[:-len(tail)].strip(" -:"), tail
                    break
        if not name or not level:
            raise ValueError(f"Add a level for \"{entry}\", for example {entry.split()[0]}: B2.")
        languages[name] = level
    return languages


def load_profile(path: str | Path) -> dict:
    return validate_profile(json.loads(Path(path).read_text(encoding="utf-8-sig")))


def validate_profile(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise ValueError("Profile must be a JSON object.")
    from .experience_matching import validate_experience_years
    profile["years_of_experience"] = validate_experience_years(profile.get("years_of_experience"))
    for key in LIST_FIELDS:
        value = profile.setdefault(key, [])
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError(f"Profile '{key}' must be a list of strings.")
        # Lists are read the same way however they were typed or pasted,
        # including profiles saved before this reading existed.
        profile[key] = split_sentences(value) if key in SENTENCE_FIELDS else split_terms(value)
    for key in TEXT_FIELDS:
        value = profile.setdefault(key, "English" if key == "cover_letter_language" else "")
        if not isinstance(value, str):
            raise ValueError(f"Profile '{key}' must be text.")
    for field in ("languages", "search_languages"):
        languages = profile.setdefault(field, {})
        if isinstance(languages, dict) and any(not isinstance(k, str) or not isinstance(v, str) for k, v in languages.items()):
            raise ValueError(f"Profile '{field}' must map language names to levels as text.")
        profile[field] = parse_languages(languages)
    from .countries import COUNTRIES, validate_countries, validate_country_preferences
    profile["target_countries"] = validate_countries(profile.get("target_countries", list(COUNTRIES)))
    profile["country_preferences"] = validate_country_preferences(profile.get("country_preferences", {}))
    from .application_answers import validate_writing_style
    profile["writing_style"] = validate_writing_style(profile.get("writing_style", {}))
    return profile


def profile_hash(profile: dict) -> str:
    return hashlib.sha256(json.dumps(profile, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


@lru_cache(maxsize=2048)
def _literal_pattern(phrase: str):
    return re.compile(re.escape(phrase), re.IGNORECASE)


def _search_hint(text: str) -> str:
    # Unicode IGNORECASE equates both Turkish I variants with ASCII I; plain
    # casefold alone would miss those matches. Other casefold expansions can
    # only create a candidate here: the exact regex still decides the result.
    return text.replace("İ", "i").replace("ı", "i").casefold()


@lru_cache(maxsize=2048)
def _phrase_hint(phrase: str) -> str:
    return _search_hint(phrase)


def contains_phrase(text: str, phrase: str, *, folded_text: str | None = None) -> bool:
    """Match literal terms without treating e.g. 'Java' as a match for 'JavaScript'."""
    phrase = phrase.strip()
    if not phrase:
        return False
    if folded_text is not None and _phrase_hint(phrase) not in folded_text:
        return False
    # A leading lookbehind makes Python's regex engine visit every character
    # for every skill. Find the literal first, then check its boundaries. Keep
    # IGNORECASE's Unicode semantics and check overlapping literal occurrences.
    pattern = _literal_pattern(phrase)
    start = 0
    while match := pattern.search(text, start):
        left, right = match.span()
        if (not left or not (text[left - 1].isalnum() or text[left - 1] == "_")) and (
                right == len(text) or not (text[right].isalnum() or text[right] == "_")):
            return True
        start = left + 1
    return False


@lru_cache(maxsize=256)
def _active_neighbours(targets: tuple[str, ...]) -> tuple[tuple[tuple[str, ...], str], ...]:
    folded = {term for role in targets for term in _role_terms(role)}
    return tuple((titles, label) for fields, titles, label in _ROLE_NEIGHBOURS if folded.intersection(fields))


@lru_cache(maxsize=256)
def _screen(phrases: tuple[str, ...]):
    """One pattern finding any phrase's folded hint, or None for no phrases.

    contains_phrase can only succeed where its hint occurs in the folded
    text, so titles this pattern misses skip the per-phrase checks. With many
    role spellings, those checks were most of a large list's matching time.
    """
    hints = {_phrase_hint(phrase.strip()) for phrase in phrases if phrase.strip()}
    return re.compile("|".join(map(re.escape, sorted(hints, key=len, reverse=True)))) if hints else None


@lru_cache(maxsize=256)
def _role_phrases(roles: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(term for role in roles for term in _role_terms(role))


def _neighbour_labels(normalized_title: str, folded_title: str, profile: dict) -> list[str]:
    groups = _active_neighbours(tuple(profile.get("target_roles", [])))
    screen = _screen(tuple(title for titles, _ in groups for title in titles))
    if screen is None or not screen.search(folded_title):
        return []
    return [label for titles, label in groups
            if any(contains_phrase(normalized_title, title, folded_text=folded_title) for title in titles)]


@lru_cache(maxsize=256)
def _active_fields(targets: tuple[str, ...]) -> tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...]:
    folded = {term for role in targets for term in _role_terms(role)}
    return tuple((phrases, acronyms, label) for fields, phrases, acronyms, label in _FIELDS if folded.intersection(fields))


@lru_cache(maxsize=512)
def _acronym_pattern(acronym: str):
    return re.compile(r"(?<![\w])" + re.escape(acronym) + r"(?![\w])")


def _field_terms(text: str, folded: str, fields) -> list[str]:
    found = []
    for phrases, acronyms, _ in fields:
        found += [phrase for phrase in phrases if _phrase_hint(phrase) in folded and contains_phrase(text, phrase, folded_text=folded)]
        found += [acronym for acronym in acronyms if acronym in text and _acronym_pattern(acronym).search(text)]
    return list(dict.fromkeys(found))


def _skill_found(skill: str, body: str, folded: str) -> bool:
    if not contains_phrase(body, skill, folded_text=folded):
        return False
    context = _SKILL_CONTEXT.get(skill.strip().casefold())
    if context is None:
        return True
    if skill.strip().casefold() == "r":
        # A single capital R beside other tools, not a stray initial or "R&D".
        if not re.search(r"(?<![\w&])R(?![\w&])", body):
            return False
    return any(term in folded for term in context)


def match_job(job: dict, profile: dict, today: date | None = None, *, recommendations_only: bool = False) -> dict | None:
    """Return matching evidence, or skip an unrelated title when requested.

    Recommendation searches use the same role evaluation as full discovery,
    but avoid scanning skills and experience in adverts they will discard.
    """
    from .countries import job_countries, profile_for_job
    original_profile = profile
    profile = profile_for_job(profile, job, isolate=False)
    title = str(job.get("title", ""))
    normalized_title = _role_text(title)
    folded_title = _search_hint(normalized_title)
    location = str(job.get("location", ""))
    body = "\n".join(str(job.get(key, "")) for key in ("title", "company", "location", "description"))
    configured = []
    screen = _screen(_role_phrases(tuple(profile.get("target_roles", [])) + tuple(profile.get("related_roles", []))))
    possible_role = screen is not None and screen.search(folded_title) is not None
    matched_roles = [x for x in profile.get("target_roles", []) if possible_role and _role_matches_normalized(normalized_title, x, folded_title=folded_title)]
    broad_topics = {"artificial intelligence", "data science", "maskinlæring", "kunstig intelligens"}
    technical_title = re.search(r"engineer|ingeniør|ingenieur|scientist|forsker|research|utvikler|developer|entwickler|"
                                r"matemati|mathemati|specialist|spesialist|spezialist|architect|arkitekt|analyst|analytiker", title, re.IGNORECASE)
    matched_roles = [role for role in matched_roles if role.casefold() not in broad_topics or technical_title]
    related_roles = [x for x in profile.get("related_roles", []) if possible_role and _role_matches_normalized(normalized_title, x, folded_title=folded_title)]
    research_labels = {"phd", "stipendiat", "research assistant", "research scientist", "research engineer"}
    academic_title = re.search(r"\bphd\b|stipendiat|postdoc|postdoktor", title, re.IGNORECASE)
    research_overlap = False
    if academic_title or any(role.casefold() in research_labels for role in related_roles):
        folded_body = body.casefold()
        research_overlap = any(term in folded_body for term in ("mathemat", "matemat", "statistic", "statistikk", "data science", "machine learning", "maskinlæring", "quantitative", "reinforcement learning", "kunstig intelligens"))
    # Broad academic titles alone should not recommend an unrelated discipline.
    related_roles = [role for role in related_roles if role.casefold() not in research_labels or research_overlap]
    if academic_title and research_overlap:
        related_roles = list(dict.fromkeys([*related_roles, "Research in a related field"]))
        matched_roles = []
    close_roles = _neighbour_labels(normalized_title, folded_title, profile) if not matched_roles else []
    # Your field in the title, or an advert centred on it, for hands-on roles.
    fields = _active_fields(tuple(profile.get("target_roles", [])))
    field_in_title, field_in_advert = [], []
    if fields and not matched_roles and not related_roles and not close_roles and _TECHNICAL_TITLE.search(title) \
            and not _NON_TECHNICAL_TITLE.search(title):
        field_in_title = _field_terms(title, _search_hint(title), fields)
        if not field_in_title:
            description = str(job.get("description") or "")
            field_in_advert = _field_terms(description, _search_hint(description), fields)
            if len(field_in_advert) < 3:
                field_in_advert = []
    if matched_roles:
        role_kind = "target"
    elif close_roles:
        role_kind = "close"
    elif related_roles:
        role_kind = "related"
    elif field_in_title:
        role_kind = "title_field"
    elif field_in_advert:
        role_kind = "advert_field"
    else:
        role_kind = None
    if close_roles:
        related_roles = list(dict.fromkeys([*related_roles, *close_roles]))
    if field_in_title:
        related_roles.append("Your field in the title: " + ", ".join(field_in_title[:3]))
    elif field_in_advert:
        related_roles.append("The advert centres on your field: " + ", ".join(field_in_advert[:4]))
    if recommendations_only and not (matched_roles or related_roles):
        return None
    search_body = _search_hint(body)
    matched_skills = [x for x in profile.get("skills", []) if _skill_found(x, body, search_body)]
    matched_locations = [x for x in profile.get("preferred_locations", []) if contains_phrase(location, x)]
    exclusions = [x for x in profile.get("excluded_keywords", []) if contains_phrase(body, x, folded_text=search_body)]
    warnings = []
    from .experience_matching import assess_experience
    experience = assess_experience(job, profile.get("years_of_experience"))
    warnings.extend(experience["reasons"])
    # Role fit carries half the score, so related roles and adverts in your
    # field rank well instead of scoring at most 41%.
    if profile.get("target_roles") or profile.get("related_roles"):
        configured.append((50, _ROLE_FIT.get(role_kind, 0.0)))
    if profile.get("skills"):
        configured.append((30, min(1.0, len(matched_skills) / min(len(profile["skills"]), _SKILL_SCORE_CAP))))
    if experience["candidate_years"] is not None:
        configured.append((20, _EXPERIENCE_FIT.get(experience["status"], 0.8)))
    if profile.get("preferred_locations"):
        configured.append((10, 1.0 if matched_locations else 0.0))
    score = round(100 * sum(weight * ratio for weight, ratio in configured) / sum(weight for weight, _ in configured)) if configured else None
    countries = job_countries(job)
    if not countries:
        warnings.append("Country is unknown; a remote label does not establish where you can work.")
    elif not set(countries).intersection(original_profile.get("target_countries", countries)):
        warnings.append("This location is outside your selected search countries.")
    context = profile.get("country_context", {})
    for key, label in (("remote_preference", "Work arrangement preference"), ("relocation_preference", "Relocation preference")):
        if context.get(key):
            warnings.append(label + ": " + context[key] + "; check the original posting.")
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
    if role_kind in {"title_field", "advert_field"}:
        reasons.append(related_roles[-1])
    elif related_roles and not matched_roles:
        reasons.append("Related to your target fields: " + ", ".join(related_roles))
    if matched_skills:
        reasons.append("Skill terms: " + ", ".join(matched_skills))
    return {"score": score, "method": "role_skill_experience_fit", "role_fit": role_kind or "none", "matched_roles": matched_roles,
            "matched_skills": matched_skills, "matched_locations": matched_locations,
            "excluded_terms": exclusions, "expired": expired, "warnings": warnings,
            "track": track, "reasons": reasons, "related_roles": related_roles,
            "experience": experience}


def preparation_brief(job: dict, profile: dict) -> str:
    """Produce a factual writing brief while the AI provider is unconfigured."""
    match = match_job(job, profile)
    evidence = profile.get("evidence", [])
    from .experience_matching import experience_fact
    candidate_facts = "\n".join(filter(None, [profile.get("summary"), experience_fact(profile.get("years_of_experience"))]))
    evidence_text = "\n".join("- " + fact for fact in evidence) or "No achievement examples supplied yet."
    return "\n\n".join([
        f"# Application preparation: {job.get('title', '')} — {job.get('company', '')}",
        "This is a preparation brief, not a generated cover letter or a submitted application.",
        f"Source: {job.get('source_url', '')}\nApply: {job.get('apply_url', '')}\nDeadline: {job.get('deadline', '') or 'Not provided'}",
        "## Candidate facts\n" + (candidate_facts or "No summary supplied yet."),
        "## Supplied experience examples\n" + evidence_text,
        "## Matching skill terms\n" + (", ".join(match["matched_skills"]) or "No matching terms identified."),
        "## Review before applying\n" + "\n".join("- " + warning for warning in match["warnings"]),
        "## Vacancy description\n" + str(job.get("description", "")),
    ]) + "\n"
