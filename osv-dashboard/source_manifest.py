# -*- coding: utf-8 -*-
"""Helpers for the immutable manifest of accepted monthly ОСВ files.

The manifest is deliberately built only after a file has been parsed and
accepted by the monthly upload pipeline.  A path in this module is always
relative to a company's upload root (``uploads/<company_id>``), never to the
application working directory.  The database stores the resulting JSON on
the :class:`database.Upload` row; this module does not perform any database
I/O.
"""

from __future__ import annotations

import hashlib
import json
import os

from key_indicators import _period_bounds


class SourceManifestError(ValueError):
    """The accepted source cannot be represented safely in a manifest."""


def sha256_file(path: str, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 digest of *path* without loading it into memory."""

    digest = hashlib.sha256()
    try:
        with open(path, "rb") as stream:
            while True:
                chunk = stream.read(chunk_size)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError as exc:
        raise SourceManifestError(
            f"Не удалось вычислить хеш исходного файла «{path}»: {exc}") from exc
    return digest.hexdigest()


def relative_upload_path(path: str, company_upload_root: str) -> str:
    """Resolve *path* to a safe POSIX path relative to the company root."""

    try:
        root = os.path.realpath(company_upload_root)
        absolute = os.path.realpath(path)
        relative = os.path.relpath(absolute, root)
    except (OSError, ValueError) as exc:
        raise SourceManifestError(
            f"Не удалось определить относительный путь исходного файла: {exc}"
        ) from exc
    if relative == os.curdir or relative == os.pardir or relative.startswith(
            os.pardir + os.sep):
        raise SourceManifestError(
            f"Исходный файл находится вне каталога загрузок предприятия: {path}")
    # The consumer contract is portable even when the application runs on
    # Windows, so the database value always uses forward slashes.
    return relative.replace(os.sep, "/")


def report_bounds(period: str) -> tuple[str | None, str | None]:
    """Conservatively return ISO report boundaries for a parsed period label.

    The parser's ``period`` text is retained verbatim in the manifest.  These
    optional boundaries are only convenience metadata: if the label is
    ambiguous they remain ``None`` instead of being inferred from upload time.
    """

    start, end = _period_bounds(period)
    return (
        start.isoformat() if start is not None else None,
        end.isoformat() if end is not None else None,
    )


def _source_id(relative_path: str, digest: str) -> str:
    """Create a deterministic identity for one accepted physical source."""

    return hashlib.sha256(
        f"{relative_path}\0{digest}".encode("utf-8")).hexdigest()


def accepted_parsed_files(paths, parsed_data, audit):
    """Return only ``(path, parsed)`` items accepted by ``parse_many``.

    ``parse_many`` keeps the first report for a duplicate account and emits
    parser failures and skipped plan-of-accounts files in its audit.  A
    successful audit item whose path disappeared is an integrity error, not
    a reason to silently publish a partial manifest.
    """

    by_name = {}
    for path in sorted(paths):
        by_name.setdefault(os.path.basename(path), []).append(path)
    accepted = []
    for item in audit or []:
        status = str(item.get("status") or "")
        account = str(item.get("acc") or "")
        filename = str(item.get("file") or "")
        accepted_status = status.startswith("разобран") or (
            item.get("ok") is True
            and not status.lower().startswith(
                ("пропущен", "ошиб", "дубль", "skip", "error", "duplicate"))
        )
        if not accepted_status or account not in parsed_data:
            continue
        candidates = by_name.get(filename, [])
        if not candidates:
            raise SourceManifestError(
                f"Принятый файл исчез до фиксации манифеста: «{filename}»")
        accepted.append((candidates.pop(0), parsed_data[account]))
    return accepted


def build_manifest(
    accepted_files,
    company_upload_root: str,
) -> list[dict]:
    """Build JSON-safe records for already accepted parsed monthly files.

    ``accepted_files`` is an iterable of ``(path, parsed_result)`` pairs.
    Callers are responsible for deciding acceptance (including duplicate
    account policy); this function intentionally never adds a record for a
    parse failure or a skipped file.
    """

    records = []
    seen_ids = set()
    for path, parsed in accepted_files:
        if not isinstance(parsed, dict):
            raise SourceManifestError(
                f"Нет результата разбора для принятого файла «{path}»")
        relative_path = relative_upload_path(path, company_upload_root)
        digest = sha256_file(path)
        source_id = _source_id(relative_path, digest)
        if source_id in seen_ids:
            raise SourceManifestError(
                f"Повторный идентификатор принятого файла «{relative_path}»")
        seen_ids.add(source_id)
        period = str(parsed.get("period") or "")
        account = str(parsed.get("acc") or "")
        if not account:
            raise SourceManifestError(
                f"У принятого файла не определён счёт: «{relative_path}»")
        report_start, report_end = report_bounds(period)
        record = {
            "source_id": source_id,
            "relative_path": relative_path,
            "sha256": digest,
            "account": account,
            "period": period,
        }
        if report_start is not None:
            record["report_start"] = report_start
        if report_end is not None:
            record["report_end"] = report_end
        records.append(record)
    return records


def dump_manifest(records) -> str | None:
    """Serialize a manifest, preserving ``None`` only for legacy callers."""

    if records is None:
        return None
    return json.dumps(records, ensure_ascii=False, separators=(",", ":"))
