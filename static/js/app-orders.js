// ================================
// 订单管理功能
// ================================

function isOrdersSectionActive() {
    const section = document.getElementById('orders-section');
    return !!section && section.classList.contains('active');
}

function stopOrdersStream() {
    ordersStreamShouldRun = false;

    if (ordersStreamReconnectTimer) {
        clearTimeout(ordersStreamReconnectTimer);
        ordersStreamReconnectTimer = null;
    }

    if (ordersStreamAbortController) {
        ordersStreamAbortController.abort();
        ordersStreamAbortController = null;
    }
}

window.addEventListener('pagehide', stopOrdersStream);

function scheduleOrdersStreamReconnect() {
    if (!ordersStreamShouldRun || !isOrdersSectionActive()) return;
    if (ordersStreamReconnectTimer) return;

    const retryDelay = Math.min(10000, [1000, 2000, 5000, 10000][Math.min(ordersStreamRetryCount, 3)]);
    ordersStreamReconnectTimer = setTimeout(() => {
        ordersStreamReconnectTimer = null;
        startOrdersStream();
    }, retryDelay);
}

function handleOrdersStreamEvent(eventName, payloadText) {
    if (!payloadText) return;
    if (eventName === 'ping' || eventName === 'stream.ready') return;

    try {
        const payload = JSON.parse(payloadText);
        if (eventName === 'order.updated' && payload.order) {
            applyRealtimeOrderUpdate(payload.order);
        }
    } catch (error) {
        console.error('解析订单实时事件失败:', error, payloadText);
    }
}

function applyRealtimeOrderUpdate(order) {
    if (!order || !order.order_id) return;

    const existingIndex = allOrdersData.findIndex(item => item.order_id === order.order_id);
    if (existingIndex === -1) {
        refreshOrdersData();
        return;
    }

    allOrdersData[existingIndex] = {
        ...allOrdersData[existingIndex],
        ...order,
    };

    filterOrders(false);
}

async function consumeOrdersStream(response, controller) {
    if (!response.body) {
        throw new Error('订单实时流不可用');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';

    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        if (controller.signal.aborted) break;

        buffer += decoder.decode(value, { stream: true });
        const chunks = buffer.split(/\r?\n\r?\n/);
        buffer = chunks.pop() || '';

        chunks.forEach(chunk => {
            let eventName = 'message';
            const dataLines = [];

            chunk.split(/\r?\n/).forEach(line => {
                if (line.startsWith('event:')) {
                    eventName = line.slice(6).trim();
                } else if (line.startsWith('data:')) {
                    dataLines.push(line.slice(5).trimStart());
                }
            });

            handleOrdersStreamEvent(eventName, dataLines.join('\n'));
        });
    }
}

async function startOrdersStream() {
    if (!authToken || !isOrdersSectionActive()) return;
    if (ordersStreamAbortController) return;

    ordersStreamShouldRun = true;

    if (ordersStreamReconnectTimer) {
        clearTimeout(ordersStreamReconnectTimer);
        ordersStreamReconnectTimer = null;
    }

    const controller = new AbortController();
    ordersStreamAbortController = controller;

    try {
        const response = await fetch(`${apiBase}/api/orders/stream`, {
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Accept': 'text/event-stream'
            },
            cache: 'no-store',
            signal: controller.signal
        });

        if (response.status === 401) {
            localStorage.removeItem('auth_token');
            window.location.href = '/';
            return;
        }

        if (!response.ok) {
            throw new Error(`订单实时流连接失败: HTTP ${response.status}`);
        }

        ordersStreamRetryCount = 0;
        await consumeOrdersStream(response, controller);
    } catch (error) {
        if (!controller.signal.aborted) {
            ordersStreamRetryCount += 1;
            console.error('订单实时流异常:', error);
            scheduleOrdersStreamReconnect();
        }
    } finally {
        if (ordersStreamAbortController === controller) {
            ordersStreamAbortController = null;
        }

        if (!controller.signal.aborted && ordersStreamShouldRun && isOrdersSectionActive()) {
            scheduleOrdersStreamReconnect();
        }
    }
}

// 加载订单列表
async function loadOrders() {
    try {
        // 先加载Cookie列表用于筛选
        await loadOrderCookieFilter();

        // 加载订单列表
        await refreshOrdersData();

        startOrdersStream();
    } catch (error) {
        console.error('加载订单列表失败:', error);
        showToast('加载订单列表失败', 'danger');
    }
}

// 只刷新订单数据，不重新加载筛选器
async function refreshOrdersData() {
    try {
        await loadAllOrders();
    } catch (error) {
        console.error('刷新订单数据失败:', error);
        showToast('刷新订单数据失败', 'danger');
    }
}

// 加载Cookie筛选选项
async function loadOrderCookieFilter() {
    try {
        const select = document.getElementById('orderCookieFilter');
        const previousValue = select ? select.value : '';

        const accounts = await fetchOrderSyncAccounts(true);
        if (select) {
            renderOrderAccountOptions(select, accounts, { includeAllOption: true });

            if (previousValue && accounts.some(account => account.id === previousValue)) {
                select.value = previousValue;
            }
        }
    } catch (error) {
        console.error('加载Cookie选项失败:', error);
    }
}

// 加载所有订单
async function loadAllOrders() {
    try {
        const response = await fetch(`${apiBase}/api/orders`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        const data = await response.json();
        if (data.success) {
            allOrdersData = data.data || [];
            // 历史同步后优先按平台下单时间排序，回退到入库时间
            allOrdersData.sort((a, b) => {
                const bTime = parseUtcDateTime(getOrderPrimarySortTime(b))?.getTime() || 0;
                const aTime = parseUtcDateTime(getOrderPrimarySortTime(a))?.getTime() || 0;
                return bTime - aTime;
            });

            // 应用当前筛选条件
            filterOrders(false);
        } else {
            console.error('加载订单失败:', data.message);
            showToast('加载订单数据失败: ' + data.message, 'danger');
        }
    } catch (error) {
        console.error('加载订单失败:', error);
        showToast('加载订单数据失败，请检查网络连接', 'danger');
    }
}

// 根据Cookie加载订单
async function loadOrdersByCookie() {
    filterOrders(false);
}

// 筛选订单
function filterOrders(resetPage = true) {
    const searchKeyword = document.getElementById('orderSearchInput')?.value.toLowerCase() || '';
    const statusFilter = document.getElementById('orderStatusFilter')?.value || '';
    const cookieFilter = document.getElementById('orderCookieFilter')?.value || '';
    const normalizedStatusFilter = statusFilter ? normalizeOrderStatus(statusFilter) : '';

    filteredOrdersData = allOrdersData.filter(order => {
        // 搜索关键词筛选（订单ID、商品ID、买家ID、买家昵称）
        const matchesSearch = !searchKeyword ||
            (order.order_id && order.order_id.toLowerCase().includes(searchKeyword)) ||
            (order.item_id && order.item_id.toLowerCase().includes(searchKeyword)) ||
            (order.buyer_id && order.buyer_id.toLowerCase().includes(searchKeyword)) ||
            (order.buyer_nick && order.buyer_nick.toLowerCase().includes(searchKeyword));

        const matchesCookie = !cookieFilter || order.cookie_id === cookieFilter;
        const matchesStatus = !normalizedStatusFilter || normalizeOrderStatus(order.order_status) === normalizedStatusFilter;

        return matchesSearch && matchesCookie && matchesStatus;
    });

    currentOrderSearchKeyword = searchKeyword;
    if (resetPage) {
        currentOrdersPage = 1; // 重置到第一页
    }

    updateOrdersDisplay();
}

// 更新订单显示
function updateOrdersDisplay() {
    const computedTotalPages = filteredOrdersData.length === 0 ? 0 : Math.ceil(filteredOrdersData.length / ordersPerPage);
    if (computedTotalPages === 0) {
        currentOrdersPage = 1;
    } else {
        currentOrdersPage = Math.min(currentOrdersPage, computedTotalPages);
    }

    displayOrders();
    updateOrdersPagination();
    updateOrdersSearchStats();
}

// 显示订单列表
function displayOrders() {
    const tbody = document.getElementById('ordersTableBody');
    if (!tbody) return;

    if (filteredOrdersData.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="11" class="text-center text-muted py-4">
                    <i class="bi bi-inbox display-6 d-block mb-2"></i>
                    ${currentOrderSearchKeyword ? '没有找到匹配的订单' : '暂无订单数据'}
                </td>
            </tr>
        `;
        return;
    }

    // 计算分页
    totalOrdersPages = Math.ceil(filteredOrdersData.length / ordersPerPage);
    const startIndex = (currentOrdersPage - 1) * ordersPerPage;
    const endIndex = startIndex + ordersPerPage;
    const pageOrders = filteredOrdersData.slice(startIndex, endIndex);

    // 生成表格行
    tbody.innerHTML = pageOrders.map(order => createOrderRow(order)).join('');
}

// 创建订单行HTML
function createOrderRow(order) {
    const statusClass = getOrderStatusClass(order.order_status);
    const statusText = getOrderStatusText(order.order_status);
    const normalizedStatus = normalizeOrderStatus(order.order_status);
    const orderId = escapeHtml(order.order_id || '');
    const itemId = escapeHtml(order.item_id || '-');
    const buyerId = escapeHtml(order.buyer_id || '-');
    const buyerNick = escapeHtml(order.buyer_nick || '-');
    const cookieId = escapeHtml(order.cookie_id || '-');
    const specName = escapeHtml(order.spec_name || '');
    const specValue = escapeHtml(order.spec_value || '');
    const specName2 = escapeHtml(order.spec_name_2 || '');
    const specValue2 = escapeHtml(order.spec_value_2 || '');
    const quantity = escapeHtml(order.quantity || '-');
    const amountDisplay = escapeHtml(formatOrderAmountDisplay(order.amount));

    // 判断是否可以手动发货（允许多次发货，除了交易关闭的订单）
    const canDeliver = !['cancelled', 'refunding'].includes(normalizedStatus);

    let specHtml = '-';
    if (order.spec_name && order.spec_value) {
        specHtml = `<small class="text-muted">${specName}:</small><br>${specValue}`;
        if (order.spec_name_2 && order.spec_value_2) {
            specHtml += `<br><small class="text-muted">${specName2}:</small><br>${specValue2}`;
        }
    }

    return `
        <tr>
            <td>
                <input type="checkbox" class="order-checkbox" value="${orderId}">
            </td>
            <td>
                <span class="text-truncate d-inline-block" style="max-width: 120px;" title="${orderId}">
                    ${orderId}
                </span>
            </td>
            <td>
                <span class="text-truncate d-inline-block" style="max-width: 100px;" title="${itemId === '-' ? '' : itemId}">
                    ${itemId}
                </span>
            </td>
            <td>
                <span class="text-truncate d-inline-block" style="max-width: 80px;" title="${buyerId === '-' ? '' : buyerId}">
                    ${buyerId}
                </span>
            </td>
            <td>
                <span class="text-truncate d-inline-block" style="max-width: 100px;" title="${buyerNick === '-' ? '' : buyerNick}">
                    ${buyerNick}
                </span>
            </td>
            <td>
                ${specHtml}
            </td>
            <td>${quantity}</td>
            <td>
                <span class="text-success fw-bold">${amountDisplay}</span>
            </td>
            <td>
                <span class="badge ${statusClass}">${escapeHtml(statusText)}</span>
            </td>
            <td>
                <span class="text-truncate d-inline-block" style="max-width: 80px;" title="${cookieId === '-' ? '' : cookieId}">
                    ${cookieId}
                </span>
            </td>
            <td>
                <div class="btn-group btn-group-sm" role="group">
                    <button class="btn btn-outline-success btn-sm order-action-btn" data-order-action="deliver" data-order-id="${orderId}" title="手动发货" ${canDeliver ? '' : 'disabled'}>
                        <i class="bi bi-truck"></i>
                    </button>
                    <button class="btn btn-outline-info btn-sm order-action-btn" data-order-action="refresh" data-order-id="${orderId}" title="刷新状态">
                        <i class="bi bi-arrow-repeat"></i>
                    </button>
                    <button class="btn btn-outline-primary btn-sm order-action-btn" data-order-action="detail" data-order-id="${orderId}" title="查看详情">
                        <i class="bi bi-eye"></i>
                    </button>
                    <button class="btn btn-outline-danger btn-sm order-action-btn" data-order-action="delete" data-order-id="${orderId}" title="删除">
                        <i class="bi bi-trash"></i>
                    </button>
                </div>
            </td>
        </tr>
    `;
}

// 获取订单状态样式类
function getOrderStatusClass(status) {
    const normalizedStatus = normalizeOrderStatus(status);
    const statusMap = {
        'processing': 'bg-warning text-dark',
        'pending_payment': 'bg-warning text-dark',
        'pending_ship': 'bg-info text-white',
        'partial_success': 'bg-primary-subtle text-primary-emphasis',
        'partial_pending_finalize': 'bg-warning-subtle text-warning-emphasis',
        'shipped': 'bg-primary text-white',
        'completed': 'bg-success text-white',
        'success': 'bg-success text-white',
        'refunding': 'bg-warning text-dark',
        'refund_cancelled': 'bg-info text-dark',
        'cancelled': 'bg-secondary text-white',
        'unknown': 'bg-secondary text-white'
    }; 
    return statusMap[normalizedStatus] || statusMap[status] || 'bg-secondary text-white';
}

// 获取订单状态文本
function getOrderStatusText(status) {
    const normalizedStatus = normalizeOrderStatus(status);
    const statusMap = {
        'processing': '处理中',
        'pending_payment': '待付款',
        'pending_ship': '待发货',
        'partial_success': '部分发货',
        'partial_pending_finalize': '部分待收尾',
        'shipped': '已发货',
        'completed': '交易成功',
        'success': '交易成功',
        'refunding': '申请退款中',
        'refund_cancelled': '退款已撤销',
        'cancelled': '交易关闭',
        'unknown': '未知'
    };
    return statusMap[normalizedStatus] || statusMap[status] || status || '未知';
}

// 更新订单分页
function updateOrdersPagination() {
    const pageInfo = document.getElementById('ordersPageInfo');
    const pageInput = document.getElementById('ordersPageInput');
    const totalPagesSpan = document.getElementById('ordersTotalPages');

    if (pageInfo) {
        const startIndex = (currentOrdersPage - 1) * ordersPerPage + 1;
        const endIndex = Math.min(currentOrdersPage * ordersPerPage, filteredOrdersData.length);
        pageInfo.textContent = `显示第 ${startIndex}-${endIndex} 条，共 ${filteredOrdersData.length} 条记录`;
    }

    if (pageInput) {
        pageInput.value = currentOrdersPage;
    }

    if (totalPagesSpan) {
        totalPagesSpan.textContent = totalOrdersPages;
    }

    // 更新分页按钮状态
    const firstPageBtn = document.getElementById('ordersFirstPage');
    const prevPageBtn = document.getElementById('ordersPrevPage');
    const nextPageBtn = document.getElementById('ordersNextPage');
    const lastPageBtn = document.getElementById('ordersLastPage');

    if (firstPageBtn) firstPageBtn.disabled = currentOrdersPage === 1;
    if (prevPageBtn) prevPageBtn.disabled = currentOrdersPage === 1;
    if (nextPageBtn) nextPageBtn.disabled = currentOrdersPage === totalOrdersPages || totalOrdersPages === 0;
    if (lastPageBtn) lastPageBtn.disabled = currentOrdersPage === totalOrdersPages || totalOrdersPages === 0;
}

// 更新搜索统计信息
function updateOrdersSearchStats() {
    const searchStats = document.getElementById('orderSearchStats');
    const searchStatsText = document.getElementById('orderSearchStatsText');

    if (searchStats && searchStatsText) {
        if (currentOrderSearchKeyword) {
            searchStatsText.textContent = `搜索 "${currentOrderSearchKeyword}" 找到 ${filteredOrdersData.length} 个结果`;
            searchStats.style.display = 'block';
        } else {
            searchStats.style.display = 'none';
        }
    }
}

// 跳转到指定页面
function goToOrdersPage(page) {
    if (page < 1 || page > totalOrdersPages) return;

    currentOrdersPage = page;
    updateOrdersDisplay();
}

// 初始化订单搜索功能
function initOrdersSearch() {
    // 初始化分页大小
    const pageSizeSelect = document.getElementById('ordersPageSize');
    if (pageSizeSelect) {
        ordersPerPage = parseInt(pageSizeSelect.value) || 20;
        pageSizeSelect.addEventListener('change', changeOrdersPageSize);
    }

    // 初始化搜索输入框事件监听器
    const searchInput = document.getElementById('orderSearchInput');
    if (searchInput) {
        // 使用防抖来避免频繁搜索
        let searchTimeout;
        searchInput.addEventListener('input', function() {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => {
                filterOrders();
            }, 300); // 300ms 防抖延迟
        });
    }

    // 初始化页面输入框事件监听器
    const pageInput = document.getElementById('ordersPageInput');
    if (pageInput) {
        pageInput.addEventListener('keydown', handleOrdersPageInput);
    }
}

// 处理分页大小变化
function changeOrdersPageSize() {
    const pageSizeSelect = document.getElementById('ordersPageSize');
    if (pageSizeSelect) {
        ordersPerPage = parseInt(pageSizeSelect.value) || 20;
        currentOrdersPage = 1; // 重置到第一页
        updateOrdersDisplay();
    }
}

// 处理页面输入
function handleOrdersPageInput(event) {
    if (event.key === 'Enter') {
        const pageInput = document.getElementById('ordersPageInput');
        if (pageInput) {
            const page = parseInt(pageInput.value);
            if (page >= 1 && page <= totalOrdersPages) {
                goToOrdersPage(page);
            } else {
                pageInput.value = currentOrdersPage; // 恢复当前页码
                showToast('页码超出范围', 'warning');
            }
        }
    }
}

// 刷新订单列表
async function refreshOrders() {
    await refreshOrdersData();
    showToast('订单列表已刷新', 'success');
}

function getOrderPrimarySortTime(order) {
    const platformCreatedAt = String(order?.platform_created_at || '').trim();
    if (platformCreatedAt) {
        return platformCreatedAt;
    }

    const createdAt = String(order?.created_at || '').trim();
    return createdAt || null;
}

function getRelativeBeijingDateInputValue(offsetDays = 0) {
    return getBeijingDateKey(new Date(Date.now() + offsetDays * 24 * 60 * 60 * 1000));
}

async function fetchOrderSyncAccounts(forceRefresh = false) {
    if (!forceRefresh && orderHistorySyncAccounts.length > 0) {
        return orderHistorySyncAccounts;
    }

    const response = await fetch(`${apiBase}/cookies/details`, {
        headers: {
            'Authorization': `Bearer ${authToken}`
        }
    });

    if (!response.ok) {
        throw new Error(`获取账号列表失败: HTTP ${response.status}`);
    }

    const accounts = await response.json();
    orderHistorySyncAccounts = Array.isArray(accounts) ? accounts : [];
    return orderHistorySyncAccounts;
}

function formatOrderAccountLabel(account) {
    const accountId = String(account?.id || '').trim();
    const remark = String(account?.remark || '').trim();
    if (remark) {
        return `${remark} (${accountId})`;
    }
    return accountId || '未命名账号';
}

function renderOrderAccountOptions(select, accounts, options = {}) {
    if (!select) return;

    const {
        includeAllOption = false,
        allOptionLabel = '所有账号',
    } = options;

    const previousValue = select.value;
    select.innerHTML = includeAllOption ? `<option value="">${allOptionLabel}</option>` : '';

    (accounts || []).forEach(account => {
        const accountId = String(account?.id || '').trim();
        if (!accountId) return;

        const option = document.createElement('option');
        option.value = accountId;
        option.textContent = formatOrderAccountLabel(account);
        select.appendChild(option);
    });

    if (previousValue && Array.from(select.options).some(option => option.value === previousValue)) {
        select.value = previousValue;
    }
}

function resetOrderHistorySyncProgress() {
    renderOrderHistorySyncJob({
        status: 'idle',
        message: '选择账号和日期范围后即可开始同步。',
        request: {},
        accounts_total: 0,
        accounts_completed: 0,
        orders_discovered: 0,
        orders_processed: 0,
        orders_saved: 0,
        orders_skipped: 0,
        orders_failed: 0,
        matched_orders: 0,
        warnings: [],
    });
}

function setOrderHistorySyncFormDisabled(disabled) {
    [
        'orderHistorySyncCookieId',
        'orderHistorySyncStartDate',
        'orderHistorySyncEndDate',
        'orderHistorySyncMaxOrders',
        'orderHistorySyncFetchDetails',
    ].forEach(id => {
        const element = document.getElementById(id);
        if (element) {
            element.disabled = disabled;
        }
    });

    const startBtn = document.getElementById('orderHistorySyncStartBtn');
    const cancelBtn = document.getElementById('orderHistorySyncCancelBtn');
    if (startBtn) {
        startBtn.disabled = disabled;
        startBtn.innerHTML = disabled
            ? '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>同步中'
            : '<i class="bi bi-play-circle"></i> 开始同步';
    }
    if (cancelBtn) {
        cancelBtn.style.display = disabled ? '' : 'none';
        cancelBtn.disabled = false;
    }
}

function stopOrderHistorySyncPolling() {
    if (orderHistorySyncPollingTimer) {
        clearTimeout(orderHistorySyncPollingTimer);
        orderHistorySyncPollingTimer = null;
    }
}

function scheduleOrderHistorySyncPolling(jobId) {
    stopOrderHistorySyncPolling();
    orderHistorySyncPollingTimer = setTimeout(() => {
        fetchOrderHistorySyncStatus(jobId).catch(error => {
            console.error('轮询历史订单同步状态失败:', error);
        });
    }, 2000);
}

function getOrderHistorySyncStatusMeta(job) {
    const status = String(job?.status || '').toLowerCase();
    const statusMap = {
        idle: { label: '待命', badgeClass: 'bg-secondary text-white', progressClass: 'bg-secondary', title: '未开始' },
        pending: { label: '排队中', badgeClass: 'bg-secondary text-white', progressClass: 'bg-secondary', title: '等待执行' },
        running: { label: '进行中', badgeClass: 'bg-primary text-white', progressClass: 'bg-primary', title: '同步中' },
        completed: { label: '已完成', badgeClass: 'bg-success text-white', progressClass: 'bg-success', title: '同步完成' },
        failed: { label: '失败', badgeClass: 'bg-danger text-white', progressClass: 'bg-danger', title: '同步失败' },
        cancelled: { label: '已取消', badgeClass: 'bg-warning text-dark', progressClass: 'bg-warning', title: '同步已取消' },
    };
    return statusMap[status] || statusMap.idle;
}

function renderOrderHistorySyncJob(job) {
    const statusMeta = getOrderHistorySyncStatusMeta(job);
    const request = job?.request || {};
    const accountsTotal = Number(job?.accounts_total || 0);
    const accountsCompleted = Number(job?.accounts_completed || 0);
    const ordersDiscovered = Number(job?.orders_discovered || 0);
    const matchedOrders = Number(job?.matched_orders || 0);
    const ordersSaved = Number(job?.orders_saved || 0);
    const ordersFailed = Number(job?.orders_failed || 0);
    const ordersProcessed = Number(job?.orders_processed || 0);
    const ordersSkipped = Number(job?.orders_skipped || 0);
    const warnings = Array.isArray(job?.warnings) ? job.warnings : [];

    const statusText = document.getElementById('orderHistorySyncStatusText');
    const messageText = document.getElementById('orderHistorySyncMessageText');
    const statusBadge = document.getElementById('orderHistorySyncStatusBadge');
    const progressBar = document.getElementById('orderHistorySyncProgressBar');
    const accountsStat = document.getElementById('orderHistorySyncAccountsStat');
    const discoveredStat = document.getElementById('orderHistorySyncDiscoveredStat');
    const matchedStat = document.getElementById('orderHistorySyncMatchedStat');
    const savedStat = document.getElementById('orderHistorySyncSavedStat');
    const metaText = document.getElementById('orderHistorySyncMetaText');
    const currentText = document.getElementById('orderHistorySyncCurrentText');
    const warningsWrap = document.getElementById('orderHistorySyncWarningsWrap');
    const warningsContainer = document.getElementById('orderHistorySyncWarnings');
    const cookieSelect = document.getElementById('orderHistorySyncCookieId');
    const startDateInput = document.getElementById('orderHistorySyncStartDate');
    const endDateInput = document.getElementById('orderHistorySyncEndDate');
    const maxOrdersInput = document.getElementById('orderHistorySyncMaxOrders');
    const fetchDetailsInput = document.getElementById('orderHistorySyncFetchDetails');

    if (cookieSelect && Object.prototype.hasOwnProperty.call(request, 'cookie_id')) {
        cookieSelect.value = request.cookie_id || '';
    }
    if (startDateInput && request.start_date) {
        startDateInput.value = request.start_date;
    }
    if (endDateInput && request.end_date) {
        endDateInput.value = request.end_date;
    }
    if (maxOrdersInput && request.max_orders) {
        maxOrdersInput.value = String(request.max_orders);
    }
    if (fetchDetailsInput && Object.prototype.hasOwnProperty.call(request, 'fetch_details')) {
        fetchDetailsInput.checked = Boolean(request.fetch_details);
    }

    if (statusText) {
        statusText.textContent = statusMeta.title;
    }
    if (messageText) {
        messageText.textContent = job?.message || '选择账号和日期范围后即可开始同步。';
    }
    if (statusBadge) {
        statusBadge.className = `badge ${statusMeta.badgeClass}`;
        statusBadge.textContent = statusMeta.label;
    }

    let progressPercent = 0;
    const status = String(job?.status || '').toLowerCase();
    if (status === 'completed' || status === 'failed' || status === 'cancelled') {
        progressPercent = 100;
    } else if (accountsTotal > 0) {
        const accountProgress = accountsCompleted / accountsTotal;
        const orderProgress = matchedOrders > 0 ? (ordersProcessed / matchedOrders) : 0;
        progressPercent = Math.max(accountProgress, orderProgress) * 100;
    } else if (status === 'pending') {
        progressPercent = 8;
    }

    if (progressBar) {
        progressBar.className = `progress-bar ${statusMeta.progressClass}`;
        progressBar.style.width = `${Math.max(0, Math.min(100, progressPercent))}%`;
    }

    if (accountsStat) {
        accountsStat.textContent = `${accountsCompleted} / ${accountsTotal}`;
    }
    if (discoveredStat) {
        discoveredStat.textContent = String(ordersDiscovered);
    }
    if (matchedStat) {
        matchedStat.textContent = String(matchedOrders);
    }
    if (savedStat) {
        savedStat.textContent = `${ordersSaved} / ${ordersFailed}`;
    }

    const requestParts = [
        request.cookie_id ? `账号 ${request.cookie_id}` : '全部账号',
        request.max_orders ? `最多同步 ${request.max_orders} 单` : '',
        request.fetch_details === false ? '仅基础信息' : '含订单详情',
        request.start_date && request.end_date ? `时间范围 ${request.start_date} 至 ${request.end_date}` : '',
    ].filter(Boolean);
    const metaParts = [
        requestParts.join(' · '),
        job?.started_at ? `开始于 ${job.started_at}` : '',
        job?.finished_at ? `结束于 ${job.finished_at}` : '',
    ].filter(Boolean);
    if (metaText) {
        metaText.textContent = metaParts.join(' · ') || '尚未开始任务';
    }

    const currentParts = [];
    if (job?.current_account) {
        currentParts.push(`当前账号: ${job.current_account}`);
    }
    if (job?.current_order_id) {
        currentParts.push(`当前订单: ${job.current_order_id}`);
    }
    if (ordersProcessed > 0 || ordersSkipped > 0) {
        currentParts.push(`已处理 ${ordersProcessed} 单，跳过 ${ordersSkipped} 单`);
    }
    if (currentText) {
        if (matchedOrders > 0 && ordersProcessed > 0) {
            currentParts.unshift(`范围内进度: ${ordersProcessed} / ${matchedOrders}`);
        }
        currentText.textContent = currentParts.join(' · ');
    }

    if (warningsWrap && warningsContainer) {
        if (warnings.length > 0) {
            warningsWrap.style.display = '';
            warningsContainer.innerHTML = warnings.map(message => `
                <div class="border rounded-3 bg-white px-3 py-2 text-muted small">
                    ${escapeHtml(message)}
                </div>
            `).join('');
        } else {
            warningsWrap.style.display = 'none';
            warningsContainer.innerHTML = '';
        }
    }

    setOrderHistorySyncFormDisabled(status === 'pending' || status === 'running');
}

async function openOrderHistorySyncModal() {
    try {
        const modalElement = document.getElementById('orderHistorySyncModal');
        if (!modalElement) return;

        orderHistorySyncModalInstance = bootstrap.Modal.getOrCreateInstance(modalElement);

        const accounts = await fetchOrderSyncAccounts(true);
        const select = document.getElementById('orderHistorySyncCookieId');
        renderOrderAccountOptions(select, accounts, { includeAllOption: true });

        const pageFilterValue = document.getElementById('orderCookieFilter')?.value || '';
        const startDateInput = document.getElementById('orderHistorySyncStartDate');
        const endDateInput = document.getElementById('orderHistorySyncEndDate');
        const maxOrdersInput = document.getElementById('orderHistorySyncMaxOrders');
        const fetchDetailsInput = document.getElementById('orderHistorySyncFetchDetails');

        if (startDateInput && !startDateInput.value) {
            startDateInput.value = getRelativeBeijingDateInputValue(-30);
        }
        if (endDateInput && !endDateInput.value) {
            endDateInput.value = getRelativeBeijingDateInputValue(0);
        }
        if (maxOrdersInput && !maxOrdersInput.value) {
            maxOrdersInput.value = '120';
        }
        if (fetchDetailsInput && !activeOrderHistorySyncJobId) {
            fetchDetailsInput.checked = true;
        }

        if (select && !activeOrderHistorySyncJobId) {
            select.value = pageFilterValue || '';
        }

        if (activeOrderHistorySyncJobId) {
            try {
                await fetchOrderHistorySyncStatus(activeOrderHistorySyncJobId, { silentToast: true });
            } catch (error) {
                if (activeOrderHistorySyncJobId) {
                    throw error;
                }
            }
        }

        if (!activeOrderHistorySyncJobId) {
            resetOrderHistorySyncProgress();
        }

        orderHistorySyncModalInstance.show();
    } catch (error) {
        console.error('打开历史订单同步弹窗失败:', error);
        showToast('加载历史同步配置失败', 'danger');
    }
}

async function startOrderHistorySync() {
    try {
        const cookieId = document.getElementById('orderHistorySyncCookieId')?.value || '';
        const startDate = document.getElementById('orderHistorySyncStartDate')?.value || '';
        const endDate = document.getElementById('orderHistorySyncEndDate')?.value || '';
        const maxOrders = parseInt(document.getElementById('orderHistorySyncMaxOrders')?.value || '120', 10);
        const fetchDetails = Boolean(document.getElementById('orderHistorySyncFetchDetails')?.checked);

        if (!startDate || !endDate) {
            showToast('请选择开始日期和结束日期', 'warning');
            return;
        }
        if (startDate > endDate) {
            showToast('开始日期不能晚于结束日期', 'warning');
            return;
        }
        if (!Number.isFinite(maxOrders) || maxOrders < 1 || maxOrders > 500) {
            showToast('最多同步单数需在 1 到 500 之间', 'warning');
            return;
        }

        const startBtn = document.getElementById('orderHistorySyncStartBtn');
        if (startBtn) {
            startBtn.disabled = true;
            startBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span>创建任务中';
        }

        const response = await fetch(`${apiBase}/api/orders/history-sync`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                cookie_id: cookieId || null,
                start_date: startDate,
                end_date: endDate,
                max_orders: maxOrders,
                fetch_details: fetchDetails,
            })
        });

        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.success || !result.data) {
            throw new Error(result.detail || result.message || '创建历史订单同步任务失败');
        }

        activeOrderHistorySyncJobId = result.data.job_id;
        orderHistorySyncNotifiedJobId = '';
        renderOrderHistorySyncJob(result.data);
        scheduleOrderHistorySyncPolling(activeOrderHistorySyncJobId);
        showToast('历史订单同步已开始', 'success');
    } catch (error) {
        console.error('创建历史订单同步任务失败:', error);
        showToast(error.message || '创建历史订单同步任务失败', 'danger');
        setOrderHistorySyncFormDisabled(false);
    } finally {
        const startBtn = document.getElementById('orderHistorySyncStartBtn');
        if (startBtn && !startBtn.disabled) {
            startBtn.innerHTML = '<i class="bi bi-play-circle"></i> 开始同步';
        }
    }
}

async function fetchOrderHistorySyncStatus(jobId, options = {}) {
    if (!jobId) return null;

    const { silentToast = false } = options;
    const response = await fetch(`${apiBase}/api/orders/history-sync/${jobId}`, {
        headers: {
            'Authorization': `Bearer ${authToken}`
        }
    });

    const result = await response.json().catch(() => ({}));
    if (!response.ok || !result.success || !result.data) {
        if (response.status === 404) {
            activeOrderHistorySyncJobId = '';
            stopOrderHistorySyncPolling();
            resetOrderHistorySyncProgress();
        }
        throw new Error(result.detail || result.message || '获取历史订单同步状态失败');
    }

    const job = result.data;
    activeOrderHistorySyncJobId = job.job_id || activeOrderHistorySyncJobId;
    renderOrderHistorySyncJob(job);

    const status = String(job?.status || '').toLowerCase();
    if (status === 'pending' || status === 'running') {
        scheduleOrderHistorySyncPolling(job.job_id);
    } else {
        stopOrderHistorySyncPolling();

        const startBtn = document.getElementById('orderHistorySyncStartBtn');
        if (startBtn) {
            startBtn.innerHTML = '<i class="bi bi-play-circle"></i> 开始同步';
        }

        if (!silentToast && orderHistorySyncNotifiedJobId !== job.job_id) {
            orderHistorySyncNotifiedJobId = job.job_id;
            if (status === 'completed') {
                showToast(job.message || '历史订单同步完成', 'success');
            } else if (status === 'failed') {
                showToast(job.error || job.message || '历史订单同步失败', 'danger');
            } else if (status === 'cancelled') {
                showToast(job.message || '历史订单同步已取消', 'warning');
            }
            await refreshOrdersData();
        }
    }

    return job;
}

async function cancelOrderHistorySync() {
    if (!activeOrderHistorySyncJobId) {
        showToast('当前没有可取消的历史同步任务', 'warning');
        return;
    }

    try {
        const response = await fetch(`${apiBase}/api/orders/history-sync/${activeOrderHistorySyncJobId}/cancel`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        const result = await response.json().catch(() => ({}));
        if (!response.ok || !result.success || !result.data) {
            throw new Error(result.detail || result.message || '取消历史订单同步失败');
        }

        stopOrderHistorySyncPolling();
        renderOrderHistorySyncJob(result.data);
        orderHistorySyncNotifiedJobId = result.data.job_id || orderHistorySyncNotifiedJobId;
        const startBtn = document.getElementById('orderHistorySyncStartBtn');
        if (startBtn) {
            startBtn.innerHTML = '<i class="bi bi-play-circle"></i> 开始同步';
        }
        showToast(result.data.message || '历史订单同步已取消', 'warning');
        await refreshOrdersData();
    } catch (error) {
        console.error('取消历史订单同步失败:', error);
        showToast(error.message || '取消历史订单同步失败', 'danger');
    }
}

// 清空订单筛选条件
function clearOrderFilters() {
    const searchInput = document.getElementById('orderSearchInput');
    const statusFilter = document.getElementById('orderStatusFilter');
    const cookieFilter = document.getElementById('orderCookieFilter');

    if (searchInput) searchInput.value = '';
    if (statusFilter) statusFilter.value = '';
    if (cookieFilter) cookieFilter.value = '';

    filterOrders();
    showToast('筛选条件已清空', 'info');
}

// 显示订单详情
async function showOrderDetail(orderId) {
    try {
        const order = allOrdersData.find(o => o.order_id === orderId);
        if (!order) {
            showToast('订单不存在', 'warning');
            return;
        }

        // 创建模态框内容
        const safeOrderId = escapeHtml(order.order_id || '');
        const safeItemId = escapeHtml(order.item_id || '未知');
        const safeBuyerId = escapeHtml(order.buyer_id || '未知');
        const safeBuyerNick = escapeHtml(order.buyer_nick || '未知');
        const safeCookieId = escapeHtml(order.cookie_id || '未知');
        const safeSpecName = escapeHtml(order.spec_name || '无');
        const safeSpecValue = escapeHtml(order.spec_value || '无');
        const safeSpecName2 = escapeHtml(order.spec_name_2 || '无');
        const safeSpecValue2 = escapeHtml(order.spec_value_2 || '无');
        const safeQuantity = escapeHtml(order.quantity || '1');
        const safeAmount = escapeHtml(formatOrderAmountDisplay(order.amount));
        const safePlatformCreatedAt = escapeHtml(formatBeijingDateTimeWithSeconds(order.platform_created_at));
        const safePlatformPaidAt = escapeHtml(formatBeijingDateTimeWithSeconds(order.platform_paid_at));
        const safePlatformCompletedAt = escapeHtml(formatBeijingDateTimeWithSeconds(order.platform_completed_at));
        const safeCreatedAt = escapeHtml(formatBeijingDateTimeWithSeconds(order.created_at));
        const safeUpdatedAt = escapeHtml(formatBeijingDateTimeWithSeconds(order.updated_at));
        const safeStatusText = escapeHtml(getOrderStatusText(order.order_status));

        const modalContent = `
            <div class="modal fade" id="orderDetailModal" tabindex="-1">
                <div class="modal-dialog modal-lg">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title">
                                <i class="bi bi-receipt-cutoff me-2"></i>
                                订单详情
                            </h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                        </div>
                        <div class="modal-body">
                            <div class="row">
                                <div class="col-md-6">
                                    <h6>基本信息</h6>
                                    <table class="table table-sm">
                                        <tr><td>订单ID</td><td>${safeOrderId}</td></tr>
                                        <tr><td>商品ID</td><td>${safeItemId}</td></tr>
                                        <tr><td>买家ID</td><td>${safeBuyerId}</td></tr>
                                        <tr><td>买家昵称</td><td>${safeBuyerNick}</td></tr>
                                        <tr><td>Cookie账号</td><td>${safeCookieId}</td></tr>
                                        <tr><td>订单状态</td><td><span class="badge ${getOrderStatusClass(order.order_status)}">${safeStatusText}</span></td></tr>
                                    </table>
                                </div>
                                <div class="col-md-6">
                                    <h6>商品信息</h6>
                                    <table class="table table-sm">
                                        <tr><td>规格1名称</td><td>${safeSpecName}</td></tr>
                                        <tr><td>规格1值</td><td>${safeSpecValue}</td></tr>
                                        <tr><td>规格2名称</td><td>${safeSpecName2}</td></tr>
                                        <tr><td>规格2值</td><td>${safeSpecValue2}</td></tr>
                                        <tr><td>数量</td><td>${safeQuantity}</td></tr>
                                        <tr><td>金额</td><td>${safeAmount}</td></tr>
                                    </table>
                                </div>
                            </div>
                            <div class="row mt-3">
                                <div class="col-12">
                                    <h6>时间信息</h6>
                                    <table class="table table-sm">
                                        <tr><td>平台下单时间</td><td>${safePlatformCreatedAt}</td></tr>
                                        <tr><td>平台付款时间</td><td>${safePlatformPaidAt}</td></tr>
                                        <tr><td>平台完成时间</td><td>${safePlatformCompletedAt}</td></tr>
                                        <tr><td>入库时间</td><td>${safeCreatedAt}</td></tr>
                                        <tr><td>更新时间</td><td>${safeUpdatedAt}</td></tr>
                                    </table>
                                </div>
                            </div>
                            <div class="row mt-3">
                                <div class="col-12">
                                    <h6>商品详情</h6>
                                    <div id="itemDetailContent">
                                        <div class="text-center">
                                            <div class="spinner-border spinner-border-sm" role="status">
                                                <span class="visually-hidden">加载中...</span>
                                            </div>
                                            <span class="ms-2">正在加载商品详情...</span>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">关闭</button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        // 移除已存在的模态框
        const existingModal = document.getElementById('orderDetailModal');
        if (existingModal) {
            existingModal.remove();
        }

        // 添加新模态框到页面
        document.body.insertAdjacentHTML('beforeend', modalContent);

        // 显示模态框
        const modal = new bootstrap.Modal(document.getElementById('orderDetailModal'));
        modal.show();

        // 异步加载商品详情
        if (order.item_id) {
            loadItemDetailForOrder(order.item_id, order.cookie_id);
        }

    } catch (error) {
        console.error('显示订单详情失败:', error);
        showToast('显示订单详情失败', 'danger');
    }
}

// 为订单加载商品详情
async function loadItemDetailForOrder(itemId, cookieId) {
    try {
        const token = localStorage.getItem('auth_token');

        // 尝试从数据库获取商品信息
        let response = await fetch(`${apiBase}/items/${cookieId}/${itemId}`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const content = document.getElementById('itemDetailContent');
        if (!content) return;

        if (response.ok) {
            const data = await response.json();
            const item = data.item;
            const safeTitle = escapeHtml(item.item_title || '商品标题未知');
            const safeDescription = escapeHtml(item.item_description || '暂无描述');
            const safeCategory = escapeHtml(item.item_category || '未知');
            const safePrice = escapeHtml(item.item_price || '未知');
            const safeDetail = escapeHtml(item.item_detail || '');

            content.innerHTML = `
                <div class="card">
                    <div class="card-body">
                        <h6 class="card-title">${safeTitle}</h6>
                        <p class="card-text">${safeDescription}</p>
                        <div class="row">
                            <div class="col-md-6">
                                <small class="text-muted">分类：${safeCategory}</small>
                            </div>
                            <div class="col-md-6">
                                <small class="text-muted">价格：${safePrice}</small>
                            </div>
                        </div>
                        ${item.item_detail ? `
                            <div class="mt-2">
                                <small class="text-muted">详情：</small>
                                <div class="border p-2 mt-1" style="max-height: 200px; overflow-y: auto;">
                                    <small>${safeDetail}</small>
                                </div>
                            </div>
                        ` : ''}
                    </div>
                </div>
            `;
        } else {
            content.innerHTML = `
                <div class="alert alert-warning">
                    <i class="bi bi-exclamation-triangle me-2"></i>
                    无法获取商品详情信息
                </div>
            `;
        }
    } catch (error) {
        console.error('加载商品详情失败:', error);
        const content = document.getElementById('itemDetailContent');
        if (content) {
            content.innerHTML = `
                <div class="alert alert-danger">
                    <i class="bi bi-exclamation-triangle me-2"></i>
                    加载商品详情失败：${escapeHtml(error.message || '未知错误')}
                </div>
            `;
        }
    }
}

// 删除订单
async function deleteOrder(orderId) {
    try {
        const confirmed = confirm(`确定要删除订单吗？\n\n订单ID: ${orderId}\n\n此操作不可撤销！`);
        if (!confirmed) {
            return;
        }

        const response = await fetch(`${apiBase}/api/orders/${orderId}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            showToast('订单删除成功', 'success');
            // 刷新列表
            await refreshOrdersData();
        } else {
            const error = await response.text();
            showToast(`删除失败: ${error}`, 'danger');
        }
    } catch (error) {
        console.error('删除订单失败:', error);
        showToast('删除订单失败', 'danger');
    }
}

// 批量删除订单
async function batchDeleteOrders() {
    const checkboxes = document.querySelectorAll('.order-checkbox:checked');
    if (checkboxes.length === 0) {
        showToast('请先选择要删除的订单', 'warning');
        return;
    }

    const orderIds = Array.from(checkboxes).map(cb => cb.value);
    const confirmed = confirm(`确定要删除选中的 ${orderIds.length} 个订单吗？\n\n此操作不可撤销！`);

    if (!confirmed) return;

    try {
        let successCount = 0;
        let failCount = 0;

        for (const orderId of orderIds) {
            try {
                const response = await fetch(`${apiBase}/api/orders/${orderId}`, {
                    method: 'DELETE',
                    headers: {
                        'Authorization': `Bearer ${authToken}`
                    }
                });

                if (response.ok) {
                    successCount++;
                } else {
                    failCount++;
                }
            } catch (error) {
                failCount++;
            }
        }

        if (successCount > 0) {
            showToast(`成功删除 ${successCount} 个订单${failCount > 0 ? `，${failCount} 个失败` : ''}`,
                     failCount > 0 ? 'warning' : 'success');
            await refreshOrdersData();
        } else {
            showToast('批量删除失败', 'danger');
        }

    } catch (error) {
        console.error('批量删除订单失败:', error);
        showToast('批量删除订单失败', 'danger');
    }
}

// 手动发货订单
async function manualDeliverOrder(orderId) {
    try {
        const confirmed = confirm(`确定要手动发货此订单吗？\n\n订单ID: ${orderId}\n\n系统将根据发货规则自动匹配发货内容并发送给买家。`);
        if (!confirmed) {
            return;
        }

        showToast('正在执行发货...', 'info');

        const response = await fetch(`${apiBase}/api/orders/${orderId}/deliver`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            }
        });

        const result = await response.json();

        if (response.ok) {
            if (result.delivered) {
                showToast(`发货成功！\n${result.message}`, 'success');
                // 刷新今日发货统计
                refreshTodayDeliveryCount();
            } else {
                showToast(`发货失败: ${result.message}`, 'warning');
            }
            // 刷新订单列表
            await refreshOrdersData();
        } else {
            showToast(`发货失败: ${result.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('手动发货失败:', error);
        showToast('手动发货失败: ' + error.message, 'danger');
    }
}

// 刷新订单状态
async function refreshOrderStatus(orderId) {
    try {
        showToast('正在刷新订单状态...', 'info');

        const response = await fetch(`${apiBase}/api/orders/${orderId}/refresh`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            }
        });

        const result = await response.json();

        if (response.ok) {
            if (result.updated) {
                showToast(`订单状态已更新: ${getOrderStatusText(result.new_status)}`, 'success');
            } else {
                showToast(result.message || '订单状态无变化', 'info');
            }
            // 刷新订单列表
            await refreshOrdersData();
        } else {
            showToast(`刷新失败: ${result.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('刷新订单状态失败:', error);
        showToast('刷新订单状态失败: ' + error.message, 'danger');
    }
}

// 切换全选订单
function toggleSelectAllOrders(checkbox) {
    const orderCheckboxes = document.querySelectorAll('.order-checkbox');
    orderCheckboxes.forEach(cb => {
        cb.checked = checkbox.checked;
    });

    updateOrderBatchButtons();
}

// 更新批量操作按钮状态
function updateOrderBatchButtons() {
    const checkboxes = document.querySelectorAll('.order-checkbox:checked');
    const batchDeleteBtn = document.getElementById('batchDeleteOrdersBtn');
    const batchRefreshBtn = document.getElementById('batchRefreshOrdersBtn');

    const hasSelection = checkboxes.length > 0;

    if (batchDeleteBtn) {
        batchDeleteBtn.disabled = !hasSelection;
    }
    if (batchRefreshBtn) {
        batchRefreshBtn.disabled = !hasSelection;
    }
}

// 批量刷新订单状态
async function batchRefreshOrders() {
    const checkboxes = document.querySelectorAll('.order-checkbox:checked');
    if (checkboxes.length === 0) {
        showToast('请先选择要刷新的订单', 'warning');
        return;
    }

    const orderIds = Array.from(checkboxes).map(cb => cb.value);
    const confirmed = confirm(`确定要刷新选中的 ${orderIds.length} 个订单状态吗？\n\n这可能需要一些时间...`);

    if (!confirmed) return;

    showToast(`正在刷新 ${orderIds.length} 个订单状态...`, 'info');

    let successCount = 0;
    let failCount = 0;

    for (const orderId of orderIds) {
        try {
            const response = await fetch(`${apiBase}/api/orders/${orderId}/refresh`, {
                method: 'POST',
                headers: {
                    'Authorization': `Bearer ${authToken}`,
                    'Content-Type': 'application/json'
                }
            });

            if (response.ok) {
                successCount++;
            } else {
                failCount++;
            }
        } catch (error) {
            console.error(`刷新订单 ${orderId} 失败:`, error);
            failCount++;
        }
    }

    // 刷新订单列表
    await refreshOrdersData();

    if (failCount === 0) {
        showToast(`成功刷新 ${successCount} 个订单状态`, 'success');
    } else {
        showToast(`刷新完成: ${successCount} 成功, ${failCount} 失败`, 'warning');
    }
}


// 页面加载完成后初始化订单搜索功能
document.addEventListener('DOMContentLoaded', function() {
    // 延迟初始化，确保DOM完全加载
    setTimeout(() => {
        initOrdersSearch();

        const orderHistorySyncModal = document.getElementById('orderHistorySyncModal');
        if (orderHistorySyncModal) {
            orderHistorySyncModal.addEventListener('hidden.bs.modal', () => {
                stopOrderHistorySyncPolling();
            });
        }

        // 绑定复选框变化事件
        document.addEventListener('change', function(e) {
            if (e.target.classList.contains('order-checkbox')) {
                updateOrderBatchButtons();
            }
        });

        document.addEventListener('click', function(e) {
            const actionButton = e.target.closest('.order-action-btn');
            if (!actionButton) return;

            const orderId = actionButton.dataset.orderId;
            const action = actionButton.dataset.orderAction;
            if (!orderId || !action) return;

            if (action === 'deliver') {
                manualDeliverOrder(orderId);
            } else if (action === 'refresh') {
                refreshOrderStatus(orderId);
            } else if (action === 'detail') {
                showOrderDetail(orderId);
            } else if (action === 'delete') {
                deleteOrder(orderId);
            }
        });
    }, 100);
});

