"""Presentation numbers only: never use these values as database identifiers."""
from flask import g, has_request_context

from database import LimitVersion


def version_numbers():
    """Number all existing uploads globally, including unfinished uploads."""
    rows = LimitVersion.query.with_entities(LimitVersion.id).filter(
        LimitVersion.status != 'purged').order_by(
            LimitVersion.uploaded_at.asc(), LimitVersion.id.asc()).all()
    return {row.id: number for number, row in enumerate(rows, 1)}


def display_number(version_or_id, numbers=None):
    if numbers is None:
        if has_request_context():
            if not hasattr(g, 'limits_version_numbers'):
                g.limits_version_numbers = version_numbers()
            numbers = g.limits_version_numbers
        else:
            numbers = version_numbers()
    identifier = getattr(version_or_id, 'id', version_or_id)
    return numbers.get(identifier, '—')