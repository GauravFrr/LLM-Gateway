# LLM Gateway

A highly resilient, high-performance API Proxy and router for LLM Providers (Gemini, Claude, Groq, Ollama) featuring distributed rate limiting, monthly budget tracking, automatic failovers, and circuit breaking.

---

## 1. Problem Statement

Deploying LLM-powered applications to production exposes teams to three critical operational challenges:
1. **Upstream Instability**: Upstream provider outages or rate limits (429s) can degrade user experience.
2. **Cost & Budget Runaways**: Lacking granular, API-key-level monthly budgets can lead to unexpected billing runaways.
3. **Vendor Lock-in**: Hardcoding provider-specific SDKs prevents routing optimization based on speed, cost, or quality.

The **LLM Gateway** addresses these by acting as a lightweight, resilient, and fully instrumented middle tier that handles model routing, fallbacks, rate limits, and budget tracking transparently with **<10ms internal overhead**.

---

## 2. Architecture & Request Flow

```mermaid
sequenceDiagram
    Client ->> Gateway: POST /v1/chat/completions (with Team API Key)
    Gateway ->> Redis: 1. Validate Auth & Cache Lookup
    Gateway ->> Redis: 2. Check Rate Limits (Lua Token Bucket)
    Gateway ->> Redis: 3. Verify Monthly Budget Spend
    Gateway ->> Redis: 4. Check Provider Circuit Status (Closed/Open)
    alt Circuit is CLOSED (Healthy)
        Gateway ->> Primary Provider: 5a. Dispatch Completion Request
    else Circuit is OPEN (Unhealthy)
        Gateway ->> Fallback Provider: 5b. Direct Bypass to Fallback
    end
    alt Primary Provider Succeeds
        Primary Provider ->> Gateway: 200 OK Response
    else Primary Provider Fails (5xx or timeout)
        Gateway ->> Redis: 6. Record Failure & Increment Counter
        Gateway ->> Fallback Provider: 7. Fallback Route Request
        Fallback Provider ->> Gateway: 200 OK Response
    end
    Gateway ->> Postgres: Async log request metadata
    Gateway ->> Prometheus: Increment counters (Spend, Latency, Tokens)
    Gateway ->> Client: 200 OK Unified Response Payload
```

---

## 3. Quickstart (Under 5 Minutes)

### Prerequisites
- Docker & Docker Compose
- Python 3.12+ (for running the scripts/tests)

### 1. Boot the Stack
Clone the repository and spin up the gateway, database, Redis, Prometheus, and Grafana:
```bash
docker-compose up -d --build
```

### 2. Set Up Virtual Environment & Dependencies
```bash
python -m venv .venv
.venv\Scripts\activate      # On Windows
source .venv/bin/activate    # On Linux/macOS
pip install -r requirements.txt
```

### 3. Seed Database
Load the initial logical tier mappings and pre-configured teams into PostgreSQL:
```bash
.venv\Scripts\python -m app.db.seed
```

### 4. Verify Services
- **FastAPI LLM Gateway**: [http://localhost:8000](http://localhost:8000) (Docs: `/docs`)
- **Prometheus Scraper**: [http://localhost:9090](http://localhost:9090)
- **Grafana Dashboards**: [http://localhost:3000](http://localhost:3000) (Credentials: `admin` / `admin`)

---

## 4. Demo Video

*(Link to demo walkthrough video placeholder)*

---

## 5. Design Decisions & Engineering Narrative

Throughout the implementation and verification phases, we encountered and resolved several critical architectural challenges:

### 1. Lua Token Bucket Refinements (`retry_after` bug)
- *Challenge*: Our Redis rate-limiting Lua script was failing open under high load because the variable `retry_after` was used on line 76 without being initialized, triggering silent script crashes.
- *Decision*: We refactored the Lua script to calculate and return the wait duration explicitly: `local retry_after = math.max(rpm_wait, tpm_wait)`, ensuring 429 responses correctly propagate to clients with a precise `Retry-After` header.

### 2. Differentiating Provider Rate Limits from System Outages
- *Challenge*: A transient 429 quota exhaustion limit from an upstream provider (e.g. Gemini Free Tier) was originally categorized as a "system failure", causing the circuit breaker to trip `OPEN` and shut down fallback routing even when the provider was healthy.
- *Decision*: We created a specialized `ProviderRateLimitError` with `trips_circuit = False`. Upstream 429s now return immediately to the client to warn them, while keeping our circuit breakers `CLOSED` to ensure downstream fallbacks remain fully operational.

### 3. Cost Calculation Precision
- *Challenge*: High-throughput short prompts were registering as `$0.00` spend because the cost precision was capped at 6 decimal places.
- *Decision*: Increased spend tracking to 8 decimal places (`round(cost, 8)`) to correctly account for microdollar prompts on faster models.

---

## 6. Load Test & Benchmarks

To validate performance under load, we ran k6 benchmark scripts under two modes:
1. **Mock Provider Mode** (`MOCK_PROVIDERS=True`): To isolate the gateway's core overhead and database/Redis pool logic from external network fluctuations and API key quota limits.
2. **Real Provider Smoke Test** (`MOCK_PROVIDERS=False`): A smaller batch run to verify real-world end-to-end integration latency.

### How to Run

#### A. Run Mocked Load Test (50 VUs, 5,000+ Requests)
1. Configure stack for mock mode and high-resolution scrape intervals:
   ```powershell
   $env:MOCK_PROVIDERS="True"
   $env:PROMETHEUS_SCRAPE_INTERVAL="15s"
   docker-compose up -d
   ```
2. Run the k6 load test:
   ```bash
   docker run --rm --network=llmgateway_default -e GATEWAY_URL=http://gateway:8000 -v "${PWD}/tests/load:/load" grafana/k6 run /load/gateway_load_test.js
   ```

#### B. Run Real-Provider Smoke Test (Outage & Fallback Loop)
1. Revert to real providers mode:
   ```powershell
   $env:MOCK_PROVIDERS="False"
   $env:PROMETHEUS_SCRAPE_INTERVAL="2s"
   docker-compose up -d
   ```
2. Run the verification script:
   ```bash
   .venv\Scripts\python scripts/verify_phase4.py
   ```

### Measured Performance Results

| Metric | Mock (SimpleSpanProcessor) | Mock (BatchSpanProcessor) | Real Provider (35 Requests) |
| :--- | :--- | :--- | :--- |
| **Request Duration (Avg)** | **912.86 ms** | **1,360.00 ms** *(virtualization queue delay)* | **~280 ms** *(Groq)* / **~900 ms** *(Gemini)* |
| **Average Gateway Overhead** | **88.58 ms** *(sync stdout IO block)* | **133.43 ms** *(concurrency CPU switching)* | **1.77 ms** |
| **Median (P50) Overhead** | **76.77 ms** | **116.27 ms** | **1.37 ms** |
| **Max Gateway Overhead** | **562.79 ms** | **649.83 ms** | **9.31 ms** |
| **Rate Limit Accuracy** | **100%** | **100%** | **100%** |
| **Error Rate %** | **0.00%** | **0.00%** | **0.00%** |
| **Throughput** | **53.62 requests/sec** | **36.15 requests/sec** | Restricted by provider rate limits |
| **Total Iterations (35s)** | **1,910** | **1,288** | N/A |

*Note: Separating the result sets isolates the gateway's processing overhead under high database connection pool contention (50 concurrent VUs queuing for connections) versus low-concurrency real-provider routing where pure gateway latency remains <10ms.*

#### Mock Test 5k Target & Performance Regression Analysis:
The mock-mode load tests are capped below the 5,000+ requests mark in 35 seconds due to a combination of **WSL2 Docker networking virtualization bottlenecks** and **Python GIL (Global Interpreter Lock) contention**:
- **WSL2 Socket Delay**: Running 50 concurrent VUs through Docker's bridge network on Windows WSL2 introduces socket queuing delays (adding ~1.1s lag per connection outside the Uvicorn application thread).
- **OTel Processor Regression Case Study**: We observed a regression when switching to `BatchSpanProcessor` in this test run (throughput dropped from ~53 to ~36 req/s, and median overhead rose to **116.27 ms**). Our leading hypothesis is GIL contention and thread context-switching thrashing between Uvicorn's main async event loop and OTel's background export thread under Python's GIL, though we did not run repeated trials to statistically confirm this is consistent rather than run-to-run noise. 
- **Recommendation**: Regardless of the exact mechanism behind this specific regression, console span printing should be disabled entirely in production rather than offloaded to background threads. To exceed 5,000+ requests on local mock runs, increase k6 duration to `100s` or disable Console Exporter writes.

---

## 7. Known Limitations & Security Notes

- **Prometheus Scrape Interval**: The scrape interval is fixed at **`2s`** and is not configurable via environment variables because the Prometheus image version in use does not support the `--enable-feature=expand-external-env` flag for env var substitution inside `prometheus.yml`.
- **Database Connection Pool Bottleneck (Resolved / Tuning Knob)**: High-concurrency load testing (50+ concurrent VUs) originally experienced queue contention at the database pool layer because SQLAlchemy's default pool size is `5`. This caused requests to queue waiting for database connections during auth/budget dependency resolution.
  - *Tuning*: We increased the connection pool size to `50` (with `10` max overflow) in [session.py](file:///f:/LLM%20Gateway/app/db/session.py). For production deployments under higher loads, scale `pool_size` proportionally to your target concurrency. The pool parameters are conditional and only applied for PostgreSQL (not SQLite) to keep CI lightweight.
- **Streaming Fallback**: Fallbacks only apply to non-streaming requests. Mid-stream provider drops cannot be recovered mid-way and are returned to the client directly.
- **Manual Pricing Table**: Upstream model pricing is stored in PostgreSQL and must be maintained manually; it does not auto-fetch from provider pricing pages.
- **Grafana Credentials**: The default credentials (`admin` / `admin`) are configured for local demo purposes and must be secured in a production environment.
- **Fail-Open Redis Behavior**: If Redis experiences an outage, the gateway fails open for rate limits and auth validation to ensure maximum uptime, letting requests bypass limits instead of crashing.

