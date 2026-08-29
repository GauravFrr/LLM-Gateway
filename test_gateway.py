import urllib.request
import json
import sys

def test_tier(tier: str):
    url = "http://127.0.0.1:8000/v1/chat/completions"
    payload = {
        "tier": tier,
        "messages": [
            {"role": "user", "content": "Hello! Respond with the word 'Success' if you read this."}
        ],
        "max_tokens": 10
    }
    
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(
        url, 
        data=json.dumps(payload).encode("utf-8"), 
        headers=headers, 
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            print(f"\n=== Test {tier} Success ===")
            print(json.dumps(res_data, indent=2))
    except urllib.error.HTTPError as e:
        print(f"\n=== Test {tier} HTTP Error {e.code} ===")
        try:
            err_data = json.loads(e.read().decode("utf-8"))
            print(json.dumps(err_data, indent=2))
        except Exception:
            print(e.reason)
    except Exception as e:
        print(f"\n=== Test {tier} Connection Error ===")
        print(str(e))

if __name__ == "__main__":
    tier = "balanced"
    if len(sys.argv) > 1:
        tier = sys.argv[1]
    print(f"Testing LLM Gateway at http://127.0.0.1:8000 using tier: {tier}")
    test_tier(tier)
