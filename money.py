#!/usr/bin/env python3
"""Sekcja "Twoje pieniądze": liczona deterministycznie, bez udziału modelu.

Zastąpiła sekcję "Rynek" (edukacja finansowa) 2026-08-02. Powód: Maja jej nie
czytała, a to co jest jej realnie potrzebne to nie lekcja o IPO, tylko odpowiedź
na jedno pytanie: dokupić teraz do IKE/IKZE czy nie.

Zasady projektowe (nie zmieniać bez powodu):
* Model NIE dotyka tej sekcji. Liczby pochodzą z Yahoo Finance i lecą prosto do
  HTML, więc nie ma jak ich zmyślić i nie kosztują ani jednego tokena.
* Sekcja domyślnie MILCZY. VWCE i IUSQ to praktycznie ten sam koszyk (globalne
  large/mid cap, korelacja ~1,0), a konto emerytalne ma horyzont 20 lat, więc
  dzienny ruch nie niesie informacji. Sygnał odzywa się dopiero przy realnym
  obsunięciu od szczytu albo przy niewykorzystanym limicie wpłat.
* Awaria źródła danych = brak sekcji, nigdy wywalony digest.
"""
import json, time, datetime, urllib.parse, urllib.request

YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/"
# PUŁAPKA (zweryfikowana 2026-08-02): Yahoo fingerprintuje User-Agenta. Pełny
# string Chrome'a bez pasującego TLS-a leci na 429, tak samo curl/x i
# python-requests/x. Uczciwy, własny UA przechodzi bez problemu. Nie podszywać się
# pod przeglądarkę, bo to psuje działanie zamiast pomagać.
UA = "poranny-digest/1.0 (github.com/maja359/poranny-digest)"

# chart=True trafia na wykres. IUSQ celowo NIE jest na wykresie: jego linia leży
# na linii VWCE, dwa razy ten sam koszyk to nie druga informacja, tylko szum.
INSTRUMENTS = [
    {"ticker": "VWCE.DE", "label": "Vanguard FTSE All-World", "note": "IKE",
     "cur": "€", "chart": True,  "color": "#9a6f3f", "signal": True},
    {"ticker": "IUSQ.DE", "label": "iShares MSCI ACWI",     "note": "IKZE",
     "cur": "€", "chart": False, "color": "#b59a7d", "signal": True},
    {"ticker": "^GSPC",   "label": "S&P 500",              "note": "tło",
     "cur": "",  "chart": True,  "color": "#ada396", "signal": False},
]

# Limity wpłat, rocznik: (IKE, IKZE dla działalności gospodarczej, 1,8x, Maja ma JDG).
# Zweryfikowane u źródła 2026-08-02 (analizy.pl, za komunikatem MRPiPS w Monitorze
# Polskim). Brak rocznika w tym słowniku = przypomnienie o limicie się nie pokazuje.
# Nigdy nie zgadywać tych kwot, dopisać dopiero po sprawdzeniu komunikatu.
LIMITS = {2026: (28260, 16956)}

DIP_WARN = -5.0    # % od szczytu: warto dorzucić
DIP_STRONG = -10.0 # % od szczytu: dorzuć ile limit pozwala


def _get(url):
    for attempt in (0, 1, 2):
        if attempt:
            time.sleep(2 * attempt)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception:
            continue
    return None


def _series(ticker, rng, interval):
    """Zwraca [(timestamp, close), ...] z pominięciem dziur w danych."""
    j = _get("%s%s?range=%s&interval=%s" % (YAHOO, urllib.parse.quote(ticker), rng, interval))
    try:
        res = j["chart"]["result"][0]
        ts = res["timestamp"]
        cl = res["indicators"]["quote"][0]["close"]
    except Exception:
        return []
    return [(t, c) for t, c in zip(ts, cl) if c is not None]


def _quote(inst):
    """Cena, zmiana dzienna, YTD, odległość od historycznego szczytu, seria 12M."""
    hist = _series(inst["ticker"], "1y", "1d")
    if len(hist) < 30:
        return None
    last = hist[-1][1]
    prev = hist[-2][1]

    year = datetime.date.today().year
    ytd = None
    for t, c in hist:
        if datetime.datetime.utcfromtimestamp(t).year == year:
            ytd = (last / c - 1) * 100
            break

    # Szczyt liczony z pełnej historii tygodniowej. To jedyna liczba, która
    # odpowiada na pytanie "dokupić czy nie", więc nie zastępujemy jej 52-tygodniowym
    # maksimum z meta (po dłuższej bessie 52w high kłamie w drugą stronę).
    full = _series(inst["ticker"], "max", "1wk")
    peak = max([c for _, c in full] + [c for _, c in hist])

    return {
        **inst,
        "last": last,
        "d1": (last / prev - 1) * 100,
        "ytd": ytd,
        "from_peak": (last / peak - 1) * 100,
        "hist": hist,
    }


def _svg(quotes):
    """Wykres 12M, każda seria zrebasowana do 100 w punkcie startowym.

    Rebase zamiast cen absolutnych, bo instrumenty są w różnych walutach i rzędach
    wielkości. Oś X liczona z timestampów, nie z indeksu, bo Xetra i NYSE mają różne
    kalendarze sesji i indeksy by się rozjechały.
    """
    plot = [q for q in quotes if q["chart"] and len(q["hist"]) > 30]
    if not plot:
        return ""
    W, H, PAD = 640, 180, 10
    t0 = min(q["hist"][0][0] for q in plot)
    t1 = max(q["hist"][-1][0] for q in plot)
    norm = [[(t, c / q["hist"][0][1] * 100) for t, c in q["hist"]] for q in plot]
    lo = min(v for s in norm for _, v in s)
    hi = max(v for s in norm for _, v in s)
    span = (hi - lo) or 1.0

    def xy(t, v):
        x = PAD + (t - t0) / max(t1 - t0, 1) * (W - 2 * PAD)
        y = PAD + (hi - v) / span * (H - 2 * PAD)
        return "%.1f,%.1f" % (x, y)

    paths = []
    for q, s in zip(plot, norm):
        pts = " ".join(xy(t, v) for t, v in s)
        paths.append('<polyline points="%s" fill="none" stroke="%s" stroke-width="2" '
                     'stroke-linejoin="round" stroke-linecap="round"/>' % (pts, q["color"]))
    legend = " · ".join('<span style="color:%s">■</span> %s' % (q["color"], _esc(q["label"])) for q in plot)
    return ('<div class="chart"><svg viewBox="0 0 %d %d" '
            'role="img" aria-label="Wykres 12 miesięcy">%s</svg>'
            '<p class="chart-legend">%s · 12 miesięcy, start = 100</p></div>'
            % (W, H, "".join(paths), legend))


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pct(v):
    if v is None:
        return "brak danych"
    return ("+" if v > 0 else "") + ("%.1f%%" % v).replace(".", ",")


def _num(v, cur):
    s = ("%.2f" % v).replace(".", ",")
    return (s + " " + cur).strip() if cur else s


def _signal(quotes):
    """Domyślnie cisza. Odzywa się tylko wtedy, gdy jest o czym mówić."""
    tracked = [q for q in quotes if q["signal"]]
    if not tracked:
        return ""
    worst = min(q["from_peak"] for q in tracked)
    if worst <= DIP_STRONG:
        line = ("🔴 <strong>Mocny dołek.</strong> Twoje ETF-y są %s poniżej szczytu. "
                "Historycznie to są te momenty, w których dokupienie robi różnicę, "
                "a nie te, w których wszyscy chcą kupować. Dorzuć ile limit pozwala."
                % _pct(abs(worst)).lstrip("+"))
    elif worst <= DIP_WARN:
        line = ("🟡 <strong>Dołek.</strong> %s poniżej szczytu. Nic dramatycznego, ale jeśli "
                "masz wolną gotówkę i niewykorzystany limit, to lepszy moment na wpłatę "
                "niż tydzień temu." % _pct(abs(worst)).lstrip("+"))
    else:
        line = ("Blisko szczytu, %s. Nic się nie dzieje, trzymaj plan wpłat i nie klikaj nic."
                % _pct(worst))

    limits = LIMITS.get(datetime.date.today().year)
    if limits and datetime.date.today().month >= 10:
        line += ('<br><span class="limit">Końcówka roku: limit wpłat na %d to %s zł na IKE '
                 'i %s zł na IKZE przy działalności. Niewykorzystany limit przepada z 31 grudnia.</span>'
                 % (datetime.date.today().year,
                    format(limits[0], ",d").replace(",", " "),
                    format(limits[1], ",d").replace(",", " ")))
    return '<p class="signal">%s</p>' % line


CSS = (".money-grid{display:flex;flex-wrap:wrap;gap:10px;margin:14px 0}"
       ".money-cell{flex:1 1 150px;background:#fff;border:1px solid #ece8e1;border-radius:12px;padding:11px 13px}"
       ".money-cell .nm{font-size:13px;color:#8a8278;line-height:1.35}"
       ".money-cell .nm b{color:#b59a7d;font-size:11px;letter-spacing:.08em;text-transform:uppercase}"
       ".money-cell .px{font-size:20px;font-weight:700;margin:.25em 0 .1em}"
       ".money-cell .mv{font-size:13px;color:#8a8278}"
       ".up{color:#3f7d52}.down{color:#a8452f}"
       ".chart svg{width:100%;height:auto;display:block;margin:6px 0 2px}"
       ".chart-legend{font-size:12px;color:#8a8278;margin:.2em 0 0}"
       ".signal{background:#f4f1ea;border-radius:12px;padding:12px 14px;font-size:15px;margin:12px 0 0}"
       ".limit{display:inline-block;margin-top:.5em;font-size:13px;color:#8a8278}")


def section():
    """Zwraca (html, css) albo (None, None) gdy danych nie ma. Nigdy nie rzuca."""
    try:
        quotes = [q for q in (_quote(i) for i in INSTRUMENTS) if q]
        if not quotes:
            print("MONEY: brak danych, sekcja pominięta")
            return None, None

        cells = []
        for q in quotes:
            cls = "up" if q["d1"] >= 0 else "down"
            cells.append(
                '<div class="money-cell"><div class="nm"><b>%s</b><br>%s</div>'
                '<div class="px">%s</div>'
                '<div class="mv"><span class="%s">%s</span> dziś · %s w tym roku<br>'
                'od szczytu: %s</div></div>'
                % (_esc(q["note"]), _esc(q["label"]), _num(q["last"], q["cur"]), cls,
                   _pct(q["d1"]), _pct(q["ytd"]), _pct(q["from_peak"])))

        html = ('<section><div class="tag">Twoje pieniądze</div>'
                '<div class="money-grid">%s</div>%s%s</section>'
                % ("".join(cells), _svg(quotes), _signal(quotes)))
        print("MONEY: ok, %d instrumentów, od szczytu %s"
              % (len(quotes), ", ".join("%s %.1f%%" % (q["ticker"], q["from_peak"]) for q in quotes)))
        return html, CSS
    except Exception as e:
        print("MONEY_FAILED %r , sekcja pominięta, digest leci dalej" % e)
        return None, None

if __name__ == "__main__":
    h, _ = section()
    print(h or "(brak sekcji)")
