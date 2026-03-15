#!/usr/bin/env python3
# sec_xbrl_normalizer.py
# --------------------------------------------------------------
# A lightweight, dependency-minimal Python CLI that pulls SEC EDGAR
# XBRL facts via the official data.sec.gov JSON APIs, filters for a
# specific 10-K accession, and normalizes a set of core metrics.
#
# Usage examples:
#   python sec_xbrl_normalizer.py --ticker AAPL --years 3 --out aapl_financials.json 
#   python sec_xbrl_normalizer.py --cik 0000320193 --accn 0000320193-23-000106 --out apple_2023.json
#
# Notes:
#   • The script uses only the standard library + 'requests'. Install requests if needed:
#       pip install requests
#   • SEC Fair Access: set a descriptive User-Agent that includes an email address.
#       export SEC_USER_AGENT="YourApp/1.0 (your.name@example.com)"
#     If not set, the script will prompt on first run.
#   • The script queries:
#       - https://www.sec.gov/files/company_tickers.json (ticker→CIK lookup)
#       - https://data.sec.gov/submissions/CIK##########.json (filing list)
#       - https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json (all XBRL facts)
#     It then filters companyfacts by the accession number(s) of selected 10‑K filings.
#
# Output structure (per filing year):
#   {
#     "entity": {"name": str, "cik": str, "ticker": str|None},
#     "filing": {"form": "10-K", "accn": str, "filed": "YYYY-MM-DD", "fy": int, "fp": "FY", "period_end": "YYYY-MM-DD"},
#     "normalized": {
#         "revenue": number|null,
#         "operating_income_ebit": number|null,
#         "pre_tax_income": number|null,
#         "income_tax_expense": number|null,
#         "depreciation_and_amortization": number|null,
#         "capex": number|null,              # normalized as a POSITIVE cash outflow
#         "working_capital": number|null,    # reported or computed as current assets - current liabilities
#         "total_cash_and_equivalents": number|null,
#         "total_debt": number|null,
#         "shares_outstanding": number|null  # end-of-period common shares outstanding
#     },
#     "units": {  # units for each numeric field (e.g., USD, shares)
#         ...
#     },
#     "sources": { # map of field → { tag, unit, accn, filed }
#         ...
#     }
#   }
# --------------------------------------------------------------

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

try:
    import requests  # type: ignore
except Exception:
    import subprocess
    import sys as _sys
    print("'requests' not found; attempting to install via pip...", file=sys.stderr)
    try:
        subprocess.check_call([_sys.executable, "-m", "pip", "install", "--user", "requests"])
        import requests  # type: ignore
    except Exception as _e:
        print("Failed to install 'requests' automatically:", _e, file=sys.stderr)
        print("Please install requests manually: python -m pip install requests", file=sys.stderr)
        raise

SEC_TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

RATE_DELAY_S = 0.25  # Be gentle: 4 requests/second (< 10 rps guideline)

# ------------------------------ Utilities ------------------------------

def get_user_agent() -> str:
    ua = os.getenv("SEC_USER_AGENT") or os.getenv("SEC_IDENTITY")
    if not ua:
        # Interactive prompt only if running in a TTY
        if sys.stdin.isatty():
            ua = input("Enter SEC User-Agent (include an email, e.g., YourApp/1.0 (you@example.com)): ").strip()
        else:
            ua = "XBRL-Normalizer/1.0 (your.email@example.com)"  # generic fallback - override via env var
    return ua

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": get_user_agent(),
    "Accept-Encoding": "gzip, deflate",
})


def sec_get(url: str) -> requests.Response:
    """GET wrapper with minimal politeness and error handling."""
    time.sleep(RATE_DELAY_S)
    resp = SESSION.get(url, timeout=30)
    resp.raise_for_status()
    return resp


def normalize_cik(cik_or_ticker: str) -> Tuple[str, Optional[str]]:
    """Resolve input (CIK or ticker) to a 10-digit, zero-padded CIK, returning (cik10, ticker_or_none)."""
    candidate = cik_or_ticker.strip().upper()
    if candidate.isdigit():
        cik10 = candidate.zfill(10)
        return cik10, None
    # Resolve ticker → CIK using SEC mapping
    data = sec_get(SEC_TICKER_MAP_URL).json()
    # The JSON is an object keyed by index {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    by_ticker = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in data.values()}
    cik10 = by_ticker.get(candidate)
    if not cik10:
        raise ValueError(f"Ticker not found in SEC mapping: {candidate}")
    return cik10, candidate


@dataclass
class Filing:
    form: str
    accn: str
    filed: str
    report_date: Optional[str]
    fy: Optional[int]
    fp: Optional[str]


def list_10k_filings(cik10: str, include_amends: bool = False, limit_years: Optional[int] = None) -> List[Filing]:
    """Load submissions JSON and return recent 10‑K filings (optionally including 10‑K/A)."""
    url = SEC_SUBMISSIONS_URL.format(cik=cik10)
    subs = sec_get(url).json()
    recent = subs.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    filed = recent.get("filingDate", [])
    report_date = recent.get("reportDate", [])
    fy = recent.get("fy", [])
    fp = recent.get("fp", [])

    filings: List[Filing] = []
    for i, form in enumerate(forms):
        if form == "10-K" or (include_amends and form == "10-K/A"):
            filings.append(Filing(
                form=form,
                accn=accns[i],
                filed=filed[i],
                report_date=report_date[i] if i < len(report_date) else None,
                fy=int(fy[i]) if i < len(fy) and str(fy[i]).isdigit() else None,
                fp=fp[i] if i < len(fp) else None,
            ))

    # Sort by filed date desc
    filings.sort(key=lambda f: f.filed, reverse=True)

    if limit_years is not None:
        # Keep up to N unique fiscal years
        seen = set()
        trimmed = []
        for f in filings:
            key = f.fy or f.filed[:4]
            if key not in seen:
                trimmed.append(f)
                seen.add(key)
            if len(trimmed) >= limit_years:
                break
        filings = trimmed

    return filings


# Preferred tag lists for each metric (ordered by priority)
GAAP = "us-gaap"

PREFERRED_TAGS = {
    "revenue": [
        "Revenues",
        "SalesRevenueNet",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueGoodsNet",
        "SalesRevenueServicesNet",
    ],
    "operating_income_ebit": [
        "OperatingIncomeLoss",
        "EarningsBeforeInterestAndTaxes",
    ],
    "pre_tax_income": [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxes",
        "IncomeBeforeEquityMethodInvestments",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    ],
    "income_tax_expense": [
        "IncomeTaxExpenseBenefit",
        "IncomeTaxExpense",
    ],
    "depreciation_and_amortization": [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet",
        "Depreciation",
        "AmortizationOfIntangibleAssets",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PurchaseOfPropertyPlantAndEquipment",
        "CapitalExpenditures",
    ],
    "working_capital": [
        "WorkingCapital",
    ],
    "cash_and_equivalents": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "cash_and_shortterm_investments": [
        "CashCashEquivalentsAndShortTermInvestments",
        "ShortTermInvestments",
    ],
    "current_assets": [
        "AssetsCurrent",
    ],
    "current_liabilities": [
        "LiabilitiesCurrent",
    ],
    "debt_current": [
        "LongTermDebtCurrent",
        "DebtCurrent",
        "ShortTermBorrowings",
    ],
    "debt_noncurrent": [
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
        "DebtNoncurrent",
    ],
    "shares_outstanding": [
        "EntityCommonStockSharesOutstanding",
        "CommonStockSharesOutstanding",
        "CommonStockSharesIssued",
    ],
}


@dataclass
class FactPick:
    tag: str
    unit: str
    value: float
    end: Optional[str]
    fy: Optional[int]
    fp: Optional[str]
    accn: str
    filed: str


def _iter_units(fact_obj: Dict[str, Any]):
    units = fact_obj.get("units", {})
    for unit, entries in units.items():
        for e in entries:
            yield unit, e


def _is_duration(entry: Dict[str, Any]) -> bool:
    return bool(entry.get("start") and entry.get("end"))


def _is_instant(entry: Dict[str, Any]) -> bool:
    return bool(entry.get("end") and not entry.get("start"))


def pick_fact_for_accn(facts: Dict[str, Any], accn: str, tag_list: List[str], *, want_duration: Optional[bool]=None,
                       prefer_unit: Optional[str] = None) -> Optional[FactPick]:
    """Pick the first available fact for a given accession from a list of preferred tags.

    Args:
        facts: the 'facts' object under companyfacts (dict of taxonomies)
        accn: accession number to filter
        tag_list: ordered list of tags to try (within GAAP taxonomy)
        want_duration: True for period (IS/CF), False for instant (BS), None for either
        prefer_unit: e.g., 'USD', 'shares'
    """
    usgaap = facts.get(GAAP, {})
    best: Optional[FactPick] = None

    for tag in tag_list:
        obj = usgaap.get(tag)
        if not obj:
            continue
        for unit, entry in _iter_units(obj):
            if prefer_unit and unit != prefer_unit:
                continue
            # Only keep entries for the chosen accession
            if entry.get("accn") != accn:
                continue
            # Filter by duration/instant if requested
            if want_duration is True and not _is_duration(entry):
                continue
            if want_duration is False and not _is_instant(entry):
                continue
            # Keep FY/FP FY only (10-K should be FY)
            if entry.get("form") not in ("10-K", "10-K/A"):
                continue
            # Prefer the first occurrence in priority order
            best = FactPick(
                tag=tag,
                unit=unit,
                value=float(entry.get("val")),
                end=entry.get("end"),
                fy=entry.get("fy"),
                fp=entry.get("fp"),
                accn=entry.get("accn"),
                filed=entry.get("filed"),
            )
            return best
    return best


def load_companyfacts(cik10: str) -> Dict[str, Any]:
    url = SEC_COMPANYFACTS_URL.format(cik=cik10)
    return sec_get(url).json()


def normalize_metrics_for_filing(companyfacts: Dict[str, Any], filing: Filing) -> Dict[str, Any]:
    facts = companyfacts.get("facts", {})

    # Income Statement (duration)
    revenue = pick_fact_for_accn(facts, filing.accn, PREFERRED_TAGS["revenue"], want_duration=True, prefer_unit="USD")
    op_inc = pick_fact_for_accn(facts, filing.accn, PREFERRED_TAGS["operating_income_ebit"], want_duration=True, prefer_unit="USD")
    pre_tax = pick_fact_for_accn(facts, filing.accn, PREFERRED_TAGS["pre_tax_income"], want_duration=True, prefer_unit="USD")
    tax_exp = pick_fact_for_accn(facts, filing.accn, PREFERRED_TAGS["income_tax_expense"], want_duration=True, prefer_unit="USD")

    # D&A: may be combined or separate — try combined tag first; if not, try to sum separate comps
    da = pick_fact_for_accn(facts, filing.accn, [
        "DepreciationDepletionAndAmortization",
        "DepreciationAndAmortization",
        "DepreciationAmortizationAndAccretionNet"
    ], want_duration=True, prefer_unit="USD")
    if not da:
        dep = pick_fact_for_accn(facts, filing.accn, ["Depreciation"], want_duration=True, prefer_unit="USD")
        amo = pick_fact_for_accn(facts, filing.accn, ["AmortizationOfIntangibleAssets"], want_duration=True, prefer_unit="USD")
        if dep and amo and dep.unit == amo.unit:
            da = FactPick(tag="DepreciationPlusAmortization (computed)", unit=dep.unit, value=dep.value + amo.value,
                          end=dep.end or amo.end, fy=dep.fy or amo.fy, fp=dep.fp or amo.fp, accn=filing.accn, filed=filing.filed)
        else:
            da = dep or amo

    # CapEx: Cash Flow (duration). Normalize to POSITIVE outflow.
    capex = pick_fact_for_accn(facts, filing.accn, PREFERRED_TAGS["capex"], want_duration=True, prefer_unit="USD")
    if capex:
        capex_value = abs(capex.value)  # normalize to positive outflow
    else:
        capex_value = None

    # Balance Sheet (instant)
    wc = pick_fact_for_accn(facts, filing.accn, PREFERRED_TAGS["working_capital"], want_duration=False, prefer_unit="USD")
    ca = pick_fact_for_accn(facts, filing.accn, PREFERRED_TAGS["current_assets"], want_duration=False, prefer_unit="USD")
    cl = pick_fact_for_accn(facts, filing.accn, PREFERRED_TAGS["current_liabilities"], want_duration=False, prefer_unit="USD")

    if not wc and ca and cl and (ca.unit == cl.unit):
        # Compute working capital
        wc = FactPick(tag="WorkingCapital (computed AssetsCurrent - LiabilitiesCurrent)", unit=ca.unit,
                      value=ca.value - cl.value, end=ca.end or cl.end, fy=filing.fy, fp=filing.fp,
                      accn=filing.accn, filed=filing.filed)

    # Cash & equivalents (instant)
    cash = pick_fact_for_accn(facts, filing.accn, PREFERRED_TAGS["cash_and_equivalents"], want_duration=False, prefer_unit="USD")
    st_invest = pick_fact_for_accn(facts, filing.accn, PREFERRED_TAGS["cash_and_shortterm_investments"], want_duration=False, prefer_unit="USD")
    total_cash = None
    cash_unit = None
    if cash and st_invest and cash.unit == st_invest.unit:
        # Some tags like CashCashEquivalentsAndShortTermInvestments already include both; avoid double-count by preferring that single tag if found
        if st_invest.tag == "CashCashEquivalentsAndShortTermInvestments":
            total_cash = st_invest.value
            cash_unit = st_invest.unit
        else:
            total_cash = cash.value + st_invest.value
            cash_unit = cash.unit
    elif st_invest:
        total_cash = st_invest.value
        cash_unit = st_invest.unit
    elif cash:
        total_cash = cash.value
        cash_unit = cash.unit

    # Debt (instant): current + noncurrent
    debt_c = pick_fact_for_accn(facts, filing.accn, PREFERRED_TAGS["debt_current"], want_duration=False, prefer_unit="USD")
    debt_nc = pick_fact_for_accn(facts, filing.accn, PREFERRED_TAGS["debt_noncurrent"], want_duration=False, prefer_unit="USD")
    total_debt = None
    debt_unit = None
    if debt_c and debt_nc and debt_c.unit == debt_nc.unit:
        total_debt = debt_c.value + debt_nc.value
        debt_unit = debt_c.unit
    elif debt_c:
        total_debt = debt_c.value
        debt_unit = debt_c.unit
    elif debt_nc:
        total_debt = debt_nc.value
        debt_unit = debt_nc.unit

    # Shares outstanding (instant)
    shares = pick_fact_for_accn(facts, filing.accn, PREFERRED_TAGS["shares_outstanding"], want_duration=False, prefer_unit="shares")

    # Build normalized output
    normalized = {
        "revenue": revenue.value if revenue else None,
        "operating_income_ebit": op_inc.value if op_inc else None,
        "pre_tax_income": pre_tax.value if pre_tax else None,
        "income_tax_expense": tax_exp.value if tax_exp else None,
        "depreciation_and_amortization": da.value if da else None,
        "capex": capex_value,
        "working_capital": wc.value if wc else None,
        "total_cash_and_equivalents": total_cash,
        "total_debt": total_debt,
        "shares_outstanding": shares.value if shares else None,
    }

    units = {
        "revenue": revenue.unit if revenue else None,
        "operating_income_ebit": op_inc.unit if op_inc else None,
        "pre_tax_income": pre_tax.unit if pre_tax else None,
        "income_tax_expense": tax_exp.unit if tax_exp else None,
        "depreciation_and_amortization": da.unit if da else None,
        "capex": capex.unit if capex else None,
        "working_capital": wc.unit if wc else None,
        "total_cash_and_equivalents": cash_unit,
        "total_debt": debt_unit,
        "shares_outstanding": shares.unit if shares else None,
    }

    sources: Dict[str, Any] = {}

    def _src(name: str, f: Optional[FactPick]):
        if f:
            sources[name] = {"tag": f.tag, "unit": f.unit, "accn": f.accn, "filed": f.filed, "end": f.end, "fy": f.fy, "fp": f.fp}

    _src("revenue", revenue)
    _src("operating_income_ebit", op_inc)
    _src("pre_tax_income", pre_tax)
    _src("income_tax_expense", tax_exp)
    _src("depreciation_and_amortization", da)
    _src("capex", capex)
    _src("working_capital", wc)
    if total_cash is not None:
        # Prefer the contributing tag if a single combined tag; else show composition
        if st_invest and st_invest.tag == "CashCashEquivalentsAndShortTermInvestments":
            _src("total_cash_and_equivalents", st_invest)
        else:
            # record both if combined
            if cash:
                sources["total_cash_and_equivalents.cash_component"] = {"tag": cash.tag, "unit": cash.unit, "accn": cash.accn, "filed": cash.filed, "end": cash.end}
            if st_invest:
                sources["total_cash_and_equivalents.short_term_investments_component"] = {"tag": st_invest.tag, "unit": st_invest.unit, "accn": st_invest.accn, "filed": st_invest.filed, "end": st_invest.end}
    if total_debt is not None:
        if debt_c:
            sources["total_debt.current_component"] = {"tag": debt_c.tag, "unit": debt_c.unit, "accn": debt_c.accn, "filed": debt_c.filed, "end": debt_c.end}
        if debt_nc:
            sources["total_debt.noncurrent_component"] = {"tag": debt_nc.tag, "unit": debt_nc.unit, "accn": debt_nc.accn, "filed": debt_nc.filed, "end": debt_nc.end}
    _src("shares_outstanding", shares)

    result = {
        "filing": {
            "form": filing.form,
            "accn": filing.accn,
            "filed": filing.filed,
            "fy": filing.fy,
            "fp": filing.fp,
            "period_end": (revenue and revenue.end) or (op_inc and op_inc.end) or filing.report_date,
        },
        "normalized": normalized,
        "units": units,
        "sources": sources,
    }
    return result


def run_pipeline(ticker: Optional[str], cik: Optional[str], years: Optional[int], accn: Optional[str], out_path: str, include_amends: bool, scale: str = "none") -> Dict[str, Any]:
    # Resolve identifiers
    if cik:
        cik10 = cik.zfill(10)
        resolved_ticker = None
    elif ticker:
        cik10, resolved_ticker = normalize_cik(ticker)
    else:
        raise ValueError("Provide --ticker or --cik")

    # Load companyfacts once
    companyfacts = load_companyfacts(cik10)

    # Identify filings
    filings: List[Filing]
    if accn:
        # Minimal metadata: we still try to infer filing date from submissions
        all_10k = list_10k_filings(cik10, include_amends=True)
        meta = next((f for f in all_10k if f.accn == accn), None)
        if not meta:
            # fallback with placeholders
            meta = Filing(form="10-K", accn=accn, filed="", report_date=None, fy=None, fp="FY")
        filings = [meta]
    else:
        filings = list_10k_filings(cik10, include_amends=include_amends, limit_years=years)
        if not filings:
            raise RuntimeError("No 10-K filings found for the specified parameters.")

    # Normalize per filing
    items: List[Dict[str, Any]] = []
    for f in filings:
        items.append(normalize_metrics_for_filing(companyfacts, f))

    # Apply scaling if requested (e.g., convert to thousands/millions/billions)
    scale_map = {
        "none": 1.0,
        "thousands": 1e3,
        "millions": 1e6,
        "billions": 1e9,
    }
    factor = float(scale_map.get(scale, 1.0))
    if scale != "none" and factor != 1.0:
        for item in items:
            # scale numeric normalized fields
            for k, v in list(item.get("normalized", {}).items()):
                if v is not None:
                    item["normalized"][k] = v / factor
            # update units to indicate scaling
            for k, u in list(item.get("units", {}).items()):
                if u:
                    item["units"][k] = f"{u} ({scale})"
                else:
                    item["units"][k] = scale

    payload = {
        "entity": {
            "name": companyfacts.get("entityName"),
            "cik": str(companyfacts.get("cik")).zfill(10),
            "ticker": (resolved_ticker or (companyfacts.get("tickers", [None]) or [None])[0]) if isinstance(companyfacts, dict) else None,
        },
        "count": len(items),
        "results": items,
        "scale": scale,
        "at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "note": "Values are as filed (USD/shares). CapEx normalized to positive outflow. Working Capital is reported if available, else computed as Current Assets minus Current Liabilities.",
    }

    # Write output
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return payload


def main():
    parser = argparse.ArgumentParser(description="Download and normalize SEC 10‑K XBRL facts to JSON")
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--ticker", type=str, help="Stock ticker (e.g., AAPL)")
    g.add_argument("--cik", type=str, help="10-digit zero-padded CIK, or digits without leading zeros")
    parser.add_argument("--years", type=int, default=1, help="How many distinct fiscal years of 10‑K to include (default 1)")
    parser.add_argument("--accn", type=str, help="Specific accession number to extract (overrides --years)")
    parser.add_argument("--include-amends", action="store_true", help="Include 10‑K/A when selecting filings")
    parser.add_argument("--out", type=str, default="financials_normalized.json", help="Output JSON file path")
    parser.add_argument("--scale", choices=["none", "thousands", "millions", "billions"], default="none",
                        help="Scale numeric outputs (none/thousands/millions/billions). Default: none")

    args = parser.parse_args()

    try:
        payload = run_pipeline(
            ticker=args.ticker,
            cik=args.cik,
            years=args.years if not args.accn else None,
            accn=args.accn,
            out_path=args.out,
            include_amends=args.include_amends,
            scale=args.scale,
        )
        print(f"Wrote {args.out} with {payload['count']} item(s)")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
