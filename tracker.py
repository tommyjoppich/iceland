"""Check United round-trip prices ORD ⇄ Iceland for every date combo of the
next N weekends, and append them to data/prices-YYYY-MM.csv.

Weekend = leave Wed or Thu, come back Sun or Mon → 4 combos per weekend.
With 10 weekends that's ~40 searches per run (~1–2 minutes). Runs hourly.
"""

from __future__ import annotations

import csv
import json
import random
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import config

ROOT = Path(__file__).parent
DATA = ROOT / "data"
LATEST_OPTIONS = DATA / "latest_options.json"
DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
FIELDS = [
    "run_id", "checked_at_local", "local_hour", "local_weekday", "weekend", "combo",
    "depart_date", "return_date", "status", "price", "nonstop_price", "stops",
    "flight", "num_options", "error",
]
GIVE_UP_AFTER = 12           # consecutive failures → assume blocked, stop this run


def upcoming_trips(today: date, n: int):
    """[(weekend_key, combo, depart, return)] for the next n weekends.
    weekend_key = the date of the first possible departure day that week."""
    first = min(config.DEPART_DAYS, key=lambda d: (d - config.DEPART_DAYS[0]) % 7)
    anchor = today
    while anchor.weekday() != first:
        anchor -= timedelta(days=1)          # start of the current trip week
    trips = []
    for i in range(n + 1):
        a = anchor + timedelta(weeks=i)
        for dd in config.DEPART_DAYS:
            out = a + timedelta(days=(dd - first) % 7)
            for rd in config.RETURN_DAYS:
                back = out + timedelta(days=(rd - dd) % 7 or 7)
                if out > today:              # departure must still be ahead
                    trips.append((a, f"{DAY[dd]}→{DAY[rd]}", out, back))
    keys = sorted({t[0] for t in trips})[:n]
    return [t for t in trips if t[0] in keys]


def build_query(out: date, back: date):
    from fast_flights import FlightQuery, Passengers, create_query
    legs = [
        FlightQuery(date=out.isoformat(), from_airport=config.ORIGIN, to_airport=config.DEST,
                    airlines=[config.AIRLINE_CODE]),
        FlightQuery(date=back.isoformat(), from_airport=config.DEST, to_airport=config.ORIGIN,
                    airlines=[config.AIRLINE_CODE]),
    ]
    return create_query(
        flights=legs, seat=config.SEAT, trip="round-trip", max_stops=config.MAX_STOPS,
        passengers=Passengers(adults=config.ADULTS), currency=config.CURRENCY,
        language="en-US", exclude_basic_economy=config.EXCLUDE_BASIC_ECONOMY,
    )


def fetch(out: date, back: date):
    from fast_flights import get_flights
    q = build_query(out, back)
    try:
        return get_flights(q)
    except Exception:
        time.sleep(8)                         # one retry after a short pause
        return get_flights(q)


def united_options(results) -> list[dict]:
    opts = []
    for f in results or []:
        if not f.flights or not f.price:
            continue
        names = f.airlines or []
        if not names or not all(config.AIRLINE_NAME.lower() in n.lower() for n in names):
            continue
        if len(f.flights) - 1 > config.MAX_STOPS:
            continue
        first, last = f.flights[0], f.flights[-1]
        via = [s.to_airport.code for s in f.flights[:-1]] if hasattr(f.flights[0].to_airport, "code") else []
        opts.append({
            "p": int(f.price), "s": len(f.flights) - 1,
            "d": f"{first.departure.time[0]:02d}:{first.departure.time[1]:02d}",
            "a": f"{last.arrival.time[0]:02d}:{last.arrival.time[1]:02d}",
            "m": int(sum(s.duration for s in f.flights)),
            "via": "/".join(v for v in via if v),
        })
    return sorted(opts, key=lambda o: o["p"])


def run(fetcher=fetch, now: datetime | None = None, sleep=time.sleep) -> int:
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(config.TIMEZONE))
    run_id = now.strftime("%Y-%m-%dT%H:%MZ")
    trips = upcoming_trips(local.date(), config.WEEKENDS_AHEAD)

    DATA.mkdir(exist_ok=True)
    path = DATA / f"prices-{local:%Y-%m}.csv"
    new = not path.exists()
    latest, ok, streak = {}, 0, 0
    with path.open("a", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if new:
            w.writeheader()
        for anchor, combo, out, back in trips:
            row = {"run_id": run_id, "checked_at_local": local.strftime("%Y-%m-%d %H:%M"),
                   "local_hour": local.hour, "local_weekday": local.strftime("%A"),
                   "weekend": anchor.isoformat(), "combo": combo,
                   "depart_date": out.isoformat(), "return_date": back.isoformat()}
            try:
                opts = united_options(fetcher(out, back))
                streak = 0
                if not opts:
                    raise LookupError("no matching United flights")
                ns = [o for o in opts if o["s"] == 0]
                row.update(status="ok", price=opts[0]["p"],
                           nonstop_price=ns[0]["p"] if ns else "", stops=opts[0]["s"],
                           flight=f"{opts[0]['d']}→{opts[0]['a']}", num_options=len(opts), error="")
                latest[f"{out.isoformat()}|{back.isoformat()}"] = opts[:12]
                ok += 1
            except Exception as e:
                if not isinstance(e, LookupError):
                    streak += 1
                row.update(status="none" if isinstance(e, LookupError) else "error",
                           price="", nonstop_price="", stops="", flight="", num_options=0,
                           error=str(e)[:160])
            w.writerow(row)
            print(f"{combo} {out:%b %d}–{back:%b %d}: {row['status']} {row['price']}", flush=True)
            if streak >= GIVE_UP_AFTER:
                print("Many failures in a row — Google is probably blocking; stopping early.", file=sys.stderr)
                break
            sleep(random.uniform(*config.DELAY_RANGE))

    LATEST_OPTIONS.write_text(json.dumps({"run_id": run_id, "trips": latest}, separators=(",", ":")))
    print(f"Done: {ok} of {len(trips)} trips priced")
    return ok


if __name__ == "__main__":
    run()
