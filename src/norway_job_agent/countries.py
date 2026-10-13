"""Country labels and conservative location matching, never eligibility inference."""

from copy import deepcopy
import re

COUNTRIES = {"NO": "Norway", "US": "USA", "DE": "Germany", "UA": "Ukraine"}
_COUNTRY_NAMES = {
    "NO": ("Norway", "Norge", "NOR"),
    "US": ("United States of America", "United States", "USA", "U.S.A.", "U.S."),
    "DE": ("Germany", "Deutschland", "DEU"),
    "UA": ("Ukraine", "Україна", "Украина", "UKR"),
}
_US_STATE_SUFFIX = re.compile(
    r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)"
    r"(?:\s+\d{5}(?:-\d{4})?)?(?=\s*(?:$|[,(/]|[-–]\s*(?:Remote|Hybrid|On.?site)))"
)
# Prevent city-name collisions when a location explicitly names a country
# outside the supported markets. This list is not a worldwide geocoder.
_OTHER_COUNTRY_NAMES = (
    "Canada", "United Kingdom", "UK", "England", "Scotland", "Wales", "Ireland",
    "France", "Spain", "Italy", "Poland", "Netherlands", "Sweden", "Denmark",
    "Finland", "Switzerland", "Austria", "Belgium", "Portugal", "Czechia",
    "Czech Republic", "Romania", "Bulgaria", "Hungary", "Greece", "Turkey",
    "Australia", "New Zealand", "India", "Pakistan", "Bangladesh", "China",
    "Japan", "Singapore", "South Korea", "Taiwan", "Hong Kong", "Israel",
    "United Arab Emirates", "UAE", "Brazil", "Mexico", "Argentina", "Chile",
    "Colombia", "South Africa", "Nigeria", "Kenya", "Egypt",
)
COUNTRY_TERMS = {
    "NO": ["Norway", "Norge", "Oslo", "Bergen", "Trondheim", "Stavanger", "Tromsø", "Fornebu", "Lysaker", "Kongsberg", "Høvik", "Drammen", "Kristiansand", "Sandnes", "Ålesund", "Haugesund"],
    "US": ["United States", "United States of America", "USA", "U.S.A.", "U.S.", "New York", "San Francisco", "Seattle", "Boston", "Chicago", "Austin", "Los Angeles", "San Diego", "Washington, DC", "Washington D.C.", "Mountain View", "Palo Alto", "San Jose", "Sunnyvale", "Atlanta", "Dallas", "Houston", "Denver", "California", "Massachusetts", "Texas"],
    "DE": ["Germany", "Deutschland", "Berlin", "Munich", "München", "Hamburg", "Frankfurt", "Cologne", "Köln", "Düsseldorf", "Dusseldorf", "Stuttgart", "Dresden", "Leipzig", "Bonn", "Karlsruhe", "Darmstadt", "Walldorf", "Erlangen", "Nuremberg", "Nürnberg"],
    "UA": ["Ukraine", "Україна", "Украина", "Kyiv", "Kiev", "Київ", "Киев", "Lviv", "Львів", "Львов", "Kharkiv", "Харків", "Харьков", "Odesa", "Odessa", "Одеса", "Dnipro", "Дніпро", "Vinnytsia", "Ivano-Frankivsk", "Uzhhorod", "Ternopil", "Chernivtsi", "Rivne", "Lutsk", "Zhytomyr", "Cherkasy", "Zaporizhzhia"],
}


def validate_countries(value):
    if not isinstance(value, list) or any(not isinstance(code, str) or code not in COUNTRIES for code in value):
        raise ValueError("Countries must be a list containing NO, US, DE or UA.")
    return list(dict.fromkeys(value))


def country_code(value):
    """Normalize a structured country value without treating a city as one."""
    if isinstance(value, dict):
        value = value.get("name") or value.get("code")
    if not isinstance(value, str):
        return None
    value = value.strip().casefold()
    return next((code for code, names in _COUNTRY_NAMES.items()
                 if value in {code.casefold(), *(name.casefold() for name in names)}), None)


def _contains_name(location, name):
    return bool(re.search(r"(?<!\w)" + re.escape(name) + r"(?!\w)", location, re.I))


def _location_countries(location):
    named = [code for code, names in _COUNTRY_NAMES.items()
             if any(_contains_name(location, name) for name in names)]
    if named:
        return named
    if any(_contains_name(location, name) for name in _OTHER_COUNTRY_NAMES):
        return []
    # A city such as Berlin or Oslo also exists in the USA. An explicit US
    # city/state suffix takes precedence over city-only hints, including when
    # followed by a remote/hybrid qualifier or another location separator.
    if _US_STATE_SUFFIX.search(location):
        return ["US"]
    codes = [code for code in COUNTRIES if re.search(r"(?<!\w)" + code + r"(?!\w)", location)]
    if codes:
        return codes
    return [code for code, terms in COUNTRY_TERMS.items()
            if any(_contains_name(location, term) for term in terms)]


def job_countries(job):
    explicit = job.get("countries")
    if explicit is not None:
        explicit = validate_countries(explicit)
        if explicit:
            return explicit
    raw = job.get("raw_json")
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw)
        except (ValueError, RecursionError):
            raw = None
    # A structured foreign country must not fall back to a same-named city
    # in one of our supported markets. Legacy rows retain their fallback.
    resolved = raw.get("resolved_location_countries") if isinstance(raw, dict) else None
    if isinstance(resolved, list):
        return validate_countries(resolved)
    evidence = raw.get("location_country_evidence") if isinstance(raw, dict) else None
    if isinstance(evidence, list) and evidence and all(isinstance(value, str) and value.strip() for value in evidence):
        return list(dict.fromkeys(code for value in evidence if (code := country_code(value))))
    location = str(job.get("location") or "")
    # Interpret each separate location independently: a German office must
    # not hide an additional US city, nor turn Berlin, NH into a German job.
    return list(dict.fromkeys(code for part in re.split(r"[;|•\n]", location)
                              for code in _location_countries(part)))


def profile_for_job(profile, job):
    """Apply one country's search/writing overrides without leaking permission."""
    result = deepcopy(profile)
    codes = job_countries(job)
    preferences = profile.get("country_preferences", {})
    if len(codes) == 1:
        code = codes[0]
        overrides = preferences.get(code, {})
        for key in ("target_roles", "related_roles", "preferred_locations", "excluded_keywords", "cover_letter_language"):
            if overrides.get(key):
                result[key] = deepcopy(overrides[key])
        # Legacy field was explicitly Norway-only in the original app.
        result["work_authorization"] = overrides.get("work_authorization", profile.get("work_authorization", "") if code == "NO" else "")
        result["country_context"] = {"country": code, "remote_preference": overrides.get("remote_preference", ""), "relocation_preference": overrides.get("relocation_preference", "")}
    else:
        result["work_authorization"] = ""
        result["country_context"] = {"countries": codes, "note": "Country-specific eligibility needs review."}
    return result


def validate_country_preferences(value):
    if not isinstance(value, dict) or any(code not in COUNTRIES for code in value):
        raise ValueError("Country preferences must map NO, US, DE or UA to preferences.")
    text_fields = {"work_authorization", "cover_letter_language", "remote_preference", "relocation_preference"}
    list_fields = {"target_roles", "related_roles", "preferred_locations", "excluded_keywords"}
    for code, preferences in value.items():
        if not isinstance(preferences, dict) or set(preferences) - text_fields - list_fields:
            raise ValueError(f"Invalid preference fields for {code}.")
        for key, item in preferences.items():
            if key in text_fields and (not isinstance(item, str) or len(item) > 4000):
                raise ValueError(f"{code} {key} must be text of at most 4,000 characters.")
            if key in list_fields and (not isinstance(item, list) or len(item) > 100 or any(not isinstance(term, str) or len(term) > 300 for term in item)):
                raise ValueError(f"{code} {key} must be a list of short text values.")
    return deepcopy(value)
