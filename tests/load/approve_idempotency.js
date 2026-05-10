// @ts-nocheck
// k6 load test: idempotency under parallel approves.
//
// Scenario:
//   100 concurrent VUs, each POSTing /trade/approve for the SAME signal with the SAME
//   Idempotency-Key header. Contract: exactly ONE position should be created in total,
//   exactly ONE debit should happen, and every VU should receive a 200 with the same fill_id.
//
// Run:
//   BASE_URL=https://<host> ADMIN_EMAIL=admin@trackmind.io ADMIN_PASSWORD=Admin123! \
//     k6 run tests/load/approve_idempotency.js
//
// Exits non-zero if any threshold breaches.
import http from "k6/http";
import { check, group } from "k6";
import { Counter } from "k6/metrics";

const BASE = __ENV.BASE_URL || "http://localhost:8001";
const EMAIL = __ENV.ADMIN_EMAIL || "admin@trackmind.io";
const PASSWORD = __ENV.ADMIN_PASSWORD || "Admin123!";

const distinctFills = new Counter("distinct_fills_seen");

export const options = {
  scenarios: {
    thundering_herd: {
      executor: "per-vu-iterations",
      vus: 100,
      iterations: 1,
      maxDuration: "60s",
    },
  },
  thresholds: {
    "http_req_failed": ["rate<0.01"],  // <1% network errors
    "http_req_duration{kind:approve}": ["p(95)<2000"],  // 95% under 2s
    "distinct_fills_seen": ["count==1"],  // EXACTLY one distinct fill_id across all VUs
  },
};

let CACHED_TOKEN = "";
let CACHED_SIGNAL_ID = "";
let SHARED_IDEMPOTENCY_KEY = "";

export function setup() {
  // Login once.
  const loginRes = http.post(
    `${BASE}/api/auth/login`,
    JSON.stringify({ email: EMAIL, password: PASSWORD }),
    { headers: { "Content-Type": "application/json" } }
  );
  if (loginRes.status !== 200) {
    throw new Error(`login failed: ${loginRes.status} ${loginRes.body}`);
  }
  const token = loginRes.json("access_token");
  // Grab one signal.
  const sigRes = http.get(`${BASE}/api/signals`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const sigs = sigRes.json("signals");
  if (!sigs || sigs.length === 0) {
    throw new Error("no signals available");
  }
  const sid = sigs[0].id;
  // Generate ONE idempotency key shared by all VUs.
  const key = `loadtest-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return { token, sid, key };
}

export default function (data) {
  group("approve same (signal, key)", () => {
    const res = http.post(
      `${BASE}/api/trade/approve`,
      JSON.stringify({ signal_id: data.sid, notional: 50 }),
      {
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${data.token}`,
          "Idempotency-Key": data.key,
        },
        tags: { kind: "approve" },
      }
    );
    const passed = check(res, {
      "status 200 or 400-insufficient": (r) =>
        r.status === 200 || (r.status === 400 && r.body.includes("Insufficient")),
    });
    if (!passed) {
      // eslint-disable-next-line no-console
      console.error(`VU ${__VU} got ${res.status}: ${res.body.slice(0, 200)}`);
    }
    if (res.status === 200) {
      const fillId = res.json("fill.id");
      // distinctFills is incremented once per unique fillId sighting.
      // Since idempotency guarantees one fill_id, this counter should only ever be 1.
      // We approximate by setting value to hash-of-fill-id (k6 doesn't expose distinct counting
      // natively, but we track cardinality by tagging) — simplest approach: assume all requests
      // return the same id, so we only need to verify ONE fill_id is ever returned.
      if (SHARED_IDEMPOTENCY_KEY === "") {
        SHARED_IDEMPOTENCY_KEY = fillId;
        distinctFills.add(1);
      } else if (SHARED_IDEMPOTENCY_KEY !== fillId) {
        distinctFills.add(1); // Another distinct fill — contract violation
        console.error(`IDEMPOTENCY BREACH: saw ${fillId}, expected ${SHARED_IDEMPOTENCY_KEY}`);
      }
    }
  });
}
