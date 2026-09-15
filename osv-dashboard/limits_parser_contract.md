# Limits parser contract

`limits_parser.py` is deliberately standalone: it has no Flask, database, or
job-queue imports.

## `parse_xlsx(path)`

Returns a JSON-compatible dictionary:

```python
{
  "columns": [{"key", "label", "category", "year", "source"}],
  "rows": [{"uin", "row", "cells": {
      "column_key": {"raw", "value", "kind", "cell"}
  }}],
  "warnings": [],
  "metadata": {"sheet", "header_row", "data_row", "schema"}
}
```

`raw` is the source display value; `cell` is the source coordinate. `kind` is
one of `blank`, `dash`, `money`, `date`, or `text`. Monetary `value` is a
canonical decimal string (for example `"0"` or `"82746.07"`), while blank and
dash remain distinct. Dates have ISO `value` and source `raw`.

The parser finds the 68 headers by their Russian meaning, not spreadsheet
letters or fixed positions. It requires a single sheet and all 68 recognised
semantic fields. It rejects unsafe ZIP archives and formulas with no saved
Excel cache value. A non-empty data row whose UIN is missing or is not the
full two-part identifier (`part.part`, without whitespace; the second part may
contain the GIS REB letter `Ш`) rejects the import instead of creating a
snapshot that could be accepted.

## `compare_snapshots(old, new, resolutions=None)`

Returns `{"changes": [], "issues": [], "summary": {}, "metadata": {}}`.
Objects are grouped by full UIN. Rows then match by the stable tuple
`list_type, ap_status, gp_code, pp_code, om_code, dm_code`. One unmatched row
on each side is a transfer. Any other unmatched repeated-UIN group produces
`ambiguous_row_match` and is blocked—no inferred additions/removals are made.

Coordinates are **`<full UIN>@<Excel row>`**, e.g.
`1000000850.1000000533@42`; they are generated from `row.uin` and `row.row`,
not cell letters. An explicit transfer can be a bijective mapping
`{"UIN@old_excel_row": "UIN@new_excel_row"}` or a list:

```python
{"transfers": [
  {"old_row": "UIN@42", "new_row": "UIN@43", "action": "transfer"},
  {"old_row": "UIN@44", "action": "absent"},
  {"new_row": "UIN@45", "action": "added"},
]}
```

Use `absent`/`added` for an independently confirmed removal/addition in an
otherwise ambiguous repeated-UIN group; do not falsely call it a transfer.
Every named coordinate must exist, be unmatched, belong to the same UIN for a
transfer, and every transfer is one-to-one. Violations raise
`LimitsParserError`. Every change
contains stable keys, UIN, source row references, and the programme identifiers
(`gp_code`, `pp_code`, `om_code`, `dm_code`); cell changes also contain
source cells, raw values, decimal delta, and percent when its old monetary base
is nonzero. Financial categories remain `annual_funding`,
`funding_snapshot`, and `limit_cost`; a completion date whose year changes is
separately categorized as `completion_year`. `match_type` distinguishes stable
row matches from transfer matches; summary's `stable_changed_cells` intentionally
excludes changes on transferred rows.

`summary` always contains `new_objects`, `absent_objects`, `new_rows`,
`absent_rows`, `transfers`, `changed_cells`, `total_changes`, `issues`, and
`blocked_uins`. Missing objects mean only absent from this XLSX, not cancelled.

## `export_comparison(comparison, old_snapshot=None, new_snapshot=None)`

Returns a seeked `BytesIO` XLSX with exactly these worksheets: `Сводка`,
`Новые объекты`, `Отсутствующие объекты`, `Финансирование`, `Сроки`,
`Реквизиты и коды`, and `Проблемы данных`. Summary includes comparison version
metadata and the problems worksheet includes source-workbook warnings whenever
the optional snapshots are supplied. Text derived from raw workbook values is protected from Excel formula
injection by prefixing `=`, `+`, `-`, and `@` with an apostrophe.