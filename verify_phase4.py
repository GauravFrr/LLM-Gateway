import urllib.request
import json
import time
import redis

def make_request(url, method, headers, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as response:
            return response.status, json.loads(response.read().decode("utf-8")), response.headers
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8")), e.headers
        except Exception:
            try:
                return e.code, e.read().decode("utf-8"), e.headers
            except Exception:
                return e.code, e.reason, e.headers
    except Exception as e:
        return 0, str(e), {}

def run_traffic_generation():
    print("=== Phase 4: Observability Traffic Generator ===")

    # Connect to Redis
    r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)

    base_url = "http://127.0.0.1:8000"
    admin_headers = {"Authorization": "Bearer abcd", "Content-Type": "application/json"}

    # Clear ALL circuit breaker states to start clean
    print("\n0. Clearing ALL circuit breaker states...")
    for provider in ["groq", "gemini", "claude", "ollama"]:
        r.delete(
            f"circuit:{provider}:state",
            f"circuit:{provider}:failures",
            f"circuit:{provider}:cooldown_end"
        )
    print("   Done.")

    # 1. Create test team with RPM=1 to guarantee 429 on back-to-back requests
    team_name = f"team-obs-{int(time.time())}"
    print(f"\n1. Creating team with RPM=1: {team_name}")
    status, team_data, _ = make_request(
        f"{base_url}/admin/teams", "POST", admin_headers,
        {"name": team_name, "monthly_budget_usd": 50.0, "priority_tier": "normal"}
    )
    if status != 200:
        print(f"   FAILED: {team_data}")
        return

    team_id = team_data["id"]
    api_key = team_data["api_key"]
    print(f"   Team ID: {team_id}")

    # 2. Configure access: RPM=1, invalid gemini primary -> groq/openai/gpt-oss-20b fallback (fully working model)
    print("\n2. Configuring: RPM=1, gemini/invalid -> groq/openai/gpt-oss-20b fallback...")
    status, _, _ = make_request(
        f"{base_url}/admin/teams/{team_id}/access", "POST", admin_headers,
        {
            "logical_tier": "fast",
            "rate_limit_rpm": 1,
            "rate_limit_tpm": 100000,
            "primary_provider": "gemini",
            "primary_model": "invalid-model-to-trigger-outage",
            "fallback_provider": "groq",
            "fallback_model": "openai/gpt-oss-20b"
        }
    )
    if status != 200:
        print(f"   FAILED configuring access")
        return

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    # Use a prompt that guarantees non-empty output -> non-zero tokens -> non-zero cost
    payload = {
        "tier": "fast",
        "messages": [{"role": "user", "content": "Write a haiku about API gateways. Reply with only the haiku."}],
        "max_tokens": 50
    }

    # 3. First successful fallback request
    print("\n3. Request 1 - fallback to groq/openai/gpt-oss-20b (primary gemini invalid)...")
    status, body, _ = make_request(f"{base_url}/v1/chat/completions", "POST", headers, payload)
    usage = body.get("usage", {})
    print(f"   Status: {status}, Provider: {body.get('provider')}/{body.get('model')}")
    print(f"   Content: {repr(body.get('content', '')[:60])}")
    print(f"   Tokens: {usage.get('input_tokens')} in / {usage.get('output_tokens')} out, Cost: ${usage.get('cost_usd', 0):.8f}")

    # 4. Immediate second request — GUARANTEED 429 (bucket now empty, RPM=1)
    print("\n4. Request 2 - IMMEDIATE (should 429, RPM=1 bucket empty)...")
    status, body, _ = make_request(f"{base_url}/v1/chat/completions", "POST", headers, payload)
    if status == 429:
        print(f"   [OK] 429 confirmed! Retry-After: {body.get('error', {}).get('retry_after_seconds', '?')}s")
    else:
        print(f"   [WARN] Expected 429, got {status}: {body}")

    # 5. Wait for RPM=1 bucket to replenish (65s to be safe)
    print("\n5. Waiting 65s for rate bucket replenishment...")
    for i in range(65, 0, -1):
        print(f"\r   {i}s remaining...", end="", flush=True)
        time.sleep(1)
    print()

    # 6. Send requests 2-5 to trip Gemini circuit (need 5 total failures including request 1)
    print("\n6. Sending 4 more fallback requests to trip Gemini circuit (failures 2-5)...")
    for i in range(2, 6):
        print(f"   Request {i+1}: ", end="", flush=True)
        status, body, _ = make_request(f"{base_url}/v1/chat/completions", "POST", headers, payload)
        usage = body.get("usage", {})
        print(f"Status {status}, Fallback: {body.get('was_fallback')}, Cost: ${usage.get('cost_usd', 0):.8f}")
        gemini_state = r.get("circuit:gemini:state") or "closed"
        gemini_fails = r.get("circuit:gemini:failures") or "0"
        print(f"           Gemini circuit: {gemini_state}, failures: {gemini_fails}")
        if i < 5:
            print(f"   Waiting 65s before next request (RPM=1)...")
            for j in range(65, 0, -1):
                print(f"\r   {j}s remaining...", end="", flush=True)
                time.sleep(1)
            print()

    # 7. Final request with Gemini OPEN - bypass Gemini instantly, go direct to Groq
    print("\n\n7. Final request with Gemini circuit OPEN (bypass Gemini entirely)...")
    print("   Waiting 65s for rate bucket replenishment first...")
    for j in range(65, 0, -1):
        print(f"\r   {j}s remaining...", end="", flush=True)
        time.sleep(1)
    print()
    t0 = time.time()
    status, body, _ = make_request(f"{base_url}/v1/chat/completions", "POST", headers, payload)
    duration = (time.time() - t0) * 1000
    usage = body.get("usage", {})
    gemini_state = r.get("circuit:gemini:state") or "closed"
    print(f"   Status: {status}, Provider: {body.get('provider')}/{body.get('model')}")
    print(f"   Duration: {duration:.0f}ms, Was Fallback: {body.get('was_fallback')}")
    print(f"   Cost: ${usage.get('cost_usd', 0):.8f}, Gemini circuit: {gemini_state}")

    print("\n=== Traffic generation complete! ===")
    print("   Open Grafana: http://localhost:3000 (admin/admin)")
    print("   Set time range to Last 30 minutes.")

if __name__ == "__main__":
    run_traffic_generation()
