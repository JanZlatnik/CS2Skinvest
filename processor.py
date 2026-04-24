import os, json, requests, time
import pandas as pd
import database
import csf_pricer
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()
API_KEY     = os.getenv("CSFLOAT_API_KEY")
BASE_URL    = "https://csfloat.com/api/v1"
CSFLOAT_CSV = "data/csfloat_parsed.csv"
MANUAL_CSV  = "data/manual_ledger.csv"

WEAR_ORDER = ["Factory New", "Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred"]

database.init_db()


# ── Item helpers ──────────────────────────────────────────────────────────────

def get_item_type(name: str) -> str:
    """Fallback only for manual/bulk imports without API type_name. Returns 'Unknown'."""
    return "Unknown"


ITEM_TYPES = ["Skin", "Container", "Sticker", "Agent", "Charm", "Patch", "Collectible", "Music Kit"]

_CSFLOAT_TYPE_MAP = {
    "skin":        "Skin",
    "container":   "Container",
    "sticker":     "Sticker",
    "agent":       "Agent",
    "charm":       "Charm",
    "patch":       "Patch",
    "collectible": "Collectible",
    "music kit":   "Music Kit",
}

def normalize_item_type(api_type_name: str) -> str:
    """Convert CSFloat API type_name to canonical item type. Falls back to 'Unknown'."""
    return _CSFLOAT_TYPE_MAP.get(api_type_name.lower().strip(), "Unknown")


def get_wear(name: str) -> str | None:
    for w in WEAR_ORDER:
        if f"({w})" in name:
            return w
    return None


def split_item_name(name: str) -> tuple[str, str]:
    if " | " in name:
        weapon, skin = name.split(" | ", 1)
        for w in WEAR_ORDER:
            skin = skin.replace(f" ({w})", "")
        return weapon.strip(), skin.strip()
    return name, ""


# ── Ledger normalization ──────────────────────────────────────────────────────

def _load_ledger() -> pd.DataFrame:
    """Load and normalize combined CSFloat + manual ledger."""
    df_f = pd.read_csv(CSFLOAT_CSV, dtype={"Trade_ID": str}) \
           if os.path.exists(CSFLOAT_CSV) else pd.DataFrame()
    df_m = pd.read_csv(MANUAL_CSV) \
           if os.path.exists(MANUAL_CSV) else pd.DataFrame()
    df = pd.concat([df_f, df_m], ignore_index=True)
    if df.empty:
        return df

    df["Quantity"]  = pd.to_numeric(df["Quantity"],  errors="coerce").fillna(0).astype(int)
    df["Price_USD"] = pd.to_numeric(df["Price_USD"], errors="coerce").fillna(0.0)
    if "Category"   not in df.columns: df["Category"]   = 1
    if "Item_Type"  not in df.columns: df["Item_Type"]   = df["Item_Name"].apply(get_item_type)
    if "Float"      not in df.columns: df["Float"]       = None
    if "Paint_Seed" not in df.columns: df["Paint_Seed"]  = None
    df["Category"]  = pd.to_numeric(df["Category"],  errors="coerce").fillna(1).astype(int)
    df["Float"]     = pd.to_numeric(df["Float"],     errors="coerce")
    # Paint_Seed must always be integer. pd.to_numeric + round handles "651.0" from CSV.
    df["Paint_Seed"] = pd.to_numeric(df["Paint_Seed"], errors="coerce").round(0).astype("Int64")
    return df


def _assign_item_keys(df: pd.DataFrame) -> pd.DataFrame:
    """Add item_key column to ledger DataFrame."""
    def _key(row):
        f4   = round(float(row["Float"]), 4)     if pd.notna(row["Float"])      else None
        seed = int(row["Paint_Seed"])             if pd.notna(row["Paint_Seed"]) else None
        return database.make_item_key(
            row["Item_Name"], int(row["Category"]), row["Item_Type"],
            paint_seed=seed, float_val=f4,
        )
    df = df.copy()
    df["item_key"] = df.apply(_key, axis=1)
    return df


# ── Sync Inventory ────────────────────────────────────────────────────────────

def fetch_csfloat_trades() -> bool:
    """
    Fetch verified trades from CSFloat with full pagination.

    First run  : pages through ALL history until exhausted (~100/page).
    Subsequent : uses `last_trade_id` stored in meta to only fetch new trades,
                 still paginating in case many new trades arrived at once.

    CSFloat returns trades newest-first. We paginate with `page` (0-indexed)
    and stop when a page returns fewer results than the page size (last page).
    On incremental runs we stop as soon as we see a trade_id we already have.

    Saves newest trade_id to meta after a successful full fetch so the next
    incremental run knows where to stop.
    """
    if not API_KEY:
        return False

    headers       = {"Authorization": API_KEY}
    last_trade_id = database.meta_get("last_trade_id")
    PAGE_SIZE     = 100   # CSFloat max per page

    try:
        me = requests.get(f"{BASE_URL}/me", headers=headers, timeout=10)
        me.raise_for_status()
        my_id = me.json().get("user", {}).get("steam_id")

        all_trades: list = []
        page = 0

        while True:
            params: dict = {
                "limit": PAGE_SIZE,
                "state": "verified",
                "page":  page,
            }
            resp = requests.get(f"{BASE_URL}/me/trades", headers=headers,
                                params=params, timeout=15)
            resp.raise_for_status()
            raw    = resp.json()
            trades = raw if isinstance(raw, list) else raw.get("trades", [])

            if not trades:
                break   # no more pages

            # On incremental runs: stop paging once we hit a known trade
            if last_trade_id:
                new_on_page = []
                hit_known   = False
                for t in trades:
                    if str(t.get("id", "")) == last_trade_id:
                        hit_known = True
                        break
                    new_on_page.append(t)
                all_trades.extend(new_on_page)
                if hit_known:
                    break
            else:
                all_trades.extend(trades)

            # If the page was not full we have reached the last page
            if len(trades) < PAGE_SIZE:
                break

            page += 1
            time.sleep(0.3)   # be polite between pagination requests

        if not all_trades:
            return False

        os.makedirs("data", exist_ok=True)
        with open("data/csfloat_raw_new.json", "w", encoding="utf-8") as f:
            json.dump({"trades": all_trades, "my_steam_id": my_id}, f)

        # Save the newest trade id (first in the list — API returns newest-first)
        newest_id = str(all_trades[0].get("id", ""))
        if newest_id:
            database.meta_set("last_trade_id", newest_id)
        return True

    except requests.HTTPError as exc:
        import logging
        logging.getLogger(__name__).error(f"CSFloat trades fetch HTTP error: {exc}")
        return False
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error(f"CSFloat trades fetch error: {exc}")
        return False


def parse_and_append_trades():
    """Parse newly fetched trades and append (deduplicated) to CSV."""
    raw_path = "data/csfloat_raw_new.json"
    if not os.path.exists(raw_path):
        return

    with open(raw_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    trades = data.get("trades", [])
    my_id  = data.get("my_steam_id")
    if not trades:
        return

    records = []
    for t in trades:
        if t.get("state") != "verified":
            continue
        c    = t.get("contract", {})
        i    = c.get("item", {})
        name = i.get("market_hash_name", "Unknown")

        # Use API type_name — authoritative source
        api_type_name = i.get("type_name", "")
        item_type     = normalize_item_type(api_type_name) if api_type_name else "Unknown"

        # paint_seed: integer for Skin, keychain_pattern integer for Charm
        if item_type == "Charm":
            raw_seed = i.get("keychain_pattern")
        else:
            raw_seed = i.get("paint_seed")

        # Strictly store as int or None — never as float
        try:
            paint_seed = int(raw_seed) if raw_seed is not None else None
        except (TypeError, ValueError):
            paint_seed = None

        records.append({
            "Trade_ID":   str(t.get("id", "")),
            "Date":       t.get("created_at", "")[:10],
            "Item_Name":  name,
            "Item_Type":  item_type,
            "Category":   2 if i.get("is_stattrak") else (3 if i.get("is_souvenir") else 1),
            "Float":      i.get("float_value"),
            "Paint_Seed": paint_seed,
            "Action":     "Sell" if t.get("seller_id") == my_id else "Buy",
            "Quantity":   -1    if t.get("seller_id") == my_id else 1,
            "Price_USD":  round(c.get("price", 0) / 100.0, 2),
        })

    if not records:
        return

    new_df = pd.DataFrame(records)
    # Ensure Paint_Seed is stored as integer in CSV (never as 651.0)
    if "Paint_Seed" in new_df.columns:
        new_df["Paint_Seed"] = pd.to_numeric(new_df["Paint_Seed"], errors="coerce") \
                                 .round(0).astype("Int64")
    if os.path.exists(CSFLOAT_CSV):
        existing = pd.read_csv(CSFLOAT_CSV, dtype={"Trade_ID": str})
        if "Trade_ID" in existing.columns:
            new_df = new_df[~new_df["Trade_ID"].isin(existing["Trade_ID"])]
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df

    combined.to_csv(CSFLOAT_CSV, index=False)


def rebuild_inventory():
    """
    Rebuild the inventory table from scratch by replaying the entire ledger.

    avg_cost = weighted avg of BUY prices only.
    Sells reduce quantity but do NOT change avg_cost.
    Items with qty=0 are kept (so their price history stops but record remains).
    """
    df = _load_ledger()
    if df.empty:
        database.upsert_inventory([])
        return

    df = _assign_item_keys(df)
    rows = []

    for key, g in df.groupby("item_key"):
        qty   = int(g["Quantity"].sum())
        buys  = g[g["Quantity"] > 0]
        sells = g[g["Quantity"] < 0]

        avg_cost = (
            (buys["Price_USD"] * buys["Quantity"]).sum() / buys["Quantity"].sum()
            if not buys.empty else 0.0
        )

        # Take metadata from the first buy row (or any row if no buys)
        meta = buys.iloc[0] if not buys.empty else g.iloc[0]
        name  = meta["Item_Name"]
        itype = meta["Item_Type"]
        cat   = int(meta["Category"])

        f4   = round(float(g["Float"].dropna().mean()), 4) \
               if itype in ("Skin", "Knife") and not g["Float"].isnull().all() else None
        seed = int(g["Paint_Seed"].dropna().iloc[0]) \
               if itype in ("Skin", "Knife") and not g["Paint_Seed"].isnull().all() else None

        buy_date = buys["Date"].min() if not buys.empty else None

        rows.append({
            "item_key":  key,
            "item_name": name,
            "item_type": itype,
            "wear":      get_wear(name),
            "category":  cat,
            "float_val": f4,
            "paint_seed":seed,
            "quantity":  qty,
            "avg_cost":  round(avg_cost, 2),
            "buy_date":  buy_date,
        })

    database.upsert_inventory(rows)
    database.meta_set("last_inventory_sync",
                      datetime.now().strftime("%Y-%m-%d %H:%M"))


def sync_inventory() -> int:
    """
    Full Sync Inventory flow.
    Returns number of active items after rebuild.
    """
    if fetch_csfloat_trades():
        parse_and_append_trades()
    rebuild_inventory()
    inv = database.get_active_inventory_df()
    return len(inv)


# ── Sync Prices ───────────────────────────────────────────────────────────────


def fetch_steam_price(item_name: str, delay: float = 1.5) -> float:
    """
    Fetch Steam market lowest price in USD (currency=1).
    `delay` is pre-applied by the caller; this function does not sleep itself.
    Handles both US ($1,234.56) and European (1.234,56) number formats safely.
    """
    url    = "https://steamcommunity.com/market/priceoverview/"
    params = {"appid": 730, "currency": 1, "market_hash_name": item_name}
    try:
        r = requests.get(url, params=params, timeout=8,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 429:
            time.sleep(5)
            r = requests.get(url, params=params, timeout=8,
                             headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            d   = r.json()
            raw = d.get("lowest_price", "") or d.get("median_price", "")
            if raw:
                # Normalise: remove everything except digits, dot, comma
                # then handle both 1,234.56 (US) and 1.234,56 (EU) formats
                digits_only = "".join(ch for ch in raw if ch.isdigit() or ch in ".,")
                if not digits_only:
                    return 0.0
                # If last separator is comma → EU format: swap . and ,
                if digits_only and digits_only[-3] == "," if len(digits_only) >= 3 else False:
                    digits_only = digits_only.replace(".", "").replace(",", ".")
                else:
                    digits_only = digits_only.replace(",", "")
                return round(float(digits_only), 2)
    except Exception:
        pass
    return 0.0


def sync_prices(
    progress_cb=None,
    trigger: str = "manual",
    retry_unpriced: bool = False,
    cf_delay: float = 0.35,
    steam_delay: float = 1.5,
) -> pd.DataFrame:
    """
    Fetch current CF + Steam prices for active inventory items.

    Interleaved mode: fetch CSFloat then Steam for each item before moving to
    the next. This spreads the load evenly and avoids hammering one API
    continuously for large inventories.

    Parameters
    ----------
    progress_cb     : optional callable(pct, msg, log_line | None)
    trigger         : "manual" | "auto" — recorded in sync_log
    retry_unpriced  : if True, also re-fetch items that already have a
                      price_history row today but with cf_price == 0 (no_price)
                      OR stale == 1. Allows a second pass to catch items the
                      first run missed due to rate limiting.
    cf_delay        : seconds to sleep after each CSFloat call (default 0.35s
                      for manual; auto_sync passes a higher value e.g. 1.5s)
    steam_delay     : seconds to sleep after each Steam call
    """
    import uuid

    def _progress(pct: float, msg: str, log_line: str | None = None):
        if progress_cb:
            progress_cb(pct, msg, log_line)

    inv = database.get_active_inventory_df()
    if inv.empty:
        return inv

    already_priced = database.get_items_with_todays_price()

    if retry_unpriced:
        # Include items that were attempted today but returned no price or stale
        unpriced_today = database.get_items_unpriced_today()
        skip_keys      = already_priced - unpriced_today   # priced OK → skip
        inv_todo       = inv[~inv["item_key"].isin(skip_keys)]
    else:
        inv_todo = inv[~inv["item_key"].isin(already_priced)]

    if inv_todo.empty:
        _progress(1.0, "✅ All items already priced today — nothing to fetch.")
        return build_portfolio_from_db()

    total  = len(inv_todo)
    ts     = datetime.now().strftime("%Y-%m-%d %H:%M")
    run_id = uuid.uuid4().hex[:12]

    # Track which steam names we've already fetched this run (dedup across
    # multiple rows with the same item_name, e.g. same skin different floats)
    steam_cache: dict[str, float] = {}
    log_rows:    list[dict]       = []

    # ── Interleaved CSFloat + Steam per item ──────────────────────────────────
    for idx, (_, row) in enumerate(inv_todo.iterrows()):
        name    = row["item_name"]
        key     = row["item_key"]
        pct     = idx / total
        count   = idx + 1

        # — CSFloat —
        _progress(pct, f"📦 ({count}/{total}) CSFloat: {name}", None)
        cf_price, stale, method = csf_pricer.fetch_cf_price(row.to_dict())

        if cf_price > 0 and not stale:
            cf_log = f"✅ {name}: {method} → ${cf_price:.2f}"
        elif stale:
            cf_log = f"⚠️ {name}: stale → ${cf_price:.2f}"
        else:
            cf_log = f"🔴 {name}: no CSFloat price"
        _progress(pct, f"📦 ({count}/{total}) CSFloat: {name}", cf_log)
        time.sleep(cf_delay)

        # — Steam (skip if already fetched for this name in this run) —
        if name not in steam_cache:
            _progress(pct, f"🌐 ({count}/{total}) Steam: {name}", None)
            st_price = fetch_steam_price(name)
            steam_cache[name] = st_price
            st_log = f"   🌐 Steam → ${st_price:.2f}" if st_price else "   🌐 Steam → no price"
            _progress(pct, f"🌐 ({count}/{total}) Steam: {name}", st_log)
            time.sleep(steam_delay)
        else:
            st_price = steam_cache[name]

        # — Save snapshot immediately (don't wait until the end) —
        if cf_price > 0 or st_price > 0:
            database.save_price_snapshot(key, cf_price, st_price, ts, stale=stale)

        log_rows.append({
            "run_id":      run_id,
            "timestamp":   ts,
            "item_key":    key,
            "item_name":   name,
            "item_type":   row.get("item_type", ""),
            "cf_price":    cf_price,
            "steam_price": st_price,
            "method":      method,
            "stale":       int(stale),
            "trigger":     trigger,
        })

    _progress(0.98, "💾 Saving sync log…", "💾 Saving sync log…")
    database.save_sync_log_rows(log_rows)

    portfolio = build_portfolio_from_db()
    database.save_portfolio_snapshot(
        cf_value=portfolio["cf_value"].sum(),
        steam_value=portfolio["steam_value"].sum(),
        total_cost=portfolio["total_cost"].sum(),
        timestamp=ts,
    )

    database.meta_set("last_price_sync", datetime.now().strftime("%Y-%m-%d %H:%M"))
    if trigger == "auto":
        database.meta_set("last_auto_sync", datetime.now().strftime("%Y-%m-%d %H:%M"))
    database.compress_old_price_history()
    database.compress_old_portfolio_snapshots()

    skipped  = len(inv) - len(inv_todo)
    ok_n     = sum(1 for r in log_rows if r["cf_price"] > 0 and not r["stale"])
    miss_n   = sum(1 for r in log_rows if r["cf_price"] == 0)
    done_msg = (f"✅ Done! {ok_n} priced, {miss_n} missing, "
                f"{skipped} skipped (already priced today).")
    _progress(1.0, done_msg, done_msg)

    return portfolio


# ── Read portfolio (fast, from DB) ────────────────────────────────────────────

def build_portfolio_from_db() -> pd.DataFrame:
    """
    No API calls. Reads inventory + latest prices from SQLite.
    Used on every page load.
    """
    inv = database.get_active_inventory_df()
    if inv.empty:
        return inv

    prices = database.get_latest_prices()   # item_key, cf_price, steam_price, cf_stale
    if prices.empty:
        portfolio = inv.copy()
        for col in ("cf_price", "steam_price"):
            portfolio[col] = 0.0
        portfolio["cf_stale"] = False
    else:
        portfolio = inv.merge(prices, on="item_key", how="left")
        portfolio["cf_price"]    = portfolio["cf_price"].fillna(0)
        portfolio["steam_price"] = portfolio["steam_price"].fillna(0)
        portfolio["cf_stale"]    = portfolio["cf_stale"].fillna(False).astype(bool)

    portfolio["cf_value"]   = portfolio["quantity"] * portfolio["cf_price"]
    portfolio["steam_value"]= portfolio["quantity"] * portfolio["steam_price"]
    portfolio["total_cost"] = portfolio["quantity"] * portfolio["avg_cost"]
    portfolio["cf_pnl"]     = portfolio["cf_value"] - portfolio["total_cost"]
    portfolio["steam_pnl"]  = portfolio["steam_value"] - portfolio["total_cost"]

    return portfolio