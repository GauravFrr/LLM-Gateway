# LLM Gateway

Unified, resilient API Proxy for LLM Providers (Gemini, Claude, Groq, Ollama) built with FastAPI, PostgreSQL, and Redis.

## 1. Quickstart

To run the gateway locally with all observability systems (Prometheus & Grafana) pre-provisioned, run:

```bash
docker-compose up --build -d
```

Once running:
- **FastAPI LLM Gateway**: [http://localhost:8000](http://localhost:8000)
- **Interactive Swagger Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **Prometheus Scraper**: [http://localhost:9090](http://localhost:9090)
- **Grafana Dashboards**: [http://localhost:3000](http://localhost:3000) (Credentials: `admin` / `admin`)

To seed the database with initial logical tiers and team accounts:
```bash
.venv\Scripts\python app/db/seed.py
```

## 2. Observability Dashboards

Grafana is pre-provisioned with three custom dashboards:
1. **Operations Dashboard**: Visualizes request rate, error rate %, latency percentiles, circuit breaker states, and fallback activation rates.
2. **Business Dashboard**: Tracks accumulated spend, spend by team, spend by provider, and teams approaching budget thresholds (>=80%).
3. **Performance Dashboard**: Measures isolated gateway logic overhead (in milliseconds, demonstrating the <10ms claim), token throughput, and rate limit rejections.

## 3. Known Limitations & Security Notes

- **Grafana Credentials**: The default credentials (`admin` / `admin`) are configured for local demo and development purposes only. In a production environment, these must be secured using docker environment variables or custom Grafana configs.
- **Manual Pricing Table**: The token pricing matrix is stored statically in PostgreSQL and does not automatically fetch real-time pricing changes from provider endpoints.
- **Streaming Fallback**: Fallback logic only applies to non-streaming requests. Stream-based completion routing does not support mid-stream failovers.
