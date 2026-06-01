// ================================
// 用户管理功能
// ================================

// 加载用户管理页面
async function loadUserManagement() {
    console.log('加载用户管理页面');

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
                showToast('您没有权限访问用户管理功能', 'danger');
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

    // 加载数据
    await loadUserSystemStats();
    await loadUsers();
}

// 加载用户系统统计信息
async function loadUserSystemStats() {
    try {
        const token = localStorage.getItem('auth_token');

        // 获取用户统计
        const usersResponse = await fetch('/admin/users', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (usersResponse.ok) {
            const usersData = await usersResponse.json();
            document.getElementById('totalUsers').textContent = usersData.users.length;
        }

        // 获取Cookie统计
        const cookiesResponse = await fetch(`${apiBase}/admin/data/cookies`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (cookiesResponse.ok) {
            const cookiesData = await cookiesResponse.json();
            document.getElementById('totalUserCookies').textContent = cookiesData.data ? cookiesData.data.length : 0;
        }

        // 获取卡券统计
        const cardsResponse = await fetch(`${apiBase}/admin/data/cards`, {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (cardsResponse.ok) {
            const cardsData = await cardsResponse.json();
            document.getElementById('totalUserCards').textContent = cardsData.data ? cardsData.data.length : 0;
        }

    } catch (error) {
        console.error('加载系统统计失败:', error);
    }
}

// 加载用户列表
async function loadUsers() {
    const loadingDiv = document.getElementById('loadingUsers');
    const usersListDiv = document.getElementById('usersList');
    const noUsersDiv = document.getElementById('noUsers');

    // 显示加载状态
    loadingDiv.style.display = 'block';
    usersListDiv.style.display = 'none';
    noUsersDiv.style.display = 'none';

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch('/admin/users', {
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            loadingDiv.style.display = 'none';

            if (data.users && data.users.length > 0) {
                usersListDiv.style.display = 'block';
                displayUsers(data.users);
            } else {
                noUsersDiv.style.display = 'block';
            }
        } else {
            throw new Error('获取用户列表失败');
        }
    } catch (error) {
        console.error('加载用户列表失败:', error);
        loadingDiv.style.display = 'none';
        noUsersDiv.style.display = 'block';
        showToast('加载用户列表失败', 'danger');
    }
}

// 显示用户列表
function displayUsers(users) {
    const usersListDiv = document.getElementById('usersList');
    usersListDiv.innerHTML = '';

    users.forEach(user => {
        const userCard = createUserCard(user);
        usersListDiv.appendChild(userCard);
    });
}

// 创建用户卡片
function createUserCard(user) {
    const col = document.createElement('div');
    col.className = 'col-md-6 col-lg-4 mb-3';

    // 使用is_admin字段判断是否为管理员
    const isAdmin = user.is_admin === true;
    const badgeClass = isAdmin ? 'bg-danger' : 'bg-primary';
    const badgeText = isAdmin ? '管理员' : '普通用户';

    // 获取当前登录用户的ID
    let currentUserId = null;
    try {
        const userInfo = JSON.parse(localStorage.getItem('user_info') || '{}');
        currentUserId = userInfo.user_id;
    } catch (e) {
        console.error('解析用户信息失败:', e);
    }
    const isSelf = user.id === currentUserId;

    col.innerHTML = `
        <div class="card user-card h-100">
            <div class="card-body">
                <div class="d-flex justify-content-between align-items-start mb-2">
                    <h6 class="card-title mb-0">${user.username}</h6>
                    <span class="badge ${badgeClass}">${badgeText}</span>
                </div>
                <p class="card-text text-muted small">
                    <i class="bi bi-envelope me-1"></i>${user.email || '未设置邮箱'}
                </p>
                <p class="card-text text-muted small">
                    <i class="bi bi-calendar me-1"></i>注册时间：${formatDateTime(user.created_at)}
                </p>
                <div class="d-flex justify-content-between align-items-center">
                    <small class="text-muted">
                        Cookie数: ${user.cookie_count || 0} |
                        卡券数: ${user.card_count || 0}
                    </small>
                    <div class="btn-group btn-group-sm">
                        ${!isSelf ? `
                            <button class="btn ${isAdmin ? 'btn-warning' : 'btn-outline-success'}"
                                    onclick="toggleUserAdmin('${user.id}', '${user.username}', ${!isAdmin})"
                                    title="${isAdmin ? '取消管理员权限' : '设置为管理员'}">
                                <i class="bi ${isAdmin ? 'bi-person-dash' : 'bi-person-check'}"></i>
                            </button>
                            <button class="btn btn-outline-danger" onclick="deleteUser('${user.id}', '${user.username}')">
                                <i class="bi bi-trash"></i>
                            </button>
                        ` : `
                            <span class="badge bg-secondary">当前用户</span>
                        `}
                    </div>
                </div>
            </div>
        </div>
    `;

    return col;
}

// 切换用户管理员状态
async function toggleUserAdmin(userId, username, setAdmin) {
    const action = setAdmin ? '设置为管理员' : '取消管理员权限';

    if (!confirm(`确定要将用户 "${username}" ${action}吗？`)) {
        return;
    }

    try {
        const token = localStorage.getItem('auth_token');
        const response = await fetch(`/admin/users/${userId}/admin-status?is_admin=${setAdmin}`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${token}`,
                'Content-Type': 'application/json'
            }
        });

        if (response.ok) {
            const data = await response.json();
            showToast(data.message || `用户已${action}`, 'success');

            // 刷新用户列表
            await loadUsers();
        } else {
            const errorData = await response.json();
            showToast(`操作失败: ${errorData.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('更新用户权限失败:', error);
        showToast('更新用户权限失败', 'danger');
    }
}

// 全局变量用于存储当前要删除的用户信息
let currentDeleteUserId = null;
let currentDeleteUserName = null;
let deleteUserModal = null;

// 删除用户
function deleteUser(userId, username) {
    // 存储要删除的用户信息
    currentDeleteUserId = userId;
    currentDeleteUserName = username;

    // 初始化模态框（如果还没有初始化）
    if (!deleteUserModal) {
        deleteUserModal = new bootstrap.Modal(document.getElementById('deleteUserModal'));
    }

    // 显示确认模态框
    deleteUserModal.show();
}

// 确认删除用户
async function confirmDeleteUser() {
    if (!currentDeleteUserId) return;

    try {
        const token = localStorage.getItem('auth_token');

        const response = await fetch(`/admin/users/${currentDeleteUserId}`, {
            method: 'DELETE',
            headers: {
                'Authorization': `Bearer ${token}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            deleteUserModal.hide();
            showToast(data.message || '用户删除成功', 'success');

            // 刷新页面数据
            await loadUserSystemStats();
            await loadUsers();
        } else {
            const errorData = await response.json();
            showToast(`删除失败: ${errorData.detail || '未知错误'}`, 'danger');
        }
    } catch (error) {
        console.error('删除用户失败:', error);
        showToast('删除用户失败', 'danger');
    } finally {
        // 清理状态
        currentDeleteUserId = null;
        currentDeleteUserName = null;
    }
}

// 刷新用户列表
async function refreshUsers() {
    await loadUserSystemStats();
    await loadUsers();
    showToast('用户列表已刷新', 'success');
}

