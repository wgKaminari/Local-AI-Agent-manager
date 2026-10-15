# Version 0.4 performance and reliability

Measured on 15 October 2026 by running the 0.3 and 0.4 code back to back against the same private copy of a real database of about 4,000 vacancies on the development machine. These timings describe that dataset and machine, not guaranteed response times.

| Area | Before | After |
| --- | --- | --- |
| Discover list | 3.9 s, 5.9 MB response | 1.2 s, 2.8 MB |
| Discover search ("data") | 3.2 s, 4.6 MB | 1.0 s, 2.1 MB |
| For you | 0.70 s | 0.40 s |
| Saved / Applications | 4.0 s | 0.2 s |
| Germany collection, 23 live sources | 3 min 32 s (last recorded 0.3 run) | 46–56 s (two runs) |

- **Matching.** Experience-requirement extraction depends only on the advert text, so it is memoized by a digest of that text. Matching no longer deep-copies the profile for every advert. Saved and Applications match only their own stages while reporting the same scope counts. List rows carry only the match fields a card renders; full evidence still loads with the selected vacancy.
- **Storage.** SQLite uses WAL mode, so the interface can read while a collection writes. A database with a complete schema opens without rerunning migration DDL; incomplete or older databases are still migrated.
- **Collection.** Up to six sources download at once. Each source is still validated completely and written as one transaction, in configured order, so duplicate handling remains deterministic. An ordinary page read that loses its connection is retried once; HTTP errors and POST searches are not retried. Lever jobs use the configured employer name instead of the board ID.
- **Local AI.** Cover letters, profile suggestions and application-answer batches retry up to three attempts in total when the output fails validation, telling the model which check failed. Every safety check still applies to every attempt, and connection, setup and timeout errors are not retried. The context window now grows with the input instead of a fixed 12k tokens.
- **Resilience.** A damaged `last-collection.json` no longer prevents the application from opening.

The full suite with `RUN_BROWSER_TESTS=1` passed: **508 tests**, including the real-Chromium interface and delivery tests.

# Version 0.3 implementation and verification

Release checks recorded on 14 October 2026. This report describes the implemented changes and completed checks; the broader design targets remain in [UI/UX research](ui-ux-research.md).

## Desktop workspace

The default launcher opens a dedicated pywebview window using WebView2 on Windows. Python continues to own collection, matching, SQLite storage, local Ollama drafting and reviewed delivery. HTML, CSS and JavaScript are packaged locally and served through a loopback-only Flask/Waitress service. There is no hosted website or browser-tab workspace to configure. The previous Tkinter interface remains available with `python run.py gui --classic`.

The workspace provides list-and-detail discovery, country selection, saved opportunities, application stages, profile and writing preferences, source management, job alerts and activity feedback. Light/dark themes, keyboard shortcuts, editing buffers and bounded background tasks support the same workflow across pages. Late draft or save responses are guarded against replacing newer edits. The native close handler tracks unsaved work and running tasks without making a synchronous JavaScript call during window shutdown.

Existing profiles, databases and preparation histories remain in the same private data folder. Nested transactions and complete-batch rollback protect source imports, email imports and related workflow changes. Saved and application records remain accessible after their source posting is withdrawn.

## Discovery and relevance

- The country catalog contains **366 distinct employers and 109 automatic employer routes**. Country counts overlap: USA 80 routes, Germany 48, Ukraine 21 and Norway 18. Career links and job-board research links are shown separately from collectors. Verification dates and live-feed limits are documented in [Discovery rework](discovery-rework.md).
- **Add all country collectors** expands an existing configuration explicitly. Country switches preserve the shared profile and respect previously removed presets. Generated city restrictions are removed when recognized; custom location filters remain.
- Collection uses structured country evidence where available, applies geographic filtering before relevant job caps, and reports failures and bounded scans separately from successful empty results. Complete validated Greenhouse, Lever and Ashby snapshots can retire disappeared postings; failed or partial snapshots cannot.
- The profile accepts decimal **years of professional experience**. Recommendations distinguish explicit required years, preferences, education alternatives and title-based seniority signals. Clear conflicts are hidden by the default experience filter, while uncertain requirements remain visible for review.
- **For you** distributes recommendations across employers while retaining role relevance. Cards emphasize role and experience evidence; keyword overlap is supporting information rather than a hiring-probability claim. Coverage feedback reports collected jobs, employers, configured sources, failures and limits.

Experience screening does not establish domain expertise, work authorization or overall eligibility. The directory is a curated subset of the market, and manual job-board links do not imply automatic coverage.

Matching avoids repeated prose scans and exits early for unrelated roles in **For you**, using the same role rules as full discovery. An isolated database of 3,433 public adverts returned that view in 0.78–0.91 seconds on the checked machine. Full matching results were compared with the previous implementation for parity; profile and posting edits are evaluated afresh. These timings describe this dataset and machine, not a guaranteed response time.

## Reviewed preparation and delivery

Cover letters and application answers remain editable drafts. A supported form requires a review of the destination, answers and attachments followed by an explicit Send action. Review snapshots and duplicate-attempt guards remain enforced. Unsupported or conditional forms may require manual completion; email access remains read-only. No employer applications were sent during release verification.

Private API calls require a per-launch session token and validate request origin/host. The interface renders untrusted vacancy, email and model text safely, packages its assets without a UI CDN, and uses bounded imports with opaque attachment handles. These controls do not make the private data folder an encrypted vault or isolate it from other software running as the same Windows user.

## Checks completed

| Check | Observed result |
| --- | --- |
| Full automated suite with `RUN_BROWSER_TESTS=1` | **493 tests passed** in 169.686 seconds, including 23 modern-interface browser tests |
| Core and API regressions | Experience screening, geographic filtering, source coverage, employer diversity, snapshot withdrawal, transactional imports, settings concurrency, draft revision guards and reviewed-delivery boundaries passed in the full suite |
| Interface regressions | Profile experience entry, recommendation filters, saved records, coverage feedback and editing/review workflows passed with temporary fictional data |
| Native Windows window | Actual WebView2 startup, experience/profile editing, unsaved-close confirmation, Cancel preserving edits, explicit Save, Ctrl+S and clean close after saving were verified separately from browser tests |
| Package build | Version 0.3.0 wheel built; inspection confirmed three UI assets, five catalog JSON files and the desktop/API/experience modules, with no database or email files included |

The checked environment was **Windows with Python 3.12**. Automated fixtures used temporary fictional profiles and mocked or controlled services; public-source verification is described separately in the discovery report. Successful automated and native checks do not establish macOS/Linux support, screen-reader usability, accessibility certification or reliable delivery to every employer form.

For setup, everyday use and recovery steps, see [Modern local desktop](modern-desktop.md). Test commands are in the [README](../README.md#development-and-checks).
