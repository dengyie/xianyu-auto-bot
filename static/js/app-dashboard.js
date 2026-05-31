function getDashboardAnnouncementDismissKey(id) {
    return `${DASHBOARD_ANNOUNCEMENT_DISMISS_PREFIX}${String(id || '').trim()}`;
}

function normalizeDashboardAnnouncementState(payload) {
    return {
        current: payload?.current || null,
        history: Array.isArray(payload?.history) ? payload.history : []
    };
}

function isDashboardAnnouncementDismissed(announcement) {
    const announcementId = String(announcement?.id || '').trim();
    if (!announcementId) {
        return false;
    }
    return localStorage.getItem(getDashboardAnnouncementDismissKey(announcementId)) === 'true';
}

function dismissDashboardAnnouncement(announcement) {
    const announcementId = String(announcement?.id || '').trim();
    if (announcementId) {
        localStorage.setItem(getDashboardAnnouncementDismissKey(announcementId), 'true');
    }
    renderDashboardAnnouncement();
}

function handleDashboardAnnouncementAction(announcement) {
    const actionType = String(announcement?.action_type || '').trim().toLowerCase();
    if (!actionType) {
        return;
    }

    if (actionType === 'url') {
        const targetUrl = String(announcement?.action_url || '').trim();
        if (targetUrl) {
            window.open(targetUrl, '_blank', 'noopener,noreferrer');
        }
    }
}

function getDashboardAnnouncementLevelText(level) {
    const normalizedLevel = String(level || '').trim().toLowerCase();
    if (normalizedLevel === 'success') return '成功';
    if (normalizedLevel === 'warning') return '提醒';
    if (normalizedLevel === 'danger') return '重要';
    return '公告';
}

function getDashboardAnnouncementStatusText(status) {
    const normalizedStatus = String(status || '').trim().toLowerCase();
    if (normalizedStatus === 'active') return '当前生效';
    if (normalizedStatus === 'scheduled') return '尚未生效';
    if (normalizedStatus === 'expired') return '已结束';
    if (normalizedStatus === 'disabled') return '未启用';
    return '历史记录';
}

function getDashboardAnnouncementDisplayTime(announcement) {
    const timeValue = String(
        announcement?.published_at
        || announcement?.start_at
        || announcement?.end_at
        || ''
    ).trim();
    if (!timeValue) {
        return '未设置时间';
    }
    return formatDateTime(timeValue);
}

function showDashboardAnnouncementHistoryModal() {
    const history = Array.isArray(dashboardAnnouncementState.history) ? dashboardAnnouncementState.history : [];
    if (!history.length) {
        showToast('暂无公告记录', 'info');
        return;
    }

    const modalId = 'dashboardAnnouncementHistoryModal';
    const existingModal = document.getElementById(modalId);
    if (existingModal) {
        existingModal.remove();
    }

    const historyHtml = history.map((announcement, index) => {
        const level = ['info', 'success', 'warning', 'danger'].includes(String(announcement?.level || '').trim().toLowerCase())
            ? String(announcement.level || '').trim().toLowerCase()
            : 'info';
        const status = String(announcement?.status || '').trim().toLowerCase() || 'disabled';
        const title = String(announcement?.title || '').trim() || '未命名公告';
        const message = String(announcement?.message || '').trim() || '暂无内容';
        const actionText = String(announcement?.action_type ? (announcement?.action_text || '') : '').trim();
        const timeText = getDashboardAnnouncementDisplayTime(announcement);
        const currentBadge = announcement?.is_current
            ? '<span class="dashboard-announcement-history-badge is-current">当前</span>'
            : '';

        return `
            <article class="dashboard-announcement-history-item ${announcement?.is_current ? 'is-current' : ''}">
                <div class="dashboard-announcement-history-head">
                    <div class="dashboard-announcement-history-meta">
                        <div class="dashboard-announcement-history-title-row">
                            <h6 class="dashboard-announcement-history-title mb-0">${escapeHtml(title)}</h6>
                            ${currentBadge}
                            <span class="dashboard-announcement-history-badge is-${level}">${escapeHtml(getDashboardAnnouncementLevelText(level))}</span>
                            <span class="dashboard-announcement-history-badge is-status">${escapeHtml(getDashboardAnnouncementStatusText(status))}</span>
                        </div>
                        <div class="dashboard-announcement-history-time">
                            <i class="bi bi-clock-history"></i>
                            <span>${escapeHtml(timeText)}</span>
                        </div>
                    </div>
                    ${actionText ? `
                        <button
                            type="button"
                            class="btn btn-sm dashboard-announcement-history-action"
                            data-announcement-history-action-index="${index}"
                        >
                            ${escapeHtml(actionText)}
                        </button>
                    ` : ''}
                </div>
                <div class="dashboard-announcement-history-message">${escapeHtml(message)}</div>
            </article>
        `;
    }).join('');

    document.body.insertAdjacentHTML('beforeend', `
        <div class="modal fade" id="${modalId}" tabindex="-1" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered modal-lg modal-dialog-scrollable">
                <div class="modal-content dashboard-announcement-history-modal">
                    <div class="modal-header dashboard-announcement-history-modal-header">
                        <div>
                            <h5 class="modal-title mb-1">
                                <i class="bi bi-megaphone-fill me-2"></i>公告记录
                            </h5>
                            <div class="dashboard-announcement-history-modal-subtitle">按发布时间倒序展示近期公告内容</div>
                        </div>
                        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal" aria-label="关闭"></button>
                    </div>
                    <div class="modal-body dashboard-announcement-history-modal-body">
                        <div class="dashboard-announcement-history-list">
                            ${historyHtml}
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `);

    const modalElement = document.getElementById(modalId);
    if (!modalElement) {
        return;
    }

    modalElement.querySelectorAll('[data-announcement-history-action-index]').forEach(button => {
        button.addEventListener('click', () => {
            const index = Number(button.getAttribute('data-announcement-history-action-index'));
            const announcement = Number.isFinite(index) ? history[index] : null;
            if (!announcement) {
                return;
            }
            const modalInstance = bootstrap.Modal.getInstance(modalElement);
            if (modalInstance) {
                modalInstance.hide();
            }
            setTimeout(() => {
                handleDashboardAnnouncementAction(announcement);
            }, 120);
        });
    });

    modalElement.addEventListener('hidden.bs.modal', () => {
        modalElement.remove();
    }, { once: true });

    const modal = new bootstrap.Modal(modalElement);
    modal.show();
}

function renderDashboardAnnouncement() {
    const slot = document.getElementById('dashboardAnnouncementSlot');
    if (!slot) return;

    const currentAnnouncement = dashboardAnnouncementState.current;
    if (!currentAnnouncement || isDashboardAnnouncementDismissed(currentAnnouncement)) {
        slot.style.display = 'none';
        slot.innerHTML = '';
        return;
    }

    const level = ['info', 'success', 'warning', 'danger'].includes(String(currentAnnouncement.level || '').trim().toLowerCase())
        ? String(currentAnnouncement.level || '').trim().toLowerCase()
        : 'info';
    const title = String(currentAnnouncement.title || '').trim();
    const message = String(currentAnnouncement.message || '').trim();
    const summary = String(currentAnnouncement.summary || currentAnnouncement.brief || currentAnnouncement.short_message || '').trim();
    const displayMessage = summary || message;
    const actionText = String(currentAnnouncement.action_type ? (currentAnnouncement.action_text || '') : '').trim();
    const dismissible = currentAnnouncement.dismissible !== false;

    slot.style.display = '';
    slot.innerHTML = `
        <div class="dashboard-announcement-card is-${level}" role="status" aria-live="polite">
            <button
                type="button"
                class="dashboard-announcement-main"
                id="dashboardAnnouncementOpenBtn"
                title="点击查看公告记录"
                aria-label="查看公告记录"
            >
                <span class="dashboard-announcement-icon">
                    <i class="bi bi-megaphone-fill"></i>
                </span>
                <span class="dashboard-announcement-body">
                    ${title ? `<span class="dashboard-announcement-title">${escapeHtml(title)}</span>` : ''}
                    ${displayMessage ? `<span class="dashboard-announcement-message">${escapeHtml(displayMessage)}</span>` : ''}
                </span>
            </button>
            <div class="dashboard-announcement-actions">
                ${actionText ? `<button type="button" class="btn btn-sm dashboard-announcement-action" id="dashboardAnnouncementActionBtn">${escapeHtml(actionText)}</button>` : ''}
                ${dismissible ? `
                    <button type="button" class="btn btn-sm dashboard-announcement-close" id="dashboardAnnouncementCloseBtn" aria-label="关闭公告">
                        <i class="bi bi-x-lg"></i>
                    </button>
                ` : ''}
            </div>
        </div>
    `;

    const openButton = document.getElementById('dashboardAnnouncementOpenBtn');
    if (openButton) {
        openButton.onclick = () => showDashboardAnnouncementHistoryModal();
    }

    const actionButton = document.getElementById('dashboardAnnouncementActionBtn');
    if (actionButton) {
        actionButton.onclick = () => handleDashboardAnnouncementAction(currentAnnouncement);
    }

    const closeButton = document.getElementById('dashboardAnnouncementCloseBtn');
    if (closeButton) {
        closeButton.onclick = () => dismissDashboardAnnouncement(currentAnnouncement);
    }
}

async function loadDashboardAnnouncement() {
    const result = await fetchDashboardResource('/api/announcement', { success: false, current: null, history: [] });
    dashboardAnnouncementState = normalizeDashboardAnnouncementState(result?.success ? result : null);
    renderDashboardAnnouncement();
}

function renderDashboardSummaryCard(label, value, tone = 'primary', details = []) {
    const detailMarkup = Array.isArray(details) && details.length ? `
        <div class="dashboard-account-summary-details">
            ${details.map(([detailLabel, detailValue]) => `
                <span class="dashboard-account-summary-detail">
                    <span class="dashboard-account-summary-detail-label">${escapeHtml(detailLabel)}</span>
                    <span class="dashboard-account-summary-detail-value">${escapeHtml(detailValue)}</span>
                </span>
            `).join('')}
        </div>
    ` : '';

    return `
        <div class="dashboard-account-summary-item is-${tone}">
            <div class="dashboard-account-summary-main">
                <div class="dashboard-account-summary-label">${escapeHtml(label)}</div>
            </div>
            <div class="dashboard-account-summary-side">
                <div class="dashboard-account-summary-value">${escapeHtml(value)}</div>
                ${detailMarkup}
            </div>
        </div>
    `;
}

function renderDashboardAccountMetric(label, value, tone = 'off') {
    return `
        <div class="dashboard-account-metric is-${tone}">
            <div class="dashboard-account-metric-label">${escapeHtml(label)}</div>
            <div class="dashboard-account-metric-value">${escapeHtml(value)}</div>
        </div>
    `;
}

function isRuntimeStatusHealthy(runtimeStatus) {
    return Boolean(
        runtimeStatus?.running
        && runtimeStatus.ws_ready
        && runtimeStatus.session_ready
        && runtimeStatus.has_current_token
        && runtimeStatus.message_stream_ready
    );
}

function getRuntimeStatusRecentAnchor(runtimeStatus) {
    const normalizedRuntimeStatus = runtimeStatus || {};
    const timestampKeys = [
        'state_last_changed_at',
        'last_successful_connection_at',
        'last_heartbeat_response_at',
        'session_keepalive_at',
        'token_last_refreshed_at',
        'last_message_received_at',
    ];

    const timestamps = timestampKeys
        .map(key => Number(normalizedRuntimeStatus[key] || 0))
        .filter(value => Number.isFinite(value) && value > 0);

    return timestamps.length ? Math.max(...timestamps) : 0;
}

function shouldAutoRetryRuntimeStatus(runtimeStatus) {
    if (!runtimeStatus?.running) {
        return false;
    }

    const connectionState = String(runtimeStatus.connection_state || '').trim();
    if (connectionState === 'connecting' || connectionState === 'reconnecting') {
        return true;
    }

    if (isRuntimeStatusHealthy(runtimeStatus)) {
        return false;
    }

    const recentAnchor = getRuntimeStatusRecentAnchor(runtimeStatus);
    if (!recentAnchor) {
        return false;
    }

    return ((Date.now() / 1000) - recentAnchor) <= 90;
}

function getMessageStreamRuntimeDisplay(runtimeStatus) {
    const normalizedRuntimeStatus = runtimeStatus || {};
    const explicitStatus = String(normalizedRuntimeStatus.message_stream_status || '').trim();
    const explicitNote = String(normalizedRuntimeStatus.message_stream_note || '').trim();
    const connectionState = String(normalizedRuntimeStatus.connection_state || '').trim();

    let status = explicitStatus;
    if (!status) {
        if (!normalizedRuntimeStatus.running) {
            status = 'not_running';
        } else if (connectionState === 'connecting' || connectionState === 'reconnecting') {
            status = 'recovering';
        } else if (connectionState !== 'connected' || normalizedRuntimeStatus.ws_ready === false) {
            status = 'connection_unready';
        } else if (normalizedRuntimeStatus.message_stream_ready) {
            status = 'watching';
        } else {
            status = 'connection_unready';
        }
    }

    let note = explicitNote;
    if (!note) {
        if (!normalizedRuntimeStatus.running) {
            note = '账号实例未启动，业务消息流尚未建立';
        } else if (status === 'recovering') {
            note = '连接正在恢复，业务消息流状态将在重连稳定后更新';
        } else if (status === 'connection_unready') {
            note = '连接未就绪，业务消息流状态待 WebSocket 恢复后更新';
        } else if (status === 'watching') {
            note = '当前连接尚未收到非心跳业务包';
        } else {
            note = '业务消息流状态等待更多运行时数据';
        }
    }

    return { status, note };
}

function scheduleDashboardRuntimeAutoRetry(accounts) {
    if (dashboardRuntimeRetryTimer) {
        clearTimeout(dashboardRuntimeRetryTimer);
        dashboardRuntimeRetryTimer = null;
    }

    if (!document.getElementById('dashboard-section')?.classList.contains('active')) {
        return;
    }

    if (!Array.isArray(accounts) || !accounts.some(account => shouldAutoRetryRuntimeStatus(account.runtime_status))) {
        return;
    }

    if (Date.now() - lastDashboardRuntimeRetryAt < 15000) {
        return;
    }

    const hasTransientState = accounts.some(account => {
        const connectionState = String(account?.runtime_status?.connection_state || '').trim();
        return connectionState === 'connecting' || connectionState === 'reconnecting';
    });
    const delay = hasTransientState ? 3500 : 5000;

    dashboardRuntimeRetryTimer = setTimeout(() => {
        dashboardRuntimeRetryTimer = null;
        if (!document.getElementById('dashboard-section')?.classList.contains('active')) {
            return;
        }
        lastDashboardRuntimeRetryAt = Date.now();
        refreshDashboardRuntimeSnapshots();
    }, delay);
}

function scheduleAboutRuntimeAutoRetry(accountId, runtimeStatus) {
    if (aboutRuntimeRetryTimer) {
        clearTimeout(aboutRuntimeRetryTimer);
        aboutRuntimeRetryTimer = null;
    }

    const normalizedAccountId = String(accountId || '').trim();
    if (!normalizedAccountId) {
        return;
    }

    if (!document.getElementById('accounts-section')?.classList.contains('active')) {
        return;
    }

    if (!shouldAutoRetryRuntimeStatus(runtimeStatus)) {
        return;
    }

    if (Date.now() - lastAboutRuntimeRetryAt < 12000) {
        return;
    }

    const connectionState = String(runtimeStatus?.connection_state || '').trim();
    const delay = (connectionState === 'connecting' || connectionState === 'reconnecting') ? 3000 : 5000;

    aboutRuntimeRetryTimer = setTimeout(() => {
        aboutRuntimeRetryTimer = null;
        if (!document.getElementById('accounts-section')?.classList.contains('active')) {
            return;
        }
        if (getAboutSelectedAccountId() !== normalizedAccountId) {
            return;
        }
        lastAboutRuntimeRetryAt = Date.now();
        loadAboutRuntimeStatus(normalizedAccountId);
    }, delay);
}

function renderDashboardAccountRuntimeSnapshot(runtimeStatus) {
    const normalizedRuntimeStatus = runtimeStatus || {};
    const connectionState = normalizedRuntimeStatus.connection_state || 'not_running';
    const keepaliveDisplayStatus = normalizedRuntimeStatus.session_keepalive_display_status || normalizedRuntimeStatus.session_keepalive_status || '';
    const tokenStatus = normalizedRuntimeStatus.token_refresh_status || '';
    const messageStreamDisplay = getMessageStreamRuntimeDisplay(normalizedRuntimeStatus);
    const messageStreamStatus = messageStreamDisplay.status;

    const connectionText = getAboutStatusText('connection', connectionState) || '未运行';
    const connectionTone = getAboutStatusVariant('connection', connectionState);
    const keepaliveText = keepaliveDisplayStatus
        ? (getAboutStatusText('keepalive', keepaliveDisplayStatus) || keepaliveDisplayStatus)
        : (normalizedRuntimeStatus.running ? '未执行' : '未运行');
    const keepaliveTone = keepaliveDisplayStatus
        ? getAboutStatusVariant('keepalive', keepaliveDisplayStatus)
        : 'secondary';
    const tokenText = tokenStatus
        ? (getAboutStatusText('token', tokenStatus) || tokenStatus)
        : (normalizedRuntimeStatus.running ? '未刷新' : '未运行');
    const tokenTone = tokenStatus
        ? getAboutStatusVariant('token', tokenStatus)
        : 'secondary';
    const messageStreamText = messageStreamStatus
        ? (getAboutStatusText('stream', messageStreamStatus) || messageStreamStatus)
        : (normalizedRuntimeStatus.running ? '观察中' : '未运行');
    const messageStreamTone = messageStreamStatus
        ? getAboutStatusVariant('stream', messageStreamStatus)
        : 'secondary';
    const runningHealthy = isRuntimeStatusHealthy(normalizedRuntimeStatus);
    const summaryText = !normalizedRuntimeStatus.running
        ? '未运行'
        : (runningHealthy ? '运行正常' : '部分异常');
    const summaryTone = !normalizedRuntimeStatus.running
        ? 'secondary'
        : (runningHealthy ? 'success' : 'warning');
    const items = [
        { label: '连接', text: connectionText, tone: connectionTone },
        { label: '保活', text: keepaliveText, tone: keepaliveTone },
        { label: 'Token', text: tokenText, tone: tokenTone },
        { label: '消息流', text: messageStreamText, tone: messageStreamTone }
    ];

    return `
        <div class="dashboard-account-runtime" aria-label="账号运行态快照">
            <div class="dashboard-account-runtime-summary is-${summaryTone}">
                <span class="dashboard-account-runtime-summary-dot" aria-hidden="true"></span>
                <span class="dashboard-account-runtime-summary-text">${escapeHtml(summaryText)}</span>
            </div>
            <div class="dashboard-account-runtime-signals">
                ${items.map(item => {
                    const detailText = `${item.label}: ${item.text}`;
                    return `
                        <span class="dashboard-account-runtime-signal is-${item.tone}" title="${escapeHtml(detailText)}" aria-label="${escapeHtml(detailText)}">
                            <span class="dashboard-account-runtime-signal-dot" aria-hidden="true"></span>
                            <span class="dashboard-account-runtime-signal-label">${escapeHtml(item.label)}</span>
                        </span>
                    `;
                }).join('')}
            </div>
        </div>
    `;
}

function renderStatusNoteBadge(statusNote, className) {
    const noteText = String(statusNote || '').trim();
    if (!noteText) {
        return '';
    }
    const safeClassName = className || 'account-status-note-badge';
    return `
        <span class="${safeClassName}" title="${escapeHtml(noteText)}">
            <i class="bi bi-shield-exclamation"></i>
            ${escapeHtml(noteText)}
        </span>
    `;
}

function renderDashboardAccountCard(account) {
    const isEnabled = account.enabled === undefined ? true : account.enabled;
    const keywordCount = account.keywordCount || 0;
    const defaultReplyEnabled = Boolean(account.defaultReply?.enabled);
    const aiReplyEnabled = Boolean(account.aiReply?.ai_enabled);
    const autoConfirmEnabled = account.auto_confirm === undefined ? true : Boolean(account.auto_confirm);
    const autoCommentEnabled = Boolean(account.auto_comment);
    const hasCredentials = Boolean(account.username) && Boolean(account.has_password);
    const hasPartialCredentials = !hasCredentials && (Boolean(account.username) || Boolean(account.has_password));
    const pauseDuration = account.pause_duration === 0 ? '不暂停' : `${account.pause_duration || 10} 分钟`;
    const polishSchedule = account.polishSchedule;
    const remarkText = account.remark || '';
    const statusNoteText = String(account.status_note || '').trim();

    let replyModeText = '未开启';
    let replyModeTone = 'off';
    if (aiReplyEnabled && defaultReplyEnabled) {
        replyModeText = 'AI + 默认';
        replyModeTone = 'info';
    } else if (aiReplyEnabled) {
        replyModeText = 'AI 回复';
        replyModeTone = 'info';
    } else if (defaultReplyEnabled) {
        replyModeText = '默认回复';
        replyModeTone = 'on';
    }

    let polishScheduleMetricText = '未设置';
    let polishScheduleTone = 'off';
    if (polishSchedule) {
        if (polishSchedule.enabled) {
            const displayHour = formatPolishScheduleHour(polishSchedule.delay_minutes ?? polishSchedule.run_hour);
            polishScheduleMetricText = `${displayHour}`;
            polishScheduleTone = 'info';
        } else {
            const displayHour = formatPolishScheduleHour(polishSchedule.delay_minutes ?? polishSchedule.run_hour);
            polishScheduleMetricText = `${displayHour} 未开`;
            polishScheduleTone = 'warn';
        }
    } else if (isEnabled) {
        polishScheduleMetricText = '未设置';
        polishScheduleTone = 'off';
    }

    const metrics = [
        renderDashboardAccountMetric('关键词', keywordCount > 0 ? `${keywordCount} 个` : '未配置', keywordCount > 0 ? 'on' : 'off'),
        renderDashboardAccountMetric('回复模式', replyModeText, replyModeTone),
        renderDashboardAccountMetric('定时擦亮', polishScheduleMetricText, polishScheduleTone)
    ].join('');
    const runtimeSnapshot = renderDashboardAccountRuntimeSnapshot(account.runtime_status);

    const secondarySummary = [
        {
            label: '关键词',
            icon: 'chat-left-text-fill',
            tone: keywordCount > 0 ? 'on' : 'off'
        },
        {
            label: '自动发货',
            icon: 'lightning-charge-fill',
            tone: autoConfirmEnabled ? 'on' : 'off'
        },
        {
            label: '自动好评',
            icon: 'chat-heart-fill',
            tone: autoCommentEnabled ? 'on' : 'off'
        },
        {
            label: '账密',
            icon: hasPartialCredentials ? 'exclamation-triangle-fill' : 'shield-lock-fill',
            tone: hasCredentials ? 'info' : (hasPartialCredentials ? 'warn' : 'off')
        },
        {
            label: '暂停',
            value: pauseDuration,
            icon: 'clock-history',
            tone: 'neutral'
        }
    ].map(({ label, value = '', icon, tone }) => `
        <span class="dashboard-account-secondary-pill is-${tone}">
            <i class="bi bi-${icon} dashboard-account-secondary-pill-icon"></i>
            <span class="dashboard-account-secondary-pill-label">${escapeHtml(label)}</span>
            ${value ? `<span class="dashboard-account-secondary-pill-value">${escapeHtml(value)}</span>` : ''}
        </span>
    `).join('');

    return `
        <div class="dashboard-account-card ${isEnabled ? '' : 'is-disabled'}" data-account-id="${escapeHtml(account.id)}" role="button" tabindex="0" onclick="openAccountManagement(this.dataset.accountId)" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openAccountManagement(this.dataset.accountId);}">
            <div class="dashboard-account-card-head">
                <div class="dashboard-account-card-main">
                    <div class="dashboard-account-card-title">
                        <div class="dashboard-account-card-id">${escapeHtml(account.id)}</div>
                        ${remarkText ? `<span class="dashboard-account-card-remark-badge">${escapeHtml(remarkText)}</span>` : ''}
                    </div>
                    <div class="dashboard-account-secondary">${secondarySummary}</div>
                </div>
                <div class="dashboard-account-card-side">
                    <span class="dashboard-account-status ${isEnabled ? 'is-enabled' : 'is-disabled'}">
                        <i class="bi bi-${isEnabled ? 'check-circle-fill' : 'pause-circle-fill'}"></i>
                        ${isEnabled ? '启用中' : '已禁用'}
                    </span>
                    ${renderStatusNoteBadge(statusNoteText, 'dashboard-account-status-note')}
                </div>
            </div>
            <div class="dashboard-account-main-metrics">${metrics}</div>
            ${runtimeSnapshot}
        </div>
    `;
}

function renderDashboardAccountOverview(accounts, totalItems = 0) {
    const summary = document.getElementById('dashboardAccountSummary');
    const enabledContainer = document.getElementById('dashboardEnabledAccounts');
    const disabledContainer = document.getElementById('dashboardDisabledAccounts');
    const enabledHint = document.getElementById('dashboardEnabledAccountsHint');
    const disabledHint = document.getElementById('dashboardDisabledAccountsHint');

    if (!summary || !enabledContainer || !disabledContainer || !enabledHint || !disabledHint) {
        return;
    }

    const enabledAccounts = accounts.filter(account => account.enabled === undefined ? true : account.enabled);
    const disabledAccounts = accounts.filter(account => !(account.enabled === undefined ? true : account.enabled));
    const riskProtectedAccounts = disabledAccounts.filter(account => String(account.status_note || '').trim()).length;
    const activeKeywordAccounts = enabledAccounts.filter(account => (account.keywordCount || 0) > 0).length;
    const totalKeywords = enabledAccounts.reduce((sum, account) => sum + (account.keywordCount || 0), 0);

    summary.innerHTML = [
        ['全部账号', String(accounts.length), 'primary', []],
        ['已启用 / 已禁用', `${enabledAccounts.length} / ${disabledAccounts.length}`, 'success', []],
        ['关键词总数', String(totalKeywords), 'info', []],
        ['商品总数', String(totalItems), 'muted', []]
    ].map(([label, value, tone, details]) => renderDashboardSummaryCard(label, value, tone, details)).join('');

    enabledHint.textContent = `${enabledAccounts.length} 个账号`;
    disabledHint.textContent = disabledAccounts.length
        ? `${disabledAccounts.length} 个账号待恢复${riskProtectedAccounts ? `，其中 ${riskProtectedAccounts} 个处于风控保护中` : ''}`
        : '暂无禁用账号';

    const sortAccounts = (items) => [...items].sort((a, b) => {
        const keywordDiff = (b.keywordCount || 0) - (a.keywordCount || 0);
        if (keywordDiff !== 0) {
            return keywordDiff;
        }
        return String(a.id || '').localeCompare(String(b.id || ''), 'zh-Hans-CN');
    });

    enabledContainer.innerHTML = enabledAccounts.length
        ? sortAccounts(enabledAccounts).map(renderDashboardAccountCard).join('')
        : '<div class="dashboard-account-empty"><i class="bi bi-inbox me-1"></i>暂无启用账号</div>';

    disabledContainer.innerHTML = disabledAccounts.length
        ? sortAccounts(disabledAccounts).map(renderDashboardAccountCard).join('')
        : '<div class="dashboard-account-empty"><i class="bi bi-inbox me-1"></i>暂无禁用账号</div>';
}

// 加载仪表盘数据
async function loadDashboard() {
    try {
    toggleLoading(true);
    loadDashboardAnnouncement();

    // 获取账号列表
    const cookiesResponse = await fetch(`${apiBase}/cookies/details`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (cookiesResponse.ok) {
        const cookiesData = await cookiesResponse.json();

        const accountsWithKeywords = await enrichDashboardAccounts(cookiesData);

        dashboardData.accounts = accountsWithKeywords;
        dashboardData.totalKeywords = accountsWithKeywords.reduce((sum, account) => {
        const isEnabled = account.enabled === undefined ? true : account.enabled;
        return sum + (isEnabled ? (account.keywordCount || 0) : 0);
        }, 0);

        // 加载商品总数
        const totalItems = await loadItemsCount();
        dashboardData.totalItems = totalItems;

        // 加载订单看板数据
        const orderMetrics = await loadOrderDashboardMetrics();

        // 加载销售额摘要数据
        await loadSalesSummary();

        // 加载销售额图表数据（默认显示最近1周）
        await loadSalesChart('week');

        // 更新仪表盘显示
        renderDashboardAccountOverview(accountsWithKeywords, totalItems);
        scheduleDashboardRuntimeAutoRetry(accountsWithKeywords);
        await loadDashboardDeliveryLogs();
    }
    } catch (error) {
    console.error('加载仪表盘数据失败:', error);
    showToast('加载仪表盘数据失败', 'danger');
    } finally {
    toggleLoading(false);
    }
}

async function refreshDashboardRuntimeSnapshots() {
    if (!dashboardData.accounts.length) {
        return;
    }

    try {
        const cookieDetails = await fetchJSON(`${apiBase}/cookies/details`);
        const runtimeStatusMap = new Map(
            (Array.isArray(cookieDetails) ? cookieDetails : []).map(cookie => [
                String(cookie.id),
                {
                    runtime_status: cookie.runtime_status || null,
                    enabled: cookie.enabled,
                    status_note: cookie.status_note || '',
                }
            ])
        );

        dashboardData.accounts = dashboardData.accounts.map(account => {
            const accountId = String(account.id || '');
            if (!runtimeStatusMap.has(accountId)) {
                return account;
            }
            const latestDetail = runtimeStatusMap.get(accountId);
            return {
                ...account,
                runtime_status: latestDetail.runtime_status,
                enabled: latestDetail.enabled,
                status_note: latestDetail.status_note,
            };
        });

        renderDashboardAccountOverview(dashboardData.accounts, dashboardData.totalItems || 0);
        scheduleDashboardRuntimeAutoRetry(dashboardData.accounts);
    } catch (error) {
        console.error('刷新仪表盘运行态失败:', error);
    }
}

// 加载商品总数
async function loadItemsCount() {
    try {
        const response = await fetch(`${apiBase}/items`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            throw new Error('获取商品列表失败');
        }

        const data = await response.json();
        const items = Array.isArray(data.items) ? data.items : [];
        return items.length;
    } catch (error) {
        console.error('加载商品总数失败:', error);
        return 0;
    }
}

// 加载仪表盘订单指标
async function loadOrderDashboardMetrics() {
    const defaultMetrics = {
        totalOrders: 0,
        totalSalesAmount: 0,
        completionRate: 0,
        todayOrders: 0
    };

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch('/api/orders', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();
        if (!data.success) {
            console.error('加载订单数量失败:', data.message);
            updateDashboardOrderMetrics(defaultMetrics);
            return defaultMetrics;
        }

        const orders = Array.isArray(data.data) ? data.data : [];
        const totalOrders = orders.length;

        let totalSalesAmount = 0;
        let completedOrders = 0;
        let completionEligibleOrders = 0;
        let todayOrders = 0;

        orders.forEach(order => {
            const normalizedStatus = normalizeOrderStatus(order?.order_status);
            const parsedAmount = parseOrderAmount(order);

            if (isSalesEligibleOrder(normalizedStatus) && parsedAmount !== null) {
                totalSalesAmount += parsedAmount;
            }

            if (isCompletionEligibleOrder(normalizedStatus)) {
                completionEligibleOrders++;
                if (isCompletedOrder(normalizedStatus)) {
                    completedOrders++;
                }
            }

            if (isTodayOrder(getEffectiveOrderSalesTime(order))) {
                todayOrders++;
            }
        });

        const metrics = {
            totalOrders,
            totalSalesAmount,
            completionRate: completionEligibleOrders > 0 ? (completedOrders / completionEligibleOrders) * 100 : 0,
            todayOrders
        };

        updateDashboardOrderMetrics(metrics);
        return metrics;
    } catch (error) {
        console.error('加载订单数量失败:', error);
        updateDashboardOrderMetrics(defaultMetrics);
        return defaultMetrics;
    }
}

// 销售额摘要定时刷新定时器
let salesSummaryRefreshTimer = null;

// 加载销售额摘要数据
async function loadSalesSummary() {
    const todaySalesEl = document.getElementById('dashboardTodaySales');
    const weekSalesEl = document.getElementById('dashboardWeekSales');
    const monthSalesEl = document.getElementById('dashboardMonthSales');
    const updateTimeEl = document.getElementById('dashboardSalesUpdateTime');
    
    // 显示加载状态
    showSalesLoadingState(todaySalesEl);
    showSalesLoadingState(weekSalesEl);
    showSalesLoadingState(monthSalesEl);
    
    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch('/api/sales/summary', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();
        if (data.success && data.data) {
            updateDashboardSalesMetrics(data.data);
        } else {
            showSalesErrorState(todaySalesEl, '获取失败');
            showSalesErrorState(weekSalesEl, '获取失败');
            showSalesErrorState(monthSalesEl, '获取失败');
        }
    } catch (error) {
        console.error('加载销售额摘要失败:', error);
        showSalesErrorState(todaySalesEl, '加载失败');
        showSalesErrorState(weekSalesEl, '加载失败');
        showSalesErrorState(monthSalesEl, '加载失败');
    }
    
    // 启动定时刷新（每5分钟刷新一次）
    startSalesSummaryRefreshTimer();
}

// 显示销售额加载状态
function showSalesLoadingState(element) {
    if (element) {
        element.innerHTML = '<span class="sales-value-loading">加载中...</span>';
    }
}

// 显示销售额错误状态
function showSalesErrorState(element, message) {
    if (element) {
        element.innerHTML = `<span class="sales-value-error">${message}</span>`;
    }
}

// 格式化销售额显示（带千分位分隔符）
function formatSalesAmount(amount) {
    return amount.toLocaleString('zh-CN', {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
}

// 更新销售额指标
function updateDashboardSalesMetrics(metrics) {
    const todaySalesEl = document.getElementById('dashboardTodaySales');
    const weekSalesEl = document.getElementById('dashboardWeekSales');
    const monthSalesEl = document.getElementById('dashboardMonthSales');
    const updateTimeEl = document.getElementById('dashboardSalesUpdateTime');

    if (todaySalesEl) {
        todaySalesEl.innerHTML = `￥${formatSalesAmount(metrics.today_sales)}`;
    }

    if (weekSalesEl) {
        weekSalesEl.innerHTML = `￥${formatSalesAmount(metrics.week_sales)}`;
    }

    if (monthSalesEl) {
        monthSalesEl.innerHTML = `￥${formatSalesAmount(metrics.month_sales)}`;
    }

    if (updateTimeEl) {
        updateTimeEl.textContent = metrics.update_time;
    }
}

// 启动销售额摘要定时刷新
function startSalesSummaryRefreshTimer() {
    // 清除现有定时器
    if (salesSummaryRefreshTimer) {
        clearInterval(salesSummaryRefreshTimer);
    }
    
    // 每5分钟刷新一次
    salesSummaryRefreshTimer = setInterval(async () => {
        try {
            const token = localStorage.getItem('auth_token');
            if (!token) {
                clearInterval(salesSummaryRefreshTimer);
                return;
            }
            
            const response = await fetch('/api/sales/summary', {
                headers: {
                    'Authorization': `Bearer ${token}`
                }
            });

            const data = await response.json();
            if (data.success && data.data) {
                updateDashboardSalesMetrics(data.data);
            }
        } catch (error) {
            console.error('定时刷新销售额摘要失败:', error);
        }
    }, 5 * 60 * 1000); // 5分钟
}

// 停止销售额摘要定时刷新
function stopSalesSummaryRefreshTimer() {
    if (salesSummaryRefreshTimer) {
        clearInterval(salesSummaryRefreshTimer);
        salesSummaryRefreshTimer = null;
    }
}

// 销售额图表实例
let salesChartInstance = null;
let currentChartPeriod = null;
let salesDateRangeOutsideClickBound = false;

// 显示图表加载状态
function showChartLoading() {
    const chartContainer = document.querySelector('.chart-container');
    if (!chartContainer) return;
    
    // 添加加载遮罩
    let loadingOverlay = chartContainer.querySelector('.chart-loading-overlay');
    if (!loadingOverlay) {
        loadingOverlay = document.createElement('div');
        loadingOverlay.className = 'chart-loading-overlay';
        loadingOverlay.innerHTML = `
            <div class="chart-loading-spinner">
                <div class="spinner-border text-primary" role="status">
                    <span class="visually-hidden">加载中...</span>
                </div>
                <span class="chart-loading-text">数据加载中...</span>
            </div>
        `;
        chartContainer.style.position = 'relative';
        chartContainer.appendChild(loadingOverlay);
    }
    loadingOverlay.style.display = 'flex';
}

// 隐藏图表加载状态
function hideChartLoading() {
    const loadingOverlay = document.querySelector('.chart-loading-overlay');
    if (loadingOverlay) {
        loadingOverlay.style.display = 'none';
    }
}

// 更新按钮激活状态
function updateChartButtonState(activePeriod) {
    const buttons = document.querySelectorAll('.sales-period-button');
    buttons.forEach(btn => {
        const btnPeriod = btn.dataset.period;
        const isActive = btnPeriod === activePeriod;

        btn.classList.toggle('is-active', isActive);
        btn.setAttribute('aria-pressed', isActive ? 'true' : 'false');
    });
}

// 加载销售额图表数据
async function loadSalesChart(period) {
    showChartLoading();
    updateChartButtonState(period);
    setDateRangePickerVisible(false);
    
    try {
        const token = localStorage.getItem('auth_token');
        let startDate, endDate;
        const now = new Date();

        if (period === 'week') {
            startDate = new Date(now);
            startDate.setDate(now.getDate() - 6);
        } else if (period === 'month') {
            startDate = new Date(now);
            startDate.setMonth(now.getMonth() - 1);
        }

        const startDateStr = startDate.toISOString().split('T')[0];
        const endDateStr = now.toISOString().split('T')[0];

        const response = await fetch(`/api/sales?start_date=${startDateStr}&end_date=${endDateStr}`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();
        if (data.success && data.data) {
            currentChartPeriod = period;
            renderSalesChart(data.data.sales, period);
        }
    } catch (error) {
        console.error('加载销售额图表数据失败:', error);
        showToast('加载销售额数据失败', 'danger');
    } finally {
        hideChartLoading();
    }
}

// 加载自定义日期范围的销售额数据
async function loadCustomSalesChart() {
    const startDate = document.getElementById('startDate').value;
    const endDate = document.getElementById('endDate').value;

    if (!startDate || !endDate) {
        showToast('请选择开始和结束日期', 'warning');
        return;
    }

    if (new Date(startDate) > new Date(endDate)) {
        showToast('开始日期不能晚于结束日期', 'warning');
        return;
    }

    showChartLoading();
    updateChartButtonState('custom');

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch(`/api/sales?start_date=${startDate}&end_date=${endDate}`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();
        if (data.success && data.data) {
            currentChartPeriod = 'custom';
            renderSalesChart(data.data.sales, 'custom');
        }
    } catch (error) {
        console.error('加载自定义销售额数据失败:', error);
        showToast('加载销售额数据失败', 'danger');
    } finally {
        hideChartLoading();
    }
}

function setDateRangePickerVisible(visible) {
    const dateRangePicker = document.getElementById('dateRangePicker');
    const customButton = document.querySelector('.sales-period-button[data-period="custom"]');
    const timeRangeSelector = document.querySelector('.time-range-selector');
    if (!dateRangePicker) {
        return;
    }

    dateRangePicker.hidden = !visible;
    if (timeRangeSelector) {
        timeRangeSelector.classList.toggle('is-open', visible);
    }
    if (customButton) {
        customButton.setAttribute('aria-expanded', visible ? 'true' : 'false');
    }

    if (!salesDateRangeOutsideClickBound) {
        document.addEventListener('click', event => {
            const control = document.querySelector('.time-range-selector');
            const picker = document.getElementById('dateRangePicker');
            if (!control || !picker || picker.hidden) {
                return;
            }

            if (!control.contains(event.target)) {
                setDateRangePickerVisible(false);
                updateChartButtonState(currentChartPeriod || 'week');
            }
        });

        document.addEventListener('keydown', event => {
            const picker = document.getElementById('dateRangePicker');
            if (event.key === 'Escape' && picker && !picker.hidden) {
                setDateRangePickerVisible(false);
                updateChartButtonState(currentChartPeriod || 'week');
            }
        });

        salesDateRangeOutsideClickBound = true;
    }
}

// 切换日期选择器显示
function toggleDateRangePicker() {
    const dateRangePicker = document.getElementById('dateRangePicker');
    if (!dateRangePicker) {
        return;
    }

    const willShow = dateRangePicker.hidden;
    setDateRangePickerVisible(willShow);

    if (willShow) {
        updateChartButtonState('custom');
        return;
    }

    updateChartButtonState(currentChartPeriod || 'week');
}

// 渲染销售额图表
function renderSalesChart(salesData, period) {
    const ctx = document.getElementById('salesChart').getContext('2d');
    
    // 准备数据
    const labels = salesData.map(item => item.date);
    const data = salesData.map(item => item.amount);

    // 创建渐变填充
    const gradient = ctx.createLinearGradient(0, 0, 0, 300);
    gradient.addColorStop(0, 'rgba(0, 123, 255, 0.3)');
    gradient.addColorStop(0.5, 'rgba(0, 123, 255, 0.15)');
    gradient.addColorStop(1, 'rgba(0, 123, 255, 0.02)');

    // 如果图表已存在，使用平滑更新
    if (salesChartInstance) {
        // 使用动画更新数据
        salesChartInstance.data.labels = labels;
        salesChartInstance.data.datasets[0].data = data;
        salesChartInstance.data.datasets[0].backgroundColor = gradient;
        
        // 更新标题
        salesChartInstance.options.plugins.title.text = getChartTitle(period);
        
        // 平滑过渡更新
        salesChartInstance.update('active');
        return;
    }

    // 创建新图表
    salesChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                label: '销售额',
                data: data,
                borderColor: '#007bff',
                backgroundColor: gradient,
                borderWidth: 3,
                tension: 0.4,
                cubicInterpolationMode: 'monotone',
                fill: true,
                pointBackgroundColor: '#007bff',
                pointBorderColor: '#fff',
                pointBorderWidth: 2,
                pointRadius: 5,
                pointHoverRadius: 7,
                pointHoverBackgroundColor: '#0056b3',
                pointHoverBorderColor: '#fff',
                pointHoverBorderWidth: 3
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            animation: {
                duration: 750,
                easing: 'easeInOutQuart'
            },
            transitions: {
                active: {
                    animation: {
                        duration: 750,
                        easing: 'easeInOutQuart'
                    }
                }
            },
            interaction: {
                mode: 'index',
                intersect: false
            },
            plugins: {
                legend: {
                    position: 'top',
                    labels: {
                        usePointStyle: true,
                        padding: 15,
                        font: {
                            size: 13,
                            weight: '500'
                        }
                    }
                },
                tooltip: {
                    mode: 'index',
                    intersect: false,
                    backgroundColor: 'rgba(0, 0, 0, 0.8)',
                    titleColor: '#fff',
                    bodyColor: '#fff',
                    borderColor: '#007bff',
                    borderWidth: 1,
                    padding: 12,
                    displayColors: true,
                    callbacks: {
                        label: function(context) {
                            return `销售额: ￥${context.parsed.y.toFixed(2)}`;
                        }
                    }
                },
                title: {
                    display: true,
                    text: getChartTitle(period),
                    font: {
                        size: 16,
                        weight: '600'
                    },
                    padding: {
                        bottom: 15
                    }
                }
            },
            scales: {
                x: {
                    display: true,
                    title: {
                        display: true,
                        text: '日期',
                        font: {
                            size: 12,
                            weight: '500'
                        }
                    },
                    grid: {
                        display: false
                    },
                    ticks: {
                        font: {
                            size: 11
                        }
                    }
                },
                y: {
                    display: true,
                    title: {
                        display: true,
                        text: '销售额 (￥)',
                        font: {
                            size: 12,
                            weight: '500'
                        }
                    },
                    beginAtZero: true,
                    grid: {
                        color: 'rgba(0, 0, 0, 0.05)',
                        drawBorder: false
                    },
                    ticks: {
                        callback: function(value) {
                            return '￥' + value;
                        },
                        font: {
                            size: 11
                        }
                    }
                }
            }
        }
    });
}

// 获取图表标题
function getChartTitle(period) {
    if (period === 'week') {
        return '最近1周销售额趋势';
    } else if (period === 'month') {
        return '最近1月销售额趋势';
    } else {
        return '自定义时间范围销售额趋势';
    }
}

function parseOrderAmount(order) {
    const amountCandidates = [
        order?.amount,
        order?.total_amount,
        order?.order_amount,
        order?.pay_amount,
        order?.price
    ];

    for (const amount of amountCandidates) {
        if (amount === undefined || amount === null || amount === '') continue;
        const normalized = String(amount).replace(/[^\d.-]/g, '');
        if (!normalized || normalized === '-' || normalized === '.' || normalized === '-.') {
            continue;
        }
        const numericAmount = parseFloat(normalized);
        if (!Number.isNaN(numericAmount)) {
            return numericAmount;
        }
    }

    return null;
}

function formatOrderAmountDisplay(rawAmount) {
    if (rawAmount === undefined || rawAmount === null) {
        return '-';
    }

    const amountText = String(rawAmount).trim();
    if (!amountText) {
        return '-';
    }

    // 已包含货币符号时直接展示，避免重复拼接
    if (/[¥￥$]/.test(amountText)) {
        return amountText;
    }

    return `¥${amountText}`;
}

function normalizeOrderStatus(status) {
    const value = String(status || '').toLowerCase();
    const aliasMap = {
        success: 'completed',
        finished: 'completed',
        pending_delivery: 'pending_ship',
        partial_success: 'partial_success',
        partial_pending_finalize: 'partial_pending_finalize',
        delivered: 'shipped',
        closed: 'cancelled',
        refunded: 'cancelled',
        canceled: 'cancelled'
    };
    return aliasMap[value] || value || 'unknown';
}

function isCompletedOrder(normalizedStatus) {
    return normalizedStatus === 'completed';
}

function isSalesEligibleOrder(normalizedStatus) {
    const salesEligibleStatuses = ['pending_ship', 'partial_success', 'partial_pending_finalize', 'shipped', 'completed'];
    return salesEligibleStatuses.includes(normalizedStatus);
}

function isCompletionEligibleOrder(normalizedStatus) {
    const completionEligibleStatuses = ['pending_ship', 'partial_success', 'partial_pending_finalize', 'shipped', 'completed', 'cancelled', 'refunding', 'refund_cancelled'];
    return completionEligibleStatuses.includes(normalizedStatus);
}

function parseUtcDateTime(dateString) {
    if (!dateString) return null;

    if (dateString instanceof Date) {
        return Number.isNaN(dateString.getTime()) ? null : dateString;
    }

    const raw = String(dateString).trim();
    if (!raw) return null;

    const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T');
    const hasTimezone = /([zZ]|[+-]\d{2}:\d{2})$/.test(normalized);
    const parsed = new Date(hasTimezone ? normalized : `${normalized}Z`);

    return Number.isNaN(parsed.getTime()) ? null : parsed;
}

const beijingMinuteFormatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    hourCycle: 'h23'
});

const beijingDateFormatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit'
});

const beijingSecondFormatter = new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    hourCycle: 'h23'
});

function formatBeijingDateTime(dateString) {
    const date = parseUtcDateTime(dateString);
    if (!date) return '--';

    const parts = {};
    beijingMinuteFormatter.formatToParts(date).forEach(part => {
        if (part.type !== 'literal') {
            parts[part.type] = part.value;
        }
    });

    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

function formatBeijingDateTimeWithSeconds(dateInput) {
    const date = parseUtcDateTime(dateInput);
    if (!date) return '--';

    const parts = {};
    beijingSecondFormatter.formatToParts(date).forEach(part => {
        if (part.type !== 'literal') {
            parts[part.type] = part.value;
        }
    });

    return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}:${parts.second}`;
}

function getBeijingDateKey(dateInput) {
    const date = parseUtcDateTime(dateInput);
    if (!date) return '';

    const parts = {};
    beijingDateFormatter.formatToParts(date).forEach(part => {
        if (part.type !== 'literal') {
            parts[part.type] = part.value;
        }
    });

    return `${parts.year}-${parts.month}-${parts.day}`;
}

function getEffectiveOrderSalesTime(order) {
    const platformPaidAt = String(order?.platform_paid_at || '').trim();
    if (platformPaidAt) return platformPaidAt;

    const platformCreatedAt = String(order?.platform_created_at || '').trim();
    if (platformCreatedAt) return platformCreatedAt;

    const createdAt = String(order?.created_at || '').trim();
    return createdAt || null;
}

function formatAboutRuntimeTime(displayValue, rawTimestamp) {
    const displayText = typeof displayValue === 'string' ? displayValue.trim() : '';
    if (displayText) {
        if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$/.test(displayText)) {
            return displayText.replace('T', ' ');
        }

        const normalizedDisplay = formatBeijingDateTimeWithSeconds(displayText);
        if (normalizedDisplay !== '--') {
            return normalizedDisplay;
        }

        return displayText;
    }

    const numericTimestamp = Number(rawTimestamp);
    if (!Number.isFinite(numericTimestamp) || numericTimestamp <= 0) {
        return '暂无记录';
    }

    const millis = numericTimestamp > 1e12 ? numericTimestamp : numericTimestamp * 1000;
    return formatBeijingDateTimeWithSeconds(new Date(millis));
}

function isTodayOrder(createdAt) {
    const orderDateKey = getBeijingDateKey(createdAt);
    if (!orderDateKey) return false;

    return orderDateKey === getBeijingDateKey(new Date());
}

function updateDashboardOrderMetrics(metrics) {
    const totalOrdersEl = document.getElementById('dashboardOrderTotal');
    const salesAmountEl = document.getElementById('dashboardSalesAmount');
    const completionRateEl = document.getElementById('dashboardCompletionRate');
    const todayOrdersEl = document.getElementById('dashboardTodayOrders');

    if (totalOrdersEl) {
        totalOrdersEl.textContent = metrics.totalOrders;
    }

    if (salesAmountEl) {
        salesAmountEl.textContent = `￥${metrics.totalSalesAmount.toLocaleString('zh-CN', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        })}`;
    }

    if (completionRateEl) {
        completionRateEl.textContent = `${metrics.completionRate.toFixed(1)}%`;
    }

    if (todayOrdersEl) {
        todayOrdersEl.textContent = metrics.todayOrders;
    }
}

// 更新仪表盘统计数据
function openAccountManagement(accountId) {
    pendingAccountManagementFocusId = accountId || '';
    const accountsSection = document.getElementById('accounts-section');
    if (accountsSection && accountsSection.classList.contains('active')) {
        loadCookies();
        return;
    }
    showSection('accounts');
}

function focusPendingAccountManagementRow() {
    if (!pendingAccountManagementFocusId) {
        return;
    }

    const rows = document.querySelectorAll('#cookieTable tbody tr[data-account-id]');
    const targetRow = Array.from(rows).find(row => row.dataset.accountId === pendingAccountManagementFocusId);
    if (!targetRow) {
        return;
    }

    pendingAccountManagementFocusId = '';
    targetRow.classList.add('dashboard-account-focus');
    targetRow.scrollIntoView({ behavior: 'smooth', block: 'center' });
    window.setTimeout(() => targetRow.classList.remove('dashboard-account-focus'), 2200);
}

async function loadDashboardDeliveryLogs() {
    const tbody = document.getElementById('dashboardDeliveryLogsList');
    if (!tbody) return;

    try {
        const response = await fetch(`${apiBase}/delivery-logs/recent?limit=20`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const data = await response.json();
        const logs = Array.isArray(data.logs) ? data.logs : [];
        renderDashboardDeliveryLogs(logs);
    } catch (error) {
        console.error('加载仪表盘发货日志失败:', error);
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="text-center text-muted py-4">
                    <i class="bi bi-exclamation-triangle fs-4 d-block mb-2"></i>
                    发货日志加载失败
                </td>
            </tr>
        `;
    }
}

function renderDashboardDeliveryLogs(logs) {
    const tbody = document.getElementById('dashboardDeliveryLogsList');
    if (!tbody) return;

    tbody.innerHTML = '';

    if (!logs.length) {
        tbody.innerHTML = `
            <tr>
                <td colspan="8" class="text-center text-muted py-4">
                    <i class="bi bi-inbox fs-1 d-block mb-2"></i>
                    暂无发货日志
                </td>
            </tr>
        `;
        return;
    }

    logs.forEach(log => {
        const normalizedStatus = String(log.status || '').toLowerCase();
        const isSuccess = normalizedStatus === 'success';
        const isSkipped = normalizedStatus === 'skipped';
        const statusBadge = isSuccess
            ? '<span class="badge bg-success">成功</span>'
            : (isSkipped
                ? '<span class="badge bg-secondary">已跳过</span>'
                : '<span class="badge bg-danger">失败</span>');

        const matchModeLabelMap = {
            no_spec_match: '无规格',
            one_spec_exact: '一组规格',
            one_spec_fallback_no_spec: '单规兜底',
            two_spec_exact: '两组规格',
            blocked_no_rule: '无规则',
            blocked_no_spec_parsed: '缺少规格',
            blocked_multiple_no_spec_rules: '多规则阻断',
            blocked_rule_mode_mismatch: '模式不一致'
        };

        const specModeLabelMap = {
            no_spec: '无规格',
            one_spec: '一组规格',
            two_spec: '两组规格',
            spec_enabled: '已开规格'
        };

        function buildBadge(text, className) {
            return `<span class="badge ${className}">${escapeHtml(text)}</span>`;
        }

        let matchBadge = buildBadge(matchModeLabelMap[log.match_mode] || (log.match_mode || '未知'), 'bg-secondary');
        if (log.match_mode === 'one_spec_exact' || log.match_mode === 'two_spec_exact') {
            matchBadge = buildBadge(matchModeLabelMap[log.match_mode], 'bg-primary');
        } else if (log.match_mode === 'one_spec_fallback_no_spec') {
            matchBadge = buildBadge(matchModeLabelMap[log.match_mode], 'bg-info text-dark');
        } else if (log.match_mode === 'no_spec_match') {
            matchBadge = buildBadge(matchModeLabelMap[log.match_mode], 'bg-warning text-dark');
        } else if (String(log.match_mode || '').startsWith('blocked_')) {
            matchBadge = buildBadge(matchModeLabelMap[log.match_mode] || log.match_mode, 'bg-danger');
        }

        const specModes = [log.order_spec_mode, log.rule_spec_mode, log.item_config_mode].filter(Boolean);
        const uniqueSpecLabels = [...new Set(specModes.map(mode => specModeLabelMap[mode] || mode))];
        const hasEnabledSpecMode = specModes.some(mode => ['one_spec', 'two_spec', 'spec_enabled'].includes(mode));
        const hasNoSpecMode = specModes.some(mode => mode === 'no_spec');
        let specModeTitle = '';
        if (log.match_mode === 'blocked_rule_mode_mismatch') {
            specModeTitle = uniqueSpecLabels.join(' / ') || '规格不一致';
        } else if (log.match_mode === 'two_spec_exact' || specModes.includes('two_spec')) {
            specModeTitle = '两组规格';
        } else if (log.match_mode === 'one_spec_exact' || log.match_mode === 'one_spec_fallback_no_spec' || specModes.includes('one_spec')) {
            specModeTitle = '一组规格';
        } else if (log.match_mode === 'no_spec_match' || hasNoSpecMode) {
            specModeTitle = '无规格';
        } else if (specModes.includes('spec_enabled')) {
            specModeTitle = '已开规格';
        }

        let specSummary = '<span class="text-muted">-</span>';
        if (log.match_mode === 'blocked_rule_mode_mismatch') {
            specSummary = `<span title="${escapeHtml(specModeTitle || '规格模式不一致')}">${buildBadge('规格不一致', 'bg-warning text-dark')}</span>`;
        } else if (hasEnabledSpecMode || ['one_spec_exact', 'one_spec_fallback_no_spec', 'two_spec_exact'].includes(log.match_mode)) {
            specSummary = `<span title="${escapeHtml(specModeTitle || '已开规格')}">${buildBadge('已开规格', 'bg-info text-dark')}</span>`;
        } else if (hasNoSpecMode || log.match_mode === 'no_spec_match') {
            specSummary = `<span title="${escapeHtml(specModeTitle || '未开规格')}">${buildBadge('未开规格', 'bg-secondary')}</span>`;
        }

        const ruleText = log.rule_keyword
            ? `<div class="dashboard-delivery-rule" title="${escapeHtml(log.rule_keyword)}">${escapeHtml(log.rule_keyword)}</div>`
            : '<span class="text-muted">未命中规则</span>';

        const channelText = log.channel === 'manual' ? '手动' : '自动';
        const channelBadgeClass = log.channel === 'manual' ? 'dashboard-delivery-channel-manual' : 'dashboard-delivery-channel-auto';
        const reasonText = isSuccess
            ? (log.reason || '发货成功')
            : (isSkipped
                ? (log.reason || '已跳过重复发货')
                : (log.reason || '未知失败原因'));

        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td class="text-nowrap"><small>${escapeHtml(formatDateTime(log.created_at || ''))}</small></td>
            <td class="text-nowrap">${escapeHtml(log.order_id || '-')}</td>
            <td>${statusBadge}</td>
            <td>${ruleText}</td>
            <td>${matchBadge}</td>
            <td>${specSummary}</td>
            <td>
                <span class="badge ${channelBadgeClass}">${escapeHtml(channelText)}</span>
            </td>
            <td class="dashboard-delivery-reason" title="${escapeHtml(reasonText)}">${escapeHtml(reasonText)}</td>
        `;
        tbody.appendChild(tr);
    });
}

// 获取账号关键词数量（带缓存）- 包含普通关键词和商品关键词
async function getAccountKeywordCount(accountId) {
    const now = Date.now();

    // 检查缓存
    if (accountKeywordCache[accountId] && (now - cacheTimestamp) < CACHE_DURATION) {
    return accountKeywordCache[accountId];
    }

    try {
    const response = await fetch(`${apiBase}/keywords/${accountId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const keywordsData = await response.json();
        // 现在API返回的是包含普通关键词和商品关键词的完整列表
        const count = keywordsData.length;

        // 更新缓存
        accountKeywordCache[accountId] = count;
        cacheTimestamp = now;

        return count;
    } else {
        return 0;
    }
    } catch (error) {
    console.error(`获取账号 ${accountId} 关键词失败:`, error);
    return 0;
    }
}

// 清除关键词缓存
function clearKeywordCache() {
    accountKeywordCache = {};
    cacheTimestamp = 0;
}

