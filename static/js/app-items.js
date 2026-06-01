// ================================
// 【商品发布菜单】相关功能
// ================================

async function loadItemPublish() {
    ensureItemPublishPageInitialized();
    handlePublishDeliveryChoiceChange();
    await loadItemPublishAccounts();
}

function ensureItemPublishPageInitialized() {
    if (itemPublishInitialized) {
        return;
    }

    const form = document.getElementById('itemPublishForm');
    if (form) {
        form.addEventListener('reset', () => {
            window.setTimeout(() => clearItemPublishForm(true), 0);
        });
    }

    itemPublishInitialized = true;
}

async function loadItemPublishAccounts() {
    const select = document.getElementById('publishCookieId');
    if (!select) {
        return;
    }

    const currentValue = select.value;

    try {
        const response = await fetch(`${apiBase}/cookies/details`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const accounts = await response.json();
        const availableAccounts = accounts.filter(account => account.has_cookie_value !== false && account.enabled !== false);

        select.innerHTML = '<option value="">请选择账号</option>';

        if (availableAccounts.length === 0) {
            const option = document.createElement('option');
            option.value = '';
            option.disabled = true;
            option.textContent = '暂无可用账号';
            select.appendChild(option);
            return;
        }

        availableAccounts.forEach(account => {
            const option = document.createElement('option');
            option.value = account.id;
            option.textContent = buildItemPublishAccountLabel(account);
            select.appendChild(option);
        });

        if (currentValue && availableAccounts.some(account => account.id === currentValue)) {
            select.value = currentValue;
        } else if (availableAccounts.length === 1) {
            select.value = availableAccounts[0].id;
        }
    } catch (error) {
        console.error('加载发布账号失败:', error);
        select.innerHTML = '<option value="">加载账号失败</option>';
        showToast('加载发布账号失败', 'danger');
    }
}

function buildItemPublishAccountLabel(account) {
    const remark = String(account.remark || '').trim();
    const username = String(account.username || '').trim();
    if (remark) {
        return `${account.id} · ${remark}`;
    }
    if (username) {
        return `${account.id} · ${username}`;
    }
    return account.id;
}

function handlePublishDeliveryChoiceChange() {
    const choice = document.getElementById('publishDeliveryChoice')?.value || '包邮';
    const postPriceWrap = document.getElementById('publishPostPriceWrap');
    const postPriceInput = document.getElementById('publishPostPrice');
    const shouldShowPostPrice = choice === '一口价';

    if (postPriceWrap) {
        postPriceWrap.style.display = shouldShowPostPrice ? '' : 'none';
    }
    if (postPriceInput) {
        postPriceInput.required = shouldShowPostPrice;
        if (!shouldShowPostPrice) {
            postPriceInput.value = '';
        }
    }
}

function handlePublishImagesChange() {
    const input = document.getElementById('publishImages');
    if (!input) {
        return;
    }

    const files = Array.from(input.files || []);
    if (files.length > 9) {
        showToast('单次最多上传 9 张图片', 'warning');
        input.value = '';
        clearItemPublishImagePreviews();
        return;
    }

    renderItemPublishImagePreviews(files);
}

function renderItemPublishImagePreviews(files) {
    const previewContainer = document.getElementById('publishImagePreviewList');
    const summary = document.getElementById('publishImageSummary');

    clearItemPublishImagePreviews();

    if (!previewContainer) {
        return;
    }

    if (!files || files.length === 0) {
        previewContainer.innerHTML = '<div class="item-publish-preview-empty">尚未选择图片</div>';
        if (summary) {
            summary.textContent = '请上传 1-9 张图片，建议首图清晰展示商品主体。';
        }
        return;
    }

    const totalSize = files.reduce((sum, file) => sum + (file.size || 0), 0);
    previewContainer.innerHTML = files.map((file, index) => {
        const objectUrl = URL.createObjectURL(file);
        itemPublishPreviewUrls.push(objectUrl);
        return `
            <div class="item-publish-preview-card">
                <img src="${objectUrl}" alt="预览图 ${index + 1}">
                <div class="item-publish-preview-meta">
                    <div class="item-publish-preview-name" title="${escapeHtml(file.name || `图片 ${index + 1}`)}">${escapeHtml(file.name || `图片 ${index + 1}`)}</div>
                    <div class="item-publish-preview-size">${formatFileSize(file.size || 0)}</div>
                </div>
            </div>
        `;
    }).join('');

    if (summary) {
        summary.textContent = `已选择 ${files.length} 张图片，总大小 ${formatFileSize(totalSize)}。`;
    }
}

function clearItemPublishImagePreviews() {
    itemPublishPreviewUrls.forEach(url => URL.revokeObjectURL(url));
    itemPublishPreviewUrls = [];

    const previewContainer = document.getElementById('publishImagePreviewList');
    const summary = document.getElementById('publishImageSummary');
    if (previewContainer) {
        previewContainer.innerHTML = '<div class="item-publish-preview-empty">尚未选择图片</div>';
    }
    if (summary) {
        summary.textContent = '请上传 1-9 张图片，建议首图清晰展示商品主体。';
    }
}

function clearItemPublishForm(clearResult = true) {
    clearItemPublishImagePreviews();
    handlePublishDeliveryChoiceChange();

    const imagesInput = document.getElementById('publishImages');
    if (imagesInput) {
        imagesInput.value = '';
    }

    if (clearResult) {
        hideItemPublishResult();
    }
}

function hideItemPublishResult() {
    const panel = document.getElementById('publishResultPanel');
    const meta = document.getElementById('publishResultMeta');
    if (panel) {
        panel.style.display = 'none';
    }
    if (meta) {
        meta.innerHTML = '';
    }
}

function renderItemPublishResult(data, isSuccess) {
    const panel = document.getElementById('publishResultPanel');
    const badge = document.getElementById('publishResultBadge');
    const title = document.getElementById('publishResultTitle');
    const message = document.getElementById('publishResultMessage');
    const meta = document.getElementById('publishResultMeta');

    if (!panel || !badge || !title || !message || !meta) {
        return;
    }

    panel.style.display = '';
    badge.className = `badge ${isSuccess ? 'text-bg-success' : 'text-bg-danger'}`;
    badge.textContent = isSuccess ? '成功' : '失败';
    title.textContent = isSuccess ? '商品发布完成' : '商品发布失败';
    message.textContent = data.message || (isSuccess ? '商品发布成功' : '商品发布失败');

    const metaRows = [];
    if (data.published_item_id) {
        metaRows.push({ label: '商品ID', value: data.published_item_id });
    }

    const syncResult = data.sync_result || {};
    if (syncResult.message) {
        metaRows.push({ label: '同步结果', value: syncResult.message });
    }

    const pageSync = syncResult.page_sync || {};
    if (pageSync.current_count || pageSync.saved_count) {
        metaRows.push({
            label: '最近页同步',
            value: `获取 ${pageSync.current_count || 0} 个商品，写入 ${pageSync.saved_count || 0} 个`
        });
    }

    const fullSync = syncResult.full_sync || {};
    if (fullSync.used) {
        metaRows.push({
            label: '补充同步',
            value: fullSync.success
                ? `全量扫描 ${fullSync.total_count || 0} 个商品，写入 ${fullSync.total_saved || 0} 个`
                : (fullSync.error || '补充同步失败')
        });
    }

    if (!isSuccess && data.detail) {
        metaRows.push({ label: '错误详情', value: data.detail });
    }

    if (metaRows.length === 0) {
        meta.innerHTML = '<div class="text-muted small">当前没有更多结果详情。</div>';
        return;
    }

    meta.innerHTML = metaRows.map(row => `
        <div class="item-publish-result-row">
            <span class="item-publish-result-label">${escapeHtml(row.label)}</span>
            <span class="item-publish-result-value">${escapeHtml(String(row.value || ''))}</span>
        </div>
    `).join('');
}

async function submitItemPublishForm() {
    if (itemPublishSubmitting) {
        return;
    }

    const cookieId = document.getElementById('publishCookieId')?.value || '';
    const title = document.getElementById('publishTitle')?.value.trim() || '';
    const description = document.getElementById('publishDescription')?.value.trim() || '';
    const currentPrice = document.getElementById('publishCurrentPrice')?.value.trim() || '';
    const originalPrice = document.getElementById('publishOriginalPrice')?.value.trim() || '';
    const deliveryChoice = document.getElementById('publishDeliveryChoice')?.value || '包邮';
    const postPrice = document.getElementById('publishPostPrice')?.value.trim() || '';
    const canSelfPickup = document.getElementById('publishCanSelfPickup')?.checked || false;
    const imageInput = document.getElementById('publishImages');
    const files = Array.from(imageInput?.files || []);
    const submitButton = document.getElementById('itemPublishSubmitBtn');

    if (!cookieId) {
        showToast('请选择发布账号', 'warning');
        return;
    }
    if (!title) {
        showToast('请输入商品标题', 'warning');
        return;
    }
    if (!description) {
        showToast('请输入商品描述', 'warning');
        return;
    }
    if (files.length === 0) {
        showToast('请至少上传 1 张商品图片', 'warning');
        return;
    }
    if (files.length > 9) {
        showToast('单次最多上传 9 张图片', 'warning');
        return;
    }
    if (originalPrice && !currentPrice) {
        showToast('填写原价时必须同时填写现价', 'warning');
        return;
    }
    if (deliveryChoice === '一口价' && !postPrice) {
        showToast('运费方式为一口价时必须填写邮费', 'warning');
        return;
    }

    const formData = new FormData();
    formData.append('cookie_id', cookieId);
    formData.append('title', title);
    formData.append('description', description);
    formData.append('current_price', currentPrice);
    formData.append('original_price', originalPrice);
    formData.append('delivery_choice', deliveryChoice);
    formData.append('post_price', postPrice);
    formData.append('can_self_pickup', canSelfPickup ? 'true' : 'false');
    files.forEach(file => formData.append('images', file));

    itemPublishSubmitting = true;
    if (submitButton) {
        submitButton.disabled = true;
        submitButton.innerHTML = '<span class="spinner-border spinner-border-sm me-1" role="status" aria-hidden="true"></span>发布中...';
    }

    try {
        const response = await fetch(`${apiBase}/item-publish`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            },
            body: formData
        });

        const responseText = await response.text();
        let responseData = {};
        try {
            responseData = responseText ? JSON.parse(responseText) : {};
        } catch (parseError) {
            responseData = { detail: responseText || `HTTP ${response.status}` };
        }

        if (!response.ok) {
            const errorMessage = responseData.detail || responseData.message || `HTTP ${response.status}`;
            renderItemPublishResult({ message: errorMessage, detail: errorMessage }, false);
            showToast(errorMessage, 'danger');
            return;
        }

        renderItemPublishResult(responseData, true);
        showToast(responseData.message || '商品发布成功', 'success');
    } catch (error) {
        console.error('发布商品失败:', error);
        const errorMessage = error.message || '发布商品失败';
        renderItemPublishResult({ message: errorMessage, detail: errorMessage }, false);
        showToast(errorMessage, 'danger');
    } finally {
        itemPublishSubmitting = false;
        if (submitButton) {
            submitButton.disabled = false;
            submitButton.innerHTML = '<i class="bi bi-cloud-upload me-1"></i>发布商品';
        }
    }
}

// ================================
// 【商品管理菜单】相关功能
// ================================

// 切换商品多规格状态
async function toggleItemMultiSpec(cookieId, itemId, isMultiSpec) {
    try {
    const response = await fetch(`${apiBase}/items/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}/multi-spec`, {
        method: 'PUT',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({
        is_multi_spec: isMultiSpec
        })
    });

    if (response.ok) {
        showToast(`${isMultiSpec ? '开启' : '关闭'}多规格成功`, 'success');
        // 刷新商品列表
        await refreshItemsData();
    } else {
        const errorData = await response.json();
        throw new Error(errorData.error || '操作失败');
    }
    } catch (error) {
    console.error('切换多规格状态失败:', error);
    showToast(`切换多规格状态失败: ${error.message}`, 'danger');
    }
}

// 切换商品多数量发货状态
async function toggleItemMultiQuantityDelivery(cookieId, itemId, multiQuantityDelivery) {
    try {
    const response = await fetch(`${apiBase}/items/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}/multi-quantity-delivery`, {
        method: 'PUT',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({
        multi_quantity_delivery: multiQuantityDelivery
        })
    });

    if (response.ok) {
        showToast(`${multiQuantityDelivery ? '开启' : '关闭'}多数量发货成功`, 'success');
        // 刷新商品列表
        await refreshItemsData();
    } else {
        const errorData = await response.json();
        throw new Error(errorData.error || '操作失败');
    }
    } catch (error) {
    console.error('切换多数量发货状态失败:', error);
    showToast(`切换多数量发货状态失败: ${error.message}`, 'danger');
    }
}

// 加载商品列表
async function loadItems() {
    try {
    // 先加载Cookie列表用于筛选
    await loadCookieFilter('itemCookieFilter');

    // 加载商品列表
    await refreshItemsData();
    } catch (error) {
    console.error('加载商品列表失败:', error);
    showToast('加载商品列表失败', 'danger');
    }
}

// 只刷新商品数据，不重新加载筛选器
async function refreshItemsData() {
    try {
    const selectedCookie = document.getElementById('itemCookieFilter').value;
    if (selectedCookie) {
        await loadItemsByCookie();
    } else {
        await loadAllItems();
    }
    } catch (error) {
    console.error('刷新商品数据失败:', error);
    showToast('刷新商品数据失败', 'danger');
    }
}

// 加载Cookie筛选选项
async function loadCookieFilter(id) {
    try {
    const response = await fetch(`${apiBase}/cookies/details`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const accounts = await response.json();
        const select = document.getElementById(id);

        // 保存当前选择的值
        const currentValue = select.value;

        // 清空现有选项（保留"所有账号"）
        select.innerHTML = '<option value="">所有账号</option>';

        if (accounts.length === 0) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = '❌ 暂无账号';
        option.disabled = true;
        select.appendChild(option);
        return;
        }

        // 分组显示：先显示启用的账号，再显示禁用的账号
        const enabledAccounts = accounts.filter(account => {
        const enabled = account.enabled === undefined ? true : account.enabled;
        return enabled;
        });
        const disabledAccounts = accounts.filter(account => {
        const enabled = account.enabled === undefined ? true : account.enabled;
        return !enabled;
        });

        // 添加启用的账号
        enabledAccounts.forEach(account => {
        const option = document.createElement('option');
        option.value = account.id;
        option.textContent = `🟢 ${account.id}`;
        select.appendChild(option);
        });

        // 添加禁用的账号
        if (disabledAccounts.length > 0) {
        // 添加分隔线
        if (enabledAccounts.length > 0) {
            const separator = document.createElement('option');
            separator.value = '';
            separator.textContent = '────────────────';
            separator.disabled = true;
            select.appendChild(separator);
        }

        disabledAccounts.forEach(account => {
            const option = document.createElement('option');
            option.value = account.id;
            option.textContent = `🔴 ${account.id} (已禁用)`;
            select.appendChild(option);
        });
        }

        // 恢复之前选择的值
        if (currentValue) {
        select.value = currentValue;
        }
    }
    } catch (error) {
    console.error('加载Cookie列表失败:', error);
    showToast('加载账号列表失败', 'danger');
    }
}

// 加载所有商品
async function loadAllItems() {
    try {
    const response = await fetch(`${apiBase}/items`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        displayItems(data.items);
    } else {
        throw new Error('获取商品列表失败');
    }
    } catch (error) {
    console.error('加载商品列表失败:', error);
    showToast('加载商品列表失败', 'danger');
    }
}

// 按Cookie加载商品
async function loadItemsByCookie() {
    const cookieId = document.getElementById('itemCookieFilter').value;

    if (!cookieId) {
    await loadAllItems();
    return;
    }

    try {
    const response = await fetch(`${apiBase}/items/cookie/${encodeURIComponent(cookieId)}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        displayItems(data.items);
    } else {
        throw new Error('获取商品列表失败');
    }
    } catch (error) {
    console.error('加载商品列表失败:', error);
    showToast('加载商品列表失败', 'danger');
    }
}

// 显示商品列表
function displayItems(items) {
    // 存储所有商品数据
    allItemsData = items || [];

    // 应用搜索过滤
    applyItemsFilter();

    // 显示当前页数据
    displayCurrentPageItems();

    // 更新分页控件
    updateItemsPagination();
}

// 应用搜索过滤
function applyItemsFilter() {
    const searchKeyword = currentSearchKeyword.toLowerCase().trim();

    if (!searchKeyword) {
        filteredItemsData = [...allItemsData];
    } else {
        filteredItemsData = allItemsData.filter(item => {
            const title = (item.item_title || '').toLowerCase();
            const detail = getItemDetailText(item.item_detail || '').toLowerCase();
            return title.includes(searchKeyword) || detail.includes(searchKeyword);
        });
    }

    // 重置到第一页
    currentItemsPage = 1;

    // 计算总页数
    totalItemsPages = Math.ceil(filteredItemsData.length / itemsPerPage);

    // 更新搜索统计
    updateItemsSearchStats();
}

// 获取商品详情的纯文本内容
function getItemDetailText(itemDetail) {
    if (!itemDetail) return '';

    try {
        // 尝试解析JSON
        const detail = JSON.parse(itemDetail);
        if (detail.content) {
            return detail.content;
        }
        return itemDetail;
    } catch (e) {
        // 如果不是JSON格式，直接返回原文本
        return itemDetail;
    }
}

// 显示当前页的商品数据
function displayCurrentPageItems() {
    const tbody = document.getElementById('itemsTableBody');

    if (!filteredItemsData || filteredItemsData.length === 0) {
        tbody.innerHTML = '<tr><td colspan="10" class="text-center text-muted">暂无商品数据</td></tr>';
        resetItemsSelection();
        return;
    }

    // 计算当前页的数据范围
    const startIndex = (currentItemsPage - 1) * itemsPerPage;
    const endIndex = startIndex + itemsPerPage;
    const currentPageItems = filteredItemsData.slice(startIndex, endIndex);

    const itemsHtml = currentPageItems.map(item => {
        // 处理商品标题显示
        let itemTitleDisplay = item.item_title || '未设置';
        if (itemTitleDisplay.length > 30) {
            itemTitleDisplay = itemTitleDisplay.substring(0, 30) + '...';
        }

        // 处理商品详情显示
        let itemDetailDisplay = '未设置';
        if (item.item_detail) {
            const detailText = getItemDetailText(item.item_detail);
            itemDetailDisplay = detailText.substring(0, 50) + (detailText.length > 50 ? '...' : '');
        }

        // 多规格状态显示
        const isMultiSpec = item.is_multi_spec;
        const multiSpecDisplay = isMultiSpec ?
            '<span class="badge bg-success">多规格</span>' :
            '<span class="badge bg-secondary">普通</span>';

        // 多数量发货状态显示
        const isMultiQuantityDelivery = item.multi_quantity_delivery;
        const multiQuantityDeliveryDisplay = isMultiQuantityDelivery ?
            '<span class="badge bg-success">已开启</span>' :
            '<span class="badge bg-secondary">已关闭</span>';

        return `
            <tr>
            <td>
                <input type="checkbox" name="itemCheckbox"
                        data-cookie-id="${escapeHtml(item.cookie_id)}"
                        data-item-id="${escapeHtml(item.item_id)}"
                        onchange="updateSelectAllState()">
            </td>
            <td>${escapeHtml(item.cookie_id)}</td>
            <td>${escapeHtml(item.item_id)}</td>
            <td title="${escapeHtml(item.item_title || '未设置')}">${escapeHtml(itemTitleDisplay)}</td>
            <td title="${escapeHtml(getItemDetailText(item.item_detail || ''))}">${escapeHtml(itemDetailDisplay)}</td>
            <td>${escapeHtml(item.item_price || '未设置')}</td>
            <td>${multiSpecDisplay}</td>
            <td>${multiQuantityDeliveryDisplay}</td>
            <td>${formatDateTime(item.updated_at)}</td>
            <td>
                <div class="btn-group" role="group">
                <button class="btn btn-sm btn-outline-primary" onclick="editItem('${escapeHtml(item.cookie_id)}', '${escapeHtml(item.item_id)}')" title="编辑详情">
                    <i class="bi bi-pencil"></i>
                </button>
                <button class="btn btn-sm btn-outline-danger" onclick="deleteItem('${escapeHtml(item.cookie_id)}', '${escapeHtml(item.item_id)}', '${escapeHtml(item.item_title || item.item_id)}')" title="删除">
                    <i class="bi bi-trash"></i>
                </button>
                <button class="btn btn-sm ${isMultiSpec ? 'btn-warning' : 'btn-success'}" onclick="toggleItemMultiSpec('${escapeHtml(item.cookie_id)}', '${escapeHtml(item.item_id)}', ${!isMultiSpec})" title="${isMultiSpec ? '关闭多规格' : '开启多规格'}">
                    <i class="bi ${isMultiSpec ? 'bi-toggle-on' : 'bi-toggle-off'}"></i>
                </button>
                <button class="btn btn-sm ${isMultiQuantityDelivery ? 'btn-warning' : 'btn-success'}" onclick="toggleItemMultiQuantityDelivery('${escapeHtml(item.cookie_id)}', '${escapeHtml(item.item_id)}', ${!isMultiQuantityDelivery})" title="${isMultiQuantityDelivery ? '关闭多数量发货' : '开启多数量发货'}">
                    <i class="bi ${isMultiQuantityDelivery ? 'bi-box-arrow-down' : 'bi-box-arrow-up'}"></i>
                </button>
                </div>
            </td>
            </tr>
        `;
    }).join('');

    // 更新表格内容
    tbody.innerHTML = itemsHtml;

    // 重置选择状态
    resetItemsSelection();
}

// 重置商品选择状态
function resetItemsSelection() {
    const selectAllCheckbox = document.getElementById('selectAllItems');
    if (selectAllCheckbox) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
    }
    updateBatchDeleteButton();
}

// 商品搜索过滤函数
function filterItems() {
    const searchInput = document.getElementById('itemSearchInput');
    currentSearchKeyword = searchInput ? searchInput.value : '';

    // 应用过滤
    applyItemsFilter();

    // 显示当前页数据
    displayCurrentPageItems();

    // 更新分页控件
    updateItemsPagination();
}

// 更新搜索统计信息
function updateItemsSearchStats() {
    const statsElement = document.getElementById('itemSearchStats');
    const statsTextElement = document.getElementById('itemSearchStatsText');

    if (!statsElement || !statsTextElement) return;

    if (currentSearchKeyword) {
        statsTextElement.textContent = `搜索"${currentSearchKeyword}"，找到 ${filteredItemsData.length} 个商品`;
        statsElement.style.display = 'block';
    } else {
        statsElement.style.display = 'none';
    }
}

// 更新分页控件
function updateItemsPagination() {
    const paginationElement = document.getElementById('itemsPagination');
    const pageInfoElement = document.getElementById('itemsPageInfo');
    const totalPagesElement = document.getElementById('itemsTotalPages');
    const pageInputElement = document.getElementById('itemsPageInput');

    if (!paginationElement) return;

    // 分页控件总是显示
    paginationElement.style.display = 'block';

    // 更新页面信息
    const startIndex = (currentItemsPage - 1) * itemsPerPage + 1;
    const endIndex = Math.min(currentItemsPage * itemsPerPage, filteredItemsData.length);

    if (pageInfoElement) {
        pageInfoElement.textContent = `显示第 ${startIndex}-${endIndex} 条，共 ${filteredItemsData.length} 条记录`;
    }

    if (totalPagesElement) {
        totalPagesElement.textContent = totalItemsPages;
    }

    if (pageInputElement) {
        pageInputElement.value = currentItemsPage;
        pageInputElement.max = totalItemsPages;
    }

    // 更新分页按钮状态
    updateItemsPaginationButtons();
}

// 更新分页按钮状态
function updateItemsPaginationButtons() {
    const firstPageBtn = document.getElementById('itemsFirstPage');
    const prevPageBtn = document.getElementById('itemsPrevPage');
    const nextPageBtn = document.getElementById('itemsNextPage');
    const lastPageBtn = document.getElementById('itemsLastPage');

    if (firstPageBtn) firstPageBtn.disabled = currentItemsPage <= 1;
    if (prevPageBtn) prevPageBtn.disabled = currentItemsPage <= 1;
    if (nextPageBtn) nextPageBtn.disabled = currentItemsPage >= totalItemsPages;
    if (lastPageBtn) lastPageBtn.disabled = currentItemsPage >= totalItemsPages;
}

// 跳转到指定页面
function goToItemsPage(page) {
    if (page < 1 || page > totalItemsPages) return;

    currentItemsPage = page;
    displayCurrentPageItems();
    updateItemsPagination();
}

// 处理页面输入框的回车事件
function handleItemsPageInput(event) {
    if (event.key === 'Enter') {
        const pageInput = event.target;
        const page = parseInt(pageInput.value);

        if (page >= 1 && page <= totalItemsPages) {
            goToItemsPage(page);
        } else {
            pageInput.value = currentItemsPage;
        }
    }
}

// 改变每页显示数量
function changeItemsPageSize() {
    const pageSizeSelect = document.getElementById('itemsPageSize');
    if (!pageSizeSelect) return;

    itemsPerPage = parseInt(pageSizeSelect.value);

    // 重新计算总页数
    totalItemsPages = Math.ceil(filteredItemsData.length / itemsPerPage);

    // 调整当前页码，确保不超出范围
    if (currentItemsPage > totalItemsPages) {
        currentItemsPage = Math.max(1, totalItemsPages);
    }

    // 重新显示数据
    displayCurrentPageItems();
    updateItemsPagination();
}

// 初始化商品搜索功能
let itemsSearchInitialized = false; // 标记是否已初始化
function initItemsSearch() {
    // 避免重复初始化
    if (itemsSearchInitialized) return;
    
    // 初始化分页大小
    const pageSizeSelect = document.getElementById('itemsPageSize');
    if (pageSizeSelect) {
        itemsPerPage = parseInt(pageSizeSelect.value) || 20;
        pageSizeSelect.addEventListener('change', changeItemsPageSize);
    }

    // 初始化搜索输入框事件监听器
    const searchInput = document.getElementById('itemSearchInput');
    if (searchInput) {
        // 使用防抖来避免频繁搜索
        let searchTimeout;
        searchInput.addEventListener('input', function() {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                filterItems();
            }, 300); // 300ms 防抖延迟
        });
        
        // 标记已初始化
        itemsSearchInitialized = true;
        console.log('商品搜索功能已初始化');
    }

    // 初始化页面输入框事件监听器
    const pageInput = document.getElementById('itemsPageInput');
    if (pageInput) {
        pageInput.addEventListener('keydown', handleItemsPageInput);
    }
}

// 刷新商品列表
async function refreshItems() {
    await refreshItemsData();
    showToast('本地商品列表已刷新', 'success');
}

// 获取商品信息
async function getAllItemsFromAccount() {
    const cookieSelect = document.getElementById('itemCookieFilter');
    const selectedCookieId = cookieSelect.value;
    const pageNumber = parseInt(document.getElementById('pageNumber').value) || 1;

    if (!selectedCookieId) {
    showToast('请先选择一个账号', 'warning');
    return;
    }

    if (pageNumber < 1) {
    showToast('页码必须大于0', 'warning');
    return;
    }

    // 显示加载状态
    const button = event.target;
    const originalText = button.innerHTML;
    button.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>同步中...';
    button.disabled = true;

    try {
    const response = await fetch(`${apiBase}/items/get-by-page`, {
        method: 'POST',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({
        cookie_id: selectedCookieId,
        page_number: pageNumber,
        page_size: 20
        })
    });

    if (response.ok) {
        const data = await response.json();
        if (data.success) {
        showToast(`成功同步第${pageNumber}页 ${data.current_count} 个商品，最新详情已更新`, 'success');
        // 刷新商品列表（保持筛选器选择）
        await refreshItemsData();
        } else {
        showToast(data.message || '同步商品信息失败', 'danger');
        }
    } else {
        throw new Error(`HTTP ${response.status}`);
    }
    } catch (error) {
    console.error('同步商品信息失败:', error);
    showToast('同步商品信息失败', 'danger');
    } finally {
    // 恢复按钮状态
    button.innerHTML = originalText;
    button.disabled = false;
    }
}

// 获取所有页商品信息
async function getAllItemsFromAccountAll() {
    const cookieSelect = document.getElementById('itemCookieFilter');
    const selectedCookieId = cookieSelect.value;

    if (!selectedCookieId) {
    showToast('请先选择一个账号', 'warning');
    return;
    }

    // 显示加载状态
    const button = event.target;
    const originalText = button.innerHTML;
    button.innerHTML = '<i class="bi bi-hourglass-split me-1"></i>同步中...';
    button.disabled = true;

    try {
    const response = await fetch(`${apiBase}/items/get-all-from-account`, {
        method: 'POST',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({
        cookie_id: selectedCookieId
        })
    });

    if (response.ok) {
        const data = await response.json();
        if (data.success) {
        const message = data.total_pages ?
            `成功同步 ${data.total_count} 个商品（共${data.total_pages}页），最新详情已更新` :
            `成功同步商品信息，最新详情已更新`;
        showToast(message, 'success');
        // 刷新商品列表（保持筛选器选择）
        await refreshItemsData();
        } else {
        showToast(data.message || '同步商品信息失败', 'danger');
        }
    } else {
        throw new Error(`HTTP ${response.status}`);
    }
    } catch (error) {
    console.error('同步商品信息失败:', error);
    showToast('同步商品信息失败', 'danger');
    } finally {
    // 恢复按钮状态
    button.innerHTML = originalText;
    button.disabled = false;
    }
}



// 编辑商品详情
async function editItem(cookieId, itemId) {
    try {
    const response = await fetch(`${apiBase}/items/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        const item = data.item;

        // 填充表单
        document.getElementById('editItemCookieId').value = item.cookie_id;
        document.getElementById('editItemId').value = item.item_id;
        document.getElementById('editItemCookieIdDisplay').value = item.cookie_id;
        document.getElementById('editItemIdDisplay').value = item.item_id;
        document.getElementById('editItemDetail').value = item.item_detail || '';

        // 显示模态框
        const modal = new bootstrap.Modal(document.getElementById('editItemModal'));
        modal.show();
    } else {
        throw new Error('获取商品详情失败');
    }
    } catch (error) {
    console.error('获取商品详情失败:', error);
    showToast('获取商品详情失败', 'danger');
    }
}

// 保存商品详情
async function saveItemDetail() {
    const cookieId = document.getElementById('editItemCookieId').value;
    const itemId = document.getElementById('editItemId').value;
    const itemDetail = document.getElementById('editItemDetail').value.trim();

    if (!itemDetail) {
    showToast('请输入商品详情', 'warning');
    return;
    }

    try {
    const response = await fetch(`${apiBase}/items/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}`, {
        method: 'PUT',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({
        item_detail: itemDetail
        })
    });

    if (response.ok) {
        showToast('商品详情更新成功', 'success');

        // 关闭模态框
        const modal = bootstrap.Modal.getInstance(document.getElementById('editItemModal'));
        modal.hide();

        // 刷新列表（保持筛选器选择）
        await refreshItemsData();
    } else {
        const error = await response.text();
        showToast(`更新失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('更新商品详情失败:', error);
    showToast('更新商品详情失败', 'danger');
    }
}

// 删除商品信息
async function deleteItem(cookieId, itemId, itemTitle) {
    try {
    // 确认删除
    const confirmed = confirm(`确定要删除商品信息吗？\n\n商品ID: ${itemId}\n商品标题: ${itemTitle || '未设置'}\n\n此操作不可撤销！`);
    if (!confirmed) {
        return;
    }

    const response = await fetch(`${apiBase}/items/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}`, {
        method: 'DELETE',
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        showToast('商品信息删除成功', 'success');
        // 刷新列表（保持筛选器选择）
        await refreshItemsData();
    } else {
        const error = await response.text();
        showToast(`删除失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('删除商品信息失败:', error);
    showToast('删除商品信息失败', 'danger');
    }
}

// 批量删除商品信息
async function batchDeleteItems() {
    try {
    // 获取所有选中的复选框
    const checkboxes = document.querySelectorAll('input[name="itemCheckbox"]:checked');
    if (checkboxes.length === 0) {
        showToast('请选择要删除的商品', 'warning');
        return;
    }

    // 确认删除
    const confirmed = confirm(`确定要删除选中的 ${checkboxes.length} 个商品信息吗？\n\n此操作不可撤销！`);
    if (!confirmed) {
        return;
    }

    // 构造删除列表
    const itemsToDelete = Array.from(checkboxes).map(checkbox => {
        const row = checkbox.closest('tr');
        return {
        cookie_id: checkbox.dataset.cookieId,
        item_id: checkbox.dataset.itemId
        };
    });

    const response = await fetch(`${apiBase}/items/batch`, {
        method: 'DELETE',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({ items: itemsToDelete })
    });

    if (response.ok) {
        const result = await response.json();
        showToast(`批量删除完成: 成功 ${result.success_count} 个，失败 ${result.failed_count} 个`, 'success');
        // 刷新列表（保持筛选器选择）
        await refreshItemsData();
    } else {
        const error = await response.text();
        showToast(`批量删除失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('批量删除商品信息失败:', error);
    showToast('批量删除商品信息失败', 'danger');
    }
}

// 全选/取消全选
function toggleSelectAll(selectAllCheckbox) {
    const checkboxes = document.querySelectorAll('input[name="itemCheckbox"]');
    checkboxes.forEach(checkbox => {
    checkbox.checked = selectAllCheckbox.checked;
    });
    updateBatchDeleteButton();
}

// 更新全选状态
function updateSelectAllState() {
    const checkboxes = document.querySelectorAll('input[name="itemCheckbox"]');
    const checkedCheckboxes = document.querySelectorAll('input[name="itemCheckbox"]:checked');
    const selectAllCheckbox = document.getElementById('selectAllItems');

    if (checkboxes.length === 0) {
    selectAllCheckbox.checked = false;
    selectAllCheckbox.indeterminate = false;
    } else if (checkedCheckboxes.length === checkboxes.length) {
    selectAllCheckbox.checked = true;
    selectAllCheckbox.indeterminate = false;
    } else if (checkedCheckboxes.length > 0) {
    selectAllCheckbox.checked = false;
    selectAllCheckbox.indeterminate = true;
    } else {
    selectAllCheckbox.checked = false;
    selectAllCheckbox.indeterminate = false;
    }

    updateBatchDeleteButton();
}

// 更新批量删除按钮状态
function updateBatchDeleteButton() {
    const checkedCheckboxes = document.querySelectorAll('input[name="itemCheckbox"]:checked');
    const batchDeleteBtn = document.getElementById('batchDeleteBtn');

    if (checkedCheckboxes.length > 0) {
    batchDeleteBtn.disabled = false;
    batchDeleteBtn.innerHTML = `<i class="bi bi-trash"></i> 批量删除 (${checkedCheckboxes.length})`;
    } else {
    batchDeleteBtn.disabled = true;
    batchDeleteBtn.innerHTML = '<i class="bi bi-trash"></i> 批量删除';
    }
}

function toggleSelectAllItemReplies(selectAllCheckbox) {
    const checkboxes = document.querySelectorAll('input[name="itemReplyCheckbox"]');
    checkboxes.forEach(checkbox => {
        checkbox.checked = selectAllCheckbox.checked;
    });
    updateItemReplyBatchDeleteButton();
}

function updateItemReplySelectAllState() {
    const checkboxes = document.querySelectorAll('input[name="itemReplyCheckbox"]');
    const checkedCheckboxes = document.querySelectorAll('input[name="itemReplyCheckbox"]:checked');
    const selectAllCheckbox = document.getElementById('selectAllItemReplies');

    if (!selectAllCheckbox) return;

    if (checkboxes.length === 0) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
    } else if (checkedCheckboxes.length === checkboxes.length) {
        selectAllCheckbox.checked = true;
        selectAllCheckbox.indeterminate = false;
    } else if (checkedCheckboxes.length > 0) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = true;
    } else {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
    }

    updateItemReplyBatchDeleteButton();
}

function updateItemReplyBatchDeleteButton() {
    const checkedCheckboxes = document.querySelectorAll('input[name="itemReplyCheckbox"]:checked');
    const batchDeleteBtn = document.getElementById('batchDeleteItemRepliesBtn');

    if (!batchDeleteBtn) return;

    if (checkedCheckboxes.length > 0) {
        batchDeleteBtn.disabled = false;
        batchDeleteBtn.innerHTML = `<i class="bi bi-trash"></i> 批量删除 (${checkedCheckboxes.length})`;
    } else {
        batchDeleteBtn.disabled = true;
        batchDeleteBtn.innerHTML = '<i class="bi bi-trash"></i> 批量删除';
    }
}

// 格式化日期时间
function formatDateTime(dateString) {
    const date = parseUtcDateTime(dateString);
    return date ? date.toLocaleString('zh-CN') : '未知';
}

// HTML转义函数
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// ================================
// 【商品回复管理菜单】相关功能
// ================================

// 加载商品回复列表
async function loadItemsReplay() {
    try {
    // 先加载Cookie列表用于筛选
    await loadCookieFilter('itemReplayCookieFilter');
    await loadCookieFilterPlus('editReplyCookieIdSelect');
    // 加载商品列表
    await refreshItemsReplayData();
    } catch (error) {
    console.error('加载商品列表失败:', error);
    showToast('加载商品列表失败', 'danger');
    }
}

// 只刷新商品回复数据，不重新加载筛选器
async function refreshItemsReplayData() {
    try {
    const selectedCookie = document.getElementById('itemReplayCookieFilter').value;
    if (selectedCookie) {
        await loadItemsReplayByCookie();
    } else {
        await loadAllItemReplays();
    }
    } catch (error) {
    console.error('刷新商品数据失败:', error);
    showToast('刷新商品数据失败', 'danger');
    }
}

// 加载Cookie筛选选项添加弹框中使用
async function loadCookieFilterPlus(id) {
    try {
    const response = await fetch(`${apiBase}/cookies/details`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const accounts = await response.json();
        const select = document.getElementById(id);

        // 保存当前选择的值
        const currentValue = select.value;

        // 清空现有选项（保留"所有账号"）
        select.innerHTML = '<option value="">选择账号</option>';

        if (accounts.length === 0) {
        const option = document.createElement('option');
        option.value = '';
        option.textContent = '❌ 暂无账号';
        option.disabled = true;
        select.appendChild(option);
        return;
        }

        // 分组显示：先显示启用的账号，再显示禁用的账号
        const enabledAccounts = accounts.filter(account => {
        const enabled = account.enabled === undefined ? true : account.enabled;
        return enabled;
        });
        const disabledAccounts = accounts.filter(account => {
        const enabled = account.enabled === undefined ? true : account.enabled;
        return !enabled;
        });

        // 添加启用的账号
        enabledAccounts.forEach(account => {
        const option = document.createElement('option');
        option.value = account.id;
        option.textContent = `🟢 ${account.id}`;
        select.appendChild(option);
        });

        // 添加禁用的账号
        if (disabledAccounts.length > 0) {
        // 添加分隔线
        if (enabledAccounts.length > 0) {
            const separator = document.createElement('option');
            separator.value = '';
            separator.textContent = '────────────────';
            separator.disabled = true;
            select.appendChild(separator);
        }

        disabledAccounts.forEach(account => {
            const option = document.createElement('option');
            option.value = account.id;
            option.textContent = `🔴 ${account.id} (已禁用)`;
            select.appendChild(option);
        });
        }

        // 恢复之前选择的值
        if (currentValue) {
        select.value = currentValue;
        }
    }
    } catch (error) {
    console.error('加载Cookie列表失败:', error);
    showToast('加载账号列表失败', 'danger');
    }
}

// 刷新商品回复列表
async function refreshItemReplayS() {
    await refreshItemsReplayData();
    showToast('商品列表已刷新', 'success');
}

// 加载所有商品回复
async function loadAllItemReplays() {
    try {
    const response = await fetch(`${apiBase}/itemReplays`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        displayItemReplays(data.items);
    } else {
        throw new Error('获取商品列表失败');
    }
    } catch (error) {
    console.error('加载商品列表失败:', error);
    showToast('加载商品列表失败', 'danger');
    }
}

// 按Cookie加载商品回复
async function loadItemsReplayByCookie() {
    const cookieId = document.getElementById('itemReplayCookieFilter').value;
    if (!cookieId) {
    await loadAllItemReplays();
    return;
    }

    try {
    const response = await fetch(`${apiBase}/itemReplays/cookie/${encodeURIComponent(cookieId)}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        displayItemReplays(data.items);
    } else {
        throw new Error('获取商品列表失败');
    }
    } catch (error) {
    console.error('加载商品列表失败:', error);
    showToast('加载商品列表失败', 'danger');
    }
}

// 显示商品回复列表
function displayItemReplays(items) {
    const tbody = document.getElementById('itemReplaysTableBody');

    if (!items || items.length === 0) {
    tbody.innerHTML = '<tr><td colspan="8" class="text-center text-muted">暂无商品数据</td></tr>';
    // 重置选择状态
    const selectAllCheckbox = document.getElementById('selectAllItemReplies');
    if (selectAllCheckbox) {
        selectAllCheckbox.checked = false;
        selectAllCheckbox.indeterminate = false;
    }
    updateItemReplyBatchDeleteButton();
    return;
    }

    const itemsHtml = items.map(item => {
    // 处理商品标题显示
    let itemTitleDisplay = item.item_title || '未设置';
    if (itemTitleDisplay.length > 30) {
        itemTitleDisplay = itemTitleDisplay.substring(0, 30) + '...';
    }

    // 处理商品详情显示
    let itemDetailDisplay = '未设置';
    if (item.item_detail) {
        try {
        // 尝试解析JSON并提取有用信息
        const detail = JSON.parse(item.item_detail);
        if (detail.content) {
            itemDetailDisplay = detail.content.substring(0, 50) + (detail.content.length > 50 ? '...' : '');
        } else {
            // 如果是纯文本或其他格式，直接显示前50个字符
            itemDetailDisplay = item.item_detail.substring(0, 50) + (item.item_detail.length > 50 ? '...' : '');
        }
        } catch (e) {
        // 如果不是JSON格式，直接显示前50个字符
        itemDetailDisplay = item.item_detail.substring(0, 50) + (item.item_detail.length > 50 ? '...' : '');
        }
    }

    return `
        <tr>
         <td>
            <input type="checkbox" name="itemReplyCheckbox"
                    data-cookie-id="${escapeHtml(item.cookie_id)}"
                    data-item-id="${escapeHtml(item.item_id)}"
                    onchange="updateItemReplySelectAllState()">
        </td>
        <td>${escapeHtml(item.cookie_id)}</td>
        <td>${escapeHtml(item.item_id)}</td>
        <td title="${escapeHtml(item.item_title || '未设置')}">${escapeHtml(itemTitleDisplay)}</td>
        <td title="${escapeHtml(item.item_detail || '未设置')}">${escapeHtml(itemDetailDisplay)}</td>
        <td title="${escapeHtml(item.reply_content || '未设置')}">${escapeHtml(item.reply_content)}</td>
        <td>${formatDateTime(item.updated_at)}</td>
        <td>
            <div class="btn-group" role="group">
            <button class="btn btn-sm btn-outline-primary" onclick="editItemReply('${escapeHtml(item.cookie_id)}', '${escapeHtml(item.item_id)}')" title="编辑详情">
                <i class="bi bi-pencil"></i>
            </button>
            <button class="btn btn-sm btn-outline-danger" onclick="deleteItemReply('${escapeHtml(item.cookie_id)}', '${escapeHtml(item.item_id)}', '${escapeHtml(item.item_title || item.item_id)}')" title="删除">
                <i class="bi bi-trash"></i>
            </button>
            </div>
        </td>
        </tr>
    `;
    }).join('');

    // 更新表格内容
    tbody.innerHTML = itemsHtml;

    // 重置选择状态
    const selectAllCheckbox = document.getElementById('selectAllItemReplies');
    if (selectAllCheckbox) {
    selectAllCheckbox.checked = false;
    selectAllCheckbox.indeterminate = false;
    }
    updateItemReplyBatchDeleteButton();
}

// 显示添加弹框
async function showItemReplayEdit(){
    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('editItemReplyModal'));
    document.getElementById('editReplyCookieIdSelect').value = '';
    document.getElementById('editReplyItemIdSelect').value = '';
    document.getElementById('editReplyItemIdSelect').disabled = true
    document.getElementById('editItemReplyContent').value = '';
    document.getElementById('itemReplayTitle').textContent = '添加商品回复';
    modal.show();
}

// 当账号变化时加载对应商品
async function onCookieChangeForReply() {
  const cookieId = document.getElementById('editReplyCookieIdSelect').value;
  const itemSelect = document.getElementById('editReplyItemIdSelect');

  itemSelect.innerHTML = '<option value="">选择商品</option>';
  if (!cookieId) {
    itemSelect.disabled = true;  // 禁用选择框
    return;
  } else {
    itemSelect.disabled = false; // 启用选择框
  }

  const response = await fetch(`${apiBase}/items/cookie/${encodeURIComponent(cookieId)}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });
    try {
       if (response.ok) {
            const data = await response.json();
            data.items.forEach(item => {
                  const opt = document.createElement('option');
                  opt.value = item.item_id;
                  opt.textContent = `${item.item_id} - ${item.item_title || '无标题'}`;
                  itemSelect.appendChild(opt);
                });
        } else {
            throw new Error('获取商品列表失败');
        }
    }catch (error) {
        console.error('加载商品列表失败:', error);
        showToast('加载商品列表失败', 'danger');
    }
}

// 编辑商品回复
async function editItemReply(cookieId, itemId) {
  try {
    const response = await fetch(`${apiBase}/item-reply/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}`, {
      headers: {
        'Authorization': `Bearer ${authToken}`
      }
    });
    if (response.ok) {
      const data = await response.json();
      document.getElementById('itemReplayTitle').textContent = '编辑商品回复';
      // 填充表单
      document.getElementById('editReplyCookieIdSelect').value = data.cookie_id;
      let res = await onCookieChangeForReply()
      document.getElementById('editReplyItemIdSelect').value = data.item_id;
      document.getElementById('editItemReplyContent').value = data.reply_content || '';

    } else if (response.status === 404) {
      // 如果没有记录，则填充空白内容（用于添加）
//      document.getElementById('editReplyCookieIdSelect').value = data.cookie_id;
//      document.getElementById('editReplyItemIdSelect').value = data.item_id;
//      document.getElementById('editItemReplyContent').value = data.reply_content || '';
    } else {
      throw new Error('获取商品回复失败');
    }

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('editItemReplyModal'));
    modal.show();

  } catch (error) {
    console.error('获取商品回复失败:', error);
    showToast('获取商品回复失败', 'danger');
  }
}

// 保存商品回复
async function saveItemReply() {
  const cookieId = document.getElementById('editReplyCookieIdSelect').value;
  const itemId = document.getElementById('editReplyItemIdSelect').value;
  const replyContent = document.getElementById('editItemReplyContent').value.trim();

  console.log(cookieId)
  console.log(itemId)
  console.log(replyContent)
  if (!cookieId) {
    showToast('请选择账号', 'warning');
    return;
  }

  if (!itemId) {
    showToast('请选择商品', 'warning');
    return;
  }

  if (!replyContent) {
    showToast('请输入商品回复内容', 'warning');
    return;
  }

  try {
    const response = await fetch(`${apiBase}/item-reply/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}`, {
      method: 'PUT',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
      },
      body: JSON.stringify({
        reply_content: replyContent
      })
    });

    if (response.ok) {
      showToast('商品回复保存成功', 'success');

      // 关闭模态框
      const modal = bootstrap.Modal.getInstance(document.getElementById('editItemReplyModal'));
      modal.hide();

      // 可选：刷新数据
      await refreshItemsReplayData?.();
    } else {
      const error = await response.text();
      showToast(`保存失败: ${error}`, 'danger');
    }
  } catch (error) {
    console.error('保存商品回复失败:', error);
    showToast('保存商品回复失败', 'danger');
  }
}

// 删除商品回复
async function deleteItemReply(cookieId, itemId, itemTitle) {
  try {
    const confirmed = confirm(`确定要删除该商品的自动回复吗？\n\n商品ID: ${itemId}\n商品标题: ${itemTitle || '未设置'}\n\n此操作不可撤销！`);
    if (!confirmed) return;

    const response = await fetch(`${apiBase}/item-reply/${encodeURIComponent(cookieId)}/${encodeURIComponent(itemId)}`, {
      method: 'DELETE',
      headers: {
        'Authorization': `Bearer ${authToken}`
      }
    });

    if (response.ok) {
      showToast('商品回复删除成功', 'success');
      await loadItemsReplayByCookie?.(); // 如果你有刷新商品列表的函数
    } else {
      const error = await response.text();
      showToast(`删除失败: ${error}`, 'danger');
    }
  } catch (error) {
    console.error('删除商品回复失败:', error);
    showToast('删除商品回复失败', 'danger');
  }
}

// 批量删除商品回复
async function batchDeleteItemReplies() {
  try {
    const checkboxes = document.querySelectorAll('input[name="itemReplyCheckbox"]:checked');
    if (checkboxes.length === 0) {
      showToast('请选择要删除回复的商品', 'warning');
      return;
    }

    const confirmed = confirm(`确定要删除选中商品的自动回复吗？\n共 ${checkboxes.length} 个商品\n\n此操作不可撤销！`);
    if (!confirmed) return;

    const itemsToDelete = Array.from(checkboxes).map(checkbox => ({
      cookie_id: checkbox.dataset.cookieId,
      item_id: checkbox.dataset.itemId
    }));

    const response = await fetch(`${apiBase}/item-reply/batch`, {
      method: 'DELETE',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
      },
      body: JSON.stringify({ items: itemsToDelete })
    });

    if (response.ok) {
      const result = await response.json();
      showToast(`批量删除回复完成: 成功 ${result.success_count} 个，失败 ${result.failed_count} 个`, 'success');
      await loadItemsReplayByCookie?.();
    } else {
      const error = await response.text();
      showToast(`批量删除失败: ${error}`, 'danger');
    }
  } catch (error) {
    console.error('批量删除商品回复失败:', error);
    showToast('批量删除商品回复失败', 'danger');
  }
}

// ================================
// 商品搜索功能
// ================================
let searchResultsData = [];
let currentSearchPage = 1;
let searchPageSize = 20;
let totalSearchPages = 0;

// 初始化商品搜索功能
function initItemSearch() {
    const searchForm = document.getElementById('itemSearchForm');
    if (searchForm) {
        searchForm.addEventListener('submit', handleItemSearch);
    }
}

// 处理商品搜索
async function handleItemSearch(event) {
    event.preventDefault();

    const keyword = document.getElementById('searchKeyword').value.trim();
    const totalPages = parseInt(document.getElementById('searchTotalPages').value) || 1;
    const pageSize = parseInt(document.getElementById('searchPageSize').value) || 20;

    if (!keyword) {
        showToast('请输入搜索关键词', 'warning');
        return;
    }

    // 显示搜索状态
    showSearchStatus(true);
    hideSearchResults();

    try {
        // 检查是否有有效的cookies账户
        const cookiesCheckResponse = await fetch('/cookies/check', {
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('auth_token')}`
            }
        });

        if (cookiesCheckResponse.ok) {
            const cookiesData = await cookiesCheckResponse.json();
            if (!cookiesData.hasValidCookies) {
                showToast('搜索失败：系统中不存在有效的账户信息。请先在Cookie管理中添加有效的闲鱼账户。', 'warning');
                showSearchStatus(false);
                return;
            }
        }

        const token = localStorage.getItem('auth_token');
        
        // 启动会话检查器（在搜索过程中检查是否有验证会话）
        let sessionChecker = null;
        let checkCount = 0;
        const maxChecks = 30; // 最多检查30次（30秒）
        let isSearchCompleted = false; // 标记搜索是否完成
        
        sessionChecker = setInterval(async () => {
            // 如果搜索已完成，停止检查
            if (isSearchCompleted) {
                if (sessionChecker) {
                    clearInterval(sessionChecker);
                    sessionChecker = null;
                }
                return;
            }
            
            try {
                checkCount++;
                const checkResponse = await fetch('/api/captcha/sessions');
                const checkData = await checkResponse.json();
                
                if (checkData.sessions && checkData.sessions.length > 0) {
                    for (const session of checkData.sessions) {
                        if (!session.completed) {
                            console.log(`🎨 检测到验证会话: ${session.session_id}`);
                            if (sessionChecker) {
                                clearInterval(sessionChecker);
                                sessionChecker = null;
                            }
                            
                            // 确保监控已启动
                            if (typeof startCaptchaSessionMonitor === 'function') {
                                startCaptchaSessionMonitor();
                            }
                            
                            // 弹出验证窗口
                            if (typeof showCaptchaVerificationModal === 'function') {
                                showCaptchaVerificationModal(session.session_id);
                                showToast('🎨 检测到滑块验证，请完成验证', 'warning');
                                
                                // 停止搜索时的会话检查器，因为已经弹窗了，由弹窗的监控接管
                                if (sessionChecker) {
                                    clearInterval(sessionChecker);
                                    sessionChecker = null;
                                    console.log('✅ 已弹窗，停止搜索时的会话检查器');
                                }
                            } else {
                                // 如果函数未定义，使用备用方案
                                console.error('showCaptchaVerificationModal 未定义，使用备用方案');
                                window.location.href = `/api/captcha/control/${session.session_id}`;
                            }
                            return;
                        }
                    }
                }
                
                // 如果检查次数超过限制，停止检查
                if (checkCount >= maxChecks) {
                    if (sessionChecker) {
                        clearInterval(sessionChecker);
                        sessionChecker = null;
                    }
                }
            } catch (error) {
                console.error('检查验证会话失败:', error);
            }
        }, 1000); // 每秒检查一次
        
        // 使用 Promise 包装，以便使用 finally
        const fetchPromise = fetch('/items/search_multiple', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${token}`
            },
            body: JSON.stringify({
                keyword: keyword,
                total_pages: totalPages
            })
        });

        // 请求完成后，停止会话检查器
        fetchPromise.finally(() => {
            isSearchCompleted = true;
            if (sessionChecker) {
                clearInterval(sessionChecker);
                sessionChecker = null;
                console.log('✅ 搜索完成，已停止会话检查器');
            }
        });

        const response = await fetchPromise;
        console.log('API响应状态:', response.status);

        if (response.ok) {
            const data = await response.json();
            console.log('API返回的完整数据:', data);

            // 检查是否需要滑块验证
            if (data.need_captcha || data.status === 'need_verification') {
                console.log('检测到需要滑块验证');
                showSearchStatus(false);
                
                // 显示滑块验证模态框
                const sessionId = data.session_id || 'default';
                const modal = showCaptchaVerificationModal(sessionId);
                
                try {
                    // 等待用户完成验证
                    await checkCaptchaCompletion(modal, sessionId);
                    
                    // 验证成功，显示搜索状态并重新发起搜索请求
                    showSearchStatus(true);
                    document.getElementById('searchProgress').textContent = '验证成功，继续搜索商品...';
                    
                    // 重新发起搜索请求
                    const retryResponse = await fetch('/items/search_multiple', {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/json',
                            'Authorization': `Bearer ${token}`
                        },
                        body: JSON.stringify({
                            keyword: keyword,
                            total_pages: totalPages
                        })
                    });
                    
                    if (retryResponse.ok) {
                        const retryData = await retryResponse.json();
                        
                        // 再次检查是否需要验证（理论上不应该再需要）
                        if (retryData.need_captcha || retryData.status === 'need_verification') {
                            showSearchStatus(false);
                            showToast('验证后仍需要滑块，请联系管理员', 'danger');
                            return;
                        }
                        
                        // 处理搜索结果
                        searchResultsData = retryData.data || [];
                        console.log('验证后搜索结果:', searchResultsData);
                        console.log('searchResultsData长度:', searchResultsData.length);

                        searchPageSize = pageSize;
                        currentSearchPage = 1;
                        totalSearchPages = Math.ceil(searchResultsData.length / searchPageSize);

                        if (retryData.error) {
                            showToast(`搜索完成，但遇到问题: ${retryData.error}`, 'warning');
                        }

                        showSearchStatus(false);
                        displaySearchResults();
                        updateSearchStats(retryData);
                    } else {
                        const retryError = await retryResponse.json();
                        showSearchStatus(false);
                        showToast(`验证后搜索失败: ${retryError.detail || '未知错误'}`, 'danger');
                        showNoSearchResults();
                    }
                } catch (error) {
                    console.error('滑块验证失败:', error);
                    showSearchStatus(false);
                    showToast('滑块验证失败或超时', 'danger');
                    showNoSearchResults();
                }
                return;
            }

            // 正常搜索结果（无需验证）
            // 修复字段名：使用data.data而不是data.items
            searchResultsData = data.data || [];
            console.log('设置searchResultsData:', searchResultsData);
            console.log('searchResultsData长度:', searchResultsData.length);
            console.log('完整响应数据:', data);

            searchPageSize = pageSize;
            currentSearchPage = 1;
            totalSearchPages = Math.ceil(searchResultsData.length / searchPageSize);

            if (data.error) {
                showToast(`搜索完成，但遇到问题: ${data.error}`, 'warning');
            }

            showSearchStatus(false);
            
            // 确保显示搜索结果
            if (searchResultsData.length > 0) {
            displaySearchResults();
            updateSearchStats(data);
            } else {
                console.warn('搜索结果为空，显示无结果提示');
                showNoSearchResults();
            }
        } else {
            const errorData = await response.json();
            showSearchStatus(false);
            showToast(`搜索失败: ${errorData.detail || '未知错误'}`, 'danger');
            showNoSearchResults();
        }
    } catch (error) {
        console.error('搜索商品失败:', error);
        showSearchStatus(false);
        showToast('搜索商品失败', 'danger');
        showNoSearchResults();
    }
}

// 显示搜索状态
function showSearchStatus(isSearching) {
    const statusDiv = document.getElementById('searchStatus');
    const progressDiv = document.getElementById('searchProgress');

    if (isSearching) {
        statusDiv.style.display = 'block';
        progressDiv.textContent = '正在搜索商品数据...';
    } else {
        statusDiv.style.display = 'none';
    }
}

// 隐藏搜索结果
function hideSearchResults() {
    document.getElementById('searchResults').style.display = 'none';
    document.getElementById('searchResultStats').style.display = 'none';
    document.getElementById('noSearchResults').style.display = 'none';
}

// 显示搜索结果
function displaySearchResults() {
    if (searchResultsData.length === 0) {
        showNoSearchResults();
        return;
    }

    const startIndex = (currentSearchPage - 1) * searchPageSize;
    const endIndex = startIndex + searchPageSize;
    const pageItems = searchResultsData.slice(startIndex, endIndex);

    const container = document.getElementById('searchResultsContainer');
    container.innerHTML = '';

    pageItems.forEach(item => {
        const itemCard = createItemCard(item);
        container.appendChild(itemCard);
    });

    updateSearchPagination();
    document.getElementById('searchResults').style.display = 'block';
}

// 创建商品卡片
function createItemCard(item) {
    console.log('createItemCard被调用，item数据:', item);
    console.log('item的所有字段:', Object.keys(item));

    const col = document.createElement('div');
    col.className = 'col-md-6 col-lg-4 col-xl-3 mb-4';

    // 修复字段映射：使用main_image而不是image_url
    const imageUrl = item.main_image || item.image_url || 'https://via.placeholder.com/200x200?text=图片加载失败';
    const wantCount = item.want_count || 0;

    console.log('处理后的数据:', {
        title: item.title,
        price: item.price,
        seller_name: item.seller_name,
        imageUrl: imageUrl,
        wantCount: wantCount,
        url: item.item_url || item.url
    });

    col.innerHTML = `
        <div class="card item-card h-100">
            <img src="${escapeHtml(imageUrl)}" class="item-image" alt="${escapeHtml(item.title)}"
                 onerror="this.src='https://via.placeholder.com/200x200?text=图片加载失败'"
                 style="width: 100%; height: 200px; object-fit: cover; border-radius: 10px;">
            <div class="card-body d-flex flex-column">
                <h6 class="card-title" title="${escapeHtml(item.title)}">
                    ${escapeHtml(item.title.length > 50 ? item.title.substring(0, 50) + '...' : item.title)}
                </h6>
                <div class="price mb-2" style="color: #e74c3c; font-weight: bold; font-size: 1.2em;">
                    ${escapeHtml(item.price)}
                </div>
                <div class="seller-name mb-2" style="color: #6c757d; font-size: 0.9em;">
                    <i class="bi bi-person me-1"></i>
                    ${escapeHtml(item.seller_name)}
                </div>
                ${wantCount > 0 ? `<div class="want-count mb-2">
                    <i class="bi bi-heart-fill me-1" style="color: #ff6b6b;"></i>
                    <span class="badge bg-danger">${wantCount}人想要</span>
                </div>` : ''}
                <div class="mt-auto">
                    <a href="${escapeHtml(item.item_url || item.url)}" target="_blank" class="btn btn-primary btn-sm w-100">
                        <i class="bi bi-eye me-1"></i>查看详情
                    </a>
                </div>
            </div>
        </div>
    `;

    return col;
}

// 更新搜索统计
function updateSearchStats(data) {
    document.getElementById('totalItemsFound').textContent = searchResultsData.length;
    document.getElementById('totalPagesSearched').textContent = data.total_pages || 0;
    document.getElementById('currentDisplayPage').textContent = currentSearchPage;
    document.getElementById('totalDisplayPages').textContent = totalSearchPages;
    document.getElementById('searchResultStats').style.display = 'block';
}

// 更新搜索分页
function updateSearchPagination() {
    const paginationContainer = document.getElementById('searchPagination');
    paginationContainer.innerHTML = '';

    if (totalSearchPages <= 1) return;

    const pagination = document.createElement('nav');
    pagination.innerHTML = `
        <ul class="pagination">
            <li class="page-item ${currentSearchPage === 1 ? 'disabled' : ''}">
                <a class="page-link" href="#" onclick="changeSearchPage(${currentSearchPage - 1})">上一页</a>
            </li>
            ${generateSearchPageNumbers()}
            <li class="page-item ${currentSearchPage === totalSearchPages ? 'disabled' : ''}">
                <a class="page-link" href="#" onclick="changeSearchPage(${currentSearchPage + 1})">下一页</a>
            </li>
        </ul>
    `;

    paginationContainer.appendChild(pagination);
}

// 生成搜索分页页码
function generateSearchPageNumbers() {
    let pageNumbers = '';
    const maxVisiblePages = 5;
    let startPage = Math.max(1, currentSearchPage - Math.floor(maxVisiblePages / 2));
    let endPage = Math.min(totalSearchPages, startPage + maxVisiblePages - 1);

    if (endPage - startPage + 1 < maxVisiblePages) {
        startPage = Math.max(1, endPage - maxVisiblePages + 1);
    }

    for (let i = startPage; i <= endPage; i++) {
        pageNumbers += `
            <li class="page-item ${i === currentSearchPage ? 'active' : ''}">
                <a class="page-link" href="#" onclick="changeSearchPage(${i})">${i}</a>
            </li>
        `;
    }

    return pageNumbers;
}

// 切换搜索页面
function changeSearchPage(page) {
    if (page < 1 || page > totalSearchPages || page === currentSearchPage) return;

    currentSearchPage = page;
    displaySearchResults();
    updateSearchStats({ total_pages: document.getElementById('totalPagesSearched').textContent });
}

// 显示无搜索结果
function showNoSearchResults() {
    document.getElementById('noSearchResults').style.display = 'block';
    document.getElementById('searchResults').style.display = 'none';
    document.getElementById('searchResultStats').style.display = 'none';
}

// 导出搜索结果
function exportSearchResults() {
    if (searchResultsData.length === 0) {
        showToast('没有可导出的搜索结果', 'warning');
        return;
    }

    try {
        // 准备导出数据
        const exportData = searchResultsData.map(item => ({
            '商品标题': item.title,
            '价格': item.price,
            '卖家': item.seller_name,
            '想要人数': item.want_count || 0,
            '商品链接': item.url,
            '图片链接': item.image_url
        }));

        // 转换为CSV格式
        const headers = Object.keys(exportData[0]);
        const csvContent = [
            headers.join(','),
            ...exportData.map(row => headers.map(header => `"${row[header] || ''}"`).join(','))
        ].join('\n');

        // 创建下载链接
        const blob = new Blob(['\ufeff' + csvContent], { type: 'text/csv;charset=utf-8;' });
        const link = document.createElement('a');
        const url = URL.createObjectURL(blob);
        link.setAttribute('href', url);
        link.setAttribute('download', `商品搜索结果_${new Date().toISOString().slice(0, 10)}.csv`);
        link.style.visibility = 'hidden';
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);

        showToast('搜索结果导出成功', 'success');
    } catch (error) {
        console.error('导出搜索结果失败:', error);
        showToast('导出搜索结果失败', 'danger');
    }
}

