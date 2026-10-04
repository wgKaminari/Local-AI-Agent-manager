"""Read public application questions. Never fill, upload or submit a form.

Greenhouse exposes a public question schema; other sites are inspected as static
HTML. All downloads share the existing DNS-pinned, robots-aware downloader.
"""
from __future__ import annotations

import hashlib
import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit

from .sources import SourceError, _board, _fetch, _plain, _url_parts

MAX_FIELDS = 120
MAX_OPTIONS = 250
_TYPES = {"text", "textarea", "single_select", "multi_select", "file", "unsupported"}
_PROTECTED = re.compile(
    r"consent|privacy|terms|certif|attest|agree|acknowledg|signature|gdpr|"
    r"authori[sz]|eligible|eligibility|visa|sponsor|citizen|nationality|work permit|right to work|"
    r"salary|compensation|pay expectation|start date|notice period|relocat|"
    r"gender|race|ethnic|disabilit|veteran|religion|birth|\bage\b|sexual|criminal|background check|"
    r"samtykke|personvern|arbeidstillat|statsborger|lønn|oppsigelse|"
    r"einwillig|datenschutz|zustimm|arbeitserlaub|staatsangehör|gehalt|kündigungs|"
    r"згод|персональн|громадян|дозвіл|дозвол|зарплат|інвалід|вік\b|народжен",
    re.IGNORECASE,
)


def needs_personal_decision(label: str) -> bool:
    return bool(_PROTECTED.search(label))


def _field(identifier, label, kind="text", required=False, options=None, **extra):
    label = _plain(label).strip()[:2000]
    return {"id": str(identifier), "label": label or "Unlabelled field", "type": kind,
            "required": bool(required), "options": options or [], "max_length": None,
            "review_only": kind in {"file", "unsupported"} or needs_personal_decision(label), **extra}


def validate_form(value: dict) -> dict:
    """Validate saved/manual forms too; never trust cached extraction metadata."""
    if not isinstance(value, dict) or not isinstance(value.get("fields"), list) or len(value["fields"]) > MAX_FIELDS:
        raise ValueError(f"An application form must contain at most {MAX_FIELDS} fields.")
    identifiers = set()
    for field in value["fields"]:
        if not isinstance(field, dict):
            raise ValueError("Each application field must be an object.")
        for key, limit in (("id", 500), ("label", 2000)):
            if not isinstance(field.get(key), str) or not field[key].strip() or len(field[key]) > limit:
                raise ValueError(f"Application field {key} is missing or too long.")
        if field["id"] in identifiers:
            raise ValueError("Application field identifiers must be unique.")
        identifiers.add(field["id"])
        if field.get("type") not in _TYPES or not isinstance(field.get("required"), bool):
            raise ValueError("Application field type or required flag is invalid.")
        maximum = field.get("max_length")
        if maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 100000):
            raise ValueError("Application field character limit is invalid.")
        options = field.get("options", [])
        if not isinstance(options, list) or len(options) > MAX_OPTIONS:
            raise ValueError("Application field contains too many options.")
        seen = set()
        for option in options:
            if not isinstance(option, dict) or not all(isinstance(option.get(k), str) and len(option[k]) <= 2000 for k in ("value", "label")):
                raise ValueError("Application options need text values and labels.")
            if option["value"] in seen:
                raise ValueError("Application option values must be unique.")
            seen.add(option["value"])
    return value


def form_fingerprint(form: dict) -> str:
    validate_form(form)
    content = {key: form.get(key) for key in ("source_url", "provider", "fields", "delivery")}
    return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _result(url, provider, fields, warnings, coverage):
    value = {"source_url": url, "provider": provider, "fields": fields,
             "warnings": warnings, "coverage": coverage}
    value["fingerprint"] = form_fingerprint(value)
    return value


def _greenhouse_identity(job):
    if job.get("source") == "greenhouse":
        parts = str(job.get("source_id", "")).rsplit(":", 1)
        if len(parts) == 2 and parts[1].isdecimal():
            return _board(parts[0]), parts[1]
    for raw in (job.get("apply_url"), job.get("source_url")):
        if not raw:
            continue
        parts, host, _ = _url_parts(raw)
        if host not in {"boards.greenhouse.io", "job-boards.greenhouse.io", "boards.eu.greenhouse.io", "job-boards.eu.greenhouse.io"}:
            continue
        match = re.fullmatch(r"/([A-Za-z0-9_-]+)/jobs/(\d+)/?", parts.path)
        if match:
            return _board(match[1]), match[2]
        query = parse_qs(parts.query)
        if parts.path.rstrip("/") == "/embed/job_app" and query.get("for") and query.get("token", [""])[0].isdecimal():
            return _board(query["for"][0]), query["token"][0]
    return None


def parse_greenhouse_questions(data: dict, source_url: str) -> dict:
    if not isinstance(data, dict) or not isinstance(data.get("questions"), list):
        raise SourceError("Greenhouse did not return application questions; use manual questions.")
    fields, warnings = [], []
    types = {"input_text": "text", "textarea": "textarea", "input_file": "file",
             "multi_value_single_select": "single_select", "multi_value_multi_select": "multi_select"}
    for section in ("questions", "location_questions", "compliance"):
        rows = data.get(section, [])
        if not isinstance(rows, list):
            warnings.append(f"The {section} section needs review on the original site.")
            continue
        for index, question in enumerate(rows):
            if not isinstance(question, dict) or not isinstance(question.get("fields"), list):
                warnings.append("A question could not be interpreted; check the original form.")
                continue
            alternatives = [f for f in question["fields"] if isinstance(f, dict) and f.get("type") != "input_hidden"]
            for item in alternatives:
                if not item.get("name"):
                    warnings.append("An unnamed question needs review on the original site.")
                    continue
                options = []
                for option in item.get("values") or []:
                    if isinstance(option, dict) and isinstance(option.get("value"), (int, str)):
                        options.append({"value": str(option["value"]), "label": _plain(option.get("label"))})
                kind = types.get(item.get("type"), "unsupported")
                field = _field(f"greenhouse:{section}:{item['name']}", question.get("label", ""), kind,
                               question.get("required", False) and len(alternatives) == 1, options,
                               review_only=section == "compliance" or kind in {"file", "unsupported"} or needs_personal_decision(question.get("label", "")))
                if len(alternatives) > 1:
                    field.update(alternative_group=f"{section}:{index}", group_required=bool(question.get("required")))
                fields.append(field)
    demographic = data.get("demographic_questions") or {}
    if isinstance(demographic, dict):
        for index, question in enumerate(demographic.get("questions", [])):
            if not isinstance(question, dict):
                continue
            options = [{"value": str(o["id"]), "label": _plain(o.get("label"))}
                       for o in question.get("answer_options", []) if isinstance(o, dict) and "id" in o]
            fields.append(_field(f"greenhouse:demographic:{question.get('id', index)}", question.get("label", ""),
                                 types.get(question.get("type"), "unsupported"), question.get("required", False), options,
                                 review_only=True))
    if data.get("data_compliance"):
        warnings.append("This form includes data-processing/retention consent. Read and choose these yourself on the original site.")
    warnings.append("Public question snapshot only. Conditional fields, consent wording and later application steps must be checked on the original site.")
    if any(f.get("alternative_group") for f in fields):
        warnings.append("Fields sharing an alternative_group are alternative ways to answer one question, such as uploading or pasting a CV.")
    return _result(source_url, "greenhouse", fields, warnings, "public_form" if fields else "unavailable")


class _Node:
    def __init__(self, tag="root", attrs=None, parent=None):
        self.tag, self.attrs, self.parent = tag, attrs or {}, parent
        self.children = []

    def text(self):
        return " ".join(child if isinstance(child, str) else child.text() for child in self.children if isinstance(child, str) or child.tag not in {"script", "style"})


class _FormHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = self.current = _Node()
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        if len(self.nodes) >= 15000:
            raise SourceError("This application page is too complex; paste its questions instead.")
        node = _Node(tag, dict(attrs), self.current)
        self.nodes.append(node)
        self.current.children.append(node)
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.current = node

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        node = self.current
        while node.parent is not None:
            if node.tag == tag:
                self.current = node.parent
                break
            node = node.parent

    def handle_data(self, data):
        self.current.children.append(data)


def _ancestors(node):
    while node.parent is not None:
        node = node.parent
        yield node


def _visible(node):
    return not any("hidden" in n.attrs or "disabled" in n.attrs or n.attrs.get("aria-hidden") == "true"
                   or re.search(r"display\s*:\s*none|visibility\s*:\s*hidden", n.attrs.get("style", ""), re.I)
                   or n.tag in {"script", "style", "template"} for n in [node, *_ancestors(node)])


def parse_html_form(html: str, source_url: str = "") -> dict:
    """Extract visible static controls, excluding values, tokens and passwords."""
    if not isinstance(html, str) or len(html) > 2 * 1024 * 1024:
        raise ValueError("Application HTML exceeds the 2 MB limit.")
    parser = _FormHTML()
    try:
        parser.feed(html)
        parser.close()
    except RecursionError:
        raise SourceError("This application page is too deeply nested; paste its questions instead.") from None
    lookup = {n.attrs["id"]: n for n in parser.nodes if n.attrs.get("id")}
    labels = {n.attrs["for"]: n.text() for n in parser.nodes if n.tag == "label" and n.attrs.get("for")}
    forms = [n for n in parser.nodes if n.tag == "form"]
    controls = [n for n in parser.nodes if n.tag in {"input", "select", "textarea"} and _visible(n)
                and n.attrs.get("type", "text").lower() not in {"hidden", "password", "submit", "reset", "button", "image", "search"}]
    def owner(node):
        return lookup.get(node.attrs.get("form", "")) or next((n for n in _ancestors(node) if n.tag == "form"), None)
    def score(form):
        metadata = " ".join(str(form.attrs.get(k) or "") for k in ("id", "class", "action"))
        owned = [n for n in controls if owner(n) is form]
        return (10 if re.search(r"appl|candidate|bewerb|søknad|заяв", metadata, re.I) else 0) + sum(n.tag == "textarea" or n.attrs.get("type") == "file" for n in owned)
    warnings = ["Static HTML only: JavaScript, conditional questions, hidden steps, login and uploads are not inspected. Verify all questions on the original page."]
    if forms:
        chosen = max(forms, key=score)
        controls = [n for n in controls if owner(n) is chosen]
        if len(forms) > 1:
            warnings.append("Multiple forms were present; only the form most likely to be the application was inspected.")
    else:
        controls = []
    fields, groups = [], {}
    def label_for(node):
        attrs = node.attrs
        referenced = " ".join(lookup[key].text() for key in attrs.get("aria-labelledby", "").split() if key in lookup)
        wrapping = next((n.text() for n in _ancestors(node) if n.tag == "label"), "")
        return _plain(attrs.get("aria-label") or referenced or labels.get(attrs.get("id")) or wrapping or attrs.get("placeholder") or attrs.get("name") or "Unlabelled field")
    for index, node in enumerate(controls):
        attrs = node.attrs
        kind = attrs.get("type", "text").lower() if node.tag == "input" else node.tag
        label = label_for(node)
        name = attrs.get("name") or attrs.get("id") or f"field-{index}"
        identifier = "html:" + name
        required = "required" in attrs or attrs.get("aria-required") == "true"
        options = []
        if kind in {"radio", "checkbox"}:
            fieldset = next((n for n in _ancestors(node) if n.tag == "fieldset"), None)
            legend = next((n.text() for n in fieldset.children if not isinstance(n, str) and n.tag == "legend"), "") if fieldset else ""
            group_label = _plain(legend) or label
            option = {"value": str(attrs.get("value") or "on"), "label": label}
            if identifier in groups:
                groups[identifier]["options"].append(option)
                groups[identifier]["required"] |= required
                groups[identifier]["review_only"] |= needs_personal_decision(label)
                continue
            field = _field(identifier, group_label, "single_select" if kind == "radio" else "multi_select", required, [option])
            groups[identifier] = field
        else:
            if kind == "select":
                for option in parser.nodes:
                    if option.tag == "option" and node in _ancestors(option) and _visible(option):
                        value = option.attrs.get("value", _plain(option.text()))
                        if value != "":
                            options.append({"value": str(value), "label": _plain(option.text())})
                field_type = "multi_select" if "multiple" in attrs else "single_select"
            else:
                field_type = kind if kind in {"textarea", "file"} else "text" if kind in {"text", "email", "tel", "url"} else "unsupported"
            field = _field(identifier, label, field_type, required, options)
            maximum = attrs.get("maxlength", "")
            if maximum and maximum.isdecimal() and 0 < int(maximum) <= 100000:
                field["max_length"] = int(maximum)
        if any(f["id"] == field["id"] for f in fields):
            field["id"] += f":{index}"
        fields.append(field)
    if not fields:
        warnings.append("No readable application questions were found. Open the application and paste the questions manually.")
    if "captcha" in html.casefold():
        warnings.append("The page mentions CAPTCHA; complete any verification yourself on the original site.")
    result = _result(source_url, "html", fields, warnings, "partial" if fields else "unavailable")
    if forms:
        result["delivery"] = {"form_index": forms.index(chosen),
                              "action": urljoin(source_url, chosen.attrs.get("action") or source_url),
                              "method": chosen.attrs.get("method", "get").lower()}
        result["fingerprint"] = form_fingerprint(result)
    return result


def discover_application_form(job: dict) -> dict:
    if not isinstance(job, dict):
        raise ValueError("Choose a saved vacancy to inspect its application form.")
    url = job.get("apply_url") or job.get("source_url") or ""
    if not url:
        return _result("", "manual", [], ["No application URL is saved. Paste the questions manually."], "unavailable")
    _url_parts(url)
    identity = _greenhouse_identity(job)
    if identity:
        board, identifier = identity
        endpoint = f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{identifier}?questions=true"
        _, _, body, _ = _fetch(endpoint, respect_robots=True)
        try:
            data = json.loads(body)
        except (ValueError, UnicodeError, RecursionError):
            raise SourceError("Greenhouse returned invalid questions; paste them manually.") from None
        return parse_greenhouse_questions(data, url)
    _, headers, body, final = _fetch(url, respect_robots=True)
    if headers.get("content-type") and not any(t in headers["content-type"].lower() for t in ("text/html", "application/xhtml")):
        return _result(final, "html", [], ["The application URL did not return an HTML form. Paste the questions manually."], "unavailable")
    return parse_html_form(body.decode("utf-8-sig", errors="replace"), final)


def parse_manual_questions(text: str, source_url: str = "") -> dict:
    """One question per line; use `Question | Option A | Option B` for choices."""
    if not isinstance(text, str) or len(text) > 50000:
        raise ValueError("Paste at most 50,000 characters of application questions.")
    fields = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = [part.strip() for part in line.split("|")]
        if not parts[0] or any(not part for part in parts[1:]):
            raise ValueError("Each question and option needs text; separate choice options with |.")
        options = [{"value": p, "label": p} for p in parts[1:]]
        identifier = "manual:" + hashlib.sha256(line.strip().encode()).hexdigest()[:16]
        if any(f["id"] == identifier for f in fields):
            continue
        fields.append(_field(identifier, parts[0], "single_select" if options else "textarea", options=options))
    if not fields:
        raise ValueError("Paste at least one application question.")
    return _result(source_url, "manual", fields, ["Manually copied questions; required flags, character limits and later steps must be checked on the original form."], "partial")
