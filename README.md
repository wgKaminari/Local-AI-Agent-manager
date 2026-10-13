# Personal Job Agent

A private **Python desktop application** for discovering vacancies in Norway, the USA, Germany and Ukraine, organizing opportunities, and preparing application answers and cover letters. Version 0.3 opens in its own desktop window with a modern, locally packaged interface. Your existing profile, SQLite database and draft histories stay in the same private folder.

## Start

Use Python **3.12 or newer**. Install the dependencies once, then start the application:

```powershell
python -m pip install -r requirements.txt
python run.py gui
```

You can also double-click **Start-Personal-Job-Agent.cmd**. The existing **Start-Norway-Job-Agent.cmd** launcher remains compatible. Windows needs the Microsoft Edge WebView2 Runtime for the desktop window.

The interface uses bundled HTML, CSS and JavaScript inside pywebview; Python runs the application logic. The workspace opens as a desktop application, with no hosted website to configure. Opening an employer link or completing Google sign-in uses your normal browser. The earlier Tkinter interface remains available with:

```powershell
python run.py gui --classic
```

Your profile, CV text, database, notes and drafts remain in `%USERPROFILE%\.norway-job-agent`, outside this OneDrive project. The personalized bootstrap profile under `private/` is excluded from Git. See [desktop setup and migration](docs/modern-desktop.md) for runtime requirements, data continuity and troubleshooting.

## Daily workflow

1. Open **Profile & writing**. Review your facts, years of professional experience (for example, 2.5), actual language proficiency, search preferences, CV, writing style and country-specific work authorization. Save before generating drafts.
2. Choose **Searching in**, then **Find opportunities**. Switching countries changes discovery and source context while keeping your shared profile. A country's supported employer presets are added and saved on first selection; later switches respect removed sources.
3. Browse **Discover**, **For you**, **Saved** and **Applications**. Search, stage and experience filters narrow the list. For you excludes clear experience conflicts by default, balances results across employers, and keeps unknown requirements marked for review. All experience levels remain available, and Saved/Applications keep your follow-up records. Keyword overlap is supporting evidence, not eligibility or hiring probability.
4. Open **Notes & stage** to record next steps. You control the stage: generating or sending material never automatically marks a vacancy as applied.
5. Open **Cover letter**, choose **Draft with local AI**, check every claim, then edit and save a version or export it.
6. Open **Application**, read public questions or paste them, draft answers, review the choices and attach files explicitly. Use **Review & send** for supported forms, or transfer the prepared material to the employer's site.

Editing buffers stay with you between pages and vacancies. Save versions before closing; the native window warns about unsaved work or tasks still running. Collection and AI generation run in the background, and the status area describes actual activity. Failed operations keep previously saved records available.

Use **Ctrl K** for Quick actions, **Ctrl F** for opportunity search, **Ctrl S** to save the current editor, and **Ctrl Shift L** to switch light/dark appearance. The Help control lists these shortcuts. The redesign includes both themes, semantic form controls, keyboard navigation and responsive reading/editor layouts. Read the [UI/UX research](docs/ui-ux-research.md) for design references and validation targets.

## Features and coverage

- **366 researched employer entries**: Norway 77, USA 154, Germany 116 and Ukraine 77. Some employers cover multiple countries.
- **109 available automatic employer collectors** (80 USA, 48 Germany, 18 Norway, 21 Ukraine), plus **46 job-source directory entries**. Other career and job-board links support research, alerts and manual import; a directory listing does not enable scraping.
- NAV/Arbeidsplassen's incremental feed, Greenhouse, Lever and Ashby company boards, bounded Workday and public career sitemap scans, and individual public pages with `JobPosting` structured data.
- **Source library** with country filters, coverage notes and separate labels for automatic collectors and career links. **Add visible collectors** applies to the filtered list. **Add all country collectors** adds the current catalog to an existing country configuration. The coverage panel separates available collectors, configured sources and successful/failed checks.
- **Job alerts** with read-only Gmail access, editable review candidates, `.eml` import and email provenance. Candidates enter the database only after you select and import them.
- SQLite duplicate detection, private notes, explicit stages, saved cover-letter versions, application-answer versions and delivery history.
- CV text import from TXT, Markdown, DOCX and text PDFs. Local AI suggestions require review before becoming profile facts. Scanned PDFs need OCR or pasted text.
- Configurable tone, language, instructions, examples, phrases to avoid and answer length. Writing examples guide wording; they do not supply biographical facts.

Read the [discovery rework and verified coverage](docs/discovery-rework.md), including examples of early-career roles and remaining limitations. Earlier source evidence and access limits are in [Norway and Ukraine research](docs/research-no-ua.md) and [USA and Germany research](docs/research-us-de.md). These are curated starting points, not measured popularity rankings.

NAV begins with the last **90 days of feed updates**, reads at most five pages per regular collection and resumes from a saved checkpoint. A backlog notice means more pages remain. Changed search terms restart that window. This is not every active vacancy, and NAV excludes FINN-origin vacancies. The public token is experimental; for ongoing use, register with NAV for a private token and set `NAV_API_TOKEN`. See [NAV documentation](https://navikt.github.io/pam-stilling-feed/) and [usage terms](https://arbeidsplassen.nav.no/vilkar-api).

Company scans are bounded by configured limits and report failures or partial coverage under **Settings & activity**. A successful check may return zero jobs. Unlike NAV, company scans restart at their listing window and do not maintain historical pagination. NAV withdrawals and complete successful Greenhouse, Lever and Ashby board snapshots retire vanished postings from active discovery. Failed or partial scans do not retire records. Saved/application records retain notes and drafts when a posting is withdrawn. Check the original vacancy before applying.

The configuration limit is 250 sources. Country/source additions that exceed it are rejected without partially changing configuration. Country/city matching is a discovery hint, not proof of remote eligibility or work authorization. FINN, Indeed, Stepstone, Work.ua and LinkedIn remain browser/email/manual routes. Login requirements and blocked or unsupported pages require manual import or a dedicated connector. Robots restrictions are respected. The app does not collect while closed.

## Local AI

AI runs through **Ollama** at `127.0.0.1:11434` with a downloaded local text model. Remote endpoints and cloud model aliases are rejected. No paid AI API or cloud model account is required.

1. Install and open [Ollama for Windows](https://ollama.com/download/windows).
2. Under **Settings & activity**, choose **Start Ollama**, then **Download saved model**. The default is `qwen3:4b`, downloaded separately from Ollama itself. Save a different model name before downloading it.
3. Use **Check setup** to list installed models, select one and save settings.

The optional `scripts/setup-local-ai.ps1` downloads the official installer, verifies its Authenticode publisher signature, installs Ollama and pulls the model. Collection and manual editing work while AI is unavailable. **Install PDF support** repairs PDF support in the exact Python environment running the app.

Only saved profile facts are used in matching and drafting. Employment dates or years of experience absent from the evidence must not be invented. Search-language preferences never upgrade the proficiency stated in application materials.

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

Existing databases migrate through the storage layer while retaining vacancies, notes, stages, letters and identities. Close the app before backing up the whole private folder. Avoid running both interfaces against it while editing shared profile/settings files. Personal data, credentials, model downloads and generated application materials are excluded from Git. See [requirements](docs/requirements.md) for the agreed scope.
