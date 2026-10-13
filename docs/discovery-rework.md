# Discovery and experience review

Audit completed 11 October 2026. Public-feed observations below were collected on 9 October 2026; linked example vacancy pages were checked again on 11 October. Openings and counts change.

## Why the old results were poor

The employer directory was much larger than the set of working collectors. The previous 294 employer records included only 36 automatic sources across all countries. Enabling the USA preset therefore searched a small, concentrated set of employers. A directory link did not increase the number of jobs collected.

Keyword overlap also answered the wrong question: a staff scientist could share the candidate's Python and machine-learning terms while requiring much more experience. The profile had no explicit total-experience field, and a keyword percentage did not communicate that mismatch. Finally, generated city allowlists ran in addition to country filtering and could discard valid locations outside the short vocabulary.

## Verified collection coverage

The catalog now separates automatic collection from career links and job-alert imports. Counts below describe supported routes, not currently open vacancies or candidate eligibility. An employer can operate in several countries, so country totals overlap.

| Country | Employer directory entries | Automatic employer feeds | Job-board directory entries |
| --- | ---: | ---: | ---: |
| Norway | 77 | 18 | 10 |
| USA | 154 | 80 | 13 |
| Germany | 116 | 48 | 12 |
| Ukraine | 77 | 21 | 11 |
| Distinct entries across countries | 366 | 109 | 46 |

The 109 automatic routes comprise 67 Greenhouse, 28 Ashby, 8 Lever, 4 structured career-page and 2 Workday collectors. The 103 Greenhouse/Ashby/Lever entries were checked with the application's actual collectors on 9 October. Each catalog record contains the endpoint, check date, observed posting count and observed country counts. The six existing Workday/career-page routes retain their earlier 3 October verification date; they are not represented as freshly retested. Crayon's public board returned zero postings at its last check, and remains explicitly described as possibly empty.

These are documented public job-posting interfaces: [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html), [Ashby Job Postings API](https://developers.ashbyhq.com/docs/public-job-posting-api), and [Lever Postings API](https://github.com/lever/postings-api). Application collection reads published vacancies. It does not need access to employer accounts or submit applications.

The catalog includes additional employers such as Affirm, May Mobility, Oscar Health, Striveworks, Ibotta, Good Inside, Moloco, YipitData and Worldly, widening the original small group of US technology employers. Employers are included because their actual public feed and geographic scope were verified; names and career-page URLs alone do not qualify them as collectors.

## Experience-aware recommendations

The profile now accepts decimal years of professional experience, including `2.5`. This is a user-supplied fact; employment dates and skill terms are not used to invent an experience total. Country switching keeps the same profile.

Recommendation screening considers title seniority and explicit experience requirements before presenting a role as suitable for review. Senior, staff, principal and management signals are distinguishable from quoted numeric requirements. Preferred experience and alternatives involving education remain review items rather than automatically becoming mandatory minimums. Unclear experience requirements stay visible as uncertainty.

Total professional experience is not proof of the requested domain experience. A role asking for two years of production ML may still be unsuitable for a candidate with 2.5 years in a different discipline. Work authorization, location restrictions, qualifications and special requirements still need review. Match scores are ranking aids, not probabilities of qualification or hiring.

## Concrete early-career examples

These public pages confirm that the expanded feeds contain opportunities beyond senior and staff titles. They illustrate coverage, not a claim that every role fits the current user's full profile.

| Employer and posting | Experience signal | Other conditions to review |
| --- | --- | --- |
| [Affirm — Machine Learning Engineer II, Underwriting ML](https://job-boards.greenhouse.io/affirm/jobs/7822387003) | Two or more years as an ML engineer, or a relevant PhD | US employment location; production Python, modeling and distributed data skills; sponsorship questions |
| [May Mobility — Machine Learning Engineer II, Performance Evaluation](https://job-boards.greenhouse.io/maymobility/jobs/8187068002) | Minimum two years building ML evaluation, metrics or analysis systems | USA remote; related education, production systems and robotics knowledge |
| [Oscar Health — Data Scientist I](https://job-boards.greenhouse.io/oscar/jobs/7592274) | One or more years using SQL/Python and analytical data models | Office/residency expectations and specific analytical skills |
| [Striveworks — Junior Machine Learning Engineer](https://job-boards.greenhouse.io/striveworks/jobs/8002515003) | Junior opening with a zero-to-two-year experience route | Austin hybrid/on-site, related BS degree, US citizenship and eligibility for security clearance |

A three-year minimum is a visible experience gap for a profile set to 2.5 years, even if the title is not senior. A two-year requirement combined with citizenship, clearance or residency restrictions is not automatically a full match.

## Geographic scope and limits

Country checks use structured location information where available and bounded country/location recognition. Generic “Remote” is not automatically classified as USA, Germany, Norway or Ukraine. Known country scope does not establish permission to work remotely from another country.

The old generated city allowlists are removed when their exact generated contents are recognized. Intentional custom city filters remain. Bounded scans report partial coverage when a budget is reached, and collection failures stay separate from successful empty results.

The directory is still a curated subset of the market. LinkedIn and other job-board directory entries are not silently scraped or counted as automatic employer feeds. Their supported paths are opening the board, importing job-alert emails and importing a specific accessible vacancy. The expanded catalog cannot promise more relevant vacancies than LinkedIn for every profile, country or date.

## Verification

Offline catalog tests cover packaged JSON resources, valid source configurations, independent feed identities, country scope, evidence metadata, truthful automatic/manual classification, and preservation of custom location filters. Catalog observations were cross-checked against saved public collector results. Live vacancy pages were checked separately to substantiate the examples above.

Experience, discovery, API and interface regression tests cover the resulting recommendation behavior separately. No application submission is part of catalog verification.
