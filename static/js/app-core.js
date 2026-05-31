
// ================================
// 全局变量和配置
// ================================
const apiBase = location.origin;
let keywordsData = {};
let currentCookieId = '';
let editCookieId = '';
let authToken = localStorage.getItem('auth_token');
let dashboardData = {
    accounts: [],
    totalKeywords: 0,
    totalItems: 0
};
let pendingAccountManagementFocusId = '';
let aboutDiagnosticsAccounts = [];
let aboutDiagnosticsInitialized = false;
let dashboardRuntimeRetryTimer = null;
let aboutRuntimeRetryTimer = null;
let lastDashboardRuntimeRetryAt = 0;
let lastAboutRuntimeRetryAt = 0;
const DASHBOARD_ANNOUNCEMENT_DISMISS_PREFIX = 'dashboard_announcement_dismissed_';
let dashboardAnnouncementState = {
    current: null,
    history: []
};

// 账号关键词缓存
let accountKeywordCache = {};
let cacheTimestamp = 0;
const CACHE_DURATION = 30000; // 30秒缓存

// 商品列表搜索和分页相关变量
let allItemsData = []; // 存储所有商品数据
let filteredItemsData = []; // 存储过滤后的商品数据
let currentItemsPage = 1; // 当前页码
let itemsPerPage = 20; // 每页显示数量
let totalItemsPages = 0; // 总页数
let currentSearchKeyword = ''; // 当前搜索关键词
let itemPublishPreviewUrls = [];
let itemPublishInitialized = false;
let itemPublishSubmitting = false;

// 订单列表搜索和分页相关变量
let allOrdersData = []; // 存储所有订单数据
let filteredOrdersData = []; // 存储过滤后的订单数据
let currentOrdersPage = 1; // 当前页码
let ordersPerPage = 20; // 每页显示数量
let totalOrdersPages = 0; // 总页数
let currentOrderSearchKeyword = ''; // 当前搜索关键词
let ordersStreamAbortController = null;
let ordersStreamReconnectTimer = null;
let ordersStreamRetryCount = 0;
let ordersStreamShouldRun = false;
let orderHistorySyncModalInstance = null;
let orderHistorySyncPollingTimer = null;
let activeOrderHistorySyncJobId = '';
let orderHistorySyncNotifiedJobId = '';
let orderHistorySyncAccounts = [];
let loadingRequestCount = 0;
let loadingShowTimer = null;
const LOADING_SHOW_DELAY = 120;

// ================================
// 通用功能 - 菜单切换和导航
// ================================
function showSection(sectionName) {
    console.log('切换到页面:', sectionName); // 调试信息

    // 获取并校验目标内容区域
    const targetSection = document.getElementById(sectionName + '-section');
    if (!targetSection) {
        console.error('找不到页面元素:', sectionName + '-section'); // 调试信息
        return;
    }

    // 如果已经是当前页面，避免重复切换导致闪烁
    if (targetSection.classList.contains('active')) {
        return;
    }

    // 仅切换当前激活页面和目标页面，避免“先全关再全开”造成白闪
    const currentActiveSection = document.querySelector('.content-section.active');
    if (currentActiveSection) {
        currentActiveSection.classList.remove('active');
    }

    targetSection.classList.add('active');
    console.log('页面已激活:', sectionName + '-section'); // 调试信息

    // 仅处理侧边栏菜单 active，避免影响内容区域 tab 的 .nav-link
    document.querySelectorAll('#sidebar .sidebar-nav .nav-link').forEach(link => {
        link.classList.remove('active');
    });

    const activeMenuLink = document.querySelector(`#sidebar .nav-item[data-menu-id="${sectionName}"] .nav-link`);
    if (activeMenuLink) {
        activeMenuLink.classList.add('active');
    }

    // 根据不同section加载对应数据
    switch(sectionName) {
    case 'dashboard':        // 【仪表盘菜单】
        loadDashboard();
        break;
    case 'accounts':         // 【账号管理菜单】
        loadCookies();
        break;
    case 'item-publish':    // 【商品发布菜单】
        loadItemPublish();
        break;
    case 'items':           // 【商品管理菜单】
        loadItems();
        initItemsSearch(); // 确保搜索功能已初始化
        break;
    case 'items-reply':           // 【商品回复管理菜单】
        loadItemsReplay();
        break;
    case 'orders':          // 【订单管理菜单】
        loadOrders();
        break;
    case 'auto-reply':      // 【自动回复菜单】
        refreshAccountList();
        break;
    case 'cards':           // 【卡券管理菜单】
        loadCards();
        break;
    case 'auto-delivery':   // 【自动发货菜单】
        loadDeliveryRules();
        break;
    case 'notification-channels':  // 【通知渠道菜单】
        loadNotificationChannels();
        break;
    case 'message-notifications':  // 【消息通知菜单】
        loadMessageNotifications();
        loadNotificationTemplates();
        break;
    case 'system-settings':    // 【系统设置菜单】
        loadSystemSettings();
        initMenuManagement();
        break;
    case 'logs':            // 【日志管理菜单】
        // 自动加载系统日志
        setTimeout(() => {
            // 检查是否在正确的页面并且元素存在
            const systemLogContainer = document.getElementById('systemLogContainer');
            if (systemLogContainer) {
                console.log('首次进入日志页面，自动加载日志...');
                loadSystemLogs();
            }
        }, 100);
        break;
    case 'risk-control-logs': // 【风控日志菜单】
        // 自动加载风控日志
        setTimeout(() => {
            const riskLogContainer = document.getElementById('riskLogContainer');
            if (riskLogContainer) {
                console.log('首次进入风控日志页面，自动加载日志...');
                loadRiskControlLogs();
                loadCookieFilterOptions();
            }
        }, 100);
        break;
    case 'user-management':  // 【用户管理菜单】
        loadUserManagement();
        break;
    case 'online-im':        // 【在线客服菜单】
        loadOnlineIm();
        break;
    case 'data-management':  // 【数据管理菜单】
        loadDataManagement();
        break;
    }

    if (sectionName !== 'orders') {
        stopOrdersStream();
    }

    if (sectionName !== 'online-im') {
        stopChatStream();
    }

    // 如果切换到非日志页面，停止自动刷新
    if (sectionName !== 'logs' && window.autoRefreshInterval) {
    clearInterval(window.autoRefreshInterval);
    window.autoRefreshInterval = null;
    const button = document.querySelector('#autoRefreshText');
    const icon = button?.previousElementSibling;
    if (button) {
        button.textContent = '开启自动刷新';
        if (icon) icon.className = 'bi bi-play-circle me-1';
    }
    }

    if (sectionName !== 'dashboard' && dashboardRuntimeRetryTimer) {
        clearTimeout(dashboardRuntimeRetryTimer);
        dashboardRuntimeRetryTimer = null;
    }

    if (sectionName !== 'accounts' && aboutRuntimeRetryTimer) {
        clearTimeout(aboutRuntimeRetryTimer);
        aboutRuntimeRetryTimer = null;
    }
}

function getAuthToken() {
    authToken = localStorage.getItem('auth_token');
    return authToken || '';
}

// 移动端侧边栏切换
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('show');
}

// 侧边栏折叠切换
function toggleSidebarCollapse() {
    const sidebar = document.getElementById('sidebar');
    const body = document.body;
    sidebar.classList.toggle('collapsed');
    body.classList.toggle('sidebar-collapsed');
    // 保存状态到 localStorage
    localStorage.setItem('sidebarCollapsed', sidebar.classList.contains('collapsed'));
}

// 初始化侧边栏折叠状态
function initSidebarCollapse() {
    const isCollapsed = localStorage.getItem('sidebarCollapsed') === 'true';
    if (isCollapsed) {
        const sidebar = document.getElementById('sidebar');
        const body = document.body;
        if (sidebar) {
            sidebar.classList.add('collapsed');
            body.classList.add('sidebar-collapsed');
        }
    }
}

// ================================
// 暗色模式功能
// ================================

// 检测系统是否为暗色模式
function isSystemDarkMode() {
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
}

// 更新主题图标
function updateDarkModeIcon(mode) {
    const icon = document.getElementById('darkModeIcon');
    if (!icon) return;

    // 清除所有可能的图标类
    icon.classList.remove('bi-moon-fill', 'bi-sun-fill', 'bi-circle-half');

    if (mode === 'auto') {
        icon.classList.add('bi-circle-half');
    } else if (mode === 'dark') {
        icon.classList.add('bi-sun-fill');
    } else {
        icon.classList.add('bi-moon-fill');
    }
}

// 应用主题
function applyDarkMode(mode) {
    const html = document.documentElement;
    let shouldBeDark = false;

    if (mode === 'auto') {
        shouldBeDark = isSystemDarkMode();
    } else if (mode === 'dark') {
        shouldBeDark = true;
    }

    if (shouldBeDark) {
        html.setAttribute('data-theme', 'dark');
    } else {
        html.removeAttribute('data-theme');
    }

    updateDarkModeIcon(mode);
}

// 切换暗色模式（三态切换：light → dark → auto）
function toggleDarkMode() {
    const currentMode = localStorage.getItem('darkMode') || 'light';
    let nextMode;

    if (currentMode === 'light') {
        nextMode = 'dark';
    } else if (currentMode === 'dark') {
        nextMode = 'auto';
    } else {
        nextMode = 'light';
    }

    localStorage.setItem('darkMode', nextMode);
    applyDarkMode(nextMode);

    // 显示提示
    const modeNames = {
        'light': '浅色模式',
        'dark': '深色模式',
        'auto': '跟随系统'
    };
    showToast(`已切换至${modeNames[nextMode]}`, 'info');
}

// 初始化暗色模式
function initDarkMode() {
    const savedMode = localStorage.getItem('darkMode') || 'light';
    applyDarkMode(savedMode);

    // 监听系统主题变化
    if (window.matchMedia) {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
            const currentMode = localStorage.getItem('darkMode') || 'light';
            if (currentMode === 'auto') {
                applyDarkMode('auto');
            }
        });
    }
}

// ================================
// 【仪表盘菜单】相关功能
// ================================

async function fetchDashboardResource(path, fallbackValue) {
    try {
        const response = await fetch(`${apiBase}${path}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            return fallbackValue;
        }

        return await response.json();
    } catch (error) {
        console.error(`加载仪表盘资源失败: ${path}`, error);
        return fallbackValue;
    }
}

async function enrichDashboardAccounts(accounts) {
    const scheduledTaskData = await fetchDashboardResource('/scheduled-tasks', { success: false, tasks: [] });
    const scheduledTasks = scheduledTaskData && scheduledTaskData.success ? (scheduledTaskData.tasks || []) : [];

    return Promise.all(accounts.map(async (account) => {
        const [keywordsData, defaultReplyData, aiReplyData] = await Promise.all([
            fetchDashboardResource(`/keywords/${encodeURIComponent(account.id)}`, []),
            fetchDashboardResource(`/default-replies/${encodeURIComponent(account.id)}`, { enabled: false, reply_content: '' }),
            fetchDashboardResource(`/ai-reply-settings/${encodeURIComponent(account.id)}`, { ai_enabled: false, model_name: 'qwen-plus' })
        ]);

        return {
            ...account,
            keywords: Array.isArray(keywordsData) ? keywordsData : [],
            keywordCount: Array.isArray(keywordsData) ? keywordsData.length : 0,
            defaultReply: defaultReplyData || { enabled: false, reply_content: '' },
            aiReply: aiReplyData || { ai_enabled: false, model_name: 'qwen-plus' },
            polishSchedule: getPolishScheduledTask(scheduledTasks, account.id)
        };
    }));
}

// ================================
// 通用工具函数
// ================================

// 显示提示消息
function showToast(message, type = 'success') {
    // 将 'error' 类型映射为 'danger'，因为 Bootstrap 使用 'danger' 作为错误类型
    if (type === 'error') {
        type = 'danger';
    }
    
    let toastContainer = document.querySelector('.toast-container');
    
    // 如果 toast 容器不存在，创建一个
    if (!toastContainer) {
        toastContainer = document.createElement('div');
        toastContainer.className = 'toast-container position-fixed top-0 end-0 p-3';
        toastContainer.style.zIndex = '9999';
        document.body.appendChild(toastContainer);
    }
    
    const toast = document.createElement('div');
    toast.className = `toast align-items-center text-white bg-${type} border-0`;
    toast.setAttribute('role', 'alert');
    toast.setAttribute('aria-live', 'assertive');
    toast.setAttribute('aria-atomic', 'true');

    const toastRow = document.createElement('div');
    toastRow.className = 'd-flex';

    const toastBody = document.createElement('div');
    toastBody.className = 'toast-body';
    toastBody.style.whiteSpace = 'pre-line';
    toastBody.textContent = String(message ?? '');

    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'btn-close btn-close-white me-2 m-auto';
    closeButton.setAttribute('data-bs-dismiss', 'toast');
    closeButton.setAttribute('aria-label', 'Close');

    toastRow.appendChild(toastBody);
    toastRow.appendChild(closeButton);
    toast.appendChild(toastRow);

    toastContainer.appendChild(toast);
    const bsToast = new bootstrap.Toast(toast, { delay: 5000 });  // 增加显示时间到5秒
    bsToast.show();

    // 自动移除
    toast.addEventListener('hidden.bs.toast', () => {
        toast.remove();
    });
}

// 错误处理
async function handleApiError(err) {
    console.error(err);
    showToast(err.message || '操作失败', 'danger');
    toggleLoading(false);
}

// API请求包装
async function fetchJSON(url, opts = {}) {
    toggleLoading(true);
    try {
    // 添加认证头
    const token = getAuthToken();
    if (token) {
        opts.headers = opts.headers || {};
        opts.headers['Authorization'] = `Bearer ${token}`;
    }

    const res = await fetch(url, opts);
    if (res.status === 401) {
        // 未授权，跳转到登录页面
        localStorage.removeItem('auth_token');
        window.location.href = '/';
        return;
    }
    if (!res.ok) {
        let errorMessage = `HTTP ${res.status}`;
        try {
        const errorText = await res.text();
        if (errorText) {
            // 尝试解析JSON错误信息
            try {
            const errorJson = JSON.parse(errorText);
            errorMessage = errorJson.detail || errorJson.message || errorText;
            } catch {
            errorMessage = errorText;
            }
        }
        } catch {
        errorMessage = `HTTP ${res.status} ${res.statusText}`;
        }
        throw new Error(errorMessage);
    }
    const data = await res.json();
    toggleLoading(false);
    return data;
    } catch (err) {
    handleApiError(err);
    throw err;
    }
}

