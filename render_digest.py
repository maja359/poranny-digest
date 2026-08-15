"""Render + publikacja wydania, wyciagniete z generate_digest.py.

Powod rozdzialu (15.08.2026): przez dwa dni run padal TUTAJ, juz po oplaconym
researchu i pisaniu, wiec dzien kosztowal ~$0.9 i nie dawal strony. Teraz
`generate_digest.py` zrzuca gotowa tresc do snapshotu, zanim wejdzie w render,
a `resume_digest.py` potrafi z tego snapshotu dokonczyc wydanie za zero dolarow.
Oba wolaja te sama funkcje `render()`, wiec nie ma dwoch wersji renderu, ktore
moglyby sie rozjechac.

`ctx` to dokladnie to, co zapisuje `snapshot.dump()`: tresc plus rzeczy, ktore
wybral skrypt przed generowaniem (nazwa marki i osoby z puli, katy rotacji) i
ktorych nie da sie odtworzyc z samej tresci.
"""

import os, sys, json, re, urllib.parse

import money
import fx


def render(ctx, seen, seen_path):
    """Sklada strone, zapisuje ja, dopisuje seen.json i pisze payload na Slacka.

    Zwraca URL opublikowanego wydania. Wyjscie z kodem 1 (za malo sekcji) zostaje
    jak bylo: pusta strona nigdy nie ma isc jako sukces.
    """
    c          = ctx["c"]
    inn_tag    = ctx["inn_tag"]
    date_pl    = ctx["date_pl"]
    brand_name = ctx.get("brand_name")
    person_name= ctx.get("person_name")
    cieka_cat  = ctx.get("cieka_cat")
    nauka_theme= ctx.get("nauka_theme")
    ai_angle   = ctx.get("ai_angle")
    book_theme = ctx.get("book_theme")
    cost       = ctx.get("cost", 0.0)
    ROOT       = ctx["ROOT"]
    PAGES      = ctx["PAGES"]

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

    return url
