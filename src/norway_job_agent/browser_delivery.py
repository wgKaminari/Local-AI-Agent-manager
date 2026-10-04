"""One reviewed native HTML form submission in a visible, ephemeral browser.

Playwright is optional. JavaScript on employer pages is disabled, and all requests
are blocked while the filled preview is under review. Dynamic ATS applications,
logins and CAPTCHA remain a manual workflow. Explicitly selected files are
snapshotted for review. No browser state is saved.
"""
from __future__ import annotations

import copy
import hashlib
import json
import mimetypes
import secrets
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from .application_forms import form_fingerprint, parse_html_form, validate_form
from .sources import _check_robots, _public_addresses, _url_parts


class BrowserDeliveryError(ValueError):
    """A user-readable error that contains no page content or candidate data."""


MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_TOTAL_ATTACHMENT_BYTES = 25 * 1024 * 1024

_SNAPSHOT = """async index => {
  const f = document.forms[index];
  if (!f) throw new Error('Form unavailable');
  const controls = Array.from(f.elements).filter(e => ['INPUT', 'TEXTAREA', 'SELECT', 'BUTTON'].includes(e.tagName));
  const fields = await Promise.all(controls.filter(e => !['hidden', 'submit', 'button', 'reset'].includes(e.type)).map(async e => ({
    name: e.name, type: e.type, disabled: e.disabled, readOnly: !!e.readOnly,
    dirname: e.getAttribute('dirname') || '',
    required: e.required, maxLength: e.maxLength || null,
    value: e.type === 'file' ? '' : e.value,
    checked: ['radio', 'checkbox'].includes(e.type) ? e.checked : null,
    options: e.tagName === 'SELECT' ? Array.from(e.options).map(o => ({value:o.value, label:o.text, selected:o.selected, disabled:o.disabled})) : [],
    visible: !!(e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'),
    files: e.type === 'file' ? await Promise.all(Array.from(e.files).map(async file => ({
      name:file.name, size:file.size, type:file.type,
      sha256: Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', await file.arrayBuffer()))).map(b=>b.toString(16).padStart(2,'0')).join('')
    }))) : []
  })));
  const buttons = controls.filter(e => e.type === 'submit' && !e.disabled && e.getClientRects().length).map(e => ({
    name:e.name, value:e.value, label:e.innerText || e.value,
    action:e.hasAttribute('formaction') ? e.formAction : f.action,
    method:e.hasAttribute('formmethod') ? e.formMethod : f.method,
    enctype:e.hasAttribute('formenctype') ? e.formEnctype : f.enctype,
    target:e.hasAttribute('formtarget') ? e.formTarget : f.target
  }));
  const hidden = await Promise.all(controls.filter(e => e.type === 'hidden' && !e.disabled).map(async e => ({
    name:e.name,
    sha256:Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(e.value)))).map(b=>b.toString(16).padStart(2,'0')).join('')
  })));
  return {action:f.action, method:f.method, enctype:f.enctype, target:f.target, hidden,
          fields, buttons, valid:f.checkValidity(),
          frames:document.querySelectorAll('iframe, frame').length,
          captcha:!!document.querySelector('[class*="captcha"], [id*="captcha"], [name*="captcha"]')};
}"""

_FILL = """({index, answers}) => {
  const f = document.forms[index];
  if (!f) throw new Error('Form unavailable');
  const elements = Array.from(f.elements);
  for (const answer of answers) {
    const name = answer.field_id.slice(5);
    const group = elements.filter(e => e.name === name && !e.disabled && e.type !== 'hidden');
    if (!group.length) throw new Error('Control unavailable');
    for (const e of group) {
      if (e.type === 'file') continue;
      if (['radio', 'checkbox'].includes(e.type)) e.checked = answer.selected_options.includes(e.value);
      else if (e.tagName === 'SELECT') {
        for (const option of e.options) option.selected = answer.selected_options.includes(option.value);
      } else e.value = answer.answer;
    }
  }
}"""

_SUBMIT = """index => {
  const form = document.forms[index];
  if (!form || !form.checkValidity()) throw new Error('Invalid form');
  const buttons = Array.from(form.elements).filter(e => e.type === 'submit' && !e.disabled && e.getClientRects().length);
  if (buttons.length !== 1) throw new Error('Ambiguous submit button');
  HTMLFormElement.prototype.requestSubmit.call(form, buttons[0]);
}"""


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _origin(url):
    parts, host, port = _url_parts(url)
    return parts.scheme, host, port


def _reviewed_answers(form, answers, attachments=None):
    """Validate the exact editable snapshot; never let the browser infer values."""
    if not isinstance(answers, list) or len(answers) != len(form['fields']):
        raise BrowserDeliveryError('Review an answer for every form field before opening the browser preview.')
    expected = {f['id']: f for f in form['fields']}
    attachments = attachments or {}
    seen, result = set(), []
    for answer in answers:
        if not isinstance(answer, dict) or answer.get('field_id') not in expected or answer['field_id'] in seen:
            raise BrowserDeliveryError('The reviewed answers do not match the application questions.')
        field = expected[answer['field_id']]
        seen.add(field['id'])
        value, choices = answer.get('answer', ''), answer.get('selected_options', [])
        if not isinstance(value, str) or len(value) > 100000 or not isinstance(choices, list) or any(not isinstance(c, str) for c in choices):
            raise BrowserDeliveryError('A reviewed answer has an invalid value.')
        if field['type'] == 'unsupported' or not field['id'].startswith('html:'):
            raise BrowserDeliveryError('This form needs unsupported controls. Complete it on the employer site.')
        if field.get('max_length') and len(value) > field['max_length']:
            raise BrowserDeliveryError('A reviewed answer exceeds its character limit.')
        if field['type'] == 'file':
            if value or choices:
                raise BrowserDeliveryError('Choose an attachment with the file picker; do not enter a file path as an answer.')
            missing = field['id'] not in attachments
        elif field['type'] in {'single_select', 'multi_select'}:
            allowed = {option['value'] for option in field.get('options', [])}
            if len(choices) != len(set(choices)) or any(c not in allowed for c in choices) or (field['type'] == 'single_select' and len(choices) > 1):
                raise BrowserDeliveryError('A reviewed selection is not offered by this form.')
            missing = not choices
        else:
            if choices:
                raise BrowserDeliveryError('A text question cannot contain selected options.')
            missing = not value.strip()
        if field['required'] and missing:
            raise BrowserDeliveryError('Complete every required question before opening the browser preview.')
        result.append({'field_id':field['id'], 'answer':value, 'selected_options':list(choices)})
    return result


def _load_attachments(form, attachments):
    if attachments is None:
        return {}
    if not isinstance(attachments, dict):
        raise BrowserDeliveryError('Choose attachments with the file picker.')
    allowed = {f['id'] for f in form['fields'] if f['type'] == 'file'}
    if set(attachments) - allowed:
        raise BrowserDeliveryError('An attachment does not match an application upload field.')
    result, total = {}, 0
    for field_id, path in attachments.items():
        if not isinstance(path, (str, Path)) or not str(path):
            raise BrowserDeliveryError('Choose an existing file for each attachment.')
        try:
            selected = Path(path)
            with selected.open('rb') as stream:
                data = stream.read(MAX_ATTACHMENT_BYTES + 1)
        except (OSError, ValueError):
            raise BrowserDeliveryError('A chosen attachment could not be read. Choose the file again.') from None
        total += len(data)
        if not data or len(data) > MAX_ATTACHMENT_BYTES or total > MAX_TOTAL_ATTACHMENT_BYTES:
            raise BrowserDeliveryError('Attachments must be nonempty, at most 10 MB each and at most 25 MB together.')
        result[field_id] = {'name':selected.name, 'size':len(data), 'sha256':hashlib.sha256(data).hexdigest(),
                            'mimeType':mimetypes.guess_type(selected.name)[0] or 'application/octet-stream', 'buffer':data}
    return result


def _validate_snapshot(snapshot, form, answers):
    """Reject pages whose native behavior cannot be represented by our preview."""
    if snapshot['frames'] or snapshot['captcha']:
        raise BrowserDeliveryError('Embedded applications or CAPTCHA need manual completion on the employer site.')
    if len(snapshot['buttons']) != 1:
        raise BrowserDeliveryError('This form has no single unambiguous Send button; complete it manually.')
    button = snapshot['buttons'][0]
    # A named submitter adds its own name/value to the POST, outside the
    # editable question answers. It can also override an answer by repeating
    # its name, so leave these forms for manual completion.
    if button.get('name'):
        raise BrowserDeliveryError('This Send button adds an unreviewed application value. Complete this form manually.')
    action = button['action']
    if (snapshot['method'].lower() != 'post' or button['method'].lower() != 'post'
            or snapshot['target'] not in {'', '_self'} or button['target'] not in {'', '_self'}
            or snapshot['enctype'] not in {'application/x-www-form-urlencoded', 'multipart/form-data'}
            or button['enctype'] != snapshot['enctype']):
        raise BrowserDeliveryError('Only native same-tab POST forms are supported. Complete this form manually.')
    if _origin(action) != _origin(form['source_url']) or urlsplit(action).scheme != 'https':
        raise BrowserDeliveryError('The Send destination must be HTTPS on the same website as the reviewed form.')
    if action != snapshot['action']:
        raise BrowserDeliveryError('The button overrides the form destination. Complete this form manually.')
    names = {a['field_id'][5:] for a in answers}
    hidden_names = set()
    for control in snapshot.get('hidden', []):
        name = control.get('name', '')
        if (name in names or name in hidden_names or not re.fullmatch(
                r'(?:csrf(?:middlewaretoken|token|_token)?|_csrf(?:_token)?|__RequestVerificationToken|authenticity_token|_token|nonce|__VIEWSTATE(?:GENERATOR|ENCRYPTED)?|__EVENTVALIDATION)', name, re.I)):
            raise BrowserDeliveryError('Hidden application values need manual review on the employer site. Only recognized anti-forgery fields can be sent automatically.')
        hidden_names.add(name)
    actual_names = set()
    for control in snapshot['fields']:
        if control['disabled']:
            continue
        if (not control['name'] or control['type'] not in {'text', 'email', 'tel', 'url', 'textarea', 'radio', 'checkbox', 'select-one', 'select-multiple', 'file'}
                or control['readOnly'] or not control['visible'] or control.get('dirname')):
            raise BrowserDeliveryError('The page contains unreviewed, hidden or unsupported controls. Complete it manually.')
        actual_names.add(control['name'])
    if names != actual_names:
        raise BrowserDeliveryError('The browser contains different questions. Inspect the application again before sending.')
    for answer in answers:
        group = [c for c in snapshot['fields'] if c['name'] == answer['field_id'][5:] and not c['disabled']]
        if len(group) != 1 and any(c['type'] not in {'radio', 'checkbox'} for c in group):
            raise BrowserDeliveryError('Repeated field names are ambiguous; complete this form manually.')
    return action


def _validate_filled_values(snapshot, answers, attachments=None):
    attachments = attachments or {}
    for answer in answers:
        group = [c for c in snapshot['fields'] if c['name'] == answer['field_id'][5:] and not c['disabled']]
        if group[0]['type'] == 'file':
            attachment = attachments.get(answer['field_id'])
            expected = [{'name':attachment['name'], 'size':attachment['size'], 'type':attachment['mimeType'], 'sha256':attachment['sha256']}] if attachment else []
            if group[0]['files'] != expected:
                raise BrowserDeliveryError('A browser attachment differs from the file you reviewed. Prepare a new preview.')
            continue
        if group[0]['type'] in {'radio', 'checkbox'}:
            actual = [c['value'] for c in group if c['checked']]
        elif group[0]['type'] in {'select-one', 'select-multiple'}:
            actual = [o['value'] for o in group[0]['options'] if o['selected'] and o['value']]
        else:
            if group[0]['value'] != answer['answer']:
                raise BrowserDeliveryError('The browser changed a reviewed text answer. Correct it before preparing a new preview.')
            continue
        if sorted(actual) != sorted(answer['selected_options']):
            raise BrowserDeliveryError('The browser changed a reviewed selection. Correct it before preparing a new preview.')


class BrowserApplicationSession:
    """Thread-safe facade; all Playwright calls run on one dedicated thread.

    prepare(form, answers) opens and fills a preview. send(preview['token']) is
    exclusively for an explicit user Send action. A session permits one attempt.
    """

    def __init__(self):
        self._worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix='application-browser')
        self._playwright = self._browser = self._page = None
        self._phase = 'closed'
        self._attempted = False
        self._sent_request = False
        self._token = None
        self._disposed = False

    def prepare(self, form, answers, attachments=None):
        return self._worker.submit(self._prepare, copy.deepcopy(form), copy.deepcopy(answers), copy.deepcopy(attachments)).result()

    def send(self, token):
        return self._worker.submit(self._send, token).result()

    def close(self):
        if self._disposed:
            return
        self._worker.submit(self._close).result()
        self._worker.shutdown(wait=True)
        self._disposed = True

    def _route(self, route):
        request = route.request
        try:
            same_origin = _origin(request.url) == self._allowed_origin
            main_navigation = request.is_navigation_request() and request.frame == self._page.main_frame
            if self._phase == 'opening' and same_origin and request.method == 'GET' and main_navigation:
                route.continue_()
            elif (self._phase == 'sending' and not self._sent_request and same_origin and main_navigation
                  and request.method == 'POST' and request.url == self._destination):
                self._sent_request = True
                route.continue_()
            elif (self._phase == 'sending' and self._sent_request and same_origin and main_navigation
                  and request.method == 'GET'):
                route.continue_()
            else:
                route.abort()
        except Exception:
            route.abort()

    def _prepare(self, form, answers, attachments=None):
        if self._phase != 'closed' or self._attempted:
            raise BrowserDeliveryError('Close the existing preview before preparing a new application.')
        validate_form(form)
        if form.get('provider') != 'html' or not isinstance(form.get('delivery'), dict):
            raise BrowserDeliveryError('Browser delivery supports native HTML forms. Complete this ATS or manually copied form on its website.')
        files = _load_attachments(form, attachments)
        answers = _reviewed_answers(form, answers, files)
        parts, host, port = _url_parts(form.get('source_url', ''))
        if parts.scheme != 'https' or port != 443:
            raise BrowserDeliveryError('Browser delivery requires a public HTTPS application page on port 443.')
        addresses = _public_addresses(host, port)
        _check_robots(form['source_url'], {})
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise BrowserDeliveryError('Optional browser delivery is not installed. Run python -m pip install "playwright>=1.49,<2", then python -m playwright install chromium.') from None
        try:
            address = next((ip for ip in addresses if ':' not in ip), addresses[0])
            resolved = '[' + address + ']' if ':' in address else address
            self._allowed_origin = _origin(form['source_url'])
            self._phase = 'opening'
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=False, args=[
                '--no-proxy-server', '--host-resolver-rules=MAP ' + host + ' ' + resolved + ', MAP * ~NOTFOUND'])
            context = self._browser.new_context(java_script_enabled=False, accept_downloads=False, service_workers='block')
            self._page = context.new_page()
            context.route('**/*', self._route)
            self._page.goto(form['source_url'], wait_until='domcontentloaded', timeout=30000)
            self._phase = 'review'
            current = parse_html_form(self._page.content(), self._page.url)
            if form_fingerprint(current) != form_fingerprint(form):
                raise BrowserDeliveryError('The application form changed. Inspect it and review fresh answers before preparing the browser.')
            self._index = current['delivery']['form_index']
            before = self._page.evaluate(_SNAPSHOT, self._index)
            self._destination = _validate_snapshot(before, form, answers)
            if files and before['enctype'] != 'multipart/form-data':
                raise BrowserDeliveryError('This form does not encode file uploads correctly; complete it manually.')
            self._page.evaluate(_FILL, {'index':self._index, 'answers':answers})
            for field in form['fields']:
                if field['type'] == 'file':
                    # Match the actual name as an exact attribute, with CSS escaping.
                    name = field['id'][5:]
                    selector = 'form input[type="file"][name=' + json.dumps(name) + ']'
                    target = self._page.locator(selector)
                    if target.count() != 1:
                        raise BrowserDeliveryError('The file upload control is ambiguous; complete it manually.')
                    attachment = files.get(field['id'])
                    target.set_input_files({k:attachment[k] for k in ('name', 'mimeType', 'buffer')} if attachment else [])
            preview = self._page.evaluate(_SNAPSHOT, self._index)
            _validate_snapshot(preview, form, answers)
            _validate_filled_values(preview, answers, files)
            if not preview['valid']:
                raise BrowserDeliveryError('The employer form rejected one or more answers. Correct them before preparing a new preview.')
            self._snapshot_hash = _digest(preview)
            self._form = form
            self._token = secrets.token_urlsafe(32)
            return {'token':self._token, 'destination':self._destination, 'fields':copy.deepcopy(answers),
                    'technical_fields':[item['name'] for item in preview.get('hidden', [])],
                    'files':[{'field_id':field_id, **{k:file[k] for k in ('name','size','sha256')}} for field_id,file in files.items()],
                    'status':'ready_for_review', 'limitations':[
                        'Check the filled browser and destination, then press Send in the application.',
                        'One native POST attempt. Acceptance by the employer must be confirmed on its website.',
                        'JavaScript, embedded forms, login and CAPTCHA are not supported.']}
        except BrowserDeliveryError:
            self._close()
            raise
        except Exception:
            self._close()
            raise BrowserDeliveryError('The browser preview could not be prepared. Install Playwright Chromium if needed, or complete this form manually.') from None

    def _send(self, token):
        if self._phase != 'review' or self._attempted or not isinstance(token, str) or not secrets.compare_digest(token, self._token or ''):
            raise BrowserDeliveryError('No matching reviewed browser preview is available, or this application was already attempted.')
        try:
            current = parse_html_form(self._page.content(), self._page.url)
            snapshot = self._page.evaluate(_SNAPSHOT, self._index)
            if (form_fingerprint(current) != form_fingerprint(self._form)
                    or _digest(snapshot) != self._snapshot_hash or not snapshot['valid']):
                raise BrowserDeliveryError('The form or filled answers changed after review. Close this preview and review the updated application.')
            self._attempted = True
            self._token = None
            self._phase = 'sending'
            with self._page.expect_navigation(wait_until='domcontentloaded', timeout=30000) as navigation:
                self._page.evaluate(_SUBMIT, self._index)
            response = navigation.value
            self._phase = 'finished'
            if self._sent_request and response and response.status < 400:
                return {'status':'submitted_unconfirmed', 'message':'The reviewed application was sent once. Check the employer confirmation page before marking it as applied.'}
            return {'status':'uncertain', 'message':'The employer did not confirm acceptance. Check its website; this application will not be retried automatically.'}
        except BrowserDeliveryError:
            raise
        except Exception:
            self._phase = 'finished'
            return {'status':'uncertain' if self._sent_request else 'blocked',
                    'message':'The browser could not confirm the result. Check the employer website before any further attempt; no automatic retry was made.'}

    def _close(self):
        for instance, method in ((self._browser, 'close'), (self._playwright, 'stop')):
            if instance:
                try:
                    getattr(instance, method)()
                except Exception:
                    pass
        self._page = self._browser = self._playwright = None
        self._token = None
        self._phase = 'closed'
