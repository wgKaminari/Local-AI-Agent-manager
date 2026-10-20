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
  globe: 'M21 12H3 M12 3a16 16 0 0 1 0 18 16 16 0 0 1 0-18 M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0'
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
  status: '',
  experience: 'relevant',
  sort: 'match',
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
  theme: 'light'
};
const titles = {
  discover: ['Discover', 'Every collected vacancy in your search.'],
  'for-you': ['For you', 'Target and related roles that fit your experience.'],
  saved: ['Saved', 'Opportunities you bookmarked.'],
  applications: ['Applications', 'Everything you are preparing or have sent.'],
  profile: ['Profile & writing', 'The facts and voice your local AI writes from. Used only after you save.'],
  sources: ['Source library', 'Employers and job boards you can collect from.'],
  mail: ['Job alerts', 'Turn job-alert emails into a reviewed shortlist.'],
  settings: ['Settings & activity', 'Local AI, configured sources and collection results.']
};
const token = $('meta[name=session-token]').content;
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

function toast(message, error = false) {
  clearTimeout(toastTimer);
  $('#notifications').innerHTML = `<div class="toast ${error?'error':''}"><span>${esc(message)}</span>${button('×','dismiss-toast','quiet','aria-label="Dismiss notification"')}</div>`;
  toastTimer = setTimeout(() => $('#notifications').replaceChildren(), error ? 16000 : 6000);
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

function syncNativeCloseState() {
  const bridge = window.pywebview?.api;
  if (!bridge?.update_close_state) return;
  // Cache state before closing: asking JavaScript from the native close callback
  // blocks WebView2's UI thread. Revisions also reject out-of-order bridge calls.
  Promise.resolve(bridge.update_close_state(token, dirty(), ++closeStateRevision)).catch(() => {});
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
async function task(action, extra = {}, done) {
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
    const result = await pollTask(task_id, key);
    await done?.(result);
    return result;
  } finally {
    S.tasks.delete(key);
    updateStatus();
  }
}
async function pollTask(id, key) {
  for (;;) {
    const state = await api('/tasks/' + encodeURIComponent(id));
    if (state.status === 'done') return state.result;
    if (state.status === 'error') throw Error(state.error || state.message || 'The action could not finish.');
    S.tasks.set(key, state.message || 'Working…');
    updateStatus();
    await new Promise(r => setTimeout(r, 700));
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
    ['profile', 'Profile & writing', 'profile'],
    ['sources', 'Source library', 'sources'],
    ['mail', 'Job alerts', 'mail'],
    ['settings', 'Settings & activity', 'settings']
  ];
  $('#app').innerHTML = `<div class="shell"><aside class="sidebar"><div class="brand"><span class="brand-mark">J</span><span class="brand-copy">Job Agent</span>${button(icon('sun'),'theme','icon-only quiet','aria-label="Switch light or dark theme" title="Switch theme (Ctrl Shift L)"')}${button(icon('help'),'help','icon-only quiet','aria-label="Help and shortcuts" title="Help and shortcuts"')}</div>${button(icon('search')+'<span>Quick actions</span><kbd>Ctrl K</kbd>','commands','command-trigger','aria-label="Quick actions" title="Quick actions (Ctrl K)"')}<label class="country-control" title="Search country">${icon('globe')}<select id="country" aria-label="Search country" ${S.settingsBusy?'disabled':''}>${options(Object.entries(S.boot.countries),S.country,'All countries')}</select></label><nav aria-label="Main navigation">${nav.map(([id,label,i],n)=>`${n===4?'<div class="nav-label">You</div>':''}<button class="nav-item ${id===S.view?'active':''}" data-view="${id}" title="${label}" aria-label="${label}" ${id===S.view?'aria-current="page"':''}>${icon(i)}<span>${label}</span></button>`).join('')}</nav><div class="sidebar-bottom">${refreshCard()}</div></aside><div class="workspace"><main id="main" tabindex="-1" class="page ${isDiscovery()?'discovery':''}"></main><footer class="statusbar"><div id="task-status" class="task-indicator" role="status" aria-live="polite"></div><div class="hints" aria-hidden="true"><span><kbd>J</kbd><kbd>K</kbd>move</span><span><kbd>/</kbd>search</span><span><kbd>Ctrl K</kbd>actions</span><span><kbd>Ctrl S</kbd>save</span></div><span class="right">Local AI · you review every application</span></footer></div></div>`;
  renderPage();
  updateStatus();
}

function isDiscovery(view = S.view) {
  return ['discover', 'for-you', 'saved', 'applications'].includes(view);
}

function ago(value) {
  const time = Date.parse(value || '');
  if (!time) return '';
  const minutes = Math.max(0, (Date.now() - time) / 60000);
  if (minutes < 1) return 'now';
  if (minutes < 60) return Math.round(minutes) + 'm';
  if (minutes < 1440) return Math.round(minutes / 60) + 'h';
  if (minutes < 10080) return Math.round(minutes / 1440) + 'd';
  if (minutes < 525600) return Math.round(minutes / 10080) + 'w';
  return Math.round(minutes / 525600) + 'y';
}

function refreshCard() {
  const r = S.boot.collection;
  const failed = r?.sources?.filter(s => !s.ok).length || 0;
  const when = r?.completed_at || r?.started_at;
  const status = when ? `Refreshed ${ago(when)} ago` : 'Not refreshed yet';
  const detail = r ? `${r.sources?.length||0} sources checked${failed?` · ${failed} need attention`:''}` : 'Choose a country, then find opportunities.';
  return `<div class="refresh-card"><strong title="${esc(status)}"><span class="dot ${failed||!when?'amber':''}"></span><span>${esc(status)}</span></strong><small>${esc(detail)}</small>${button(icon('spark')+'Find opportunities','collect','','title="Find opportunities"')}</div>`;
}

function renderPage() {
  const main = $('#main');
  main.innerHTML = isDiscovery() ? '' : heading();
  if (isDiscovery()) renderDiscovery();
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

function renderDiscovery() {
  const filtered = S.query || S.status || S.sort !== 'match' || (['discover', 'for-you'].includes(S.view) && S.experience !== 'relevant');
  const select = (id, label, items, value, on) => `<select id="${id}" aria-label="${label}" class="${on?'on':''}">${items}</select>`;
  $('#main').insertAdjacentHTML('beforeend', `<div class="results-layout ${S.selected&&S.showDetail?'has-selection':''}"><section class="results-panel" aria-label="Opportunities"><div class="list-head"><div class="list-title"><h1>${titles[S.view][0]}</h1><span class="count">${listCount()}</span><div class="heading-actions">${button(icon('plus'),'add-job','icon-only quiet','aria-label="Add opportunity" title="Add an opportunity you found"')}</div></div><div class="filterbar"><div class="search">${icon('search')}<input id="job-search" type="search" aria-label="Search opportunities" placeholder="Search roles, companies, skills" value="${esc(S.query)}"><kbd>/</kbd></div>${['discover','for-you'].includes(S.view)?select('experience-filter','Experience level',options([['relevant','Fits my experience'],['all','All levels'],['mismatch','Above my experience']],S.experience),S.experience,S.experience!=='relevant'):''}${select('status-filter','Filter by stage',options(orderedStatuses,S.status,'Any stage'),S.status,Boolean(S.status))}${select('sort','Sort opportunities',options([['match','Best match'],['newest','Newest'],['company','Company A–Z']],S.sort),S.sort,S.sort!=='match')}${filtered?button('Reset','reset-filters','quiet'):''}</div></div>${coverageNotice()}<div id="job-results"></div></section><section id="job-detail" class="detail" aria-label="Opportunity details"></section></div>`);
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

function jobTags(j) {
  const e = j.match?.experience || {};
  const tags = [];
  if (j.status !== 'new') tags.push(pill(cap(j.status), stageTones[j.status] || ''));
  if (e.status === 'mismatch') tags.push(pill(e.minimum_years != null ? `${e.minimum_years}+ yrs asked` : 'Above your level', 'amber'));
  else if (j.match?.track === 'horizon' && S.view !== 'for-you') tags.push(pill('Related role'));
  return tags.join('');
}

// Display names for where a vacancy was found.
const SITE_LABELS = {nav: 'NAV', arbeitsagentur: 'Arbeitsagentur', arbeitnow: 'Arbeitnow', djinni: 'Djinni', dou: 'DOU',
  remotive: 'Remotive', remoteok: 'Remote OK', himalayas: 'Himalayas', linkedin: 'LinkedIn', gmail: 'job-alert email'};
const siteLabel = source => SITE_LABELS[source] || 'employer site';

function marketBar() {
  const q = S.query.trim();
  if (!['discover', 'for-you'].includes(S.view) || q.length < 2) return '';
  const saved = (S.boot.settings.market?.searches || []).some(x => x.toLowerCase() === q.toLowerCase());
  const busy = S.tasks.has('market-search:');
  const text = busy ? `Searching job sites for “${esc(q)}”… results appear here as they are saved.` :
    `These are adverts already collected. Search the whole market for “${esc(q)}” too.`;
  return `<div class="market-bar">${icon('search')}<span class="market-text">${text}</span><span class="market-actions">${button(busy?'Searching…':'Search job sites','market-search','primary',busy?'disabled':'title="Search every enabled job site for this now"')}${saved?pill('Saved search','green'):button('Save search','save-search','quiet','title="Include this search in every refresh"')}</span></div>`;
}

function renderCards() {
  const root = $('#job-results');
  if (!root) return;
  root.innerHTML = marketBar() + (S.jobs.length ? S.jobs.map(j => {
    const score = j.match?.score,
      tier = score == null ? '' : score >= 70 ? 'high' : score >= 45 ? 'mid' : '';
    const selected = S.selected === j.id;
    return `<button type="button" class="job-card ${selected?'selected':''}" data-job="${j.id}" aria-pressed="${selected}"><span class="score ${tier}" title="Keyword overlap with your profile">${score??'–'}</span><span class="title-line"><span class="sr-only">${score==null?'No match score. ':score+'% keyword match. '}</span>${esc(j.title)}</span><span class="age" title="${esc(j.published_at||j.discovered_at||'')}">${ago(j.published_at||j.discovered_at)}</span><span class="job-sub">${esc(j.company||'Employer not listed')} · ${esc(j.location||'Location to confirm')}${j.also_on?.length?`<span class="also-on" title="${esc('Also on '+[...new Set(j.also_on.map(o=>siteLabel(o.source)))].join(', '))}"> · +${j.also_on.length} site${j.also_on.length>1?'s':''}</span>`:''}</span><span class="job-tags">${jobTags(j)}</span></button>`;
  }).join('') + (S.summary?.has_more ? `<div class="empty">${button(`Show ${Math.min(PAGE_SIZE,(S.summary.shown||0)-S.jobs.length)} more of ${(S.summary.shown||0).toLocaleString()}`, 'more-jobs', 'quiet')}</div>` : '') : `<div class="empty"><div class="empty-icon">${icon('discover')}</div><h2>${S.query||S.status?'Nothing matches these filters':'No opportunities yet'}</h2><p>${S.query?'Nothing collected matches yet. Search job sites above, try other words, or reset the filters.':S.status?'Try another stage or reset the filters. Your saved work is unaffected.':'Choose a country and find opportunities, or add a vacancy you found yourself.'}</p>${button(S.query||S.status?'Reset filters':'Find opportunities',S.query||S.status?'reset-filters':'collect',S.query?'':'primary')}</div>`);
}
// The server pages lists; the database may hold tens of thousands of vacancies.
const PAGE_SIZE = 80;

function listParams(offset) {
  return new URLSearchParams({
    country: S.country,
    query: S.query,
    status: S.status,
    sort: S.sort,
    experience: ['discover', 'for-you'].includes(S.view) ? S.experience : 'all',
    view: S.view,
    offset,
    limit: PAGE_SIZE
  });
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
  const {
    jobs,
    summary
  } = await api('/jobs?' + listParams(0));
  if (version !== S.generation) return;
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
  root.innerHTML = `<div class="detail-head">${button(icon('back')+' All opportunities','back-list','quiet back-list button-icon')}<div class="detail-company"><span class="company-mark">${esc((j.company||'Co').slice(0,2).toUpperCase())}</span>${esc(j.company||'Employer')}</div><h2>${esc(j.title)}</h2><div class="detail-meta"><span>${esc(j.location||'Location to confirm')}</span>${j.employment_type?`<span>${esc(j.employment_type)}</span>`:''}${j.deadline?`<span>Closes ${esc(j.deadline.slice(0,10))}</span>`:''}</div><div class="detail-actions">${S.tab!=='letter'&&j.is_active?button(icon('spark')+' Write cover letter','','primary button-icon','data-tab="letter"'):''}${button(icon('saved')+' '+(b.status==='saved'?'Saved':['new','saved'].includes(b.status)?'Save':cap(b.status)),'bookmark','button-icon',['new','saved'].includes(b.status)?'':'disabled title="Change stage in Notes & stage"')}${link(BOARD_SOURCES.includes(j.source)?'On '+siteLabel(j.source):'Original posting',j.source_url)}${rawOf(j).external_apply_url?link('Apply on employer site',rawOf(j).external_apply_url):BOARD_SOURCES.includes(j.source)?'':link('Apply on site',j.apply_url||j.source_url)}${(S.jobs.find(x=>x.id===j.id)?.also_on||[]).slice(0,4).map(o=>link('On '+siteLabel(o.source),o.url)).join('')}</div><div class="tabs" role="tablist" aria-label="Opportunity workspace">${[['overview','Overview'],['notes','Notes & stage'],['letter','Cover letter'],['answers','Application']].map(([id,label])=>`<button role="tab" aria-label="${label}" id="tab-${id}" aria-selected="${S.tab===id}" aria-controls="detail-content" class="tab ${S.tab===id?'active':''}" data-tab="${id}">${label}${b.dirty.has(id==='answers'?'app':id)?' •':''}</button>`).join('')}</div></div><div id="detail-content" class="detail-body" role="tabpanel" aria-labelledby="tab-${S.tab}"></div>`;
  const body = $('#detail-content');
  if (!j.is_active || j.match?.expired) body.innerHTML = '<div class="notice warning">This posting is withdrawn or past its deadline. Your notes and drafts are still available.</div>';
  if (!S.jobs.some(item => item.id === j.id)) body.insertAdjacentHTML('beforeend', '<div class="notice">This opportunity is outside the current filters. Your editor stays open so you can finish your work.</div>');
  if (S.tab === 'overview') body.insertAdjacentHTML('beforeend', overview(j));
  else if (S.tab === 'notes') body.insertAdjacentHTML('beforeend', notes(b));
  else if (S.tab === 'letter') body.insertAdjacentHTML('beforeend', letter(b));
  else body.insertAdjacentHTML('beforeend', answers(b, data));
  if (needsDetails(j) && !S.loadingDetails.has(j.id) && !S.detailFailures.has(j.id)) loadDetails(j.id);
}

// Job sites whose lists omit the advert text (Djinni: the employer).
const BOARD_SOURCES = ['arbeitsagentur', 'arbeitnow', 'djinni', 'dou', 'remotive', 'remoteok', 'himalayas', 'linkedin'];
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
  const experience = e.status === 'mismatch' ? ['bad', 'Experience mismatch', `${e.minimum_years!=null?`Asks for ${e.minimum_years}+ years`:`${cap(e.seniority)} title`} · you have ${years}`] :
    years == null ? ['warn', 'Not screened', 'Add your years of experience in Profile & writing'] :
    e.status === 'no_conflict' ? ['good', `${e.minimum_years}+ years asked`, `You have ${years}`] :
    ['warn', 'Needs review', e.minimum_years != null ? `Asks for ${e.minimum_years}+ years · you have ${years}` : 'No clear minimum stated'];
  const skill = skills.length ? [matched.length * 2 >= skills.length ? 'good' : 'warn', `${matched.length} of ${skills.length} skills`, matched.join(', ') || 'None of your skills are mentioned'] :
    ['', 'Skills not set', 'Add skills in Profile & writing'];
  return `<div class="fit-grid">${tile('Role', role)}${tile('Experience', experience)}${tile('Skills', skill)}</div>`;
}

function overview(j) {
  const m = j.match || {},
    e = m.experience;
  const experience = e ? `<details class="evidence"><summary>How experience was screened</summary>${e.reasons.map(reason=>`<p>${esc(reason)}</p>`).join('')}${(e.requirements||[]).map(r=>`<p>${esc(cap(r.kind))}: ${esc(r.evidence)}</p>`).join('')}</details>` : '';
  const failure = S.detailFailures.get(j.id);
  const missing = needsDetails(j) ? (failure ? `${siteLabel(j.source)} could not show the full advert: ${failure} Open it on the site instead.` :
    `Loading the full advert from ${siteLabel(j.source)}…`) : 'No full description was imported. Read the original posting for details.';
  return `${fitTiles(j)}<div class="prose">${esc(String(j.description||'').trim()?j.description:missing)}</div>${experience}<details class="evidence"><summary>Things to check before applying</summary>${(m.warnings||[]).map(w=>`<p>${esc(w)}</p>`).join('')}<p>${m.score==null?'No keyword score.':`${m.score}% keyword overlap.`} This measures text overlap, not eligibility or hiring probability.</p></details>`;
}

function notes(b) {
  return `<label class="field"><span>Application stage</span><select id="job-stage">${options(orderedStatuses,b.status)}</select><small>You control the stage. Generating or sending a draft does not change it.</small></label><label class="field"><span>Your private notes</span><textarea id="job-notes" class="editor" placeholder="What stands out? What would you like to ask?">${esc(b.notes)}</textarea></label><div class="savebar"><small>Edits stay with this opportunity as you move around the app.</small>${button('Save notes & stage','save-notes','primary')}</div>`;
}

function letter(b) {
  return `<div class="answers-toolbar">${button(icon('spark')+' Draft with local AI','generate-letter','primary button-icon')}${button('Version history','letter-history')}${button('Export text','export-letter')}</div><label class="field"><span>Your cover letter</span><textarea id="letter-text" class="editor" placeholder="Write in your own words, or start a draft with your local AI model.">${esc(b.letter)}</textarea></label><div class="editor-meta"><span id="word-count">${b.letter.trim().split(/\s+/).filter(Boolean).length} words</span><span>Style: ${esc(S.profile.writing_style?.tone||'Professional and natural')}</span></div>${b.letterNotes?.length?`<div class="notice warning"><strong>Check before using this draft</strong>${b.letterNotes.map(n=>`<p>${esc(n)}</p>`).join('')}${b.letterEvidence?.length?`<details class="evidence"><summary>Profile facts the draft relies on</summary>${b.letterEvidence.map(e=>`<p>${esc(e)}</p>`).join('')}</details>`:''}</div>`:'<div class="notice">Review the facts and the employer’s requirements before using this letter. Your writing preferences are in Profile & writing.</div>'}<div class="savebar"><small>${b.dirty.has('letter')?'Unsaved edits':'Saved versions remain in history.'}</small>${button('Save version','save-letter','primary')}</div>`;
}

function answers(b, data) {
  const p = b.app;
  return `<div class="answers-toolbar">${button('Read questions','read-questions')}${button('Paste questions','paste-questions')}${p?button(icon('spark')+' Draft answers','generate-answers','primary button-icon'):''}</div>${!p?`<div class="empty"><div class="empty-icon">${icon('applications')}</div><h2>A thoughtful application starts here.</h2><p>Read the employer’s public questions or paste them in. Your local AI prepares drafts for you to review and edit.</p></div>`:`<div class="notice">${p.form.fields.length} questions · ${esc(p.form.coverage||'partial')} coverage. Check the original form for missing or conditional questions.<details class="evidence"><summary>Source details and limitations</summary>${(p.form.warnings||[]).map(w=>`<p>${esc(w)}</p>`).join('')}<p>${esc(p.form.source_url||'Manually supplied questions')}</p></details></div><div class="ready-line">${icon('check')} ${p.form.fields.filter(f=>hasAnswer(p,f)).length} of ${p.form.fields.length} questions completed</div><br>${p.form.fields.map((f,i)=>question(p,f,i)).join('')}<div class="savebar"><div class="section-actions">${button('Save version','save-answers')}${button('History','answers-history')}${button('Export','export-answers')}</div>${sendable(p)?button('Review & send','review-application','primary'):button('Copy answers & open application','copy-apply','primary')}</div><p class="evidence">${sendable(p)?'This is a standard public form: Review & send fills it in a browser for your final check before one explicit send.':'This employer’s form needs its own website (it uses JavaScript, a login or a hosted applicant system). Copy your reviewed answers, paste them into the form, and attach your files there.'}</p>`}${data.deliveries.length?`<div class="notice">Last delivery: ${esc(cap(data.deliveries[0].status))}. Check the employer’s confirmation before updating your stage.</div>`:''}`;
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
  <section class="panel"><div class="panel-title"><div><h2>Your story</h2><p>Facts your local AI can use when preparing an application.</p></div>${pill('Shared profile','blue')}</div>
  <div class="form-grid">${field('Full name','name',p.name)}${field('Cover-letter language','cover_letter_language',p.cover_letter_language)}${field('Years of professional experience','years_of_experience',p.years_of_experience??'',{type:'number',hint:'Use a decimal such as 2.5. Blank means unknown. Used to screen seniority and required experience.'})}
  <div class="span-2">${field('Professional summary','summary',p.summary,{lines:3})}</div>${field('Skills','skills',listText(p.skills),{lines:4,hint:'One skill per line.'})}${field('Projects & experience evidence','evidence',listText(p.evidence),{lines:4,hint:'One factual achievement per line.'})}
  ${field('Actual language proficiency','languages',Object.entries(p.languages||{}).map(([k,v])=>k+': '+v).join('\n'),{lines:3,hint:'Language: level, one per line.'})}${field('Language levels to include in searches','search_languages',Object.entries(p.search_languages||{}).map(([k,v])=>k+': '+v).join('\n'),{lines:3,hint:'Search preferences never change your stated proficiency.'})}</div></section>
  <section class="panel"><div class="panel-title"><div><h2>What you’re looking for</h2><p>These preferences stay with you when you switch countries.</p></div></div><div class="form-grid">${[['Target roles','target_roles'],['Related roles to explore','related_roles'],['Preferred locations','preferred_locations'],['Terms to exclude','excluded_keywords']].map(([label,key])=>field(label,key,listText(p[key]),{lines:3,hint:'One item per line. Leave locations empty to consider all.'})).join('')}</div></section>
  <section class="panel"><div class="panel-title"><div><h2>Your CV</h2><p>Import a text PDF, DOCX, TXT or Markdown file, or paste its content.</p></div><div class="section-actions">${button('Import CV','import-cv')}${button(icon('spark')+' Suggest profile','suggest-profile','button-icon')}</div></div>${field('CV text','cv_text',p.cv_text,{lines:10})}</section>
  <section class="panel"><div class="panel-title"><div><h2>Your writing style</h2><p>Tell the AI how you want to sound. Examples guide wording, not qualifications.</p></div></div><div class="form-grid">${field('Tone','writing_style.tone',w.tone||'professional and natural')}${field('Language override','writing_style.language',w.language||'',{hint:'Leave blank to use your cover-letter language.'})}${field('Maximum words','writing_style.max_words',w.max_words||180,{type:'number',hint:'20–500 words per answer or letter.'})}${field('Phrases to avoid','writing_style.avoid_phrases',listText(w.avoid_phrases),{lines:3})}<div class="span-2">${field('Style instructions','writing_style.instructions',w.instructions||'',{lines:3})}${field('Writing examples','writing_style.examples',listText(w.examples),{lines:4,hint:'Up to five examples, one paragraph per line.'})}</div></div></section>
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

function jobSitesPanel(settings) {
  const m = settings.market || {enabled: {}, searches: []};
  const on = Object.values(m.enabled || {}).filter(Boolean).length;
  const rows = (S.boot.job_sites || []).map(s => `<label class="site-row"><input type="checkbox" data-site="${esc(s.id)}" ${m.enabled?.[s.id]?'checked':''}><span><strong>${esc(s.name)}</strong> <small>${esc(s.countries.map(c=>S.boot.countries[c]).join(' · '))}</small> ${s.opt_in?pill('Opt-in','amber'):''}<small class="site-note">${esc(s.note)}</small></span></label>`).join('');
  return `<section class="panel"><div class="panel-title"><div><h2>Job sites</h2><p>Whole-market search: every employer on these sites, for your saved searches and target roles. NAV covers Norway and is listed under connected sources.</p></div>${pill(on+' on',on?'green':'')}</div><div class="site-list">${rows}</div><label class="field"><span>Saved searches</span><textarea id="market-searches" rows="3" placeholder="One search per line, for example: Linux DevOps" aria-label="Saved searches">${esc((m.searches||[]).join('\n'))}</textarea><small>Every refresh searches these first, then your target roles${m.include_related_roles?' and related roles':''}. Use the search box on Discover to search all sites once.</small></label><label class="check-row"><input type="checkbox" id="market-related" ${m.include_related_roles?'checked':''}> Also search my related roles</label><label class="check-row"><input type="checkbox" id="market-everything" ${m.collect_everything!==false?'checked':''}> Collect everything in my fields</label><p class="evidence">On: each refresh also browses whole fields (IT, data, engineering, science…) and NAV's entire Norwegian market, and keeps every vacancy in your countries; matching happens in your database. The first refresh per country can take 10–20 minutes. Vacancies closed or unseen for 30 days are removed, except ones you saved, noted or applied to. Changes apply after Save settings, below.</p></section>`;
}

function updatesPanel() {
  const u = S.update;
  const version = S.boot.version || '';
  const status = !u ? `You have version ${esc(version)}. Updates come from the app's GitHub releases.` :
    u.newer ? `Version ${esc(u.latest)} is available; you have ${esc(u.current)}.` :
    `You have version ${esc(u.current)}. ${esc(u.reason || '')}`;
  const notes = u?.newer && u.notes ? `<details class="evidence"><summary>What's new in ${esc(u.latest)}</summary><div class="prose">${esc(u.notes)}</div></details>` : '';
  const action = u?.newer ? (u.can_update ? button('Update now', 'apply-update', 'primary') : `<p class="evidence">${esc(u.reason)}</p>`) : '';
  const install = u?.kind === 'source' ? `<p class="evidence">Install once from PowerShell, then the app updates itself: <code>${esc(u.install_command)}</code></p>` : '';
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
  $('#main').insertAdjacentHTML('beforeend', `<div class="content-width"><section class="panel"><div class="panel-title"><div><h2>Local AI</h2><p>Ollama runs your model on this computer. No cloud AI key is needed.</p></div>${pill('On-device','green')}</div><div class="form-grid">${field('Model name','model',settings.model,{group:'settings',hint:'Save a model name before downloading or generating.'})}<div><label class="field"><span>Available models</span><select id="model-picker"><option value="">Check setup to list installed models</option></select></label></div></div><div class="section-actions">${button('Check setup','check-setup')}${button('Start Ollama','start-ollama')}${button('Download saved model','download-model')}${button('Install PDF support','install-pdf')}${link('Install Ollama','https://ollama.com/download/windows')}</div><div id="setup-result" class="evidence"></div></section>
  ${jobSitesPanel(settings)}<section class="panel"><div class="panel-title"><div><h2>Connected job sources</h2><p>${S.boot.settings.sources.length} configured. The country switch limits each collection to that market.</p></div>${button('Browse library','go-sources')}</div><div class="table-wrap"><table><thead><tr><th>Source</th><th>Countries</th><th>Action</th></tr></thead><tbody>${settings.sources.map((s,i)=>`<tr><td>${esc(s.name||s.board||cap(s.type))}<span class="sub">${esc(s.type)}</span></td><td>${esc(s.countries?.join(', ')||(s.type==='nav'?'NO':'Any'))}</td><td>${button('Remove','remove-source','quiet',`data-source-index="${i}"`)}</td></tr>`).join('')}</tbody></table></div><details class="evidence"><summary>Advanced source configuration</summary><p>Edit the source array for custom boards, locations or limits. Invalid changes are rejected when you save.</p><textarea id="sources-json" rows="10" aria-label="Advanced sources JSON">${esc(S.sourcesText??JSON.stringify(settings.sources,null,2))}</textarea></details><div class="savebar"><small>${S.settingsDirty?'Unsaved settings':'Changes apply after saving.'}</small>${button('Save settings','save-settings','primary')}</div></section>
  <section class="panel"><div class="panel-title"><div><h2>Collection activity</h2><p>See what was collected and which sources need attention.</p></div>${button('Find opportunities','collect')}</div>${collectionReport()}</section><section class="panel"><div class="panel-title"><div><h2>Desktop app</h2><p>Open Job Agent from the Start menu or desktop like any other program: its own icon, no console window, and only one window at a time.</p></div></div><p class="evidence">The app runs entirely on this computer. Your profile, database, notes and drafts stay in your private data folder.</p><div class="section-actions">${button('Add Start menu & desktop shortcuts','create-shortcuts','primary')}${button('Switch theme','theme')}${button('Keyboard shortcuts','help')}</div></section>${updatesPanel()}</div>`);
}

function collectionReport() {
  const r = S.boot.collection;
  if (!r) return '<div class="notice">No collection yet. Add sources, choose a country and find opportunities.</div>';
  return `<p class="evidence">${esc(r.country?S.boot.countries[r.country]:'Country not recorded / all countries')}${r.started_at?' · '+esc(new Date(r.started_at).toLocaleString()):''}. Counts describe collection, before your role and experience filters.</p><div class="tags">${pill(`${r.created||0} new`,'green')}${pill(`${r.updated||0} updated`)}${pill(`${r.withdrawn||0} withdrawn`)}</div>${(r.sources||[]).map(s=>`<div class="activity-item"><strong>${esc(s.source?.name||s.source?.board||cap(s.source?.type)||'Source')}</strong> ${pill(s.ok?(s.has_more?'Scan limit reached':'Checked'):'Needs attention',s.ok&&!s.has_more?'green':'amber')}<p>${esc(s.error||s.coverage||`${s.created||0} new, ${s.updated||0} updated.`)}</p>${s.fetched!=null?`<small>${s.fetched} read · ${s.matched??((s.created||0)+(s.updated||0))} in geographic scope · ${s.unknown_country||0} with unknown country · ${s.created||0} new · ${s.updated||0} refreshed</small>`:''}</div>`).join('')}`;
}

function renderMail() {
  const connected = S.boot.gmail?.connected;
  $('#main').insertAdjacentHTML('beforeend', `<div class="content-width"><section class="panel"><div class="panel-title"><div><h2>Your job-alert inbox</h2><p>${esc(S.boot.gmail?.message||S.boot.gmail?.detail||'Connect Gmail with read-only access or import saved email files.')}</p></div>${pill(connected?'Connected · read-only':'Not connected',connected?'green':'')}</div><div class="section-actions">${button(connected?'Disconnect Gmail':'Connect Gmail',connected?'disconnect-gmail':'connect-gmail')}${button('Cancel sign-in','cancel-gmail')}${button('Import .eml','import-eml')}${button('Setup guide','gmail-help')}</div><br><label class="field"><span>Gmail search</span><input id="email-query" value="${esc(S.emailQuery)}"></label><div class="section-actions">${button('Find job alerts','fetch-mail','primary')}${button('Next batch','next-mail','',S.emailNext?'':'disabled')}</div></section><section class="panel"><div class="panel-title"><div><h2>Review opportunities</h2><p>Select only the vacancies you want to add. Emails stay unchanged.</p></div>${button('Add selected','import-candidates','primary')}</div><div id="email-results"></div></section></div>`);
  renderEmailResults();
}

function renderEmailResults() {
  const root = $('#email-results');
  if (!root) return;
  root.innerHTML = S.emails.length ? `<label class="checkline"><input type="checkbox" id="select-all-emails"> Select all ${S.emails.length} opportunities</label><div class="table-wrap"><table><thead><tr><th>Select</th><th>Opportunity</th><th>Action</th></tr></thead><tbody>${S.emails.map((e,i)=>`<tr><td><input type="checkbox" data-email-index="${i}" ${e._selected?'checked':''} aria-label="Select ${esc(e.title)}"></td><td>${esc(e.title)}<span class="sub">${esc(e.company||'Employer to confirm')} · ${esc(e.location||'Location to confirm')}</span></td><td>${button('Review','edit-email','quiet',`data-email-index="${i}"`)}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty"><div class="empty-icon">' + icon('mail') + '</div><h2>Your shortlist starts with a review.</h2><p>Find job alerts or import an .eml message. Nothing is added until you choose it.</p></div>';
}

function mergeEmails(result) {
  const old = new Map(S.emails.map(e => [e.source_url, e]));
  for (const e of result.candidates || [])
    if (!old.has(e.source_url)) old.set(e.source_url, e);
  S.emails = [...old.values()];
  S.emailNext = result.next_page_token || '';
  if (S.view === 'mail') renderPage();
  toast(`${result.candidates?.length||0} opportunities found for review.${result.warnings?.length?' Some messages need manual review.':''}`);
}

function profileValue(el) {
  const key = el.dataset.profile;
  let value = el.value;
  if (['skills', 'evidence', 'target_roles', 'related_roles', 'preferred_locations', 'excluded_keywords', 'writing_style.examples', 'writing_style.avoid_phrases'].includes(key)) value = value.split('\n').map(v => v.trim()).filter(Boolean);
  else if (['languages', 'search_languages'].includes(key)) {
    value = {};
    for (const line of el.value.split('\n').filter(x => x.trim())) {
      const at = line.indexOf(':');
      if (at < 1) throw Error('Write languages as Language: level, one per line.');
      value[line.slice(0, at).trim()] = line.slice(at + 1).trim();
    }
  } else if (key === 'writing_style.max_words') value = Number(value);
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
    } else if (el.id === 'market-related' || el.id === 'market-everything') {
      S.settingsDraft ??= clone(S.boot.settings);
      S.settingsDraft.market[el.id === 'market-related' ? 'include_related_roles' : 'collect_everything'] = el.checked;
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
async function history(kind) {
  const id = S.selected,
    data = await refreshJob(id);
  const rows = kind === 'letter' ? data.letters : data.preparations;
  modal(kind === 'letter' ? 'Cover-letter versions' : 'Application versions', rows.length ? rows.map((r, i) => `<div class="history-row"><small>${esc(r.created_at)} · ${esc(r.model||'Application answers')}</small><p>${esc(kind==='letter'?r.content:r.payload.answers.map(a=>a.answer).filter(Boolean).join('\n')||'Questions without answers')}</p>${button('Restore for editing','restore-version','',`data-kind="${kind}" data-version-index="${i}" data-job-id="${id}"`)}</div>`).join('') : '<p class="muted">No saved versions yet.</p>');
}

function commands() {
  modal('Quick actions', `<input id="command-search" type="search" placeholder="Search actions…" aria-label="Search quick actions"><div class="command-list">${Object.entries(titles).map(([view,[label]])=>`<button data-view="${view}">${icon(view==='for-you'?'spark':view)}${label}</button>`).join('')}${button(icon('plus')+' Add opportunity','add-job')}${button(icon('spark')+' Find opportunities','collect')}${button(icon('sun')+' Switch theme','theme')}</div>`);
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
  if (name === 'close-modal') {
    closeModal();
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
    S.experience = 'relevant';
    await loadJobs();
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
    toast('Saved to your private workspace.');
    if (name === 'save-notes') await loadJobs();
    else renderDetail();
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
    await history(name === 'letter-history' ? 'letter' : 'app');
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
    toast('Profile saved. Updating matches for every collected vacancy in the background…');
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
async function boot() {
  try {
    S.boot = await api('/bootstrap');
    S.profile = clone(S.boot.profile);
    S.country = S.boot.settings.active_country || '';
    S.theme = S.boot.appearance?.theme || 'light';
    await refreshCoverage();
    document.body.dataset.theme = S.theme;
    shell();
    await loadJobs();
    if (S.boot.update_check) setTimeout(autoCheckUpdates, 4000);
  } catch (err) {
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
