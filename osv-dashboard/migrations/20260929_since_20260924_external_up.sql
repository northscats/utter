-- Только psql, отдельное соединение; по умолчанию ROLLBACK.
-- См. одноимённый README. Никаких запусков приложения/бота.
\set ON_ERROR_STOP on
\if :{?apply}
\else
  \set apply false
\endif
BEGIN;
SET LOCAL search_path = public, pg_catalog;
SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '10min';
SELECT pg_advisory_xact_lock(20260929, 20260924);

-- Временные эталоны нужны для проверки каталога, не содержат бизнес-данных.
CREATE TEMP TABLE migration_specs (
  name text PRIMARY KEY, ddl text NOT NULL, indexes text[] NOT NULL,
  prerequisite boolean NOT NULL DEFAULT false
);
INSERT INTO migration_specs(name, ddl, indexes, prerequisite) VALUES
('osv_source_cleanups', $ddl$
 id serial PRIMARY KEY, company_id integer NOT NULL, relative_path text NOT NULL,
 status varchar(20) NOT NULL, attempts integer NOT NULL, last_error varchar(255) NOT NULL,
 created_at timestamptz NOT NULL, last_attempt_at timestamptz,
 CONSTRAINT uq_osv_source_cleanup_company_path UNIQUE(company_id, relative_path)
$ddl$, ARRAY['company_id', 'status'], true),
('analog_monthly_indices', $ddl$
 id serial PRIMARY KEY, year integer NOT NULL, month integer NOT NULL,
 percent numeric(8,3) NOT NULL, source varchar(255) NOT NULL, updated_at timestamptz NOT NULL,
 CONSTRAINT uq_analog_monthly_index_period UNIQUE(year, month)
$ddl$, ARRAY[]::text[], false),
('max_limits_subscriptions', $ddl$
 id serial PRIMARY KEY, user_id bigint NOT NULL, uin varchar(120) NOT NULL,
 active boolean DEFAULT true NOT NULL, created_at timestamptz NOT NULL
$ddl$, ARRAY['UNIQUE user_id', 'uin', 'active'], false),
('max_limits_profiles', $ddl$
 user_id bigserial PRIMARY KEY, full_name varchar(200) NOT NULL,
 phone varchar(32) NOT NULL, step varchar(24) NOT NULL, notice text NOT NULL,
 updated_at timestamptz NOT NULL
$ddl$, ARRAY[]::text[], false),
('max_limits_uins', $ddl$
 id serial PRIMARY KEY, user_id bigint NOT NULL, uin varchar(120) NOT NULL,
 status varchar(16) NOT NULL, created_at timestamptz NOT NULL,
 CONSTRAINT uq_max_limits_user_uin UNIQUE(user_id, uin)
$ddl$, ARRAY['user_id', 'uin', 'status'], false),
('max_limits_applications', $ddl$
 id serial PRIMARY KEY, user_id bigint NOT NULL, full_name varchar(200) NOT NULL,
 phone varchar(32) NOT NULL, uins_json text NOT NULL, status varchar(16) NOT NULL,
 created_at timestamptz NOT NULL, sent_at timestamptz, decided_at timestamptz, decided_by bigint
$ddl$, ARRAY['user_id', 'status'], false),
('max_limits_cursors', $ddl$
 id serial PRIMARY KEY, marker bigint, updated_at timestamptz NOT NULL
$ddl$, ARRAY[]::text[], false),
('max_limits_outbox', $ddl$
 id serial PRIMARY KEY, user_id bigint NOT NULL, uin varchar(120) NOT NULL,
 version_id integer NOT NULL, text text NOT NULL, attempts integer NOT NULL,
 next_attempt_at timestamptz NOT NULL, delivered_at timestamptz, cancelled_at timestamptz,
 created_at timestamptz NOT NULL,
 CONSTRAINT uq_max_limits_outbox_recipient_change UNIQUE(user_id, version_id, uin)
$ddl$, ARRAY['user_id', 'uin', 'version_id', 'next_attempt_at', 'delivered_at', 'cancelled_at'], false);

DO $templates$
DECLARE s record; col text;
BEGIN
  FOR s IN SELECT * FROM migration_specs LOOP
    EXECUTE format('CREATE TEMP TABLE %I (%s)', s.name, s.ddl);
    FOREACH col IN ARRAY s.indexes LOOP
      EXECUTE format('CREATE %s INDEX %I ON pg_temp.%I (%I)',
        CASE WHEN col LIKE 'UNIQUE %' THEN 'UNIQUE' ELSE '' END,
        'ix_' || s.name || '_' || replace(col, 'UNIQUE ', ''),
        s.name, replace(col, 'UNIQUE ', ''));
    END LOOP;
  END LOOP;
END $templates$;

-- Все предусловия и совместимость проверяются до изменения public.
DO $preflight$
DECLARE s record; a record; c record; i record; t text; col text;
        actual oid; expected oid; found_any boolean; expr text; seq text;
BEGIN
  FOREACH t IN ARRAY ARRAY[
    'settings','companies','uploads','limit_datasets','limit_versions',
    'limit_objects','limit_row_snapshots','limit_comparisons','limit_changes',
    'limit_comparison_resolutions','limit_version_tombstones',
    'analog_passports','analog_passport_versions','analog_cost_index_references'
  ] LOOP
    IF NOT EXISTS (SELECT FROM pg_class WHERE oid=to_regclass('public.' || t) AND relkind='r') THEN
      RAISE EXCEPTION 'Не выполнена миграция до cutoff: отсутствует обычная таблица public.%', t;
    END IF;
  END LOOP;
  IF NOT EXISTS (SELECT FROM pg_attribute WHERE attrelid='public.settings'::regclass
      AND attname='key' AND atttypid='varchar'::regtype AND atttypmod=84 AND attnotnull)
    OR NOT EXISTS (SELECT FROM pg_attribute WHERE attrelid='public.settings'::regclass
      AND attname='value' AND atttypid='text'::regtype)
    OR NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid='public.settings'::regclass
      AND contype='p' AND pg_get_constraintdef(oid)='PRIMARY KEY (key)') THEN
    RAISE EXCEPTION 'Несовместима структура settings';
  END IF;
  -- Даже наличие нового MAX состояния не доказывает корректный перенос старых прав.
  FOREACH t IN ARRAY ARRAY[
    'limit_recipients','limit_subscriptions','limit_moderation_requests',
    'limit_bot_dialogs','limit_deliveries','limit_delivery_parts',
    'limit_delivery_attempts','limit_bot_outbound','limit_max_events',
    'daily_reconciliation_alert_deliveries'
  ] LOOP
    IF to_regclass('public.' || t) IS NOT NULL THEN
      EXECUTE format('SELECT EXISTS (SELECT FROM public.%I)', t) INTO found_any;
      IF found_any THEN
        RAISE EXCEPTION 'MAX legacy blocker: public.% содержит историю/права. Требуется отдельный согласованный план, автоматического переноса нет', t;
      END IF;
    END IF;
  END LOOP;
  FOREACH t IN ARRAY ARRAY['telegram_limits_outbox','telegram_limits_cursors','telegram_limits_subscriptions'] LOOP
    IF to_regclass('public.' || t) IS NOT NULL THEN
      RAISE EXCEPTION 'Startup удалит public.%; сначала устраните destructive startup в отдельном релизе, не удаляйте историю', t;
    END IF;
  END LOOP;
  IF EXISTS (SELECT FROM public.settings WHERE key IN ('telegram_limits_offset','telegram_limits_username')) THEN
    RAISE EXCEPTION 'Startup удаляет legacy Telegram settings; нужен отдельный безопасный релиз';
  END IF;
  found_any := false;
  IF to_regclass('public.max_limits_outbox') IS NOT NULL THEN
    EXECUTE 'SELECT EXISTS (SELECT FROM public.max_limits_outbox)' INTO found_any;
  END IF;
  IF NOT EXISTS (SELECT FROM public.settings WHERE key='max_limits_version_id')
     AND (EXISTS (SELECT FROM public.settings WHERE key='max_limits_dataset_id' AND coalesce(value,'')<>'')
          OR found_any) THEN
    RAISE EXCEPTION 'MAX selection blocker: отсутствует max_limits_version_id; startup может выбрать версию/отменить outbox. Нужен отдельный согласованный перенос';
  END IF;
  IF EXISTS (SELECT FROM public.settings
      WHERE key='migration_20260929_since_20260924_structural'
      AND value IS DISTINCT FROM '4e9ca82') THEN
    RAISE EXCEPTION 'Несовместимое значение маркера этой миграции';
  END IF;
  IF EXISTS (SELECT FROM pg_attribute WHERE attrelid='public.limit_versions'::regclass
      AND attname='progress_json' AND NOT attisdropped
      AND (atttypid<>'text'::regtype OR attnotnull OR attgenerated<>'' OR attidentity<>'')) THEN
    RAISE EXCEPTION 'Несовместима limit_versions.progress_json';
  END IF;
  IF EXISTS (SELECT FROM pg_attrdef d JOIN pg_attribute att ON
      att.attrelid=d.adrelid AND att.attnum=d.adnum
      WHERE d.adrelid='public.limit_versions'::regclass AND att.attname='progress_json'
      AND pg_get_expr(d.adbin,d.adrelid) <> '''{}''::text') THEN
    RAISE EXCEPTION 'Неожиданный DEFAULT limit_versions.progress_json';
  END IF;
  FOR s IN SELECT * FROM migration_specs LOOP
    actual := to_regclass('public.' || s.name);
    expected := to_regclass('pg_temp.' || s.name);
    IF actual IS NULL THEN
      IF s.prerequisite THEN
        RAISE EXCEPTION 'Вне периода: public.% должна существовать до 24.09; ремонт отдельной миграцией', s.name;
      END IF;
      -- SERIAL и именованные constraints/indexes также занимают имена relations.
      FOR t IN SELECT relname FROM pg_class WHERE relnamespace=pg_my_temp_schema()
        AND (oid IN (SELECT indexrelid FROM pg_index WHERE indrelid=expected)
          OR relname=s.name || '_id_seq' OR relname=s.name || '_user_id_seq') LOOP
        IF to_regclass('public.' || t) IS NOT NULL THEN
          RAISE EXCEPTION 'Конфликт имени public.%', t;
        END IF;
      END LOOP;
      CONTINUE;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_class WHERE oid=actual AND relkind='r' AND NOT relrowsecurity) THEN
      RAISE EXCEPTION 'Необычный объект/RLS public.%', s.name;
    END IF;
    EXECUTE format('LOCK TABLE public.%I IN ACCESS EXCLUSIVE MODE', s.name);
    EXECUTE format('SELECT EXISTS (SELECT FROM public.%I)', s.name) INTO found_any;
    FOR a IN SELECT * FROM pg_attribute WHERE attrelid=expected AND attnum>0 AND NOT attisdropped LOOP
      SELECT * INTO c FROM pg_attribute WHERE attrelid=actual AND attname=a.attname AND NOT attisdropped;
      IF NOT FOUND THEN
        IF s.prerequisite OR a.attname IN ('id','user_id') OR (a.attnotnull AND found_any) THEN
          RAISE EXCEPTION 'Нельзя безопасно дополнить public.%.% (ключ/непустая таблица/предусловие)', s.name,a.attname;
        END IF;
        CONTINUE;
      END IF;
      IF (c.atttypid,c.atttypmod,c.attnotnull,c.attgenerated,c.attidentity,c.attcollation)
         IS DISTINCT FROM (a.atttypid,a.atttypmod,a.attnotnull,a.attgenerated,a.attidentity,a.attcollation) THEN
        RAISE EXCEPTION 'Несовместимая колонка public.%.%',s.name,a.attname;
      END IF;
      SELECT pg_get_expr(adbin,adrelid) INTO expr FROM pg_attrdef WHERE adrelid=actual AND adnum=c.attnum;
      IF a.attname='id' OR (s.name='max_limits_profiles' AND a.attname='user_id') THEN
        seq := pg_get_serial_sequence('public.' || s.name,a.attname);
        IF seq IS NULL OR expr IS DISTINCT FROM format('nextval(%L::regclass)',seq::regclass::text) THEN
          RAISE EXCEPTION 'Нет штатного SERIAL/владения sequence public.%.%',s.name,a.attname;
        END IF;
      ELSIF expr IS DISTINCT FROM (SELECT pg_get_expr(adbin,adrelid) FROM pg_attrdef WHERE adrelid=expected AND adnum=a.attnum) THEN
        RAISE EXCEPTION 'Несовместимый DEFAULT public.%.%',s.name,a.attname;
      END IF;
    END LOOP;
    IF EXISTS (SELECT FROM pg_attribute att WHERE att.attrelid=actual AND att.attnum>0 AND NOT att.attisdropped
      AND NOT EXISTS (SELECT FROM pg_attribute e WHERE e.attrelid=expected AND e.attname=att.attname AND NOT e.attisdropped)) THEN
      RAISE EXCEPTION 'Неожиданные колонки public.% (не удаляются)',s.name;
    END IF;
    FOR c IN SELECT * FROM pg_constraint WHERE conrelid=actual LOOP
      IF NOT EXISTS (SELECT FROM pg_constraint e WHERE e.conrelid=expected AND e.conname=c.conname
        AND pg_get_constraintdef(e.oid)=pg_get_constraintdef(c.oid)) THEN
        RAISE EXCEPTION 'Несовместимый constraint %.%',s.name,c.conname;
      END IF;
    END LOOP;
    FOR c IN SELECT * FROM pg_constraint WHERE conrelid=expected LOOP
      IF NOT EXISTS (SELECT FROM pg_constraint WHERE conrelid=actual AND conname=c.conname)
        AND (s.prerequisite OR to_regclass('public.' || c.conname) IS NOT NULL) THEN
        RAISE EXCEPTION 'Отсутствует constraint/конфликт имени %.%',s.name,c.conname;
      END IF;
    END LOOP;
    FOR i IN SELECT x.*, r.relname FROM pg_index x JOIN pg_class r ON r.oid=x.indexrelid
      WHERE x.indrelid=expected LOOP
      actual := to_regclass('public.' || i.relname);
      IF actual IS NULL THEN
        IF s.prerequisite THEN RAISE EXCEPTION 'Нет prerequisite index %',i.relname; END IF;
      ELSIF NOT EXISTS (SELECT FROM pg_index x WHERE x.indexrelid=actual
        AND x.indrelid=to_regclass('public.' || s.name)
        AND x.indisvalid AND x.indisready AND x.indisunique=i.indisunique
        AND substring(pg_get_indexdef(x.indexrelid) FROM ' USING .*')
          =substring(pg_get_indexdef(i.indexrelid) FROM ' USING .*')) THEN
        RAISE EXCEPTION 'Несовместимый index public.%',i.relname;
      END IF;
      actual := to_regclass('public.' || s.name);
    END LOOP;
    -- Дополнительные UNIQUE индексы меняют допустимые данные: не принимаем молча.
    IF EXISTS (SELECT FROM pg_index x JOIN pg_class r ON r.oid=x.indexrelid
      WHERE x.indrelid=actual AND x.indisunique AND NOT EXISTS (
        SELECT FROM pg_index e JOIN pg_class er ON er.oid=e.indexrelid
        WHERE e.indrelid=expected AND er.relname=r.relname)) THEN
      RAISE EXCEPTION 'Неожиданный UNIQUE index public.%',s.name;
    END IF;
  END LOOP;
END $preflight$;

DO $ddl$
DECLARE s record; a record; c record; col text; actual oid; expected oid;
BEGIN
  -- Nullable, без backfill и server default: Python default ORM не является SQL default.
  IF NOT EXISTS (SELECT FROM pg_attribute WHERE attrelid='public.limit_versions'::regclass
      AND attname='progress_json' AND NOT attisdropped) THEN
    ALTER TABLE public.limit_versions ADD COLUMN progress_json text;
  END IF;
  FOR s IN SELECT * FROM migration_specs WHERE NOT prerequisite LOOP
    actual := to_regclass('public.' || s.name);
    expected := to_regclass('pg_temp.' || s.name);
    IF actual IS NULL THEN
      EXECUTE format('CREATE TABLE public.%I (%s)',s.name,s.ddl);
    ELSE
      FOR a IN SELECT * FROM pg_attribute WHERE attrelid=expected AND attnum>0 AND NOT attisdropped
        AND NOT EXISTS (SELECT FROM pg_attribute x WHERE x.attrelid=actual AND x.attname=pg_attribute.attname AND NOT x.attisdropped) LOOP
        EXECUTE format('ALTER TABLE public.%I ADD COLUMN %I %s %s %s',
          s.name,a.attname,format_type(a.atttypid,a.atttypmod),
          CASE WHEN a.attnotnull THEN 'NOT NULL' ELSE '' END,
          CASE WHEN s.name='max_limits_subscriptions' AND a.attname='active' THEN 'DEFAULT true' ELSE '' END);
      END LOOP;
      FOR c IN SELECT * FROM pg_constraint WHERE conrelid=expected
        AND NOT EXISTS (SELECT FROM pg_constraint x WHERE x.conrelid=actual AND x.conname=pg_constraint.conname) LOOP
        EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I %s',s.name,c.conname,pg_get_constraintdef(c.oid));
      END LOOP;
    END IF;
    FOREACH col IN ARRAY s.indexes LOOP
      IF to_regclass('public.ix_' || s.name || '_' || replace(col,'UNIQUE ','')) IS NULL THEN
        EXECUTE format('CREATE %s INDEX %I ON public.%I (%I)',
          CASE WHEN col LIKE 'UNIQUE %' THEN 'UNIQUE' ELSE '' END,
          'ix_' || s.name || '_' || replace(col,'UNIQUE ',''),s.name,replace(col,'UNIQUE ',''));
      END IF;
    END LOOP;
  END LOOP;
END $ddl$;

-- Только маркер этого структурного SQL, НЕ маркеры startup/backfill/MAX.
INSERT INTO public.settings(key,value)
VALUES ('migration_20260929_since_20260924_structural','4e9ca82')
ON CONFLICT (key) DO NOTHING;
\if :apply
  COMMIT;
  \echo 'APPLIED: только структурная миграция; запуск приложения НЕ разрешён этим результатом.'
\else
  ROLLBACK;
  \echo 'DRY RUN OK: все изменения отменены. Для записи: psql -X -v apply=true -f ...'
\endif