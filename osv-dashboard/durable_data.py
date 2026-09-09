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
)
_BACKUP_LOCK = threading.Lock()


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


def _encoded(value):
    return value.isoformat() if isinstance(value, (datetime, date)) else value


def _snapshot_rows(models):
    return {
        model.__name__: [
            {column.name: _encoded(getattr(row, column.name))
             for column in model.__table__.columns}
            for row in model.query.order_by(model.id).all()
        ]
        for model in models
    }


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
        counts = _counts(db)
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
            "tables": _snapshot_rows(models),
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
                < previous.get("counts", {}).get(name, 0)
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


def _restore_json(db, manifest, models, include_parents=True):
    restored = skipped = conflicts = 0
    by_name = {model.__name__: model for model in models}
    blocked_users, blocked_companies = _parent_identity_conflicts(
        db, manifest, by_name)
    conflicts += len(blocked_users) + len(blocked_companies)
    for model in models:
        if not include_parents and model.__name__ in ("User", "Company"):
            continue
        for values in manifest.get("tables", {}).get(model.__name__, []):
            row_id = values.get("id")
            if row_id is None or db.session.get(model, row_id) is not None:
                skipped += 1
                continue
            converted = {
                column.name: _decoded(column, values.get(column.name))
                for column in model.__table__.columns if column.name in values
            }
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
            except IntegrityError:
                skipped += 1
                conflicts += 1
    if restored and db.engine.dialect.name == "postgresql":
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
            if maximum is not None:
                db.session.execute(
                    text("SELECT setval(CAST(:sequence AS regclass), "
                         ":maximum, true)"),
                    {"sequence": sequence, "maximum": maximum},
                )
    db.session.commit()
    return restored, skipped, conflicts


def _safe_destination(root, relative):
    parts = PurePosixPath(relative).parts
    if (not parts or not parts[0].isdigit()
            or any(part in ("", ".", "..") for part in parts)):
        raise DurableDataError(f"Недопустимый путь в manifest: {relative}")
    return Path(root).joinpath(*parts)


def _restore_files(store, manifest, uploads_root, output_root):
    roots = {"uploads": uploads_root, "output": output_root}
    restored = 0
    for item in manifest.get("files", []):
        if item.get("area") not in roots:
            raise DurableDataError("Неизвестная файловая область в manifest.")
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
    restored, skipped, conflicts = _restore_json(
        db, manifest, models, include_parents=True)
    recovery_needed = restored > 0 or conflicts > 0
    files = _restore_files(store, manifest, uploads_root, output_root)
    return {
        "rows": restored, "files": files, "skipped": skipped,
        "conflicts": conflicts, "database_restored": False,
        "recovery_needed": recovery_needed,
    }