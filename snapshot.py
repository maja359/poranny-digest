"""Snapshot gotowej tresci wydania, zanim zacznie sie render.

Po co: research i pass pisania kosztuja realne pieniadze, a render nie kosztuje
nic. Jesli render sie wywali (14-15.08.2026: jedno zrodlo w zlym ksztalcie),
bez snapshotu caly ten koszt przepada i jedyna droga do wydania jest zaplacic
drugi raz. Ze snapshotem workflow wrzuca plik jako artefakt runu, a
`resume_digest.py` sklada z niego strone za zero dolarow.

Format jest celowo plaskim JSON-em: ma go dac sie obejrzec i recznie poprawic.
"""

import json, os

FILENAME = "content.json"


def path():
    return os.path.join(os.environ.get("RUNNER_TEMP", "/tmp"), FILENAME)


def dump(ctx, where=None):
    p = where or path()
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(ctx, f, ensure_ascii=False, indent=2)
        print("SNAPSHOT_SAVED=" + p)
        return p
    except Exception as e:
        # Snapshot to siatka bezpieczenstwa, nie warunek wydania: jesli sie nie
        # zapisze, run ma leciec dalej i opublikowac strone normalnie.
        print("SNAPSHOT_FAILED:", repr(e)[:200])
        return None


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)
