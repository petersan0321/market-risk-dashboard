# Stock Pages — Design Spec

Extends the market risk dashboard (`2026-10-14-market-risk-dashboard-design.md`, "the main spec")
with one page per stock on a watchlist. Same audience: a beginner investor who wants a plain-English
explanation and caveat next to every number.

## 1. Purpose

For each watchlist stock, show what the business, its price, its valuation and its risks look like
today, how it compares with competitors, how it behaved in past market crises, and which dates are
coming up, each with a note on what to consider.

### Success criteria

- Every watchlist stock has a page with the 11 sections in §4, rebuilt daily with the rest of the site.
- Every number has a "what this means" note. Every light shows its rule and why.
- The page links the stock to the main dashboard: today's market score, regime, and the crash events.
- One stock failing to download never stops the run or the other stocks.
- No personal data in the repo; no new secrets required.

### Non-goals

- **No overall stock score or buy/sell rating.** It would read as a recommendation and can't be
  back-tested honestly the way the market score was (main spec §9).
- No live search of arbitrary tickers (needs a paid data service or a server).
- No intraday data, options data, or portfolio tracking.

## 2. Architecture

A new pipeline step, **stocks**, runs after context (main spec §2) and before alerts, so it can read the
market score, regime series, event windows and calendar, and so alerts can use its results.

```
config/watchlist.yaml ─┐
config/stock_metrics.yaml ─┤
                           ▼
dashboard/stocks/fetch.py      download (Yahoo Finance, Wikipedia S&P 500 list), save to data/stocks/
dashboard/stocks/peers.py      choose 3-5 competitors
dashboard/stocks/analyse.py    pure functions, one per section (§4); returns plain dicts
dashboard/stocks/notes.py      plain-English notes, lights, "what stands out", things to consider
dashboard/stocks/run.py        orchestrates per stock; isolates failures
dashboard/site/build.py        renders stocks/index.html and stocks/<TICKER>.html
```

### 2.1 Configuration

`config/watchlist.yaml`:

```yaml
stocks:
  - {ticker: AAPL}
  - {ticker: MSFT}
  - {ticker: NVDA}
  - {ticker: GOOGL}
  - {ticker: AMZN}
# Optional per stock: peers: [DELL, HPQ, MSFT]   (overrides the automatic choice, 3-5 tickers)
```

The initial file has no overrides. After the first build I review the automatic picks and propose
overrides to the user where they are clearly off.

`config/stock_metrics.yaml`: for every metric shown, `name`, `what_it_is`, `why_it_matters`, and (where
lit) the light rule text. Keeping the wording in config matches the main dashboard (indicators, glossary).
New glossary terms go in `config/glossary.yaml`: moving_average, rsi, beta, pe_ratio, forward_pe, peg_ratio,
price_to_sales, ev_ebitda, free_cash_flow, fcf_yield, operating_margin, return_on_equity, net_debt,
dividend_yield, payout_ratio, ex_dividend_date, eps, earnings_surprise, guidance, short_interest,
analyst_rating, price_target, insider_transaction, market_cap.

### 2.2 Data sources

All free, no keys:

| Source | What | Notes |
|---|---|---|
| Yahoo Finance (`yfinance`) | daily prices (full history), `info` (ratios, holders %, short interest, analyst targets, forward EPS), `calendar`, `get_earnings_dates(limit=48)`, annual and quarterly income / cash-flow / balance sheet, dividends, institutional holders, insider transactions, recommendations, upgrades/downgrades, EPS trend, news | Unofficial; may break. Annual statements cover about 4 years; quarterly EPS history covers about 12 years. |
| Wikipedia "List of S&P 500 companies" | ticker, name, GICS sector and sub-industry, CIK | Used only for competitor choice. |

The SEC's EDGAR API was considered for longer financial history. It rejects requests without a
contact email in the User-Agent, and the user's email must not appear in code, so it is not used.

### 2.3 Storage (`data/stocks/`, committed by the daily job)

```
data/stocks/universe.json          S&P 500 list + market caps, refreshed when older than 7 days
data/stocks/<TICKER>/prices.csv    date, close, adj_close, volume (full history; appended daily)
data/stocks/<TICKER>/snapshot.json everything else from Yahoo, with fetched_at per part
data/stocks/<TICKER>/earnings.csv  quarterly: date, eps_estimate, eps_reported, surprise_pct
```

Peers are stored the same way (prices and snapshot), so peer tables work offline with `--no-fetch`.

### 2.4 Reliability

- Each stock (and each part of its snapshot) is fetched independently with the existing retry/backoff.
  A failure keeps the last saved copy.
- Every section shows its data date. A part older than 3 trading days gets a **stale** badge.
- A section whose inputs are missing shows "Not available" with the reason, not an error.
- Implausible values (negative price, P/E > 1000, yield > 25%) are dropped and noted.

## 3. Competitor choice (`peers.py`)

1. Find the stock's GICS sub-industry in the universe.
2. Candidates: other members of that sub-industry, with share classes of the same company removed
   (same CIK, for example GOOG and GOOGL).
3. If fewer than 3 candidates, widen to the GICS sector.
4. Rank by closeness in size: `|log(market cap) − log(stock's market cap)|`; take the closest 4.
5. A `peers:` override in the watchlist replaces steps 1-4 entirely.

The page always says how the peers were chosen ("Same industry group: Semiconductors, closest in size"
or "Chosen by hand"). A stock not in the S&P 500 with no override gets no peer table and says why.

## 4. Page sections

Lights use the main dashboard's green / amber / red with an icon and label (never colour alone). Each
light states its rule and a one-sentence "why". Thresholds below are the initial rules; they live in
`config/stock_metrics.yaml`.

### 4.1 Snapshot (top banner)

Name, ticker, sector and industry, price, 1-day and 1-year change, market value, a 52-week range bar
with today marked, and the data date.

**What stands out:** three bullets picked from these, in priority order:
1. earnings within 14 days;
2. any red light (fundamentals, valuation, risk), most severe first;
3. within 5% of the 52-week high or low;
4. more than 20% below the all-time high;
5. valuation in the top or bottom 10% of its own 10-year range;
6. otherwise, the 1-year change versus the S&P 500.

### 4.2 Price and technicals

- 5-year price chart with 50- and 200-day moving averages; market-crash events shaded (as on the main
  site).
- Price vs the 200-day average (above / below, by how much); 50-day vs 200-day.
- RSI (14-day): labelled "stretched" above 70, "washed out" below 30, otherwise "neutral".
- Distance from the 52-week and all-time high.
- Relative strength: 3-, 6- and 12-month return minus the S&P 500's.

Technicals are **descriptive labels, not lights**: they describe recent price behaviour and the note says
they do not predict the future on their own.

### 4.3 Fundamentals (business health)

Trend charts: revenue, operating income, net income, free cash flow (annual, about 4 years, plus the
last 4-5 quarters as trailing-12-month sums where available).

| Metric | Green | Amber | Red |
|---|---|---|---|
| Revenue growth (TTM vs prior TTM) | ≥ 5% | 0–5% | < 0 |
| Operating margin change (TTM vs a year earlier) | up > 1 pt | within ±1 pt | down > 1 pt |
| Free cash flow (TTM) | positive and growing | positive, shrinking | negative |
| Net debt / EBITDA | < 1 (or net cash) | 1–3 | > 3 |

Also shown without lights: gross and operating margin, return on equity, cash, debt.

### 4.4 Valuation

Metrics: trailing P/E, forward P/E, PEG, price-to-sales, EV/EBITDA, free-cash-flow yield.

- **Own history:** trailing P/E over 10 years = month-end price ÷ trailing-12-month reported EPS (sum of the
  last 4 quarters from `earnings.csv`). Today's percentile in that history, with a range bar.
  Light: green below the 33rd percentile, amber 33rd–67th, red above the 67th. Shown only with ≥ 3 years
  of positive EPS; otherwise "not meaningful" with the reason (losses or too little history).
- **Versus competitors:** each metric against the peer median. Shown as a premium or discount, no light.
- Note: the P/E history uses Yahoo's reported (usually adjusted, "non-GAAP") EPS, which can differ from
  the official figure.

### 4.5 Earnings and events

- Next earnings date and the consensus EPS and revenue estimate (range low–high).
- Last 8 quarters: estimate, reported, surprise %, and the 1-day price move after each report.
- **Typical earnings move:** median absolute 1-day move over the last 12 reports. The move is measured
  from the close before the report to the close after it (reports after the close count on the next day).
- Estimate trend: current-year EPS estimate today vs 30 and 90 days ago (`eps_trend`), labelled rising /
  flat (±1%) / falling.

### 4.6 Dividends

Yield, annual dividend per share, payout ratio, years of consecutive increases, the next or last
ex-dividend date, and a 10-year dividend-per-share chart. No dividend: says so and explains that the
company returns cash through buybacks or reinvestment instead. A dividend begun within 3 years (e.g.
GOOGL) says when it started. Light on payout ratio: green < 60%, amber 60–90%, red > 90%.

### 4.7 Ownership and sentiment

- Held by institutions % and insiders %; top 10 institutional holders.
- Insider transactions, last 6 months: number and value of buys and sells. The note explains that
  insider sales are often pre-planned or for taxes, while open-market buys are rarer and more telling.
- Short interest (% of float). Light: green < 3%, amber 3–10%, red > 10%.
- Analyst ratings: latest month's count of strong buy / buy / hold / sell / strong sell; upgrades and
  downgrades in the last 90 days.
- Company headlines (Yahoo news): up to 5 recent ones. **Context only**, as on the main site.

### 4.8 Risk (connected to the market dashboard)

- Beta vs the S&P 500 (5 years of weekly returns, computed by us) and annualised volatility (1 year, daily).
- Largest drop from a peak: all-time and last 5 years.
- **Past crashes:** for each `kind: crash` event in `config/events.yaml` with stocks in `assets` that falls
  inside the stock's price history, the stock's peak-to-trough fall within the event's window vs the
  S&P 500's, linked to the event page. Light on the median ratio (stock fall ÷ market fall): green < 0.9,
  amber 0.9–1.3, red > 1.3.
- **By economic regime:** median annualised 3-month return in each regime (main spec §5.4) since the
  stock's history starts, vs the S&P 500, and today's regime highlighted.
- **Interest-rate sensitivity:** 5-year regression of weekly stock returns on weekly 10-year-yield changes,
  shown as "% move per 1-point rise in the 10-year yield", labelled as a rough historical relationship.
- **Today's line:** "Market risk is <band> (<score>). In past crashes this stock fell about <ratio>× as
  much as the market."

### 4.9 Competitor comparison

A table of the stock and its peers: market value, 1-year return, revenue growth, operating margin,
trailing and forward P/E, PEG, EV/EBITDA, dividend yield, beta, largest 5-year drop. Each column shows
where the stock ranks. Peer tickers link to their own page if they are also on the watchlist.

### 4.10 Upside vs downside

Shown together on one horizontal range chart against today's price:

- **Analysts:** low, mean and high price target and the number of analysts. Note: analyst targets lean
  optimistic and are rarely set below the current price.
- **Bull / base / bear:** forward EPS × the 90th / 50th / 10th percentile of the stock's own 10-year P/E
  (§4.4). Not shown when the P/E history is not meaningful.
- **Stress case:** today's price × (1 − the stock's median fall in past crashes, §4.8), and its worst fall.
- Each figure shows the % change from today. The note states that these are scenarios built from
  history and estimates, not forecasts.

### 4.11 Key dates and things to consider

Next 60 days, sorted by date: the stock's earnings date and ex-dividend date, plus the FOMC, CPI and jobs
dates from the main calendar (main spec §10.1). Each item has a rule-based "things to consider" note:

| Item | Note template |
|---|---|
| Earnings in ≤ 14 days | "Earnings on <date> (<n> days). The price has typically moved <x>% on earnings days; a position opened before then carries that event risk. Analysts' estimates have been <rising/flat/falling>." |
| Ex-dividend date | "To receive the next dividend (<amount>) you must own the shares before <date>. The price usually drops by about the dividend amount that day." |
| FOMC / CPI / jobs | "<event>. This stock has moved about <rate sensitivity>% per 1-point rise in the 10-year yield, so rate surprises tend to <matter more / matter less> for it than for the average stock." (more if the absolute sensitivity is above the S&P 500's) |

Notes never say buy or sell.

### 4.12 Watchlist overview (`stocks/index.html`)

A table of all watchlist stocks: price, 1-day and 1-year change, P/E percentile in own history, next
earnings date, past-crash ratio, and each section's lights in a compact row, with links to each page.

## 5. Alerts (extends main spec §11)

- **Monday summary:** a "Watchlist this week" section listing earnings and ex-dividend dates in the
  next 7 days.
- **Daily digest trigger:** a watchlist stock whose 1-day move exceeds 2 × its 1-year daily standard
  deviation (roughly 1 trading day in 20 for a typical stock). Text: "NVDA −7.2% today (usual daily
  range ±3.1%)". Priority after the existing triggers.

## 6. Site

- New nav tab **Stocks** → `stocks/index.html`; stock pages at `stocks/<TICKER>.html`.
- Charts use the existing Plotly build and chart styling; every chart has a table view.
- Footer disclaimer as on the main site, plus: "Company data from Yahoo Finance; not official and may
  contain errors."

## 7. Testing

All offline on fixtures (recorded Yahoo responses for one stock and two peers; a small universe file).

- **Fetch parsing:** prices, snapshot parts, earnings dates; failures keep the saved copy and mark stale.
- **Peers:** sub-industry choice, share-class dedup by CIK, widening to sector, size ranking, override.
- **Analyse:** each section on fixtures with hand-checked values: TTM sums, P/E history and percentile,
  typical earnings move (including after-close timing), dividend streak, insider aggregation, beta,
  crash falls within event windows, regime returns, rate sensitivity, scenarios.
- **No look-ahead in history-based figures:** P/E history at a month uses only EPS reported by that date.
- **Notes:** each light at its boundaries; "what stands out" priority; each key-date template; no note tells
  the reader to buy, sell or hold (checked against a list of advice phrases such as "you should buy").
- **Missing data:** loss-making stock (P/E not meaningful), no dividend, not in the S&P 500, too-short
  history.
- **Alerts:** big-move trigger at the boundary; Monday watchlist section.
- **Config:** watchlist and stock_metrics validate; every glossary term referenced exists.
- **Site:** stock pages and overview build from fixtures; all internal links resolve.

## 8. Build milestones

7. **Data and peers:** watchlist config, fetchers and storage, universe and peer choice, overview table
   skeleton, Stocks tab.
8. **Sections 4.1–4.7:** snapshot, technicals, fundamentals, valuation, earnings, dividends, ownership,
   with metric explanations and glossary terms.
9. **Sections 4.8–4.11 and alerts:** risk tie-in, competitor table, upside vs downside, key dates,
   alert additions, review of automatic peer picks.
