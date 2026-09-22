# -*- coding: utf-8 -*-
"""Versioned durable database and file backups for Replit App Storage."""
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path, PurePosixPath

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError


ROOT = "osv-dashboard"
LATEST_KEY = f"{ROOT}/latest.json"
SNAPSHOTS_PREFIX = f"{ROOT}/snapshots"
BLOBS_PREFIX = f"{ROOT}/blobs"
COUNT_TABLES = (
    "companies", "uploads", "daily_rows", "historical_imports",
    "historical_year_versions", "historical_metric_values",
    "historical_raw_values", "historical_mappings",
    "debtor_analytic_mappings",
    "analog_passports", "analog_passport_versions", "analog_sources",
    "analog_imports",
    "limit_datasets", "limit_versions", "limit_objects", "limit_row_snapshots",
    "limit_version_tombstones",
    "limit_comparisons", "limit_changes", "limit_recipients",
    "limit_subscriptions", "limit_moderation_requests", "limit_bot_outbound",
    "limit_deliveries",
)
_BACKUP_LOCK = threading.Lock()
_LIMIT_TOMBSTONE_LOCK = threading.Lock()
LIMIT_TOMBSTONES_KEY = f"{ROOT}/limit-version-tombstones.json"
class DurableDataError(RuntimeError):
    """The durable store or PostgreSQL backup utility failed."""


class ConcurrentPromotionError(DurableDataError):
    """The latest pointer changed while a backup attempted to promote."""


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _LocalStore:
    def __init__(self, root):
        self.root = Path(root)

    def put_file(self, key, source, immutable=False):
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        if immutable and destination.exists():
            raise DurableDataError(f"Immutable object already exists: {key}")
        if not destination.exists():
            shutil.copyfile(source, destination)

    def put_json(self, key, value, immutable=False,
                 if_generation_match=None):
        destination = self.root / key
        destination.parent.mkdir(parents=True, exist_ok=True)
        if immutable and destination.exists():
            raise DurableDataError(f"Immutable object already exists: {key}")
        current_generation = (
            hashlib.sha256(destination.read_bytes()).hexdigest()
            if destination.exists() else "0"
        )
        if (if_generation_match is not None
                and current_generation != str(if_generation_match)):
            raise ConcurrentPromotionError(
                f"Object changed concurrently: {key}")
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8")
        temporary.replace(destination)

    def get_json(self, key):
        path = self.root / key
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None

    def get_json_versioned(self, key):
        path = self.root / key
        if not path.is_file():
            return None, "0"
        raw = path.read_bytes()
        return (json.loads(raw.decode("utf-8")),
                hashlib.sha256(raw).hexdigest())

    def get_file(self, key, destination):
        source = self.root / key
        if not source.is_file():
            raise DurableDataError(f"Backup object is missing: {key}")
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)


class _GcsStore:
    def __init__(self, bucket_name, prefix=""):
        try:
            from google.api_core.exceptions import NotFound, PreconditionFailed
            from google.auth import identity_pool
            from google.cloud import storage
        except ImportError as exc:
            raise DurableDataError(
                "Для durable backup требуется google-cloud-storage.") from exc
        if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""):
            client = storage.Client()
        else:
            # Replit App Storage sidecar contract. Keep this in sync with the
            # platform server template; it uses short-lived credentials only.
            config = {
                "type": "external_account",
                "audience": "replit",
                "subject_token_type": "access_token",
                "token_url": "http://127.0.0.1:1106/token",
                "credential_source": {
                    "url": "http://127.0.0.1:1106/credential",
                    "format": {
                        "type": "json",
                        "subject_token_field_name": "access_token",
                    },
                },
                "universe_domain": "googleapis.com",
            }
            credentials = identity_pool.Credentials.from_info(config)
            client = storage.Client(project="", credentials=credentials)
        self.bucket = client.bucket(bucket_name)
        self.prefix = prefix.strip("/")
        self._not_found = NotFound
        self._precondition_failed = PreconditionFailed

    def _key(self, key):
        return "/".join(part for part in (self.prefix, key.lstrip("/")) if part)

    def put_file(self, key, source, immutable=False):
        blob = self.bucket.blob(self._key(key))
        if immutable and blob.exists():
            raise DurableDataError(f"Immutable object already exists: {key}")
        if immutable:
            blob.upload_from_filename(source, if_generation_match=0)
        elif not blob.exists():
            blob.upload_from_filename(source)

    def put_json(self, key, value, immutable=False,
                 if_generation_match=None):
        blob = self.bucket.blob(self._key(key))
        kwargs = {}
        if immutable:
            kwargs["if_generation_match"] = 0
        elif if_generation_match is not None:
            kwargs["if_generation_match"] = int(if_generation_match)
        try:
            blob.upload_from_string(
                json.dumps(value, ensure_ascii=False),
                content_type="application/json", **kwargs)
        except self._precondition_failed as exc:
            raise ConcurrentPromotionError(
                f"Object changed concurrently: {key}") from exc

    def get_json(self, key):
        blob = self.bucket.blob(self._key(key))
        return json.loads(blob.download_as_text()) if blob.exists() else None

    def get_json_versioned(self, key):
        blob = self.bucket.blob(self._key(key))
        for _attempt in range(5):
            try:
                blob.reload()
            except self._not_found:
                return None, 0
            generation = int(blob.generation)
            try:
                raw = blob.download_as_text(
                    if_generation_match=generation)
            except (self._precondition_failed, self._not_found):
                # Pointer changed between metadata and payload reads. The
                # payload must never be paired with another generation.
                continue
            return json.loads(raw), generation
        raise ConcurrentPromotionError(
            f"Не удалось атомарно прочитать object generation: {key}")

    def get_file(self, key, destination):
        blob = self.bucket.blob(self._key(key))
        if not blob.exists():
            raise DurableDataError(f"Backup object is missing: {key}")
        Path(destination).parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(destination)


def _store():
    local = os.environ.get("DURABLE_BACKUP_DIR", "").strip()
    if local:
        return _LocalStore(local)
    bucket = next((
        os.environ.get(name, "").strip()
        for name in (
            "REPLIT_APP_STORAGE_BUCKET", "REPLIT_OBJECT_STORAGE_BUCKET",
            "REPLIT_OBJECT_STORAGE_BUCKET_NAME", "DEFAULT_OBJECT_STORAGE_BUCKET_ID",
            "DURABLE_GCS_BUCKET",
        )
        if os.environ.get(name, "").strip()
    ), "")
    if not bucket:
        raise DurableDataError(
            "Не задан Replit App Storage bucket "
            "(или DURABLE_BACKUP_DIR для теста).")
    private_dir = os.environ.get("PRIVATE_OBJECT_DIR", "").strip("/")
    if private_dir == bucket:
        private_dir = ""
    elif private_dir.startswith(bucket + "/"):
        private_dir = private_dir[len(bucket) + 1:]
    return _GcsStore(bucket, private_dir)


def has_snapshot():
    """Return whether the durable latest pointer already exists."""
    pointer, _ = _store().get_json_versioned(LATEST_KEY)
    return bool(pointer and pointer.get("manifest"))

def _limit_path(path):
    """Return the manifest-relative path for a limits source file."""
    value = str(path or "").replace("\\", "/")
    marker = "/uploads/"
    if marker in value:
        value = value.rsplit(marker, 1)[1]
    elif value.startswith("uploads/"):
        value = value[len("uploads/"):]
    return value.lstrip("/")
def _encoded(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value) if isinstance(value, Decimal) else value


def _snapshot_rows(models):
    return {
        model.__name__: [
            {column.name: _encoded(getattr(row, column.name))
             for column in model.__table__.columns}
            for row in model.query.order_by(model.id).all()
        ]
        for model in models
    }

def _manifest_counts(manifest, models):
    """Use the rows actually stored in a manifest for protected model counts."""
    counts = dict(manifest.get("counts", {}))
    tables = manifest.get("tables", {})
    for model in models:
        if model.__tablename__ in COUNT_TABLES and model.__name__ in tables:
            counts[model.__tablename__] = len(tables[model.__name__])
    return counts
def _counts(db):
    inspector = __import__("sqlalchemy").inspect(db.engine)
    existing = set(inspector.get_table_names())
    return {
        table: (db.session.execute(
            text(f'SELECT COUNT(*) FROM "{table}"')).scalar() if table in existing else 0)
        for table in COUNT_TABLES
    }


def _numeric_files(root, area):
    base = Path(root)
    if not base.is_dir():
        return
    for company_dir in base.iterdir():
        if not company_dir.is_dir() or not company_dir.name.isdigit():
            continue
        for path in company_dir.rglob("*"):
            if path.is_file():
                yield path, {
                    "area": area,
                    "path": path.relative_to(base).as_posix(),
                }


def _backup_files(store, uploads_root, output_root):
    files = []
    uploaded = set()
    for root, area in ((uploads_root, "uploads"), (output_root, "output")):
        for path, item in _numeric_files(root, area):
            digest = _sha256(path)
            if digest not in uploaded:
                store.put_file(f"{BLOBS_PREFIX}/{digest}", str(path))
                uploaded.add(digest)
            item["sha256"] = digest
            files.append(item)
    # Limit source XLSX files are independent of an ОСВ company and live below
    # uploads/limits/<dataset-id>.  They must be backed up just like numeric
    # company uploads, but are deliberately not exposed as a public file area.
    limits_root = Path(uploads_root) / "limits"
    if limits_root.is_dir():
        from database import LimitVersion
        # Files without a live import record (including post-commit purge
        # leftovers) must not leak back into the next immutable manifest.
        for version in LimitVersion.query.filter(LimitVersion.status != 'purged').all():
            path = Path(version.source_path)
            if not path.is_relative_to(limits_root) or not path.is_file():
                continue
            digest = _sha256(path)
            if digest not in uploaded:
                store.put_file(f"{BLOBS_PREFIX}/{digest}", str(path))
                uploaded.add(digest)
            files.append({
                "area": "uploads",
                "path": path.relative_to(Path(uploads_root)).as_posix(),
                "sha256": digest,
            })
    return files


def _pg_environment(database_uri):
    url = make_url(database_uri)
    environment = os.environ.copy()
    # Never pass a password-bearing URL to child processes. libpq receives
    # connection details exclusively through its dedicated PG* variables.
    environment.pop("DATABASE_URL", None)
    values = {
        "PGHOST": url.host,
        "PGPORT": str(url.port) if url.port else None,
        "PGUSER": url.username,
        "PGPASSWORD": url.password,
        "PGDATABASE": url.database,
    }
    environment.update({key: value for key, value in values.items() if value})
    return environment


def _run(command, environment):
    try:
        subprocess.run(
            command, env=environment, check=True, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise DurableDataError(f"Не найдена утилита {command[0]}.") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise DurableDataError(f"{command[0]} завершился с ошибкой: {detail}") from exc


def _snapshot_id():
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    return f"{stamp}-{uuid.uuid4().hex}"


def _removed_snapshot_tables(previous, current, models):
    """Detect removals even when replacement rows keep aggregate counts equal."""
    removed = set()
    previous_tables = previous.get("tables", {}) if previous else {}
    current_tables = current.get("tables", {})
    for model in models:
        if model.__name__ == "User":
            continue
        old_ids = {
            row.get("id") for row in previous_tables.get(model.__name__, [])
            if row.get("id") is not None
        }
        new_ids = {
            row.get("id") for row in current_tables.get(model.__name__, [])
            if row.get("id") is not None
        }
        if old_ids - new_ids:
            removed.add(model.__tablename__)
    return removed


def backup(db, uploads_root, output_root, models, reason,
           allow_removals=False, allowed_removed_tables=()):
    """Create an immutable snapshot, promoting latest only after validation."""
    with _BACKUP_LOCK:
        store = _store()
        snapshot_id = _snapshot_id()
        snapshot_prefix = f"{SNAPSHOTS_PREFIX}/{snapshot_id}"
        tables = _snapshot_rows(models)
        counts = _counts(db)
        for model in models:
            if model.__tablename__ in COUNT_TABLES:
                counts[model.__tablename__] = len(tables.get(model.__name__, []))
        files = _backup_files(store, uploads_root, output_root)
        manifest = {
            "format": 2,
            "snapshot_id": snapshot_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "database": "postgresql" if db.engine.dialect.name == "postgresql"
                        else "json",
            "counts": counts,
            "files": files,
            # JSON is retained even for PostgreSQL. It is the non-destructive
            # repair path when a live database has only partially lost rows.
            "tables": tables,
        }
        if db.engine.dialect.name == "postgresql":
            fd, dump_path = tempfile.mkstemp(prefix="osv_", suffix=".dump")
            os.close(fd)
            try:
                _run(["pg_dump", "--no-password", "--format=custom",
                      "--file", dump_path],
                     _pg_environment(db.engine.url))
                dump_key = f"{snapshot_prefix}/database.dump"
                store.put_file(dump_key, dump_path, immutable=True)
                manifest["database_object"] = dump_key
                manifest["database_sha256"] = _sha256(dump_path)
            finally:
                Path(dump_path).unlink(missing_ok=True)
        manifest_key = f"{snapshot_prefix}/manifest.json"
        store.put_json(manifest_key, manifest, immutable=True)
        promoted = superseded = False
        shrunk = set()
        for _attempt in range(5):
            previous_pointer, pointer_generation = (
                store.get_json_versioned(LATEST_KEY))
            previous = (
                store.get_json(previous_pointer["manifest"])
                if previous_pointer and previous_pointer.get("manifest")
                else None
            )
            previous_created_at = (
                previous.get("created_at", "") if previous else "")
            if previous_created_at >= manifest["created_at"]:
                superseded = True
                break
            shrunk = {
                name for name in COUNT_TABLES
                if previous
                and counts.get(name, 0)
                < _manifest_counts(previous, models).get(name, 0)
            }
            shrunk.update(_removed_snapshot_tables(
                previous, manifest, models))
            blocked_removals = shrunk - set(allowed_removed_tables)
            if blocked_removals and not allow_removals:
                break
            try:
                # Pointer publication is the final write and is conditional
                # on the generation just read, so another process cannot be
                # silently overwritten.
                store.put_json(
                    LATEST_KEY,
                    {"snapshot_id": snapshot_id, "manifest": manifest_key},
                    if_generation_match=pointer_generation,
                )
                promoted = True
                break
            except ConcurrentPromotionError:
                continue
        else:
            raise ConcurrentPromotionError(
                "Не удалось атомарно обновить latest после 5 попыток.")
        return {
            "snapshot_id": snapshot_id, "promoted": promoted,
            "shrink_rejected": sorted(shrunk), "files": len(files),
            "superseded": superseded,
            "rows": sum(len(rows) for rows in manifest.get("tables", {}).values()),
        }


def _decoded(column, value):
    if value is None:
        return None
    python_type = getattr(column.type, "python_type", None)
    if python_type is datetime:
        return datetime.fromisoformat(value)
    if python_type is date:
        return date.fromisoformat(value)
    if python_type is Decimal:
        return Decimal(str(value))
    return value


def _foreign_keys_present(session, model, values):
    for column in model.__table__.columns:
        value = values.get(column.name)
        if value is None:
            continue
        for foreign_key in column.foreign_keys:
            target_table = foreign_key.column.table
            target = target_table.c[foreign_key.column.name]
            if session.execute(
                    target_table.select().where(target == value)).first() is None:
                return False
    return True


def _same_company_identity(row, snapshot):
    if snapshot.get("identity_key"):
        return row.identity_key == snapshot["identity_key"]
    if snapshot.get("internal_code"):
        return row.internal_code == snapshot["internal_code"]
    current = _encoded(row.created_at)
    expected = snapshot.get("created_at")
    try:
        return (datetime.fromisoformat(current).replace(tzinfo=None)
                == datetime.fromisoformat(expected).replace(tzinfo=None))
    except (TypeError, ValueError):
        return current == expected


def _parent_identity_conflicts(db, manifest, by_name):
    blocked_users = set()
    blocked_companies = set()
    user_model = by_name.get("User")
    company_model = by_name.get("Company")
    for snapshot in manifest.get("tables", {}).get("User", []):
        current = db.session.get(user_model, snapshot.get("id")) if user_model else None
        if current is not None and current.username != snapshot.get("username"):
            blocked_users.add(snapshot["id"])
        elif current is None and user_model is not None:
            same_identity = user_model.query.filter_by(
                username=snapshot.get("username")).first()
            if same_identity is not None:
                blocked_users.add(snapshot["id"])
    for snapshot in manifest.get("tables", {}).get("Company", []):
        current = (db.session.get(company_model, snapshot.get("id"))
                   if company_model else None)
        if current is not None and not _same_company_identity(current, snapshot):
            blocked_companies.add(snapshot["id"])
        elif current is None and company_model is not None:
            if snapshot.get("identity_key"):
                same_identity = company_model.query.filter_by(
                    identity_key=snapshot["identity_key"]).first()
            elif snapshot.get("internal_code"):
                same_identity = company_model.query.filter_by(
                    internal_code=snapshot["internal_code"]).first()
            else:
                same_identity = next((
                    row for row in company_model.query.all()
                    if _same_company_identity(row, snapshot)
                ), None)
            if same_identity is not None:
                blocked_companies.add(snapshot["id"])
    return blocked_users, blocked_companies


def _references_blocked_parent(model, values, blocked_users, blocked_companies):
    for column in model.__table__.columns:
        value = values.get(column.name)
        for foreign_key in column.foreign_keys:
            table = foreign_key.column.table.name
            if ((table == "users" and value in blocked_users)
                    or (table == "companies" and value in blocked_companies)):
                return True
    return False

def _database_limit_tombstones(db):
    """Read live deletion markers without requiring them in a model tuple."""
    from database import LimitVersionTombstone
    inspector = __import__("sqlalchemy").inspect(db.engine)
    if LimitVersionTombstone.__tablename__ not in inspector.get_table_names():
        return []
    return [{
        "version_id": row.version_id,
        "dataset_id": row.dataset_id,
        "source_path": row.source_path or "",
        "path": _limit_path(row.source_path),
        "source_sha256": row.source_sha256 or "",
    } for row in LimitVersionTombstone.query.all()]
def _restore_json(db, manifest, models, include_parents=True,
                  external_tombstones=None):
    restored = skipped = conflicts = 0
    deferred_limit_heads = []
    tombstoned_object_ids = set()
    checkpoint_size = max(
        1, int(os.environ.get("DURABLE_RESTORE_COMMIT_BATCH_SIZE", "250")))
    processed_since_checkpoint = 0
    # A retry may encounter rows committed by an earlier attempt.  Sequence
    # repair must still run in that case, even when this attempt inserts zero
    # new rows.
    manifest_has_rows = any(
        manifest.get("tables", {}).get(model.__name__)
        for model in models)

    def checkpoint():
        nonlocal processed_since_checkpoint
        if processed_since_checkpoint >= checkpoint_size:
            db.session.commit()
            processed_since_checkpoint = 0

    by_name = {model.__name__: model for model in models}
    (tombstoned_versions, tombstoned_comparisons,
     tombstoned_deliveries) = _limit_tombstone_sets(
         manifest, db, external_tombstones)
    # Persist sidecar markers into the recovered database as well.  Otherwise
    # a later backup would lose the only evidence that an older manifest must
    # continue to filter this version.
    from database import LimitVersionTombstone
    for item in _normalise_limit_tombstones(external_tombstones or []):
        if LimitVersionTombstone.query.filter_by(
                version_id=item["version_id"]).first() is not None:
            continue
        db.session.add(LimitVersionTombstone(
            version_id=item["version_id"],
            dataset_id=item.get("dataset_id") or 0,
            source_path=item.get("source_path") or "",
            source_sha256=item.get("source_sha256") or "",
        ))
        db.session.flush()
        restored += 1
    blocked_users, blocked_companies = _parent_identity_conflicts(
        db, manifest, by_name)
    conflicts += len(blocked_users) + len(blocked_companies)
    for model in models:
        if not include_parents and model.__name__ in ("User", "Company"):
            continue
        for values in manifest.get("tables", {}).get(model.__name__, []):
            processed_since_checkpoint += 1
            row_id = values.get("id")
            if (model.__name__ == "LimitObject"
                    and values.get("last_seen_version_id") in tombstoned_versions
                    and row_id is not None):
                tombstoned_object_ids.add(row_id)
            if _restore_row_blocked(
                    model.__name__, values, tombstoned_versions,
                    tombstoned_comparisons, tombstoned_deliveries):
                skipped += 1
                checkpoint()
                continue
            existing = (
                None if row_id is None else db.session.get(model, row_id))
            if existing is not None:
                # If a previous checkpoint committed the dataset but the
                # connection died before pointer reconciliation, retain the
                # manifest head as a deferred repair.  A non-NULL live head
                # is never overwritten.
                if (model.__name__ == "LimitDataset"
                        and existing.current_version_id is None
                        and values.get("current_version_id") is not None):
                    deferred_limit_heads.append(
                        (model, row_id, values["current_version_id"]))
                skipped += 1
                checkpoint()
                continue
            converted = {
                column.name: _decoded(column, values.get(column.name))
                for column in model.__table__.columns if column.name in values
            }
            # Dataset -> current version -> dataset is a legitimate cycle.
            # Defer only the pointer of a newly restored dataset; existing
            # datasets and their current version must never be overwritten.
            pending_head = None
            if model.__name__ == 'LimitDataset':
                pending_head = converted.get('current_version_id')
                converted['current_version_id'] = None
            elif (model.__name__ == 'LimitVersion'
                  and converted.get('previous_version_id') in tombstoned_versions):
                # The link is optional and the deleted version must not become
                # a reason to skip a still valid historical version.
                converted['previous_version_id'] = None
            elif (model.__name__ == 'LimitObject'
                  and converted.get('last_seen_version_id')
                  in tombstoned_versions):
                converted['last_seen_version_id'] = None
            if _references_blocked_parent(
                    model, converted, blocked_users, blocked_companies):
                skipped += 1
                conflicts += 1
                continue
            if not _foreign_keys_present(db.session, model, converted):
                skipped += 1
                conflicts += 1
                continue
            try:
                with db.session.begin_nested():
                    db.session.add(model(**converted))
                    db.session.flush()
                restored += 1
                if pending_head is not None:
                    deferred_limit_heads.append((model, row_id, pending_head))
            except IntegrityError:
                skipped += 1
                conflicts += 1
            checkpoint()
    has_limit_tombstones = bool(_database_limit_tombstones(db))
    if ((restored or manifest_has_rows or has_limit_tombstones)
            and db.engine.dialect.name == "postgresql"):
        # Explicit recovery IDs do not advance PostgreSQL sequences.
        for model in models:
            table_name = model.__tablename__
            sequence = db.session.execute(
                text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
                {"table_name": table_name},
            ).scalar()
            if not sequence:
                continue
            maximum = db.session.execute(
                text(f'SELECT MAX(id) FROM "{table_name}"')).scalar()
            if model.__name__ == "LimitVersion":
                # A deleted version ID remains reserved by its tombstone.
                # Otherwise PostgreSQL could allocate that ID to a new upload,
                # which an old manifest would then mistake for the deleted
                # source during a later additive restore.
                tombstone_maximum = db.session.execute(
                    text("SELECT MAX(version_id) "
                         "FROM limit_version_tombstones")).scalar()
                if tombstone_maximum is not None:
                    maximum = max(maximum or 0, tombstone_maximum)
            if maximum is not None:
                db.session.execute(
                    text("SELECT setval(CAST(:sequence AS regclass), "
                         ":maximum, true)"),
                    {"sequence": sequence, "maximum": maximum},
                )
    version_model = by_name.get('LimitVersion')
    for model, dataset_id, version_id in deferred_limit_heads:
        dataset = db.session.get(model, dataset_id)
        version = db.session.get(version_model, version_id) if version_model else None
        if dataset and version and version.dataset_id == dataset_id and version.status == 'accepted':
            dataset.current_version_id = version_id
        elif dataset and version_model and version_id in tombstoned_versions:
            fallback = _latest_limit_version(
                db, version_model, dataset_id, tombstoned_versions)
            dataset.current_version_id = fallback.id if fallback else None
    # A partially lost dataset may retain a pointer to a manually deleted
    # version.  Repair only invalid/tombstoned pointers; a deliberate NULL
    # current pointer remains NULL.
    dataset_model = by_name.get("LimitDataset")
    if dataset_model and version_model:
        for dataset in dataset_model.query.all():
            if not dataset.current_version_id:
                continue
            current = db.session.get(version_model, dataset.current_version_id)
            if (current is None or current.id in tombstoned_versions
                    or current.dataset_id != dataset.id
                    or current.status != "accepted"):
                fallback = _latest_limit_version(
                    db, version_model, dataset.id, tombstoned_versions)
                dataset.current_version_id = fallback.id if fallback else None
    object_model = by_name.get("LimitObject")
    if object_model and version_model and tombstoned_object_ids:
        for object_id in tombstoned_object_ids:
            obj = db.session.get(object_model, object_id)
            if not obj:
                continue
            dataset = db.session.get(dataset_model, obj.dataset_id) if dataset_model else None
            head = (db.session.get(version_model, dataset.current_version_id)
                    if dataset and dataset.current_version_id else None)
            if head and head.status == "accepted":
                _restore_limit_object_from_head(obj, head)
            else:
                obj.is_present = False
                obj.last_seen_version_id = None
    db.session.commit()
    return restored, skipped, conflicts


def _safe_destination(root, relative):
    parts = PurePosixPath(relative).parts
    if (not parts or (not parts[0].isdigit() and parts[0] != "limits")
            or any(part in ("", ".", "..") for part in parts)):
        raise DurableDataError(f"Недопустимый путь в manifest: {relative}")
    return Path(root).joinpath(*parts)


def _restore_files(store, manifest, uploads_root, output_root,
                   external_tombstones=None):
    roots = {"uploads": uploads_root, "output": output_root}
    tombstoned_paths = {
        item["path"] for item in _normalise_limit_tombstones(
            external_tombstones or [])
        if item.get("path")
    }
    # The DB and manifest copies are needed when the sidecar was not yet
    # written or when this is a direct recovery call in an existing database.
    from database import db as application_db
    for item in _database_limit_tombstones(application_db):
        if item.get("path"):
            tombstoned_paths.add(item["path"])
    for item in _normalise_limit_tombstones(
            manifest.get("tables", {}).get("LimitVersionTombstone", [])):
        if item.get("path"):
            tombstoned_paths.add(item["path"])
    restored = 0
    for item in manifest.get("files", []):
        if item.get("area") not in roots:
            raise DurableDataError("Неизвестная файловая область в manifest.")
        if (item.get("area") == "uploads"
                and _limit_path(item.get("path", "")) in tombstoned_paths):
            continue
        destination = _safe_destination(roots[item["area"]], item.get("path", ""))
        if destination.exists():
            continue
        store.get_file(f"{BLOBS_PREFIX}/{item['sha256']}", str(destination))
        if _sha256(destination) != item["sha256"]:
            destination.unlink(missing_ok=True)
            raise DurableDataError(f"Неверная контрольная сумма: {item['path']}")
        restored += 1
    return restored


def restore(db, uploads_root, output_root, models):
    """Safely merge missing protected rows/files; never overwrite live data."""
    store = _store()
    pointer = store.get_json(LATEST_KEY)
    if not pointer:
        return {
            "rows": 0, "files": 0, "skipped": 0, "conflicts": 0,
            "database_restored": False, "recovery_needed": False,
        }
    manifest = store.get_json(pointer["manifest"])
    if not manifest:
        raise DurableDataError("Latest manifest отсутствует.")
    external_tombstones = _read_limit_tombstones(store)
    restored, skipped, conflicts = _restore_json(
        db, manifest, models, include_parents=True,
        external_tombstones=external_tombstones)
    recovery_needed = restored > 0 or conflicts > 0
    files = _restore_files(
        store, manifest, uploads_root, output_root,
        external_tombstones=external_tombstones)
    return {
        "rows": restored, "files": files, "skipped": skipped,
        "conflicts": conflicts, "database_restored": False,
        "recovery_needed": recovery_needed,
    }

def _restore_limit_object_from_head(obj, version):
    """Rebuild object display fields from the surviving accepted snapshot."""
    try:
        snapshot = json.loads(version.snapshot_json or "{}")
    except (TypeError, ValueError):
        snapshot = {}
    rows = [
        row for row in snapshot.get("rows", [])
        if isinstance(row, dict) and row.get("uin") == obj.uin
    ]
    obj.is_present = bool(rows)
    if not rows:
        obj.last_seen_version_id = None
        return
    obj.last_seen_version_id = version.id
    cells_by_row = [
        row.get("cells", {}) if isinstance(row.get("cells", {}), dict) else {}
        for row in rows
    ]
    titles = sorted({
        str(_limit_snapshot_value(cells.get("object_name")) or "")
        for cells in cells_by_row
    } - {""})
    addresses = sorted({
        str(_limit_snapshot_value(cells.get("address")) or "")
        for cells in cells_by_row
    } - {""})
    obj.title = " / ".join(titles)
    obj.address = " / ".join(addresses)

def _restore_row_blocked(model_name, values, version_ids,
                         comparison_ids, delivery_ids):
    """Keep all materialisations whose required version was deleted."""
    if model_name == "LimitVersion" and values.get("id") in version_ids:
        return True
    if model_name == "LimitVersionTombstone":
        return False
    if values.get("version_id") in version_ids:
        return True
    if (values.get("old_version_id") in version_ids
            or values.get("new_version_id") in version_ids):
        return True
    if values.get("comparison_id") in comparison_ids:
        return True
    if values.get("delivery_id") in delivery_ids:
        return True
    return False

def _read_limit_tombstones(store=None):
    """Read the tiny external deletion sidecar, if one exists."""
    store = store or _store()
    value = store.get_json(LIMIT_TOMBSTONES_KEY)
    return _normalise_limit_tombstones(value or [])

def _normalise_limit_tombstones(value):
    """Accept the current sidecar format and the original list shape."""
    if isinstance(value, dict):
        value = value.get("tombstones", [])
    if not isinstance(value, list):
        raise DurableDataError("Недопустимый формат sidecar tombstones.")
    result = []
    for item in value:
        if not isinstance(item, dict) or item.get("version_id") is None:
            continue
        try:
            version_id = int(item["version_id"])
        except (TypeError, ValueError):
            continue
        result.append({
            "version_id": version_id,
            "dataset_id": item.get("dataset_id"),
            "source_path": str(item.get("source_path") or ""),
            "path": _limit_path(item.get("path") or item.get("source_path") or ""),
            "source_sha256": str(item.get("source_sha256") or ""),
        })
    return result

def _limit_tombstone_sets(manifest, db, external_tombstones=None):
    """Combine current DB, manifest, and external deletion knowledge."""
    records = {}
    for item in _database_limit_tombstones(db):
        records[int(item["version_id"])] = item
    for item in _normalise_limit_tombstones(external_tombstones or []):
        records[int(item["version_id"])] = item
    for item in _normalise_limit_tombstones(
            manifest.get("tables", {}).get("LimitVersionTombstone", [])):
        records[int(item["version_id"])] = item
    version_ids = set(records)
    # SQLite may reuse a deleted integer primary key.  A marker must block
    # the old manifest row, but not a newer upload that legitimately reused
    # that ID with a different immutable source.
    for row in manifest.get("tables", {}).get("LimitVersion", []):
        version_id = row.get("id")
        try:
            version_id = int(version_id)
        except (TypeError, ValueError):
            continue
        marker = records.get(version_id)
        if marker and not _limit_version_matches_tombstone(row, marker):
            version_ids.discard(version_id)
    tables = manifest.get("tables", {})
    comparison_ids = {
        row.get("id") for row in tables.get("LimitComparison", [])
        if row.get("id") is not None and (
            row.get("old_version_id") in version_ids
            or row.get("new_version_id") in version_ids)
    }
    delivery_ids = {
        row.get("id") for row in tables.get("LimitDelivery", [])
        if row.get("id") is not None and row.get("version_id") in version_ids
    }
    return version_ids, comparison_ids, delivery_ids

def _limit_version_matches_tombstone(values, marker):
    marker_sha = str(marker.get("source_sha256") or "")
    source_sha = str(values.get("sha256") or "")
    if marker_sha and source_sha and marker_sha != source_sha:
        return False
    marker_path = _limit_path(
        marker.get("path") or marker.get("source_path") or "")
    source_path = _limit_path(values.get("source_path") or "")
    if marker_path and source_path and marker_path != source_path:
        return False
    return True

def _latest_limit_version(db, version_model, dataset_id, blocked_ids):
    """Find a safe accepted fallback after a tombstoned current pointer."""
    return version_model.query.filter(
        version_model.dataset_id == dataset_id,
        version_model.status == "accepted",
        ~version_model.id.in_(blocked_ids),
    ).order_by(
        version_model.accepted_at.desc(), version_model.id.desc()).first()

def _limit_snapshot_value(cell):
    return cell.get("value") if isinstance(cell, dict) else cell

def backup_limit_tombstones(db):
    """Publish all database markers to the small external sidecar."""
    from database import LimitVersionTombstone
    rows = LimitVersionTombstone.query.order_by(
        LimitVersionTombstone.version_id).all()
    for row in rows:
        record_limit_tombstone(
            row.version_id, row.dataset_id, row.source_path, row.source_sha256)
    return len(rows)

def record_limit_tombstone(version_id, dataset_id, source_path="",
                           source_sha256=""):
    """Atomically add one deletion marker to the small durable sidecar.

    The database tombstone is authoritative and is written by the caller in
    its purge transaction.  This sidecar covers the narrower full-database
    loss case where the latest manifest still predates that transaction.
    Rewriting this small object is deliberately separate from a full backup.
    """
    record = {
        "version_id": int(version_id),
        "dataset_id": dataset_id,
        "source_path": str(source_path or ""),
        "path": _limit_path(source_path),
        "source_sha256": str(source_sha256 or ""),
    }
    with _LIMIT_TOMBSTONE_LOCK:
        store = _store()
        for _attempt in range(5):
            current, generation = store.get_json_versioned(LIMIT_TOMBSTONES_KEY)
            records = _normalise_limit_tombstones(current or [])
            by_version = {item["version_id"]: item for item in records}
            by_version[record["version_id"]] = record
            payload = {
                "format": 1,
                "tombstones": sorted(
                    by_version.values(), key=lambda item: item["version_id"]),
            }
            try:
                store.put_json(
                    LIMIT_TOMBSTONES_KEY, payload,
                    if_generation_match=generation)
                return payload["tombstones"]
            except ConcurrentPromotionError:
                continue
        raise ConcurrentPromotionError(
            "Не удалось атомарно обновить sidecar tombstones после 5 попыток.")
