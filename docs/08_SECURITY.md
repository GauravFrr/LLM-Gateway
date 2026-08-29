# LLM Gateway — Security Documentation

## 1. Threat Model (what this gateway must defend against)

1. Unauthorized use of provider API keys (someone using the gateway to burn your Gemini/Claude/Groq credits)
2. Leakage of provider API keys or team API keys (via logs, error messages, source control, or responses)
3. One team/client abusing shared infrastructure (rate limit bypass, budget bypass)
4. Injection attacks via request content (prompt injection is a provider-level LLM concern, but the gateway must not be vulnerable to SQL injection, header injection, or SSRF through request handling)
5. Denial of service against the gateway itself (distinct from rate limiting individual teams — this is about the gateway's own resource exhaustion)
6. Admin API abuse (someone without admin rights modifying team budgets/limits)

## 2. API Key Management

- **Team API keys:** generated server-side (`secrets.token_urlsafe(32)`), shown to the admin ONCE at creation time, never retrievable again (standard practice — like AWS access keys). Only the hash is stored (`api_key_hash` column, SHA-256 minimum, bcrypt/argon2 not necessary here since these are high-entropy random tokens, not user passwords).
- **Provider API keys** (Gemini/Claude/Groq): stored only in environment variables, loaded via `.env` (git-ignored) locally or a secrets manager in any real deployment. NEVER stored in Postgres, NEVER logged, NEVER included in OTel span attributes.
- **Admin API key:** separate from team keys, held only by Gaurav for demo purposes; in a real deployment this would be a proper auth system (out of scope per PRD non-goals, but documented here as a known simplification).

## 3. What Gets Logged vs. What Never Gets Logged

**Safe to log:**
- Team ID, request ID, provider used, model used, token counts, cost, latency, status, error codes

**NEVER logged, anywhere (files, stdout, Postgres, OTel spans, Grafana):**
- Provider API keys, in full or partial
- Team API keys, in full — only first 8 chars of the *hash* may appear in debug logs for correlation purposes
- Full request/response message content by default (this may contain sensitive user data from whatever app is calling the gateway) — content logging is opt-in per team via a config flag, off by default, and if enabled, should be treated as sensitive data requiring the same handling as the app's own user data

## 4. Secrets Handling

- `.env` file is git-ignored (verify `.gitignore` includes it before first commit — this is a real, common mistake worth explicitly checking)
- `.env.example` contains variable names with placeholder values only (`GEMINI_API_KEY=your_key_here`)
- Docker Compose reads secrets from `.env`, never hardcodes them in `docker-compose.yml`
- If pushing this to GitHub as a portfolio piece: run a secrets scan (`gitleaks` or GitHub's own secret scanning) before the first public push, and before every push thereafter as a habit

## 5. Input Validation

- All request bodies validated via Pydantic models — reject malformed requests before they reach any business logic (this also protects against a class of injection issues by construction)
- `tier` and other enum-like fields validated against an explicit allowlist, not just "any string" — prevents attempts to probe internal routing logic
- Team-supplied `max_tokens` and similar numeric fields have server-side upper bounds regardless of what the client requests, to prevent a single request from requesting an absurdly expensive completion

## 6. SQL Injection

- SQLAlchemy ORM used exclusively for all Postgres queries — no raw SQL string concatenation anywhere. If a raw query is ever genuinely needed (rare), it must use parameterized queries only, never f-string/`.format()` interpolation of user input into SQL.

## 7. SSRF Considerations

- Provider base URLs are hardcoded per provider module (`app/providers/*.py`), never derived from client-supplied input — a client cannot direct the gateway to make requests to an arbitrary URL. This is the primary SSRF defense: there's no user-controllable "destination" parameter anywhere in the API surface.
- Ollama's local endpoint is only reachable within the docker-compose network, not exposed externally.

## 8. Rate Limiting as a Security Control (not just a cost control)

- Rate limiting doubles as a basic DoS defense against a single team/key hammering the gateway
- Consider a global (not per-team) rate limit as a secondary layer, protecting the gateway itself if many teams' limits collectively could overwhelm it — document this as a Phase 3+ consideration, implement if time allows, otherwise document as a known gap

## 9. Admin API Access Control

- Admin routes (`/admin/*`) require a separate admin API key, checked via a distinct FastAPI dependency (`require_admin`), never the same code path as team key auth
- Admin routes should be excluded from public exposure in any real deployment (bind to internal network only / require VPN) — for the local demo, document this as the intended production posture even though the demo itself runs everything on localhost

## 10. Dependency Security

- Run `pip-audit` (or `safety`) periodically, and at minimum once before the final portfolio push, to check for known CVEs in dependencies
- Pin dependency versions in `requirements.txt` / `pyproject.toml` — no unpinned `latest` in a project meant to demonstrate production readiness

## 11. Known Limitations (document these honestly rather than hiding them)

- No mTLS or network-level encryption between gateway and providers beyond standard HTTPS (acceptable — providers only offer HTTPS anyway)
- No key rotation mechanism built (would be a real requirement in production; out of scope for the portfolio build, mention as a "next steps" item)
- Redis unavailability currently fails "open" for rate limiting in the initial design (a request proceeds if Redis can't be reached) — this is a deliberate availability-over-strictness tradeoff for a portfolio demo, but the doc should say explicitly that a production system handling real money would likely fail closed instead, and that's a one-line config change (`fail_open: bool` in `rate_limiter.py`) — call this out in the README's "design decisions" section as an interview talking point, since reviewers may probe exactly this tradeoff
