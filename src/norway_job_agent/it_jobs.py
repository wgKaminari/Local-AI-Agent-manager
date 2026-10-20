"""Which vacancies are IT jobs.

With "IT jobs only" on (Settings & activity → Job sites), refreshes keep IT
vacancies and nothing else, so each country's count means IT jobs.

An official classification decides where a source has one: NAV's occupation
category ("IT") and STYRK-08 code, the Bundesagentur's IT occupation fields,
Djinni's and DOU's job categories and Adzuna's IT category. They are stored
as raw_json["it_category"] (True or False). Otherwise the title decides, in
English, German, Norwegian and Ukrainian:

* a clear IT title counts ("Software Engineer", "Softwareentwickler",
  "systemutvikler", "розробник", "Data Scientist", "DevOps", "IT-Leiter");
* a vague one ("Systems Engineer", "Consultant", "Entwickler") counts when
  its advert names at least three distinct IT technologies;
* known traps never count: real-estate "Projektentwickler", "Business
  Developer", Norwegian "utvikle deg" (develop yourself), CNC/PLC
  programmers and security guards.

An official "not IT" yields only to a clear IT title, so a carpenter advert
saying "utvikler deg" stays out while a misfiled "Java Developer" stays in.
"""

from __future__ import annotations

import json
import re

_I = re.IGNORECASE
_TECH = (r"\b(?:web|front[\s-]?end|back[\s-]?end|full[\s-]?stack|mobile|ios|android|java|python|php|\.net|c\#|c\+\+|golang|go|"
         r"ruby|rust|scala|kotlin|swift|react(?:\.js)?|angular|vue(?:\.js)?|node(?:\.js)?|javascript|typescript|sql|etl|"
         r"bi|data|game|unity|unreal|salesforce|sap|abap|crm|erp|cloud|devops|blockchain|smart[\s-]?contract|ai|ml|"
         r"embedded|firmware|wordpress|shopify|magento|drupal|power[\s-]?bi|qlik|tableau|api|platform|integration|"
         r"applikasjons|anwendungs|app|software|system|programvare|it|datenbank|database|spiele)")

# Case-sensitive acronyms: "IT" is not "it", "AI" is not "ai" in a sentence.
_ACRONYMS = re.compile(r"(?<![\w.])(?:IT|ICT|IKT|EDV|SRE|DBA|MLOps|DevOps|DevSecOps|SAP|ABAP|QA|UX|UI|AI|KI|ML|LLM|NLP|"
                       r"BI|ERP|SOC|CISO|CTO|CIO)(?![a-zA-Z])")
_STRONG = re.compile(r"|".join([
    r"software", r"programvare", r"(?:system|applikasjons|web|front[\s-]?end|back[\s-]?end|full[\s-]?stack|app|java|\.net|"
    r"python|data|it|sky|plattform|integrasjons|bi|sap|spill)[\s-]*utvikler",
    _TECH + r"[\s-]*(?:developer|entwickler(?:in)?|engineer|ingenieur(?:in)?|ingeniør|architect|architekt(?:in)?|arkitekt|"
    r"administrator|consultant|berater(?:in)?|konsulent|specialist|spezialist(?:in)?|spesialist|programmer|programmierer(?:in)?)",
    r"\bprogramm(?:er|ierer(?:in)?|erer)\b", r"розробни\w*", r"програміст\w*", r"тестувальни\w*", r"девопс",
    r"системн\w*\s+адміністратор\w*", r"аналітик\w*\s+даних", r"інженер\w*\s+(?:з\s+)?даних",
    r"data[\s-]*(?:scien\w*|engineer\w*|analy\w*|architect\w*|ingeni\w*|warehouse|platform)", r"dataingeni\w*",
    r"dataanalyti\w*", r"dataforsk\w*", r"daten(?:analy\w*|bank\w*|ingenieur\w*|architekt\w*|wissenschaft\w*)",
    r"analytics\s+engineer", r"business\s+intelligence", r"machine[\s-]*learning", r"deep[\s-]*learning",
    r"maschinelles\s+lernen", r"maskinlæring", r"künstliche\w*\s+intelligenz", r"kunstig\s+intelligens",
    r"artificial\s+intelligence", r"штучн\w*\s+інтелект\w*", r"computer\s+vision", r"generative\s+ai", r"data\s+science",
    r"devops", r"devsecops", r"mlops", r"site\s+reliability", r"kubernetes", r"\blinux\b", r"\bunix\b", r"sysadmin",
    r"system[\s-]*administrat\w*", r"systemadministrat\w*", r"netzwerk\w*", r"nettverk\w*", r"network\s+(?:engineer|"
    r"administrator|architect|specialist|technician|security)", r"cyber\w*", r"information[s]?\s*security",
    r"informationssicherheit", r"it[\s-]*sicherheit", r"security\s+(?:engineer|analyst|architect|consultant|specialist|"
    r"operations|researcher)", r"penetration", r"pentest\w*", r"soc\s+analyst", r"test[\s-]*automat\w*", r"softwaretest\w*",
    r"quality\s+assurance\s+(?:engineer|analyst|automation)", r"informatiker\w*", r"\binformatik", r"\binformatikk",
    r"help[\s-]?desk", r"service[\s-]?desk", r"(?:it|technical|tech|desktop|end[\s-]?user|application|anwendungs|"
    r"client|2nd[\s-]level|1st[\s-]level)[\s-]*support", r"\bsalesforce\b", r"servicenow", r"dynamics\s*365",
    r"(?:solutions?|enterprise|cloud|software|data|it|system|security|integration|technical|lösungs|daten)[\s-]*"
    r"(?:architect|architekt\w*|arkitekt)", r"scrum[\s-]*master", r"product[\s-]*owner", r"agile\s+coach",
    r"technical\s+product\s+manager", r"(?:ux|ui|user\s+experience|interaction)[\s/-]*(?:designer|researcher|writer|lead)",
    r"product\s+designer", r"member\s+of\s+technical\s+staff", r"(?:research|applied)\s+(?:scientist|engineer)\w*\s*"
    r"[,(–-]?\s*(?:ml|ai|machine|nlp|llm|deep)", r"firmware", r"\bfpga\b", r"cloud[\s-]*(?:engineer|ingeniør|ingenieur|"
    r"consultant|developer|administrator|specialist|operations)", r"plattform[\s-]*ingeni\w*", r"platform\s+engineer",
    r"infrastructure\s+engineer", r"developer\s+(?:advocate|relations|experience)", r"web[\s-]*(?:design|master)\w*",
    r"front[\s-]?end", r"back[\s-]?end", r"full[\s-]?stack", r"\bios\b", r"\bandroid\b", r"\bdeveloper\b.*\b(?:java|python|"
    r"php|react|node|\.net|c\#|golang|ruby|rust|scala|kotlin|swift|typescript|javascript)\b", r"\bcoder\b",
]), _I)
_WEAK = re.compile(r"\b(?:engineer\w*|ingenieur\w*|ingeniør\w*|інженер\w*|developer|utvikler\w*|entwickler\w*|"
                   r"architect|architekt\w*|arkitekt\w*|consultant|berater\w*|konsulent\w*|rådgiver\w*|analyst\w*|"
                   r"analytiker\w*|аналітик\w*|specialist|spezialist\w*|spesialist\w*|administrator\w*|technician|"
                   r"techniker\w*|tekniker\w*|manager|leder|leiter\w*|lead|head|director|researcher|scientist|"
                   r"associate|staff|technical|technisch\w*|teknisk\w*|multiple\s+roles|roles?|positions?)\b", _I)
_NEVER = re.compile(r"|".join([
    r"business[\s-]+develop", r"projekt[\s-]*entwickl", r"immobilien", r"personal[\s-]*entwickl",
    r"utvikl\w*\s+(?:deg|dere|seg|oss)\b", r"(?:CNC|SPS|PLC|NC)[\s-]*(?:programm|developer|entwickler)",
    r"security\s+(?:officer|guard)", r"sicherheits(?:mitarbeiter|dienst)", r"wachschutz", r"objektschutz", r"vekter",
    r"\bvakt\b", r"pflege", r"sykepleier", r"krankenpfleg",
]), _I)
_IT_TERMS = re.compile(r"(?<![\w#+.])(?:python|java|javascript|typescript|c#|\.net|c\+\+|golang|rust|scala|kotlin|sql|"
                       r"nosql|postgres\w*|mysql|mongodb|linux|unix|kubernetes|k8s|docker|aws|azure|gcp|google cloud|"
                       r"terraform|ansible|ci/cd|jenkins|gitlab|github|git|devops|api|apis|microservices?|react|angular|"
                       r"vue|node\.js|spark|kafka|hadoop|airflow|tensorflow|pytorch|scikit-learn|machine learning|"
                       r"deep learning|llms?|software|cloud|database|datenbank\w*|cybersecurity|"
                       r"it-sicherheit|abap|salesforce|servicenow|jira|active directory|vmware|windows server|cisco|"
                       r"firewall|power bi|tableau|etl|data warehouse|rest api|graphql|backend|frontend|full[\s-]?stack)"
                       r"(?![\w#+])", _I)


def _raw(job: dict) -> dict:
    raw = job.get("raw_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, RecursionError):
            raw = None
    return raw if isinstance(raw, dict) else {}


def clear_it_title(title: str) -> bool:
    """A title that names an IT role by itself."""
    title = title or ""
    if _NEVER.search(title):
        return False
    return bool(_STRONG.search(title) or _ACRONYMS.search(title))


def is_it(job: dict) -> bool:
    title = str(job.get("title") or "")
    raw = _raw(job)
    official = raw.get("it_category")
    occupation = str(raw.get("occupation") or "")
    if _NEVER.search(title):
        return False
    if official is True:
        return True
    if clear_it_title(title) or (occupation and clear_it_title(occupation)):
        return True
    if official is False:
        return False
    if _WEAK.search(title):
        terms = {match.group(0).casefold() for match in _IT_TERMS.finditer(str(job.get("description") or "")[:20000])}
        return len(terms) >= 3
    return False
