"""Transparent experience screening; title heuristics are not eligibility claims.

Only the user's explicit experience value is used. A CV's employment dates,
skills and search preferences are never converted into years automatically.
"""
from __future__ import annotations

import hashlib
import html
import json
import math
import re
import sqlite3
import threading
import unicodedata
from collections import OrderedDict
from contextlib import closing
from pathlib import Path


def validate_experience_years(value):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 60 or not math.isfinite(value):
        raise ValueError("Years of experience must be a number from 0 to 60, such as 2.5, or blank when unknown.")
    return value


def experience_fact(value):
    years = validate_experience_years(value)
    return "" if years is None else f"{years:g} years of total professional experience."


_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
          "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "fifteen": 15,
          "twenty": 20, "ett": 1, "en": 1, "to": 2, "tre": 3, "fire": 4,
          "fem": 5, "ein": 1, "eine": 1, "zwei": 2, "drei": 3,
          "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "zehn": 10}
_NUMBER = r"(?:\d{1,2}(?:[.,]\d+)?|" + "|".join(_WORDS) + r")"
_YEAR_UNIT_TEXT = r"(?:years?|yrs?|år(?:s)?|jahre?n?|jährig(?:e|er|en)?|рок(?:и|ів|у)|рік)\b"
# These are necessary substrings of every unit above, not a separate grammar.
# Casefold additionally covers IGNORECASE's dotted/dotless-I spellings.
_YEAR_HINTS = ("year", "yr", "år", "jahr", "jähr", "рок", "рік")
_YEARS = re.compile(rf"(?<![\w\d])(?P<low>{_NUMBER})(?:\s*\(\s*(?P<parenthetical>{_NUMBER})\s*\))?(?:\s*(?:[-–—]|to|bis|til)\s*(?P<high>{_NUMBER}))?\s*(?P<plus>\+|or more|or greater)?[\s-]*{_YEAR_UNIT_TEXT}", re.I)
_EXPERIENCE = re.compile(r"\b(?:experience|experienced|erfaring|berufserfahrung|erfahrung|досвід\w*)\b", re.I)
_AFTER_YEARS = re.compile(r"^.{0,70}?\b(?:experience|erfaring|berufserfahrung|erfahrung|досвід\w*)\b|^\s*(?:of\s+)?(?:working|building|developing|using|with\s+\w|(?:in\s+)?(?:data|software|engineering|machine learning|analytics|research|development)\b)", re.I)
_PREFERRED = re.compile(r"\b(?:preferred|preferably|ideally|desirable|desired|nice[- ]to[- ]have|bonus|advantage|a plus|ønskelig|gjerne|en fordel|wünschenswert|von vorteil|idealerweise|бажано|перевагою|плюсом)\b", re.I)
_REQUIRED = re.compile(r"\b(?:required|require|requires|must|minimum|min\.?|at least|more than|essential|need|needs|you have|you bring|you will have|we expect|mindestens|erforderlich|voraussetzung|minst|må ha|kreves|не менше|щонайменше|від)\b", re.I)
_ALTERNATIVE = re.compile(r"\b(?:equivalent|equivalency|in lieu|alternatively|instead|combination of|gleichwertig|tilsvarende|еквівалент\w*|"
                          r"(?:experience|erfaring|erfahrung)\s+(?:or|eller|oder)\s+(?:education|studies|study|utdanning|ausbildung|studium))\b", re.I)
# A line opening with "OR 5+ years ..." is a second route to the previous one.
# Degree words that cannot be a product name ("MS Teams" is not a degree).
_DEGREE_TRACK = re.compile(r"\b(?:degree|bachelor\w*|master'?s|masters|ph\.?\s?d|doctorate|bsc|msc|b\.\s?sc?\.?|m\.\s?sc?\.?|"
                           r"diploma|abschluss|studium|ступінь)\b", re.I)
_OR_ROUTE = re.compile(r"^\s*(?:or|oder|eller|або|alternatively)\b", re.I)
_DEGREE = re.compile(r"\b(?:degree|bachelor|master|ph\.?d|bsc|msc|bs|ms|b\.s|m\.s|studium|abschluss|ступінь)\b", re.I)
_COMPANY_HISTORY = re.compile(r"\b(?:our (?:company|team|business|firm|founder)|we have|we bring|founded|established|company has|company with)\b|\bour\s+(?:over\s+|more than\s+)?\d+\s+years", re.I)
_NOT_REQUIRED = re.compile(r"\b(?:not required|not essential|no minimum|not necessary|no prior|no previous|not a requirement)\b", re.I)
_NO_EXPERIENCE = re.compile(r"\b(?:no (?:prior |previous |professional )?experience (?:is )?(?:required|necessary|needed)|keine berufserfahrung (?:erforderlich|notwendig))\b", re.I)
_APPLICANT_SUBJECT = re.compile(r"\b(?:you|candidates?|applicants?)\b", re.I)
_PREFERRED_HEADINGS = {"preferred", "preferred qualifications", "preferred experience", "nice to have", "nice-to-have", "bonus", "desired qualifications", "desirable", "wünschenswert", "ønskelige kvalifikasjoner"}
_REQUIRED_HEADINGS = {"required", "requirements", "minimum qualifications", "required qualifications", "qualifications", "what you bring", "what you will bring", "what we need", "must have", "must-have", "krav", "kvalifikasjoner", "anforderungen", "вимоги"}
_CLAUSE_SEPARATOR = re.compile(r"[\r\n;.!?]")
# "5+ years in site reliability", "3+ years as a Business Analyst", "4+ роки на
# позиції", "5+ Jahre E-Mail-Marketing": the field follows the years directly,
# without the word "experience".
_FIELD_AFTER = re.compile(
    r"^\s*(?:\([^)]*\)\s*)?(?:of|in|as|at|on|within|with|doing|across|leading|у|в|на|з|als|im|i|som|med|"
    r"erfahrung|erfaring|досвід\w*)\b|^\s*(?:\([^)]*\)\s*)?\w+ing\b|"
    r"^\s*(?:hands-on|professional|industry|relevant|commercial|practical|production|full[- ]time)\b", re.I)
# Minimum wording directly before the number: "від 1 року", "at least 3 years".
_FROM_BEFORE = re.compile(r"(?:\bfrom|\bвід|\bminimum(?:\s+of)?|\bmin\.?|\bat least|\bmindestens|\bminst|"
                          r"\bне менше|\bщонайменше)\s*$", re.I)
_APPLICANT_BEFORE = re.compile(r"\b(?:you have|you bring|you've|with|have|has|bring|track record of|proven)\s*$", re.I)
# Years of a company, a contract, a benefit or an age, not of a candidate.
_NOT_APPLICANT_AFTER = re.compile(
    r"^\s*(?:\+\s*)?(?:of\s+)?(?:old|ago|тому|alt|gammel|employment|service|tenure|heritage|history|impact|audio|age|"
    r"operation|existence|experience in (?:the|our) (?:market|industry))\b|"
    r"^\s*(?:on the market|in business|на ринку|у бізнесі|в бізнесі|на українському ринку)", re.I)
_HISTORY_BEFORE = re.compile(r"(?:\b(?:for (?:over|more than|the past|the last)|over the (?:past|last)|in the (?:past|last)|"
                             r"since|seit|siden|понад|вже|уже|протягом|already|over|for)|(?:^|\s)за)\s*$", re.I)
# "5+ years of data work, of which at least 1.5 years as a lead": the second
# number narrows the first, it is not an alternative route.
_NARROWING = re.compile(r"\b(?:of which|including|incl\.?|with at least|at least|davon|hvorav|herav|з них|з яких|"
                        r"зокрема|включно|у тому числі|в тому числі)\b|(?:^|\s)(?:з них|з яких)", re.I)
_OR_WORD = re.compile(r"\b(?:or|oder|eller|або|чи)\b", re.I)
_ASIDE_DASH = re.compile(r"\s[–—-]\s")


def _without_asides(text: str, keep: str) -> str:
    """Text without parentheses and dash asides that do not contain keep."""
    text = re.sub(r"\([^()]*(?:\)|$)", lambda m: m.group(0) if keep in m.group(0) else " ", text)
    parts = _ASIDE_DASH.split(text)
    return next((part for part in parts if keep in part), text)
# Experience asked for without a number.
_UNSPECIFIED = re.compile(
    r"\b(?:several|many|multiple|a few|a couple of|extensive|significant|substantial|considerable)\s+years?\b(?!\s+ago)|"
    r"\b(?:flere|noen|mange)\s+års\b|\b(?:lang|langvarig)\s+(?:\w+\s+)?erfaring\b|"
    r"\b(?:mehrjährig\w*|langjährig\w*|mehrere\s+jahre|einige\s+jahre|mehreren\s+jahren)\b|"
    r"\berste\w*\s+(?:berufs|praxis)?erfahrung\b|"
    r"\b(?:кілька|декілька|багато)\s+рок\w*|\bбагаторічн\w*|\bкількарічн\w*", re.I)


def _local_context(clause, matches, index):
    """Separate comma-delimited requirements without mixing their modalities.

    For example, the preference in '2 years required, 5 years preferred' must
    not erase the explicit two-year requirement or turn five into a minimum.
    """
    match = matches[index]
    left = matches[index - 1].end() if index else 0
    right = matches[index + 1].start() if index + 1 < len(matches) else len(clause)
    before, after = clause[left:match.start()], clause[match.end():right]
    separators = list(re.finditer(r",|\b(?:but|however|whereas)\b", before, re.I))
    if separators:
        left += separators[-1].end()
    separator = re.search(r",|\b(?:but|however|whereas)\b", after, re.I)
    if separator:
        right = match.end() + separator.start()
    return clause[left:right]


def _number(value):
    return float(value.replace(",", ".")) if value[0].isdigit() else _WORDS[value.casefold()]


def _clauses(value):
    # Preserve boundaries in raw HTML if a collector did not already strip it.
    if "<" in value:
        value = re.sub(r"</?(?:p|li|div|br|h[1-6])\b[^>]*>", "\n", value, flags=re.I)
        value = re.sub(r"<[^>]*>", " ", value)
    value = unicodedata.normalize("NFKC", html.unescape(value))[:100000]
    # Degree abbreviations must not split a qualification alternative into
    # separate, apparently mandatory requirements.
    folded = value.casefold()
    if any(abbreviation in folded for abbreviation in ("ph.", "b.", "m.")):
        value = re.sub(r"\b(?:Ph\.D|B\.S|M\.S|B\.Sc|M\.Sc)\.?", lambda m: m.group().replace(".", "\x00"), value, flags=re.I)
    # Search delimiters directly instead of evaluating a lookbehind at every
    # character of every advert. Preserve decimal numbers and the original
    # punctuation rules, including Unicode decimal digits.
    parts, start = [], 0
    for match in _CLAUSE_SEPARATOR.finditer(value):
        pos = match.start()
        if value[pos] in ".!?" and ((pos and value[pos - 1].isdecimal()) or
                (pos + 1 < len(value) and value[pos + 1].isdecimal())):
            continue
        part = value[start:pos]
        if part.strip():
            parts.append(" ".join(part.replace("\x00", ".").split()).strip(" •*-"))
        start = pos + 1
    if value[start:].strip():
        parts.append(" ".join(value[start:].replace("\x00", ".").split()).strip(" •*-"))
    return parts


# A stated minimum this many years above yours is a stretch, not a mismatch.
STRETCH_YEARS = 1.0
_REQUIREMENT_CACHE: OrderedDict[bytes, tuple[dict, ...]] = OrderedDict()
_REQUIREMENT_CACHE_LIMIT = 20000
_requirement_cache_lock = threading.Lock()
# Results also persist between app starts: parsing every stored advert again
# made the first list after launch several times slower than later ones.
# Bump the version whenever extraction rules change, so old results retire.
_PERSIST_TABLE = "requirements_v3"
_persist_path: Path | None = None
_pending: dict[bytes, tuple[dict, ...]] = {}


def attach_cache(path: Path) -> None:
    """Load requirements saved by earlier runs; later results save to this file.

    Language requirements share the same cache file.
    """
    global _persist_path
    path = Path(path)
    from .language_requirements import attach_cache as attach_languages
    attach_languages(path)
    with _requirement_cache_lock:
        if _persist_path == path:
            return
    try:
        with closing(sqlite3.connect(path, timeout=10)) as database, database:
            database.execute(f"CREATE TABLE IF NOT EXISTS {_PERSIST_TABLE} (digest BLOB PRIMARY KEY, value TEXT NOT NULL)")
            rows = database.execute(f"SELECT digest, value FROM {_PERSIST_TABLE}").fetchall()
    except sqlite3.Error:
        return  # A cache problem must never stop matching.
    with _requirement_cache_lock:
        for digest, value in rows[-_REQUIREMENT_CACHE_LIMIT:]:
            try:
                _REQUIREMENT_CACHE.setdefault(digest, tuple(json.loads(value)))
            except (ValueError, TypeError):
                continue
        _persist_path = path
        _pending.clear()


def flush_cache() -> None:
    """Save requirements computed since the last flush."""
    from .language_requirements import flush_cache as flush_languages
    flush_languages()
    with _requirement_cache_lock:
        path, pending = _persist_path, dict(_pending)
        _pending.clear()
    if path is None or not pending:
        return
    try:
        with closing(sqlite3.connect(path, timeout=10)) as database, database:
            database.executemany(f"INSERT OR REPLACE INTO {_PERSIST_TABLE} (digest, value) VALUES (?, ?)",
                                 [(digest, json.dumps(value)) for digest, value in pending.items()])
    except sqlite3.Error:
        pass


def experience_requirements(description):
    """Extract clear experience quantities with their original supporting clause.

    Desired quantities and educational alternatives remain review items, never
    hard rejection thresholds. Unrelated numbers (company age, salary, degree
    duration, project duration) do not become experience requirements.

    Extraction depends only on the advert text, so results are memoized by a
    digest of that text. Every listing request rematches all stored adverts;
    without the cache this parse dominated each request. A changed advert has
    a different digest, and callers always receive fresh copies.
    """
    text = str(description or "")
    key = hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=16).digest()
    with _requirement_cache_lock:
        cached = _REQUIREMENT_CACHE.get(key)
        if cached is not None:
            _REQUIREMENT_CACHE.move_to_end(key)
            return [dict(item) for item in cached]
    result = _extract_experience_requirements(text)
    with _requirement_cache_lock:
        _REQUIREMENT_CACHE[key] = tuple(dict(item) for item in result)
        if _persist_path is not None:
            _pending[key] = _REQUIREMENT_CACHE[key]
        while len(_REQUIREMENT_CACHE) > _REQUIREMENT_CACHE_LIMIT:
            _REQUIREMENT_CACHE.popitem(last=False)
    return result


def _extract_experience_requirements(description):
    result = []
    experience_heading = False
    preferred_section = False
    chain: list[dict] = []  # Items of consecutive lines joined by "OR".
    plain_route = False
    for clause in _clauses(str(description or "")):
        clause_start = len(result)
        heading = clause.strip(" :").casefold()
        if heading in _PREFERRED_HEADINGS:
            preferred_section = True
            experience_heading = bool(_EXPERIENCE.search(clause))
            continue
        if heading in _REQUIRED_HEADINGS:
            preferred_section = False
            experience_heading = False
            continue
        if _NO_EXPERIENCE.search(clause):
            result.append({"years": 0, "evidence": clause[:600], "kind": "required"})
            continue
        # Most advert prose has no experience quantity. Checking its unit
        # first avoids running a large multilingual number expression across
        # every word, while sharing the exact unit grammar with the extractor.
        folded = clause.casefold().replace("ı", "i").replace("i\u0307", "i")
        matches = list(_YEARS.finditer(clause)) if any(unit in folded for unit in _YEAR_HINTS) else []
        if not matches:
            experience_heading = bool(len(clause) < 60 and _EXPERIENCE.search(clause) and not re.search(r"[.!?]", clause))
            continue
        # 'BS or MS plus five years' keeps the same experience requirement.
        # 'BS plus five years or MS' is an alternative route needing review.
        alternative_joiners = list(re.finditer(r"\b(?:or|oder|eller|або)\b|/", clause, re.I))
        # Two quantities are alternatives only when "or" joins them ("3 years
        # with Python or 2 with Go"), not when an "or" elsewhere lists fields
        # or the second narrows the first.
        between = [re.sub(r"\([^()]*\)", " ", clause[matches[k].end():matches[k + 1].start()])
                   for k in range(len(matches) - 1)]
        alternatives = bool(any(
            _OR_WORD.search(gap) and not _NARROWING.search(gap) for gap in between) or any(
            joiner.start() > matches[0].end() and _DEGREE.search(clause[joiner.end():]) for joiner in alternative_joiners))
        for index, match in enumerate(matches):
            local_end = matches[index + 1].start() if index + 1 < len(matches) else len(clause)
            before = clause[max(0, match.start() - 80):match.start()]
            after = clause[match.end():min(local_end, match.end() + 100)]
            # An employer's age or accumulated experience is not an applicant
            # requirement, even if the same sentence also says "we need".
            history = list(_COMPANY_HISTORY.finditer(before))
            if (history and not _APPLICANT_SUBJECT.search(before[history[-1].end():])) or re.search(r"\bour\s+(?:(?:over|more than)\s+)?$", before, re.I):
                continue
            # The number must be attached to professional experience, rather
            # than another fact somewhere in the same vacancy.
            direct = _AFTER_YEARS.search(after)
            prior_experience = _EXPERIENCE.search(before[-50:]) or experience_heading
            if not direct and not prior_experience and not _NOT_APPLICANT_AFTER.match(after) \
                    and not _HISTORY_BEFORE.search(before):
                from_before = _FROM_BEFORE.search(before)
                led = match.group("plus") or match.start() <= 5 or from_before or _APPLICANT_BEFORE.search(before)
                # A list item opening with "5+ years ..." states a requirement
                # whatever field follows ("8+ yrs AppSec"); otherwise the field
                # must follow the years, or experience must lead the sentence.
                if (match.group("plus") and match.start() <= 5) or (led and _FIELD_AFTER.match(after)) or \
                        (from_before and _EXPERIENCE.search(clause[:match.start()])) or (match.group("plus") and from_before):
                    direct = True
            if (not direct and not prior_experience) or re.match(r"^\s*(?:of\s+)?(?:old|ago|degree|contract|warranty|study|studies|(?:bachelor(?:'s)?|master(?:'s)?)\s+degree)\b", after, re.I):
                continue
            if re.match(r"^\s*[-–—]?\s*(?:projects?|programs?|programmes?|roadmaps?|strateg(?:y|ies)|plans?)\b", after, re.I):
                continue
            local = _local_context(clause, matches, index)
            lower = _number(match.group("low"))
            if match.group("parenthetical") and _number(match.group("parenthetical")) != lower:
                # Conflicting spellings such as "five (2)" are ambiguous.
                result.append({"years": lower, "evidence": clause[:600], "kind": "alternative"})
                continue
            if match.group("high"):
                lower = min(lower, _number(match.group("high")))
            if lower > 60:
                continue
            # "or equivalent" applies within a normal sentence; in run-on text
            # (a list whose line breaks were lost) only near the years, since
            # "Snowflake eller tilsvarende plattformer" is about tools.
            nearby = clause if len(clause) <= 250 else clause[max(0, match.start() - 100):min(len(clause), match.end() + 100)]
            if alternatives or _ALTERNATIVE.search(nearby) or _NOT_REQUIRED.search(local) or re.search(r"\b(?:up to|at most|maximum|bis zu)\b", before, re.I):
                kind = "alternative"
            elif _PREFERRED.search(_without_asides(local, match.group(0))) or preferred_section:
                kind = "preferred"
            elif _REQUIRED.search(local) or match.group("plus") or match.start() <= 5 or prior_experience:
                kind = "required"
            else:
                # A prose mention without a clear obligation stays reviewable.
                kind = "alternative"
            item = {"years": lower, "evidence": clause[:600], "kind": kind}
            if item not in result:
                result.append(item)
        added = result[clause_start:]
        if _OR_ROUTE.match(clause):
            chain.extend(added)
            # Degree tracks only ("8 years with a Bachelor's / or 5 with a
            # Master's") keep the Bachelor track as stated: the route a
            # Bachelor's holder must meet. A route without a degree ("OR 10+
            # years") makes every route in the chain an alternative.
            plain_route = plain_route or bool(added and not _DEGREE_TRACK.search(clause))
            if plain_route:
                for item in chain:
                    if item["kind"] == "required":
                        item["kind"] = "alternative"
        elif heading not in _PREFERRED_HEADINGS | _REQUIRED_HEADINGS:
            chain, plain_route = list(added), False
        experience_heading = False
    if not any(item["kind"] in {"required", "preferred"} for item in result):
        # No number: keep up to two plain statements such as "flere års
        # erfaring" or "mehrjährige Berufserfahrung" for review.
        for clause in _clauses(str(description or "")):
            if len(result) >= 2 or not _UNSPECIFIED.search(clause):
                continue
            if _EXPERIENCE.search(clause) or re.search(r"\byears?\s+(?:of|in|as)\b|\bjahre\b|\bрок\w*\s+досвід", clause, re.I):
                if not _COMPANY_HISTORY.search(clause):
                    result.append({"years": None, "evidence": clause[:600], "kind": "unspecified"})
    return result[:50]


_SENIORITY = (
    ("leadership", re.compile(r"\b(?:director|head of|vice president|vp|chief|manager|team lead|team leader|engineering lead|керівник|директор|начальник|abteilungsleiter|teamleiter|avdelingsleder)\b", re.I), 5),
    ("principal", re.compile(r"\b(?:principal|distinguished|fellow|chief scientist)\b", re.I), 8),
    ("staff", re.compile(r"\b(?:staff|старший провідний)\b", re.I), 7),
    ("senior", re.compile(r"\b(?:senior|sr\.?|lead|старший|провідний|senioringeniør|seniorforsker)\b", re.I), 5),
    ("entry", re.compile(r"\b(?:junior|jr\.?|entry[- ]level|graduate|trainee|intern|internship|молодший|стажер|стажист)\b", re.I), None),
    ("mid", re.compile(r"\b(?:mid[- ]level|middle|intermediate|medior)\b", re.I), None),
)


def site_requirements(job) -> dict:
    """Requirements the job site states as data (Djinni, level-filtered feeds)."""
    raw = job.get("raw_json")
    if isinstance(raw, str):
        if "site_requirements" not in raw:
            return {}
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    facts = raw.get("site_requirements") if isinstance(raw, dict) else None
    return facts if isinstance(facts, dict) else {}


def assess_experience(job, candidate_years=None):
    candidate_years = validate_experience_years(candidate_years)
    title = str(job.get("title") or "")
    # Here "senior" describes a customer population, not the vacancy level.
    title = re.sub(r"\bsenior\s+(?:citizens?|care|living|housing)\b", "", title, flags=re.I)
    found = [(level, match, guideline) for level, pattern, guideline in _SENIORITY if (match := pattern.search(title))]
    level, title_match, guideline = found[0] if found else ("unknown", None, None)
    mixed_level = (len(found) > 1 and any(item[0] in {"entry", "mid"} for item in found)) or bool(re.search(r"\bmember of (?:the )?technical staff\b", title, re.I))
    requirements = experience_requirements(str(job.get("description") or ""))
    facts = site_requirements(job)
    stated = facts.get("experience_years")
    if isinstance(stated, (int, float)) and not isinstance(stated, bool) and 0 <= stated <= 40:
        # The employer's own entry in the site's experience field.
        label = facts.get("experience_label") or (f"{stated:g} years of experience" if stated else "no experience")
        requirements = [{"years": float(stated), "evidence": f"{facts.get('source') or 'The job site'} lists: {label}",
                         "kind": "required"},
                        *(item for item in requirements if item["kind"] != "unspecified")]
    required = [item["years"] for item in requirements if item["kind"] == "required"]
    minimum = max(required) if required else None
    reasons, mismatch, stretch = [], False, False
    if candidate_years is None:
        reasons.append("Add your years of professional experience to screen experience requirements; nothing was inferred from your CV.")
    elif minimum is not None and candidate_years < minimum and minimum - candidate_years <= STRETCH_YEARS:
        # A small gap is commonly worth applying for when the rest fits.
        stretch = True
        reasons.append(f"The posting asks for {minimum:g} years; you recorded {candidate_years:g}. A stretch of "
                       f"{minimum - candidate_years:g} year{'' if minimum - candidate_years == 1 else 's'}: worth applying if the rest fits.")
    elif minimum is not None and candidate_years < minimum:
        mismatch = True
        reasons.append(f"The posting asks for at least {minimum:g} years of experience; you recorded {candidate_years:g}.")
    elif minimum is not None:
        reasons.append(f"Your {candidate_years:g} total years do not fall below the stated {minimum:g}-year minimum. Check the required field and skills separately.")
    elif unspecified := [item for item in requirements if item["kind"] == "unspecified"]:
        reasons.append(f"The advert asks for experience without a number: \u201c{unspecified[0]['evidence'][:160]}\u201d.")
    else:
        reasons.append("No unambiguous minimum experience was found; check the original requirements.")
    if title_match and guideline is not None:
        if mixed_level:
            reasons.append("The title includes more than one experience level; review which opening applies to you.")
        elif candidate_years is not None and candidate_years < guideline and (minimum is None or (candidate_years < minimum and not stretch)):
            mismatch = True
            reasons.append(f"The title signals {level} scope ('{title_match.group(0)}'), usually beyond {candidate_years:g} years. This is a title-screening guideline, not an employer-stated minimum.")
        else:
            reasons.append(f"The {level} title needs a separate scope and responsibility check; total years alone do not establish that level.")
    if any(item["kind"] == "preferred" for item in requirements):
        reasons.append("Preferred experience is shown for review and does not create a hard exclusion.")
    if any(item["kind"] == "alternative" for item in requirements):
        reasons.append("Alternative or ambiguous experience requirements need review; equivalent qualifications were not inferred.")
    status = "mismatch" if mismatch else "stretch" if stretch else "no_conflict" if candidate_years is not None and minimum is not None and not mixed_level and not any(item["kind"] == "alternative" for item in requirements) and guideline is None else "review"
    return {"status": status, "candidate_years": candidate_years, "minimum_years": minimum,
            "seniority": level, "title_experience_guideline": guideline,
            "requirements": requirements, "reasons": reasons}
