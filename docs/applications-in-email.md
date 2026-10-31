# Your applications in Gmail

**Job alerts → Your applications in Gmail** finds the applications you have already sent, from the mail they left behind: employers' confirmations ("Thank you for applying"), interview invitations and rejections, and applications you emailed yourself. It counts them, matches them to the vacancies in your list and marks those vacancies, so you can see at a glance that you already applied for a position.

It uses your existing read-only Gmail connection (connect it once under **Job alerts**). Nothing in your mailbox changes: no message is sent, labelled, moved or marked as read, and no link in a message is opened.

## Scanning

Choose how far back to read (3 months to 2 years) and **Scan my email**. One Gmail search finds application mail: subjects such as "application", "interview", "Bewerbung", "søknad" or "відгук", and mail from applicant-tracking systems (Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Teamtailor, Personio, Recruitee, Webcruiter, Jobylon, Djinni and others). Up to 500 messages are read per scan; **Scan for new mail** later reads only messages it has not read before.

Each message is reduced to: the outcome (applied, interview or rejected), the employer, the position, the date, the subject and a short excerpt (1,500 characters, links removed), stored in your local database. Newsletters and job alerts are skipped: mailing-list mail cannot count as an interview invitation just because it says "interview".

The rules read English, Norwegian, German and Ukrainian (and Russian) wording. The employer comes from the message ("…applying to the Data Scientist position at Acme", "hos Statens vegvesen"), else from the sender: a company's own domain counts more than a display name, which is often a recruiter's own name. Applicant-tracking systems and mail providers never count as the employer. Djinni hides employers, so its mail names only the position.

**Read unclear emails with AI** passes mail whose employer or position the rules could not find to the AI you chose under **Settings & activity → AI assistant** (your local model, or a cloud provider, which then receives those emails' subject, sender and excerpt). An employer or position the AI names is kept only if it appears in that email; mail the AI judges not to be an application is set aside.

## What you see

- **Counts**: applications, waiting for a reply, interviews, rejections and the response rate (interviews and rejections out of all applications), plus applications per month.
- **Applications found**: one row per application. Messages about the same employer and position count once, and a reply that names no position counts as the answer to your latest application to that employer. The outcome is the latest news: a rejection or invitation after a confirmation.
- **On your vacancies**: a vacancy you applied for shows the outcome and its date on its card (for example "Rejected 1 Oct") while its stage is still New, Saved, Preparing or Ready, and its Overview says "Your email shows you applied for this job", with the messages. A vacancy at an employer you applied to before lists those earlier applications. Matching needs the same employer (also "Nordic Fixture" against nordicfixture.com) and the same position; newly collected vacancies are matched after every refresh.
- **Update your stages**: vacancies in your list whose mail has news are listed with the stage it suggests (New → Applied, Applied → Interview, … → Rejected). Nothing changes until you tick them and choose **Update selected stages**. A stage is never moved backwards, and archived vacancies are left alone.
- **Add to Applications** turns an application found only in your mail into a vacancy in your list, at its stage, linked to the Gmail thread.

**Forget results** deletes what the scan stored. Your mailbox and vacancies are not changed.

## Smarter job-alert import

When **Find job alerts** reads an alert email but its rules find no vacancy in it (an unusual layout, a site the rules do not know), the review list offers **Read with AI**. The AI reads up to 10 such emails at a time and lists their vacancies for review like any other. It chooses each vacancy's link by number from the email's own job links, so it cannot invent an address, and a title must appear in the email. Nothing is added until you select it and choose **Add selected**.
