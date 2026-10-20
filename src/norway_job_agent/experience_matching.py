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
_YEAR_UNIT_TEXT = r"(?:years?|yrs?|år(?:s)?|jahre?n?|jährig(?:e|er|en)?|рок(?:и|ів)|рік)\b"
# These are necessary substrings of every unit above, not a separate grammar.
# Casefold additionally covers IGNORECASE's dotted/dotless-I spellings.
_YEAR_HINTS = ("year", "yr", "år", "jahr", "jähr", "рок", "рік")
_YEARS = re.compile(rf"(?<![\w\d])(?P<low>{_NUMBER})(?:\s*\(\s*(?P<parenthetical>{_NUMBER})\s*\))?(?:\s*(?:[-–—]|to|bis|til)\s*(?P<high>{_NUMBER}))?\s*(?P<plus>\+|or more|or greater)?[\s-]*{_YEAR_UNIT_TEXT}", re.I)
_EXPERIENCE = re.compile(r"\b(?:experience|experienced|erfaring|berufserfahrung|erfahrung|досвід\w*)\b", re.I)
_AFTER_YEARS = re.compile(r"^.{0,70}?\b(?:experience|erfaring|berufserfahrung|erfahrung|досвід\w*)\b|^\s*(?:of\s+)?(?:working|building|developing|using|with\s+\w|(?:in\s+)?(?:data|software|engineering|machine learning|analytics|research|development)\b)", re.I)
_PREFERRED = re.compile(r"\b(?:preferred|preferably|ideally|desirable|desired|nice[- ]to[- ]have|bonus|advantage|a plus|ønskelig|gjerne|wünschenswert|von vorteil|бажано)\b", re.I)
_REQUIRED = re.compile(r"\b(?:required|require|requires|must|minimum|min\.?|at least|more than|essential|need|needs|you have|you bring|you will have|we expect|mindestens|erforderlich|voraussetzung|minst|må ha|kreves|не менше|від)\b", re.I)
_ALTERNATIVE = re.compile(r"\b(?:equivalent|equivalency|in lieu|alternatively|instead|combination of|gleichwertig|tilsvarende|еквівалент\w*)\b", re.I)
_DEGREE = re.compile(r"\b(?:degree|bachelor|master|ph\.?d|bsc|msc|bs|ms|b\.s|m\.s|studium|abschluss|ступінь)\b", re.I)
_COMPANY_HISTORY = re.compile(r"\b(?:our (?:company|team|business|firm|founder)|we have|we bring|founded|established|company has|company with)\b|\bour\s+(?:over\s+|more than\s+)?\d+\s+years", re.I)
_NOT_REQUIRED = re.compile(r"\b(?:not required|not essential|no minimum|not necessary|no prior|no previous|not a requirement)\b", re.I)
_NO_EXPERIENCE = re.compile(r"\b(?:no (?:prior |previous |professional )?experience (?:is )?(?:required|necessary|needed)|keine berufserfahrung (?:erforderlich|notwendig))\b", re.I)
_APPLICANT_SUBJECT = re.compile(r"\b(?:you|candidates?|applicants?)\b", re.I)
_PREFERRED_HEADINGS = {"preferred", "preferred qualifications", "preferred experience", "nice to have", "nice-to-have", "bonus", "desired qualifications", "desirable", "wünschenswert", "ønskelige kvalifikasjoner"}
_REQUIRED_HEADINGS = {"required", "requirements", "minimum qualifications", "required qualifications", "qualifications", "what you bring", "what you will bring", "what we need", "must have", "must-have", "krav", "kvalifikasjoner", "anforderungen", "вимоги"}
_CLAUSE_SEPARATOR = re.compile(r"[\r\n;.!?]")


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
_PERSIST_TABLE = "requirements_v1"
_persist_path: Path | None = None
_pending: dict[bytes, tuple[dict, ...]] = {}


def attach_cache(path: Path) -> None:
    """Load requirements saved by earlier runs; later results save to this file."""
    global _persist_path
    path = Path(path)
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
    for clause in _clauses(str(description or "")):
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
        alternatives = bool(_ALTERNATIVE.search(clause) or any(
            len(matches) > 1 or joiner.start() > matches[0].end() and _DEGREE.search(clause[joiner.end():])
            for joiner in alternative_joiners))
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
            if alternatives or _NOT_REQUIRED.search(local) or re.search(r"\b(?:up to|at most|maximum|bis zu)\b", before, re.I):
                kind = "alternative"
            elif _PREFERRED.search(local) or preferred_section:
                kind = "preferred"
            elif _REQUIRED.search(local) or match.group("plus") or match.start() <= 5 or prior_experience:
                kind = "required"
            else:
                # A prose mention without a clear obligation stays reviewable.
                kind = "alternative"
            item = {"years": lower, "evidence": clause[:600], "kind": kind}
            if item not in result:
                result.append(item)
        experience_heading = False
    return result[:50]


_SENIORITY = (
    ("leadership", re.compile(r"\b(?:director|head of|vice president|vp|chief|manager|team lead|team leader|engineering lead|керівник|директор|начальник|abteilungsleiter|teamleiter|avdelingsleder)\b", re.I), 5),
    ("principal", re.compile(r"\b(?:principal|distinguished|fellow|chief scientist)\b", re.I), 8),
    ("staff", re.compile(r"\b(?:staff|старший провідний)\b", re.I), 7),
    ("senior", re.compile(r"\b(?:senior|sr\.?|lead|старший|провідний|senioringeniør|seniorforsker)\b", re.I), 5),
    ("entry", re.compile(r"\b(?:junior|jr\.?|entry[- ]level|graduate|trainee|intern|internship|молодший|стажер|стажист)\b", re.I), None),
    ("mid", re.compile(r"\b(?:mid[- ]level|middle|intermediate|medior)\b", re.I), None),
)


def assess_experience(job, candidate_years=None):
    candidate_years = validate_experience_years(candidate_years)
    title = str(job.get("title") or "")
    # Here "senior" describes a customer population, not the vacancy level.
    title = re.sub(r"\bsenior\s+(?:citizens?|care|living|housing)\b", "", title, flags=re.I)
    found = [(level, match, guideline) for level, pattern, guideline in _SENIORITY if (match := pattern.search(title))]
    level, title_match, guideline = found[0] if found else ("unknown", None, None)
    mixed_level = (len(found) > 1 and any(item[0] in {"entry", "mid"} for item in found)) or bool(re.search(r"\bmember of (?:the )?technical staff\b", title, re.I))
    requirements = experience_requirements(str(job.get("description") or ""))
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
