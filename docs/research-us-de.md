# USA and Germany job-source research

Research and public-collector checks completed 3 October 2026. This is a curated coverage shortlist across sectors, not a measured traffic ranking. The records retain the official URLs and the verification evidence used to distinguish a useful career link from a working integration.

## Coverage and counting

| Country | Employer directory entries | Configured employer collectors | General/search sources |
| --- | ---: | ---: | ---: |
| USA | 89 | 15 | 13 |
| Germany | 80 | 12 | 12 |
| Norway | 69 | 10 | 10 |
| Ukraine | 67 | 10 | 11 |

The USA/Germany file contains **158 employer entries**, including 11 entries shared by both markets. It supplies **16 distinct automated Greenhouse collectors** and 142 manual career links. Its 25 general/search sources are manual routes. Country totals therefore cannot be added to count distinct records or integrations.

Across all four countries, the merged catalog contains **294 employer records and 46 general/search records**, with **36 distinct configured employer collectors**. There are no repeated employer names within a country's directory. Globally, Accenture, Google and Microsoft each retain separate Norway and USA records; 294 records represent 291 distinct display names, not a claim about independent corporate groups. Subsidiaries and country operations are intentionally retained when they provide useful hiring destinations.

The machine-readable sources are [employers_us_de.json](../src/norway_job_agent/data/employers_us_de.json) and [job_boards_us_de.json](../src/norway_job_agent/data/job_boards_us_de.json). For Norway/Ukraine evidence and the limits of bounded Workday and career-page scans, see [research-no-ua.md](research-no-ua.md). Counts describe the checked catalog snapshot, not the number of live vacancies.

## USA shortlist

The public starting points are CareerOneStop and USAJOBS: [USAGov's job-search guide](https://www.usa.gov/job-help) links to CareerOneStop's general and state searches and identifies USAJOBS as the central federal jobs destination. General boards add employer coverage; specialist boards add technology, startup, nonprofit and academic opportunities.

All entries below open the source in the browser. Relevant vacancy text, supported public vacancy URLs, or alert emails can be imported; adding these directory entries does not enable automatic board crawling.

| Source | Main use |
| --- | --- |
| [USAJOBS](https://www.usajobs.gov/) | Federal government vacancies; check each announcement's eligibility and application instructions |
| [CareerOneStop Job Finder](https://www.careeronestop.org/JobSearch/FindJobs/find-jobs.aspx) | General/local search, linked by USAGov; automated research access returned 403 |
| [Indeed USA](https://www.indeed.com/) | Broad job discovery; explicitly select the USA if the site chooses another locale |
| [LinkedIn Jobs USA](https://www.linkedin.com/jobs/search/?location=United%20States) | Professional jobs and employer discovery; account-specific applications stay on LinkedIn |
| [ZipRecruiter](https://www.ziprecruiter.com/) | General vacancies |
| [Glassdoor Jobs](https://www.glassdoor.com/Job/index.htm) | General vacancies and employer discovery |
| [Dice](https://www.dice.com/) | Technology vacancies |
| [Built In](https://builtin.com/jobs) | Technology employers and vacancies |
| [Wellfound](https://wellfound.com/jobs) | Startup vacancies |
| [Idealist](https://www.idealist.org/en/jobs) | Nonprofit and public-interest opportunities |
| [Inside Higher Ed Careers](https://careers.insidehighered.com/) | Higher-education opportunities |
| [Monster](https://www.monster.com/) | Additional general vacancies |
| [SimplyHired](https://www.simplyhired.com/) | Aggregated discovery; inspect the original vacancy before applying |

## Germany shortlist

[Bundesagentur für Arbeit Jobsuche](https://www.arbeitsagentur.de/jobsuche/) supports occupation and geographic search across sectors. [Make it in Germany](https://www.make-it-in-germany.com/en/working-in-germany/job-listings) provides an official multilingual entry point with sector and federal-state filters. Neither listing inclusion nor a country filter proves visa sponsorship, language suitability or eligibility.

| Source | Main use |
| --- | --- |
| [Bundesagentur für Arbeit Jobsuche](https://www.arbeitsagentur.de/jobsuche/) | Public nationwide search, including apprenticeships |
| [Stepstone Germany](https://www.stepstone.de/) | General/professional vacancies |
| [Indeed Germany](https://de.indeed.com/) | Broad job discovery; German edition confirmed in the [official worldwide directory](https://www.indeed.com/worldwide) |
| [LinkedIn Jobs Germany](https://www.linkedin.com/jobs/search/?location=Germany) | Professional jobs and employer discovery |
| [XING Jobs](https://www.xing.com/jobs) | Professional vacancies |
| [Make it in Germany](https://www.make-it-in-germany.com/en/working-in-germany/job-listings) | Official multilingual job listings |
| [INTERAMT](https://interamt.de/) | Public-service vacancies; direct research navigation reached a redirect limit |
| [service.bund.de](https://www.service.bund.de/Content/DE/Stellen/Suche/Formular.html) | Public-sector vacancy search; direct search access returned 403 |
| [stellenanzeigen.de](https://www.stellenanzeigen.de/) | General vacancies |
| [Jobware](https://www.jobware.de/) | General/professional vacancies |
| [heise jobs](https://jobs.heise.de/) | Technology vacancies |
| [academics](https://www.academics.de/) | Research and academic opportunities |

These are browser/manual-import routes in this version. INTERAMT and service.bund.de retain official indexed or publisher evidence rather than being labeled verified automatic feeds.

## Employer coverage

The USA directory spans technology, semiconductors, consulting, financial services, healthcare, pharmaceuticals, manufacturing, aerospace, energy, retail, logistics, hospitality, media and telecommunications. Germany coverage additionally emphasizes automotive, chemicals, industrial manufacturing, public transport and research. Every employer entry carries sector tags so the directory can expand without tying it to one candidate's job profile.

A global career portal is a discovery destination: use its country filters and inspect the actual vacancy. The 142 manual USA/Germany entries have stored HTTP 200 response/title evidence from official career pages. This establishes the checked page's identity and purpose; it does not verify available jobs, pagination or application support. Application forms and submission methods must be inspected separately.

## Automated collector evidence

The saved evidence records actual application `fetch_greenhouse` checks, including the board identity endpoint and `/jobs?content=true`. Sixteen boards returned public jobs successfully; only countries with observed location hints were assigned to each board. The checks were read-only and sent no applications.

| Employer / board | All jobs observed | USA location hints | Germany location hints |
| --- | ---: | ---: | ---: |
| [Asana](https://job-boards.greenhouse.io/asana) | 97 | 49 | 2 |
| [Cockroach Labs](https://job-boards.greenhouse.io/cockroachlabs) | 19 | 13 | 0 |
| [commercetools](https://job-boards.greenhouse.io/commercetools) | 37 | 9 | 16 |
| [Commvault](https://job-boards.greenhouse.io/commvault) | 60 | 20 | 3 |
| [Contentful](https://job-boards.greenhouse.io/contentful) | 20 | 7 | 1 |
| [Discord](https://job-boards.greenhouse.io/discord) | 51 | 48 | 0 |
| [Duolingo](https://job-boards.greenhouse.io/duolingo) | 61 | 40 | 0 |
| [Figma](https://job-boards.greenhouse.io/figma) | 162 | 107 | 8 |
| [GetYourGuide](https://job-boards.greenhouse.io/getyourguide) | 56 | 3 | 40 |
| [Grafana Labs](https://job-boards.greenhouse.io/grafanalabs) | 122 | 39 | 9 |
| [Instacart](https://job-boards.greenhouse.io/instacart) | 131 | 70 | 0 |
| [N26](https://job-boards.greenhouse.io/n26) | 52 | 0 | 34 |
| [Planet](https://job-boards.greenhouse.io/planetlabs) | 121 | 41 | 24 |
| [Reddit](https://job-boards.greenhouse.io/reddit) | 152 | 120 | 1 |
| [Rubrik](https://job-boards.greenhouse.io/rubrik) | 128 | 49 | 2 |
| [think-cell](https://job-boards.greenhouse.io/thinkcellsoftware) | 27 | 12 | 12 |

These numbers are point-in-time probe observations. Country/city strings are discovery hints and can be incomplete or ambiguous. “Remote” alone does not establish a country or work authorization. When adding a shared board from a country's view, the saved source is narrowed to the selected country; changing countries later does not reinterpret Norway-specific sitemap or Workday search settings as another country's feed.

## Verification and maintenance

- `collector_checked` in the USA/Germany data means the application collector returned valid jobs; details retain total jobs and observed country-location counts. Norway/Ukraine use the equivalent `collector_verified` label and retain their probe scope.
- `official_page_reviewed` means an official/employer-hosted page established the source identity. HTTP/title checks are weaker than a collector check and do not justify adding an automatic source configuration.
- `official_link_reviewed` means an official referring page confirmed the destination. CareerOneStop remains a manual route because direct research access was blocked.
- `official_index_reviewed` means official indexed/publisher content confirmed the service while direct research access was limited. Indexed content may be stale.
- Norway/Ukraine additionally retain explicit `access_limited`, `page_response_only` and `official_search_result` entries, as explained in their report.

Catalog regression checks verify packaged data presence, identifiers, evidence dates/URLs, minimum country coverage, duplicate source identities, connector configuration, and country narrowing without modifying catalog records. They are offline checks of persisted evidence, not a guarantee that remote sites still work. Recheck a collector when its endpoint, board ID, response format, access rules or scope changes, and record the result before presenting it as automated coverage.
