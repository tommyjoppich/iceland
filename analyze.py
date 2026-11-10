"""Build REPORT.md, the website (docs/index.html) and email alerts from data/.

Every trip (a weekend × a date combo like Wed→Mon) gets its own price history.
"Cheapest trip" = the lowest fare across all tracked trips at that check.

Alerts (→ alert.md → GitHub issue → email):
  * a trip drops to/below TARGET_PRICE
  * a trip is DEAL_THRESHOLD below its own typical price
  * the cheapest trip hits a new all-time low (after the first 2 days)
"""

from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime
from pathlib import Path

import pandas as pd

import config

ROOT = Path(__file__).parent
DATA = ROOT / "data"
LATEST_OPTIONS = DATA / "latest_options.json"
ALERTED_CSV = DATA / "alerted.csv"
REPORT = ROOT / "REPORT.md"
TEMPLATE = ROOT / "dashboard_template.html"
DASHBOARD = ROOT / "docs" / "index.html"
ALERT = ROOT / "alert.md"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
MIN_DAYS_FOR_PATTERNS = 7


def money(x) -> str:
    return "—" if x is None or pd.isna(x) else f"${x:,.0f}"


def num(x):
    return None if x is None or pd.isna(x) else int(round(float(x)))


def pct(x) -> str:
    r = round(x * 100)
    return "±0%" if r == 0 else f"{r:+d}%".replace("-", "−")


def hour_label(h: int) -> str:
    return f"{(h % 12) or 12} {'AM' if h < 12 else 'PM'}"


def trip_label(out: str, back: str) -> str:
    a, b = date.fromisoformat(out), date.fromisoformat(back)
    return f"{DAY[a.weekday()]} {a:%b} {a.day} – {DAY[b.weekday()]} {b:%b} {b.day}"


def trip_text() -> str:
    leave = " or ".join(DAY[d] for d in config.DEPART_DAYS)
    back = " or ".join(DAY[d] for d in config.RETURN_DAYS)
    stops = "nonstop" if config.MAX_STOPS == 0 else f"up to {config.MAX_STOPS} stop"
    return f"Leave {leave}, return {back} (any time) · {stops}"


def load():
    files = sorted(DATA.glob("prices-*.csv"))
    if not files:
        return None
    df = pd.concat([pd.read_csv(f, dtype={"price": "float", "nonstop_price": "float"}) for f in files],
                   ignore_index=True)
    df["key"] = df["depart_date"] + "|" + df["return_date"]
    df["day"] = df["checked_at_local"].str[:10]
    return df


def main():
    df = load()
    if df is None:
        print("No data yet.")
        return
    if ALERT.exists():
        ALERT.unlink()

    runs = sorted(df["run_id"].unique())
    run_idx = {r: i for i, r in enumerate(runs)}
    latest_run = runs[-1]
    ok = df[df["status"] == "ok"].copy()
    latest = df[df["run_id"] == latest_run]
    cur = ok[ok["run_id"] == latest_run].sort_values("price")

    # ---------- one row per check: the cheapest trip at that moment
    checks = []
    for r, g in df.groupby("run_id", sort=True):
        gok = g[g["status"] == "ok"]
        errs = g[g["status"] == "error"]["error"]
        if gok.empty:
            checks.append({"t": g["checked_at_local"].iloc[0], "best": None, "key": None,
                           "n": 0, "tot": int(len(g)), "err": errs.iloc[0][:120] if len(errs) else "no prices"})
            continue
        b = gok.loc[gok["price"].idxmin()]
        checks.append({"t": g["checked_at_local"].iloc[0], "best": num(b["price"]), "key": b["key"],
                       "n": int(len(gok)), "tot": int(len(g)),
                       "err": f"{len(errs)} searches failed" if len(errs) else None})
    best = pd.Series([c["best"] for c in checks], dtype="float")
    good_best = best.dropna()

    # ---------- per trip (only trips still being tracked)
    hist_all = ok.groupby("key")
    trips = {}
    for r in latest.itertuples():
        k = r.key
        h = hist_all.get_group(k) if k in hist_all.groups else None
        now = cur[cur["key"] == k]
        t = {"w": r.weekend, "c": r.combo, "out": r.depart_date, "back": r.return_date,
             "label": trip_label(r.depart_date, r.return_date),
             "now": None, "stops": None, "flight": None, "nonstop": None,
             "typical": None, "low": None, "high": None, "vs": None, "n": 0, "hist": [],
             "status": r.status, "err": (str(r.error)[:120] if r.status == "error" and pd.notna(r.error) else None)}
        if h is not None and not h.empty:
            t.update(typical=float(h["price"].median()), low=num(h["price"].min()),
                     high=num(h["price"].max()), n=int(h["run_id"].nunique()),
                     hist=[[run_idx[x.run_id], int(x.price)] for x in h.itertuples()])
        if not now.empty:
            n0 = now.iloc[0]
            t.update(now=num(n0["price"]), stops=num(n0["stops"]), flight=n0["flight"],
                     nonstop=num(n0["nonstop_price"]))
            if t["typical"]:
                t["vs"] = t["now"] / t["typical"] - 1
        trips[k] = t

    # ---------- deals: trips well below their own typical price
    deals = [dict(key=k, **t) for k, t in trips.items()
             if t["now"] is not None and t["n"] >= config.DEAL_MIN_CHECKS
             and t["now"] <= t["typical"] * (1 - config.DEAL_THRESHOLD)]
    deals.sort(key=lambda d: d["vs"])

    # ---------- headline numbers
    have = not good_best.empty
    now_price = good_best.iloc[-1] if have else None
    now_check = next((c for c in reversed(checks) if c["best"] is not None), None)
    low = good_best.min() if have else None
    low_i = int(good_best.idxmin()) if have else None
    prev_low = good_best.iloc[:-1].min() if len(good_best) > 1 else None
    pct_cheaper = (good_best < now_price).mean() * 100 if have else 0
    first_day = datetime.fromisoformat(checks[0]["t"]).date()
    last_day = datetime.fromisoformat(checks[-1]["t"]).date()
    days_tracked = (last_day - first_day).days
    target = config.TARGET_PRICE
    under_target = [t for t in trips.values() if target and t["now"] is not None and t["now"] <= target]

    # ---------- signal
    if not have:
        sig = ("wait", "NO DATA", "No United prices found yet.")
    elif under_target:
        sig = ("buy", "BUY", f"{len(under_target)} trip{'s' if len(under_target) > 1 else ''} at or below "
                             f"your {money(target)} target.")
    elif now_price == low and len(good_best) > 48:
        sig = ("buy", "BUY", "The cheapest trip is at the lowest price the bot has ever seen.")
    elif deals:
        sig = ("good", "DEAL", f"{len(deals)} trip{'s are' if len(deals) > 1 else ' is'} "
                               f"{config.DEAL_THRESHOLD*100:.0f}%+ below usual — best: "
                               f"{deals[0]['label']} at {money(deals[0]['now'])}.")
    elif now_price <= low * 1.03:
        sig = ("good", "GOOD PRICE", f"Within 3% of the all-time low ({money(low)}).")
    else:
        sig = ("wait", "WAIT", f"{pct_cheaper:.0f}% of past checks had a cheaper trip; the low is {money(low)}.")

    # ---------- buy-time patterns (each fare vs. that trip's average that day)
    rel = ok.assign(rel=ok["price"] - ok.groupby(["key", "day"])["price"].transform("mean"))
    by_hour = rel.groupby("local_hour").agg(v=("rel", "mean"), avg=("price", "mean"),
                                            n=("run_id", "nunique")).sort_index()
    # weekday effect must be measured against the week, not the same day
    ok["week"] = pd.to_datetime(ok["day"]).dt.to_period("W").astype(str)
    relw = ok.assign(rel=ok["price"] - ok.groupby(["key", "week"])["price"].transform("mean"))
    by_day = relw.groupby("local_weekday").agg(v=("rel", "mean"), avg=("price", "mean"),
                                               n=("run_id", "nunique"))
    by_day = by_day.reindex([d for d in WEEKDAYS if d in by_day.index])
    bars = lambda agg, lab: [{"k": lab(k), "v": round(float(r.v), 1), "avg": num(r.avg), "n": int(r.n)}
                             for k, r in agg.iterrows()] if len(agg) > 1 else []

    # ---------- REPORT.md
    lines = [f"# 🇮🇸 United Chicago ⇄ {config.DEST_NAME} tracker", "",
             f"{trip_text()} · United only · regular Economy · next {config.WEEKENDS_AHEAD} weekends",
             f"_Updated {checks[-1]['t']} Chicago time · {len(runs)} checks so far_", "",
             f"**{sig[1]}** — {sig[2]}", ""]
    if have:
        lines += ["| | |", "|---|---|",
                  f"| **Cheapest trip now** | **{money(now_price)}** · {trips.get(now_check['key'], {}).get('label', '')} |",
                  f"| All-time low | {money(low)} on {checks[low_i]['t']} |",
                  f"| Typical cheapest | {money(good_best.median())} |", ""]
    order = {f"{DAY[d]}→{DAY[r]}": (i, j) for i, d in enumerate(config.DEPART_DAYS)
             for j, r in enumerate(config.RETURN_DAYS)}
    combos = sorted({t["c"] for t in trips.values()}, key=lambda c: order.get(c, (9, 9)))
    lines += ["## Every trip right now", "", "| Weekend | " + " | ".join(combos) + " |"]
    lines.append("|---|" + "---|" * len(combos))
    for w in sorted({t["w"] for t in trips.values()}):
        cells = []
        for c in combos:
            t = next((t for t in trips.values() if t["w"] == w and t["c"] == c), None)
            cells.append("—" if not t or t["now"] is None else
                         f"{money(t['now'])}" + (f" ({pct(t['vs'])})" if t["vs"] is not None and t["n"] >= 6 else ""))
        wd = date.fromisoformat(w)
        lines.append(f"| {wd:%b} {wd.day} | " + " | ".join(cells) + " |")
    lines += ["", "_(+/−% = vs. that trip's typical price)_", ""]
    if deals:
        lines += ["## 🔥 Deals", ""] + [f"- **{money(d['now'])}** {d['label']} — {abs(d['vs'])*100:.0f}% below "
                                        f"its typical {money(d['typical'])}" for d in deals] + [""]
    lines.append("**Website:** see README → \"Turn on the website\" for the interactive dashboard.")
    REPORT.write_text("\n".join(lines) + "\n")

    # ---------- alerts
    reasons = []
    if prev_low is not None and now_price <= prev_low - 3 and len(good_best) > 48:
        reasons.append(f"**New all-time low:** {money(now_price)} for "
                       f"{trips.get(now_check['key'], {}).get('label', '')} (previous low {money(prev_low)}).")
    last_alert = {}
    if ALERTED_CSV.exists():
        for r in csv.DictReader(ALERTED_CSV.open()):
            last_alert[r["key"]] = int(r["price"])
    fresh = [t for t in deals + [dict(key=k, **t) for k, t in trips.items() if t in under_target]
             if t["key"] not in last_alert or t["now"] <= last_alert[t["key"]] - 10]
    seen = set()
    fresh = [t for t in fresh if not (t["key"] in seen or seen.add(t["key"]))]
    for t in fresh:
        why = "at/below target" if target and t["now"] <= target else f"{abs(t['vs'])*100:.0f}% below its typical {money(t['typical'])}"
        reasons.append(f"**{money(t['now'])}** — {t['label']} ({why})")
    if fresh:
        new = not ALERTED_CSV.exists()
        with ALERTED_CSV.open("a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["run_id", "key", "price"])
            if new:
                w.writeheader()
            for t in fresh:
                w.writerow({"run_id": latest_run, "key": t["key"], "price": t["now"]})
    if reasons:
        ALERT.write_text("United ORD ⇄ KEF, regular Economy:\n\n" + "\n".join(f"- {r}" for r in reasons)
                         + "\n\nPrices change fast — confirm in Chase Travel before booking.\n")

    # ---------- website
    if TEMPLATE.exists():
        latest_opts = {}
        if LATEST_OPTIONS.exists():
            latest_opts = json.loads(LATEST_OPTIONS.read_text()).get("trips", {})
        data = {
            "updated": checks[-1]["t"], "dest": config.DEST_NAME, "trip": trip_text(),
            "fare": f"{config.ADULTS} adult · {config.SEAT}"
                    + (" (no Basic Economy)" if config.EXCLUDE_BASIC_ECONOMY else ""),
            "target": target, "deal_threshold": config.DEAL_THRESHOLD, "deal_min": config.DEAL_MIN_CHECKS,
            "signal": {"level": sig[0], "label": sig[1], "text": sig[2]},
            "checks": checks, "trips": trips, "options": latest_opts,
            "combos": combos, "weekends": sorted({t["w"] for t in trips.values()}),
            "by_hour": bars(by_hour, hour_label), "by_day": bars(by_day, lambda d: d[:3]),
            "patterns_ready": days_tracked >= MIN_DAYS_FOR_PATTERNS,
            "days_tracked": days_tracked, "min_days": MIN_DAYS_FOR_PATTERNS,
        }
        DASHBOARD.parent.mkdir(exist_ok=True)
        blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
        DASHBOARD.write_text(TEMPLATE.read_text().replace("/*__DATA__*/null", blob))

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a") as fh:
            fh.write(f"alert={'true' if reasons else 'false'}\n")
            fh.write(f"price={num(now_price) or 0}\n")
    print(f"Cheapest {money(now_price)} | low {money(low)} | {sig[1]}: {sig[2]} | alerts: {len(reasons)}")


if __name__ == "__main__":
    main()
