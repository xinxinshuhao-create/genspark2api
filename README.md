# genspark2api

OpenAI-compatible API bridge for the Genspark web session — **multi-account round-robin**,
streaming support, and an automated registration toolkit.

The bridge reuses a web session **you exported yourself**, so it works on the free tier
where the official API key path is blocked. The browser is only used once, to export
cookies; the proxy itself is pure HTTP.

---

## How it works

```
OpenAI-compatible client
        │  POST /v1/chat/completions
        ▼
genspark2api.py  (127.0.0.1:8899)
        │  round-robin over the account pool
        ├─ account 1  cookie + optional egress proxy
        ├─ account 2  cookie + optional egress proxy
        └─ account N  ...
        ▼
upstream web session endpoint  (SSE)
```

**The browser is NOT in the request path.** A single login run (`gs_login.py`) exports the
session cookies; after that the bridge talks HTTP directly.

---

## Quick start

### 1. Requirements

```bash
pip install fastapi uvicorn curl_cffi cloakbrowser
```

### 2. Export a session cookie

Log in through a dedicated browser profile (never your system Chrome profile):

```bash
python gs_login.py            # opens a window; log in, then create a .proceed file
python gs_login.py --auto     # or export immediately if already logged in
```

This writes `cookies1.json` containing the session cookies.

### 3. Configure the account pool

```bash
cp accounts.example.json accounts.json
```

Fill in one entry per account. Only `cookie_file` is strictly required; `proxy` is optional
but recommended for per-account egress isolation.

```json
{
  "accounts": [
    { "seq": 1, "email": "you@example.com", "cookie_file": "cookies1.json",
      "proxy": "", "status": "active" }
  ]
}
```

### 4. Run

```bash
python genspark2api.py
# serving on :8899
```

### 5. Call it

```bash
curl http://127.0.0.1:8899/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"gpt-6-luna","messages":[{"role":"user","content":"hi"}]}'
```

### Endpoints

| Endpoint | Description |
|---|---|
| `POST /v1/chat/completions` | OpenAI-compatible; supports `stream: true` and `tools` (emulated — see below) |
| `GET /v1/models` | Model list |
| `GET /health` | Per-account status: `ready`, `proxy`, `cooldown_left_s`, success/failure counters |

---

## Tool calling (emulated)

**The upstream web session does not accept OpenAI-style `tools`.** Measured 2026-09-23:
the parameter is accepted (HTTP 200) but silently ignored, and every model answers
"I can't call a tool in this chat" — verified on `gpt-6-luna`, `claude-opus-5-5`,
`gemini-3.8-flash` and `GLM-5.3`.

The bridge therefore emulates tools at the gateway, the usual approach for web bridges:

1. **Inject** — the tool schemas are rendered into a system prompt with a strict output
   contract (`{"tool_call": {"name": ..., "arguments": {...}}}`).
2. **Parse** — the model's reply is parsed back into OpenAI `tool_calls`, with
   `finish_reason: "tool_calls"`.
3. **Flatten** — OpenAI tool-protocol messages are rewritten before they go upstream,
   because the native shapes are rejected with **HTTP 422**:
   `assistant{tool_calls:[...]}` → `assistant{content: <contract line>}` and
   `tool{tool_call_id, content}` → `user{content: "TOOL RESULT ..."}`.

Both streaming and non-streaming are supported. In streaming mode the response is
buffered when `tools` is present (content already emitted cannot be retracted), then
emitted as `tool_calls` deltas: an id+name chunk followed by an arguments chunk.

Verified end to end against the live upstream: 14/14 checks covering the call/no-call
decision, argument fidelity, streaming reassembly, and round-tripping a tool result.

### Limitations vs native function calling

| Aspect | Native | This emulation |
|---|---|---|
| Parallel tool calls | Supported | **One per reply** (the model is told to call the first, the rest follow after its result) |
| Argument validation | Enforced by the API | Prompt-level only |
| Reliability | Schema-constrained | Depends on the model following the contract |
| Streaming `tool_calls` | Native | Re-emitted as deltas (buffered first) |

---

## Supported models

Tested 2026-09-23 — **50 of 53 reachable** on a free-tier account.
Grouped by upstream family; the model IDs below are the ones you pass in `"model"`.

### OpenAI

| Model ID | Notes |
|---|---|
| `gpt-6-luna` | Current default; verified against upstream fingerprint |
| `gpt-6-sol` | |
| `gpt-5.6-luna` / `gpt-5.6-sol` / `gpt-5.6-terra` | |
| `gpt-5.5` / `gpt-5.5-pro` | |
| `gpt-5.4` / `gpt-5.4-mini` / `gpt-5.4-nano` / `gpt-5.4-pro` | |
| `gpt-5.2` / `gpt-5.1-high` / `gpt-5-pro` / `gpt-5` | |

### Anthropic

`claude-opus-5-5`, `claude-opus-5`, `claude-opus-4-8`, `claude-opus-4-7`,
`claude-opus-4-6`, `claude-sonnet-5`, `claude-sonnet-4-6`, `claude-sonnet-4-5`,
`claude-sonnet-4`, `claude-4-5-haiku`

### Google

`gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.6-flash`,
`gemini-3.1-pro-preview`, `gemini-3.1-flash-lite-preview`, `gemini-2.5-flash`

### Other

`grok-4.7`, `grok-4.6`, `grok-4.5`, `kimi-k3`, `GLM-5.3`, `glm-5p3`,
`deep-seek-v4.1-flash`, `deep-seek-v4-flash`, `minimax-m3`, `nemotron-3-ultra`

**Not reachable:** `claude-opus-4-1`, `kimi-k2-instruct` (upstream returns an error),
`claude-opus-4-5` (transient network error during testing).

> Model availability changes upstream without notice. The list above is a snapshot.

---

## Free-tier limits (measured)

| Limit | Value |
|---|---|
| Credits per request | **1** |
| Signup grant | **100** credits, **one-time**, expires in 24 h |
| Rate limit | **6 requests/minute, 60/hour** per account |
| Concurrent | **3+ → HTTP 429** |
| 429 recovery | ~30 s (no `Retry-After` header) |

**The 100-credit grant is issued once per account, not daily.** The ledger type is
named `daily_gift`, which is misleading — a full read of `recharge_logs` across a
dozen accounts shows exactly one entry per account, dated the day it was created,
with no subsequent top-ups.

The grant expires 24 h after it is issued, so an account's usable budget is roughly
**100 requests for its entire lifetime**. There is no replenishment mechanism; a
depleted account stays depleted. Scale comes from the number of accounts, and the
per-account rate limit binds earlier than the credit budget.

---

## Registration toolkit (optional)

`signup_e2e.py` automates the whole signup pipeline, including the image CAPTCHA:

```bash
export TWOCAPTCHA_KEY=<your-2captcha-key>
export UM_DIR=<dir containing your mail CLI>        # optional, for code retrieval
python signup_e2e.py --email you@example.com --seq 1
```

It will: configure and launch the browser driver, navigate to the form, fill the email,
solve the image CAPTCHA, poll for the email verification code, submit it, fill the password
twice, create the account, export cookies, and append the account to `accounts.json`.

**With `TWOCAPTCHA_KEY` set, no human interaction is required.** Measured end to end:
**~96–116 s per account, zero human steps.**

Headless is supported and slightly faster (`GS_HEADLESS=1`); the default is headed so
the window stays visible and a run can be watched:

```bash
GS_HEADLESS=1 python signup_e2e.py --email you@example.com --seq 1
```

### Email domain matters

Signup **validates the email domain only at the final `Create` step** — a code that
arrives in the inbox does not mean the domain is acceptable. A rejected domain fails
with `Email domain not allowed` and the page never leaves the OAuth hop.

| Domain | Result |
|---|---|
| `@outlook.com` | works (real mailbox, unlimited `+` aliases) |
| `.com`, `.dev` | works |
| `.xyz`, disposable-mailbox TLDs | **rejected** (`Email domain not allowed`) |

Without a solver key the driver still exposes a manual path: capture the image with
`capimg`, write the answer to a file, and continue.

### Automatic CAPTCHA solving

`two_captcha.py` submits the CAPTCHA to 2captcha and returns the answer. Measured on the
live signup flow (2026-09-23):

| Metric | Value |
|---|---|
| Success rate | 2/2 signups passed on the first image |
| Solve time | 6–20 s |
| Cost | **$0.001 per solve** (measured: two single-solve balance deltas, billed with a ~30 s lag) |
| Stability | 3/3 identical answers for the same image |

**One implementation detail matters a lot:** read the image from `img.src` (a
`data:image/jpeg;base64,...` URL). Do **not** screenshot the element by coordinates — the
screenshot includes the surrounding background, and the solver then misreads the distorted
glyphs. This single difference was the gap between a wrong answer and a passing one.

The driver exposes an `autocap` command that does the whole loop: read image → solve →
fill → submit → verify, retrying with a fresh image on failure.

### Notes

- **Without a solver key, the image CAPTCHA needs a human.** Vision models refuse the
  request outright, and when asked neutrally they misread the distorted glyphs (they
  confuse strokes with characters).
- **Email verification code retrieval:** if you use an MCP-based mail tool, beware that it
  may return a **cached** code. Poll the mailbox directly to get the newest one; a stale
  code produces `We are having trouble verifying your email address`.
- **Order matters:** email → CAPTCHA → *Send verification code* → verification code →
  *Verify code* → password ×2 → *Create*. The password fields stay `disabled` until the
  verification step succeeds; filling them earlier times out rather than failing loudly.

---

## Session lifetime

| Cookie | Role | Lifetime |
|---|---|---|
| `session_id` | **session identity** | ~20 days |
| auth tokens | request signing | ~24 h, auto-renewed |
| bot-management cookie | anti-bot | ~30 min, auto-renewed per request |

**Re-login is fully automatic.** The login form has **no image CAPTCHA** (unlike signup),
so a plain email + password login works headlessly — re-run `gs_login.py` when
`session_id` expires.

---

## Architecture notes

### Required headers

The upstream web endpoint rejects requests that are missing a `User-Agent` with a
`400` whose body reads `bad request cf` — which looks like an edge/CDN block but is
actually an application-layer check. A browser-like `User-Agent` is mandatory.

### Authentication

A single `session_id` cookie is sufficient. Sending the full cookie jar also works; sending
only the auxiliary auth cookies does not.

### Egress isolation

Per-account `proxy` values are supported and recommended. Accounts sharing one egress IP
are more likely to be rate-limited or restricted together. The bridge attaches a separate
HTTP session per account, so each account can use its own egress.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the gateway integration pattern and
the per-account egress isolation design.

---

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — gateway integration (config shape,
  alias/priority semantics, verification ladder) and per-account egress isolation design

---

## Project layout

```
genspark2api.py          # the proxy (multi-account round-robin, streaming)
gs_login.py              # one-time login + cookie export
signup_e2e.py            # end-to-end signup: register -> solve CAPTCHA -> export -> pool
gs_reg_driver.py         # browser driver used by signup_e2e.py
two_captcha.py           # automatic CAPTCHA solving (optional)
gs_export.py             # cookie export from a browser profile
accounts.example.json    # account-pool template (copy to accounts.json)
docs/ARCHITECTURE.md     # gateway integration + egress isolation design
DISCLAIMER.md            # full terms — read this
LICENSE                  # MIT
```

---

## Disclaimer

This project is unofficial and unaffiliated with the upstream service. It automates a
browser session **you control**, using credentials **you exported**, and it does **not**
bypass authentication or grant access to any account but your own. You are responsible for
complying with the upstream Terms of Service, and your account may be rate-limited or
suspended at your own risk.

See [DISCLAIMER.md](DISCLAIMER.md) for the full terms.

---

## License

MIT — see [LICENSE](LICENSE).
