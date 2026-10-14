# Market Risk Dashboard — Design Spec

- **Date:** 2026-10-14
- **Status:** Draft, awaiting review
- **Owner:** peter

## 1. Purpose

A personal early-warning dashboard for a beginner investor in US stocks and bonds. It tracks the macro, market, and credit factors that historically moved US stocks and bonds, combines them into an explained risk score, shows how those factors behaved around past crises and rallies, lets the user look up the score on any past day or range, and emails the user when conditions change.

It is a learning and monitoring tool, not a trading system and not financial advice. It never recommends trades.

### Success criteria

1. Every weekday morning, an up-to-date dashboard is live at a public GitHub Pages URL, with no manual action required.
2. Every score on the site (indicator, category, overall) can answer "why is it this number?" on the page itself.
3. The user is emailed within one daily run of any band/light/regime change, and receives a weekly summary every Monday even if nothing changed.
4. Historical scores are computed point-in-time (no look-ahead), enforced by an automated test.
5. The user can pick any past day or date range and see the scores as they stood then.
6. Total running cost: $0.

### Non-goals (v1)

- Trade recommendations, portfolio tracking, brokerage integration.
- Individual stock analysis.
- Intraday data or real-time updates.
- Interactive weight tuning (weights are edited in config).
- Indicators without a reliable free source (see §4.3).

## 2. Architecture

A single Python pipeline runs daily on GitHub Actions, writes data back to the repo, builds a static site, deploys it to GitHub Pages, and sends email.

```
GitHub Actions — daily.yml (cron 11:00 UTC ≈ 7am US Eastern)
  1. fetch      sources → data/raw/<indicator_id>.csv
  2. signals    raw → per-indicator point-in-time risk scores → data/scores/indicators.csv
  3. composite  indicator scores → category + overall scores → data/scores/composite.csv
  4. regime     growth/inflation direction → data/scores/regime.csv
  5. rationale  score explanations, outcome-by-band table → data/scores/rationale.json
  6. history    event analysis, similarity, base rates → data/scores/history.json
  7. alerts     diff vs data/state.json → send email (or write preview with --dry-run)
  8. site       render templates + export time-machine JSON → site/ → deploy to Pages
  9. commit     data/ (raw, scores, state.json) committed back to main
```

Each step is a module in `dashboard/` with a `main()` callable as `python -m dashboard.<step>`. Each step reads only files written by earlier steps, so any step can be re-run alone. `python -m dashboard.run` runs all steps in order.

The full score history is **recomputed from scratch every run**. This is cheap (≈40 series × ≈20k days), it keeps the pipeline deterministic, and it absorbs upstream data revisions automatically.

### 2.1 Repository layout

```
config/
  indicators.yaml   indicator catalog (§4)
  events.yaml       historical event library (§7)
  weights.yaml      category weights (§5.3)
  untracked.yaml    "Not tracked yet" indicators with explanations (§4.3)
dashboard/
  fetch/            fred.py, yahoo.py, shiller.py, base.py (retry, staleness)
  signals.py  composite.py  regime.py  rationale.py  history.py  alerts.py
  site/             build.py, templates/*.html.j2, static/ (css, js)
  run.py            orchestrates all steps
  config.py         loads + validates YAML
data/
  raw/  scores/  state.json
tests/
  fixtures/         small sample series, recorded API responses
.github/workflows/
  daily.yml         pipeline + deploy + commit
  ci.yml            tests on every push / PR
docs/superpowers/specs/
```

`site/` is a build output, deployed via `actions/deploy-pages`, and git-ignored.

### 2.2 Stack

Python 3.12, `uv` for dependencies, `pandas`, `requests`, `yfinance`, `pyyaml`, `jinja2`, `plotly` (charts rendered client-side from JSON via the Plotly CDN), `pytest`. Plain HTML/CSS/vanilla JS on the site, mobile-first, light and dark themes.

### 2.3 Secrets (GitHub Secrets, never committed — the repo is public)

| Secret | Use |
|---|---|
| `FRED_API_KEY` | FRED API |
| `GMAIL_USER`, `GMAIL_APP_PASSWORD` | SMTP sending via smtp.gmail.com:587 |
| `ALERT_TO` | Recipient address |

## 3. Categories

| id | Name | What it captures |
|---|---|---|
| `rates` | Rates & Fed | Cost of money, curve shape, liquidity |
| `inflation` | Inflation | Price pressure that drives Fed policy and bond prices |
| `growth` | Growth | Direction of the real economy |
| `labour` | Labour market | Early recession signals from jobs |
| `credit` | Credit & financial stress | Lenders' fear; often earliest crisis signal |
| `valuation` | Valuation | How much room prices have to fall |
| `volatility` | Volatility, sentiment & breadth | Fear/complacency and market internals |
| `global` | Dollar, commodities & global | External pressures and growth proxies |

## 4. Indicator catalog

### 4.1 Schema (`config/indicators.yaml`)

```yaml
- id: hy_spread
  name: High-yield credit spread
  category: credit
  source: fred              # fred | yahoo | shiller | derived
  series: BAMLH0A0HYM2      # source-specific id; derived uses `inputs` + `formula`
  frequency: daily          # daily | weekly | monthly
  release_lag_days: 1       # shift applied before scoring (point-in-time)
  transform: level          # one name, or a list applied in order (see transforms below)
  risk_when: high           # high | low
  revised: false            # true = historical values are revised figures
  plausible: [0, 30]        # sanity range for the raw value
  rules:                    # optional hard overrides (§5.1)
    - {when: "value > 8", min_score: 86, label: "Spread above 8%: crisis-level stress"}
  explain: >
    Extra yield investors demand to lend to riskier companies. Widening spreads
    mean lenders fear defaults and often lead stock declines.
```

Transforms: `level`; `yoy_pct` (% change vs 12 months earlier); `change_3m|6m|12m` (absolute difference, for series already in % or rate units); `pct_change_6m` (% change, for ratios and counts); `ann_6m` (6-month % change, annualized); `ma_4w` (4-week moving average); `pct_from_ma200` (% above/below 200-day moving average); `drawdown_252d` (% below the running 252-trading-day high).

`config.py` validates every entry on load (known source, known category, known transform, `explain` present, unique ids) and the pipeline fails fast if invalid.

### 4.2 v1 indicators (37)

R = `revised: true`.

| Category | id | Source / series | Transform | Risk when | R |
|---|---|---|---|---|---|
| rates | fed_funds_chg | FRED `DFF` | change_12m | high | |
| rates | ust10y_chg | FRED `DGS10` | change_6m | high | |
| rates | curve_10y2y | FRED `T10Y2Y` | level | low | |
| rates | curve_10y3m | FRED `T10Y3M` | level | low | |
| rates | real_yield_10y | FRED `DFII10` | level | high | |
| rates | term_premium | FRED `THREEFYTP10` | level | high | |
| rates | fed_balance_sheet | FRED `WALCL` | yoy_pct | low | |
| rates | move_index | Yahoo `^MOVE` | level | high | |
| inflation | cpi_yoy | FRED `CPIAUCSL` | yoy_pct | high | R |
| inflation | core_pce_6m | FRED `PCEPILFE` | ann_6m | high | R |
| inflation | breakeven_10y | FRED `T10YIE` | level | high | |
| inflation | wages_yoy | FRED `AHETPI` | yoy_pct | high | R |
| inflation | oil_yoy | FRED `DCOILWTICO` | yoy_pct | high | |
| growth | cfnai_ma3 | FRED `CFNAIMA3` | level | low | R |
| growth | permits_yoy | FRED `PERMIT` | yoy_pct | low | R |
| growth | retail_sales_yoy | FRED `RSAFS` | yoy_pct | low | R |
| growth | indpro_yoy | FRED `INDPRO` | yoy_pct | low | R |
| growth | consumer_sentiment | FRED `UMCSENT` | level | low | |
| labour | claims_4w | FRED `ICSA` | [ma_4w, pct_change_6m] | high | R |
| labour | unemployment | FRED `UNRATE` | change_12m | high | R |
| labour | sahm_rule | FRED `SAHMREALTIME` | level | high | |
| labour | payrolls_3m | FRED `PAYEMS` | change_3m | low | R |
| credit | hy_spread | FRED `BAMLH0A0HYM2` | level | high | |
| credit | ig_spread | FRED `BAMLC0A0CM` | level | high | |
| credit | stlfsi | FRED `STLFSI4` | level | high | |
| credit | nfci | FRED `NFCI` | level | high | R |
| credit | sloos_tightening | FRED `DRTSCILM` | level | high | |
| valuation | cape | Shiller | level | high | |
| valuation | equity_risk_premium | derived: Shiller E/P − `DGS10` | level | low | |
| volatility | vix | Yahoo `^VIX` | level | high | |
| volatility | vix_term | derived: `^VIX` / `^VIX3M` | level | high | |
| volatility | breadth_rsp_spy | derived: `RSP`/`SPY` | pct_change_6m | low | |
| volatility | spx_vs_200dma | Yahoo `^GSPC` | pct_from_ma200 | low | |
| volatility | spx_drawdown | Yahoo `^GSPC` | drawdown_252d | high | |
| global | dollar_yoy | Yahoo `DX-Y.NYB` | yoy_pct | high | |
| global | copper_gold | derived: `HG=F` / `GC=F` | pct_change_6m | low | |
| global | gold_yoy | Yahoo `GC=F` | yoy_pct | high | |

Rule overrides in v1:

| Indicator | Rule | Effect |
|---|---|---|
| curve_10y2y, curve_10y3m | value < 0 | min_score 86, "Curve inverted" |
| curve_10y2y, curve_10y3m | crossed from < 0 to > 0 within last 6 months | min_score 86, "Curve un-inverting: recession often begins here" |
| sahm_rule | value ≥ 0.5 | min_score 90, "Sahm Rule triggered" |
| hy_spread | value > 8 | min_score 86 |
| vix | value > 40 | min_score 90 |

Exact `release_lag_days` per series are set during implementation from each series' release schedule (daily market data: 1; weekly claims/STLFSI/NFCI: 5–7; monthly CPI/PCE/payrolls/etc.: 14–45; quarterly SLOOS: ~35 after quarter end; Shiller: 30).

### 4.3 Not tracked yet (`config/untracked.yaml`)

Shown on the Overview in a "Not tracked yet" panel with a one-paragraph explanation each and why it isn't tracked: forward P/E, earnings revisions, AAII sentiment, put/call ratio, margin debt.

### 4.4 Data history coverage

Coverage varies by series (e.g. S&P 500 and yields to the 1950s+, VIX 1990, HY spread 1997, VIX3M 2007, RSP 2003). Where data doesn't exist, the site shows "no data" — never backfilled or imputed.

## 5. Scoring

### 5.1 Indicator risk score (0–100)

For each indicator on each date *t*:

1. Apply `release_lag_days`: a value observed for period *p* becomes "known" only on its release date. Forward-fill known values to daily.
2. Apply `transform`.
3. **Point-in-time percentile:** rank the transformed value at *t* against all transformed values known on or before *t* (expanding window). Require ≥ 5 years of history; earlier dates get no score.
4. If `risk_when: low`, score = 100 − percentile; else score = percentile.
5. Apply `rules`: if a rule matches, score = max(score, `min_score`) and record the rule label.

Output per indicator per day: `value`, `score`, `rule_label` (nullable), `stale` flag.

**Light:** green < 60, amber 60–85, red > 85.
**Trend arrow:** score(t) − score(t − 63 trading days): ↑ if ≥ +10, ↓ if ≤ −10, → otherwise.

### 5.2 Category score

Equal-weighted mean of the non-stale, available indicator scores in the category. A category with no available indicator is "no data".

### 5.3 Overall risk score

Weighted mean of available category scores using `config/weights.yaml` (default: all 1.0). Requires ≥ 5 of 8 categories; otherwise "no data".

**Bands:** Calm 0–40, Normal 40–60, Elevated 60–75, High 75–100.

**Contribution** of category *c* = w_c × (score_c − 50) / Σw. The contributions sum to (overall − 50).

### 5.4 Regime

- **Growth direction:** mean of z-scores (point-in-time) of `cfnai_ma3`, −`claims_4w`, `permits_yoy`; "rising" if its 6-month change > 0.
- **Inflation direction:** core PCE 6-month annualized minus core PCE 12-month YoY; "rising" if > 0.
- Four regimes: Growth ↑ Inflation ↓ ("Goldilocks"), Growth ↑ Inflation ↑ ("Reflation"), Growth ↓ Inflation ↓ ("Slowdown"), Growth ↓ Inflation ↑ ("Stagflation").
- For each regime, the site shows median annualized S&P 500 and bond returns across all historical days in that regime.

### 5.5 Bond return series

- From 2002: total return of `AGG` and `TLT` (Yahoo adjusted close).
- Before 2002: estimated 10-year Treasury total return from `DGS10`: monthly return ≈ y(t−1)/12 − D × Δy, with D = 8. Labelled "estimated" wherever shown.

## 6. Score rationale ("Why this score?")

Computed in `rationale.py`, rendered on the Overview (short form), the "Why this score?" page (full), category pages (indicator form), and emails (3-line form).

### 6.1 Indicator rationale

- Current value, percentile, score, light, trend.
- **Spectrum bar:** the value scale from the indicator's historical min to max, marking the median, the values at the amber (60) and red (85) score thresholds (inverse-percentile on point-in-time history), today's value, and the value at the worst point of up to 3 major events in its history.
- Sentence: "Higher than N% of days since YYYY. Up/down from S three months ago."
- Rule label, if any.
- Data note: "Market price, never revised" or the revised-data badge (§6.6).

### 6.2 Outcome-by-band table (meaning of the overall score)

For every historical day with an overall score, compute forward outcomes:
- S&P 500 price return over the next 3, 6, 12 months
- Max S&P 500 drawdown within the next 12 months; flag if ≥ 15%
- Bond return over the next 12 months (§5.5)

Grouped by band: number of days, median 12-month S&P return, % of days followed by a ≥ 15% drawdown within 12 months, median 12-month bond return. Also the overall score's percentile today ("riskier than N% of days since YYYY").

Days whose 12-month forward window extends past today are excluded from the table.

### 6.3 Drivers

- Contributions per category (§5.3), sorted by magnitude.
- Change attribution over 7 and 30 days: Δoverall decomposed into per-category Δcontribution.

### 6.4 Nearest thresholds

- For the next band up and down: the indicators whose light is closest to changing, with current value and the value at which the light changes; and a statement like "2 more indicators turning red would move the score into High", computed by greedily raising the closest indicators to score 86 and recomputing.

### 6.5 Confidence

- **Coverage:** fresh indicators / total.
- **Agreement:** standard deviation of category scores; < 12 "categories broadly agree", 12–20 "somewhat mixed", > 20 "mixed signal", naming the highest and lowest categories.
- **Revised-data share:** share of the overall score's weight coming from `revised: true` indicators.

### 6.6 Revised-data badge

Every indicator with `revised: true` shows a "Revised data" badge on cards, charts, event pages, and the time machine. Tooltip: "Historical values are today's revised figures. What investors saw at the time may have differed, so treat past readings as approximate." Event pages state the share of the pre-event signal that came from revised indicators.

### 6.7 Methodology page

Explains §5–§6 in plain English: how percentiles work, why bands are 40/60/75 (round starting points, justified by the outcome-by-band table, which is the evidence), point-in-time calculation, revised data, data coverage gaps, and known limitations. Includes a "not financial advice" disclaimer.

## 7. History playbook

### 7.1 Event library (`config/events.yaml`)

```yaml
- id: gfc_2008
  name: Global Financial Crisis
  kind: crash               # crash | rally
  assets: [stocks, bonds]
  anchor: {start: 2007-10-09, end: 2009-03-09}
  trigger: Subprime mortgage losses, Lehman collapse, and a global credit freeze.
  context: >
    ...
  lesson: >
    ...
```

`anchor` dates are approximate. `history.py` finds the exact peak and trough in the relevant asset (S&P 500 for stock events, the bond return series for bond events) within ±30 days of each anchor. The story text (`trigger`, `context`, `lesson`) is hand-written; every number shown is computed.

Initial events (≈ 22):

| Kind | Asset | Event | Anchor start → end |
|---|---|---|---|
| crash | stocks | 1973–74 oil shock & stagflation | 1973-01-11 → 1974-10-03 |
| crash | bonds | Volcker rate shock | 1979-10-01 → 1981-09-30 |
| rally | stocks | 1982 bull market begins | 1982-08-12 → 1983-10-10 |
| crash | stocks | Black Monday | 1987-08-25 → 1987-12-04 |
| crash | bonds | 1994 bond massacre | 1993-10-15 → 1994-11-07 |
| crash | stocks | LTCM / Russia default | 1998-07-17 → 1998-10-08 |
| crash | stocks | Dot-com bust | 2000-03-24 → 2002-10-09 |
| crash | stocks | Global Financial Crisis | 2007-10-09 → 2009-03-09 |
| rally | bonds | 2008 flight to safety | 2008-06-13 → 2008-12-30 |
| rally | stocks | 2009 bottom and recovery | 2009-03-09 → 2010-04-23 |
| crash | stocks | US downgrade / euro crisis | 2011-04-29 → 2011-10-03 |
| crash | bonds | Taper tantrum | 2013-05-02 → 2013-09-05 |
| crash | stocks | China devaluation / oil crash | 2015-05-21 → 2016-02-11 |
| crash | stocks | Q4 2018 Fed tightening sell-off | 2018-09-20 → 2018-12-24 |
| rally | stocks | 2019 Fed pivot | 2018-12-24 → 2019-07-26 |
| crash | stocks | COVID crash | 2020-02-19 → 2020-03-23 |
| rally | bonds | 2020 flight to safety | 2020-01-17 → 2020-03-09 |
| rally | stocks | 2020 recovery | 2020-03-23 → 2021-01-08 |
| crash | stocks+bonds | 2022 inflation & rate shock | 2022-01-03 → 2022-10-12 |
| rally | stocks | October 2022 bottom | 2022-10-12 → 2023-07-31 |
| crash | stocks | Regional bank crisis (SVB) | 2023-03-08 → 2023-03-13 |
| crash | stocks | April 2025 tariff shock | 2025-02-19 → 2025-04-08 |

### 7.2 Event page

1. Story: trigger, context, lesson; computed peak/trough dates and % moves for stocks and bonds.
2. Price chart: S&P 500 and bond series from 24 months before start to 12 months after end, event window shaded.
3. Factor heatmap: rows = indicators (grouped by category), columns = −12, −6, −3, 0 (start), end, +3, +6 months; cells = score colored by light; "no data" cells greyed; revised indicators badged.
4. "Did the composite warn?": overall score line over the same window with band shading; reports the lead time (trading days before start) at which the score first entered Elevated or High and stayed there ≥ 10 days, or "No warning" if it didn't.
5. Link to the Time machine for the event's range.

### 7.3 Cross-event summary ("Which factors warned?")

For each indicator, across crash events where it had data:
- **Hit rate:** % of events where its light was amber or red at −3 or −6 months.
- **False-alarm rate:** % of red episodes (≥ 20 consecutive trading days at red) not followed by a crash event start within 12 months.
- Sorted by hit rate, with event counts shown so small samples are obvious.

### 7.4 "Today looks like…"

- **Fingerprint:** vector of the 8 category scores plus their 63-day changes.
- Compared with each crash and rally event's fingerprint at −6 and −3 months, using Euclidean distance over the dimensions available in both, divided by the number of dimensions compared.
- Show the top 3 closest (event, offset, distance, categories compared).
- **Base rate:** among all historical days whose distance to today's fingerprint is ≤ the 3rd-closest match's distance, the % not followed by any crash event start within 12 months. Shown with every match ("similar readings were followed by no crisis N% of the time").

## 8. Time machine

A page for looking up scores on any past day or range.

### 8.1 Data export

`site/build.py` exports per-year JSON files to `site/data/tm/<year>.json`:

```json
{
  "dates": ["2008-01-02", ...],
  "overall": [58, ...],
  "categories": {"rates": [...], ...},
  "indicators": {"hy_spread": {"value": [...], "score": [...]}, ...},
  "regime": ["slowdown", ...],
  "spx": [...], "bond": [...]
}
```

Scores are rounded to integers and values to 3 significant figures; `null` for no data. The page lazy-loads only the years it needs. A small `site/data/tm/index.json` lists available years, the indicator metadata (name, category, revised, explain), and events with their computed dates.

### 8.2 Day view

- Date picker, plus URL parameter `?date=YYYY-MM-DD` for linking. Non-trading days snap to the previous trading day, with a note.
- Shows, as of that day: overall score and band, its percentile at the time, regime, the 8 category lights with trend arrows, category contributions, and all indicator scores/lights (revised badges, rule labels).
- Events in progress on that day, or starting within the next 12 months.
- **What happened next:** S&P 500 and bond returns over the next 1, 3, 6, 12 months and max drawdown in 12 months. Hidden behind a "Show what happened next" toggle so the user can form a view first.

### 8.3 Range view

- From/to pickers, plus `?from=YYYY-MM-DD&to=YYYY-MM-DD`.
- Chart: overall score with band shading; toggle to overlay category scores; S&P 500 and bond series on a second panel; events in the range shaded and labelled.
- Summary: start/end/min/max/average score (with dates for min/max), days spent in each band, list of band changes with dates, S&P 500 and bond return over the range.

### 8.4 Links in

Event pages link to the range view for their window; the Overview's score history chart links clicked points to the day view.

## 9. Alerts

### 9.1 State and triggers

`data/state.json` stores, as of the last run: overall band and score, each category light, each indicator light, regime, and for curves/Sahm the rule-label set.

Triggers (compared with stored state):
1. Overall band change
2. Category light change
3. Indicator light change
4. Rule label appears or disappears (e.g. curve inverts / un-inverts, Sahm triggers)
5. Regime change
6. Overall score up ≥ 10 points vs 5 trading days ago, without a band change

### 9.2 Hysteresis

Moving up through a threshold uses the threshold; moving back down requires falling 5 points below it. Applies to indicator/category lights (60, 85) and overall bands (40, 60, 75). The stored state holds the hysteresis-adjusted light/band, which is what pages display as "current".

### 9.3 Emails

- At most one alert email per run, a digest of all triggers, ordered by the trigger list above.
- Subject: `[Market Watch] <most important change>`; e.g. `[Market Watch] Risk score Normal → Elevated (63)`.
- Body (HTML with plain-text fallback): what changed (with each indicator's `explain` text); the 3-line rationale (outcome stats for the current band, top 3 drivers, nearest threshold); top "Today looks like" match with its base rate; dashboard link; data health footer.
- **Weekly summary** every Monday run regardless of triggers: overall score and 7-day change, category lights, regime, top drivers, data health.
- `--dry-run` writes the email to `out/email-preview.html` instead of sending.
- Disclaimer footer: "Informational only, not financial advice."

## 10. Reliability and error handling

- **Fetch:** each source call retries 3 times with exponential backoff (2s, 4s, 8s). On final failure, the indicator keeps its last raw data and is marked stale; the run continues.
- **Staleness:** an indicator is stale if its last observation is older than 2× its normal interval plus its release lag. Stale indicators are excluded from category and overall scores and show a "stale since <date>" badge.
- **Sanity:** raw values outside `plausible` are dropped and logged; an indicator whose latest value is dropped is treated as stale. Alerts never fire from implausible data.
- **Coverage:** the overall score always displays "based on N/37 indicators".
- **Whole-run failure:** the workflow fails visibly (GitHub's failure email to the repo owner). The site is not redeployed and state is not updated, so the next successful run diffs against the last good state.
- **Partial-run rule:** alerts send only after the scores and state are computed successfully; the commit step runs after the deploy.
- **Repo activity:** the daily data commit prevents GitHub from disabling the schedule after 60 days of inactivity.

## 11. Site pages

All pages share a header with navigation and a footer with the last update time, data health, and the disclaimer.

1. **Overview** (`index.html`): overall score gauge with band and short rationale; regime quadrant; 8 category lights with trends (linking to category pages); "What changed" (7 and 30 days); "Today looks like…"; overall score history (5 years, events shaded); data health; "Not tracked yet" panel.
2. **Category pages** (`category/<id>.html`): indicator cards (value, score, light, trend, spectrum bar, 10-year chart with events shaded, explanation, badges).
3. **Why this score?** (`why.html`): full rationale (§6.2–§6.5) and full-history score chart.
4. **History** (`history/index.html`, `history/<event_id>.html`, `history/which-factors-warned.html`).
5. **Time machine** (`time-machine.html`).
6. **Learn** (`learn.html`): methodology (§6.7), glossary, limitations, disclaimer.

## 12. Testing

All unit tests run offline on fixtures.

- **No look-ahead (critical):** for a fixture dataset, compute scores; append future data; recompute; assert all scores at dates ≤ the original end date are identical. Covers indicator, category, overall, and regime.
- **Release lag:** a monthly value becomes visible exactly on its release date.
- **Signals:** percentile scoring, `risk_when` flipping, each transform, rule overrides and labels, 5-year minimum history.
- **Composite:** weighted means, missing categories, ≥ 5-category requirement, contributions summing to overall − 50.
- **Rationale:** outcome table excludes incomplete forward windows; change attribution sums to Δoverall; nearest-threshold logic.
- **History:** anchor-to-exact-date search, lead-time calculation, hit/false-alarm rates on a synthetic event set, similarity with partial dimensions, base rate.
- **Alerts:** each trigger type, hysteresis (no flip-flop around a threshold), Monday summary, dry-run output.
- **Reliability:** retry/backoff, stale marking, implausible-value dropping, exclusion from composite.
- **Config:** every entry in `indicators.yaml`, `events.yaml`, `weights.yaml`, `untracked.yaml` validates.
- **Fetchers:** parse recorded FRED / Yahoo / Shiller responses; one optional live smoke test (`pytest -m live`), not run in CI.
- **Site:** build from fixtures; all pages exist; all internal links resolve; time-machine JSON matches the schema and covers the expected years.

`ci.yml` runs the offline tests on every push and PR. `daily.yml` runs them before the pipeline and aborts on failure.

## 13. Build milestones

1. **Skeleton:** repo layout, config loading/validation, FRED + Yahoo fetchers, signals for 5 indicators (hy_spread, curve_10y3m, vix, cpi_yoy, sahm_rule), basic Overview, daily workflow deploying to Pages.
2. **Full scoring:** all 37 indicators, Shiller source, composite, regime, rationale, category pages, "Why this score?", Learn page.
3. **History and time machine:** event library, event pages, which-factors-warned, "Today looks like", time machine.
4. **Alerts:** state, triggers, hysteresis, digest and Monday emails, secrets.
