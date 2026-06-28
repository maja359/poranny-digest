#!/usr/bin/env python3
"""Poranny Digest generator — runs inside GitHub Actions.

1. Calls the Claude API (web search) to produce the day's content as JSON.
2. Renders it to a static HTML page (images = direct <img src>, loaded by the
   reader's browser).
3. Writes RRRR-MM-DD.html + index.html + updates seen.json IN THE REPO CHECKOUT.

The workflow commits & pushes the result (GITHUB_TOKEN has contents:write — no PAT,
no api.github.com integration proxy, so none of the 403 problems the cloud routine hit).
Then the workflow posts the link to Slack via an incoming webhook.

Env: ANTHROPIC_API_KEY (required). Run from the repo root.
"""
import os, sys, json, re, datetime, urllib.parse
import anthropic

MODEL = "claude-opus-4-8"
PAGES = "https://maja359.github.io/poranny-digest/"
ROOT = os.path.dirname(os.path.abspath(__file__))

PL_MONTHS = ["stycznia","lutego","marca","kwietnia","maja","czerwca",
             "lipca","sierpnia","września","października","listopada","grudnia"]

# ---------------------------------------------------------------- date + memory
today = datetime.date.today()
date_file = today.isoformat()
date_pl = f"{today.day} {PL_MONTHS[today.month-1]} {today.year}"

seen = {}
seen_path = os.path.join(ROOT, "seen.json")
if os.path.exists(seen_path):
    try:
        seen = json.load(open(seen_path, encoding="utf-8"))
    except Exception:
        seen = {}
for k in ("books","beauty","topics","rynek","osoby"):
    seen.setdefault(k, [])

# ---------------------------------------------------------------- prompt
SYSTEM = """You are the "Poranny Digest" agent — you write a daily morning newsletter in Polish for Maja Regula, founder of Owlsome Studio (a branding studio in Warsaw, Poland). She reads it on her phone with morning coffee.

Audience: Maja follows AI news casually. She knows OpenAI, Google, Anthropic, Meta, Apple, what a language model / ChatGPT is — do NOT explain these. No finance background — the Rynek section is her daily financial education in plain language with every concept explained. Never patronize.

You have web_search and web_fetch. Use them to research everything fresh. Today's date is %(date_pl)s. Images are loaded by Maja's BROWSER from direct URLs you provide — you do not download them.

## ANTI-REPEAT (hard rules)
Do NOT repeat anything already used. Already used:
- Books: %(books)s
- Beauty brands: %(beauty)s
- AI personalities: %(osoby)s
- Financial concepts: %(rynek)s
- News topics: %(topics)s

## Sections to produce
1. **rynek** — daily financial-literacy mini-lesson (NOT a market report). Search the most-talked-about financial story of the last 24h (IPO, acquisition, earnings, central-bank decision, USD/PLN or EUR/PLN move, inflation). Flowing prose, three parts no labels: (1) the news in 1-2 plain sentences; (2) the ONE concept inside it explained in 2-4 sentences with an everyday analogy; (3) "więc ta wiadomość oznacza, że..." what follows for the company / ordinary people / Poland. 5-8 sentences, one concept. Also output rynek_concept (short label). Plain language ("giełda w USA mocno spadła", not "S&P 500 odnotowało korektę").
2. **ai** — 1-3 AI stories from the last 24h (`newer_than:1d`): launches, big company moves, what's going viral / debated. Lead with what happened, then needed context, then why it matters. 1-2 source links each.
3. **nauka** — 1-2 longevity/neuroscience stories from the last 24h: human clinical results, aging/brain/Alzheimer's, evidence-based sleep/exercise/diet. Skip supplement marketing and weak single studies.
4. **osoba** (Twarz AI) — ONE well-known AI person (researcher/founder/leader). Pool to rotate (skip seen): Geoffrey Hinton, Yoshua Bengio, Yann LeCun, Fei-Fei Li, Andrew Ng, Demis Hassabis, Dario Amodei, Sam Altman, Ilya Sutskever, Mira Murati, Andrej Karpathy, Jensen Huang, Mustafa Suleyman, Timnit Gebru, Stuart Russell, Max Tegmark, Daniela Amodei. GATED ON A PHOTO: web_fetch `https://en.wikipedia.org/api/rest_v1/page/summary/<Exact_Article_Title>`, read originalimage.source; if present (a https://upload.wikimedia.org/... URL) use it verbatim as image_url; if absent pick someone else. Write 2-3 short paragraphs: lead with the single most surprising thing, explain ONE concrete contribution in plain language, wrap it in a story/quote/quirk so it sticks. NOT a CV, skip dates/career lists. Output rola = 3-6 word tagline.
5. **ksiazka** — ONE popular-science book (AI & society, neuroscience, longevity, behavioral science, sleep, gut-brain, psychology, evolutionary biology). MUST have a Polish translation (verify on lubimyczytac.pl or empik.com) AND **must have been first published in the last 5 years (2021 or later) — prefer the newest strong title; neuroscience moves fast, no classics.** Use the Polish title (original in parentheses if very different). Write the ONE idea/story that makes it worth reading, with a vivid hook (follow editorial rule). cover_url: find the book's English-original ISBN-13 and set cover_url = `https://covers.openlibrary.org/b/isbn/<ISBN13>-L.jpg`; if you can confirm an Open Library cover id, you may instead use `https://covers.openlibrary.org/b/id/<id>-L.jpg`. May be null if no cover.
6. **beauty** (Beauty Brand) — ONE well-known beauty brand (skip seen). GATED ON A PHOTO exactly like osoba: web_fetch the brand's Wikipedia REST summary, use originalimage.source as image_urls[0]; if absent pick another brand. Lead with the most surprising thing, wrap the origin in a short story, land on concrete visual-identity keywords (palette, packaging mood, photography, typography). 2-3 tight paragraphs. Output styl = 3 keywords joined by " · ". Brands confirmed to have a Wikipedia photo: Glossier, Fenty Beauty, Rare Beauty, Byredo, Le Labo, Dior, Chanel, YSL, Tom Ford, Tower 28, CeraVe — but verify whichever you pick.
7. **inn** — ONE culturally interesting thing from the last ~3-5 days: a viral story / real debate / surprising beauty-wellness-branding-creative trend / AI-culture moment / a brand doing something remarkable. Stay in branding, beauty/lifestyle, wellness, AI creativity & culture, social media, creative industry. 1-2 source links.

## STYLE (all sections)
Polish, like a smart well-read friend — natural, not corporate, not AI-polished. Editorial rule for EVERY section: only the most interesting, memorable facts wrapped in a small story/hook; cut CVs, chronologies, lists of titles, dates unless the date is the point; lead with the most surprising thing. Maja's test: could she retell it to a friend in one sentence. NO em dashes anywhere — use commas or periods. Never use przełomowy / rewolucyjny / game-changer as hype. Body fields may use **bold** and [text](url) markdown links. 1-2 source links per news story.

## OUTPUT
Respond with EXACTLY ONE JSON object and NOTHING else (no prose before or after, no code fences). Shape:
{
  "date_pl": "%(date_pl)s",
  "date_file": "%(date_file)s",
  "rynek": "...", "rynek_concept": "...",
  "ai": [{"headline":"...","body":"para\\n\\npara","sources":[["Name","https://..."]],"image_url":null}],
  "nauka": [{"headline":"...","body":"...","sources":[["Name","https://..."]],"image_url":null}],
  "osoba": {"name":"...","rola":"...","body":"...","image_url":"https://upload.wikimedia.org/...","sources":[["Name","https://..."]]},
  "ksiazka": {"title":"Polski tytuł — Autor","body":"...","cover_url":"https://covers.openlibrary.org/b/isbn/...-L.jpg"},
  "beauty": {"name":"...","styl":"k · k · k","body":"...","image_urls":["https://upload.wikimedia.org/..."]},
  "inn": {"headline":"...","body":"...","sources":[["Name","https://..."]],"image_url":null}
}
Use null for any image you cannot confirm. osoba.image_url and beauty.image_urls[0] should almost always be a real Wikipedia upload.wikimedia.org URL (sections 4 and 6 are gated on it). A quiet news day (1 item each) is fine — do not pad.""" % {
    "date_pl": date_pl, "date_file": date_file,
    "books": "; ".join(seen["books"]) or "(none)",
    "beauty": "; ".join(seen["beauty"]) or "(none)",
    "osoby": "; ".join(seen["osoby"]) or "(none)",
    "rynek": "; ".join(seen["rynek"]) or "(none)",
    "topics": "; ".join(seen["topics"][-60:]) or "(none)",
}

# ---------------------------------------------------------------- API call
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
tools = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
]
messages = [{"role": "user", "content": "Wygeneruj dzisiejszy Poranny Digest jako JSON zgodnie z instrukcją."}]

resp = None
for _ in range(8):  # server-tool loop: re-send on pause_turn
    resp = client.messages.create(
        model=MODEL, max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM, tools=tools, messages=messages,
    )
    if resp.stop_reason == "pause_turn":
        messages.append({"role": "assistant", "content": resp.content})
        continue
    break

if resp.stop_reason == "refusal":
    print("REFUSAL", getattr(resp, "stop_details", None)); sys.exit(1)

text = "".join(b.text for b in resp.content if b.type == "text").strip()
m = re.search(r"\{.*\}", text, re.S)  # tolerate stray prose / fences
if not m:
    print("NO_JSON_IN_RESPONSE\n" + text[:1000]); sys.exit(1)
c = json.loads(m.group(0))
c.setdefault("date_pl", date_pl)
c.setdefault("date_file", date_file)

# ---------------------------------------------------------------- render HTML
def esc(s):
    return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

IMG_EXT = (".jpg",".jpeg",".png",".webp",".gif")
OK_HOSTS = ("upload.wikimedia.org","covers.openlibrary.org","s.lubimyczytac.pl")
def ok_img(u):
    if not u or not isinstance(u, str) or not u.startswith("https://"): return False
    p = urllib.parse.urlsplit(u)
    if p.path.lower().endswith(IMG_EXT): return True
    if p.netloc.lower() in OK_HOSTS: return True
    return False
def img(u, alt):
    if not ok_img(u): return ""
    if "covers.openlibrary.org" in u and "default=" not in u:
        u = u + ("&" if "?" in u else "?") + "default=false"
    return '<img src="%s" alt="%s" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">' % (esc(u), esc(alt))

def md(text):
    text = esc(text)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^)\s]+)\)', r'<a href="\2">\1</a>', text)
    text = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', text)
    out = []
    for p in [p.strip() for p in text.split("\n\n") if p.strip()]:
        if p.startswith("### "): out.append("<h3>%s</h3>" % p[4:].strip())
        else: out.append("<p>%s</p>" % p.replace("\n","<br>"))
    return "\n".join(out)

def sources(s):
    if not s: return ""
    links = " · ".join('<a href="%s">%s</a>' % (esc(u), esc(n)) for n,u in s)
    return '<p class="src">Źródła: %s</p>' % links

P = []
P.append('<header><div class="kicker">Poranny digest</div><h1>%s</h1></header>' % esc(c["date_pl"]))
if c.get("rynek"):
    P.append('<section><div class="tag">Rynek</div>%s</section>' % md(c["rynek"]))
for s in c.get("ai") or []:
    P.append('<section><div class="tag">AI</div><h2>%s</h2>%s%s%s</section>' % (esc(s["headline"]), img(s.get("image_url"), s["headline"]), md(s.get("body","")), sources(s.get("sources"))))
o = c.get("osoba")
if o:
    rola = '<p class="styl">%s</p>' % esc(o["rola"]) if o.get("rola") else ""
    P.append('<section><div class="tag">Twarz AI</div><h2>%s</h2>%s%s%s%s</section>' % (esc(o["name"]), rola, img(o.get("image_url"), o["name"]), md(o.get("body","")), sources(o.get("sources"))))
for s in c.get("nauka") or []:
    P.append('<section><div class="tag">Nauka</div><h2>%s</h2>%s%s%s</section>' % (esc(s["headline"]), img(s.get("image_url"), s["headline"]), md(s.get("body","")), sources(s.get("sources"))))
k = c.get("ksiazka")
if k:
    P.append('<section><div class="tag">Polecana książka</div><h2>%s</h2>%s%s</section>' % (esc(k["title"]), img(k.get("cover_url"), "Okładka: "+k["title"]), md(k.get("body",""))))
b = c.get("beauty")
if b:
    gal = "".join(img(u, b["name"]) for u in (b.get("image_urls") or []))
    if not gal and b.get("image_url"): gal = img(b.get("image_url"), b["name"])
    styl = '<p class="styl">%s</p>' % esc(b["styl"]) if b.get("styl") else ""
    gal = '<div class="gallery">%s</div>' % gal if gal else ""
    P.append('<section><div class="tag">Beauty Brand</div><h2>%s</h2>%s%s%s</section>' % (esc(b["name"]), styl, gal, md(b.get("body",""))))
i = c.get("inn")
if i:
    P.append('<section><div class="tag">Inn</div><h2>%s</h2>%s%s%s</section>' % (esc(i["headline"]), img(i.get("image_url"), i["headline"]), md(i.get("body","")), sources(i.get("sources"))))

CSS = "*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;max-width:680px;margin:0 auto;padding:28px 20px 80px;color:#1d1d1f;line-height:1.62;font-size:17px;background:#fafaf8}header{margin:8px 0 28px}.kicker{text-transform:uppercase;letter-spacing:.14em;font-size:12px;color:#9b8d7a;font-weight:700}h1{font-size:30px;margin:.15em 0 0;font-weight:700}section{padding:26px 0;border-top:1px solid #ece8e1}.tag{display:inline-block;text-transform:uppercase;letter-spacing:.1em;font-size:11px;font-weight:700;color:#fff;background:#b59a7d;padding:3px 9px;border-radius:99px;margin-bottom:10px}h2{font-size:21px;margin:.1em 0 .45em;line-height:1.3}h3{font-size:17px;margin:1.1em 0 .3em}p{margin:.55em 0}a{color:#9a6f3f;text-decoration:underline;text-underline-offset:2px}.src{font-size:14px;color:#8a8278;margin-top:.7em}.styl{font-style:italic;color:#8a8278;margin-top:-.2em}.gallery{display:flex;flex-direction:column;gap:12px;margin:14px 0}img{max-width:100%;max-height:340px;width:auto;height:auto;border-radius:14px;display:block;background:#efece6;margin:14px auto}.gallery img{margin:0 auto}footer{margin-top:40px;font-size:13px;color:#b3a89a;text-align:center}"

html_doc = '<!doctype html><html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Poranny digest — %s</title><style>%s</style></head><body>%s<footer>Poranny digest · generowany automatycznie</footer></body></html>' % (esc(c["date_pl"]), CSS, "\n".join(P))

page = c["date_file"] + ".html"
open(os.path.join(ROOT, page), "w", encoding="utf-8").write(html_doc)
redirect = '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex"><meta http-equiv="refresh" content="0; url=%s"><title>Poranny digest</title><a href="%s">Poranny digest — %s</a>' % (page, page, esc(c["date_pl"]))
open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8").write(redirect)

# ---------------------------------------------------------------- update seen.json
def norm(s): return re.sub(r"\s+"," ",(s or "")).strip().lower()
def remember(key, val):
    if not val: return
    if norm(val) not in [norm(x) for x in seen[key]]: seen[key].append(val)
remember("books", k.get("title") if k else None)
remember("beauty", b.get("name") if b else None)
remember("osoby", o.get("name") if o else None)
remember("rynek", c.get("rynek_concept"))
for s in (c.get("ai") or []) + (c.get("nauka") or []): remember("topics", s.get("headline"))
if i: remember("topics", i.get("headline"))
seen["_note"] = "Persistent anti-repeat memory for Poranny Digest. Appended automatically each day by the GitHub Action."
json.dump(seen, open(seen_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

url = PAGES + page
# Hand the URL + Polish date to the workflow (for the Slack step)
gh_out = os.environ.get("GITHUB_OUTPUT")
if gh_out:
    with open(gh_out, "a", encoding="utf-8") as f:
        f.write("published_url=%s\n" % url)
        f.write("date_pl=%s\n" % date_pl)
print("PUBLISHED_URL=" + url)
