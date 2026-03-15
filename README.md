# sec_xbrl_normalizer

_Created by [@nickb20050-beep](https://github.com/nickb20050-beep)_

Lightweight CLI to fetch SEC XBRL companyfacts, normalize core financial metrics, and write JSON output.

Location
- `sec_xbrl_normalizer.py` (script) — in this folder.

Requirements
- Python 3.8+ (3.14 used in examples)
- A virtual environment is recommended (the script will try an automatic pip fallback if `requests` is missing).
- Set a descriptive SEC User-Agent for fair access (includes an email).

Environment / installation
1. Create and activate a venv (recommended):

```powershell
cd "C:\Users\N\Downloads"
python -m venv .venv
.\.venv\Scripts\Activate.ps1   # PowerShell
```

2. Install dependencies into the venv:

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install requests
```

3. **Manually set** a SEC user agent for the shell session (required). This should include a valid email address so you comply with SEC fair‑access guidelines. **DO NOT** reuse someone else's value.

```powershell
$env:SEC_USER_AGENT = "MyApp/1.0 (you@example.com)"
```

(To set permanently for your user, use `setx SEC_USER_AGENT "..."` or similar. New terminals will pick up the value.)

Usage

```powershell
# basic: fetch 1 most recent 10-K by ticker and write JSON
.\.venv\Scripts\python.exe .\sec_xbrl_normalizer.py --ticker AAPL --years 1 --out aapl.json

# specify accession
.\.venv\Scripts\python.exe .\sec_xbrl_normalizer.py --cik 0000320193 --accn 0000320193-25-000079 --out apple_2025.json

# scale numeric outputs (none/thousands/millions/billions)
.\.venv\Scripts\python.exe .\sec_xbrl_normalizer.py --ticker AAPL --years 1 --out aapl_millions.json --scale millions
```

What `--scale` does
- `none` (default): outputs raw values as filed (full USD, shares).
- `thousands`: divide numbers by 1e3.
- `millions`: divide numbers by 1e6.
- `billions`: divide numbers by 1e9.

Output structure
- Top-level `entity`, `count`, `results` (one entry per filing).
- Each `result` includes:
  - `filing` metadata (form, accn, filed, fy, period_end)
  - `normalized`: numeric fields (revenue, operating_income_ebit, pre_tax_income, income_tax_expense,
    depreciation_and_amortization, capex, working_capital, total_cash_and_equivalents, total_debt, shares_outstanding)
  - `units`: unit strings; when `--scale` is used they include the scale (e.g. `USD (millions)`).
  - `sources`: source tag/unit/accn/filed details for each field.

Notes
- The script chooses preferred GAAP tags (see `PREFERRED_TAGS`) and prefers USD or `shares` units.
- CapEx is normalized to a positive outflow.
- Working capital is used if reported; otherwise computed as Current Assets − Current Liabilities.
- Some XBRL facts provide `decimals`; the script uses the numeric `val` value returned by the SEC companyfacts JSON.

Troubleshooting
- If you see pip errors because your Python is externally managed (PEP 668), create a local venv and install into it (see steps above).
- If `SEC_USER_AGENT` is not set, the script may prompt for one when run interactively.

License / Contact
- Small helper script for personal use. For questions or contributions, open an issue or PR on GitHub. Do **not** publish your SEC user‑agent string or other personal credentials.
