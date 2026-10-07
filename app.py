"""
FindIt LITE - Lost & Found AI agent. NO pip install needed (Python standard library only).

  * AI chat with tool calling via Hugging Face (needs token.txt + internet)
  * Forms for reporting and claiming that work even when the AI/internet is down
  * Auto-matching of lost vs found items + secret-detail verification (3 attempts)

Run:  py app.py     then open http://127.0.0.1:7860
"""
import json
import os
import re
import sqlite3
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# ======================= EDIT THIS PART TO CUSTOMIZE =======================
APP_TITLE = "FindIt - Campus Lost & Found"
APP_NAME = "FindIt"
TAGLINE = "Report something you lost, or claim something that was found."
PORT = int(os.getenv("PORT", 7860))      # hosts like Render set PORT
HOSTED = bool(os.getenv("PORT"))            # True when running on a server
RATE_LIMIT = 30                             # AI requests per IP per hour (protects credits)
MODEL_ID = os.getenv("MODEL_ID", "Qwen/Qwen2.5-72B-Instruct")
MAX_ATTEMPTS = 3
THEME = "soft"   # choose: "soft", "blue", "red" or "green"
# ===========================================================================

THEMES = {
    "soft": "color-scheme:light;--bg:#fbf6f4;--card:#ffffff;--tx:#2b2326;--mut:#85777a;--pri:#b85c5c;"
            "--bd:#ecdcd8;--soft:#f6ebe8;--ok:#2f7d4f;--bad:#b3261e",
    "blue": "color-scheme:dark;--bg:#10131a;--card:#181c26;--tx:#e8eaf0;--mut:#9aa3b2;--pri:#4f46e5;"
            "--bd:#2a3040;--soft:#10131a;--ok:#7fd69a;--bad:#ff9a90",
    "red": "color-scheme:dark;--bg:#1a1416;--card:#241a1d;--tx:#f2e8e9;--mut:#a89a9d;--pri:#d96b6b;"
           "--bd:#3a2a2e;--soft:#2e2226;--ok:#7fd69a;--bad:#ff9a90",
    "green": "color-scheme:light;--bg:#f5f2eb;--card:#ffffff;--tx:#1f2a24;--mut:#6b7468;--pri:#1f6f4a;"
             "--bd:#ddd8cc;--soft:#eef3ee;--ok:#1f7a3a;--bad:#b3261e",
}

DB = os.getenv("DB_PATH", "lostfound_lite.db")
API_URL = "https://router.huggingface.co/v1/chat/completions"
STOP = set("a an the and or of in on at to is it my i was with for this that near from by "
           "have has had very lost found some one been left".split())


TOKEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "token.txt")


def load_token():
    """Find the hf_ token in HF_TOKEN or in token.txt next to app.py (any encoding, quotes ok)."""
    t = os.getenv("HF_TOKEN", "")
    if not t and os.path.exists(TOKEN_PATH):
        raw = open(TOKEN_PATH, "rb").read()
        for enc in ("utf-8-sig", "utf-16"):
            try:
                t = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    m = re.search(r"hf_[A-Za-z0-9]{20,}", (t or "").replace("\x00", ""))
    return m.group(0) if m else ""


TOKEN = load_token()

# ---- Hugging Face SDK (optional: falls back to plain urllib if not installed) ----
try:
    from huggingface_hub import InferenceClient
except ImportError:
    InferenceClient = None

_client = InferenceClient(api_key=TOKEN) if (InferenceClient and TOKEN) else None



# ------------------------------- Database --------------------------------
def q(sql, params=(), fetch=True):
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.execute(sql, params)
    rows = [dict(r) for r in cur.fetchall()] if fetch else None
    con.commit()
    con.close()
    return rows


def init_db():
    q("""CREATE TABLE IF NOT EXISTS items(
         id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, title TEXT, description TEXT,
         location TEXT, when_text TEXT, name TEXT, email TEXT, secret TEXT,
         status TEXT DEFAULT 'OPEN', attempts INTEGER DEFAULT 0)""", fetch=False)
    q("""CREATE TABLE IF NOT EXISTS links(
         lost_id INTEGER, found_id INTEGER, verdict TEXT, confidence REAL, reason TEXT,
         PRIMARY KEY(lost_id, found_id))""", fetch=False)
    if not q("SELECT 1 FROM items LIMIT 1"):
        seed = [
            ("FOUND", "Black laptop backpack", "Black Dell backpack with a laptop sleeve",
             "Library 2nd floor", "2026-10-05", "Library Desk", "library@campus.edu",
             "panda keychain on the zipper"),
            ("FOUND", "Blue steel water bottle", "Steel blue bottle with a dented bottom",
             "Canteen", "2026-10-06", "Canteen Staff", "canteen@campus.edu",
             "guitar sticker on the side"),
            ("LOST", "Silver wristwatch", "Casio silver watch with a metal strap",
             "Gym", "2026-10-04", "Rohan", "rohan@campus.edu", "none"),
            ("LOST", "Student ID card holder", "Brown leather card holder with ID inside",
             "Bus stop", "2026-10-06", "Meera", "meera@campus.edu", "none"),
        ]
        for s in seed:
            q("""INSERT INTO items(kind,title,description,location,when_text,name,email,secret)
                 VALUES (?,?,?,?,?,?,?,?)""", s, fetch=False)


# ------------------------------- Matching --------------------------------
def tokens(text):
    out = set()
    for t in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if t in STOP or len(t) < 2:
            continue
        if len(t) > 3 and t.endswith("s"):
            t = t[:-1]
        out.add(t)
    return out


SYN_GROUPS = [
    "bag backpack rucksack satchel knapsack schoolbag handbag",
    "watch wristwatch smartwatch",
    "bottle flask tumbler thermos",
    "phone mobile smartphone cellphone iphone",
    "earphone earphones earbud earbuds headphone headphones airpod airpods",
    "wallet purse",
    "holder cardholder",
    "umbrella brolly",
    "glasses spectacles eyeglasses sunglasses",
    "charger adapter adaptor",
    "key keys keychain keyring",
    "jacket coat hoodie sweater",
]
SYN = {w: g.split()[0] for g in SYN_GROUPS for w in g.split()}
COMPOUNDS = {"waterbottle": "water bottle", "powerbank": "power bank", "earbuds": "earbud"}
COLORS = set("black white grey blue red green yellow brown silver gold pink purple orange "
             "beige navy maroon".split())
KEYWORD_MIN = 0.3   # used when the AI is off or fails for a pair


def mtokens(text):
    """Tokens for MATCHING only (synonyms, compounds, gray=grey). Ownership checks keep
    using the strict tokens() above so a claimant cannot get in with a loose synonym."""
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text or "").lower()
    for k, v in COMPOUNDS.items():
        text = text.replace(k, v)
    out = set()
    for t in re.findall(r"[a-z0-9]+", text):
        if t in STOP or len(t) < 2:
            continue
        if t == "gray":
            t = "grey"
        if t not in SYN and len(t) > 3 and t.endswith("s"):
            t = t[:-1]
        out.add(SYN.get(t, t))
    return out


def item_tokens(it):
    return tokens(f"{it['title']} {it['title']} {it['description']} {it['location']}")


def similarity(a, b):
    """0..1 keyword score. Overlap (not Jaccard) so short complaints like 'bag, black'
    are not punished; colour clash is a penalty; same place is a small bonus."""
    ta, tb = mtokens(a["title"]), mtokens(b["title"])
    A, B = ta | mtokens(a["description"]), tb | mtokens(b["description"])
    ao, bo = A - COLORS, B - COLORS
    ac, bc = A & COLORS, B & COLORS
    if not ao or not bo:
        return 0.0
    s = 0.5 * len(ao & bo) / min(len(ao), len(bo))
    if (ta & tb) - COLORS:
        s += 0.3
    if ac and bc:
        s += 0.2 if ac & bc else -0.5
    if mtokens(a["location"]) & mtokens(b["location"]):
        s += 0.1
    return max(0.0, min(s, 1.0))


def describe(it, score=None, judge=None):
    s = (f"#{it['id']} [{it['kind']}] {it['title']} - {it['description']} "
         f"(place: {it['location']}, date: {it['when_text']})")
    if judge:
        s += (f"\n    AI says {judge['verdict'].upper()} match "
              f"({int(judge['confidence'] * 100)}%): {judge['reason']}")
    elif score is not None:
        s += " -> HIGH match" if score >= 0.7 else " -> POSSIBLE match"
    return s


_judge_cache = {}


def judge_match(lost, found):
    """AI job: read one LOST complaint and one FOUND item, decide if they are the same
    thing and say why. The hidden secret detail is NEVER sent to the AI."""
    prompt = (
        "You match lost-and-found reports. Decide if the LOST complaint and the FOUND item "
        "could be the same object.\n"
        "Rules:\n"
        "- Judge mainly on object type, colour, brand and marks. Synonyms are the same thing "
        "(bag = backpack, watch = wristwatch, bottle = flask).\n"
        "- A detail missing from one report is NOT a conflict. Short complaints are normal.\n"
        "- Place and date are WEAK clues: objects move and people misremember. "
        "Never answer no only because the place or date differs.\n"
        "- Answer no only for a clear conflict (different object type, or different colour/brand).\n"
        "- yes = very likely the same (confidence 0.8-1.0); maybe = could be the same "
        "(0.3-0.7); no = clearly different.\n"
        "Reply with ONLY JSON: "
        '{"verdict":"yes|maybe|no","confidence":0.0-1.0,"reason":"one short sentence"}\n\n'
        f"LOST complaint: {lost['title']} - {lost['description']} "
        f"(place: {lost['location']}, date: {lost['when_text']})\n"
        f"FOUND item: {found['title']} - {found['description']} "
        f"(place: {found['location']}, date: {found['when_text']})")
    if prompt in _judge_cache:
        return _judge_cache[prompt]
    for attempt in range(2):          # one retry: free tier sometimes rate-limits (429)
        try:
            text = ask_model(prompt)
            d = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
            verdict = str(d.get("verdict", "")).lower()
            if verdict not in ("yes", "maybe", "no"):
                raise ValueError("bad verdict: " + verdict)
            out = {"verdict": verdict,
                   "confidence": min(max(float(d.get("confidence", 0)), 0.0), 1.0),
                   "reason": str(d.get("reason", ""))[:200]}
            print(f"[match] lost #{lost['id']} vs found #{found['id']}: "
                  f"{out['verdict']} {out['confidence']:.2f} - {out['reason']}")
            _judge_cache[prompt] = out
            return out
        except Exception as e:
            print(f"[match] lost #{lost['id']} vs found #{found['id']} AI error "
                  f"(try {attempt + 1}): {str(e)[:120]}")
            time.sleep(1.5)
    return None


def matches_for(item, limit=3):
    other = "FOUND" if item["kind"] == "LOST" else "LOST"
    cands = q("SELECT * FROM items WHERE kind=? AND status='OPEN' AND id<>?", (other, item["id"]))
    scored = sorted(((similarity(item, c), c) for c in cands), key=lambda x: -x[0])
    if not (TOKEN and scored):
        return [(s, c, None) for s, c in scored if s >= KEYWORD_MIN][:limit]
    top = scored[:6]    # keyword score only shortlists; the AI makes the call

    def one(sc):
        c = sc[1]
        lost, found = (item, c) if item["kind"] == "LOST" else (c, item)
        return judge_match(lost, found)
    with ThreadPoolExecutor(max_workers=3) as ex:
        judged = list(ex.map(one, top))
    out = []
    for (s, c), j in zip(top, judged):
        if j is None:                       # AI failed for this pair: keyword fallback
            if s >= KEYWORD_MIN:
                out.append((s, c, None))
        elif j["verdict"] != "no":
            out.append((j["confidence"], c, j))
    return sorted(out, key=lambda x: -x[0])[:limit]


def save_links(item, found):
    """Remember each AI link (lost complaint <-> found item) so the graph can draw it."""
    col = "lost_id" if item["kind"] == "LOST" else "found_id"
    q(f"DELETE FROM links WHERE {col}=?", (item["id"],), fetch=False)   # drop stale links
    for score, c, j in found:
        lost_id, found_id = (item["id"], c["id"]) if item["kind"] == "LOST" else (c["id"], item["id"])
        verdict = j["verdict"] if j else "keyword"
        conf = j["confidence"] if j else min(score, 1.0)
        why = j["reason"] if j else "Matching words in the descriptions."
        q("INSERT OR REPLACE INTO links VALUES (?,?,?,?,?)",
          (lost_id, found_id, verdict, conf, why), fetch=False)


def relink():
    lost = q("SELECT * FROM items WHERE kind='LOST' AND status='OPEN'")
    for it in lost:
        save_links(it, matches_for(it))
    return f"Checked {len(lost)} lost complaint(s). Graph updated."


def graph_data():
    lost = q("SELECT id, title, name FROM items WHERE kind='LOST' AND status='OPEN' ORDER BY id DESC LIMIT 12")
    found = q("SELECT id, title FROM items WHERE kind='FOUND' AND status='OPEN' ORDER BY id DESC LIMIT 12")
    links = q("""SELECT l.lost_id AS lost, l.found_id AS found, l.verdict, l.confidence, l.reason
                 FROM links l JOIN items a ON a.id=l.lost_id AND a.status='OPEN'
                              JOIN items b ON b.id=l.found_id AND b.status='OPEN'""")
    for r in lost:    # first name only: the graph is public, contact details stay private
        r["name"] = (r["name"] or "").split(" ")[0]
    return {"lost": lost[::-1], "found": found[::-1], "links": links}


# --------------------------------- Tools ----------------------------------
def report_item(kind, title, description, location, when, contact_name, contact_email,
                secret_detail):
    kind = str(kind).strip().upper()
    if kind not in ("LOST", "FOUND"):
        return "Kind must be LOST or FOUND."
    email = str(contact_email).strip().lower()
    if "@" not in email or "." not in email:
        return "Invalid email. Ask for a valid email."
    if not str(title).strip():
        return "Item title is required."
    secret = str(secret_detail).strip().lower() or "none"
    dup = q("""SELECT * FROM items WHERE kind=? AND status='OPEN' AND email=?
               AND lower(title)=lower(?) AND lower(description)=lower(?)
               AND lower(location)=lower(?)""",
            (kind, email, str(title).strip(), str(description).strip(), str(location).strip()))
    if dup:
        item = dup[0]
    else:
        q("""INSERT INTO items(kind,title,description,location,when_text,name,email,secret)
             VALUES (?,?,?,?,?,?,?,?)""",
          (kind, str(title).strip(), str(description).strip(), str(location).strip(),
           str(when).strip(), str(contact_name).strip(), email, secret), fetch=False)
        item = q("SELECT * FROM items ORDER BY id DESC LIMIT 1")[0]
    found = matches_for(item)
    save_links(item, found)
    msg = (f"Report #{item['id']} was already saved." if dup else f"Saved {kind} report #{item['id']}.")
    if found:
        msg += "\nPossible matches:\n" + "\n".join(describe(c, s, j) for s, c, j in found)
        if kind == "LOST":
            msg += "\nTo claim a FOUND item the owner must describe its hidden detail."
    else:
        msg += "\nNo matches yet. The report stays open for future matching."
    return msg


def search_items(query, kind="ANY"):
    kind = str(kind).strip().upper()
    sql, params = "SELECT * FROM items WHERE status='OPEN'", ()
    if kind in ("LOST", "FOUND"):
        sql, params = sql + " AND kind=?", (kind,)
    qt = tokens(query)
    scored = [(len(qt & item_tokens(it)), it) for it in q(sql, params)]
    scored = sorted([x for x in scored if x[0]], key=lambda x: -x[0])
    return "\n".join(describe(it) for _, it in scored[:5]) or "No matching open reports."


def list_items(kind="ANY"):
    kind = str(kind).strip().upper()
    if kind in ("LOST", "FOUND"):
        rows = q("SELECT * FROM items WHERE status='OPEN' AND kind=?", (kind,))
    else:
        rows = q("SELECT * FROM items WHERE status='OPEN'")
    return "\n".join(describe(r) for r in rows) or "No open reports."


def verify_and_claim(item_id, claimant_name, claimant_email, answer):
    try:
        item_id = int(str(item_id).replace("#", "").strip())
    except ValueError:
        return "Item id must be a number."
    rows = q("SELECT * FROM items WHERE id=? AND kind='FOUND'", (item_id,))
    if not rows:
        return "No FOUND item with that id."
    it = rows[0]
    if it["status"] != "OPEN":
        return "This item has already been returned."
    if it["attempts"] >= MAX_ATTEMPTS:
        return "Too many failed attempts. Item locked; visit the office in person."
    if it["secret"] in ("", "none"):
        return "The finder set no hidden detail. Collect it in person at the office."
    need, got = tokens(it["secret"]), tokens(answer)
    if need and len(need & got) / len(need) >= 0.5:
        q("UPDATE items SET status='RETURNED' WHERE id=?", (item_id,), fetch=False)
        return (f"Verified! Finder contact for {claimant_name}: {it['name']} <{it['email']}>. "
                f"'{it['title']}' is marked as returned.")
    q("UPDATE items SET attempts=attempts+1 WHERE id=?", (item_id,), fetch=False)
    return f"Verification failed. {MAX_ATTEMPTS - it['attempts'] - 1} attempt(s) left. No hints."


TOOL_FUNCS = {"report_item": report_item, "search_items": search_items,
              "list_items": list_items, "verify_and_claim": verify_and_claim}


def run_tool(name, args):
    fn = TOOL_FUNCS.get(name)
    if not fn:
        return f"Unknown tool {name}."
    try:
        return fn(**args)
    except TypeError as e:
        return f"Missing or wrong information: {e}"


def _schema(name, desc, props):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object",
                       "properties": {k: {"type": v[0], "description": v[1]} for k, v in props.items()},
                       "required": [k for k, v in props.items() if len(v) < 3]}}}


S = "string"
TOOLS = [
    _schema("report_item", "Save a LOST or FOUND report and return matches.", {
        "kind": (S, "LOST or FOUND"), "title": (S, "Short item name"),
        "description": (S, "Visible details: colour, brand, marks"),
        "location": (S, "Where lost/found"), "when": (S, "When lost/found"),
        "contact_name": (S, "Reporter name"), "contact_email": (S, "Reporter email"),
        "secret_detail": (S, "FOUND: hidden detail only the owner knows. LOST: 'none'")}),
    _schema("search_items", "Search open reports by keywords (no contacts).", {
        "query": (S, "keywords"), "kind": (S, "LOST, FOUND or ANY")}),
    _schema("list_items", "List open reports (no contacts).", {
        "kind": (S, "LOST, FOUND or ANY")}),
    _schema("verify_and_claim", "Check claimant's description against hidden detail; "
            "returns finder contact on success.", {
        "item_id": ("integer", "FOUND item id"), "claimant_name": (S, "Claimant name"),
        "claimant_email": (S, "Claimant email"),
        "answer": (S, "Claimant's description of hidden/identifying details")}),
]

INSTRUCTIONS = """You are FindIt, a friendly campus lost-and-found assistant.
- Call report_item only ONCE per report. Always ask for item type, colour, brand and any marks.
- LOST report: collect item, visible details, where, when, name and email (one short question at a time), then call report_item with secret_detail "none".
- FOUND report: collect the same plus ONE hidden detail only the real owner would know. Say it stays private. Then call report_item.
- After saving, give the report number and any matches, including the AI's reason for each.
- CLAIM: ask the claimant to describe hidden/identifying details plus name and email, then call verify_and_claim.
- Never reveal or guess hidden details, never give hints, never show emails except from a successful verify_and_claim.
- Never invent items; use list_items or search_items. Keep replies short and kind."""


# ------------------------------ Agent + fallback --------------------------
SESSIONS = {}


def llm(messages):
    if _client:
        r = _client.chat.completions.create(
            model=MODEL_ID, messages=messages, tools=TOOLS,
            tool_choice="auto", max_tokens=700, temperature=0.2)
        m = r.choices[0].message
        calls = []
        for c in (m.tool_calls or []):
            a = c.function.arguments
            calls.append({"id": c.id, "type": "function", "function": {
                "name": c.function.name,
                "arguments": a if isinstance(a, str) else json.dumps(a)}})
        return {"content": m.content, "tool_calls": calls}
    body = json.dumps({"model": MODEL_ID, "messages": messages, "tools": TOOLS,
                       "tool_choice": "auto", "max_tokens": 700, "temperature": 0.2}).encode()
    req = urllib.request.Request(API_URL, body, {
        "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r)["choices"][0]["message"]


def ask_model(prompt):
    msgs = [{"role": "user", "content": prompt}]
    if _client:
        r = _client.chat.completions.create(model=MODEL_ID, messages=msgs,
                                            max_tokens=200, temperature=0)
        return r.choices[0].message.content or ""
    body = json.dumps({"model": MODEL_ID, "messages": msgs,
                       "max_tokens": 200, "temperature": 0}).encode()
    req = urllib.request.Request(API_URL, body, {
        "Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=45) as r:
        return json.load(r)["choices"][0]["message"]["content"] or ""


def offline_reply(text, why):
    t = text.lower().strip()
    if t.startswith("search "):
        body = search_items(t[7:], "ANY")
    elif "found" in t and any(w in t for w in ("list", "show", "what", "item", "all")):
        body = list_items("FOUND")
    elif "lost" in t and any(w in t for w in ("list", "show", "what", "item", "all")):
        body = list_items("LOST")
    else:
        body = ("Try: 'show found items', 'show lost items' or 'search backpack'. "
                "For reporting or claiming, use the forms on the right.")
    return f"(AI offline: {why[:90]})\n{body}"


def agent_reply(sid, text):
    if not TOKEN:
        return offline_reply(text, "no token in token.txt")
    hist = SESSIONS.setdefault(sid, [{"role": "system", "content": INSTRUCTIONS}])
    n0 = len(hist)
    hist.append({"role": "user", "content": text})
    try:
        for _ in range(6):
            msg = llm(hist)
            calls = msg.get("tool_calls") or []
            if not calls:
                answer = msg.get("content") or "Done."
                hist.append({"role": "assistant", "content": answer})
                return answer
            hist.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
            for c in calls:
                fn = c["function"]
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                hist.append({"role": "tool", "tool_call_id": c["id"],
                             "content": run_tool(fn["name"], args)})
        return "Sorry, that took too many steps. Please try again."
    except Exception as e:
        del hist[n0:]  # drop the failed turn so the history stays valid
        return offline_reply(text, str(e))


# ------------------------------ Web server --------------------------------
def data():
    def tbl(kind):
        return [[r["id"], r["title"], r["location"], r["when_text"], r["description"]]
                for r in q("SELECT * FROM items WHERE kind=? AND status='OPEN' ORDER BY id DESC",
                           (kind,))]

    def n(where):
        return q("SELECT COUNT(*) AS n FROM items WHERE " + where)[0]["n"]
    counts = {"lost": n("kind='LOST' AND status='OPEN'"),
              "found": n("kind='FOUND' AND status='OPEN'"),
              "back": n("status='RETURNED'")}
    return {"counts": counts, "found": tbl("FOUND"), "lost": tbl("LOST")}


def ping_ai():
    if not TOKEN:
        return {"ok": False, "msg": "AI asleep: no hf_ token found in token.txt next to app.py (forms still work)"}
    t0 = time.time()
    try:
        llm([{"role": "user", "content": "Reply with just: ok"}])
        return {"ok": True, "msg": f"AI is awake ({time.time() - t0:.1f}s)"}
    except Exception as e:
        err = str(e)
        hint = ("token rejected, check token.txt" if "401" in err or "403" in err else
                "free credits used up" if "402" in err else
                "model not available" if "404" in err or "400" in err else err[:60])
        return {"ok": False, "msg": "AI asleep: " + hint}


PAGE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<style>
:root{__THEME__}
*{box-sizing:border-box}
body{margin:0;font:15px/1.5 system-ui,"Segoe UI",sans-serif;background:var(--bg);color:var(--tx)}
header,main{max-width:1100px;margin:0 auto;padding-left:24px;padding-right:24px}
header{padding-top:28px;padding-bottom:18px}
h1{margin:0;font-size:24px;font-weight:600}.sub{color:var(--mut);margin-top:2px}
#stats{margin-top:12px;font-size:14px}
#ai{font-size:13px;margin-top:2px;cursor:pointer;color:var(--mut)}#ai.ok{color:var(--ok)}#ai.bad{color:var(--bad)}
main{display:grid;grid-template-columns:3fr 2fr;gap:20px;padding-bottom:32px}
@media(max-width:850px){main{grid-template-columns:1fr}}
.card{background:var(--card);border:1px solid var(--bd);border-radius:6px;padding:16px;margin-bottom:20px}
h2{font-size:14px;font-weight:600;margin:0 0 12px}
#log{height:380px;overflow:auto;display:flex;flex-direction:column;gap:8px}
.m{padding:8px 12px;border-radius:6px;max-width:85%;white-space:pre-wrap}
.me{align-self:flex-end;background:var(--pri);color:#fff}.bot{background:var(--soft)}
.row{display:flex;gap:8px;margin-top:12px}
input,select,textarea{width:100%;padding:8px 10px;border-radius:6px;border:1px solid var(--bd);
background:#fff;color:var(--tx);font:inherit;margin-bottom:8px}
input:focus,select:focus,textarea:focus{outline:2px solid var(--pri);outline-offset:-1px}
.row input{margin:0}
button{background:var(--pri);color:#fff;border:0;border-radius:6px;padding:8px 16px;cursor:pointer;font:inherit}
button:hover{filter:brightness(.94)}
button.ghost{background:transparent;color:var(--pri);border:1px solid var(--bd);padding:4px 10px;font-size:13px}
button.ghost:hover{background:var(--soft);filter:none}
.chips{display:flex;flex-wrap:wrap;gap:6px;margin-top:12px}
table{width:100%;border-collapse:collapse;font-size:13px}
th{color:var(--mut);font-weight:500}
td,th{padding:6px 4px;border-bottom:1px solid var(--bd);text-align:left;vertical-align:top}
details summary{cursor:pointer;font-weight:600;margin-bottom:10px}
#out{white-space:pre-wrap;color:var(--mut);margin-top:8px}
#graph{width:100%;height:auto;display:block}#graph text{fill:var(--tx);font-size:12px}#graph .gh{fill:var(--mut)}
#graph rect{fill:var(--soft);stroke:var(--bd)}
.edge{stroke:var(--pri);pointer-events:none}.edge.maybe{stroke-dasharray:5 4;opacity:.7}.edge.keyword{stroke:var(--mut)}
.hit{stroke:transparent;stroke-width:14;cursor:pointer}
.gtop{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}.gtop h2{margin:0}
#why{color:var(--mut);margin-top:8px;min-height:22px;white-space:pre-wrap}
</style></head><body>
<header><h1>__TITLE__</h1><div class="sub">__TAG__</div><div id="stats"></div><div id="ai" onclick="ping()">checking AI...</div></header>
<main><section>
<div class="card"><h2>Assistant</h2><div id="log"><div class="m bot">Hi! I can help you report or claim lost items. What happened?</div></div>
<div class="row"><input id="msg" placeholder="e.g. I lost my black Dell backpack in the library">
<button onclick="send()">Send</button></div>
<div class="chips" id="chips"></div></div>
<div class="card"><div class="gtop"><h2>Match graph: who lost it, and which found item the AI links to it</h2><button class="ghost" onclick="relink()">Find matches</button></div>
<svg id="graph" viewBox="0 0 560 120"></svg>
<div id="why">Click a line to see why the AI linked a lost complaint to a found item.</div></div></section>
<section>
<div class="card"><h2>Found items waiting</h2><table><thead><tr><th>ID</th><th>Item</th><th>Where</th><th>When</th><th>Details</th></tr></thead><tbody id="found"></tbody></table></div>
<div class="card"><h2>Lost items</h2><table><thead><tr><th>ID</th><th>Item</th><th>Where</th><th>When</th><th>Details</th></tr></thead><tbody id="lost"></tbody></table></div>
<div class="card"><details><summary>Report an item (form)</summary>
<form id="rf"><select name="kind"><option>LOST</option><option>FOUND</option></select>
<input name="title" placeholder="Item (e.g. Red umbrella)" required>
<input name="description" placeholder="Colour, brand, marks">
<input name="location" placeholder="Where"><input name="when" placeholder="When (date)">
<input name="contact_name" placeholder="Your name"><input name="contact_email" placeholder="Your email" required>
<input name="secret_detail" placeholder="FOUND only: hidden detail the owner would know">
<button>Save report</button></form></details></div>
<div class="card"><details><summary>Claim a found item (form)</summary>
<form id="cf"><input name="item_id" placeholder="Found item ID (number)" required>
<input name="claimant_name" placeholder="Your name"><input name="claimant_email" placeholder="Your email">
<textarea name="answer" rows="2" placeholder="Describe hidden/identifying details"></textarea>
<button>Verify and claim</button></form></details><div id="out"></div></div>
</section></main>
<script>
const $=id=>document.getElementById(id),sid=Math.random().toString(36).slice(2);
const post=async(u,b)=>(await fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)})).json();
function fill(id,rs){const tb=$(id);tb.innerHTML='';rs.forEach(r=>{const tr=document.createElement('tr');
r.forEach(c=>{const td=document.createElement('td');td.textContent=c;tr.appendChild(td)});tb.appendChild(tr)})}
async function refresh(){const d=await(await fetch('/api/data')).json(),c=d.counts;
$('stats').textContent=c.lost+' lost open  |  '+c.found+' found waiting  |  '+c.back+' reunited';fill('found',d.found);fill('lost',d.lost);drawGraph()}
const NS='http://www.w3.org/2000/svg';
function el(n,a,t){const e=document.createElementNS(NS,n);for(const k in a)e.setAttribute(k,a[k]);if(t!==undefined)e.textContent=t;return e}
async function drawGraph(){const g=await(await fetch('/api/graph')).json(),svg=$('graph');svg.innerHTML='';
const W=560,rows=Math.max(g.lost.length,g.found.length,1),H=40+rows*36,y=i=>44+i*36,L={},F={};
svg.setAttribute('viewBox','0 0 '+W+' '+H);
svg.appendChild(el('text',{x:10,y:16,class:'gh'},'LOST complaint (node X)'));
svg.appendChild(el('text',{x:W-10,y:16,class:'gh','text-anchor':'end'},'FOUND item (node Y)'));
g.lost.forEach((r,i)=>L[r.id]=y(i));g.found.forEach((r,i)=>F[r.id]=y(i));
g.links.forEach(l=>{if(!(l.lost in L&&l.found in F))return;const a={x1:200,y1:L[l.lost],x2:W-200,y2:F[l.found]};
const hit=el('line',Object.assign({class:'hit'},a));hit.addEventListener('click',()=>{
$('why').textContent='#'+l.lost+' and #'+l.found+' ('+l.verdict+', '+Math.round(l.confidence*100)+'%): '+l.reason});
svg.appendChild(hit);svg.appendChild(el('line',Object.assign({class:'edge '+l.verdict,'stroke-width':1+3*l.confidence},a)))});
g.lost.forEach(r=>{svg.appendChild(el('rect',{x:10,y:L[r.id]-14,width:190,height:28,rx:4}));
svg.appendChild(el('text',{x:18,y:L[r.id]+4},'#'+r.id+' '+r.title.slice(0,15)+(r.name?' ('+r.name.slice(0,9)+')':'')))});
g.found.forEach(r=>{svg.appendChild(el('rect',{x:W-200,y:F[r.id]-14,width:190,height:28,rx:4}));
svg.appendChild(el('text',{x:W-192,y:F[r.id]+4},'#'+r.id+' '+r.title.slice(0,24)))})}
async function relink(){$('why').textContent='Checking matches...';const r=await post('/api/relink',{});$('why').textContent=r.reply;refresh()}
function add(c,t){const d=document.createElement('div');d.className='m '+c;d.textContent=t;$('log').appendChild(d);$('log').scrollTop=1e9;return d}
async function send(t){t=(t||$('msg').value).trim();if(!t)return;$('msg').value='';add('me',t);const w=add('bot','Thinking...');
try{const r=await post('/api/chat',{sid,message:t});w.textContent=r.reply}catch(e){w.textContent='Server error'}refresh()}
$('msg').addEventListener('keydown',e=>{if(e.key==='Enter')send()});
['What items have been found?','I lost my black Dell backpack in the library','Search for water bottle'].forEach(t=>{
const b=document.createElement('button');b.className='ghost';b.textContent=t;b.onclick=()=>send(t);$('chips').appendChild(b)});
async function ping(){const s=$('ai');s.className='';s.textContent='checking AI...';
try{const r=await(await fetch('/api/ping')).json();s.className=r.ok?'ok':'bad';s.textContent=r.msg+' (click to recheck)'}catch(e){s.className='bad';s.textContent='server not answering'}}
function form(id,url){$(id).onsubmit=async e=>{e.preventDefault();const r=await post(url,Object.fromEntries(new FormData(e.target)));
$('out').textContent=r.reply;refresh()}}
form('rf','/api/report');form('cf','/api/claim');refresh();ping();
</script></body></html>
"""


_hits = {}


def too_many(ip):
    """Simple per-IP limit for the AI endpoints so strangers cannot drain free credits."""
    now = time.time()
    recent = [t for t in _hits.get(ip, []) if now - t < 3600]
    if len(recent) >= RATE_LIMIT:
        _hits[ip] = recent
        return True
    recent.append(now)
    _hits[ip] = recent
    return False


class Server(ThreadingHTTPServer):
    # On Windows, reuse lets a 2nd copy start on a busy port while the OLD copy keeps answering.
    allow_reuse_address = os.name != "nt"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, obj, ctype="application/json"):
        body = obj if isinstance(obj, bytes) else json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/":
            self._send(200, PAGE.replace("__TITLE__", APP_TITLE).replace("__NAME__", APP_NAME).replace("__TAG__", TAGLINE).replace("__THEME__", THEMES.get(THEME, THEMES["blue"])).encode(), "text/html; charset=utf-8")
        elif self.path == "/api/data":
            self._send(200, data())
        elif self.path == "/api/graph":
            self._send(200, graph_data())
        elif self.path == "/api/ping":
            self._send(200, ping_ai())
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            body = {}
        ip = (self.headers.get("X-Forwarded-For") or self.client_address[0]).split(",")[0].strip()
        if self.path in ("/api/chat", "/api/relink") and too_many(ip):
            return self._send(200, {"reply": "Too many AI requests from your network. Please wait a while and try again."})
        if self.path == "/api/chat":
            msg = str(body.get("message", ""))[:1000]
            reply = agent_reply(str(body.get("sid", "x")), msg)
            print(f"[chat] you: {msg[:60]}\n       bot: {reply[:60]!r}")
        elif self.path == "/api/relink":
            reply = relink()
        elif self.path == "/api/report":
            reply = run_tool("report_item", body)
        elif self.path == "/api/claim":
            reply = run_tool("verify_and_claim", body)
        else:
            return self._send(404, {"error": "not found"})
        self._send(200, {"reply": reply})

    def log_message(self, *args):
        pass


def main():
    init_db()
    try:
        server = Server(("0.0.0.0" if HOSTED else "127.0.0.1", PORT), Handler)
    except OSError:
        print(f"\nPort {PORT} is already in use, so FindIt is probably already running in another window.")
        print("Close that black window (or run: taskkill /F /IM python.exe), then start this again.")
        return
    mode = ("AI chat ON (" + ("HF SDK" if _client else "urllib, SDK not installed") + ")") if TOKEN else \
        "AI chat OFF (no token found) - forms still work"
    url = f"http://127.0.0.1:{PORT}"
    print(f"{APP_TITLE}  [version 4]\n{mode}\nOpen {url}   (Ctrl+C to stop)")
    if not TOKEN:
        print(f"Looking for the token in: {TOKEN_PATH}  (file found: {os.path.exists(TOKEN_PATH)})")
    if not HOSTED:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    server.serve_forever()


if __name__ == "__main__":
    main()
