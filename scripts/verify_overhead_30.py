import httpx
import time
import statistics

BASE_URL = "http://localhost:8000"
API_KEY = "team-search-token-12345"  # seeded team-search key

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

payload = {
    "tier": "fast",
    "messages": [{"role": "user", "content": "hi"}],
    "max_tokens": 10
}

print("=== Running Real-Provider Latency Overhead Test (35 requests) ===")
print("Sleeping 2.1s between requests to prevent upstream Groq rate limits (30 RPM)...")

overhead_values = []
total_durations = []

with httpx.Client(timeout=10.0) as client:
    for i in range(1, 36):
        start = time.perf_counter()
        try:
            r = client.post(f"{BASE_URL}/v1/chat/completions", json=payload, headers=headers)
            duration_ms = (time.perf_counter() - start) * 1000
            
            if r.status_code == 200:
                overhead_header = r.headers.get("X-Gateway-Overhead-Ms")
                overhead_ms = float(overhead_header) if overhead_header else 0.0
                
                overhead_values.append(overhead_ms)
                total_durations.append(duration_ms)
                
                print(f"Request {i:02d}: Status {r.status_code} | Total: {duration_ms:.1f}ms | Overhead: {overhead_ms:.2f}ms")
            else:
                print(f"Request {i:02d}: Status {r.status_code} | Error: {r.text}")
        except Exception as e:
            print(f"Request {i:02d}: Exception occurred: {str(e)}")
            
        time.sleep(2.1)

if overhead_values:
    p50 = statistics.median(overhead_values)
    p95 = statistics.quantiles(overhead_values, n=20)[18]  # 95th percentile
    max_val = max(overhead_values)
    avg_val = statistics.mean(overhead_values)
    
    print("\n=== Latency Overhead Results (ms) ===")
    print(f"Sample Size: {len(overhead_values)}")
    print(f"P50 (Median) Overhead : {p50:.2f} ms")
    print(f"P95 Overhead          : {p95:.2f} ms")
    print(f"Max Overhead          : {max_val:.2f} ms")
    print(f"Average Overhead      : {avg_val:.2f} ms")
else:
    print("No successful requests recorded.")
