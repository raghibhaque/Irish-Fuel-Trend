import sqlite3
from pathlib import Path

db = Path(__file__).resolve().parents[1] / "data" / "fuel_trend.db"
conn = sqlite3.connect(db)
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute("SELECT COUNT(*) FROM fuel_prices")
print("total rows:", c.fetchone()[0])

c.execute("SELECT MIN(date), MAX(date) FROM fuel_prices WHERE country='IE'")
lo, hi = c.fetchone()
print(f"IE date range: {lo} .. {hi}")

c.execute("""
    SELECT date, fuel_type, price_eur_per_litre
    FROM fuel_prices WHERE country='IE'
    ORDER BY date DESC LIMIT 6
""")
print("\nlatest 6 fuel_prices:")
for r in c.fetchall():
    print(f"  {r['date']}  {r['fuel_type']:6s}  {r['price_eur_per_litre']:.4f}")

c.execute("SELECT COUNT(*), MIN(date), MAX(date) FROM fx_rates")
n, lo, hi = c.fetchone()
print(f"\nfx_rates: {n} rows, {lo} .. {hi}")
c.execute("SELECT date, eur_usd FROM fx_rates ORDER BY date DESC LIMIT 5")
for r in c.fetchall():
    print(f"  {r['date']}  EUR/USD={r['eur_usd']:.4f}")

c.execute("SELECT COUNT(*), MIN(date), MAX(date), source FROM brent_crude GROUP BY source")
for r in c.fetchall():
    print(f"\nbrent_crude [{r['source']}]: {r[0]} rows, {r[1]} .. {r[2]}")
c.execute("SELECT date, price_usd_per_barrel FROM brent_crude ORDER BY date DESC LIMIT 5")
for r in c.fetchall():
    print(f"  {r['date']}  Brent=${r['price_usd_per_barrel']:.2f}")
