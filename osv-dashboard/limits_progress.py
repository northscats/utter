"""Measured, durable checkpoints. Never commit the caller's financial session."""
import json
import time
from datetime import datetime, timezone

from database import db, LimitVersion

STAGES = {
    'opening': ('Проверка и открытие XLSX', 0, 5),
    'rows': ('Чтение строк XLSX', 5, 55),
    'saving': ('Сохранение строк', 60, 5),
    'matching': ('Сопоставление объектов', 65, 10),
    'comparing': ('Сравнение строк', 75, 15),
    'changes': ('Сохранение отчёта', 90, 3),
    'applying': ('Применение данных', 93, 6),
}


class Progress:
    def __init__(self, version):
        self.version = version
        self.version_id = version.id
        self.started = datetime.now(timezone.utc).timestamp()
        self.stage = None
        self.last_write = 0
        self.stage_start = 0
        self.samples = 0

    def __call__(self, stage, done=0, total=0):
        now = time.monotonic()
        changed = stage != self.stage
        if changed:
            self.stage, self.stage_start, self.samples = stage, now, 0
        # At most one write per second, plus actual stage transitions.
        if not changed and now - self.last_write < 1 and not (total and done == total):
            return
        self.samples += 1
        elapsed = now - self.stage_start
        eta = round((total - done) * elapsed / done) if (
            done >= 100 and total > done and elapsed >= 5 and self.samples >= 3) else None
        _, base, weight = STAGES[stage]
        value = dict(stage=stage, done=done, total=total,
                     percent=min(99, base + int(weight * done / total) if total else base),
                     started=self.started, updated=datetime.now(timezone.utc).timestamp(),
                     eta=eta)
        encoded = json.dumps(value)
        # Only called before the financial session writes. Row loops inside
        # write transactions intentionally have no independent UPDATE.
        with db.engine.begin() as connection:
            connection.execute(LimitVersion.__table__.update().where(
                LimitVersion.id == self.version_id).values(progress_json=encoded))
        self.last_write = now
        # Keep the identity map coherent without marking this column dirty.
        from sqlalchemy.orm.attributes import set_committed_value
        set_committed_value(self.version, 'progress_json', encoded)


def presentation(version, state):
    try:
        value = json.loads(version.progress_json or '{}')
    except (ValueError, TypeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    stage = value.get('stage')
    if state == 'accepted':
        result = dict(percent=100, label='Готово')
        accepted = getattr(version, 'accepted_at', None)
        if accepted and value.get('started'):
            result['elapsed'] = max(0, int(accepted.replace(tzinfo=timezone.utc).timestamp() - value['started']))
        return result
    if state == 'queued' or stage not in STAGES:
        return dict(percent=0, label='Ожидание начала обработки', eta=None)
    running = state == 'parsing'
    now = datetime.now(timezone.utc).timestamp()
    end = now if running else value.get('updated', now)
    return dict(value, label=STAGES[stage][0],
                elapsed=max(0, int(end - value.get('started', end))),
                eta=value.get('eta') if running and now - value.get('updated', 0) < 15 else None)