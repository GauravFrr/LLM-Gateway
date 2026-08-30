import urllib.request
import json
import time

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

def run_verification():
    base_url = "http://127.0.0.1:8000"
    
    # 1. Test invalid API key
    print("\n--- 1. Testing Invalid API Key ---")
    headers = {"Authorization": "Bearer invalid-key", "Content-Type": "application/json"}
    status, body, _ = make_request(
        f"{base_url}/v1/chat/completions",
        "POST",
        headers,
        {"tier": "balanced", "messages": [{"role": "user", "content": "hi"}]}
    )
    print(f"Status: {status}")
    print(f"Body: {json.dumps(body, indent=2)}")
    
    # 2. Test valid API key (seeded team-search-token-12345)
    print("\n--- 2. Testing Valid API Key ---")
    headers = {"Authorization": "Bearer team-search-token-12345", "Content-Type": "application/json"}
    status, body, _ = make_request(
        f"{base_url}/v1/chat/completions",
        "POST",
        headers,
        {"tier": "balanced", "messages": [{"role": "user", "content": "hi"}]}
    )
    print(f"Status: {status}")
    print(f"Body: {json.dumps(body, indent=2)}")

    # 3. Test Admin key auth check
    print("\n--- 3. Testing Admin Key invalid auth ---")
    admin_headers = {"Authorization": "Bearer wrong-admin-key", "Content-Type": "application/json"}
    status, body, _ = make_request(
        f"{base_url}/admin/teams",
        "POST",
        admin_headers,
        {"name": "temp-team", "monthly_budget_usd": 100.0, "priority_tier": "normal"}
    )
    print(f"Status: {status}")
    print(f"Body: {json.dumps(body, indent=2)}")

    # 4. Test Team creation via Admin key
    print("\n--- 4. Creating Team via Admin API ---")
    admin_headers = {"Authorization": "Bearer abcd", "Content-Type": "application/json"}
    team_name = f"team-billing-{int(time.time())}"
    status, team_data, _ = make_request(
        f"{base_url}/admin/teams",
        "POST",
        admin_headers,
        {"name": team_name, "monthly_budget_usd": 10.0, "priority_tier": "normal"}
    )
    print(f"Status: {status}")
    print(f"Body: {json.dumps(team_data, indent=2)}")

    if status != 200:
        print("Team creation failed. Cannot proceed with further tests.")
        return
        
    team_id = team_data["id"]
    new_team_key = team_data["api_key"]

    # 5. Configure model access for the new team
    print("\n--- 5. Configuring logical tier access for new team ---")
    status, access_data, _ = make_request(
        f"{base_url}/admin/teams/{team_id}/access",
        "POST",
        admin_headers,
        {
            "logical_tier": "fast",
            "primary_provider": "groq",
            "primary_model": "openai/gpt-oss-20b",
            "rate_limit_rpm": 2,  # Low rate limit to test rate limiting easily
            "rate_limit_tpm": 1000
        }
    )
    print(f"Status: {status}")
    print(f"Body: {json.dumps(access_data, indent=2)}")

    # 6. Test rate limiting on new team (RPM = 2)
    print("\n--- 6. Testing rate limiting (RPM = 2) ---")
    new_headers = {"Authorization": f"Bearer {new_team_key}", "Content-Type": "application/json"}
    
    # Send 3 requests quickly
    for i in range(1, 4):
        print(f"Sending request {i}...")
        status, body, resp_headers = make_request(
            f"{base_url}/v1/chat/completions",
            "POST",
            new_headers,
            {"tier": "fast", "messages": [{"role": "user", "content": "Success check"}]}
        )
        print(f"Request {i} status: {status}")
        if status == 429:
            print(f"Rate limited! Header Retry-After: {resp_headers.get('Retry-After')}")
            print(f"Response: {json.dumps(body, indent=2)}")
        else:
            # Ignore encoding issues when printing to a cp1252 Windows terminal
            content_str = body.get('content') if isinstance(body, dict) else str(body)
            safe_str = content_str.encode('ascii', errors='replace').decode('ascii')
            print(f"Response content: {safe_str}")
        time.sleep(0.1)

    # 7. Hot-reload test (update RPM limit via Admin API mid-test and confirm it takes effect)
    print("\n--- 7. Mid-test Hot-reload: Raising rate limit ---")
    status, access_data, _ = make_request(
        f"{base_url}/admin/teams/{team_id}/access",
        "POST",
        admin_headers,
        {
            "logical_tier": "fast",
            "primary_provider": "groq",
            "primary_model": "openai/gpt-oss-20b",
            "rate_limit_rpm": 100,  # Raise to 100 RPM
            "rate_limit_tpm": 50000
        }
    )
    print(f"Admin API update status: {status}")
    
    # Try requesting again immediately
    print("Sending request immediately after limit raise...")
    status, body, _ = make_request(
        f"{base_url}/v1/chat/completions",
        "POST",
        new_headers,
        {"tier": "fast", "messages": [{"role": "user", "content": "Hot reload verification"}]}
    )
    print(f"Request status after hot-reload: {status}")
    if status == 200:
        print("Success! Rate limit hot-reload worked instantly without restart.")
    else:
        print(f"Failed to hot-reload: {status}")

if __name__ == "__main__":
    run_verification()
