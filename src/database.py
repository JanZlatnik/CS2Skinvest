"""
Schema
──────
inventory          – one row per unique physical item
                     qty + avg_cost change only on Sync Inventory
                     avg_cost = weighted avg of BUYS only (sells don't touch it)

price_history      – item_key | cf_price | steam_price | timestamp
                     only tracked while quantity > 0
                     compressed to one daily avg per item_key

portfolio_snapshots – cf_value | steam_value | total_cost | timestamp
                      compressed to one daily avg

meta               – key/value store (last_sync, last_trade_id, …)
"""

import sqlite3
import pandas as pd
from datetime import datetime, date

DB_PATH = "data/tracker.db"


def get_conn():
    import os
    os.makedirs("data", exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS inventory (
                item_key   TEXT PRIMARY KEY,
                item_name  TEXT NOT NULL,
                item_type  TEXT NOT NULL,
                wear       TEXT,
                category   INTEGER NOT NULL DEFAULT 1,
                float_val  REAL,
                paint_seed INTEGER,
                quantity   INTEGER NOT NULL DEFAULT 0,
                avg_cost   REAL    NOT NULL DEFAULT 0,
                buy_date   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS price_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                item_key    TEXT NOT NULL,
                cf_price    REAL NOT NULL DEFAULT 0,
                steam_price REAL NOT NULL DEFAULT 0,
                timestamp   TEXT NOT NULL,
                stale       INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                cf_value    REAL NOT NULL DEFAULT 0,
                steam_value REAL NOT NULL DEFAULT 0,
                total_cost  REAL NOT NULL DEFAULT 0,
                timestamp   TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        # ── Sync log: one row per item per sync run ────────────────────────────
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                item_key    TEXT NOT NULL,
                item_name   TEXT NOT NULL,
                item_type   TEXT NOT NULL DEFAULT '',
                cf_price    REAL NOT NULL DEFAULT 0,
                steam_price REAL NOT NULL DEFAULT 0,
                method      TEXT NOT NULL DEFAULT '',
                stale       INTEGER NOT NULL DEFAULT 0,
                trigger     TEXT NOT NULL DEFAULT 'manual'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ph_key   ON price_history(item_key)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ph_ts    ON price_history(timestamp)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sl_runid ON sync_log(run_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sl_ts    ON sync_log(timestamp)")
    migrate_db()


# ── item_key ──────────────────────────────────────────────────────────────────

def make_item_key(item_name: str, category: int, item_type: str,
                  paint_seed=None, float_val=None) -> str:
    """
    Deterministic unique key for one physical item type.
    Skin/Knife:  'AK-47 | Redline (Field-Tested)|1|372|0.1506'
    Others:      'Operation Bravo Case|1'
    """
    if item_type in ("Skin", "Knife"):
        seed = str(int(paint_seed)) if paint_seed is not None else ""
        flt  = f"{round(float(float_val), 4):.4f}" if float_val is not None else ""
        return f"{item_name}|{category}|{seed}|{flt}"
    return f"{item_name}|{category}"


# ── Inventory upserts ─────────────────────────────────────────────────────────

def upsert_inventory(rows: list[dict]):
    """
    Full replace of inventory table from rebuilt ledger state.
    rows = list of dicts with all inventory columns.
    """
    with get_conn() as conn:
        conn.execute("DELETE FROM inventory")
        if rows:
            conn.executemany("""
                INSERT INTO inventory
                    (item_key, item_name, item_type, wear, category,
                     float_val, paint_seed, quantity, avg_cost, buy_date)
                VALUES
                    (:item_key, :item_name, :item_type, :wear, :category,
                     :float_val, :paint_seed, :quantity, :avg_cost, :buy_date)
            """, rows)


def get_inventory_df() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query("SELECT * FROM inventory ORDER BY item_name", conn)


def get_active_inventory_df() -> pd.DataFrame:
    """Items currently in inventory (quantity > 0)."""
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT * FROM inventory WHERE quantity > 0 ORDER BY item_name", conn
        )


# ── Price history ─────────────────────────────────────────────────────────────

def migrate_db():
    """Add any missing columns/tables to existing databases (safe to run on every startup)."""
    with get_conn() as conn:
        # meta table: create if not present (required before any meta_get/set calls)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        # price_history: add stale column if missing
        cols = {r[1] for r in conn.execute("PRAGMA table_info(price_history)").fetchall()}
        if "stale" not in cols:
            conn.execute("ALTER TABLE price_history ADD COLUMN stale INTEGER NOT NULL DEFAULT 0")

        # sync_log: create if not present (for users upgrading from earlier versions)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id      TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                item_key    TEXT NOT NULL,
                item_name   TEXT NOT NULL,
                item_type   TEXT NOT NULL DEFAULT '',
                cf_price    REAL NOT NULL DEFAULT 0,
                steam_price REAL NOT NULL DEFAULT 0,
                method      TEXT NOT NULL DEFAULT '',
                stale       INTEGER NOT NULL DEFAULT 0,
                trigger     TEXT NOT NULL DEFAULT 'manual'
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sl_runid ON sync_log(run_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sl_ts    ON sync_log(timestamp)")


def save_price_snapshot(item_key: str, cf_price: float, steam_price: float,
                        timestamp: str = None, stale: bool = False):
    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO price_history (item_key, cf_price, steam_price, timestamp, stale) "
            "VALUES (?,?,?,?,?)",
            (item_key, cf_price, steam_price, ts, int(stale)),
        )


def get_latest_prices() -> pd.DataFrame:
    """
    Most recent cf_price, steam_price and stale flag per item_key.
    Returns DataFrame with columns: item_key, cf_price, steam_price, cf_stale.
    """
    with get_conn() as conn:
        return pd.read_sql_query("""
            SELECT item_key, cf_price, steam_price,
                   stale as cf_stale
            FROM price_history
            WHERE id IN (SELECT MAX(id) FROM price_history GROUP BY item_key)
        """, conn)


def get_last_known_cf_price(item_key: str) -> tuple[float, bool]:
    """
    Return (price, stale=True) for the most recent CF price for this item_key.
    Returns (0.0, False) if no price exists at all.
    """
    with get_conn() as conn:
        row = conn.execute(
            "SELECT cf_price, stale FROM price_history "
            "WHERE item_key=? AND cf_price > 0 ORDER BY id DESC LIMIT 1",
            (item_key,),
        ).fetchone()
    if row:
        return float(row[0]), True   # always stale=True since it's a carried-forward price
    return 0.0, False


def get_items_with_todays_price() -> set[str]:
    """
    Return set of item_keys that already have a price snapshot for today.
    """
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT item_key FROM price_history "
            "WHERE substr(timestamp,1,10) = ?",
            (today,),
        ).fetchall()
    return {r[0] for r in rows}


def get_price_history_for_item(item_key: str, source: str = "cf") -> pd.DataFrame:
    """
    source: 'cf' or 'steam'
    Returns DataFrame with columns: timestamp, price_usd
    """
    col = "cf_price" if source == "cf" else "steam_price"
    with get_conn() as conn:
        df = pd.read_sql_query(
            f"SELECT timestamp, {col} as price_usd FROM price_history "
            f"WHERE item_key=? AND {col}>0 ORDER BY timestamp",
            conn, params=(item_key,),
        )
    return df


def compress_old_price_history():
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT item_key, substr(timestamp,1,10) as day,
                   AVG(cf_price), AVG(steam_price), MAX(stale)
            FROM price_history
            WHERE substr(timestamp,1,10) < ?
            GROUP BY item_key, day
        """, (today,)).fetchall()
        if not rows:
            return
        conn.execute("DELETE FROM price_history WHERE substr(timestamp,1,10) < ?", (today,))
        conn.executemany(
            "INSERT INTO price_history (item_key, cf_price, steam_price, timestamp, stale) "
            "VALUES (?,?,?,?,?)",
            [(r[0], round(r[2], 4), round(r[3], 4), f"{r[1]} 12:00", r[4]) for r in rows],
        )


# ── Portfolio snapshots ───────────────────────────────────────────────────────

def save_portfolio_snapshot(cf_value: float, steam_value: float,
                            total_cost: float, timestamp: str = None):
    ts = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO portfolio_snapshots (cf_value, steam_value, total_cost, timestamp) "
            "VALUES (?,?,?,?)",
            (cf_value, steam_value, total_cost, ts),
        )


def get_portfolio_history() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            "SELECT timestamp, cf_value, steam_value, total_cost "
            "FROM portfolio_snapshots ORDER BY timestamp",
            conn,
        )


def compress_old_portfolio_snapshots():
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT substr(timestamp,1,10) as day,
                   AVG(cf_value), AVG(steam_value), AVG(total_cost)
            FROM portfolio_snapshots
            WHERE substr(timestamp,1,10) < ?
            GROUP BY day
        """, (today,)).fetchall()
        if not rows:
            return
        conn.execute("DELETE FROM portfolio_snapshots WHERE substr(timestamp,1,10) < ?", (today,))
        conn.executemany(
            "INSERT INTO portfolio_snapshots (timestamp, cf_value, steam_value, total_cost) "
            "VALUES (?,?,?,?)",
            [(f"{r[0]} 12:00", round(r[1],2), round(r[2],2), round(r[3],2)) for r in rows],
        )


# ── Meta ──────────────────────────────────────────────────────────────────────

def meta_set(key: str, value: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO meta (key,value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def meta_get(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None

# ── Sync log ──────────────────────────────────────────────────────────────────

def save_sync_log_rows(rows: list[dict]):
    """
    Persist one sync run's item results to sync_log.
    Each dict must have: run_id, timestamp, item_key, item_name, item_type,
                         cf_price, steam_price, method, stale, trigger
    """
    if not rows:
        return
    with get_conn() as conn:
        conn.executemany("""
            INSERT INTO sync_log
                (run_id, timestamp, item_key, item_name, item_type,
                 cf_price, steam_price, method, stale, trigger)
            VALUES
                (:run_id, :timestamp, :item_key, :item_name, :item_type,
                 :cf_price, :steam_price, :method, :stale, :trigger)
        """, rows)


def get_sync_run_dates() -> list[str]:
    """Return distinct sync run dates (YYYY-MM-DD), newest first."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT DISTINCT substr(timestamp, 1, 10) as day
            FROM sync_log
            ORDER BY day DESC
        """).fetchall()
    return [r[0] for r in rows]


def get_sync_runs_for_date(day: str) -> list[dict]:
    """
    Return list of {run_id, timestamp, trigger, item_count} for a given date.
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT run_id,
                   MIN(timestamp) as timestamp,
                   MAX(trigger)   as trigger,
                   COUNT(*)       as item_count
            FROM sync_log
            WHERE substr(timestamp, 1, 10) = ?
            GROUP BY run_id
            ORDER BY timestamp DESC
        """, (day,)).fetchall()
    return [{"run_id": r[0], "timestamp": r[1],
             "trigger": r[2], "item_count": r[3]} for r in rows]


def get_sync_log_for_run(run_id: str) -> pd.DataFrame:
    """Return all item rows for a specific sync run."""
    with get_conn() as conn:
        return pd.read_sql_query("""
            SELECT item_name, item_type, method, cf_price, steam_price, stale
            FROM sync_log
            WHERE run_id = ?
            ORDER BY item_name
        """, conn, params=(run_id,))


def get_items_unpriced_today() -> set[str]:
    """
    Return set of item_keys that have a price_history row for today BUT
    either cf_price == 0 (no price found) or stale == 1 (carried forward).
    Used by retry_unpriced mode in sync_prices.
    """
    today = date.today().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT item_key FROM price_history "
            "WHERE substr(timestamp,1,10) = ? AND (cf_price = 0 OR stale = 1)",
            (today,),
        ).fetchall()
    return {r[0] for r in rows}


def get_last_two_snapshots() -> tuple[dict | None, dict | None]:
    """
    Return the two most recent portfolio snapshots ordered by timestamp DESC.
    Each snapshot is a dict with keys: cf_value, steam_value, total_cost, timestamp.
    Returns (latest, previous) -- either can be None if not enough snapshots exist.
    Always sorts by timestamp, never by id (id order is unreliable).
    """
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT cf_value, steam_value, total_cost, timestamp
            FROM portfolio_snapshots
            ORDER BY timestamp DESC
            LIMIT 2
        """).fetchall()

    def _to_dict(row) -> dict:
        return {
            "cf_value":    float(row[0]),
            "steam_value": float(row[1]),
            "total_cost":  float(row[2]),
            "timestamp":   row[3],
        }

    latest   = _to_dict(rows[0]) if len(rows) >= 1 else None
    previous = _to_dict(rows[1]) if len(rows) >= 2 else None
    return latest, previous