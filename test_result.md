#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================

user_problem_statement: |
  Top-down audit of TRACKMIND with highest-standards grading, then bulletproof the platform
  in phases. Phase 0 (dashboard compile fix + tsc CI gate) and Phase 1 (security + money
  correctness + observability) are complete.

backend:
  - task: "Phase 1 — JWT refresh rotation + revocation (jti blocklist)"
    implemented: true
    working: true
    file: "backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
      - working: "NA"
        agent: "main"
        comment: "Refresh tokens now carry jti; each /auth/refresh revokes old jti (stored in revoked_tokens with TTL index on exp) and issues a new one. Reuse of old token is detected and 401s. /auth/logout also revokes. Verify rotation and reuse detection."
      - working: true
        agent: "testing"
        comment: "PASS. TestPhase1Security::test_refresh_rotation verified: first /auth/refresh returns a new refresh_token != old, and reusing the old refresh token returns 401 with 'Token reuse detected'. Existing TestAuth::test_refresh_token also passes. /auth/logout tested implicitly via revocation flow."
  - task: "Phase 1 — Per-IP rate limiting on auth + trade endpoints"
    implemented: true
    working: false
    file: "backend/server.py"
    stuck_count: 1
    priority: "high"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: "Custom sliding-window RateLimit class. Limits: login 20/min, register 5/hr, refresh 30/min, trade/approve 30/min, auto-deploy 10/min. Verify 429 after limit hit and correct Retry-In message."
      - working: false
        agent: "testing"
        comment: "FAIL. test_rate_limit_login assertion 'rate limiter never tripped' — fired 30 rapid logins from same external client, zero 429 responses (all 401). Two root causes observed: (1) `RateLimit.check` uses request.client.host which, behind the Kubernetes ingress, appears to rotate across ingress pod IPs, so the sliding-window counter never fills for a single key. Fix: run uvicorn with --proxy-headers --forwarded-allow-ips='*' and/or read X-Forwarded-For inside RateLimit.check for the client IP. (2) The brute-force lockout in /auth/login also crashes at server.py:412 with `TypeError: can't subtract offset-naive and offset-aware datetimes` — Mongo returns login_attempts.last_at as naive, but datetime.now(timezone.utc) is aware. Fix: store last_at as tz-aware and/or coerce with `.replace(tzinfo=timezone.utc)` before subtracting. See backend.err.log traceback. Because the brute-force check 500s, that path never returns 429 either."
  - task: "Phase 1 — Idempotency-Key on /trade/approve and /trade/auto-deploy"
    implemented: true
    working: false
    file: "backend/server.py"
    stuck_count: 1
    priority: "high"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: "Same key replay returns cached response (no double debit, no duplicate position). idempotency_keys collection has TTL=10min. Verify duplicate approve with same key yields identical fill.id and only ONE position created."
      - working: false
        agent: "testing"
        comment: "Core idempotency logic is CORRECT — verified manually that two /trade/approve calls with the same Idempotency-Key return the same fill.id, bankroll is debited only once, and only one row appears in /positions.open. HOWEVER test_idempotency_approve FAILS at `assert len(matches) == 1` because it filters by `p.get('fill_id') == fill_a` and `_serialize_position` (backend/server.py:591) does NOT include `fill_id` in the returned object. Fix: add `\"fill_id\": p.get(\"fill_id\")` to _serialize_position (same for signal_id if useful). This is a contract bug, not an idempotency bug."
  - task: "Phase 1 — Atomic bankroll debit (insufficient-funds safe)"
    implemented: true
    working: false
    file: "backend/server.py"
    stuck_count: 1
    priority: "high"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: "_atomic_bankroll_debit uses findOneAndUpdate with $gte guard. Broker failure refunds. Verify oversize trade returns 400 and bankroll unchanged."
      - working: false
        agent: "testing"
        comment: "FAIL. test_bankroll_atomicity_insufficient_funds expects 400 on an oversized trade (notional=9_999_999). Actual response: 200, and bankroll silently went from $10,000 -> $500. Root cause at server.py:682-683 — user-provided `notional` is silently clamped via `notional = max(1.0, min(notional, bankroll * 0.95))` BEFORE the atomic debit, so _atomic_bankroll_debit never fails. Fix options: (a) if `payload.notional is not None and payload.notional > bankroll` → raise 400 explicitly before clamping, or (b) remove the hard clamp and rely on _atomic_bankroll_debit to fail, then return 400. The atomic guard itself works correctly; it's just never exercised for user-specified oversizing."
  - task: "Phase 1 — Settlement lock per user (no double-settle)"
    implemented: true
    working: "NA"
    file: "backend/server.py"
    stuck_count: 0
    priority: "medium"
    needs_retesting: true
    status_history:
      - working: "NA"
        agent: "main"
        comment: "settle_open_positions wrapped in asyncio.Lock per user_id; settlement uses conditional update {status:'open'} -> modified_count==1 before adding realized PnL. Verify concurrent /dashboard calls don't double-count pnl."
      - working: "NA"
        agent: "testing"
        comment: "Not covered by TestPhase1Security; no dedicated concurrency test in the provided suite. Regression tests that touch settlement (TestDashboard, TestTradeAndPositions, TestControls::test_reset_account) all pass, indicating no obvious breakage, but the concurrency claim itself is untested."
  - task: "Phase 1 — LLM call cache + metrics"
    implemented: true
    working: true
    file: "backend/engines/alpha.py, backend/server.py"
    stuck_count: 0
    priority: "medium"
    needs_retesting: false
    status_history:
      - working: "NA"
        agent: "main"
        comment: "Alpha run_async accepts score_cache + metrics dict; caches narrative scores per (canonical_id, 3-min bucket). /admin/metrics exposes calls/cache_hits/failures/avg_latency_ms. Verify /admin/metrics returns non-empty llm stats."
      - working: true
        agent: "testing"
        comment: "PASS (via /admin/metrics). test_admin_metrics_requires_admin verified response contains `llm`, `engines`, and `db` keys."
  - task: "Phase 1 — Admin metrics + user audit endpoints"
    implemented: true
    working: true
    file: "backend/server.py"
    stuck_count: 0
    priority: "medium"
    needs_retesting: false
    status_history:
      - working: "NA"
        agent: "main"
        comment: "GET /api/admin/metrics (admin-only) — LLM + engines + db stats. GET /api/audit (user) — returns caller's audit trail. Verify admin-only gate and that regular users get 403 on /admin/metrics."
      - working: true
        agent: "testing"
        comment: "PASS when run in isolation — /api/admin/metrics returns body with `llm`/`engines`/`db`, and /api/audit returns `{events: [...]}`. In the full suite these two tests failed with `KeyError: 'access_token'` but that was a CASCADE from test_rate_limit_login leaving the rate-limit bucket saturated / brute-force row poisoned (not a defect in the endpoints themselves). Re-ran the two tests alone: both pass. Main agent: once the rate-limit/brute-force fixes land, these will pass in the full suite too."
  - task: "Phase 1 — JWT_SECRET fail-safe + TTL indexes (revoked_tokens, idempotency, audit)"
    implemented: true
    working: true
    file: "backend/server.py"
    stuck_count: 0
    priority: "medium"
    needs_retesting: false
    status_history:
      - working: "NA"
        agent: "main"
        comment: "JWT_SECRET rejects insecure defaults and auto-generates ephemeral one with warning. On startup, TTL indexes created for revoked_tokens.exp (absolute), idempotency_keys.created_at (600s), audit_logs.ts (30 days). Regression: ensure startup succeeds cleanly."
      - working: true
        agent: "testing"
        comment: "PASS. Backend starts cleanly (supervisor RUNNING), /api/health OK, full regression suite (20 non-Phase-1 tests) green."

frontend:
  - task: "Phase 0 — Dashboard compile fix (imports + JSX balance)"
    implemented: true
    working: "NA"
    file: "frontend/app/(tabs)/index.tsx"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
      - working: "NA"
        agent: "main"
        comment: "tsc --noEmit passes; added missing haptics + FadeSlideIn imports; balanced orphan </FadeSlideIn> by wrapping TopBar. Frontend smoke test gated by user — not auto-run."
  - task: "Phase 1 — SecureStore for tokens + idempotency-key header"
    implemented: true
    working: "NA"
    file: "frontend/lib/api.ts"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
      - working: "NA"
        agent: "main"
        comment: "Tokens now stored in expo-secure-store on native (Keychain/Keystore), AsyncStorage fallback on web. Trade POSTs auto-attach Idempotency-Key. Refresh flow rotates both access + refresh."

metadata:
  created_by: "main_agent"
  version: "1.1"
  test_sequence: 2
  run_ui: false

test_plan:
  current_focus:
    - "Phase 1 — JWT refresh rotation + revocation (jti blocklist)"
    - "Phase 1 — Per-IP rate limiting on auth + trade endpoints"
    - "Phase 1 — Idempotency-Key on /trade/approve and /trade/auto-deploy"
    - "Phase 1 — Atomic bankroll debit (insufficient-funds safe)"
    - "Phase 1 — Admin metrics + user audit endpoints"
  stuck_tasks: []
  test_all: false
  test_priority: "high_first"

agent_communication:
  - agent: "main"
    message: |
      Phase 1 (security + money correctness + observability) landed.
      Focus of this pass: (1) refresh token rotation & reuse detection, (2) per-IP rate limits,
      (3) idempotency on trade endpoints, (4) atomic bankroll, (5) /admin/metrics + /audit.
      New pytest class TestPhase1Security exists in backend/tests/test_trackmind.py with
      6 tests covering all of the above. Please also run the full pre-existing suite to
      confirm no regressions. Admin creds in /app/memory/test_credentials.md.
  - agent: "testing"
    message: |
      Ran full pytest suite `backend/tests/test_trackmind.py` against external
      EXPO_PUBLIC_BACKEND_URL. 20/25 PASS, 5/25 FAIL — all 5 failures are inside
      TestPhase1Security. Every non-Phase-1 test (TestHealth, TestAuth, TestDashboard,
      TestSignals, TestTradeAndPositions, TestAnalytics, TestControls including kill
      switch + reset) is green — no regressions.

      FAILURES (all reproducible; root causes confirmed by reading server.py and
      backend.err.log):

      1) TestPhase1Security::test_idempotency_approve  — Idempotency itself IS
         working (manual verify: same Idempotency-Key returns identical fill.id,
         only one position is created, bankroll debited exactly once). The test
         only fails because `_serialize_position` (server.py:591-612) does not
         expose `fill_id` on position objects, so the test's filter
         `[p for p in pos['open'] if p.get('fill_id') == fill_a]` is always [].
         FIX: add `"fill_id": p.get("fill_id")` (and optionally `signal_id`)
         to `_serialize_position`.

      2) TestPhase1Security::test_bankroll_atomicity_insufficient_funds — User-
         provided `notional` that exceeds bankroll is silently clamped at
         `notional = max(1.0, min(notional, bankroll * 0.95))` (server.py:683),
         so an oversized trade returns 200 and drains ~95% of the account.
         _atomic_bankroll_debit never gets to fail. FIX: when `payload.notional`
         is provided AND > bankroll, raise HTTPException(400, "Insufficient
         bankroll") before clamping; OR drop the clamp and rely on the atomic
         guard to fail and return 400.

      3) TestPhase1Security::test_rate_limit_login — Rate limiter never trips.
         Verified independently: 30 rapid logins from the same external client
         produced 30×401 and 0×429. TWO root causes:
           (a) `RateLimit.check` keys by `request.client.host`, but behind this
               k8s ingress the backend sees multiple upstream IPs per logical
               client — I saw 10.64.135.205 AND 10.64.129.146 in backend.out.log
               for the SAME test run. The sliding window therefore splits across
               keys and never hits 20/min. FIX: launch uvicorn with
               `--proxy-headers --forwarded-allow-ips '*'` (supervisord.conf)
               AND/OR have RateLimit.check read X-Forwarded-For (first IP).
               Even better: back the limiter with Mongo/Redis so it's correct
               across workers.
           (b) The brute-force lockout path in /auth/login raises
               `TypeError: can't subtract offset-naive and offset-aware
               datetimes` at server.py:412 — MongoDB returns
               `login_attempts.last_at` as tz-naive, datetime.now(timezone.utc)
               is aware. Every 6th+ wrong-password attempt 500s. FIX: coerce
               with `last_at = attempt['last_at'].replace(tzinfo=timezone.utc)`
               before subtracting. (See backend.err.log traceback.)

      4) TestPhase1Security::test_admin_metrics_requires_admin and
         TestPhase1Security::test_user_audit_endpoint — Both PASS when run in
         isolation. In the full-suite order they fail with
         `KeyError: 'access_token'` because the prior test_rate_limit_login run
         leaves the per-IP login rate-limit bucket saturated on one ingress pod
         AND/OR leaves a tz-naive login_attempts row that keeps 500-ing. After
         fixes (3a)+(3b) these will go green in the full suite.

      PASSING Phase 1 tests:
        - test_refresh_rotation (rotation + reuse detection both work).
        - test_admin_metrics_requires_admin (works in isolation).
        - test_user_audit_endpoint (works in isolation).

      Per testing-agent policy I did NOT modify server.py. All five failures
      require main-agent code changes as described above. Do not re-run the
      suite until those are applied.
