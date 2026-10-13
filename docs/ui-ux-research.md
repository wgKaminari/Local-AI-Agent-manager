# Local desktop redesign: research and acceptance criteria

Research checked on **5 October 2026**. This document separates external guidance, our design decisions, and the checks needed to validate those decisions. It is a design brief, not a claim that every acceptance criterion already passes. The user selected a **separate desktop window containing a modern web interface**, while keeping the application local and its business logic in Python.

## Selected desktop architecture

Version 0.3 keeps the existing Python service layer, SQLite records, local Ollama integration, and reviewed application delivery. The main presentation surface is locally packaged HTML/CSS/JavaScript in **pywebview**, with a small **Flask** API served on the loopback interface. **Waitress** runs the application server, and WebView2 renders the dedicated Windows window. The Tkinter interface remains a compatibility fallback through `python run.py gui --classic`. Tkinter is the previous UI toolkit; IDLE is Python's editor, not the toolkit being replaced.

This recommendation is a project-specific judgment: the existing service functions already provide most business operations, and the user wants a dedicated window with finer control over layout and interaction. pywebview officially supports both local web-server applications and a JavaScript/Python bridge. Its architecture guidance includes a Flask example and discourages relying on `file://` for an application. [pywebview architecture](https://pywebview.flowrl.com/guide/architecture.html)

### Options considered

| Option | Fit for this project | Tradeoff | Decision |
| --- | --- | --- | --- |
| Existing Tkinter | Existing features and desktop tests stay usable | The current nested panes and text-heavy forms require substantial interaction/layout work; CSS design cannot be reused | Retain as fallback |
| Flask + custom web UI + pywebview | Dedicated window, reusable Python service functions, precise responsive layout | Requires explicit request validation, UI state management, and native-runtime testing | Selected direction |
| FastAPI + custom web UI + pywebview | Typed API validation, OpenAPI and asynchronous capabilities | An API framework change adds little immediate value when services and validation already exist | Valid alternative if the API grows substantially |
| NiceGUI in native mode | Python-authored web components and built-in local/native presentation | Adds its component and real-time communication model; custom interactions must fit that model | Good rapid-prototype alternative, unnecessary for this migration |
| Local web UI in a normal browser | Same interface can aid automated testing | Does not match the user's required dedicated desktop window | Test tooling only; not the user launch workflow |

FastAPI's documented strengths include Python-type-based validation, generated API documentation and Starlette integration. NiceGUI documents a FastAPI foundation, local web serving and native-window mode. These are capabilities, not evidence that either framework automatically produces a better interface. [FastAPI features](https://fastapi.tiangolo.com/features/), [NiceGUI documentation](https://nicegui.io/documentation), [NiceGUI configuration](https://nicegui.io/documentation/section_configuration_deployment)

### Local operation and migration constraints

- Reuse existing service operations; do not introduce a second database or duplicate matching/application rules in JavaScript.
- Package fonts/icons/styles/scripts locally. The shell and saved jobs must open without a network connection; collection still needs source access and AI needs the local model runtime.
- Bind the server to `127.0.0.1` on a dynamically allocated port. Shut it down with the desktop window. Do not expose the application to the LAN.
- Authenticate private API calls with an unpredictable per-launch token, validate origin/host, and protect mutating requests from CSRF. A loopback bind alone does not protect against another website targeting localhost. Keep the token out of URLs, logs and persistent storage.
- Escape job descriptions, email excerpts, model output and profile values as text. Block unapproved URL schemes, restrict embedded content with CSP, and keep employer sites outside the privileged app webview.
- Limit request/upload sizes. A browser-facing endpoint must not become an arbitrary filesystem reader; native pickers or explicit bounded uploads should be the file entry point.
- Preserve the existing review token, file snapshot, duplicate-send guard and delivery log. A prettier Send button must call the same guarded service operation.
- Show a useful startup error and fallback instructions if the new dependencies or WebView2 are unavailable. Do not silently reset user data.

pywebview documents session-unique API tokens and the need for CSRF protection. Its Windows installation guidance requires WebView2 for its modern Chromium renderer. Flask documents escaping, security headers, request limits and trusted-host checks. These controls must be implemented and tested for this application; installing the frameworks is insufficient. [pywebview security](https://pywebview.flowrl.com/guide/security.html), [Windows prerequisites](https://pywebview.flowrl.com/guide/installation.html), [Flask security](https://flask.palletsprojects.com/en/stable/web-security/)

Flask explicitly treats private/local single-user delivery as production and advises against its development server for that use. Waitress directly supports Windows and a loopback-only bind. Collection and generation should run in bounded worker jobs, leaving the UI/API responsive; job IDs and truthful status messages are preferable to blocking a request for minutes. [Flask deployment](https://flask.palletsprojects.com/en/stable/deploying/), [Flask's Waitress guide](https://flask.palletsprojects.com/en/stable/deploying/waitress/)

## What the design references contribute

### Uizard: useful visual starting points, dated inspiration

The supplied article was published **3 October 2022**. It is a set of template examples, not evidence of the latest 2026 interaction practices. Its project-management and cloud-storage examples are relevant to this app's tasks and documents; the dating, social and video-call templates are much less relevant. Use the references to explore a coherent workspace, clear grouping and readable surfaces. Do not copy marketing claims, purchase a template, upload a CV, or introduce Uizard as a runtime dependency. [User-provided Uizard article](https://uizard.io/blog/10-app-templates-you-can-use-right-now/)

The project-management and cloud-storage pages offer concrete prototype references. Our adaptation is a job workspace with a visible current task and document review, not a literal clone of either template. No proprietary template assets are required. [Project-management template](https://uizard.io/templates/website-templates/project-management-website/), [Cloud-storage template](https://uizard.io/templates/web-app-templates/cloud-storage-web-app/)

### Current guidance: navigation, feedback and evidence

Microsoft Fluent 2 recommends brief, understandable navigation focused on user goals, a visible selected destination, and predictable hierarchy. Apply that to a small persistent navigation rail with labels: opportunities, applications, sources, profile and settings. Country selection belongs in the workspace header as shared context. Keep editing actions inside their pages. [Fluent navigation](https://fluent2.microsoft.design/components/web/react/core/nav/usage)

Fluent's layout and typography guidance emphasizes grouping, hierarchy, readable text and native system fonts. Use a consistent spacing scale and one restrained accent; give the vacancy title, company, location and next action priority. Light and dark themes should use the same semantic tokens. This is a design decision, not a claim that rounded cards or a particular color are inherently more usable. [Fluent layout](https://fluent2.microsoft.design/layout), [Fluent typography](https://fluent2.microsoft.design/typography)

Carbon's data-table guidance supports search/filter controls close to results, progressive disclosure of details, and moving complex content into a dedicated detail surface. It also distinguishes AI-generated content through a visible label. Apply those ideas to a scannable opportunity list and adjacent details: list items show only enough to choose a job, while requirements, evidence, notes and drafts live in the detail pane. Identify AI drafts and explain matching evidence without implying hiring probability. [Carbon data tables](https://www.carbondesignsystem.com/building-blocks/core/components/data-table/guidelines)

Empty states must distinguish first use, no filter matches, and a failed operation. A new database should offer profile setup and collection. A filtered empty list should offer clearing filters. A failed source should retain existing jobs and explain how to retry. These are different states with different next actions. [Carbon empty states](https://www.carbondesignsystem.com/building-blocks/core/patterns/empty-states)

The GOV.UK check-answers pattern supports a final summary that can be reviewed and edited before submission. Apply it to application answers, exact attachments and destination immediately before the explicit Send action. Keep draft readiness separate from successful delivery. A network success with no verified employer receipt must remain unconfirmed. [Check answers](https://design-system.service.gov.uk/patterns/check-answers/)

Validation should preserve entered values and tell the user what to fix near the field. For multiple errors, provide a summary linking to the affected fields and move focus to it after submission fails. Service failures require a useful recovery message; they should not make valid user input look wrong. [Validation](https://design-system.service.gov.uk/patterns/validation/), [Error summary](https://design-system.service.gov.uk/components/error-summary/), [Error messages](https://design-system.service.gov.uk/components/error-message/)

## Workflow design

The shipped desktop navigation uses **Discover**, **For you**, **Saved**, **Applications**, **Profile & writing**, **Source library**, **Job alerts** and **Settings & activity**. The task model below guided those screens. It also records desired interaction details; the validation checklist remains separate from claims about checks actually completed.

| User task | Proposed interaction | Success condition |
| --- | --- | --- |
| Find suitable work | Search and country/status filters above a result list; remembered selection; separate details | Filtering does not discard notes, letters or answers; no false empty state during refresh |
| Switch country | One consistently located selector with full country names | Only source/discovery context changes; shared profile stays byte-for-byte unchanged |
| Judge a vacancy | Company, location, source freshness, status and matching evidence next to the description | The user can understand why it appeared and open its original posting |
| Manage applications | Filterable stages with counts and an accessible stage selector | Status change is explicit; drafting never implies applied |
| Prepare an application | Overview, letter, answers and activity/history grouped inside the selected vacancy | Native text/select/checkbox controls fit extracted question types; missing facts remain visibly unresolved |
| Review and send | Complete summary with Edit links, attachment names and destination | Only the explicit send action initiates one guarded attempt; unsupported sites offer manual continuation |
| Maintain profile | Separate facts, search preferences, country eligibility and writing style | CV suggestions remain proposals; style examples never become biographical facts |
| Maintain sources | Country directory plus enabled collectors and honest coverage labels | A research link cannot be confused with an active automatic collector |
| Wait for collection/AI | Persistent job progress and completion/error result | Other pages remain usable; no fabricated progress percentage; a late response cannot overwrite newer edits |

Favor a list/detail layout at desktop widths and stack the views at narrow widths. Do not force a long form into a tiny nested scroll region. Place contextual commands near their content and make the next meaningful action visually clear. Use shortcuts as accelerators, not as the only way to reach a function. Keep technical logs under details rather than making them the primary workflow.

## Measurable UX acceptance criteria

These are release targets and regression scenarios. Automated checks support them but do not replace a manual keyboard/screen-reader review or user feedback.

1. **Responsive layout:** verify 1440×900 and 1120×720 desktop viewports, narrow 390-pixel layouts, and 200% zoom. Content must remain reachable; no clipped save/send actions. At 320 CSS pixels of effective width, ordinary content should reflow without two-dimensional scrolling. Genuine data tables have a WCAG exception, but application forms do not. [WCAG reflow](https://www.w3.org/WAI/WCAG22/Understanding/reflow.html)
2. **Keyboard:** complete search → select → edit → save → review with the keyboard. Use semantic controls, meaningful labels, a skip link and logical focus order. Keyboard focus must remain visible and not disappear behind sticky controls. [WCAG keyboard](https://www.w3.org/WAI/WCAG22/Understanding/keyboard.html), [Focus not obscured](https://www.w3.org/WAI/WCAG22/Understanding/focus-not-obscured-minimum.html)
3. **Dialogs:** entering a modal places focus appropriately; Tab stays within it, Escape closes when safe, and focus returns to the invoking control. Large application editing belongs on a page/pane rather than a stack of dialogs. [WAI dialog pattern](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/)
4. **Target sizes:** aim for 40–44 CSS-pixel main controls. Ensure other targets meet WCAG's 24×24 CSS-pixel minimum or the documented spacing/other exceptions. These are different thresholds: 44 pixels is our comfort target, not the WCAG 2.2 AA minimum. [WCAG target size](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)
5. **Contrast:** measure both themes. Normal text requires at least 4.5:1 contrast and large text 3:1 under the criterion's definitions/exceptions. Status and errors must include text, not color alone. [WCAG contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)
6. **Feedback:** saving, filtering, collection and generation expose their state to assistive technology without repeatedly stealing focus. Errors persist until understood; a transient toast cannot be the only record of a failed send. [WCAG status messages](https://www.w3.org/WAI/WCAG22/Understanding/status-messages.html)
7. **Motion:** transitions only clarify a state change, remain short, and respect reduced-motion preferences. Do not animate large background surfaces or stagger every result on repeated refresh. [Fluent motion](https://fluent2.microsoft.design/motion)
8. **State preservation:** editing then changing theme, country, filter, selected job or page must either preserve the edit or present Save/Discard/Stay. Cancellation retains it. A delayed AI result must not replace text typed after generation began. Save failure retains local edits and explains recovery.
9. **Trust and delivery:** AI text is identified as a draft; source-derived fields show their source; sensitive answers require the user's choice. Any edit after delivery preview invalidates that preview. Duplicate clicks/reloads cannot send a second application. The app distinguishes blocked, uncertain and submitted-but-unconfirmed outcomes.
10. **Startup and reliability:** launch and close the native window repeatedly without a leftover listening server; show actionable missing-runtime/offline-model errors. Saved data opens offline. Collection failure must not clear stored vacancies. Reopen the existing private database without profile or workflow loss.
11. **Perceived speed:** input and navigation acknowledge actions immediately; expensive work runs off the UI thread. Preserve existing results while refreshing and show work in progress. Measure locally before reporting startup/filter timings; do not invent performance promises.

## Validation plan

- Run the existing service, storage, country, generation, Gmail and delivery regression suite against the preserved Python logic.
- Add API tests for valid/invalid payloads, authentication, hostile origins/hosts, HTML injection, body limits, missing records and service failures.
- Add browser scenarios with fictional temporary data covering country filtering, profile edits, draft edits/history, theme/navigation preservation, failed saves, loading/empty states and unsupported delivery.
- Test the native WebView2 launcher separately from Chromium browser tests; browser success does not prove native-window startup, close handling or file pickers.
- Use isolated fictional employer forms for delivery tests, with all traffic intercepted. Do not submit real applications or use the user's profile as a test fixture.
- Perform visual checks in both themes and small/large windows, followed by the keyboard checklist. Record what was actually tested in the implementation report, including any unverified accessibility or native-runtime behavior.

The desired improvement is observable: fewer context switches, larger readable editing surfaces, reliable preservation of work, clearer next actions and honest progress/delivery states. Design-system adoption by itself is not proof of that improvement; these workflows and checks are the evidence.
