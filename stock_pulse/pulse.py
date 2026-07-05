"""
stock_pulse - the Ne engine.

A fast pattern-seeker. It samples the whole live field at once, latches hard
onto the names that are firing RIGHT NOW, rides them, and drops them the moment
the pattern decays. Fast and hard in, fast and hard out.

By design there is NO backtester and NO validation gate. This is the seek-latch-drop
machine, nothing else. It tells you what's hot now and what just went cold.

Heat is RELATIVE to the live field (cross-sectional percentile rank), not absolute --
so it's always pointing at whatever is strongest in the universe at this moment.

Run:  python pulse.py
State (what we're currently latched onto) lives in state.json next to this file.
"""

import json
import os
import sys
from datetime import datetime

try:
    import yfinance as yf
    import pandas as pd
except ImportError:
    sys.exit("Need deps:  pip install yfinance pandas")

# ----------------------------- CONFIG (edit freely) -----------------------------

# The live field. Mix of liquid megacaps + high-beta movers so there's always
# something firing. Add/remove anything you want -- this is your hunting ground.
UNIVERSE = [
    # megacap / liquid
    "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN", "GOOGL", "NFLX",
    "AVGO", "MU", "INTC", "QCOM",
    # high-beta / fast movers
    "PLTR", "COIN", "MARA", "RIOT", "SOFI", "HOOD", "AFRM", "RIVN", "LCID",
    "SMCI", "CVNA", "DKNG", "SNAP", "UBER", "GME", "AMC", "ROKU", "DASH",
    # sector / theme ETFs (the tide)
    "SMH", "XLK", "XLE", "XLF", "ARKK", "SPY", "QQQ",
]

HISTORY = "6mo"          # how much price history to pull for context

ENTER_HEAT = 80.0        # latch a NEW name when its heat crosses this (0-100 percentile)
EXIT_HEAT = 55.0         # drop a held name if heat falls below this
DECAY_FROM_PEAK = 0.25   # OR drop it if heat falls 25% from its peak while we held it

# How the heat score is built from the raw signals. All cross-sectional (vs the field).
WEIGHTS = {
    "mom3": 0.35,        # 3-day momentum  -> the fast pulse
    "mom5": 0.25,        # 5-day momentum  -> short trend
    "relvol": 0.20,      # volume vs its 20d avg -> is the crowd showing up
    "breakout": 0.20,    # close vs prior 10d high -> fresh breakout
}

STATE_FILE = os.path.join(os.path.dirname(__file__), "state.json")

# ----------------------------- DATA + METRICS -----------------------------


def fetch(tickers):
    """Pull daily history per ticker. Skips anything that fails or is too short."""
    data = {}
    for t in tickers:
        try:
            h = yf.Ticker(t).history(period=HISTORY, auto_adjust=True)
            if len(h) > 25:
                data[t] = h
        except Exception:
            pass
    return data


def raw_metrics(h):
    """The fast signals for one name, computed off the most recent bars."""
    close = h["Close"]
    vol = h["Volume"]

    mom3 = close.iloc[-1] / close.iloc[-4] - 1
    mom5 = close.iloc[-1] / close.iloc[-6] - 1
    relvol = vol.iloc[-1] / vol.tail(20).mean()
    prior_high = close.iloc[-11:-1].max()
    breakout = close.iloc[-1] / prior_high - 1

    # acceleration: is the fast pulse still RISING, or already rolling over?
    mom3_prev = close.iloc[-2] / close.iloc[-5] - 1
    accel = mom3 - mom3_prev

    return {
        "price": round(float(close.iloc[-1]), 2),
        "mom3": float(mom3),
        "mom5": float(mom5),
        "relvol": float(relvol),
        "breakout": float(breakout),
        "accel": float(accel),
    }


def compute_heat(data):
    """Build a heat table for the whole field. Heat is percentile-ranked vs the field."""
    rows = {}
    for t, h in data.items():
        try:
            rows[t] = raw_metrics(h)
        except Exception:
            pass

    df = pd.DataFrame(rows).T
    if df.empty:
        return df

    def pr(col):  # percentile rank across the live field -> 0..100
        return df[col].rank(pct=True) * 100

    df["heat"] = (
        pr("mom3") * WEIGHTS["mom3"]
        + pr("mom5") * WEIGHTS["mom5"]
        + pr("relvol") * WEIGHTS["relvol"]
        + pr("breakout") * WEIGHTS["breakout"]
    )
    return df.sort_values("heat", ascending=False)


# ----------------------------- STATE (latched positions) -----------------------------


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE) as f:
            return json.load(f)
    return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ----------------------------- THE ENGINE -----------------------------


def run():
    today = datetime.now().strftime("%Y-%m-%d")
    print(f"\n  stock_pulse  -  {today}  -  scanning {len(UNIVERSE)} names\n")

    data = fetch(UNIVERSE)
    df = compute_heat(data)
    if df.empty:
        sys.exit("No data came back (network blocked or all tickers failed).")

    state = load_state()
    ignitions, riding, drops = [], [], []

    for t, row in df.iterrows():
        heat = float(row["heat"])
        accel = float(row["accel"])

        if t in state:
            # we're holding this -- update its peak, then decide ride vs drop
            state[t]["peak_heat"] = max(state[t]["peak_heat"], heat)
            peak = state[t]["peak_heat"]
            decayed = heat < EXIT_HEAT or heat < peak * (1 - DECAY_FROM_PEAK)
            (drops if decayed else riding).append((t, row, dict(state[t])))
        else:
            # fresh name -- latch only if it's HOT and still accelerating
            if heat >= ENTER_HEAT and accel > 0:
                ignitions.append((t, row))

    # apply state changes: latch the new, cut the decayed
    for t, row in ignitions:
        state[t] = {
            "entry_date": today,
            "entry_heat": round(float(row["heat"]), 1),
            "peak_heat": round(float(row["heat"]), 1),
        }
    for t, _, _ in drops:
        del state[t]
    save_state(state)

    _report(ignitions, riding, drops, df)


def _bar(heat):
    n = int(round(heat / 10))
    return "#" * n + "." * (10 - n)


def _report(ignitions, riding, drops, df):
    print("=" * 64)
    print("  DROP NOW  (held, pattern decayed -- cut it fast)")
    print("=" * 64)
    if drops:
        for t, row, s in drops:
            print(f"  {t:<6} heat {row['heat']:5.1f}  peak {s['peak_heat']:5.1f}  "
                  f"in@{s['entry_heat']:5.1f} ({s['entry_date']})  ${row['price']}")
    else:
        print("  (nothing decaying)")

    print("\n" + "=" * 64)
    print("  NEW IGNITIONS  (hot + still accelerating -- latch candidates)")
    print("=" * 64)
    if ignitions:
        for t, row in ignitions:
            print(f"  {t:<6} heat {row['heat']:5.1f} [{_bar(row['heat'])}]  "
                  f"3d {row['mom3']*100:+5.1f}%  vol x{row['relvol']:.1f}  ${row['price']}")
    else:
        print("  (nothing crossing the ignition line right now)")

    print("\n" + "=" * 64)
    print("  RIDING  (held, still strong)")
    print("=" * 64)
    if riding:
        for t, row, s in riding:
            print(f"  {t:<6} heat {row['heat']:5.1f}  peak {s['peak_heat']:5.1f}  "
                  f"3d {row['mom3']*100:+5.1f}%  ${row['price']}")
    else:
        print("  (holding nothing)")

    print("\n" + "-" * 64)
    print("  TOP OF THE FIELD right now (whether we're in it or not)")
    print("-" * 64)
    for t, row in df.head(8).iterrows():
        flag = "*held*" if t in load_state() else ""
        print(f"  {t:<6} heat {row['heat']:5.1f} [{_bar(row['heat'])}]  "
              f"3d {row['mom3']*100:+5.1f}%  vol x{row['relvol']:.1f} {flag}")
    print()


if __name__ == "__main__":
    run()
