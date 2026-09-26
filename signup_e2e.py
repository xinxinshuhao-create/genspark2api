"""End-to-end signup automation: register an account with no human interaction.

Pipeline:
  1. configure the driver and launch it (headful browser)
  2. navigate to the signup form and fill the email
  3. `autocap` -> solve the image CAPTCHA via 2captcha, submit, verify
  4. poll the mailbox for the email verification code, submit it
  5. fill the password twice, create the account
  6. export cookies and append the account to the pool file

Measured end to end: ~116 s per account, zero human steps.

Configuration (environment):
  GS_EMAIL        required   the address to register
  GS_SEQ          required   account index (drives profile/log/cookie paths)
  TWOCAPTCHA_KEY  required   solver key; enables automatic CAPTCHA
  TWOCAPTCHA_PROXY optional  proxy for solver API calls
  GS_PROXY        optional   egress proxy for the browser
  GS_BASE_DIR     optional   working directory (default: this file's directory)
  GS_PYTHON       optional   interpreter used for child processes
  UM_DIR          optional   directory holding a `um.py` mail CLI, used to
                             poll for the verification code

Mail polling note: some MCP-based mail tools return a CACHED code, which
produces `We are having trouble verifying your email address`. Always poll the
mailbox directly for the newest message. Point UM_DIR at a CLI that does that.

Usage:
  python signup_e2e.py --email you@example.com --seq 1
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

BASE = os.environ.get("GS_BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
DRIVER = os.path.join(BASE, "gs_reg_driver.py")
CMDFILE = os.path.join(BASE, "gs_cmd.txt")
UM_DIR = os.environ.get("UM_DIR", "")
PY = os.environ.get("GS_PYTHON", sys.executable)

ap = argparse.ArgumentParser()
ap.add_argument("--email", required=True)
ap.add_argument("--seq", default="1")
args = ap.parse_args()
SEQ, EMAIL = args.seq, args.email

LOG = os.path.join(BASE, f"gs_reg_{SEQ}.log")
PROFILE = os.path.join(BASE, f"profile_{SEQ}")
OUT = os.path.join(BASE, f"out_{SEQ}")
COOKIE_FILE = os.path.join(BASE, f"cookies_{SEQ}.json")
POOL_FILE = os.path.join(BASE, "accounts.json")


def log(m):
    print(f"[e2e] {m}", flush=True)


def cmd(text):
    with open(CMDFILE, "w", encoding="utf-8") as f:
        f.write(text.rstrip("\n") + "\n")


def tail(n=1):
    if not os.path.exists(LOG):
        return ""
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        return "".join(f.readlines()[-n:])


def wait_log(pat, timeout=180, label=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if re.search(pat, tail(400)):
            log(f"  OK  {label or pat}  ({int(time.time()-t0)}s)")
            return True
        time.sleep(2)
    log(f"  TIMEOUT {label or pat} ({timeout}s)")
    return False


def fetch_code(timeout=300, interval=8):
    """Poll the mailbox for a 6-digit verification code."""
    if not UM_DIR:
        log("  UM_DIR not set - cannot poll for the code")
        return None
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = subprocess.run(
            f'cd /d "{UM_DIR}" && python -X utf8 um.py code '
            f'--address "{EMAIL}" --timeout 20 --raw',
            shell=True, capture_output=True, text=True, timeout=90,
            encoding="utf-8", errors="replace")
        m = re.search(r'"value":\s*"(\d{6})"', (r.stdout or "") + (r.stderr or ""))
        if m:
            log(f"  OK  email code {m.group(1)}  ({int(time.time()-t0)}s)")
            return m.group(1)
        time.sleep(interval)
    return None


# ---------------------------------------------------------------- 1. driver
# The driver reads GS_PROFILE / GS_OUT / GS_LOG / GS_EMAIL from the
# environment itself, so nothing needs rewriting on disk.
if os.path.exists(CMDFILE):
    os.remove(CMDFILE)

# DETACHED_PROCESS so the browser survives this script's exit
DETACHED = 0x00000008 | 0x00000200
env = {**os.environ, "https_proxy": "", "http_proxy": "",
       "GS_EMAIL": EMAIL,
       "GS_PROFILE": PROFILE, "GS_LOG": LOG, "GS_OUT": OUT}
log(f"driver configured: seq={SEQ} email={EMAIL}")
log("launching driver (a browser window will open)...")
subprocess.Popen([PY, "gs_reg_driver.py", "open"], cwd=BASE,
                 creationflags=DETACHED,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                 stdin=subprocess.DEVNULL, env=env)
time.sleep(5)

if not wait_log(r"fill:#email\] ok", timeout=240, label="email filled"):
    log("did not reach the signup form")
    sys.exit(1)

# ---------------------------------------------------------------- 2. captcha
log("running autocap (2captcha solves the image CAPTCHA)")
cmd("autocap")
if not wait_log(r"\[autocap\] PASSED", timeout=600, label="CAPTCHA passed"):
    log("autocap failed")
    print(tail(30))
    sys.exit(1)

# ------------------------------------------------------------ 3. email code
log("polling for the email verification code...")
code = fetch_code(timeout=300)
if not code:
    log("no email code received")
    sys.exit(1)

log(f"submitting email code {code}")
cmd(f"type=#emailVerificationCode|{code}")
time.sleep(20)
log("clicking Verify code")
cmd("click=Verify code")
time.sleep(28)

# -------------------------------------------------------- 4. password/create
log("filling the password twice")
cmd("password")
wait_log(r"fill:#reenterPassword\] ok", timeout=180, label="password filled")
time.sleep(3)
log("clicking Create")
cmd("create")
# Anchor on the home page itself. A loose pattern also matches the OAuth hop
# URL (/api/auth?code=...) which the browser passes through before landing,
# so a mid-hop match would be read as success and the caller would quit before
# the session cookies are written. Note the trailing \s rather than $: these
# patterns run against a multi-line log tail without re.MULTILINE, where $
# only matches the very end of the whole string.
ok = wait_log(r"state:after_create\] url=https://www\.genspark\.ai/\s",
              timeout=120, label="account created")

log("=" * 70)
log(f"result: {'ACCOUNT CREATED' if ok else 'no success redirect detected'}")
log("=" * 70)
if not ok:
    print(tail(25))
    sys.exit(1)

# Landing on the home page means the form was accepted, but the browser still
# needs a moment to persist the session cookies. Exporting immediately can
# yield an unauthenticated cookie jar (no c1/c2, 401 on first use).
log("waiting for the session to settle before exporting...")
time.sleep(25)

# ------------------------------------------- 5. export cookies + add to pool
log("stopping the driver to release the profile lock...")
cmd("quit")
time.sleep(12)
if os.path.exists(CMDFILE):
    os.remove(CMDFILE)

log("exporting cookies...")
env2 = {k: v for k, v in os.environ.items()
        if k.lower() not in ("https_proxy", "http_proxy", "all_proxy",
                             "HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY")}
try:
    pr = subprocess.run([PY, "gs_export.py", "--profile", PROFILE,
                         "--account", str(SEQ), "--email", EMAIL,
                         "--out", COOKIE_FILE],
                        cwd=BASE, env=env2, capture_output=True, text=True,
                        timeout=300, encoding="utf-8", errors="replace")
    out = (pr.stdout or "") + (pr.stderr or "")
except Exception as e:
    out = f"EXC {type(e).__name__}: {e}"
for line in out.strip().split("\n")[-6:]:
    log(f"  {line}")

# cogen_id, for the pool entry
cogen = None
if os.path.exists(COOKIE_FILE):
    try:
        cogen = json.load(open(COOKIE_FILE, encoding="utf-8")).get("cogen_id")
    except Exception:
        pass

# password, for the pool entry
pwd = None
if os.path.exists(LOG):
    m = re.search(r"\[password\]\s+(\S+)",
                  open(LOG, encoding="utf-8", errors="replace").read())
    if m:
        pwd = m.group(1)

if cogen:
    seq_i = int(SEQ) if str(SEQ).isdigit() else 0
    pool = {"accounts": []}
    if os.path.exists(POOL_FILE):
        try:
            pool = json.load(open(POOL_FILE, encoding="utf-8"))
        except Exception:
            pass
    pool["accounts"] = [a for a in pool.get("accounts", []) if a.get("seq") != seq_i]
    pool["accounts"].append({
        "seq": seq_i, "email": EMAIL, "password": pwd, "cogen_id": cogen,
        "cookie_file": COOKIE_FILE, "proxy": os.environ.get("GS_PROXY", ""),
        "status": "active",
        "note": f"{time.strftime('%Y-%m-%d')} signup (automatic)",
    })
    pool["accounts"].sort(key=lambda a: a.get("seq", 0))
    json.dump(pool, open(POOL_FILE, "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    log(f"added to the pool ({len(pool['accounts'])} accounts)")
else:
    log(f"no cogen_id - pool not updated (cookie file: {COOKIE_FILE})")

log("done. restart the bridge to pick up the new account.")
