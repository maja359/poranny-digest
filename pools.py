#!/usr/bin/env python3
"""Pule tematow i rotacja katow dla Porannego Digestu.

Do 08.2026 to model wybieral bohatera dnia z krotkiej listy wklejonej w prompt,
z instrukcja "wybierz kogos, kogo jeszcze nie bylo". Dwa skutki, oba w danych:
pula osob miala 17 nazwisk i 16 bylo juz zuzytych (czyli za dzien zaczelyby sie
powtorki), a pula marek 18 pozycji przy 26 wpisach w historii. Do tego kazda
sekcja miala codziennie ten sam kat, wiec "nauka" byla przez dwa miesiace
wylacznie alzheimerem i dlugowiecznoscia i studnia po prostu wyschla.

Tutaj wybor robi SKRYPT, deterministycznie i za zero tokenow, dokladnie tak jak
`money.py` liczy ETF-y bez udzialu modelu. Model dostaje gotowy temat i ma o nim
napisac, a nie go szukac. Zasada: czego kod moze pilnowac sam, tego nie
zostawiamy modelowi.

Wybor jest losowy, ale ZASIANY data, wiec dwa przebiegi tego samego dnia
(cron + reczny dispatch) daja ten sam wynik i nie robia dwoch roznych wydan.
"""
import random

# ---------------------------------------------------------------- ludzie
# Pogrupowane w warstwy nie dla porzadku, tylko po to, zeby rotacja warstw
# gwarantowala rozstrzal: bez tego los potrafi dac tydzien samych prezesow.
# (nazwa wyswietlana, dokladny tytul artykulu na angielskiej Wikipedii)
PEOPLE = {
    "labs": [
        ("Sam Altman", "Sam Altman"),
        ("Dario Amodei", "Dario Amodei"),
        ("Demis Hassabis", "Demis Hassabis"),
        ("Ilya Sutskever", "Ilya Sutskever"),
        ("Mira Murati", "Mira Murati"),
        ("Mustafa Suleyman", "Mustafa Suleyman"),
        ("Arthur Mensch", "Arthur Mensch"),
        ("Aidan Gomez", "Aidan Gomez"),
        ("Alexandr Wang", "Alexandr Wang"),
        ("Emad Mostaque", "Emad Mostaque"),
    ],
    "pionierzy": [
        ("Alan Turing", "Alan Turing"),
        ("John McCarthy", "John McCarthy (computer scientist)"),
        ("Marvin Minsky", "Marvin Minsky"),
        ("Claude Shannon", "Claude Shannon"),
        ("Norbert Wiener", "Norbert Wiener"),
        ("Frank Rosenblatt", "Frank Rosenblatt"),
        ("Herbert Simon", "Herbert A. Simon"),
        ("Joseph Weizenbaum", "Joseph Weizenbaum"),
        ("Ada Lovelace", "Ada Lovelace"),
        ("Grace Hopper", "Grace Hopper"),
        ("Karen Spärck Jones", "Karen Spärck Jones"),
        ("Seymour Papert", "Seymour Papert"),
        ("Douglas Hofstadter", "Douglas Hofstadter"),
        ("Judea Pearl", "Judea Pearl"),
        ("Jürgen Schmidhuber", "Jürgen Schmidhuber"),
    ],
    "badacze": [
        ("Geoffrey Hinton", "Geoffrey Hinton"),
        ("Yoshua Bengio", "Yoshua Bengio"),
        ("Yann LeCun", "Yann LeCun"),
        ("Fei-Fei Li", "Fei-Fei Li"),
        ("Andrew Ng", "Andrew Ng"),
        ("Andrej Karpathy", "Andrej Karpathy"),
        ("Richard Sutton", "Richard S. Sutton"),
        ("Shane Legg", "Shane Legg"),
        ("Daphne Koller", "Daphne Koller"),
        ("Anima Anandkumar", "Anima Anandkumar"),
        ("Sebastian Thrun", "Sebastian Thrun"),
        ("Peter Norvig", "Peter Norvig"),
    ],
    "krytycy": [
        ("Timnit Gebru", "Timnit Gebru"),
        ("Joy Buolamwini", "Joy Buolamwini"),
        ("Kate Crawford", "Kate Crawford"),
        ("Meredith Whittaker", "Meredith Whittaker"),
        ("Margaret Mitchell", "Margaret Mitchell (scientist)"),
        ("Emily M. Bender", "Emily M. Bender"),
        ("Gary Marcus", "Gary Marcus"),
        ("Melanie Mitchell", "Melanie Mitchell"),
        ("Stuart Russell", "Stuart J. Russell"),
        ("Nick Bostrom", "Nick Bostrom"),
        ("Max Tegmark", "Max Tegmark"),
        ("Eliezer Yudkowsky", "Eliezer Yudkowsky"),
        ("Cathy O'Neil", "Cathy O'Neil"),
        ("Safiya Noble", "Safiya Noble"),
        ("Shoshana Zuboff", "Shoshana Zuboff"),
    ],
    "kreatywni": [
        ("Refik Anadol", "Refik Anadol"),
        ("Holly Herndon", "Holly Herndon"),
        ("Mario Klingemann", "Mario Klingemann"),
        ("Sougwen Chung", "Sougwen Chung"),
        ("Trevor Paglen", "Trevor Paglen"),
        ("Harold Cohen", "Harold Cohen (artist)"),
        ("Vera Molnár", "Vera Molnár"),
        ("Casey Reas", "Casey Reas"),
        ("Ian Goodfellow", "Ian Goodfellow"),
    ],
    "biznes": [
        ("Jensen Huang", "Jensen Huang"),
        ("Lisa Su", "Lisa Su"),
        ("Morris Chang", "Morris Chang"),
        ("Masayoshi Son", "Masayoshi Son"),
        ("Reid Hoffman", "Reid Hoffman"),
        ("Kai-Fu Lee", "Kai-Fu Lee"),
        ("Robin Li", "Robin Li"),
        ("Satya Nadella", "Satya Nadella"),
        ("Sundar Pichai", "Sundar Pichai"),
        ("Marc Benioff", "Marc Benioff"),
    ],
}
PEOPLE_TIERS = ["labs", "pionierzy", "badacze", "krytycy", "kreatywni", "biznes"]

# ---------------------------------------------------------------- marki beauty
# Stara pula miala 18 pozycji, bo byla recznie zawezona do marek ZE ZDJECIEM na
# Wikipedii, i to jej rozmiar dusil rozmaitosc: 26 wpisow w historii przy 18
# pozycjach oznacza, ze od dawna chodzila w kolko.
#
# Weryfikacja 10.08.2026 pokazala, dlaczego ta lista byla taka krotka: Wikipedia
# prawie nie ma wolnych zdjec marek kosmetycznych. Clinique, MAC, Lancome,
# Maybelline, Revlon maja artykul i ZERO fotografii. Skoro Maja projektuje marki
# beauty zawodowo, wieksza wartosc ma wlasciwa marka niz obrazek, wiec:
# **wymagamy artykulu, zdjecie jest mile widziane, ale nie decyduje o wyborze.**
# Artykul jest twardym warunkiem, bo bez zrodla model zaczyna zmyslac historie
# marki. Wpisy potwierdzone jako 404 (Tatcha, Cosrx, Rituals, Ilia, Hourglass,
# Augustinus Bader, The Ordinary) sa tu SWIADOMIE pominiete, nie zapomniane.
BRANDS = {
    "nisza zapachowa": [
        ("Byredo", "Byredo"), ("Le Labo", "Le Labo"), ("Diptyque", "Diptyque"),
        ("Penhaligon's", "Penhaligon's"), ("Creed", "Creed (perfume)"),
        ("Comme des Garçons Parfums", "Comme des Garçons"),
        ("Amouage", "Amouage"), ("L'Artisan Parfumeur", "L'Artisan Parfumeur"),
        ("Santa Maria Novella", "Officina Profumo-Farmaceutica di Santa Maria Novella"),
        ("Frédéric Malle", "Frédéric Malle"), ("Serge Lutens", "Serge Lutens"),
        ("Acqua di Parma", "Acqua di Parma"), ("Jo Malone London", "Jo Malone London"),
        ("Floris London", "Floris of London"),
    ],
    "dziedzictwo": [
        ("Guerlain", "Guerlain"), ("Chanel", "Chanel"), ("Dior", "Parfums Christian Dior"),
        ("Estée Lauder", "Estée Lauder Companies"), ("Shiseido", "Shiseido"),
        ("Helena Rubinstein", "Helena Rubinstein"), ("Elizabeth Arden", "Elizabeth Arden, Inc."),
        ("Yves Saint Laurent Beauté", "Yves Saint Laurent (brand)"),
        ("Kiehl's", "Kiehl's"), ("Weleda", "Weleda"), ("Clarins", "Clarins"),
        ("Lancôme", "Lancôme"), ("Max Factor", "Max Factor"), ("Revlon", "Revlon"),
        ("L'Oréal", "L'Oréal"),
    ],
    "nowa fala": [
        ("Glossier", "Glossier"), ("Rare Beauty", "Rare Beauty"),
        ("Fenty Beauty", "Fenty Beauty"), ("Drunk Elephant", "Drunk Elephant"),
        ("Milk Makeup", "Milk Makeup"), ("Huda Beauty", "Huda Beauty"),
        ("Kylie Cosmetics", "Kylie Cosmetics"), ("Anastasia Beverly Hills", "Anastasia Beverly Hills"),
        ("Sol de Janeiro", "Sol de Janeiro"), ("NYX Cosmetics", "NYX Cosmetics"),
        ("Laura Mercier", "Laura Mercier"), ("Bobbi Brown", "Bobbi Brown Cosmetics"),
    ],
    "azja": [
        ("Sulwhasoo", "Sulwhasoo"), ("Laneige", "Laneige"), ("Innisfree", "Innisfree (brand)"),
        ("Amorepacific", "Amorepacific Corporation"), ("SK-II", "SK-II"),
        ("Shu Uemura", "Shu Uemura"), ("Dr. Jart+", "Dr. Jart+"), ("Missha", "Missha"),
        ("Etude House", "Etude House"), ("Nature Republic", "Nature Republic"),
    ],
    "apteka i derma": [
        ("CeraVe", "CeraVe"), ("La Roche-Posay", "La Roche-Posay"),
        ("Avène", "Pierre Fabre"), ("Bioderma", "Bioderma"), ("Eucerin", "Eucerin"),
        ("Nivea", "Nivea"), ("Neutrogena", "Neutrogena"), ("Paula's Choice", "Paula's Choice"),
        ("Clinique", "Clinique"), ("Olay", "Olay"),
    ],
    "etyka i lifestyle": [
        ("Aesop", "Aesop (brand)"), ("Lush", "Lush (company)"), ("The Body Shop", "The Body Shop"),
        ("Burt's Bees", "Burt's Bees"), ("Dr. Hauschka", "Dr. Hauschka"),
        ("L'Occitane", "L'Occitane en Provence"), ("Origins", "Origins (cosmetics)"),
        ("Aveda", "Aveda"), ("Yves Rocher", "Yves Rocher"), ("Oriflame", "Oriflame"),
        ("Tom Ford Beauty", "Tom Ford (brand)"), ("Marc Jacobs Beauty", "Marc Jacobs"),
        ("Victoria Beckham Beauty", "Victoria Beckham (brand)"),
        ("MAC Cosmetics", "MAC Cosmetics"), ("Maybelline", "Maybelline"),
    ],
}
BRAND_TIERS = ["nisza zapachowa", "dziedzictwo", "nowa fala", "azja", "apteka i derma", "etyka i lifestyle"]

# ---------------------------------------------------------------- ciekawostka
# Osiem z 23 zapisanych ciekawostek to byla ta sama etymologia slowa "algorytm".
# Model zostawiony z otwartym "wymysl cos zaskakujacego" wraca do swoich
# ulubionych faktow. Kategoria narzucona z gory zamyka mu te droge: w dniu
# typografii nie ma jak wjechac al-Chwarizmi.
CIEKAWOSTKA_CATS = [
    ("typografia i druk", "historia kroju pisma, znaku drukarskiego albo decyzji typograficznej, ktora do dzis widac"),
    ("pochodzenie przedmiotu", "skad naprawde wzial sie codzienny przedmiot i po co pierwotnie powstal"),
    ("kolor i pigment", "historia jednego koloru albo pigmentu, jak go zdobywano i co to kosztowalo"),
    ("dziwny eksperyment", "prawdziwy eksperyment naukowy o zaskakujacym przebiegu albo wyniku"),
    ("bledy i wpadki", "kosztowna pomylka, literowka albo przypadek, ktory zmienil produkt, marke lub historie"),
    ("jezyk i slowa", "zaskakujaca etymologia albo slowo, ktore znaczylo kiedys cos zupelnie innego"),
    ("architektura i miasto", "detal budynku, ulicy albo miasta, ktory ma nieoczywisty powod"),
    ("jedzenie i kuchnia", "nieoczywista historia potrawy, skladnika albo zwyczaju przy stole"),
    ("moda i ubior", "dlaczego element ubioru wyglada tak, jak wyglada, i skad sie wzial"),
    ("muzyka i dzwiek", "historia dzwieku, instrumentu, nagrania albo ciszy"),
    ("zwierzeta i natura", "zaskakujace zachowanie zwierzecia albo rosliny, potwierdzone przez nauke"),
    ("mapy i podroze", "blad kartograficzny, granica albo miejsce z dziwna historia"),
    ("liczby i miary", "skad wziela sie jednostka, data w kalendarzu albo dziwna konwencja liczenia"),
    ("rzemioslo i material", "jak powstaje material albo przedmiot i co w tym procesie jest nieoczywiste"),
    ("reklama i marka", "kampania, logo albo nazwa z historia, ktorej nikt sie nie spodziewa"),
    ("cialo czlowieka", "nieoczywisty fakt o ludzkim ciele albo zmyslach"),
]

# ---------------------------------------------------------------- kat sekcji nauka
# Stara instrukcja brzmiala "longevity/neuroscience" i przez dwa miesiace
# produkowala te same cztery historie o tau, APOE i starzeniu. Rozszerzamy
# studnie, trzymajac wymog twardych dowodow.
NAUKA_THEMES = [
    "mozg i pamiec (twarde wyniki kliniczne, nie spekulacje)",
    "sen, rytm dobowy i regeneracja",
    "metabolizm, odzywianie i realne dane z badan na ludziach",
    "mikrobiom i uklad odpornosciowy",
    "ruch, miesnie i wydolnosc jako biologia, nie fitness",
    "psychologia i zachowanie, badania z powtarzalnym wynikiem",
    "genetyka i biologia starzenia",
    "zmysly: wzrok, sluch, wech, dotyk",
    "serce, krazenie i cisnienie",
    "skora jako narzad, dermatologia oparta na dowodach",
    "zdrowie kobiet, hormony, cykl, menopauza",
    "leki i terapie, ktore wlasnie przechodza probe kliniczna",
    "nauka o stresie, uwadze i wypaleniu",
    "srodowisko a zdrowie: powietrze, swiatlo, halas, mikroplastik",
]

# ---------------------------------------------------------------- kat sekcji AI
AI_ANGLES = [
    "premiery produktow i modeli, co realnie umieja inaczej niz wczoraj",
    "pieniadze: inwestycje, przejecia, wyceny, wojna cenowa",
    "prawo i regulacje, co sie zmienia dla firm i tworcow",
    "AI w pracy kreatywnej: design, obraz, wideo, muzyka, reklama",
    "spor i krytyka: kto sie z czym nie zgadza i dlaczego",
    "badania i nowe wyniki, ktore nie sa produktem",
    "AI w zdrowiu, nauce i medycynie",
    "kultura i obyczaje wokol AI: jak ludzie faktycznie tego uzywaja",
    "bezpieczenstwo, naduzycia i to, co poszlo nie tak",
    "sprzet, chipy, energia i infrastruktura",
]

# ---------------------------------------------------------------- ksiazka
KSIAZKA_THEMES = [
    "mozg i neurobiologia", "dlugowiecznosc i starzenie", "sen i regeneracja",
    "psychologia i podejmowanie decyzji", "AI i spoleczenstwo",
    "biologia ewolucyjna", "mikrobiom, jelita i odzywianie",
    "ekonomia behawioralna i pieniadze", "uwaga, technologia i zycie cyfrowe",
    "swiadomosc i psychodeliki", "historia nauki i wielkie odkrycia",
    "projektowanie, estetyka i kultura wizualna",
    "socjologia pracy i tego, jak zyjemy", "klimat i przyszlosc planety",
]

# ---------------------------------------------------------------- sekcja "Inn"
# Ta sekcja miala jedna instrukcje na okraglo, wiec codziennie wychodzil z niej
# ten sam ksztalt tekstu. Rotujemy nie tylko temat, ale i etykiete na stronie,
# zeby wydanie inaczej WYGLADALO. Klucz JSON zostaje `inn`, wiec render sie nie
# zmienia poza napisem na plakietce.
INN_VARIANTS = [
    ("Inn", "cos kulturalnie ciekawego z ostatnich kilku dni: wiralowa historia, prawdziwy spor, zaskakujacy trend w beauty, wellness, brandingu albo kulturze AI"),
    ("Marka tygodnia", "jedna konkretna rzecz, ktora jakas marka wlasnie zrobila i ktora warto podpatrzec albo skrytykowac: kampania, rebranding, opakowanie, ruch komunikacyjny"),
    ("Archiwum", "cos z historii designu, brandingu albo reklamy, co wlasnie teraz znowu ma znaczenie; pokaz, co z tego wynika dzis"),
    ("Kontrapunkt", "poglad, z ktorym wiekszosc branzy sie zgadza, i konkretny powod, dla ktorego moze byc bledny; oprzyj to na czyms, co ktos naprawde powiedzial albo pokazal"),
    ("Rzemioslo", "jak cos jest naprawde zrobione: material, technika druku, proces produkcji opakowania, warsztat; konkret zamiast ogolnikow"),
    ("Liczba dnia", "jedna liczba z ostatnich dni, ktora zmienia sposob patrzenia na branze beauty, kreatywna albo AI; wyjasnij, co za nia stoi"),
    ("Warsztat", "narzedzie, technika albo sposob pracy, ktory realnie oszczedza czas w studiu projektowym; konkretnie, bez reklamy"),
]


def _rng(seed):
    return random.Random(str(seed))


def _pick_tier(tiers, seed, offset=0):
    """Warstwy ida po kolei wedlug numeru dnia, nie losowo: los potrafi trzy razy
    z rzedu trafic w te sama szufladke, a caly sens warstw to rozstrzal."""
    return tiers[(int(seed) + offset) % len(tiers)]


def pick_person(seen_names, daynum, seed):
    """Zwraca LISTE kandydatow w kolejnosci preferencji.

    Lista, nie jeden wybor, bo zdjecie weryfikuje skrypt i musi miec na czym
    zejsc nizej, gdy ktos nie ma fotografii na Wikipedii. Najpierw warstwa dnia,
    potem cala reszta puli, na koncu, gdy naprawde wszystko bylo, najdawniej
    uzyci (a nie ci sami co zawsze)."""
    from dedup import canon_person
    used = {canon_person(n) for n in (seen_names or [])}
    rng = _rng(seed)
    tier = _pick_tier(PEOPLE_TIERS, daynum)
    fresh_tier = [p for p in PEOPLE[tier] if canon_person(p[0]) not in used]
    rest = [p for t in PEOPLE_TIERS if t != tier for p in PEOPLE[t]
            if canon_person(p[0]) not in used]
    rng.shuffle(fresh_tier)
    rng.shuffle(rest)
    # zuzyci, od najdawniej pokazanych: kolejnosc w seen.json to kolejnosc uzycia
    order = {canon_person(n): i for i, n in enumerate(seen_names or [])}
    stale = sorted([p for t in PEOPLE_TIERS for p in PEOPLE[t]
                    if canon_person(p[0]) in used],
                   key=lambda p: order.get(canon_person(p[0]), 0))
    return fresh_tier + rest + stale


def pick_brand(seen_names, daynum, seed):
    from dedup import canon_brand
    used = {canon_brand(n) for n in (seen_names or [])}
    rng = _rng(seed)
    tier = _pick_tier(BRAND_TIERS, daynum, offset=3)  # przesuniete, zeby nie chodzic w parze z osobami
    fresh_tier = [b for b in BRANDS[tier] if canon_brand(b[0]) not in used]
    rest = [b for t in BRAND_TIERS if t != tier for b in BRANDS[t]
            if canon_brand(b[0]) not in used]
    rng.shuffle(fresh_tier)
    rng.shuffle(rest)
    order = {canon_brand(n): i for i, n in enumerate(seen_names or [])}
    stale = sorted([b for t in BRAND_TIERS for b in BRANDS[t]
                    if canon_brand(b[0]) in used],
                   key=lambda b: order.get(canon_brand(b[0]), 0))
    return fresh_tier + rest + stale


def pick_ciekawostka_cat(seen_cats, daynum, seed):
    """Kategoria nieuzyta w ostatnich 10 dniach; jesli wszystkie byly, ta
    najdawniejsza."""
    recent = [c for c in (seen_cats or [])[-10:]]
    fresh = [c for c in CIEKAWOSTKA_CATS if c[0] not in recent]
    pool = fresh or CIEKAWOSTKA_CATS
    return _rng(seed).choice(pool)


def pick_nauka_theme(seen_themes, seed):
    recent = (seen_themes or [])[-6:]
    pool = [t for t in NAUKA_THEMES if t not in recent] or NAUKA_THEMES
    return _rng(str(seed) + "n").choice(pool)


def pick_ai_angle(seen_angles, seed):
    recent = (seen_angles or [])[-4:]
    pool = [t for t in AI_ANGLES if t not in recent] or AI_ANGLES
    return _rng(str(seed) + "a").choice(pool)


def pick_book_theme(seen_themes, seed):
    recent = (seen_themes or [])[-7:]
    pool = [t for t in KSIAZKA_THEMES if t not in recent] or KSIAZKA_THEMES
    return _rng(str(seed) + "k").choice(pool)


def pick_inn_variant(daynum):
    return INN_VARIANTS[int(daynum) % len(INN_VARIANTS)]
