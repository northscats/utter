"""Monthly investment index register and analogue cost conversion.

The initial data is a transcription of the user's 2017–July 2026 tables,
exclusively from the row "К предыдущему месяцу". Later edits in the database
are never overwritten during startup.
"""
import json
from datetime import date
from decimal import Decimal

from database import AnalogMonthlyIndex, db


INITIAL_INDICES = {
    2017: '100.19 101.71 97.46 100.75 101.23 100.13 100.84 100.89 100.08 100.25 99.6 100.32',
    2018: '99.85 100.1 100.51 100.71 100.46 100.44 100.5 100.77 100.62 100.28 100.59 100.65',
    2019: '100.81 100.27 100.31 100.32 100.39 100.33 100.34 100.64 100.3 100.22 100.25 100.08',
    2020: '100 100.52 101.68 100.42 99.97 99.64 100.47 100.63 100.52 100.41 100.09 99.88',
    2021: '100.43 100.08 101.06 100.85 101.61 101.18 101.23 100.85 100.58 100.48 100.65 100.71',
    2022: '100.73 100.74 104.44 101.05 100.71 100.51 100 100.37 101.17 100.32 100.74 100.13',
    2023: '100.31 100.9 99.77 100.8 100.13 101.12 101.03 100.73 100.48 100.57 99.97 100.61',
    2024: '100.16 100.59 100.14 100.42 100.47 100.21 100.45 100.32 100.49 100.64 100.62 100.48',
    2025: '100.36 99.08 99.87 98.37 101.01 102.25 100.18 99.81 101.28 100.18 100.67 100.1',
    2026: '100.72 100.08 101.7 98.74 100.13 100.88 101.67',
}


def seed_monthly_indices():
    """Add only missing periods so admin corrections survive restart."""
    existing = {(row.year, row.month) for row in AnalogMonthlyIndex.query.all()}
    for year, values in INITIAL_INDICES.items():
        for month, value in enumerate(values.split(), start=1):
            if (year, month) not in existing:
                db.session.add(AnalogMonthlyIndex(
                    year=year, month=month, percent=Decimal(value),
                    source='Таблицы индексов, предоставленные владельцем'))
    db.session.commit()


def price_period(version):
    """Use SSR price month, or SSR approval month when only quarter is given."""
    try:
        ssr = json.loads(version.payload_json or '{}').get('ssr') or {}
    except (TypeError, ValueError):
        return None
    year = ssr.get('price_year') or version.price_year
    month = ssr.get('price_month')
    if not month:
        approved = ssr.get('approved_date')
        try:
            approval_date = date.fromisoformat(str(approved)[:10])
        except (TypeError, ValueError):
            return None
        # Do not silently assign a month of approval to a different price year.
        if year != approval_date.year:
            return None
        month = approval_date.month
    try:
        year, month = int(year), int(month)
        if 1 <= month <= 12 and 1900 <= year <= 2100:
            return year, month
    except (TypeError, ValueError):
        pass
    return None


def indexed_cost(version, indices, latest):
    """Return (cost, factor, start) or None on missing source/any month in range."""
    if version is None or version.total_cost is None or latest is None:
        return None
    start = price_period(version)
    if start is None or start > latest:
        return None
    factor = Decimal('1')
    year, month = start
    while (year, month) <= latest:
        percent = indices.get((year, month))
        if percent is None or Decimal(str(percent)) <= 0:
            return None
        factor *= Decimal(str(percent)) / 100
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return Decimal(str(version.total_cost)) * factor, factor, start