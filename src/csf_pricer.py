"""
csf_pricer.py
─────────────
Smart CSFloat price fetcher.

Pricing strategy:

NON-SKIN / NON-CHARM  (Container, Sticker, Agent, Patch, …)
  → market_hash_name + buy_now only. No category param (causes errors for these types).

CHARM
  → If we have keychain_pattern: try with paint_seed first, then without.
  → No float.

SKIN (standard)
  Strategy: start broad (whole wear tier), then progressively narrow float range.
  1. Wear-level price  — just market_hash_name, no float filter (baseline, always succeeds if listed)
  2. Float ±10%        — if found, try to narrow to ±5%, then ±1%
  3. Return the most precise price found. If step 1 also failed → stale fallback.

SKIN (pattern-based: Fade, Doppler, Case Hardened, …)
  Same as standard skin BUT paint_seed is always included.
  If paint_seed+float search fails completely → fallback to paint_seed only (no float),
  then to no paint_seed (wear-level), then stale.

NOTE: No `category` param is sent for any request. StatTrak™ / Souvenir is already
encoded in the market_hash_name so the API finds the correct listing automatically.

Wear float boundaries (CS2 standard):
  Factory New    0.00 – 0.07
  Minimal Wear   0.07 – 0.15
  Field-Tested   0.15 – 0.38
  Well-Worn      0.38 – 0.45
  Battle-Scarred 0.45 – 1.00
"""

import os
import time
import requests
from dotenv import load_dotenv
import database

load_dotenv()
API_KEY  = os.getenv("CSFLOAT_API_KEY")
BASE_URL = "https://csfloat.com/api/v1"

# ── Wear boundaries ───────────────────────────────────────────────────────────

WEAR_BOUNDS: dict[str, tuple[float, float]] = {
    "Factory New":     (0.00, 0.07),
    "Minimal Wear":    (0.07, 0.15),
    "Field-Tested":    (0.15, 0.38),
    "Well-Worn":       (0.38, 0.45),
    "Battle-Scarred":  (0.45, 1.00),
}

# ── Pattern-based skins ───────────────────────────────────────────────────────
# Skins where paint_seed significantly affects market price.

PATTERN_BASED_SKINS: frozenset[str] = frozenset({
    "fade",
    "doppler",
    "gamma doppler",
    "marble fade",
    "case hardened",
    "crimson web",
    "blue steel",
    "stained",
})


def _is_pattern_based(item_name: str) -> bool:
    name_lower = item_name.lower()
    return any(skin in name_lower for skin in PATTERN_BASED_SKINS)


# ── Wear clamping ─────────────────────────────────────────────────────────────

def _clamp_to_wear(lo: float, hi: float, wear: str | None) -> tuple[float, float]:
    """Clamp a float range to the wear tier boundaries."""
    if wear and wear in WEAR_BOUNDS:
        w_lo, w_hi = WEAR_BOUNDS[wear]
        lo = max(lo, w_lo)
        hi = min(hi, w_hi)
    return max(lo, 0.0), min(hi, 1.0)


def _wear_bounds(wear: str | None) -> tuple[float, float]:
    """Return the full float bounds for a wear tier (or 0–1 if unknown)."""
    if wear and wear in WEAR_BOUNDS:
        return WEAR_BOUNDS[wear]
    return 0.0, 1.0


# ── Core API call ─────────────────────────────────────────────────────────────

def _fetch_lowest(params: dict) -> float | None:
    """
    Call CSFloat listings endpoint.
    Returns lowest buy_now price in USD, or None if no listings / error.
    Injects type=buy_now and sort_by=lowest_price automatically.
    """
    call_params = {**params, "type": "buy_now", "sort_by": "lowest_price", "limit": 1}
    try:
        r = requests.get(
            f"{BASE_URL}/listings",
            headers={"Authorization": API_KEY},
            params=call_params,
            timeout=6,
        )
        if r.status_code == 429:
            time.sleep(5)
            r = requests.get(
                f"{BASE_URL}/listings",
                headers={"Authorization": API_KEY},
                params=call_params,
                timeout=6,
            )
        if r.status_code != 200:
            return None
        data = r.json()
        listings = data if isinstance(data, list) else data.get("data", [])
        if listings:
            return round(listings[0].get("price", 0) / 100.0, 2)
        return None
    except Exception:
        return None


# ── Result codes (used by sync_page for display) ─────────────────────────────
# "basic" | "float" | "seed" | "seed_float" | "stale" | "no_price"

# ── Public fetch function ─────────────────────────────────────────────────────

def fetch_cf_price(item: dict) -> tuple[float, bool, str]:
    """
    Fetch the best CSFloat floor price for an inventory item.

    Parameters
    ----------
    item : dict / pandas Series with keys:
        item_key   str
        item_name  str
        item_type  str    ("Skin", "Charm", "Container", …)
        float_val  float | None
        paint_seed int   | None   (must be integer — paint_seed or keychain_pattern)
        wear       str   | None

    Returns
    -------
    (price_usd, stale, method)
        price_usd : float  — 0.0 only if stale fallback also had no price
        stale     : bool   — True if carried forward from DB
        method    : str    — one of: "basic" | "float" | "seed" | "seed_float"
                                     "stale" | "no_price"
    """
    name       = item["item_name"]
    itype      = item.get("item_type", "Skin")
    float_val  = item.get("float_val")
    paint_seed = item.get("paint_seed")
    item_key   = item["item_key"]

    # Strict type normalisation
    try:
        float_val = float(float_val) if float_val is not None else None
    except (TypeError, ValueError):
        float_val = None

    try:
        # paint_seed MUST be integer — handles pandas Int64, numpy int64, float "651.0", None, NA
        raw_seed = item.get("paint_seed")
        if raw_seed is None or str(raw_seed) in ("", "nan", "<NA>", "None"):
            paint_seed = None
        else:
            paint_seed = int(float(raw_seed))
    except (TypeError, ValueError):
        paint_seed = None

    base = {"market_hash_name": name}

    # ── Non-skin / non-charm: simple lookup ──────────────────────────────────
    if itype not in ("Skin", "Charm"):
        p = _fetch_lowest(base)
        if p:
            return p, False, "basic"
        last = _get_stale(item_key)
        return last, last > 0, "stale" if last > 0 else "no_price"

    # ── Charm ─────────────────────────────────────────────────────────────────
    # Step 1: name + paint_seed (keychain_pattern) if available
    # Step 2: name only
    if itype == "Charm":
        if paint_seed is not None:
            p = _fetch_lowest({**base, "paint_seed": paint_seed})
            if p:
                return p, False, "seed"
        p = _fetch_lowest(base)
        if p:
            return p, False, "basic"
        last = _get_stale(item_key)
        return last, last > 0, "stale" if last > 0 else "no_price"

    # ── Skin ──────────────────────────────────────────────────────────────────
    pattern_based = _is_pattern_based(name)

    if pattern_based and paint_seed is not None:
        # Step 1: name + paint_seed  (wear already in market_hash_name)
        p = _fetch_lowest({**base, "paint_seed": paint_seed})
        if p:
            # Step 2: add max_float to get more precise price
            if float_val is not None:
                _, w_hi = _wear_bounds(item.get("wear"))
                max_f   = round(min(float_val, w_hi), 6)
                p2 = _fetch_lowest({**base, "paint_seed": paint_seed, "max_float": max_f})
                if p2:
                    return p2, False, "seed_float"
            return p, False, "seed"
        # Seed search found nothing — fall back to basic
        p = _fetch_lowest(base)
        if p:
            return p, False, "basic"
    else:
        # Standard skin
        # Step 1: name only  (wear in market_hash_name)
        p = _fetch_lowest(base)
        if p:
            # Step 2: add max_float
            if float_val is not None:
                _, w_hi = _wear_bounds(item.get("wear"))
                max_f   = round(min(float_val, w_hi), 6)
                p2 = _fetch_lowest({**base, "max_float": max_f})
                if p2:
                    return p2, False, "float"
            return p, False, "basic"

    last = _get_stale(item_key)
    return last, last > 0, "stale" if last > 0 else "no_price"


def _get_stale(item_key: str) -> float:
    last, _ = database.get_last_known_cf_price(item_key)
    return last