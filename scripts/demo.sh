#!/bin/bash

# Exit immediately if a command exits with a non-zero status
set -e

BASE_URL="http://localhost:8000"
ADMIN_KEY="abcd"
ADMIN_HEADERS="Authorization: Bearer $ADMIN_KEY"

echo "=== LLM Gateway End-to-End Walkthrough Demo ==="
echo "Make sure the docker-compose stack is running: docker-compose up -d"

# 1. Reset circuits to start clean
echo -e "\n1. Resetting all circuit breaker states..."
curl -s -X POST "$BASE_URL/admin/circuits/reset" \
  -H "$ADMIN_HEADERS" \
  -H "Content-Type: application/json" | json_pp || echo "Reset complete."

# 2. Create a new demo team with low budget and limits for demonstration
echo -e "\n2. Creating a new team 'team-demo' via Admin API..."
TEAM_RESPONSE=$(curl -s -X POST "$BASE_URL/admin/teams" \
  -H "$ADMIN_HEADERS" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "team-demo",
    "monthly_budget_usd": 0.0001,
    "priority_tier": "normal"
  }')

echo "$TEAM_RESPONSE" | json_pp || echo "$TEAM_RESPONSE"

TEAM_ID=$(echo "$TEAM_RESPONSE" | grep -o '"id":"[^"]*' | grep -o '[^"]*$')
API_KEY=$(echo "$TEAM_RESPONSE" | grep -o '"api_key":"[^"]*' | grep -o '[^"]*$')

echo "   Team ID: $TEAM_ID"
echo "   API Key: $API_KEY"

# 3. Configure logical tier access (RPM=2, primary=groq, fallback=gemini)
echo -e "\n3. Configuring model access for logical tier 'fast' (RPM=2)..."
curl -s -X POST "$BASE_URL/admin/teams/$TEAM_ID/access" \
  -H "$ADMIN_HEADERS" \
  -H "Content-Type: application/json" \
  -d '{
    "logical_tier": "fast",
    "primary_provider": "groq",
    "primary_model": "openai/gpt-oss-20b",
    "fallback_provider": "gemini",
    "fallback_model": "gemini-3.6-flash",
    "rate_limit_rpm": 2,
    "rate_limit_tpm": 50000
  }' | json_pp

TEAM_HEADERS="Authorization: Bearer $API_KEY"

# 4. Happy Path request
echo -e "\n4. Sending Request 1 (Happy Path through Groq)..."
curl -s -X POST "$BASE_URL/v1/chat/completions" \
  -H "$TEAM_HEADERS" \
  -H "Content-Type: application/json" \
  -d '{
    "tier": "fast",
    "messages": [{"role": "user", "content": "Hello"}],
    "max_tokens": 15
  }' | json_pp

# 5. Rate Limiting check
echo -e "\n5. Sending Request 2 & 3 immediately (Triggers 429 Rate Limiter block, RPM=2)..."
echo "Request 2 (Succeeds):"
curl -s -o /dev/null -w "   Status Code: %{http_code}\n" -X POST "$BASE_URL/v1/chat/completions" \
  -H "$TEAM_HEADERS" \
  -H "Content-Type: application/json" \
  -d '{"tier": "fast", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 15}'

echo "Request 3 (Rate Limited - 429):"
curl -s -X POST "$BASE_URL/v1/chat/completions" \
  -H "$TEAM_HEADERS" \
  -H "Content-Type: application/json" \
  -d '{"tier": "fast", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 15}' | json_pp

# 6. Wait for rate bucket to replenish
echo -e "\n6. Waiting 60s for rate limit bucket replenishment..."
sleep 60

# 7. Budget Warning / Block check
echo -e "\n7. Sending request to check budget enforcement (Warning header or 402 block)..."
BUDGET_RESP=$(curl -s -i -X POST "$BASE_URL/v1/chat/completions" \
  -H "$TEAM_HEADERS" \
  -H "Content-Type: application/json" \
  -d '{"tier": "fast", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 15}')

echo "$BUDGET_RESP" | grep -iE "HTTP/|x-budget|budget" || echo "$BUDGET_RESP"

# 8. Outage & Fallback check
echo -e "\n8. Simulating primary outage (pointing team-demo to invalid model)..."
curl -s -X POST "$BASE_URL/admin/teams/$TEAM_ID/access" \
  -H "$ADMIN_HEADERS" \
  -H "Content-Type: application/json" \
  -d '{
    "logical_tier": "fast",
    "primary_provider": "groq",
    "primary_model": "invalid-model-name-outage",
    "fallback_provider": "gemini",
    "fallback_model": "gemini-3.6-flash",
    "rate_limit_rpm": 1000,
    "rate_limit_tpm": 500000
  }' | json_pp

# Increase budget for team-demo so we can bypass budget check
echo -e "\nUpdating team-demo budget to $50 to bypass budget restrictions..."
curl -s -X POST "$BASE_URL/admin/teams" \
  -H "$ADMIN_HEADERS" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"team-demo\",
    \"monthly_budget_usd\": 50.00,
    \"priority_tier\": \"normal\"
  }" | json_pp

echo -e "\nSending request with primary outage (successfully falls back to Gemini)..."
curl -s -X POST "$BASE_URL/v1/chat/completions" \
  -H "$TEAM_HEADERS" \
  -H "Content-Type: application/json" \
  -d '{"tier": "fast", "messages": [{"role": "user", "content": "Say hi"}], "max_tokens": 15}' | json_pp

# 9. Circuit Breaker trigger & bypass
echo -e "\n9. Sending 4 more outage requests to trip Gemini circuit..."
for i in {2..5}; do
  echo "   Attempt $i:"
  curl -s -o /dev/null -X POST "$BASE_URL/v1/chat/completions" \
    -H "$TEAM_HEADERS" \
    -H "Content-Type: application/json" \
    -d '{"tier": "fast", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 15}'
done

echo -e "\nRetrieving circuit status for groq..."
curl -s -X GET "http://localhost:8000/metrics" | grep "circuit_breaker_state" || echo "Check Grafana operations dashboard."

echo -e "\nSending request with circuit OPEN (instantly bypasses primary Groq)..."
curl -s -i -X POST "$BASE_URL/v1/chat/completions" \
  -H "$TEAM_HEADERS" \
  -H "Content-Type: application/json" \
  -d '{"tier": "fast", "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 15}' | grep -E "HTTP/|was_fallback|provider" || true

echo -e "\n=== Demo walkthrough complete! ==="
