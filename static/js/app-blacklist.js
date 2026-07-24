// ================================
// 【黑名单管理菜单】相关功能
// content-port from upstream personal blacklist UI; local modular frontend
// ================================

let blacklistState = {
    page: 1,
    pageSize: 20,
    total: 0,
    accountsLoaded: false
};

async function loadBlacklistPage() {
    await loadBlacklistAccountOptions();
    await loadPersonalBlacklist(blacklistState.page || 1);
}

async function loadBlacklistAccountOptions(force = false) {
    const accountSelect = document.getElementById('blacklistCookieId');
    if (!accountSelect) return;
    if (blacklistState.accountsLoaded && !force) return;

    try {
        const currentValue = accountSelect.value;
        const accounts = await fetchJSON(`${apiBase}/cookies/details`);
        const safeAccounts = Array.isArray(accounts) ? accounts : [];
        accountSelect.innerHTML = '<option value="">全部账号</option>' + safeAccounts.map(account => {
            const accountId = String(account.id || '').trim();
            const remark = String(account.remark || '').trim();
            const label = remark ? `${accountId}（${remark}）` : accountId;
            return `<option value="${escapeHtml(accountId)}">${escapeHtml(label)}</option>`;
        }).join('');
        if (currentValue && safeAccounts.some(account => String(account.id || '') === currentValue)) {
            accountSelect.value = currentValue;
        }
        blacklistState.accountsLoaded = true;
    } catch (error) {
        console.error('加载黑名单账号选项失败:', error);
    }
}

function handleBlacklistFilterKeydown(event) {
    if (event.key === 'Enter') {
        event.preventDefault();
        loadPersonalBlacklist(1);
    }
}

async function loadPersonalBlacklist(page = 1) {
    const tableBody = document.getElementById('blacklistTableBody');
    if (!tableBody) return;

    const pageSizeSelect = document.getElementById('blacklistPageSize');
    const pageSize = Math.max(1, parseInt(pageSizeSelect?.value || '20', 10) || 20);
    const safePage = Math.max(1, parseInt(page, 10) || 1);
    const params = new URLSearchParams({
        page: String(safePage),
        page_size: String(pageSize)
    });

    const buyerId = document.getElementById('blacklistFilterBuyerId')?.value?.trim();
    const buyerNick = document.getElementById('blacklistFilterBuyerNick')?.value?.trim();
    if (buyerId) params.set('buyer_id', buyerId);
    if (buyerNick) params.set('buyer_nick', buyerNick);

    try {
        const result = await fetchJSON(`${apiBase}/api/blacklist/personal?${params.toString()}`);
        const records = Array.isArray(result?.data) ? result.data : [];
        blacklistState.page = Number(result?.page || safePage);
        blacklistState.pageSize = Number(result?.page_size || pageSize);
        blacklistState.total = Number(result?.total || 0);
        renderPersonalBlacklist(records);
        renderBlacklistPagination();
    } catch (error) {
        console.error('加载个人黑名单失败:', error);
        tableBody.innerHTML = `
            <tr>
                <td colspan="8" class="text-center py-4 text-danger">
                    <i class="bi bi-exclamation-triangle fs-1 d-block mb-3"></i>
                    加载黑名单失败
                </td>
            </tr>
        `;
    }
}

function getBlacklistScopeBadge(scope) {
    const normalizedScope = String(scope || 'user');
    const config = {
        item: { text: '商品级', cls: 'bg-warning text-dark' },
        account: { text: '账号级', cls: 'bg-info text-dark' },
        user: { text: '用户级', cls: 'bg-secondary' }
    }[normalizedScope] || { text: normalizedScope || '未知', cls: 'bg-secondary' };
    return `<span class="badge ${config.cls}">${escapeHtml(config.text)}</span>`;
}

function getBlacklistTargetHtml(record) {
    const cookieId = String(record?.cookie_id || '').trim();
    const itemId = String(record?.item_id || '').trim();
    const parts = [];
    parts.push(cookieId ? `账号 ${cookieId}` : '全部账号');
    if (itemId) parts.push(`商品 ${itemId}`);
    return parts.map(part => `<div class="small text-muted text-nowrap" title="${escapeHtml(part)}">${escapeHtml(part)}</div>`).join('');
}

function renderPersonalBlacklist(records) {
    const tableBody = document.getElementById('blacklistTableBody');
    const totalText = document.getElementById('blacklistTotalText');
    const selectAll = document.getElementById('blacklistSelectAll');
    if (!tableBody) return;

    if (totalText) {
        totalText.textContent = `共 ${blacklistState.total || 0} 条`;
    }
    if (selectAll) {
        selectAll.checked = false;
        selectAll.indeterminate = false;
    }

    if (!Array.isArray(records) || records.length === 0) {
        tableBody.innerHTML = `
            <tr>
                <td colspan="8" class="text-center py-4 text-muted">
                    <i class="bi bi-person-x fs-1 d-block mb-3"></i>
                    暂无黑名单记录
                </td>
            </tr>
        `;
        updateBlacklistBatchDeleteState();
        return;
    }

    tableBody.innerHTML = records.map(record => {
        const recordId = Number(record.id || 0);
        const buyerId = String(record.buyer_id || '').trim();
        const buyerNick = String(record.buyer_nick || '').trim();
        const reason = String(record.reason || '').trim();
        const enabled = Boolean(record.is_enabled);
        const createdAt = formatDateTime(record.created_at || record.updated_at || '');
        return `
            <tr>
                <td>
                    <input class="form-check-input blacklist-row-check" type="checkbox" data-id="${recordId}" onchange="updateBlacklistBatchDeleteState()">
                </td>
                <td>${getBlacklistScopeBadge(record.scope)}</td>
                <td>
                    <div class="fw-semibold" title="${escapeHtml(buyerId)}">${escapeHtml(buyerId)}</div>
                    ${buyerNick ? `<div class="small text-muted" title="${escapeHtml(buyerNick)}">${escapeHtml(buyerNick)}</div>` : ''}
                </td>
                <td>${getBlacklistTargetHtml(record)}</td>
                <td style="max-width: 220px;">
                    <span class="d-inline-block text-truncate" style="max-width: 100%;" title="${escapeHtml(reason)}">${escapeHtml(reason || '-')}</span>
                </td>
                <td>
                    <div class="form-check form-switch m-0" title="${enabled ? '点击禁用' : '点击启用'}">
                        <input class="form-check-input" type="checkbox" ${enabled ? 'checked' : ''} onchange="togglePersonalBlacklist(${recordId}, this.checked)">
                    </div>
                </td>
                <td><small class="text-muted text-nowrap">${escapeHtml(createdAt)}</small></td>
                <td>
                    <button type="button" class="btn btn-outline-danger btn-sm" onclick="deletePersonalBlacklist(${recordId})" title="删除">
                        <i class="bi bi-trash"></i>
                    </button>
                </td>
            </tr>
        `;
    }).join('');
    updateBlacklistBatchDeleteState();
}

function renderBlacklistPagination() {
    const pagination = document.getElementById('blacklistPagination');
    const pageText = document.getElementById('blacklistPageText');
    if (!pagination) return;

    const pageSize = Math.max(1, Number(blacklistState.pageSize || 20));
    const total = Math.max(0, Number(blacklistState.total || 0));
    const totalPages = Math.max(1, Math.ceil(total / pageSize));
    const currentPage = Math.min(Math.max(1, Number(blacklistState.page || 1)), totalPages);

    if (currentPage !== blacklistState.page && total > 0) {
        loadPersonalBlacklist(currentPage);
        return;
    }

    if (pageText) {
        pageText.textContent = `第 ${currentPage} / ${totalPages} 页`;
    }

    const startPage = Math.max(1, currentPage - 2);
    const endPage = Math.min(totalPages, startPage + 4);
    const buttons = [];
    const addButton = (label, targetPage, disabled = false, active = false, title = '') => {
        buttons.push(`
            <button type="button" class="btn btn-sm ${active ? 'btn-primary' : 'btn-outline-secondary'}" ${disabled ? 'disabled' : ''} onclick="loadPersonalBlacklist(${targetPage})" title="${escapeHtml(title || label)}">
                ${label}
            </button>
        `);
    };

    addButton('<i class="bi bi-chevron-left"></i>', currentPage - 1, currentPage <= 1, false, '上一页');
    for (let page = startPage; page <= endPage; page += 1) {
        addButton(String(page), page, false, page === currentPage);
    }
    addButton('<i class="bi bi-chevron-right"></i>', currentPage + 1, currentPage >= totalPages, false, '下一页');
    pagination.innerHTML = buttons.join('');
}

async function createPersonalBlacklist() {
    const buyerIds = document.getElementById('blacklistBuyerIds')?.value?.trim() || '';
    if (!buyerIds) {
        showToast('请填写买家ID', 'warning');
        return;
    }

    const payload = {
        buyer_ids: buyerIds,
        cookie_id: document.getElementById('blacklistCookieId')?.value?.trim() || null,
        item_id: document.getElementById('blacklistItemId')?.value?.trim() || null,
        buyer_nick: document.getElementById('blacklistBuyerNick')?.value?.trim() || '',
        reason: document.getElementById('blacklistReason')?.value?.trim() || '',
        is_enabled: Boolean(document.getElementById('blacklistEnabled')?.checked)
    };

    try {
        const result = await fetchJSON(`${apiBase}/api/blacklist/personal`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        showToast(result?.message || '黑名单已保存', 'success');
        resetPersonalBlacklistForm();
        await loadPersonalBlacklist(1);
    } catch (error) {
        console.error('新增个人黑名单失败:', error);
    }
}

function resetPersonalBlacklistForm() {
    const form = document.getElementById('personalBlacklistForm');
    if (form) form.reset();
    const enabled = document.getElementById('blacklistEnabled');
    if (enabled) enabled.checked = true;
}

async function togglePersonalBlacklist(recordId, isEnabled) {
    try {
        await fetchJSON(`${apiBase}/api/blacklist/personal/${recordId}/toggle`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ is_enabled: Boolean(isEnabled) })
        });
        showToast(isEnabled ? '黑名单已启用' : '黑名单已禁用', 'success');
    } catch (error) {
        console.error('更新黑名单状态失败:', error);
        await loadPersonalBlacklist(blacklistState.page || 1);
    }
}

async function deletePersonalBlacklist(recordId) {
    if (!confirm('确定删除这条黑名单记录吗？')) return;
    try {
        const result = await fetchJSON(`${apiBase}/api/blacklist/personal/${recordId}`, {
            method: 'DELETE'
        });
        showToast(result?.message || '黑名单已删除', 'success');
        await loadPersonalBlacklist(blacklistState.page || 1);
    } catch (error) {
        console.error('删除个人黑名单失败:', error);
    }
}

function getSelectedBlacklistIds() {
    return Array.from(document.querySelectorAll('.blacklist-row-check:checked'))
        .map(checkbox => parseInt(checkbox.dataset.id || '0', 10))
        .filter(id => id > 0);
}

function toggleBlacklistSelectAll(checked) {
    document.querySelectorAll('.blacklist-row-check').forEach(checkbox => {
        checkbox.checked = Boolean(checked);
    });
    updateBlacklistBatchDeleteState();
}

function updateBlacklistBatchDeleteState() {
    const selectedIds = getSelectedBlacklistIds();
    const batchButton = document.getElementById('blacklistBatchDeleteBtn');
    const selectAll = document.getElementById('blacklistSelectAll');
    const rowChecks = Array.from(document.querySelectorAll('.blacklist-row-check'));

    if (batchButton) {
        batchButton.disabled = selectedIds.length === 0;
        batchButton.innerHTML = selectedIds.length > 0
            ? `<i class="bi bi-trash me-1"></i>批量删除 (${selectedIds.length})`
            : '<i class="bi bi-trash me-1"></i>批量删除';
    }

    if (selectAll) {
        selectAll.checked = rowChecks.length > 0 && selectedIds.length === rowChecks.length;
        selectAll.indeterminate = selectedIds.length > 0 && selectedIds.length < rowChecks.length;
    }
}

async function batchDeletePersonalBlacklist() {
    const selectedIds = getSelectedBlacklistIds();
    if (selectedIds.length === 0) {
        showToast('请先选择要删除的黑名单', 'warning');
        return;
    }
    if (!confirm(`确定删除选中的 ${selectedIds.length} 条黑名单记录吗？`)) return;

    try {
        const result = await fetchJSON(`${apiBase}/api/blacklist/personal/batch-delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids: selectedIds })
        });
        showToast(result?.message || '批量删除完成', 'success');
        await loadPersonalBlacklist(blacklistState.page || 1);
    } catch (error) {
        console.error('批量删除个人黑名单失败:', error);
    }
}

function resetBlacklistFilters() {
    const buyerId = document.getElementById('blacklistFilterBuyerId');
    const buyerNick = document.getElementById('blacklistFilterBuyerNick');
    if (buyerId) buyerId.value = '';
    if (buyerNick) buyerNick.value = '';
    loadPersonalBlacklist(1);
}

async function exportPersonalBlacklist() {
    toggleLoading(true);
    try {
        const response = await fetch(`${apiBase}/api/blacklist/personal/export`, {
            headers: { 'Authorization': `Bearer ${getAuthToken()}` }
        });
        if (response.status === 401) {
            localStorage.removeItem('auth_token');
            window.location.href = '/';
            return;
        }
        if (!response.ok) {
            let message = `导出失败: HTTP ${response.status}`;
            try {
                const errorText = await response.text();
                if (errorText) message = errorText;
            } catch {}
            throw new Error(message);
        }
        const blob = await response.blob();
        const disposition = response.headers.get('Content-Disposition') || '';
        const filenameMatch = disposition.match(/filename="?([^";]+)"?/i);
        const filename = filenameMatch ? filenameMatch[1] : `personal_blacklist_${Date.now()}.xlsx`;
        const url = URL.createObjectURL(blob);
        const link = document.createElement('a');
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        link.remove();
        URL.revokeObjectURL(url);
        showToast('黑名单已导出', 'success');
    } catch (error) {
        console.error('导出个人黑名单失败:', error);
        showToast(error.message || '导出个人黑名单失败', 'danger');
    } finally {
        toggleLoading(false);
    }
}

async function importPersonalBlacklistFile() {
    const input = document.getElementById('blacklistImportFile');
    const file = input?.files?.[0];
    if (!file) return;
    if (!file.name.toLowerCase().endsWith('.xlsx')) {
        showToast('仅支持 .xlsx 文件', 'warning');
        input.value = '';
        return;
    }

    const formData = new FormData();
    formData.append('file', file);
    try {
        const result = await fetchJSON(`${apiBase}/api/blacklist/personal/import`, {
            method: 'POST',
            body: formData
        });
        showToast(result?.message || '黑名单导入完成', 'success');
        await loadPersonalBlacklist(1);
    } catch (error) {
        console.error('导入个人黑名单失败:', error);
    } finally {
        input.value = '';
    }
}
