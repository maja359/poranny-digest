#!/usr/bin/env python3
"""Deterministyczny wykrywacz powtorek dla Porannego Digestu.

Do 08.2026 cala ochrona przed powtorkami byla instrukcja w prompcie: "nie
powtarzaj tego, co juz bylo, oto lista". Haiku tego nie umie. Dowod z
`seen.json`: ta sama ciekawostka o etymologii slowa "algorytm" poszla OSIEM
razy pod osmioma roznymi naglowkami, a "slow aging vs anti-aging" trzy razy.
Model przeformulowuje naglowek i sam siebie przekonuje, ze to nowy temat.

Ten modul sprawdza to w kodzie, PO wygenerowaniu, tak samo jak `strip_dashes`
pilnuje polpauz: zasady, ktore musza dzialac codziennie, nie moga zalezec od
dobrej woli modelu.

Metoda: normalizacja (male litery, bez ogonkow, bez interpunkcji, bez slow
funkcyjnych) -> zbior tokenow -> **wspolczynnik pokrycia** (czesc wspolna
podzielona przez MNIEJSZY zbior), nie Jaccard. Powod: przeformulowana powtorka
zwykle jest podzbiorem dluzszego oryginalu ("Algorytm ma prawie 1200 lat"
wobec "Slowo algorytm ma prawie 1200 lat. Ludzie nie pamietaja matematyka"),
a Jaccard karze za sama roznice dlugosci i takiej pary nie zlapie.
"""
import re
import unicodedata

# Slowa funkcyjne + kalki dziennikarskie, ktore nic nie mowia o TEMACIE.
# Gdyby zostaly, dwa zupelnie rozne newsy dzielilyby tokeny "nowy", "moze",
# "wedlug" i podbijaly sobie podobienstwo.
STOP = set("""
a aby albo ale ani az bardziej bardzo bez bo by byc byl byla bylo byly byc
ci co coraz czy czyli dla do dwa dwie gdy gdzie go i ich ile im inny iz ja
jak jakie jako je jednak jedna jeden jedno jego jej jest jesli juz kiedy
kto ktora ktore ktorego ktorej ktory ktorych ktorym lat lecz lub ma maja mam
mamy mial miala mimo moga moze mozna my na nad nam nas nasz nawet nic nie
niz no o od oraz po pod ponad poza przed przez przy raz sa sie sobie tak
takze tam te tego tej ten teraz tez to tu tych tym tylko u w we wiec wsrod
z za ze zeby juz oto ile czym czego bardzo wlasnie wciaz znow znowu dzis
dzisiaj wczoraj jutro nowy nowa nowe nowego nowa nowych nowym pierwszy
pierwsza pierwsze wedlug moze mozliwe naprawde chce chca dwoch trzech
the of and a to in for on is are with from that this it its as at by
""".split())

# Slowa, ktore w TYM newsletterze wystepuja tak czesto, ze same z siebie nie
# swiadcza o powtorce (kazdy news o AI zawiera "AI" i "model").
GENERIC = set("""
ai sztuczna sztucznej inteligencja inteligencji model modeli modele modelu
badanie badania badan naukowcy nauka firma firmy swiat swiata ludzie ludzi
rok roku lata latach procent proc mln mld miliard miliardow milion milionow
dolarow dolary euro branza branzy rynek rynku
""".split())


def fold(s):
    """male litery, bez polskich ogonkow i bez interpunkcji"""
    s = (s or "").lower().replace("ł", "l")  # l z kreska nie rozklada sie przez NFKD
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9\s]+", " ", s)


def stem(t):
    """Bardzo zgrubny stemmer polski: ucina koncowki fleksyjne.

    Nie chodzi o poprawnosc lingwistyczna, tylko o to, zeby "starzenie" i
    "starzenia" oraz "algorytm" i "algorytmu" wpadaly do jednego kubelka.
    Dzialamy na krotkich naglowkach, wiec fałszywe zlepki sa tanie, a
    nieuchwycona odmiana kosztuje przepuszczona powtorke.
    """
    for suf in ("iami", "ami", "ach", "om", "ow", "ie", "ia", "ie", "ego", "emu",
                "ych", "ymi", "im", "ym", "ej", "y", "a", "e", "u", "i", "o"):
        if len(t) - len(suf) >= 4 and t.endswith(suf):
            return t[: -len(suf)]
    return t


def tokens(s, drop_generic=True):
    out = set()
    for t in fold(s).split():
        if len(t) < 3 or t in STOP:
            continue
        if drop_generic and t in GENERIC:
            continue
        out.add(stem(t))
    return out


def overlap(a, b):
    """wspolczynnik pokrycia: |A n B| / min(|A|,|B|)"""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def is_repeat(text, seen_texts, threshold=0.6, min_shared=2):
    """Czy `text` powtarza cokolwiek z `seen_texts`.

    `min_shared` chroni przed falszywym trafieniem na bardzo krotkich
    naglowkach, gdzie jedno wspolne slowo daje pokrycie 1.0.
    Zwraca dopasowany wpis albo None, zeby dalo sie powiedziec modelowi,
    CO dokladnie odrzucamy (sama informacja "powtorka" mu nie pomaga).
    """
    a = tokens(text)
    if len(a) < min_shared:
        return None
    for s in seen_texts:
        b = tokens(s)
        if len(b) < min_shared:
            continue
        shared = a & b
        if len(shared) >= min_shared and overlap(a, b) >= threshold:
            return s
    return None


def canon_person(name):
    """klucz tozsamosci osoby: samo nazwisko-imie bez tytulow i nawiasow"""
    return " ".join(sorted(tokens(re.sub(r"\(.*?\)", "", name or ""), drop_generic=False)))


def canon_brand(name):
    """Klucz marki. "Lush" i "Lush (company)" oraz "Tom Ford" i "Tom Ford Beauty"
    trafily do seen.json jako osobne wpisy i pula wyczerpala sie szybciej, niz
    powinna. Tniemy nawiasy i slowa-ogony."""
    n = fold(re.sub(r"\(.*?\)", "", name or ""))
    n = re.sub(r"\b(beauty|cosmetics|company|companies|brand|group|paris|london|paryz)\b", " ", n)
    return " ".join(n.split())


def canon_book(title, author=""):
    """Klucz ksiazki = autor + pierwsze slowo tytulu. Ten sam tytul wracal pod
    roznymi polskimi wersjami ("Nexus. Krotka historia informacji..." kontra
    "Nexus, Krotka historia sieci informacyjnych..."), wiec porownanie calych
    napisow nic nie dawalo."""
    t = [w for w in fold(title or "").split() if w not in STOP]
    a = [w for w in fold(author or "").split() if len(w) > 2]
    head = t[0] if t else ""
    return (" ".join(sorted(a)) + "|" + stem(head)).strip("|")


def authors_from_seen(seen_books):
    """Wyciaga nazwiska autorow z historycznych wpisow (format bywal rozny:
    "Tytul, Autor" albo "Tytul - Autor"), zeby dalo sie zablokowac powrot tego
    samego autora. Pollan, Harari i Walker wrocili wlasnie tedy."""
    out = []
    for b in seen_books or []:
        parts = re.split(r"[,\-—–]| by ", b)
        if len(parts) > 1:
            tail = parts[-1].strip()
            if 3 <= len(tail) <= 40 and not tail[0].isdigit():
                out.append(tail)
    return out
