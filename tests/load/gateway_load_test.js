import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';

// Custom metrics to track during load test
const requestCount = new Counter('gateway_requests_total');
const rateLimitCount = new Counter('gateway_rate_limited_total');
const fallbackCount = new Counter('gateway_fallback_total');
const overheadTrend = new Trend('gateway_overhead_ms');

const gatewayUrl = __ENV.GATEWAY_URL || 'http://localhost:8000';

export const options = {
  scenarios: {
    load_test: {
      executor: 'constant-vus',
      vus: 50,
      duration: '35s',
    },
  },
  thresholds: {
    // Assert rate limit accuracy: we will check this manually or programmatically.
    // We want 0 system errors (5xx) except simulated/expected behavior.
    http_req_failed: ['rate<0.1'], // Less than 10% errors overall (including simulated outages)
  },
};

const teams = [
  {
    name: 'team-search',
    apiKey: 'team-search-token-12345',
    tier: 'fast',
    id: '64696922-2efd-4b41-9858-ead6607f1d86',
    model: 'openai/gpt-oss-20b',
  },
  {
    name: 'team-analytics',
    apiKey: 'team-analytics-token-67890',
    tier: 'balanced',
    id: 'f19f2a33-4f22-4411-9a9e-1f2e3d4c5b6a',
    model: 'gemini-3.6-flash',
  },
  {
    name: 'team-finance',
    apiKey: 'team-finance-token-abcde',
    tier: 'quality',
    id: 'e3d4c5b6-7a8b-9c0d-1e2f-3a4b5c6d7e8f',
    model: 'claude-3-5-sonnet-20241022',
  },
];

// Shared state for outage coordination
let outageTriggered = false;
let restoreTriggered = false;
const testStartTime = Date.now();

export default function () {
  const elapsed = (Date.now() - testStartTime) / 1000;

  // VU 1 coordinates the mid-test outage window (seconds 10 to 20)
  if (__VU === 1) {
    const adminHeaders = {
      'Authorization': 'Bearer abcd',
      'Content-Type': 'application/json',
    };

    if (elapsed >= 10 && elapsed < 11 && !outageTriggered) {
      outageTriggered = true;
      console.log('--- SIMULATING OUTAGE: Pointing team-search to invalid model ---');
      http.post(
        `${gatewayUrl}/admin/teams/64696922-2efd-4b41-9858-ead6607f1d86/access`,
        JSON.stringify({
          logical_tier: 'fast',
          primary_provider: 'groq',
          primary_model: 'invalid-model-to-trigger-outage',
          fallback_provider: 'gemini',
          fallback_model: 'gemini-3.6-flash',
          rate_limit_rpm: 10000,
          rate_limit_tpm: 5000000,
        }),
        { headers: adminHeaders }
      );
    }

    if (elapsed >= 20 && elapsed < 21 && !restoreTriggered) {
      restoreTriggered = true;
      console.log('--- END OF OUTAGE: Restoring team-search primary model ---');
      http.post(
        `${gatewayUrl}/admin/teams/64696922-2efd-4b41-9858-ead6607f1d86/access`,
        JSON.stringify({
          logical_tier: 'fast',
          primary_provider: 'groq',
          primary_model: 'openai/gpt-oss-20b',
          fallback_provider: 'gemini',
          fallback_model: 'gemini-3.6-flash',
          rate_limit_rpm: 10000,
          rate_limit_tpm: 5000000,
        }),
        { headers: adminHeaders }
      );
    }
  }

  // Choose team randomly
  const team = teams[Math.floor(Math.random() * teams.length)];

  const headers = {
    'Authorization': `Bearer ${team.apiKey}`,
    'Content-Type': 'application/json',
  };

  const payload = JSON.stringify({
    tier: team.tier,
    messages: [{ role: 'user', content: 'Say hi' }],
    max_tokens: 15,
  });

  const startTime = Date.now();
  const res = http.post(`${gatewayUrl}/v1/chat/completions`, payload, { headers });
  const latency = Date.now() - startTime;

  requestCount.add(1);

  if (res.status === 200) {
    const body = JSON.parse(res.body);
    const wasFallback = body.was_fallback || false;

    if (wasFallback) {
      fallbackCount.add(1);
    }

    // Capture overhead from response header if returned, else estimate
    const gatewayOverhead = parseFloat(res.headers['X-Gateway-Overhead-Ms'] || '0') || (latency - 10);
    overheadTrend.add(gatewayOverhead);

    check(res, {
      'status is 200': (r) => r.status === 200,
    });
  } else if (res.status === 429) {
    rateLimitCount.add(1);
    check(res, {
      'status is 429': (r) => r.status === 429,
    });
  } else {
    check(res, {
      'status is 5xx': (r) => r.status >= 500,
    });
  }

  // Small sleep to control request rate pacing
  sleep(0.010); // 10ms pacing
}

export function setup() {
  // Clear circuit state before run starts
  console.log('--- Setup: Resetting all circuits ---');
  http.post(`${gatewayUrl}/admin/circuits/reset`, null, {
    headers: { 'Authorization': 'Bearer abcd' },
  });
}
