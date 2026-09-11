# Norway Job Agent

A private Python desktop assistant for discovering Norway vacancies, organizing opportunities and preparing cover letters for you to review and submit.

## Start

Double-click **Start-Norway-Job-Agent.cmd**, or run:

```powershell
python run.py gui
```

Your profile, CV text, database, notes and draft history are stored in `%USERPROFILE%\.norway-job-agent`, outside this OneDrive project. The personalized bootstrap profile under `private/` is excluded from Git.

On a fresh Python installation, run `python -m pip install -r requirements.txt` once for PDF support. You can also choose **Sources & AI → Local AI → Install PDF support**; this installs into the exact Python environment running the app.

## What works

- A ChatGPT-inspired Windows workspace with a quiet sidebar, opportunity cards, focused reading and writing panes, and keyboard navigation.
- NAV/Arbeidsplassen collection using its free experimental feed, with resumable pagination, updates and withdrawal handling.
- Greenhouse and Lever company-board connectors; configurable location filters.
- Individual public company job pages with `JobPosting` structured data, plus manual vacancy import for unsupported sites.
- A searchable **Company library** with official career links and supported automatic collectors, including bounded Workday and public career sitemap scans. Each entry explains its collection mode.
- A **Gmail alerts** workspace with read-only OAuth, LinkedIn/job-alert link extraction, editable review candidates, `.eml` import and email provenance on imported vacancies.
- SQLite duplicate detection, source links, private notes and application statuses.
- Transparent keyword matching, with related research roles requiring a connection to the relevant technical fields.
- Local AI cover-letter drafts and CV profile suggestions through **Ollama**, with review and editing before use.
- CV text import from TXT, Markdown, DOCX and text PDFs using the free `pypdf` package.
- Versioned cover letters and `.txt` export. Generating a letter never marks a job as applied.

The app has no application-submission or messaging function. You open the original application page and submit yourself.

## Daily workflow

1. Review your facts under **Profile & CV**. Actual language proficiency and search preferences are separate fields.
2. Choose **Find opportunities**. Read source errors or remaining-backlog notices under **Sources & AI → Collection activity**.
3. Browse **Discover** or **For you**, which includes target and related roles. Search updates as you type. Match scores reflect keyword evidence, not hiring probability or eligibility.
4. Set an opportunity's stage to **saved** under **Notes & status** to keep it in **Saved**. **Applications** shows preparing, ready, applied and interview stages.
5. Open **Cover letter → Draft with AI**, review every claim, edit the letter, and save a version or export it.

Use **Ctrl K** to focus search and **Ctrl S** to save the current profile, settings, notes or cover letter. **Ctrl 1/2/3** opens opportunities, profile and settings. Use arrow keys in the opportunity list. Drag the divider to adjust the reading pane. Fields show unsaved changes; switching to another opportunity asks before replacing your edited notes or letter. If a filter hides an opportunity with unsaved edits, its editor remains open with a notice.

Profile fields are grouped into **About you**, **Search preferences** and **Your CV**. Source configuration, local model setup and collection activity have their own tabs. These page changes keep form edits in memory until you save or close the app.

Only saved profile facts are used in matching and generation. CV extraction suggests changes for review and does not silently replace your saved profile. The CV does not supply employment dates or total years of experience, so those must not be invented.

## Zero paid APIs

Collection and organization use the Python standard library. AI runs through Ollama at `127.0.0.1:11434` using a downloaded local model; cloud model aliases and remote endpoints are rejected. No AI subscription, paid API key, hosted server, or paid search service is required.

One-time local AI setup:

1. Install and open [Ollama for Windows](https://ollama.com/download/windows).
2. In **Sources & AI → Local AI**, choose **Start Ollama**, then **Download model**. The default `qwen3:4b` is approximately 2.5 GB. Download progress appears in the app; retrying resumes partial downloads.
3. Choose **Check setup**, select an installed model and save settings. The model list confirms local text-generation capability.

The optional setup script `scripts/setup-local-ai.ps1` downloads the official installer, checks its Authenticode publisher signature, installs Ollama and pulls the model. It requires normal Windows filesystem/network permissions. Collection and manual editing work while AI is unavailable.

The model is a separate download from Ollama itself. An empty installed-model list means the runtime is present but generation is not ready yet. PDF import can be repaired independently using **Install PDF support**. Scanned image PDFs still need OCR or pasted text.

## Gmail job alerts

Open **Gmail alerts → Setup guide** to create your own Google Desktop OAuth client and enable the Gmail API. Then choose **Connect Gmail**, select the credentials JSON, and complete Google sign-in and consent yourself. The app requests only `gmail.readonly`, stores the refresh token outside the project (encrypted for the current Windows user), and never sends, deletes or marks mail as read. **Cancel sign-in** stops an unfinished connection. **Disconnect** forgets local credentials; Google Account settings can revoke the grant separately.

The default search covers job alerts from the last 90 days. Change the Gmail search query to broaden it, including removing `newer_than:90d` for all matching history. Each fetch reads at most 50 messages; **Next batch** continues. Neither fetching nor parsing adds vacancies automatically. Select extracted opportunities, use **Edit details** to fill missing employer/location information, then choose **Add selected to vacancies**.

The parser reads text and links in the email itself, including job-specific LinkedIn links and supported tracking wrappers. It does not scrape LinkedIn, load tracking images, fetch attachments or follow email links automatically. Email excerpts are labelled as partial descriptions; check the original posting. Unsupported formats may need manual entry. Repeated vacancy URLs deduplicate, and short email excerpts do not overwrite complete employer postings. Original message references remain attached to the vacancy.

You can use **Import .eml** immediately without connecting an account. In Gmail, download a job-alert message as `.eml`, then select it in the app. See [Gmail setup and limits](docs/gmail-setup.md).

## Coverage and limitations

NAV starts with the last **90 days of feed updates**, then resumes from a saved checkpoint. Each regular collection reads at most five pages. A backlog notice means more pages remain; collect again to continue. Changed search terms restart this window so previously skipped records can be reconsidered. This window does not represent every active vacancy in Norway, and NAV excludes FINN-origin vacancies.

NAV's public token is explicitly for experiments. For ongoing use, register directly with NAV for a private token and set `NAV_API_TOKEN` in your environment. The app never emails NAV or creates an account for you. Read [NAV's feed documentation](https://navikt.github.io/pam-stilling-feed/) and [usage terms](https://arbeidsplassen.nav.no/vilkar-api).

The library contains 27 employers, with automatic configurations for Equinor, DNV, Tieto, AutoStore, Cognite, Bekk, Netlight, Crayon, Sopra Steria and Bouvet. The other 17 entries, including Google, provide official career links for research and email-alert imports. Each refresh reports whether a source succeeded; a successful check can return zero jobs. Company postings are retained as research records; automatic disappearance detection currently applies to the NAV feed. Always check the original vacancy before applying.

Use **Sources & AI → Company library** to browse additional employers, open their official career sites, or add selected automatic sources. **Add supported companies** adds the available automatic configurations to the settings form. Save settings to keep them. Career-link entries remain accessible for manual research and email alerts; their presence in the library does not mean automated collection is available.

Company collection is bounded by source limits. Workday searches and sitemap scans report their coverage under **Collection activity**, including unreadable pages and truncated results. Company scans currently restart at the source's listing window on each run; unlike NAV, they do not maintain a historical pagination cursor. A source's `max_jobs` can be configured up to 100. URL-based country filters can miss postings whose URLs omit location. Robots-disallowed or unsupported sites are not bypassed.

FINN and LinkedIn use manual URL/text import in this version. Generic company-page import needs public structured job data and respects robots rules. JavaScript-only pages, login requirements and blocked pages need a dedicated connector or manual import. The app does not search the entire web or continuously run while closed.

Related suggestions currently use configured related fields and posting content. They are not a learned model of browsing behavior. Search language preferences keep possibilities visible for review; they never upgrade the language proficiency stated in application materials.

## Command line

```powershell
python run.py init
python run.py collect
python run.py list --match
python run.py show 1
python run.py status 1 saved
python run.py notes 1 "Check language requirements"
python run.py letter 1
python run.py brief 1 --output "preparation-brief.md"
python run.py import "my-vacancy.json"
python run.py import-url "https://company.example/careers/specific-job"
python run.py models
```

`brief` creates an offline factual preparation brief; `letter` uses the local AI model. Use `--data-dir "C:\your\folder"` **before** the command to choose another private data directory. See `python run.py --help`.

## Source configuration

Edit sources in the app, or edit the private `sources.json`:

```json
{
  "model": "qwen3:4b",
  "sources": [
    {"type": "nav"},
    {"type": "lever", "board": "bekk", "region": "global", "locations": ["Oslo", "Trondheim"]},
    {"type": "greenhouse", "board": "cognite", "locations": ["Norway", "Oslo"]}
  ]
}
```

Without `locations`, a company board returns all its locations. Lever also supports `region: "eu"`. Use source IDs from the employer's published career page, not guessed company names. Connector references: [Greenhouse](https://docs.greenhouse.io/job-board.html), [Lever](https://github.com/lever/postings-api), [JobPosting](https://schema.org/JobPosting).

## Development and checks

Requires Python 3.12+ with Tkinter and `pypdf` for PDF import. Gmail/OAuth, collection, storage and the desktop UI use the standard library.

```powershell
python -m unittest discover -s tests -v
python scripts/preview-ui.py
```

Tests use temporary databases and mocked source/model responses. They cover duplicate identity, preservation of notes/status/drafts, resumable NAV state, withdrawals, source URL restrictions, local-only model checks, grounded evidence, truthful language claims and the offline workflow. The design preview opens fictional sample opportunities in a temporary workspace without changing personal data. Live collection and desktop checks are separate.

See [requirements](docs/requirements.md) for the agreed scope. Personal data, model downloads and generated application materials are excluded from version control.

