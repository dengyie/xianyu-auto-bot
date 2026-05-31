// ================================
// 【日志管理菜单】相关功能
// ================================

window.autoRefreshInterval = null;
window.allLogs = [];
window.filteredLogs = [];

// 刷新日志
async function refreshLogs() {
    try {
        const logLinesElement = document.getElementById('logLines');
        if (!logLinesElement) {
            console.warn('logLines 元素不存在');
            showToast('页面元素缺失，请刷新页面', 'warning');
            return;
        }

        const lines = logLinesElement.value;

        const response = await fetch(`${apiBase}/logs?lines=${lines}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            window.allLogs = data.logs || [];
            window.filteredLogs = window.allLogs; // 不再过滤，直接显示所有日志
            displayLogs();
            updateLogStats();
            showToast('日志已刷新', 'success');
        } else {
            throw new Error(`HTTP ${response.status}`);
        }
    } catch (error) {
        console.error('刷新日志失败:', error);
        showToast(`刷新日志失败: ${error.message}`, 'danger');
    }
}



// 显示日志
function displayLogs() {
    const container = document.getElementById('logContainer');

    // 检查容器是否存在
    if (!container) {
        // 只在特定页面显示警告，避免在其他页面产生无用的警告
        const currentPath = window.location.pathname;
        if (currentPath.includes('log') || currentPath.includes('admin')) {
            console.warn('logContainer 元素不存在，无法显示日志');
        }
        return;
    }

    if (!window.filteredLogs || window.filteredLogs.length === 0) {
    container.innerHTML = `
        <div class="text-center p-4 text-muted">
        <i class="bi bi-file-text fs-1"></i>
        <p class="mt-2">暂无日志数据</p>
        </div>
    `;
    return;
    }

    const logsHtml = window.filteredLogs.map(log => {
    const timestamp = formatLogTimestamp(log.timestamp);
    const levelClass = log.level || 'INFO';

    return `
        <div class="log-entry ${levelClass}">
        <span class="log-timestamp">${timestamp}</span>
        <span class="log-level">[${log.level}]</span>
        <span class="log-source">${log.source}:</span>
        <span class="log-message">${escapeHtml(log.message)}</span>
        </div>
    `;
    }).join('');

    container.innerHTML = logsHtml;

    // 滚动到底部
    container.scrollTop = container.scrollHeight;
}

// 格式化日志时间戳
function formatLogTimestamp(timestamp) {
    if (!timestamp) return '';
    const date = new Date(timestamp);
    return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    fractionalSecondDigits: 3
    });
}

// 更新日志统计信息
function updateLogStats() {
    const logCountElement = document.getElementById('logCount');
    const lastUpdateElement = document.getElementById('lastUpdate');

    if (logCountElement) {
        const count = window.filteredLogs ? window.filteredLogs.length : 0;
        logCountElement.textContent = `${count} 条日志`;
    }

    if (lastUpdateElement) {
        lastUpdateElement.textContent = new Date().toLocaleTimeString('zh-CN');
    }
}

// 清空日志显示
function clearLogsDisplay() {
    window.allLogs = [];
    window.filteredLogs = [];
    document.getElementById('logContainer').innerHTML = `
    <div class="text-center p-4 text-muted">
        <i class="bi bi-file-text fs-1"></i>
        <p class="mt-2">日志显示已清空</p>
    </div>
    `;
    updateLogStats();
    showToast('日志显示已清空', 'info');
}

// 切换自动刷新
function toggleAutoRefresh() {
    const button = document.querySelector('#autoRefreshText');
    const icon = button.previousElementSibling;

    if (window.autoRefreshInterval) {
    // 停止自动刷新
    clearInterval(window.autoRefreshInterval);
    window.autoRefreshInterval = null;
    button.textContent = '开启自动刷新';
    icon.className = 'bi bi-play-circle me-1';
    showToast('自动刷新已停止', 'info');
    } else {
    // 开启自动刷新
    window.autoRefreshInterval = setInterval(refreshLogs, 5000); // 每5秒刷新一次
    button.textContent = '停止自动刷新';
    icon.className = 'bi bi-pause-circle me-1';
    showToast('自动刷新已开启（每5秒）', 'success');

    // 立即刷新一次
    refreshLogs();
    }
}

// 清空服务器日志
async function clearLogsServer() {
    if (!confirm('确定要清空服务器端的所有日志吗？此操作不可恢复！')) {
    return;
    }

    try {
    const response = await fetch(`${apiBase}/logs/clear`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        if (data.success) {
        window.allLogs = [];
        window.filteredLogs = [];
        displayLogs();
        updateLogStats();
        showToast('服务器日志已清空', 'success');
        } else {
        showToast(data.message || '清空失败', 'danger');
        }
    } else {
        throw new Error(`HTTP ${response.status}`);
    }
    } catch (error) {
    console.error('清空服务器日志失败:', error);
    showToast('清空服务器日志失败', 'danger');
    }
}

// 显示日志统计信息
async function showLogStats() {
    try {
    const response = await fetch(`${apiBase}/logs/stats`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        if (data.success) {
        const stats = data.stats;

        let statsHtml = `
            <div class="row">
            <div class="col-md-6">
                <h6>总体统计</h6>
                <ul class="list-unstyled">
                <li>总日志数: <strong>${stats.total_logs}</strong></li>
                <li>最大容量: <strong>${stats.max_capacity}</strong></li>
                <li>使用率: <strong>${((stats.total_logs / stats.max_capacity) * 100).toFixed(1)}%</strong></li>
                </ul>
            </div>
            <div class="col-md-6">
                <h6>级别分布</h6>
                <ul class="list-unstyled">
        `;

        for (const [level, count] of Object.entries(stats.level_counts || {})) {
            const percentage = ((count / stats.total_logs) * 100).toFixed(1);
            statsHtml += `<li>${level}: <strong>${count}</strong> (${percentage}%)</li>`;
        }

        statsHtml += `
                </ul>
            </div>
            </div>
            <div class="row mt-3">
            <div class="col-12">
                <h6>来源分布</h6>
                <div class="row">
        `;

        const sources = Object.entries(stats.source_counts || {});
        sources.forEach(([source, count], index) => {
            if (index % 2 === 0) statsHtml += '<div class="col-md-6"><ul class="list-unstyled">';
            const percentage = ((count / stats.total_logs) * 100).toFixed(1);
            statsHtml += `<li>${source}: <strong>${count}</strong> (${percentage}%)</li>`;
            if (index % 2 === 1 || index === sources.length - 1) statsHtml += '</ul></div>';
        });

        statsHtml += `
                </div>
            </div>
            </div>
        `;

        // 显示模态框
        const modalHtml = `
            <div class="modal fade" id="logStatsModal" tabindex="-1">
            <div class="modal-dialog modal-lg">
                <div class="modal-content">
                <div class="modal-header">
                    <h5 class="modal-title">日志统计信息</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                </div>
                <div class="modal-body">
                    ${statsHtml}
                </div>
                <div class="modal-footer">
                    <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
                </div>
                </div>
            </div>
            </div>
        `;

        // 移除旧的模态框
        const oldModal = document.getElementById('logStatsModal');
        if (oldModal) oldModal.remove();

        // 添加新的模态框
        document.body.insertAdjacentHTML('beforeend', modalHtml);

        // 显示模态框
        const modal = new bootstrap.Modal(document.getElementById('logStatsModal'));
        modal.show();

        } else {
        showToast(data.message || '获取统计信息失败', 'danger');
        }
    } else {
        throw new Error(`HTTP ${response.status}`);
    }
    } catch (error) {
    console.error('获取日志统计失败:', error);
    showToast('获取日志统计失败', 'danger');
    }
}

// ================================
// 系统日志管理功能
// ================================
let logAutoRefreshInterval = null;
let currentLogLevel = '';

// 加载系统日志
async function loadSystemLogs() {
    const token = localStorage.getItem('auth_token');
    const lines = document.getElementById('logLines').value;
    const level = currentLogLevel;

    const loadingDiv = document.getElementById('loadingSystemLogs');
    const logContainer = document.getElementById('systemLogContainer');
    const noLogsDiv = document.getElementById('noSystemLogs');

    loadingDiv.style.display = 'block';
    logContainer.style.display = 'none';
    noLogsDiv.style.display = 'none';

    let url = `/admin/logs?lines=${lines}`;
    if (level) {
        url += `&level=${level}`;
    }

    try {
        const response = await fetch(url, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();
        loadingDiv.style.display = 'none';

        if (data.logs && data.logs.length > 0) {
            displaySystemLogs(data.logs);
            updateLogInfo(data);
            logContainer.style.display = 'block';
        } else {
            noLogsDiv.style.display = 'block';
        }

        // 更新最后更新时间
        document.getElementById('logLastUpdate').textContent =
            '最后更新: ' + new Date().toLocaleTimeString('zh-CN');
    } catch (error) {
        console.error('加载日志失败:', error);
        loadingDiv.style.display = 'none';
        noLogsDiv.style.display = 'block';
        showToast('加载日志失败', 'danger');
    }
}

// 显示系统日志
function displaySystemLogs(logs) {
    const logContainer = document.getElementById('systemLogContainer');
    logContainer.innerHTML = '';

    // 反转日志数组，让最新的日志显示在最上面
    const reversedLogs = [...logs].reverse();

    reversedLogs.forEach(log => {
        const logLine = document.createElement('div');
        logLine.className = 'log-entry';

        // 根据日志级别添加颜色类
        if (log.includes('| INFO |')) {
            logLine.classList.add('INFO');
        } else if (log.includes('| WARNING |')) {
            logLine.classList.add('WARNING');
        } else if (log.includes('| ERROR |')) {
            logLine.classList.add('ERROR');
        } else if (log.includes('| DEBUG |')) {
            logLine.classList.add('DEBUG');
        } else if (log.includes('| CRITICAL |')) {
            logLine.classList.add('CRITICAL');
        }

        logLine.textContent = log;
        logContainer.appendChild(logLine);
    });

    // 自动滚动到顶部（显示最新日志）
    scrollLogToTop();
}

// 更新日志信息
function updateLogInfo(data) {
    document.getElementById('logFileName').textContent = data.log_file || '-';
    document.getElementById('logDisplayLines').textContent = data.total_lines || '-';
}

// 按级别过滤日志
function filterLogsByLevel(level) {
    currentLogLevel = level;

    // 更新过滤按钮状态
    document.querySelectorAll('.filter-badge').forEach(badge => {
        badge.classList.remove('active');
    });
    document.querySelector(`[data-level="${level}"]`).classList.add('active');

    // 更新当前过滤显示
    const filterText = level ? level.toUpperCase() : '全部';
    document.getElementById('logCurrentFilter').textContent = filterText;

    // 重新加载日志
    loadSystemLogs();
}

// 切换日志自动刷新
function toggleLogAutoRefresh() {
    const autoRefresh = document.getElementById('autoRefreshLogs');
    const label = document.getElementById('autoRefreshLogLabel');
    const icon = document.getElementById('autoRefreshLogIcon');

    if (autoRefresh.checked) {
        // 开启自动刷新
        logAutoRefreshInterval = setInterval(loadSystemLogs, 5000); // 每5秒刷新
        label.textContent = '开启 (5s)';
        icon.style.display = 'inline';
        icon.classList.add('auto-refresh-indicator');
    } else {
        // 关闭自动刷新
        if (logAutoRefreshInterval) {
            clearInterval(logAutoRefreshInterval);
            logAutoRefreshInterval = null;
        }
        label.textContent = '关闭';
        icon.style.display = 'none';
        icon.classList.remove('auto-refresh-indicator');
    }
}

// 滚动到日志顶部
function scrollLogToTop() {
    const logContainer = document.getElementById('systemLogContainer');
    logContainer.scrollTop = 0;
}

// 滚动到日志底部
function scrollLogToBottom() {
    const logContainer = document.getElementById('systemLogContainer');
    logContainer.scrollTop = logContainer.scrollHeight;
}

// 打开日志导出模态框
function openLogExportModal() {
    const modalElement = document.getElementById('exportLogModal');
    if (!modalElement) {
        console.warn('未找到导出日志模态框元素');
        return;
    }

    resetLogFileModalState();
    const modal = new bootstrap.Modal(modalElement);
    modal.show();
    loadLogFileList();
}

function resetLogFileModalState() {
    const loading = document.getElementById('logFileLoading');
    const list = document.getElementById('logFileList');
    const empty = document.getElementById('logFileEmpty');
    const error = document.getElementById('logFileError');

    if (loading) loading.classList.remove('d-none');
    if (list) list.innerHTML = '';
    if (empty) empty.classList.add('d-none');
    if (error) {
        error.classList.add('d-none');
        error.textContent = '';
    }
}

async function loadLogFileList() {
    const token = localStorage.getItem('auth_token');
    const loading = document.getElementById('logFileLoading');
    const list = document.getElementById('logFileList');
    const empty = document.getElementById('logFileEmpty');
    const error = document.getElementById('logFileError');

    if (!loading || !list || !empty || !error) {
        console.warn('日志文件列表元素缺失');
        return;
    }

    loading.classList.remove('d-none');
    list.innerHTML = '';
    empty.classList.add('d-none');
    error.classList.add('d-none');
    error.textContent = '';

    try {
        const response = await fetch(`${apiBase}/admin/log-files`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        loading.classList.add('d-none');

        if (!response.ok) {
            const message = await response.text();
            error.classList.remove('d-none');
            error.textContent = `加载日志文件失败: ${message || response.status}`;
            return;
        }

        const data = await response.json();
        if (!data.success) {
            error.classList.remove('d-none');
            error.textContent = data.message || '加载日志文件失败';
            return;
        }

        const files = data.files || [];
        if (files.length === 0) {
            empty.classList.remove('d-none');
            return;
        }

        files.forEach(file => {
            const item = document.createElement('div');
            item.className = 'list-group-item d-flex justify-content-between align-items-start flex-wrap gap-3';

            const info = document.createElement('div');
            info.className = 'me-auto';

            const title = document.createElement('div');
            title.className = 'fw-semibold';
            title.textContent = file.name || '未知文件';

            const meta = document.createElement('div');
            meta.className = 'small text-muted';
            const sizeText = typeof file.size === 'number' ? formatFileSize(file.size) : '未知大小';
            const timeText = file.modified_at ? formatLogTimestamp(file.modified_at) : '-';
            meta.textContent = `大小: ${sizeText} · 更新时间: ${timeText}`;

            info.appendChild(title);
            info.appendChild(meta);

            const actions = document.createElement('div');
            actions.className = 'd-flex align-items-center gap-2';

            const downloadBtn = document.createElement('button');
            downloadBtn.type = 'button';
            downloadBtn.className = 'btn btn-sm btn-outline-primary';
            downloadBtn.innerHTML = '<i class="bi bi-download me-1"></i>下载';
            downloadBtn.onclick = () => downloadLogFile(file.name, downloadBtn);

            actions.appendChild(downloadBtn);

            item.appendChild(info);
            item.appendChild(actions);

            list.appendChild(item);
        });
    } catch (err) {
        console.error('加载日志文件失败:', err);
        loading.classList.add('d-none');
        error.classList.remove('d-none');
        error.textContent = '加载日志文件失败，请稍后重试';
    }
}

function refreshLogFileList() {
    resetLogFileModalState();
    loadLogFileList();
}

function formatFileSize(bytes) {
    if (bytes === 0) return '0 B';
    if (!Number.isFinite(bytes)) return '未知大小';

    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
    const size = bytes / Math.pow(1024, index);
    return `${size.toFixed(index === 0 ? 0 : 2)} ${units[index]}`;
}

function formatLogTimestamp(isoString) {
    const date = new Date(isoString);
    if (Number.isNaN(date.getTime())) {
        return '-';
    }
    return date.toLocaleString('zh-CN', { hour12: false });
}

async function downloadLogFile(fileName, buttonEl) {
    if (!fileName) {
        showToast('日志文件名无效', 'warning');
        return;
    }

    const token = localStorage.getItem('auth_token');
    if (!token) {
        showToast('请先登录后再导出日志', 'warning');
        return;
    }

    let originalHtml = '';
    if (buttonEl) {
        originalHtml = buttonEl.innerHTML;
        buttonEl.disabled = true;
        buttonEl.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>下载中...';
    }

    try {
        const response = await fetch(`${apiBase}/admin/logs/export?file=${encodeURIComponent(fileName)}`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (!response.ok) {
            const message = await response.text();
            showToast(`日志下载失败: ${message || response.status}`, 'danger');
            return;
        }

        let downloadName = fileName;
        const contentDisposition = response.headers.get('content-disposition');
        if (contentDisposition) {
            const match = contentDisposition.match(/filename="?([^"]+)"?/i);
            if (match && match[1]) {
                downloadName = decodeURIComponent(match[1]);
            }
        }

        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = downloadName;
        document.body.appendChild(anchor);
        anchor.click();
        document.body.removeChild(anchor);
        window.URL.revokeObjectURL(url);

        showToast('日志下载成功', 'success');
    } catch (error) {
        console.error('下载日志文件失败:', error);
        showToast('下载日志文件失败，请稍后重试', 'danger');
    } finally {
        if (buttonEl) {
            buttonEl.disabled = false;
            buttonEl.innerHTML = originalHtml || '<i class="bi bi-download me-1"></i>下载';
        }
    }
}

// ================================
// 风控日志管理功能
// ================================
let currentRiskLogStatus = '';
let currentRiskLogOffset = 0;
const riskLogLimit = 100;
let currentRiskSliderStatsRequestId = 0;

function getRiskSliderStatsRange() {
    const activeButton = document.querySelector('#riskSliderRangeFilter .risk-slider-range-btn.is-active');
    return activeButton?.dataset.range || 'all';
}

function getRiskSliderStatsRangeLabel(rangeValue = 'all') {
    switch (String(rangeValue || '').trim().toLowerCase()) {
        case 'today':
            return '当日';
        case '7d':
            return '近 7 天';
        default:
            return '所有';
    }
}

function onRiskSliderRangeChange(rangeValue = 'all') {
    document.querySelectorAll('#riskSliderRangeFilter .risk-slider-range-btn').forEach((button) => {
        button.classList.toggle('is-active', button.dataset.range === rangeValue);
    });
    const cookieId = document.getElementById('riskLogCookieFilter')?.value || '';
    loadRiskControlSliderStats(cookieId);
}

function setRiskControlSliderStatsLoading(scopeLabel = '全部账号') {
    const scopeElement = document.getElementById('riskSliderScope');
    const successRateElement = document.getElementById('riskSliderSuccessRate');
    const attemptCountElement = document.getElementById('riskSliderAttemptCount');
    const successCountElement = document.getElementById('riskSliderSuccessCount');
    const failureCountElement = document.getElementById('riskSliderFailureCount');
    const recentSuccessElement = document.getElementById('riskSliderRecentSuccess');
    const recentFailureElement = document.getElementById('riskSliderRecentFailure');

    if (scopeElement) scopeElement.textContent = scopeLabel;
    if (successRateElement) successRateElement.textContent = '--';
    if (attemptCountElement) attemptCountElement.textContent = '统计中...';
    if (successCountElement) successCountElement.textContent = '--';
    if (failureCountElement) failureCountElement.textContent = '--';
    if (recentSuccessElement) recentSuccessElement.textContent = '--';
    if (recentFailureElement) recentFailureElement.textContent = '--';
}

function renderRiskControlSliderStats(stats = {}) {
    const scopeElement = document.getElementById('riskSliderScope');
    const successRateElement = document.getElementById('riskSliderSuccessRate');
    const attemptCountElement = document.getElementById('riskSliderAttemptCount');
    const successCountElement = document.getElementById('riskSliderSuccessCount');
    const failureCountElement = document.getElementById('riskSliderFailureCount');
    const recentSuccessElement = document.getElementById('riskSliderRecentSuccess');
    const recentFailureElement = document.getElementById('riskSliderRecentFailure');

    const totalSessions = Number(stats.total_sessions ?? stats.total_attempts ?? 0);
    const successCount = Number(stats.success_count || 0);
    const failureCount = Number(stats.failure_count || 0);
    const processingCount = Number(stats.processing_count || 0);
    const completedSessions = Number(stats.completed_sessions || (successCount + failureCount));
    const successRate = Number.isFinite(Number(stats.success_rate)) ? Number(stats.success_rate).toFixed(1) : '0.0';
    const hasData = Boolean(stats.has_data || totalSessions > 0);
    const recentSuccessText = formatBeijingDateTime(stats.recent_success);
    const recentFailureText = formatBeijingDateTime(stats.recent_failure);
    const rangeLabel = stats.range_label || getRiskSliderStatsRangeLabel(stats.selected_range || getRiskSliderStatsRange());
    let attemptSummary = stats.summary_text || '暂无滑块验证记录';

    if (hasData) {
        if (rangeLabel === '所有') {
            attemptSummary = `累计滑块相关记录 ${totalSessions} 次`;
        } else {
            attemptSummary = `${rangeLabel}滑块相关记录 ${totalSessions} 次`;
        }
        if (processingCount > 0) {
            attemptSummary += `，进行中 ${processingCount} 次`;
        }
    }

    if (scopeElement) scopeElement.textContent = stats.scope_label || '全部账号';
    if (successRateElement) successRateElement.textContent = completedSessions > 0 ? `${successRate}%` : '--';
    if (attemptCountElement) attemptCountElement.textContent = attemptSummary;
    if (successCountElement) successCountElement.textContent = String(successCount);
    if (failureCountElement) failureCountElement.textContent = String(failureCount);
    if (recentSuccessElement) recentSuccessElement.textContent = recentSuccessText;
    if (recentFailureElement) recentFailureElement.textContent = recentFailureText;
}

async function loadRiskControlSliderStats(cookieId = '') {
    const token = localStorage.getItem('auth_token');
    const scopeLabel = cookieId || '全部账号';
    const rangeValue = getRiskSliderStatsRange();
    const rangeLabel = getRiskSliderStatsRangeLabel(rangeValue);
    const requestId = ++currentRiskSliderStatsRequestId;

    setRiskControlSliderStatsLoading(scopeLabel);

    try {
        const params = new URLSearchParams();
        if (cookieId) {
            params.set('cookie_id', cookieId);
        }
        params.set('range_key', rangeValue);
        const url = `/admin/slider-verification-stats?${params.toString()}`;

        const response = await fetch(url, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();
        if (requestId !== currentRiskSliderStatsRequestId) {
            return;
        }

        if (response.ok && data.success) {
            renderRiskControlSliderStats(data.data || {});
            return;
        }

        renderRiskControlSliderStats({
            scope_label: scopeLabel,
            total_sessions: 0,
            success_count: 0,
            failure_count: 0,
            processing_count: 0,
            completed_sessions: 0,
            success_rate: 0,
            recent_success: '--',
            recent_failure: '--',
            summary_text: rangeValue === 'all' ? '暂无滑块验证记录' : `${rangeLabel}暂无滑块验证记录`,
            selected_range: rangeValue,
            range_label: rangeLabel,
            has_data: false
        });
    } catch (error) {
        console.error('加载滑块验证统计失败:', error);
        if (requestId !== currentRiskSliderStatsRequestId) {
            return;
        }
        renderRiskControlSliderStats({
            scope_label: scopeLabel,
            total_sessions: 0,
            success_count: 0,
            failure_count: 0,
            processing_count: 0,
            completed_sessions: 0,
            success_rate: 0,
            recent_success: '--',
            recent_failure: '--',
            summary_text: rangeValue === 'all' ? '暂无滑块验证记录' : `${rangeLabel}暂无滑块验证记录`,
            selected_range: rangeValue,
            range_label: rangeLabel,
            has_data: false
        });
    }
}

function getRiskLogFilters() {
    return {
        cookieId: document.getElementById('riskLogCookieFilter')?.value || '',
        eventType: document.getElementById('riskLogEventTypeFilter')?.value || '',
        triggerScene: document.getElementById('riskLogTriggerSceneFilter')?.value || '',
        dateFrom: document.getElementById('riskLogDateFrom')?.value || '',
        dateTo: document.getElementById('riskLogDateTo')?.value || '',
        sessionId: (document.getElementById('riskLogSessionFilter')?.value || '').trim(),
        processingStatus: currentRiskLogStatus,
        limit: parseInt(document.getElementById('riskLogLimit')?.value, 10) || 100,
    };
}

function hasActiveRiskLogFilters(filters = {}) {
    return Boolean(
        filters.cookieId ||
        filters.processingStatus ||
        filters.eventType ||
        filters.triggerScene ||
        filters.dateFrom ||
        filters.dateTo ||
        filters.sessionId
    );
}

async function fetchRiskControlLogsPage(token, {
    cookieId = '',
    processingStatus = '',
    eventType = '',
    triggerScene = '',
    dateFrom = '',
    dateTo = '',
    sessionId = '',
    resultCode = '',
    limit = 100,
    offset = 0,
} = {}) {
    const params = new URLSearchParams({
        limit: String(limit),
        offset: String(offset),
    });

    if (cookieId) params.set('cookie_id', cookieId);
    if (processingStatus) params.set('processing_status', processingStatus);
    if (eventType) params.set('event_type', eventType);
    if (triggerScene) params.set('trigger_scene', triggerScene);
    if (dateFrom) params.set('date_from', dateFrom);
    if (dateTo) params.set('date_to', dateTo);
    if (sessionId) params.set('session_id', sessionId);
    if (resultCode) params.set('result_code', resultCode);

    const response = await fetch(`/admin/risk-control-logs?${params.toString()}`, {
        headers: {
            'Authorization': `Bearer ${token}`
        }
    });

    return response.json();
}

function needsClientSideRiskLogFilter(logs, processingStatus) {
    if (!processingStatus || !Array.isArray(logs) || logs.length === 0) {
        return false;
    }

    return logs.some(log => String(log.processing_status || '') !== processingStatus);
}

async function fetchRiskControlLogsWithClientFilter(token, {
    cookieId = '',
    processingStatus = '',
    eventType = '',
    triggerScene = '',
    dateFrom = '',
    dateTo = '',
    sessionId = '',
    resultCode = '',
    limit = 100,
    offset = 0,
} = {}) {
    const batchSize = 500;
    let fetchOffset = 0;
    let total = 0;
    const matchedLogs = [];

    while (true) {
        const pageData = await fetchRiskControlLogsPage(token, {
            cookieId,
            eventType,
            triggerScene,
            dateFrom,
            dateTo,
            sessionId,
            resultCode,
            limit: batchSize,
            offset: fetchOffset
        });

        const pageLogs = Array.isArray(pageData.data) ? pageData.data : [];
        total = pageData.total || total || pageLogs.length;

        matchedLogs.push(...pageLogs.filter(log => String(log.processing_status || '') === processingStatus));

        fetchOffset += pageLogs.length;
        if (pageLogs.length === 0 || fetchOffset >= total) {
            break;
        }
    }

    return {
        success: true,
        data: matchedLogs.slice(offset, offset + limit),
        total: matchedLogs.length,
        limit,
        offset,
        filter_mode: 'client'
    };
}

// 加载风控日志
async function loadRiskControlLogs(offset = 0) {
    const token = localStorage.getItem('auth_token');
    const filters = getRiskLogFilters();
    const cookieId = filters.cookieId;
    const limit = filters.limit;
    currentRiskLogOffset = offset;

    loadRiskControlSliderStats(cookieId);

    const loadingDiv = document.getElementById('loadingRiskLogs');
    const logContainer = document.getElementById('riskLogContainer');
    const noLogsDiv = document.getElementById('noRiskLogs');

    loadingDiv.style.display = 'block';
    logContainer.style.display = 'none';
    noLogsDiv.style.display = 'none';

    try {
        let data = await fetchRiskControlLogsPage(token, {
            ...filters,
            offset,
        });

        if (needsClientSideRiskLogFilter(data.data, filters.processingStatus)) {
            data = await fetchRiskControlLogsWithClientFilter(token, {
                ...filters,
                offset,
            });
        }

        loadingDiv.style.display = 'none';

        if (data.success && data.data && data.data.length > 0) {
            displayRiskControlLogs(data.data);
            updateRiskLogInfo(data);
            updateRiskLogPagination(data);
            logContainer.style.display = 'block';
        } else {
            noLogsDiv.style.display = 'block';
            updateRiskLogInfo({total: 0, data: []});
            updateRiskLogPagination({total: 0});
        }

    } catch (error) {
        console.error('加载风控日志失败:', error);
        loadingDiv.style.display = 'none';
        noLogsDiv.style.display = 'block';
        updateRiskLogPagination({total: 0});
        const countElement = document.getElementById('riskLogCount');
        const paginationInfo = document.getElementById('riskLogPaginationInfo');
        if (countElement) {
            countElement.textContent = '加载失败';
        }
        if (paginationInfo) {
            paginationInfo.textContent = '风控日志加载失败，请重试';
        }
        showToast('加载风控日志失败', 'danger');
    }
}

// 显示风控日志
function getRiskEventCategoryMeta(eventType) {
    const normalizedType = String(eventType || '').trim();

    if (normalizedType === 'unknown') {
        return {
            label: '身份验证',
            className: 'risk-event-category-trigger'
        };
    }

    if (['slider_captcha', 'face_verify', 'sms_verify', 'qr_verify', 'token_expired'].includes(normalizedType)) {
        return {
            label: '风控触发',
            className: 'risk-event-category-trigger'
        };
    }

    if (normalizedType === 'cookie_refresh') {
        return {
            label: 'Cookie刷新',
            className: 'risk-event-category-refresh'
        };
    }

    if (normalizedType === 'password_error') {
        return {
            label: '登录异常',
            className: 'risk-event-category-error'
        };
    }

    return {
        label: normalizedType || '-',
        className: 'risk-event-category-neutral'
    };
}

function getRiskTriggerSceneLabel(triggerScene) {
    const normalizedScene = String(triggerScene || '').trim();
    const sceneLabels = {
        token_refresh: 'Token刷新',
        auto_cookie_refresh: '自动Cookie刷新',
        manual_password_refresh: '手动账密刷新',
        manual_qr_refresh: '手动扫码刷新',
        password_login: '密码登录',
        qr_login: '扫码登录'
    };

    return sceneLabels[normalizedScene] || normalizedScene || '-';
}

function formatRiskDuration(durationMs) {
    const value = Number(durationMs);
    if (!Number.isFinite(value) || value <= 0) {
        return '--';
    }
    if (value < 1000) {
        return `${Math.round(value)} ms`;
    }
    if (value < 60000) {
        return `${(value / 1000).toFixed(1)} s`;
    }
    return `${(value / 60000).toFixed(1)} min`;
}

function formatRiskSessionId(sessionId, sessionDisplay = '') {
    const text = String(sessionId || '').trim();
    if (text) {
        return text;
    }
    const fallback = String(sessionDisplay || '').trim();
    return fallback || '--';
}

function renderRiskLogSummaryCell(log) {
    const descriptionText = log.event_description_display || log.event_description || '-';
    const description = escapeHtml(descriptionText);
    const resultCode = log.result_code
        ? `<div class="small text-muted mt-1">结果代码: ${escapeHtml(log.result_code)}</div>`
        : '';
    return `
        <div class="risk-log-summary-cell" title="${description}">${description}</div>
        ${resultCode}
    `;
}

function renderRiskLogOutcomeCell(log) {
    const processingResultText = log.processing_result_display || log.processing_result || '';
    const errorMessageText = log.error_message_display || log.error_message || '';
    const processingResult = processingResultText
        ? `<div class="text-wrap">${escapeHtml(processingResultText)}</div>`
        : '';
    const errorMessage = errorMessageText
        ? `<div class="small text-danger mt-1">${escapeHtml(errorMessageText)}</div>`
        : '';
    const fallbackText = !processingResult && !errorMessage
        ? '<span class="text-muted">-</span>'
        : '';
    return `
        <div class="risk-log-outcome-cell">
            ${processingResult}
            ${errorMessage}
            ${fallbackText}
        </div>
    `;
}

function displayRiskControlLogs(logs) {
    const tableBody = document.getElementById('riskLogTableBody');
    tableBody.innerHTML = '';

    logs.forEach(log => {
        const row = document.createElement('tr');

        // 格式化时间
        const createdAt = formatDateTime(log.created_at);

        // 状态标签
        let statusBadge = '';
        switch(log.processing_status) {
            case 'processing':
                statusBadge = '<span class="badge bg-warning">处理中</span>';
                break;
            case 'success':
                statusBadge = '<span class="badge bg-success">成功</span>';
                break;
            case 'failed':
                statusBadge = '<span class="badge bg-danger">失败</span>';
                break;
            default:
                statusBadge = '<span class="badge bg-secondary">未知</span>';
        }

        const eventCategory = getRiskEventCategoryMeta(log.event_type);
        const eventCategoryBadge = `
            <span
                class="badge risk-event-category-badge ${eventCategory.className}"
                title="原始类型: ${escapeHtml(log.event_type || '-')}"
            >
                ${escapeHtml(eventCategory.label)}
            </span>
        `;
        const triggerSceneLabel = getRiskTriggerSceneLabel(log.trigger_scene);
        const triggerSceneBadge = `
            <span class="badge bg-light text-dark border" title="触发场景: ${escapeHtml(log.trigger_scene || '-')}">
                ${escapeHtml(triggerSceneLabel)}
            </span>
        `;
        const sessionIdDisplay = formatRiskSessionId(log.session_id, log.session_display);
        const sessionTitle = escapeHtml(log.session_id || log.session_display || '-');
        const durationText = formatRiskDuration(log.duration_ms);

        row.innerHTML = `
            <td class="text-nowrap">${createdAt}</td>
            <td class="text-nowrap">${escapeHtml(log.cookie_id || '-')}</td>
            <td class="text-nowrap">${eventCategoryBadge}</td>
            <td class="text-nowrap">${triggerSceneBadge}</td>
            <td>${statusBadge}</td>
            <td class="risk-log-cell-summary">${renderRiskLogSummaryCell(log)}</td>
            <td class="risk-log-cell-outcome">${renderRiskLogOutcomeCell(log)}</td>
            <td class="text-nowrap">${escapeHtml(durationText)}</td>
            <td class="risk-log-cell-session" title="${sessionTitle}">${escapeHtml(sessionIdDisplay)}</td>
            <td>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteRiskControlLog(${log.id})" title="删除">
                    <i class="bi bi-trash"></i>
                </button>
            </td>
        `;

        tableBody.appendChild(row);
    });
}

// 更新风控日志信息
function updateRiskLogInfo(data) {
    const countElement = document.getElementById('riskLogCount');
    const paginationInfo = document.getElementById('riskLogPaginationInfo');
    const hasFilters = hasActiveRiskLogFilters(getRiskLogFilters());
    const total = data.total || 0;
    const currentCount = data.data ? data.data.length : 0;

    if (countElement) {
        countElement.textContent = hasFilters ? `筛选结果: ${total} 条` : `总计: ${total} 条`;
    }

    if (paginationInfo) {
        if (currentCount === 0 || total === 0) {
            paginationInfo.textContent = hasFilters ? `显示第 0-0 条，匹配 0 条记录` : '显示第 0-0 条，共 0 条记录';
            return;
        }

        const start = currentRiskLogOffset + 1;
        const end = Math.min(currentRiskLogOffset + currentCount, total);
        paginationInfo.textContent = hasFilters
            ? `显示第 ${start}-${end} 条，匹配 ${total} 条记录`
            : `显示第 ${start}-${end} 条，共 ${total} 条记录`;
    }
}

// 更新风控日志分页
function updateRiskLogPagination(data) {
    const pagination = document.getElementById('riskLogPagination');
    const limit = parseInt(document.getElementById('riskLogLimit').value);
    const total = data.total || 0;
    const totalPages = Math.ceil(total / limit);
    const currentPage = Math.floor(currentRiskLogOffset / limit) + 1;

    pagination.innerHTML = '';

    if (totalPages <= 1) return;

    // 上一页
    const prevLi = document.createElement('li');
    prevLi.className = `page-item ${currentPage === 1 ? 'disabled' : ''}`;
    prevLi.innerHTML = `<a class="page-link" href="#" onclick="loadRiskControlLogs(${(currentPage - 2) * limit})">上一页</a>`;
    pagination.appendChild(prevLi);

    // 页码
    const startPage = Math.max(1, currentPage - 2);
    const endPage = Math.min(totalPages, currentPage + 2);

    for (let i = startPage; i <= endPage; i++) {
        const li = document.createElement('li');
        li.className = `page-item ${i === currentPage ? 'active' : ''}`;
        li.innerHTML = `<a class="page-link" href="#" onclick="loadRiskControlLogs(${(i - 1) * limit})">${i}</a>`;
        pagination.appendChild(li);
    }

    // 下一页
    const nextLi = document.createElement('li');
    nextLi.className = `page-item ${currentPage === totalPages ? 'disabled' : ''}`;
    nextLi.innerHTML = `<a class="page-link" href="#" onclick="loadRiskControlLogs(${currentPage * limit})">下一页</a>`;
    pagination.appendChild(nextLi);
}

// 按状态过滤风控日志
function filterRiskLogsByStatus(status) {
    currentRiskLogStatus = status;

    // 更新过滤按钮状态
    document.querySelectorAll('.filter-badge[data-status]').forEach(badge => {
        badge.classList.remove('active');
    });
    const activeBadge = document.querySelector(`.filter-badge[data-status="${status}"]`);
    if (activeBadge) {
        activeBadge.classList.add('active');
    }

    // 重新加载日志
    loadRiskControlLogs(0);
}

// 加载账号筛选选项
async function loadCookieFilterOptions() {
    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch('/admin/cookies', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            const select = document.getElementById('riskLogCookieFilter');

            // 清空现有选项，保留"全部账号"
            select.innerHTML = '<option value="">全部账号</option>';

            if (data.success && data.cookies) {
                data.cookies.forEach(cookie => {
                    const option = document.createElement('option');
                    option.value = cookie.cookie_id;
                    // 优先显示备注，其次显示用户名，都没有则不显示括号
                    const displayName = cookie.nickname || cookie.username || '';
                    option.textContent = displayName ? `${cookie.cookie_id} (${displayName})` : cookie.cookie_id;
                    select.appendChild(option);
                });
            }
        }
    } catch (error) {
        console.error('加载账号选项失败:', error);
    }
}

// 删除风控日志记录
async function deleteRiskControlLog(logId) {
    if (!confirm('确定要删除这条风控日志记录吗？')) {
        return;
    }

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch(`/admin/risk-control-logs/${logId}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();

        if (data.success) {
            showToast('删除成功', 'success');
            loadRiskControlLogs(currentRiskLogOffset);
        } else {
            showToast(data.message || '删除失败', 'danger');
        }
    } catch (error) {
        console.error('删除风控日志失败:', error);
        showToast('删除失败', 'danger');
    }
}

// 清空风控日志
async function clearRiskControlLogs() {
    if (!confirm('确定要清空所有风控日志吗？此操作不可恢复！')) {
        return;
    }

    try {
        const token = localStorage.getItem('auth_token');

        // 调用后端批量清空接口（管理员）
        const response = await fetch('/admin/data/risk_control_logs', {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();

        if (response.ok) {
            showToast('风控日志已清空', 'success');
            loadRiskControlLogs(0);
        } else {
            showToast(data.detail || data.message || '清空失败', 'danger');
        }
    } catch (error) {
        console.error('清空风控日志失败:', error);
        showToast('清空失败', 'danger');
    }
}

