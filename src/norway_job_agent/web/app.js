'use strict';
// Every external string is escaped. The workspace never renders vacancy HTML.
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;'
} [c]));
const clone = value => JSON.parse(JSON.stringify(value));
const cap = text => String(text || '').replaceAll('_', ' ').replace(/^./, s => s.toUpperCase());
const paths = {
  discover: 'M21 11.1V12a9 9 0 1 1-5.3-8.2 M22 4l-10 10-3-3',
  spark: 'm12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5Z',
  saved: 'M6 3h12v18l-6-4-6 4Z',
  applications: 'M9 5V3h6v2 M3 7h18v14H3Z M3 12h18 M10 12v3h4v-3',
  profile: 'M20 21v-2a6 6 0 0 0-6-6h-4a6 6 0 0 0-6 6v2 M16 5a4 4 0 1 1-8 0 4 4 0 0 1 8 0',
  sources: 'M3 21V5l9-3v19 M12 8h9v13 M6 7h2 M6 11h2 M6 15h2 M15 11h3 M15 15h3 M2 21h20',
  mail: 'M3 5h18v14H3Z M3 6l9 7 9-7',
  settings: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',
  search: 'M21 21l-5-5 M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  plus: 'M12 5v14 M5 12h14',
  arrow: 'M7 17 17 7 M7 7h10v10',
  chevron: 'm9 5 7 7-7 7',
  close: 'm6 6 12 12 M6 18 18 6',
  sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1 1 M18 18l1 1 M5 19l1-1 M18 6l1-1',
  help: 'M9 9a3 3 0 1 1 4 3c-1 1-1 2-1 2 M12 17h.01 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  check: 'm5 12 4 4L19 6',
  clock: 'M12 7v5l3 2 M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0',
  file: 'M14 2H4v20h16V8Z M14 2v6h6 M8 13h8 M8 17h6',
  back: 'm12 5-7 7 7 7 M5 12h15',
  globe: 'M21 12H3 M12 3a16 16 0 0 1 0 18 16 16 0 0 1 0-18 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  more: 'M4.5 12h1 M11.5 12h1 M18.5 12h1',
  filter: 'M3 5h18 M6 12h12 M10 19h4',
  phone: 'M7 2h10v20H7Z M11 18h2',
  chat: 'M21 12a8 8 0 0 1-11.6 7.1L3 21l1.9-6.4A8 8 0 1 1 21 12Z',
  copy: 'M9 9h11v11H9Z M5 15H4V4h11v1',
  send: 'M22 2 11 13 M22 2l-7 20-4-9-9-4Z'
};
const icon = name => `<svg aria-hidden="true" viewBox="0 0 24 24"><path d="${paths[name]||paths.file}"/></svg>`;
const button = (label, action, kind = '', extra = '') => `<button type="button" class="${kind}" data-action="${action}" ${extra}>${label}</button>`;
const pill = (text, tone = '') => `<span class="pill ${tone}">${esc(text)}</span>`;
const safeUrl = text => {
  try {
    const u = new URL(text);
    return ['https:', 'http:'].includes(u.protocol) && !u.username && !u.password ? u.href : '';
  } catch {
    return '';
  }
};
const link = (label, url) => safeUrl(url) ? `<a href="${esc(safeUrl(url))}" target="_blank" rel="noopener noreferrer">${esc(label)} ${icon('arrow')}</a>` : '';
const statusTone = s => ['applied', 'interview'].includes(s) ? 'green' : ['preparing', 'ready'].includes(s) ? 'blue' : s === 'saved' ? 'amber' : '';
const orderedStatuses = ['new', 'saved', 'preparing', 'ready', 'applied', 'interview', 'rejected', 'archived'];
const options = (items, current, blank) => (blank !== undefined ? `<option value="">${esc(blank)}</option>` : '') + items.map(i => {
  let [v, l] = Array.isArray(i) ? i : [i, cap(i)];
  return `<option value="${esc(v)}" ${v===current?'selected':''}>${esc(l)}</option>`;
}).join('');
const S = {
  view: 'discover',
  tab: 'overview',
  country: '',
  query: '',
  status: '',  // one stage or several, comma-separated
  experience: 'fits,stretch',  // fits, stretch and/or above; '' = every level
  posted: '',  // days: '1', '3', '7', '30'
  minScore: '',
  remote: false,
  sort: 'match',
  listKey: '',
  markUndo: new Map(),
  jobs: [],
  selected: null,
  details: new Map(),
  buffers: new Map(),
  tasks: new Map(),
  loadingDetails: new Set(),
  detailFailures: new Map(),
  profile: null,
  profileDirty: false,
  profileRaw: {},
  settingsDirty: false,
  catalogTab: 'employers',
  catalogQuery: '',
  catalogMode: 'all',
  limit: 80,
  emails: [],
  emailNext: '',
  emailQuery: 'newer_than:90d (job OR jobs OR vacancy OR vacancies)',
  generation: 0,
  theme: 'light',
  chats: null,  // AI chat conversations, loaded when the chat opens
  chat: null,
  chatJob: null,  // vacancy a new chat is about
  chatProfile: true,
  chatDraft: '',
  chatPending: null,
  aiModels: {},
  mail: null,  // applications found in your mail
  mailDays: 365,
  emailUnread: []  // job-alert emails the rules could not read
};
const titles = {
  discover: ['Discover', 'Every collected vacancy in your search.'],
  'for-you': ['For you', 'Target and related roles that fit your experience.'],
  saved: ['Saved', 'Opportunities you bookmarked.'],
  applications: ['Applications', 'Everything you are preparing or have sent.'],
  chat: ['AI chat', 'Ask about vacancies, applications, interviews and emails.'],
  profile: ['Profile & writing', 'The facts and voice your AI writes from. Used only after you save.'],
  sources: ['Source library', 'Employers and job boards you can collect from.'],
  mail: ['Job alerts', 'Turn job-alert emails into a reviewed shortlist, and find the applications you have sent.'],
  settings: ['Settings & activity', 'AI assistant, configured sources and collection results.']
};
// On a phone (served by the PC over home Wi-Fi) the page carries no token:
// the phone pairs once with a code shown on the PC and keeps its own token.
const REMOTE = document.body.dataset.remote === '1';
// Set from /api/bootstrap: Linux wording for menus, installer and firewall.
let LINUX = false;
const DEVICE_KEY = 'job-agent-device';
const THEME_KEY = 'job-agent-theme';
const local = {
  get(key) {
    try { return localStorage.getItem(key); } catch { return null; }
  },
  set(key, value) {
    try { value == null ? localStorage.removeItem(key) : localStorage.setItem(key, value); } catch { /* private mode: this visit only */ }
  }
};
let token = REMOTE ? (local.get(DEVICE_KEY) || '') : $('meta[name=session-token]').content;
async function api(path, {
  method = 'GET',
  body
} = {}) {
  const headers = {
    'X-Session-Token': token
  };
  if (body !== undefined && !(body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(body);
  }
  const response = await fetch('/api' + path, {
    method,
    headers,
    body,
    cache: 'no-store'
  });
  let data;
  try {
    data = await response.json();
  } catch {
    throw Error('The local service returned an unreadable response. Reopen the app if this continues.');
  }
  if (!response.ok && REMOTE && response.status === 403 && data.pair) {
    // Removed on the PC, or never paired: ask for a new code.
    local.set(DEVICE_KEY, null);
    token = '';
    pairScreen();
    const error = Error(data.error);
    error.pair = true;
    throw error;
  }
  if (!response.ok) {
    const error = Error(data.error || `This action failed (${response.status}).`);
    error.data = data;
    throw error;
  }
  return data;
}

async function changeSettings(path, method, body) {
  if (S.settingsBusy) throw Error('Your source settings are still being saved. Try again when that finishes.');
  S.settingsBusy = true;
  if ($('#country')) $('#country').disabled = true;
  try {
    return await api(path, {
      method,
      body
    });
  } finally {
    S.settingsBusy = false;
    if ($('#country')) $('#country').disabled = false;
  }
}

function adoptSourceSettings(settings) {
  // Rebase untouched settings on new country/library sources while keeping any
  // edits made during the request (including an unfinished advanced JSON edit).
  if (S.settingsDraft && S.settingsDirty) {
    for (const [key, value] of Object.entries(settings)) {
      if (JSON.stringify(S.settingsDraft[key]) === JSON.stringify(S.boot.settings[key])) {
        S.settingsDraft[key] = clone(value);
      }
    }
  } else delete S.settingsDraft;
  S.boot.settings = settings;
}
let toastTimer;

// extra: an optional button such as Undo, kept clickable while the rest of
// the message lets clicks through. A message with one stays a little longer.
function toast(message, error = false, extra = '') {
  clearTimeout(toastTimer);
  $('#notifications').innerHTML = `<div class="toast ${error?'error':''}"><span>${esc(message)}</span>${extra}${button('×','dismiss-toast','quiet','aria-label="Dismiss notification"')}</div>`;
  toastTimer = setTimeout(() => $('#notifications').replaceChildren(), error ? 12000 : extra ? 8000 : 4000);
}

// After a stage change the list reloads, so a job the filters no longer
// match (only "New" shown, then marked Applied) leaves the list at once.
async function setStage(id, status) {
  const saved = await api(`/jobs/${id}/stage`, {method: 'POST', body: {status}});
  for (const job of S.jobs.filter(item => item.id === id)) job.status = saved.status;
  const buffer = S.buffers.get(id);
  if (buffer) buffer.status = saved.status;
  const detail = S.details.get(id);
  if (detail) detail.job.status = saved.status;
  await loadJobs();
  if (S.selected === id) renderDetail();
  return saved.status;
}

// Background work that is safe to interrupt: it simply completes next time.
const RESUMABLE_TASKS = ['rematch:', 'load-details:'];

function dirty() {
  return S.profileDirty || S.settingsDirty || [...S.buffers.values()].some(b => b.dirty.size) ||
    [...S.tasks.keys()].some(key => !RESUMABLE_TASKS.some(prefix => key.startsWith(prefix)));
}
window.hasUnsavedChanges = dirty;
// A new renderer starts beyond the previous renderer's sequence after reload.
let closeStateRevision = Date.now() * 1000;

function nativeBridgeCall(name, args) {
  const pv = window.pywebview;
  if (typeof pv?.api?.[name] === 'function') return Promise.resolve(pv.api[name](...args));
  // On Linux (WebKitGTK, Qt) pywebview builds pywebview.api with new Function,
  // which this page's Content-Security-Policy forbids. Its message bridge
  // needs no eval, so call the same Python method through it directly.
  if (typeof pv?._jsApiCallback !== 'function' || typeof pv._checkValue !== 'function' || !pv._returnValuesCallbacks) return null;
  if (pv.platform === 'qtwebengine' && !pv._QWebChannel) {
    // The step that throws also skipped opening Qt's channel; open it here.
    if (typeof QWebChannel !== 'function' || !window.qt?.webChannelTransport) return null;
    if (!pv._jobAgentChannel) pv._jobAgentChannel = new QWebChannel(qt.webChannelTransport, channel => {
      pv._QWebChannel = channel;
      syncNativeCloseState();
    });
    return null;
  }
  const id = String(Math.random()).slice(2);
  pv._returnValuesCallbacks[name] ||= {};
  const result = new Promise((resolve, reject) => pv._checkValue(name, resolve, reject, id));
  pv._jsApiCallback(name, args, id);
  return result;
}

function syncNativeCloseState() {
  // Cache state before closing: asking JavaScript from the native close callback
  // blocks WebView2's UI thread. Revisions also reject out-of-order bridge calls.
  const sent = nativeBridgeCall('update_close_state', [token, dirty(), closeStateRevision + 1]);
  if (!sent) return;
  closeStateRevision += 1;
  sent.catch(() => {});
}
window.addEventListener('pywebviewready', syncNativeCloseState);
window.addEventListener('beforeunload', e => {
  if (dirty() && !window.pywebview?.api) {
    e.preventDefault();
    e.returnValue = '';
  }
});

function updateStatus() {
  syncNativeCloseState();
  const el = $('#task-status');
  if (!el) return;
  const tasks = [...S.tasks.values()];
  el.innerHTML = tasks.length ? `<i class="spinner"></i><span>${esc(tasks.at(-1))}${tasks.length>1?` · ${tasks.length} actions`:''}</span>` : `<span class="dot"></span><span>${dirty()?'You have unsaved edits':'All saved. Your workspace is local.'}</span>`;
}
async function task(action, extra = {}, done, onPartial) {
  const key = action + ':' + (extra.job_id || '');
  if (S.tasks.has(key)) {
    toast('This action is already running. You can keep browsing.');
    return;
  }
  S.tasks.set(key, cap(action) + '…');
  updateStatus();
  try {
    const {
      task_id
    } = await api('/tasks', {
      method: 'POST',
      body: {
        action,
        ...extra
      }
    });
    const result = await pollTask(task_id, key, onPartial);
    await done?.(result);
    return result;
  } finally {
    S.tasks.delete(key);
    updateStatus();
  }
}
async function pollTask(id, key, onPartial) {
  for (;;) {
    const state = await api('/tasks/' + encodeURIComponent(id));
    if (state.status === 'done') return state.result;
    if (state.status === 'error') throw Error(state.error || state.message || 'The action could not finish.');
    S.tasks.set(key, state.message || 'Working…');
    if (onPartial && state.partial !== undefined) onPartial(state.partial);
    updateStatus();
    await new Promise(r => setTimeout(r, onPartial ? 350 : 700));
  }
}

function modal(title, body, footer = '') {
  const d = $('#modal');
  if (d.open) closeModal();
  modalReturnFocus = document.activeElement;
  $('#modal-content').innerHTML = `<header class="modal-head"><h2 id="modal-title">${esc(title)}</h2>${button(icon('close'),'close-modal','icon-only','aria-label="Close dialog"')}</header><div class="modal-body">${body}</div>${footer?`<footer class="modal-footer">${footer}</footer>`:''}`;
  d.showModal();
  const first = $('input:not([type=checkbox]),textarea,select', d);
  if (first) first.focus();
}
let modalCleanup = null,
  modalReturnFocus = null;

function closeModal() {
  const cleanup = modalCleanup;
  modalCleanup = null;
  cleanup?.();
  $('#modal').close();
}
$('#modal').addEventListener('close', () => {
  if ($('#modal').open) return;
  const cleanup = modalCleanup;
  modalCleanup = null;
  cleanup?.();
  if (modalReturnFocus?.isConnected) modalReturnFocus.focus({
    preventScroll: true
  });
});

function heading() {
  const [title, sub] = titles[S.view];
  return `<div class="page-heading"><div><h1>${title}</h1><p>${sub}</p></div><div class="heading-actions">${S.view==='profile'?button('Save profile','save-profile','primary'):S.view==='sources'?pill(`${S.boot.catalog.length} directory employers · ${S.boot.catalog.filter(e=>e.source).length} collectors`):''}</div></div>`;
}

function shell() {
  const nav = [
    ['discover', 'Discover', 'discover'],
    ['for-you', 'For you', 'spark'],
    ['saved', 'Saved', 'saved'],
    ['applications', 'Applications', 'applications'],
    ['chat', 'AI chat', 'chat'],
    ['profile', 'Profile & writing', 'profile'],
    ['sources', 'Source library', 'sources'],
    ['mail', 'Job alerts', 'mail'],
    ['settings', 'Settings & activity', 'settings']
  ];
  $('#app').innerHTML = `<div class="shell ${REMOTE?'remote':''}"><aside class="sidebar"><div class="brand"><span class="brand-mark">J</span><span class="brand-copy">Job Agent</span>${button(icon('sun'),'theme','icon-only quiet','aria-label="Switch light or dark theme" title="Switch theme (Ctrl Shift L)"')}${button(icon('help'),'help','icon-only quiet','aria-label="Help and shortcuts" title="Help and shortcuts"')}</div>${button(icon('search')+'<span>Quick actions</span><kbd>Ctrl K</kbd>','commands','command-trigger','aria-label="Quick actions" title="Quick actions (Ctrl K)"')}<label class="country-control" title="Search country">${icon('globe')}<select id="country" aria-label="Search country" ${S.settingsBusy?'disabled':''}>${options(Object.entries(S.boot.countries),S.country,'All countries')}</select></label><nav aria-label="Main navigation">${nav.map(([id,label,i],n)=>`${n===5?'<div class="nav-label">You</div>':''}<button class="nav-item ${id===S.view?'active':''}" data-view="${id}" title="${label}" aria-label="${label}" ${id===S.view?'aria-current="page"':''}>${icon(i)}<span>${label}</span></button>`).join('')}<button class="nav-item nav-more ${['chat','profile','sources','mail','settings'].includes(S.view)?'active':''}" data-action="more-menu" title="More" aria-label="More">${icon('more')}<span>More</span></button></nav><div class="sidebar-bottom">${refreshCard()}</div></aside><div class="workspace"><main id="main" tabindex="-1" class="page ${isDiscovery()?'discovery':''} ${S.view==='chat'?'chat-view':''}"></main><footer class="statusbar"><div id="task-status" class="task-indicator" role="status" aria-live="polite"></div><div class="hints" aria-hidden="true"><span><kbd>J</kbd><kbd>K</kbd>move</span><span><kbd>/</kbd>search</span><span><kbd>Ctrl K</kbd>actions</span><span><kbd>Ctrl S</kbd>save</span></div><span class="right">${esc(aiShort())} · you review every application</span></footer></div></div>`;
  renderPage();
  updateStatus();
}

function isDiscovery(view = S.view) {
  return ['discover', 'for-you', 'saved', 'applications'].includes(view);
}

// How long ago, in words: "just now", "3 hours ago", "yesterday", "2 weeks ago".
function ago(value) {
  const time = Date.parse(value || '');
  if (!time) return '';
  const minutes = Math.max(0, (Date.now() - time) / 60000);
  const unit = (n, word) => `${n} ${word}${n === 1 ? '' : 's'} ago`;
  if (minutes < 1) return 'just now';
  if (minutes < 60) return unit(Math.round(minutes), 'minute');
  if (minutes < 1440) return unit(Math.round(minutes / 60), 'hour');
  const days = Math.floor(minutes / 1440);
  if (days === 1) return 'yesterday';
  if (days < 7) return unit(days, 'day');
  if (days < 30) return unit(Math.floor(days / 7), 'week');
  if (days < 365) return unit(Math.floor(days / 30), 'month');
  return unit(Math.floor(days / 365), 'year');
}

// The card's age: when the employer posted it, or when the app found it.
function postedLabel(j) {
  const value = j.published_at || j.discovered_at;
  const time = Date.parse(value || '');
  if (!time) return ['', ''];
  const day = new Date(time).toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: 'numeric'});
  return [ago(value), j.published_at ? `Posted ${day}` : `Found by the app ${day} (no posting date given)`];
}

function refreshCard() {
  const r = S.boot.collection;
  const failed = r?.sources?.filter(s => !s.ok).length || 0;
  const when = r?.completed_at || r?.started_at;
  const status = when ? `Refreshed ${ago(when)}` : 'Not refreshed yet';
  const detail = r ? `${r.sources?.length||0} sources checked${failed?` · ${failed} need attention`:''}` : 'Choose a country, then find opportunities.';
  return `<div class="refresh-card"><strong title="${esc(status)}"><span class="dot ${failed||!when?'amber':''}"></span><span>${esc(status)}</span></strong><small>${esc(detail)}</small>${button(icon('spark')+'Find opportunities','collect','','title="Find opportunities"')}</div>`;
}

function renderPage() {
  const main = $('#main');
  main.innerHTML = isDiscovery() || S.view === 'chat' ? '' : heading();
  if (isDiscovery()) renderDiscovery();
  else if (S.view === 'chat') renderChat();
  else if (S.view === 'profile') renderProfile();
  else if (S.view === 'sources') renderSources();
  else if (S.view === 'settings') renderSettings();
  else renderMail();
}

function coverageNotice() {
  const c = S.coverage,
    s = S.summary;
  if (!c) return '';
  const search = s ? `${s.in_scope} collected vacancies from ${s.employers} employers in this search.` : '';
  const experience = S.profile.years_of_experience == null ? 'Add your years of experience in Profile & writing to screen seniority.' : `Screening against ${S.profile.years_of_experience} years of experience.`;
  const filtered = S.view === 'for-you' && s ? `${s.role_matches} role matches · ${s.experience_mismatches} above your experience. ` : '';
  const run = c.last_run ? `${c.checked} sources checked in the last run${c.failed?`, ${c.failed} failed`:''}${c.limited?`, ${c.limited} reached a scan limit`:''}.` : 'Run a fresh collection to check coverage for this country.';
  // One quiet line; the full explanation stays one click away.
  const sites = (c.job_sites || []).filter(x => x.enabled).map(x => x.name);
  if (c.nav_index) sites.unshift('NAV');
  const line = [isDiscovery() && s ? `${s.in_scope.toLocaleString()} vacancies · ${s.employers} employers` : `${c.configured_collectors} of ${c.available_collectors} collectors configured`,
    S.view === 'for-you' && s ? `${s.experience_mismatches} above your experience` : '',
    `${sites.length} job site${sites.length===1?'':'s'} + ${c.configured_collectors} employer boards`,
    c.last_run ? `${c.checked} sources checked${c.failed?`, ${c.failed} failed`:''}` : 'not collected yet'
  ].filter(Boolean).join(' · ');
  const market = sites.length ? `Whole-market job sites: ${sites.join(', ')}; ${c.searches} searches per refresh from your saved searches and roles.` : 'No whole-market job sites are on; turn them on in Settings & activity.';
  const nav = c.nav_index ? ` NAV index: ${c.nav_index.active.toLocaleString()} active Norwegian vacancies.` : '';
  return `<details class="coverage-notice" ${S.coverageOpen?'open':''}><summary><span class="dot ${c.failed||!c.last_run?'amber':''}"></span><span class="line">${esc(line)}</span><span class="more">Details</span></summary><div class="coverage-body"><strong>${esc(market)}${esc(nav)}</strong><p>${c.configured_collectors} configured collectors · ${c.available_collectors} available employer collectors. ${esc(search)} ${esc(filtered)}${esc(experience)} ${esc(run)}</p>${button('Manage sources','go-sources','quiet')}</div></details>`;
}
document.addEventListener('toggle', e => {
  if (e.target.matches?.('.coverage-notice')) S.coverageOpen = e.target.open;
}, true);

const SORTS = [['match', 'Best match'], ['score', 'Highest match %'], ['newest', 'Newest posted'], ['found', 'Recently found'], ['deadline', 'Deadline soonest'], ['company', 'Company A–Z']];
const DEFAULT_EXPERIENCE = 'fits,stretch';
const EXPERIENCE_LABELS = {fits: 'Fits my experience', stretch: 'Stretch', above: 'Above my experience'};
const POSTED_LABELS = {'1': 'Posted in the last 24 hours', '3': 'Posted in the last 3 days', '7': 'Posted in the last week', '30': 'Posted in the last month'};

function experienceView() {
  return ['discover', 'for-you'].includes(S.view);
}

// Filters other than the search box and sort, each removable as a chip.
function activeFilters() {
  const active = (S.status ? S.status.split(',') : []).map(stage => ({key: 'status:' + stage, label: cap(stage)}));
  if (experienceView() && S.experience !== DEFAULT_EXPERIENCE) active.push({key: 'experience', label: S.experience ? S.experience.split(',').map(g => EXPERIENCE_LABELS[g]).join(' + ') : 'All experience levels'});
  if (S.posted) active.push({key: 'posted', label: POSTED_LABELS[S.posted]});
  if (S.minScore) active.push({key: 'score', label: `Match ${S.minScore}% or more`});
  if (S.remote) active.push({key: 'remote', label: 'Remote only'});
  return active;
}

function filtersOn() {
  return Boolean(S.query || activeFilters().length || S.sort !== 'match');
}

function filtersDialog() {
  const stages = S.status ? S.status.split(',') : [];
  const groups = S.experience ? S.experience.split(',') : Object.keys(EXPERIENCE_LABELS);
  const check = (name, value, label, on) => `<label class="check-row"><input type="checkbox" name="${name}" value="${value}" ${on ? 'checked' : ''}> ${label}</label>`;
  const radio = (name, value, label, on) => `<label class="check-row"><input type="radio" name="${name}" value="${value}" ${on ? 'checked' : ''}> ${label}</label>`;
  modal('Filters', `<form id="filter-form" class="filter-form"><fieldset><legend>Stage</legend><div class="filter-options">${orderedStatuses.map(s => check('stage', s, cap(s), stages.includes(s))).join('')}</div><small>Tick several to see them together. None ticked shows every stage.</small></fieldset>${experienceView() ? `<fieldset><legend>Experience</legend><div class="filter-options">${check('experience', 'fits', 'Fits my experience', groups.includes('fits'))}${check('experience', 'stretch', 'Stretch: up to a year more', groups.includes('stretch'))}${check('experience', 'above', 'Above my experience', groups.includes('above'))}</div></fieldset>` : ''}<fieldset><legend>Posted</legend><div class="filter-options">${[['', 'Any time'], ['1', 'Last 24 hours'], ['3', 'Last 3 days'], ['7', 'Last week'], ['30', 'Last month']].map(([v, l]) => radio('posted', v, l, S.posted === v)).join('')}</div></fieldset><fieldset><legend>Match</legend><div class="filter-options">${[['', 'Any match'], ['50', '50% or more'], ['70', '70% or more'], ['85', '85% or more']].map(([v, l]) => radio('score', v, l, S.minScore === v)).join('')}</div></fieldset><fieldset><legend>Work</legend><div class="filter-options">${check('remote', '1', 'Remote only', S.remote)}</div></fieldset></form>`,
    button('Clear all', 'clear-filters', 'quiet') + button('Show results', 'apply-filters', 'primary'));
}

function renderDiscovery() {
  const filtered = filtersOn();
  const active = activeFilters();
  const select = (id, label, items, value, on) => `<select id="${id}" aria-label="${label}" class="${on?'on':''}">${items}</select>`;
  $('#main').insertAdjacentHTML('beforeend', `<div class="results-layout ${S.selected&&S.showDetail?'has-selection':''}"><section class="results-panel" aria-label="Opportunities"><div class="list-head"><div class="list-title"><h1>${titles[S.view][0]}</h1><span class="count">${listCount()}</span><div class="heading-actions">${button(icon('plus'),'add-job','icon-only quiet','aria-label="Add opportunity" title="Add an opportunity you found"')}</div></div><div class="filterbar"><div class="search">${icon('search')}<input id="job-search" type="search" aria-label="Search opportunities" placeholder="Search roles, companies, skills" value="${esc(S.query)}"><kbd>/</kbd></div>${button(icon('filter')+'Filters'+(active.length?` <span class="badge">${active.length}</span>`:''),'open-filters','filter-button button-icon'+(active.length?' on':''),'aria-haspopup="dialog"')}${select('sort','Sort opportunities',options(SORTS,S.sort),S.sort,S.sort!=='match')}${filtered?button('Reset','reset-filters','quiet'):''}</div>${active.length?`<div class="filter-chips" aria-label="Active filters">${active.map(f=>button(esc(f.label)+icon('close'),'remove-filter','chip',`data-filter="${esc(f.key)}" aria-label="Remove filter: ${esc(f.label)}" title="Remove this filter"`)).join('')}</div>`:''}</div>${coverageNotice()}<div id="job-results"></div></section><section id="job-detail" class="detail" aria-label="Opportunity details"></section></div>`);
  renderCards();
  renderDetail();
}

function listCount() {
  const n = S.summary?.shown ?? S.jobs.length;
  const where = S.country ? ' in ' + esc(S.boot.countries[S.country]) : '';
  return S.view === 'for-you' ? `${n} fit you${where} · ${S.summary?.shown_employers||0} employers` : `${n.toLocaleString()} shown${where}`;
}

const stageTones = {
  saved: 'blue',
  preparing: 'amber',
  ready: 'violet',
  applied: 'green',
  interview: 'green'
};

// What your mailbox says about a vacancy you have not marked yet.
const MAIL_OUTCOMES = {applied: ['Applied', 'blue'], interview: ['Interview', 'green'], rejected: ['Rejected', '']};
const shortDate = value => value ? new Date(value).toLocaleDateString(undefined, {day: 'numeric', month: 'short', year: new Date(value).getFullYear() === new Date().getFullYear() ? undefined : 'numeric'}) : '';
const mailPill = e => `<span class="pill ${MAIL_OUTCOMES[e.kind]?.[1] || ''}" title="From your email: applied ${esc(shortDate(e.applied_at))}${e.kind !== 'applied' ? ', ' + esc(e.kind) + ' ' + esc(shortDate(e.received_at)) : ''}">${icon('mail')} ${MAIL_OUTCOMES[e.kind]?.[0] || 'Applied'} ${esc(shortDate(e.kind === 'applied' ? e.applied_at : e.received_at))}</span>`;

function mailNotice(mail) {
  if (!mail?.length) return '';
  const mine = mail.filter(m => m.this_job), others = mail.filter(m => !m.this_job);
  const line = m => `${esc(MAIL_OUTCOMES[m.kind]?.[0] || cap(m.kind))} ${esc(shortDate(m.received_at))}${m.position ? ' · ' + esc(m.position) : ''}${m.subject ? ` <small>(“${esc(m.subject)}”)</small>` : ''}`;
  return `<div class="notice mail-notice">${icon('mail')}<div>${mine.length ? `<strong>Your email shows you applied for this job.</strong>${mine.map(m => `<p>${line(m)}</p>`).join('')}` : ''}${others.length ? `<strong>${mine.length ? 'Other applications to this employer' : 'You have applied to this employer before'}</strong>${others.slice(0, 5).map(m => `<p>${line(m)}</p>`).join('')}` : ''}</div></div>`;
}

function jobTags(j) {
  const e = j.match?.experience || {};
  const tags = [];
  if (j.status !== 'new' && !quickMarks().includes(j.status)) tags.push(pill(cap(j.status), stageTones[j.status] || ''));
  if (j.email && ['new', 'saved', 'preparing', 'ready'].includes(j.status)) tags.push(mailPill(j.email));
  if (e.status === 'mismatch') tags.push(pill(e.minimum_years != null ? `${e.minimum_years}+ yrs asked` : 'Above your level', 'amber'));
  else if (e.status === 'stretch') tags.push(pill(`Stretch: ${e.minimum_years}+ yrs`, 'blue'));
  else if (j.match?.track === 'horizon' && S.view !== 'for-you') tags.push(pill('Related role'));
  const gaps = j.match?.language_gaps || [];
  if (gaps.length) tags.push(pill(`${gaps.join(', ')} required`, 'amber'));
  return tags.join('');
}

// Display names for where a vacancy was found.
const SITE_LABELS = {nav: 'NAV', arbeitsagentur: 'Arbeitsagentur', arbeitnow: 'Arbeitnow', djinni: 'Djinni', dou: 'DOU',
  remotive: 'Remotive', remoteok: 'Remote OK', himalayas: 'Himalayas', hackernews: 'Hacker News', adzuna: 'Adzuna',
  linkedin: 'LinkedIn', gmail: 'job-alert email'};
const siteLabel = source => SITE_LABELS[source] || 'employer site';
// One job's other postings: copies on other sites, re-posts, other cities.
function alsoOn(j) {
  const others = j.also_on || [];
  if (!others.length) return '';
  const n = others.length;
  const sitesOnly = others.every(o => o.source !== j.source && (!o.location || o.location === j.location));
  const where = o => siteLabel(o.source) + (o.location && o.location !== j.location ? ' – ' + o.location : '');
  const title = 'Also posted: ' + others.slice(0, 12).map(where).join('; ') + (n > 12 ? '…' : '');
  return `<span class="also-on" title="${esc(title)}"> · +${n} ${sitesOnly ? 'site' + (n > 1 ? 's' : '') : 'more posting' + (n > 1 ? 's' : '')}</span>`;
}

function marketBar() {
  const q = S.query.trim();
  if (!['discover', 'for-you'].includes(S.view) || q.length < 2) return '';
  const saved = (S.boot.settings.market?.searches || []).some(x => x.toLowerCase() === q.toLowerCase());
  const busy = S.tasks.has('market-search:');
  const text = busy ? `Searching job sites for “${esc(q)}”… results appear here as they are saved.` :
    `These are adverts already collected. Search the whole market for “${esc(q)}” too.`;
  return `<div class="market-bar">${icon('search')}<span class="market-text">${text}</span><span class="market-actions">${button(busy?'Searching…':'Search job sites','market-search','primary',busy?'disabled':'title="Search every enabled job site for this now"')}${saved?pill('Saved search','green'):button('Save search','save-search','quiet','title="Include this search in every refresh"')}</span></div>`;
}

// One-click stage buttons on each card, chosen in Settings & activity.
function quickMarks() {
  return S.boot?.appearance?.quick_marks || ['applied', 'rejected'];
}
const MARK_ICONS = {saved: 'saved', preparing: 'file', ready: 'check', applied: 'check', interview: 'profile', rejected: 'close', archived: 'file'};

function markButtons(j) {
  return `<div class="quick-marks">${quickMarks().map(mark => {
    const on = j.status === mark;
    return `<button type="button" class="mark ${on ? 'on mark-' + mark : ''}" data-action="quick-mark" data-id="${j.id}" data-mark="${mark}" aria-pressed="${on}" aria-label="${on ? `Marked ${cap(mark)}: click to undo` : `Mark as ${cap(mark)}`}: ${esc(j.title)}" title="${on ? `Marked ${cap(mark)}. Click to undo.` : `Mark as ${cap(mark)}`}">${icon(MARK_ICONS[mark] || 'check')}<span>${cap(mark)}</span></button>`;
  }).join('')}</div>`;
}

function renderCards() {
  const root = $('#job-results');
  if (!root) return;
  const scroll = root.scrollTop;  // redrawing the cards keeps your place
  root.innerHTML = marketBar() + (S.jobs.length ? S.jobs.map(j => {
    const score = j.match?.score,
      tier = score == null ? '' : score >= 70 ? 'high' : score >= 45 ? 'mid' : '';
    const selected = S.selected === j.id;
    const [age, posted] = postedLabel(j);
    return `<div class="job-row ${selected?'selected':''}"><button type="button" class="job-card ${selected?'selected':''}" data-job="${j.id}" aria-pressed="${selected}"><span class="score ${tier}" title="Fit with your profile: role 50%, skills 30%, experience 20%">${score??'–'}</span><span class="title-line"><span class="sr-only">${score==null?'No match score. ':score+'% fit. '}</span>${esc(j.title)}</span><span class="age" title="${esc(posted)}"><span class="sr-only">${esc(posted)}. </span>${esc(age)}</span><span class="job-sub">${esc(j.company||'Employer not listed')} · ${esc(j.location||'Location to confirm')}${alsoOn(j)}</span><span class="job-tags">${jobTags(j)}</span></button>${markButtons(j)}</div>`;
  }).join('') + (S.summary?.has_more ? `<div class="empty">${button(`Show ${Math.min(PAGE_SIZE,(S.summary.shown||0)-S.jobs.length)} more of ${(S.summary.shown||0).toLocaleString()}`, 'more-jobs', 'quiet')}</div>` : '') : `<div class="empty"><div class="empty-icon">${icon('discover')}</div><h2>${filtersOn()?'Nothing matches these filters':'No opportunities yet'}</h2><p>${S.query?'Nothing collected matches yet. Search job sites above, try other words, or reset the filters.':filtersOn()?'Try other filters or reset them. Your saved work is unaffected.':'Choose a country and find opportunities, or add a vacancy you found yourself.'}</p>${button(filtersOn()?'Reset filters':'Find opportunities',filtersOn()?'reset-filters':'collect',S.query?'':'primary')}</div>`);
  root.scrollTop = scroll;
}
// The server pages lists; the database may hold tens of thousands of vacancies.
const PAGE_SIZE = 80;

function listFilters() {
  return {
    country: S.country,
    query: S.query,
    status: S.status,
    sort: S.sort,
    experience: experienceView() ? (S.experience || 'all') : 'all',
    posted: S.posted || '0',
    min_score: S.minScore || '0',
    remote: S.remote ? '1' : '0',
    view: S.view
  };
}

function listParams(offset, limit = PAGE_SIZE) {
  return new URLSearchParams({...listFilters(), offset, limit});
}

async function loadMore() {
  if (!S.summary?.has_more || S.loadingMore) return;
  const version = S.generation;
  S.loadingMore = true;
  try {
    const {jobs, summary} = await api('/jobs?' + listParams(S.jobs.length));
    if (version !== S.generation) return;
    const known = new Set(S.jobs.map(j => j.id));
    S.jobs = S.jobs.concat(jobs.filter(j => !known.has(j.id)));
    S.summary = summary;
  } finally {
    S.loadingMore = false;
  }
  renderCards();
}

async function loadJobs() {
  // A delayed search reload or a finished job-site search may arrive after
  // the user has moved to a page without a vacancy list.
  if (!['discover', 'for-you', 'saved', 'applications'].includes(S.view)) return;
  const version = ++S.generation;
  // Reloading the same list (after saving a note or a stage) keeps every
  // page you loaded and your scroll position; a new filter starts at the top.
  const key = JSON.stringify(listFilters());
  const same = key === S.listKey && S.jobs.length > 0;
  const listScroll = same ? ($('#job-results')?.scrollTop || 0) : 0;
  const {
    jobs,
    summary
  } = await api('/jobs?' + listParams(0, same ? Math.min(500, Math.max(PAGE_SIZE, S.jobs.length)) : PAGE_SIZE));
  if (version !== S.generation) return;
  S.listKey = key;
  S.jobs = jobs;
  S.summary = summary;
  if (!S.selected && jobs.length) await selectJob(jobs[0].id, false);
  else if (S.selected && !S.details.has(S.selected)) await selectJob(S.selected, false);
  if (version !== S.generation) return;
  if ($('#job-results')) {
    const focused = document.activeElement,
      focusId = focused?.id;
    const selection = focused instanceof HTMLInputElement ? [focused.selectionStart, focused.selectionEnd] : null;
    const detailScroll = $('#detail-content')?.scrollTop || 0;
    renderPage();
    if ($('#detail-content')) $('#detail-content').scrollTop = detailScroll;
    if ($('#job-results')) $('#job-results').scrollTop = listScroll;
    const replacement = focusId ? document.getElementById(focusId) : null;
    if (replacement) {
      replacement.focus({
        preventScroll: true
      });
      if (selection && selection[0] !== null) replacement.setSelectionRange(...selection);
    }
  }
}

async function refreshCoverage() {
  const country = S.country;
  const coverage = await api('/discovery/coverage?' + new URLSearchParams({
    country
  }));
  if (country === S.country) S.coverage = coverage;
}
async function selectJob(id, render = true) {
  S.selected = Number(id);
  if (!S.details.has(S.selected)) {
    const data = await api('/jobs/' + S.selected);
    S.details.set(data.job.id, data);
    if (!S.buffers.has(data.job.id)) S.buffers.set(data.job.id, {
      notes: data.job.notes || '',
      status: data.job.status,
      letter: data.letters[0]?.content || '',
      app: clone(data.preparations[0]?.payload || null),
      dirty: new Set(),
      revision: 0
    });
  }
  if (render) {
    // On narrow windows an explicit choice shows the detail instead of the list.
    S.showDetail = true;
    renderCards();
    renderDetail();
    $('.results-layout')?.classList.add('has-selection');
  }
}

async function moveSelection(step) {
  const index = S.jobs.findIndex(j => j.id === S.selected);
  // Moving past the loaded page fetches the next one first.
  if (index + step >= S.jobs.length - 1 && S.summary?.has_more) await loadMore();
  const next = Math.min(S.jobs.length - 1, Math.max(0, index < 0 ? 0 : index + step));
  await selectJob(S.jobs[next].id);
  const row = $(`[data-job="${S.jobs[next].id}"]`);
  row?.scrollIntoView({block: 'nearest'});
  row?.focus({preventScroll: true});
}

function current() {
  return S.buffers.get(S.selected);
}

function mark(part) {
  const b = current();
  if (b) {
    b.dirty.add(part);
    b.revision++;
  }
  updateStatus();
}

function renderDetail() {
  const root = $('#job-detail');
  if (!root) return;
  const data = S.details.get(S.selected),
    b = current();
  if (!data || !b) {
    root.innerHTML = `<div class="empty"><div class="empty-icon">${icon('file')}</div><h2>Select an opportunity</h2><p>Its details, your notes, cover letter and application answers open here. Use J and K to move through the list.</p></div>`;
    return;
  }
  const j = data.job;
  root.innerHTML = `<div class="detail-head">${button(icon('back')+' All opportunities','back-list','quiet back-list button-icon')}<div class="detail-company"><span class="company-mark">${esc((j.company||'Co').slice(0,2).toUpperCase())}</span>${esc(j.company||'Employer')}</div><h2>${esc(j.title)}</h2><div class="detail-meta"><span>${esc(j.location||'Location to confirm')}</span>${j.employment_type?`<span>${esc(j.employment_type)}</span>`:''}${j.deadline?`<span>Closes ${esc(j.deadline.slice(0,10))}</span>`:''}</div><div class="detail-actions">${S.tab!=='letter'&&j.is_active?button(icon('spark')+' Write cover letter','','primary button-icon','data-tab="letter"'):''}${button(icon('saved')+' '+(b.status==='saved'?'Saved':['new','saved'].includes(b.status)?'Save':cap(b.status)),'bookmark','button-icon',['new','saved'].includes(b.status)?'':'disabled title="Change stage in Notes & stage"')}${button(icon('chat')+' Ask AI','chat-about-job','button-icon')}${link(BOARD_SOURCES.includes(j.source)?'On '+siteLabel(j.source):'Original posting',j.source_url)}${rawOf(j).external_apply_url?link('Apply on employer site',rawOf(j).external_apply_url):BOARD_SOURCES.includes(j.source)?'':link('Apply on site',j.apply_url||j.source_url)}${(S.jobs.find(x=>x.id===j.id)?.also_on||[]).slice(0,4).map(o=>link('On '+siteLabel(o.source)+(o.location&&o.location!==j.location?' – '+o.location.split(/[;,]/)[0]:''),o.url)).join('')}</div><div class="tabs" role="tablist" aria-label="Opportunity workspace">${[['overview','Overview'],['notes','Notes & stage'],['letter','Cover letter'],['answers','Application'],['insights','AI insights']].map(([id,label])=>`<button role="tab" aria-label="${label}" id="tab-${id}" aria-selected="${S.tab===id}" aria-controls="detail-content" class="tab ${S.tab===id?'active':''}" data-tab="${id}">${label}${b.dirty.has(id==='answers'?'app':id)?' •':''}</button>`).join('')}</div></div><div id="detail-content" class="detail-body" role="tabpanel" aria-labelledby="tab-${S.tab}"></div>`;
  const body = $('#detail-content');
  if (!j.is_active || j.match?.expired) body.innerHTML = '<div class="notice warning">This posting is withdrawn or past its deadline. Your notes and drafts are still available.</div>';
  if (!S.jobs.some(item => item.id === j.id)) body.insertAdjacentHTML('beforeend', '<div class="notice">This opportunity is outside the current filters. Your editor stays open so you can finish your work.</div>');
  if (S.tab === 'overview') body.insertAdjacentHTML('beforeend', overview(j, data));
  else if (S.tab === 'notes') body.insertAdjacentHTML('beforeend', notes(b));
  else if (S.tab === 'letter') body.insertAdjacentHTML('beforeend', letter(b));
  else if (S.tab === 'insights') body.insertAdjacentHTML('beforeend', insights(data));
  else body.insertAdjacentHTML('beforeend', answers(b, data));
  if (needsDetails(j) && !S.loadingDetails.has(j.id) && !S.detailFailures.has(j.id)) loadDetails(j.id);
}

// Job sites whose lists omit the advert text (Djinni: the employer).
const BOARD_SOURCES = ['arbeitsagentur', 'arbeitnow', 'djinni', 'dou', 'remotive', 'remoteok', 'himalayas', 'hackernews', 'adzuna', 'linkedin'];
const needsDetails = j => Boolean(j.is_active) && ((['arbeitsagentur', 'linkedin'].includes(j.source) && !String(j.description || '').trim()) ||
  (j.source === 'djinni' && j.company === 'Company on Djinni'));

function rawOf(j) {
  try {
    const raw = typeof j.raw_json === 'string' ? JSON.parse(j.raw_json || '{}') : j.raw_json;
    return raw && typeof raw === 'object' ? raw : {};
  } catch {
    return {};
  }
}

async function loadDetails(id) {
  S.loadingDetails.add(id);
  try {
    await task('load-details', {job_id: id}, async () => {
      S.details.delete(id);
      if (S.selected === id) await selectJob(id, false);
    });
  } catch (err) {
    S.detailFailures.set(id, err.message);
  } finally {
    S.loadingDetails.delete(id);
    if (S.selected === id) renderDetail();
  }
}

function fitTiles(j) {
  const m = j.match || {},
    e = m.experience || {},
    skills = S.profile.skills || [],
    matched = m.matched_skills || [];
  const tile = (label, [tone, headline, detail]) => `<div class="fit"><small>${label}</small><strong class="${tone}">${esc(headline)}</strong><span>${esc(detail)}</span></div>`;
  const role = m.track === 'target' ? ['good', 'Target role', 'Matches ' + (m.matched_roles || []).join(', ')] :
    m.track === 'horizon' ? ['warn', 'Related role', (m.related_roles || []).join(', ') || 'Close to your target fields'] :
    ['', 'Other role', 'Not one of your target or related roles'];
  const years = e.candidate_years;
  const experience = e.status === 'stretch' ? ['warn', 'Stretch', `Asks for ${e.minimum_years}+ years · you have ${years}; worth applying if the rest fits`] :
    e.status === 'mismatch' ? ['bad', 'Experience mismatch', `${e.minimum_years!=null?`Asks for ${e.minimum_years}+ years`:`${cap(e.seniority)} title`} · you have ${years}`] :
    years == null ? ['warn', 'Not screened', 'Add your years of experience in Profile & writing'] :
    e.status === 'no_conflict' ? ['good', `${e.minimum_years}+ years asked`, `You have ${years}`] :
    e.minimum_years == null && (e.requirements || []).some(r => r.kind === 'unspecified') ?
      ['warn', 'Experience asked', `No number given · you have ${years}`] :
    ['warn', 'Needs review', e.minimum_years != null ? `Asks for ${e.minimum_years}+ years · you have ${years}` : 'No clear minimum stated'];
  const skill = skills.length ? [matched.length * 2 >= skills.length ? 'good' : 'warn', `${matched.length} of ${skills.length} skills`, matched.join(', ') || 'None of your skills are mentioned'] :
    ['', 'Skills not set', 'Add skills in Profile & writing'];
  return `<div class="fit-grid">${tile('Role', role)}${tile('Experience', experience)}${tile('Skills', skill)}${tile('Languages', languageFit(m.languages))}</div>`;
}

// One stated language: "Norwegian B2" or "English (fluent)".
function languageLabel(l) {
  const level = l.cefr && l.cefr !== l.level && l.cefr !== 'native' ? `${l.level} ≈ ${l.cefr}` : l.level;
  return level ? `${l.language} (${level})` : l.language;
}

function languageFit(langs) {
  if (!langs) return ['', 'Not screened', 'Refresh to read language requirements'];
  const required = langs.required || [], preferred = langs.preferred || [];
  const extra = preferred.length ? ` · Preferred: ${preferred.map(languageLabel).join(', ')}` : '';
  if (!required.length) return preferred.length ? ['good', 'None required', 'Preferred: ' + preferred.map(languageLabel).join(', ')] :
    ['', 'Not stated', 'No language requirement found in the advert'];
  const gaps = required.filter(l => l.status === 'gap'), checks = required.filter(l => l.status === 'check');
  const list = required.map(languageLabel).join(', ');
  if (gaps.length) return ['bad', 'Language gap', `Requires ${list}${extra}`];
  if (checks.length) return ['warn', 'Check level', `Requires ${list}${extra}`];
  return ['good', 'You meet them', `Requires ${list}${extra}`];
}

function languageDetails(langs) {
  if (!langs || !((langs.required || []).length || (langs.preferred || []).length)) return '';
  const status = {meets: 'meets your level', check: 'check your level', gap: 'not met by your profile'};
  const row = (kind, l) => `<p><strong>${kind}: ${esc(languageLabel(l))}</strong> — ${esc(status[l.status] || '')}${l.yours ? ` (you: ${esc(l.yours)})` : ''}<br><span class="sub">${esc(l.evidence)}</span></p>`;
  return `<details class="evidence"><summary>How languages were read</summary>${(langs.required || []).map(l => row('Required', l)).join('')}${(langs.preferred || []).map(l => row('Preferred', l)).join('')}<p class="sub">Levels are read from the advert's wording (≈ is the nearest CEFR level). Check the original advert before applying.</p></details>`;
}

function overview(j, data) {
  const m = j.match || {},
    e = m.experience;
  const experience = e ? `<details class="evidence"><summary>How experience was screened</summary>${e.reasons.map(reason=>`<p>${esc(reason)}</p>`).join('')}${(e.requirements||[]).map(r=>`<p>${esc(cap(r.kind))}: ${esc(r.evidence)}</p>`).join('')}</details>` : '';
  const failure = S.detailFailures.get(j.id);
  const missing = needsDetails(j) ? (failure ? `${siteLabel(j.source)} could not show the full advert: ${failure} Open it on the site instead.` :
    `Loading the full advert from ${siteLabel(j.source)}…`) : 'No full description was imported. Read the original posting for details.';
  const analysis = data?.insights?.analysis?.payload;
  return `${mailNotice(data?.mail)}${fitTiles(j)}${analysis ? keyFacts(analysis.key_facts, ['salary', 'remote_policy', 'visa_relocation', 'languages_required']) : ''}<div class="prose">${esc(String(j.description||'').trim()?j.description:missing)}</div>${experience}${languageDetails(m.languages)}<details class="evidence"><summary>Things to check before applying</summary>${(m.warnings||[]).map(w=>`<p>${esc(w)}</p>`).join('')}<p>${m.score==null?'No fit score yet: add target roles, skills or your years of experience.':`${m.score}% fit: role 50%, skills 30% (full credit at 8 of your skills), experience 20%.`} It estimates fit from the advert's text, not eligibility or hiring probability.</p></details>`;
}

// What the AI assistant found: analysis, key facts and interview preparation.
const VERDICTS = {strong: ['Strong fit', 'green'], good: ['Good fit', 'green'], stretch: ['Stretch', 'amber'], weak: ['Weak fit', '']};
const FACT_LABELS = [['salary', 'Salary'], ['remote_policy', 'Remote / on-site'], ['visa_relocation', 'Visa & relocation'], ['languages_required', 'Languages'],
  ['seniority', 'Seniority'], ['employment_type', 'Employment'], ['deadline', 'Deadline'], ['contact', 'Contact'], ['tech_stack', 'Tech stack']];

function keyFacts(facts, only) {
  const shown = FACT_LABELS.filter(([key]) => (!only || only.includes(key)) && (Array.isArray(facts?.[key]) ? facts[key].length : facts?.[key]));
  return shown.length ? `<div class="key-facts">${shown.map(([key, label]) => `<div class="fit"><small>${label}</small><span>${esc(Array.isArray(facts[key]) ? facts[key].join(', ') : facts[key])}</span></div>`).join('')}</div>` : '';
}

const bullets = (title, items, tone = '') => items?.length ? `<h3>${title}</h3><ul class="insight-list ${tone}">${items.map(item => `<li>${esc(item)}</li>`).join('')}</ul>` : '';
const madeBy = item => `<p class="evidence">By ${esc(item.model || 'AI')} · ${esc(ago(item.created_at))}. AI can be wrong: check it against the advert.</p>`;

function insights(data) {
  const j = data.job,
    a = data.insights?.analysis,
    iv = data.insights?.interview;
  const tools = `<div class="answers-toolbar">${button(icon('spark') + (a ? ' Analyse again' : ' Analyse vacancy'), 'analyze-job', (a ? '' : 'primary ') + 'button-icon')}${button(icon('profile') + (iv ? ' Prepare again' : ' Interview prep'), 'interview-prep', 'button-icon')}${button(icon('chat') + ' Ask AI', 'chat-about-job', 'button-icon')}${button(icon('mail') + ' Draft a reply', 'reply-dialog', 'button-icon', `data-job-id="${j.id}"`)}</div>`;
  if (!a && !iv) return `${tools}<div class="empty"><div class="empty-icon">${icon('spark')}</div><h2>Let AI read this vacancy for you.</h2><p>Analyse vacancy: how well you fit, gaps, red flags and key facts such as salary, remote work and visa help. Interview prep: likely questions with answers built from your profile.</p><p class="evidence">Uses ${esc(aiLabel())}. Change it in Settings & activity → AI assistant.</p></div>`;
  const p = a?.payload;
  const verdict = p ? VERDICTS[p.verdict] || [cap(p.verdict), ''] : null;
  const analysis = p ? `<section class="insight"><div class="insight-head"><h3>Analysis</h3>${pill(verdict[0], verdict[1])}</div><p>${esc(p.summary)}</p>${p.verdict_reason ? `<p class="evidence">${esc(p.verdict_reason)}</p>` : ''}${keyFacts(p.key_facts)}${bullets('You meet', p.strengths, 'good')}${bullets('Gaps', p.gaps, 'warn')}${bullets('Red flags', p.red_flags, 'bad')}${bullets('Put first in your application', p.emphasise)}${madeBy(a)}</section>` : '';
  const q = iv?.payload;
  const interview = q ? `<section class="insight"><div class="insight-head"><h3>Interview preparation</h3>${pill(q.questions.length + ' questions')}</div>${q.questions.map((item, i) => `<details class="question-prep" ${i === 0 ? 'open' : ''}><summary>${esc(item.question)}</summary>${item.why_they_ask ? `<p class="evidence">${esc(item.why_they_ask)}</p>` : ''}<div class="prose">${esc(item.answer_outline)}</div>${item.evidence?.length ? `<p class="evidence">From your profile: ${item.evidence.map(e => '“' + esc(e) + '”').join(' · ')}</p>` : ''}</details>`).join('')}${bullets('Questions you could ask them', q.questions_to_ask)}${bullets('Worth revising', q.topics_to_review)}${madeBy(iv)}</section>` : '';
  return tools + analysis + interview;
}

function notes(b) {
  return `<label class="field"><span>Application stage</span><select id="job-stage">${options(orderedStatuses,b.status)}</select><small>You control the stage. Generating or sending a draft does not change it.</small></label><label class="field"><span>Your private notes</span><textarea id="job-notes" class="editor" placeholder="What stands out? What would you like to ask?">${esc(b.notes)}</textarea></label><div class="savebar"><small>Edits stay with this opportunity as you move around the app.</small>${button('Save notes & stage','save-notes','primary')}</div>`;
}

function letter(b) {
  return `<div class="answers-toolbar">${button(icon('spark')+' Draft with '+esc(aiName()),'generate-letter','primary button-icon')}${button('Version history','letter-history')}${button('Export text','export-letter')}</div><label class="field"><span>Your cover letter</span><textarea id="letter-text" class="editor" placeholder="Write in your own words, or start a draft with AI.">${esc(b.letter)}</textarea></label><div class="editor-meta"><span id="word-count">${b.letter.trim().split(/\s+/).filter(Boolean).length} words</span><span>Style: ${esc(S.profile.writing_style?.tone||'Professional and natural')}</span></div>${b.letterNotes?.length?`<div class="notice warning"><strong>Check before using this draft</strong>${b.letterNotes.map(n=>`<p>${esc(n)}</p>`).join('')}${b.letterEvidence?.length?`<details class="evidence"><summary>Profile facts the draft relies on</summary>${b.letterEvidence.map(e=>`<p>${esc(e)}</p>`).join('')}</details>`:''}</div>`:'<div class="notice">Review the facts and the employer’s requirements before using this letter. Your writing preferences are in Profile & writing.</div>'}<div class="savebar"><small>${b.dirty.has('letter')?'Unsaved edits':'Saved versions remain in history.'}</small>${button('Save version','save-letter','primary')}</div>`;
}

function answers(b, data) {
  const p = b.app;
  return `<div class="answers-toolbar">${button('Read questions','read-questions')}${button('Paste questions','paste-questions')}${p?button(icon('spark')+' Draft answers','generate-answers','primary button-icon'):''}</div>${!p?`<div class="empty"><div class="empty-icon">${icon('applications')}</div><h2>A thoughtful application starts here.</h2><p>Read the employer’s public questions or paste them in. Your AI prepares drafts for you to review and edit.</p></div>`:`<div class="notice">${p.form.fields.length} questions · ${esc(p.form.coverage||'partial')} coverage. Check the original form for missing or conditional questions.<details class="evidence"><summary>Source details and limitations</summary>${(p.form.warnings||[]).map(w=>`<p>${esc(w)}</p>`).join('')}<p>${esc(p.form.source_url||'Manually supplied questions')}</p></details></div><div class="ready-line">${icon('check')} ${p.form.fields.filter(f=>hasAnswer(p,f)).length} of ${p.form.fields.length} questions completed</div><br>${p.form.fields.map((f,i)=>question(p,f,i)).join('')}<div class="savebar"><div class="section-actions">${button('Save version','save-answers')}${button('History','answers-history')}${button('Export','export-answers')}</div>${sendable(p)?button('Review & send','review-application','primary'):button('Copy answers & open application','copy-apply','primary')}</div><p class="evidence">${sendable(p)?'This is a standard public form: Review & send fills it in a browser for your final check before one explicit send.':'This employer’s form needs its own website (it uses JavaScript, a login or a hosted applicant system). Copy your reviewed answers, paste them into the form, and attach your files there.'}</p>`}${data.deliveries.length?`<div class="notice">Last delivery: ${esc(cap(data.deliveries[0].status))}. Check the employer’s confirmation before updating your stage.</div>`:''}`;
}

// Only native HTML forms can be filled and sent from the app; Greenhouse,
// pasted questions and other hosted forms are completed on the employer site.
const sendable = p => p.form.provider === 'html' && Boolean(p.form.delivery);

function answersText(p) {
  return p.form.fields.map(f => `${f.label}\n${p.answers.find(a=>a.field_id===f.id)?.answer||'[Your input needed]'}`).join('\n\n');
}

function hasAnswer(p, f) {
  const a = p.answers.find(a => a.field_id === f.id);
  return f.type === 'file' ? Boolean(p.attachments?.[f.id]) : Boolean(a?.answer?.trim() || a?.selected_options?.length);
}

function question(p, f, i) {
  const a = p.answers.find(a => a.field_id === f.id) || {};
  const id = 'answer-' + i;
  let control;
  if (f.type === 'file') {
    const file = p.attachment_files?.[f.id];
    control = `<div class="file-name">${file?esc(file.name):'No file attached'}</div>${button('Choose file','attach-file','',`data-field-index="${i}"`)} ${p.attachments?.[f.id]?button('Remove','remove-file','quiet',`data-field-index="${i}"`):''}`;
  } else if (f.options?.length) {
    control = f.options.map((o, n) => `<label class="option"><input type="${f.type==='multi_select'?'checkbox':'radio'}" id="${id}-${n}" name="${id}" value="${esc(o.value)}" data-answer-index="${i}" ${a.selected_options?.includes(o.value)?'checked':''}><span>${esc(o.label)}</span></label>`).join('') + (f.type === 'single_select' ? button('Clear selection', 'clear-choice', 'quiet', `data-field-index="${i}"`) : '');
  } else control = `<textarea id="${id}" data-answer-index="${i}" ${f.max_length?`maxlength="${f.max_length}"`:''} placeholder="${f.review_only?'This answer needs your personal choice.':'Write or edit your answer…'}">${esc(a.answer||'')}</textarea>`;
  return `<section class="question" ${f.options?.length||f.type==='file'?`role="group" aria-labelledby="question-label-${i}"`:''}><div class="question-head"><span class="question-number">${String(i+1).padStart(2,'0')}</span><label id="question-label-${i}" ${f.options?.length||f.type==='file'?'':`for="${id}"`}>${esc(f.label)}${f.required?' <span class="warning-text">*</span>':''}</label>${pill(f.review_only?'Your decision':hasAnswer(p,f)?'Ready to review':'Needs input',hasAnswer(p,f)?'green':'amber')}</div>${control}${f.max_length?`<small>Maximum ${f.max_length} characters</small>`:''}${a.used_evidence?.length||a.review_notes?.length?`<details class="evidence"><summary>Evidence & review notes</summary>${[...(a.used_evidence||[]),...(a.review_notes||[])].map(x=>`<p>${esc(x)}</p>`).join('')}</details>`:''}</section>`;
}
async function refreshJob(id) {
  const data = await api('/jobs/' + id);
  S.details.set(id, data);
  return data;
}
async function savePart(part, id = S.selected) {
  const b = S.buffers.get(id),
    rev = b.revision;
  if (part === 'notes') {
    await api(`/jobs/${id}/workflow`, {
      method: 'POST',
      body: {
        status: b.status,
        notes: b.notes
      }
    });
  } else if (part === 'letter') {
    await api(`/jobs/${id}/letter`, {
      method: 'POST',
      body: {
        content: b.letter
      }
    });
  } else if (part === 'app') {
    if (!b.app) return;
    const saved = await api(`/jobs/${id}/application`, {
      method: 'POST',
      body: b.app
    });
    if (rev === b.revision) b.app = saved.payload || b.app;
  }
  if (rev === b.revision) b.dirty.delete(part);
  await refreshJob(id);
  updateStatus();
}
async function generate(kind) {
  const id = S.selected,
    b = current();
  if (!b) return;
  const part = kind === 'letter' ? 'letter' : 'app';
  if (b.dirty.has(part)) await savePart(part, id);
  const rev = b.revision;
  await task(kind, {
    job_id: id,
    ...(kind === 'answers' ? {
      form: clone(b.app.form)
    } : {})
  }, async result => {
    if (kind === 'letter') {
      // Shown with the fresh draft only; saved versions keep just the text.
      b.letterNotes = result.review_notes || [];
      b.letterEvidence = result.used_evidence || [];
    }
    const kept = b.revision !== rev;
    if (!kept) {
      if (kind === 'letter') b.letter = result.cover_letter;
      else b.app = result;
      b.dirty.delete(part);
    }
    await refreshJob(id);
    if (S.selected === id) renderDetail();
    toast((kept ? 'Your newer edits were kept; the AI draft is in version history. ' : 'Draft ready. Review every fact before using it. ') + (result.performance_note || ''));
  });
}

const listText = value => Array.isArray(value) ? value.join('\n') : value || '';
const field = (label, key, value, {
  lines = 0,
  hint = '',
  type = 'text',
  group = 'profile'
} = {}) => {
  if (group === 'profile' && Object.hasOwn(S.profileRaw, key)) value = S.profileRaw[key];
  return `<label class="field"><span>${esc(label)}</span>${lines?`<textarea rows="${lines}" data-${group}="${key}">${esc(value)}</textarea>`:`<input type="${type}" ${key==='years_of_experience'?'min="0" max="60" step="0.1"':''} data-${group}="${key}" value="${esc(value)}">`}${hint?`<small>${esc(hint)}</small>`:''}</label>`;
};

function renderProfile() {
  const p = S.profile,
    w = p.writing_style || {};
  $('#main').insertAdjacentHTML('beforeend', `<div class="content-width">
  <div class="notice">Your profile is shared across countries. Only country-specific facts, such as work authorization, are separate.</div>
  <section class="panel"><div class="panel-title"><div><h2>Your story</h2><p>Facts your AI can use when preparing an application.</p></div>${pill('Shared profile','blue')}</div>
  <div class="form-grid">${field('Full name','name',p.name)}${field('Cover-letter language','cover_letter_language',p.cover_letter_language)}${field('Years of professional experience','years_of_experience',p.years_of_experience??'',{type:'number',hint:'Use a decimal such as 2.5. Blank means unknown. Used to screen seniority and required experience.'})}
  <div class="span-2">${field('Professional summary','summary',p.summary,{lines:3})}</div>${field('Skills','skills',listText(p.skills),{lines:4,hint:`${(p.skills||[]).length} skills. Paste them any way: one per line, or separated by commas, semicolons or bullets.`})}${field('Projects & experience evidence','evidence',listText(p.evidence),{lines:4,hint:'One factual achievement per line or bullet; commas inside a sentence are kept.'})}
  ${field('Actual language proficiency','languages',Object.entries(p.languages||{}).map(([k,v])=>k+': '+v).join('\n'),{lines:3,hint:'Language and level, such as English: C1, German - A2 or Norwegian (B1); one per line or comma-separated.'})}${field('Language levels to include in searches','search_languages',Object.entries(p.search_languages||{}).map(([k,v])=>k+': '+v).join('\n'),{lines:3,hint:'Search preferences never change your stated proficiency.'})}</div></section>
  <section class="panel"><div class="panel-title"><div><h2>What you’re looking for</h2><p>These preferences stay with you when you switch countries.</p></div></div><div class="form-grid">${[['Target roles','target_roles'],['Related roles to explore','related_roles'],['Preferred locations','preferred_locations'],['Terms to exclude','excluded_keywords']].map(([label,key])=>field(label,key,listText(p[key]),{lines:3,hint:'One per line, or separated by commas. Leave locations empty to consider all.'})).join('')}</div></section>
  <section class="panel"><div class="panel-title"><div><h2>Your CV</h2><p>Import a text PDF, DOCX, TXT or Markdown file, or paste its content.</p></div><div class="section-actions">${button('Import CV','import-cv')}${button(icon('spark')+' Suggest profile','suggest-profile','button-icon')}</div></div>${field('CV text','cv_text',p.cv_text,{lines:10})}</section>
  <section class="panel"><div class="panel-title"><div><h2>Your writing style</h2><p>Tell the AI how you want to sound. Examples guide wording, not qualifications.</p></div></div><div class="form-grid">${field('Tone','writing_style.tone',w.tone||'professional and natural')}${field('Language override','writing_style.language',w.language||'',{hint:'Leave blank to use your cover-letter language.'})}${field('Maximum words','writing_style.max_words',w.max_words||180,{type:'number',hint:'20–500 words per answer or letter.'})}${field('Phrases to avoid','writing_style.avoid_phrases',listText(w.avoid_phrases),{lines:4,hint:'One per line, or separated by commas. As many as you like; drafts containing one are rewritten.'})}<div class="span-2">${field('Style instructions','writing_style.instructions',w.instructions||'',{lines:3})}${field('Writing examples','writing_style.examples',listText(w.examples),{lines:4,hint:'Up to five examples, one paragraph per line.'})}</div></div></section>
  <section class="panel"><div class="panel-title"><div><h2>Country-specific facts</h2><p>Record your actual situation. Leave unknown permission blank.</p></div></div><div class="form-grid">${Object.entries(S.boot.countries).map(([code,name])=>field(name+' — work authorization','country_preferences.'+code+'.work_authorization',p.country_preferences?.[code]?.work_authorization??(code==='NO'?p.work_authorization:''),{lines:2})).join('')}</div></section><div class="savebar"><small>${S.profileDirty?'You have unsaved profile edits.':'Profile facts are used only after you save.'}</small>${button('Save profile','save-profile','primary')}</div></div>`);
}

function filteredCatalog() {
  const catalog = S.catalogTab === 'employers' ? S.boot.catalog : S.boot.boards;
  return catalog.filter(e => (!S.country || e.countries.includes(S.country)) && (!S.catalogQuery || [e.name, e.note, ...(e.sectors || [])].join(' ').toLowerCase().includes(S.catalogQuery.toLowerCase())) && (S.catalogMode !== 'automatic' || e.source));
}

function renderSources() {
  const entries = filteredCatalog();
  $('#main').insertAdjacentHTML('beforeend', `${coverageNotice()}<div class="notice"><strong>The directory is a starting point.</strong> Only configured collectors are searched automatically. ${button('Add all country collectors','refresh-country-sources','primary')} You can review and remove collectors in Settings.</div><div class="segmented" aria-label="Source directory">${button('Employers','catalog-employers',S.catalogTab==='employers'?'active':'')}${button('Job boards','catalog-boards',S.catalogTab==='boards'?'active':'')}</div><div class="filterbar"><div class="search">${icon('search')}<input type="search" id="catalog-search" value="${esc(S.catalogQuery)}" placeholder="Search companies, sectors or sources…" aria-label="Search source library"></div><select id="catalog-mode" aria-label="Source access">${options([['all','All access types'],['automatic','Automatic collection']],S.catalogMode)}</select>${button('Add visible collectors','add-visible-sources','primary')}</div><div class="result-summary"><span id="catalog-count">${entries.length} sources</span><span>Official careers & researched directories</span></div><div class="notice">Automatic collectors fetch public jobs. Career links and job boards open in your browser for research and manual import. A listing does not guarantee a vacancy or application support.</div><div id="catalog-grid" class="catalog-grid"></div>`);
  renderCatalogCards();
}

function sourceAdded(e) {
  if (!e.source) return false;
  const keys = ['type', 'board', 'url', 'tenant', 'site', 'host', 'company', 'region'];
  return S.boot.settings.sources.some(s => keys.every(k => (s[k] || '') === (e.source[k] || '')) && (!s.countries || (S.country ? s.countries.includes(S.country) : e.countries.every(c => s.countries.includes(c)))));
}

function renderCatalogCards() {
  const entries = filteredCatalog();
  if (!$('#catalog-grid')) return;
  $('#catalog-count').textContent = entries.length + ' sources';
  $('#catalog-grid').innerHTML = entries.length ? entries.map(e => `<article class="catalog-card"><div class="row"><span class="company-mark">${esc(e.name.slice(0,2).toUpperCase())}</span><h3>${esc(e.name)}</h3></div><div class="tags">${pill(e.source?'Automatic collector':'Career link',e.source?'green':'')}${pill(e.countries.map(c=>S.boot.countries[c]).join(' · '))}</div><p>${esc(e.note||e.sectors?.join(' · ')||'Explore this source on its official website.')}</p><small>Checked ${esc(e.checked_at||'date not recorded')} · ${esc(cap(e.verification?.status||'Research directory'))}</small><footer>${link('Open site',e.careers_url)}${e.source?button(sourceAdded(e)?'Added':'Add source','add-source',sourceAdded(e)?'quiet':'',`data-source-id="${esc(e.id)}" ${sourceAdded(e)?'disabled':''}`):''}</footer></article>`).join('') : '<div class="empty"><h2>No sources match</h2><p>Try a different search or access filter.</p></div>';
}

// Adzuna needs the user's own free key. The page never receives the key
// back: only whether one is saved and the last characters of its ID.
function adzunaKeyBlock() {
  const a = S.boot.adzuna || {};
  if (REMOTE) return `<div class="adzuna-key"><h3>Adzuna key ${a.configured ? pill('Saved', 'green') : pill('Not added')}</h3><p class="evidence">The Adzuna key is added or changed on the PC.</p></div>`;
  const body = a.configured
    ? `<p class="evidence">Key saved for Application ID ${esc(a.app_id_hint || '')}${a.saved_at ? ' on ' + esc(a.saved_at.slice(0, 10)) : ''}. It stays on this computer. The app uses about 72 Adzuna requests a day (50 jobs each), so the free allowance of 2,500 a month lasts the whole month.</p><div class="row">${button('Remove key', 'remove-adzuna', 'quiet')}</div>`
    : `<p class="evidence">Adzuna adds adverts from across US job sites. Create a free developer account, then copy the Application ID and Application key from its dashboard (API access details). The key is checked with one search and stays on this computer.</p><div class="adzuna-fields"><label class="field"><span>Application ID</span><input id="adzuna-id" autocomplete="off" spellcheck="false" maxlength="64"></label><label class="field"><span>Application key</span><input id="adzuna-secret" type="password" autocomplete="off" spellcheck="false" maxlength="128"></label></div><div class="row">${button('Check and save key', 'save-adzuna', 'primary')}${link('Get a free key', 'https://developer.adzuna.com/signup')}</div>`;
  return `<div class="adzuna-key"><h3>Adzuna key ${a.configured ? pill('Saved', 'green') : pill('Not added')}</h3>${body}</div>`;
}

function jobSitesPanel(settings) {
  const m = settings.market || {enabled: {}, searches: []};
  const on = Object.values(m.enabled || {}).filter(Boolean).length;
  const rows = (S.boot.job_sites || []).map(s => `<label class="site-row"><input type="checkbox" data-site="${esc(s.id)}" ${m.enabled?.[s.id]?'checked':''}><span><strong>${esc(s.name)}</strong> <small>${esc(s.countries.map(c=>S.boot.countries[c]).join(' · '))}</small> ${s.opt_in?pill('Opt-in','amber'):''}<small class="site-note">${esc(s.note)}</small></span></label>`).join('');
  return `<section class="panel"><div class="panel-title"><div><h2>Job sites</h2><p>Whole-market search: every employer on these sites, for your saved searches and target roles. NAV covers Norway and is listed under connected sources.</p></div>${pill(on+' on',on?'green':'')}</div><div class="site-list">${rows}</div>${adzunaKeyBlock()}<label class="field"><span>Saved searches</span><textarea id="market-searches" rows="3" placeholder="One search per line, for example: Linux DevOps" aria-label="Saved searches">${esc((m.searches||[]).join('\n'))}</textarea><small>Every refresh searches these first, then your target roles${m.include_related_roles?' and related roles':''}. Use the search box on Discover to search all sites once.</small></label><label class="check-row"><input type="checkbox" id="market-related" ${m.include_related_roles?'checked':''}> Also search my related roles</label><label class="check-row"><input type="checkbox" id="market-it-only" ${m.it_only!==false?'checked':''}> IT jobs only</label><p class="evidence">On: refreshes keep IT vacancies and nothing else (developers, data and AI, cloud and DevOps, security, QA, IT support and administration, IT consulting, UX and IT product roles), using official categories where a source has them: NAV's IT category, the Bundesagentur's IT occupation fields, Djinni's and DOU's categories, Adzuna's IT category. Other vacancies already collected are removed, except ones you saved, noted or applied to.</p><label class="check-row"><input type="checkbox" id="market-everything" ${m.collect_everything!==false?'checked':''}> Collect everything in my fields</label><p class="evidence">On: each refresh also browses whole fields (IT, data, engineering, science…) and NAV's entire Norwegian market, and keeps every vacancy in your countries; matching happens in your database. The first refresh per country can take 10–20 minutes. Vacancies closed or unseen for 30 days are removed, except ones you saved, noted or applied to. Changes apply after Save settings, below.</p></section>`;
}

function updatesPanel() {
  const u = S.update;
  const version = S.boot.version || '';
  const status = !u ? `You have version ${esc(version)}. Updates come from the app's GitHub releases.` :
    u.newer ? `Version ${esc(u.latest)} is available; you have ${esc(u.current)}.` :
    `You have version ${esc(u.current)}. ${esc(u.reason || '')}`;
  const notes = u?.newer && u.notes ? `<details class="evidence"><summary>What's new in ${esc(u.latest)}</summary><div class="prose">${esc(u.notes)}</div></details>` : '';
  const action = u?.newer ? (REMOTE ? '<p class="evidence">Update from the PC: it closes and reopens Job Agent there.</p>' : u.can_update ? button('Update now', 'apply-update', 'primary') : `<p class="evidence">${esc(u.reason)}</p>`) : '';
  const install = u?.kind === 'source' ? `<p class="evidence">Install once from ${LINUX ? 'a terminal' : 'PowerShell'}, then the app updates itself: <code>${esc(u.install_command)}</code></p>` : '';
  return `<section class="panel"><div class="panel-title"><div><h2>Updates</h2><p>${status}</p></div>${pill('v' + version)}</div>${notes}<div class="section-actions">${button('Check for updates', 'check-updates')}${action}${link('Releases on GitHub', u?.url || 'https://github.com/wgKaminari/Local-AI-Agent-manager/releases')}</div>${install}<p class="evidence">Updating closes the app, installs the new version and reopens it. Your profile, vacancies and drafts are not changed.</p></section>`;
}

async function autoCheckUpdates() {
  // Once per start, quietly: only a newer release produces a notice.
  try {
    S.update = await api('/updates');
    if (S.update.newer) toast(`Version ${S.update.latest} is available. Open Settings & activity → Updates.`);
  } catch {
    // Offline or GitHub unreachable: try again next start.
  }
}

function linkedinNotice() {
  modal('LinkedIn public search', `<div class="notice warning"><strong>Read before enabling</strong><p>LinkedIn shows job listings to visitors without an account, and this search uses only that public view. LinkedIn’s user agreement nevertheless forbids automated access.</p></div><p>How the app keeps this respectful and safe for you:</p><ul class="prose"><li>No login, no cookies: your LinkedIn account is never used or affected.</li><li>It identifies itself honestly and pauses between requests, with a small limit per refresh.</li><li>If LinkedIn asks it to slow down, it stops at once and waits an hour. The worst case is a temporary block of your internet address on LinkedIn.</li></ul><p class="evidence">The fully permitted alternative is LinkedIn job-alert emails, which Job alerts imports from Gmail.</p>`,
    button('Cancel', 'close-modal') + button('I understand, enable it', 'accept-linkedin', 'primary'));
}

function renderSettings() {
  const settings = S.settingsDraft || S.boot.settings;
  $('#main').insertAdjacentHTML('beforeend', `<div class="content-width">${aiPanel(settings)}
  ${jobSitesPanel(settings)}<section class="panel"><div class="panel-title"><div><h2>Connected job sources</h2><p>${S.boot.settings.sources.length} configured. One refresh collects every target country; the country switch only filters what you see.</p></div>${button('Browse library','go-sources')}</div><div class="table-wrap"><table><thead><tr><th>Source</th><th>Countries</th><th>Action</th></tr></thead><tbody>${settings.sources.map((s,i)=>`<tr><td>${esc(s.name||s.board||cap(s.type))}<span class="sub">${esc(s.type)}</span></td><td>${esc(s.countries?.join(', ')||(s.type==='nav'?'NO':'Any'))}</td><td>${button('Remove','remove-source','quiet',`data-source-index="${i}"`)}</td></tr>`).join('')}</tbody></table></div><details class="evidence"><summary>Advanced source configuration</summary><p>Edit the source array for custom boards, locations or limits. Invalid changes are rejected when you save.</p><textarea id="sources-json" rows="10" aria-label="Advanced sources JSON">${esc(S.sourcesText??JSON.stringify(settings.sources,null,2))}</textarea></details><div class="savebar"><small>${S.settingsDirty?'Unsaved settings':'Changes apply after saving.'}</small>${button('Save settings','save-settings','primary')}</div></section>
  ${quickMarksPanel()}${phonePanel()}<section class="panel"><div class="panel-title"><div><h2>Collection activity</h2><p>See what was collected and which sources need attention.</p></div>${button('Find opportunities','collect')}</div>${collectionReport()}</section><section class="panel"><div class="panel-title"><div><h2>Desktop app</h2><p>Open Job Agent from the ${LINUX ? 'applications menu' : 'Start menu'} or desktop like any other program: its own icon, no console window, and only one window at a time.</p></div></div><p class="evidence">The app runs entirely on this computer. Your profile, database, notes and drafts stay in your private data folder.</p><div class="section-actions">${REMOTE?'':button(LINUX ? 'Add to applications menu & desktop' : 'Add Start menu & desktop shortcuts','create-shortcuts','primary')}${button('Switch theme','theme')}${button('Keyboard shortcuts','help')}</div></section>${updatesPanel()}</div>`);
  loadPhonePanel();
}

// AI assistant: the free local model or a cloud provider with your own key.
function aiProvider() {
  const a = S.boot.ai || {};
  return (a.providers || []).find(p => p.id === a.provider) || null;
}
const aiName = () => aiProvider()?.name || 'local AI';
const aiShort = () => aiProvider() ? 'AI: ' + aiProvider().name : 'Local AI';
const aiLabel = () => aiProvider() ? `${aiProvider().name} (${aiProvider().model})` : `your local model (${S.boot.settings.model})`;
const tokens = n => Number(n || 0).toLocaleString('en-US');

function localModelBlock(settings) {
  return `<div class="form-grid">${field('Model name','model',settings.model,{group:'settings',hint:'Save a model name before downloading or generating.'})}<div><label class="field"><span>Available models</span><select id="model-picker"><option value="">Check setup to list installed models</option></select></label></div></div><div class="section-actions">${button('Check setup','check-setup')}${button('Start Ollama','start-ollama')}${button('Download saved model','download-model')}${button('Install PDF support','install-pdf')}${link('Install Ollama','https://ollama.com/download/windows')}</div><div id="setup-result" class="evidence"></div>`;
}

function modelOptions(p) {
  const models = [...(S.aiModels[p.id] || [])];
  if (!models.some(m => m.id === p.model)) models.unshift({id: p.model, name: p.model});
  return models.map(m => `<option value="${esc(m.id)}" ${m.id === p.model ? 'selected' : ''}>${esc(m.name && m.name !== m.id ? `${m.name} (${m.id})` : m.id)}</option>`).join('');
}

function aiProviderBody(p) {
  if (p.configured) return `<div class="form-grid"><label class="field"><span>Model</span><select data-ai-model="${esc(p.id)}">${modelOptions(p)}</select><small>Default: ${esc(p.default_model)}. Refresh the list to see every model your key can use.</small></label></div><p class="evidence">Key ${esc(p.hint)} saved${p.saved_at ? ' on ' + esc(p.saved_at.slice(0, 10)) : ''}, encrypted on this computer. ${esc(p.note)}</p><div class="row">${button('Refresh model list', 'refresh-ai-models', 'quiet', `data-provider="${esc(p.id)}"`)}${link('Usage & billing', p.billing_url)}${REMOTE ? '' : button('Remove key', 'remove-ai-key', 'quiet', `data-provider="${esc(p.id)}"`)}</div>`;
  if (REMOTE) return `<p class="evidence">${esc(p.note)} API keys are added on the PC.</p>`;
  return `<p class="evidence">${esc(p.note)} Create an API key on ${esc(p.company)}'s site and paste it here: it is checked with one free request and saved encrypted on this computer, never shown again.</p><div class="adzuna-fields"><label class="field"><span>${esc(p.name)} API key</span><input id="ai-key-${esc(p.id)}" type="password" autocomplete="off" spellcheck="false" maxlength="400"></label></div><div class="row">${button('Check and save key', 'save-ai-key', 'primary', `data-provider="${esc(p.id)}"`)}${link('Get an API key', p.keys_url)}</div>`;
}

function aiPanel(settings) {
  const a = S.boot.ai || {providers: [], usage: {}},
    chosen = a.provider || 'local',
    u = a.usage || {},
    limit = a.monthly_token_limit || 0;
  const choice = (id, title, sub, disabled = false) => `<label class="ai-choice"><input type="radio" name="ai-provider" value="${esc(id)}" ${chosen === id ? 'checked' : ''} ${disabled ? 'disabled' : ''}><span><strong>${title}</strong><small>${sub}</small></span></label>`;
  const local = `<div class="ai-provider ${chosen === 'local' ? 'active' : ''}">${choice('local', 'Local model (Ollama)', 'Free and private: runs on this computer and sends nothing anywhere. Slower, and writes less well than cloud models.')}<details class="ai-provider-body" ${chosen === 'local' ? 'open' : ''}><summary>Local model settings</summary>${localModelBlock(settings)}</details></div>`;
  const clouds = (a.providers || []).map(p => `<div class="ai-provider ${chosen === p.id ? 'active' : ''}" id="ai-${esc(p.id)}">${choice(p.id, `${esc(p.name)} <small class="company">${esc(p.company)}</small> ${p.configured ? pill('Key saved', 'green') : pill('No key')}`, p.configured ? `Uses ${esc(p.model)}; billed to your ${esc(p.company)} account.` : 'Add your API key below to use it.', !p.configured)}<div class="ai-provider-body">${aiProviderBody(p)}</div></div>`).join('');
  const share = limit ? Math.min(100, Math.round(100 * (u.tokens || 0) / limit)) : 0;
  const by = Object.entries(u.providers || {}).map(([id, c]) => `${esc((a.providers || []).find(p => p.id === id)?.name || id)} ${tokens(c.input + c.output)} (${c.requests} requests)`).join(' · ');
  const usage = `<div class="ai-usage"><h3>Cloud AI this month</h3><p><strong>${tokens(u.tokens)}</strong> tokens used${limit ? ` of your ${tokens(limit)} limit` : ''}.${by ? ' ' + by + '.' : ''}</p>${limit ? `<progress class="meter" max="100" value="${share}" aria-label="Monthly token limit used">${share}%</progress>` : ''}<div class="row"><label class="field"><span>Monthly limit (tokens)</span><input id="ai-limit" type="number" min="0" step="100000" value="${limit}"></label>${button('Save limit', 'save-ai-limit')}</div><p class="evidence">Cloud requests stop once this month's tokens reach the limit; 0 means no limit. A cover letter or analysis uses roughly 5,000–20,000 tokens, a chat answer 2,000–10,000. To be safe, also set a spending limit on the provider's billing page.</p></div>`;
  return `<section class="panel" id="ai-panel"><div class="panel-title"><div><h2>AI assistant</h2><p>Which AI writes your cover letters and application answers, analyses vacancies, prepares interviews, drafts replies and answers in AI chat. Your local model is free; cloud models write better and faster but charge your account per use.</p></div>${pill(aiProvider() ? aiProvider().name : 'Local · free', aiProvider() ? 'blue' : 'green')}</div><div class="ai-providers">${local}${clouds}</div>${usage}</section>`;
}

function renderAiPanel() {
  const panel = $('#ai-panel');
  if (panel) panel.outerHTML = aiPanel(S.settingsDraft || S.boot.settings);
  const right = $('.statusbar .right');
  if (right) right.textContent = aiShort() + ' · you review every application';
}

function aiConsent(p) {
  modal(`Use ${p.name} for AI?`, `<p>From now on, whenever you use AI in Job Agent, this is sent to ${esc(p.company)}:</p><ul class="prose"><li>your profile and CV text,</li><li>the vacancy you are working on,</li><li>your chat messages, and emails you paste for a reply draft.</li></ul><p class="evidence">${esc(p.note)} Each request is billed to your ${esc(p.company)} account. Your local model sends nothing anywhere, and you can switch back at any time.</p>`,
    button('Cancel', 'close-modal') + button('Use ' + esc(p.name), 'confirm-ai', 'primary', `data-provider="${esc(p.id)}"`));
}

async function chooseAi(id) {
  const p = (S.boot.ai?.providers || []).find(item => item.id === id);
  if (id !== 'local' && p && !p.consented) {
    aiConsent(p);
    renderAiPanel();  // The choice changes only after you confirm.
    return;
  }
  S.boot.ai = await api('/ai', {method: 'PUT', body: {provider: id}});
  renderAiPanel();
  if (S.view === 'chat') renderThread();
  toast(id === 'local' ? 'AI now runs on your local model: free and private.' : `AI now uses ${p.name} (${p.model}).`);
}

// Chat answers use a small, safe subset of Markdown: paragraphs, lists,
// headings, bold, italics, code and http(s) links. Everything is escaped first.
function md(text) {
  const inline = s => esc(s).replace(/`([^`]+)`/g, '<code>$1</code>').replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:[^\s)]+)\)/g, (all, label, url) => {
      const href = safeUrl(url.replaceAll('&amp;', '&'));
      return href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${label}</a>` : all;
    });
  const out = [];
  let list = null, para = [], code = null;
  const flush = () => {
    if (para.length) out.push(`<p>${para.join('<br>')}</p>`);
    para = [];
    if (list) out.push(`</${list}>`);
    list = null;
  };
  for (const line of String(text || '').replace(/\r/g, '').split('\n')) {
    if (code !== null) {
      if (line.trim().startsWith('```')) {
        out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
        code = null;
      } else code.push(line);
      continue;
    }
    if (line.trim().startsWith('```')) {
      flush();
      code = [];
      continue;
    }
    const bullet = line.match(/^\s*[-*•]\s+(.*)$/), numbered = line.match(/^\s*\d+[.)]\s+(.*)$/), heading = line.match(/^\s*#{1,4}\s+(.*)$/);
    if (bullet || numbered) {
      const kind = bullet ? 'ul' : 'ol';
      if (para.length || list !== kind) flush();
      if (!list) {
        out.push(`<${kind}>`);
        list = kind;
      }
      out.push(`<li>${inline((bullet || numbered)[1])}</li>`);
    } else if (heading) {
      flush();
      out.push(`<h4>${inline(heading[1])}</h4>`);
    } else if (!line.trim()) flush();
    else {
      if (list) flush();
      para.push(inline(line));
    }
  }
  if (code !== null) out.push(`<pre><code>${esc(code.join('\n'))}</code></pre>`);
  flush();
  return out.join('');
}

async function loadChats() {
  S.chats = (await api('/chats')).chats;
}

function renderChat() {
  $('#main').innerHTML = `<div class="chat-layout ${S.chat || S.chatOpen ? 'has-chat' : ''}"><aside class="chat-list" aria-label="Conversations"><div class="chat-list-head"><h1>AI chat</h1>${button(icon('plus') + ' New chat', 'new-chat', 'primary button-icon')}</div><div id="chat-items"></div></aside><section class="chat-thread" id="chat-thread" aria-live="polite"></section></div>`;
  renderChatList();
  renderThread();
  if (S.chats === null) loadChats().then(renderChatList).catch(err => toast(err.message, true));
}

function renderChatList() {
  const root = $('#chat-items');
  if (!root) return;
  if (S.chats === null) {
    root.innerHTML = '<p class="evidence">Loading…</p>';
    return;
  }
  root.innerHTML = S.chats.length ? S.chats.map(c => `<div class="chat-item ${S.chat?.id === c.id ? 'active' : ''}"><button type="button" class="chat-open" data-action="open-chat" data-chat="${c.id}"><strong>${esc(c.title || 'Chat')}</strong><small>${c.job_title ? esc(c.job_title) + ' · ' : ''}${esc(ago(c.updated_at))}</small></button>${button(icon('close'), 'delete-chat', 'icon-only quiet', `data-chat="${c.id}" aria-label="Delete chat ${esc(c.title || '')}" title="Delete chat"`)}</div>`).join('') : '<p class="evidence">Your conversations appear here. They are saved on this computer.</p>';
}

function chatJobInfo() {
  const id = S.chat ? S.chat.job_id : S.chatJob;
  if (!id) return null;
  const known = S.details.get(id)?.job || S.jobs.find(j => j.id === id);
  return {id, title: S.chat?.job_title || known?.title || 'Vacancy', company: S.chat?.job_company || known?.company || ''};
}

function chatAiSelect() {
  const a = S.boot.ai || {providers: []};
  const rows = [['local', 'Local model · free', true], ...(a.providers || []).map(p => [p.id, `${p.name} · ${p.model}`, p.configured])];
  return `<label class="chat-ai" title="Which AI answers">${icon('spark')}<select id="chat-ai" aria-label="Which AI answers">${rows.map(([id, label, ok]) => `<option value="${esc(id)}" ${id === (a.provider || 'local') ? 'selected' : ''} ${ok ? '' : 'disabled'}>${esc(label)}${ok ? '' : ' (add key in Settings)'}</option>`).join('')}</select></label>`;
}

function chatSuggestions(job) {
  const prompts = job ? ['Am I a good fit for this job? Be honest about gaps.', 'What should I emphasise in my application?', 'Write five questions I could ask in the interview.', 'Draft a short LinkedIn message to the recruiter.']
    : ['Which of my skills are most in demand for my target roles?', 'How can I improve my CV summary?', 'Help me prepare for a technical interview.', 'How should I answer “What are your salary expectations?”'];
  return `<div class="chat-empty"><div class="empty-icon">${icon('chat')}</div><h2>${job ? 'Ask about this vacancy' : 'How can I help with your job search?'}</h2><div class="chat-prompts">${prompts.map(p => button(esc(p), 'chat-prompt', '', `data-prompt="${esc(p)}"`)).join('')}${button(icon('mail') + ' Draft a reply to an email', 'reply-dialog', 'button-icon', job ? `data-job-id="${job.id}"` : '')}</div></div>`;
}

function chatMessage(m, i) {
  return `<div class="msg ${m.role}"><div class="bubble">${m.role === 'assistant' ? md(m.content) : `<p>${esc(m.content).replaceAll('\n', '<br>')}</p>`}</div>${m.role === 'assistant' ? `<div class="msg-meta"><span>${esc(m.model || '')}</span>${button(icon('copy') + ' Copy', 'copy-message', 'quiet button-icon', `data-index="${i}"`)}</div>` : ''}</div>`;
}

function renderThread() {
  const root = $('#chat-thread');
  if (!root) return;
  const job = chatJobInfo(),
    useProfile = S.chat ? S.chat.use_profile : S.chatProfile,
    messages = S.chat?.messages || [],
    p = aiProvider();
  const context = `<div class="chat-context">${button(icon('back'), 'chat-back', 'icon-only quiet chat-back', 'aria-label="All conversations"')}<h2>${esc(S.chat?.title || 'New chat')}</h2><label class="check-row"><input type="checkbox" id="chat-profile" ${useProfile ? 'checked' : ''}> Use my profile</label>${job ? `<span class="chat-job">${icon('applications')}<span>${esc(job.title)}${job.company ? ' · ' + esc(job.company) : ''}</span>${button(icon('close'), 'chat-detach-job', 'icon-only quiet', 'aria-label="Stop using this vacancy" title="Stop using this vacancy"')}</span>` : ''}${chatAiSelect()}</div>`;
  const pending = S.chatPending ? `<div class="msg user"><div class="bubble"><p>${esc(S.chatPending.text).replaceAll('\n', '<br>')}</p></div></div><div class="msg assistant"><div class="bubble" id="pending-answer">${S.chatPending.partial ? md(S.chatPending.partial) : '<span class="typing"><i></i><i></i><i></i></span>'}</div></div>` : '';
  const body = messages.length || pending ? messages.map(chatMessage).join('') + pending : chatSuggestions(job);
  const note = p ? `Sent to ${esc(p.company)} (${esc(p.model)})${useProfile ? ' with your profile' : ''}${job ? ' and this vacancy' : ''}. AI can be wrong: check facts before you rely on them.` : `Answered by your local model on this computer${useProfile ? ', with your profile' : ''}. AI can be wrong: check facts before you rely on them.`;
  root.innerHTML = `${context}<div class="chat-messages" id="chat-messages">${body}</div><div class="chat-composer"><textarea id="chat-input" rows="3" maxlength="8000" placeholder="Ask anything about your job search… (Ctrl Enter to send)" aria-label="Message">${esc(S.chatDraft)}</textarea>${button(icon('send') + ' Send', 'send-chat', 'primary button-icon', S.chatPending ? 'disabled' : '')}</div><p class="evidence chat-note">${note}</p>`;
  const box = $('#chat-messages');
  box.scrollTop = box.scrollHeight;
}

function updatePending() {
  const el = $('#pending-answer'),
    box = $('#chat-messages');
  if (!el || !S.chatPending) return;
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  el.innerHTML = S.chatPending.partial ? md(S.chatPending.partial) : '<span class="typing"><i></i><i></i><i></i></span>';
  if (atBottom) box.scrollTop = box.scrollHeight;
}

async function sendChat() {
  const text = S.chatDraft.trim();
  if (!text) throw Error('Write a message first.');
  if (S.chatPending) throw Error('Wait for the current answer to finish.');
  const chatId = S.chat?.id ?? null;
  S.chatPending = {text, partial: ''};
  S.chatDraft = '';
  renderThread();
  try {
    const chat = await task('chat', {message: text, chat_id: chatId, job_id: chatId ? null : S.chatJob, use_profile: chatId ? S.chat.use_profile : S.chatProfile}, null, partial => {
      if (S.chatPending) {
        S.chatPending.partial = partial;
        updatePending();
      }
    });
    if (!chat) {
      S.chatDraft = text;
      return;
    }
    if (!S.chat || S.chat.id === chat.id || chatId === null) S.chat = chat;
    S.chats = [chat, ...(S.chats || []).filter(c => c.id !== chat.id)];
  } catch (err) {
    if (!S.chatDraft) S.chatDraft = text;  // Nothing was saved: keep what you wrote.
    throw err;
  } finally {
    S.chatPending = null;
    if (S.view === 'chat') {
      renderChatList();
      renderThread();
    }
  }
}

function replyDialog(jobId) {
  const job = jobId ? (S.details.get(jobId)?.job || S.jobs.find(j => j.id === jobId)) : null;
  modal('Draft a reply', `<p class="evidence">Paste an email from an employer or recruiter. ${esc(aiLabel())} drafts a reply in your voice from your profile. Nothing is sent: you check it and copy it into your email yourself.</p>${job ? `<div class="notice">About: ${esc(job.title)}${job.company ? ' · ' + esc(job.company) : ''}</div>` : ''}<label class="field"><span>Their email</span><textarea id="reply-email" rows="8" maxlength="12000" placeholder="Paste the whole email here"></textarea></label><label class="field"><span>What you want to say (optional)</span><input id="reply-goal" maxlength="1000" placeholder="For example: accept the interview and suggest Tuesday or Wednesday afternoon"></label><label class="field"><span>Language (optional)</span><input id="reply-language" maxlength="80" placeholder="Same as their email"></label><div id="reply-result"></div>`,
    button('Close', 'close-modal') + button(icon('spark') + ' Draft reply', 'generate-reply', 'primary button-icon', job ? `data-job-id="${job.id}"` : ''));
}

function quickMarksPanel() {
  const chosen = quickMarks();
  return `<section class="panel"><div class="panel-title"><div><h2>Quick marks</h2><p>One-click buttons on every vacancy in the lists, so you can mark one without opening it. Click a lit mark again to undo it. Choose up to four.</p></div>${pill(chosen.length + ' chosen')}</div><div class="mark-options">${orderedStatuses.filter(stage => stage !== 'new').map(stage => `<label class="check-row"><input type="checkbox" data-quick-mark="${stage}" ${chosen.includes(stage) ? 'checked' : ''}> ${cap(stage)}</label>`).join('')}</div></section>`;
}

// Phone access, managed on the PC: a QR code and pairing code for a phone
// on the same Wi-Fi, and the list of paired phones.
function phonePanel() {
  if (REMOTE) return `<section class="panel"><div class="panel-title"><div><h2>This phone</h2><p>Paired with your PC${S.boot.client?.device?` as “${esc(S.boot.client.device)}”`:''}. The PC does the work: keep it on with Job Agent open. Connecting Gmail, Review & send, updates and phone settings happen on the PC.</p></div>${pill('Paired','green')}</div><div class="section-actions">${button('Disconnect this phone','forget-phone')}</div></section>`;
  return `<section class="panel" id="phone-panel"><div class="panel-title"><div><h2>Phone access</h2><p>Use Job Agent on your phone over your home Wi-Fi: the same features and data. This PC keeps doing the work, so it must be on with Job Agent open.</p></div>${pill(S.phone?.running?'On':'Off',S.phone?.running?'green':'')}</div><div id="phone-body"><p class="evidence">Loading…</p></div></section>`;
}

async function loadPhonePanel() {
  if (REMOTE || !$('#phone-body')) return;
  try {
    S.phone = await api('/phone');
  } catch (err) {
    S.phone = {error: err.message};
  }
  renderPhoneBody();
}

function renderPhoneBody() {
  const el = $('#phone-body');
  if (!el) return;
  const p = S.phone || {};
  const badge = $('#phone-panel .panel-title .pill');
  if (badge) badge.outerHTML = pill(p.running ? 'On' : 'Off', p.running ? 'green' : '');
  const devices = (p.devices || []).length ? `<h3>Paired phones</h3><div class="table-wrap"><table><thead><tr><th>Phone</th><th>Paired</th><th>Last used</th><th>Action</th></tr></thead><tbody>${p.devices.map(d => `<tr><td>${esc(d.name)}</td><td>${esc((d.created_at || '').slice(0, 10))}</td><td>${d.last_seen ? esc(ago(d.last_seen)) : '–'}</td><td>${button('Remove', 'phone-remove', 'quiet', `data-device="${esc(d.id)}"`)}</td></tr>`).join('')}</tbody></table></div>` : '';
  if (!p.running) {
    el.innerHTML = `${p.error ? `<div class="notice warning">${esc(p.error)}</div>` : ''}<p class="evidence">Your phone and this PC must be on the same Wi-Fi. ${LINUX ? 'If your phone cannot connect, your firewall may block the port shown after you turn this on: allow it, for example <code>sudo firewall-cmd --add-port=8765/tcp</code> (Fedora) or <code>sudo ufw allow 8765/tcp</code> (Ubuntu).' : 'When you turn this on, Windows may ask whether Python can use private networks: choose Allow.'}</p><div class="section-actions">${button(icon('phone') + ' Turn on phone access', 'phone-on', 'primary button-icon')}</div>${devices}`;
    return;
  }
  const minutes = Math.max(1, Math.ceil((p.code_seconds || 0) / 60));
  el.innerHTML = `<div class="phone-pair">${p.qr_png ? `<img class="qr" src="${esc(p.qr_png)}" alt="QR code that opens ${esc(p.url)} and pairs your phone">` : ''}<div><ol class="steps"><li>On your phone, scan this QR code with the camera, or open <strong class="mono">${esc(p.url)}</strong> in its browser.</li><li>If it asks for a pairing code, enter <strong class="pair-code mono">${esc(p.code || '–')}</strong>${p.code ? ` <small>(valid for ${minutes} min)</small>` : ' <small>(choose New code)</small>'}</li><li>In the browser menu choose <strong>Add to Home screen</strong> to open Job Agent like an app. (iPhone: Share → Add to Home Screen; it may ask for a code once more.)</li></ol><div class="section-actions">${button('New code', 'phone-code')}${button('Turn off phone access', 'phone-off', 'quiet')}</div><p class="evidence">For your home Wi-Fi only: the connection between phone and PC is not encrypted, so do not turn this on in public networks. If the PC's address changes, scan the new code.</p></div></div>${devices}`;
}

function collectionReport() {
  const r = S.boot.collection;
  if (!r) return '<div class="notice">No collection yet. Add sources, choose a country and find opportunities.</div>';
  return `<p class="evidence">${esc(r.country?S.boot.countries[r.country]:'Country not recorded / all countries')}${r.started_at?' · '+esc(new Date(r.started_at).toLocaleString()):''}. Counts describe collection, before your role and experience filters.</p><div class="tags">${pill(`${r.created||0} new`,'green')}${pill(`${r.updated||0} updated`)}${pill(`${r.withdrawn||0} withdrawn`)}</div>${(r.sources||[]).map(s=>`<div class="activity-item"><strong>${esc(s.source?.name||s.source?.board||cap(s.source?.type)||'Source')}</strong> ${pill(s.ok?(s.has_more?'Scan limit reached':'Checked'):'Needs attention',s.ok&&!s.has_more?'green':'amber')}<p>${esc(s.error||s.coverage||`${s.created||0} new, ${s.updated||0} updated.`)}</p>${s.fetched!=null?`<small>${s.fetched} read · ${s.matched??((s.created||0)+(s.updated||0))} in geographic scope · ${s.unknown_country||0} with unknown country · ${s.created||0} new · ${s.updated||0} refreshed</small>`:''}</div>`).join('')}`;
}

function renderMail() {
  const connected = S.boot.gmail?.connected;
  $('#main').insertAdjacentHTML('beforeend', `<div class="content-width"><section class="panel"><div class="panel-title"><div><h2>Your job-alert inbox</h2><p>${esc(S.boot.gmail?.message||S.boot.gmail?.detail||'Connect Gmail with read-only access or import saved email files.')}</p></div>${pill(connected?'Connected · read-only':'Not connected',connected?'green':'')}</div><div class="section-actions">${button(connected?'Disconnect Gmail':'Connect Gmail',connected?'disconnect-gmail':'connect-gmail')}${button('Cancel sign-in','cancel-gmail')}${button('Import .eml','import-eml')}${button('Setup guide','gmail-help')}${button(icon('spark')+' Draft a reply','reply-dialog','button-icon')}</div><br><label class="field"><span>Gmail search</span><input id="email-query" value="${esc(S.emailQuery)}"></label><div class="section-actions">${button('Find job alerts','fetch-mail','primary')}${button('Next batch','next-mail','',S.emailNext?'':'disabled')}</div></section><section class="panel"><div class="panel-title"><div><h2>Review opportunities</h2><p>Select only the vacancies you want to add. Emails stay unchanged.</p></div>${button('Add selected','import-candidates','primary')}</div><div id="email-results"></div></section><section class="panel" id="mail-apps"></section></div>`);
  renderEmailResults();
  renderMailApps();
  if (S.mail === null) api('/mail-applications').then(result => {
    S.mail = result;
    renderMailApps();
  }).catch(err => toast(err.message, true));
}

function renderMailApps() {
  const root = $('#mail-apps');
  if (!root) return;
  const connected = S.boot.gmail?.connected,
    m = S.mail;
  const days = [[90, '3 months'], [180, '6 months'], [365, '1 year'], [730, '2 years']];
  const controls = `<div class="row">${connected ? `<label class="field compact"><span>Read back</span><select id="mail-days">${days.map(([d, label]) => `<option value="${d}" ${S.mailDays === d ? 'selected' : ''}>${label}</option>`).join('')}</select></label>${button(icon('search') + (m?.messages ? ' Scan for new mail' : ' Scan my email'), 'scan-mail', 'primary button-icon')}` : '<p class="evidence">Connect Gmail above to find the applications you have sent.</p>'}${m?.unclear ? button(icon('spark') + ` Read ${m.unclear} unclear email${m.unclear === 1 ? '' : 's'} with ${esc(aiName())}`, 'mail-ai', 'button-icon') : ''}${m?.messages ? button('Forget results', 'forget-mail', 'quiet') : ''}</div>`;
  const head = `<div class="panel-title"><div><h2>Your applications in Gmail</h2><p>Finds the applications you have sent: confirmations, interview invitations and rejections in your mail, matched to your vacancies. Read-only: nothing in your mailbox changes, and only company, position, outcome, date and a short excerpt are kept on this computer.</p></div>${m?.applications ? pill(m.applications + ' applications', 'green') : ''}</div>`;
  if (!m) {
    root.innerHTML = head + '<p class="evidence">Loading…</p>';
    return;
  }
  if (!m.messages) {
    root.innerHTML = head + controls + `<div class="empty"><div class="empty-icon">${icon('applications')}</div><h2>How many applications have you sent?</h2><p>Scan your email to count them, see which got an answer, and mark the matching vacancies so you never apply twice.</p></div>`;
    return;
  }
  const tile = (label, value, detail = '') => `<div class="fit"><small>${label}</small><strong>${value}</strong>${detail ? `<span>${detail}</span>` : ''}</div>`;
  const stats = `<div class="mail-stats">${tile('Applications', m.applications)}${tile('Waiting for a reply', m.waiting)}${tile('Interviews', m.interview)}${tile('Rejections', m.rejected)}${tile('Response rate', m.response_rate + '%', 'Interviews and rejections')}</div>`;
  const top = Math.max(1, ...m.months.map(x => x.count));
  const months = m.months.length ? `<h3>Applications per month</h3><div class="mail-months">${m.months.map(x => `<div class="mail-month"><span>${esc(new Date(x.month + '-01T12:00').toLocaleDateString(undefined, {month: 'short', year: 'numeric'}))}</span><progress max="${top}" value="${x.count}" aria-label="${x.count} applications"></progress><strong>${x.count}</strong></div>`).join('')}</div>` : '';
  const suggestions = m.suggestions.length ? `<div class="mail-suggestions"><h3>Update your stages</h3><p class="evidence">Your mail has news about these vacancies in your list.</p>${m.suggestions.map(s => `<label class="check-row"><input type="checkbox" data-mail-suggestion="${s.job_id}" data-status="${esc(s.suggested)}" checked> <span><strong>${esc(s.title)}</strong> · ${esc(s.company)} — ${esc(cap(s.current))} → ${esc(cap(s.suggested))} <small>(${esc(shortDate(s.date))})</small></span></label>`).join('')}<div class="row">${button('Update selected stages', 'apply-mail-stages', 'primary')}</div></div>` : '';
  const outcome = g => g.outcome === 'applied' ? pill('Waiting', 'blue') : g.outcome === 'interview' ? pill('Interview', 'green') : pill('Rejected');
  const rows = m.groups.slice(0, S.mailAll ? 1000 : 50).map(g => `<tr><td>${esc(g.company || 'Employer not named')}<span class="sub">${esc(g.position || 'Position not stated')}</span></td><td>${esc(shortDate(g.applied_at))}</td><td>${outcome(g)}${g.messages > 1 ? ` <small>${g.messages} emails</small>` : ''}</td><td>${g.job_id ? button('Open', 'open-job', 'quiet', `data-job-id="${g.job_id}"`) : button('Add to Applications', 'add-mail-app', 'quiet', `data-message="${esc(g.message_ids[0])}"`)}</td></tr>`).join('');
  const table = `<h3>Applications found</h3><div class="table-wrap"><table><thead><tr><th>Employer and position</th><th>Applied</th><th>Outcome</th><th>In your list</th></tr></thead><tbody>${rows}</tbody></table></div>${m.groups.length > 50 && !S.mailAll ? button(`Show all ${m.groups.length}`, 'mail-all', 'quiet') : ''}<p class="evidence">Read from ${m.messages} emails by rules${m.unclear ? `; ${m.unclear} name no employer or position — AI can read those` : ''}. A rejection or invitation counts as the answer to the latest application to that employer. Check the original emails when it matters.</p>`;
  root.innerHTML = head + controls + stats + months + suggestions + table;
}

function renderEmailResults() {
  const root = $('#email-results');
  if (!root) return;
  const unread = S.emailUnread.length ? `<div class="notice">${S.emailUnread.length} email${S.emailUnread.length === 1 ? '' : 's'} had no vacancy the rules could read. ${esc(aiName()[0].toUpperCase() + aiName().slice(1))} can read them (10 at a time) and list their vacancies here for review.<div class="row">${button(icon('spark') + ' Read with AI', 'ai-read-alerts', 'button-icon')}</div></div>` : '';
  root.innerHTML = unread + S.emails.length ? `<label class="checkline"><input type="checkbox" id="select-all-emails"> Select all ${S.emails.length} opportunities</label><div class="table-wrap"><table><thead><tr><th>Select</th><th>Opportunity</th><th>Action</th></tr></thead><tbody>${S.emails.map((e,i)=>`<tr><td><input type="checkbox" data-email-index="${i}" ${e._selected?'checked':''} aria-label="Select ${esc(e.title)}"></td><td>${esc(e.title)}<span class="sub">${esc(e.company||'Employer to confirm')} · ${esc(e.location||'Location to confirm')}</span></td><td>${button('Review','edit-email','quiet',`data-email-index="${i}"`)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty"><div class="empty-icon">' + icon('mail') + '</div><h2>Your shortlist starts with a review.</h2><p>Find job alerts or import an .eml message. Nothing is added until you choose it.</p></div>';
}

function mergeEmails(result) {
  const old = new Map(S.emails.map(e => [e.source_url, e]));
  for (const e of result.candidates || [])
    if (!old.has(e.source_url)) old.set(e.source_url, e);
  S.emails = [...old.values()];
  S.emailNext = result.next_page_token ?? S.emailNext;
  const unread = new Map(S.emailUnread.map(e => [e.message_id, e]));
  for (const e of result.unread || []) unread.set(e.message_id, e);
  S.emailUnread = [...unread.values()];
  if (S.view === 'mail') renderPage();
  toast(`${result.candidates?.length||0} opportunities found for review.${result.warnings?.length?' Some messages need manual review.':''}`);
}

// The same reading as the server's profile.split_terms: new lines, commas,
// semicolons, "|", tabs and bullets separate; commas inside brackets stay.
function splitList(text, separators) {
  const parts = [];
  let current = '',
    depth = 0;
  for (const char of text) {
    if ('([{'.includes(char)) depth++;
    else if (')]}'.includes(char) && depth) depth--;
    if (separators.includes(char) && (depth === 0 || char === '\n')) {
      parts.push(current);
      current = '';
      if (char === '\n') depth = 0;
      continue;
    }
    current += char;
  }
  parts.push(current);
  return [...new Map(parts.map(part => part.replace(/\s+/g, ' ').trim().replace(/^(?:[-*+–—]\s+|\(?\d{1,3}[.)]\s+)/, ''))
    .filter(Boolean).map(item => [item.toLowerCase(), item])).values()];
}
const TERM_SEPARATORS = '\n\r,;|\t•·●▪◦‣⁃',
  LINE_SEPARATORS = '\n\r•●▪◦‣⁃';

function profileValue(el) {
  const key = el.dataset.profile;
  let value = el.value;
  if (['skills', 'target_roles', 'related_roles', 'preferred_locations', 'excluded_keywords', 'writing_style.avoid_phrases'].includes(key)) value = splitList(value, TERM_SEPARATORS);
  else if (['evidence', 'writing_style.examples'].includes(key)) value = splitList(value, LINE_SEPARATORS);
  // Languages accept several formats; the server reads them and reports any it cannot.
  else if (['languages', 'search_languages'].includes(key)) value = value;
  else if (key === 'writing_style.max_words') value = Number(value);
  else if (key === 'years_of_experience') {
    value = value.trim() === '' ? null : Number(value);
    if (value !== null && (!Number.isFinite(value) || value < 0 || value > 60)) throw Error('Enter years of experience from 0 to 60, such as 2.5, or leave it blank.');
  }
  const path = key.split('.');
  let target = S.profile;
  for (const segment of path.slice(0, -1)) target = target[segment] ??= {};
  target[path.at(-1)] = value;
}
let invalidProfile = new Map();

function updateAnswer(el) {
  const p = current()?.app;
  if (!p) return;
  const i = Number(el.dataset.answerIndex),
    f = p.form.fields[i];
  let a = p.answers.find(a => a.field_id === f.id);
  if (!a) {
    a = {
      field_id: f.id,
      label: f.label,
      answer: '',
      selected_options: [],
      used_evidence: [],
      review_notes: []
    };
    p.answers.push(a);
  }
  if (f.options?.length) {
    a.selected_options = $$(`[data-answer-index="${i}"]:checked`).map(o => o.value);
    a.answer = f.options.filter(o => a.selected_options.includes(o.value)).map(o => o.label).join('\n');
  } else {
    a.answer = el.value;
    a.selected_options = [];
  }
  a.status = a.answer || a.selected_options.length ? 'user_edited' : 'needs_input';
  a.used_evidence = [];
  a.review_notes = ['Edited by you; review before sending.'];
  mark('app');
}
let searchTimer;
document.addEventListener('input', e => {
  const el = e.target;
  if (el.id === 'job-search') {
    S.query = el.value;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => loadJobs().catch(err => toast(err.message, true)), 250);
  } else if (el.id === 'catalog-search') {
    S.catalogQuery = el.value;
    renderCatalogCards();
  } else if (el.dataset.profile) {
    S.profileRaw[el.dataset.profile] = el.value;
    try {
      profileValue(el);
      invalidProfile.delete(el.dataset.profile);
      el.setCustomValidity('');
    } catch (err) {
      invalidProfile.set(el.dataset.profile, err.message);
      el.setCustomValidity(err.message);
    }
    S.profileDirty = true;
    updateStatus();
  } else if (el.id === 'job-notes') {
    current().notes = el.value;
    mark('notes');
  } else if (el.id === 'letter-text') {
    current().letter = el.value;
    mark('letter');
    $('#word-count').textContent = el.value.trim().split(/\s+/).filter(Boolean).length + ' words';
  } else if (el.dataset.answerIndex !== undefined) updateAnswer(el);
  else if (el.dataset.settings) {
    S.settingsDraft ??= clone(S.boot.settings);
    S.settingsDraft[el.dataset.settings] = el.value;
    S.settingsDirty = true;
    updateStatus();
  } else if (el.id === 'sources-json') {
    S.sourcesText = el.value;
    S.settingsDirty = true;
    updateStatus();
  } else if (el.id === 'market-searches') {
    S.settingsDraft ??= clone(S.boot.settings);
    S.settingsDraft.market.searches = [...new Set(el.value.split('\n').map(x => x.trim()).filter(Boolean))];
    S.settingsDirty = true;
    updateStatus();
  } else if (el.id === 'email-query') S.emailQuery = el.value;
  else if (el.id === 'chat-input') S.chatDraft = el.value;
});
document.addEventListener('change', async e => {
  const el = e.target;
  try {
    if (el.id === 'country') {
      const old = S.country;
      try {
        if (S.settingsDirty) throw Error('Save your settings edits before changing country.');
        adoptSourceSettings(await changeSettings('/country', 'POST', {
          country: el.value
        }));
        S.country = S.boot.settings.active_country || '';
        await refreshCoverage();
        S.jobs = [];
        if (['discover', 'for-you', 'saved', 'applications'].includes(S.view)) await loadJobs();
        else renderPage();
        toast('Country changed. Your shared profile stays the same.');
      } catch (err) {
        el.value = old;
        throw err;
      }
    } else if (el.dataset.quickMark) {
      const chosen = $$('[data-quick-mark]').filter(input => input.checked).map(input => input.dataset.quickMark);
      if (chosen.length > 4) {
        el.checked = false;
        throw Error('Choose up to four quick marks.');
      }
      S.boot.appearance = await api('/appearance', {method: 'PUT', body: {quick_marks: chosen}});
      const badge = el.closest('.panel')?.querySelector('.panel-title .pill');
      if (badge) badge.textContent = chosen.length + ' chosen';
      toast(chosen.length ? `Quick marks: ${chosen.map(cap).join(', ')}.` : 'Quick marks are off.');
    } else if (['status-filter', 'sort', 'experience-filter'].includes(el.id)) {
      S[el.id === 'sort' ? 'sort' : el.id === 'experience-filter' ? 'experience' : 'status'] = el.value;
      await loadJobs();
    } else if (el.dataset.site) {
      S.settingsDraft ??= clone(S.boot.settings);
      if (el.dataset.site === 'linkedin' && el.checked && !S.settingsDraft.market.linkedin_terms_accepted) {
        el.checked = false;
        linkedinNotice();
        return;
      }
      S.settingsDraft.market.enabled[el.dataset.site] = el.checked;
      S.settingsDirty = true;
      updateStatus();
    } else if (['market-related', 'market-everything', 'market-it-only'].includes(el.id)) {
      S.settingsDraft ??= clone(S.boot.settings);
      S.settingsDraft.market[{'market-related': 'include_related_roles', 'market-everything': 'collect_everything', 'market-it-only': 'it_only'}[el.id]] = el.checked;
      S.settingsDirty = true;
      updateStatus();
    } else if (el.id === 'catalog-mode') {
      S.catalogMode = el.value;
      renderCatalogCards();
    } else if (el.id === 'job-stage') {
      current().status = el.value;
      mark('notes');
    } else if (el.id === 'model-picker' && el.value) {
      S.settingsDraft ??= clone(S.boot.settings);
      S.settingsDraft.model = el.value;
      S.settingsDirty = true;
      renderPage();
      updateStatus();
    } else if (el.name === 'ai-provider' || el.id === 'chat-ai') {
      await chooseAi(el.value);
    } else if (el.dataset.aiModel) {
      S.boot.ai = await api('/ai', {method: 'PUT', body: {models: {[el.dataset.aiModel]: el.value}}});
      renderAiPanel();
      toast(`${S.boot.ai.providers.find(p => p.id === el.dataset.aiModel)?.name} will use ${el.value}.`);
    } else if (el.id === 'chat-profile') {
      if (S.chat) S.chat = await api('/chats/' + S.chat.id, {method: 'PATCH', body: {use_profile: el.checked}});
      else S.chatProfile = el.checked;
      renderThread();
    } else if (el.dataset.emailIndex !== undefined && el.type === 'checkbox') S.emails[Number(el.dataset.emailIndex)]._selected = el.checked;
    else if (el.id === 'select-all-emails') {
      S.emails.forEach(x => x._selected = el.checked);
      renderEmailResults();
    }
  } catch (err) {
    toast(err.message, true);
  }
});
async function navigate(view) {
  S.view = view;
  S.showDetail = false;
  shell();
  if (['discover', 'for-you', 'saved', 'applications'].includes(view)) await loadJobs();
  $('#main').focus({
    preventScroll: true
  });
}

function exportText(name, content) {
  const blob = new Blob([content], {
    type: 'text/plain;charset=utf-8'
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 30000);
}
async function chooseUpload(kind, accept) {
  return new Promise((resolve, reject) => {
    const input = document.createElement('input');
    input.type = 'file';
    input.accept = accept;
    input.className = 'hidden';
    document.body.append(input);
    input.addEventListener('cancel', () => {
      input.remove();
      resolve(null);
    }, {
      once: true
    });
    input.addEventListener('change', async () => {
      try {
        const file = input.files[0];
        if (!file) {
          resolve(null);
          return;
        }
        const form = new FormData();
        form.append('kind', kind);
        form.append('file', file);
        resolve(await api('/upload', {
          method: 'POST',
          body: form
        }));
      } catch (err) {
        reject(err);
      } finally {
        input.remove();
      }
    }, {
      once: true
    });
    input.click();
  });
}
async function showHistory(kind) {
  const id = S.selected,
    data = await refreshJob(id);
  const rows = kind === 'letter' ? data.letters : data.preparations;
  modal(kind === 'letter' ? 'Cover-letter versions' : 'Application versions', rows.length ? rows.map((r, i) => `<div class="history-row"><small>${esc(r.created_at)} · ${esc(r.model||'Application answers')}</small><p>${esc(kind==='letter'?r.content:r.payload.answers.map(a=>a.answer).filter(Boolean).join('\n')||'Questions without answers')}</p>${button('Restore for editing','restore-version','',`data-kind="${kind}" data-version-index="${i}" data-job-id="${id}"`)}</div>`).join('') : '<p class="muted">No saved versions yet.</p>');
}

function commands() {
  modal('Quick actions', `<input id="command-search" type="search" placeholder="Search actions…" aria-label="Search quick actions"><div class="command-list">${Object.entries(titles).map(([view,[label]])=>`<button data-view="${view}">${icon(view==='for-you'?'spark':view)}${label}</button>`).join('')}${button(icon('plus')+' Add opportunity','add-job')}${button(icon('spark')+' Find opportunities','collect')}${button(icon('mail')+' Draft a reply to an email','reply-dialog')}${button(icon('sun')+' Switch theme','theme')}</div>`);
  $('#command-search').addEventListener('input', e => $$('.command-list button').forEach(b => b.classList.toggle('hidden', !b.textContent.toLowerCase().includes(e.target.value.toLowerCase()))));
}

function help() {
  modal('Keyboard shortcuts & help', `<p class="notice">Your country changes the search context; your profile and writing style stay shared.</p><div class="keyboard-help"><span>Next / previous opportunity</span><kbd>J / K</kbd><span>Move in the list</span><kbd>↓ / ↑</kbd><span>Search opportunities</span><kbd>/</kbd><span>Quick actions</span><kbd>Ctrl K</kbd><span>Save current editor</span><kbd>Ctrl S</kbd><span>Focus opportunity search</span><kbd>Ctrl F</kbd><span>Switch theme</span><kbd>Ctrl Shift L</kbd><span>Move through fields</span><kbd>Tab / Shift Tab</kbd><span>Close dialog</span><kbd>Escape</kbd></div><br><h3>Applications are always your decision</h3><p class="evidence">Drafts are editable and saved as versions. Review & send opens a complete review before one explicit send action. Complex employer forms still need manual completion.</p><h3>Working locally</h3><p class="evidence">Source collection and Gmail need internet access. Writing uses your installed Ollama model. Your desktop interface uses bundled assets and does not require hosting.</p>`);
}

function ensureAnswers(payload) {
  if (!payload) return;
  payload.answers = payload.form.fields.map(f => payload.answers.find(a => a.field_id === f.id) || {
    field_id: f.id,
    label: f.label,
    answer: '',
    selected_options: [],
    used_evidence: [],
    review_notes: [],
    status: 'needs_input'
  });
}

function addJobDialog() {
  modal('Add an opportunity', `<div class="notice">Import a supported public job URL, or enter the details yourself.</div><label class="field"><span>Original posting URL</span><input id="import-url" type="url" placeholder="https://company.example/careers/role"></label>${button('Read public posting','import-url','button-icon')}<hr><div class="form-grid">${field('Role title','title','',{group:'import'})}${field('Employer','company','',{group:'import'})}${field('Location','location','',{group:'import'})}${field('Application URL (optional)','apply_url','',{group:'import'})}<div class="span-2">${field('Job description','description','',{group:'import',lines:5})}</div></div>`, button('Cancel', 'close-modal') + button('Add details', 'save-import', 'primary'));
}
async function readQuestions(manual_text = '') {
  const id = S.selected,
    b = current();
  if (b.dirty.has('app')) await savePart('app', id);
  const rev = b.revision;
  await task('questions', {
    job_id: id,
    manual_text
  }, async p => {
    ensureAnswers(p);
    if (b.revision === rev) {
      b.app = p;
      b.dirty.delete('app');
    } else toast('Questions were saved in history. Your newer edits were kept.');
    await refreshJob(id);
    if (S.selected === id) renderDetail();
  });
}

function profileSuggestion(result) {
  S.suggestion = result;
  const fields = Object.keys(result).filter(k => !k.startsWith('_') && k !== 'cv_text' && (Array.isArray(result[k]) ? result[k].length : result[k]));
  modal('Review profile suggestions', `<div class="notice">Choose the fields you want to copy into your editable profile. Saving the profile is a separate step.</div>${fields.map(k=>`<section class="history-row"><label class="checkline"><input type="checkbox" data-suggestion="${esc(k)}">${esc(cap(k))}</label><div class="prose">${esc(Array.isArray(result[k])?result[k].join('\n'):typeof result[k]==='object'?Object.entries(result[k]).map(([a,b])=>a+': '+b).join('\n'):result[k])}</div></section>`).join('')}${(result._review_notes||[]).map(n=>`<p class="evidence">${esc(n)}</p>`).join('')}`, button('Cancel', 'close-modal') + button('Use selected suggestions', 'apply-suggestion', 'primary'));
}
let review = null;
async function reviewApplication() {
  const id = S.selected,
    b = current();
  ensureAnswers(b.app);
  await savePart('app', id);
  const revision = b.revision,
    payload = clone(b.app);
  const key = 'preview:' + id;
  S.tasks.set(key, 'Preparing your browser review…');
  updateStatus();
  try {
    const {
      task_id
    } = await api('/delivery/preview', {
      method: 'POST',
      body: {
        job_id: id,
        payload
      }
    });
    const result = await pollTask(task_id, key);
    if (b.revision !== revision) {
      await api('/delivery/' + result.preview_id + '/close', {
        method: 'POST',
        body: {}
      });
      throw Error('Your answers changed while the preview opened. Review them again.');
    }
    review = {
      ...result,
      job_id: id,
      revision,
      payload
    };
    const p = result.preview;
    modal('Review your application', `<div class="notice warning">Check every value below and the filled browser. Nothing is sent until you press Send reviewed application.</div><div class="section-label">Destination</div><div class="review-value">${esc(p.destination)}</div>${p.fields.map(a=>{const f=payload.form.fields.find(f=>f.id===a.field_id);return `<h3>${esc(f?.label||a.field_id)}</h3><div class="review-value">${esc(a.selected_options?.length?a.selected_options.map(v=>f?.options?.find(o=>o.value===v)?.label||v).join('\n'):a.answer||'(Blank)')}</div>`;}).join('')}${p.files.map(f=>`<h3>Attachment: ${esc(f.name)}</h3><p class="review-value">${esc(f.size)} bytes\nSHA-256: ${esc(f.sha256)}</p>`).join('')}${p.technical_fields?.length?`<p class="evidence">Form security fields: ${p.technical_fields.map(esc).join(', ')}. Their values must remain unchanged.</p>`:''}<label class="checkline"><input id="confirm-review" type="checkbox">I have reviewed the answers, files and destination.</label><div id="send-result"></div>`, button('Cancel / edit', 'close-modal') + button('Send reviewed application', 'send-application', 'primary', 'disabled'));
    modalCleanup = () => {
      api('/delivery/' + result.preview_id + '/close', {
        method: 'POST',
        body: {}
      }).catch(() => {});
      review = null;
    };
    $('#confirm-review').addEventListener('change', e => $('[data-action="send-application"]').disabled = !e.target.checked);
  } finally {
    S.tasks.delete(key);
    updateStatus();
  }
}

function toggleTheme() {
  S.theme = S.theme === 'dark' ? 'light' : 'dark';
  document.body.dataset.theme = S.theme;
  if (REMOTE) {
    local.set(THEME_KEY, S.theme);  // The PC keeps its own theme.
    return;
  }
  api('/appearance', {
    method: 'PUT',
    body: {
      theme: S.theme
    }
  }).catch(err => toast('Theme changed for this session. ' + err.message, true));
}
async function action(name, el) {
  if (name === 'dismiss-toast') {
    $('#notifications').replaceChildren();
    return;
  }
  if (name === 'more-menu') {
    const views = [['chat', 'AI chat', 'chat'], ['profile', 'Profile & writing', 'profile'], ['sources', 'Source library', 'sources'], ['mail', 'Job alerts', 'mail'], ['settings', 'Settings & activity', 'settings']];
    modal('More', `<div class="command-list more-list">${views.map(([view, label, i]) => `<button data-view="${view}" ${view===S.view?'aria-current="page"':''}>${icon(i)}${label}</button>`).join('')}${button(icon('plus')+' Add opportunity','add-job')}${button(icon('sun')+' Switch theme','theme')}${button(icon('help')+' Help','help')}</div>`);
    return;
  }
  if (['phone-on', 'phone-off', 'phone-code', 'phone-remove'].includes(name)) {
    const routes = {
      'phone-on': ['/phone', 'POST', {enabled: true}],
      'phone-off': ['/phone', 'POST', {enabled: false}],
      'phone-code': ['/phone/code', 'POST', {}],
      'phone-remove': ['/phone/devices/' + encodeURIComponent(el.dataset.device || ''), 'DELETE', undefined]
    };
    const [path, method, body] = routes[name];
    S.phone = await api(path, {method, body});
    renderPhoneBody();
    if (name === 'phone-on') toast('Phone access is on. Scan the code with your phone.');
    if (name === 'phone-remove') toast('That phone can no longer open Job Agent.');
    return;
  }
  if (name === 'forget-phone') {
    local.set(DEVICE_KEY, null);
    token = '';
    pairScreen('This phone is disconnected. Pair it again with a new code from your PC.');
    return;
  }
  if (name === 'close-modal') {
    closeModal();
    return;
  }
  if (name === 'analyze-job' || name === 'interview-prep') {
    const id = S.selected,
      kind = name === 'analyze-job' ? 'analyze' : 'interview';
    await task(kind, {job_id: id}, result => {
      const data = S.details.get(id);
      if (data) data.insights = result;
      if (S.selected === id) renderDetail();
      toast(kind === 'analyze' ? 'Analysis ready. AI can be wrong: check it against the advert.' : 'Interview preparation ready.');
    });
    return;
  }
  if (name === 'chat-about-job') {
    S.chat = null;
    S.chatJob = S.selected;
    S.chatProfile = true;
    S.chatOpen = true;
    await navigate('chat');
    $('#chat-input')?.focus();
    return;
  }
  if (name === 'new-chat') {
    S.chat = null;
    S.chatJob = null;
    S.chatOpen = true;
    renderChat();
    $('#chat-input')?.focus();
    return;
  }
  if (name === 'open-chat') {
    S.chat = await api('/chats/' + Number(el.dataset.chat));
    S.chatOpen = true;
    renderChat();
    return;
  }
  if (name === 'chat-back') {
    S.chatOpen = false;
    S.chat = null;
    renderChat();
    return;
  }
  if (name === 'delete-chat') {
    const id = Number(el.dataset.chat);
    if (!confirm('Delete this conversation? It cannot be restored.')) return;
    await api('/chats/' + id, {method: 'DELETE'});
    S.chats = (S.chats || []).filter(c => c.id !== id);
    if (S.chat?.id === id) S.chat = null;
    renderChat();
    toast('Conversation deleted.');
    return;
  }
  if (name === 'chat-detach-job') {
    if (S.chat) S.chat = await api('/chats/' + S.chat.id, {method: 'PATCH', body: {job_id: null}});
    else S.chatJob = null;
    renderThread();
    return;
  }
  if (name === 'chat-prompt') {
    S.chatDraft = el.dataset.prompt || '';
    await sendChat();
    return;
  }
  if (name === 'send-chat') {
    await sendChat();
    return;
  }
  if (name === 'copy-message') {
    const message = S.chat?.messages?.[Number(el.dataset.index)];
    if (message) {
      await navigator.clipboard.writeText(message.content);
      toast('Copied.');
    }
    return;
  }
  if (name === 'reply-dialog') {
    replyDialog(el.dataset.jobId ? Number(el.dataset.jobId) : (S.view === 'chat' ? chatJobInfo()?.id : null));
    return;
  }
  if (name === 'generate-reply') {
    const email = $('#reply-email')?.value.trim() || '';
    if (!email) throw Error('Paste the email you want to reply to.');
    const jobId = el.dataset.jobId ? Number(el.dataset.jobId) : null;
    await task('reply', {email, goal: $('#reply-goal').value, language: $('#reply-language').value, job_id: jobId}, result => {
      const root = $('#reply-result');
      if (!root) return toast('Your reply draft is ready, but the dialog was closed. Draft it again to see it.');
      root.innerHTML = `<label class="field"><span>Subject</span><input id="reply-subject" value="${esc(result.subject)}"></label><label class="field"><span>Your reply</span><textarea id="reply-text" rows="10">${esc(result.reply)}</textarea></label><div class="notice warning"><strong>Check before sending</strong>${result.review_notes.map(n => `<p>${esc(n)}</p>`).join('')}</div><div class="row">${button(icon('copy') + ' Copy reply', 'copy-reply', 'primary button-icon')}<small class="evidence">By ${esc(result.model)}</small></div>`;
      $('#reply-text').focus();
    });
    return;
  }
  if (name === 'copy-reply') {
    await navigator.clipboard.writeText($('#reply-text').value);
    toast('Reply copied. Paste it into your email, check it, and send it yourself.');
    return;
  }
  if (name === 'save-ai-key') {
    const provider = el.dataset.provider,
      input = $('#ai-key-' + provider);
    if (!input?.value.trim()) throw Error('Paste the API key first.');
    const result = await api('/ai/keys/' + encodeURIComponent(provider), {method: 'POST', body: {key: input.value}});
    input.value = '';
    S.aiModels = {...S.aiModels, ...(result.models || {})};
    delete result.models;
    S.boot.ai = result;
    renderAiPanel();
    const p = result.providers.find(item => item.id === provider);
    toast(`${p.name} key checked and saved. Choose ${p.name} above to use it.`);
    return;
  }
  if (name === 'remove-ai-key') {
    const provider = el.dataset.provider;
    S.boot.ai = await api('/ai/keys/' + encodeURIComponent(provider), {method: 'DELETE'});
    delete S.aiModels[provider];
    renderAiPanel();
    toast('Key removed from this computer.');
    return;
  }
  if (name === 'refresh-ai-models') {
    const provider = el.dataset.provider;
    await task('ai-models', {provider}, result => {
      S.aiModels[provider] = result.models;
      renderAiPanel();
      toast(`${result.models.length} models available.`);
    });
    return;
  }
  if (name === 'confirm-ai') {
    const provider = el.dataset.provider;
    S.boot.ai = await api('/ai', {method: 'PUT', body: {provider, consent: true}});
    closeModal();
    renderAiPanel();
    if (S.view === 'chat') renderThread();
    const p = S.boot.ai.providers.find(item => item.id === provider);
    toast(`AI now uses ${p.name} (${p.model}).`);
    return;
  }
  if (name === 'save-ai-limit') {
    const limit = Number($('#ai-limit').value);
    if (!Number.isInteger(limit) || limit < 0) throw Error('Enter a whole number of tokens, or 0 for no limit.');
    S.boot.ai = await api('/ai', {method: 'PUT', body: {monthly_token_limit: limit}});
    renderAiPanel();
    toast(limit ? `Cloud AI stops at ${tokens(limit)} tokens a month.` : 'No monthly limit for cloud AI.');
    return;
  }
  if (name === 'commands') {
    commands();
    return;
  }
  if (name === 'help') {
    help();
    return;
  }
  if (name === 'theme') {
    toggleTheme();
    return;
  }
  if (name === 'create-shortcuts') {
    await task('shortcuts', {}, result => toast(result.message));
    return;
  }
  if (name === 'back-list') {
    S.showDetail = false;
    $('.results-layout').classList.remove('has-selection');
    return;
  }
  if (name === 'more-jobs') {
    await loadMore();
    return;
  }
  if (name === 'reset-filters') {
    S.query = '';
    S.status = '';
    S.sort = 'match';
    S.experience = DEFAULT_EXPERIENCE;
    S.posted = '';
    S.minScore = '';
    S.remote = false;
    await loadJobs();
    return;
  }
  if (name === 'open-filters') {
    filtersDialog();
    return;
  }
  if (name === 'clear-filters') {
    $$('#filter-form input').forEach(input => {
      input.checked = input.type === 'radio' ? input.value === '' : input.name === 'experience' && input.value !== 'above';
    });
    return;
  }
  if (name === 'apply-filters') {
    const form = $('#filter-form');
    const ticked = group => $$(`input[name=${group}]:checked`, form).map(input => input.value);
    S.status = ticked('stage').join(',');
    if ($('input[name=experience]', form)) {
      const groups = ticked('experience');
      S.experience = groups.length === 0 || groups.length === 3 ? '' : groups.join(',');
    }
    S.posted = ticked('posted')[0] || '';
    S.minScore = ticked('score')[0] || '';
    S.remote = ticked('remote').length > 0;
    closeModal();
    await loadJobs();
    return;
  }
  if (name === 'remove-filter') {
    const [kind, value] = el.dataset.filter.split(':');
    if (kind === 'status') S.status = S.status.split(',').filter(stage => stage !== value).join(',');
    if (kind === 'experience') S.experience = DEFAULT_EXPERIENCE;
    if (kind === 'posted') S.posted = '';
    if (kind === 'score') S.minScore = '';
    if (kind === 'remote') S.remote = false;
    await loadJobs();
    return;
  }
  if (name === 'quick-mark') {
    const id = Number(el.dataset.id),
      mark = el.dataset.mark;
    const job = S.jobs.find(item => item.id === id);
    const buffer = S.buffers.get(id);
    if (buffer?.dirty.has('notes')) throw Error('Save your note and stage edits for this vacancy first.');
    const previous = job?.status || buffer?.status || 'new';
    // Clicking a lit mark again undoes it, back to the stage it had before.
    const next = previous === mark ? (S.markUndo.get(id) || 'new') : mark;
    const status = await setStage(id, next);
    if (previous !== mark) S.markUndo.set(id, previous);
    else S.markUndo.delete(id);
    const hidden = !S.jobs.some(item => item.id === id);
    const undo = button('Undo', 'undo-mark', 'quiet toast-action', `data-id="${id}" data-status="${esc(previous)}"`);
    toast(previous === mark ? `Mark removed: back to ${cap(status)}.` :
      `Marked as ${cap(status)}${hidden ? ' and hidden by your filters' : ''}.`, false, previous === mark ? '' : undo);
    return;
  }
  if (name === 'undo-mark') {
    const id = Number(el.dataset.id);
    const status = await setStage(id, el.dataset.status || 'new');
    S.markUndo.delete(id);
    toast(`Undone: back to ${cap(status)}.`);
    return;
  }
  if (name === 'market-search') {
    const query = S.query.trim();
    if (query.length < 2) throw Error('Type a role or skill to search job sites.');
    const running = task('market-search', {query, country: S.country}, async r => {
      await refreshCoverage();
      await loadJobs();
      const failed = (r.sources || []).filter(s => !s.ok).length;
      toast(`Searched ${r.sources.length} job site${r.sources.length===1?'':'s'} for “${query}”: ${r.found} relevant adverts, ${r.created} new.${failed?` ${failed} need attention; see Settings & activity.`:''}`);
    });
    renderCards();
    try {
      await running;
    } finally {
      renderCards();
    }
    return;
  }
  if (name === 'save-search') {
    if (S.settingsDirty) throw Error('Save your settings edits first.');
    const draft = clone(S.boot.settings);
    draft.market.searches = [...new Set([...(draft.market.searches || []), S.query.trim()])];
    S.boot.settings = await changeSettings('/settings', 'PUT', draft);
    renderCards();
    toast('Saved. Every refresh now also searches job sites for this.');
    return;
  }
  if (name === 'check-updates') {
    S.update = await api('/updates');
    renderPage();
    toast(S.update.newer ? `Version ${S.update.latest} is available.` : (S.update.reason || 'You have the latest version.'));
    return;
  }
  if (name === 'apply-update') {
    if (dirty()) throw Error('Save your edits and let running tasks finish first: the app closes to update.');
    modal('Update Personal Job Agent', `<p>The app closes, installs version ${esc(S.update.latest)} and reopens, usually within a minute. A window shows the progress.</p><p class="evidence">Your profile, vacancies, notes and drafts are not changed. If the update fails, the version you have now opens again.</p>`,
      button('Cancel', 'close-modal') + button('Update and restart', 'confirm-update', 'primary'));
    return;
  }
  if (name === 'confirm-update') {
    await api('/updates/apply', {method: 'POST', body: {tag: S.update.tag}});
    closeModal();
    toast('Updating… the app closes now and reopens when the new version is ready.');
    return;
  }
  if (name === 'accept-linkedin') {
    S.settingsDraft ??= clone(S.boot.settings);
    S.settingsDraft.market.linkedin_terms_accepted = true;
    S.settingsDraft.market.enabled.linkedin = true;
    S.settingsDirty = true;
    closeModal();
    renderPage();
    updateStatus();
    toast('LinkedIn public search turns on when you save settings.');
    return;
  }
  if (name === 'collect') {
    if (S.settingsDirty) throw Error('Save your edited source settings before starting a collection.');
    if ($('#modal').open) closeModal();
    await task('collect', {}, async result => {
      S.boot.collection = result;
      if ($('.sidebar-bottom')) $('.sidebar-bottom').innerHTML = refreshCard();
      await refreshCoverage();
      if (['discover', 'for-you', 'saved', 'applications'].includes(S.view)) await loadJobs();
      else if (S.view === 'settings') renderPage();
      toast(`${result.created||0} new opportunities. ${result.sources?.filter(x=>!x.ok).length||0} sources need attention; see Settings & activity.${result.removed?` ${result.removed.toLocaleString()} long-closed vacancies cleaned up.`:''}`);
    });
    return;
  }
  if (name === 'save-notes' || name === 'save-letter' || name === 'save-answers') {
    await savePart({
      'save-notes': 'notes',
      'save-letter': 'letter',
      'save-answers': 'app'
    } [name]);
    if (name === 'save-notes') await loadJobs();
    else renderDetail();
    toast('Saved to your private workspace.');  // once the list shows the change
    return;
  }
  if (name === 'bookmark') {
    const b = current();
    if (!['new', 'saved'].includes(b.status)) throw Error('Change the stage explicitly in Notes & stage.');
    b.status = b.status === 'saved' ? 'new' : 'saved';
    mark('notes');
    await savePart('notes');
    await loadJobs();
    toast(b.status === 'saved' ? 'Opportunity saved.' : 'Opportunity returned to new.');
    return;
  }
  if (name === 'generate-letter' || name === 'generate-answers') {
    await generate(name === 'generate-letter' ? 'letter' : 'answers');
    return;
  }
  if (name === 'letter-history' || name === 'answers-history') {
    await showHistory(name === 'letter-history' ? 'letter' : 'app');
    return;
  }
  if (name === 'restore-version') {
    const id = Number(el.dataset.jobId),
      b = S.buffers.get(id),
      data = S.details.get(id),
      kind = el.dataset.kind,
      index = Number(el.dataset.versionIndex);
    if (b.dirty.has(kind)) await savePart(kind, id);
    if (kind === 'letter') b.letter = data.letters[index].content;
    else b.app = clone(data.preparations[index].payload);
    b.dirty.add(kind);
    b.revision++;
    closeModal();
    renderDetail();
    updateStatus();
    toast('Version restored for editing. Save to keep it as the latest version.');
    return;
  }
  if (name === 'export-letter') {
    exportText('cover-letter.txt', current().letter);
    return;
  }
  if (name === 'export-answers') {
    exportText('application-answers.txt', answersText(current().app));
    return;
  }
  if (name === 'copy-apply') {
    const b = current(),
      j = S.details.get(S.selected)?.job;
    if (b.dirty.has('app')) await savePart('app');
    try {
      await navigator.clipboard.writeText(answersText(b.app));
    } catch {
      exportText('application-answers.txt', answersText(b.app));
      toast('Copying was blocked, so the answers were saved as a text file instead.');
    }
    const url = safeUrl(j?.apply_url || j?.source_url || '');
    if (url) {
      const a = document.createElement('a');
      a.href = url;
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      a.click();
    }
    toast('Answers copied. Paste them into the employer’s form, attach your files there, then set the stage to Applied in Notes & stage.');
    return;
  }
  if (name === 'read-questions') {
    await readQuestions();
    return;
  }
  if (name === 'paste-questions') {
    modal('Paste application questions', '<p class="notice">One question per line. Add choices with a vertical bar: Preferred arrangement | Remote | Hybrid | On-site</p><label class="field"><span>Questions and available options</span><textarea id="manual-questions" rows="10"></textarea></label>', button('Cancel', 'close-modal') + button('Use these questions', 'use-questions', 'primary'));
    return;
  }
  if (name === 'use-questions') {
    const text = $('#manual-questions').value;
    if (!text.trim()) throw Error('Paste at least one question.');
    closeModal();
    await readQuestions(text);
    return;
  }
  if (name === 'attach-file') {
    const b = current(),
      f = b.app.form.fields[Number(el.dataset.fieldIndex)];
    const result = await chooseUpload('attachment', '.pdf,.docx,.doc,.txt');
    if (result) {
      b.app.attachments ??= {};
      b.app.attachment_files ??= {};
      b.app.attachments[f.id] = result.upload_id;
      b.app.attachment_files[f.id] = result;
      b.dirty.add('app');
      b.revision++;
      if (b === current()) renderDetail();
      updateStatus();
    }
    return;
  }
  if (name === 'remove-file') {
    const p = current().app,
      f = p.form.fields[Number(el.dataset.fieldIndex)];
    delete p.attachments[f.id];
    delete p.attachment_files?.[f.id];
    mark('app');
    renderDetail();
    return;
  }
  if (name === 'clear-choice') {
    const b = current();
    ensureAnswers(b.app);
    const f = b.app.form.fields[Number(el.dataset.fieldIndex)],
      a = b.app.answers.find(a => a.field_id === f.id);
    a.answer = '';
    a.selected_options = [];
    a.status = 'needs_input';
    mark('app');
    renderDetail();
    return;
  }
  if (name === 'review-application') {
    await reviewApplication();
    return;
  }
  if (name === 'send-application') {
    if (!review || !$('#confirm-review')?.checked) throw Error('Review and confirm the application first.');
    if (S.buffers.get(review.job_id).revision !== review.revision) throw Error('Your answers changed. Close this preview and review again.');
    const {
      preview_id,
      preview,
      job_id
    } = review, key = 'send:' + preview_id;
    review.sent = true;
    S.tasks.set(key, 'Sending the reviewed application once…');
    updateStatus();
    try {
      const {
        task_id
      } = await api(`/delivery/${preview_id}/send`, {
        method: 'POST',
        body: {
          token: preview.token
        }
      });
      const result = await pollTask(task_id, key);
      if ($('#send-result')) $('#send-result').innerHTML = `<div class="notice warning">${esc(result.message)}</div>`;
      await refreshJob(job_id);
      toast(result.message);
    } catch (err) {
      if ($('#send-result')) $('#send-result').innerHTML = `<div class="notice error">${esc(err.message)} Check the employer page before attempting again.</div>`;
      throw err;
    } finally {
      await refreshJob(job_id).catch(() => {});
      S.tasks.delete(key);
      updateStatus();
      el.disabled = true;
    }
    return;
  }
  if (name === 'save-profile') {
    if (invalidProfile.size) throw Error([...invalidProfile.values()][0]);
    const submitted = JSON.stringify(S.profile);
    const result = await api('/profile', {
      method: 'PUT',
      body: S.profile
    });
    if (JSON.stringify(S.profile) === submitted) {
      S.profile = result;
      S.profileRaw = {};
      S.profileDirty = false;
    }
    S.boot.profile = result;
    S.details.clear();
    if (S.view === 'profile' && !S.profileDirty) renderPage();
    toast(`Profile saved: ${(result.skills||[]).length} skills, ${(result.target_roles||[]).length} target roles. Updating matches in the background…`);
    updateStatus();
    // Stored matches follow the profile; recompute them now, not on the next list.
    task('rematch', {}, async r => {
      if (['discover', 'for-you', 'saved', 'applications'].includes(S.view)) await loadJobs();
      toast(`Profile saved. ${(r.matched||0).toLocaleString()} vacancies re-matched; new drafts use these facts and preferences.`);
    }).catch(err => toast(err.message, true));
    return;
  }
  if (name === 'import-cv') {
    const result = await chooseUpload('cv', '.pdf,.docx,.txt,.md');
    if (result) {
      S.profile.cv_text = result.text;
      delete S.profileRaw.cv_text;
      S.profileDirty = true;
      renderPage();
      updateStatus();
      toast('CV text loaded for review. Save your profile when ready.');
    }
    return;
  }
  if (name === 'suggest-profile') {
    await task('suggest-profile', {
      cv_text: S.profile.cv_text
    }, profileSuggestion);
    return;
  }
  if (name === 'apply-suggestion') {
    $$('[data-suggestion]:checked').forEach(c => {
      S.profile[c.dataset.suggestion] = clone(S.suggestion[c.dataset.suggestion]);
      delete S.profileRaw[c.dataset.suggestion];
      invalidProfile.delete(c.dataset.suggestion);
    });
    S.profileDirty = true;
    closeModal();
    if (S.view === 'profile') renderPage();
    updateStatus();
    return;
  }
  if (name === 'catalog-employers' || name === 'catalog-boards') {
    S.catalogTab = name === 'catalog-employers' ? 'employers' : 'boards';
    S.catalogMode = 'all';
    renderPage();
    return;
  }
  if (name === 'add-source' || name === 'add-visible-sources' || name === 'refresh-country-sources') {
    if (S.settingsDirty) throw Error('Save your settings edits before adding library sources.');
    const entries = name === 'refresh-country-sources' ? S.boot.catalog.filter(e => !S.country || e.countries.includes(S.country)) : filteredCatalog();
    const ids = name === 'add-source' ? [el.dataset.sourceId] : entries.filter(e => e.source).map(e => e.id);
    if (!ids.length) throw Error('No automatic collectors are visible. Choose Employers and Automatic collection.');
    adoptSourceSettings(await changeSettings('/sources/add', 'POST', {
      ids,
      country: S.country
    }));
    await refreshCoverage();
    if (S.view === 'sources') renderPage();
    toast('Source configuration saved. You can now collect these employers.');
    return;
  }
  if (name === 'go-sources') {
    await navigate('sources');
    return;
  }
  if (name === 'save-adzuna' || name === 'remove-adzuna') {
    if (name === 'save-adzuna') {
      const id = $('#adzuna-id').value.trim(), secret = $('#adzuna-secret').value.trim();
      if (!id || !secret) throw Error('Paste both the Application ID and the Application key.');
      S.boot.adzuna = await api('/adzuna-key', {method: 'POST', body: {app_id: id, app_key: secret}});
      toast('Adzuna key checked and saved. The next refresh searches Adzuna.');
    } else {
      S.boot.adzuna = await api('/adzuna-key', {method: 'DELETE'});
      toast('Adzuna key removed from this computer.');
    }
    if (S.view === 'settings') renderPage();
    await refreshCoverage();
    return;
  }
  if (name === 'remove-source') {
    S.settingsDraft ??= clone(S.boot.settings);
    if (S.sourcesText !== undefined) {
      S.settingsDraft.sources = JSON.parse(S.sourcesText);
      delete S.sourcesText;
    }
    S.settingsDraft.sources.splice(Number(el.dataset.sourceIndex), 1);
    S.settingsDirty = true;
    renderPage();
    updateStatus();
    return;
  }
  if (name === 'save-settings') {
    const initial = JSON.stringify([S.settingsDraft, S.sourcesText]);
    const draft = clone(S.settingsDraft || S.boot.settings);
    if (S.sourcesText !== undefined) draft.sources = JSON.parse(S.sourcesText);
    S.boot.settings = await changeSettings('/settings', 'PUT', draft);
    if (initial === JSON.stringify([S.settingsDraft, S.sourcesText])) {
      S.settingsDraft = null;
      delete S.sourcesText;
      S.settingsDirty = false;
    }
    renderPage();
    updateStatus();
    toast(S.settingsDirty ? 'Earlier settings saved. Your newer edits are still unsaved.' : 'Settings saved.');
    return;
  }
  if (name === 'check-setup') {
    await task('setup-status', {}, result => {
      if ($('#setup-result')) $('#setup-result').innerHTML = `<pre class="prose">${esc(Object.entries(result).map(([k,v])=>cap(k)+': '+(Array.isArray(v)?v.join(', '):v)).join('\n'))}</pre>`;
    });
    await task('models', {}, r => {
      if ($('#model-picker')) $('#model-picker').innerHTML = options(r.models || [], S.boot.settings.model, 'Choose an installed model');
    });
    return;
  }
  if (name === 'start-ollama' || name === 'download-model' || name === 'install-pdf') {
    await task(name, {}, r => toast(r.message || 'Setup action completed.'));
    return;
  }
  if (name === 'add-job') {
    addJobDialog();
    return;
  }
  if (name === 'import-url') {
    const url = $('#import-url').value;
    await task('import-url', {
      url
    }, async r => {
      closeModal();
      await navigate('discover');
      if (r.ids?.length) await selectJob(r.ids[0]);
      toast('Public posting imported.');
    });
    return;
  }
  if (name === 'save-import') {
    const job = {
      source: 'manual',
      source_url: $('#import-url').value
    };
    $$('[data-import]').forEach(e => job[e.dataset.import] = e.value);
    const r = await api('/jobs', {
      method: 'POST',
      body: job
    });
    closeModal();
    await navigate('discover');
    await selectJob(r.id);
    toast(r.created ? 'Opportunity added.' : 'Existing opportunity updated.');
    return;
  }
  if (name === 'connect-gmail') {
    const result = await chooseUpload('gmail', '.json');
    if (result) await task('gmail-connect', {
      upload_id: result.upload_id
    }, async r => {
      S.boot.gmail = r;
      if (S.view === 'mail') renderPage();
      toast('Gmail connection updated.');
    });
    return;
  }
  if (name === 'cancel-gmail') {
    const r = await api('/gmail/cancel', {
      method: 'POST',
      body: {}
    });
    toast(r.cancel_requested ? 'Sign-in cancellation requested.' : 'No sign-in is running.');
    return;
  }
  if (name === 'disconnect-gmail') {
    await task('gmail-disconnect', {}, r => {
      S.boot.gmail = r;
      renderPage();
      toast('Local Gmail connection removed.');
    });
    return;
  }
  if (name === 'import-eml') {
    const result = await chooseUpload('eml', '.eml');
    if (result) mergeEmails(result);
    return;
  }
  if (name === 'scan-mail') {
    S.mailDays = Number($('#mail-days')?.value || 365);
    await task('mail-scan', {days: S.mailDays}, result => {
      S.mail = result;
      S.details.clear();  // Vacancy pages show what your mail says.
      renderMailApps();
      toast(`${result.scan.found} new application email${result.scan.found === 1 ? '' : 's'} found in ${result.scan.read} read.${result.scan.more ? ' There is more mail: scan again to continue.' : ''}${result.scan.warnings.length ? ' ' + result.scan.warnings.join(' ') : ''}`);
    });
    return;
  }
  if (name === 'mail-ai') {
    await task('mail-ai', {}, result => {
      S.mail = result;
      S.details.clear();
      renderMailApps();
      toast('AI read the unclear emails. Check the results against the original emails when it matters.');
    });
    return;
  }
  if (name === 'forget-mail') {
    if (!confirm('Forget the applications found in your mail? Your mailbox and vacancies are not changed.')) return;
    S.mail = await api('/mail-applications', {method: 'DELETE'});
    renderMailApps();
    return;
  }
  if (name === 'mail-all') {
    S.mailAll = true;
    renderMailApps();
    return;
  }
  if (name === 'apply-mail-stages') {
    const items = $$('[data-mail-suggestion]').filter(input => input.checked).map(input => ({job_id: Number(input.dataset.mailSuggestion), status: input.dataset.status}));
    if (!items.length) throw Error('Tick the stages to update.');
    S.mail = await api('/mail-applications/stages', {method: 'POST', body: {items}});
    for (const item of items) {
      S.details.delete(item.job_id);
      const b = S.buffers.get(item.job_id);
      if (b && !b.dirty.has('notes')) b.status = item.status;  // Unsaved edits stay yours.
    }
    renderMailApps();
    toast(`${S.mail.updated} stage${S.mail.updated === 1 ? '' : 's'} updated.`);
    return;
  }
  if (name === 'add-mail-app') {
    const result = await api('/mail-applications/add', {method: 'POST', body: {message_id: el.dataset.message}});
    S.mail = result;
    renderMailApps();
    toast('Added to Applications at its stage.');
    return;
  }
  if (name === 'open-job') {
    const id = Number(el.dataset.jobId);
    await navigate('discover');
    await selectJob(id);
    return;
  }
  if (name === 'ai-read-alerts') {
    const batch = S.emailUnread.slice(0, 10);
    await task('gmail-ai-read', {message_ids: batch.map(e => e.message_id)}, result => {
      const done = new Set(batch.map(e => e.message_id));
      S.emailUnread = S.emailUnread.filter(e => !done.has(e.message_id));
      mergeEmails({candidates: result.candidates, warnings: result.warnings, next_page_token: undefined});
    });
    return;
  }
  if (name === 'fetch-mail' || name === 'next-mail') {
    await task('gmail-fetch', {
      query: S.emailQuery,
      page_token: name === 'next-mail' ? S.emailNext : ''
    }, mergeEmails);
    return;
  }
  if (name === 'import-candidates') {
    const candidates = S.emails.filter(e => e._selected).map(e => {
      const copy = clone(e);
      delete copy._selected;
      return copy;
    });
    if (!candidates.length) throw Error('Select at least one opportunity to import.');
    const result = await api('/gmail/import', {
      method: 'POST',
      body: {
        candidates
      }
    });
    S.emails.forEach(e => e._selected = false);
    renderEmailResults();
    toast(`${result.created} new opportunities added; ${result.existing} already existed.`);
    return;
  }
  if (name === 'edit-email') {
    const i = Number(el.dataset.emailIndex),
      e = S.emails[i];
    modal('Review email opportunity', `<div class="form-grid">${['title','company','location'].map(k=>field(cap(k),k,e[k]||'',{group:'email'})).join('')}<div class="span-2">${field('Description excerpt','description',e.description||'',{group:'email',lines:6})}</div></div>${link('Original vacancy',e.source_url)}`, button('Cancel', 'close-modal') + button('Keep changes', 'save-email', 'primary', `data-email-index="${i}"`));
    return;
  }
  if (name === 'save-email') {
    const e = S.emails[Number(el.dataset.emailIndex)];
    $$('[data-email]').forEach(input => e[input.dataset.email] = input.value);
    e._selected = true;
    closeModal();
    renderEmailResults();
    return;
  }
  if (name === 'gmail-help') {
    modal('Connect job alerts', `<p class="notice">Gmail access is read-only. You complete Google sign-in yourself in the system browser.</p><ol class="prose"><li>Create a Google Cloud project and enable the Gmail API.</li><li>Configure OAuth consent for your own account and create a Desktop app OAuth client.</li><li>Download its credentials JSON and choose Connect Gmail.</li><li>Review and allow only the requested read-only access.</li></ol><p class="evidence">You can also download individual emails as .eml files and import them without connecting an account.</p>${link('Google Gmail API setup','https://developers.google.com/workspace/gmail/api/quickstart/python')}`);
    return;
  }
}
document.addEventListener('click', async e => {
  const el = e.target.closest('button');
  if (!el || el.disabled) return;
  const name = el.dataset.action;
  try {
    if (el.dataset.view) {
      if ($('#modal').open) closeModal();
      await navigate(el.dataset.view);
    } else if (el.dataset.job) await selectJob(Number(el.dataset.job));
    else if (el.dataset.tab) {
      S.tab = el.dataset.tab;
      renderDetail();
      $('#tab-' + S.tab).focus();
    } else if (name) {
      el.disabled = true;
      await action(name, el);
    }
  } catch (err) {
    toast(err.message || 'This action could not finish.', true);
  } finally {
    if (el.isConnected && !(name === 'send-application' && review?.sent)) el.disabled = false;
  }
});
document.addEventListener('keydown', e => {
  if (e.target.id === 'chat-input' && e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
    e.preventDefault();
    sendChat().catch(err => toast(err.message, true));
    return;
  }
  if ($('#modal').open && e.key === 'Escape') {
    e.preventDefault();
    closeModal();
    return;
  }
  if ($('#modal').open && e.key === 'Tab') {
    const controls = $$('button,input,textarea,select,a[href],[tabindex]', $('#modal')).filter(x => !x.disabled && x.tabIndex >= 0 && x.getClientRects().length);
    const first = controls[0],
      last = controls.at(-1);
    if (e.shiftKey && (document.activeElement === first || !$('#modal').contains(document.activeElement))) {
      e.preventDefault();
      last?.focus();
    } else if (!e.shiftKey && (document.activeElement === last || !$('#modal').contains(document.activeElement))) {
      e.preventDefault();
      first?.focus();
    }
    return;
  }
  if (!e.ctrlKey && !e.metaKey && !e.altKey && isDiscovery() && !e.target.closest('input,textarea,select,[contenteditable],[role=tab]')) {
    // Physical codes keep J, K and / working on Cyrillic and other layouts.
    const key = {KeyJ: 'j', KeyK: 'k', Slash: '/'}[e.code] || e.key;
    if (key === '/') {
      e.preventDefault();
      $('#job-search')?.focus();
      return;
    }
    // Arrows still scroll the detail pane; they move the selection only in the list.
    const inList = Boolean(e.target.closest('#job-results'));
    const step = {j: 1, k: -1, ...(inList ? {ArrowDown: 1, ArrowUp: -1} : {})}[key];
    if (step && S.jobs.length) {
      e.preventDefault();
      moveSelection(step).catch(err => toast(err.message, true));
      return;
    }
  }
  if (e.ctrlKey || e.metaKey) {
    const logicalKey = e.key.toLowerCase();
    // Non-Latin layouts change key, while code keeps the physical shortcut key.
    // Prefer a recognized logical shortcut for layouts such as Dvorak.
    const physicalKey = {KeyK: 'k', KeyS: 's', KeyF: 'f', KeyL: 'l'}[e.code];
    const key = ['k', 's', 'f', 'l'].includes(logicalKey) ? logicalKey : physicalKey;
    if (key === 'k') {
      e.preventDefault();
      commands();
    } else if (key === 's') {
      e.preventDefault();
      const actionName = S.view === 'profile' ? 'save-profile' : S.view === 'settings' ? 'save-settings' : S.tab === 'letter' ? 'save-letter' : S.tab === 'answers' ? 'save-answers' : 'save-notes';
      action(actionName).catch(err => toast(err.message, true));
    } else if (key === 'f' && $('#job-search')) {
      e.preventDefault();
      $('#job-search').focus();
    } else if (key === 'l' && e.shiftKey) {
      e.preventDefault();
      toggleTheme();
    }
  }
  if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key) && e.target.matches('[role=tab]')) {
    e.preventDefault();
    const tabs = $$('[role=tab]'),
      i = tabs.indexOf(e.target),
      next = e.key === 'Home' ? 0 : e.key === 'End' ? tabs.length - 1 : (i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length;
    tabs[next].click();
  }
});
function deviceName() {
  const agent = navigator.userAgent || '';
  return /iPhone/.test(agent) ? 'iPhone' : /iPad/.test(agent) ? 'iPad' : /Android/.test(agent) ? 'Android phone' : 'Phone';
}

function pairScreen(message = '') {
  const code = new URLSearchParams(location.search).get('pair') || '';
  $('#app').innerHTML = `<form class="boot pair-screen" id="pair-form"><span class="brand-mark">J</span><h1>Connect to Job Agent on your PC</h1><p>On your PC, open <strong>Settings & activity → Phone access</strong> and enter the pairing code shown there. Your phone and PC must be on the same Wi-Fi.</p>${message ? `<div class="notice warning" role="alert">${esc(message)}</div>` : ''}<label class="field"><span>Pairing code</span><input id="pair-code" autocomplete="one-time-code" autocapitalize="characters" spellcheck="false" placeholder="ABCD-EFGH" value="${esc(code)}" required></label><label class="field"><span>Name for this phone</span><input id="pair-name" maxlength="60" value="${esc(deviceName())}"></label><button type="submit" class="primary" id="pair-submit">Connect</button></form>`;
  $('#pair-form').addEventListener('submit', e => {
    e.preventDefault();
    submitPairing();
  });
  if (code && !message) submitPairing();
}

async function submitPairing() {
  const submit = $('#pair-submit');
  submit.disabled = true;
  try {
    const result = await api('/phone/pair', {method: 'POST', body: {code: $('#pair-code').value, name: $('#pair-name').value}});
    token = result.token;
    local.set(DEVICE_KEY, token);
    history.replaceState(null, '', '/');  // The code never stays in the saved address.
    await boot();
  } catch (err) {
    history.replaceState(null, '', '/');
    pairScreen(err.message);
  }
}

async function boot() {
  if (REMOTE && !token) {
    pairScreen();
    return;
  }
  if (REMOTE && location.search) history.replaceState(null, '', '/');
  try {
    S.boot = await api('/bootstrap');
    LINUX = S.boot.platform === 'linux';
    S.profile = clone(S.boot.profile);
    S.country = S.boot.settings.active_country || '';
    S.theme = (REMOTE && local.get(THEME_KEY)) || S.boot.appearance?.theme || 'light';
    await refreshCoverage();
    document.body.dataset.theme = S.theme;
    shell();
    await loadJobs();
    if (S.boot.update_check) setTimeout(autoCheckUpdates, 4000);
  } catch (err) {
    if (err.pair) return;  // The pairing screen is showing.
    const damaged = err.data?.damaged_file;
    $('#app').innerHTML = `<div class="boot"><span class="brand-mark">J</span><h1>Job Agent could not open</h1><p>${esc(err.message)}</p><div class="section-actions">${damaged?button('Restore last backup','restore-file','primary'):''}${button('Try again','retry-boot',damaged?'':'primary')}</div></div>`;
    $('[data-action=retry-boot]').addEventListener('click', boot);
    $('[data-action=restore-file]')?.addEventListener('click', async e => {
      e.target.disabled = true;
      try {
        const {message} = await api('/recover', {method: 'POST', body: {file: damaged}});
        await boot();
        toast(message);
      } catch (failure) {
        e.target.disabled = false;
        toast(failure.message, true);
      }
    });
  }
}
boot();
