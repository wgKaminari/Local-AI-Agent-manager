# Norway and Ukraine job-source research

Research started 2 October 2026; collector smoke checks and corrections completed 3 October 2026. This is a curated coverage shortlist, not a measured traffic ranking. Provider claims such as “number one” are not treated as independent evidence of popularity.

## What the catalog contains

| Country | Employer directory entries | Configured employer collectors | General/search sources |
| --- | ---: | ---: | ---: |
| Norway | 69 | 10 existing collectors, rechecked | 10 |
| Ukraine | 67 | 10 newly configured and checked | 11 |

The 136 employer entries are a research directory; they are **not 136 automated integrations**. Twenty employer collectors have passed runtime smoke checks. The general-source directory links open in the browser; NAV additionally has the existing incremental feed adapter, while the other 20 entries use browser, alert-email or manual-text routes. The final merged application catalog also contains employers contributed by the USA/Germany directory.

The machine-readable records are [employers_no_ua.json](../src/norway_job_agent/data/employers_no_ua.json) and [job_boards_no_ua.json](../src/norway_job_agent/data/job_boards_no_ua.json). Each includes country, sector/category, official or employer-hosted career URL, access notes, check date and evidence. A country tag identifies a discovery market; it does not assert that every role is in that country or that a candidate is eligible.

## Norway source shortlist

NAV and FINN are the first general-search routes because their official searches expose broad Norwegian vacancies. [NAV](https://arbeidsplassen.nav.no/stillinger) provides occupation, municipality, language, experience and work-pattern filters; [FINN Jobb](https://www.finn.no/job/search) exposes Norwegian geographic search. The other entries complement that coverage by employer type or occupation.

| Source | Main use | Integration in this version |
| --- | --- | --- |
| [NAV Arbeidsplassen](https://arbeidsplassen.nav.no/stillinger) | Public nationwide job search, including working-language filters | Existing resumable NAV feed; also browser search |
| [FINN Jobb](https://www.finn.no/job/search) | Broad Norwegian employer listings | Manual import/alerts |
| [Jobbnorge](https://www.jobbnorge.no/search/en) | Public sector, university, research and other employers | Browser search and manual import |
| [The Hub Norway](https://thehub.io/jobs?countryCode=NO) | Startup opportunities with country filtering | Browser search/alerts |
| [Indeed Norway](https://no.indeed.com/) | Additional general search coverage | Browser search/alerts |
| [LinkedIn Jobs](https://www.linkedin.com/jobs/search/?location=Norway) | Professional jobs and employer discovery | Browser search/alerts |
| [Manpower Norway](https://www.manpower.no/nb) | Agency and temporary jobs | Browser search/manual import |
| [Adecco Norway](https://www.adecco.com/nb-no) | Agency and temporary jobs | Browser search/manual import |
| [Academic Work](https://www.academicwork.no/) | Early-career and professional opportunities | Browser search/manual import |
| [kode24](https://www.kode24.no/jobb) | Software/developer vacancies | Indexed official page retained; browser review required |

NAV is already configured by default in the app. Its existing collector keeps a persistent checkpoint and handles withdrawals, starts with 90 days of updates and processes at most five pages per collection. The public token is experimental; obtain a personal consumer token from NAV for ongoing use. NAV excludes FINN-origin vacancies. See [NAV's feed documentation](https://navikt.github.io/pam-stilling-feed/).

The Norway employer directory covers energy and engineering, construction, transport, seafood, food production, retail, hospitality, finance, telecom, software, public media and research. It includes Equinor, DNV, Tieto, AutoStore, Cognite, Bekk, Netlight, Bouvet, Sopra Steria, KONGSBERG, Aker Solutions, Hydro, Yara, Elkem, Jotun, Orkla, TINE, Mowi, Lerøy, SalMar, Veidekke, NCC, AF Gruppen, Norconsult, Multiconsult, Ramboll, Asplan Viak, OBOS, Vy, Bane NOR, Avinor, Posten Bring, Norwegian, SAS, Scandic, Strawberry, Thon, NorgesGruppen, Coop, REMA 1000, DNB, Nordea, Storebrand, Gjensidige, Tryg, If, Telenor, Telia, Atea, Itera, Computas, Knowit, Visma, NRK, SINTEF and NTNU. Exact entries and links are in the JSON catalog; some group career pages require selecting Norway on the employer's site.

## Ukraine source shortlist

[Work.ua](https://www.work.ua/about-us/) and [robota.ua](https://robota.ua/?goHome=true) provide broad Ukrainian searches. [DOU](https://jobs.dou.ua/) and [Djinni](https://djinni.co/jobs/) add technology-focused discovery. The [State Employment Service's explanation of its unified vacancy portal](https://vin.dcz.gov.ua/novyna/shukayete-robotu-pochnit-z-yedynogo-portalu-vakansiy) confirms its official search destination. Portal aggregation means duplicate vacancy detection matters.

| Source | Main use | Integration in this version |
| --- | --- | --- |
| [Work.ua](https://www.work.ua/) | General vacancies across Ukrainian cities and sectors | Browser search/alerts/manual import |
| [robota.ua](https://robota.ua/) | General vacancies and remote search | Browser search/alerts/manual import |
| [DOU Jobs](https://jobs.dou.ua/) | Technology vacancies and employer discovery | Browser/manual import |
| [Djinni](https://djinni.co/jobs/) | Technology jobs with country/remote filters | Browser/manual import; account messaging stays on Djinni |
| [Happy Monday](https://happymonday.ua/jobs-search) | Professional, creative, nonprofit and junior opportunities | Browser/manual import |
| [State Employment Service](https://www.dcz.gov.ua/job) | Public unified vacancy search | Browser/manual import; no verified feed connector |
| [Jobs.ua](https://jobs.ua/) | Additional general vacancies | Browser/manual import |
| [Jooble Ukraine](https://ua.jooble.org/) | Aggregated job discovery | Browser/alerts; check original employer posting |
| [Lobby X](https://thelobbyx.com/) | Civil society, public-interest and specialist opportunities | Browser/manual import; inspect the role's actual sector |
| [OLX Jobs](https://www.olx.ua/uk/rabota/) | Local, service, retail and operational opportunities | Browser/manual import |
| [LinkedIn Jobs](https://www.linkedin.com/jobs/search/?location=Ukraine) | Professional jobs and employer discovery | Browser search/alerts |

The Ukraine employer directory includes technology/product firms and broader sectors: Ajax Systems, Readdle, appflame, HealthJoy, Enavate, SevenPro, eduki, Tatari, airSlate, Boosta, SoftServe, EPAM, GlobalLogic, Intellias, N-iX, ELEKS, Ciklum, Luxoft, DataArt, Miratech, Sigma Software, Intetics, Levi9, ZONE3000, Genesis, BetterMe, MacPaw, Preply, Reface, SKELAR, Headway, OBRIO, Universe, Jooble, Work.ua, Rozetka, EVO, Nova Poshta, Ukrposhta, Kyivstar, Vodafone, lifecell, PrivatBank, Oschadbank, Raiffeisen, PUMB, DTEK, Naftogaz, Metinvest, Interpipe, Kernel, MHP, Nibulon, Astarta, Farmak, Darnytsia, Arterium, Silpo, Fozzy Group, EVA, Comfy, Nestlé, Carlsberg, Coca-Cola HBC, Ubisoft, Plarium and Wargaming.

Fozzy Group is explicitly a hiring-contact directory entry because the checked [official contact page](https://fozzy.ua/ua/contacts/) identifies its personnel department; it is not counted as a vacancies feed. Reface's link was corrected to its [official hiring page](https://reface.ai/hiring).

## Collector smoke-check evidence

Checks used the application's actual `fetch_greenhouse`, `fetch_lever` and `fetch_company_source` functions, not just HTTP reachability. They were read-only and sent no applications. Observations below are point-in-time counts; they will change. A location match is a discovery hint, not an eligibility assessment.

### Newly enabled Ukraine collectors

| Employer / public board | Adapter and board ID | Jobs observed | Ukraine location hints |
| --- | --- | ---: | ---: |
| [Readdle](https://job-boards.eu.greenhouse.io/readdle70) | Greenhouse `readdle70` | 9 | 5 |
| [appflame](https://job-boards.eu.greenhouse.io/appflame) | Greenhouse `appflame` | 15 | 14 |
| [HealthJoy](https://job-boards.greenhouse.io/healthjoy) | Greenhouse `healthjoy` | 5 | 3 |
| [Enavate](https://job-boards.greenhouse.io/enavatecareers) | Greenhouse `enavatecareers` | 9 | 1 |
| [SevenPro](https://job-boards.eu.greenhouse.io/sevenpro) | Greenhouse `sevenpro` | 7 | 5 |
| [eduki](https://job-boards.eu.greenhouse.io/eduki) | Greenhouse `eduki` | 3 | 3 |
| [Tatari](https://job-boards.greenhouse.io/tatari) | Greenhouse `tatari` | 53 | 3 |
| [Boosta](https://job-boards.eu.greenhouse.io/boosta) | Greenhouse `boosta` | 52 | 52 |
| [Ajax Systems](https://jobs.lever.co/ajax) | Lever `ajax`, global API | 197 | 104 |
| [airSlate](https://jobs.lever.co/airslate) | Lever `airslate`, global API | 13 | 1 |

Greenhouse boards were fetched using `https://boards-api.greenhouse.io/v1/boards/{board}` and `/jobs?content=true`, including boards whose public career pages use the EU host. Each listed board worked with the application's existing API adapter. Lever used `https://api.lever.co/v0/postings/{board}?mode=json&limit=100&skip=...`; Ajax exercised more than one page. Location filters include English/Ukrainian country and city spellings. “Remote” alone is not treated as Ukraine eligibility.

### Existing Norway collectors rechecked

| Employer | Probe scope | Jobs read / Norway location hints |
| --- | --- | --- |
| Bekk | Complete public Lever board | 10 / 10 |
| Cognite | Complete public Greenhouse board | 47 / 10 |
| Crayon | Complete public Greenhouse board | 0 / 0, valid empty board |
| Netlight | Complete public Lever board | 36 / 3 |
| AutoStore | First 2 of 31 public Workday search results | 2 / 1 |
| Equinor | First 2 of 10 public Workday search results | 2 / 1 |
| Bouvet | First 2 of 43 matching public listing links | 2 / 2 |
| DNV | First 2 of 58 matching sitemap links | 2 / 2 |
| Sopra Steria | First 2 of 10 matching public listing links | 2 / 2 |
| Tieto | First 2 of 34 matching sitemap links | 2 / 2 |

The six bounded probes temporarily reduced the read limit to two; their saved configurations retain the previous maximum of 60. Workday applies geographic filtering after its bounded search. DNV and Tieto only scan sitemap paths containing `norway`; other URL patterns are outside coverage. Listing-page scans can miss jobs on further pages. Successful smoke checks do not imply complete crawling.

## Verification meaning and remaining limits

- `collector_verified`: the actual application adapter completed. Read the detail for full-board versus bounded scope. Norway configurations are inherited from the original catalog; Ukraine configurations are in the JSON data.
- `official_page_reviewed`: an official/employer-hosted page response and title/heading, or an official page's indexed content, established the link's purpose. This does not verify all vacancies or provide collection support.
- `official_search_result`: the official site's indexed evidence establishes identity/scope; it may be stale.
- `access_limited`: blocked response, timeout, redirect loop, certificate error, challenge page or other access limitation. The link is retained for browser review; page content was not verified by that check.
- `page_response_only`: a response arrived without enough extracted content to verify the career page. No collector is configured.

The employer file retains 28 access-limited entries and three response-only entries rather than presenting them as working collectors. HTTP 200 challenge pages from Atea, Vodafone and Coca-Cola HBC are treated as access-limited. TLS checks were not disabled. No credentials, login bypasses or candidate accounts were used.

The employer directory and general-search shortlist can grow independently of automated adapters. Adding a public career link does not create an integration; adding an adapter requires validating its response schema, pagination, geography, withdrawal behavior where applicable, and current access constraints. Application-form extraction and delivery are a separate capability; a job-board collector does not automatically support sending applications to that employer.
