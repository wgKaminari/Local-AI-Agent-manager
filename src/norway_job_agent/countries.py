"""Country labels and conservative location matching, never eligibility inference."""

from copy import deepcopy
import re

COUNTRIES = {"NO": "Norway", "US": "USA", "DE": "Germany", "UA": "Ukraine"}
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


def job_countries(job):
    explicit = job.get("countries")
    if explicit is not None:
        explicit = validate_countries(explicit)
        if explicit:
            return explicit
    location = str(job.get("location") or "")
    names = {"NO": ["Norway", "Norge"], "US": ["United States", "USA", "U.S.A.", "U.S."],
             "DE": ["Germany", "Deutschland"], "UA": ["Ukraine", "Україна", "Украина"]}
    named = [code for code, terms in names.items() if any(re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", location, re.I) for term in terms)]
    if named:
        return named
    # A city such as Berlin or Oslo also exists in the USA. An explicit US
    # city/state suffix takes precedence over city-only hints.
    if re.search(r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)(?:\s+\d{5})?\s*$", location):
        return ["US"]
    # Country names/cities are hints for discovery. Generic 'remote', US state
    # abbreviations and bare 'NO' are deliberately not inferred from prose.
    return [code for code, terms in COUNTRY_TERMS.items()
            if location.strip().upper() == code or any(
                re.search(r"(?<!\w)" + re.escape(term) + r"(?!\w)", location, re.I)
                for term in terms)]


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
