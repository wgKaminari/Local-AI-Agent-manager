# Modern local desktop

Personal Job Agent 0.3 opens in its own desktop window. Python still manages collection, matching, the database, local AI, and reviewed delivery. The new interface uses locally packaged HTML, CSS and JavaScript inside pywebview; Windows renders that interface with WebView2. The workspace is a desktop application, with no hosted account, public website or cloud deployment to configure.

The rationale, alternatives and design references—including the supplied Uizard templates—are in [UI/UX research](ui-ux-research.md). The interface follows a list-and-detail workflow, with larger editors, task feedback and clearly separated preparation and delivery.

## Start the application

Use Python 3.12 or newer. From the project directory, install the current dependencies once:

```powershell
python -m pip install -r requirements.txt
python run.py gui
```

After installation, double-click **Start-Personal-Job-Agent.cmd** for normal use. The previous **Start-Norway-Job-Agent.cmd** forwards to the same launcher. Neither opens the workspace in a normal browser tab.

Windows needs the Microsoft Edge WebView2 Runtime. The launcher requests its Chromium renderer explicitly. If the window cannot start, run the command above in PowerShell to see the startup error and check the [official pywebview Windows prerequisites](https://pywebview.flowrl.com/guide/installation.html).

The previous Tkinter interface remains available:

```powershell
python run.py gui --classic
```

To select a different existing data directory, put the option before the command:

```powershell
python run.py --data-dir "C:\YourPrivateFolder\JobAgent" gui
```

The optional application-delivery browser is separate from WebView2. Install it only if you want to use supported reviewed form delivery:

```powershell
python -m pip install "playwright>=1.49,<2"
python -m playwright install chromium
```

Ollama and a downloaded local text model are needed for AI drafting. Saved vacancies, profile editing, notes and manual preparation remain usable without a running model. Settings provides local AI setup and model actions; an unavailable model produces an error rather than switching to a cloud model.

## Existing data and migration

The default private folder is still `%USERPROFILE%\.norway-job-agent`. The new and classic interfaces use the same `vacancies.db`, `profile.json`, `sources.json`, histories and supporting files. Moving to the new window does not create a second profile or require re-importing vacancies. The existing SQLite schema remains version 3, with older supported schemas upgraded by the existing storage layer.

Close the application before making a backup, then copy the whole private folder to a location you control. A backup can include CV text, answers, attachments and connection credentials; it belongs outside the repository. Start only one interface against the folder while editing profile/settings files, since those are shared JSON files rather than a multi-user settings service.

The application retains the existing preparation rules: generating a letter or answer does not change the application stage. Withdrawn postings leave active discovery, while saved/application records retain their notes and version history for follow-up.

## Everyday workflow

1. Open **Profile & writing**. Review your facts, total years of professional experience, evidence, roles, locations, languages, country-specific work eligibility and writing preferences. Imported CV suggestions require review before becoming profile facts.
2. Choose **Searching in** to switch between Norway, USA, Germany, Ukraine and all countries. A country's supported employer presets are added and saved on first selection; the shared profile stays unchanged. Removing a preset is respected on later switches. Save any settings edits before changing country. If the 250-source limit would be exceeded, the entire proposed change is rejected with a recovery message.
3. Use **Discover** or **For you**, then search, filter and sort the opportunity list. Open a vacancy alongside the list to inspect matching evidence, source information and the original posting. The experience filter hides clear seniority/minimum-year conflicts by default. For you balances role matches across employers; unknown experience and other eligibility requirements remain for review. Keyword overlap is supporting evidence, not a hiring-probability estimate.
4. Use **Notes & stage**, **Cover letter**, and **Application** inside the vacancy workspace. Save changes explicitly. Versions remain available for reviewing earlier letters and answers. Keep the desktop window open until saves and background actions finish.
5. In **Application**, read public questions or paste missing questions, draft answers with local AI, then review each answer and selected option. Choose attachments explicitly. Sensitive choices and facts missing from the profile remain yours to supply.
6. For a supported form, choose **Review & send** and inspect the exact destination, answers and attachments. Only the explicit Send action starts delivery. For an unsupported application, use the prepared material on the employer's original site.

**Saved** and **Applications** organize follow-up. **Source library** distinguishes active automatic collectors from employer and job-board research links. **Job alerts** supports reviewed Gmail candidates and `.eml` imports. **Settings & activity** keeps source configuration, local AI and collection feedback together. Collection and generation run in background workers; progress text describes actual activity without promising a completion time.

Appearance controls change the workspace theme. **Ctrl K** opens Quick actions, **Ctrl F** focuses opportunity search, **Ctrl S** saves the current editor, and **Ctrl Shift L** switches themes. The Help control lists available shortcuts. Page changes preserve editing buffers, but those buffers are not a backup: save before closing or restarting the application. The native window asks before closing with unsaved work or tasks in progress.

**Save opportunity** moves only new/saved roles between those two stages. An application already preparing, ready, applied or interviewing keeps its stage until you change it explicitly under **Notes & stage**. Profile and settings saves keep edits entered while an earlier save is completing; the newer edits still need saving.

## Local operation and security boundaries

- The Python server binds only to `127.0.0.1` on an automatically chosen port for the desktop window. No LAN bind or public hosting is configured. Closing the window requests server/resource cleanup.
- Private API requests require a fresh per-launch session token. Host, Origin and Fetch Metadata checks reject other-site requests; security headers restrict embedded content. UI assets ship with the package, without an external font or UI CDN.
- The interface displays untrusted vacancy, email and AI content as text. Employer links open outside the privileged workspace. Opening an employer site or completing Gmail sign-in may use the normal browser; the job workspace itself stays in the dedicated desktop window.
- File imports use explicit file selection and bounded uploads. The API receives opaque attachment handles rather than accepting arbitrary local paths. Selected application files are copied into the private data folder and may remain there alongside saved preparation versions.
- Profile facts and drafting stay in Python and local Ollama. Network access is still needed for source collection, employer pages, optional Gmail, initial model downloads and an explicitly requested delivery.
- This is a local single-user application, not an encrypted vault or a sandbox against software already running as your Windows user. Protect the private data folder with your normal Windows account and backup controls.

## Delivery and coverage limits

Question extraction is partial. Public static forms and supported question schemas can be read; conditional questions, logged-in pages and JavaScript-heavy applications may require pasting or manual completion. Listing an employer or source in the directory does not mean its entire site can be scraped automatically.

Reviewed sending supports a restricted class of public HTTPS forms with native controls and a same-origin POST destination. It does not bypass login, CAPTCHA or anti-bot controls. Email access stays read-only; Gmail is not an application-sending channel.

Previewing does not send. The delivery guard checks the reviewed form and values, snapshots selected file bytes, records intent before the request, and prevents a duplicate attempt for the same submitted or uncertain payload. Changes after preview require a new review. A successful HTTP request is **submitted but unconfirmed**, not proof the employer accepted the application. Inspect the employer's confirmation before changing the vacancy stage or considering another attempt.

The detailed supported-form constraints and CLI preparation commands remain in [Application preparation and delivery](application-preparation.md).

## Troubleshooting and verification

- **Window does not open:** run `python run.py gui` in PowerShell, check the dependency/runtime message, or use `--classic` while resolving setup. Keep the existing private data folder intact.
- **AI action fails:** check that Ollama is running and the selected model is downloaded locally. Your saved records do not depend on Ollama availability.
- **A source fails:** inspect the collection feedback. Other sources can complete, and prior saved records remain available. Database source batches and email imports are transactional, so a conflicting record cannot leave half of that batch saved. NAV advances its cursor only after database writes finish; a failed cursor save may safely replay already-stored records on retry.
- **Send result is uncertain:** check the employer page and delivery history. Do not assume a retry is needed simply because the application has not changed stage.
- **Cannot find an old role:** check Saved or Applications and clear filters. A withdrawn vacancy is intentionally excluded from active discovery.

The automated suite uses temporary fictional data. Core integrity scenarios cover complete-batch rollback, nested transactions, country-source merging, settings preservation and withdrawn application history. API and interface checks are separate from native WebView2 startup/closing checks. See the [implementation report](implementation-report.md) for the checks actually run; the research acceptance criteria alone are not a claim of accessibility certification or cross-platform validation.

The older `scripts/preview-ui.py` script still previews the classic Tkinter interface. It does not demonstrate the new desktop shell. Development commands for the current API, UI and fictional delivery tests are in the [README](../README.md#development-and-checks).

## Discovery upgrade

Use **Source library → Add all country collectors** to expand an existing market with the current verified catalog. This is explicit so ordinary country switches continue to respect previously removed sources. The coverage panel distinguishes directory entries, enabled collectors, checked sources, failures and scan limits. See [Discovery rework](discovery-rework.md) for measured catalog coverage and known limits.
