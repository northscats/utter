---
name: Daily ZIP pipeline
description: How daily OSV ZIP archives are processed and merged with monthly data for the dashboard
---

## Rule
Daily ZIP files are stored in DailyRow DB table. Dashboard is rebuilt by merging
monthly XLS `rows` (analytics) with DailyRow `daily` entries (time series).

**Why:** Daily XLS files lack "Обороты за" sub-rows for most accounts (20, 26, 62, 90).
The day's turnover lives in the top-level account row. Monthly files have richer
sub-row detail. Merging: monthly months use XLS sub-rows; new months use DailyRow.

**How to apply:**
- `parse_zip(zip_path)` in osvparser.py — extracts ZIP, calls parse_osv per XLS,
  returns (successes, errors). Date extracted from header row 1 via RE_DAILY_HDR.
- `DailyRow` table: (company_id, date, acc) UNIQUE. Upsert on repeat upload.
- `_rebuild_dashboard_with_daily(company_id)` in app.py — re-parses monthly XLS
  (from uploads/<company_id>/<ts>/ dirs, skipping zip_ dirs), loads DailyRow for
  months NOT already covered by monthly XLS, merges into data dict, calls build_dataset.
- Route: POST /company/<id>/upload-zip
- ZIP storage: uploads/<company_id>/zip_<ts>/archive.zip (for audit)
- Monthly XLS dirs: uploads/<company_id>/<ts>/ (no zip_ prefix = monthly)

## Date format in daily headers
Row 1 of daily XLS: "Оборотно-сальдовая ведомость по счету 51 за 5 июля 2026 г."
RE_DAILY_HDR regex in osvparser.py handles all Russian month names in genitive case.

## Verified
- 168 files (14 accs × 12 days July 1-12 2026) parsed correctly
- СК→СН stitch: all 11 transitions Δ=0.00 (data is clean)
- Upsert is idempotent: repeat upload of same ZIP replaces, no duplicates
- New dashboard includes both Jan-Jun (monthly) and Jul (daily) data
