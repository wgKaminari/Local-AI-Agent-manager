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

* The USA has no free, key-less national job API; US coverage comes from
  employer boards, remote boards and the opt-in LinkedIn search. Free keyed
  APIs (Adzuna, Jooble, USAJobs) would add more and could be added later.
* NAV excludes FINN-origin adverts.
* Board adverts are not withdrawn explicitly; those not seen by any search for
  30 days leave Discover (saved ones stay).
