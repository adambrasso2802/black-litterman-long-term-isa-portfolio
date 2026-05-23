"""Fetch long-history monthly total-return proxies for the v3 dynamic backtest.

The investable UCITS ETFs in ``portfolio/universe.md`` are too young (most
listed after 2014) to backtest dynamic strategies across real regime changes.
This module fetches *long-history proxies* (indices and US-listed ETFs with
20-25y of data) for each asset-class sleeve, converts USD series to GBP, and
caches everything to ``data/`` so the backtest is reproducible offline.

Source: Yahoo Finance chart JSON endpoint (monthly, dividend-adjusted close).
Macro signals (OECD CLI, UK CPI) come from FRED's open CSV endpoint.

Run::

    python portfolio/data_fetch.py

This writes ``data/prices_gbp.csv``, ``data/prices_native.csv``,
``data/macro.csv`` and ``data/proxy_map.csv``. If the cache already exists the
backtest uses it directly and never re-hits the network.
"""

from __future__ import annotations

import io
import json
import os
import time
import urllib.parse
import urllib.request
from typing import Dict, Optional

import numpy as np
import pandas as pd

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

# --- Proxy map: sleeve ticker -> (Yahoo symbol, currency, description) -------
# Chosen for the *longest available* clean monthly history that represents the
# asset-class risk premium. Mapping is documented in the v3 report.
PROXY_MAP: Dict[str, Dict[str, str]] = {
    "VWRP": {"yahoo": "ACWI",     "ccy": "USD", "desc": "MSCI ACWI (global equity)"},
    "VUSA": {"yahoo": "^GSPC",    "ccy": "USD", "desc": "S&P 500 (US large cap)"},
    "VUKE": {"yahoo": "^FTSE",    "ccy": "GBP", "desc": "FTSE 100 (UK large cap)"},
    "VFEM": {"yahoo": "EEM",      "ccy": "USD", "desc": "iShares MSCI EM"},
    "WLDS": {"yahoo": "IWM",      "ccy": "USD", "desc": "iShares Russell 2000 (small cap)"},
    "IWVL": {"yahoo": "IVE",      "ccy": "USD", "desc": "iShares S&P 500 Value"},
    "IWQU": {"yahoo": "IVW",      "ccy": "USD", "desc": "iShares S&P 500 Growth (quality/growth proxy)"},
    "INFR": {"yahoo": "IGF",      "ccy": "USD", "desc": "iShares Global Infrastructure"},
    "AGGG": {"yahoo": "AGG",      "ccy": "USD", "desc": "iShares US Aggregate Bond"},
    "IGLS": {"yahoo": "SHY",      "ccy": "USD", "desc": "iShares 1-3y Treasury (cash proxy)"},
    "SGLN": {"yahoo": "GLD",      "ccy": "USD", "desc": "SPDR Gold Shares"},
}

# Extra series for signals and for back-filling the late-starting sleeves so the
# common window reaches back to 2004 and captures the GFC (not portfolio holdings).
SIGNAL_SYMBOLS: Dict[str, Dict[str, str]] = {
    "GBPUSD":   {"yahoo": "GBPUSD=X", "ccy": "NA",  "desc": "GBP/USD FX rate"},
    "EFA":      {"yahoo": "EFA",      "ccy": "USD", "desc": "MSCI EAFE (dev ex-US) — VWRP backfill"},
    "XLU":      {"yahoo": "XLU",      "ccy": "USD", "desc": "US utilities — INFR backfill"},
    "GCF":      {"yahoo": "GC=F",     "ccy": "USD", "desc": "COMEX gold continuous — SGLN backfill"},
    "TNX":      {"yahoo": "^TNX",     "ccy": "NA",  "desc": "US 10y Treasury yield (inflation-direction proxy)"},
}

# FRED macro series for the Bridgewater-style regime overlay (Mod 2).
# NOTE: FRED is unreachable from this execution environment (HTTP 503 / blocked
# by network policy), so these are not used; Mod 2 falls back to transparent
# market-based regime proxies built from the price panel (see backtest module).
FRED_SERIES: Dict[str, str] = {
    "OECD_CLI": "OECDLOLITOAASTSAM",   # OECD composite leading indicator, amplitude-adjusted
    "UK_CPI": "GBRCPIALLMINMEI",       # UK CPI all items, index
}

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def _fetch_yahoo_monthly(symbol: str, years: int = 26,
                         retries: int = 4) -> Optional[pd.Series]:
    """Monthly dividend-adjusted close for one Yahoo symbol, as a Series."""
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.parse.quote(symbol)}?range={years}y&interval=1mo"
           f"&includeAdjustedClose=true")
    delay = 2.0
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            raw = urllib.request.urlopen(req, timeout=25).read()
            j = json.loads(raw)
            res = j["chart"]["result"][0]
            ts = res["timestamp"]
            adj = res["indicators"]["adjclose"][0]["adjclose"]
            idx = pd.to_datetime(ts, unit="s").normalize()
            s = pd.Series(adj, index=idx, name=symbol).dropna()
            # Collapse to month-end labels.
            s.index = s.index.to_period("M").to_timestamp("M")
            s = s[~s.index.duplicated(keep="last")]
            return s
        except Exception as exc:  # noqa: BLE001
            print(f"  {symbol}: attempt {attempt + 1} failed ({exc}); "
                  f"retry in {delay:.0f}s")
            time.sleep(delay)
            delay *= 2
    print(f"  {symbol}: GIVING UP after {retries} attempts")
    return None


def _fetch_fred_monthly(series_id: str, retries: int = 4) -> Optional[pd.Series]:
    """Monthly series from FRED's open CSV endpoint."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    delay = 2.0
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            raw = urllib.request.urlopen(req, timeout=25).read().decode()
            df = pd.read_csv(io.StringIO(raw))
            df.columns = ["date", "value"]
            df["date"] = pd.to_datetime(df["date"])
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            s = df.dropna().set_index("date")["value"]
            s.index = s.index.to_period("M").to_timestamp("M")
            s.name = series_id
            return s
        except Exception as exc:  # noqa: BLE001
            print(f"  FRED {series_id}: attempt {attempt + 1} failed ({exc}); "
                  f"retry in {delay:.0f}s")
            time.sleep(delay)
            delay *= 2
    print(f"  FRED {series_id}: GIVING UP")
    return None


def fetch_all(force: bool = False) -> Dict[str, pd.DataFrame]:
    """Fetch (or load cached) prices and macro series. Returns a dict of frames."""
    os.makedirs(DATA_DIR, exist_ok=True)
    px_gbp_path = os.path.join(DATA_DIR, "prices_gbp.csv")
    px_nat_path = os.path.join(DATA_DIR, "prices_native.csv")
    macro_path = os.path.join(DATA_DIR, "macro.csv")
    map_path = os.path.join(DATA_DIR, "proxy_map.csv")

    if (not force and os.path.exists(px_gbp_path) and os.path.exists(macro_path)):
        print(f"Loading cached data from {DATA_DIR} ...")
        return {
            "prices_gbp": pd.read_csv(px_gbp_path, index_col=0, parse_dates=True),
            "prices_native": pd.read_csv(px_nat_path, index_col=0, parse_dates=True),
            "macro": pd.read_csv(macro_path, index_col=0, parse_dates=True),
        }

    print("Fetching long-history proxies from Yahoo Finance (monthly) ...")
    native: Dict[str, pd.Series] = {}
    to_fetch = {**{k: v for k, v in PROXY_MAP.items()},
                **{k: v for k, v in SIGNAL_SYMBOLS.items()}}
    # De-duplicate Yahoo symbols (IVE/IVW appear twice).
    sym_cache: Dict[str, pd.Series] = {}
    for key, meta in to_fetch.items():
        ysym = meta["yahoo"]
        if ysym not in sym_cache:
            print(f"  {key} <- {ysym}")
            s = _fetch_yahoo_monthly(ysym)
            if s is not None:
                sym_cache[ysym] = s
            time.sleep(1.0)  # polite spacing to dodge rate limits
        if ysym in sym_cache:
            native[key] = sym_cache[ysym].copy()

    native_df = pd.DataFrame(native).sort_index()

    # --- Convert USD series to GBP using GBPUSD (price in USD / (USD per GBP)) -
    fx = native_df["GBPUSD"] if "GBPUSD" in native_df else None

    def to_gbp(key: str) -> Optional[pd.Series]:
        if key not in native_df:
            return None
        s = native_df[key].dropna()
        if SIGNAL_SYMBOLS.get(key, PROXY_MAP.get(key, {})).get("ccy") == "USD" and fx is not None:
            s = (s / fx).dropna()
        return s

    gbp: Dict[str, pd.Series] = {}
    for key in PROXY_MAP:
        s = to_gbp(key)
        if s is not None:
            gbp[key] = s

    # --- Back-fill the three late-starting sleeves so the panel reaches 2004 --
    # Splice older proxy *returns* onto the front of the canonical series,
    # chained to the canonical level at the first overlapping month. This is a
    # standard index back-fill; the substitution is documented in the report.
    def splice(canonical: str, backfill_key: str) -> None:
        if canonical not in gbp or backfill_key not in native_df:
            return
        bf = to_gbp(backfill_key)
        canon = gbp[canonical]
        if bf is None or bf.dropna().index[0] >= canon.index[0]:
            return
        bf_ret = bf.pct_change()
        pre = bf_ret.index[bf_ret.index < canon.index[0]]
        if len(pre) == 0:
            return
        level = canon.iloc[0]
        back_levels = {}
        lvl = level
        for dt in reversed(pre):
            r = bf_ret.loc[dt]
            if pd.isna(r) or (1 + r) == 0:
                continue
            lvl = lvl / (1 + r)        # walk the level backwards
            back_levels[dt] = lvl
        spliced = pd.concat([pd.Series(back_levels), canon]).sort_index()
        gbp[canonical] = spliced[~spliced.index.duplicated(keep="last")]

    # VWRP (ACWI from 2008) <- EFA developed-ex-US as a global-equity backfill.
    splice("VWRP", "EFA")
    # INFR (IGF from 2008) <- XLU utilities as a real-asset/infra backfill.
    splice("INFR", "XLU")
    # SGLN (GLD from 2004-12) <- COMEX gold continuous as a gold backfill.
    splice("SGLN", "GCF")

    gbp_df = pd.DataFrame(gbp).sort_index().dropna(how="all")

    # --- Macro: FRED unreachable here, so persist the market-based proxy ------
    # inputs instead. The 10y yield (TNX) is the inflation-direction proxy and
    # VWRP itself supplies the growth-direction proxy (built in the backtest).
    print("Attempting FRED macro series (expected to be blocked) ...")
    macro: Dict[str, pd.Series] = {}
    for key, sid in FRED_SERIES.items():
        s = _fetch_fred_monthly(sid, retries=1)
        if s is not None:
            macro[key] = s
        time.sleep(0.5)
    if "TNX" in native_df:
        macro["US10Y_YIELD"] = native_df["TNX"].dropna()
    macro_df = pd.DataFrame(macro).sort_index()

    # --- Persist ------------------------------------------------------------
    native_df.to_csv(px_nat_path)
    gbp_df.to_csv(px_gbp_path)
    macro_df.to_csv(macro_path)
    pd.DataFrame([
        {"ticker": k, "yahoo": v["yahoo"], "ccy": v["ccy"], "desc": v["desc"]}
        for k, v in PROXY_MAP.items()
    ]).to_csv(map_path, index=False)

    print(f"\nSaved: {px_gbp_path}\n       {px_nat_path}\n       {macro_path}")
    return {"prices_gbp": gbp_df, "prices_native": native_df, "macro": macro_df}


def coverage_report(frames: Dict[str, pd.DataFrame]) -> None:
    """Print first/last date and count for each fetched series."""
    print("\n=== Coverage report ===")
    for name in ("prices_gbp", "macro"):
        df = frames[name]
        print(f"\n[{name}]")
        for col in df.columns:
            s = df[col].dropna()
            if len(s):
                print(f"  {col:14s} {s.index[0].date()} -> {s.index[-1].date()}"
                      f"  ({len(s)} pts)")
            else:
                print(f"  {col:14s} EMPTY")


if __name__ == "__main__":
    frames = fetch_all(force=True)
    coverage_report(frames)
