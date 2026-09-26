"""Genspark 注册 - 有头逐步驱动 v4（frame 感知 + 浏览器常驻不关）
关键修复: B2C 内容在 iframe 里 -> 所有查找/点击/填表遍历 page.frames
命令: state | frames | capimg | caprefresh | autocap | captcha=<文本> | password | sendcode
      | code=<码> | create | extract | click=<文本> | type=<sel>|<文本> | quit
"""
import sys, os, time, json, re, traceback, random, secrets, string
import cloakbrowser

URL     = "https://www.genspark.ai/"
PROFILE = os.environ.get("GS_PROFILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile"))
CMDFILE = os.environ.get("GS_CMDFILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "gs_cmd.txt"))
OUT     = os.environ.get("GS_OUT", os.path.join(os.path.dirname(os.path.abspath(__file__)), "out"))
LOG     = os.environ.get("GS_LOG", os.path.join(os.path.dirname(os.path.abspath(__file__)), "gs_reg.log"))
EMAIL   = os.environ.get("GS_EMAIL", "")
IP_PROXY = os.environ.get("GS_PROXY", "") or None   # 出口代理，留空=直连
# Headless mode (GS_HEADLESS=1). Default is headed so the window stays visible
# and the run can be watched; headless is ~15% faster and needs no display.
HEADLESS = os.environ.get("GS_HEADLESS", "0") == "1"

os.makedirs(OUT, exist_ok=True)


def gen_password():
    core = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(14))
    return "Gs!" + core + "9z"


PASSWORD = gen_password()


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


BROWSER = None


def P():
    """当前活动页：优先最新打开的、非 about:blank 的标签页"""
    global BROWSER
    try:
        pages = [p for p in BROWSER.pages if not p.is_closed()]
    except Exception:
        return None
    for p in reversed(pages):
        try:
            if p.url and p.url != "about:blank":
                return p
        except Exception:
            continue
    return pages[-1] if pages else None


def page_count():
    try:
        return len([p for p in BROWSER.pages if not p.is_closed()])
    except Exception:
        return -1


# ---------- frame 感知的查找 ----------

FIND_JS = """
(args) => {
  const rx = new RegExp(args.pat, args.flags);
  const els = [...document.querySelectorAll('button,a,[role=button],div,span,li,p,input[type=submit]')];
  let best = null;
  for (const el of els) {
    const t = (el.innerText || el.value || '').trim();
    if (!t || t.length > 80) continue;
    if (!rx.test(t)) continue;
    const st = getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none' || parseFloat(st.opacity) < 0.05) continue;
    let r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    // 关键：先滚进视口再测量，否则拿到视口外的坐标（实测 y=14961 点不到）
    if (r.top < 0 || r.bottom > innerHeight || r.left < 0 || r.right > innerWidth) {
      try { el.scrollIntoView({block: 'center', inline: 'center'}); } catch (e) {}
      r = el.getBoundingClientRect();
    }
    const inView = r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth;
    const area = r.width * r.height;
    const cand = {x: r.x + r.width/2, y: r.y + r.height/2, w: r.width, h: r.height, area,
                  text: t, tag: el.tagName.toLowerCase(),
                  cls: (el.className||'').toString().slice(0,60),
                  inView,
                  disabled: !!(el.disabled || el.getAttribute('aria-disabled')==='true')};
    // 优先取可见的；都不可见时保留面积最小的
    if (!best) { best = cand; continue; }
    if (cand.inView && !best.inView) { best = cand; continue; }
    if (cand.inView === best.inView && cand.area < best.area) best = cand;
  }
  return best;
}
"""

JS_CLICK = """
(args) => {
  const rx = new RegExp(args.pat, args.flags);
  const els = [...document.querySelectorAll('button,a,[role=button],div,span,li,p,input[type=submit]')];
  let best = null;
  for (const el of els) {
    const t = (el.innerText || el.value || '').trim();
    if (!t || t.length > 80) continue;
    if (!rx.test(t)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) continue;
    const area = r.width * r.height;
    if (!best || area < best.area) best = {el, area};
  }
  if (!best) return null;
  try { best.el.scrollIntoView({block:'center'}); } catch(e) {}
  best.el.click();
  return {text: (best.el.innerText||'').trim().slice(0,40), tag: best.el.tagName.toLowerCase()};
}
"""


def all_frames(page):
    try:
        return page.frames
    except Exception:
        return [page]


def find_in_frames(page, pattern, flags="i", timeout=8):
    """遍历所有 frame 找元素，返回 (frame, box)"""
    end = time.time() + timeout
    while time.time() < end:
        for fr in all_frames(page):
            try:
                box = fr.evaluate(FIND_JS, {"pat": pattern, "flags": flags})
            except Exception:
                continue
            if box:
                return fr, box
        time.sleep(0.5)
    return None, None


def human_click(page, box):
    x, y = box["x"], box["y"]
    try:
        page.mouse.move(x - random.uniform(6, 14), y - random.uniform(5, 12), steps=8)
        time.sleep(random.uniform(0.10, 0.22))
        page.mouse.move(x + random.uniform(-1.5, 1.5), y + random.uniform(-1.5, 1.5), steps=4)
        time.sleep(random.uniform(0.06, 0.16))
        page.mouse.down()
        time.sleep(random.uniform(0.05, 0.12))
        page.mouse.up()
        return True
    except Exception:
        log("[click] mouse EXC\n" + traceback.format_exc())
        return False


def click_text(page, pattern, timeout=10, label=None, exact=False):
    flags = "" if exact else "i"
    fr, box = find_in_frames(page, pattern, flags=flags, timeout=timeout)
    if fr is not None and box:
        fname = "main" if fr == page.main_frame else (fr.name or fr.url[:40])
        log(f"[click] {label or pattern} @frame[{fname}] -> {json.dumps(box, ensure_ascii=False)}")
        # 视口外或鼠标点击失败 -> 直接 JS click()
        if not box.get("inView", True):
            log(f"[click] {label or pattern} 不在视口内，改用 JS click()")
        elif human_click(page, box):
            time.sleep(0.6)
            return True
    # JS 兜底：在能命中的 frame 里直接 click()
    for f in all_frames(page):
        try:
            r = f.evaluate(JS_CLICK, {"pat": pattern, "flags": flags})
        except Exception:
            continue
        if r:
            log(f"[click] {label or pattern} @frame JS-fallback -> {json.dumps(r, ensure_ascii=False)}")
            return True
    log(f"[click] NOT FOUND pattern={pattern} (searched {len(all_frames(page))} frames)")
    return False


def wait_until(page, pred, timeout=30, poll=1.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            if pred(page):
                return True
        except Exception:
            pass
        time.sleep(poll)
    return False


# ---------- 表单操作（frame 感知） ----------

def has_signup_form(page):
    for fr in all_frames(page):
        try:
            if fr.query_selector("#captchaControlChallengeCode"):
                return True
        except Exception:
            continue
    return False


def field(page, sel):
    """在所有 frame 里找元素，返回 (frame, handle)
    query_selector 失败时用 JS 兜底（中文属性值 query_selector 匹配不到）
    """
    for fr in all_frames(page):
        try:
            el = fr.query_selector(sel)
            if el:
                return fr, el
        except Exception:
            continue
    # JS 兜底：用 document.querySelector + element handle
    for fr in all_frames(page):
        try:
            handle = fr.evaluate_handle(
                """(sel) => {
                    try { return document.querySelector(sel); } catch(e) { return null; }
                }""", sel)
            el = handle.as_element()
            if el:
                return fr, el
        except Exception:
            continue
    return None, None


def fill_by_id(page, sel, text, tries=3, wait_enabled=0):
    """重查句柄 + 回读校验 + 重试；frame 感知
    wait_enabled>0 时先等元素 enabled（B2C 密码框在验证码通过前是 disabled）
    """
    last = ""
    # 先等元素可用（避免 fill 白等 30s 超时）
    if wait_enabled > 0:
        deadline = time.time() + wait_enabled
        while time.time() < deadline:
            fr, el = field(page, sel)
            if el:
                try:
                    if el.is_enabled():
                        break
                except Exception:
                    pass
            time.sleep(1.0)
        else:
            log(f"[fill:{sel}] 等待 enabled 超时（{wait_enabled}s）")
    for attempt in range(1, tries + 1):
        fr, el = field(page, sel)
        if not el:
            log(f"[fill:{sel}] not found try{attempt}")
            time.sleep(0.8); continue
        try:
            try:
                if not el.is_enabled():
                    log(f"[fill:{sel}] 仍 disabled try{attempt}，等 3s")
                    time.sleep(3.0)
                    fr, el = field(page, sel)
                    if not el:
                        continue
            except Exception:
                pass
            b = el.bounding_box()
            if b:
                human_click(page, {"x": b["x"] + b["width"] / 2, "y": b["y"] + b["height"] / 2})
            fr, el = field(page, sel)
            if not el:
                time.sleep(0.6); continue
            el.fill(""); time.sleep(0.2)
            el.type(text, delay=70)
            time.sleep(0.5)
            fr2, cur = field(page, sel)
            last = (cur.input_value() if cur else "") or ""
            if last.strip() == text.strip():
                log(f"[fill:{sel}] ok try{attempt}")
                return True
            log(f"[fill:{sel}] mismatch try{attempt}: {last!r}")
        except Exception as e:
            log(f"[fill:{sel}] exc try{attempt}: {type(e).__name__}: {str(e)[:120]}")
        time.sleep(1.0)
    log(f"[fill:{sel}] FAILED last={last!r}")
    return False


CAPBOX_JS = """
() => {
  const inp = document.querySelector('#captchaControlChallengeCode');
  if (!inp) return null;
  const scope = inp.closest('form') || inp.closest('div') || document.body;
  let imgs = [...scope.querySelectorAll('img')].filter(i => {
    const r = i.getBoundingClientRect();
    return r.width > 40 && r.height > 20;
  });
  if (!imgs.length) {
    imgs = [...document.querySelectorAll('img')].filter(i => {
      const r = i.getBoundingClientRect();
      return r.width > 40 && r.height > 20 && r.width < 600;
    });
  }
  if (!imgs.length) return null;
  const i = imgs[0];
  const r = i.getBoundingClientRect();
  return {x: r.x, y: r.y, width: r.width, height: r.height,
          src: (i.src||'').slice(0,150), alt: i.alt||''};
}
"""


def dump(page, tag):
    frames_info = []
    for fr in all_frames(page):
        try:
            frames_info.append({"name": fr.name or "", "url": fr.url[:90],
                                "has_captcha": bool(fr.query_selector("#captchaControlChallengeCode"))})
        except Exception:
            pass
    inputs = []
    for fr in all_frames(page):
        try:
            for el in fr.query_selector_all("input,textarea,select"):
                try:
                    if el.is_visible():
                        inputs.append({"frame": fr.name or "main",
                                       "type": el.get_attribute("type"),
                                       "id": el.get_attribute("id"),
                                       "placeholder": el.get_attribute("placeholder"),
                                       "disabled": el.is_disabled(),
                                       "value": el.input_value()})
                except Exception:
                    pass
        except Exception:
            pass
    leafs = []
    for fr in all_frames(page):
        try:
            ls = fr.evaluate("""() => {
                const out = [];
                for (const el of document.querySelectorAll('button,a,[role=button],div,span,input[type=submit]')) {
                    const t = (el.innerText||el.value||'').trim();
                    if (!t || t.length > 40) continue;
                    const r = el.getBoundingClientRect();
                    if (r.width < 2 || r.height < 2) continue;
                    if (el.children.length > 0) continue;
                    out.push(t + (el.disabled ? ' [disabled]' : ''));
                }
                return [...new Set(out)].slice(0, 30);
            }""")
            if ls:
                leafs.append({"frame": fr.name or "main", "items": ls})
        except Exception:
            pass
    st = {"tag": tag, "url": page.url, "title": page.title(),
          "frames": frames_info, "inputs": inputs, "leafs": leafs,
          "turnstile": bool(page.query_selector('iframe[src*="challenges.cloudflare.com"]'))}
    json.dump(st, open(os.path.join(OUT, f"state_{tag}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    try:
        page.screenshot(path=os.path.join(OUT, f"state_{tag}.png"))
    except Exception:
        pass
    log(f"[state:{tag}] url={page.url}")
    log(f"[state:{tag}] frames={json.dumps(frames_info, ensure_ascii=False)}")
    log(f"[state:{tag}] inputs={json.dumps(inputs, ensure_ascii=False)}")
    for l in leafs:
        log(f"[state:{tag}] leafs@{l['frame']}={json.dumps(l['items'], ensure_ascii=False)}")
    return st


MAX_AUTOCAP_TRIES = 5
AUTOCAP_STATE_JS = """() => {
  const g = id => { const e = document.getElementById(id);
    return e ? (e.innerText || e.textContent || '').trim().slice(0,220) : null; };
  const codeEl = document.getElementById('emailVerificationCode');
  return JSON.stringify({
    err: g('emailVerificationControl_error_message'),
    ok: g('emailVerificationControl_success_message'),
    pageErr: g('pageLevelErrors'),
    codeVisible: codeEl ? codeEl.getBoundingClientRect().height > 0 : null,
    url: location.href.slice(0,120)
  });
}"""


def act_autocap(page):
    """Fully automatic CAPTCHA solve: read image -> 2captcha -> fill -> submit -> verify.
    Retries with a fresh image on failure. Returns True on success.
    Requires TWOCAPTCHA_KEY in the environment.
    """
    try:
        import two_captcha
    except ImportError:
        log("[autocap] two_captcha.py not found")
        return False

    if not two_captcha.API_KEY:
        log("[autocap] TWOCAPTCHA_KEY is not set")
        return False

    log(f"[autocap] 2captcha balance: {two_captcha.balance()}")

    for attempt in range(1, MAX_AUTOCAP_TRIES + 1):
        log(f"[autocap] --- attempt {attempt}/{MAX_AUTOCAP_TRIES} ---")
        if attempt > 1:
            act_caprefresh(page)
            time.sleep(3)

        ans, cid, info = two_captcha.solve_from_page(page, save_dir=OUT, log=log)
        if not ans:
            log(f"[autocap] solve failed: {info}")
            continue
        log(f"[autocap] answer={ans!r}  took={info:.0f}s  id={cid}")

        if not fill_by_id(page, "#captchaControlChallengeCode", ans):
            log("[autocap] could not fill the field")
            continue
        time.sleep(0.8)

        click_text(page, r"Send verification code|发送验证码", timeout=8, label="sendcode")
        time.sleep(6)

        dump(page, f"autocap_{attempt}")
        ok = False

        # Success marker: the server returns
        # "Verification code has been sent to your inbox..."
        try:
            j = json.loads(page.evaluate(AUTOCAP_STATE_JS))
            ok = bool(j.get("ok")) or bool(j.get("codeVisible"))
            err = j.get("err") or j.get("pageErr") or ""
            log(f"[autocap] server: ok={j.get('ok')!r} err={err!r} "
                f"codeVisible={j.get('codeVisible')}")
        except Exception as e:
            log(f"[autocap] state read failed: {type(e).__name__}")

        if ok:
            log(f"[autocap] PASSED with answer={ans!r}")
            two_captcha.report(cid, True)
            return True
        log(f"[autocap] rejected (answer={ans!r})")
        two_captcha.report(cid, False)

    log(f"[autocap] all {MAX_AUTOCAP_TRIES} attempts failed")
    return False


def act_capimg(page):
    box = None
    for fr in all_frames(page):
        try:
            box = fr.evaluate(CAPBOX_JS)
        except Exception:
            continue
        if box:
            break
    if not box:
        log("[capimg] 未找到验证码图片")
        return
    path = os.path.join(OUT, "captcha.png")
    try:
        page.screenshot(path=path, clip={"x": box["x"], "y": box["y"],
                                        "width": box["width"], "height": box["height"]})
        log(f"[capimg] saved {path} box={json.dumps({k: box[k] for k in ('x','y','width','height')})} src={box['src']}")
    except Exception:
        log("[capimg] screenshot EXC\n" + traceback.format_exc())


def act_caprefresh(page):
    for fr in all_frames(page):
        try:
            b = fr.evaluate("""() => {
              for (const a of document.querySelectorAll('a,button,[role=button],img')) {
                const t = ((a.innerText||'')+' '+(a.getAttribute('aria-label')||'')+' '+(a.alt||'')).toLowerCase();
                if (/refresh|reload|new code|重新|刷新|换一张/.test(t)) {
                  const r = a.getBoundingClientRect();
                  if (r.width > 2) return {x: r.x+r.width/2, y: r.y+r.height/2, t: t.trim().slice(0,30)};
                }
              }
              return null;
            }""")
        except Exception:
            continue
        if b:
            log(f"[caprefresh] {json.dumps(b, ensure_ascii=False)}")
            human_click(page, b); time.sleep(2.5)
            act_capimg(page)
            return
    log("[caprefresh] 未找到刷新控件")


HANDLERS = {
    "state":      lambda p, a: dump(p, "manual"),
    "frames":     lambda p, a: dump(p, "frames"),
    "goto":       lambda p, a: (p.goto(a, wait_until="domcontentloaded", timeout=60000),
                                time.sleep(6), dump(p, "goto_" + re.sub(r"\W+", "_", a)[-30:])),
    "api":        lambda p, a: act_api(p, a),
    "capimg":     lambda p, a: act_capimg(p),
    "autocap":    lambda p, a: act_autocap(p),
    "caprefresh": lambda p, a: act_caprefresh(p),
    "captcha":    lambda p, a: (fill_by_id(p, "#captchaControlChallengeCode", a), dump(p, "captcha_filled")),
    "email":      lambda p, a: (fill_by_id(p, "#email", EMAIL), dump(p, "email_filled")),
    "password":   lambda p, a: (fill_by_id(p, "#newPassword", PASSWORD, wait_enabled=90),
                                fill_by_id(p, "#reenterPassword", PASSWORD, wait_enabled=30),
                                dump(p, "password_filled")),
    "sendcode":   lambda p, a: (click_text(p, r"Send verification code|发送验证码", timeout=8, label="sendcode"),
                                time.sleep(7), dump(p, "after_sendcode")),
    "create":     lambda p, a: (click_text(p, r"^Create$|^创建$", timeout=8, label="create", exact=True),
                                # Create redirects through /api/auth?code=... (OAuth hop)
                                # before landing on the home page. A fixed sleep(10) can
                                # dump mid-hop, so callers see a non-home URL and quit too
                                # early -- the session cookies (c1/c2) then never land and
                                # the exported cookie is unauthenticated. Poll until the
                                # real home page is reached (max 60s), then dump.
                                wait_until(p, lambda pg: re.match(
                                    r"https://www\.genspark\.ai/(\?|$)", pg.url or ""),
                                    timeout=60, poll=1.0),
                                time.sleep(3), dump(p, "after_create")),
    "extract":    lambda p, a: act_extract(p),
    "type":       lambda p, a: (page_type_any(p, a), dump(p, "manual_type")),
}


def act_extract(page):
    allc = {c["name"]: c["value"] for c in page.context.cookies()}
    json.dump({k: (v[:80] + "..." if len(v) > 80 else v) for k, v in allc.items()},
              open(os.path.join(OUT, "cookies_now.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    log(f"[extract] url={page.url} cookies={sorted(allc)}")


def act_api(page, arg):
    """在浏览器内 fetch，带 session cookie 绕过 CF。
    格式: api<GET|POST>|路径[|json body]
    """
    parts = arg.split("|")
    method = (parts[0] or "GET").strip().upper()
    path = parts[1].strip() if len(parts) > 1 else "/"
    body = parts[2].strip() if len(parts) > 2 else ""
    try:
        res = page.evaluate("""async (a) => {
            const opt = {method: a.method, headers: {'Accept': 'application/json'}, credentials: 'include'};
            if (a.body) { opt.headers['Content-Type'] = 'application/json'; opt.body = a.body; }
            try {
                const r = await fetch(a.path, opt);
                const t = await r.text();
                return {status: r.status, len: t.length, body: t.slice(0, 4000)};
            } catch (e) { return {error: String(e)}; }
        }""", {"method": method, "path": path, "body": body})
        log(f"[api] {method} {path} -> {json.dumps(res, ensure_ascii=False)[:2500]}")
        fn = "api_" + re.sub(r"\W+", "_", path)[:40] + ".json"
        json.dump(res, open(os.path.join(OUT, fn), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    except Exception:
        log("[api] EXC\n" + traceback.format_exc())


def page_type_any(page, arg):
    sel, _, text = arg.partition("|")
    sel, text = sel.strip(), text.strip()
    if not sel or not text:
        log("[type] 用法: type=<selector>|<text>"); return
    fill_by_id(page, sel, text)


def act_click(page, text):
    if not click_text(page, text, timeout=6, label=f"click:{text}", exact=True):
        click_text(page, re.escape(text), timeout=4, label=f"click~:{text}")
    time.sleep(4)
    dump(page, "click_" + re.sub(r"\W+", "_", text)[:20])


def act_open(page):
    """到注册表单 + 填邮箱。每步带重试，避免时序脆弱。"""
    r = page.goto(URL, wait_until="domcontentloaded", timeout=60000)
    log(f"[goto] status={r.status if r else '?'}")

    # 等页面真正可用（有「注册」或「登录」按钮）
    ok = wait_until(page, lambda p: find_in_frames(p, r"^注册$|^登录$", timeout=3)[1] is not None,
                    timeout=45)
    log(f"[open] 首页就绪={ok}")
    time.sleep(3)
    dump(page, "01_home")

    # 步骤 1：点「注册」—— 重试 3 次
    for attempt in range(1, 4):
        if click_text(page, r"^注册$", timeout=15, label="注册", exact=True):
            break
        log(f"[open] 点『注册』失败 try{attempt}，等 5s 重试")
        time.sleep(5)
    else:
        log("[open] ❌ 点『注册』3 次都失败")
        dump(page, "01_home_fail")
        return False

    ok = wait_until(page, lambda p: find_in_frames(p, r"更多选项|More options", timeout=2)[1] is not None,
                    timeout=30)
    log(f"[open] 出现『更多选项』={ok}")
    dump(page, "02_signup_modal")

    # 步骤 2：点「更多选项」
    for attempt in range(1, 4):
        if click_text(page, r"更多选项|More options", timeout=12, label="更多选项"):
            break
        log(f"[open] 点『更多选项』失败 try{attempt}")
        time.sleep(4)

    ok = wait_until(page, lambda p: find_in_frames(p, r"Sign up now|Login with email", timeout=2)[1] is not None,
                    timeout=35)
    log(f"[open] B2C 落地页={ok}")
    dump(page, "03_b2c_landing")

    # 步骤 3：点「Sign up now」到注册表单
    if not has_signup_form(page):
        for attempt in range(1, 4):
            if click_text(page, r"Sign up now|立即注册", timeout=12, label="signupnow"):
                if wait_until(page, has_signup_form, timeout=25):
                    break
            log(f"[open] 『Sign up now』未到表单 try{attempt}")
            time.sleep(4)
        dump(page, "04_signup_form")

    if not has_signup_form(page):
        log("[open] ❌ 未到注册表单")
        dump(page, "04_fail")
        return False

    log("[open] ✅ 已到注册表单")
    # 填邮箱（带重试）
    for attempt in range(1, 4):
        if fill_by_id(page, "#email", EMAIL):
            break
        log(f"[open] 填邮箱失败 try{attempt}")
        time.sleep(3)
    dump(page, "05_email_filled")
    act_capimg(page)
    log("[HOLD] 等你读验证码；然后发 captcha=<文本>")
    dump(page, "06_final")
    return True


def main():
    global BROWSER
    step = sys.argv[1] if len(sys.argv) > 1 else "open"
    if step == "quit":
        return
    log(f"===== START v4 step={step} =====")
    log(f"[password] {PASSWORD}")
    browser = cloakbrowser.launch_persistent_context(
        user_data_dir=PROFILE, headless=HEADLESS, stealth_args=True,
        proxy=IP_PROXY,   # 每号独立 IP（IP 池槽位），None=不走代理
        viewport={"width": 1440, "height": 900})
    BROWSER = browser
    page = browser.pages[0] if browser.pages else browser.new_page()
    try:
        if step == "open":
            act_open(page)
    except Exception:
        log("[EXCEPTION]\n" + traceback.format_exc())

    if os.path.exists(CMDFILE):
        os.remove(CMDFILE)
    log(f"[HOLD] 浏览器常驻；命令写 {CMDFILE}")
    while True:
        time.sleep(2)
        if not os.path.exists(CMDFILE):
            continue
        try:
            cmds = [l.strip() for l in open(CMDFILE, encoding="utf-8").read().splitlines() if l.strip()]
            os.remove(CMDFILE)
        except Exception:
            continue
        for c in cmds:
            log(f"[cmd] {c}")
            try:
                if c == "quit":
                    browser.close(); log("[quit] closed"); return
                if c == "pages":
                    for i, p in enumerate(browser.pages):
                        try:
                            log(f"[pages] #{i} url={p.url[:110]}")
                        except Exception:
                            pass
                    continue
                pg = P()
                if pg is None:
                    log("[cmd] 无可用标签页"); continue
                if pg != page:
                    log(f"[cmd] 活动标签页切换 -> {pg.url[:90]}")
                    page = pg
                # 命令格式（避免 "=" 被 partition 破坏）：
                #   click<TAB>文本
                #   fill<TAB>选择器<TAB>文本
                if "\t" in c:
                    parts = c.split("\t")
                    op = parts[0].strip()
                    if op == "click" and len(parts) >= 2:
                        act_click(page, parts[1].strip()); continue
                    if op == "fill" and len(parts) >= 3:
                        fill_by_id(page, parts[1].strip(), parts[2].strip())
                        dump(page, "manual_fill"); continue
                    if op == "goto" and len(parts) >= 2:
                        page.goto(parts[1].strip(), wait_until="domcontentloaded", timeout=60000)
                        time.sleep(6); dump(page, "manual_goto"); continue
                    if op == "api" and len(parts) >= 2:
                        act_api(page, "\t".join(parts[1:])); continue
                    log(f"[cmd] 未知 tab 命令: {op}"); continue
                name, _, arg = c.partition("=")
                name = name.strip()
                if name == "click":
                    act_click(page, arg.strip()); continue
                h = HANDLERS.get(name)
                if not h:
                    log(f"[cmd] unknown: {name}"); continue
                h(page, arg.strip())
            except Exception:
                log(f"[cmd:{c}] EXC\n" + traceback.format_exc())


if __name__ == "__main__":
    main()
