"""A more forgiving version of fast_flights' result parser.

Google sometimes lists an itinerary with a missing piece (most often no price on
international round trips). The stock parser crashes on that one itinerary and
throws away the whole search ("'NoneType' object is not subscriptable").
This version skips just the broken itinerary and keeps the rest.

Imported by tracker.py, which swaps it in for the library's parser.
"""

from __future__ import annotations

import json

import fast_flights.parser as _p
from fast_flights.exceptions import FlightsNotFound
from fast_flights.model import (Airline, Airport, Alliance, CarbonEmission, Flights,
                                JsMetadata, SimpleDatetime, SingleFlight)

skipped = 0          # how many broken itineraries were skipped (for the log)


def _get(seq, *idx):
    """seq[i][j]... or None if any step is missing."""
    for i in idx:
        if not isinstance(seq, (list, tuple)) or i >= len(seq) or seq[i] is None:
            return None
        seq = seq[i]
    return seq


def parse_js(js: str):
    global skipped
    data = js.split("data:", 1)[1].rsplit(",", 1)[0]
    if data.endswith("errorHasStatus: true"):
        raise FlightsNotFound("no flights found; received error")
    payload = json.loads(data)

    alliances = [Alliance(code=c, name=n) for c, n in (_get(payload, 7, 1, 0) or [])]
    airlines = [Airline(code=c, name=n) for c, n in (_get(payload, 7, 1, 1) or [])]
    flights = _p.ResultList()
    flights.metadata = JsMetadata(alliances=alliances, airlines=airlines)

    for k in _get(payload, 3, 0) or []:
        try:
            flight, price = k[0], _get(k, 1, 0, 1)
            if not flight or price is None:
                raise ValueError("no price")
            legs = []
            for sf in flight[2]:
                if _get(sf, 20) is None or _get(sf, 21) is None:
                    raise ValueError("no date")
                legs.append(SingleFlight(
                    from_airport=Airport(code=sf[3], name=sf[4]),
                    to_airport=Airport(code=sf[6], name=sf[5]),
                    departure=SimpleDatetime(date=tuple(sf[20]), time=_p._parse_time(sf[8])),
                    arrival=SimpleDatetime(date=tuple(sf[21]), time=_p._parse_time(sf[10])),
                    duration=sf[11] or 0,
                    plane_type=_get(sf, 17),
                ))
            if not legs:
                raise ValueError("no legs")
            flights.append(Flights(
                type=flight[0], price=price, airlines=flight[1] or [], flights=legs,
                carbon=CarbonEmission(typical_on_route=_get(flight, 22, 8) or 0,
                                      emission=_get(flight, 22, 7) or 0),
            ))
        except Exception:
            skipped += 1
    return flights


_p.parse_js = parse_js   # parse() looks this name up at call time
