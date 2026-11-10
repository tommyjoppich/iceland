"""Build REPORT.md, the website (docs/index.html) and email alerts from data/.

A "trip" is one pair of dates (e.g. Wed Dec 2 → Mon Dec 7). Each trip has a
separate price history per airline (United, Icelandair).

Far-out weekends are only searched every few hours, so "cheapest trip right now"
uses each trip's most recent price (carried forward up to 6 hours).

Alerts (→ alert.md → GitHub issue → email), always naming the airline:
  * a trip drops to/below TARGET_PRICE
  * a trip is DEAL_THRESHOLD below its own typical price on that airline
  * the cheapest trip hits a new all-time low (after the first 2 days)
"""

from __future__ import annotations

import csv
import json
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import config
import tracker

ROOT = Path(__file__).parent
DATA = ROOT / "data"
LATEST_OPTIONS = DATA / "latest_options.json"
ALERTED_CSV = DATA / "alerted.csv"
REPORT = ROOT / "REPORT.md"
TEMPLATE = ROOT / "dashboard_template.html"
DASHBOARD = ROOT / "docs" / "index.html"
ALERT = ROOT / "alert.md"
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY = tracker.DAY
AL = config.AIRLINES
MIN_DAYS_FOR_PATTERNS = 7
CARRY_HOURS = 6              # how long a far-out trip's last price counts as "current"


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
    parts = []
    for t in config.TRIPS:
        s = f"{t['out']}→{t['back']}"
        extra = []
        if t.get("leave_before"):
            extra.append(f"leave before {hour_label(t['leave_before'])}")
        if t.get("land_after"):
            extra.append(f"land after {hour_label(t['land_after'])}")
        parts.append(s + (f" ({', '.join(extra)})" if extra else ""))
    stops = "nonstop" if config.MAX_STOPS == 0 else f"up to {config.MAX_STOPS} stop"
    return f"{', '.join(parts)} · {stops} · through {date.fromisoformat(config.TRACK_UNTIL):%b %Y}"


def load():
    files = sorted(DATA.glob("prices-*.csv"))
    if not files:
        return None
    df = pd.concat([pd.read_csv(f, dtype={"price": "float", "nonstop_price": "float"}) for f in files],
                   ignore_index=True)
    if "airline" not in df:
        df["airline"] = np.nan
    df["airline"] = df["airline"].fillna("UA")          # early files were United-only
    df["key"] = df["depart_date"] + "|" + df["return_date"]
    df["day"] = df["checked_at_local"].str[:10]
    return df


def compact_hist(h: pd.DataFrame, run_idx: dict, last_t: pd.Timestamp) -> list:
    """Every check from the last 7 days; one point per day (the day's low) before that."""
    h = h.assign(t=pd.to_datetime(h["checked_at_local"]))
    recent = h[h["t"] >= last_t - pd.Timedelta(days=7)]
    older = h[h["t"] < last_t - pd.Timedelta(days=7)]
    older = older.loc[older.groupby("day")["price"].idxmin()] if not older.empty else older
    both = pd.concat([older, recent]).sort_values("t")
    return [[run_idx[r.run_id], int(r.price)] for r in both.itertuples()]


def main():
    df = load()
    if df is None:
        print("No data yet.")
        return
    if ALERT.exists():
        ALERT.unlink()

    run_meta = (df.groupby("run_id").agg(t=("checked_at_local", "first")).sort_index())
    runs = list(run_meta.index)
    run_idx = {r: i for i, r in enumerate(runs)}
    run_t = pd.to_datetime(run_meta["t"])
    last_t = run_t.iloc[-1]
    today = last_t.date()
    ok = df[df["status"] == "ok"].copy()

    # ---------- "current" price grid: runs × (trip, airline), carried forward
    marks = df[df["status"].isin(["ok", "none"])].copy()
    marks["v"] = marks["price"].where(marks["status"] == "ok", np.inf)   # inf = "no flights"
    wide = marks.pivot_table(index="run_id", columns=["key", "airline"], values="v", aggfunc="min")
    wide = wide.reindex(runs)
    # carry forward by time, not by run count
    wide.index = run_t.values
    wide = wide.sort_index()
    filled = wide.ffill()
    last_seen = pd.DataFrame(np.where(wide.notna(), wide.index.values[:, None], np.datetime64("NaT")),
                             index=wide.index, columns=wide.columns).ffill()
    age = (pd.Series(wide.index, index=wide.index).values[:, None] - last_seen.values)
    filled = filled.where(age <= np.timedelta64(CARRY_HOURS, "h"))
    dep = np.array([np.datetime64(k.split("|")[0]) for k, _ in wide.columns])
    run_days = wide.index.values.astype("datetime64[D]")
    departed = dep[None, :] <= run_days[:, None]
    filled = filled.mask(departed).replace(np.inf, np.nan)
    filled.index = runs

    def best_of(cols):
        if not cols:
            return pd.Series(np.nan, index=runs), pd.Series([None] * len(runs), index=runs, dtype=object)
        vals = filled[cols].to_numpy(dtype=float)
        has = ~np.isnan(vals).all(axis=1)
        i = np.where(has, np.nanargmin(np.where(np.isnan(vals), np.inf, vals), axis=1), 0)
        mn = pd.Series(np.where(has, vals[np.arange(len(vals)), i], np.nan), index=runs)
        arg = pd.Series([cols[j] if h else None for j, h in zip(i, has)], index=runs, dtype=object)
        return mn, arg

    all_cols = list(filled.columns)
    best_all, arg_all = best_of(all_cols)
    best_by = {code: best_of([c for c in all_cols if c[1] == code])[0] for code in AL}

    # ---------- one row per check
    per_run = df.groupby("run_id").agg(
        tot=("key", "nunique"),
        n=("status", lambda s: int((s == "ok").sum())),
        errs=("status", lambda s: int((s == "error").sum())),
        err=("error", lambda s: next((str(x) for x in s if isinstance(x, str) and x and x != "no matching flights"), None)),
    )
    checks = []
    for i, r in enumerate(runs):
        b = best_all.iloc[i]
        a = arg_all.iloc[i] if pd.notna(b) else None
        pr = per_run.loc[r]
        checks.append({
            "t": run_meta.loc[r, "t"], "best": num(b),
            "key": a[0] if a is not None and isinstance(a, tuple) else None,
            "al": a[1] if a is not None and isinstance(a, tuple) else None,
            **{code.lower(): num(best_by[code].iloc[i]) for code in AL},
            "n": int(pr["n"]), "tot": int(pr["tot"]),
            "err": (f"{int(pr['errs'] // len(AL))} searches failed" if pr["errs"] else None)
                   if pd.notna(b) else (pr["err"] or "no prices"),
        })
    good_best = best_all.dropna().reset_index(drop=True)
    good_idx = [i for i in range(len(runs)) if pd.notna(best_all.iloc[i])]

    # ---------- per trip × airline
    all_trips = tracker.upcoming_trips(today)
    latest_row = (df.sort_values("run_id").groupby(["key", "airline"]).tail(1)
                    .set_index(["key", "airline"]))
    hist_g = ok.groupby(["key", "airline"])
    trips = {}
    for anchor, combo, out, back, _ in all_trips:
        k = f"{out.isoformat()}|{back.isoformat()}"
        t = {"w": anchor.isoformat(), "c": combo, "out": out.isoformat(), "back": back.isoformat(),
             "label": trip_label(out.isoformat(), back.isoformat()), "a": {}}
        for code in AL:
            e = {"now": None, "stops": None, "flight": None, "typical": None, "low": None,
                 "high": None, "vs": None, "n": 0, "hist": [], "status": "pending", "err": None,
                 "checked": None}
            if (k, code) in latest_row.index:
                lr = latest_row.loc[(k, code)]
                e["status"], e["checked"] = lr["status"], lr["checked_at_local"]
                if lr["status"] == "ok":
                    e.update(now=num(lr["price"]), stops=num(lr["stops"]), flight=lr["flight"])
                elif lr["status"] == "error":
                    e["err"] = str(lr["error"])[:120]
            if (k, code) in hist_g.groups:
                h = hist_g.get_group((k, code))
                e.update(typical=float(h["price"].median()), low=num(h["price"].min()),
                         high=num(h["price"].max()), n=int(h["run_id"].nunique()),
                         hist=compact_hist(h, run_idx, last_t))
                if e["now"] is not None:
                    e["vs"] = e["now"] / e["typical"] - 1
            t["a"][code] = e
        trips[k] = t

    # ---------- deals
    deals = [dict(key=k, al=code, label=t["label"], **e) for k, t in trips.items()
             for code, e in t["a"].items()
             if e["now"] is not None and e["n"] >= config.DEAL_MIN_CHECKS
             and e["now"] <= e["typical"] * (1 - config.DEAL_THRESHOLD)]
    deals.sort(key=lambda d: d["vs"])

    # ---------- headline + signal
    have = not good_best.empty
    now_price = good_best.iloc[-1] if have else None
    now_check = checks[good_idx[-1]] if have else None
    low = good_best.min() if have else None
    low_check = checks[good_idx[int(good_best.idxmin())]] if have else None
    prev_low = good_best.iloc[:-1].min() if len(good_best) > 1 else None
    pct_cheaper = (good_best < now_price).mean() * 100 if have else 0
    days_tracked = (last_t.date() - run_t.iloc[0].date()).days
    target = config.TARGET_PRICE
    under_target = [dict(key=k, al=code, label=t["label"], **e) for k, t in trips.items()
                    for code, e in t["a"].items() if target and e["now"] is not None and e["now"] <= target]
    now_desc = (f"{trips[now_check['key']]['label']} on {AL[now_check['al']]}"
                if have and now_check["key"] in trips else "")

    if not have:
        sig = ("wait", "NO DATA", "No prices found yet.")
    elif under_target:
        b = min(under_target, key=lambda d: d["now"])
        sig = ("buy", "BUY", f"{len(under_target)} fare{'s' if len(under_target) > 1 else ''} at or below your "
                             f"{money(target)} target — best: {b['label']} on {AL[b['al']]} at {money(b['now'])}.")
    elif now_price == low and len(good_best) > 48:
        sig = ("buy", "BUY", f"Lowest price the bot has ever seen: {money(now_price)} ({now_desc}).")
    elif deals:
        d = deals[0]
        sig = ("good", "DEAL", f"{len(deals)} fare{'s are' if len(deals) > 1 else ' is'} "
                               f"{config.DEAL_THRESHOLD*100:.0f}%+ below usual — best: {d['label']} on "
                               f"{AL[d['al']]} at {money(d['now'])}.")
    elif now_price <= low * 1.03:
        sig = ("good", "GOOD PRICE", f"Within 3% of the all-time low ({money(low)}).")
    else:
        sig = ("wait", "WAIT", f"{pct_cheaper:.0f}% of past checks had a cheaper trip; the low is {money(low)}.")

    # ---------- buy-time patterns (fare vs same trip+airline that day / that week)
    ok["week"] = pd.to_datetime(ok["day"]).dt.to_period("W").astype(str)
    rel_d = ok.assign(rel=ok["price"] - ok.groupby(["key", "airline", "day"])["price"].transform("mean"))
    rel_w = ok.assign(rel=ok["price"] - ok.groupby(["key", "airline", "week"])["price"].transform("mean"))
    by_hour = rel_d.groupby("local_hour").agg(v=("rel", "mean"), avg=("price", "mean"),
                                              n=("run_id", "nunique")).sort_index()
    by_day = rel_w.groupby("local_weekday").agg(v=("rel", "mean"), avg=("price", "mean"),
                                                n=("run_id", "nunique"))
    by_day = by_day.reindex([d for d in WEEKDAYS if d in by_day.index])
    bars = lambda agg, lab: [{"k": lab(k), "v": round(float(r.v), 1), "avg": num(r.avg), "n": int(r.n)}
                             for k, r in agg.iterrows()] if len(agg) > 1 else []

    order = {f"{t['out']}→{t['back']}": i for i, t in enumerate(config.TRIPS)}
    combos = sorted({t["c"] for t in trips.values()}, key=lambda c: order.get(c, 99))
    weekends = sorted({t["w"] for t in trips.values()})

    # ---------- REPORT.md
    lines = [f"# 🇮🇸 Chicago ⇄ {config.DEST_NAME} fare tracker", "",
             f"{' & '.join(AL.values())} · regular Economy · {trip_text()}",
             f"_Updated {checks[-1]['t']} Chicago time · {len(runs)} checks so far_", "",
             f"**{sig[1]}** — {sig[2]}", ""]
    if have:
        lines += ["| | |", "|---|---|",
                  f"| **Cheapest trip now** | **{money(now_price)}** · {now_desc} |",
                  f"| All-time low | {money(low)} on {low_check['t']} |",
                  f"| Typical cheapest | {money(good_best.median())} |", ""]
    lines += ["## Every trip right now (cheapest airline shown)", "",
              "| Weekend | " + " | ".join(combos) + " |", "|---|" + "---|" * len(combos)]
    for w in weekends:
        cells = []
        for c in combos:
            t = next((t for t in trips.values() if t["w"] == w and t["c"] == c), None)
            prices = [(e["now"], code) for code, e in (t["a"].items() if t else []) if e["now"] is not None]
            if not prices:
                cells.append("—")
            else:
                p, code = min(prices)
                cells.append(f"{money(p)} {AL[code]}")
        wd = date.fromisoformat(w)
        lines.append(f"| {wd:%b} {wd.day} | " + " | ".join(cells) + " |")
    lines.append("")
    if deals:
        lines += ["## 🔥 Deals", ""] + [f"- **{money(d['now'])} on {AL[d['al']]}** — {d['label']} "
                                        f"({abs(d['vs'])*100:.0f}% below its typical {money(d['typical'])})"
                                        for d in deals] + [""]
    lines.append("**Website:** see README → \"Turn on the website\" for the interactive dashboard.")
    REPORT.write_text("\n".join(lines) + "\n")

    # ---------- alerts
    reasons = []
    if prev_low is not None and now_price <= prev_low - 3 and len(good_best) > 48:
        reasons.append(f"**New all-time low: {money(now_price)}** — {now_desc} (previous low {money(prev_low)}).")
    last_alert = {}
    if ALERTED_CSV.exists():
        for r in csv.DictReader(ALERTED_CSV.open()):
            last_alert[r["key"]] = int(r["price"])
    cand, seen = [], set()
    for d in deals + under_target:
        ak = f"{d['key']}|{d['al']}"
        if ak in seen:
            continue
        seen.add(ak)
        if ak not in last_alert or d["now"] <= last_alert[ak] - 10:
            cand.append((ak, d))
    for ak, d in cand:
        why = ("at/below your target" if target and d["now"] <= target
               else f"{abs(d['vs'])*100:.0f}% below its typical {money(d['typical'])}")
        reasons.append(f"**{money(d['now'])} on {AL[d['al']]}** — {d['label']} ({why})")
    if cand:
        new = not ALERTED_CSV.exists()
        with ALERTED_CSV.open("a", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["run_id", "key", "price"])
            if new:
                w.writeheader()
            for ak, d in cand:
                w.writerow({"run_id": runs[-1], "key": ak, "price": d["now"]})
    if reasons:
        ALERT.write_text(f"ORD ⇄ KEF, regular Economy ({' & '.join(AL.values())}):\n\n"
                         + "\n".join(f"- {r}" for r in reasons)
                         + "\n\nPrices change fast — confirm in Chase Travel before booking.\n")

    # ---------- website
    if TEMPLATE.exists():
        latest_opts = json.loads(LATEST_OPTIONS.read_text()).get("trips", {}) if LATEST_OPTIONS.exists() else {}
        data = {
            "updated": checks[-1]["t"], "dest": config.DEST_NAME, "trip": trip_text(),
            "fare": f"{config.ADULTS} adult · {config.SEAT}"
                    + (" (no Basic Economy)" if config.EXCLUDE_BASIC_ECONOMY else ""),
            "airlines": AL, "target": target,
            "signal": {"level": sig[0], "label": sig[1], "text": sig[2]},
            "checks": checks, "trips": trips, "options": latest_opts,
            "combos": combos, "weekends": weekends,
            "hourly_weekends": config.HOURLY_WEEKENDS, "far_every": config.FAR_EVERY_HOURS,
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
    print(f"Cheapest {money(now_price)} ({now_desc}) | low {money(low)} | {sig[1]}: {sig[2]} | alerts: {len(reasons)}")


if __name__ == "__main__":
    main()
