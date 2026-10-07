"""Finds out why 'AI asleep' shows up. Run it with check_ai.bat (or: py check_ai.py)."""
import json
import os
import urllib.error
import urllib.request

MODEL_ID = os.getenv("MODEL_ID", "Qwen/Qwen2.5-72B-Instruct")


def get_token():
    import re
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "token.txt")
    t = os.getenv("HF_TOKEN", "")
    print("Looking for:", path, "| file found:", os.path.exists(path))
    if not t and os.path.exists(path):
        raw = open(path, "rb").read()
        for enc in ("utf-8-sig", "utf-16"):
            try:
                t = raw.decode(enc)
                break
            except UnicodeDecodeError:
                continue
    m = re.search(r"hf_[A-Za-z0-9]{20,}", t.replace("\x00", ""))
    return m.group(0) if m else ""


def call(url, tok, body=None):
    data = json.dumps(body).encode() if body else None
    headers = {"Authorization": "Bearer " + tok}
    if body:
        headers["Content-Type"] = "application/json"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data, headers), timeout=45) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:300]
    except Exception as e:
        return None, str(e)


tok = get_token()
print("Step 1: token file")
if not tok:
    print("  PROBLEM: no hf_ token found. Create token.txt in the folder shown above and paste your token in it.")
    print("  (Check the file is not named token.txt.txt - turn on File name extensions in Explorer.)")
    raise SystemExit
if not tok.startswith("hf_"):
    print("  PROBLEM: the token must start with hf_ . Check for extra words or quotes in token.txt.")
    raise SystemExit
print("  ok, found a token starting with hf_")

print("Step 2: is the token valid?")
code, res = call("https://huggingface.co/api/whoami-v2", tok)
if code == 200:
    print("  ok, token belongs to:", res.get("name"))
elif code == 401:
    print("  PROBLEM: Hugging Face rejected this token (deleted or mistyped).")
    print("  Fix: create a new token at huggingface.co/settings/tokens and paste it in token.txt.")
    raise SystemExit
else:
    print("  could not check:", code, res)
    print("  (If this says a network error, check your internet or firewall.)")

print("Step 3: ask the model", MODEL_ID)
code, res = call("https://router.huggingface.co/v1/chat/completions", tok, {
    "model": MODEL_ID, "max_tokens": 10,
    "messages": [{"role": "user", "content": "Reply with just: ok"}]})
if code == 200:
    print("  SUCCESS: the AI works. Restart the app and click 'checking AI' to recheck.")
    raise SystemExit
print("  PROBLEM:", code, res)
hints = {
    402: "Free credits are used up. Wait for the monthly reset or use another account's token.",
    403: "The token lacks permission. Create a token that allows 'Inference Providers'.",
    429: "Too many requests. Wait a minute and try again.",
    400: "This model is not available right now. Pick another one from the list below.",
    404: "This model is not available right now. Pick another one from the list below.",
}
print("  Meaning:", hints.get(code, "See the message above."))

print("Step 4: models you can use right now")
code, res = call("https://router.huggingface.co/v1/models", tok)
if code == 200:
    ids = [m.get("id") for m in res.get("data", [])][:25]
    for i in ids:
        print("   ", i)
    print("\nTo use one, add this line above 'py app.py' in run.bat:")
    print("  set MODEL_ID=<paste a name from the list>")
else:
    print("  could not load the list:", code, res)
