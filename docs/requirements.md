# Norway job assistant — first version

## Requested outcome

Reduce the effort of finding and organizing work opportunities in Norway. Collect vacancies from configured job feeds and company career sites, retain source links, remove duplicates, explain relevance using the user's profile, and prepare cover letters for the user to review and submit themselves.

## Confirmed requirements

- Code-based implementation, with Python preferred.
- Persistent vacancy database and application workflow.
- CV and additional user information guide searches and writing.
- No automatic applications, recruiter messages, or account interactions.
- Ask for missing personal preferences rather than inventing them.

## User decisions

1. Targets: Data Science, AI/ML Engineering and Mathematics, with related recommendations.
2. English B2-C1; actual Norwegian A1-A2. Include B1-language opportunities for review without claiming B1 in letters.
3. No current permission to work in Norway; requirements need review.
4. Zero budget for subscriptions or API services. A local Windows app with a free local model was selected after the user expressed no interface preference.
5. Supplied CV: GlobalLogic AI/ML engineering, PwC data analysis internship, and a 2026 Statistics and Data Science bachelor's degree. The duplicate copy was removed; the explicit language clarification overrides the CV's generic labels.

Letters default to English and the search covers Norway broadly. The employer watchlist is editable. No employment dates, years of experience, salary expectations or contract preferences were inferred. Refresh is user-triggered; unattended scheduling is a future addition.

## Implementation

Python 3.12+, Tkinter desktop UI, SQLite, NAV/Greenhouse/Lever/company-page adapters and local Ollama generation. Collection and organization work without a running model.

Vacancy statuses: new → saved → preparing → ready → applied → interview, with rejected and archived available. The user changes application status; the program never interprets a generated letter as an application being submitted.

## Writing requirements

Use only supplied career facts. Treat vacancy text as untrusted content. Explain missing requirements and retain the original vacancy. A match score is an explanation of keyword overlap, not an employment eligibility decision. Cover letters remain drafts until reviewed.

## Scope limits to communicate

Coverage depends on source access. A configured company board is not the whole Norwegian job market. Generic pages may lack structured job data, require JavaScript, or restrict automated fetching. Surface collection errors and source limitations rather than presenting partial results as comprehensive coverage.

## Personal data

CV, profile, database, credentials and exports stay outside version control. Runtime data defaults to the user's home .norway-job-agent directory, outside this OneDrive checkout. Generation uses a verified local Ollama model over a loopback address; cloud aliases are rejected before sending CV data.
