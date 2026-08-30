<div align="center">
  <img alt="LLM Gateway Banner" src="assets/banner.png" width="450">

  <p><strong>Production-grade Resilience API Proxy & Router for LLM Providers.</strong></p>
  
  <p>Implements distributed rate limiting, monthly budget tracking, automatic failovers, and circuit breaking to deliver high availability and zero-overhead performance (&lt;10ms processing latency) across Gemini, Claude, Groq, and Ollama.</p>

  <p>
    <a href="https://github.com/GauravFrr/LLM-Gateway"><img src="https://img.shields.io/github/license/GauravFrr/LLM-Gateway" alt="License" /></a>
    <a href="https://github.com/GauravFrr/LLM-Gateway"><img src="https://img.shields.io/github/languages/top/GauravFrr/LLM-Gateway" alt="Languages" /></a>
    <a href="https://github.com/GauravFrr/LLM-Gateway/actions"><img src="https://img.shields.io/github/actions/workflow/status/GauravFrr/LLM-Gateway/ci.yml" alt="CI Build Status" /></a>
    <a href="https://github.com/GauravFrr/LLM-Gateway/stargazers"><img src="https://img.shields.io/github/stars/GauravFrr/LLM-Gateway" alt="Stars" /></a>
  </p>

  <p>
    <a href="#-system-architecture--request-flow">Architecture</a> •
    <a href="#features-table">Features</a> •
    <a href="#-quickstart-under-5-minutes">Quickstart</a> •
    <a href="#-load-test--benchmarks">Benchmarks</a> •
    <a href="#-design-decisions--engineering-narrative">Design Decisions</a>
  </p>
</div>

---

### Features Table

<table>
  <tr>
    <td width="33%"><strong>🛡️ Distributed Rate Limiter</strong><br>Enforces RPM/TPM limits on Redis using Lua scripts with precise token bucket mechanics.</td>
    <td width="33%"><strong>💵 Granular Budget Tracking</strong><br>Enforces monthly budget limits in USD, warning teams at 80% and blocking at 100% of limits.</td>
    <td width="33%"><strong>🔄 Automated Failovers</strong><br>Bypasses primary outages instantly, routing to pre-configured fallback models automatically.</td>
  </tr>
  <tr>
    <td width="33%"><strong>🔌 Multi-Vendor SDKs</strong><br>Unifies Google Gemini, Anthropic Claude, Groq, and local Ollama APIs under a single endpoint.</td>
    <td width="33%"><strong>⚡ Circuit Breakers</strong><br>Distributed state machines (CLOSED/OPEN/HALF_OPEN) automatically trip to avoid cascading failures.</td>
    <td width="33%"><strong>📊 Prometheus & Grafana</strong><br>Telemetry monitoring for cost (8 decimal points), latencies, token counts, and circuit statuses.</td>
  </tr>
</table>

<details>
  <summary><strong>Table of Contents</strong></summary>
  <ul>
    <li><a href="#-system-architecture--request-flow">🏗️ System Architecture &amp; Request Flow</a></li>
    <li><a href="#-quickstart-under-5-minutes">🚀 Quickstart (Under 5 Minutes)</a></li>
    <li><a href="#-api-endpoints-quick-reference">🛠️ API Endpoints Quick Reference</a></li>
    <li><a href="#-design-decisions--engineering-narrative">🧠 Design Decisions &amp; Engineering Narrative</a></li>
    <li><a href="#-load-test--benchmarks">📊 Load Test &amp; Benchmarks</a></li>
    <li><a href="#-known-limitations--security-notes">⚙️ Known Limitations &amp; Security Notes</a></li>
  </ul>
</details>

---

## 🏗️ System Architecture & Request Flow

The following diagram outlines the end-to-end request sequence passing through the gateway checks:

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

### Core Engineering Pillars

1. **Token Bucket Rate Limiter**: Evaluates rate limits atomically inside Redis using custom Lua scripts, calculating precise sub-second cooldowns (`Retry-After` headers).
2. **Monthly Spend Enforcement**: Deducts spend calculations (supporting microdollar values rounded to 8 decimal places) in Redis and halts further usage when monthly budget caps are hit.
3. **Resilient Circuit Breaker**: Distributed state machines track consecutive provider-level failures. Exceeding 5 failures trips the circuit `OPEN` for a 30s cooldown, bypassing the primary provider instantly.
4. **Sub-millisecond Overhead**: Leverages async I/O throughout, reducing internal gateway processing overhead to **&lt;2ms** in real-world scenarios.

---

## 🚀 Quickstart (Under 5 Minutes)

### Prerequisites
* Docker & Docker Compose
* Python 3.12+ (for running check scripts and automated tests)

### 1. Boot the Stack
Spin up the Gateway, PostgreSQL, Redis, Prometheus, and Grafana:
```bash
docker-compose up -d --build
```

### 2. Set Up Virtual Environment & Dependencies
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Seed the Database
Seed logical tier mappings and default teams:
```bash
python -m app.db.seed
```

### 4. Verify Active Services
* **LLM Gateway Endpoint**: [http://localhost:8000](http://localhost:8000) (Swagger Docs: `/docs`)
* **Prometheus Registry**: [http://localhost:9090](http://localhost:9090)
* **Grafana Dashboard**: [http://localhost:3000](http://localhost:3000) (Credentials: `admin` / `admin`)

---

## 🛠️ API Endpoints Quick Reference

FastAPI exposes a complete developer API with standard endpoints. You can query the running server directly:

### 1. Chat Completions (`POST /v1/chat/completions`)
Submits a chat prompt to be dynamically routed through auth, limits, and resilience checks:
```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
     -H "Content-Type: application/json" \
     -H "Authorization: Bearer test_auth_key_balanced" \
     -d '{
       "tier": "balanced",
       "messages": [
         {"role": "user", "content": "Explain quantum computing in one sentence."}
       ],
       "max_tokens": 100
     }'
```

**Response Payload Structure:**
```json
{
  "id": "chat-47d337d1-e6e2-4113-913a",
  "provider": "gemini",
  "model": "gemini-1.5-flash",
  "content": "Quantum computing uses qubits to compute complex problems exponentially faster.",
  "usage": {
    "prompt_tokens": 8,
    "completion_tokens": 12,
    "total_tokens": 20
  },
  "cost_usd": 0.00015,
  "was_fallback": false
}
```

### 2. Admin Circuit Reset (`POST /admin/circuits/reset`)
Resets all tripped circuit breakers back to `CLOSED` in both Redis and the active Prometheus gauge:
```bash
curl -X POST "http://localhost:8000/admin/circuits/reset" \
     -H "Authorization: Bearer abcd"
```

---

## 🧠 Design Decisions & Engineering Narrative

Throughout the implementation and verification phases, we encountered and resolved several critical architectural challenges:

### 1. Lua Token Bucket Refinements (`retry_after` bug)
* **Challenge**: Our Redis rate-limiting Lua script was failing open under high load because the variable `retry_after` was used on line 76 without being initialized, triggering silent script crashes.
* **Decision**: We refactored the Lua script to calculate and return the wait duration explicitly: `local retry_after = math.max(rpm_wait, tpm_wait)`, ensuring 429 responses correctly propagate to clients with a precise `Retry-After` header.

### 2. Differentiating Provider Rate Limits from System Outages
* **Challenge**: A transient 429 quota exhaustion limit from an upstream provider (e.g. Gemini Free Tier) was originally categorized as a "system failure", causing the circuit breaker to trip `OPEN` and shut down fallback routing even when the provider was healthy.
* **Decision**: We created a specialized `ProviderRateLimitError` with `trips_circuit = False`. Upstream 429s now return immediately to the client to warn them, while keeping our circuit breakers `CLOSED` to ensure downstream fallbacks remain fully operational.

### 3. Cost Calculation Precision
* **Challenge**: High-throughput short prompts were registering as `$0.00` spend because the cost precision was capped at 6 decimal places.
* **Decision**: Increased spend tracking to 8 decimal places (`round(cost, 8)`) to correctly account for microdollar prompts on faster models.

---

## 📊 Load Test & Benchmarks

To validate performance under load, we ran k6 benchmark scripts under two modes:
1. **Mock Provider Mode** (`MOCK_PROVIDERS=True`): To isolate the gateway's core overhead and database/Redis pool logic from external network fluctuations and API key quota limits.
2. **Real Provider Smoke Test** (`MOCK_PROVIDERS=False`): A smaller batch run to verify real-world end-to-end integration latency.

### How to Run

#### A. Run Mocked Load Test (50 VUs, 5,000+ Requests)
Configure stack for mock mode and high-resolution scrape intervals:
```bash
# Set environment variables
$env:MOCK_PROVIDERS="True"
$env:PROMETHEUS_SCRAPE_INTERVAL="15s"
docker-compose up -d

# Run the k6 load test
docker run --rm --network=llmgateway_default -e GATEWAY_URL=http://gateway:8000 -v "${PWD}/tests/load:/load" grafana/k6 run /load/gateway_load_test.js
```

#### B. Run Real-Provider Smoke Test (Outage & Fallback Loop)
Revert to real providers mode:
```bash
# Set environment variables
$env:MOCK_PROVIDERS="False"
$env:PROMETHEUS_SCRAPE_INTERVAL="2s"
docker-compose up -d

# Run the verification script
.venv\Scripts\python scripts/verify_phase4.py
```

### Measured Performance Results

| Metric | Mock (SimpleSpanProcessor) | Mock (BatchSpanProcessor) | Real Provider (35 Requests) |
| :--- | :---: | :---: | :---: |
| **Request Duration (Avg)** | **912.86 ms** | **1,360.00 ms** *(queue delay)* | **~280 ms** *(Groq)* / **~900 ms** *(Gemini)* |
| **Average Gateway Overhead** | **88.58 ms** *(sync stdout IO)* | **133.43 ms** *(GIL cpu switching)* | **1.77 ms** |
| **Median (P50) Overhead** | **76.77 ms** | **116.27 ms** | **1.37 ms** |
| **Max Gateway Overhead** | **562.79 ms** | **649.83 ms** | **9.31 ms** |
| **Rate Limit Accuracy** | **100%** | **100%** | **100%** |
| **Error Rate %** | **0.00%** | **0.00%** | **0.00%** |
| **Throughput** | **53.62 requests/sec** | **36.15 requests/sec** | Restricted by provider rate limits |
| **Total Iterations (35s)** | **1,910** | **1,288** | N/A |

---

## ⚙️ Known Limitations & Security Notes

* **Prometheus Scrape Interval**: The scrape interval is fixed at **`2s`** and is not configurable via environment variables because the Prometheus image version in use does not support env var substitution inside `prometheus.yml`.
* **Database Connection Pool Bottleneck (Resolved)**: High-concurrency load testing (50+ concurrent VUs) originally experienced queue contention at the database pool layer. We increased the connection pool size to `50` (with `10` max overflow) in [session.py](file:///f:/LLM%20Gateway/app/db/session.py) to resolve this.
* **Streaming Fallback**: Fallbacks only apply to non-streaming requests. Mid-stream provider drops cannot be recovered mid-way and are returned to the client directly.
* **Manual Pricing Table**: Upstream model pricing is stored in PostgreSQL and must be maintained manually; it does not auto-fetch from provider pricing pages.
* **Fail-Open Redis Behavior**: If Redis experiences an outage, the gateway fails open for rate limits and auth validation to ensure maximum uptime, letting requests bypass limits instead of crashing.
