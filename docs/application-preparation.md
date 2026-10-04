# Prepare, review and send an application

The application workspace reads available questions, drafts answers using your
saved facts and writing preferences, and lets you edit them. For supported public
HTML forms, you can review a filled browser preview and explicitly press **Send
reviewed application**. Reading questions, drafting, saving and opening a preview
do not submit anything.

## Desktop workflow

1. Select a saved vacancy and open its application workspace. Choose **Read
   questions**. The app reads the saved application URL, using Greenhouse's public
   question schema when available and static HTML otherwise.
2. Check the coverage message against the employer page. If the questions are
   missing or incomplete, use **Paste questions**. Put one question per line;
   use `Question | Option A | Option B` for choices.
3. Choose **Draft answers** with your local model available. Select each question
   to review the draft and edit it. For choice questions, enter a listed option
   label or value; put multiple selections on separate lines.
4. Complete questions marked as needing your input. Consent, work authorization,
   salary, start dates, relocation commitments and sensitive personal questions
   require your own answer. A blank profile value is unknown; the model must not
   guess. Contact details are copied only when explicitly saved for that field.
5. Select an upload question and choose **Attach file** to pick its document.
   **Save version** keeps a private copy of your work; **History** restores earlier
   answers for editing. **Export answers** saves readable text for manual use.
6. For a supported native form, choose **Review in browser**. Review all answer
   values, choices, destination and attached filenames in the review dialog and
   the filled browser. Use **Cancel / edit answers** to make changes and create
   a fresh preview.
7. Press **Send reviewed application** only when the preview is ready. This allows
   one submission attempt. Inspect the employer's resulting page to confirm
   acceptance, then update the vacancy's application status yourself.

The send action uses the reviewed snapshot, including selected file contents.
Changes to browser values, choices, required fields, destination, files or
recognized anti-forgery tokens invalidate that preview. Editing a selected file
on disk after opening the preview does not replace the bytes already reviewed;
cancel and create a new preview to include a revised file.

## Writing preferences

Under **Countries & writing**, configure tone, language, style instructions,
writing examples, phrases to avoid, and maximum answer length. Writing examples
guide wording; they are not evidence of your qualifications. The model uses
saved profile facts and CV text, and provides supporting excerpts for its drafts.
Check the claims themselves: an excerpt is useful context, not proof that every
generated sentence is accurate.

The writing-language override takes precedence over the profile's usual cover
letter language. The word limit is 20–500, with a default of 180. A question's own
character limit also applies. Style and your shared job preferences persist when
you switch between Norway, USA, Germany and Ukraine. Actual country-specific work
authorization can be set separately; choosing a country never establishes it.

## What can be read and sent

| Application type | Question preparation | Delivery |
| --- | --- | --- |
| Public Greenhouse application | Public questions, listed options, upload alternatives and available compliance questions | Complete on the employer website |
| Public static HTML form | Visible labels, native controls, listed options and requirements | Reviewed send when the checks below pass |
| Manually pasted questions | Text questions and supplied choices | Copy or export to the employer website |
| JavaScript application, embedded form, login, CAPTCHA or later steps | Public/static portion may be incomplete; paste missing questions | Complete on the employer website |

This version does not promise complete extraction or automatic submission on
every job source. Job discovery support and application delivery support are
separate: a vacancy may be collected successfully while its application must be
completed manually. Static extraction cannot discover conditional questions or
later screens reliably. When several forms exist, it chooses the most likely
application form and reports that choice.

Browser delivery requires a public HTTPS page on port 443, a single native POST
form with an unambiguous submit button that adds no extra posted values, and a same-website destination in the
same browser tab. All active answer controls must match the reviewed questions.
Supported controls include text, email, telephone, URL, multiline text, radios,
checkboxes, selects and explicitly selected files. Unsupported, ambiguous,
read-only or undisclosed answer controls require manual completion.

Known anti-forgery fields may accompany the form; the preview lists their names,
and their value hashes are checked immediately before sending. Their raw values
are not included in the answer draft. Other hidden application values require
manual completion so undisclosed answers or consent cannot be silently sent.

Files must be nonempty, at most 10 MB each and at most 25 MB combined. Uploads
require a multipart form. The review shows each file's name, size and content
hash. Typed file paths are not accepted as answers; use the file picker.

## Optional browser setup

Question extraction and answer drafting work without Playwright. To enable the
reviewed browser delivery feature, install it into the Python environment that
runs the desktop app:

```powershell
python -m pip install "playwright>=1.49,<2"
python -m playwright install chromium
```

The preview uses a fresh visible Chromium session with employer JavaScript,
downloads and service workers disabled. It does not save browser login state.
During review, outgoing browser requests are blocked. Clicking a website button
in the preview does not authorize a submission; use the app's explicit Send
button. Sites needing external resources or dynamic behavior should be completed
manually using the prepared answers.

## Delivery results and retry behavior

- `submitted_unconfirmed` means the reviewed POST was allowed and the browser
  received a response below HTTP 400. It is not proof that the employer accepted
  the application.
- `uncertain` means acceptance could not be confirmed, including navigation
  timeouts and server errors. Check the employer page before attempting again.
- `blocked` means the browser could not send the approved request in that attempt.

The app records a delivery attempt before sending and stores its result privately.
It never retries automatically or marks a vacancy as applied merely because a
browser request completed. A used browser preview cannot send again. The reviewed
payload is also recorded by fingerprint to prevent an identical application from
being resubmitted through a fresh preview without checking the previous attempt.

## Command line preparation

The CLI prepares questions and answer drafts; it has no send command. For example:

```powershell
python run.py application 12
python run.py application 12 --draft
python run.py application 12 --questions questions.txt --draft
```

Use your existing `--data-dir` setting when working with a nondefault private data
folder. Keep CVs, profile facts, generated answers, selected documents and the
private database out of source control.

## Validation

`tests/test_application_preparation.py` verifies question extraction and grounded
draft validation without calling a live model or employer site.
`tests/test_browser_delivery.py` uses a fake browser and mocked network lookups to
exercise reviewed snapshots, request restrictions, attachment limits, changed
forms, explicit send tokens and one-attempt behavior. It does not send a real
application or claim to validate every browser/ATS implementation.
`tests/test_browser_integration.py` checks the same review boundary in real Chromium
using intercepted fixture pages: no employer server receives those test requests.
Set `RUN_BROWSER_TESTS=1` to include these optional browser checks.
