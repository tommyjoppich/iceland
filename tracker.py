"""Check round-trip prices ORD ⇄ Iceland on United and Icelandair for every trip
in config.TRIPS, every week through config.TRACK_UNTIL, and save them to
data/prices-YYYY-MM-DD.csv (one small file per day).

One search per trip covers both airlines; the results are split by airline.
The next HOURLY_WEEKENDS weekends are searched every run (hourly); weekends
further out only every FAR_EVERY_HOURS hours, to keep the load on Google low.
"""

from __future__ import annotations

import csv
import json
import os
import random
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import config
import safe_parse  # noqa: F401  (makes the flight library skip broken listings)
from fast_flights.exceptions import FlightsNotFound

ROOT = Path(__file__).parent
DATA = ROOT / "data"
LATEST_OPTIONS = DATA / "latest_options.json"
DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
FIELDS = [
    "run_id", "checked_at_local", "local_hour", "local_weekday", "weekend", "combo",
    "depart_date", "return_date", "airline", "status", "price", "nonstop_price", "stops",
    "flight", "num_options", "error",
]
GIVE_UP_AFTER = 12           # consecutive failures → assume blocked, stop this run


def upcoming_trips(today: date, until: date | None = None):
    """All trips departing after today and on/before TRACK_UNTIL:
    [(weekend_key, combo, depart, return, spec)] where weekend_key is the
    Wednesday-ish start of that trip week (the earliest departure day)."""
    until = until or date.fromisoformat(config.TRACK_UNTIL)
    out_days = [DAY.index(t["out"]) for t in config.TRIPS]
    first = min(out_days)
    anchor = today - timedelta(days=(today.weekday() - first) % 7)
    trips = []
    while anchor <= until:
        for t in config.TRIPS:
            od, rd = DAY.index(t["out"]), DAY.index(t["back"])
            out = anchor + timedelta(days=od - first)
            back = out + timedelta(days=(rd - od) % 7 or 7)
            if today < out <= until:
                trips.append((anchor, f"{t['out']}→{t['back']}", out, back, t))
        anchor += timedelta(weeks=1)
    return trips


def build_query(out: date, back: date, spec: dict):
    from fast_flights import FlightQuery, Passengers, create_query
    lb, la, lf = spec.get("leave_before"), spec.get("land_after"), spec.get("leave_after")
    legs = [
        FlightQuery(date=out.isoformat(), from_airport=config.ORIGIN, to_airport=config.DEST,
                    airlines=list(config.AIRLINES),
                    earliest_departure_hour=lf if lf else None,
                    latest_departure_hour=(lb - 1) if lb else None),
        FlightQuery(date=back.isoformat(), from_airport=config.DEST, to_airport=config.ORIGIN,
                    airlines=list(config.AIRLINES),
                    earliest_arrival_hour=la if la else None),
    ]
    return create_query(
        flights=legs, seat=config.SEAT, trip="round-trip", max_stops=config.MAX_STOPS,
        passengers=Passengers(adults=config.ADULTS), currency=config.CURRENCY,
        language="en-US", exclude_basic_economy=config.EXCLUDE_BASIC_ECONOMY,
    )


def fetch(out: date, back: date, spec: dict):
    from fast_flights import get_flights
    q = build_query(out, back, spec)
    try:
        return get_flights(q)
    except FlightsNotFound:
        return []                             # Google: no flights for these dates
    except Exception:
        time.sleep(8)                         # one retry after a short pause
        try:
            return get_flights(q)
        except FlightsNotFound:
            return []


def airline_of(names: list[str]) -> str | None:
    """'UA' / 'FI' if every segment is that one airline, else None (mixed/other)."""
    for code, name in config.AIRLINES.items():
        if names and all(name.lower() in n.lower() for n in names):
            return code
    return None


def split_options(results, spec: dict) -> dict[str, list[dict]]:
    lb, lf = spec.get("leave_before"), spec.get("leave_after")
    by = {code: [] for code in config.AIRLINES}
    for f in results or []:
        if not f.flights or not f.price:
            continue
        code = airline_of(f.airlines or [])
        if not code or len(f.flights) - 1 > config.MAX_STOPS:
            continue
        first, last = f.flights[0], f.flights[-1]
        if lb is not None and first.departure.time[0] >= lb:
            continue
        if lf is not None and first.departure.time[0] < lf:
            continue
        by[code].append({
            "p": int(f.price), "s": len(f.flights) - 1, "al": code,
            "d": f"{first.departure.time[0]:02d}:{first.departure.time[1]:02d}",
            "a": f"{last.arrival.time[0]:02d}:{last.arrival.time[1]:02d}",
            "m": int(sum(s.duration or 0 for s in f.flights)),
            "via": "/".join(s.to_airport.code for s in f.flights[:-1] if s.to_airport.code),
        })
    return {k: sorted(v, key=lambda o: o["p"]) for k, v in by.items()}


def run(fetcher=fetch, now: datetime | None = None, sleep=time.sleep) -> int:
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(config.TIMEZONE))
    run_id = now.strftime("%Y-%m-%dT%H:%MZ")
    trips = upcoming_trips(local.date())
    weeks = sorted({t[0] for t in trips})
    near = set(weeks[:config.HOURLY_WEEKENDS])
    full = os.environ.get("FULL_RUN", "").lower() == "true"   # "Search all weekends" box
    do_far = full or local.hour % config.FAR_EVERY_HOURS == 0
    todo = [t for t in trips if t[0] in near or do_far]
    print(f"{len(todo)} of {len(trips)} trips this run "
          f"({'all weekends' if do_far else f'next {config.HOURLY_WEEKENDS} weekends only'})")

    DATA.mkdir(exist_ok=True)
    path = DATA / f"prices-{local:%Y-%m-%d}.csv"
    new = not path.exists()
    latest = json.loads(LATEST_OPTIONS.read_text()).get("trips", {}) if LATEST_OPTIONS.exists() else {}
    ok = streak = 0
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for anchor, combo, out, back, spec in todo:
            base = {"run_id": run_id, "checked_at_local": local.strftime("%Y-%m-%d %H:%M"),
                    "local_hour": local.hour, "local_weekday": local.strftime("%A"),
                    "weekend": anchor.isoformat(), "combo": combo,
                    "depart_date": out.isoformat(), "return_date": back.isoformat()}
            key = f"{out.isoformat()}|{back.isoformat()}"
            try:
                by = split_options(fetcher(out, back, spec), spec)
                streak = 0
                latest[key] = [o for code in by for o in by[code][:8]]
                msg = []
                for code, opts in by.items():
                    row = {**base, "airline": code}
                    if opts:
                        ns = [o for o in opts if o["s"] == 0]
                        row.update(status="ok", price=opts[0]["p"],
                                   nonstop_price=ns[0]["p"] if ns else "", stops=opts[0]["s"],
                                   flight=f"{opts[0]['d']}→{opts[0]['a']}", num_options=len(opts),
                                   error="")
                        ok += 1
                        msg.append(f"{code} ${opts[0]['p']}")
                    else:
                        row.update(status="none", price="", nonstop_price="", stops="", flight="",
                                   num_options=0, error="no matching flights")
                        msg.append(f"{code} none")
                    w.writerow(row)
                print(f"{combo} {out:%b %d}–{back:%b %d}: {', '.join(msg)}", flush=True)
            except Exception as e:
                streak += 1
                for code in config.AIRLINES:
                    w.writerow({**base, "airline": code, "status": "error", "price": "",
                                "nonstop_price": "", "stops": "", "flight": "", "num_options": 0,
                                "error": str(e)[:160]})
                print(f"{combo} {out:%b %d}–{back:%b %d}: ERROR {e}", flush=True)
                if streak >= GIVE_UP_AFTER:
                    print("Many failures in a row — Google is probably blocking; stopping early.",
                          file=sys.stderr)
                    break
            sleep(random.uniform(*config.DELAY_RANGE))

    # keep flight lists only for trips still ahead
    keep = {f"{t[2].isoformat()}|{t[3].isoformat()}" for t in trips}
    latest = {k: v for k, v in latest.items() if k in keep}
    LATEST_OPTIONS.write_text(json.dumps({"run_id": run_id, "trips": latest}, separators=(",", ":")))
    print(f"Done: {ok} airline prices found ({safe_parse.skipped} incomplete Google listings skipped)")
    return ok


if __name__ == "__main__":
    run()
