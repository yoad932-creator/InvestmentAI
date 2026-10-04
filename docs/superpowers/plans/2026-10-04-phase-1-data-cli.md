# Phase 1 Data and CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a reproducible, offline-capable Swing daily-data pipeline and CLI with Massive ingestion, SQLite snapshots, automatic liquidity eligibility, indicators, and SPY context.

**Architecture:** A synchronous Python application separates provider transport, canonical market data, transactional storage, and pure analytics. Ingestion seals immutable snapshots; analysis reads only a selected snapshot at an explicit cutoff and never calls the provider. Provider replacement changes the adapter and composition root, not feature calculations or repository consumers.

**Tech Stack:** Python 3.12; standard-library dataclasses, Decimal, sqlite3, argparse, tomllib, zoneinfo, math/statistics; httpx for HTTP; python-dotenv for optional local secrets; exchange_calendars and tzdata for sessions; pytest and pytest-socket for offline testing. Pin resolved dependency versions during implementation, not during this planning task.

**Spec:** [Approved InvestmentAI design](../../specs/2026-10-04-investment-ai-design.md), particularly sections 1–5, 8, 14–17. The user's Phase 1 instructions resolve provider = Massive and benchmark = SPY and take precedence over broader later-phase deliverables.

## Global Constraints

- "The system, not the user, determines whether an instrument is liquid enough for new setups."
- "Swing v0.1 evaluates the fixed 20–50-stock candidate universe using daily data."
- "Require 150 valid sessions as an initial EMA stabilization policy."
- "Zero denominators yield unavailable values and reason codes, not infinities."
- "No missing return is replaced by zero."
- "Missing sessions must be distinguished from exchange holidays, halts, and provider gaps; never fabricate tradable bars by forward-filling."
- "SQLite initially, PostgreSQL when justified."
- Horizons remain 5, 10, and 20 sessions with 10 primary, as configuration metadata only; Phase 1 computes no future outcome labels.
- CLI only: no FastAPI, React, ML, LLM, broker integration, real-money execution, opportunity score/state engine, backtest, or paper portfolio. No stubs for these are necessary.
- Report `eligible`/`ineligible` and reason codes, not AVOID/WATCH/SETUP/READY. The later state vocabulary in the spec is deliberately not implemented in Phase 1.
- Keep active databases/cache outside the OneDrive checkout; default on Windows to `%LOCALAPPDATA%/InvestmentAI`. Tests use temporary directories.
- This document is a plan only. Do not execute its commands, create its code/config/fixtures, install dependencies, or make its commits until implementation is separately requested.

## Review Focus

Five concrete failure classes receive explicit tests below:

1. A late revision or a split after the analysis cutoff must not change a pinned earlier analysis (Tasks 4, 7, 9, 14).
2. The second API page fails or a process stops after caching it: no partial dataset becomes a successful snapshot (Tasks 6, 8).
3. A holiday, DST change, early close, or unavailable current daily bar must not be misclassified as a usable complete observation (Tasks 3, 8).
4. One volume spike hides otherwise illiquid sessions, or missing values silently shorten a window: automatic rejection must be reproducible (Task 10).
5. A valid stock lacks aligned SPY history or the long volatility baseline: show explicit unavailable context, not neutral/bullish defaults (Tasks 12, 13).

---

## 1. Decisions that make the plan executable

### Massive constraints and adapter scope

Documentation checked 2026-10-04. Massive lists Basic Stocks at five requests per minute, two years of historical data, and end-of-day delivery. Treat these as configurable entitlement assumptions, not permanent guarantees; never silently upgrade a subscription. [Massive Stocks plans](https://www.massive.com/stocks).

Use direct REST via httpx to keep retries, caching, request accounting, and pagination under our control. Daily bars use `/v2/aggs/ticker/{symbol}/range/1/day/{from}/{to}`, `adjusted=false`, ascending order, and an explicit limit. Follow `next_url`; the limit counts base aggregates, not necessarily returned bars. Map `o,h,l,c,v,t` into canonical records and preserve source timestamps. Missing aggregates do not prove a provider outage or zero volume. [Custom bars contract](https://massive.com/docs/rest/stocks/aggregates/custom-bars).

Metadata uses `/v3/reference/tickers/{ticker}` with an as-of date. Do not treat a ticker as a permanent identity or infer sector from free text. [Ticker overview](https://massive.com/docs/rest/stocks/tickers/ticker-overview).

Use the currently documented `/stocks/v1/splits` and `/stocks/v1/dividends` contracts, including pagination and date filters. Map split ratios and effective dates; preserve cash dividend dates, amount, and currency. Do not use cumulative adjustment factors that incorporate later actions to adjust an earlier analysis. Quickstart examples still show legacy reference endpoints; endpoint-specific contracts govern the adapter. [Splits](https://massive.com/docs/rest/stocks/corporate-actions/splits), [dividends](https://massive.com/docs/rest/stocks/corporate-actions/dividends).

Authenticate with a Bearer header; keys never belong in stored URLs, request-cache keys, snapshots, exception strings, or CLI output. [REST authentication](https://massive.com/docs/rest/quickstart).

### Explicit provisional research/operational settings

These are implementation defaults for exercising the pipeline, not claims that the thresholds are optimal. Record them in each config hash and eligibility result; changing them creates a different analysis identity.

| Setting | Phase 1 value / behavior |
|---|---|
| Candidate list | Configurable 20–50 unique U.S. stocks; SPY is additional context, not counted toward 20–50 |
| Example candidates | AAPL, MSFT, AMZN, GOOGL, META, NVDA, JPM, BAC, WFC, GS, V, MA, XOM, CVX, JNJ, PG, KO, PEP, WMT, HD; this is a test list, not investment advice or a liquidity assertion |
| Liquidity window | 20 completed sessions, with no skipped missing session |
| Minimum average dollar volume | USD 20,000,000 |
| Separate average share-volume floor | Disabled by default; optional positive configured value, always calculate the metric |
| Daily consistency floor | USD 10,000,000 on at least 16 of 20 sessions (fraction ≥ 0.80) |
| Single-session concentration | Largest daily dollar volume / total 20-session dollar volume ≤ 0.20 |
| Required policy fields | Missing/invalid required fields fail configuration validation; no per-symbol manual override |
| Initial requested history | 550 calendar days before selected end date; report actual coverage and warmup shortfalls |
| Entitlement floor | Two calendar years before injected current date; out-of-range requests fail with `history_not_entitled`, never silently shorten |
| Incremental update | Re-fetch last five completed sessions plus new sessions; re-fetch actions over the entire stored analysis range; `--refresh` re-fetches the explicit full range |
| Request scheduling | One network request at a time, minimum 13 seconds between request starts across all endpoints and retries; no catch-up bursts |
| Timeout / attempts | 10-second connect, 30-second read; at most four attempts total per request |
| Retry policy | Transport timeout, 429, 500/502/503/504 only; exponential delays 1/2/4 seconds plus injected jitter in [0,1], combined with rate-limiter delay and Retry-After |
| Retry-After ceiling | If server asks for > 120 seconds, end the run resumably with a typed error; do not retry earlier than requested |
| Coverage profile | `massive_daily_vendor_v1`, explicitly not guaranteed regular-session-only; preserve source bar boundary semantics |
| Conservative availability | For session D, assume not ready before 08:00 America/New_York on D+1 calendar day, and never before exchange close. This is a policy assumption, not a provider finality guarantee |
| Returns | Phase 1 momentum/relative strength are split-adjusted price returns, explicitly excluding cash dividends; future total-return labels/ledger remain out of scope |

At roughly 51 instruments × four initial endpoint requests, even unpaginated bootstrap is tens of minutes under this request budget. Show progress and resume information; no one-minute live-ingestion promise. Cached analytics remain a separate performance target. More than 150 stock bars does not guarantee enough SPY bars for volatility context: 273 aligned closes are needed for 252 preceding 20-return volatilities plus the current observation.

### Temporal and persistence contract

- `as_of` is always an aware UTC instant; `session` is an exchange date. Date-only CLI input is not accepted for `--as-of` to avoid ambiguous intraday cutoffs.
- Every observation carries event/source time, assumed or observed availability, first ingestion time, provenance hash, and availability basis. Historical imports use `reconstructed_history`; their availability is a labeled assumption and cannot establish true historical revision availability.
- Observation-mode updates set availability to `max(policy_ready_time, first_seen_at)` and retain that original first-seen time on retries. Revisions append records with their own availability. Historical reconstructed and observed analyses are never presented as equivalent.
- A snapshot chooses exact bar, metadata, corporate-action, calendar, config, and quality-report revisions. Reads filter both session/event time and `available_at <= as_of`, and only within the snapshot membership. No implicit latest-row query.
- Empty history, incomplete action coverage, and missing observations are explicit coverage/quality states. Lack of an action event differs from failure to retrieve the action history.
- Staging and request cache can survive failed runs, but only a complete acquisition/validation pass can atomically seal a snapshot. A completed acquisition may seal a snapshot containing documented per-instrument quality exclusions; an API failure cannot seal a partial one.
- Snapshot ID is the SHA-256 of canonical manifest bytes. Include selected revision IDs, provenance, versions, and policy; exclude volatile run timestamps/IDs from the content identity. Repeating an unchanged ingestion reuses observations and the same content snapshot; run records remain distinct.

## 2. File and responsibility map

All paths below are repository-relative. Every listed production file is future work. Create `src/investment_ai/__init__.py` and use namespace subpackages; no empty modules for later phases. Prefer files below roughly 200 lines; split only along an interface already identified here, not into speculative abstractions.

| Area | Exact planned files | Responsibility |
|---|---|---|
| Setup/config | `pyproject.toml`, `requirements.lock`, `.gitignore`, `.env.example`, `config/phase1.toml`, `src/investment_ai/__init__.py`, `src/investment_ai/config.py`, `src/investment_ai/errors.py` | Reproducible environment, validated public policy, separately loaded secrets |
| Domain | `src/investment_ai/domain/instruments.py`, `bars.py`, `actions.py`, `provenance.py` in the same domain directory | Immutable market records and explicit time semantics |
| Calendar | `src/investment_ai/market/calendar.py` | Session schedule and availability policy |
| Quality/actions | `src/investment_ai/market/quality.py`, `src/investment_ai/market/adjustments.py` | Quality diagnostics and causal split normalization |
| Provider | `src/investment_ai/providers/base.py`, `transport.py`, `massive.py`, `massive_mapping.py`, `fixture.py` | Replaceable contract, bounded HTTP, Massive mapping, offline import |
| Storage | `src/investment_ai/storage/database.py`, `migrate.py`, `ingest_store.py`, `snapshots.py`, `cache.py`, `analysis_store.py`, `migrations/001_market_data.sql`, `migrations/002_analysis.sql` under storage | Transactions, migrations, append-only records, manifests, request cache |
| Ingestion | `src/investment_ai/ingestion/planner.py`, `service.py` in ingestion | Range planning, staging, resumability, atomic publication |
| Analytics | `src/investment_ai/analytics/windows.py`, `liquidity.py`, `smoothing.py`, `indicators.py`, `context.py`, `service.py` | Pure calculations, aligned benchmark context, stored-data orchestration |
| CLI | `src/investment_ai/cli.py`, `src/investment_ai/__main__.py`, `src/investment_ai/reporting.py` | Composition root, commands, deterministic text/JSON |
| Fixtures/docs | `tests/fixtures/phase1/*` (enumerated in Task 14), `README.md`, `docs/phase1-data-contract.md` | Offline evidence and operational instructions |
| Tests | Exact test files are assigned per task below; `tests/conftest.py`, `tests/support.py` | Network prohibition, temporary storage, injected clock and shared record builders |

## 3. Shared execution and type conventions

For each task, write each named behavior test first, run it and observe a meaningful red assertion, implement only that behavior, and rerun. Add the minimal declaration returning an empty/unsupported value if needed to get past import collection into a behavioral failure; do not call a broken import the only red evidence. Refactor only after green. Before each commit run both the task command and the accumulated full suite, `python -m pytest -q`. The default pytest configuration disables sockets; provider tests use httpx.MockTransport and a fake clock, never a real endpoint or wall-clock sleep.

Commands assume a separately authorized implementation session at the repository root, with its virtual environment active. During Task 1 only, prepare it with `py -3.12 -m venv .venv`, activate it in PowerShell with `.\.venv\Scripts\Activate.ps1`, and install the eventual project using `python -m pip install -e ".[test]"`. Resolve/pin runtime and test dependencies in `requirements.lock`, verify with `python -m pip check`, and record the Python/library versions. Do not initialize Git or commit unrelated files silently; if the checkout has no Git repository, report that commit boundaries are pending repository setup.

`date`, `datetime`, `timedelta`, `Decimal`, `Path`, `Mapping`, `Sequence`, and `Iterator` refer to standard-library types. IDs are strings; UTC is enforced at constructors. Dataclasses are frozen except operational run state. JSON uses sorted keys, fixed decimal strings for raw decimal values, no NaN/Infinity, and declared schema versions. Calculation outputs are floats or explicit unavailable values. All exact constructors below may use keyword-only fields.

## 4. Implementation tasks

### Task 1: Validated configuration and reproducible project foundation

**Files:** Create `pyproject.toml`, `requirements.lock`, `.gitignore`, `.env.example`, `config/phase1.toml`, `src/investment_ai/__init__.py`, `src/investment_ai/config.py`, `src/investment_ai/errors.py`, `tests/conftest.py`, `tests/test_config.py`.

**Interfaces:** Produce frozen `LiquidityPolicy(window: int, min_average_dollars: Decimal, min_average_shares: Decimal | None, daily_dollar_floor: Decimal, min_passing_fraction: float, max_day_fraction: float, version: str)`; `ProviderPolicy(base_url: str, history_years: int, request_interval_seconds: float, attempts: int, ready_hour_et: int)`; `Settings(mode: str, timeframe: str, benchmark: str, universe: tuple[str, ...], horizons: tuple[int, ...], primary_horizon: int, data_dir: Path, liquidity: LiquidityPolicy, provider: ProviderPolicy)`; `load_settings(path: Path, env: Mapping[str, str]) -> Settings`; `load_api_key(env: Mapping[str, str], dotenv_path: Path | None) -> str`; `config_hash(settings: Settings) -> str`. Define `InvestmentAIError(code: str, message: str)`, with typed `ConfigError`, `ProviderError`, `StorageError`, and `DataError` subclasses. Secrets are not Settings fields. The semantic config hash excludes data_dir and all other machine-local paths; store those only in operational run metadata.

- [ ] **Tests first:** `test_phase1_defaults` asserts mode `swing`, timeframe `1d`, benchmark `SPY`, horizons `(5,10,20)`, primary `10`, and 20 configured candidates. `test_universe_bounds` rejects 19/51, duplicates after normalization, SPY in candidate list, and empty symbols. `test_missing_liquidity_policy_fails` rejects absent/negative thresholds and fractions outside [0,1]. `test_env_overrides_dotenv` asserts environment key wins and neither key appears in Settings repr/hash. `test_offline_needs_no_key` loads configuration with no secret. `test_data_directory_default` asserts the local-app-data path, not cwd/OneDrive. Scope-changing settings fail validation.
- [ ] **Red:** `python -m pytest tests/test_config.py -q`; expected failures for the missing configuration validation behavior.
- [ ] **Implement:** Define the interfaces in `config.py`, use TOML and explicit dotenv opt-in, and add the defaults above. Add console entry metadata `investment-ai = investment_ai.cli:main` (the target arrives in Task 13). Ignore `.env`, virtual environments, active SQLite/journal files, and cache; `.env.example` contains only `MASSIVE_API_KEY=`. Configure src-layout tests and automatic socket disabling.
- [ ] **Green:** Run the task command, then `python -m pytest -q` and `python -m pip check`; all pass without an API key.
- [ ] **Commit boundary:** Stage only the Task 1 files and commit `chore: establish phase one configuration and test environment`.

### Task 2: Canonical instruments, bars, actions, and provenance

**Files:** Create `src/investment_ai/domain/instruments.py`, `src/investment_ai/domain/bars.py`, `src/investment_ai/domain/actions.py`, `src/investment_ai/domain/provenance.py`, `tests/test_domain.py`, `tests/support.py`.

**Interfaces:** Produce:

- `Provenance(provider: str, payload_hash: str, source_revision: str, available_at: datetime, ingested_at: datetime, availability_basis: str)`; `canonical_hash(value: Mapping[str, object]) -> str`.
- `Instrument(instrument_id: str, symbol: str, exchange: str, currency: str, kind: str, active: bool, provenance: Provenance)` and `SymbolAlias(instrument_id: str, symbol: str, effective_from: date, effective_to: date | None)`.
- `DailyBar(instrument_id: str, session: date, source_start: datetime, source_end: datetime, open: Decimal, high: Decimal, low: Decimal, close: Decimal, volume: Decimal, currency: str, adjustment: str, coverage_profile: str, provenance: Provenance)`; timeframe is fixed `1d` for this contract.
- `Split(instrument_id: str, effective_date: date, split_from: Decimal, split_to: Decimal, provenance: Provenance)`; `Dividend(instrument_id: str, ex_date: date, cash_amount: Decimal, currency: str, declaration_date: date | None, record_date: date | None, pay_date: date | None, provenance: Provenance)`; `CorporateAction = Split | Dividend`.

- [ ] **Tests first:** `test_decimal_roundtrip` asserts exact raw values survive canonical serialization. `test_non_utc_or_nonfinite_rejected` rejects naive timestamps, NaN, and Infinity. `test_alias_does_not_change_identity` keeps one instrument ID across alias intervals; a recycled ticker cannot merge two instrument IDs. `test_action_dates_do_not_imply_known_at` asserts declaration/effective dates never replace provenance availability. `test_payload_hash_stable` ignores mapping key order but changes on a changed price.
- [ ] **Red:** `python -m pytest tests/test_domain.py -q`; expected missing invariant/serialization failures.
- [ ] **Implement:** Add frozen records and identity validation; stable internal IDs are deterministic UUID5 values derived from a fixed project namespace and a stable source instrument identifier (prefer composite FIGI plus venue); persist provider-identifier mappings for replacement adapters. Fixture records carry fixed IDs. If metadata lacks a stable identifier, reject with identity_unresolved rather than derive identity from a ticker or assign a fresh random ID on each import. Add `stable_instrument_id(source_id: str, venue: str) -> str` to instruments.py and use the same project namespace constant in every adapter. Preserve unsupported/missing source fields as explicit errors rather than inventing currency or an action ratio. Put test-only constructors in `tests/support.py`.
- [ ] **Green:** Task command and `python -m pytest -q`; exact decimal/provenance assertions pass.
- [ ] **Commit boundary:** Stage the six Task 2 files; commit `feat: define canonical daily market data contracts`.

### Task 3: Exchange sessions and conservative availability

**Files:** Create `src/investment_ai/market/calendar.py`, `tests/test_calendar.py`; modify `tests/support.py` for a frozen clock fixture.

**Interfaces:** Produce `Session(label: date, open_at: datetime, close_at: datetime)`; `SessionCalendar.sessions(start: date, end: date) -> tuple[Session, ...]`; `SessionCalendar.expected_session(as_of: datetime) -> date`; `SessionCalendar.ready_at(session: date) -> datetime`; `SessionCalendar.export(start: date, end: date) -> tuple[Session, ...]`. Constructor `SessionCalendar(ready_hour_et: int = 8, frozen_sessions: Sequence[Session] | None = None)` wraps a pinned XNYS exchange calendar for the supported U.S. daily schedule. Snapshot replay supplies frozen sessions.

- [ ] **Tests first:** `test_holiday_and_half_day` asserts 2024-07-04 absent and 2024-07-03 close `17:00:00Z`. `test_dst` asserts open `14:30Z` on 2024-03-08 and `13:30Z` on 2024-03-11. `test_ready_cutoff` asserts Friday 2024-07-05 is unavailable at Saturday `11:59:59Z`, available at `12:00:00Z`; Monday pre-readiness uses Friday. `test_frozen_schedule_replay` does not consult the installed calendar. `test_daily_boundary_not_exchange_open` preserves vendor aggregate timestamps independently of session opens.
- [ ] **Red:** `python -m pytest tests/test_calendar.py -q`; expected incorrect/missing session/availability behavior.
- [ ] **Implement:** Use the library only through this wrapper. Pin/export the actual schedule used, including holidays and exceptional closures. Unknown exchange schedule mappings fail rather than guessing; explicitly map supported NYSE/Nasdaq/NYSE Arca daily equity schedules to the chosen policy. [Calendar library reference](https://github.com/gerrymanoim/exchange_calendars).
- [ ] **Green:** Task command and full suite; tests use no provider calls and no host-local timezone assumptions.
- [ ] **Commit boundary:** Stage Task 3 files; commit `feat: add exchange session and availability rules`.

### Task 4: Corporate-action adjustment and data quality

**Files:** Create `src/investment_ai/market/adjustments.py`, `src/investment_ai/market/quality.py`, `tests/test_adjustments.py`, `tests/test_quality.py`.

**Interfaces:** Consume `DailyBar`, `CorporateAction`, `SessionCalendar`. Produce `AdjustedBar(session: date, open: float, high: float, low: float, close: float, volume: float)`; `adjust_splits(bars: Sequence[DailyBar], actions: Sequence[CorporateAction], as_of: datetime) -> tuple[AdjustedBar, ...]`; `QualityIssue(code: str, instrument_id: str, session: date | None, severity: str)`; `QualityReport(issues: tuple[QualityIssue, ...], reliable: bool)`; `validate_history(bars: Sequence[DailyBar], actions: Sequence[CorporateAction], expected: Sequence[Session], as_of: datetime, actions_complete: bool) -> QualityReport`.

- [ ] **Tests first:** `test_two_for_one_split` maps pre-event price 100/volume 100 to 50/200 after an effective known 2:1 split; raw dollar turnover stays 10,000. `test_future_split_ignored` and `test_late_known_action_ignored` leave earlier bars unchanged. `test_reverse_split` exercises the inverse ratio. `test_dividend_not_price_adjustment` preserves close values and the dividend record. `test_quality_ohlc_volume` rejects high below open/close, low above them, nonpositive prices, and negative volume while retaining known zero volume. `test_missing_session_not_filled` reports a missing expected session; holidays do not count. `test_conflicting_duplicate` flags contradictory same-session observations. `test_unexplained_jump` quarantines an absolute split-normalized close return > 50%; an explained split does not trigger it. `test_actions_fetch_failure_not_empty_history` makes unreliable action coverage block usable analytics.
- [ ] **Red:** `python -m pytest tests/test_adjustments.py tests/test_quality.py -q`; expected adjustment and quality assertion failures.
- [ ] **Implement:** Multiply historical prices by `split_from/split_to` and volumes by its inverse only for events after the bar session, effective by the analysis session and available by cutoff. Apply all relevant events in order; no cumulative future factor. Return quality reasons including `missing_session`, `invalid_ohlcv`, `conflicting_duplicate`, `unexplained_jump`, and `incomplete_actions`. Missing bars have unknown cause unless sourced halt/no-trade evidence exists; never label them definitively as provider failure or manufacture bars.
- [ ] **Green:** Task command and full suite; raw records remain unchanged and all causal fixtures pass.
- [ ] **Commit boundary:** Stage Task 4 files; commit `feat: validate market histories and apply causal split adjustments`.

### Task 5: Replaceable provider interface

**Files:** Create `src/investment_ai/providers/base.py`, `tests/test_provider_contract.py`; modify `tests/support.py`.

**Interfaces:** Produce `ProviderCapabilities(provider: str, daily: bool, raw_bars: bool, splits: bool, dividends: bool, history_years: int)`; `ProviderBatch(instrument: Instrument, bars: tuple[DailyBar, ...], actions: tuple[CorporateAction, ...], request_hashes: tuple[str, ...], actions_complete: bool)`; protocol `MarketDataProvider.capabilities() -> ProviderCapabilities`, `MarketDataProvider.fetch(symbol: str, start: date, end: date, observed_at: datetime, availability_basis: str, *, actions_start: date, cache_not_before: datetime) -> ProviderBatch`. A batch is complete or raises `ProviderError`; HTTP page/cursor details stay inside adapters. The public operation fetches metadata, bars, and both action streams for one instrument/range. actions_start can precede the bar start during updates; cache_not_before carries the planner refresh policy through the adapter. observed_at anchors request planning only: actual first-seen time comes from each CachedResponse.fetched_at, never the earlier run start.

- [ ] **Tests first:** `test_provider_substitution` uses two minimal test adapters and asserts identical canonical batches and application behavior without provider-specific fields. `test_partial_result_is_error` asserts an action-stream failure produces no successful batch. `test_unsupported_capabilities` rejects missing raw-bar/split capability before fetching. `test_inclusive_range_contract` pins start/end session filtering. Stub adapters live only in test support.
- [ ] **Red:** `python -m pytest tests/test_provider_contract.py -q`; expected contract-check failures.
- [ ] **Implement:** Define the protocol and `require_daily_capabilities(capabilities: ProviderCapabilities) -> None` in `base.py`; avoid an abstract broker or intraday framework. Reserve source details in provenance, not feature logic.
- [ ] **Green:** Task command and full suite; both test providers satisfy the same behavior contract.
- [ ] **Commit boundary:** Stage Task 5 files; commit `feat: define replaceable daily market provider contract`.

### Task 6: Bounded HTTP, request cache contract, and Massive mapping

**Files:** Create `src/investment_ai/providers/transport.py`, `src/investment_ai/providers/massive.py`, `src/investment_ai/providers/massive_mapping.py`, `tests/test_transport.py`, `tests/test_massive.py`.

**Interfaces:** Consume `ProviderPolicy`, `MarketDataProvider`, domain records, `SessionCalendar`. Produce:

- `Clock` protocol: `now() -> datetime`, `monotonic() -> float`, `sleep(seconds: float) -> None`.
- `CachedResponse(payload: Mapping[str, object], fetched_at: datetime, request_hash: str)`; `RequestCache.get(key: str, not_before: datetime) -> CachedResponse | None`, `RequestCache.put(key: str, response: CachedResponse) -> None`.
- `JsonTransport(client: httpx.Client, api_key: str, policy: ProviderPolicy, clock: Clock, cache: RequestCache, jitter: Callable[[], float])`; `pages(path: str, params: Mapping[str, str], not_before: datetime) -> Iterator[CachedResponse]`.
- `MassiveProvider(transport: JsonTransport, calendar: SessionCalendar)` implementing Task 5; mapping functions `map_instrument(payload, provenance) -> Instrument`, `map_bar(payload, instrument, calendar, provenance) -> DailyBar`, `map_action(payload, kind: str, instrument, provenance) -> CorporateAction`, where payload is `Mapping[str, object]` and provenance is `Provenance`.

- [ ] **Tests first — transport:** `test_request_spacing` records six fake-clock request starts and asserts each gap ≥ 13 seconds, including retries/pages. `test_429_retry_after` asserts no early retry and final typed failure after four attempts. Parametrize 401/403/404 as no retry, 500→success as retry, connect timeout as retry, malformed JSON/HTTP-200 error status as failure. `test_pagination` merges two pages without truncation; repeated cursor, >100 pages, and foreign-host/non-HTTPS next URLs fail. `test_secrets_redacted` asserts a sentinel API key absent from cache, exceptions, and captured logs. `test_cache_hit_no_network` reuses a fresh complete response; stale cache triggers HTTP.
- [ ] **Tests first — mapping:** `test_raw_daily_request` asserts the daily path, `adjusted=false`, ascending sort, and configured limit through MockTransport responses. `test_metadata_and_actions` normalizes the documented fields and returns actions only when both streams complete. `test_adjusted_response_rejected` blocks `adjusted=true`. `test_out_of_range_history` produces `history_not_entitled` without HTTP. `test_timestamp_mapping` retains UTC source timestamp and ET session date. `test_no_results` preserves empty coverage instead of inventing bars.
- [ ] **Red:** `python -m pytest tests/test_transport.py tests/test_massive.py -q`; expected request-policy, parsing, and batch-completeness failures.
- [ ] **Implement:** Use a single shared transport/limiter for the whole run. Disable automatic redirects and validate continuation host/path before attaching auth. Parse Retry-After seconds and HTTP dates; combine the greatest applicable delay. Hash redacted canonical requests (provider, endpoint version, parameters, no secret); strip secret query parameters from continuations before storage. Keep provider payload parsing isolated in `massive_mapping.py`. Use monthly inclusive chunks for bars if necessary; pagination completes each chunk before the next. Map source revisions using canonical per-record content hashes where Massive lacks revision IDs. Metadata/actions cache max age is 24 hours; completed historical page responses can be reused for exact reruns, while update/refresh supplies a new `not_before` to bypass affected pages.
- [ ] **Green:** Task command and full suite; no sockets or real sleeps; failures reveal no key and no partial ProviderBatch.
- [ ] **Commit boundary:** Stage Task 6 files; commit `feat: add rate limited Massive daily provider`.

### Task 7: SQLite migrations, staging, and immutable snapshots

**Files:** Create `src/investment_ai/storage/database.py`, `src/investment_ai/storage/migrate.py`, `src/investment_ai/storage/ingest_store.py`, `src/investment_ai/storage/snapshots.py`, `src/investment_ai/storage/cache.py`, `src/investment_ai/storage/migrations/001_market_data.sql`, `tests/test_storage.py`, `tests/test_snapshots.py`, `tests/test_cache.py`.

**Interfaces:** Produce `connect_database(path: Path) -> sqlite3.Connection`; `migrate(connection: sqlite3.Connection) -> None`; `SQLiteRequestCache(connection)` implementing Task 6; `IngestStore(connection).begin(config_hash: str, request: Mapping[str, object]) -> str`, `.stage(run_id: str, batch: ProviderBatch, quality: QualityReport) -> None`, `.fail(run_id: str, code: str) -> None`; `SnapshotRef(snapshot_id: str, manifest: Mapping[str, object])`; `SnapshotStore(connection).seal(run_id: str, settings: Settings, sessions: Sequence[Session]) -> SnapshotRef`, `.get(snapshot_id: str) -> SnapshotRef`, `.bars(snapshot_id: str, instrument_id: str, as_of: datetime) -> tuple[DailyBar, ...]`, `.actions(snapshot_id: str, instrument_id: str, as_of: datetime) -> tuple[CorporateAction, ...]`, `.instrument(snapshot_id: str, symbol: str, as_of: datetime) -> Instrument`, `.quality(snapshot_id: str, instrument_id: str) -> QualityReport`. Repository methods do not commit callers' half-finished transactions.

**Schema:** `schema_migrations(version PK, checksum)`; `instruments(id PK)`; `instrument_revisions(id PK, instrument_id FK, payload, hash UNIQUE)`; `provider_identifiers(provider, source_id, instrument_id FK)`; `symbol_history(instrument_id FK, symbol, start, end)`; `universe_versions(hash PK, payload)`; `bar_revisions(id PK, instrument_id FK, session, provider, source_revision, available_at, ingested_at, payload, UNIQUE(instrument_id, session, provider, source_revision))`; `action_revisions(id PK, instrument_id FK, provider, source_revision, available_at, payload)`; `ingestion_runs(id PK, request_json, status, failure_code)`; `staged_batches(run_id, symbol, payload, quality)`; `request_cache(request_hash, fetched_at, payload_hash, payload)`; `snapshots(id PK, manifest_json)`; `snapshot_bars(snapshot_id FK, bar_revision_id FK, PRIMARY KEY(snapshot_id,bar_revision_id))`; `snapshot_actions(snapshot_id FK, action_revision_id FK, PRIMARY KEY(snapshot_id,action_revision_id))`; `snapshot_instruments(snapshot_id FK, instrument_revision_id FK, PRIMARY KEY(snapshot_id,instrument_revision_id))`. Add indexes on instrument/session/availability and snapshot membership. Revision IDs are content hashes, not auto-increment values; snapshot metadata also pins quality reports and the calendar schedule.

- [ ] **Tests first:** `test_migration_repeat` applies migrations twice with identical schema version/checksum; changed checksum fails. `test_transaction_rollback` injects failure between bar/action writes and asserts no snapshot or partial published records. `test_sealed_immutable` rejects SQL UPDATE/DELETE on snapshots and selected revision rows. `test_new_revision_preserves_old_snapshot` reads original values after a correction. `test_duplicate_ingest_deduplicates` keeps bar counts and snapshot ID unchanged. `test_cutoff_reads` excludes bars/actions/revisions available later than cutoff. `test_cache_roundtrip` retains first fetch time, hashes, and no credentials. `test_foreign_key_and_busy_failure` proves bounded busy handling and unchanged database after failure.
- [ ] **Red:** `python -m pytest tests/test_storage.py tests/test_snapshots.py tests/test_cache.py -q`; expected integrity/immutability failures.
- [ ] **Implement:** Standard sqlite3, foreign keys ON, explicit transactions, busy timeout 5 seconds and at most two further attempts for lock errors only. Use migration checksum/version checks, append-only triggers for published records, and no cascading deletion of snapshot data. Store raw Decimals as canonical decimal strings; no price rounding via SQL REAL. Seal revisions and membership in one transaction only after all planned symbols are staged. Persist the frozen calendar and runtime/config versions in the manifest. Cache is replaceable operational state; it cannot change an existing snapshot. Resolve Massive source identity consistently and reject conflicting alias mappings.
- [ ] **Green:** Task command and full suite; integrity checks pass and interrupted transactions leave old snapshots readable.
- [ ] **Commit boundary:** Stage Task 7 files; commit `feat: persist versioned market snapshots atomically`.

### Task 8: Historical ingestion and safe universe updates

**Files:** Create `src/investment_ai/ingestion/planner.py`, `src/investment_ai/ingestion/service.py`, `tests/test_ingestion.py`; modify `src/investment_ai/storage/ingest_store.py` with checkpoint reads.

**Interfaces:** Produce `IngestRequest(start: date, end: date, mode: str, availability_basis: str, resume_run_id: str | None = None)` with mode `history|update|refresh`; `SymbolRequest(symbol: str, start: date, end: date, actions_start: date, cache_not_before: datetime)`; `plan_ingestion(settings: Settings, request: IngestRequest, prior: SnapshotRef | None, now: datetime, calendar: SessionCalendar) -> tuple[SymbolRequest, ...]`; `IngestResult(run_id: str, snapshot: SnapshotRef | None, status: str, completed_symbols: tuple[str, ...], errors: tuple[str, ...])`; `ingest_universe(settings: Settings, request: IngestRequest, provider: MarketDataProvider, store: IngestStore, snapshots: SnapshotStore, calendar: SessionCalendar, clock: Clock) -> IngestResult`; `IngestStore.completed(run_id: str) -> tuple[str, ...]` and `.resume(run_id: str, request_hash: str) -> None`.

- [ ] **Tests first:** `test_universe_includes_spy_once` checks 20 candidates + SPY. `test_historical_range_and_entitlement` pins the 550-day default (start = end minus 550 calendar days) and asserts history_not_entitled when the configured provider entitlement cannot cover that range, without silently shortening it; actual coverage and warmup diagnostics remain reported. `test_update_overlap` requests the last five completed sessions plus new ones and full prior action coverage. `test_second_page_failure_no_publish` fails inside the provider and asserts previous snapshot unchanged, resumable status, and no success snapshot. `test_resume_skips_completed_symbols` reuses staged complete batches and cached successful pages. `test_resume_wrong_config_rejected` rejects changed request/config. `test_missing_expected_bar` seals a quality exclusion, not a fictitious bar. `test_rerun_idempotent` preserves snapshot identity; `test_forced_refresh_revision` makes a new snapshot when source values change. `test_not_finalized_session_excluded` excludes a current daily bar even if returned by the provider.
- [ ] **Red:** `python -m pytest tests/test_ingestion.py -q`; expected planning, resume, and atomic-publication failures.
- [ ] **Implement:** Serialize the universe in stable order and handle SPY explicitly. The context manager `acquire_ingestion_lock(path: Path) -> Iterator[None]` provides a per-data-directory OS file lock (stdlib platform wrapper inside `service.py`) prevents simultaneous ingestion commands; OS release on process death avoids persistent stale lock ownership. Wait one request interval on a new process before its first request, preserving a conservative restart budget. Abort without publication on any API failure; retain checkpoints and failure codes. Stage quality exclusions for complete but bad datasets. Update snapshots merge pinned prior records with replacement observations only for refreshed coverage; do not discard older history. Do not automatically fall back to another provider or claim missing action coverage is complete.
- [ ] **Green:** Task command and full suite; repeat ingestion changes neither published bars nor snapshot hash unless source/policy changes.
- [ ] **Commit boundary:** Stage Task 8 files; commit `feat: ingest and resume fixed universe daily history`.

### Task 9: Causal analysis windows and derived-result persistence

**Files:** Create `src/investment_ai/analytics/windows.py`, `src/investment_ai/storage/analysis_store.py`, `src/investment_ai/storage/migrations/002_analysis.sql`, `tests/test_analysis_windows.py`, `tests/test_analysis_store.py`.

**Interfaces:** Produce `AnalysisWindow(instrument: Instrument, raw: tuple[DailyBar, ...], adjusted: tuple[AdjustedBar, ...], expected_sessions: tuple[date, ...], as_of: datetime, snapshot_id: str, quality: QualityReport)`; `load_window(store: SnapshotStore, snapshot_id: str, symbol: str, as_of: datetime) -> AnalysisWindow`; `AnalysisStore(connection).save(kind: str, identity: Mapping[str, object], payload: Mapping[str, object]) -> str`. Kind is `eligibility|features`; returns deterministic result ID. Migration adds `eligibility_decisions(id PK, snapshot_id FK, instrument_id FK, as_of, policy_hash, payload)` and `feature_snapshots(id PK, snapshot_id FK, eligibility_id FK, feature_version, as_of, payload)` with append-only triggers and uniqueness over the logical identity.

- [ ] **Tests first:** `test_append_future_bars_no_change` asserts equality of all window values before/after future rows/actions are added. `test_same_session_late_revision_not_selected` enforces availability. `test_missing_day_no_compression` retains expected-session coverage failure rather than taking the last N arbitrary observations. `test_unknown_snapshot_or_symbol` gives typed data errors. `test_derived_record_immutable` rejects mutation and deduplicates identical saves; same identity/different payload is an integrity error. `test_reconstructed_flag_survives` preserves the historical availability warning.
- [ ] **Red:** `python -m pytest tests/test_analysis_windows.py tests/test_analysis_store.py -q`; expected temporal/membership/persistence failures.
- [ ] **Implement:** Define AnalysisWindow and load_window in windows.py so indicators/liquidity never import the orchestration service. Rebuild the frozen calendar from the snapshot, select only member revisions including metadata at the cutoff, enforce both cutoff checks, then adjust effective known splits. Recompute window quality over the cutoff-bounded sessions; do not attach later-session quality failures to an earlier analysis. Ingestion quality is retained as audit evidence, not blindly used as the earlier window verdict. Valid series end at `expected_session(as_of)`. No “latest available” fallback that silently hides stale data. Require 150 contiguous valid sessions for the complete stock feature bundle; shorter history can still produce a liquidity diagnostic but cannot be called full analysis readiness. Derive canonical result identities from snapshot, symbol, as-of, feature/rule versions, and configuration.
- [ ] **Green:** Task command and full suite; no network or provider dependency enters analysis-window loading.
- [ ] **Commit boundary:** Stage Task 9 files; commit `feat: add point in time analysis windows and result records`.

### Task 10: Automatic liquidity eligibility

**Files:** Create `src/investment_ai/analytics/liquidity.py`, `tests/test_liquidity.py`.

**Interfaces:** Consume `AnalysisWindow`, `LiquidityPolicy`. Produce `LiquidityMetrics(average_shares: float, average_dollars: float, median_dollars: float, passing_fraction: float, largest_day_fraction: float | None)`; `Eligibility(eligible: bool, reasons: tuple[str, ...], metrics: LiquidityMetrics | None, policy_version: str, observation_count: int)`; `evaluate_liquidity(window: AnalysisWindow, policy: LiquidityPolicy) -> Eligibility`. Serialize full threshold values and snapshot/cutoff identity when saving through `AnalysisStore`.

- [ ] **Tests first:** `test_liquid_passes` uses 20 sessions at $100 and 250,000 shares: average dollars 25,000,000, passing fraction 1, largest-day fraction 0.05, eligible true. `test_average_is_mean_of_products` uses alternating (100,100) and (200,10): average dollars 6,000, not 8,250. `test_low_liquidity_rejected` uses $100 × 10,000 and asserts `insufficient_liquidity`. `test_single_spike_rejected` uses 19 days at $1m and one at $500m and asserts `unstable_liquidity` despite average > $20m. `test_threshold_equality` accepts exactly 16/20 floor passes and concentration exactly 0.20. `test_optional_share_floor` fails only when enabled. `test_missing_stale_unreliable` pins `insufficient_history`, `stale_data`, and `unreliable_liquidity_data` as applicable. `test_all_zero_volume` rejects without division by zero; `test_split_share_basis` preserves dollar metrics and normalizes shares.
- [ ] **Red:** `python -m pytest tests/test_liquidity.py -q`; expected metric and automatic-rejection failures.
- [ ] **Implement:** Evaluate exactly the last 20 expected sessions, using raw daily products and current-basis split-adjusted share volume. Collect all determinable reason codes in sorted order, including `unstable_liquidity`; metrics are null when missing/unreliable input prevents a trustworthy calculation. Zero total turnover gives concentration unavailable internally and causes insufficient liquidity, not Infinity. No user “liquid enough” flag exists.
- [ ] **Green:** Task command and full suite; the examples above pass exactly or within 1e-12 float tolerance.
- [ ] **Commit boundary:** Stage Task 10 files; commit `feat: reject insufficient and unstable liquidity automatically`.

### Task 11: EMA, RSI, ATR, volume strength, momentum, and volatility

**Files:** Create `src/investment_ai/analytics/smoothing.py`, `src/investment_ai/analytics/indicators.py`, `tests/test_smoothing.py`, `tests/test_indicators.py`.

**Interfaces:** `ema(values: Sequence[float], period: int) -> tuple[float | None, ...]`; `wilder(values: Sequence[float], period: int) -> tuple[float | None, ...]`; `rsi(closes: Sequence[float], period: int = 14) -> float | None`; `atr(bars: Sequence[AdjustedBar], period: int = 14) -> float | None`; `volume_strength(volumes: Sequence[float], period: int = 20) -> float | None`; `momentum(closes: Sequence[float], sessions: int) -> float | None`; `volatility(closes: Sequence[float], period: int = 20) -> float | None`; `FeatureBundle(ema20: float | None, ema50: float | None, rsi14: float | None, atr14: float | None, volume_strength: float | None, momentum: Mapping[int, float | None], volatility20: float | None, unavailable: Mapping[str, str])`; `calculate_features(window: AnalysisWindow) -> FeatureBundle`.

- [ ] **Tests first — smoothing:** `test_ema_seed` asserts mean seed at index period−1; on closes 1..21, EMA20 ends 11.5; on 1..51, EMA50 ends 26.5. `test_wilder_seed_update` checks seed mean and next value `(old*(n−1)+new)/n`. `test_rsi_edges` asserts flat=50, only gains=100, only losses=0; 14 changes require 15 closes. `test_rsi_mixed` uses seven +1 and seven −1 changes for RSI=50, followed by +1 for 53.57142857142857.
- [ ] **Tests first — other indicators:** `test_atr_gap` checks true range with previous close and a constant 2-unit true-range sequence giving ATR14=2; use a prior close plus 14 true ranges (15 bars), then verify a Wilder update. `test_volume_excludes_current` uses twenty volumes of 100 and current 200 for 2.0. `test_momentum_horizons` uses closes 100..120 giving 5-session return `120/115−1`, 10=`120/110−1`, 20=0.20. `test_sample_volatility` uses 20 log returns alternating +0.01/−0.01 and expects `sqrt((20*0.01**2)/19)*sqrt(252)`. Flat prices produce 0. Parametrize zero denominator, nonfinite input, insufficient observations, and missing expected sessions to unavailable/error outcomes, never fabricated zero. `test_bundle_warmup` marks bundle fields unavailable below 150 contiguous sessions.
- [ ] **Red:** `python -m pytest tests/test_smoothing.py tests/test_indicators.py -q`; expected formula/seed/warmup failures.
- [ ] **Implement:** Pure functions, no provider indicators or implicit pandas defaults. EMA alpha `2/(n+1)`; Wilder seed arithmetic mean. ATR explicitly requires previous close for every true range in its seed. Use sample standard deviation (`ddof=1`), natural log returns, annualization sqrt(252). Return decimal-fraction returns internally; format percentages only in reporting. Invalid raw numerical input raises DataError; legitimate warmup/zero-denominator absence becomes None with a bundle reason. Compute from all contiguous history in the pinned window, not a silently moving EMA seed.
- [ ] **Green:** Task command and full suite; independent hand calculations match with absolute tolerance 1e-10 (or tighter where exact).
- [ ] **Commit boundary:** Stage Task 11 files; commit `feat: calculate daily swing indicators from causal windows`.

### Task 12: SPY relative strength and market regime

**Files:** Create `src/investment_ai/analytics/context.py`, `tests/test_context.py`.

**Interfaces:** Consume aligned `AnalysisWindow` and indicator functions. Produce `MarketRegime(trend: str, volatility: str, reasons: tuple[str, ...])`; `relative_strength(stock: AnalysisWindow, benchmark: AnalysisWindow, sessions: int = 20) -> float | None`; `market_regime(benchmark: AnalysisWindow) -> MarketRegime`.

- [ ] **Tests first:** `test_relative_strength` asserts 10% stock return minus 4% SPY = 0.06; different start/end session or missing interior expected bar gives None. `test_trend_bull_bear_sideways` asserts close > EMA20 > EMA50 and positive five-session EMA20 slope is bullish, strict inverse bearish, flat/equality sideways. `test_regime_warmup` requires 150 contiguous bars for trend, otherwise unknown. `test_volatility_uses_prior_252` verifies the threshold excludes current observation using a hand-specified volatility series and tests strict greater-than versus equality. `test_273_closes_required` marks volatility unknown at 272 closes. `test_missing_spy` returns explicit unavailable context, not neutral values. `test_future_benchmark_bar_ignored` proves cutoff invariance.
- [ ] **Red:** `python -m pytest tests/test_context.py -q`; expected alignment/regime failures.
- [ ] **Implement:** Require identical session coverage for the return interval; do not align by array position or compress gaps. Compute historical rolling volatilities and use a documented linear percentile at index `(n−1)*0.8` for 252 preceding values. Trend and volatility can be independently unknown. No state gate, score, forecast, or entry permission is implemented here.
- [ ] **Green:** Task command and full suite; short history remains honestly unknown while computable stock diagnostics remain available.
- [ ] **Commit boundary:** Stage Task 12 files; commit `feat: add aligned SPY strength and market context`.

### Task 13: Stored-data analysis service and CLI commands

**Files:** Create `src/investment_ai/cli.py`, `src/investment_ai/__main__.py`, `src/investment_ai/reporting.py`, `src/investment_ai/analytics/service.py`, `tests/test_analysis.py`, `tests/test_cli.py`.

**Interfaces:** Produce `AnalysisReport(symbol: str, snapshot_id: str, as_of: datetime, eligibility: Eligibility, features: FeatureBundle | None, relative_strength20: float | None, regime: MarketRegime, warnings: tuple[str, ...], status: str)`; `analyze_symbol(store: SnapshotStore, results: AnalysisStore, settings: Settings, snapshot_id: str, symbol: str, as_of: datetime) -> AnalysisReport`; `render_json(report: AnalysisReport) -> str`; `render_text(report: AnalysisReport) -> str`; `main(argv: Sequence[str] | None = None) -> int`.

CLI contract:

- `investment-ai --config config/phase1.toml ingest-universe --start YYYY-MM-DD --end YYYY-MM-DD [--update | --refresh] [--resume RUN_ID]` uses Massive unless `--fixture PATH` is supplied in Task 14. Validate incompatible flags. Output run ID, progress on stderr, snapshot ID and per-symbol quality summary on stdout. New history defaults to reconstructed availability; update defaults to observed first-seen availability and labels the distinction.
- `investment-ai --config config/phase1.toml analyze AAPL --snapshot SNAPSHOT_ID --as-of 2026-10-03T12:00:00Z --format json|text` reads stored data only. The timestamp is illustrative; data coverage is checked.
- Exit codes: 0 usable eligible analysis/successful acquisition; 2 invalid arguments/config; 3 ineligible or insufficient stock data; 4 provider/storage failure; 5 missing/stale SPY required context. Insufficient long SPY volatility history alone is a warning with unknown volatility, not exit 5 when SPY is otherwise current and aligned.

- [ ] **Tests first:** `test_analyze_stored_valid` asserts expected indicator fields, eligibility, snapshot/version provenance, SPY context, and zero network calls without an API key. `test_ineligible_has_no_features` asserts reason codes, null feature bundle, no opportunity-state fields, and exit 3. `test_missing_benchmark_diagnostic` retains stock diagnostics with explicit unknown context and exit 5. `test_ingest_cli` uses a test provider at the composition boundary, checks inclusive dates, progress, exit codes, and snapshot ID. `test_cli_no_future_asof` enforces selected snapshot and cutoff. `test_cli_errors_redact_key` rejects untrusted raw exception output. `test_cli_no_forbidden_fields` asserts absence of score, expected return, confidence, orders, and states.
- [ ] **Red:** `python -m pytest tests/test_analysis.py tests/test_cli.py -q`; expected service/CLI contract failures.
- [ ] **Implement:** Evaluate and persist eligibility first, including rejections; only then compute/persist eligible stock features. Benchmark context bypasses stock liquidity gating because it is context. Feature unavailability and liquidity eligibility are distinct: an otherwise liquid 100-bar stock has insufficient feature history. Assemble deterministic JSON with sorted keys and explicit units/versions/availability warnings; no generation timestamp in the reproducible report body. The composition root constructs Massive only for network ingestion. Analyze opens repository services without loading an API key. Both installed command and `python -m investment_ai` call `main`.
- [ ] **Green:** Task command and full suite; valid stored symbol works offline, automatic rejection has the documented exit code, and only requested commands appear in `--help`.
- [ ] **Commit boundary:** Stage Task 13 files; commit `feat: expose daily ingestion and stored analysis through CLI`.

### Task 14: Offline fixture, fault-injection acceptance, and operating guide

**Files:** Create `src/investment_ai/providers/fixture.py`, `tests/fixtures/phase1/manifest.json`, `tests/fixtures/phase1/config.toml`, `tests/fixtures/phase1/instruments.json`, `tests/fixtures/phase1/sessions.csv`, `tests/fixtures/phase1/bars.csv`, `tests/fixtures/phase1/actions.json`, `tests/fixtures/phase1/expected.json`, `tests/fixtures/phase1/README.md`, `tests/test_offline_acceptance.py`, `README.md`, `docs/phase1-data-contract.md`; modify `src/investment_ai/cli.py` to wire `--fixture` and `tests/test_cli.py` to exercise it.

**Interfaces:** `FixtureProvider(root: Path)` implements the same `MarketDataProvider` contract; file formats carry the canonical records from Task 2 and frozen calendar from Task 3. `manifest.json` holds hashes, schema version, deterministic observed-at time, and availability basis; validate hashes before ingesting. No fixture-only switch inside the analytics engine.

- [ ] **Tests first:** `test_offline_end_to_end` ingests the fixture into two fresh temporary SQLite stores and asserts identical snapshot IDs and byte-identical JSON reports for the same symbol/cutoff. `test_fixture_illiquid_rejected` returns exit 3 plus `insufficient_liquidity`; `test_fixture_spike_rejected` returns `unstable_liquidity`. `test_future_mutation_does_not_change_past_report` compares semantic outputs for identical prior inputs/cutoff despite added future values and checks the earlier snapshot remains byte-identical. `test_api_failure_then_resume` injects a timeout after cached pages, resumes, and compares the final result against uninterrupted ingestion. `test_storage_failure_during_seal` injects a write failure and checks previous snapshot hashes plus `PRAGMA integrity_check = ok`. `test_repeat_ingestion` checks counts and snapshot identity. `test_cli_symbol_from_stored_data` launches the real CLI subprocess with empty API-key environment and disabled sockets and checks exit 0/features. `test_concurrent_ingestion_lock` rejects a second process cleanly.
- [ ] **Red:** `python -m pytest tests/test_offline_acceptance.py tests/test_cli.py -q`; expected missing fixture importer and end-to-end behavior failures.
- [ ] **Implement:** Check in synthetic, clearly labeled data for the 20 configured candidate symbols plus SPY over actual exchange sessions from 2024-10-01 through 2025-12-30 (at least 300 sessions). Fix fixture clock and evaluation cutoff to 2025-12-31T13:00:00Z; use reconstructed availability. Fixture import defaults to the manifest range, not the live 550-day default. Include valid, illiquid, single-spike, split, flat-price, and deliberately missing/stale cases. Keep the main eligible symbol and SPY complete. The manifest records exact evaluation cutoff and expected eligible/rejected symbols; `expected.json` contains independently reviewed indicator values and reason codes, never outputs regenerated by the production functions. Explain hand calculations and synthetic sequence definitions in fixture README. Fix the clock for fixture ingestion; repeated data-directory changes must not affect content hashes. Each run's absolute local paths remain operational metadata, outside snapshot identity.
- [ ] **Document:** In `README.md`, include install/test commands, key configuration without sample secrets, universe configuration, initial ingestion/update/refresh/resume, API budget expectations, local database location, and offline example below. In `docs/phase1-data-contract.md`, document schema/units, adjustment and calendar policy, reconstructed-history limitation, provisional liquidity thresholds, unknown values, and provider replacement steps. Document a consistent SQLite backup via its backup API as an operational procedure, not another CLI feature. No live API test is required to pass the suite; optionally verify one ticker during a later authorized implementation session without making acceptance depend on network access.
- [ ] **Green:** Run `python -m pytest tests/test_offline_acceptance.py tests/test_cli.py -q`, then `python -m pytest -q`, then `python -m pip check`. Expected: all pass offline, fixture reports match reviewed values, no credentials or forbidden phase features. Manual offline smoke after implementation: `python -m investment_ai --config tests/fixtures/phase1/config.toml ingest-universe --fixture tests/fixtures/phase1`; copy its snapshot ID, then run `python -m investment_ai --config tests/fixtures/phase1/config.toml analyze AAPL --snapshot SNAPSHOT_ID --as-of 2025-12-31T13:00:00Z --format json`. Fixture config defaults its runtime storage outside the checkout like normal configuration.
- [ ] **Commit boundary:** Stage only Task 14 files; commit `test: prove reproducible offline phase one pipeline`.

## 5. Self-review of this plan

Reviewed against the approved specification and all 27 Phase 1 deliverables. Tasks 1–3 cover structure/configuration/key handling/models/calendar; Tasks 4–8 cover actions/provider/SQLite/ingestion/validation/snapshots; Tasks 9–12 cover causal analysis, automatic eligibility, all requested indicators, SPY strength, and market regime; Tasks 13–14 cover both CLI commands, offline fixtures, and integrated evidence.

Dependencies are sequential where contracts require it. In particular, derived features do not exist until snapshot time filters are defined; eligibility runs before stock feature analysis; the Massive adapter cannot leak transport payloads into analytics. Each task has files, exact produced/consumed interfaces, named test assertions, red/green commands, expected behavior, and a commit boundary.

Resolved planning ambiguities: no Phase 2 state names in Phase 1 output; dividends are preserved but not silently included in price-return indicators; ATR uses 14 true ranges with a preceding close; 273 closes are required for the full SPY volatility regime; conservative vendor-daily readiness is distinct from the regular-session close; acquisition completion is distinct from data quality. Provisional liquidity thresholds are explicitly versioned rather than left to manual stock judgment. Provider history limits produce coverage errors, not fabricated warmup data.

Remaining factual limits are exposed by design: historical revisions may not be truly point-in-time, daily turnover does not prove opening execution capacity, current data entitlements can change, and vendor data can be corrected. These do not justify implementing a backtester, broker, wider scanner, or advanced model in this phase. Test commands in this plan have not been run: no product code or dependencies were created during planning.

## 6. Phase 1 acceptance checklist

Complete these only during implementation, backed by the named tests and their actual outputs:

- [ ] **No future-data leakage:** Tasks 3/4/7/9/12/14 prove session, availability, snapshot-membership, action-effectiveness, and revision cutoffs. Appending future observations cannot alter earlier numerical results; pinned historical outputs stay unchanged. Reconstructed-history assumptions are visibly labeled, never claimed as verified historical availability.
- [ ] **Calculations match fixtures:** Task 11 verifies EMA20/EMA50 seed/update, RSI14 edges/mixed changes, ATR14 gaps, prior-20 volume strength, 5/10/20 momentum, and sample annualized volatility; Task 12 verifies aligned SPY strength and regime. Task 14 matches independent expected values.
- [ ] **API failures do not corrupt stored data:** Timeout, 429 exhaustion, bad JSON, unauthorized/entitlement failure, failed later pages/actions, and failed SQLite sealing publish no partial snapshot. Existing snapshots and integrity checks remain valid.
- [ ] **Ingestion can be rerun safely:** Duplicate retries/pages/runs do not duplicate bars or create different snapshots for unchanged inputs; changed observations append revisions; resume and update preserve old history and audit provenance.
- [ ] **Offline run is reproducible:** Two clean databases using the pinned fixture/config/calendar/clock produce identical content snapshot IDs and analysis JSON without sockets or API keys; the entire suite passes with network access disabled.
- [ ] **Illiquid/ineligible instruments are rejected automatically:** Low turnover, unstable one-day spikes, insufficient history, stale data, and unreliable volume produce stored machine-readable reasons; no manual override or opportunity feature bundle bypasses the gate.
- [ ] **CLI analyzes a valid stored symbol:** `analyze` returns eligibility, all supported daily indicators, SPY context, cutoff, snapshot/version provenance, and explicit unavailable warnings from stored data with exit 0 for a valid complete fixture.
- [ ] **Universe command works:** `ingest-universe` supports the configured 20–50 candidates plus SPY, safe history/update/refresh/resume, provider abstraction, bounded request budget, pagination, and cache reuse.
- [ ] **Scope remains Phase 1:** No opportunity scoring or states, return forecasts, ML/LLM, FastAPI/React, scanner expansion, backtest, paper trading, broker, or real-money execution code is present.
- [ ] **Full verification recorded:** `python -m pytest -q` and `python -m pip check` pass; README offline commands succeed; each completed task has its scoped commit or an explicitly recorded pending commit boundary if Git setup is unavailable.
