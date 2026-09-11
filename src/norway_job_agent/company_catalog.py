"""Norway employer watchlist, checked against official career sites in September 2026.

A career link is not automatically a working collector. Entries with source=None
remain useful for opening the employer site or importing an email job alert.
"""
from copy import deepcopy

NORWAY_LOCATIONS = [
    "Norway", "Norge", "Oslo", "Bergen", "Trondheim", "Stavanger", "Tromsø",
    "Drammen", "Fornebu", "Lysaker", "Høvik", "Sandvika", "Kongsberg",
    "Haugesund", "Vats", "Kristiansand", "Ålesund", "Sandnes", "Porsgrunn",
]


def _entry(identifier, name, careers_url, source=None, note=""):
    if source is not None:
        source = {**source, "name": name, "locations": list(NORWAY_LOCATIONS)}
    return {"id": identifier, "name": name, "careers_url": careers_url,
            "source": source, "note": note, "checked_at": "2026-09-10"}


COMPANY_CATALOG = [
    _entry("equinor", "Equinor", "https://www.equinor.com/careers", {
        "type": "workday", "url": "https://equinor.wd3.myworkdayjobs.com/en-US/EQNR",
        "search_text": "Norway", "max_jobs": 60,
    }, "Public Workday search for Norway, followed by location filtering. Up to 60 results per scan."),
    _entry("dnv", "DNV", "https://jobs.dnv.com/", {
        "type": "career_page", "url": "https://jobs.dnv.com/",
        "sitemap_url": "https://jobs.dnv.com/sitemap.xml", "job_path": "/job-search/",
        "url_keywords": ["norway"], "max_jobs": 60,
    }, "Reads up to 60 public sitemap vacancies with Norway in the URL. Other URLs are outside this scan."),
    _entry("tieto", "Tieto", "https://careers.tieto.com/", {
        "type": "career_page", "url": "https://careers.tieto.com/",
        "sitemap_url": "https://careers.tieto.com/vacanciessitemap.xml", "job_path": "/job/",
        "url_keywords": ["norway"], "max_jobs": 60,
    }, "Reads up to 60 public sitemap vacancies with Norway in the URL. Other URLs are outside this scan."),
    _entry("google", "Google", "https://www.google.com/about/careers/applications/", note=
           "Open Google Careers and filter by Norway. Automated collection is not supported here; import relevant job-alert emails or paste a vacancy."),
    _entry("autostore", "AutoStore", "https://www.autostoresystem.com/careers", {
        "type": "workday", "url": "https://autostore.wd3.myworkdayjobs.com/en-US/autostore",
        "search_text": "Norway", "max_jobs": 60,
    }, "Public Workday search for Norway, followed by location filtering. Up to 60 results per scan."),
    _entry("cognite", "Cognite", "https://www.cognite.com/en/company/careers", {
        "type": "greenhouse", "board": "cognite",
    }, "Public Greenhouse board, filtered to Norway locations."),
    _entry("bekk", "Bekk", "https://jobs.lever.co/bekk", {
        "type": "lever", "board": "bekk", "region": "global",
    }, "Public Lever board, filtered to Norway locations."),
    _entry("netlight", "Netlight", "https://jobs.lever.co/netlight", {
        "type": "lever", "board": "netlight", "region": "global",
    }, "Public Lever board, filtered to Norway locations."),
    _entry("crayon", "Crayon", "https://job-boards.greenhouse.io/crayon", {
        "type": "greenhouse", "board": "crayon",
    }, "Public Greenhouse board, filtered to Norway locations. An empty board is possible."),
    _entry("sopra-steria", "Sopra Steria", "https://careers.soprasteria.no/jobs", {
        "type": "career_page", "url": "https://careers.soprasteria.no/jobs",
        "job_path": "/job/", "max_jobs": 60,
    }, "Reads public structured vacancies linked on the listing page. Further pages may contain additional jobs."),
    _entry("bouvet", "Bouvet", "https://www.bouvet.no/ledige-stillinger", {
        "type": "career_page", "url": "https://www.bouvet.no/ledige-stillinger",
        "job_path": "/bli-en-av-oss/ledige-stillinger/", "max_jobs": 60,
    }, "Reads public structured vacancies linked on the listing page. Further pages may contain additional jobs."),
    _entry("statkraft", "Statkraft", "https://www.statkraft.com/careers/", note=
           "Career-page and email import. The SmartRecruiters endpoint disallows this collector in robots.txt."),
    _entry("tomra", "TOMRA", "https://www.tomra.com/careers", note=
           "Open the official career page or import job-alert emails. No automatic connector is enabled."),
    _entry("kongsberg", "KONGSBERG", "https://www.kongsberg.com/careers/", note=
           "Open the official career page or import job-alert emails. No automatic connector is enabled."),
    _entry("aker-solutions", "Aker Solutions", "https://www.akersolutions.com/careers/", note=
           "Open the official career page or import job-alert emails. No automatic connector is enabled."),
    _entry("telenor", "Telenor", "https://www.telenor.com/career/open-positions/", note=
           "Open the official career page and filter by Norway, or import job-alert emails. No automatic connector is enabled."),
    _entry("telenor-cyberdefence", "Telenor Cyberdefence", "https://jobs.telenorcyberdefence.com/jobs", note=
           "The checked vacancy page did not expose JobPosting structured data. Use the career page or email import."),
    _entry("storebrand", "Storebrand", "https://www.storebrand.no/om-storebrand/jobb-i-storebrand", note=
           "Open the official career page or import job-alert emails. No automatic connector is enabled."),
    _entry("dnb", "DNB", "https://www.dnb.no/om-oss/jobb-og-karriere", note=
           "Open the official career page or import job-alert emails. No automatic connector is enabled."),
    _entry("nordea", "Nordea", "https://www.nordea.com/en/careers", note=
           "Open the official career page and filter by Norway, or import job-alert emails. No automatic connector is enabled."),
    _entry("capgemini", "Capgemini", "https://www.capgemini.com/no-no/careers/", note=
           "Open the official Norway career page or import job-alert emails. No automatic connector is enabled."),
    _entry("accenture", "Accenture", "https://www.accenture.com/no-en/careers", note=
           "Open the official Norway career page or import job-alert emails. No automatic connector is enabled."),
    _entry("microsoft", "Microsoft", "https://careers.microsoft.com/", note=
           "Open the official career page and filter by Norway, or import job-alert emails. No automatic connector is enabled."),
    _entry("schibsted", "Schibsted", "https://schibsted.com/career/", note=
           "Open the official career page or import job-alert emails. No automatic connector is enabled."),
    _entry("statnett", "Statnett", "https://www.statnett.no/karriere/", note=
           "Open the official career page or import job-alert emails. No automatic connector is enabled."),
    _entry("sintef", "SINTEF", "https://www.sintef.no/en/sintef-group/career/vacant-positions/", note=
           "Research opportunities. Check both Norwegian and English listings; SINTEF says they can differ. Career-page and email import."),
    _entry("ntnu", "NTNU", "https://www.ntnu.edu/vacancies", note=
           "Research and university opportunities. Open the official vacancy page or import job-alert emails; check each role's education requirements."),
]


def company_catalog() -> list[dict]:
    """Return independent settings-ready entries, safe for UI edits."""
    return deepcopy(COMPANY_CATALOG)
