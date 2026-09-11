# Gmail job alerts

The app can turn LinkedIn and other job-alert emails into individual opportunities for review. It reads matching mail, extracts vacancy links and short excerpts, and lets you choose what enters your database. It does not send mail, mark messages as read, change labels, delete messages, apply for jobs, or visit links while parsing. Email import does not require Ollama or a paid AI API.

## Start with a downloaded email

Save a job-alert message from Gmail as an `.eml` file, then use **Import .eml** in the app's Gmail page. Review the detected opportunities and add the ones you want. This works without a Google Cloud project or account connection. Attachments and remote images are ignored; the email size limit is 5 MB.

## Connect your Gmail account

You complete Google sign-in and consent in your own browser. Do not paste your password or OAuth JSON into chat.

1. Create or select a personal project in [Google Cloud Console](https://console.cloud.google.com/), and enable the **Gmail API**. Google provides the corresponding [Gmail API setup instructions](https://developers.google.com/workspace/gmail/api/quickstart/python).
2. Under **Google Auth platform → Branding**, configure an app named `Norway Job Agent` with your support/contact email. For a personal Gmail account, choose **External** under Audience, retain **Testing**, and add your own Gmail address as a test user. See Google's [consent-screen guide](https://developers.google.com/workspace/guides/configure-oauth-consent).
3. Under **Data Access**, add only `https://www.googleapis.com/auth/gmail.readonly`. This permission can read your mailbox; the app's search query limits what it actually requests. Google classifies it as a restricted scope. See [Gmail scopes](https://developers.google.com/workspace/gmail/api/auth/scopes).
4. Under **Clients**, create an OAuth client with application type **Desktop app**, and download its JSON file. A web client, API key or service-account file will not work. Store the JSON in a private local folder.
5. In the app's Gmail page, choose **Connect Gmail**, select that JSON file, select the correct Google account in your browser, and review the read-only consent. Return to the app after Google's response. The connection times out after three minutes; retry if needed.

This uses Google's [desktop OAuth flow](https://developers.google.com/identity/protocols/oauth2/native-app), with a temporary listener on `127.0.0.1`, random state validation and PKCE. It does not run a public web server. The app includes its own standard-library Gmail client, so you do not need to install Google's sample Python packages to use this feature.

## Find useful alerts

The default search covers 90 days of LinkedIn and common job-alert subjects. You can narrow it to a sender or a Gmail label, or widen the date range. Example queries:

```text
newer_than:30d from:linkedin.com
label:job-alerts newer_than:90d
newer_than:180d {subject:"job alert" subject:vacancy subject:stillingsvarsel}
```

Read a batch, review the extracted roles, and add selected opportunities. Continue to the next batch for older results. Changing the query starts a new search. Repeating a search does not create another database vacancy for the same identifiable job URL. If a message fails, restart that search to retry it; the results explain partial failures.

Matching uses your saved target roles, related roles and skills. It is conservative: an email with no identifiable individual vacancy link may yield no candidates. A digest can yield several vacancies. Company and location are extracted when explicitly labelled; missing details remain unknown. Excerpts are incomplete and should be checked against the original vacancy before preparing an application. LinkedIn job links are imported from alerts; the app does not scrape your LinkedIn account.

## Stored data and connection issues

Only opportunities you add, their short excerpts and email provenance are saved to the vacancy database. The original inbox is not copied into it. Pending results remain in memory. Windows encrypts saved OAuth tokens with DPAPI for the current user; other systems use a local file with owner-only permissions. Keep your downloaded OAuth JSON private as well.

**Disconnect** forgets the app's local token and keeps imported opportunities. To revoke the Google grant too, remove the app through your Google Account's third-party connections settings.

External apps in **Testing** normally receive refresh tokens that expire after seven days. Reconnect when prompted. Google documents this in [OAuth token expiration](https://developers.google.com/identity/protocols/oauth2#expiration). An access-denied error can also mean the account was not added as a test user, Gmail API is disabled, consent was denied, or a Workspace administrator blocks access.

Gmail provenance links use a thread ID and the browser's default Gmail account (`u/0`). If you have several browser accounts and connected a different one, switch to that account in Gmail and find the message using its saved subject/date. The app does not infer account identity from email headers.

## Verification

`python -m unittest discover -s tests -p test_gmail.py -v` checks digest separation, tracking-link decoding, offline `.eml` import, malformed messages, OAuth state/PKCE, token refresh, read-only HTTP requests, failure recovery, and Windows credential encryption using synthetic data. The Windows encryption check needs a normal Windows user context; an application sandbox that blocks DPAPI cannot perform that one check. No test signs in to Google or reads a real mailbox.
