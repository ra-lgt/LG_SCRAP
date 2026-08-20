# LG Bidding Bot — Implementation Plan

## 1. Objective

Build a resilient, stateful bidding bot for the LG NGSI portal that can:

- load the bidding page
- parse the inventory grid
- filter rows by configured rules
- keep session state valid across ASP.NET postbacks
- trigger OTP flow when a valid bid is ready
- submit batched bidding data in a stable, repeatable way
- report health and KPIs back to the control API

This project is intended as a controlled automation script for a known internal workflow, with operational safety, logging, and retry behavior as core design goals.

## 2. Scope

In scope:

- HTML parsing for ASP.NET GridView pages
- session persistence with `httpx`
- hidden form field extraction (`__VIEWSTATE`, `__EVENTVALIDATION`, etc.)
- pagination and inventory collection
- price/DP filtering
- OTP begin/fetch/end lifecycle
- page method calls for validation and `SaveBiddingData`
- KPI reporting over HTTP and optional WebSocket
- graceful retry and shutdown behavior

Out of scope:

- browser automation with Selenium/Playwright
- UI-only manual clicking
- bypassing portal validation or exploiting undocumented endpoints
- intentionally disruptive behavior against other workers

## 3. High-Level Architecture

The bot is designed as a single long-running worker with a predictable loop:

1. Load the target LG page
2. Validate the session and hidden ASP.NET fields
3. Parse inventory rows and deduplicate them
4. Filter rows against configured min/max price and DP rules
5. Detect matched items
6. Re-open the correct grid page state when needed
7. Trigger OTP flow
8. Validate OTP
9. Submit the selected rows in one batch
10. Report success/failure to the API
11. Sleep for the next scan interval

Core components:

- `LGBot`: main orchestration class
- `get_initial()`: fetches the page and reads state
- `collect_pages()`: walks all pagination pages
- `parse_inventory()`: extracts rows from the grid
- `_filter_inventory()`: applies configured filters
- `fetch_otp()`: manages OTP lifecycle
- `validate_otp()`: validates OTP via page method
- `save_bidding_data()`: submits selectedRows batch
- `_send_kpi()`: reports health and counters

## 4. Design Principles

### 4.1 Stateful and resilient

The bot must behave like a standard ASP.NET WebForms client, not like a random request generator. It must carry valid hidden-field state and respect the portal’s page lifecycle.

### 4.2 Batch-first submission

The portal’s JS clearly expects a batched payload via `PageMethods.SaveBiddingData(selectedRows)`. The bot should submit in grouped batches rather than per-row.

### 4.3 Minimal state churn

The current implementation should avoid rebuilding full page state unnecessarily on every loop. Keeping the session warm and preserving page state reduces latency and avoids de-sync issues.

### 4.4 Graceful failure

Any failed network request, invalid login state, or OTP timeout should log cleanly, cancel the current flow, and continue retrying instead of crashing the worker.

### 4.5 Observability

Every action should log structured JSON output with context such as:

- iteration number
- page fetch time
- detected rows
- matched rows
- OTP status
- API response status
- final submission result

## 5. Implementation Plan

### Phase 1 — Baseline Bot

Goals:

- load page
- parse GridView rows
- collect inventory across pages
- deduplicate serials
- filter by configured price and DP rules
- print logs in JSON format

Deliverables:

- stable session object using `httpx.Client`
- `get_initial()` and `search()` functions
- hidden-field extraction helpers
- grid parser for inventory rows
- basic loop with `run_forever()`

Acceptance criteria:

- bot starts with a valid URL or API-configured profile
- bot loads the page without crashing
- bot extracts inventory rows correctly
- bot logs structured output for each iteration

### Phase 2 — Session Stability and ASP.NET Compatibility

Goals:

- preserve and reuse ASP.NET hidden values properly
- handle GridView pagination correctly
- remember page-specific state for re-opened pages
- handle save/submit postback lifecycle safely

Deliverables:

- `__VIEWSTATE`, `__EVENTVALIDATION`, `__VIEWSTATEGENERATOR` handling
- `collect_pages()` with pager-aware navigation
- `save()` form post using selected checkboxes
- page-state restoration before OTP submission

Acceptance criteria:

- postbacks continue to work across multiple pages
- selected rows are re-opened correctly before triggering OTP request
- no stale `__VIEWSTATE` errors during high-activity loops

### Phase 3 — OTP Workflow

Goals:

- integrate the OTP begin/fetch/end flow with the external API
- send the page save request only when needed
- validate OTP through the page method
- cancel and clean up stale OTP requests on failure

Deliverables:

- `fetch_otp()` implementation
- OTP polling loop with timeout and retry logic
- cancellation on HTTP or validation failure
- structured logging around OTP timing and outcomes

Acceptance criteria:

- OTP is requested exactly once per batch
- timeout and lock scenarios are handled
- failed validation cancels the flow cleanly

### Phase 4 — Bid Submission and Batching

Goals:

- submit only valid matched rows
- batch rows in a single `SaveBiddingData` call
- avoid per-row submission patterns
- strip formatting consistently before numeric serialization

Deliverables:

- `save_bidding_data()` payload builder
- numeric normalization for prices, DP values, and decimals
- serialization of `selectedRows` in the structure expected by the page
- rejection handling for portal-side validation errors

Acceptance criteria:

- selected rows are submitted in one call
- numeric payload matches portal expectations
- errors from LG portal are readable and logged

### Phase 5 — Performance Tuning and State Reuse

Goals:

- reduce unnecessary page reloads
- reuse valid grid/page state
- keep the bot efficient while respecting the portal’s request timing
- reduce full re-scan churn during active bid windows

Deliverables:

- page-cache strategy for current inventory snapshot
- “last known valid page” tracking
- reduced redundant browsing loops
- better selection of trigger rows
- optional adaptive scan interval based on observed portal response time

Acceptance criteria:

- fewer duplicate network calls
- lower average action time
- stable session continuity over long runs

### Phase 6 — API Reporting and Operations

Goals:

- report worker status to the control API
- maintain KPI counters for detected, booked, failed, attempted, OTP received
- support optional WebSocket push when available
- support graceful shutdown via OS signal handlers

Deliverables:

- `/api/bot/report` fallback payload sending
- WebSocket KPI push when available
- service-key auth handling
- graceful SIGINT/SIGTERM shutdown

Acceptance criteria:

- bot stays alive if the KPI channel fails
- API communication never blocks the main loop
- shutdown signals exit cleanly without corrupt state

## 6. Request Flow to Preserve

The bot should continue to follow a realistic portal-oriented flow similar to the page’s own contract:

1. GET page
2. parse GridView and hidden state
3. optional search/dropdown postback
4. page pagination if needed
5. checkbox selection for trigger rows
6. `btnSave` postback to initiate OTP flow
7. `ValidateOtp` page method
8. `SaveBiddingData` page method

That flow matches the browser behavior and is more robust than inventing a custom protocol.

## 7. Core Data Model

Each row parsed from the grid should include:

- `checkbox_name`
- `serial_no`
- `model_code`
- `product`
- `branch`
- `bill_branch`
- `category`
- `from_date`
- `to_date`
- `dealer_price`
- `dp`
- `base_price`
- `batchno`
- `image_name`
- `dmg_image`
- `image_url`
- `dmg_image_url`
- `defect`

Deduplication should primarily be by serial number to avoid repeated duplicate inventory entries from paginated views.

## 8. Validation and Quality Gates

Before shipping a production release, validate:

- page loads successfully over multiple iterations
- hidden fields update after postback
- pagination works and returns valid `Page$N` args
- selected rows trigger OTP correctly
- OTP validation succeeds under normal conditions
- final `SaveBiddingData` response is parsed and interpreted correctly
- worker remains alive if the API is temporarily unreachable

## 9. Deployment and Run Modes

The bot should support:

- `--api-url=...`
- `--filter-id=...`
- `--worker-id=...`
- direct URL mode for standalone local runs
- environment variables for API configuration and service key
- Docker deployment with a long-lived process

Recommended runtime behavior:

- keep scanning loop alive
- log every action as plain JSON
- avoid hard exits on transient failures
- recover automatically on the next iteration

## 10. Risk Areas to Monitor

- stale `__VIEWSTATE` after page changes
- row selection mismatch after pagination
- invalid numeric formatting for prices or DP values
- OTP timeout or lock contention
- API outage while KPI reporting is down
- hidden field expiry on long-lived sessions
- portal-side validation rejection on malformed payloads

## 11. Recommended Next Milestones

### Milestone A — Stable scan loop

- parse rows reliably
- deduplicate inventory
- filter rows by configured rules

### Milestone B — Stable postback submission

- page reload and `__VIEWSTATE` continuity
- save trigger flow
- OTP call chain

### Milestone C — Efficient batch submission

- grouped `selectedRows` payload
- lower network churn
- reliable final submission

### Milestone D — Production readiness

- logging improvements
- alerting
- retry logic
- performance tuning
- deployable Docker configuration

## 12. Recommended File Layout

```text
LG_SCRAP/
├── bot.py
├── README.md
├── requirements.txt
├── Dockerfile
└── .gitignore
```

## 13. Summary

The correct technical roadmap is not to hack around the portal but to model the actual ASP.NET WebForms workflow accurately, keep state valid, reduce wasted round trips, and submit rows in a proper batch flow. That gives the bot a realistic path to becoming stable, fast, and operationally safe.
