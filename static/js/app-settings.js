// ==================== 系统设置功能 ====================

// 加载用户设置
async function loadUserSettings() {
    const token = getAuthToken();
    if (!token) return;
    try {
        const response = await fetch(`${apiBase}/user-settings`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const settings = await response.json();

            // 设置主题颜色
            if (settings.theme_color && settings.theme_color.value) {
                const color = settings.theme_color.value;
                const picker = document.getElementById('themeColorPicker');
                const hex = document.getElementById('themeColorHex');
                if (picker) picker.value = color;
                if (hex) hex.value = color;
                applyThemeColor(color);
                updatePresetSelection(color);
            } else {
                localStorage.removeItem('themeColor');
            }
        }
    } catch (error) {
        console.error('加载用户设置失败:', error);
    }
}

// 应用主题颜色（支持任意十六进制颜色）
function applyThemeColor(color) {
    if (!color || !color.startsWith('#')) return;

    document.documentElement.style.setProperty('--primary-color', color);

    // 计算hover颜色（稍微深一点）
    const hoverColor = adjustBrightness(color, -20);
    document.documentElement.style.setProperty('--primary-hover', hoverColor);

    // 计算浅色版本（用于某些UI元素）
    const lightColor = adjustBrightness(color, 40);
    document.documentElement.style.setProperty('--primary-light', lightColor);

    // 缓存主题色，供页面首次渲染前预应用，避免刷新闪回默认蓝色
    localStorage.setItem('themeColor', color);
}

// 调整颜色亮度
function adjustBrightness(hex, percent) {
    const num = parseInt(hex.replace("#", ""), 16);
    const amt = Math.round(2.55 * percent);
    const R = (num >> 16) + amt;
    const G = (num >> 8 & 0x00FF) + amt;
    const B = (num & 0x0000FF) + amt;
    return "#" + (0x1000000 + (R < 255 ? R < 1 ? 0 : R : 255) * 0x10000 +
        (G < 255 ? G < 1 ? 0 : G : 255) * 0x100 +
        (B < 255 ? B < 1 ? 0 : B : 255)).toString(16).slice(1);
}

// 更新预设颜色按钮选中状态
function updatePresetSelection(selectedColor) {
    document.querySelectorAll('.color-preset').forEach(btn => {
        if (btn.dataset.color === selectedColor) {
            btn.style.border = '2px solid #333';
            btn.style.boxShadow = '0 0 0 2px #fff, 0 0 0 4px #333';
        } else {
            btn.style.border = '2px solid transparent';
            btn.style.boxShadow = 'none';
        }
    });
}

// ==================== 菜单管理功能 ====================

// 菜单项配置（默认顺序）
const DEFAULT_MENU_ITEMS = [
    { id: 'dashboard', name: '仪表盘', icon: 'bi-speedometer2', required: true },
    { id: 'accounts', name: '账号管理', icon: 'bi-person-circle', required: false },
    { id: 'item-publish', name: '商品发布', icon: 'bi-bag-plus', required: false },
    { id: 'items', name: '商品管理', icon: 'bi-box-seam', required: false },
    { id: 'orders', name: '订单管理', icon: 'bi-receipt-cutoff', required: false },
    { id: 'auto-reply', name: '自动回复', icon: 'bi-chat-left-text', required: false },
    { id: 'items-reply', name: '指定商品回复', icon: 'bi-chat-left-text', required: false },
    { id: 'cards', name: '卡券管理', icon: 'bi-credit-card', required: false },
    { id: 'auto-delivery', name: '自动发货', icon: 'bi-truck', required: false },
    { id: 'notification-channels', name: '通知渠道', icon: 'bi-bell', required: false },
    { id: 'message-notifications', name: '消息通知', icon: 'bi-chat-dots', required: false },
    { id: 'online-im', name: '在线客服', icon: 'bi-headset', required: false },
    { id: 'system-settings', name: '系统设置', icon: 'bi-gear', required: true },
    { id: 'about', name: '关于', icon: 'bi-info-circle', required: true }
];

// 当前菜单设置
let menuSettings = {};  // 显示/隐藏设置
let menuOrder = [];     // 菜单顺序
let draggedItem = null; // 当前拖拽的元素

// 获取排序后的菜单项
function getSortedMenuItems() {
    if (menuOrder.length === 0) {
        return [...DEFAULT_MENU_ITEMS];
    }

    // 按保存的顺序排列
    const sorted = [];
    menuOrder.forEach(id => {
        const item = DEFAULT_MENU_ITEMS.find(m => m.id === id);
        if (item) sorted.push(item);
    });

    // 添加可能遗漏的新菜单项
    DEFAULT_MENU_ITEMS.forEach(item => {
        if (!sorted.find(m => m.id === item.id)) {
            sorted.push(item);
        }
    });

    return sorted;
}

// 初始化菜单管理UI
function initMenuManagement() {
    const container = document.getElementById('menuManagementList');
    if (!container) return;

    const sortedItems = getSortedMenuItems();

    container.innerHTML = sortedItems.map(item => `
        <div class="menu-sort-item" draggable="true" data-menu-id="${item.id}">
            <span class="drag-handle">
                <i class="bi bi-grip-vertical"></i>
            </span>
            <span class="menu-icon">
                <i class="bi ${item.icon}"></i>
            </span>
            <span class="menu-name">${item.name}</span>
            ${item.required ? '<span class="badge bg-secondary">必选</span>' : ''}
            <div class="menu-checkbox">
                <div class="form-check form-switch mb-0">
                    <input class="form-check-input" type="checkbox" id="menu-${item.id}"
                        ${item.required ? 'checked disabled' : (menuSettings[item.id] !== false ? 'checked' : '')}
                        data-menu-id="${item.id}">
                </div>
            </div>
        </div>
    `).join('');

    // 绑定拖拽事件
    initDragAndDrop();
}

// 初始化拖拽功能
function initDragAndDrop() {
    const container = document.getElementById('menuManagementList');
    if (!container) return;

    const items = container.querySelectorAll('.menu-sort-item');

    items.forEach(item => {
        item.addEventListener('dragstart', handleDragStart);
        item.addEventListener('dragend', handleDragEnd);
        item.addEventListener('dragover', handleDragOver);
        item.addEventListener('dragenter', handleDragEnter);
        item.addEventListener('dragleave', handleDragLeave);
        item.addEventListener('drop', handleDrop);
    });
}

function handleDragStart(e) {
    draggedItem = this;
    this.classList.add('dragging');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/html', this.innerHTML);
}

function handleDragEnd(e) {
    this.classList.remove('dragging');
    document.querySelectorAll('.menu-sort-item').forEach(item => {
        item.classList.remove('drag-over');
    });
    draggedItem = null;
}

function handleDragOver(e) {
    e.preventDefault();
    e.dataTransfer.dropEffect = 'move';
    return false;
}

function handleDragEnter(e) {
    if (this !== draggedItem) {
        this.classList.add('drag-over');
    }
}

function handleDragLeave(e) {
    this.classList.remove('drag-over');
}

function handleDrop(e) {
    e.stopPropagation();
    e.preventDefault();

    if (draggedItem !== this) {
        const container = document.getElementById('menuManagementList');
        const items = Array.from(container.querySelectorAll('.menu-sort-item'));
        const draggedIndex = items.indexOf(draggedItem);
        const targetIndex = items.indexOf(this);

        if (draggedIndex < targetIndex) {
            this.parentNode.insertBefore(draggedItem, this.nextSibling);
        } else {
            this.parentNode.insertBefore(draggedItem, this);
        }
    }

    this.classList.remove('drag-over');
    return false;
}

// 获取当前菜单顺序
function getCurrentMenuOrder() {
    const container = document.getElementById('menuManagementList');
    if (!container) return [];

    const items = container.querySelectorAll('.menu-sort-item');
    return Array.from(items).map(item => item.dataset.menuId);
}

// 保存菜单设置（包括顺序和显示/隐藏）
async function saveMenuSettings() {
    // 获取显示/隐藏设置
    const visibility = {};
    DEFAULT_MENU_ITEMS.forEach(item => {
        if (!item.required) {
            const checkbox = document.getElementById(`menu-${item.id}`);
            if (checkbox) {
                visibility[item.id] = checkbox.checked;
            }
        }
    });

    // 获取顺序
    const order = getCurrentMenuOrder();

    try {
        // 保存显示设置
        await fetch(`${apiBase}/user-settings/menu_visibility`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                value: JSON.stringify(visibility),
                description: '菜单显示设置'
            })
        });

        // 保存顺序设置
        await fetch(`${apiBase}/user-settings/menu_order`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                value: JSON.stringify(order),
                description: '菜单顺序设置'
            })
        });

        menuSettings = visibility;
        menuOrder = order;
        applyMenuSettings();
        showToast('菜单设置保存成功', 'success');
    } catch (error) {
        console.error('保存菜单设置失败:', error);
        showToast('保存菜单设置失败', 'danger');
    }
}

// 重置菜单设置
async function resetMenuSettings() {
    try {
        // 重置显示设置
        await fetch(`${apiBase}/user-settings/menu_visibility`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                value: JSON.stringify({}),
                description: '菜单显示设置'
            })
        });

        // 重置顺序设置
        await fetch(`${apiBase}/user-settings/menu_order`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                value: JSON.stringify([]),
                description: '菜单顺序设置'
            })
        });

        menuSettings = {};
        menuOrder = [];

        // 重新初始化UI
        initMenuManagement();
        applyMenuSettings();
        showToast('菜单设置已恢复默认', 'success');
    } catch (error) {
        console.error('重置菜单设置失败:', error);
        showToast('重置菜单设置失败', 'danger');
    }
}

// 应用菜单设置（顺序和显示/隐藏）
function applyMenuSettings() {
    const sidebar = document.querySelector('.sidebar-nav');
    if (!sidebar) return;

    const sortedItems = getSortedMenuItems();

    // 按顺序重新排列侧边栏菜单（普通菜单项使用 0-99）
    sortedItems.forEach((item, index) => {
        const menuItem = sidebar.querySelector(`.nav-item[data-menu-id="${item.id}"]`);
        if (menuItem) {
            // 设置显示/隐藏
            if (!item.required) {
                const isVisible = menuSettings[item.id] !== false;
                menuItem.style.display = isVisible ? '' : 'none';
            }

            // 设置顺序（通过CSS order属性）
            menuItem.style.order = index;
        }
    });

    // 确保管理员菜单区块在普通菜单之后（order: 100）
    const adminSection = document.getElementById('adminMenuSection');
    if (adminSection) {
        adminSection.style.order = 100;
    }

    // 底部分隔符和登出按钮在最后（order: 200+）
    const dividers = sidebar.querySelectorAll('.nav-divider');
    dividers.forEach((divider, idx) => {
        // 跳过管理员区块内的分隔符
        if (!divider.closest('#adminMenuSection')) {
            divider.style.order = 200 + idx;
        }
    });

    // 登出按钮（没有data-menu-id的nav-item）在最后
    const logoutItem = sidebar.querySelector('.nav-item:not([data-menu-id])');
    if (logoutItem) {
        logoutItem.style.order = 999;
    }
}

// 兼容旧函数名
function applyMenuVisibility() {
    applyMenuSettings();
}

// 加载菜单设置
async function loadMenuSettings() {
    const token = getAuthToken();
    if (!token) return;
    try {
        const response = await fetch(`${apiBase}/user-settings`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const settings = await response.json();

            // 加载显示设置
            if (settings.menu_visibility && settings.menu_visibility.value) {
                try {
                    menuSettings = JSON.parse(settings.menu_visibility.value);
                } catch (e) {
                    menuSettings = {};
                }
            }

            // 加载顺序设置
            if (settings.menu_order && settings.menu_order.value) {
                try {
                    menuOrder = JSON.parse(settings.menu_order.value);
                } catch (e) {
                    menuOrder = [];
                }
            }

            applyMenuSettings();
        }
    } catch (error) {
        console.error('加载菜单设置失败:', error);
    }
}

// 主题表单提交处理
document.addEventListener('DOMContentLoaded', function() {
    // 颜色选择器同步
    const themeColorPicker = document.getElementById('themeColorPicker');
    const themeColorHex = document.getElementById('themeColorHex');

    if (themeColorPicker && themeColorHex) {
        themeColorPicker.addEventListener('input', function() {
            themeColorHex.value = this.value;
            applyThemeColor(this.value);
            updatePresetSelection(this.value);
        });

        themeColorHex.addEventListener('input', function() {
            if (/^#[0-9A-Fa-f]{6}$/.test(this.value)) {
                themeColorPicker.value = this.value;
                applyThemeColor(this.value);
                updatePresetSelection(this.value);
            }
        });
    }

    // 预设颜色按钮点击
    document.querySelectorAll('.color-preset').forEach(btn => {
        btn.addEventListener('click', function() {
            const color = this.dataset.color;
            if (themeColorPicker) themeColorPicker.value = color;
            if (themeColorHex) themeColorHex.value = color;
            applyThemeColor(color);
            updatePresetSelection(color);
        });
    });

    const themeForm = document.getElementById('themeForm');
    if (themeForm) {
        themeForm.addEventListener('submit', async function(e) {
            e.preventDefault();

            const themeColor = document.getElementById('themeColorHex')?.value || '#4f46e5';

            try {
                await fetch(`${apiBase}/user-settings/theme_color`, {
                    method: 'PUT',
                    headers: {
                        'Authorization': `Bearer ${authToken}`,
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        value: themeColor,
                        description: '主题颜色'
                    })
                });

                applyThemeColor(themeColor);
                showToast('主题设置保存成功', 'success');
            } catch (error) {
                console.error('主题设置失败:', error);
                showToast('主题设置失败', 'danger');
            }
        });
    }

    // 密码表单提交处理
    const passwordForm = document.getElementById('passwordForm');
    if (passwordForm) {
    passwordForm.addEventListener('submit', async function(e) {
        e.preventDefault();

        const currentPassword = document.getElementById('currentPassword').value;
        const newPassword = document.getElementById('newPassword').value;
        const confirmPassword = document.getElementById('confirmPassword').value;

        if (newPassword !== confirmPassword) {
        showToast('新密码和确认密码不匹配', 'warning');
        return;
        }

        if (newPassword.length < 6) {
        showToast('新密码长度至少6位', 'warning');
        return;
        }

        try {
        const response = await fetch(`${apiBase}/change-admin-password`, {
            method: 'POST',
            headers: {
            'Authorization': `Bearer ${authToken}`,
            'Content-Type': 'application/json'
            },
            body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword
            })
        });

        if (response.ok) {
            const result = await response.json();
            if (result.success) {
            showToast('密码更新成功，请重新登录', 'success');
            passwordForm.reset();
            // 3秒后跳转到登录页面
            setTimeout(() => {
                localStorage.removeItem('auth_token');
                window.location.href = '/login.html';
            }, 3000);
            } else {
            showToast(`密码更新失败: ${result.message}`, 'danger');
            }
        } else {
            const error = await response.text();
            showToast(`密码更新失败: ${error}`, 'danger');
        }
        } catch (error) {
        console.error('密码更新失败:', error);
        showToast('密码更新失败', 'danger');
        }
    });
    }

    // 页面加载时加载用户设置（仅在已登录时）
    if (authToken) {
        loadUserSettings();
    }
});

// ==================== 备份管理功能 ====================

// 下载数据库备份
async function downloadDatabaseBackup() {
    try {
    showToast('正在准备数据库备份，请稍候...', 'info');

    const response = await fetch(`${apiBase}/admin/backup/download`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        // 获取文件名
        const contentDisposition = response.headers.get('content-disposition');
        let filename = 'xianyu_backup.db';
        if (contentDisposition) {
        const filenameMatch = contentDisposition.match(/filename="(.+)"/);
        if (filenameMatch) {
            filename = filenameMatch[1];
        }
        }

        // 下载文件
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);

        showToast('数据库备份下载成功', 'success');
    } else {
        const error = await response.text();
        showToast(`下载失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('下载数据库备份失败:', error);
    showToast('下载数据库备份失败', 'danger');
    }
}

// 上传数据库备份
async function uploadDatabaseBackup() {
    const fileInput = document.getElementById('databaseFile');
    const file = fileInput.files[0];

    if (!file) {
    showToast('请选择数据库文件', 'warning');
    return;
    }

    if (!file.name.endsWith('.db')) {
    showToast('只支持.db格式的数据库文件', 'warning');
    return;
    }

    // 文件大小检查（限制100MB）
    if (file.size > 100 * 1024 * 1024) {
    showToast('数据库文件大小不能超过100MB', 'warning');
    return;
    }

    if (!confirm('恢复数据库将完全替换当前所有数据，包括所有用户、Cookie、卡券等信息。\n\n此操作不可撤销！\n\n确定要继续吗？')) {
    return;
    }

    try {
    showToast('正在上传并恢复数据库，请稍候...', 'info');

    const formData = new FormData();
    formData.append('backup_file', file);

    const response = await fetch(`${apiBase}/admin/backup/upload`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`
        },
        body: formData
    });

    if (response.ok) {
        const result = await response.json();
        showToast(`数据库恢复成功！包含 ${result.user_count} 个用户`, 'success');

        // 清空文件选择
        fileInput.value = '';

        // 提示用户刷新页面
        setTimeout(() => {
        if (confirm('数据库已恢复，建议刷新页面以加载新数据。是否立即刷新？')) {
            window.location.reload();
        }
        }, 2000);

    } else {
        const error = await response.json();
        showToast(`恢复失败: ${error.detail}`, 'danger');
    }
    } catch (error) {
    console.error('上传数据库备份失败:', error);
    showToast('上传数据库备份失败', 'danger');
    }
}

// 导出备份（JSON格式，兼容旧版本）
async function exportBackup() {
    try {
    showToast('正在导出备份，请稍候...', 'info');

    const response = await fetch(`${apiBase}/backup/export`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const backupData = await response.json();

        // 生成文件名
        const now = new Date();
        const timestamp = now.getFullYear() +
                        String(now.getMonth() + 1).padStart(2, '0') +
                        String(now.getDate()).padStart(2, '0') + '_' +
                        String(now.getHours()).padStart(2, '0') +
                        String(now.getMinutes()).padStart(2, '0') +
                        String(now.getSeconds()).padStart(2, '0');
        const filename = `xianyu_backup_${timestamp}.json`;

        // 创建下载链接
        const blob = new Blob([JSON.stringify(backupData, null, 2)], { type: 'application/json' });
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);

        showToast('备份导出成功', 'success');
    } else {
        const error = await response.text();
        showToast(`导出失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('导出备份失败:', error);
    showToast('导出备份失败', 'danger');
    }
}

// 导入备份
async function importBackup() {
    const fileInput = document.getElementById('backupFile');
    const file = fileInput.files[0];

    if (!file) {
    showToast('请选择备份文件', 'warning');
    return;
    }

    if (!file.name.endsWith('.json')) {
    showToast('只支持JSON格式的备份文件', 'warning');
    return;
    }

    if (!confirm('导入备份将覆盖当前所有数据，确定要继续吗？')) {
    return;
    }

    try {
    showToast('正在导入备份，请稍候...', 'info');

    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${apiBase}/backup/import`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`
        },
        body: formData
    });

    if (response.ok) {
        showToast('备份导入成功！正在刷新数据...', 'success');

        // 清空文件选择
        fileInput.value = '';

        // 清除前端缓存
        clearKeywordCache();

        // 延迟一下再刷新数据，确保后端缓存已更新
        setTimeout(async () => {
        try {
            // 如果当前在关键字管理页面，重新加载数据
            if (currentCookieId) {
            await loadAccountKeywords();
            }

            // 刷新仪表盘数据
            if (document.getElementById('dashboard-section').classList.contains('active')) {
            await loadDashboard();
            }

            // 刷新账号列表
            if (document.getElementById('accounts-section').classList.contains('active')) {
            await loadCookies();
            }

            showToast('数据刷新完成！', 'success');
        } catch (error) {
            console.error('刷新数据失败:', error);
            showToast('备份导入成功，但数据刷新失败，请手动刷新页面', 'warning');
        }
        }, 1000);
    } else {
        const error = await response.text();
        showToast(`导入失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('导入备份失败:', error);
    showToast('导入备份失败', 'danger');
    }
}

// 刷新系统缓存
async function reloadSystemCache() {
    try {
    showToast('正在刷新系统缓存...', 'info');

    const response = await fetch(`${apiBase}/system/reload-cache`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const result = await response.json();
        showToast('系统缓存刷新成功！关键字等数据已更新', 'success');

        // 清除前端缓存
        clearKeywordCache();

        // 如果当前在关键字管理页面，重新加载数据
        if (currentCookieId) {
        setTimeout(() => {
            loadAccountKeywords();
        }, 500);
        }
    } else {
        const error = await response.text();
        showToast(`刷新缓存失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('刷新系统缓存失败:', error);
    showToast('刷新系统缓存失败', 'danger');
    }
}

// 重启系统 - 显示确认对话框
function restartSystem() {
    // 使用 Bootstrap 模态框进行二次确认
    const modalHtml = `
        <div class="modal fade" id="restartConfirmModal" tabindex="-1">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header bg-danger text-white">
                        <h5 class="modal-title">
                            <i class="bi bi-exclamation-triangle me-2"></i>确认重启系统
                        </h5>
                        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <p class="mb-2"><strong>确定要重启系统吗？</strong></p>
                        <p class="text-muted mb-0">重启期间系统将暂时不可用，所有账号任务将重新启动。</p>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                        <button type="button" class="btn btn-danger" onclick="doRestartSystem()">
                            <i class="bi bi-power me-1"></i>确认重启
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `;

    // 移除已存在的模态框
    const existingModal = document.getElementById('restartConfirmModal');
    if (existingModal) {
        existingModal.remove();
    }

    // 添加模态框到页面
    document.body.insertAdjacentHTML('beforeend', modalHtml);

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('restartConfirmModal'));
    modal.show();
}

// 执行重启系统
async function doRestartSystem() {
    // 关闭确认模态框
    const confirmModal = bootstrap.Modal.getInstance(document.getElementById('restartConfirmModal'));
    if (confirmModal) {
        confirmModal.hide();
    }

    try {
        showToast('正在重启系统...', 'info');

        const response = await fetch('/api/update/restart', {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            showToast('系统正在重启，请稍候刷新页面...', 'success');

            // 5秒后自动刷新页面
            setTimeout(() => {
                window.location.reload();
            }, 5000);
        } else {
            const error = await response.json();
            showToast(`重启失败: ${error.detail || error.message || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('重启系统失败:', error);
        showToast('重启系统失败，请检查网络连接', 'danger');
    }
}

// ==================== 工具提示初始化 ====================

// 初始化工具提示
function initTooltips() {
    // 初始化所有工具提示
    const tooltipTriggerList = [].slice.call(document.querySelectorAll('[data-bs-toggle="tooltip"]'));
    tooltipTriggerList.map(function (tooltipTriggerEl) {
        return new bootstrap.Tooltip(tooltipTriggerEl);
    });
}

// ==================== 系统设置功能 ====================

// 加载系统设置
async function loadSystemSettings() {
    console.log('加载系统设置');

    // 通过验证接口获取用户信息（更可靠）
    try {
        const response = await fetch(`${apiBase}/verify`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            const isAdmin = result.is_admin === true;

            console.log('用户信息:', result, '是否管理员:', isAdmin);

            // 显示/隐藏管理员专用设置（仅管理员可见）
            const apiSecuritySettings = document.getElementById('api-security-settings');
            const loginInfoSettings = document.getElementById('login-info-settings');
            const riskControlSettings = document.getElementById('risk-control-settings');
            const outgoingConfigs = document.getElementById('outgoing-configs');
            const backupManagement = document.getElementById('backup-management');
            const systemRestartBtn = document.getElementById('system-restart-btn');
            const dashboardHotUpdateGroup = document.getElementById('dashboardHotUpdateGroup');

            if (apiSecuritySettings) {
                apiSecuritySettings.style.display = isAdmin ? 'block' : 'none';
            }
            if (loginInfoSettings) {
                loginInfoSettings.style.display = isAdmin ? 'flex' : 'none';
            }
            if (riskControlSettings) {
                riskControlSettings.style.display = isAdmin ? 'block' : 'none';
            }
            if (outgoingConfigs) {
                outgoingConfigs.style.display = isAdmin ? 'block' : 'none';
            }
            if (backupManagement) {
                backupManagement.style.display = isAdmin ? 'block' : 'none';
            }
            if (systemRestartBtn) {
                systemRestartBtn.style.display = isAdmin ? 'inline-block' : 'none';
            }
            if (dashboardHotUpdateGroup) {
                dashboardHotUpdateGroup.style.display = isAdmin ? 'inline-flex' : 'none';
            }

            // 如果是管理员，加载所有管理员设置
            if (isAdmin) {
                refreshHotUpdatePreferencesMenu();
                await loadAPISecuritySettings();
                await loadRegistrationSettings();
                await loadLoginInfoSettings();
                await loadRiskControlNightSettings();
                await loadOutgoingConfigs();
            }
        }
    } catch (error) {
        console.error('获取用户信息失败:', error);
        // 出错时隐藏管理员功能
        const loginInfoSettings = document.getElementById('login-info-settings');
        const riskControlSettings = document.getElementById('risk-control-settings');
        const dashboardHotUpdateGroup = document.getElementById('dashboardHotUpdateGroup');
        if (loginInfoSettings) {
            loginInfoSettings.style.display = 'none';
        }
        if (riskControlSettings) {
            riskControlSettings.style.display = 'none';
        }
        if (dashboardHotUpdateGroup) {
            dashboardHotUpdateGroup.style.display = 'none';
        }
    }
}

// 加载API安全设置
async function loadAPISecuritySettings() {
    try {
        const response = await fetch('/system-settings', {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const settings = await response.json();

            // 加载QQ回复消息秘钥
            const qqReplySecretKey = settings.qq_reply_secret_key || '';
            const qqReplySecretKeyInput = document.getElementById('qqReplySecretKey');
            if (qqReplySecretKeyInput) {
                qqReplySecretKeyInput.value = qqReplySecretKey;
            }
        }
    } catch (error) {
        console.error('加载API安全设置失败:', error);
        showToast('加载API安全设置失败', 'danger');
    }
}

async function loadRiskControlNightSettings() {
    try {
        const response = await fetch('/system-settings', {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            throw new Error('加载夜间风控降频设置失败');
        }

        const settings = await response.json();
        const enabledInput = document.getElementById('riskControlNightModeEnabled');
        const startHourInput = document.getElementById('riskControlNightStartHour');
        const endHourInput = document.getElementById('riskControlNightEndHour');

        if (enabledInput) {
            enabledInput.checked = settings.risk_control_night_mode_enabled === 'true';
        }
        if (startHourInput) {
            startHourInput.value = settings.risk_control_night_start_hour || '1';
        }
        if (endHourInput) {
            endHourInput.value = settings.risk_control_night_end_hour || '6';
        }
    } catch (error) {
        console.error('加载夜间风控降频设置失败:', error);
        showToast('加载夜间风控降频设置失败', 'danger');
    }
}

async function saveRiskControlNightSettings() {
    const enabledInput = document.getElementById('riskControlNightModeEnabled');
    const startHourInput = document.getElementById('riskControlNightStartHour');
    const endHourInput = document.getElementById('riskControlNightEndHour');
    const statusBox = document.getElementById('riskControlNightSettingsStatus');

    if (!enabledInput || !startHourInput || !endHourInput) {
        return;
    }

    const startHour = Number.parseInt(startHourInput.value, 10);
    const endHour = Number.parseInt(endHourInput.value, 10);
    if (Number.isNaN(startHour) || startHour < 0 || startHour > 23 || Number.isNaN(endHour) || endHour < 0 || endHour > 23) {
        showToast('夜间时间必须填写 0-23 的整数小时', 'warning');
        return;
    }

    const payloads = [
        {
            key: 'risk_control_night_mode_enabled',
            value: enabledInput.checked ? 'true' : 'false',
            description: '是否启用夜间风控降频',
        },
        {
            key: 'risk_control_night_start_hour',
            value: String(startHour),
            description: '夜间风控降频开始小时',
        },
        {
            key: 'risk_control_night_end_hour',
            value: String(endHour),
            description: '夜间风控降频结束小时',
        }
    ];

    try {
        for (const item of payloads) {
            const response = await fetch(`/system-settings/${item.key}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({
                    value: item.value,
                    description: item.description,
                })
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.detail || `保存 ${item.key} 失败`);
            }
        }

        if (statusBox) {
            statusBox.textContent = `夜间风控降频设置已保存：${enabledInput.checked ? '开启' : '关闭'}，区间 ${String(startHour).padStart(2, '0')}:00 - ${String(endHour).padStart(2, '0')}:00`;
            statusBox.classList.remove('d-none');
        }
        showToast('夜间风控降频设置已保存', 'success');
    } catch (error) {
        console.error('保存夜间风控降频设置失败:', error);
        showToast(`保存夜间风控降频设置失败: ${error.message || '未知错误'}`, 'danger');
    }
}

// 加载防抖延迟设置
async function loadDebounceDelay() {
    try {
        const response = await fetch('/system-settings', {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        if (response.ok) {
            const settings = await response.json();
            const val = settings.message_debounce_delay;
            const input = document.getElementById('debounceDelay');
            if (input && val) {
                input.value = parseInt(val) || 3;
            }
        }
    } catch (error) {
        console.error('加载防抖延迟设置失败:', error);
    }
}

// 保存防抖延迟设置
async function saveDebounceDelay() {
    const input = document.getElementById('debounceDelay');
    if (!input) return;
    const val = parseInt(input.value);
    if (isNaN(val) || val < 1 || val > 10) {
        showToast('防抖延迟需在1-10秒之间', 'warning');
        return;
    }
    try {
        const response = await fetch('/system-settings/message_debounce_delay', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                key: 'message_debounce_delay',
                value: String(val),
                description: '消息防抖延迟时间（秒）'
            })
        });
        if (response.ok) {
            showToast('防抖延迟已保存', 'success');
        } else {
            showToast('保存防抖延迟失败', 'danger');
        }
    } catch (error) {
        console.error('保存防抖延迟失败:', error);
        showToast('保存防抖延迟失败', 'danger');
    }
}

// 切换密码可见性
function togglePasswordVisibility(inputId) {
    const input = document.getElementById(inputId);
    const icon = document.getElementById(inputId + '-icon');

    if (input && icon) {
        if (input.type === 'password') {
            input.type = 'text';
            icon.className = 'bi bi-eye-slash';
        } else {
            input.type = 'password';
            icon.className = 'bi bi-eye';
        }
    }
}

// 生成随机秘钥
function generateRandomSecretKey() {
    // 生成32位随机字符串
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    let result = 'xianyu_qq_';
    for (let i = 0; i < 24; i++) {
        result += chars.charAt(Math.floor(Math.random() * chars.length));
    }

    const qqReplySecretKeyInput = document.getElementById('qqReplySecretKey');
    if (qqReplySecretKeyInput) {
        qqReplySecretKeyInput.value = result;
        showToast('随机秘钥已生成', 'success');
    }
}

// 更新QQ回复消息秘钥
async function updateQQReplySecretKey() {
    const qqReplySecretKey = document.getElementById('qqReplySecretKey').value.trim();

    if (!qqReplySecretKey) {
        showToast('请输入QQ回复消息API秘钥', 'warning');
        return;
    }

    if (qqReplySecretKey.length < 8) {
        showToast('秘钥长度至少需要8位字符', 'warning');
        return;
    }

    try {
        const response = await fetch('/system-settings/qq_reply_secret_key', {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                value: qqReplySecretKey,
                description: 'QQ回复消息API秘钥'
            })
        });

        if (response.ok) {
            showToast('QQ回复消息API秘钥更新成功', 'success');

            // 显示状态信息
            const statusDiv = document.getElementById('qqReplySecretStatus');
            const statusText = document.getElementById('qqReplySecretStatusText');
            if (statusDiv && statusText) {
                statusText.textContent = `秘钥已更新，长度: ${qqReplySecretKey.length} 位`;
                statusDiv.style.display = 'block';

                // 3秒后隐藏状态
                setTimeout(() => {
                    statusDiv.style.display = 'none';
                }, 3000);
            }
        } else {
            const errorData = await response.json();
            showToast(`更新失败: ${errorData.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('更新QQ回复消息秘钥失败:', error);
        showToast('更新QQ回复消息秘钥失败', 'danger');
    }
}

// 加载外发配置
async function loadOutgoingConfigs() {
    try {
        const response = await fetch('/system-settings', {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        if (response.ok) {
            const settings = await response.json();
            
            // 渲染外发配置界面
            renderOutgoingConfigs(settings);
        }
    } catch (error) {
        console.error('加载外发配置失败:', error);
        showToast('加载外发配置失败', 'danger');
    }
}

// 渲染外发配置界面
function renderOutgoingConfigs(settings) {
    const container = document.getElementById('outgoing-configs');
    if (!container) return;
    
    let html = '<div class="row">';
    
    // 渲染SMTP配置
    const smtpConfig = outgoingConfigs.smtp;
    html += `
        <div class="col-12">
            <div class="card">
                <div class="card-header">
                    <h5 class="mb-0">
                        <i class="bi ${smtpConfig.icon} text-${smtpConfig.color} me-2"></i>
                        ${smtpConfig.title}
                    </h5>
                </div>
                <div class="card-body">
                    <p class="text-muted">${smtpConfig.description}</p>
                    <form id="smtp-config-form">
                        <div class="row">`;
    
    smtpConfig.fields.forEach(field => {
        const value = settings[field.id] || '';
        html += `
            <div class="col-md-6 mb-3">
                <label for="${field.id}" class="form-label">${field.label}</label>
                ${generateOutgoingFieldHtml(field, value)}
                <div class="form-text">${field.help}</div>
            </div>`;
    });
    
    html += `
                        </div>
                        <div class="text-end">
                            <button type="submit" class="btn btn-primary">
                                <i class="bi bi-save me-1"></i>保存SMTP配置
                            </button>
                        </div>
                    </form>
                </div>
            </div>
        </div>`;
    
    html += '</div>';
    container.innerHTML = html;
    
    // 绑定表单提交事件
    const form = document.getElementById('smtp-config-form');
    if (form) {
        form.addEventListener('submit', saveOutgoingConfigs);
    }
}

// 生成外发配置字段HTML
function generateOutgoingFieldHtml(field, value) {
    switch (field.type) {
        case 'select':
            let options = '';
            field.options.forEach(option => {
                const selected = value === option.value ? 'selected' : '';
                options += `<option value="${option.value}" ${selected}>${option.text}</option>`;
            });
            return `<select class="form-select" id="${field.id}" name="${field.id}" ${field.required ? 'required' : ''}>${options}</select>`;
        
        case 'password':
            return `<input type="password" class="form-control" id="${field.id}" name="${field.id}" value="${value}" placeholder="${field.placeholder}" ${field.required ? 'required' : ''}>`;
        
        case 'number':
            return `<input type="number" class="form-control" id="${field.id}" name="${field.id}" value="${value}" placeholder="${field.placeholder}" ${field.required ? 'required' : ''}>`;
        
        case 'email':
            return `<input type="email" class="form-control" id="${field.id}" name="${field.id}" value="${value}" placeholder="${field.placeholder}" ${field.required ? 'required' : ''}>`;
        
        default:
            return `<input type="text" class="form-control" id="${field.id}" name="${field.id}" value="${value}" placeholder="${field.placeholder}" ${field.required ? 'required' : ''}>`;
    }
}

// 保存外发配置
async function saveOutgoingConfigs(event) {
    event.preventDefault();
    
    const form = event.target;
    const formData = new FormData(form);
    const configs = {};
    
    // 收集表单数据
    for (let [key, value] of formData.entries()) {
        configs[key] = value;
    }
    
    try {
        // 逐个保存配置项
        for (const [key, value] of Object.entries(configs)) {
            const response = await fetch(`/system-settings/${key}`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({
                    key: key,
                    value: value,
                    description: `SMTP配置 - ${key}`
                })
            });
            
            if (!response.ok) {
                throw new Error(`保存${key}失败`);
            }
        }
        
        showToast('外发配置保存成功', 'success');
        
        // 重新加载配置
        await loadOutgoingConfigs();
        
    } catch (error) {
        console.error('保存外发配置失败:', error);
        showToast('保存外发配置失败: ' + error.message, 'danger');
    }
}

// 加载注册设置
async function loadRegistrationSettings() {
    try {
        const response = await fetch('/registration-status');
        if (response.ok) {
            const data = await response.json();
            const checkbox = document.getElementById('registrationEnabled');
            if (checkbox) {
                checkbox.checked = data.enabled;
            }
        }
    } catch (error) {
        console.error('加载注册设置失败:', error);
        showToast('加载注册设置失败', 'danger');
    }
}

// 加载默认登录信息设置
async function loadLoginInfoSettings() {
    try {
        const response = await fetch('/system-settings', {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const settings = await response.json();
            const checkbox = document.getElementById('showDefaultLoginInfo');
            const captchaCheckbox = document.getElementById('loginCaptchaEnabled');

            if (checkbox && settings.show_default_login_info !== undefined) {
                checkbox.checked = settings.show_default_login_info === 'true';
            }

            if (captchaCheckbox && settings.login_captcha_enabled !== undefined) {
                captchaCheckbox.checked = settings.login_captcha_enabled === 'true';
            } else if (captchaCheckbox) {
                // 默认开启
                captchaCheckbox.checked = true;
            }
        }
    } catch (error) {
        console.error('加载登录信息设置失败:', error);
        showToast('加载登录信息设置失败', 'danger');
    }
}

// 更新登录与注册设置
async function updateLoginInfoSettings() {
    const registrationCheckbox = document.getElementById('registrationEnabled');
    const checkbox = document.getElementById('showDefaultLoginInfo');
    const captchaCheckbox = document.getElementById('loginCaptchaEnabled');
    const statusDiv = document.getElementById('loginInfoStatus');
    const statusText = document.getElementById('loginInfoStatusText');

    try {
        let messages = [];

        // 更新用户注册设置
        if (registrationCheckbox) {
            const regEnabled = registrationCheckbox.checked;
            const regResponse = await fetch('/registration-settings', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({ enabled: regEnabled })
            });

            if (regResponse.ok) {
                messages.push(regEnabled ? '用户注册已开启' : '用户注册已关闭');
            } else {
                const errorData = await regResponse.json();
                showToast(`更新注册设置失败: ${errorData.detail || '未知错误'}`, 'danger');
                return;
            }
        }

        // 更新显示默认登录信息设置
        if (checkbox) {
            const enabled = checkbox.checked;
            const response = await fetch('/login-info-settings', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({ enabled: enabled })
            });

            if (response.ok) {
                messages.push(enabled ? '默认登录信息显示已开启' : '默认登录信息显示已关闭');
            } else {
                const errorData = await response.json();
                showToast(`更新默认登录信息设置失败: ${errorData.detail || '未知错误'}`, 'danger');
                return;
            }
        }

        // 更新登录验证码设置
        if (captchaCheckbox) {
            const captchaEnabled = captchaCheckbox.checked;
            const captchaResponse = await fetch('/login-captcha-settings', {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({ enabled: captchaEnabled })
            });

            if (captchaResponse.ok) {
                messages.push(captchaEnabled ? '登录验证码已开启' : '登录验证码已关闭');
            } else {
                const errorData = await captchaResponse.json();
                showToast(`更新登录验证码设置失败: ${errorData.detail || '未知错误'}`, 'danger');
                return;
            }
        }

        // 显示成功消息
        const message = messages.join('，');
        showToast('设置保存成功', 'success');

        // 显示状态信息
        if (statusDiv && statusText) {
            statusText.textContent = message;
            statusDiv.style.display = 'block';

            // 3秒后隐藏状态信息
            setTimeout(() => {
                statusDiv.style.display = 'none';
            }, 3000);
        }
    } catch (error) {
        console.error('更新登录信息设置失败:', error);
        showToast('更新登录信息设置失败', 'danger');
    }
}

// ================================
// 数据管理功能
// ================================

// 全局变量
let currentTable = '';
let currentData = [];

// 表的中文描述
const tableDescriptions = {
    'users': '用户表',
    'cookies': 'Cookie账号表',
    'cookie_status': 'Cookie状态表',
    'keywords': '关键字表',
    'item_replay': '指定商品回复表',
    'default_replies': '默认回复表',
    'default_reply_records': '默认回复记录表',
    'ai_reply_settings': 'AI回复设置表',
    'ai_conversations': 'AI对话历史表',
    'ai_item_cache': 'AI商品信息缓存表',
    'item_info': '商品信息表',
    'message_notifications': '消息通知表',
    'cards': '卡券表',
    'delivery_rules': '发货规则表',
    'notification_channels': '通知渠道表',
    'user_settings': '用户设置表',
    'system_settings': '系统设置表',
    'email_verifications': '邮箱验证表',
    'captcha_codes': '验证码表',
    'orders': '订单表'
};

// 加载数据管理页面
async function loadDataManagement() {
    console.log('加载数据管理页面');

    // 检查管理员权限
    try {
        const response = await fetch(`${apiBase}/verify`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const result = await response.json();
            if (!result.is_admin) {
                showToast('您没有权限访问数据管理功能', 'danger');
                showSection('dashboard'); // 跳转回仪表盘
                return;
            }
        } else {
            showToast('权限验证失败', 'danger');
            return;
        }
    } catch (error) {
        console.error('权限验证失败:', error);
        showToast('权限验证失败', 'danger');
        return;
    }

    // 重置状态
    currentTable = '';
    currentData = [];

    // 重置界面
    showNoTableSelected();

    // 重置表格选择器
    const tableSelect = document.getElementById('tableSelect');
    if (tableSelect) {
        tableSelect.value = '';
    }
}

// 显示未选择表格状态
function showNoTableSelected() {
    document.getElementById('loadingTable').style.display = 'none';
    document.getElementById('noTableSelected').style.display = 'block';
    document.getElementById('noTableData').style.display = 'none';
    document.getElementById('tableContainer').style.display = 'none';

    // 重置统计信息
    document.getElementById('recordCount').textContent = '-';
    document.getElementById('tableTitle').innerHTML = '<i class="bi bi-table"></i> 数据表';

    // 禁用按钮
    document.getElementById('clearBtn').disabled = true;
}

// 显示加载状态
function showLoading() {
    document.getElementById('loadingTable').style.display = 'block';
    document.getElementById('noTableSelected').style.display = 'none';
    document.getElementById('noTableData').style.display = 'none';
    document.getElementById('tableContainer').style.display = 'none';
}

// 显示无数据状态
function showNoData() {
    document.getElementById('loadingTable').style.display = 'none';
    document.getElementById('noTableSelected').style.display = 'none';
    document.getElementById('noTableData').style.display = 'block';
    document.getElementById('tableContainer').style.display = 'none';
}

// 加载表数据
async function loadTableData() {
    const tableSelect = document.getElementById('tableSelect');
    const selectedTable = tableSelect.value;

    if (!selectedTable) {
        showNoTableSelected();
        return;
    }

    currentTable = selectedTable;
    showLoading();

    const token = localStorage.getItem('auth_token');

    try {
        const response = await fetch(`/admin/data/${selectedTable}`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        const data = await response.json();

        if (data.success) {
            currentData = data.data;
            displayTableData(data.data, data.columns);
            updateTableInfo(selectedTable, data.data.length);
        } else {
            showToast('加载数据失败: ' + data.message, 'danger');
            showNoData();
        }
    } catch (error) {
        console.error('加载数据失败:', error);
        showToast('加载数据失败', 'danger');
        showNoData();
    }
}

// 显示表格数据
function displayTableData(data, columns) {
    if (!data || data.length === 0) {
        showNoData();
        return;
    }

    // 显示表格容器
    document.getElementById('loadingTable').style.display = 'none';
    document.getElementById('noTableSelected').style.display = 'none';
    document.getElementById('noTableData').style.display = 'none';
    document.getElementById('tableContainer').style.display = 'block';

    // 生成表头（添加操作列）
    const tableHeaders = document.getElementById('tableHeaders');
    const headerHtml = columns.map(col => `<th>${col}</th>`).join('') + '<th width="100">操作</th>';
    tableHeaders.innerHTML = headerHtml;

    // 生成表格内容（添加删除按钮）
    const tableBody = document.getElementById('tableBody');
    tableBody.innerHTML = data.map((row, index) => {
        const dataCells = columns.map(col => {
            let value = row[col];
            if (value === null || value === undefined) {
                value = '<span class="text-muted">NULL</span>';
            } else if (typeof value === 'string' && value.length > 50) {
                value = `<span title="${escapeHtml(value)}">${escapeHtml(value.substring(0, 50))}...</span>`;
            } else {
                value = escapeHtml(String(value));
            }
            return `<td>${value}</td>`;
        }).join('');

        // 添加操作列（删除按钮）
        const recordId = row.id || row.user_id || index;
        const actionCell = `<td>
            <button class="btn btn-danger btn-sm" onclick="deleteRecordByIndex(${index})" title="删除记录">
                <i class="bi bi-trash"></i>
            </button>
        </td>`;

        return `<tr>${dataCells}${actionCell}</tr>`;
    }).join('');
}

// HTML转义函数
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// 更新表格信息
function updateTableInfo(tableName, recordCount) {
    const description = tableDescriptions[tableName] || tableName;
    document.getElementById('tableTitle').innerHTML = `<i class="bi bi-table"></i> ${description}`;
    document.getElementById('recordCount').textContent = recordCount;

    // 启用清空按钮
    document.getElementById('clearBtn').disabled = false;
}

// 刷新表格数据
function refreshTableData() {
    if (currentTable) {
        loadTableData();
        showToast('数据已刷新', 'success');
    } else {
        showToast('请先选择数据表', 'warning');
    }
}

// 导出表格数据
async function exportTableData() {
    if (!currentTable || !currentData || currentData.length === 0) {
        showToast('没有可导出的数据', 'warning');
        return;
    }

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch(`/admin/data/${currentTable}/export`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const blob = await response.blob();
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = `${currentTable}_${new Date().toISOString().slice(0, 10)}.xlsx`;
            document.body.appendChild(a);
            a.click();
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);

            showToast('数据导出成功', 'success');
        } else {
            showToast('导出失败', 'danger');
        }
    } catch (error) {
        console.error('导出数据失败:', error);
        showToast('导出数据失败', 'danger');
    }
}

// 清空表格数据
async function clearTableData() {
    if (!currentTable) {
        showToast('请先选择数据表', 'warning');
        return;
    }

    const description = tableDescriptions[currentTable] || currentTable;
    const confirmed = confirm(`确定要清空 "${description}" 的所有数据吗？\n\n此操作不可撤销！`);

    if (!confirmed) return;

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch(`/admin/data/${currentTable}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            showToast(data.message || '数据清空成功', 'success');
            // 重新加载数据
            loadTableData();
        } else {
            const errorData = await response.json();
            showToast(`清空失败: ${errorData.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('清空数据失败:', error);
        showToast('清空数据失败', 'danger');
    }
}

// 删除记录相关变量
let currentDeleteId = null;
let deleteRecordModal = null;

// 初始化删除记录模态框
function initDeleteRecordModal() {
    if (!deleteRecordModal) {
        deleteRecordModal = new bootstrap.Modal(document.getElementById('deleteRecordModal'));
    }
}

// 通过索引删除记录
function deleteRecordByIndex(index) {
    console.log('deleteRecordByIndex被调用，index:', index);
    console.log('currentData:', currentData);
    console.log('当前currentTable:', currentTable);

    if (!currentData || index >= currentData.length) {
        console.error('无效的索引或数据不存在');
        showToast('删除失败：数据不存在', 'danger');
        return;
    }

    const record = currentData[index];
    console.log('获取到的record:', record);

    deleteRecord(record, index);
}

// 删除记录
function deleteRecord(record, index) {
    console.log('deleteRecord被调用');
    console.log('record:', record);
    console.log('index:', index);
    console.log('当前currentTable:', currentTable);

    initDeleteRecordModal();

    // 尝试多种方式获取记录ID
    currentDeleteId = record.id || record.user_id || record.cookie_id || record.keyword_id ||
                     record.card_id || record.item_id || record.order_id || index;

    console.log('设置currentDeleteId为:', currentDeleteId);
    console.log('record的所有字段:', Object.keys(record));
    console.log('record的所有值:', record);

    // 显示记录信息
    const deleteRecordInfo = document.getElementById('deleteRecordInfo');
    deleteRecordInfo.innerHTML = '';

    Object.keys(record).forEach(key => {
        const div = document.createElement('div');
        div.innerHTML = `<strong>${key}:</strong> ${record[key] || '-'}`;
        deleteRecordInfo.appendChild(div);
    });

    deleteRecordModal.show();
}

// 确认删除记录
async function confirmDeleteRecord() {
    console.log('confirmDeleteRecord被调用');
    console.log('currentDeleteId:', currentDeleteId);
    console.log('currentTable:', currentTable);

    if (!currentDeleteId || !currentTable) {
        console.error('缺少必要参数:', { currentDeleteId, currentTable });
        showToast('删除失败：缺少必要参数', 'danger');
        return;
    }

    try {
        const token = localStorage.getItem('auth_token');
        const url = `/admin/data/${currentTable}/${currentDeleteId}`;
        console.log('发送删除请求到:', url);

        const response = await fetch(url, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        console.log('删除响应状态:', response.status);

        if (response.ok) {
            const data = await response.json();
            console.log('删除成功响应:', data);
            deleteRecordModal.hide();
            showToast(data.message || '删除成功', 'success');
            loadTableData(); // 重新加载数据
        } else {
            const errorData = await response.json();
            console.error('删除失败响应:', errorData);
            showToast(`删除失败: ${errorData.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('删除记录失败:', error);
        showToast('删除记录失败: ' + error.message, 'danger');
    }
}

