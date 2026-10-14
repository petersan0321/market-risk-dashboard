# Market Risk Dashboard — Design Spec

- **Date:** 2026-10-14
- **Status:** Draft, awaiting review
- **Owner:** peter

## 1. Purpose

A personal early-warning dashboard for a beginner investor in US stocks and bonds. It tracks the macro, market, credit, and policy factors that historically moved US stocks and bonds, combines them into an explained risk score, shows how those factors behaved around past crises and rallies, lets the user look up the score on any past day or range, and emails the user when conditions change. Alongside the score it shows upcoming market-moving events (Fed meetings, data releases, elections) and recent news headlines as context, and every event — past or upcoming — comes with a plain-English explanation of *why* it moves markets, so the user learns as they go. A model report card backtests the score and shows how far it can be trusted.

It is a learning and monitoring tool, not a trading system and not financial advice. It never recommends trades.

### Success criteria

1. Every weekday morning, an up-to-date dashboard is live at a public GitHub Pages URL, with no manual action required.
2. Every score on the site (indicator, category, overall) can answer "why is it this number?" on the page itself.
3. The user is emailed within one daily run of any band/light/regime change, and receives a weekly summary every Monday even if nothing changed.
4. Historical scores are computed point-in-time (no look-ahead), enforced by an automated test.
5. The user can pick any past day or date range and see the scores as they stood then.
6. Total running cost: $0.
7. Every event the site mentions — historical events, calendar events, elections, and news topics — has a plain-English explanation of why it affects stocks and bonds.
8. The site reports how well the model has worked historically, with honest confidence ranges, and compares it with simple alternatives.

### Non-goals (v1)

- Trade recommendations, portfolio tracking, brokerage integration. (The §9.6 simulation is an educational illustration of past signals, not a recommendation.)
- Individual stock analysis.
- Intraday data or real-time updates.
- Interactive weight tuning (weights are edited in config).
- Indicators without a reliable free source (see §4.3).
- News headlines influencing the score (they are context only, §10.3).
- AI-written news summaries (would add a running cost).

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
  7. backtest   report card + simulation → data/scores/backtest.json
  8. context    upcoming calendar + news headlines → data/context/*.json
  9. alerts     diff vs data/state.json → send email (or write preview with --dry-run)
 10. site       render templates + export time-machine JSON → site/ → deploy to Pages
 11. commit     data/raw, data/context, data/state.json committed back to main
                (data/scores is rebuilt every run, so it is not committed)
```

Each step is a module in `dashboard/` with a `main()` callable as `python -m dashboard.<step>`. Each step reads only files written by earlier steps, so any step can be re-run alone. `python -m dashboard.run` runs all steps in order.

The full score history is **recomputed from scratch every run**. This is cheap (≈50 series × ≈20k days), it keeps the pipeline deterministic, and it absorbs upstream data revisions automatically.

### 2.1 Repository layout

```
config/
  indicators.yaml   indicator catalog (§4)
  events.yaml       historical event library (§7)
  weights.yaml      category weights (§5.3)
  simulation.yaml   allocation per band for the simulation (§9.6)
  untracked.yaml    "Not tracked yet" indicators with explanations (§4.3)
  calendar.yaml     calendar event types + manually maintained dates (§10.1)
  news_topics.yaml  news topic rules and explanations (§10.3)
  glossary.yaml     plain-English definitions of terms (§10.4)
dashboard/
  fetch/            fred.py, yahoo.py, shiller.py, gpr.py, treasury.py, news.py, base.py (retry, staleness)
  signals.py  composite.py  regime.py  rationale.py  history.py  backtest.py  simulate.py  context.py  alerts.py
  site/             build.py, templates/*.html.j2, static/ (css, js)
  run.py            orchestrates all steps
  config.py         loads + validates YAML
data/
  raw/  context/  state.json   (scores/ is git-ignored build output)
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
| `FRED_API_KEY` | FRED API (optional: without it, FRED's public CSV download is used, e.g. for local runs) |
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
| `policy` | Policy & geopolitics | Government, trade, Fed, and geopolitical uncertainty; fiscal pressure on bonds |

## 4. Indicator catalog

### 4.1 Schema (`config/indicators.yaml`)

```yaml
- id: baa_spread
  name: Corporate credit spread (Baa minus 10-year Treasury)
  category: credit
  source: fred              # fred | yahoo | shiller | gpr | treasury | derived
  series: BAA10Y            # source-specific id; derived uses `inputs` + `formula`
  frequency: daily          # daily | weekly | monthly
  release_lag_days: 1       # shift applied before scoring (point-in-time)
  transform: level          # one name, or a list applied in order (see transforms below)
  risk_when: high           # high | low | extreme (far from normal in either direction)
  revised: false            # true = historical values are revised figures
  unit: "pp"                # display unit: "%", "pp" (percentage points), or ""
  plausible: [0, 10]        # sanity range for the raw value
  rules:                    # optional hard overrides (§5.1)
    - {when: "value > 4", min_score: 86, label: "Spread above 4 points: crisis-level stress"}
  explain: >
    Extra yield investors demand to lend to riskier companies. Widening spreads
    mean lenders fear defaults and often lead stock declines.
```

Transforms: `level`; `yoy_pct` (% change vs 12 months earlier); `change_3m|6m|12m` (absolute difference, for series already in % or rate units); `pct_change_6m` (% change, for ratios and counts); `ann_6m` (6-month % change, annualized); `ma_4w` (4-week moving average); `pct_from_ma200` (% above/below 200-day moving average); `drawdown_252d` (% below the running 252-trading-day high); `ma_30d` (30-day moving average, for noisy daily series); `sum_12m` (rolling 12-month sum).

`config.py` validates every entry on load (known source, known category, known transform, `explain` present, unique ids) and the pipeline fails fast if invalid.

### 4.2 v1 indicators (44)

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
| credit | baa_spread | FRED `BAA10Y` | level | high | |
| credit | baa_aaa_spread | derived: `BAA10Y` − `AAA10Y` | level | high | |
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
| policy | epu_daily | FRED `USEPUINDXD` | ma_30d | high | |
| policy | epu_trade | FRED `EPUTRADE` | level | high | |
| policy | epu_monetary | FRED `EPUMONETARY` | level | high | |
| policy | geopolitical_risk | GPR daily index (matteoiacoviello.com) | ma_30d | high | |
| policy | deficit_gdp | derived: −`MTSDS133FMS` summed 12m ÷ nominal `GDP` × 100 | level | high | R |
| policy | auction_demand | TreasuryDirect: bid-to-cover, mean of last 3 10Y + 30Y auctions | level | low | |
| policy | fed_path | derived: `DGS2` − `DFF` | level | extreme | |

`risk_when: extreme` scores as 2 × |percentile − 50|: a market pricing big cuts *or* big hikes both signal a policy shift.

Rule overrides in v1:

| Indicator | Rule | Effect |
|---|---|---|
| curve_10y2y, curve_10y3m | value < 0 | min_score 86, "Curve inverted" |
| curve_10y2y, curve_10y3m | crossed from < 0 to > 0 within last 6 months | min_score 86, "Curve un-inverting: recession often begins here" |
| sahm_rule | value ≥ 0.5 | min_score 90, "Sahm Rule triggered" |
| baa_spread | value > 4 | min_score 86 |
| vix | value > 40 | min_score 90 |
| vix_term | value > 1 | min_score 86, "Near-term fear above longer-term fear" |

Exact `release_lag_days` per series are set during implementation from each series' release schedule (daily market data: 1; weekly claims/STLFSI/NFCI: 5–7; monthly CPI/PCE/payrolls/etc.: 14–45; quarterly SLOOS: ~35 after quarter end; Shiller: 30).

### 4.3 Not tracked yet (`config/untracked.yaml`)

Shown on the Overview in a "Not tracked yet" panel with a one-paragraph explanation each and why it isn't tracked: forward P/E, earnings revisions, AAII sentiment, put/call ratio, margin debt, high-yield and investment-grade (ICE BofA) spreads (FRED is licensed to publish only the last 3 years, too short to score; the Moody's Baa spread back to 1986 is used instead), Treasury auction tails (needs when-issued yields, which aren't freely available).

### 4.4 Data history coverage

Coverage varies by series (e.g. S&P 500 and yields to the 1950s+, EPU and GPR 1985, VIX 1990, Baa spread 1986, VIX3M 2007, RSP 2003; auction data starts at the earliest auction the TreasuryDirect API returns). Where data doesn't exist, the site shows "no data" — never backfilled or imputed.

## 5. Scoring

### 5.1 Indicator risk score (0–100)

For each indicator on each date *t*:

1. Apply `release_lag_days`: a value observed for period *p* becomes "known" only on its release date. Forward-fill known values to daily.
2. Apply `transform`.
3. **Point-in-time percentile:** rank the transformed value at *t* against all transformed values known on or before *t* (expanding window). Require ≥ 5 years of history; earlier dates get no score.
4. If `risk_when: low`, score = 100 − percentile; else score = percentile.
5. Apply `rules`: if a rule matches, score = max(score, `min_score`) and record the rule label.

Output per indicator per day: `value`, `score`, `rule_label` (nullable), `stale` flag. Staleness is computed for every past day, not just today, so historical composites exclude data that was out of date at the time. Stored as wide tables (date × indicator), one per column.

**Light:** green < 60, amber 60–85, red > 85.
**Trend arrow:** score(t) − score(t − 63 trading days): ↑ if ≥ +10, ↓ if ≤ −10, → otherwise.

### 5.2 Category score

Equal-weighted mean of the non-stale, available indicator scores in the category. A category with no available indicator is "no data".

### 5.3 Overall risk score

Weighted mean of available category scores using `config/weights.yaml` (default: all 1.0). Requires ≥ 5 of 9 categories; otherwise "no data".

**Bands:** Calm 0–40, Normal 40–60, Elevated 60–75, High 75–100.

**Contribution** of category *c* = w_c × (score_c − 50) / Σw. The contributions sum to (overall − 50).

### 5.4 Regime

- **Growth direction:** mean of z-scores (point-in-time) of `cfnai_ma3`, −`claims_4w`, `permits_yoy`; "rising" if its 6-month change > 0.
- **Inflation direction:** core PCE 6-month annualized minus core PCE 12-month YoY; "rising" if > 0.
- Four regimes: Growth ↑ Inflation ↓ ("Goldilocks"), Growth ↑ Inflation ↑ ("Reflation"), Growth ↓ Inflation ↓ ("Slowdown"), Growth ↓ Inflation ↑ ("Stagflation").
- **Confirmation:** the daily signal is noisy (it flipped 465 times in 60 years), so the dashboard switches regime only after a new one has been signalled for 42 consecutive trading days (about 2 months; ~105 switches in 60 years). The page also shows the latest unconfirmed signal.
- For each regime, the site shows the average annualized S&P 500 and bond return (mean daily log return × 252) while that regime was in place, using the previous day's regime to avoid look-ahead.

### 5.5 Return series

- Stocks: S&P 500 price (`^GSPC`) for charts and outcome statistics; S&P 500 total return (`^SP500TR`, from 1988) for the simulation (§9.6).
- Bonds from 2002: total return of `AGG` and `TLT` (Yahoo adjusted close).
- Bonds before 2002: estimated 10-year Treasury total return from `DGS10`: monthly return ≈ y(t−1)/12 − D × Δy, with D = 8. Labelled "estimated" wherever shown.

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
  tags: [credit, banking]    # filters: fed, inflation, credit, banking, policy, election, geopolitics, valuation, global
  summary: >                 # one line, used in lists, time machine, emails
    US housing loans went bad, big banks nearly failed, and lending froze worldwide.
  explain:
    background: >            # what the world looked like before, for someone who wasn't there
      Banks had lent heavily to home buyers with weak credit, then packaged those
      loans into securities sold around the world, often borrowing 30x their capital to do it.
    chain:                   # cause → effect, one step per line, plain English
      - House prices stopped rising, so weak borrowers could no longer refinance and began to default.
      - Securities built from those loans lost value; nobody knew which banks held the worst ones.
      - Banks stopped lending to each other; Lehman Brothers failed in September 2008.
      - Businesses couldn't borrow, cut jobs and spending, and the economy fell into recession.
    why_stocks: >
      Company profits collapsed in the recession, and bank shares were hit hardest because
      their losses could wipe out their capital.
    why_bonds: >
      Investors fled to safety: US Treasuries rallied as the Fed cut rates to zero, while
      corporate bonds fell because investors feared companies would default.
    signals: >               # how the dashboard's factors showed it, for learning
      Credit spreads and the financial stress index rose through 2007, the yield curve
      had inverted in 2006, and jobless claims climbed from early 2008.
    lesson: >
      Credit markets warned well before the stock market's worst months. When lenders get
      scared, pay attention.
    terms: [credit_spread, leverage, yield_curve]   # links to glossary (§10.4)
```

`anchor` dates are approximate. `history.py` finds the exact peak and trough in the relevant asset (S&P 500 for stock events, the bond return series for bond events) within ±30 days of each anchor. All text fields are hand-written, aimed at a reader with no finance background, and every term that needs it links to the glossary. Every number shown is computed. `config.py` rejects an event missing `summary` or any `explain` field.

`kind: event` is used for events that matter for learning but are not a crash or rally (e.g. elections); they get full event pages but are excluded from the crash statistics in §7.3–§7.4.

Initial events (25):

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
| event | stocks+bonds | 2016 presidential election | 2016-10-01 → 2016-12-31 |
| event | stocks+bonds | 2020 presidential election | 2020-09-01 → 2020-11-30 |
| event | stocks+bonds | 2024 presidential election | 2024-09-01 → 2024-12-31 |

Policy-driven events already in the list are tagged `policy` or `fed` (e.g. Volcker shock, taper tantrum, US downgrade, Q4 2018, 2019 pivot, 2022, April 2025 tariffs), so the History page can filter to "events caused by government or Fed decisions".

### 7.2 Event page

1. Story, written for a beginner: summary; background; the cause → effect chain drawn as numbered steps; why stocks moved and why bonds moved; what the dashboard's signals showed; the lesson; glossary terms as tap-to-define links. Plus computed peak/trough dates and % moves for stocks and bonds.
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

- **Fingerprint:** vector of the 9 category scores plus their 63-day changes.
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
  "indicators": {"baa_spread": {"value": [...], "score": [...]}, ...},
  "regime": ["slowdown", ...],
  "spx": [...], "bond": [...]
}
```

Scores are rounded to integers and values to 3 significant figures; `null` for no data. The page lazy-loads only the years it needs. A small `site/data/tm/index.json` lists available years, the indicator metadata (name, category, revised, explain), and events with their computed dates.

### 8.2 Day view

- Date picker, plus URL parameter `?date=YYYY-MM-DD` for linking. Non-trading days snap to the previous trading day, with a note.
- Shows, as of that day: overall score and band, its percentile at the time, regime, the 9 category lights with trend arrows, category contributions, and all indicator scores/lights (revised badges, rule labels).
- Events in progress on that day, or starting within the next 12 months, each with its one-line `summary` and a link to its event page.
- **What happened next:** S&P 500 and bond returns over the next 1, 3, 6, 12 months and max drawdown in 12 months. Hidden behind a "Show what happened next" toggle so the user can form a view first.

### 8.3 Range view

- From/to pickers, plus `?from=YYYY-MM-DD&to=YYYY-MM-DD`.
- Chart: overall score with band shading; toggle to overlay category scores; S&P 500 and bond series on a second panel; events in the range shaded and labelled.
- Summary: start/end/min/max/average score (with dates for min/max), days spent in each band, list of band changes with dates, S&P 500 and bond return over the range.

### 8.4 Links in

Event pages link to the range view for their window; the Overview's score history chart links clicked points to the day view.

## 9. Model report card (backtest)

A page and a module (`backtest.py`, `simulate.py`) that answer "how much should I trust this model?" Recomputed every daily run from the stored point-in-time scores, so it never uses information the model wouldn't have had.

### 9.1 Definitions

- **Bad stock outcome** for day *t*: S&P 500 max drawdown ≥ 15% within the next 252 trading days.
- **Bad bond outcome** for day *t*: bond series (§5.5) max drawdown ≥ 5% within the next 252 trading days.
- Days whose 252-day forward window extends past today are excluded.
- **Warning:** the overall score is Elevated or High for ≥ 10 consecutive trading days.

### 9.2 Metrics

1. **Discrimination (AUC):** how well the overall score separates days followed by a bad outcome from days that weren't, computed separately for stocks and bonds. Shown with a plain-English grade: < 0.6 weak, 0.6–0.7 modest, 0.7–0.8 useful, ≥ 0.8 strong; and the explanation "0.5 = coin flip".
2. **Band check:** % of days followed by a bad outcome, per band. Passes if the % rises from Calm → Normal → Elevated → High; the page shows pass/fail.
3. **Event hit rate:** share of crash events (§7.1, `kind: crash`) with a warning that began within the 12 months before the event's computed start; median lead time in trading days.
4. **False alarms:** High-band episodes (≥ 20 consecutive trading days) not followed by a bad stock outcome within 12 months of the episode's start; reported as a count and per decade.

### 9.3 Confidence ranges

- Day-based metrics (AUC, band percentages): 90% intervals from a **block bootstrap** (block length 252 trading days, 1,000 resamples), because consecutive days are not independent.
- Event-based metrics (hit rate): 90% Wilson interval on the event count.
- Every metric is shown with its range, and the page states the number of crash events in the sample with the warning: "With only N crashes in the data, every figure here is uncertain."

### 9.4 Robustness checks

- **Two periods:** all metrics for 1990–2007 and 2008–today separately.
- **Leave one category out:** AUC and hit rate with each category removed in turn (indicator scores are reused; only the composite is recomputed).
- **Threshold sensitivity:** metrics with all band cutoffs shifted −5 and +5.
- **Hindsight note:** the indicators were chosen knowing past crises; this bias cannot be removed and is stated on the page.

### 9.5 Baselines

The same metrics for three single-indicator models, using each indicator's own score as the "overall" score: `curve_10y3m`, `vix`, `baa_spread`. The page states plainly whether the composite beats each baseline (AUC difference with its bootstrap interval).

### 9.6 "What if" investment simulation (educational)

Shows whether acting on the signals would have helped, and what it would have cost.

- **Portfolios:**
  - **Hold 60/40:** 60% stocks / 40% bonds, rebalanced monthly.
  - **Signal-based:** allocation set by the overall band (hysteresis-adjusted) at each month-end, from `config/simulation.yaml` (default: Calm/Normal 60/40, Elevated 50/50, High 40/60).
  - **Hold 100% stocks:** reference line.
- **Mechanics:** the month-end band decides the allocation, applied at the next trading day's close (no same-day look-ahead). Trading cost 0.1% of the amount traded. Taxes ignored.
- **Returns:** stocks = S&P 500 total return (`^SP500TR`); bonds = §5.5. The simulation starts at the later of 1990-01-01 and the first month with an overall score.
- **Outputs:** growth of $10,000 chart; drawdown chart; allocation over time; table of annualized return, volatility, max drawdown, longest time to recover, number of switches; per-crash-event table (drawdown of each portfolio during the event); per-decade returns; held-out period (2015–today) shown separately; 90% block-bootstrap interval on the annualized return difference vs Hold 60/40.
- **Labelling:** a banner on the simulation and on every chart: "Educational. Built with hindsight, ignores taxes, not a recommendation. Past results do not predict future results." The text summary describes misses and lagging periods as prominently as successes.

### 9.7 Confidence on today's score

One line on the Overview and in alert emails, computed from §9.2–§9.3, e.g. "When the model said Elevated in the past, a 15%+ stock drop followed within 12 months 28% of the time (likely range 18–40%). Model accuracy 0.72: useful, not reliable."

### 9.8 Comparing alternative setups (CLI)

```
python -m dashboard.backtest --weights alt_weights.yaml [--indicators alt_indicators.yaml] [--bands 40,60,75] [--simulation alt_sim.yaml]
```

Writes `out/compare.html`: every §9.2 metric, the simulation table, and the robustness checks for the current and alternative setups side by side. The headline comparison uses the **held-out period 2015–today**; full-history numbers are shown but labelled "includes the period the change may have been tuned on". The CLI prints an over-tuning warning when more than 3 comparisons have been run in the same day (tracked in `out/compare-log.json`).

## 10. Context: calendar, elections, and news

None of this section affects the risk score. It gives the user the "what's going on and what's coming" context around the numbers, and teaches why each kind of event matters.

### 10.1 Upcoming-events calendar (`config/calendar.yaml`)

Each event **type** carries a beginner explanation:

```yaml
- type: fomc
  name: Fed interest-rate decision (FOMC)
  why_it_matters: >
    The Fed sets the short-term interest rate that every other borrowing cost builds on.
    Higher rates make borrowing costlier for companies and households and make bonds
    you already own worth less; cuts do the opposite.
  what_to_watch: The rate decision, the "dot plot" of future rate expectations, and the press conference tone.
  typical_reaction: Biggest moves come from surprises versus what markets expected, not the decision itself.
  related_indicators: [fed_funds_chg, fed_path, ust10y_chg, epu_monetary]
```

Types and date sources:

| Type | Dates from |
|---|---|
| FOMC meeting, Jackson Hole symposium | `calendar.yaml`, maintained manually (the Fed publishes a year ahead) |
| CPI, jobs report, PCE, GDP, retail sales | FRED release-dates API |
| 10Y and 30Y Treasury auctions | TreasuryDirect upcoming-auctions API |
| Presidential and midterm elections | `calendar.yaml` (fixed dates) |
| Debt-ceiling deadlines, government-funding deadlines | `calendar.yaml`, maintained manually |
| Earnings season start | `calendar.yaml` (approximate quarterly dates) |

If manually maintained types have fewer than 60 days of future dates, the Monday email includes a "calendar needs updating" reminder.

### 10.2 Elections

- Election dates appear in the calendar with their explanation (why elections matter: tax, trade, regulation, and spending policy can change; uncertainty tends to rise beforehand).
- Past elections are full event pages in the history playbook (§7.1).
- The Learn page includes a **presidential cycle** section: median S&P 500 and bond returns by year of the 4-year cycle, computed from the data since 1950, with the sample size (~19 cycles) and a plain warning that the pattern is weak and inconsistent.
- Pre-election uncertainty is captured in the score through the policy-uncertainty indicators, not through the election date itself.

### 10.3 News context

- **Sources (free, no paid keys):** official RSS feeds (Federal Reserve press releases, US Treasury press releases, BLS releases) and the GDELT DOC API for market-relevant headlines from major outlets.
- Only headline, source, date, and link are stored and shown — no article text.
- **Topics (`config/news_topics.yaml`):** keyword rules tag each headline with a topic: Fed & rates, inflation, jobs & economy, tariffs & trade, fiscal & debt ceiling, elections & politics, geopolitics, banking & credit, energy, earnings. Each topic has a `why_it_matters` explanation and `related_indicators`, e.g. "Tariffs & trade: tariffs raise costs for importers and invite retaliation, which can slow growth and push up prices. Watch: trade policy uncertainty, dollar, copper/gold." Headlines matching no topic are dropped.
- Up to 5 headlines per topic per day, deduplicated by URL and normalized title. Kept for 90 days in `data/context/news/<date>.json`.
- **Context only:** headlines never feed the score.

### 10.4 Glossary (`config/glossary.yaml`)

Every finance term used on the site, in events, or in emails (e.g. yield curve, credit spread, basis point, duration, inflation breakeven, leverage, recession) has a one-paragraph plain-English definition. Terms are tap-to-define links everywhere they appear. A test fails if an event's `terms` references a missing glossary entry.

## 11. Alerts

### 11.1 State and triggers

`data/state.json` stores, as of the last run: overall band and score, each category light, each indicator light, regime, and for curves/Sahm the rule-label set.

Triggers (compared with stored state):
1. Overall band change
2. Category light change
3. Indicator light change
4. Rule label appears or disappears (e.g. curve inverts / un-inverts, Sahm triggers)
5. Regime change
6. Overall score up ≥ 10 points vs 5 trading days ago, without a band change

### 11.2 Hysteresis

Moving up through a threshold uses the threshold; moving back down requires falling 5 points below it. Applies to indicator/category lights (60, 85) and overall bands (40, 60, 75). The stored state holds the hysteresis-adjusted light/band, which is what pages display as "current".

### 11.3 Emails

- At most one alert email per run, a digest of all triggers, ordered by the trigger list above.
- Subject: `[Market Watch] <most important change>`; e.g. `[Market Watch] Risk score Normal → Elevated (63)`.
- Body (HTML with plain-text fallback): what changed (with each indicator's `explain` text); the 4-line rationale (outcome stats for the current band, top 3 drivers, nearest threshold, model confidence line from §9.7); top "Today looks like" match with its base rate; dashboard link; data health footer.
- **Headlines this week:** up to 5 headlines from topics whose `related_indicators` moved, each with its topic's one-line `why_it_matters`.
- **Weekly summary** every Monday run regardless of triggers: overall score and 7-day change, category lights, regime, top drivers, data health, plus **"This week"** — the calendar events in the next 7 days, each with its `why_it_matters`.
- `--dry-run` writes the email to `out/email-preview.html` instead of sending.
- Disclaimer footer: "Informational only, not financial advice."

## 12. Reliability and error handling

- **Fetch:** each source call retries 3 times with exponential backoff (2s, 4s, 8s). On final failure, the indicator keeps its last raw data and is marked stale; the run continues.
- **Staleness:** an indicator is stale if its last observation is older than 2× its normal interval plus its release lag. Stale indicators are excluded from category and overall scores and show a "stale since <date>" badge.
- **Sanity:** raw values outside `plausible` are dropped and logged; an indicator whose latest value is dropped is treated as stale. Alerts never fire from implausible data.
- **Context failures are non-fatal:** if calendar or news sources fail, the run continues and the panels show "unavailable today".
- **Coverage:** the overall score always displays "based on N/44 indicators".
- **Whole-run failure:** the workflow fails visibly (GitHub's failure email to the repo owner). The site is not redeployed and state is not updated, so the next successful run diffs against the last good state.
- **Partial-run rule:** alerts send only after the scores and state are computed successfully; the commit step runs after the deploy.
- **Repo activity:** the daily data commit prevents GitHub from disabling the schedule after 60 days of inactivity.

## 13. Site pages

All pages share a header with navigation and a footer with the last update time, data health, and the disclaimer.

1. **Overview** (`index.html`): overall score gauge with band, short rationale, and the model confidence line (§9.7); regime quadrant; 9 category lights with trends (linking to category pages); "What changed" (7 and 30 days); "Today looks like…"; **Coming up** (next 14 days of calendar events, each expandable to its explanation); **In the news** (recent headlines by topic, each topic expandable to why it matters); overall score history (5 years, events shaded); data health; "Not tracked yet" panel.
2. **Category pages** (`category/<id>.html`): indicator cards (value, score, light, trend, spectrum bar, 10-year chart with events shaded, explanation, badges).
3. **Why this score?** (`why.html`): full rationale (§6.2–§6.5) and full-history score chart.
4. **History** (`history/index.html` with filters by kind and tag, `history/<event_id>.html`, `history/which-factors-warned.html`).
5. **Time machine** (`time-machine.html`).
6. **Model report card** (`report-card.html`): §9.2–§9.6, with a plain-English summary at the top ("what this model is good at, and where it fails").
7. **Learn** (`learn.html`): methodology (§6.7), "what moves markets" (every calendar event type and news topic with its explanation), presidential cycle (§10.2), glossary, limitations, disclaimer.

## 14. Testing

All unit tests run offline on fixtures.

- **No look-ahead (critical):** for a fixture dataset, compute scores; append future data; recompute; assert all scores at dates ≤ the original end date are identical. Covers indicator, category, overall, and regime.
- **Release lag:** a monthly value becomes visible exactly on its release date.
- **Signals:** percentile scoring, `risk_when` flipping, each transform, rule overrides and labels, 5-year minimum history.
- **Composite:** weighted means, missing categories, ≥ 5-of-9-category requirement, contributions summing to overall − 50.
- **Rationale:** outcome table excludes incomplete forward windows; change attribution sums to Δoverall; nearest-threshold logic.
- **History:** anchor-to-exact-date search, lead-time calculation, hit/false-alarm rates on a synthetic event set, similarity with partial dimensions, base rate.
- **Backtest:** outcome labels exclude incomplete windows; AUC on a synthetic score with known separation; Wilson and block-bootstrap intervals (seeded, deterministic); hit/false-alarm counting; leave-one-out and threshold shifts; baselines.
- **Simulation:** allocations change only at month-end and apply next day; trading cost; hand-computed 3-month example matches; a perfect-foresight signal is impossible (shuffled future data doesn't change past allocations).
- **Alerts:** each trigger type, hysteresis (no flip-flop around a threshold), Monday summary, dry-run output.
- **Reliability:** retry/backoff, stale marking, implausible-value dropping, exclusion from composite.
- **Config:** every entry in `indicators.yaml`, `events.yaml`, `weights.yaml`, `untracked.yaml`, `calendar.yaml`, `news_topics.yaml`, `glossary.yaml` validates; every event has `summary` and all `explain` fields; every event `terms` entry and every calendar type exists; every `related_indicators` id exists.
- **Context:** release-date and auction API parsing; headline topic tagging, dedup, and 90-day retention; context failures don't fail the run.
- **Scoring extras:** `risk_when: extreme`, `ma_30d`, `sum_12m`, derived deficit series.
- **Fetchers:** parse recorded FRED / Yahoo / Shiller / GPR / TreasuryDirect / RSS / GDELT responses; one optional live smoke test (`pytest -m live`), not run in CI.
- **Site:** build from fixtures; all pages exist; all internal links resolve; time-machine JSON matches the schema and covers the expected years.

`ci.yml` runs the offline tests on every push and PR. `daily.yml` runs them before the pipeline and aborts on failure.

## 15. Build milestones

1. **Skeleton:** repo layout, config loading/validation, FRED + Yahoo fetchers, signals for 5 indicators (baa_spread, curve_10y3m, vix, cpi_yoy, sahm_rule), basic Overview, daily workflow deploying to Pages.
2. **Full scoring:** all 44 indicators, Shiller / GPR / TreasuryDirect sources, composite, regime, rationale, category pages, "Why this score?", Learn page.
3. **History and time machine:** event library with full beginner explanations, glossary, event pages, which-factors-warned, "Today looks like", time machine.
4. **Report card:** backtest metrics, confidence ranges, robustness checks, baselines, simulation, today's confidence line, comparison CLI.
5. **Context:** calendar (with event-type explanations), elections and presidential-cycle section, news headlines with topic explanations.
6. **Alerts:** state, triggers, hysteresis, digest and Monday emails (including headlines and "This week"), secrets.
