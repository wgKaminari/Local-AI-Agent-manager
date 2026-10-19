# Whole-market search: investigation and rework (0.6.0)

## The problem

A web search for a role such as "Linux DevOps" finds dozens of vacancies; the
app showed a handful. The investigation, run against the real database on
17 October 2026, found five causes.

| Cause | Evidence |
|---|---|
| **Only individual employers were searched.** All 92 configured sources were single companies (66 Greenhouse, 23 Ashby, ...), mostly US tech firms. Germany, the USA and Ukraine had no market-wide source at all. | The database held 165 German vacancies in total. Germany's federal job agency alone listed 1,351 "DevOps" and 294 "Linux DevOps" vacancies that day. |
| **NAV read about 3% of Norway per refresh.** The collector read 5 feed pages per run; 90 days of NAV's feed is about 169 pages (167,000 updates). Only titles matching the profile *at that moment* were kept, and the position then moved on, so skipped adverts were never revisited. NAV was also skipped whenever another country was selected. | NAV's saved position had not moved since 11 September. |
| **Search matched the whole phrase literally.** "Linux DevOps" only matched adverts containing exactly "linux devops". | 0 results, although 18 stored adverts contained both words. |
| **The search box never asked any site.** It filtered the local database only. | — |
| **Strict, diluted role matching.** Exact title phrases with few spellings; the skill score divided by all 17 profile skills, so a strong advert scored low. | — |

## The new design

```
                      saved searches + target roles (+ related roles)
                                         │
     ┌───────────────────────────────────┼───────────────────────────────────┐
     ▼                                   ▼                                   ▼
NAV full index                 Whole-market job sites                  Employer boards
(market-index.db:              Arbeitsagentur, Arbeitnow, Djinni,      Greenhouse, Lever, Ashby,
 every Norwegian title)        DOU, Remotive, Remote OK, Himalayas,    Workday, career sitemaps
     │                         opt-in LinkedIn public search                 │
     └────────── relevance check per query ── full advert loading ───────────┘
                                         │
                               vacancies.db (one record per site)
                                         │
                 word-by-word search · cross-site duplicate merging · role matching
```

* **Market providers** (`market.py`) share one interface: one search per query
  where the site supports it, otherwise one recent feed filtered locally. Every
  result is checked against the query that found it (`matches_query`), so loose
  site results do not flood Discover. Downloads run in parallel and never touch
  the database; each site is then saved as one transaction, where a single
  conflicting advert skips only itself.
* **NAV index** (`nav_index.py`): each refresh reads the feed to its tail into
  a separate, rebuildable SQLite cache. Searches match all indexed titles
  locally; matches are downloaded in full (4 parallel requests) and multi-word
  searches are confirmed in the advert text. Adverts are recorded as checked
  only after they are safely stored, so a failed run retries them.
* **Live search** (`service.market_search`): **Search job sites** on Discover
  searches every enabled site for the typed query at once.
* **Lazy adverts**: Bundesagentur für Arbeit and LinkedIn list no advert text;
  Djinni lists no employer. The app loads them when a vacancy is opened, and
  during refreshes for role matches within a small per-site budget.
* **Duplicates** across sites are merged for display only (title without gender
  markers, employer without legal suffix, first city). The employer's own
  posting is shown first; two postings on the same site stay separate.
* **Search** matches every word in any order, keeps quoted phrases, treats role
  spellings as the same search, and ranks titles naming the search first.
* **Matching** adds German/Norwegian/Ukrainian role spellings, "close roles"
  (MLOps, NLP, LLM, computer-vision engineers; decision scientists; quants) as
  related roles when the profile targets that field, and caps the skill score at
  five skills.

## Sites that forbid or block automation

| Site | Route in the app |
|---|---|
| LinkedIn | Opt-in public guest search: no login, honest User-Agent, 5 s between requests, at most 24 requests per refresh, one-hour pause on any refusal. Or job-alert emails. |
| FINN | Its terms forbid automated collection (robots.txt cites Norwegian copyright law). Saved-search alert emails via **Job alerts**; links keep their FINN code. |
| Indeed, StepStone, Glassdoor, robota.ua | Anti-bot protection (Cloudflare and similar). The app does not bypass it. Alert emails are recognised by their advert codes. |

## Measured on a copy of the real data (17 October 2026)

| | Before | After one refresh |
|---|---|---|
| German vacancies | 165 | 981 |
| "Data Scientist" in Germany | 16 | 100 |
| "For you" role matches in Germany | — | 379 |
| Ukrainian vacancies | 0 | 329 |
| Germany refresh | — | 50 s |
| Ukraine refresh | — | 38 s |

## Remaining limits

* The USA has no free, key-less national job API like NAV or the
  Bundesagentur. Since 0.7.5, US coverage comes from the US tech employers
  job site (below), Hacker News, remote boards, configured employer boards
  and the opt-in LinkedIn search. Free keyed APIs (Adzuna, Jooble, USAJobs)
  would add more and could be added later.
* NAV excludes FINN-origin adverts.
* Board adverts are not withdrawn explicitly; those not seen by any search for
  30 days leave Discover (saved ones stay).

## 0.7.0: collect everything, match in the database

Version 0.6.0 still discarded every job-site result that did not answer the
search that found it, so a new search or a changed profile could not use it.
Now collection and matching are separate stages:

1. **Collect broadly.** With *Collect everything in my fields* (default on),
   each refresh adds field-wide searches to your roles and saved searches:
   twenty German field terms at Bundesagentur für Arbeit (up to the API's
   10,000 results each, then incrementally by publication date per term),
   all IT categories at Djinni, DOU and Remotive, the newest Arbeitnow pages
   (until adverts already collected), field searches at Himalayas, and every
   active NAV advert. Everything in a supported country is kept and tagged
   with the searches it answers. Each site has a time budget; an unfinished
   site continues on the next refresh. LinkedIn stays search-only.
2. **Store derived data.** Schema version 4 adds `job_matches` (each job's
   match, countries and duplicate group, computed once) and `jobs_fts` (an
   FTS5 index). Triggers keep the index in sync and discard a stored match
   only when a job's content, stage or activity really changes; a refresh
   that merely sees a job again recomputes nothing. A saved profile change
   re-matches everything in the background.
3. **List from the database.** Views filter, count and sort stored results
   in SQL and load 80 records per page. Search combines the FTS index (word
   prefixes, phrases, whole short words) with a title substring check for
   German compounds, plus role aliases and the job-site tags.
4. **Clean up.** After every refresh, vacancies withdrawn or retired by their
   source, job-site adverts no search has seen, and adverts past their
   deadline are removed after 30 days, unless you saved, staged, annotated,
   drafted, prepared, sent, added or imported them.

### Measured on a copy of the real data (18 October 2026)

| | Before (0.6.0 data) | After one collect-everything refresh per country |
|---|---|---|
| Germany | 165 | 52,502 (refresh 9 min; Bundesagentur 51,503) |
| Norway | 266 | 9,537 (refresh 10 min; 9,240 full NAV adverts, 538 left for the next refresh) |
| Ukraine | 0 | 2,920 (refresh 1 min; Djinni 1,894, DOU 712) |
| Active vacancies | 3,869 | 68,221 |
| Data files | 56 MB | 313 MB |

Upgrading the existing database to schema 4 and storing its first matches
took 5.5 s for 4,249 vacancies. On 68,221 vacancies, a Discover page took
0.76 s, Germany 0.6 s, For you 0.66 s and a search about 0.25 s; the
interface opened in 1.6 s. The previous design needed 2 s per list for
4,600 vacancies.

## US tech employers

Norway and Germany publish nearly every vacancy through free government
APIs (NAV, Bundesagentur für Arbeit); the USA does not, and Indeed,
LinkedIn, Glassdoor and ZipRecruiter forbid automated collection. In 0.7.4
the USA therefore had about 3,900 vacancies against 52,800 in Germany.

0.7.5 adds two job sites, both on by default:

* **US tech employers** reads 332 public career boards of US employers that
  are not already in the employer directory: 286 Greenhouse, Lever and Ashby
  boards (OpenAI, Anthropic, Databricks, Stripe, SpaceX, Anduril, Waymo, ...)
  and 46 Workday boards (NVIDIA, Capital One, Visa, Disney, Johnson &
  Johnson, ...). The list lives in `data/us_employer_boards.json`; each board
  was downloaded with the app's own collector on 19 October 2026, and slugs
  that belonged to a different company (for example a veterinary clinic
  called Archer) were removed. Greenhouse, Lever and Ashby boards are read
  whole and retire postings they no longer list. Workday has no whole-board
  feed, so each Workday employer is searched for your first two roles (25
  adverts each). Jobs in any supported country are kept, so a US company's
  Berlin office counts for Germany.
* **Hacker News: Who is hiring?** reads the latest two monthly threads
  through HN's official search API (two requests per thread). The first line
  of each post gives company, role and place; posts naming no supported
  country are skipped, and a fully remote post naming no region counts as
  open everywhere, like the remote boards.

Boards are a job site rather than configured sources, so they do not count
toward the 250-source limit, need no setup, and updates can add boards.
Boards you configured yourself are skipped here. Each board is read at most
once every 20 hours: a full pass downloads about 450 MB (SpaceX and Anduril
publish over 20 MB each, so the download limit for whole boards is now
48 MB). A refresh that runs out of its 10-minute budget continues with the
boards it did not reach. Workday and Hacker News adverts that no refresh has
seen for 30 days leave Discover, like other job-site adverts.

### Measured on a copy of the real data (19 October 2026)

One refresh across Norway, Germany, Ukraine and the USA, 14 minutes:

| | Before | After |
|---|---|---|
| US vacancies | 3,543 | 27,320 |
| US "For you" (fit your experience) | 306 | 1,973 |
| US tech employers | — | 306 of 333 boards read, 23,728 vacancies kept |
| Hacker News | — | 215 posts |
| All countries "For you" | ~1,500 | 3,230 |

## 0.7.6: all of Djinni, and Adzuna for the USA

**Djinni.** Its RSS feed returns the newest 100 jobs for any filter and
ignores paging, so 36 keyword searches reached 1,892 of the 6,172 jobs on the
site. Djinni's job categories split the board: 157 categories including
sub-categories (a parent such as JavaScript does not include React.js). A
category that fills its 100-job feed is split by experience level, and a
level that is still full by region. A live run read 415 feeds in 5.6 minutes
and found 6,136 unique jobs. The full crawl runs at most once every 20 hours;
refreshes in between read the newest-jobs feed and your role searches.

**Adzuna.** The only free whole-market US source; it needs the user's own
key (developer.adzuna.com). The key is entered under Settings & activity →
Job sites, checked with one search, saved in `adzuna-key.json` (not in
sources.json or backups) and never sent back to the page, which shows only
the last characters of the Application ID. Adzuna's terms allow 25 requests a
minute, 250 a day, 1,000 a week and 2,500 a month by default; the app stays
under 90% of each, counts requests in market-state.json, pauses 2.6 seconds
between requests, and uses at most 40 per refresh and 2 per live search.
Searches cover your saved searches and roles in the USA, adverts from the
last 30 days, newest first, 50 per page. Results carry an excerpt of the
advert and link to Adzuna, which the terms ask to credit as the source.
