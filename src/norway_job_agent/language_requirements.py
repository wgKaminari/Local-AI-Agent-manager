"""Which languages an advert asks for, at what level, and whether it insists.

Adverts state languages in many ways: "Fluent English required", "German is
a plus", "Gode norskkunnskaper, muntlig og skriftlig", "Sehr gute Deutsch- und
Englischkenntnisse", "Англійська на рівні B2 буде перевагою". This module
reads English, Norwegian, German and Ukrainian adverts and returns two lists:

* required - languages the advert asks for without softening;
* preferred - languages marked as a plus, an advantage, desirable, ideal.

Each item carries the level as written ("fluent", "B2", "sehr gut"), an
approximate CEFR level for comparison, and the sentence it came from. A
language counts only where the text is about knowing it (a level, "speak",
"written", "Kenntnisse", "språk", "мова" ...), so "Deutsche Bahn", "our
Norwegian customers" or "the Ukrainian market" are not requirements. Courses
offered, the language to apply in, and benefit sections are ignored.

compare_languages() then checks each requirement against the levels in your
profile: meets, check (borderline or level not stated) or gap.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections import OrderedDict
from contextlib import closing
from pathlib import Path

# Canonical name, then the stems that name it in EN / NO / DE / UK adverts.
# German "deutsch" excludes "Deutschland"; English "Polish" is matched only
# capitalised, so "polish your skills" is not a language.
_LANGUAGES = (
    ("English", r"(?i:english|engelsk\w*|englisch\w*|англійськ\w*|англ\.)"),
    ("Norwegian", r"(?i:norwegian|norsk\w*|norwegisch\w*|норвезьк\w*)"),
    ("Scandinavian", r"(?i:scandinavian|skandinavisk\w*|nordic languages?)"),
    ("German", r"(?i:german\b|deutsch(?!land)\w*|tysk\w*|німецьк\w*)"),
    ("Ukrainian", r"(?i:ukrainian|ukrainsk\w*|ukrainisch\w*|українськ\w*)"),
    ("Swedish", r"(?i:swedish|svensk\w*|schwedisch\w*|шведськ\w*)"),
    ("Danish", r"(?i:danish|dansk\w*|dänisch\w*|данськ\w*)"),
    ("Finnish", r"(?i:finnish|finsk\w*|finnisch\w*|фінськ\w*)"),
    ("French", r"(?i:french|fransk\w*|französisch\w*|французьк\w*)"),
    ("Spanish", r"(?i:spanish|spansk\w*|spanisch\w*|іспанськ\w*)"),
    ("Italian", r"(?i:italian|italiensk\w*|italienisch\w*|італійськ\w*)"),
    ("Portuguese", r"(?i:portuguese|portugisisk\w*|portugiesisch\w*|португальськ\w*)"),
    ("Dutch", r"(?i:dutch|nederlandsk\w*|niederländisch\w*|нідерландськ\w*|голландськ\w*)"),
    ("Polish", r"(?:Polish|POLISH|(?i:polsk\w*|polnisch\w*|польськ\w*))"),
    ("Russian", r"(?i:russian|russisk\w*|russisch\w*|російськ\w*)"),
    ("Czech", r"(?i:czech|tsjekkisk\w*|tschechisch\w*|чеськ\w*)"),
    ("Turkish", r"(?i:turkish|tyrkisk\w*|türkisch\w*|турецьк\w*)"),
    ("Arabic", r"(?i:arabic|arabisk\w*|arabisch\w*|арабськ\w*)"),
    ("Japanese", r"(?i:japanese|japansk\w*|japanisch\w*|японськ\w*)"),
    ("Chinese", r"(?i:chinese|mandarin|cantonese|kinesisk\w*|chinesisch\w*|китайськ\w*)"),
    ("Korean", r"(?i:korean|koreansk\w*|koreanisch\w*|корейськ\w*)"),
    ("Hindi", r"(?i:hindi)"),
)
_LANGUAGE = re.compile("|".join(f"(?P<l{index}>(?<![\\w-]){pattern})" for index, (_, pattern) in enumerate(_LANGUAGES)))
_NAMES = [name for name, _ in _LANGUAGES]
# Cheap pre-check: adverts naming no language skip the full scan.
_HINTS = ("engl", "engel", "англ", "norw", "norsk", "норвез", "scandinav", "skandinav", "nordic", "german", "deutsch",
          "tysk", "німец", "ukrain", "україн", "swed", "svensk", "schwed", "шведськ", "danish", "dansk", "dänisch",
          "finn", "finsk", "french", "fransk", "franz", "француз", "spani", "spansk", "іспан", "ital", "італ",
          "portug", "португ", "dutch", "nederl", "niederl", "голланд", "нідерл", "polish", "polsk", "polni", "польськ",
          "russ", "росій", "czech", "tsjekk", "tschech", "чеськ", "turk", "tyrk", "türk", "турец", "arab", "араб",
          "japan", "япон", "chines", "mandarin", "canton", "kines", "китай", "korea", "корей", "hindi")

# Words showing the text is about knowing a language.
_CONTEXT = re.compile(
    r"\b(?:languages?|speak\w*|spoken|written|writing|verbal|oral|communicat\w*|proficien\w*|fluen\w*|native|"
    r"mother tongue|skills?|knowledge|command|level|read\w*|understand\w*|"
    r"sprache\w*|sprach\w*|kenntnis\w*|mündlich\w*|schriftlich\w*|wort und schrift|beherrsch\w*|"
    r"språk\w*|muntlig\w*|skriftlig\w*|snakke\w*|skrive\w*|behersk\w*|kunnskap\w*|morsmål\w*|"
    r"мов\w*|володі\w*|розмовн\w*|письмов\w*|рів\w*|знан\w*)\b|kenntnisse|kunnskaper|kenntnissen", re.I)
# Compounds carry the context in the language word itself.
_COMPOUND = re.compile(r"(?:kenntnis\w*|kunnskap\w*|sprachig\w*|språklig\w*|speaking|speaker)$", re.I)

# (pattern, CEFR rank 1-7, label). Rank 7 is native. Most specific first.
_RANKS = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6, "native": 7}
_CEFR_OF = {rank: name for name, rank in _RANKS.items()}
_LEVELS = (
    (re.compile(r"(?<![\w])(?P<cefr>[ABC][12])\+?(?:\s*[-–/]\s*[ABC][12]\+?)?(?![\w])"), None, None),
    (re.compile(r"upper[- ]?intermediate|вище середнього", re.I), 4, "upper-intermediate"),
    (re.compile(r"pre[- ]?intermediate", re.I), 2, "pre-intermediate"),
    (re.compile(r"\bintermediate|середн\w*", re.I), 3, "intermediate"),
    (re.compile(r"(?<!not )(?<!non-)(?<!non )(?<!nicht )(?<!ikke )\b(?:native|mother tongue|first language|bilingual|muttersprach\w*|zweisprachig\w*|morsmål\w*|"
                r"tospråklig\w*|рідн\w*|носі\w*)", re.I), 7, "native"),
    (re.compile(r"\b(?:fluent\w*|fluency|fließend\w*|fliessend\w*|flytende|вільн\w*)", re.I), 5, "fluent"),
    (re.compile(r"\b(?:verhandlungssicher\w*|business[- ]fluent|advanced|proficient|fortgeschritten\w*|"
                r"просунут\w*|високий)", re.I), 5, "advanced"),
    (re.compile(r"\b(?:excellent|very good|outstanding|sehr gut\w*|hervorragend\w*|ausgezeichnet\w*|exzellent\w*|"
                r"svært god\w*|meget god\w*|utmerket\w*|veldig god\w*|відмінн\w*|чудов\w*)", re.I), 5, "very good"),
    (re.compile(r"\b(?:business|professional working|working proficiency|professional)\b", re.I), 4, "business"),
    (re.compile(r"\b(?:good|solid|strong|gute?[nrs]?|gut|god|gode|godt|добр\w*|впевнен\w*|sicher\w*)\b", re.I), 4, "good"),
    (re.compile(r"\b(?:conversational|working knowledge|kommunikativ\w*|розмовн\w*)", re.I), 3, "conversational"),
    (re.compile(r"\bworking\b", re.I), 3, "working"),
    (re.compile(r"\b(?:basic|elementary|beginner|grundkenntnis\w*|grunnleggende|базов\w*|початков\w*)", re.I), 2, "basic"),
)

# Softened statements. Without one, a stated language is required.
_PREFERRED = re.compile(
    r"\b(?:preferred|preferably|ideally|desirable|desired|nice[- ]to[- ]have|bonus|advantage\w*|advantageous|a plus|"
    r"is a plus|(?:big|significant|strong|huge|great|real|definite|nice|additional) plus|beneficial|helpful|appreciated|welcome\w*|optional|asset|"
    r"von vorteil|vorteilhaft|wünschenswert|idealerweise|ein plus|gerne|gern|"
    r"en fordel|fordel\w*|ønskelig\w*|gjerne|positivt|"
    r"перевагою|перевага|плюсом|плюс|бажано|бажан\w*)\b", re.I)
_REQUIRED_MARKER = re.compile(
    r"\b(?:required|requirement|must|mandatory|essential|necessary|need\w*|erforderlich|voraussetzung|zwingend|"
    r"notwendig|unabdingbar|krav|kreves|påkrevd|nødvendig|må|обов'?язков\w*|необхідн\w*|потрібн\w*)\b", re.I)
_PREFERRED_HEADING = re.compile(r"\b(?:nice[- ]to[- ]have|bonus|preferred|desirable|plus|wünschenswert|von vorteil|"
                                r"fordel|ønskelig|буде плюсом|буде перевагою|перевагою|бажано)\b", re.I)
_REQUIRED_HEADING = re.compile(r"\b(?:requirements?|qualifications?|what you bring|what we need|must[- ]have|about you|"
                               r"your profile|who you are|you have|anforderungen|ihr profil|dein profil|qualifikation\w*|"
                               r"krav|kvalifikasjoner|vi ser etter|du har|вимоги|ми очікуємо|очікування|ти маєш|що потрібно)\b", re.I)
_OFFER_HEADING = re.compile(r"\b(?:we offer|what we offer|benefits|perks|why join|wir bieten|was wir bieten|unser angebot|"
                            r"vi tilbyr|vi kan tilby|hva vi tilbyr|ми пропонуємо|пропонуємо|бонуси|переваги роботи)\b", re.I)
# Mentions that are not language requirements.
_NOT_REQUIREMENT = re.compile(
    r"\b(?:courses?|classes|lessons?|tuition|training|kurs\w*|unterricht|sprachkurs\w*|курс\w*|заняття|уроки)\b|"
    r"\b(?:application|applications|cv|resume|résumé|cover letter|søknad\w*|bewerbung\w*|unterlagen|резюме)\b"
    r".{0,40}\b(?:in|på|auf|англійською)\b|"
    r"\b(?:documentation|documents|dokumentasjon|vitnemål|diplomas?|certificates?|zeugnisse)\b.{0,30}\b(?:in|på|auf)\b|"
    r"\b(?:educational|education|school|utdannings|bildungs)\s*system|"
    r"\b(?:citizen\w*|nationality|security clearance|clearance|staatsangehörig\w*|staatsbürger\w*|statsborger\w*|"
    r"громадян\w*)\b|"
    r"\b(?:find|more|other|search)\b.{0,25}\bjobs\b|"
    r"\bdeutsche\s+(?:bahn|telekom|bank|börse|post|welle|rentenversicherung)\b", re.I)
_PARENTHESES = re.compile(r"\([^()]*\)")
# "Applicants who do not master a Scandinavian language must ..." names a
# language to describe who must do something else.
_NEGATED = re.compile(r"\b(?:not|don't|do not|does not|doesn't|without|ikke|uten|nicht|ohne|kein\w*|не|без)\s+(?:\w+\s+){0,3}$", re.I)
_AFTER_LEVEL = re.compile(r"(?<=\b[ABC][12])\.\s+(?=[A-ZÆØÅÄÖÜА-ЯІЇЄҐ])")
# "whose preferred language is Korean" names a language, it does not soften it.
_PREFERRED_LANGUAGE = re.compile(r"\bpreferred\s+language\b", re.I)
_OR = re.compile(r"^\s*(?:,\s*)?(?:or|oder|eller|або|чи|and/or|und/oder|og/eller|/)\s*$", re.I)
_LIST_GAP = re.compile(r"^\s*(?:,|-?\s*(?:and|und|og|та|і|й|&)|-)\s*$", re.I)
_SEGMENT = re.compile(r",|;|\b(?:but|while|whereas|however|aber|während|men|mens|але|проте)\b", re.I)

_CACHE: OrderedDict[bytes, dict] = OrderedDict()
_CACHE_LIMIT = 40000
_cache_lock = threading.Lock()
# Results persist between app starts beside the experience cache, so a new
# profile or app start does not read every stored advert again. Bump the
# version whenever extraction rules change, so old results retire.
_PERSIST_TABLE = "languages_v1"
_persist_path: Path | None = None
_pending: dict[bytes, dict] = {}


def attach_cache(path: Path) -> None:
    """Load results saved by earlier runs; later results save to this file."""
    global _persist_path
    path = Path(path)
    with _cache_lock:
        if _persist_path == path:
            return
    try:
        with closing(sqlite3.connect(path, timeout=10)) as database, database:
            database.execute(f"CREATE TABLE IF NOT EXISTS {_PERSIST_TABLE} (digest BLOB PRIMARY KEY, value TEXT NOT NULL)")
            rows = database.execute(f"SELECT digest, value FROM {_PERSIST_TABLE}").fetchall()
    except sqlite3.Error:
        return  # A cache problem must never stop matching.
    with _cache_lock:
        for digest, value in rows[-_CACHE_LIMIT:]:
            try:
                loaded = json.loads(value)
            except (ValueError, TypeError):
                continue
            if isinstance(loaded, dict):
                _CACHE.setdefault(digest, loaded)
        _persist_path = path
        _pending.clear()


def flush_cache() -> None:
    with _cache_lock:
        path, pending = _persist_path, dict(_pending)
        _pending.clear()
    if path is None or not pending:
        return
    try:
        with closing(sqlite3.connect(path, timeout=10)) as database, database:
            database.executemany(f"INSERT OR REPLACE INTO {_PERSIST_TABLE} (digest, value) VALUES (?, ?)",
                                 [(digest, json.dumps(value, ensure_ascii=False)) for digest, value in pending.items()])
    except sqlite3.Error:
        pass


def _name_of(match) -> str:
    return _NAMES[int(match.lastgroup[1:])]


def _level(text: str):
    """The first level wording in text: (rank, label, cefr) or None."""
    best = None
    for pattern, rank, label in _LEVELS:
        found = pattern.search(text)
        if found and (best is None or found.start() < best[0]):
            if rank is None:
                cefr = found.group("cefr").upper()
                best = (found.start(), _RANKS[cefr], cefr, cefr)
            else:
                best = (found.start(), rank, label, _CEFR_OF[rank] if rank < 7 else "native")
    return None if best is None else best[1:]


def _nearest(text: str, *, last: bool):
    """(distance from the mention, level) for the level closest to it in text."""
    best = None
    for pattern, rank, label in _LEVELS:
        for found in pattern.finditer(text):
            distance = len(text) - found.end() if last else found.start()
            if rank is None:
                cefr = found.group("cefr").upper()
                level = (_RANKS[cefr], cefr, cefr)
            else:
                level = (rank, label, _CEFR_OF[rank] if rank < 7 else "native")
            if best is None or distance < best[0] or (distance == best[0] and rank is None):
                best = (distance, level)
    return best


def _segment(clause: str, start: int, end: int) -> tuple[int, int]:
    """Bounds of the comma/'but'-delimited part of a clause around one mention."""
    left = 0
    for separator in _SEGMENT.finditer(clause, 0, start):
        left = separator.end()
    after = _SEGMENT.search(clause, end)
    return left, after.start() if after else len(clause)


def _heading(clause: str) -> str | None:
    words = clause.split()
    if not words or len(words) > 8 or len(clause) > 70:
        return None
    if _OFFER_HEADING.search(clause):
        return "offer"
    if clause.rstrip().endswith(":") or len(words) <= 4:
        if _PREFERRED_HEADING.search(clause):
            return "preferred"
        if _REQUIRED_HEADING.search(clause):
            return "required"
        if clause.rstrip().endswith(":"):
            return "other"
    return None


def _group_mentions(clause: str, mentions: list) -> list[list]:
    """Join mentions listed with 'or' ("Norwegian or English") into one choice."""
    groups, current, choice = [], [], False
    for mention in mentions:
        if current:
            between = clause[current[-1].end():mention.start()]
            if _OR.match(between):
                current.append(mention)
                choice = True
                continue
            if _LIST_GAP.match(between) and between.strip() in {",", ""}:
                current.append(mention)  # "English, German or French": decided by the last joiner.
                continue
            groups.extend([current] if choice else [[item] for item in current])
            current, choice = [], False
        current.append(mention)
    if current:
        groups.extend([current] if choice else [[item] for item in current])
    return groups


def language_requirements(description: str) -> dict:
    """Required and preferred languages stated in one advert (cached by text)."""
    text = str(description or "")
    key = hashlib.blake2b(text.encode("utf-8", "surrogatepass"), digest_size=16).digest()
    with _cache_lock:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return {kind: [dict(item) for item in items] for kind, items in cached.items()}
    result = _extract(text)
    with _cache_lock:
        _CACHE[key] = result
        if _persist_path is not None:
            _pending[key] = result
        while len(_CACHE) > _CACHE_LIMIT:
            _CACHE.popitem(last=False)
    return {kind: [dict(item) for item in items] for kind, items in result.items()}


def _extract(text: str) -> dict:
    found: dict[tuple, dict] = {}
    folded = text.casefold()
    if not any(hint in folded for hint in _HINTS):
        return {"required": [], "preferred": []}
    from .experience_matching import _clauses
    section = None
    language_heading = False
    # The shared splitter keeps "1.5" whole, which also keeps "German C1.
    # Swedish is a plus" together; split after a level as well.
    pieces = [piece for clause in _clauses(text) for piece in _AFTER_LEVEL.split(clause) if piece.strip()]
    for clause in pieces:
        lowered = clause.casefold()
        has_language = any(hint in lowered for hint in _HINTS)
        heading = _heading(clause) if len(clause) <= 70 else None
        if heading is None and not has_language:
            continue
        if heading is not None:
            section = heading
            language_heading = bool(re.search(r"\b(?:languages?|sprachen?|språk|мови|мова)\b", clause, re.I))
            if not _LANGUAGE.search(clause) or clause.rstrip().endswith(":"):
                continue
        mentions = list(_LANGUAGE.finditer(clause))
        if not mentions or section == "offer":
            continue
        groups = _group_mentions(clause, mentions)
        previous_level = None
        for index, group in enumerate(groups):
            start, end = group[0].start(), group[-1].end()
            # A level belongs to the nearest language without crossing another.
            span_left = groups[index - 1][-1].end() if index else 0
            span_right = groups[index + 1][0].start() if index + 1 < len(groups) else len(clause)
            left, right = _segment(clause, start, end)
            segment = clause[left:right]
            window = clause[max(0, start - 60):min(len(clause), end + 70)]
            word = group[-1].group(0)
            if _NOT_REQUIREMENT.search(window) or _NEGATED.search(clause[max(span_left, start - 40):start]):
                continue
            # A short statement with a modal word is about the language itself:
            # "German is a plus", "Norwegian would be helpful", or a bullet in a
            # requirements list.
            short = len(segment.split()) <= 7 and (_PREFERRED.search(segment) or _REQUIRED_MARKER.search(segment))
            bullet = section in {"required", "preferred"} and len(clause.split()) <= 8
            if not (_CONTEXT.search(window) or _COMPOUND.search(word) or language_heading or short or bullet
                    or _level(clause[left:right])):
                continue
            # The level nearest the mention within its own part of the
            # sentence: after it first, then before it.
            low, high = max(left, span_left), min(right, span_right)
            before = max(low, start - 90)
            while before > low and not clause[before - 1].isspace():
                before -= 1  # Never cut a level word such as "Upper-Intermediate".
            after_level = _nearest(clause[end:min(high, end + 60)], last=False)
            before_level = _nearest(clause[before:start], last=True)
            if after_level and before_level:
                level = after_level[1] if after_level[0] <= before_level[0] else before_level[1]
            else:
                level = (after_level or before_level or (None, None))[1]
            if level is None and index and previous_level and _LIST_GAP.match(clause[span_left:start]):
                level = previous_level  # "Sehr gute Deutsch- und Englischkenntnisse"
            previous_level = level
            # "B2+ (C1 preferred)" is a B2 requirement: a parenthetical
            # preference refines the level, not the requirement itself.
            plain = _PREFERRED_LANGUAGE.sub(" ", _PARENTHESES.sub(" ", segment))
            preferred = bool(_PREFERRED.search(plain)) or (
                len(mentions) == len(group) and _PREFERRED.search(_PARENTHESES.sub(" ", clause))
                and not _REQUIRED_MARKER.search(plain) and not _PREFERRED.search(segment))
            if section == "preferred" and not _REQUIRED_MARKER.search(segment):
                preferred = True
            names = list(dict.fromkeys(_name_of(mention) for mention in group))
            item = {"language": " or ".join(names), "any_of": names,
                    "level": level[1] if level else "", "cefr": level[2] if level else "",
                    "rank": level[0] if level else 0, "evidence": clause[:400]}
            _merge(found, "preferred" if preferred else "required", item)
    return _finish(found)


def _merge(found: dict, kind: str, item: dict) -> None:
    key = tuple(item["any_of"])
    current = found.get(key)
    if current is None:
        found[key] = {**item, "kind": kind}
        return
    # A requirement anywhere beats a preference; the higher stated level wins
    # within the same strength.
    if current["kind"] == "preferred" and kind == "required":
        found[key] = {**item, "kind": kind, **({} if item["rank"] else {k: current[k] for k in ("level", "cefr", "rank")})}
    elif current["kind"] == kind and item["rank"] > current["rank"]:
        current.update({k: item[k] for k in ("level", "cefr", "rank", "evidence")})


def _finish(found: dict) -> dict:
    # A single language also named inside a stated choice ("Norwegian or
    # English") is covered by that choice unless stated separately as required.
    result = {"required": [], "preferred": []}
    for item in found.values():
        kind = item.pop("kind")
        result[kind].append(item)
    required_singles = {item["any_of"][0] for item in result["required"] if len(item["any_of"]) == 1}
    result["preferred"] = [item for item in result["preferred"]
                           if not (len(item["any_of"]) == 1 and item["any_of"][0] in required_singles)]
    return result


def site_languages(items) -> dict:
    """Languages a job site lists as structured data (always the minimum)."""
    result = {"required": [], "preferred": []}
    for entry in items if isinstance(items, list) else []:
        if not isinstance(entry, dict) or entry.get("language") not in _NAMES:
            continue
        level = _level(str(entry.get("level") or ""))
        result["required"].append({"language": entry["language"], "any_of": [entry["language"]],
                                   "level": str(entry.get("level") or "")[:60], "cefr": level[2] if level else "",
                                   "rank": level[0] if level else 0,
                                   "evidence": str(entry.get("evidence") or "")[:400]})
    return result


def combine(text_result: dict, site_result: dict) -> dict:
    """Site-listed languages first; the advert adds what the site did not list."""
    found: dict[tuple, dict] = {}
    for kind in ("required", "preferred"):
        for item in site_result.get(kind, []):
            _merge(found, kind, dict(item))
    for kind in ("required", "preferred"):
        for item in text_result.get(kind, []):
            key = tuple(item["any_of"])
            if key in found and found[key]["rank"] and found[key]["kind"] == "required":
                continue  # The site's stated minimum stands.
            _merge(found, kind, dict(item))
    return _finish(found)


def _profile_ranks(value: str) -> tuple[int, int] | None:
    """Lowest and highest level in a profile entry such as 'B2-C1' or 'Native'."""
    text = str(value or "")
    ranks = [_RANKS[match.upper()] for match in re.findall(r"(?<![\w])([ABC][12])(?![\w])", text, re.I)]
    level = _level(text)
    if level:
        ranks.append(level[0])
    return (min(ranks), max(ranks)) if ranks else None


def _canonical(name: str) -> str | None:
    match = _LANGUAGE.search(str(name or ""))
    return _name_of(match) if match else None


def compare_languages(requirements: dict, profile_languages: dict) -> dict:
    """Mark each stated language as meets, check or gap against the profile.

    A profile range such as 'B2-C1' meets what its lower end reaches and is
    'check' where only its upper end does. A requirement without a level is
    met from B2, otherwise needs a check. Scandinavian is met by Norwegian,
    Swedish or Danish.
    """
    known: dict[str, tuple[int, int]] = {}
    for name, level in (profile_languages or {}).items():
        canonical = _canonical(name)
        ranks = _profile_ranks(level)
        if canonical and ranks:
            known[canonical] = max(known.get(canonical, ranks), ranks)
    for scandinavian in ("Norwegian", "Swedish", "Danish"):
        if scandinavian in known:
            known["Scandinavian"] = max(known.get("Scandinavian", known[scandinavian]), known[scandinavian])
    result = {"required": [], "preferred": [], "gaps": []}
    for kind in ("required", "preferred"):
        for item in requirements.get(kind, []):
            options = [known[name] for name in item["any_of"] if name in known]
            need = item["rank"] or 4
            if not options:
                status = "gap"
            elif any(low >= need for low, _ in options):
                status = "meets"
            elif any(high >= need for _, high in options) or not item["rank"]:
                status = "check"
            else:
                status = "gap"
            entry = {**item, "status": status,
                     "yours": ", ".join(str((profile_languages or {}).get(name) or "") for name in item["any_of"]
                                        if name in (profile_languages or {})) or ""}
            result[kind].append(entry)
            if kind == "required" and status == "gap":
                result["gaps"].append(entry)
    return result
