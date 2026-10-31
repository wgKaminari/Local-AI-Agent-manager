# AI assistant

Job Agent's AI writes cover letters and application answers, suggests profile facts from your CV, analyses vacancies, prepares interviews, drafts replies to employers and answers in **AI chat**. It can run on your free local model (Ollama) or on a cloud provider with your own API key: **Claude** (Anthropic), **ChatGPT** (OpenAI), **Gemini** (Google) or **DeepSeek**.

## Choosing the AI

Open **Settings & activity → AI assistant**.

- **Local model (Ollama)** is the default. It is free and private: it runs on this computer and sends nothing anywhere. It is slower and writes less well than the cloud models.
- **A cloud provider** writes better and faster, and charges your account per use. To add one:
  1. Create an API key on the provider's site (each provider row has a **Get an API key** link).
  2. Paste it and choose **Check and save key**. The key is checked with one free request (listing the models your key can use).
  3. Choose the provider. The app asks you once to confirm what is sent to it (below).
  4. Pick a model. **Refresh model list** shows every chat model your key can use.

You can switch back to the local model at any time, here or with the picker at the top of AI chat.

Chat subscriptions (ChatGPT Plus or Pro, Claude Pro, Gemini Advanced) do not include API use: the API is paid separately per use, from credit you add on the provider's site.

| Provider | Default model | Notes |
|---|---|---|
| Claude | `claude-opus-5-5` | Writes best. `claude-sonnet-5-5` and `claude-haiku-5-5` cost less. If the chosen model declines a request, Anthropic's server-side fallback lets another Claude model answer it. |
| ChatGPT | `gpt-6.1-terra` | A fast, efficient model for writing. `gpt-6.1-sol` and `gpt-6-astra` are stronger and cost more. Requests are sent with `store: false`. |
| Gemini | `gemini-3.8-flash` | Google AI Studio has a free tier with daily limits. Google may use free-tier requests to improve its products; requests on a paid plan are not used that way. |
| DeepSeek | `deepseek-flash` | The lowest prices. DeepSeek stores requests on servers in China. |

## What is sent, and what is kept

With a cloud provider, each AI request sends that provider:

- your saved profile and CV text (in chat, only while **Use my profile** is ticked),
- the vacancy you are working on,
- your chat messages, and any email you paste for a reply draft.

Requests go straight from this computer to the provider; nothing passes through any other server. The local model sends nothing anywhere.

**Keys** are saved in `ai-keys.json` in your data folder, encrypted for your Windows account (DPAPI). They are never stored in `sources.json` or backups, never written to logs, and never sent back to the page: it only learns whether a key is saved and its last four characters. Adding or removing a key happens on the PC, not from a paired phone. Provider error messages are shown with anything that looks like a key removed.

**Analyses, interview preparation and chats** are saved in your local database. A vacancy you analysed or chatted about is kept by the "IT jobs only" clean-up, like one you saved.

## Costs and the monthly limit

The panel shows the tokens used with cloud AI this month, per provider. **Monthly limit** stops cloud requests once the month's tokens reach it (default 1,000,000; 0 means no limit). It counts input and output tokens, including the "thinking" tokens that reasoning models are billed for. Rough sizes:

- a cover letter, an analysis or interview preparation: 5,000–20,000 tokens,
- a chat answer: 2,000–10,000 tokens, growing as a conversation gets longer (up to about 60,000 characters of recent history is sent with each message; start a new chat for a new topic).

Prices change, so check the provider's page. As a guide, Claude Opus 5.5 costs $4 per million input tokens and $20 per million output tokens, Claude Haiku 5.5 $0.10 and $0.50. The app's limit is a safety net; set a spending limit on the provider's billing page as well.

## The features

**AI insights** (a tab on every vacancy):

- **Analyse vacancy**: a fit verdict (strong, good, stretch or weak) with the reason, what you meet, gaps, red flags in the advert, and what to put first in your application. **Key facts** come from the advert only: salary, remote policy, visa and relocation help, languages, seniority, employment type, deadline, contact and tech stack. A salary whose numbers do not appear in the advert, a contact email the advert does not contain or a technology it never names is dropped, so the AI cannot invent them. Salary, remote policy, visa help and languages also show on the Overview tab.
- **Interview prep**: likely questions, why they ask each one, and an answer outline built from your profile. Quotes from your profile are checked word for word; quotes that are not in it are dropped. Also: questions you could ask them and topics worth revising.

**AI chat** (in the sidebar, or **More** on a phone): conversations are saved on this computer. **Ask AI** on a vacancy starts a chat about it. The answer appears while it is written and is only saved once complete; if it fails, your message stays in the box. Answers show bold, lists, headings and links; everything else is shown as plain text.

**Draft a reply** (on AI insights, in AI chat, under Job alerts and in Quick actions): paste an email from an employer or recruiter and, optionally, what you want to say. The AI drafts a reply in your voice with a subject line and a list of things to fill in or check. Nothing is sent: you copy it into your email yourself. Availability, dates and salary figures are left as placeholders such as `[your available times]` instead of being invented.

**Cover letters, application answers and Suggest profile** use the AI you chose, with the same checks as before: evidence must quote your profile word for word, language levels are never raised, gaps go to your review notes instead of the letter, and a draft that fails a check is retried (up to three attempts) and never shown.

## Safety rules the AI follows

Vacancy text, emails and pasted content are treated as untrusted data: instructions inside them are ignored. The AI may not invent skills, degrees, dates, experience or language levels, and nationality or residence never counts as work authorization. It has no tools: it cannot browse, send email, fill forms or apply. AI output is a draft for you to check.

## Troubleshooting

- **"did not accept the API key"**: the key was mistyped, revoked or belongs to another project. Create a new key and add it again.
- **"no credit left"**: add credit on the provider's billing page, or switch to the local model.
- **"does not offer the model … to your key"**: choose another model; **Refresh model list** shows what your key can use.
- **"is busy" / "limiting how fast"**: the app already retried twice. Wait a minute and try again.
- **"reached its length limit"**: ask for something shorter. No partial answer is kept.
- **"This month's cloud AI limit … is used up"**: raise the limit or switch to the local model.

API references: [Claude Messages API](https://platform.claude.com/docs/en/api/messages), [OpenAI Responses API](https://developers.openai.com/api/docs/api-reference/responses), [Gemini generateContent](https://ai.google.dev/api/generate-content), [DeepSeek chat completions](https://api-docs.deepseek.com/api/create-chat-completion).
