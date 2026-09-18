/* Read-only source passport for the operational and annual indicators. */
(function() {
    'use strict';

    const root = window;
    const state = {
        mode: 'operational',
        operational: null,
        annual: null,
        reason: null,
        expanded: false,
        initialized: false
    };

    const esc = value => {
        if (value === null || value === undefined) return '';
        return String(value).replace(/[&<>'"]/g, character => ({
            '&': '&amp;',
            '<': '&lt;',
            '>': '&gt;',
            "'": '&#39;',
            '"': '&quot;'
        })[character]);
    };

    const isObject = value => value !== null && typeof value === 'object' &&
        !Array.isArray(value);
    const own = (object, key) => isObject(object) &&
        Object.prototype.hasOwnProperty.call(object, key);
    const first = (object, keys) => {
        if (!isObject(object)) return null;
        for (const key of keys) {
            if (own(object, key) && object[key] !== null &&
                object[key] !== undefined && object[key] !== '') {
                return object[key];
            }
        }
        return null;
    };
    const text = value => {
        if (value === null || value === undefined) return '';
        if (typeof value === 'string' || typeof value === 'number' ||
            typeof value === 'boolean') return String(value);
        try {
            return JSON.stringify(value);
        } catch (error) {
            return String(value);
        }
    };
    const scalarList = value => {
        if (value === null || value === undefined || value === '') return [];
        if (Array.isArray(value)) return value.reduce(
            (result, item) => result.concat(scalarList(item)), []);
        if (isObject(value)) {
            const nested = first(value, [
                'items', 'values', 'dates', 'accounts', 'actual', 'available',
                'loaded', 'expected', 'metadata'
            ]);
            if (nested !== null && nested !== value) return scalarList(nested);
            return Object.keys(value).map(key => key);
        }
        return [value];
    };
    const listValue = value => value === null || value === undefined ? [] :
        (Array.isArray(value) ? value : [value]);
    const unique = values => Array.from(new Set(values.map(value => text(value))
        .map(value => value.trim()).filter(Boolean)));

    const formatDate = value => {
        const raw = text(value).trim();
        if (!raw) return '';
        const match = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)?$/.exec(raw);
        if (!match) return raw;
        const date = `${match[3]}.${match[2]}.${match[1]}`;
        return match[4] ? `${date} ${match[4]}:${match[5]}${match[6] ? `:${match[6]}` : ''}` : date;
    };
    const display = value => esc(formatDate(value) || text(value) || 'Не указано');

    const accountLabel = value => {
        if (!isObject(value)) return text(value);
        const number = first(value, ['account', 'number', 'code', 'acc', 'id']);
        const name = first(value, ['name', 'title', 'label', 'description']);
        if (number !== null && name !== null) return `${text(number)} — ${text(name)}`;
        return text(number !== null ? number : (name !== null ? name : value));
    };
    const accountList = value => {
        if (Array.isArray(value)) return unique(value.map(accountLabel));
        if (!isObject(value)) return scalarList(value);
        return unique(Object.keys(value).map(key => {
            const item = value[key];
            if (isObject(item)) {
                const label = accountLabel(Object.assign({account: key}, item));
                return label || key;
            }
            return key;
        }));
    };

    const htmlList = (items, empty = 'Нет данных') => {
        const values = unique(items || []);
        if (!values.length) return `<div class="ki-source-passport-empty">${esc(empty)}</div>`;
        return `<ul class="ki-source-passport-list">${values.map(item =>
            `<li>${esc(item)}</li>`).join('')}</ul>`;
    };
    const section = (title, body, modifier = '') =>
        `<section class="ki-source-passport-section${modifier ? ` ${modifier}` : ''}">
            <h3>${esc(title)}</h3>${body}
        </section>`;
    const fact = (label, value, modifier = '') =>
        `<div class="ki-source-passport-fact${modifier ? ` ${modifier}` : ''}">
            <dt>${esc(label)}</dt><dd>${value || '<span class="ki-source-passport-muted">Не указано</span>'}</dd>
        </div>`;

    const issueLabel = item => {
        if (!isObject(item)) return text(item);
        const start = first(item, ['start', 'from', 'date_from', 'period_start']);
        const end = first(item, ['end', 'to', 'date_to', 'period_end']);
        const date = first(item, ['date', 'day', 'period']);
        const range = start && end ? `${formatDate(start)} — ${formatDate(end)}` :
            (date !== null ? formatDate(date) : '');
        const account = first(item, ['account', 'acc', 'account_number']);
        const reason = first(item, ['reason', 'message', 'detail', 'description']);
        const count = first(item, ['count', 'rows', 'occurrences']);
        const parts = [];
        if (range) parts.push(range);
        if (account !== null) parts.push(`счёт ${text(account)}`);
        if (reason !== null) parts.push(text(reason));
        if (count !== null && !isObject(count)) parts.push(`строк: ${text(count)}`);
        if (!parts.length) return text(item);
        return parts.join(' · ');
    };
    const issueList = (value, empty) => {
        let items = [];
        if (Array.isArray(value)) items = value;
        else if (value !== null && value !== undefined) {
            if (isObject(value)) {
                const nested = first(value, ['items', 'values', 'dates', 'records',
                    'gaps', 'duplicates', 'discrepancies']);
                items = nested !== null ? (Array.isArray(nested) ? nested : [nested]) :
                    Object.keys(value).map(key => {
                        const item = value[key];
                        return isObject(item) ? Object.assign({date: key}, item) :
                            `${key}: ${text(item)}`;
                    });
            } else items = [value];
        }
        return htmlList(items.map(issueLabel), empty);
    };

    const dateItems = value => {
        if (value === null || value === undefined) return [];
        if (Array.isArray(value)) return value.reduce(
            (result, item) => result.concat(dateItems(item)), []);
        if (!isObject(value)) return [formatDate(value)];
        const directDate = first(value, ['date', 'day', 'period_end', 'end']);
        if (directDate !== null) return [formatDate(directDate)];
        if (own(value, 'all')) {
            return dateItems(value.all)
                .concat(own(value, 'opening') ? dateItems(value.opening) : [])
                .concat(own(value, 'closing') ? dateItems(value.closing) : []);
        }
        if (own(value, 'observations')) {
            return dateItems(value.opening).concat(dateItems(value.closing))
                .concat(dateItems(value.observations));
        }
        const nested = first(value, ['dates', 'items', 'values', 'exact']);
        if (nested !== null && nested !== value) return dateItems(nested);
        const result = [];
        Object.keys(value).forEach(key => {
            const item = value[key];
            if (/^\d{4}-\d{2}-\d{2}/.test(key)) {
                result.push(formatDate(key));
            } else if (Array.isArray(item)) {
                item.forEach(date => result.push(`${key}: ${formatDate(
                    isObject(date) ? first(date, ['date', 'day', 'period_end']) : date)}`));
            } else if (isObject(item)) {
                const date = first(item, ['date', 'day', 'period_end', 'end']);
                if (date !== null) result.push(`${key}: ${formatDate(date)}`);
            } else if (item !== null && item !== undefined) {
                result.push(`${key}: ${text(item)}`);
            }
        });
        return result;
    };

    const statusLabel = value => {
        const labels = {
            accepted: 'Принят',
            active: 'Активна',
            complete: 'Полное покрытие',
            partial: 'Неполное покрытие',
            aggregate_only: 'Только агрегатные данные',
            observed_without_declared_period: 'Даты наблюдений без заявленного периода',
            no_turnover_dates: 'Нет дат оборотов',
            no_accepted_data: 'Принятых данных нет',
            parsed: 'Файл принят',
            excluded: 'Исключён',
            failure: 'Ошибка чтения',
            unreadable: 'Не прочитан'
        };
        const raw = text(value).trim();
        return labels[raw] || raw;
    };

    const balanceRows = accounts => Object.keys(accounts || {}).sort().map(account => {
        const item = accounts[account];
        const exact = isObject(item) ? first(item, ['exact_balances', 'balances']) : null;
        return {
            account,
            opening: isObject(exact) && own(exact, 'opening') ? exact.opening : null,
            closing: isObject(exact) && own(exact, 'closing') ? exact.closing : null
        };
    });

    const sourceRecords = value => {
        if (Array.isArray(value)) return value;
        if (!isObject(value)) return [];
        const nested = first(value, ['items', 'values', 'sources', 'files', 'uploads',
            'versions', 'inactive_versions', 'accepted_versions']);
        if (nested !== null && nested !== value) return sourceRecords(nested);
        return Object.keys(value).map(key => {
            const item = value[key];
            return isObject(item) ? Object.assign({year: key}, item) :
                {year: key, filename: item};
        });
    };

    const operationalModel = data => {
        const passport = data && (data.source_passport || data.sourcePassport);
        if (!isObject(passport)) return null;
        const period = passport.actual_period;
        const periodObject = isObject(period) ? period : {};
        const selectedPeriod = isObject(passport.period) ? passport.period : {};
        let accounts = first(passport, [
            'accounts', 'account_sets', 'account_coverage'
        ]);
        accounts = isObject(accounts) ? accounts : {};
        let actualAccounts = first(passport, [
            'actual_accounts', 'available_accounts', 'loaded_accounts',
            'accounts_actual', 'accounts_loaded'
        ]);
        let metadataAccounts = first(passport, [
            'metadata_accounts', 'expected_accounts', 'accounts_metadata',
            'required_accounts', 'metadata'
        ]);
        if (actualAccounts === null) {
            actualAccounts = first(accounts, [
                'actual', 'available', 'loaded', 'actual_accounts', 'available_accounts'
            ]);
        }
        if (metadataAccounts === null) {
            metadataAccounts = first(accounts, [
                'metadata', 'expected', 'required', 'metadata_accounts', 'expected_accounts'
            ]);
        }
        if (metadataAccounts === null) {
            const uploadMetadata = first(passport, ['uploads', 'upload_metadata']);
            const uploadRecords = sourceRecords(uploadMetadata);
            const declared = uploadRecords.length ?
                uploadRecords.reduce((result, item) => {
                    if (!isObject(item)) return result;
                    let raw = item.accounts_loaded;
                    if (typeof raw === 'string') {
                        try { raw = JSON.parse(raw); } catch (error) { /* keep raw */ }
                    }
                    return result.concat(scalarList(raw));
                }, []) : [];
            if (declared.length) metadataAccounts = declared;
        }
        const uploadRecords = sourceRecords(first(passport, [
            'uploads', 'upload_metadata'
        ]));
        const uploadDates = uploadRecords.reduce((result, record) => {
            const id = first(record, ['id', 'upload_id']);
            const uploaded = first(record, ['uploaded_at', 'imported_at', 'created_at']);
            if (id !== null && uploaded !== null) result[text(id)] = uploaded;
            return result;
        }, {});
        const sources = sourceRecords(first(passport, [
            'sources', 'files', 'uploads', 'accepted_sources'
        ])).map(record => {
            if (!isObject(record) || first(record, [
                'uploaded_at', 'imported_at', 'created_at'
            ]) !== null) return record;
            const uploadDate = uploadDates[text(first(record, [
                'upload_id', 'import_id'
            ]))];
            return uploadDate === undefined ? record :
                Object.assign({}, record, {uploaded_at: uploadDate});
        });
        const balanceDates = first(passport, [
            'exact_balance_dates', 'balance_dates', 'available_balance_dates',
            'balances'
        ]);
        const accountBalanceDates = Object.keys(accounts).reduce((result, account) => {
            const item = accounts[account];
            if (!isObject(item)) return result;
            const exact = first(item, ['exact_balances', 'balances']);
            return result.concat(dateItems(exact).map(date => `${account}: ${date}`));
        }, []);
        const turnover = first(passport, [
            'turnover_gaps', 'gaps', 'coverage_gaps', 'missing_turnover',
            'turnover'
        ]);
        const turnoverGaps = isObject(turnover) ? first(turnover, [
            'gaps', 'missing', 'items', 'dates', 'values'
        ]) || turnover : turnover;
        const accountTurnoverGaps = Object.keys(accounts).reduce((result, account) => {
            const item = accounts[account];
            if (!isObject(item)) return result;
            const coverage = first(item, ['turnover_coverage', 'turnover']);
            const gaps = isObject(coverage) ? first(coverage, [
                'gaps', 'missing', 'items'
            ]) : coverage;
            if (!gaps) return result;
            const values = Array.isArray(gaps) ? gaps : [gaps];
            return result.concat(values.map(gap => isObject(gap) ?
                Object.assign({account}, gap) : `${account}: ${text(gap)}`));
        }, []);
        const cardRecords = Array.isArray(passport.cards) ? passport.cards :
            (isObject(passport.cards) ? Object.keys(passport.cards).map(id =>
                Object.assign({id}, passport.cards[id])) : []);
        const cardReasons = cardRecords.filter(card =>
            card && (card.available === false || card.status === 'unavailable' ||
                card.reason || card.missing_reason)).map(card => ({
                    title: first(card, ['title', 'name', 'id']) || 'Показатель',
                    reason: first(card, ['reason', 'missing_reason',
                        'unavailable_reason']) || 'Недостаточно фактов'
                }));
        const sourceDiscrepancies = first(passport, [
            'discrepancies', 'metadata_discrepancies'
        ]);
        const overlaps = first(passport, ['overlaps']);
        const failures = first(passport, ['failures']);
        const dailyControlReconciliations = sourceRecords(first(passport, [
            'daily_control_reconciliations'
        ]));
        const coverageStatuses = unique(Object.keys(accounts).map(account => {
            const item = accounts[account];
            const coverage = isObject(item) ?
                first(item, ['turnover_coverage', 'turnover']) : null;
            return isObject(coverage) ? first(coverage, ['status']) : null;
        }).filter(Boolean)).map(statusLabel);
        return {
            passport,
            uploadedAt: first(passport, [
                'uploaded_at', 'latest_upload_at', 'imported_at', 'upload_date',
                'created_at'
            ]),
            periodStart: first(periodObject, ['start']),
            periodEnd: first(periodObject, ['end']),
            selectedStart: first(selectedPeriod, ['start']),
            selectedEnd: first(selectedPeriod, ['end']),
            status: first(passport, ['period_status']),
            provenanceStatus: (listValue(sourceDiscrepancies).length ||
                listValue(failures).length) ?
                'Есть замечания к источникам и метаданным' :
                'Замечания к источникам и метаданным не переданы',
            sources,
            actualAccounts: accountList(actualAccounts),
            metadataAccounts: accountList(metadataAccounts),
            balanceRows: balanceRows(accounts),
            balanceDates: dateItems(balanceDates).concat(accountBalanceDates),
            turnoverGaps: listValue(turnoverGaps).concat(accountTurnoverGaps),
            duplicates: first(passport, [
                'duplicates', 'duplicate_rows', 'duplicate_periods'
            ]),
            discrepancies: listValue(sourceDiscrepancies).concat(
                listValue(overlaps)).concat(listValue(failures)),
            dailyControlReconciliations,
            cardReasons,
            coverageStatus: coverageStatuses.join(', ')
        };
    };

    const annualModel = data => {
        if (!isObject(data)) return null;
        const passport = isObject(data.source_passport) ? data.source_passport : {};
        const sourceValue = first(passport, [
            'accepted_versions', 'versions'
        ]) || first(data, ['sources', 'accepted_sources', 'accepted_versions', 'versions']);
        const selectedRange = first(passport, ['selected_range']) || {};
        const activatedAt = first(data, ['updated_at', 'activated_at']) ||
            first(passport, ['latest_accepted']) &&
            first(first(passport, ['latest_accepted']), ['activated_at']);
        const sources = sourceRecords(sourceValue).map(source => {
            if (!isObject(source) || first(source, [
                'updated_at', 'activated_at', 'activation_date'
            ]) !== null || activatedAt === null) return source;
            return Object.assign({}, source, {updated_at: activatedAt});
        });
        const sourceCardReasons = sourceRecords(first(passport, [
            'card_reason_summary'
        ]));
        const reasonByCard = sourceCardReasons.reduce((result, card) => {
            if (isObject(card) && first(card, ['id']) !== null) {
                result[text(first(card, ['id']))] = first(card, [
                    'missing_reason', 'reason'
                ]);
            }
            return result;
        }, {});
        let cards = Array.isArray(data.cards) ? data.cards :
            (isObject(data.cards) ? Object.keys(data.cards).map(id =>
                isObject(data.cards[id]) ? Object.assign({id}, data.cards[id]) :
                    {id, value: data.cards[id]}) : []);
        cards = cards.map(card => {
            if (!isObject(card)) return card;
            const reason = first(card, ['missing_reason', 'unavailable_reason',
                'reason']) || reasonByCard[text(first(card, ['id']))];
            return reason ? Object.assign({}, card, {missing_reason: reason}) : card;
        });
        if (!cards.length && sourceCardReasons.length) cards = sourceCardReasons;
        const unavailableCards = cards.filter(card =>
            card && (card.value === null || card.value === undefined ||
                card.available === false || card.is_available === false ||
                card.missing_reason || card.unavailable_reason));
        return {
            status: first(passport, ['status', 'state']) || 'accepted',
            year: first(data, ['year', 'year_to']) ||
                first(selectedRange, ['year_to']),
            yearFrom: first(data, ['year_from']) ||
                first(selectedRange, ['year_from']),
            selectedRange,
            acceptedYears: scalarList(first(passport, ['accepted_years'])),
            missingYears: scalarList(first(passport, ['missing_years']) ||
                first(selectedRange, ['missing_years'])),
            sources,
            inactiveVersions: sourceRecords(first(passport, [
                'inactive_version_history'
            ])),
            duplicateUploads: first(passport, ['duplicate_uploads']),
            activeConflicts: first(passport, ['active_conflicts']),
            cards,
            unavailableCards,
            warnings: listValue(passport.warnings).concat(listValue(data.warnings))
        };
    };

    const sourceRow = (record, fallbackUpload) => {
        const filename = first(record, ['filename', 'file', 'name', 'source_file']) ||
            'Файл без названия';
        const year = first(record, ['year', 'period_year']);
        const periodEnd = first(record, [
            'actual_period_end', 'period_end', 'end', 'report_end'
        ]) || (year !== null ? `31.12.${text(year)}` : null);
        const uploadedAt = first(record, [
            'uploaded_at', 'imported_at', 'created_at'
        ]) || fallbackUpload;
        const fileMtime = first(record, [
            'source_updated_at', 'file_mtime', 'mtime', 'modified_at'
        ]);
        const version = first(record, ['version', 'version_no', 'revision']);
        const rawStatus = first(record, ['status', 'state']);
        const status = (rawStatus && statusLabel(rawStatus)) ||
            (record.accepted === true || record.is_active === true ? 'Принята' : '');
        return `<tr>
            <td>${esc(filename)}${year !== null ? `<span class="ki-source-passport-subvalue">${esc(year)} год</span>` : ''}</td>
            <td>${display(uploadedAt)}</td>
            <td>${display(periodEnd)}</td>
            <td>${display(fileMtime)}</td>
            <td>${version !== null ? esc(version) : '<span class="ki-source-passport-muted">Не указана</span>'}</td>
            <td>${status ? esc(status) : '<span class="ki-source-passport-muted">Принята</span>'}</td>
        </tr>`;
    };

    const sourcesTable = (records, fallbackUpload, annual) => {
        if (!records.length) {
            return `<div class="ki-source-passport-empty">${annual ?
                'В ответе нет принятых годовых версий.' :
                'В ответе нет перечня файлов ОСВ.'}</div>`;
        }
        return `<div class="ki-source-passport-table-wrap"><table class="ki-source-passport-table">
            <thead><tr>
                <th>${annual ? 'Годовой файл' : 'Файл ОСВ'}</th>
                <th>Загружен в систему</th>
                <th>Фактический конец периода</th>
                <th>${annual ? 'Активирован' : 'Изменён файл (mtime)'}</th>
                <th>Версия</th><th>Состояние</th>
            </tr></thead>
            <tbody>${records.map(record => sourceRow(
                isObject(record) ? record : {filename: record}, fallbackUpload
            )).join('')}</tbody>
        </table></div>`;
    };

    const annualVersionsTable = records => {
        if (!records.length) {
            return '<div class="ki-source-passport-empty">В ответе нет принятых годовых версий.</div>';
        }
        return `<div class="ki-source-passport-table-wrap"><table class="ki-source-passport-table ki-source-passport-versions">
            <thead><tr>
                <th>Год</th><th>Файл</th><th>Версия</th><th>Импорт</th>
                <th>Хеш файла</th><th>Загружен</th><th>Подтверждён</th>
                <th>Активирован</th><th>Фактический конец периода</th>
            </tr></thead>
            <tbody>${records.map(record => {
                record = isObject(record) ? record : {filename: record};
                const year = first(record, ['year', 'period_year']);
                const filename = first(record, ['filename', 'file', 'name']) ||
                    'Файл без названия';
                const version = first(record, ['version', 'version_no', 'revision']);
                const importId = first(record, ['import_id', 'id']);
                const hash = first(record, ['file_hash', 'hash']);
                const uploaded = first(record, [
                    'uploaded_at', 'imported_at', 'created_at'
                ]);
                const confirmed = first(record, ['confirmed_at', 'confirmed']);
                const activated = first(record, [
                    'activated_at', 'updated_at', 'activation_date'
                ]);
                const periodEnd = first(record, [
                    'factual_period_end', 'actual_period_end', 'period_end',
                    'end', 'report_end'
                ]) || (year !== null ? `31.12.${text(year)}` : null);
                return `<tr>
                    <td>${display(year)}</td><td>${esc(filename)}</td>
                    <td>${display(version)}</td><td>${display(importId)}</td>
                    <td>${display(hash)}</td><td>${display(uploaded)}</td>
                    <td>${display(confirmed)}</td><td>${display(activated)}</td>
                    <td>${display(periodEnd)}</td>
                </tr>`;
            }).join('')}</tbody>
        </table></div>`;
    };

    const unavailableBalance = '<span class="ki-source-passport-balance-unavailable">Недоступно</span>';
    const amountDisplay = value => {
        if (value === null || value === undefined || value === '') {
            return unavailableBalance;
        }
        const numeric = typeof value === 'number' ? value :
            (typeof value === 'string' && value.trim() !== '' ? Number(value) : NaN);
        if (Number.isFinite(numeric)) {
            return esc(new Intl.NumberFormat('ru-RU', {
                maximumFractionDigits: 2
            }).format(numeric));
        }
        return esc(text(value));
    };
    const balanceCell = (record, key, isDate = false) => {
        if (!isObject(record) || record[key] === null ||
            record[key] === undefined || record[key] === '') {
            return unavailableBalance;
        }
        return isDate ? display(record[key]) : amountDisplay(record[key]);
    };
    const balanceDateCell = record => {
        const date = balanceCell(record, 'date', true);
        if (!isObject(record) || !record.source_file) return date;
        return `${date}<span class="ki-source-passport-balance-source">${esc(
            record.source_file)}</span>`;
    };
    const balancesTable = rows => {
        if (!rows.length) {
            return '<div class="ki-source-passport-empty">Точные остатки по счетам не переданы.</div>';
        }
        return `<div class="ki-source-passport-table-wrap"><table class="ki-source-passport-table ki-source-passport-balance-table">
            <thead><tr>
                <th rowspan="2">Счёт</th>
                <th colspan="3">Открывающий остаток</th>
                <th colspan="3">Закрывающий остаток</th>
            </tr><tr>
                <th>Дата</th><th>Дебет</th><th>Кредит</th>
                <th>Дата</th><th>Дебет</th><th>Кредит</th>
            </tr></thead>
            <tbody>${rows.map(row => `<tr>
                <td><strong>${esc(row.account)}</strong></td>
                <td>${balanceDateCell(row.opening)}</td>
                <td>${balanceCell(row.opening, 'debit')}</td>
                <td>${balanceCell(row.opening, 'credit')}</td>
                <td>${balanceDateCell(row.closing)}</td>
                <td>${balanceCell(row.closing, 'debit')}</td>
                <td>${balanceCell(row.closing, 'credit')}</td>
            </tr>`).join('')}</tbody>
        </table></div>`;
    };

    const reconciliationTable = rows => {
        if (!rows.length) {
            return '<div class="ki-source-passport-empty">Сверка с полным контрольным снимком не передана.</div>';
        }
        return `<div class="ki-source-passport-table-wrap"><table class="ki-source-passport-table">
            <thead><tr>
                <th>Счёт</th><th>Сторона</th><th>Интервал</th>
                <th>Контрольная сумма</th><th>Сумма дневных строк</th><th>Разница</th>
            </tr></thead>
            <tbody>${rows.map(row => `<tr class="${row.matches === false ?
                'ki-source-passport-reconciliation-mismatch' : ''}">
                <td><strong>${esc(row.account)}</strong></td>
                <td>${row.side === 'debit' ? 'Дебет' : 'Кредит'}</td>
                <td>${display(row.start)} — ${display(row.end)}</td>
                <td>${amountDisplay(row.control_amount)}</td>
                <td>${amountDisplay(row.daily_amount)}</td>
                <td><strong>${amountDisplay(row.difference)}</strong></td>
            </tr>`).join('')}</tbody>
        </table></div>`;
    };

    const operationalBody = model => {
        const passport = model.passport;
        const facts = [
            fact('Загружено в систему', display(model.uploadedAt)),
            fact('Фактическое начало периода', display(model.periodStart)),
            fact('Фактический конец периода', display(model.periodEnd)),
            fact('Выбранный период', `${display(model.selectedStart)} — ${display(model.selectedEnd)}`),
            fact('Покрытие выбранного периода', model.status ? esc(statusLabel(model.status)) : ''),
            fact('Проверка происхождения данных', esc(model.provenanceStatus)),
            fact('Статус покрытия оборотов', model.coverageStatus ?
                esc(model.coverageStatus) : '')
        ].join('');
        const accounts = `<div class="ki-source-passport-columns">
            <div><h4>Фактически доступны в данных</h4>${htmlList(
                model.actualAccounts, 'Не удалось определить счета из источника')}</div>
            <div><h4>Счета из метаданных</h4>${htmlList(
                model.metadataAccounts, 'Метаданные о счетах не переданы')}</div>
        </div>`;
        const dates = htmlList(model.balanceDates,
            'Точные даты сальдо не переданы');
        const cardReasons = model.cardReasons && model.cardReasons.length ?
            `<ul class="ki-source-passport-card-reasons">${model.cardReasons.map(card =>
                `<li><strong>${esc(card.title)}</strong><span>${esc(card.reason)}</span></li>`
            ).join('')}</ul>` :
            '<div class="ki-source-passport-empty">Недоступные карточки в выбранном ответе не обнаружены.</div>';
        return [
            `<dl class="ki-source-passport-facts">${facts}</dl>`,
            section('Файлы и фактические границы периода',
                sourcesTable(model.sources, null, false)),
            section('Счета: факт и метаданные', accounts),
            section('Точные остатки на границах периода',
                balancesTable(model.balanceRows)),
            section('Точные даты доступного сальдо', dates),
            section('Пропуски оборотов', issueList(model.turnoverGaps,
                'Пропуски оборотов не обнаружены или не переданы')),
            section('Сверка дневных оборотов с контрольной ОСВ',
                reconciliationTable(model.dailyControlReconciliations)),
            section('Дубли', issueList(model.duplicates,
                'Дубли не обнаружены или не переданы')),
            section('Расхождения и контрольные проверки', issueList(model.discrepancies,
                'Расхождения не обнаружены или не переданы')),
            section('Недоступность карточек', cardReasons)
        ].join('');
    };

    const annualBody = model => {
        const selected = isObject(model.selectedRange) ? model.selectedRange : {};
        const selectedFrom = first(selected, ['year_from', 'from']) ||
            model.yearFrom;
        const selectedTo = first(selected, ['year_to', 'to']) || model.year;
        const range = first(selected, ['label', 'period_label']) ||
            (selectedFrom !== null && selectedTo !== null &&
            String(selectedFrom) !== String(selectedTo) ?
                `${text(selectedFrom)}–${text(selectedTo)}` : text(selectedTo));
        const statusLabels = {
            accepted: 'Принятые данные активны',
            no_accepted_data: 'Принятых данных нет'
        };
        const status = statusLabels[model.status] || model.status;
        const unavailable = model.unavailableCards.map(card => {
            const title = first(card, ['title', 'name', 'id']) || 'Показатель';
            const reason = first(card, [
                'missing_reason', 'unavailable_reason', 'reason'
            ]) || 'Причина недоступности не передана';
            return `<li><strong>${esc(title)}</strong><span>${esc(reason)}</span></li>`;
        }).join('');
        const cardSection = unavailable ?
            `<ul class="ki-source-passport-card-reasons">${unavailable}</ul>` :
            '<div class="ki-source-passport-empty">Недоступные карточки в выбранном ответе не обнаружены.</div>';
        const requested = first(selected, ['requested', 'requested_year']);
        const requestedText = isObject(requested) ?
            (first(requested, ['label', 'period_label']) ||
                `${text(first(requested, ['year_from', 'from']) || '')}–${text(
                    first(requested, ['year_to', 'to']) || '')}`) : text(requested);
        const selectedSection = `<dl class="ki-source-passport-facts">
            ${fact('Состояние', status ? esc(status) : '')}
            ${fact('Запрошенный диапазон', requestedText ? esc(requestedText) : '')}
            ${fact('Выбранный диапазон', range ? esc(range) : '')}
            ${fact('Принятые годы', htmlList(model.acceptedYears,
                'Не переданы'))}
            ${fact('Отсутствующие годы', htmlList(model.missingYears,
                'Нет пропусков'))}
        </dl>`;
        const duplicateSection = section('Повторные загрузки',
            issueList(model.duplicateUploads,
                'Повторные загрузки не обнаружены или не переданы'));
        const conflictSection = section('Конфликты активных версий',
            issueList(model.activeConflicts,
                'Конфликты активных версий не обнаружены или не переданы'));
        const historySection = model.inactiveVersions.length ?
            section('История неактивных версий',
                annualVersionsTable(model.inactiveVersions)) : '';
        const warningsSection = model.warnings.length ?
            section('Предупреждения', issueList(model.warnings,
                'Предупреждений нет')) : '';
        return [
            selectedSection,
            section('Принятые годовые версии',
                annualVersionsTable(model.sources)),
            historySection,
            duplicateSection,
            conflictSection,
            warningsSection,
            section('Карточки без достаточных данных', cardSection)
        ].join('');
    };

    const Passport = {
        state,
        esc,
        operationalModel,
        annualModel,
        setMode(mode) {
            const normalized = mode === 'annual' ? 'annual' : 'operational';
            if (state.mode !== normalized) {
                state.mode = normalized;
                state.operational = null;
                state.annual = null;
                state.reason = null;
            }
            this.render();
        },
        clear(reason) {
            state.operational = null;
            state.annual = null;
            state.reason = reason ? text(reason) : null;
            this.render();
        },
        renderOperational(data) {
            state.mode = 'operational';
            state.operational = operationalModel(data);
            state.annual = null;
            state.reason = null;
            this.render();
        },
        renderAnnual(data) {
            state.mode = 'annual';
            state.annual = annualModel(data);
            state.operational = null;
            state.reason = null;
            this.render();
        },
        render() {
            const panel = document.getElementById('ki-source-passport');
            const body = document.getElementById('ki-source-passport-body');
            const toggle = document.getElementById('ki-source-passport-toggle');
            const summary = document.getElementById('ki-source-passport-summary');
            if (!panel || !body || !toggle) return;

            const isAnnual = state.mode === 'annual';
            const model = isAnnual ? state.annual : state.operational;
            const title = isAnnual ? 'Паспорт годовых источников' : 'Паспорт источника ОСВ';
            const content = model ? (isAnnual ? annualBody(model) :
                (model.passport ? operationalBody(model) : '')) :
                `<div class="ki-source-passport-empty">${state.reason ?
                    `Паспорт временно недоступен: ${esc(state.reason)}` :
                    'Паспорт будет заполнен после загрузки данных.'}</div>`;

            panel.classList.toggle('ki-source-passport-annual', isAnnual);
            panel.classList.toggle('ki-source-passport-operational', !isAnnual);
            panel.setAttribute('data-mode', state.mode);
            panel.querySelector('h2').textContent = title;
            summary.textContent = isAnnual ?
                'Принятые версии, даты импорта и причины недоступности карточек' :
                'Фактические даты, счета и полнота оборотов';
            body.innerHTML = content;
            body.hidden = !state.expanded;
            body.style.display = state.expanded ? 'block' : 'none';
            toggle.setAttribute('aria-expanded', state.expanded ? 'true' : 'false');
            const label = toggle.querySelector('span');
            if (label) label.textContent = state.expanded ? 'Свернуть' : 'Раскрыть';
            panel.classList.toggle('is-expanded', state.expanded);
        },
        bind() {
            const toggle = document.getElementById('ki-source-passport-toggle');
            if (!toggle || toggle.dataset.bound === '1') return;
            toggle.dataset.bound = '1';
            toggle.addEventListener('click', () => {
                state.expanded = !state.expanded;
                this.render();
            });
        },
        installAnnualHook() {
            const annual = root.KI_AnnualApp;
            if (!annual || annual.__sourcePassportHooked) return;
            annual.__sourcePassportHooked = true;
            const originalRender = annual.render;
            if (typeof originalRender === 'function') {
                annual.render = function() {
                    const result = originalRender.apply(this, arguments);
                    if (this.state && this.state.active) Passport.renderAnnual(this.state.data);
                    return result;
                };
            }
            const originalFetch = annual.fetchData;
            if (typeof originalFetch === 'function') {
                annual.fetchData = function() {
                    Passport.setMode('annual');
                    Passport.clear('Загрузка годовых данных');
                    const result = originalFetch.apply(this, arguments);
                    if (result && typeof result.then === 'function') {
                        return result.then(value => {
                            if (this.state && !this.state.data) {
                                Passport.clear('Не удалось загрузить годовые данные');
                            }
                            return value;
                        }, error => {
                            Passport.clear(error && error.message ?
                                error.message : 'Не удалось загрузить годовые данные');
                            throw error;
                        });
                    }
                    return result;
                };
            }
            if (annual.state && annual.state.active && annual.state.data) {
                this.renderAnnual(annual.state.data);
            }
        },
        init() {
            if (state.initialized) return;
            state.initialized = true;
            const appMode = root.KI_App && root.KI_App.state ?
                root.KI_App.state.mode : null;
            state.mode = appMode === 'annual' ||
                (root.location && /(?:^|[?&])mode=annual(?:&|$)/.test(
                    root.location.search || '')) ? 'annual' : 'operational';
            this.bind();
            this.installAnnualHook();
            this.render();
        }
    };

    root.KI_SourcePassport = Passport;
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => Passport.init());
    } else {
        Passport.init();
    }
})();