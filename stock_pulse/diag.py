"""Transparency dump: full field table + exact heat arithmetic per name."""
import pandas as pd
from pulse import UNIVERSE, WEIGHTS, fetch, raw_metrics

pd.set_option("display.width", 200)
pd.set_option("display.max_rows", 100)
pd.set_option("display.float_format", lambda x: f"{x:8.3f}")

data = fetch(UNIVERSE)
rows = {t: raw_metrics(h) for t, h in data.items()}
df = pd.DataFrame(rows).T

# percentile-rank each component across the field (this is `pr` from pulse.py)
for c in ["mom3", "mom5", "relvol", "breakout"]:
    df[f"r_{c}"] = df[c].rank(pct=True) * 100

df["heat"] = (
    df["r_mom3"] * WEIGHTS["mom3"]
    + df["r_mom5"] * WEIGHTS["mom5"]
    + df["r_relvol"] * WEIGHTS["relvol"]
    + df["r_breakout"] * WEIGHTS["breakout"]
)
df = df.sort_values("heat", ascending=False)

print("\n================ FULL FIELD (raw signals) ================\n")
print(df[["price", "mom3", "mom5", "relvol", "breakout", "accel"]].to_string())

print("\n================ PERCENTILE RANKS (0-100 vs field) ================\n")
print(df[["r_mom3", "r_mom5", "r_relvol", "r_breakout", "heat"]].to_string())

print("\n================ EXACT HEAT ARITHMETIC ================\n")
for t in ["AFRM", "UBER"]:
    if t not in df.index:
        print(f"{t}: not in field this run\n")
        continue
    r = df.loc[t]
    terms = [
        ("r_mom3", WEIGHTS["mom3"]),
        ("r_mom5", WEIGHTS["mom5"]),
        ("r_relvol", WEIGHTS["relvol"]),
        ("r_breakout", WEIGHTS["breakout"]),
    ]
    print(f"{t}:")
    total = 0.0
    for col, w in terms:
        contrib = r[col] * w
        total += contrib
        print(f"   {col:<12} rank {r[col]:6.2f}  x {w:>4}  = {contrib:6.2f}")
    print(f"   {'':<12}{'':>11}            -------")
    print(f"   {'':<12}{'':>11}    HEAT  = {total:6.2f}\n")
