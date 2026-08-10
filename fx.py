#!/usr/bin/env python3
"""Sekcja "Waluty": kiedy wymieniać EUR i USD na złotówki.

Maja fakturuje klientów w euro i dolarach, a żyje i płaci podatki w złotówkach,
więc jedyne pytanie brzmi: wymieniać teraz czy poczekać. Sekcja odpowiada na nie
i na nic więcej.

Zasady projektowe (nie zmieniać bez powodu):
* Model NIE dotyka tej sekcji. Liczby idą prosto z NBP do HTML, więc nie ma jak
  ich zmyślić i nie kosztują ani jednego tokena. To ta sama umowa co w money.py.
* Sygnał liczony z POZYCJI W ROCZNYM ZAKRESIE, nie ze zmiany dziennej. Dzienne
  0,3% to szum, a pytanie "wymieniać?" ma sens tylko względem tego, co dało się
  dostać przez ostatni rok.
* Kierunek jest ODWROTNY niż przy ETF-ach. Tam dołek = okazja do kupna, tu wysoki
  kurs = okazja do sprzedaży waluty. Nie kopiować progów z money.py.
* Sekcja domyślnie MILCZY. Odzywa się dopiero w górnych 20% (wymieniaj) albo
  w dolnych 20% (poczekaj, jeśli możesz) rocznego zakresu.
* Awaria źródła danych = brak sekcji, nigdy wywalony digest.
"""
import json, time, datetime, urllib.request

# NBP API, tabela A (kursy średnie). Publiczne, bez klucza, bez limitów, bez
# fingerprintowania User-Agenta (w odróżnieniu od Yahoo, patrz money.py).
# Tabela A wychodzi w dni robocze ~12:00, więc rano digest widzi kurs z wczoraj.
NBP = "https://api.nbp.pl/api/exchangerates/rates/a/%s/%s/%s/?format=json"

# Kolory muszą się ROZRÓŻNIAĆ na wykresie. Paleta digestu jest ciepła i brązowa,
# więc dwa odcienie brązu (jak w money.py) zlewają się przy dwóch splątanych liniach.
# Dolar dostaje chłodny kontrapunkt, sprawdzony na renderze.
CURRENCIES = [
    {"code": "eur", "label": "Euro",  "sym": "€", "color": "#9a6f3f"},
    {"code": "usd", "label": "Dolar", "sym": "$", "color": "#5f7d8a"},
]

# Percentyl w rocznym zakresie: powyżej HIGH wymieniaj, poniżej LOW poczekaj.
PCT_HIGH = 80.0
PCT_LOW = 20.0

# Kwota referencyjna w sygnale. Rząd wielkości typowej transzy od klienta,
# żeby różnica kursowa była podana w złotówkach, a nie w procentach.
REF_AMOUNT = 10000


def _get(url):
    for attempt in (0, 1, 2):
        if attempt:
            time.sleep(2 * attempt)
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.load(r)
        except Exception:
            continue
    return None


def _series(code):
    """Kursy średnie z ostatnich 12 miesięcy jako [(data, kurs), ...].

    NBP tnie zapytanie na maks. 367 dni, ale i tak dzielimy na dwa okna, bo przy
    pełnym roku API bywa kapryśne o dzień graniczny. Puste okno = pusta seria,
    nigdy wyjątek.
    """
    today = datetime.date.today()
    start = today - datetime.timedelta(days=365)
    mid = start + datetime.timedelta(days=180)
    out = []
    for a, b in ((start, mid), (mid + datetime.timedelta(days=1), today)):
        j = _get(NBP % (code, a.isoformat(), b.isoformat()))
        try:
            out += [(r["effectiveDate"], r["mid"]) for r in j["rates"]]
        except Exception:
            continue
    return out


def _quote(cur):
    hist = _series(cur["code"])
    if len(hist) < 60:
        return None
    vals = [v for _, v in hist]
    last = vals[-1]
    prev = vals[-2]
    lo, hi = min(vals), max(vals)
    avg = sum(vals) / len(vals)
    # Percentyl: ile procent notowań z roku było niżej niż dzisiejsze.
    pct = 100.0 * sum(1 for v in vals if v < last) / len(vals)
    return {
        **cur,
        "last": last,
        "d1": (last / prev - 1) * 100,
        "lo": lo, "hi": hi, "avg": avg, "pct": pct,
        "vs_avg": (last - avg) * REF_AMOUNT,
        "date": hist[-1][0],
        "hist": hist,
    }


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _pct(v):
    return ("+" if v > 0 else "") + ("%.2f%%" % v).replace(".", ",")


def _rate(v):
    return ("%.4f" % v).replace(".", ",")


def _pl_date(iso):
    try:
        d = datetime.date.fromisoformat(iso)
        return "%d.%02d" % (d.day, d.month)
    except Exception:
        return _esc(iso)


def _zl(v):
    return format(int(round(v)), ",d").replace(",", " ") + " zł"


def _svg(quotes):
    """Wykres 12M, obie waluty zrebasowane do 100.

    Rebase, bo 4,30 i 3,73 na jednej osi zamieniłyby wykres w dwie płaskie kreski
    daleko od siebie. Oś X z dat, nie z indeksu, bo NBP publikuje tylko w dni
    robocze i święta w PL nie pokrywają się z liczbą notowań każdej waluty.
    """
    plot = [q for q in quotes if len(q["hist"]) > 60]
    if not plot:
        return ""
    W, H, PAD = 640, 180, 10

    def ord_(d):
        return datetime.date.fromisoformat(d).toordinal()

    t0 = min(ord_(q["hist"][0][0]) for q in plot)
    t1 = max(ord_(q["hist"][-1][0]) for q in plot)
    norm = [[(ord_(d), v / q["hist"][0][1] * 100) for d, v in q["hist"]] for q in plot]
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
    legend = " · ".join('<span style="color:%s">■</span> %s do złotego'
                        % (q["color"], _esc(q["label"])) for q in plot)
    return ('<div class="chart"><svg viewBox="0 0 %d %d" role="img" '
            'aria-label="Kursy walut, 12 miesięcy">%s</svg>'
            '<p class="chart-legend">%s · 12 miesięcy, start = 100. Wyżej = złoty słabszy, '
            'czyli lepiej dla ciebie.</p></div>' % (W, H, "".join(paths), legend))


def _signal(quotes):
    """Domyślnie cisza. Odzywa się tylko na skraju rocznego zakresu."""
    if not quotes:
        return ""
    best = max(quotes, key=lambda q: q["pct"])
    worst = min(quotes, key=lambda q: q["pct"])

    if best["pct"] >= PCT_HIGH:
        # Kwota liczona ZAWSZE per waluta. Jedna liczba na dwie waluty myli, bo
        # euro i dolar rozjeżdżają się względem złotego o kilkaset złotych na transzy.
        hot = [q for q in quotes if q["pct"] >= PCT_HIGH]
        gains = ", ".join("na 10 000 %s jakieś %s więcej" % (q["sym"], _zl(q["vs_avg"]))
                          for q in hot)
        if len(hot) > 1:
            head = "%s stoją wyżej niż przez większość roku" % " i ".join(q["label"].lower() for q in hot)
        else:
            head = "%s stoi wyżej niż przez %d%% ostatniego roku" % (hot[0]["label"].lower(), int(hot[0]["pct"]))
        line = ("🟢 <strong>Dobry moment na wymianę.</strong> %s. Licząc od średniej z roku: %s. "
                "Jeśli masz nadwyżkę na koncie walutowym, to jest dzień, żeby ją ruszyć."
                % (head.capitalize(), gains))
    elif worst["pct"] <= PCT_LOW:
        line = ("🔴 <strong>Słaby moment.</strong> %s jest niżej niż przez %d%% ostatniego roku. "
                "Wymieniaj tylko tyle, ile realnie potrzebujesz na koszty i podatki, resztę "
                "zostaw na koncie walutowym."
                % (worst["label"], int(100 - worst["pct"])))
    else:
        line = ("Kursy w środku rocznego zakresu. Nic się nie dzieje, wymieniaj tyle, "
                "ile potrzebujesz na bieżąco.")
    return '<p class="signal">%s</p>' % line


# Współdzielone klasy powtórzone celowo z money.py: gdyby tamta sekcja padła na
# Yahoo, jej CSS nie trafia na stronę i waluty zostałyby bez stylu. Duplikat reguł
# w CSS jest nieszkodliwy, brak stylu nie.
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
       ".fx-note{font-size:12px;color:#b3a89a;margin:.6em 0 0}")


def section():
    """Zwraca (html, css) albo (None, None) gdy danych nie ma. Nigdy nie rzuca.

    Reużywa klas CSS z money.py (.money-grid, .money-cell, .chart, .signal),
    żeby obie sekcje pieniężne wyglądały jak jedna rodzina. Dokłada tylko .fx-note.
    """
    try:
        quotes = [q for q in (_quote(c) for c in CURRENCIES) if q]
        if not quotes:
            print("FX: brak danych, sekcja pominięta")
            return None, None

        cells = []
        for q in quotes:
            cls = "up" if q["d1"] >= 0 else "down"
            cells.append(
                '<div class="money-cell"><div class="nm"><b>%s / PLN</b><br>%s do złotego</div>'
                '<div class="px">%s</div>'
                '<div class="mv"><span class="%s">%s</span> dzień do dnia<br>'
                'rok: %s do %s<br>wyżej niż przez %d%% roku</div></div>'
                % (q["code"].upper(), _esc(q["label"]), _rate(q["last"]), cls,
                   _pct(q["d1"]), _rate(q["lo"]), _rate(q["hi"]), int(q["pct"])))

        html = ('<section><div class="tag">Waluty</div>'
                '<div class="money-grid">%s</div>%s%s'
                '<p class="fx-note">Kursy średnie NBP z %s. W kantorze albo w Revolucie '
                'dostaniesz o 0,3 do 1,5%% mniej, i ten spread bywa większy niż to, '
                'co wygrasz czekając tydzień.</p></section>'
                % ("".join(cells), _svg(quotes), _signal(quotes), _pl_date(quotes[0]["date"])))
        print("FX: ok, %s" % ", ".join("%s %.4f p%d" % (q["code"].upper(), q["last"], q["pct"])
                                       for q in quotes))
        return html, CSS
    except Exception as e:
        print("FX_FAILED %r , sekcja pominięta, digest leci dalej" % e)
        return None, None


if __name__ == "__main__":
    h, _ = section()
    print(h or "(brak sekcji)")
