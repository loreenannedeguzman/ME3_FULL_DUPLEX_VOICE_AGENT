# Normal assistant provider admission â€” WB-01.3

The selected Groq backend now checks known provider balances and cooldowns
before creating a client or issuing an HTTP POST. The separate SharedBudget
continues to enforce project reservations and its unchanged local rate guard.
This change adds no provider, tokenizer dependency, retry, wait loop, microphone
capture, TTS change or permission to execute an action.

Groq documents request headers as **requests per day**, token headers as
**tokens per minute**, and retry-after as seconds on HTTP 429:
[Groq rate-limit documentation](https://console.groq.com/docs/rate-limits).
Account limits apply across the organization; this Pi cannot observe concurrent
usage by every external client. A public limits table is not current account
allowance. Existing fresh allowance/readiness and project grants still apply.

## Behaviour

- Production Groq transports share `evidence/assistant/provider-admission/groq.json`
  across instances/processes/sessions. It stores only numeric quota observations,
  expiry/cooldown and ownership metadata. No key, prompt, transcript, tool argument
  or provider error prose is retained. ASR and reasoning balances are separate.
- Before dispatch, a short file lock checks known RPD and token balances,
  cooldown and a 60-second per-model lease. It reserves a local forecast;
  the lock is released before network I/O. A denial has code429 and safe metadata,
  sends zero HTTP requests, and retains existing fail-closed quota handling.
- Reasoning demand includes declared maximum completion tokens (unchanged1024)
  plus UTF-8 JSON bytes/3 rounded up and128 framing tokens. **This is a planning
  estimate, not an exact tokenizer count or a guaranteed upper bound.** It can
  under/overestimate different languages/content. Logs explicitly mark it inexact.
  A known shortfall or estimated request larger than known capacity is rejected.
- Actual server headers replace only the local forecast. They do not refund or
  decrease project call reservations. Cancellation/failed calls retain their
  forecast until new observations or expiry. HTTP429 creates a cooldown;
  absent/invalid retry-after uses60seconds. No automatic retry occurs afterward.
- Missing reset durations use conservative defaults:60seconds for token balance,
  one day for request balance. On expiry the balance becomes UNKNOWN, never an
  invented refill. Only a new explicit request may contact the server again.
- Corrupt metadata, clock reversal and unsafe file/lock symlinks fail closed.
  A lease expires after60seconds if the owner dies; the owned HTTP request still
  has its unchanged30-second timeout. An old owner cannot clear a newer lease.
- Injected fixture sessions default to isolated memory. Local TLS fixture tests
  inject memory explicitly; they never seed the production observation file.

## Limits of this evidence

Provider-free checks establish admission, persistence, cancellation and no-POST
behaviour. They do not establish current account balance, physical speech latency,
exact token counting, ASR audio-hour/day accounting, separate ITPM/OTPM/TPD
account limits or the behaviour of external clients. ASR request balance/cooldown
is enforced; audio-second quota still needs existing external-allowance checks.
Google retains its existing project/rate/grant controls; the Groq header guard
does not claim to predict Google token quotas. These boundaries remain explicit
in the work board and current-candidate qualification rather than inferred PASS.


WB-02.1 correction: for the controlled JSON planning shape, estimate decoded message UTF-8 text plus framing and the response schema, instead of counting HTTP JSON escaping a second time. The known GPT-OSS20B ASCII profile uses bytes/4 plus128 framing and the full declared output capacity; Unicode/escaped Unicode retains bytes/3. This is an empirical inexact forecast, motivated by the public summary observation of2686 input tokens versus an earlier5841 total forecast; it is not an exact tokenizer or upper-bound guarantee. Unknown/native request shapes retain the original serialization fallback. The524288-byte wire limit, output allowance, known-balance denial, no refunds/retries and privacy rules remain. No promise that other account clients or unknown tokenization cannot cause429.


Current correction-003 policy supersedes the provisional c57 ASCII/4 calibration: all decoded JSON-profile forecasts again use bytes/3 +128 +full output capacity. Completed goals now enter an answer-only model pass with no operation declarations; removing unneeded tool schemas reduces that final request without weakening admission. Unknown/native fallback, caps, cooldown, no refunds/retries and inexact-estimate limitations remain. The c57 forecast was not the typed-001 failure: all three requests were admitted; an extra repeated goal exhausted the reasoning ceiling.
