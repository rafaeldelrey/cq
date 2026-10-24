# Benchmarks: rating a metric's level, with its source

The trend says whether a metric moved. This file says whether its level is good, by a standard someone published. Use it when writing `analysis.md`: rate each target's headline metrics against the bands below and name the source in the same sentence ("worst CC 15, radon C; at NIST's 15 ceiling, above its recommended 10").

cq computes the same ratings itself (`cq/benchmarks.py`): the report's **Rating** column, each target's **Grade**, and the dashboard's labels. Quote those; this file says where they come from and how to read them.

Three rules:

- **Same unit, or no rating.** Every band below is stated in the unit cq reports. A band quoted elsewhere in another unit (the original 0–171 maintainability index, statement-only coverage, a percentage of lines in a risk band) does not apply to cq's number: convert it, or leave it out.
- **The repo's own bar wins.** A gate the repo runs (its `complexity_limit`, a coverage `fail_under`, a ratchet baseline) is the bar for that repo. The bands here say where that bar sits against outside practice; they don't replace it.
- **No source, no rating.** Metrics with no published standard are listed at the end, with how to judge them instead. Don't invent bands for them.

## The labels

One scale for every rated metric: **Excellent, Good, Fair, Poor, Critical** (5 to 1). Each band also carries its source's own name for it ("radon B", "Google: commendable", "SIG ++"), and the report shows both. A scale with fewer published bands uses fewer labels: maintainability has no Excellent or Critical, mutation score no Excellent or Critical.

## Cyclomatic complexity (per function; worst and mean)

cq's `complexity` collector reports radon's McCabe complexity per function, the worst, the mean, and the count over the repo's `complexity_limit` (default 15).

| CC | radon rank | radon's reading | Label |
|---|---|---|---|
| 1–5 | A | low risk, simple block | Excellent |
| 6–10 (over 5) | B | low risk, well structured and stable block | Good |
| 11–20 (over 10) | C | moderate risk, slightly complex block | Fair |
| 21–30 (over 20) | D | more than moderate risk, more complex block | Poor |
| 31–40 (over 30) | E | high risk, complex block, alarming | Critical |
| 41+ (over 40) | F | very high risk, error-prone, unstable block | Critical |

- **Ranks:** radon's `cc_rank` (`radon/complexity.py`), the readings are its own. radon ranks an average the same way (`radon cc -a`), which is how cq rates the mean: a mean between two bands (10.4) takes the higher one (C).
- **Limit:** McCabe, "A Complexity Measure", IEEE Transactions on Software Engineering SE-2(4), 1976, proposed 10. NIST SP 500-235 (Watson and McCabe, "Structured Testing", 1996, [PDF](https://nvlpubs.nist.gov/nistpubs/Legacy/SP/nistspecialpublication500-235.pdf)): "The original limit of 10 as proposed by McCabe has significant supporting evidence, but limits as high as 15 have been used successfully as well", reserved for projects with "several operational advantages", such as experienced staff, formal design, code walkthroughs and a comprehensive test plan. cq's default limit of 15 is that ceiling, not the recommendation.

Say both: the label of the worst function and of the mean, and the count over the limit with the worst offenders by name.

## Cognitive complexity (per function)

cq's `cognitive` collector (complexipy) reports SonarSource cognitive complexity per function and the count over `cognitive_limit` (default 15).

- **Source:** G. Ann Campbell, "Cognitive Complexity: A new way of measuring understandability", SonarSource white paper. SonarQube's rule S3776 flags a function over 15 by default ([SonarSource on why 15](https://community.sonarsource.com/t/s3776-reason-for-the-current-default-value-of-15/127103)).
- **No bands** beyond that one threshold are published. Report the count over 15 and the worst functions; don't grade the mean. cq gives it no rating.

## Maintainability index (per file; line-weighted mean)

cq's `maintainability` collector reports radon's MI per file and a line-weighted mean. radon normalizes it to 0–100: `max(0, (171 − 5.2 ln V − 0.23 G − 16.2 ln L + 50 sin(√(2.46 × radians(C)))) × 100 / 171)` (`mi_compute` in `radon/metrics.py`).

| MI (0–100) | radon rank | Visual Studio | Label |
|---|---|---|---|
| over 19 | A | green, good maintainability | Good |
| over 9 to 19 | B | yellow, moderate maintainability | Fair |
| 9 and under | C | red, low maintainability | Poor |

- **Ranks:** radon's `mi_rank`.
- **Same scale:** Microsoft, "[Code metrics: Maintainability index range and meaning](https://learn.microsoft.com/en-us/visualstudio/code-quality/code-metrics-maintainability-index-range-and-meaning)": the same ×100/171 normalization and the same 20 / 10 cut points, set "conservative" so that red means a real problem.
- **Not this scale:** the often-quoted "85 and over highly maintainable, 65–85 moderate, under 65 difficult" (Coleman, Ash, Lowther and Oman, "Using Metrics to Evaluate Software System Maintainability", IEEE Computer 27(8), 1994) is on the original, unnormalized index. On radon's scale those cut points sit at about 50 and 38 (× 100/171). Applied to a radon number, they call good code "difficult": a file at radon MI 47 is rank A here and under 65 there. Quote Coleman et al. only after converting.

MI is a weak signal on its own: it rewards comments and punishes long files whatever their structure. Rate it, but lead with complexity.

## Test coverage

cq's `tests` collector reports coverage.py's total with the app's own `[tool.coverage]` config. With `branch = true` that total counts lines and branches together, so it reads lower than a line-only figure; say which one you quote.

| Coverage | Source's name | Label |
|---|---|---|
| 90% and over | Google: exemplary | Excellent |
| 75–90% | Google: commendable | Good |
| 60–75% | Google: acceptable | Fair |
| 20–60% | under Google's 60 | Poor |
| under 20% | SIG: under 20 | Critical |

- **Google:** Arguelles, Ivanković and Bender, "[Code Coverage Best Practices](https://testing.googleblog.com/2020/08/code-coverage-best-practices.html)", Google Testing Blog, 2020: "60% as 'acceptable', 75% as 'commendable' and 90% as 'exemplary'". The same post says the coverage of changed code in review matters more than the project total.
- **SIG:** Heitlager, Kuipers and Visser, "[A Practical Model for Measuring Maintainability](https://www.di.uminho.pt/~joost/publications/HeitlagerKuipersVisser-Quatic2007.pdf)", QUATIC 2007: ++ 95–100%, + 80–95%, o 60–80%, − 20–60%, −− 0–20%. The bands share their ends; cq puts 20% itself in Poor.
- **SonarQube:** the built-in "Sonar way" quality gate fails new code under 80% coverage ([docs](https://docs.sonarsource.com/sonarqube-server/quality-standards-administration/managing-quality-gates/introduction-to-quality-gates)). A gate on changed code, not a band for the total.
- **OpenSSF Best Practices badge** ([criteria](https://www.bestpractices.dev/en/criteria)): Silver asks for 80% statement coverage; Gold for 90% statement and 80% branch.

## Duplication

cq's `duplication` collector (jscpd) reports duplicated lines as a share of the target.

| Duplicated lines | SIG band | Label |
|---|---|---|
| 0–3% | ++ | Excellent |
| 3–5% | + | Good |
| 5–10% | o | Fair |
| 10–20% | − | Poor |
| over 20% | −− | Critical |

- **SIG:** Heitlager, Kuipers and Visser 2007 (above). SIG counts code in equal blocks of at least six lines; jscpd's minimum block differs, so the bands are close, not exact.
- **SonarQube:** the "Sonar way" gate fails new code with more than 3% duplicated lines: the same line as SIG's ++.
- **pyscn's near-duplicate percentage** (renamed and structural copies) is a wider measure: don't hold it to these bands. cq doesn't rate it.

## Mutation score

cq's `mutation` collector reports the share of judged mutants the tests killed.

| Score | Stryker | Label |
|---|---|---|
| 80% and over | high | Good |
| 60–80% | low | Fair |
| under 60% | under low ("danger") | Poor |

- **Stryker:** the defaults of its `thresholds` setting, high 80 and low 60 ([configuration](https://stryker-mutator.io/docs/stryker-js/configuration/)). A tool default, not a study; no academic bands are published. PIT sets no recommended threshold.

## The overall grade

cq grades each target by averaging its ratings (Excellent 5 … Critical 1), as SIG's model averages its scores, and rounds the mean down to a whole label, so a grade never reads better than its ratings do (rounding down is cq's choice). The weakest rated metric is named with it. It needs two rated metrics at least. Each rated metric counts once, so worst and mean complexity count as two.

Grades elsewhere, for context, not to be mixed with cq's:

- **SIG / TÜViT stars** (1–5): thresholds recalibrated at least yearly so that systems spread 5/30/30/30/5% over the stars; 3 stars is the market average, and a TÜViT certificate needs at least 3 overall and 2 on every sub-characteristic (Baggen, Correia, Schill and Visser, "[Standardized code quality benchmarking for improving software maintainability](https://webarchive.di.uminho.pt/wiki.di.uminho.pt/twiki/pub/Personal/Joost/PublicationList/SQM2010-StandardizedCodeQuality.pdf)", 2010). The authors themselves "do not attribute much added value" to a single overall number: lead with the ratings.
- **SonarQube maintainability rating** A–E from the technical debt ratio: A up to 5%, B 5–10%, C 10–20%, D 20–50%, E 50% and over ([metrics](https://docs.sonarsource.com/sonarqube-server/latest/user-guide/code-metrics/metrics-definition/)). Reliability and security ratings follow the worst open issue's severity.
- **Qlty** grades a project A–F from its debt ratio, with the same 5 / 10 / 20 / 50 cut points ([docs](https://docs.qlty.sh/cloud/maintainability/metrics.md)).

## Readiness

There is no published standard for "production ready". The `/cq` analysis gives a readiness class as **cq's own classification, and says so**: the overall grade, plus three blockers.

- **Blockers:** a secret in the current tree or a live key in history; a critical or high vulnerability with a fix available; a failing gate the repo runs (its complexity limit, its coverage floor).

| Class | When |
|---|---|
| Enterprise grade | Grade Excellent, no blocker |
| Production ready | Grade Good or better, no blocker |
| Development stage | Grade Fair, or Good or better with a blocker |
| Prototype | Grade Poor or Critical |

## SIG's current limits, which cq doesn't rate

SIG/TÜV NORD CERT, "[Evaluation Criteria Trusted Product Maintainability: Guidance for Producers](https://softwareimprovementgroup.com/wp-content/uploads/SIG-TUViT-Evaluation-Criteria-Trusted-Product-Maintainability-Guidance-for-producers.pdf)", version 17.0, 2025, rates **risk profiles**: the share of a system's lines in each band. For 4 stars:

- **Unit complexity** (bands 1–5, 6–10, 11–25, 26+): at most 20.2% of lines in units over 5, 7.3% over 10, 1.1% over 25.
- **Unit size** (1–15, 16–30, 31–60, 61+ lines): at most 47.1%, 23.1%, 8.3% of lines in units over 15, 30, 60.
- **Unit interfacing** (parameters): at most 15.0%, 3.3%, 0.9% of lines in units with 3+, 5+, 7+ parameters.
- **Duplication:** at most 5.6% redundant lines.

They are recalibrated yearly: cite the version. cq reports per-function values and a count over one limit, not a share of lines per band, so these don't apply to cq's numbers. Mention them as the outside practice, never as a rating.

## Metrics with no published standard

Judge these by their trend and the repo's own bar, never by an invented band:

- **Lint findings (ruff) and type errors (pyright):** the count depends on the rules switched on; the same code yields many times more findings under a strict ruleset than under `E,F,W,I`. A density compares only within one repo at one config (compare the snapshots' `configs` hashes). Zero new findings on changed lines is the bar that transfers.
- **Dead code (vulture):** a candidate list, not a rate. Confirm one before calling it dead.
- **Docstring coverage (interrogate):** no published standard. interrogate's `--fail-under` default of 80 is a tool default; PEP 257 sets no percentage.
- **Dependency lag (libyears) and outdated packages:** Cox, Bouwers, van Eekelen and Visser, "Measuring Dependency Freshness in Software Systems" (ICSE 2015), rate the number of *releases* behind, not years; no bands exist for libyears.
- **Known vulnerabilities:** judged by severity and whether a fix exists, not by a count band.
- **The AI-slop tools** (aislop's score, smells, radarlint, pyscn, fallow, vale, cspell): tool-specific scores; judge the new findings in changed code.
- **ISO/IEC 25010 and 25023, ISO/IEC 5055 (CISQ):** they define the characteristics and how to measure them, and leave the targets to the user.

## A stricter profile: the ESA Python Coding Rules

ESA, "ESA Python Coding Rules", PE-RS-ESA-GS-0871, version 1.0, published with its guidelines on the [EOP-CFI site](https://eop-cfi.esa.int/Repo/PUBLIC/DOCUMENTATION/SYSTEM_SUPPORT_DOCS/), is normative for software some ESA contracts commission. The rules a cq snapshot can check:

| Rule | Requirement | cq metric |
|---|---|---|
| PY-0240 | Cyclomatic complexity of the code shall be less than or equal to 10 | `complexity`: the worst function, and the count over 10 |
| PY-0090 | Unit test coverage shall be 100% of the Python packages developed; gaps justified | `tests` coverage |
| PY-0230 | Code checks include raw metrics, duplicated lines, cyclomatic complexity, maintainability index, Halstead metrics | `size`, `duplication`, `complexity`, `maintainability` |
| PY-0350 | The design shall avoid any code duplication | `duplication`, `pyscn` |
| PY-0050, PY-0100 | Coding rules and product metrics verified systematically in CI; linters and checkers run in CI | the gate in CI |

Use this profile only when the repo says it applies; otherwise mention it at most as "stricter than ESA's 10" in passing.
