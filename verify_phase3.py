import urllib.request
import json
import time
import subprocess
import sys
import redis

def make_request(url, method, headers, payload=None):
    data = json.dumps(payload).encode("utf-8") if payload else None
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method=method
    )
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

def print_redis_state(r):
    state = r.get("circuit:groq:state") or "closed"
    failures = r.get("circuit:groq:failures") or "0"
    cooldown = r.get("circuit:groq:cooldown_end") or "none"
    print(f"   [Redis Status] state={state}, failures={failures}, cooldown_end={cooldown}")

def run_verification():
    print("=== Phase 3: Fallback & Resilience E2E Verification ===")
    
    # Connect to Redis
    r = redis.Redis(host="127.0.0.1", port=6379, decode_responses=True)
    print("Clearing existing circuit breaker state for groq and gemini...")
    r.delete(
        "circuit:groq:state", "circuit:groq:failures", "circuit:groq:cooldown_end",
        "circuit:gemini:state", "circuit:gemini:failures", "circuit:gemini:cooldown_end"
    )
    
    base_url = "http://127.0.0.1:8000"
    admin_headers = {"Authorization": "Bearer abcd", "Content-Type": "application/json"}
    
    try:
        # 2. Register a new team specifically for resilience testing
        team_name = f"team-resilience-{int(time.time())}"
        print(f"\n1. Creating team: {team_name}")
        status, team_data, _ = make_request(
            f"{base_url}/admin/teams",
            "POST",
            admin_headers,
            {"name": team_name, "monthly_budget_usd": 100.0, "priority_tier": "normal"}
        )
        if status != 200:
            print(f"Failed to create team. Status: {status}, Response: {team_data}")
            return
            
        print(f"Status: {status}, Team ID: {team_data.get('id')}")
        
        team_id = team_data["id"]
        team_key = team_data["api_key"]
        team_headers = {"Authorization": f"Bearer {team_key}", "Content-Type": "application/json"}
        
        # 3. Configure logical tier access with an INVALID primary model to trigger failure,
        # but a VALID fallback model to trigger successful fallback!
        print("\n2. Configuring access (Primary: Invalid Groq Model -> Fallback: Valid Gemini Model)")
        status, access_data, _ = make_request(
            f"{base_url}/admin/teams/{team_id}/access",
            "POST",
            admin_headers,
            {
                "logical_tier": "fast",
                "primary_provider": "groq",
                "primary_model": "invalid-model-to-trigger-outage",  # Invalid model causes instant error
                "fallback_provider": "gemini",
                "fallback_model": "gemini-3.5-flash",
                "rate_limit_rpm": 100,
                "rate_limit_tpm": 50000
            }
        )
        print(f"Status: {status}, Primary: {access_data['primary_provider']}/{access_data['primary_model']}, Fallback: {access_data['fallback_provider']}/{access_data['fallback_model']}")
        
        # 4. Make requests to fast tier: Should fallback to Gemini successfully!
        # 4. Make requests to fast tier: Should fallback to Gemini successfully!
        print("\n3. Testing first request (Should trigger primary fail and fallback to Gemini)...")
        while True:
            status, body, _ = make_request(
                f"{base_url}/v1/chat/completions",
                "POST",
                team_headers,
                {"tier": "fast", "messages": [{"role": "user", "content": "Success check"}]}
            )
            print(f"Status: {status}")
            if status == 503:
                print("503 returned (both failed, likely rate limits). Waiting 10 seconds to retry...")
                time.sleep(10)
                continue
            elif status != 200:
                print(f"First request failed: {body}")
                return
            else:
                break
                
        print(f"Fallback Activated: {body.get('was_fallback')}")
        print(f"Served by: {body.get('provider')} / {body.get('model')}")
        print(f"Response Content: {body.get('content')[:50].encode('ascii', errors='replace').decode('ascii')}...")
        print_redis_state(r)
        
        # 5. Send 4 more requests to trip the Groq circuit breaker (Total 5 failures)
        print("\n4. Sending 4 more requests to trip the Groq circuit breaker...")
        for i in range(2, 6):
            print(f"Sending request {i}...")
            status, body, _ = make_request(
                f"{base_url}/v1/chat/completions",
                "POST",
                team_headers,
                {"tier": "fast", "messages": [{"role": "user", "content": "Check"}]}
            )
            if status != 200:
                print(f"Request {i} failed: {body}")
            else:
                print(f"Request {i} status: {status}, Was Fallback: {body.get('was_fallback')}")
            print_redis_state(r)
            # Sleep 4 seconds between requests to completely avoid Gemini free-tier rate limits
            time.sleep(4.0)

        # 6. Verify circuit state is OPEN. 
        # When OPEN, the primary (Groq) will be skipped immediately. We can confirm this by verifying
        # that the request latency drops significantly because we no longer spend time making the failed HTTP request to Groq!
        print("\n5. Testing 6th request with Groq circuit OPEN (should bypass Groq and route instantly to Gemini)...")
        print_redis_state(r)
        start_time = time.perf_counter()
        status, body, _ = make_request(
            f"{base_url}/v1/chat/completions",
            "POST",
            team_headers,
            {"tier": "fast", "messages": [{"role": "user", "content": "Bypass check"}]}
        )
        elapsed_ms = (time.perf_counter() - start_time) * 1000
        print(f"Status: {status}")
        if status != 200:
            print(f"Bypass check failed: {body}")
        else:
            print(f"Was Fallback: {body.get('was_fallback')}")
            print(f"Served by: {body.get('provider')} / {body.get('model')}")
            print(f"Latency: {body.get('latency_ms')} ms (Wall time: {elapsed_ms:.1f} ms)")
        print_redis_state(r)

    finally:
        print("Done.")

if __name__ == "__main__":
    run_verification()
