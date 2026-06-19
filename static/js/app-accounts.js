// ================================
// 账号保活诊断
// ================================

function getAboutDiagnosticsElements() {
    return {
        accountSelect: document.getElementById('aboutDiagnosticsAccount'),
        accountMeta: document.getElementById('aboutDiagnosticsAccountMeta'),
        refreshButton: document.getElementById('aboutDiagnosticsRefreshBtn'),
        keepaliveButton: document.getElementById('aboutDiagnosticsKeepaliveBtn'),
        historyButton: document.getElementById('aboutDiagnosticsHistoryBtn'),
        conversationInput: document.getElementById('aboutDiagnosticsConversationId'),
        statusContainer: document.getElementById('aboutDiagnosticsStatus'),
        historyContainer: document.getElementById('aboutConversationHistory'),
    };
}

function getAboutSelectedAccountId() {
    return document.getElementById('aboutDiagnosticsAccount')?.value?.trim() || '';
}

function getAboutStatusText(type, value) {
    const normalized = String(value || '').trim();
    if (!normalized) {
        return '暂无';
    }

    const maps = {
        connection: {
            connected: '已连接',
            reconnecting: '重连中',
            connecting: '连接中',
            disconnected: '未连接',
            failed: '失败',
            closed: '已关闭',
            not_running: '未运行',
            unknown: '未知',
        },
        keepalive: {
            started: '执行中',
            success: '成功',
            recovered: '已恢复',
            auth_failed: '鉴权失败',
            api_failed: '接口失败',
            network_failed: '网络异常',
            response_parse_failed: '响应解析失败',
            exception: '执行异常',
        },
        token: {
            started: '执行中',
            success: '成功',
            skipped_cooldown: '冷却跳过',
            manual_refresh_active: '手动刷新进行中',
            manual_refresh_browser_stabilizing: '浏览器稳定中',
            post_slider_session_settling: '滑块后稳定中',
            restarted_after_cookie_refresh: '已触发重连',
            captcha_max_retries_exceeded: '滑块重试超限',
            token_expired_recovery_failed: '过期恢复失败',
            token_refresh_failed: '刷新失败',
            token_refresh_exception: '刷新异常',
            token_init_failed: '初始化失败',
            token_missing_after_refresh: '刷新后无 Token',
            token_missing: '无 Token',
            failed: '失败',
        },
        stream: {
            healthy: '正常',
            recovered: '已恢复',
            warming_up: '预热中',
            watching: '观察中',
            recovering: '恢复中',
            suspected_stale: '疑似停滞',
            connection_unready: '连接未就绪',
            not_running: '未运行',
        },
    };

    return maps[type]?.[normalized] || normalized;
}

function getAboutStatusVariant(type, value) {
    const normalized = String(value || '').trim();
    if (!normalized) {
        return 'secondary';
    }

    if (type === 'connection') {
        if (normalized === 'connected') return 'success';
        if (normalized === 'connecting' || normalized === 'reconnecting') return 'warning';
        if (normalized === 'failed') return 'danger';
        if (normalized === 'not_running' || normalized === 'disconnected' || normalized === 'closed') return 'secondary';
        return 'info';
    }

    if (type === 'stream') {
        if (normalized === 'healthy' || normalized === 'recovered') return 'success';
        if (normalized === 'warming_up' || normalized === 'watching' || normalized === 'recovering') return 'info';
        if (normalized === 'suspected_stale') return 'warning';
        if (normalized === 'connection_unready' || normalized === 'not_running') return 'secondary';
        return 'secondary';
    }

    if (normalized === 'success' || normalized === 'recovered') return 'success';
    if (normalized === 'started' || normalized === 'connecting' || normalized === 'reconnecting') return 'info';
    if (normalized.includes('failed') || normalized.includes('exception') || normalized.includes('error')) return 'danger';
    if (normalized.includes('skipped') || normalized.includes('retry') || normalized.includes('restarted')) return 'warning';
    return 'secondary';
}

function buildAboutStatusBadge(type, value) {
    const text = getAboutStatusText(type, value);
    const variant = getAboutStatusVariant(type, value);
    return `<span class="about-status-badge is-${variant}">${escapeHtml(text)}</span>`;
}

function escapeHtmlAttribute(text) {
    return escapeHtml(text).replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function buildAboutMonitoringNotice(runtimeStatus) {
    const modeText = runtimeStatus?.monitoring_mode === 'local_snapshot'
        ? '本地状态刷新，不触发闲鱼探活'
        : '运行态快照';
    const safetyText = runtimeStatus?.external_probe_performed === false
        ? '未执行外部探测'
        : '未知探测状态';
    return `
        <div class="account-diagnostics-status-note-bar is-info">
            <div class="account-diagnostics-status-note-title">${escapeHtml(modeText)}</div>
            <div class="account-diagnostics-status-note-text">${escapeHtml(safetyText)} · ${escapeHtml(runtimeStatus?.monitoring_description || '仅读取服务本地内存和数据库状态')}</div>
        </div>
    `;
}

function buildAboutRiskControlNotice(runtimeStatus) {
    if (!runtimeStatus?.risk_control_summary && !runtimeStatus?.risk_control_detail) {
        return '';
    }
    const tone = runtimeStatus?.operator_action_required ? 'warning' : 'info';
    const title = runtimeStatus?.operator_action_required ? '需要人工处理账号验证' : '最近刷新/风控状态';
    const detail = [runtimeStatus.risk_control_summary, runtimeStatus.risk_control_detail]
        .filter(Boolean)
        .join(' · ');
    return `
        <div class="account-diagnostics-status-note-bar is-${tone}">
            <div class="account-diagnostics-status-note-title">${escapeHtml(title)}</div>
            <div class="account-diagnostics-status-note-text">${escapeHtml(detail)}</div>
        </div>
    `;
}

function getAccountRuntimeBadge(runtimeStatus) {
    const status = runtimeStatus || {};
    const tokenStatus = String(status.risk_control_status || status.token_refresh_status || '').trim();
    const connectionState = String(status.connection_state || '').trim();
    if (status.operator_action_required || tokenStatus === 'captcha_max_retries_exceeded' || tokenStatus === 'password_login_backoff_wait') {
        return {
            label: '风控中',
            className: 'bg-warning text-dark',
            title: status.risk_control_summary || status.risk_control_detail || tokenStatus,
        };
    }
    if (connectionState === 'connecting' || connectionState === 'reconnecting') {
        return {
            label: '重连中',
            className: 'bg-info text-dark',
            title: status.message_stream_note || status.connection_state,
        };
    }
    if (status.running && status.ws_ready && status.session_ready && status.message_stream_ready) {
        return {
            label: '运行中',
            className: 'bg-success',
            title: status.message_stream_note || '本地运行态显示链路就绪',
        };
    }
    if (status.running) {
        return {
            label: '恢复中',
            className: 'bg-secondary',
            title: status.message_stream_note || status.connection_state || '本地运行态尚未完全就绪',
        };
    }
    return {
        label: '未运行',
        className: 'bg-secondary',
        title: '本地没有运行中的账号实例',
    };
}

function renderAccountRuntimeBadge(runtimeStatus) {
    const badge = getAccountRuntimeBadge(runtimeStatus);
    return `<span class="badge ${badge.className}" title="${escapeHtmlAttribute(badge.title || '')}">${escapeHtml(badge.label)}</span>`;
}

function buildAboutMetaCard({ label, value, supporting = '' }) {
    return `
        <div class="account-diagnostics-summary-item">
            <div class="account-diagnostics-summary-label">${escapeHtml(label)}</div>
            <div class="account-diagnostics-summary-value">${escapeHtml(value)}</div>
            ${supporting ? `<div class="account-diagnostics-summary-support">${escapeHtml(supporting)}</div>` : ''}
        </div>
    `;
}

function buildAboutRuntimeStatusItem({ label, value, note = '', tone = '', richValue = false, accent = '', icon = '' }) {
    return `
        <div class="account-diagnostics-status-item ${tone ? `is-${tone}` : ''} ${accent ? `is-${accent}` : ''}">
            <div class="account-diagnostics-status-item-head">
                <div class="account-diagnostics-status-item-icon">
                    ${icon ? `<i class="bi bi-${icon}"></i>` : ''}
                </div>
                <div class="account-diagnostics-status-item-label">${escapeHtml(label)}</div>
            </div>
            <div class="account-diagnostics-status-item-value">${richValue ? value : escapeHtml(value)}</div>
            ${note ? `<div class="account-diagnostics-status-item-note">${escapeHtml(note)}</div>` : ''}
        </div>
    `;
}

function buildAboutRuntimeMetaItem(label, value) {
    return `
        <div class="account-diagnostics-status-meta-item">
            <span class="account-diagnostics-status-meta-label">${escapeHtml(label)}</span>
            <span class="account-diagnostics-status-meta-value">${escapeHtml(value)}</span>
        </div>
    `;
}

function buildAboutReadinessValue(items) {
    const normalizedItems = Array.isArray(items) ? items : [];
    const totalCount = normalizedItems.length;
    const readyCount = normalizedItems.filter(item => item.ready).length;
    const progressPercent = totalCount
        ? Math.max(0, Math.min(100, Math.round((readyCount / totalCount) * 100)))
        : 0;
    const pendingLabels = normalizedItems
        .filter(item => !item.ready)
        .map(item => item.label);

    let summaryNote = '暂无链路状态';
    if (totalCount > 0 && pendingLabels.length === 0) {
        summaryNote = '四条关键链路均已就绪';
    } else if (totalCount > 0 && pendingLabels.length === totalCount) {
        summaryNote = '四条关键链路均未就绪';
    } else if (pendingLabels.length > 0) {
        summaryNote = `待处理：${pendingLabels.join(' / ')}`;
    }

    return `
        <div class="account-diagnostics-readiness-summary">
            <div class="account-diagnostics-readiness-hero">
                <div class="account-diagnostics-readiness-ratio">
                    <span class="account-diagnostics-readiness-ratio-current">${readyCount}</span>
                    <span class="account-diagnostics-readiness-ratio-total">/ ${totalCount}</span>
                </div>
                <div class="account-diagnostics-readiness-caption">关键链路已就绪</div>
            </div>
            <div class="account-diagnostics-readiness-progress" aria-hidden="true">
                <span class="account-diagnostics-readiness-progress-bar" style="width: ${progressPercent}%"></span>
            </div>
            <div class="account-diagnostics-readiness-percent">${progressPercent}% 就绪</div>
            <div class="account-diagnostics-readiness-list">
                ${normalizedItems.map(item => `
                <span class="account-diagnostics-readiness-chip ${item.ready ? 'is-ready' : 'is-pending'}">
                    <span class="account-diagnostics-readiness-name-wrap">
                        <span class="account-diagnostics-readiness-dot"></span>
                        <span class="account-diagnostics-readiness-name">${escapeHtml(item.label)}</span>
                    </span>
                    <span class="account-diagnostics-readiness-state">${item.ready ? '已就绪' : '未就绪'}</span>
                </span>
                `).join('')}
            </div>
            <div class="account-diagnostics-readiness-summary-note">${escapeHtml(summaryNote)}</div>
        </div>
    `;
}

function renderAboutAccountMeta(account) {
    const { accountMeta } = getAboutDiagnosticsElements();
    if (!accountMeta) return;

    if (!account) {
        accountMeta.innerHTML = '';
        return;
    }

    const metaParts = [
        buildAboutMetaCard({
            label: '账号 ID',
            value: account.id,
        }),
        buildAboutMetaCard({
            label: '登录名',
            value: account.username || '未设置用户名',
            supporting: account.username ? '用于账号识别与后续 Cookie 刷新' : '建议补充用户名，便于后续维护',
        }),
        buildAboutMetaCard({
            label: '备注',
            value: account.remark || '未设置备注',
            supporting: account.remark ? '' : '可在账号管理中补充备注',
        }),
    ];

    accountMeta.innerHTML = metaParts.join('');
}

function renderAboutDiagnosticsPlaceholder(container, icon, title, subtitle) {
    if (!container) return;

    container.innerHTML = `
        <div class="about-placeholder">
            <i class="bi bi-${icon}"></i>
            <div>
                <div class="about-placeholder-title">${escapeHtml(title)}</div>
                <div class="about-placeholder-sub">${escapeHtml(subtitle)}</div>
            </div>
        </div>
    `;
}

function renderAboutRuntimePlaceholder(title, subtitle) {
    const { statusContainer } = getAboutDiagnosticsElements();
    renderAboutDiagnosticsPlaceholder(statusContainer, 'hdd-network', title, subtitle);
}

function renderAboutHistoryPlaceholder(title, subtitle) {
    const { historyContainer } = getAboutDiagnosticsElements();
    renderAboutDiagnosticsPlaceholder(historyContainer, 'clock-history', title, subtitle);
}

function getAboutRuntimeOverview(runtimeStatus, readinessCount = 0) {
    if (!runtimeStatus?.running) {
        return {
            tone: 'danger',
            title: '实例未启动',
            note: '轻保活和历史消息查询都依赖账号实例，当前应先启动实例。',
        };
    }

    if (runtimeStatus?.connection_state === 'connecting' || runtimeStatus?.connection_state === 'reconnecting') {
        return {
            tone: 'info',
            title: '连接正在恢复',
            note: '主链路还在波动，先观察连接状态与最近消息时间是否继续推进。',
        };
    }

    if (!runtimeStatus?.ws_ready || !runtimeStatus?.session_ready || !runtimeStatus?.has_current_token || !runtimeStatus?.message_stream_ready) {
        return {
            tone: 'warning',
            title: `${readinessCount} / 4 关键链路已就绪`,
            note: '链路部分可用，优先处理未就绪项，再观察保活与消息链路。',
        };
    }

    return {
        tone: 'success',
        title: '链路稳定可用',
        note: '连接、轻保活、Token 与业务消息流四条主信号都处于正常状态。',
    };
}

function renderAboutRuntimeStatus(runtimeStatus) {
    const { statusContainer } = getAboutDiagnosticsElements();
    if (!statusContainer) return;

    if (!runtimeStatus) {
        renderAboutRuntimePlaceholder('暂无运行态', '当前账号还没有可用的运行态信息。');
        return;
    }

    const lastConnectionDisplay = formatAboutRuntimeTime(
        runtimeStatus.last_successful_connection_at_display,
        runtimeStatus.last_successful_connection_at
    );
    const keepaliveDisplay = formatAboutRuntimeTime(
        runtimeStatus.session_keepalive_at_display,
        runtimeStatus.session_keepalive_at
    );
    const tokenRefreshDisplay = formatAboutRuntimeTime(
        runtimeStatus.token_last_refreshed_at_display,
        runtimeStatus.token_last_refreshed_at
    );
    const lastMessageDisplay = formatAboutRuntimeTime(
        runtimeStatus.last_message_received_at_display,
        runtimeStatus.last_message_received_at
    );
    const stateChangedDisplay = formatAboutRuntimeTime(
        runtimeStatus.state_last_changed_at_display,
        runtimeStatus.state_last_changed_at
    );
    const messageStreamDisplay = getMessageStreamRuntimeDisplay(runtimeStatus);
    const messageStreamStatus = messageStreamDisplay.status;
    const readinessItems = [
        { label: '实例', ready: !!runtimeStatus.running },
        { label: 'WS', ready: !!runtimeStatus.ws_ready },
        { label: 'Session', ready: !!runtimeStatus.session_ready },
        { label: 'Token', ready: !!runtimeStatus.has_current_token },
        { label: '业务流', ready: !!runtimeStatus.message_stream_ready },
    ];
    const readinessSignalItems = readinessItems.slice(1);
    const readinessSignalCount = readinessSignalItems.filter(item => item.ready).length;
    const overview = getAboutRuntimeOverview(runtimeStatus, readinessSignalCount);
    const connectionTone = getAboutStatusVariant('connection', runtimeStatus.connection_state);
    const keepaliveDisplayStatus = runtimeStatus.session_keepalive_display_status || runtimeStatus.session_keepalive_status;
    const keepaliveTone = getAboutStatusVariant('keepalive', keepaliveDisplayStatus);
    const tokenTone = getAboutStatusVariant('token', runtimeStatus.token_refresh_status);
    const messageStreamTone = getAboutStatusVariant('stream', messageStreamStatus);
    const readinessTone = readinessSignalItems.every(item => item.ready)
        ? 'success'
        : readinessSignalItems.some(item => item.ready)
            ? 'warning'
            : 'danger';

    statusContainer.innerHTML = `
        <div class="account-diagnostics-status-shell">
            <div class="account-diagnostics-status-note-bar is-${overview.tone}">
                <div class="account-diagnostics-status-note-title">${escapeHtml(overview.title)}</div>
                <div class="account-diagnostics-status-note-text">${escapeHtml(overview.note)}</div>
            </div>
            ${buildAboutMonitoringNotice(runtimeStatus)}
            ${buildAboutRiskControlNotice(runtimeStatus)}
            <div class="account-diagnostics-status-body">
                <div class="account-diagnostics-status-primary">
                    <div class="account-diagnostics-status-grid">
                        ${buildAboutRuntimeStatusItem({
                            label: '连接状态',
                            value: buildAboutStatusBadge('connection', runtimeStatus.connection_state),
                            note: `最近连接成功：${lastConnectionDisplay}`,
                            tone: connectionTone,
                            richValue: true,
                            accent: 'connection',
                            icon: 'hdd-network',
                        })}
                        ${buildAboutRuntimeStatusItem({
                            label: '轻保活状态',
                            value: buildAboutStatusBadge('keepalive', keepaliveDisplayStatus),
                            note: runtimeStatus.session_keepalive_display_note
                                ? `最近执行：${keepaliveDisplay} · ${runtimeStatus.session_keepalive_display_note}`
                                : `最近执行：${keepaliveDisplay}`,
                            tone: keepaliveTone,
                            richValue: true,
                            accent: 'keepalive',
                            icon: 'heart-pulse',
                        })}
                        ${buildAboutRuntimeStatusItem({
                            label: 'Token 刷新状态',
                            value: buildAboutStatusBadge('token', runtimeStatus.token_refresh_status),
                            note: `最近刷新：${tokenRefreshDisplay}`,
                            tone: tokenTone,
                            richValue: true,
                            accent: 'token',
                            icon: 'key',
                        })}
                        ${buildAboutRuntimeStatusItem({
                            label: '业务消息流',
                            value: buildAboutStatusBadge('stream', messageStreamStatus),
                            note: messageStreamDisplay.note,
                            tone: messageStreamTone,
                            richValue: true,
                            accent: 'readiness',
                            icon: 'broadcast-pin',
                        })}
                    </div>
                </div>
                <div class="account-diagnostics-status-sidebar">
                    ${buildAboutRuntimeStatusItem({
                        label: '链路就绪情况',
                        value: buildAboutReadinessValue(readinessSignalItems),
                        tone: readinessTone,
                        richValue: true,
                        accent: 'readiness',
                        icon: 'diagram-3',
                    })}
                </div>
            </div>
            <div class="account-diagnostics-status-meta">
                ${buildAboutRuntimeMetaItem('最近收到消息', lastMessageDisplay)}
                ${buildAboutRuntimeMetaItem('状态变化时间', stateChangedDisplay)}
            </div>
        </div>
    `;
}

function getAboutHistoryMessageText(message) {
    if (message == null) {
        return '空消息';
    }

    if (typeof message === 'string') {
        return message;
    }

    if (typeof message?.text?.text === 'string' && message.text.text.trim()) {
        return message.text.text;
    }

    if (typeof message?.raw === 'string' && message.raw.trim()) {
        return message.raw;
    }

    try {
        return JSON.stringify(message, null, 2);
    } catch (error) {
        return String(message);
    }
}

function getAboutHistorySenderInitial(senderName) {
    const normalized = String(senderName || '').trim();
    if (!normalized) {
        return 'U';
    }
    return normalized.charAt(0).toUpperCase();
}

function renderAboutConversationHistory(messages, meta = {}) {
    const { historyContainer } = getAboutDiagnosticsElements();
    if (!historyContainer) return;

    if (!Array.isArray(messages) || messages.length === 0) {
        renderAboutHistoryPlaceholder('未查询到历史消息', '确认会话 ID 是否正确，以及该账号实例是否正在运行。');
        return;
    }

    const summaryText = `共查询到 ${messages.length} 条消息`;
    const conversationIdText = meta.conversationId ? `会话 ID: ${meta.conversationId}` : '';

    historyContainer.innerHTML = `
        <div class="about-history-summary">
            <span class="about-history-summary-main">${escapeHtml(summaryText)}</span>
            ${conversationIdText ? `<span class="about-history-summary-meta">${escapeHtml(conversationIdText)}</span>` : ''}
        </div>
        <div class="about-history-items">
            ${messages.map((item, index) => {
                const senderName = item?.send_user_name || '未知用户';
                const senderId = item?.send_user_id || '-';
                const senderInitial = getAboutHistorySenderInitial(senderName);
                const messageText = getAboutHistoryMessageText(item?.message);
                const rawText = typeof item?.message === 'object'
                    ? (() => {
                        try {
                            return JSON.stringify(item.message, null, 2);
                        } catch (error) {
                            return messageText;
                        }
                    })()
                    : messageText;

                return `
                    <div class="about-history-item">
                        <div class="about-history-item-header">
                            <div class="about-history-sender-block">
                                <div class="about-history-sender-row">
                                    <span class="about-history-sender-avatar">${escapeHtml(senderInitial)}</span>
                                    <div class="about-history-sender-meta">
                                        <div class="about-history-sender">${escapeHtml(senderName)}</div>
                                        <div class="about-history-sender-id">发送者 ID: ${escapeHtml(senderId)}</div>
                                    </div>
                                </div>
                            </div>
                            <div class="about-history-index">第 ${index + 1} 条</div>
                        </div>
                        <div class="about-history-message-shell">
                            <div class="about-history-message">${escapeHtml(messageText)}</div>
                        </div>
                        ${rawText !== messageText ? `
                            <details class="about-history-raw">
                                <summary>查看原始内容</summary>
                                <pre>${escapeHtml(rawText)}</pre>
                            </details>
                        ` : ''}
                    </div>
                `;
            }).join('')}
        </div>
    `;
}

function populateAboutAccountOptions(accounts) {
    const { accountSelect } = getAboutDiagnosticsElements();
    if (!accountSelect) return;

    if (!Array.isArray(accounts) || accounts.length === 0) {
        accountSelect.innerHTML = '<option value="">暂无账号</option>';
        accountSelect.disabled = true;
        return;
    }

    accountSelect.disabled = false;
    accountSelect.innerHTML = `
        <option value="">请选择账号</option>
        ${accounts.map(account => {
            const runtimeLabel = getAccountRuntimeBadge(account.runtime_status).label;
            return `<option value="${escapeHtml(account.id)}">${escapeHtml(`${account.id} · ${runtimeLabel}`)}</option>`;
        }).join('')}
    `;
}

async function loadAboutRuntimeStatus(accountId = '') {
    const normalizedAccountId = String(accountId || getAboutSelectedAccountId()).trim();
    if (!normalizedAccountId) {
        renderAboutAccountMeta(null);
        renderAboutRuntimePlaceholder('请选择账号', '选择账号后会显示当前连接状态、轻保活结果和最近活动时间。');
        return;
    }

    const selectedAccount = aboutDiagnosticsAccounts.find(account => account.id === normalizedAccountId) || null;
    renderAboutAccountMeta(selectedAccount);
    renderAboutRuntimeStatus(selectedAccount?.runtime_status || null);

    try {
        const result = await fetchJSON(`${apiBase}/cookies/${encodeURIComponent(normalizedAccountId)}/runtime-status`);
        const runtimeStatus = result?.runtime_status || null;
        const targetAccount = aboutDiagnosticsAccounts.find(account => account.id === normalizedAccountId);
        if (targetAccount) {
            targetAccount.runtime_status = runtimeStatus;
            renderAboutAccountMeta(targetAccount);
        }
        renderAboutRuntimeStatus(runtimeStatus);
        scheduleAboutRuntimeAutoRetry(normalizedAccountId, runtimeStatus);
    } catch (error) {
        console.error('加载账号运行态失败:', error);
    }
}

async function loadAboutDiagnostics() {
    initAboutDiagnosticsEvents();

    try {
        const previousAccountId = getAboutSelectedAccountId();
        const accounts = await fetchJSON(`${apiBase}/cookies/details`);
        aboutDiagnosticsAccounts = Array.isArray(accounts) ? accounts : [];
        populateAboutAccountOptions(aboutDiagnosticsAccounts);

        const { accountSelect } = getAboutDiagnosticsElements();
        if (!accountSelect || aboutDiagnosticsAccounts.length === 0) {
            renderAboutAccountMeta(null);
            renderAboutRuntimePlaceholder('暂无账号', '请先在账号管理中添加闲鱼账号。');
            renderAboutHistoryPlaceholder('暂无历史消息', '请先添加账号并确保实例已启动。');
            return;
        }

        const nextAccountId = aboutDiagnosticsAccounts.some(account => account.id === previousAccountId)
            ? previousAccountId
            : (aboutDiagnosticsAccounts.find(account => account.runtime_status?.running)?.id || aboutDiagnosticsAccounts[0]?.id || '');

        accountSelect.value = nextAccountId;
        await loadAboutRuntimeStatus(nextAccountId);
    } catch (error) {
        console.error('加载账号保活诊断失败:', error);
    }
}

async function refreshAboutDiagnosticsStatus() {
    const { refreshButton } = getAboutDiagnosticsElements();
    const accountId = getAboutSelectedAccountId();
    if (!accountId) {
        showToast('请先选择账号', 'warning');
        return;
    }

    const originalHtml = refreshButton?.innerHTML;
    if (refreshButton) {
        refreshButton.disabled = true;
        refreshButton.innerHTML = '<i class="bi bi-arrow-repeat me-1"></i>刷新中...';
    }

    try {
        await loadAboutRuntimeStatus(accountId);
        showToast(`账号 "${accountId}" 运行态已刷新`, 'success');
    } finally {
        if (refreshButton) {
            refreshButton.disabled = false;
            refreshButton.innerHTML = originalHtml;
        }
    }
}

async function triggerAboutSessionKeepalive() {
    const { keepaliveButton } = getAboutDiagnosticsElements();
    const accountId = getAboutSelectedAccountId();
    if (!accountId) {
        showToast('请先选择账号', 'warning');
        return;
    }

    const originalHtml = keepaliveButton?.innerHTML;
    if (keepaliveButton) {
        keepaliveButton.disabled = true;
        keepaliveButton.innerHTML = '<i class="bi bi-lightning-charge-fill me-1"></i>执行中...';
    }

    try {
        const result = await fetchJSON(`${apiBase}/cookies/${encodeURIComponent(accountId)}/session-keepalive`, {
            method: 'POST',
        });
        const targetAccount = aboutDiagnosticsAccounts.find(account => account.id === accountId);
        if (targetAccount) {
            targetAccount.runtime_status = result?.runtime_status || null;
            renderAboutAccountMeta(targetAccount);
        }
        renderAboutRuntimeStatus(result?.runtime_status || null);
        showToast(result?.message || '轻保活已执行', result?.success ? 'success' : 'warning');
    } catch (error) {
        console.error('执行轻保活失败:', error);
    } finally {
        if (keepaliveButton) {
            keepaliveButton.disabled = false;
            keepaliveButton.innerHTML = originalHtml;
        }
    }
}

async function loadAboutConversationHistory() {
    const { historyButton, conversationInput } = getAboutDiagnosticsElements();
    const accountId = getAboutSelectedAccountId();
    const conversationId = conversationInput?.value?.trim() || '';

    if (!accountId) {
        showToast('请先选择账号', 'warning');
        return;
    }

    if (!conversationId) {
        showToast('请输入会话 ID', 'warning');
        return;
    }

    const originalHtml = historyButton?.innerHTML;
    if (historyButton) {
        historyButton.disabled = true;
        historyButton.innerHTML = '<i class="bi bi-chat-left-text-fill me-1"></i>查询中...';
    }

    renderAboutHistoryPlaceholder('正在查询历史消息', '请稍候，系统正在尝试拉取最近的会话消息。');

    try {
        const result = await fetchJSON(
            `${apiBase}/cookies/${encodeURIComponent(accountId)}/conversations/${encodeURIComponent(conversationId)}/history`
        );
        renderAboutConversationHistory(result?.messages || [], {
            conversationId: result?.conversation_id || conversationId,
        });
        showToast(`账号 "${accountId}" 历史消息查询完成`, 'success');
    } catch (error) {
        console.error('查询历史消息失败:', error);
        renderAboutHistoryPlaceholder('历史消息查询失败', error?.message || '请稍后重试。');
    } finally {
        if (historyButton) {
            historyButton.disabled = false;
            historyButton.innerHTML = originalHtml;
        }
    }
}

function initAboutDiagnosticsEvents() {
    if (aboutDiagnosticsInitialized) {
        return;
    }

    const {
        accountSelect,
        refreshButton,
        keepaliveButton,
        historyButton,
        conversationInput,
    } = getAboutDiagnosticsElements();

    accountSelect?.addEventListener('change', async () => {
        renderAboutHistoryPlaceholder('暂无历史消息', '切换账号后，请重新输入会话 ID 并查询历史消息。');
        await loadAboutRuntimeStatus(accountSelect.value);
    });

    refreshButton?.addEventListener('click', refreshAboutDiagnosticsStatus);
    keepaliveButton?.addEventListener('click', triggerAboutSessionKeepalive);
    historyButton?.addEventListener('click', loadAboutConversationHistory);
    conversationInput?.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') {
            event.preventDefault();
            loadAboutConversationHistory();
        }
    });
    document.addEventListener('visibilitychange', () => {
        if (aboutRuntimeRetryTimer) {
            clearTimeout(aboutRuntimeRetryTimer);
            aboutRuntimeRetryTimer = null;
        }
        if (document.visibilityState !== 'visible') {
            return;
        }
        if (!document.getElementById('accounts-section')?.classList.contains('active')) {
            return;
        }
        const accountId = getAboutSelectedAccountId();
        if (accountId) {
            loadAboutRuntimeStatus(accountId);
        }
    });

    aboutDiagnosticsInitialized = true;
}

// ================================
// 【账号管理菜单】相关功能
// ================================

// 加载Cookie列表
async function loadCookies() {
    try {
    toggleLoading(true);
    const tbody = document.querySelector('#cookieTable tbody');
    tbody.innerHTML = '';

    const cookieDetails = await fetchJSON(apiBase + '/cookies/details');

    if (cookieDetails.length === 0) {
        tbody.innerHTML = `
        <tr>
            <td colspan="11" class="text-center py-4 text-muted empty-state">
            <i class="bi bi-inbox fs-1 d-block mb-3"></i>
            <h5>暂无账号</h5>
            <p class="mb-0">请添加新的闲鱼账号开始使用</p>
            </td>
        </tr>
        `;
        return;
    }

    // 为每个账号获取关键词数量和默认回复设置并渲染
    const accountsWithKeywords = await Promise.all(
        cookieDetails.map(async (cookie) => {
        try {
            // 获取关键词数量
            const keywordsResponse = await fetch(`${apiBase}/keywords/${cookie.id}`, {
            headers: { 'Authorization': `Bearer ${authToken}` }
            });

            let keywordCount = 0;
            if (keywordsResponse.ok) {
            const keywordsData = await keywordsResponse.json();
            keywordCount = keywordsData.length;
            }

            // 获取默认回复设置
            const defaultReplyResponse = await fetch(`${apiBase}/default-replies/${cookie.id}`, {
            headers: { 'Authorization': `Bearer ${authToken}` }
            });

            let defaultReply = { enabled: false, reply_content: '' };
            if (defaultReplyResponse.ok) {
            defaultReply = await defaultReplyResponse.json();
            }

            // 获取AI回复设置
            const aiReplyResponse = await fetch(`${apiBase}/ai-reply-settings/${cookie.id}`, {
            headers: { 'Authorization': `Bearer ${authToken}` }
            });

            let aiReply = { ai_enabled: false, model_name: 'qwen-plus' };
            if (aiReplyResponse.ok) {
            aiReply = await aiReplyResponse.json();
            }

            return {
            ...cookie,
            keywordCount: keywordCount,
            defaultReply: defaultReply,
            aiReply: aiReply
            };
        } catch (error) {
            return {
            ...cookie,
            keywordCount: 0,
            defaultReply: { enabled: false, reply_content: '' },
            aiReply: { ai_enabled: false, model_name: 'qwen-plus' }
            };
        }
        })
    );

    accountsWithKeywords.forEach(cookie => {
        // 使用数据库中的实际状态，默认为启用
        const isEnabled = cookie.enabled === undefined ? true : cookie.enabled;
        const statusNoteBadge = renderStatusNoteBadge(cookie.status_note, 'account-status-note-badge');
        const runtimeBadge = renderAccountRuntimeBadge(cookie.runtime_status);

        console.log(`账号 ${cookie.id} 状态: enabled=${cookie.enabled}, isEnabled=${isEnabled}`); // 调试信息

        const tr = document.createElement('tr');
        tr.className = `account-row ${isEnabled ? 'enabled' : 'disabled'}`;
        tr.dataset.accountId = cookie.id;
        // 默认回复状态标签
        const defaultReplyBadge = cookie.defaultReply.enabled ?
        '<span class="badge bg-success">启用</span>' :
        '<span class="badge bg-secondary">禁用</span>';

        // AI回复状态标签
        const aiReplyBadge = cookie.aiReply.ai_enabled ?
        '<span class="badge bg-primary">AI启用</span>' :
        '<span class="badge bg-secondary">AI禁用</span>';

        // 自动确认发货状态（默认开启）
        const autoConfirm = cookie.auto_confirm === undefined ? true : cookie.auto_confirm;
        
        // 自动好评状态（默认关闭）
        const autoComment = cookie.auto_comment === undefined ? false : cookie.auto_comment;

        tr.innerHTML = `
        <td class="align-middle">
            <div class="cookie-id">
            <strong class="text-primary">${cookie.id}</strong>
            </div>
        </td>
        <td class="align-middle">
            <div class="cookie-value" title="点击复制Cookie" style="font-family: monospace; font-size: 0.875rem; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">
            ${cookie.value || '未设置'}
            </div>
        </td>
        <td class="align-middle">
            <span class="badge ${cookie.keywordCount > 0 ? 'bg-success' : 'bg-secondary'}">
            ${cookie.keywordCount} 个关键词
            </span>
        </td>
        <td class="align-middle">
            <div class="d-flex align-items-center gap-2 flex-wrap account-status-cell">
            <label class="status-toggle" title="${isEnabled ? '点击禁用' : '点击启用'}">
                <input type="checkbox" ${isEnabled ? 'checked' : ''} onchange="toggleAccountStatus('${cookie.id}', this.checked)">
                <span class="status-slider"></span>
            </label>
            <span class="status-badge ${isEnabled ? 'enabled' : 'disabled'}" title="${isEnabled ? '账号已启用' : '账号已禁用'}">
                <i class="bi bi-${isEnabled ? 'check-circle-fill' : 'x-circle-fill'}"></i>
            </span>
            ${runtimeBadge}
            ${statusNoteBadge}
            </div>
        </td>
        <td class="align-middle">
            ${defaultReplyBadge}
        </td>
        <td class="align-middle">
            ${aiReplyBadge}
        </td>
        <td class="align-middle">
            <div class="d-flex align-items-center gap-2">
            <label class="status-toggle" title="${autoConfirm ? '点击关闭自动确认发货' : '点击开启自动确认发货'}">
                <input type="checkbox" ${autoConfirm ? 'checked' : ''} onchange="toggleAutoConfirm('${cookie.id}', this.checked)">
                <span class="status-slider"></span>
            </label>
            <span class="status-badge ${autoConfirm ? 'enabled' : 'disabled'}" title="${autoConfirm ? '自动确认发货已开启' : '自动确认发货已关闭'}">
                <i class="bi bi-${autoConfirm ? 'truck' : 'truck-flatbed'}"></i>
            </span>
            </div>
        </td>
        <td class="align-middle">
            <div class="d-flex align-items-center gap-2">
            <label class="status-toggle" title="${autoComment ? '点击关闭自动好评' : '点击开启自动好评'}">
                <input type="checkbox" ${autoComment ? 'checked' : ''} onchange="toggleAutoComment('${cookie.id}', this.checked)">
                <span class="status-slider"></span>
            </label>
            <span class="status-badge ${autoComment ? 'enabled' : 'disabled'}" title="${autoComment ? '自动好评已开启' : '自动好评已关闭'}">
                <i class="bi bi-${autoComment ? 'star-fill' : 'star'}"></i>
            </span>
            <button class="btn btn-sm btn-outline-warning ms-1" onclick="showCommentTemplates('${cookie.id}')" title="管理好评模板">
                <i class="bi bi-card-text"></i>
            </button>
            </div>
        </td>
        <td class="align-middle">
            <div class="remark-cell" data-cookie-id="${cookie.id}">
                <span class="remark-display" onclick="editRemark('${cookie.id}', '${(cookie.remark || '').replace(/'/g, '&#39;')}')" title="点击编辑备注" style="cursor: pointer; color: #6c757d; font-size: 0.875rem;">
                    ${cookie.remark || '<i class="bi bi-plus-circle text-muted"></i> 添加备注'}
                </span>
            </div>
        </td>
        <td class="align-middle">
            <div class="pause-duration-cell" data-cookie-id="${cookie.id}">
                <span class="pause-duration-display" onclick="editPauseDuration('${cookie.id}', ${cookie.pause_duration !== undefined ? cookie.pause_duration : 10})" title="点击编辑暂停时间" style="cursor: pointer; color: #6c757d; font-size: 0.875rem;">
                    <i class="bi bi-clock me-1"></i>${cookie.pause_duration === 0 ? '不暂停' : (cookie.pause_duration || 10) + '分钟'}
                </span>
            </div>
        </td>
        <td class="align-middle">
            <div class="btn-group" role="group">
            <button class="btn btn-sm btn-outline-secondary" onclick="showFaceVerification('${cookie.id}')" title="验证截图">
                <i class="bi bi-shield-check"></i>
            </button>
            <button class="btn btn-sm btn-outline-primary" onclick="editCookieInline('${cookie.id}', '${cookie.value}')" title="修改Cookie" ${!isEnabled ? 'disabled' : ''}>
                <i class="bi bi-pencil"></i>
            </button>
            <button class="btn btn-sm btn-outline-success" onclick="goToAutoReply('${cookie.id}')" title="${isEnabled ? '设置自动回复' : '配置关键词 (账号已禁用)'}">
                <i class="bi bi-arrow-right-circle"></i>
            </button>
            <button class="btn btn-sm btn-outline-warning" onclick="configAIReply('${cookie.id}')" title="配置AI回复" ${!isEnabled ? 'disabled' : ''}>
                <i class="bi bi-robot"></i>
            </button>
            <button class="btn btn-sm btn-outline-secondary" onclick="polishAccountItems('${cookie.id}')" title="一键擦亮" ${!isEnabled ? 'disabled' : ''}>
                <i class="bi bi-stars"></i>
            </button>
            <button class="btn btn-sm btn-outline-info" onclick="openPolishScheduleModal('${cookie.id}')" title="定时擦亮" ${!isEnabled ? 'disabled' : ''}>
                <i class="bi bi-clock"></i>
            </button>

            <button class="btn btn-sm btn-outline-danger" onclick="delCookie('${cookie.id}')" title="删除账号">
                <i class="bi bi-trash"></i>
            </button>
            </div>
        </td>
        `;
        tbody.appendChild(tr);
    });

    // 为Cookie值添加点击复制功能
    document.querySelectorAll('.cookie-value').forEach(element => {
        element.style.cursor = 'pointer';
        element.addEventListener('click', function() {
        const row = this.closest('tr');
        const cookieId = row?.querySelector('.cookie-id strong')?.textContent;
        if (cookieId) {
            copyCookie(cookieId);
        }
        });
    });

    // 重新初始化工具提示
    initTooltips();
    focusPendingAccountManagementRow();

    } catch (err) {
    // 错误已在fetchJSON中处理
    } finally {
    toggleLoading(false);
    if (document.getElementById('accounts-section')?.classList.contains('active')) {
        loadAboutDiagnostics();
    }
    }
}

// 复制Cookie
async function copyCookie(id) {
    try {
    const details = await fetchJSON(`${apiBase}/cookie/${encodeURIComponent(id)}/details?include_secrets=true`);
    const value = details?.value || '';

    if (!value || value === '未设置') {
        showToast('该账号暂无Cookie值', 'warning');
        return;
    }

    navigator.clipboard.writeText(value).then(() => {
        showToast(`账号 "${id}" 的Cookie已复制到剪贴板`, 'success');
    }).catch(() => {
        const textArea = document.createElement('textarea');
        textArea.value = value;
        document.body.appendChild(textArea);
        textArea.select();
        try {
            document.execCommand('copy');
            showToast(`账号 "${id}" 的Cookie已复制到剪贴板`, 'success');
        } catch (err) {
            showToast('复制失败，请手动复制', 'error');
        }
        document.body.removeChild(textArea);
    });
    } catch (error) {
    console.error('获取Cookie详情失败:', error);
    showToast('获取Cookie详情失败，请稍后重试', 'danger');
    }
}

// 一键擦亮
async function polishAccountItems(accountId) {
    toggleLoading(true);
    showToast('正在擦亮所有商品，请稍候...', 'info');
    try {
        const response = await fetch(`${apiBase}/accounts/${encodeURIComponent(accountId)}/polish-items`, {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${authToken}` }
        });
        const data = await response.json();
        if (data.success) {
            showToast(`擦亮完成: ${data.polished}/${data.total} 个商品成功`, 'success');
        } else {
            showToast(`擦亮失败: ${data.message}`, 'danger');
        }
    } catch (error) {
        showToast(`擦亮请求异常: ${error.message}`, 'danger');
    } finally {
        toggleLoading(false);
    }
}

// 刷新真实Cookie
async function refreshRealCookie(cookieId) {
    if (!cookieId) {
        showToast('缺少账号ID', 'warning');
        return;
    }

    // 获取当前cookie值
    try {
        const currentCookie = await fetchJSON(`${apiBase}/cookie/${encodeURIComponent(cookieId)}/details?include_secrets=true`);

        if (!currentCookie || !currentCookie.value) {
            showToast('未找到有效的Cookie信息', 'warning');
            return;
        }

        // 确认操作
        if (!confirm(`确定要刷新账号 "${cookieId}" 的真实Cookie吗？\n\n此操作将使用当前Cookie访问闲鱼IM界面获取最新的真实Cookie。`)) {
            return;
        }

        // 显示加载状态
        const button = event.target.closest('button');
        const originalContent = button.innerHTML;
        button.disabled = true;
        button.innerHTML = '<i class="bi bi-arrow-clockwise spin"></i>';

        // 调用刷新API
        const response = await fetch(`${apiBase}/qr-login/refresh-cookies`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                qr_cookies: currentCookie.value,
                cookie_id: cookieId
            })
        });

        const result = await response.json();

        if (result.success) {
            showToast(`账号 "${cookieId}" 真实Cookie刷新成功`, 'success');
            // 刷新账号列表以显示更新后的cookie
            loadCookies();
        } else {
            showToast(`真实Cookie刷新失败: ${result.message}`, 'danger');
        }

    } catch (error) {
        console.error('刷新真实Cookie失败:', error);
        showToast(`刷新真实Cookie失败: ${error.message || '未知错误'}`, 'danger');
    } finally {
        // 恢复按钮状态
        const button = event.target.closest('button');
        if (button) {
            button.disabled = false;
            button.innerHTML = '<i class="bi bi-arrow-clockwise"></i>';
        }
    }
}

// 显示冷却状态
async function showCooldownStatus(cookieId) {
    if (!cookieId) {
        showToast('缺少账号ID', 'warning');
        return;
    }

    try {
        const response = await fetch(`${apiBase}/qr-login/cooldown-status/${cookieId}`, {
            method: 'GET',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            }
        });

        const result = await response.json();

        if (result.success) {
            const { remaining_time, cooldown_duration, is_in_cooldown, remaining_minutes, remaining_seconds } = result;

            let statusMessage = `账号: ${cookieId}\n`;
            statusMessage += `冷却时长: ${cooldown_duration / 60}分钟\n`;

            if (is_in_cooldown) {
                statusMessage += `冷却状态: 进行中\n`;
                statusMessage += `剩余时间: ${remaining_minutes}分${remaining_seconds}秒\n\n`;
                statusMessage += `在冷却期间，_refresh_cookies_via_browser 方法将被跳过。\n\n`;
                statusMessage += `是否要重置冷却时间？`;

                if (confirm(statusMessage)) {
                    await resetCooldownTime(cookieId);
                }
            } else {
                statusMessage += `冷却状态: 无冷却\n`;
                statusMessage += `可以正常执行 _refresh_cookies_via_browser 方法`;
                alert(statusMessage);
            }
        } else {
            showToast(`获取冷却状态失败: ${result.message}`, 'danger');
        }

    } catch (error) {
        console.error('获取冷却状态失败:', error);
        showToast(`获取冷却状态失败: ${error.message || '未知错误'}`, 'danger');
    }
}

// 重置冷却时间
async function resetCooldownTime(cookieId) {
    if (!cookieId) {
        showToast('缺少账号ID', 'warning');
        return;
    }

    try {
        const response = await fetch(`${apiBase}/qr-login/reset-cooldown/${cookieId}`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            }
        });

        const result = await response.json();

        if (result.success) {
            const previousTime = result.previous_remaining_time || 0;
            const previousMinutes = Math.floor(previousTime / 60);
            const previousSeconds = previousTime % 60;

            let message = `账号 "${cookieId}" 的扫码登录冷却时间已重置`;
            if (previousTime > 0) {
                message += `\n原剩余时间: ${previousMinutes}分${previousSeconds}秒`;
            }

            showToast(message, 'success');
        } else {
            showToast(`重置冷却时间失败: ${result.message}`, 'danger');
        }

    } catch (error) {
        console.error('重置冷却时间失败:', error);
        showToast(`重置冷却时间失败: ${error.message || '未知错误'}`, 'danger');
    }
}

// 删除Cookie
async function delCookie(id) {
    if (!confirm(`确定要删除账号 "${id}" 吗？此操作不可恢复。`)) return;

    try {
    await fetchJSON(apiBase + `/cookies/${id}`, { method: 'DELETE' });
    showToast(`账号 "${id}" 已删除`, 'success');
    loadCookies();
    } catch (err) {
    // 错误已在fetchJSON中处理
    }
}

// 内联编辑Cookie
async function editCookieInline(id, currentValue) {
    try {
        toggleLoading(true);
        
        // 获取账号详细信息
        const details = await fetchJSON(apiBase + `/cookie/${id}/details?include_secrets=true`);
        
        // 打开编辑模态框
        openAccountEditModal(details);
    } catch (err) {
        console.error('获取账号详情失败:', err);
        showToast(`获取账号详情失败: ${err.message || '未知错误'}`, 'danger');
    } finally {
        toggleLoading(false);
    }
}

// 打开账号编辑模态框
async function openAccountEditModal(accountData) {
    // 设置模态框数据
    document.getElementById('accountEditId').value = accountData.id;
    document.getElementById('editAccountCookie').value = accountData.value || '';
    document.getElementById('editAccountUsername').value = accountData.username || '';
    document.getElementById('editAccountPassword').value = accountData.password || '';
    document.getElementById('editAccountShowBrowser').checked = accountData.show_browser || false;
    
    // 显示账号ID
    document.getElementById('accountEditIdDisplay').textContent = accountData.id;
    
    // 加载代理配置
    try {
        const proxyData = await fetchJSON(apiBase + `/cookie/${accountData.id}/proxy?include_secret=true`);
        if (proxyData && proxyData.data) {
            document.getElementById('editProxyType').value = proxyData.data.proxy_type || 'none';
            document.getElementById('editProxyHost').value = proxyData.data.proxy_host || '';
            document.getElementById('editProxyPort').value = proxyData.data.proxy_port || '';
            document.getElementById('editProxyUser').value = proxyData.data.proxy_user || '';
            document.getElementById('editProxyPass').value = proxyData.data.proxy_pass || '';
        } else {
            // 设置默认值
            document.getElementById('editProxyType').value = 'none';
            document.getElementById('editProxyHost').value = '';
            document.getElementById('editProxyPort').value = '';
            document.getElementById('editProxyUser').value = '';
            document.getElementById('editProxyPass').value = '';
        }
        // 更新代理字段显示状态
        toggleProxyFields();
    } catch (err) {
        console.error('加载代理配置失败:', err);
        // 设置默认值
        document.getElementById('editProxyType').value = 'none';
        toggleProxyFields();
    }
    
    // 打开模态框
    const modal = new bootstrap.Modal(document.getElementById('accountEditModal'));
    modal.show();
    
    // 初始化模态框中的 tooltips
    setTimeout(() => {
        initTooltips();
    }, 100);
}

// 切换代理配置字段显示
function toggleProxyFields() {
    const proxyType = document.getElementById('editProxyType').value;
    const showProxy = proxyType !== 'none';
    
    document.getElementById('proxyHostGroup').style.display = showProxy ? 'block' : 'none';
    document.getElementById('proxyPortGroup').style.display = showProxy ? 'block' : 'none';
    document.getElementById('proxyAuthGroup').style.display = showProxy ? 'flex' : 'none';
}

// 保存账号编辑
async function saveAccountEdit() {
    const id = document.getElementById('accountEditId').value;
    const cookie = document.getElementById('editAccountCookie').value.trim();
    const username = document.getElementById('editAccountUsername').value.trim();
    const password = document.getElementById('editAccountPassword').value.trim();
    const showBrowser = document.getElementById('editAccountShowBrowser').checked;
    
    // 代理配置
    const proxyType = document.getElementById('editProxyType').value;
    const proxyHost = document.getElementById('editProxyHost').value.trim();
    const proxyPort = parseInt(document.getElementById('editProxyPort').value) || 0;
    const proxyUser = document.getElementById('editProxyUser').value.trim();
    const proxyPass = document.getElementById('editProxyPass').value.trim();
    
    if (!cookie) {
        showToast('Cookie值不能为空', 'warning');
        return;
    }
    
    // 如果选择了代理，验证必要字段
    if (proxyType !== 'none') {
        if (!proxyHost) {
            showToast('请输入代理服务器地址', 'warning');
            return;
        }
        if (!proxyPort || proxyPort <= 0) {
            showToast('请输入有效的代理端口', 'warning');
            return;
        }
    }
    
    try {
        toggleLoading(true);
        
        // 保存账号基本信息
        await fetchJSON(apiBase + `/cookie/${id}/account-info`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                value: cookie,
                username: username,
                password: password,
                show_browser: showBrowser
            })
        });
        
        // 保存代理配置
        await fetchJSON(apiBase + `/cookie/${id}/proxy`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                proxy_type: proxyType,
                proxy_host: proxyHost,
                proxy_port: proxyPort,
                proxy_user: proxyUser,
                proxy_pass: proxyPass
            })
        });
        
        showToast(`账号 "${id}" 信息已更新`, 'success');
        
        // 关闭模态框
        const modal = bootstrap.Modal.getInstance(document.getElementById('accountEditModal'));
        modal.hide();
        
        // 重新加载账号列表
        loadCookies();
    } catch (err) {
        console.error('保存账号信息失败:', err);
        showToast(`保存失败: ${err.message || '未知错误'}`, 'danger');
    } finally {
        toggleLoading(false);
    }
}

// 保存内联编辑的Cookie
async function saveCookieInline(id) {
    const input = document.getElementById(`edit-${id}`);
    const newValue = input.value.trim();

    if (!newValue) {
    showToast('Cookie值不能为空', 'warning');
    return;
    }

    try {
    toggleLoading(true);

    await fetchJSON(apiBase + `/cookies/${id}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
        id: id,
        value: newValue
        })
    });

    showToast(`账号 "${id}" Cookie已更新`, 'success');
    loadCookies(); // 重新加载列表

    } catch (err) {
    console.error('Cookie更新失败:', err);
    showToast(`Cookie更新失败: ${err.message || '未知错误'}`, 'danger');
    // 恢复原内容
    cancelCookieEdit(id);
    } finally {
    toggleLoading(false);
    }
}

// 取消Cookie编辑
function cancelCookieEdit(id) {
    if (!window.editingCookieData || window.editingCookieData.id !== id) {
    console.error('编辑数据不存在');
    return;
    }

    const row = document.querySelector(`#edit-${id}`).closest('tr');
    const cookieValueCell = row.querySelector('.cookie-value');

    // 恢复原内容
    cookieValueCell.innerHTML = window.editingCookieData.originalContent;

    // 恢复按钮状态
    const actionButtons = row.querySelectorAll('.btn-group button');
    actionButtons.forEach(btn => btn.disabled = false);

    // 清理全局数据
    delete window.editingCookieData;
}



// 切换账号启用/禁用状态
async function toggleAccountStatus(accountId, enabled) {
    try {
    toggleLoading(true);

    // 这里需要调用后端API来更新账号状态
    // 由于当前后端可能没有enabled字段，我们先在前端模拟
    // 实际项目中需要后端支持

    const response = await fetch(`${apiBase}/cookies/${accountId}/status`, {
        method: 'PUT',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({ enabled: enabled })
    });

    if (response.ok) {
        const result = await response.json();
        showToast(`账号 "${accountId}" 已${enabled ? '启用' : '禁用'}`, 'success');

        // 清除相关缓存，确保数据一致性
        clearKeywordCache();

        // 更新界面显示
        updateAccountRowStatus(accountId, enabled, result.status_note || '');

        // 刷新自动回复页面的账号列表
        refreshAccountList();
        if (dashboardData.accounts.length) {
            await refreshDashboardRuntimeSnapshots();
        }

        // 如果禁用的账号在自动回复页面被选中，更新显示
        const accountSelect = document.getElementById('accountSelect');
        if (accountSelect && accountSelect.value === accountId) {
        if (!enabled) {
            // 更新徽章显示禁用状态
            updateAccountBadge(accountId, false);
            showToast('账号已禁用，配置的关键词不会参与自动回复', 'warning');
        } else {
            // 更新徽章显示启用状态
            updateAccountBadge(accountId, true);
            showToast('账号已启用，配置的关键词将参与自动回复', 'success');
        }
        }

    } else {
        // 如果后端不支持，先在前端模拟
        console.warn('后端暂不支持账号状态切换，使用前端模拟');
        showToast(`账号 "${accountId}" 已${enabled ? '启用' : '禁用'} (前端模拟)`, enabled ? 'success' : 'warning');
        updateAccountRowStatus(accountId, enabled);
    }

    } catch (error) {
    console.error('切换账号状态失败:', error);

    // 后端不支持时的降级处理
    showToast(`账号 "${accountId}" 已${enabled ? '启用' : '禁用'} (本地模拟)`, enabled ? 'success' : 'warning');
    updateAccountRowStatus(accountId, enabled);

    // 恢复切换按钮状态
    const toggle = document.querySelector(`input[onchange*="${accountId}"]`);
    if (toggle) {
        toggle.checked = enabled;
    }
    } finally {
    toggleLoading(false);
    }
}

// 更新账号行的状态显示
function updateAccountRowStatus(accountId, enabled, statusNote = '') {
    const toggle = document.querySelector(`input[onchange*="${accountId}"]`);
    if (!toggle) return;

    const row = toggle.closest('tr');
    const statusBadge = row.querySelector('.status-badge');
    const statusCell = row.querySelector('.account-status-cell');
    const actionButtons = row.querySelectorAll('.btn-group .btn:not(.btn-outline-info):not(.btn-outline-danger)');

    // 更新行样式
    row.className = `account-row ${enabled ? 'enabled' : 'disabled'}`;

    // 更新状态徽章
    statusBadge.className = `status-badge ${enabled ? 'enabled' : 'disabled'}`;
    statusBadge.title = enabled ? '账号已启用' : '账号已禁用';
    statusBadge.innerHTML = `
    <i class="bi bi-${enabled ? 'check-circle-fill' : 'x-circle-fill'}"></i>
    `;

    const existingStatusNote = statusCell?.querySelector('.account-status-note-badge');
    const renderedStatusNote = renderStatusNoteBadge(statusNote, 'account-status-note-badge').trim();
    if (existingStatusNote) {
        existingStatusNote.remove();
    }
    if (statusCell && renderedStatusNote) {
        statusCell.insertAdjacentHTML('beforeend', renderedStatusNote);
    }

    // 更新按钮状态（只禁用编辑Cookie按钮，其他按钮保持可用）
    actionButtons.forEach(btn => {
    if (btn.onclick && btn.onclick.toString().includes('editCookieInline')) {
        btn.disabled = !enabled;
    }
    // 设置自动回复按钮始终可用，但更新提示文本
    if (btn.onclick && btn.onclick.toString().includes('goToAutoReply')) {
        btn.title = enabled ? '设置自动回复' : '配置关键词 (账号已禁用)';
    }
    });

    // 更新切换按钮的提示
    const label = toggle.closest('.status-toggle');
    label.title = enabled ? '点击禁用' : '点击启用';
}

// 切换自动确认发货状态
async function toggleAutoConfirm(accountId, enabled) {
    try {
    toggleLoading(true);

    const response = await fetch(`${apiBase}/cookies/${accountId}/auto-confirm`, {
        method: 'PUT',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({ auto_confirm: enabled })
    });

    if (response.ok) {
        const result = await response.json();
        showToast(result.message, 'success');

        // 更新界面显示
        updateAutoConfirmRowStatus(accountId, enabled);
    } else {
        const error = await response.json();
        showToast(error.detail || '更新自动确认发货设置失败', 'error');

        // 恢复切换按钮状态
        const toggle = document.querySelector(`input[onchange*="toggleAutoConfirm('${accountId}'"]`);
        if (toggle) {
        toggle.checked = !enabled;
        }
    }

    } catch (error) {
    console.error('切换自动确认发货状态失败:', error);
    showToast('网络错误，请稍后重试', 'error');

    // 恢复切换按钮状态
    const toggle = document.querySelector(`input[onchange*="toggleAutoConfirm('${accountId}'"]`);
    if (toggle) {
        toggle.checked = !enabled;
    }
    } finally {
    toggleLoading(false);
    }
}

// 更新自动确认发货行状态
function updateAutoConfirmRowStatus(accountId, enabled) {
    const row = document.querySelector(`tr:has(input[onchange*="toggleAutoConfirm('${accountId}'"])`);
    if (!row) return;

    const statusBadge = row.querySelector('.status-badge:has(i.bi-truck, i.bi-truck-flatbed)');
    const toggle = row.querySelector(`input[onchange*="toggleAutoConfirm('${accountId}'"]`);

    if (statusBadge && toggle) {
    // 更新状态徽章
    statusBadge.className = `status-badge ${enabled ? 'enabled' : 'disabled'}`;
    statusBadge.title = enabled ? '自动确认发货已开启' : '自动确认发货已关闭';
    statusBadge.innerHTML = `
        <i class="bi bi-${enabled ? 'truck' : 'truck-flatbed'}"></i>
    `;

    // 更新切换按钮的提示
    const label = toggle.closest('.status-toggle');
    label.title = enabled ? '点击关闭自动确认发货' : '点击开启自动确认发货';
    }
}

// 切换自动好评状态
async function toggleAutoComment(accountId, enabled) {
    try {
        toggleLoading(true);

        const response = await fetch(`${apiBase}/cookies/${accountId}/auto-comment`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({ auto_comment: enabled })
        });

        if (response.ok) {
            const result = await response.json();
            showToast(result.message, 'success');

            // 更新界面显示
            updateAutoCommentRowStatus(accountId, enabled);
        } else {
            const error = await response.json();
            showToast(error.detail || '更新自动好评设置失败', 'error');

            // 恢复切换按钮状态
            const toggle = document.querySelector(`input[onchange*="toggleAutoComment('${accountId}'"]`);
            if (toggle) {
                toggle.checked = !enabled;
            }
        }

    } catch (error) {
        console.error('切换自动好评状态失败:', error);
        showToast('网络错误，请稍后重试', 'error');

        // 恢复切换按钮状态
        const toggle = document.querySelector(`input[onchange*="toggleAutoComment('${accountId}'"]`);
        if (toggle) {
            toggle.checked = !enabled;
        }
    } finally {
        toggleLoading(false);
    }
}

// 更新自动好评行状态
function updateAutoCommentRowStatus(accountId, enabled) {
    const row = document.querySelector(`tr:has(input[onchange*="toggleAutoComment('${accountId}'"])`);
    if (!row) return;

    const statusBadge = row.querySelector('.status-badge:has(i.bi-star, i.bi-star-fill)');
    const toggle = row.querySelector(`input[onchange*="toggleAutoComment('${accountId}'"]`);

    if (statusBadge && toggle) {
        // 更新状态徽章
        statusBadge.className = `status-badge ${enabled ? 'enabled' : 'disabled'}`;
        statusBadge.title = enabled ? '自动好评已开启' : '自动好评已关闭';
        statusBadge.innerHTML = `
            <i class="bi bi-${enabled ? 'star-fill' : 'star'}"></i>
        `;

        // 更新切换按钮的提示
        const label = toggle.closest('.status-toggle');
        label.title = enabled ? '点击关闭自动好评' : '点击开启自动好评';
    }
}

// 当前编辑的好评模板账号ID
let currentCommentTemplateAccountId = null;

// 显示好评模板管理弹窗
async function showCommentTemplates(accountId) {
    currentCommentTemplateAccountId = accountId;
    
    try {
        toggleLoading(true);
        
        // 获取好评模板列表
        const response = await fetch(`${apiBase}/cookies/${accountId}/comment-templates`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        if (!response.ok) {
            throw new Error('获取好评模板列表失败');
        }
        
        const data = await response.json();
        const templates = data.templates || [];
        
        // 生成模板列表HTML
        let templatesHtml = '';
        if (templates.length === 0) {
            templatesHtml = '<div class="text-center text-muted py-4"><i class="bi bi-inbox fs-1 d-block mb-2"></i>暂无好评模板，请添加</div>';
        } else {
            templatesHtml = templates.map(template => `
                <div class="card mb-2 ${template.is_active ? 'border-success' : ''}">
                    <div class="card-body py-2 px-3">
                        <div class="d-flex justify-content-between align-items-start">
                            <div class="flex-grow-1">
                                <div class="d-flex align-items-center mb-1">
                                    <strong class="me-2">${escapeHtml(template.name)}</strong>
                                    ${template.is_active ? '<span class="badge bg-success">使用中</span>' : ''}
                                </div>
                                <p class="mb-0 text-muted small" style="white-space: pre-wrap; max-height: 60px; overflow: hidden;">${escapeHtml(template.content)}</p>
                            </div>
                            <div class="btn-group btn-group-sm ms-2">
                                ${!template.is_active ? `<button class="btn btn-outline-success" onclick="activateCommentTemplate('${accountId}', ${template.id})" title="使用此模板"><i class="bi bi-check-circle"></i></button>` : ''}
                                <button class="btn btn-outline-primary" onclick="editCommentTemplate(${template.id}, '${escapeHtml(template.name)}', '${escapeHtml(template.content)}')" title="编辑"><i class="bi bi-pencil"></i></button>
                                <button class="btn btn-outline-danger" onclick="deleteCommentTemplate('${accountId}', ${template.id})" title="删除"><i class="bi bi-trash"></i></button>
                            </div>
                        </div>
                    </div>
                </div>
            `).join('');
        }
        
        // 显示模态框
        const modalHtml = `
            <div class="modal fade" id="commentTemplatesModal" tabindex="-1" aria-labelledby="commentTemplatesModalLabel" aria-hidden="true">
                <div class="modal-dialog modal-lg">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title" id="commentTemplatesModalLabel">
                                <i class="bi bi-star-fill text-warning me-2"></i>好评模板管理 - ${accountId}
                            </h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                        </div>
                        <div class="modal-body">
                            <div class="mb-3">
                                <button class="btn btn-primary" onclick="showAddCommentTemplateForm()">
                                    <i class="bi bi-plus-circle me-1"></i>添加模板
                                </button>
                            </div>
                            <div id="addTemplateForm" class="card mb-3" style="display: none;">
                                <div class="card-body">
                                    <h6 class="card-title">添加新模板</h6>
                                    <div class="mb-2">
                                        <label class="form-label">模板名称</label>
                                        <input type="text" class="form-control" id="newTemplateName" placeholder="例如：默认好评">
                                    </div>
                                    <div class="mb-2">
                                        <label class="form-label">好评内容</label>
                                        <textarea class="form-control" id="newTemplateContent" rows="3" placeholder="请输入好评内容..."></textarea>
                                    </div>
                                    <div class="form-check mb-2">
                                        <input class="form-check-input" type="checkbox" id="newTemplateActive">
                                        <label class="form-check-label" for="newTemplateActive">立即使用此模板</label>
                                    </div>
                                    <div class="d-flex gap-2">
                                        <button class="btn btn-success" onclick="addCommentTemplate()">保存</button>
                                        <button class="btn btn-secondary" onclick="hideAddCommentTemplateForm()">取消</button>
                                    </div>
                                </div>
                            </div>
                            <div id="editTemplateForm" class="card mb-3" style="display: none;">
                                <div class="card-body">
                                    <h6 class="card-title">编辑模板</h6>
                                    <input type="hidden" id="editTemplateId">
                                    <div class="mb-2">
                                        <label class="form-label">模板名称</label>
                                        <input type="text" class="form-control" id="editTemplateName">
                                    </div>
                                    <div class="mb-2">
                                        <label class="form-label">好评内容</label>
                                        <textarea class="form-control" id="editTemplateContent" rows="3"></textarea>
                                    </div>
                                    <div class="d-flex gap-2">
                                        <button class="btn btn-success" onclick="saveEditCommentTemplate()">保存</button>
                                        <button class="btn btn-secondary" onclick="hideEditCommentTemplateForm()">取消</button>
                                    </div>
                                </div>
                            </div>
                            <div id="templatesList">
                                ${templatesHtml}
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
                        </div>
                    </div>
                </div>
            </div>
        `;
        
        // 检查模态框是否已存在
        const existingModalEl = document.getElementById('commentTemplatesModal');
        if (existingModalEl) {
            // 模态框已存在，只更新模板列表内容
            const templatesList = existingModalEl.querySelector('#templatesList');
            if (templatesList) {
                templatesList.innerHTML = templatesHtml;
            }
            // 隐藏添加和编辑表单
            const addForm = existingModalEl.querySelector('#addTemplateForm');
            const editForm = existingModalEl.querySelector('#editTemplateForm');
            if (addForm) addForm.style.display = 'none';
            if (editForm) editForm.style.display = 'none';
        } else {
            // 模态框不存在，创建新的
            // 先清理可能残留的遮罩层
            document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
            
            // 添加新模态框
            document.body.insertAdjacentHTML('beforeend', modalHtml);
            
            // 显示模态框
            const modal = new bootstrap.Modal(document.getElementById('commentTemplatesModal'));
            modal.show();
        }
        
    } catch (error) {
        console.error('获取好评模板失败:', error);
        showToast('获取好评模板失败: ' + error.message, 'error');
    } finally {
        toggleLoading(false);
    }
}

// 显示添加模板表单
function showAddCommentTemplateForm() {
    document.getElementById('addTemplateForm').style.display = 'block';
    document.getElementById('editTemplateForm').style.display = 'none';
    document.getElementById('newTemplateName').value = '';
    document.getElementById('newTemplateContent').value = '';
    document.getElementById('newTemplateActive').checked = false;
}

// 隐藏添加模板表单
function hideAddCommentTemplateForm() {
    document.getElementById('addTemplateForm').style.display = 'none';
}

// 添加好评模板
async function addCommentTemplate() {
    const name = document.getElementById('newTemplateName').value.trim();
    const content = document.getElementById('newTemplateContent').value.trim();
    const isActive = document.getElementById('newTemplateActive').checked;
    
    if (!name) {
        showToast('请输入模板名称', 'warning');
        return;
    }
    if (!content) {
        showToast('请输入好评内容', 'warning');
        return;
    }
    
    try {
        toggleLoading(true);
        
        const response = await fetch(`${apiBase}/cookies/${currentCommentTemplateAccountId}/comment-templates`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                name: name,
                content: content,
                is_active: isActive
            })
        });
        
        if (response.ok) {
            showToast('添加好评模板成功', 'success');
            toggleLoading(false);
            // 刷新模板列表
            await showCommentTemplates(currentCommentTemplateAccountId);
            return;
        } else {
            const error = await response.json();
            showToast(error.detail || '添加好评模板失败', 'error');
        }
    } catch (error) {
        console.error('添加好评模板失败:', error);
        showToast('网络错误，请稍后重试', 'error');
    }
    toggleLoading(false);
}

// 编辑好评模板
function editCommentTemplate(templateId, name, content) {
    document.getElementById('addTemplateForm').style.display = 'none';
    document.getElementById('editTemplateForm').style.display = 'block';
    document.getElementById('editTemplateId').value = templateId;
    document.getElementById('editTemplateName').value = name;
    document.getElementById('editTemplateContent').value = content;
}

// 隐藏编辑模板表单
function hideEditCommentTemplateForm() {
    document.getElementById('editTemplateForm').style.display = 'none';
}

// 保存编辑的好评模板
async function saveEditCommentTemplate() {
    const templateId = document.getElementById('editTemplateId').value;
    const name = document.getElementById('editTemplateName').value.trim();
    const content = document.getElementById('editTemplateContent').value.trim();
    
    if (!name) {
        showToast('请输入模板名称', 'warning');
        return;
    }
    if (!content) {
        showToast('请输入好评内容', 'warning');
        return;
    }
    
    try {
        toggleLoading(true);
        
        const response = await fetch(`${apiBase}/cookies/${currentCommentTemplateAccountId}/comment-templates/${templateId}`, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                name: name,
                content: content
            })
        });
        
        if (response.ok) {
            showToast('更新好评模板成功', 'success');
            toggleLoading(false);
            // 刷新模板列表
            await showCommentTemplates(currentCommentTemplateAccountId);
            return;
        } else {
            const error = await response.json();
            showToast(error.detail || '更新好评模板失败', 'error');
        }
    } catch (error) {
        console.error('更新好评模板失败:', error);
        showToast('网络错误，请稍后重试', 'error');
    }
    toggleLoading(false);
}

// 删除好评模板
async function deleteCommentTemplate(accountId, templateId) {
    if (!confirm('确定要删除此好评模板吗？')) {
        return;
    }
    
    try {
        toggleLoading(true);
        
        const response = await fetch(`${apiBase}/cookies/${accountId}/comment-templates/${templateId}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        if (response.ok) {
            showToast('删除好评模板成功', 'success');
            toggleLoading(false);
            // 刷新模板列表
            await showCommentTemplates(accountId);
            return;
        } else {
            const error = await response.json();
            showToast(error.detail || '删除好评模板失败', 'error');
        }
    } catch (error) {
        console.error('删除好评模板失败:', error);
        showToast('网络错误，请稍后重试', 'error');
    }
    toggleLoading(false);
}

// 激活好评模板
async function activateCommentTemplate(accountId, templateId) {
    try {
        toggleLoading(true);
        
        const response = await fetch(`${apiBase}/cookies/${accountId}/comment-templates/${templateId}/activate`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        if (response.ok) {
            showToast('已切换使用此模板', 'success');
            toggleLoading(false);
            // 刷新模板列表
            await showCommentTemplates(accountId);
            return;
        } else {
            const error = await response.json();
            showToast(error.detail || '切换模板失败', 'error');
        }
    } catch (error) {
        console.error('切换模板失败:', error);
        showToast('网络错误，请稍后重试', 'error');
    }
    toggleLoading(false);
}

// HTML转义函数
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 跳转到自动回复页面并选择指定账号
function goToAutoReply(accountId) {
    // 切换到自动回复页面
    showSection('auto-reply');

    // 设置账号选择器的值
    setTimeout(() => {
    const accountSelect = document.getElementById('accountSelect');
    if (accountSelect) {
        accountSelect.value = accountId;
        // 触发change事件来加载关键词
        loadAccountKeywords();
    }
    }, 100);

    showToast(`已切换到自动回复页面，账号 "${accountId}" 已选中`, 'info');
}





// 登出功能
async function logout() {
    // 停止销售额摘要定时刷新
    stopSalesSummaryRefreshTimer();
    
    try {
    if (authToken) {
        await fetch('/logout', {
        method: 'POST',
        headers: {
            'Authorization': `Bearer ${authToken}`
        }
        });
    }
    localStorage.removeItem('auth_token');
    window.location.href = '/';
    } catch (err) {
    console.error('登出失败:', err);
    localStorage.removeItem('auth_token');
    window.location.href = '/';
    }
}

// 检查认证状态
async function checkAuth() {
    const token = getAuthToken();
    if (!token) {
    window.location.href = '/';
    return false;
    }

    try {
    const response = await fetch('/verify', {
        headers: {
        'Authorization': `Bearer ${token}`
        }
    });
    const result = await response.json();

    if (!result.authenticated) {
        localStorage.removeItem('auth_token');
        window.location.href = '/';
        return false;
    }

    // 检查是否为管理员，显示管理员菜单和功能
    if (result.is_admin === true) {
        const adminMenuSection = document.getElementById('adminMenuSection');
        if (adminMenuSection) {
        adminMenuSection.style.display = 'block';
        }

        // 显示备份管理功能
        const backupManagement = document.getElementById('backup-management');
        if (backupManagement) {
        backupManagement.style.display = 'block';
        }

        // 显示系统重启功能
        const systemRestartBtn = document.getElementById('system-restart-btn');
        if (systemRestartBtn) {
        systemRestartBtn.style.display = 'inline-block';
        }

        const dashboardHotUpdateGroup = document.getElementById('dashboardHotUpdateGroup');
        if (dashboardHotUpdateGroup) {
        dashboardHotUpdateGroup.style.display = 'inline-flex';
        }

        // 显示登录与注册设置
        const loginInfoSettings = document.getElementById('login-info-settings');
        if (loginInfoSettings) {
        loginInfoSettings.style.display = 'flex';
        }

        const riskControlSettings = document.getElementById('risk-control-settings');
        if (riskControlSettings) {
        riskControlSettings.style.display = 'block';
        }

        await loadRiskControlNightSettings();
    } else {
        const riskControlSettings = document.getElementById('risk-control-settings');
        if (riskControlSettings) {
        riskControlSettings.style.display = 'none';
        }
    }

    return true;
    } catch (err) {
    localStorage.removeItem('auth_token');
    window.location.href = '/';
    return false;
    }
}

// 初始化事件监听
document.addEventListener('DOMContentLoaded', async () => {
    // 首先检查认证状态
    const isAuthenticated = await checkAuth();
    if (!isAuthenticated) return;

    // 初始化侧边栏折叠状态
    initSidebarCollapse();
    // 初始化暗色模式
    initDarkMode();
    // 初始化账号保活诊断事件
    initAboutDiagnosticsEvents();
    // 加载系统版本号
    // 加载防抖延迟设置
    loadDebounceDelay();
    // 启动验证会话监控
    startCaptchaSessionMonitor();
    // 添加Cookie表单提交
    document.getElementById('addForm').addEventListener('submit', handleManualCookieImport);

    // 添加账号密码登录表单提交
    const passwordLoginForm = document.getElementById('passwordLoginFormElement');
    if (passwordLoginForm) {
        passwordLoginForm.addEventListener('submit', handlePasswordLogin);
    }

    // 增强的键盘快捷键和用户体验
    // textarea 中 Enter 允许换行，Ctrl+Enter 提交
    document.getElementById('newKeyword')?.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && e.ctrlKey) {
        e.preventDefault();
        addKeyword();
    }
    });

    document.getElementById('newReply')?.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && e.ctrlKey) {
        e.preventDefault();
        addKeyword();
    }
    });

    // ESC键取消编辑
    document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape' && typeof window.editingIndex !== 'undefined') {
        e.preventDefault();
        cancelEdit();
    }
    });

    // 输入框实时验证和提示
    document.getElementById('newKeyword')?.addEventListener('input', function(e) {
    const value = e.target.value.trim();
    const addBtn = document.querySelector('.add-btn');
    const replyInput = document.getElementById('newReply');

    if (value.length > 0) {
        e.target.style.borderColor = '#10b981';
        // 只要关键词有内容就可以添加，不需要回复内容
        addBtn.style.opacity = '1';
        addBtn.style.transform = 'scale(1)';
    } else {
        e.target.style.borderColor = '#e5e7eb';
        addBtn.style.opacity = '0.7';
        addBtn.style.transform = 'scale(0.95)';
    }
    });

    document.getElementById('newReply')?.addEventListener('input', function(e) {
    const value = e.target.value.trim();
    const keywordInput = document.getElementById('newKeyword');

    // 回复内容可以为空，只需要关键词有内容即可
    if (value.length > 0) {
        e.target.style.borderColor = '#10b981';
    } else {
        e.target.style.borderColor = '#e5e7eb';
    }

    // 按钮状态只依赖关键词是否有内容
    const addBtn = document.querySelector('.add-btn');
    if (keywordInput.value.trim().length > 0) {
        addBtn.style.opacity = '1';
        addBtn.style.transform = 'scale(1)';
    } else {
        addBtn.style.opacity = '0.7';
        addBtn.style.transform = 'scale(0.95)';
    }
    });

    // 初始加载仪表盘
    loadDashboard();

    // 加载菜单设置并应用
    loadMenuSettings();

    // 初始化图片关键词事件监听器
    initImageKeywordEventListeners();

    // 初始化卡券图片文件选择器
    initCardImageFileSelector();

    // 初始化编辑卡券图片文件选择器
    initEditCardImageFileSelector();

    // 初始化工具提示
    initTooltips();

    // 初始化商品搜索功能
    initItemsSearch();

    // 初始化商品搜索界面功能
    initItemSearch();

    // 点击侧边栏外部关闭移动端菜单
    document.addEventListener('click', function(e) {
    const sidebar = document.getElementById('sidebar');
    const toggle = document.querySelector('.mobile-toggle');

    if (window.innerWidth <= 768 &&
        !sidebar.contains(e.target) &&
        !toggle.contains(e.target) &&
        sidebar.classList.contains('show')) {
        sidebar.classList.remove('show');
    }
    });
});
