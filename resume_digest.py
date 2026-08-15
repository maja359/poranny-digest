"""Sklada wydanie ze snapshotu, bez jednego wywolania API.

Kiedy tego uzyc: run padl PO researchu, czyli w logach jest `SNAPSHOT_SAVED=`,
a strony nie ma. Wtedy tresc jest juz kupiona i lezy w artefakcie runu.

    python resume_digest.py content.json

Albo z Actions: odpal workflow recznie i wpisz numer padnietego runu w pole
`resume_run_id`, workflow sam sciagnie artefakt i wywola ten skrypt.

Render idzie przez ta sama funkcje co normalny przebieg (`render_digest.render`),
wiec wydanie ze wznowienia jest identyczne z tym, ktore run mial wypuscic.
"""

import json, os, sys

import render_digest
import snapshot

ROOT = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    path = argv[1] if len(argv) > 1 else snapshot.path()
    if not os.path.exists(path):
        print("NO_SNAPSHOT " + path); return 1

    ctx = snapshot.load(path)
    # Sciezki bierzemy z BIEZACEGO checkoutu, nie ze snapshotu: katalog runnera,
    # ktory zrzucil plik, juz nie istnieje.
    ctx["ROOT"] = ROOT
    ctx.setdefault("PAGES", "https://maja359.github.io/poranny-digest/")

    date_file = ctx.get("date_file") or ctx["c"].get("date_file")
    page_path = os.path.join(ROOT, date_file + ".html")
    if os.path.exists(page_path) and os.path.getsize(page_path) > 3000:
        print("ALREADY_PUBLISHED " + date_file + " — nie nadpisuje"); return 0

    seen_path = os.path.join(ROOT, "seen.json")
    seen = {}
    if os.path.exists(seen_path):
        try:
            seen = json.load(open(seen_path, encoding="utf-8"))
        except Exception:
            seen = {}
    for k in ("books", "beauty", "topics", "osoby", "ciekawostki",
              "cieka_cats", "nauka_themes", "ai_angles", "book_themes", "authors"):
        seen.setdefault(k, [])

    print("RESUME z %s (wydanie %s, koszt oryginalnego runu ~$%.2f)"
          % (path, date_file, ctx.get("cost", 0.0)))
    render_digest.render(ctx, seen, seen_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
