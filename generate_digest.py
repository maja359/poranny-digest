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

# importuj obok tego pliku niezaleznie od katalogu roboczego
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import money  # sekcja "Twoje pieniadze": liczona lokalnie, model jej nie dotyka
import fx     # sekcja "Waluty": kursy NBP, tez lokalnie, tez bez udzialu modelu
import pools  # pule tematow + rotacja katow: bohatera dnia wybiera skrypt, nie model
import dedup  # deterministyczny wykrywacz powtorek (prompt tego nie udzwignal)

MODEL = "claude-haiku-4-5"          # researcher: cheap, web tools, gathers the facts
MODEL_WRITER = "claude-sonnet-5"    # writer: one no-tools pass, rewrites everything into natural Polish

# Hard cost controls (the 2026-07 blowup: Sonnet + unlimited adaptive thinking +
# uncapped web_fetch = ~$25/run). Every knob below exists to keep one run in cents.
MAX_ROUNDS = 6                 # pause_turn continuations
MAX_SEARCHES = 8               # web_search $10/1000
MAX_FETCHES = 8                # web_fetch is free per-call but its content bills as input tokens
FETCH_TOKEN_CAP = 5000         # truncate every fetched page
COST_GUARD_USD = 1.00          # abort the run outright if estimate crosses this
# 2026-07-27: researcher hit stop_reason=max_tokens on EVERY run for a week at 10000
# (the digest grew: Ciekawostka dnia + longer sections). A truncated tail either
# breaks the JSON outright (25.07 needed a salvage call) or silently drops the last
# section. Output is billed per token produced, so a higher cap costs nothing unless
# the model actually uses it: ~$0.03 worst case, cheaper than one salvage round-trip.
RESEARCH_MAX_TOKENS = 16000
# Haiku 4.5 pricing per MTok
PRICE_IN, PRICE_OUT, PRICE_CACHE_W, PRICE_CACHE_R = 1.00, 5.00, 1.25, 0.10
# Sonnet 5 pricing per MTok (writer pass: small token volume, no tools)
PRICE_IN_W, PRICE_OUT_W = 3.00, 15.00
PAGES = "https://maja359.github.io/poranny-digest/"
ROOT = os.path.dirname(os.path.abspath(__file__))

PL_MONTHS = ["stycznia","lutego","marca","kwietnia","maja","czerwca",
             "lipca","sierpnia","września","października","listopada","grudnia"]

# ---------------------------------------------------------------- date + memory
today = datetime.date.today()
date_file = today.isoformat()
date_pl = f"{today.day} {PL_MONTHS[today.month-1]} {today.year}"

# Idempotency: if today's page already exists with real content, don't burn an
# API call (covers manual dispatch + delayed cron firing on the same day).
_page_path = os.path.join(ROOT, date_file + ".html")
if os.path.exists(_page_path) and os.path.getsize(_page_path) > 3000:
    print("ALREADY_PUBLISHED " + date_file + " — skipping (no Slack payload written)")
    sys.exit(0)

seen = {}
seen_path = os.path.join(ROOT, "seen.json")
if os.path.exists(seen_path):
    try:
        seen = json.load(open(seen_path, encoding="utf-8"))
    except Exception:
        seen = {}
for k in ("books","beauty","topics","osoby","ciekawostki"):
    seen.setdefault(k, [])
# pamiec rotacji: co juz bylo w ostatnich dniach jako KAT sekcji, nie jako tresc.
# Bez tego rotacja losowalaby ten sam temat dwa dni z rzedu.
for k in ("cieka_cats","nauka_themes","ai_angles","book_themes","authors"):
    seen.setdefault(k, [])

# ------------------------------------------------- wyszukiwanie zdjec (runner ma siec)
# Te funkcje stoja TU, przed promptem, bo od 08.2026 zdjecie bohatera i marki
# sprawdzamy PRZED generowaniem: skrypt schodzi po liscie kandydatow, az trafi
# na kogos z fotografia, i dopiero wtedy mowi modelowi, o kim ma pisac. Wczesniej
# bylo odwrotnie (model wybieral z recznie zweryfikowanej listy 18 pozycji) i to
# wlasnie rozmiar tej listy dusil rozmaitosc.
import urllib.request, time

def http_json(u):
    for attempt in (0, 1, 2):  # Wikipedia REST rate-limits bursts; back off and retry
        if attempt: time.sleep(3 * attempt)
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "poranny-digest/1.0 (github.com/maja359/poranny-digest)"})
            with urllib.request.urlopen(req, timeout=15) as r:
                return json.load(r)
        except Exception:
            continue
    return None

def url_is_image(u):
    try:
        req = urllib.request.Request(u, method="HEAD", headers={"User-Agent": "poranny-digest/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status == 200 and r.headers.get("Content-Type", "").startswith("image/")
    except Exception:
        return False

def wiki_image(title, langs=("en", "fr", "de", "pl")):
    if not title: return None
    for lang in langs:
        j = http_json("https://%s.wikipedia.org/api/rest_v1/page/summary/" % lang
                      + urllib.parse.quote(str(title).replace(" ", "_")))
        if not j: continue
        for k in ("originalimage", "thumbnail"):
            src = (j.get(k) or {}).get("source")
            if src and url_is_image(src): return src
    return None

def cover_from_isbn(isbn):
    isbn = re.sub(r"\D", "", str(isbn or ""))
    if len(isbn) not in (10, 13): return None
    u = "https://covers.openlibrary.org/b/isbn/%s-L.jpg?default=false" % isbn
    return u if url_is_image(u) else None

def cover_from_search(q):
    if not q: return None
    j = http_json("https://openlibrary.org/search.json?limit=5&fields=cover_i&q="
                  + urllib.parse.quote(str(q)))
    for doc in (j or {}).get("docs", []):
        if doc.get("cover_i"):
            u = "https://covers.openlibrary.org/b/id/%s-L.jpg?default=false" % doc["cover_i"]
            if url_is_image(u): return u
    return None

# ------------------------------------------------- wybor tematow dnia (bez modelu)
# Losowanie zasiane data: dwa przebiegi tego samego dnia daja to samo wydanie.
daynum = today.toordinal()
seed = date_file

def first_with_photo(cands, limit=6):
    """Schodzi po liscie kandydatow do pierwszego ze zdjeciem na Wikipedii.

    Tylko wersja angielska: tytuly w puli sa angielskie, a pelne przebiegi przez
    cztery jezyki z ponowieniami potrafily przy dlawieniu Wikipedii zamienic
    szybki test w kilkuminutowe czekanie. `limit` domyka to samo od gory.
    Brak zdjecia nie jest bledem, strona wyjdzie bez fotografii."""
    for name, wiki in cands[:limit]:
        u = wiki_image(wiki, langs=("en",))
        if u:
            return name, wiki, u
        print("NOPIC %s (%s), biore nastepnego" % (name, wiki))
        time.sleep(1)  # REST Wikipedii dlawi serie zapytan; bez tego falszywe "brak zdjecia"
    return (cands[0][0], cands[0][1], None) if cands else (None, None, None)

# Ludzi na Wikipedii prawie zawsze da sie znalezc ze zdjeciem, wiec przy osobie
# szukamy dluzej. Przy markach odwrotnie: wolne zdjecia marek kosmetycznych sa
# rzadkie (Clinique, MAC, Lancome maja artykul i zero fotografii), a wlasciwa
# marka jest dla Mai wazniejsza niz obrazek, wiec probujemy tylko trzech i
# godzimy sie na sekcje bez zdjecia.
person_name, person_wiki, person_img = first_with_photo(
    pools.pick_person(seen["osoby"], daynum, seed), limit=6)
brand_name, brand_wiki, brand_img = first_with_photo(
    pools.pick_brand(seen["beauty"], daynum, seed), limit=3)
cieka_cat, cieka_hint = pools.pick_ciekawostka_cat(seen["cieka_cats"], daynum, seed)
nauka_theme = pools.pick_nauka_theme(seen["nauka_themes"], seed)
ai_angle    = pools.pick_ai_angle(seen["ai_angles"], seed)
book_theme  = pools.pick_book_theme(seen["book_themes"], seed)
inn_tag, inn_hint = pools.pick_inn_variant(daynum)
banned_authors = (dedup.authors_from_seen(seen["books"]) + list(seen["authors"]))[-40:]

print("PICKED osoba=%s | marka=%s | ciekawostka=%s | nauka=%s | ai=%s | inn=%s | ksiazka=%s"
      % (person_name, brand_name, cieka_cat, nauka_theme[:28], ai_angle[:28], inn_tag, book_theme))
print("PICKED_IMAGES osoba=%s marka=%s" % (bool(person_img), bool(brand_img)))

# ---------------------------------------------------------------- prompt
SYSTEM = """You are the "Poranny Digest" agent — you write a daily morning newsletter in Polish for Maja Regula, founder of Owlsome Studio (a branding studio in Warsaw, Poland). She reads it on her phone with morning coffee.

Audience: Maja follows AI news casually. She knows OpenAI, Google, Anthropic, Meta, Apple, what a language model / ChatGPT is — do NOT explain these. No finance background. Never patronize.

You have web_search and web_fetch. Use them to research everything fresh. Today's date is %(date_pl)s. Images are loaded by Maja's BROWSER from direct URLs you provide — you do not download them.

RESEARCH BUDGET (hard): you have at most 8 searches and 8 fetches for the WHOLE digest. Plan them: ~1 search per news section, reserve fetches for the book's Polish-translation check. Images cost you NOTHING — they are resolved automatically after you answer; never spend searches or fetches on photos or covers. Never fetch a page when the search snippet already tells you enough. If the budget runs out, finish with what you have rather than skipping the JSON.

## ANTI-REPEAT (hard rules)
Do NOT repeat anything already used. Match by SUBSTANCE, not wording: if the freshest item in a section is the same underlying study, launch, deal or finding as something below, it counts as a repeat even if you phrase the headline differently. Reworded duplicates are the most common failure here, so a story like "protein tau and memory" that already ran must not come back under a new headline. When the top news in a section is just a re-report of something below, pick a genuinely different item or drop the section: a shorter digest is much better than a recycled one. A separate program checks your output against this history and REJECTS repeats, so a rephrased duplicate does not get through, it only wastes the section.
- News topics already used: %(topics)s
- Ciekawostki already used: %(ciekawostki)s
- Book authors already used (pick a DIFFERENT author): %(authors)s
- Book titles already used: %(books)s

## TODAY'S ASSIGNMENT (chosen for you, not negotiable)
Today's subjects and angles were selected by the system to keep the newsletter varied. Do not substitute your own picks. Write what is assigned:
- Twarz AI today: **%(person)s** (English Wikipedia title: "%(person_wiki)s")
- Beauty Brand today: **%(brand)s** (English Wikipedia title: "%(brand_wiki)s")
- Ciekawostka category today: **%(cieka_cat)s** (%(cieka_hint)s)
- Nauka angle today: **%(nauka_theme)s**
- AI angle today: **%(ai_angle)s**
- Book theme today: **%(book_theme)s**
- Section 6 today is "**%(inn_tag)s**": %(inn_hint)s

## Sections to produce
1. **ai** — 1-3 AI stories from the last 24h (`newer_than:1d`). Today's angle is **%(ai_angle)s**: make at least the first story fit that angle, the rest can be the day's biggest AI news. Lead with what happened, then needed context, then why it matters. 1-2 source links each.
2. **nauka** — 1-2 stories on today's assigned angle: **%(nauka_theme)s**. Prefer the last 7 days (real science does not break daily, and forcing a 24h window is what made this section recycle the same studies). Human results and replicated findings only. Skip supplement marketing and weak single studies. If nothing solid exists on the assigned angle, take the strongest evidence-based health story of the week instead, but do NOT fall back on Alzheimer's, tau protein or general "aging" stories, those have been run to death.
3. **osoba** (Twarz AI) — write about **%(person)s**, assigned above. Output wiki_title exactly as given. This may be a living founder, a historical pioneer, a critic of the field or an artist working with AI: write them as a person, whoever they are. If one of today's AI news items is about this same person, mention it in one clause and then go somewhere else entirely. Write 2-3 short paragraphs: lead with the single most surprising thing, explain ONE concrete contribution in plain language, and build it around a real ANECDOTE or quirk (a specific thing they did/said/believe) so it sticks. Prefer an anecdote over a quote; use a direct quote only if the exact words genuinely add something a paraphrase cannot. NOT a CV, skip dates/career lists. Output rola = 3-6 word tagline.
4. **ksiazka** — ONE popular-science book on today's assigned theme: **%(book_theme)s**. MUST have a Polish translation (verify on lubimyczytac.pl or empik.com) AND **must have been first published in the last 5 years (2021 or later)**. The author must NOT be on the "authors already used" list above. If you cannot confirm a Polish edition exists, pick a DIFFERENT book. The `title` field must be the POLISH title, never an English-only title (original in parentheses only if very different). Write the ONE idea/story that makes it worth reading, with a vivid hook (follow editorial rule). Output isbn13 = the ENGLISH original edition's ISBN-13 (digits only, null if unknown) PLUS orig_title (English original title) and author — the cover is fetched automatically from these.
5. **beauty** (Beauty Brand) — write about **%(brand)s**, assigned above. Output wiki_title exactly as given. Lead with the most surprising thing, wrap the origin in a short story, land on concrete visual-identity keywords (palette, packaging mood, photography, typography). Maja art-directs beauty brands for a living, so give her something she can use: a specific design decision, a material, a founder's stubborn choice. 2-3 tight paragraphs. Output styl = 3 keywords joined by " · ".
6. **inn** — today this section is "**%(inn_tag)s**". Write exactly that: %(inn_hint)s. Keep it inside branding, beauty/lifestyle, wellness, AI creativity & culture, social media, creative industry. 1-2 source links.
7. **ciekawostka** (Ciekawostka dnia) — ONE genuinely surprising standalone fact from today's assigned category: **%(cieka_cat)s** (%(cieka_hint)s). NOT tied to any news, it does not need to be timely. Pick something with a "no way, really?" flavour that Maja would immediately retell. Stay inside the assigned category, and do NOT overlap with today's other sections. It is not required to have a source; add one only if it helps. 2-4 sentences, one fact, land the surprise early. Output headline = a short intriguing title, and `temat` = the subject in 2-4 words (e.g. "etymologia slowa algorytm"), used for repeat checking.

## STYLE (all sections)
Polish, like a smart well-read friend — natural, not corporate, not AI-polished. Editorial rule for EVERY section: only the most interesting, memorable facts wrapped in a small story/hook; cut CVs, chronologies, lists of titles, dates unless the date is the point; lead with the most surprising thing. Maja's test: could she retell it to a friend in one sentence. NO em dashes anywhere — use commas or periods. Never use przełomowy / rewolucyjny / game-changer as hype. Body fields may use **bold** and [text](url) markdown links. 1-2 source links per news story.

## OUTPUT
Respond with EXACTLY ONE JSON object and NOTHING else (no prose before or after, no code fences). Shape:
{
  "date_pl": "%(date_pl)s",
  "date_file": "%(date_file)s",
  "ai": [{"headline":"...","body":"para\\n\\npara","sources":[["Name","https://..."]],"image_url":null}],
  "nauka": [{"headline":"...","body":"...","sources":[["Name","https://..."]],"image_url":null}],
  "osoba": {"name":"...","wiki_title":"Exact_Wikipedia_Title","rola":"...","body":"...","sources":[["Name","https://..."]]},
  "ksiazka": {"title":"Polski tytuł, Autor","body":"...","isbn13":"9780000000000","orig_title":"English Title","author":"Author Name"},
  "beauty": {"name":"...","wiki_title":"Exact_Wikipedia_Title","styl":"k · k · k","body":"..."},
  "inn": {"headline":"...","body":"...","sources":[["Name","https://..."]],"image_url":null},
  "ciekawostka": {"headline":"...","temat":"2-4 slowa","body":"...","sources":[]}
}
Images for osoba/beauty/ksiazka are resolved automatically from wiki_title/isbn13 — never spend searches or fetches on them. news image_url: only if you happened to see a direct image URL, else null. A quiet news day (1 item each) is fine — do not pad.""" % {
    "date_pl": date_pl, "date_file": date_file,
    "books": "; ".join(seen["books"][-30:]) or "(none)",
    "topics": "; ".join(seen["topics"][-80:]) or "(none)",
    "ciekawostki": "; ".join(seen["ciekawostki"][-40:]) or "(none)",
    "authors": "; ".join(banned_authors) or "(none)",
    "person": person_name, "person_wiki": person_wiki,
    "brand": brand_name, "brand_wiki": brand_wiki,
    "cieka_cat": cieka_cat, "cieka_hint": cieka_hint,
    "nauka_theme": nauka_theme, "ai_angle": ai_angle,
    "book_theme": book_theme, "inn_tag": inn_tag, "inn_hint": inn_hint,
}

# ---------------------------------------------------------------- API call
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
tools = [
    {"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES,
     "allowed_callers": ["direct"]},  # Haiku has no programmatic tool calling
    {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": MAX_FETCHES,
     "max_content_tokens": FETCH_TOKEN_CAP, "allowed_callers": ["direct"]},
]
# cache_control: on continuation rounds the system prompt + prior turns are read
# from prompt cache at 0.1x instead of being re-billed at full input price
system_blocks = [{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}]
messages = [{"role": "user", "content": "Wygeneruj dzisiejszy Poranny Digest jako JSON zgodnie z instrukcją."}]

cost = 0.0
searches_used = 0
def add_usage(u):
    global cost, searches_used
    stu = getattr(u, "server_tool_use", None)
    ws = getattr(stu, "web_search_requests", 0) or 0 if stu else 0
    searches_used += ws
    cost += (
        (getattr(u, "input_tokens", 0) or 0) * PRICE_IN
        + (getattr(u, "output_tokens", 0) or 0) * PRICE_OUT
        + (getattr(u, "cache_creation_input_tokens", 0) or 0) * PRICE_CACHE_W
        + (getattr(u, "cache_read_input_tokens", 0) or 0) * PRICE_CACHE_R
    ) / 1_000_000 + ws * 0.01

def add_usage_writer(u):
    global cost
    cost += (
        (getattr(u, "input_tokens", 0) or 0) * PRICE_IN_W
        + (getattr(u, "output_tokens", 0) or 0) * PRICE_OUT_W
    ) / 1_000_000

resp = None
container_id = None
for _ in range(MAX_ROUNDS):  # server-tool loop: re-send on pause_turn
    kwargs = dict(
        model=MODEL, max_tokens=RESEARCH_MAX_TOKENS,
        system=system_blocks, tools=tools, messages=messages,
    )
    if container_id:  # web_search/web_fetch run in a code-exec container; reuse it on continuation
        kwargs["container"] = container_id
    resp = client.messages.create(**kwargs)
    add_usage(resp.usage)
    if cost > COST_GUARD_USD:
        print("COST_GUARD_TRIPPED est=$%.2f — aborting instead of burning money" % cost)
        sys.exit(1)
    c = getattr(resp, "container", None)
    if c is not None:
        container_id = c.id
    if resp.stop_reason == "pause_turn":
        messages.append({"role": "assistant", "content": resp.content})
        continue
    break
print("RUN_COST_EST=$%.3f searches=%d stop_reason=%s" % (cost, searches_used, resp.stop_reason))
if resp.stop_reason == "max_tokens":
    # not fatal (salvage may still recover the object) but it means the tail was cut,
    # so raise RESEARCH_MAX_TOKENS rather than letting it become the daily normal
    print("WARN_RESEARCHER_TRUNCATED cap=%d — the last section may be missing" % RESEARCH_MAX_TOKENS)

if resp.stop_reason == "refusal":
    print("REFUSAL", getattr(resp, "stop_details", None)); sys.exit(1)

text = "".join(b.text for b in resp.content if b.type == "text").strip()
# Haiku leaks literal <cite index="..."> tags into its text. The unescaped quotes
# inside them break the JSON (2026-07-08: empty page shipped as success), and when
# the JSON survives they show up as visible junk on the page. Strip them first.
text = re.sub(r'</?\s*(cite|cyt)\b[^>]*>', '', text)
# tolerate stray prose / fences / trailing junk: decode the first valid JSON object
# that actually looks like a digest (a bare inner fragment must not pass)
DIGEST_KEYS = {"ai", "nauka", "osoba", "ksiazka", "beauty", "inn"}

def find_json_with_keys(t, keys):
    # strict=False: Haiku sometimes puts literal newlines/control chars inside
    # JSON strings (2026-07-18/19: two runs in a row died on this)
    dec = json.JSONDecoder(strict=False)
    pos = t.find("{")
    while pos != -1:
        try:
            cand, _ = dec.raw_decode(t, pos)
            if isinstance(cand, dict) and keys & set(cand):
                return cand
        except json.JSONDecodeError:
            pass
        pos = t.find("{", pos + 1)
    return None

def find_digest_json(t):
    return find_json_with_keys(t, DIGEST_KEYS)

c = find_digest_json(text)
if c is None:
    # salvage pass: the content exists but the JSON is malformed (unescaped quote,
    # truncated tail, stray junk). One no-tools call re-emits it as valid JSON.
    # A slightly repaired digest beats no digest.
    print("JSON_PARSE_FAILED — trying salvage pass; tail of response:\n" + text[-1500:])
    try:
        fix = client.messages.create(
            model=MODEL, max_tokens=RESEARCH_MAX_TOKENS,
            system="You repair malformed JSON. The user message contains a newsletter response whose JSON object is broken (unescaped quotes, control characters, truncation, surrounding prose). Output ONLY the corrected valid JSON object, preserving all content exactly. If the JSON is truncated, close it cleanly without inventing new content. No prose, no code fences.",
            messages=[{"role": "user", "content": text[-30000:]}],
        )
        add_usage(fix.usage)
        c = find_digest_json("".join(b.text for b in fix.content if b.type == "text"))
        if c is not None:
            print("SALVAGE_OK est_cost_now=$%.3f" % cost)
    except Exception as e:
        print("SALVAGE_CALL_FAILED %r" % (e,))
if not isinstance(c, dict):
    print("NO_DIGEST_JSON_IN_RESPONSE\nHEAD:\n" + text[:1200] + "\n...\nTAIL:\n" + text[-2500:]); sys.exit(1)
c.setdefault("date_pl", date_pl)
c.setdefault("date_file", date_file)

# ------------------------------------------------- bramka powtorek (deterministyczna)
# Prompt prosil o to od czerwca i nie dzialalo: w historii siedzi osiem wersji tej
# samej ciekawostki o etymologii slowa "algorytm" i trzy razy ten sam news o
# "slow aging". Model przeformulowuje naglowek i uznaje temat za nowy. Tu
# sprawdza to kod, wedlug pokrycia tokenow, dokladnie tak jak `strip_dashes`
# pilnuje polpauz zamiast wierzyc na slowo.
# Prog 0.6 wybrany na danych: na 204 historycznych naglowkach lapie 21 realnych
# powtorek i ZERO falszywych trafien. Przy 0.5 zaczyna sklejac rozne newsy, w
# ktorych padaja te same nazwy firm, wiec nie schodzic nizej dla newsow.
NEWS_THRESHOLD = 0.6
CIEKA_THRESHOLD = 0.5   # ciekawostki sa krotkie, a falszywy alarm kosztuje tu tylko przelosowanie

rejected = []   # (sekcja, opis, z czym kolidowalo) — sluzy tez za brief dla dogrywki
# Lista robocza: historia + to, co juz przeszlo DZIS. Trzymana osobno od
# seen["topics"], bo tam zapisujemy dopiero po udanym renderze. Dzieki temu dwa
# ujecia tej samej historii w jednym wydaniu tez sie nie przeslizgna, takze po
# dogrywce.
used_now = list(seen["topics"])

def _drop_repeats(items, label):
    keep = []
    for s in (items or []):
        h = s.get("headline", "")
        m = dedup.is_repeat(h, used_now, threshold=NEWS_THRESHOLD)
        if m:
            rejected.append((label, h, m))
            print("DEDUP_REJECT [%s] %r ~ %r" % (label, h[:70], m[:70]))
        else:
            keep.append(s)
            used_now.append(h)
    return keep

c["ai"] = _drop_repeats(c.get("ai"), "ai")
c["nauka"] = _drop_repeats(c.get("nauka"), "nauka")
if isinstance(c.get("inn"), dict):
    ih = c["inn"].get("headline", "")
    m = dedup.is_repeat(ih, used_now, threshold=NEWS_THRESHOLD)
    if m:
        rejected.append(("inn", ih, m))
        print("DEDUP_REJECT [inn] %r ~ %r" % (ih[:70], m[:70]))
        c["inn"] = None
    else:
        used_now.append(ih)

cw0 = c.get("ciekawostka")
if isinstance(cw0, dict):
    probe = (cw0.get("temat") or "") + " " + (cw0.get("headline") or "")
    m = dedup.is_repeat(probe, seen["ciekawostki"], threshold=CIEKA_THRESHOLD)
    if m:
        rejected.append(("ciekawostka", cw0.get("headline", ""), m))
        print("DEDUP_REJECT [ciekawostka] %r ~ %r" % (probe[:70], m[:70]))
        c["ciekawostka"] = None

k0d = c.get("ksiazka")
if isinstance(k0d, dict):
    key = dedup.canon_book(k0d.get("title"), k0d.get("author"))
    seen_keys = {dedup.canon_book(t) for t in seen["books"]}
    # Autor liczy sie jako powtorka, gdy jeden zapis zawiera sie w drugim:
    # "Harari" i "Yuval Noah Harari" to ten sam czlowiek, a wpisy w historii maja
    # rozna dlugosc.
    auth = dedup.tokens(k0d.get("author") or "", drop_generic=False)
    auth_clash = bool(auth) and any(
        auth <= (bt := dedup.tokens(a, drop_generic=False)) or (bt and bt <= auth)
        for a in banned_authors)
    if key in seen_keys or auth_clash:
        rejected.append(("ksiazka", k0d.get("title", ""), k0d.get("author", "")))
        print("DEDUP_REJECT [ksiazka] %r autor=%r" % (k0d.get("title", "")[:60], k0d.get("author")))
        c["ksiazka"] = None

# Jedna dogrywka, tylko dla tego, co wypadlo. Odpala sie wylacznie wtedy, gdy
# brak sekcji realnie zubozylby wydanie, wiec w wiekszosci dni nie kosztuje nic.
need = []
if not c.get("ai"):
    need.append("sekcja ai: potrzebne 1-2 CALKIEM INNE newsy AI z ostatnich 24-48h, kat: %s" % ai_angle)
if not c.get("nauka"):
    need.append("sekcja nauka: potrzebna 1 inna historia naukowa z ostatniego tygodnia, kat: %s" % nauka_theme)
if not c.get("ciekawostka"):
    need.append("sekcja ciekawostka: potrzebny INNY fakt z kategorii '%s' (%s), pole temat obowiazkowe" % (cieka_cat, cieka_hint))
if not c.get("ksiazka"):
    need.append("sekcja ksiazka: potrzebna INNA ksiazka (temat: %s), autor spoza listy uzytych, polskie wydanie potwierdzone, premiera 2021+" % book_theme)

if need:
    was = "; ".join("%s: %r bo powtarza %r" % (a, b[:60], (c_ or "")[:60]) for a, b, c_ in rejected)
    print("REPAIR_NEEDED %d sekcji" % len(need))
    try:
        rep_msgs = [{"role": "user", "content":
            "Odrzucone jako powtorki: %s\n\nUzupelnij: %s\n\nJuz uzyte naglowki (nie zblizaj sie do nich): %s"
            % (was, " | ".join(need), "; ".join(seen["topics"][-40:]))}]
        rep_sys = [{"type": "text", "text":
            "Uzupelniasz brakujace sekcje polskiego newslettera. Odrzucono je, bo powtarzaly tresc, ktora juz byla. "
            "Zwroc WYLACZNIE obiekt JSON zawierajacy tylko te klucze, o ktore prosi uzytkownik, w tym samym ksztalcie co reszta digestu "
            "(ai i nauka to listy obiektow z polami headline, body, sources; ciekawostka to obiekt z headline, temat, body; "
            "ksiazka to obiekt z title, body, isbn13, orig_title, author). Bez tekstu przed ani po. "
            "Absolutnie nie wracaj do odrzuconych tematow ani do niczego z nimi pokrewnego."}]
        rep, rep_container = None, None
        for _ in range(3):   # ta sama petla pause_turn co przy researchu: z web_search
            kw = dict(model=MODEL, max_tokens=6000, system=rep_sys, tools=tools, messages=rep_msgs)
            if rep_container: kw["container"] = rep_container
            rep = client.messages.create(**kw)
            add_usage(rep.usage)
            rc = getattr(rep, "container", None)
            if rc is not None: rep_container = rc.id
            if rep.stop_reason == "pause_turn":
                rep_msgs.append({"role": "assistant", "content": rep.content})
                continue
            break
        if cost > COST_GUARD_USD:
            print("COST_GUARD_TRIPPED est=$%.2f — aborting" % cost); sys.exit(1)
        # Wlasny zestaw kluczy: dogrywka czesto zwraca SAMA ciekawostke, ktorej nie
        # ma w DIGEST_KEYS, wiec zwykly parser digestu odrzucilby poprawna odpowiedz.
        rj = find_json_with_keys(
            re.sub(r'</?\s*(cite|cyt)\b[^>]*>', '',
                   "".join(b.text for b in rep.content if b.type == "text")),
            {"ai", "nauka", "ciekawostka", "ksiazka"})
        if rj:
            for key_ in ("ai", "nauka"):
                if not c.get(key_) and isinstance(rj.get(key_), list):
                    # dogrywka przechodzi przez te sama bramke: model potrafi
                    # podac "nowy" temat, ktory znowu jest tym samym
                    c[key_] = _drop_repeats(rj[key_], key_ + "/repair")
            for key_ in ("ciekawostka", "ksiazka"):
                if not c.get(key_) and isinstance(rj.get(key_), dict):
                    c[key_] = rj[key_]
            print("REPAIR_OK est=$%.3f" % cost)
        else:
            print("REPAIR_NO_JSON — publikujemy krotsze wydanie")
    except Exception as e:
        print("REPAIR_FAILED, publikujemy bez tych sekcji:", repr(e)[:200])

# Podmieniamy wybor modelu na wybor skryptu: to skrypt sprawdzil zdjecie, wiec
# to on ma ostatnie slowo co do tozsamosci bohatera i marki.
if isinstance(c.get("osoba"), dict):
    c["osoba"]["name"] = person_name          # nadpisujemy TWARDO: zdjecie juz sprawdzone pod ten wpis
    c["osoba"]["wiki_title"] = person_wiki
    if person_img: c["osoba"]["image_url"] = person_img
if isinstance(c.get("beauty"), dict):
    c["beauty"]["name"] = brand_name
    c["beauty"]["wiki_title"] = brand_wiki
    if brand_img: c["beauty"]["image_urls"] = [brand_img]

# ------------------------------------------------- writer pass (Sonnet, no tools)
# Haiku is a solid researcher but writes stiff, translated-from-English Polish and
# leaks em dashes / anglicisms. One cheap Sonnet call rewrites ONLY the human-text
# fields into natural Polish. It never sees URLs/images/sources, so it cannot corrupt
# links even if it hallucinates. On any failure we keep Haiku's text and ship anyway.
WRITER_SYSTEM = """Jesteś redaktorem "Porannego digestu", polskiego newslettera, który Maja czyta rano na telefonie przy kawie. Dostajesz surowe teksty sekcji (fakty zebrane przez researchera) i przepisujesz KAŻDY na żywy, naturalny polski. Zwracasz dokładnie tę samą strukturę JSON, tylko z lepszym tekstem.

To redakcja i przekład na ludzki polski, NIE research. Nie dodawaj faktów, nie zmyślaj liczb ani nazwisk. Pracuj z tym, co dostałeś.

GŁOS:
- Piszesz jak bystra, oczytana znajoma, która opowiada coś ciekawego, nie jak raport prasowy ani korpo-mail. Zdania jak w rozmowie.
- Każda sekcja MUSI mieć hak: zacznij od najbardziej zaskakującej, zapamiętywalnej rzeczy. Test: czy Maja opowie to znajomej jednym zdaniem. Wytnij CV, chronologie, listy tytułów i suchą rekapitulację.
- Więcej smaczku i ciekawostki, mniej sprawozdania. Konkret i obraz zamiast ogólników.
- RÓŻNICUJ sekcje między sobą. Nie każda ma zaczynać się tak samo: jedna może wejść od sceny, druga od liczby, trzecia od zdania kogoś konkretnego, czwarta od pytania. Jeśli po przeczytaniu całości widać jeden szablon powtórzony siedem razy, przepisz otwarcia. To samo dotyczy długości akapitów, nie rób siedmiu bliźniaczych bloków.

BEZBŁĘDNY POLSKI (to jest krytyczne, tu wcześniej leciały błędy):
- To ma być literacki, bezbłędny polski. Zero literówek, zero wymyślonych słów (nie "przewodziuje", "materństwa", "czteroolatek", "financować", "snobbistyczne"), zero rozjechanych wyrazów ze spacją w środku ("suweren ność", "wirus ować"). Przeczytaj każde zdanie i sprawdź, czy Polak naprawdę tak powie.
- Pisz WYŁĄCZNIE po polsku, alfabetem łacińskim. Nigdy nie wstawiaj słów w innym alfabecie (cyrylica itp.) ani przypadkowych obcych wtrąceń.
- Piszesz w trzeciej osobie, referujesz. Nigdy nie pisz w pierwszej osobie ("piłem", "uczy mnie"), to nie twoje wspomnienia.
- Bądź zwięzły: przepisujesz istniejący tekst, nie rozbudowujesz go. Nie wydłużaj sekcji ponad długość wejścia.
- ZERO angielskich słów, których Maja nie używa na co dzień. Tłumacz je: capital→kapitał, world models→modele świata, performance→skuteczność (albo wyniki), utilities→zwykła usługa, free tier→darmowy plan, add-on→dodatek, inclusivity→różnorodność, postpartum→poporodowy, agentic task→zadanie agentowe, chip-race→wyścig o chipy. Zostają tylko nazwy własne, które ona zna (OpenAI, ChatGPT, TikTok, Google, startup). Jeśli fraza brzmi jak przetłumaczona z angielskiego, napisz ją od zera po polsku. Żadnych "stake", "best-in-class", "game-changer", "zmienia grę", "robi pieniądze".
- Nie zostawiaj urwanych zdań ani takich, które nie mają sensu ("złoty będzie chciał"). Jeśli surowy tekst jest niejasny, napisz prościej to, co na pewno wiadomo, zamiast zgadywać.

WIERNOŚĆ FAKTOM (ważniejsza niż błyskotliwość):
- Nie dodawaj żadnych liczb, nazwisk, krajów, podatków ani przykładów, których NIE MA w surowym tekście. Zwłaszcza w sekcji nauka.

TWARDE ZAKAZY:
- NIGDY nie używaj myślnika ani półpauzy. Zamiast nich przecinek, kropka, dwukropek albo nawias. Zero tolerancji.
- Zero słów-wytrychów: przełomowy, rewolucyjny, game-changer, "zmienia grę".
- Nie zaczynaj sekcji od komplementu ani od powtórzenia nagłówka.

ZACHOWAJ dokładnie:
- Markdown: **pogrubienia** i [tekst](adres) zostają, nie ruszaj adresów w linkach.
- Akapity oddzielone podwójnym enterem.
- Tę samą liczbę elementów w listach i te same klucze co na wejściu.

Sekcja ciekawostka to zamykający smaczek dnia: napisz ją tak, żeby Maja od razu chciała ją komuś powtórzyć. Sam fakt, lekko, bez suchego tonu.

WYJŚCIE: odpowiedz samym obiektem JSON o tej samej strukturze co wejście. Bez znaczników code fence, bez żadnego tekstu przed ani po. Zakończ od razu po ostatnim zamykającym nawiasie."""

def _decode_digest_json(txt):
    txt = re.sub(r'</?\s*(cite|cyt)\b[^>]*>', '', txt)
    dec = json.JSONDecoder()
    pos = txt.find("{")
    while pos != -1:
        try:
            cand, _ = dec.raw_decode(txt, pos)
            if isinstance(cand, dict):
                return cand
        except json.JSONDecodeError:
            pass
        pos = txt.find("{", pos + 1)
    return None

# Build a text-only payload (no URLs/images/sources reach the writer).
tp = {}
tp["ai"] = [{"headline": s.get("headline",""), "body": s.get("body","")} for s in (c.get("ai") or [])]
tp["nauka"] = [{"headline": s.get("headline",""), "body": s.get("body","")} for s in (c.get("nauka") or [])]
if isinstance(c.get("osoba"), dict):
    tp["osoba"] = {k: c["osoba"].get(k,"") for k in ("name","rola","body")}
if isinstance(c.get("ksiazka"), dict):
    tp["ksiazka"] = {k: c["ksiazka"].get(k,"") for k in ("title","body")}
if isinstance(c.get("beauty"), dict):
    tp["beauty"] = {k: c["beauty"].get(k,"") for k in ("name","styl","body")}
if isinstance(c.get("inn"), dict):
    tp["inn"] = {k: c["inn"].get(k,"") for k in ("headline","body")}
if isinstance(c.get("ciekawostka"), dict):
    tp["ciekawostka"] = {k: c["ciekawostka"].get(k,"") for k in ("headline","body")}

try:
    w = client.messages.create(
        model=MODEL_WRITER, max_tokens=16000,   # generous headroom; truncation is treated as failure below
        system=[{"type": "text", "text": WRITER_SYSTEM}],
        messages=[{"role": "user", "content": json.dumps(tp, ensure_ascii=False)}],
    )
    add_usage_writer(w.usage)
    if cost > COST_GUARD_USD:
        print("COST_GUARD_TRIPPED est=$%.2f — aborting" % cost); sys.exit(1)
    wtext = "".join(b.text for b in w.content if b.type == "text").strip()
    print("WRITER_RAW stop=%s len=%d head=%r" % (w.stop_reason, len(wtext), wtext[:160]))
    # A truncated writer degrades badly (garbled words, wrong alphabets); never trust
    # a partial rewrite. Fall back to the researcher text instead, which the dash
    # killer still cleans. (2026-07-16: max_tokens truncation produced broken output.)
    if w.stop_reason == "max_tokens":
        raise ValueError("writer truncated (max_tokens) — discarding partial rewrite")
    wj = _decode_digest_json(wtext)
    if not isinstance(wj, dict):
        raise ValueError("writer returned no JSON")

    def _take(dst, src, keys):
        for k in keys:
            v = src.get(k)
            if isinstance(v, str) and v.strip():
                dst[k] = v
    for arr in ("ai", "nauka"):
        src, dst = wj.get(arr) or [], c.get(arr) or []
        for i in range(min(len(src), len(dst))):
            if isinstance(src[i], dict) and isinstance(dst[i], dict):
                _take(dst[i], src[i], ("headline", "body"))
    # `name` celowo NIE jest przepisywalne dla osoby i marki: bohatera wybral
    # skrypt i pod ten wybor sciagnal zdjecie, wiec redaktor nie moze go
    # przemianowac (w tescie potrafil zwrocic wlasna wersje nazwy).
    for sec, keys in (("osoba",("rola","body")), ("ksiazka",("title","body")),
                      ("beauty",("styl","body")), ("inn",("headline","body")),
                      ("ciekawostka",("headline","body"))):
        if isinstance(c.get(sec), dict) and isinstance(wj.get(sec), dict):
            _take(c[sec], wj[sec], keys)
    print("WRITER_OK model=%s est=$%.3f" % (MODEL_WRITER, cost))
except Exception as e:
    print("WRITER_FAILED, keeping researcher text:", repr(e)[:200])

# --------------------------------------- deterministic em/en dash killer (hard rule #1)
# Maja's #1 zero-tolerance rule: no — or – ever reaches the page. The writer is told
# to avoid them, this guarantees it regardless of what any model emits.
def strip_dashes(s):
    if not isinstance(s, str):
        return s
    s = re.sub(r'(\d)\s*[—–]\s*(\d)', r'\1-\2', s)   # number ranges: 8—10 -> 8-10
    s = re.sub(r'\s*[—–]\s*', ', ', s)                # everything else -> comma
    s = re.sub(r'\s+,', ',', s)
    s = re.sub(r',\s*,', ',', s)
    s = re.sub(r',\s*\.', '.', s)
    return s
def _walk(x):
    if isinstance(x, str):  return strip_dashes(x)
    if isinstance(x, list): return [_walk(i) for i in x]
    if isinstance(x, dict): return {k: _walk(v) for k, v in x.items()}
    return x
c = _walk(c)

# ------------------------------------------------- dociagniecie reszty obrazkow
# Zdjecia bohatera i marki sa juz rozwiazane PRZED generowaniem (patrz
# `first_with_photo` na gorze), tu zostaje tylko okladka ksiazki i sprzatanie
# martwych URL-i, ktore podal model. Funkcje sieciowe zdefiniowane wyzej.
o0, k0, b0 = c.get("osoba"), c.get("ksiazka"), c.get("beauty")
if o0 and not (o0.get("image_url") and url_is_image(o0["image_url"])):
    o0["image_url"] = wiki_image(o0.get("wiki_title") or o0.get("name"))
if k0 and not (k0.get("cover_url") and url_is_image(k0["cover_url"])):
    k0["cover_url"] = (cover_from_isbn(k0.get("isbn13"))
                       or cover_from_search("%s %s" % (k0.get("orig_title") or "", k0.get("author") or "")))
if b0:
    urls = [u for u in (b0.get("image_urls") or []) if url_is_image(u)]
    if not urls:
        w = wiki_image(b0.get("wiki_title") or b0.get("name"))
        urls = [w] if w else []
    b0["image_urls"] = urls
# drop dead model-provided news images too
for sec in (c.get("ai") or []) + (c.get("nauka") or []) + ([c["inn"]] if c.get("inn") else []):
    if sec.get("image_url") and not url_is_image(sec["image_url"]):
        sec["image_url"] = None
print("IMAGES: osoba=%s ksiazka=%s beauty=%s" % (
    bool(o0 and o0.get("image_url")), bool(k0 and k0.get("cover_url")),
    bool(b0 and b0.get("image_urls"))))

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

def src_host(u):
    try: return urllib.parse.urlsplit(u).netloc.replace("www.", "") or u
    except Exception: return u

def src_pair(item):
    # Model oddaje zrodla w roznych ksztaltach (para, dict, sam URL, plaska lista
    # stringow). Czego kod moze pilnowac sam, tego nie zostawiamy modelowi:
    # nieznany ksztalt = pominiete zrodlo, nigdy wywalony run po oplaconym researchu.
    if isinstance(item, str):
        u = item.strip()
        return (src_host(u), u) if u.startswith("http") else None
    if isinstance(item, dict):
        u = item.get("url") or item.get("href") or item.get("link")
        n = item.get("name") or item.get("title") or item.get("source")
        if isinstance(u, str) and u.startswith("http"):
            return (n if isinstance(n, str) and n.strip() else src_host(u), u)
        return None
    if isinstance(item, (list, tuple)):
        parts = [x for x in item if isinstance(x, str)]
        u = next((x for x in parts if x.startswith("http")), None)
        if not u: return None
        n = next((x for x in parts if not x.startswith("http") and x.strip()), None)
        return (n or src_host(u), u)
    return None

def sources(s):
    if isinstance(s, (str, dict)): s = [s]
    if not s or not isinstance(s, (list, tuple)): return ""
    pairs = [p for p in (src_pair(i) for i in s) if p]
    if not pairs: return ""
    links = " · ".join('<a href="%s">%s</a>' % (esc(u), esc(n)) for n, u in pairs)
    return '<p class="src">Źródła: %s</p>' % links

P = []
P.append('<header><div class="kicker">Poranny digest</div><h1>%s</h1></header>' % esc(c["date_pl"]))
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
    # plakietka rotuje razem z tematem (Inn / Archiwum / Kontrapunkt / ...), zeby
    # wydanie tez WYGLADALO inaczej, nie tylko czytalo sie inaczej
    P.append('<section><div class="tag">%s</div><h2>%s</h2>%s%s%s</section>' % (esc(inn_tag), esc(i["headline"]), img(i.get("image_url"), i["headline"]), md(i.get("body","")), sources(i.get("sources"))))
cw = c.get("ciekawostka")
if cw and cw.get("body"):
    P.append('<section><div class="tag">Ciekawostka dnia</div><h2>%s</h2>%s%s</section>' % (esc(cw.get("headline","")), md(cw.get("body","")), sources(cw.get("sources"))))

# Never ship a hollow page as success: header-only output means the model's
# answer was lost upstream — fail loudly so the workflow alert fires.
n_sections = len(P) - 1  # P[0] is the header
if n_sections < 3:
    print("TOO_FEW_SECTIONS n=%d — refusing to publish" % n_sections); sys.exit(1)

# "Twoje pieniądze" goes in AFTER the guard on purpose: it is generated locally and
# would always succeed, so counting it would let a page with no model content at all
# pass the hollow-page check. Rendered right under the header, above the news.
money_html, money_css = money.section()
if money_html:
    P.insert(1, money_html)

# "Waluty" follows the same contract as "Twoje pieniądze": local, deterministic,
# inserted after the hollow-page guard. Placed right under it, because both answer
# a money question and reading them together is the point.
fx_html, fx_css = fx.section()
if fx_html:
    P.insert(2 if money_html else 1, fx_html)

CSS = "*{box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;max-width:680px;margin:0 auto;padding:28px 20px 80px;color:#1d1d1f;line-height:1.62;font-size:17px;background:#fafaf8}header{margin:8px 0 28px}.kicker{text-transform:uppercase;letter-spacing:.14em;font-size:12px;color:#9b8d7a;font-weight:700}h1{font-size:30px;margin:.15em 0 0;font-weight:700}section{padding:26px 0;border-top:1px solid #ece8e1}.tag{display:inline-block;text-transform:uppercase;letter-spacing:.1em;font-size:11px;font-weight:700;color:#fff;background:#b59a7d;padding:3px 9px;border-radius:99px;margin-bottom:10px}h2{font-size:21px;margin:.1em 0 .45em;line-height:1.3}h3{font-size:17px;margin:1.1em 0 .3em}p{margin:.55em 0}a{color:#9a6f3f;text-decoration:underline;text-underline-offset:2px}.src{font-size:14px;color:#8a8278;margin-top:.7em}.styl{font-style:italic;color:#8a8278;margin-top:-.2em}.gallery{display:flex;flex-direction:column;gap:12px;margin:14px 0}img{max-width:100%;max-height:340px;width:auto;height:auto;border-radius:14px;display:block;background:#efece6;margin:14px auto}.gallery img{margin:0 auto}footer{margin-top:40px;font-size:13px;color:#b3a89a;text-align:center}"

if money_css:
    CSS += money_css

if fx_css:
    CSS += fx_css

html_doc = '<!doctype html><html lang="pl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>Poranny digest, %s</title><style>%s</style></head><body>%s<footer>Poranny digest · generowany automatycznie</footer></body></html>' % (esc(c["date_pl"]), CSS, "\n".join(P))

page = c["date_file"] + ".html"
open(os.path.join(ROOT, page), "w", encoding="utf-8").write(html_doc)
redirect = '<!doctype html><meta charset="utf-8"><meta name="robots" content="noindex"><meta http-equiv="refresh" content="0; url=%s"><title>Poranny digest</title><a href="%s">Poranny digest, %s</a>' % (page, page, esc(c["date_pl"]))
open(os.path.join(ROOT, "index.html"), "w", encoding="utf-8").write(redirect)

# ---------------------------------------------------------------- update seen.json
def norm(s): return re.sub(r"\s+"," ",(s or "")).strip().lower()
def remember(key, val):
    if not val: return
    if norm(val) not in [norm(x) for x in seen[key]]: seen[key].append(val)
remember("books", k.get("title") if k else None)
# Zapisujemy nazwe Z PULI, nie te, ktora wypisal model. Do 08.2026 do seen.json
# wpadaly warianty tego samego ("Lush" i "Lush (company)", "Tom Ford" i "Tom Ford
# Beauty") i pula wyczerpywala sie szybciej, niz naprawde byla zuzyta.
remember("beauty", brand_name if b else None)
remember("osoby", person_name if o else None)
# Autor ksiazki osobno: to on wracal (Pollan, Harari, Walker, Sapolsky), a
# porownanie samych tytulow tego nie widzialo, bo polskie wydania roznily sie
# podtytulem.
if k and k.get("author"): remember("authors", k.get("author"))
# Pamiec rotacji katow. Bez zapisu rotacja gubi sie i potrafi dac ten sam kat
# dwa dni z rzedu.
for key_, val_ in (("cieka_cats", cieka_cat), ("nauka_themes", nauka_theme),
                   ("ai_angles", ai_angle), ("book_themes", book_theme)):
    seen[key_].append(val_)
    seen[key_] = seen[key_][-60:]
for s in (c.get("ai") or []) + (c.get("nauka") or []): remember("topics", s.get("headline"))
if i: remember("topics", i.get("headline"))
if cw: remember("ciekawostki", ((cw.get("temat") or "") + " " + (cw.get("headline") or "")).strip())
seen["_note"] = "Persistent anti-repeat memory for Poranny Digest. Appended automatically each day by the GitHub Action."
json.dump(seen, open(seen_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

url = PAGES + page

# Write the Slack payload to a temp file; the workflow posts it after push.
slack = {
    "text": "<@ULYLZE1KQ> Poranny digest, %s → %s" % (date_pl, url),
    "blocks": [
        {"type": "section", "text": {"type": "mrkdwn",
            "text": "<@ULYLZE1KQ>\n*Poranny digest, %s*\nPełne wydanie z obrazkami:" % date_pl}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "☕ Otwórz digest"},
             "url": url, "style": "primary"}]},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": "koszt API tego wydania: ~$%.2f" % cost}]},
    ],
}
tmp = os.environ.get("RUNNER_TEMP", "/tmp")
open(os.path.join(tmp, "slack_payload.json"), "w", encoding="utf-8").write(json.dumps(slack, ensure_ascii=False))

print("PUBLISHED_URL=" + url)
