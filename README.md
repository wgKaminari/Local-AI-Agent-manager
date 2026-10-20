# Personal Job Agent

A private **Python desktop application** for discovering vacancies in Norway, the USA, Germany and Ukraine, organizing opportunities, and preparing application answers and cover letters. It opens in its own desktop window with a locally packaged interface; your profile, database and drafts stay on your computer.

## Install (Windows)

Open **PowerShell** and run:

```powershell
irm https://raw.githubusercontent.com/wgKaminari/Local-AI-Agent-manager/main/install.ps1 | iex
```

The installer downloads the latest release, creates a private Python environment in `%LOCALAPPDATA%\PersonalJobAgent`, and adds **Personal Job Agent** to the Start menu and desktop. It needs Python 3.12 or newer and offers to install it (and the free local AI, Ollama) with winget if they are missing. Your data in `%USERPROFILE%\.norway-job-agent` is never changed by installing or updating, so an existing profile and database are picked up automatically. Prefer to read the script first? Download [install.ps1](install.ps1), review it, and run `powershell -ExecutionPolicy Bypass -File install.ps1`.

### Update

The app checks GitHub for a newer release once per start and shows a notice. **Settings & activity → Updates → Update now** closes the app, installs the new version and reopens it; if anything fails, the version you had opens again. Running the install command again also updates. A developer checkout (a `git clone`) updates itself with `git pull` instead, and only when it has no local changes. Releases are published from version tags by GitHub Actions, after the tests and a test install pass; see [Releases](https://github.com/wgKaminari/Local-AI-Agent-manager/releases).

## Run from source

Use Python **3.12 or newer**. Install the dependencies once, then start the application:

```powershell
python -m pip install -r requirements.txt
python run.py gui
```

To open it like any other Windows program, add shortcuts once:

```powershell
python run.py shortcuts
```

This creates **Personal Job Agent** in the Start menu and on the desktop. They open the app without a console window, with its own icon and taskbar entry, so you can pin it from the Start menu. Starting it again brings the open window to the front instead of opening a second copy. Run the command again after moving the project folder; `python run.py shortcuts --remove` removes them. The same option is under **Settings & activity → Desktop app**. You can also double-click **Start-Personal-Job-Agent.cmd**; the older **Start-Norway-Job-Agent.cmd** launcher remains compatible. Windows needs the Microsoft Edge WebView2 Runtime for the desktop window.

The interface uses bundled HTML, CSS and JavaScript inside pywebview; Python runs the application logic. The workspace opens as a desktop application, with no hosted website to configure. Opening an employer link or completing Google sign-in uses your normal browser. The earlier Tkinter interface remains available with:

```powershell
python run.py gui --classic
```

Your profile, CV text, database, notes and drafts remain in `%USERPROFILE%\.norway-job-agent`, outside this OneDrive project. The personalized bootstrap profile under `private/` is excluded from Git. See [desktop setup and migration](docs/modern-desktop.md) for runtime requirements, data continuity and troubleshooting.

## Daily workflow

1. Open **Profile & writing**. Review your facts, years of professional experience (for example, 2.5), actual language proficiency, search preferences, CV, writing style and country-specific work authorization. Save before generating drafts.
2. Choose **Find opportunities**. One refresh collects every country in your profile's target countries (Norway, the USA, Germany and Ukraine by default); each job site searches with that country's role preferences. **Searching in** only filters what you see, and in a country view, jobs located there come before remote jobs open to it. A country's supported employer presets are added and saved on first selection; later switches respect removed sources.
   Matching reads the whole advert, not just the title: a job counts as related when its title names your field ("Software Engineer, Applied AI") or the advert centres on it, in English, Norwegian (Bokmål and Nynorsk), German or Ukrainian. Product, sales, legal and management roles are not recommended unless they are one of your target titles. The score is role fit 50%, skills 30% (full credit at 8 of your skills) and experience 20%. A posting asking for up to one year more than you have is marked **Stretch** and stays in For you.
3. Browse **Discover**, **For you**, **Saved** and **Applications**. Search, stage and experience filters narrow the list. For you excludes clear experience conflicts by default, balances results across employers, and keeps unknown requirements marked for review. All experience levels remain available, and Saved/Applications keep your follow-up records. Keyword overlap is supporting evidence, not eligibility or hiring probability.
4. Open **Notes & stage** to record next steps. You control the stage: generating or sending material never automatically marks a vacancy as applied.
5. Open **Cover letter**, choose **Draft with local AI**, check every claim, then edit and save a version or export it.
6. Open **Application**, read public questions or paste them, draft answers, review the choices and attach files explicitly. Use **Review & send** for supported forms, or transfer the prepared material to the employer's site.

Editing buffers stay with you between pages and vacancies. Save versions before closing; the native window warns about unsaved work or tasks still running. Collection and AI generation run in the background, and the status area describes actual activity. Failed operations keep previously saved records available.

Use **J** and **K** to move through opportunities, **/** or **Ctrl F** to search, **Ctrl K** for Quick actions, **Ctrl S** to save the current editor, and **Ctrl Shift L** to switch light/dark appearance. The interface uses the bundled Geist typeface (SIL Open Font License, in `web/fonts`). The Help control lists these shortcuts. The redesign includes both themes, semantic form controls, keyboard navigation and responsive reading/editor layouts. Read the [UI/UX research](docs/ui-ux-research.md) for design references and validation targets.

## Features and coverage

- **366 researched employer entries**: Norway 77, USA 154, Germany 116 and Ukraine 77. Some employers cover multiple countries.
- **109 available automatic employer collectors** (80 USA, 48 Germany, 18 Norway, 21 Ukraine), plus **46 job-source directory entries**. Other career and job-board links support research, alerts and manual import; a directory listing does not enable scraping.
- **Whole-market job sites**, searched for your saved searches and target roles on every refresh: NAV (Norway, complete local index), Bundesagentur für Arbeit and Arbeitnow (Germany), Djinni and DOU (Ukraine), Remotive, Remote OK and Himalayas (remote jobs), **US tech employers** (the career boards of about 330 US companies, from OpenAI, Anthropic and NVIDIA to Stripe and Capital One, each read at most once a day), **Hacker News: Who is hiring?** (the latest two monthly threads, mostly US and remote startups), **Adzuna** (adverts from across US job sites, your roles first and then its whole IT category; paste your own free key from developer.adzuna.com under **Settings & activity → Job sites**; about 72 requests a day, so the free monthly allowance lasts the month), and an opt-in LinkedIn public search. Djinni and DOU are read whole once a day through their job categories (their feeds show only the newest 100 or 25 jobs per search; DOU's pass continues over two refreshes). Switch each site on or off under **Settings & activity → Job sites**.
- Employer boards: Greenhouse, Lever and Ashby, bounded Workday and public career sitemap scans, and individual public pages with `JobPosting` structured data.
- **Source library** with country filters, coverage notes and separate labels for automatic collectors and career links. **Add visible collectors** applies to the filtered list. **Add all country collectors** adds the current catalog to an existing country configuration. The coverage panel separates available collectors, configured sources and successful/failed checks.
- **Job alerts** with read-only Gmail access, editable review candidates, `.eml` import and email provenance. Candidates enter the database only after you select and import them.
- SQLite duplicate detection, private notes, explicit stages, saved cover-letter versions, application-answer versions and delivery history.
- CV text import from TXT, Markdown, DOCX and text PDFs. Local AI suggestions require review before becoming profile facts. Scanned PDFs need OCR or pasted text.
- Configurable tone, language, instructions, examples, phrases to avoid and answer length. Writing examples guide wording; they do not supply biographical facts.

Read the [discovery rework and verified coverage](docs/discovery-rework.md), including examples of early-career roles and remaining limitations. Earlier source evidence and access limits are in [Norway and Ukraine research](docs/research-no-ua.md) and [USA and Germany research](docs/research-us-de.md). These are curated starting points, not measured popularity rankings.

### Searching the whole market

**IT jobs only** (on by default, under **Settings & activity → Job sites**) keeps IT vacancies and nothing else, so each country's count means IT jobs: developers, data and AI, cloud and DevOps, security, QA, IT support and administration, IT consulting, UX and IT product roles. Official categories decide where a source has them (NAV's IT category and STYRK codes, the Bundesagentur's four IT occupation fields, Djinni's and DOU's categories, Adzuna's IT category); otherwise the title decides, and a vague title ("Systems Engineer") counts when its advert names at least three IT technologies. Vacancies collected earlier that are not IT jobs are removed, except ones you saved, noted or applied to. One job counts once: the same advert on several sites, posted again, or posted for several cities of one country shows as one vacancy with its other postings listed ("+3 more postings"); a posting you acted on always stays visible.

Employer boards only show the companies you configured, which is why a web search used to find far more vacancies than the app. Job sites list every employer. With **Collect everything in my fields** (on by default, under **Settings & activity → Job sites**), each refresh searches the enabled sites for your **saved searches**, target roles and related roles, and also browses whole fields: IT, software, data, engineering, mathematics and science at Bundesagentur für Arbeit, all IT categories at Djinni, DOU and Remotive, the newest Arbeitnow pages, and NAV's entire Norwegian market. **Everything** a site returns in one of your countries is kept, tagged with the searches it answers; matching happens in your database, so a later search or profile change can use any collected advert. Collection is incremental: Bundesagentur für Arbeit reads up to 10,000 adverts per search once, then only adverts published since the last refresh; Arbeitnow stops at adverts it already has; each site has a time budget, and an unfinished site continues on the next refresh. The first refresh per country can take 10–20 minutes. LinkedIn is never browsed broadly (it refuses more than a few dozen requests) and stays limited to your searches.

Type a search on **Discover** and choose **Search job sites** to search every enabled site for it at once, like a web search; **Save search** adds it to every refresh. Search uses a full-text index: each word must appear, in any order; words of four or more letters also match longer words (Pyth finds Python) and words inside titles (entwickler finds Softwareentwickler); shorter words such as AI or Go must be whole words; "quoted phrases" stay together; other spellings of a role (ML Engineer, Machine Learning Engineer) count as the same search. The same job found on several sites shows once, preferring the employer's own posting, with links to the other copies. Some sites list adverts without their text (Bundesagentur für Arbeit, LinkedIn) or without the employer (Djinni); the full advert loads when you open it, and during refreshes for your role matches.

**How large collections stay fast.** Each vacancy's match against your profile is computed once and stored; it is recomputed only when the vacancy's content, stage or activity changes, or when you save a changed profile (then in the background). Lists filter, count and sort these stored results in the database and load 80 vacancies at a time (**Show more**, or keep pressing **J**). **Cleanup** after every refresh removes vacancies that were withdrawn or retired by their source, job-site adverts no search has seen, and adverts whose deadline passed, each more than 30 days ago. A vacancy you saved, moved to another stage, wrote notes, drafts or answers for, sent, added yourself or imported from email is never removed. The search index and stored matches live in `vacancies.db` and are rebuilt automatically; the experience-requirement cache is `analysis-cache.db`.

**NAV** now keeps a complete local index of its public feed in `market-index.db`: the first refresh reads the last 90 days (about 170 feed pages, a few minutes), later refreshes read only new updates. Any search, including one typed later, is matched against every indexed Norwegian vacancy title, and only matches are downloaded in full; multi-word searches are confirmed in the advert text. NAV excludes FINN-origin vacancies. The public token is experimental; for ongoing use, register with NAV for a private token and set `NAV_API_TOKEN`. See [NAV documentation](https://navikt.github.io/pam-stilling-feed/) and [usage terms](https://arbeidsplassen.nav.no/vilkar-api).

**LinkedIn public search** is off until you enable it and confirm its notice. It reads only the job listings LinkedIn shows visitors without an account: no login or cookies (your account is never involved), the app's honest name instead of a browser disguise, a 5-second pause between requests and at most 24 requests per refresh. If LinkedIn signals it to slow down (HTTP 429/999 or a login wall), it stops immediately and pauses for an hour instead of retrying. LinkedIn's user agreement forbids automated access, so the worst case is a temporary block of your internet address on LinkedIn. The fully permitted route is LinkedIn job-alert emails through **Job alerts**.

Collection downloads up to six sources at a time, then saves each source in its configured order, so a full run takes a fraction of the earlier sequential time. A dropped or cut-off download, or a failed address lookup, is retried twice for ordinary page reads and NAV (after 1 and 4 seconds); HTTP errors such as 429 or 503 are reported, not retried. A download ends early only if the site goes silent for 20 seconds, so large boards still finish while many sites download at once. If NAV's feed stops answering partway, the adverts already indexed still load and the next refresh continues reading. Company scans are bounded by configured limits and report failures or partial coverage under **Settings & activity**. A successful check may return zero jobs. Unlike NAV, company scans restart at their listing window and do not maintain historical pagination. NAV withdrawals and complete successful Greenhouse, Lever and Ashby board snapshots retire vanished postings from active discovery. Failed or partial scans do not retire records. Saved/application records retain notes and drafts when a posting is withdrawn. Check the original vacancy before applying.

The configuration limit is 250 sources. Country/source additions that exceed it are rejected without partially changing configuration. Country/city matching is a discovery hint, not proof of remote eligibility or work authorization; remote boards are placed in a country only when the advert names it or a region that includes it (Europe, Worldwide). FINN forbids automated collection and Indeed, Stepstone, Glassdoor and robota.ua block automated visitors; the app does not work around either. Use their saved-search alert emails through **Job alerts**, or manual import. Robots restrictions are respected, except for LinkedIn's opt-in public search described above. The app does not collect while closed.

## Local AI

AI runs through **Ollama** at `127.0.0.1:11434` with a downloaded local text model. Remote endpoints and cloud model aliases are rejected. No paid AI API or cloud model account is required.

Drafting starts an installed Ollama automatically when it is not running, shows live progress while the model writes, and only gives up if Ollama stops responding, so a slow computer is never cut off mid-draft. If drafting is slow, close memory-heavy programs (browsers with many tabs, games): with little free memory Windows swaps the model to disk. The app tells you when that happened.

1. Install and open [Ollama for Windows](https://ollama.com/download/windows).
2. Under **Settings & activity**, choose **Start Ollama**, then **Download saved model**. The default is `qwen3:4b`, downloaded separately from Ollama itself. Save a different model name before downloading it.
3. Use **Check setup** to list installed models, select one and save settings.

The optional `scripts/setup-local-ai.ps1` downloads the official installer, verifies its Authenticode publisher signature, installs Ollama and pulls the model. Collection and manual editing work while AI is unavailable. **Install PDF support** repairs PDF support in the exact Python environment running the app.

Small local models sometimes produce a draft that fails a safety check, such as a paraphrased evidence citation or a letter over your word limit. The app then retries up to three attempts in total, telling the model which check failed; no draft that fails a check is ever shown. Connection, setup and timeout problems are reported immediately. Only saved profile facts are used in matching and drafting. Employment dates or years of experience absent from the evidence must not be invented. Search-language preferences never upgrade the proficiency stated in application materials.

## Reviewed application delivery

Read or paste questions, generate local drafts, edit them and choose your files. **Review & send** prepares a summary and opens the supported employer form for inspection. Only **Send reviewed application**, after your confirmation, starts one guarded delivery attempt.

Delivery supports native public HTTPS forms with matching questions and a same-origin POST destination. Login, CAPTCHA, embedded and JavaScript-dependent applications need manual completion. Question extraction is partial: check for conditional or missing questions. Gmail stays read-only.

Nothing is sent by collection, generation, saving or previewing. Reviewed files are snapshotted, changed forms require another review, and attempted payloads are not silently retried. A successful HTTP request is **submitted but unconfirmed**, not proof the employer accepted it. Check the employer confirmation before changing the stage. See [application preparation and delivery](docs/application-preparation.md).

Install the optional free runtime for supported delivery and browser-based development tests:

```powershell
python -m pip install "playwright>=1.49,<2"
python -m playwright install chromium
```

This Chromium runtime is separate from WebView2, which displays the desktop application.

## Gmail job alerts

Open **Job alerts → Setup guide** to create a Google Desktop OAuth client and enable the Gmail API. Choose **Connect Gmail**, select its credentials JSON and complete Google sign-in yourself. Only `gmail.readonly` is requested. The refresh token stays outside the project and is encrypted for the current Windows user. The app never sends, deletes or marks mail as read. **Disconnect Gmail** forgets the local connection; Google Account settings can revoke the grant separately.

Each fetch reads at most 50 messages. **Next batch** continues; edit the query to change its default 90-day window. Review candidates, fill missing details and use **Add selected**. Repeated vacancy URLs deduplicate, and partial email excerpts do not replace full employer descriptions.

The parser reads email links and text. It does not scrape LinkedIn, load tracking images, fetch attachments or automatically visit email links. **Import .eml** works without an account connection. See [Gmail setup and limits](docs/gmail-setup.md).

## Command line and source configuration

```powershell
python run.py init
python run.py collect
python run.py list --country DE --match
python run.py show 1
python run.py status 1 saved
python run.py notes 1 "Check language requirements"
python run.py letter 1
python run.py brief 1 --output "preparation-brief.md"
python run.py import "my-vacancy.json"
python run.py import-url "https://company.example/careers/specific-job"
python run.py models
python run.py catalog --country US
python run.py catalog --country UA --boards
python run.py application 1
python run.py application 1 --questions questions.txt --draft
```

`brief` creates an offline factual preparation brief; `letter` uses local AI. Put `--data-dir "C:\your\folder"` **before** the command to choose another private folder. See `python run.py --help`.

Edit sources under **Settings & activity → Advanced source configuration**, or in the private `sources.json`. An example source array is:

```json
[
  {"type": "nav"},
  {"type": "lever", "board": "bekk", "region": "global", "locations": ["Oslo", "Trondheim"]},
  {"type": "greenhouse", "board": "cognite", "locations": ["Norway", "Oslo"]}
]
```

Without `locations`, a company board returns all locations. Lever also supports `region: "eu"`. Use IDs published by the employer, not guessed names. Connector references: [Greenhouse](https://docs.greenhouse.io/job-board.html), [Lever](https://github.com/lever/postings-api), [JobPosting](https://schema.org/JobPosting).

## Development and checks

Python 3.12+, Flask, Waitress and pywebview run the local desktop shell; `pypdf` handles PDFs. Tkinter is needed for the classic fallback and its tests. UI assets and employer catalogs ship in the Python package, with no remote font or UI CDN.

```powershell
python -m unittest discover -s tests -v
```

Tests use temporary fictional profiles/databases and mocked external services. They cover collection, countries, profile grounding, migrations, duplicate identity, batch rollback, histories, reviewed delivery and local API request boundaries. Native launcher unit tests cover lifecycle logic; they do not by themselves prove a real WebView2 window opens correctly.

Enable real Chromium tests after installing the optional browser runtime:

```powershell
$env:RUN_BROWSER_TESTS='1'
python -m unittest discover -s tests -p test_modern_ui.py -v
python -m unittest discover -s tests -p test_browser_integration.py -v
```

Interface tests exercise the bundled UI against an isolated local Python service. Delivery tests intercept requests with fictional forms; no employer receives test applications. Native-window startup, closing, rendering and file dialogs require separate checks. Consult the change report for checks actually run; research acceptance criteria are targets, not accessibility certification.

`python scripts/preview-ui.py` previews the **classic Tkinter** interface with fictional data. The default `gui` command opens the new desktop application against your selected private data folder.

Existing databases migrate through the storage layer while retaining vacancies, notes, stages, letters and identities. The app keeps an automatic daily backup of the database and settings in `backups/automatic` (the newest ten; Gmail credentials and attachment files are not included). If the profile or source settings file is ever damaged, the app offers **Restore last backup** when it opens and keeps the damaged copy. Unexpected errors are written to `logs/job-agent.log`, which records error details but not your profile, CV, vacancy or letter text. Close the app before backing up the whole private folder. The database uses SQLite's WAL mode, so copy any `vacancies.db-wal` and `vacancies.db-shm` files together with `vacancies.db`. Avoid running both interfaces against it while editing shared profile/settings files. Personal data, credentials, model downloads and generated application materials are excluded from Git. See [requirements](docs/requirements.md) for the agreed scope.
