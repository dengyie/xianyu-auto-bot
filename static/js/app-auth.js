// ==================== 导入导出功能 ====================

// 导出关键词
async function exportKeywords() {
    if (!currentCookieId) {
    showToast('请先选择账号', 'warning');
    return;
    }

    try {
    const response = await fetch(`${apiBase}/keywords-export/${currentCookieId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        // 创建下载链接
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;

        // 根据当前账号是否有数据来设置文件名和提示
        const currentKeywords = keywordsData[currentCookieId] || [];
        const hasData = currentKeywords.length > 0;

        if (hasData) {
        a.download = `keywords_${currentCookieId}_${new Date().getTime()}.xlsx`;
        showToast('关键词导出成功！', 'success');
        } else {
        a.download = `keywords_template_${currentCookieId}_${new Date().getTime()}.xlsx`;
        showToast('导入模板导出成功！模板中包含示例数据供参考', 'success');
        }

        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
    } else {
        const error = await response.json();
        showToast(`导出失败: ${error.detail}`, 'error');
    }
    } catch (error) {
    console.error('导出关键词失败:', error);
    showToast('导出关键词失败', 'error');
    }
}

// 显示导入模态框
function showImportModal() {
    if (!currentCookieId) {
    showToast('请先选择账号', 'warning');
    return;
    }

    const modal = new bootstrap.Modal(document.getElementById('importKeywordsModal'));
    modal.show();
}

// 导入关键词
async function importKeywords() {
    if (!currentCookieId) {
    showToast('请先选择账号', 'warning');
    return;
    }

    const fileInput = document.getElementById('importFileInput');
    const file = fileInput.files[0];

    if (!file) {
    showToast('请选择要导入的Excel文件', 'warning');
    return;
    }

    try {
    // 显示进度条
    const progressDiv = document.getElementById('importProgress');
    const progressBar = progressDiv.querySelector('.progress-bar');
    progressDiv.style.display = 'block';
    progressBar.style.width = '30%';

    const formData = new FormData();
    formData.append('file', file);

    const response = await fetch(`${apiBase}/keywords-import/${currentCookieId}`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`
        },
        body: formData
    });

    progressBar.style.width = '70%';

    if (response.ok) {
        const result = await response.json();
        progressBar.style.width = '100%';

        setTimeout(() => {
        progressDiv.style.display = 'none';
        progressBar.style.width = '0%';

        // 关闭模态框
        const modal = bootstrap.Modal.getInstance(document.getElementById('importKeywordsModal'));
        modal.hide();

        // 清空文件输入
        fileInput.value = '';

        // 重新加载关键词列表
        loadAccountKeywords(currentCookieId);

        showToast(`导入成功！新增: ${result.added}, 更新: ${result.updated}`, 'success');
        }, 500);
    } else {
        const error = await response.json();
        progressDiv.style.display = 'none';
        progressBar.style.width = '0%';
        showToast(`导入失败: ${error.detail}`, 'error');
    }
    } catch (error) {
    console.error('导入关键词失败:', error);
    document.getElementById('importProgress').style.display = 'none';
    document.querySelector('#importProgress .progress-bar').style.width = '0%';
    showToast('导入关键词失败', 'error');
    }
}

// ========================= 账号添加相关函数 =========================

// 切换手动输入表单显示/隐藏
function toggleManualInput() {
    const manualForm = document.getElementById('manualInputForm');
    const passwordForm = document.getElementById('passwordLoginForm');
    const refreshForm = document.getElementById('refreshCookieForm');
    if (manualForm.style.display === 'none') {
        // 隐藏账号密码登录表单
        if (passwordForm) {
            passwordForm.style.display = 'none';
        }
        // 隐藏刷新Cookie表单
        if (refreshForm) {
            refreshForm.style.display = 'none';
        }
        manualForm.style.display = 'block';
        // 清空表单
        document.getElementById('addForm').reset();
    } else {
        manualForm.style.display = 'none';
        resetManualCookieImportForm();
    }
}

let manualCookieImportCheckInterval = null;
let manualCookieImportSessionId = null;
let manualCookieImportPollingState = {
    sessionId: null,
    inFlight: false,
    completed: false
};

async function handleManualCookieImport(event) {
    event.preventDefault();

    const accountId = document.getElementById('cookieId').value.trim();
    const cookieValue = document.getElementById('cookieValue').value.trim();
    const showBrowserCheckbox = document.getElementById('manualCookieShowBrowser');
    const showBrowser = showBrowserCheckbox ? showBrowserCheckbox.checked : false;

    if (!accountId || !cookieValue) {
        showToast('请填写完整的账号ID和Cookie', 'warning');
        return;
    }

    const submitBtn = event.target.querySelector('button[type="submit"]');
    const originalText = submitBtn.innerHTML;
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>验证中...';

    try {
        const response = await fetch(`${apiBase}/manual-cookie-import`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                account_id: accountId,
                cookie: cookieValue,
                show_browser: showBrowser
            })
        });

        const data = await response.json();
        if (response.ok && data.success && data.session_id) {
            manualCookieImportSessionId = data.session_id;
            startManualCookieImportCheck(originalText);
        } else {
            showToast(data.message || 'Cookie 导入验证失败', 'danger');
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalText;
        }
    } catch (error) {
        console.error('手动导入 Cookie 失败:', error);
        showToast('网络错误，请重试', 'danger');
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalText;
    }
}

function clearManualCookieImportCheck() {
    if (manualCookieImportCheckInterval) {
        clearInterval(manualCookieImportCheckInterval);
        manualCookieImportCheckInterval = null;
    }
}

function resetManualCookieImportForm() {
    manualCookieImportSessionId = null;
    clearManualCookieImportCheck();
    manualCookieImportPollingState = {
        sessionId: null,
        inFlight: false,
        completed: false
    };

    const submitBtn = document.querySelector('#addForm button[type="submit"]');
    if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="bi bi-plus-lg me-1"></i>导入并验证账号';
    }
}

function handleManualCookieImportSuccess(data) {
    closePasswordLoginQRModal();
    showToast(`账号 ${data.account_id} 导入并验证成功`, 'success');

    const form = document.getElementById('addForm');
    if (form) {
        form.reset();
    }
    const manualForm = document.getElementById('manualInputForm');
    if (manualForm) {
        manualForm.style.display = 'none';
    }
    loadCookies();
    resetManualCookieImportForm();
}

function handleManualCookieImportFailure(data) {
    closePasswordLoginQRModal();
    showToast(data.message || data.error || 'Cookie 导入验证失败', 'danger');
    resetManualCookieImportForm();
}

function startManualCookieImportCheck(originalText) {
    clearManualCookieImportCheck();

    const submitBtn = document.querySelector('#addForm button[type="submit"]');
    if (submitBtn) {
        submitBtn.dataset.originalText = originalText;
    }

    manualCookieImportPollingState = {
        sessionId: manualCookieImportSessionId,
        inFlight: false,
        completed: false
    };

    manualCookieImportCheckInterval = setInterval(checkManualCookieImportStatus, 2000);
    checkManualCookieImportStatus();
}

async function checkManualCookieImportStatus() {
    if (!manualCookieImportSessionId || manualCookieImportPollingState.completed || manualCookieImportPollingState.inFlight) {
        return;
    }

    const sessionId = manualCookieImportSessionId;
    manualCookieImportPollingState.inFlight = true;

    try {
        const response = await fetch(`${apiBase}/manual-cookie-import/check/${sessionId}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            if (manualCookieImportPollingState.sessionId !== sessionId || manualCookieImportPollingState.completed) {
                return;
            }

            switch (data.status) {
                case 'processing':
                    break;
                case 'verification_required':
                    showPasswordLoginQRCode(
                        data.screenshot_path || data.verification_url,
                        data.screenshot_path,
                        data.verification_type
                    );
                    break;
                case 'success':
                    manualCookieImportPollingState.completed = true;
                    clearManualCookieImportCheck();
                    handleManualCookieImportSuccess(data);
                    break;
                case 'failed':
                    manualCookieImportPollingState.completed = true;
                    clearManualCookieImportCheck();
                    handleManualCookieImportFailure(data);
                    break;
                case 'not_found':
                case 'forbidden':
                case 'error':
                    manualCookieImportPollingState.completed = true;
                    clearManualCookieImportCheck();
                    closePasswordLoginQRModal();
                    showToast(data.message || 'Cookie 导入验证检查失败', 'danger');
                    resetManualCookieImportForm();
                    break;
            }
        } else {
            let errorMessage = 'Cookie 导入验证检查失败';
            try {
                const errorData = await response.json();
                errorMessage = errorData.message || errorData.detail || errorMessage;
            } catch (e) {
                // ignore parse error
            }
            manualCookieImportPollingState.completed = true;
            clearManualCookieImportCheck();
            closePasswordLoginQRModal();
            showToast(errorMessage, 'danger');
            resetManualCookieImportForm();
        }
    } catch (error) {
        console.error('检查手动导入 Cookie 状态失败:', error);
        manualCookieImportPollingState.completed = true;
        clearManualCookieImportCheck();
        closePasswordLoginQRModal();
        showToast('网络错误，请重试', 'danger');
        resetManualCookieImportForm();
    } finally {
        if (manualCookieImportPollingState.sessionId === sessionId) {
            manualCookieImportPollingState.inFlight = false;
        }
    }
}

// 切换账号密码登录表单显示/隐藏
function togglePasswordLogin() {
    const passwordForm = document.getElementById('passwordLoginForm');
    const manualForm = document.getElementById('manualInputForm');
    const refreshForm = document.getElementById('refreshCookieForm');
    if (passwordForm.style.display === 'none') {
        // 隐藏手动输入表单
        if (manualForm) {
            manualForm.style.display = 'none';
            resetManualCookieImportForm();
        }
        // 隐藏刷新Cookie表单
        if (refreshForm) {
            refreshForm.style.display = 'none';
        }
        passwordForm.style.display = 'block';
        // 清空表单
        document.getElementById('passwordLoginFormElement').reset();
    } else {
        passwordForm.style.display = 'none';
    }
}

// 切换刷新Cookie表单显示/隐藏
function toggleRefreshCookieForm() {
    const refreshForm = document.getElementById('refreshCookieForm');
    const manualForm = document.getElementById('manualInputForm');
    const passwordForm = document.getElementById('passwordLoginForm');

    if (refreshForm.style.display === 'none') {
        // 隐藏其他表单
        if (manualForm) {
            manualForm.style.display = 'none';
            resetManualCookieImportForm();
        }
        if (passwordForm) {
            passwordForm.style.display = 'none';
        }
        refreshForm.style.display = 'block';
        // 清空表单
        document.getElementById('refreshCookieFormElement').reset();
        document.getElementById('refreshCookieAccountStatus').innerHTML = '请先选择账号';
        // 加载账号列表到下拉框
        loadRefreshCookieAccountList();
    } else {
        refreshForm.style.display = 'none';
    }
}

// 加载账号列表到刷新Cookie下拉框
async function loadRefreshCookieAccountList() {
    const select = document.getElementById('refreshCookieAccountSelect');
    select.innerHTML = '<option value="">请选择账号...</option>';

    try {
        const response = await fetch(`${apiBase}/cookies/details`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        const data = await response.json();

        if (data && data.length > 0) {
            data.forEach(cookie => {
                const option = document.createElement('option');
                option.value = cookie.id;
                // 显示账号ID和是否配置了用户名密码
                const hasCredentials = cookie.username && cookie.has_password ? '(已配置账密)' : '(未配置账密)';
                option.textContent = `${cookie.id} ${hasCredentials}`;
                option.dataset.hasCredentials = cookie.username && cookie.has_password ? 'true' : 'false';
                option.dataset.username = cookie.username || '';
                select.appendChild(option);
            });
        }
    } catch (error) {
        console.error('加载账号列表失败:', error);
        showToast('加载账号列表失败', 'danger');
    }
}

// 刷新Cookie账号选择变化时显示状态
document.addEventListener('DOMContentLoaded', function() {
    const select = document.getElementById('refreshCookieAccountSelect');
    if (select) {
        select.addEventListener('change', function() {
            const statusDiv = document.getElementById('refreshCookieAccountStatus');
            const selectedOption = this.options[this.selectedIndex];

            if (this.value) {
                const hasCredentials = selectedOption.dataset.hasCredentials === 'true';
                const username = selectedOption.dataset.username;

                if (hasCredentials) {
                    statusDiv.innerHTML = `<span class="text-success"><i class="bi bi-check-circle me-1"></i>已配置用户名: ${username}</span>`;
                } else {
                    statusDiv.innerHTML = `<span class="text-danger"><i class="bi bi-x-circle me-1"></i>未配置用户名和密码，无法刷新</span>`;
                }
            } else {
                statusDiv.innerHTML = '请先选择账号';
            }
        });
    }

    // 绑定刷新Cookie表单提交事件
    const refreshForm = document.getElementById('refreshCookieFormElement');
    if (refreshForm) {
        refreshForm.addEventListener('submit', handleRefreshCookie);
    }
});

// 处理刷新Cookie表单提交
async function handleRefreshCookie(event) {
    event.preventDefault();

    const select = document.getElementById('refreshCookieAccountSelect');
    const cookieId = select.value;
    const selectedOption = select.options[select.selectedIndex];
    const showBrowser = document.getElementById('refreshCookieShowBrowser').checked;

    if (!cookieId) {
        showToast('请选择要刷新的账号', 'warning');
        return;
    }

    const hasCredentials = selectedOption.dataset.hasCredentials === 'true';
    if (!hasCredentials) {
        showToast('该账号未配置用户名和密码，无法刷新Cookie', 'danger');
        return;
    }

    // 显示loading
    toggleLoading(true);

    try {
        // 调用密码登录API刷新Cookie
        const response = await fetch(`${apiBase}/password-login`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                account_id: cookieId,
                refresh_mode: true,  // 标记为刷新模式
                show_browser: showBrowser
            })
        });

        const data = await response.json();

        if (data.session_id) {
            // 开始轮询检查登录状态
            showToast('正在验证账号并刷新Cookie，请稍候...', 'info');
            startRefreshCookiePolling(data.session_id, cookieId);
        } else {
            toggleLoading(false);
            showToast(data.message || '启动刷新失败', 'danger');
        }
    } catch (error) {
        toggleLoading(false);
        console.error('刷新Cookie失败:', error);
        showToast('刷新Cookie失败: ' + error.message, 'danger');
    }
}

// 更新刷新Cookie状态显示
function updateRefreshCookieStatus(message) {
    const statusDiv = document.getElementById('refreshCookieAccountStatus');
    if (statusDiv) {
        statusDiv.innerHTML = `<span class="text-info"><i class="bi bi-hourglass-split me-1"></i>${message}</span>`;
    }
}

// 轮询检查刷新Cookie状态
let refreshCookieCheckInterval = null;
let refreshCookiePollingState = {
    sessionId: null,
    cookieId: null,
    inFlight: false,
    completed: false
};

function stopRefreshCookiePolling(sessionId = refreshCookiePollingState.sessionId) {
    if (sessionId && refreshCookiePollingState.sessionId && refreshCookiePollingState.sessionId !== sessionId) {
        return;
    }

    if (refreshCookieCheckInterval) {
        clearInterval(refreshCookieCheckInterval);
        refreshCookieCheckInterval = null;
    }

    refreshCookiePollingState.completed = true;
}

function startRefreshCookiePolling(sessionId, cookieId) {
    // 清除之前的轮询
    stopRefreshCookiePolling();

    refreshCookiePollingState = {
        sessionId,
        cookieId,
        inFlight: false,
        completed: false
    };

    let checkCount = 0;
    const maxChecks = 120; // 最多检查120次，每次2秒，共4分钟

    const pollRefreshCookieStatus = async () => {
        if (refreshCookiePollingState.completed || refreshCookiePollingState.inFlight || refreshCookiePollingState.sessionId !== sessionId) {
            return;
        }

        refreshCookiePollingState.inFlight = true;
        checkCount++;

        if (checkCount > maxChecks) {
            stopRefreshCookiePolling(sessionId);
            closePasswordLoginQRModal();
            toggleLoading(false);
            showToast('刷新Cookie超时，请重试', 'warning');
            refreshCookiePollingState.inFlight = false;
            return;
        }

        try {
            const response = await fetch(`${apiBase}/password-login/check/${sessionId}`, {
                headers: {
                    'Authorization': `Bearer ${authToken}`
                }
            });
            const data = await response.json();

            if (refreshCookiePollingState.sessionId !== sessionId || refreshCookiePollingState.completed) {
                return;
            }

            console.log('刷新Cookie状态检查:', data); // 调试日志

            switch (data.status) {
                case 'processing':
                    // 处理中，更新状态显示
                    updateRefreshCookieStatus('正在登录中，请稍候...');
                    break;
                case 'verification_required':
                    // 需要身份验证，显示验证截图或链接
                    updateRefreshCookieStatus(`需要${getPasswordLoginVerificationTypeLabel(data.verification_type)}，请查看弹出的验证窗口`);
                    // 使用账号密码登录的验证显示函数
                    showPasswordLoginQRCode(
                        data.screenshot_path || data.verification_url || data.qr_code_url,
                        data.screenshot_path,
                        data.verification_type
                    );
                    break;
                case 'success':
                    stopRefreshCookiePolling(sessionId);
                    const passwordLoginQRModal = document.getElementById('passwordLoginQRModal');
                    if (passwordLoginQRModal && passwordLoginQRModal.classList.contains('show')) {
                        setPasswordLoginQRModalStatus('验证已完成，正在刷新账号状态...');
                        await new Promise(resolve => setTimeout(resolve, 400));
                    }
                    closePasswordLoginQRModal();
                    toggleLoading(false);
                    showToast(`账号 ${cookieId} Cookie刷新成功！`, 'success');
                    // 隐藏表单
                    document.getElementById('refreshCookieForm').style.display = 'none';
                    // 刷新账号列表
                    loadCookies();
                    break;
                case 'failed':
                case 'cancelled':
                case 'error':
                case 'not_found':
                case 'forbidden':
                    stopRefreshCookiePolling(sessionId);
                    closePasswordLoginQRModal();
                    toggleLoading(false);
                    if (data.status === 'cancelled') {
                        showToast(data.message || '刷新Cookie已取消', 'info');
                    } else {
                        showToast(`刷新失败: ${data.message || data.error || '未知错误'}`, 'danger');
                    }
                    break;
            }
        } catch (error) {
            console.error('检查刷新状态失败:', error);
        } finally {
            if (refreshCookiePollingState.sessionId === sessionId) {
                refreshCookiePollingState.inFlight = false;
            }
        }
    };

    refreshCookieCheckInterval = setInterval(pollRefreshCookieStatus, 2000);
    pollRefreshCookieStatus();
}

// ========================= 账号密码登录相关函数 =========================

let passwordLoginCheckInterval = null;
let passwordLoginSessionId = null;
let passwordLoginPollingState = {
    sessionId: null,
    inFlight: false,
    completed: false
};
let passwordLoginQRModalEventsBound = false;
let passwordLoginQRModalState = {
    systemClosing: false,
    cancelInFlight: false
};

// 处理账号密码登录表单提交
async function handlePasswordLogin(event) {
    event.preventDefault();
    
    const accountId = document.getElementById('passwordLoginAccountId').value.trim();
    const account = document.getElementById('passwordLoginAccount').value.trim();
    const password = document.getElementById('passwordLoginPassword').value;
    const showBrowser = document.getElementById('passwordLoginShowBrowser').checked;
    
    if (!accountId || !account || !password) {
        showToast('请填写完整的登录信息', 'warning');
        return;
    }
    
    // 禁用提交按钮，显示加载状态
    const submitBtn = event.target.querySelector('button[type="submit"]');
    const originalText = submitBtn.innerHTML;
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span class="spinner-border spinner-border-sm me-2"></span>登录中...';
    
    try {
        const response = await fetch(`${apiBase}/password-login`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                account_id: accountId,
                account: account,
                password: password,
                show_browser: showBrowser
            })
        });
        
        const data = await response.json();
        
        if (response.ok && data.success && data.session_id) {
            passwordLoginSessionId = data.session_id;
            // 开始轮询检查登录状态
            startPasswordLoginCheck();
        } else {
            showToast(data.message || '登录失败，请检查账号密码是否正确', 'danger');
            submitBtn.disabled = false;
            submitBtn.innerHTML = originalText;
        }
    } catch (error) {
        console.error('账号密码登录失败:', error);
        showToast('网络错误，请重试', 'danger');
        submitBtn.disabled = false;
        submitBtn.innerHTML = originalText;
    }
}

// 开始检查账号密码登录状态
function startPasswordLoginCheck() {
    clearPasswordLoginCheck();

    passwordLoginPollingState = {
        sessionId: passwordLoginSessionId,
        inFlight: false,
        completed: false
    };

    passwordLoginCheckInterval = setInterval(checkPasswordLoginStatus, 2000); // 每2秒检查一次
    checkPasswordLoginStatus();
}

// 检查账号密码登录状态
async function checkPasswordLoginStatus() {
    if (!passwordLoginSessionId || passwordLoginPollingState.completed || passwordLoginPollingState.inFlight) return;

    const sessionId = passwordLoginSessionId;
    passwordLoginPollingState.inFlight = true;
    
    try {
        const response = await fetch(`${apiBase}/password-login/check/${sessionId}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        
        if (response.ok) {
            const data = await response.json();

            if (passwordLoginPollingState.sessionId !== sessionId || passwordLoginPollingState.completed) {
                return;
            }

            console.log('账号密码登录状态检查:', data); // 调试日志
            
            switch (data.status) {
                case 'processing':
                    // 处理中，继续等待
                    break;
                case 'verification_required':
                    // 需要身份验证，显示验证截图或链接
                    showPasswordLoginQRCode(
                        data.screenshot_path || data.verification_url || data.qr_code_url,
                        data.screenshot_path,
                        data.verification_type
                    );
                    // 继续监控（人脸认证后需要继续等待登录完成）
                    break;
                case 'success':
                    // 登录成功
                    passwordLoginPollingState.completed = true;
                    clearPasswordLoginCheck();
                    handlePasswordLoginSuccess(data);
                    break;
                case 'failed':
                    // 登录失败
                    passwordLoginPollingState.completed = true;
                    clearPasswordLoginCheck();
                    handlePasswordLoginFailure(data);
                    break;
                case 'cancelled':
                    passwordLoginPollingState.completed = true;
                    clearPasswordLoginCheck();
                    closePasswordLoginQRModal();
                    showToast(data.message || '登录已取消', 'info');
                    resetPasswordLoginForm();
                    break;
                case 'not_found':
                case 'forbidden':
                case 'error':
                    // 错误情况
                    passwordLoginPollingState.completed = true;
                    clearPasswordLoginCheck();
                    closePasswordLoginQRModal();
                    showToast(data.message || '登录检查失败', 'danger');
                    resetPasswordLoginForm();
                    break;
            }
        } else {
            // 响应不OK时也尝试解析错误消息
            try {
                const errorData = await response.json();
                passwordLoginPollingState.completed = true;
                clearPasswordLoginCheck();
                closePasswordLoginQRModal();
                showToast(errorData.message || '登录检查失败', 'danger');
                resetPasswordLoginForm();
            } catch (e) {
                passwordLoginPollingState.completed = true;
                clearPasswordLoginCheck();
                closePasswordLoginQRModal();
                showToast('登录检查失败，请重试', 'danger');
                resetPasswordLoginForm();
            }
        }
    } catch (error) {
        console.error('检查账号密码登录状态失败:', error);
        passwordLoginPollingState.completed = true;
        clearPasswordLoginCheck();
        closePasswordLoginQRModal();
        showToast('网络错误，请重试', 'danger');
        resetPasswordLoginForm();
    } finally {
        if (passwordLoginPollingState.sessionId === sessionId) {
            passwordLoginPollingState.inFlight = false;
        }
    }
}

function getPasswordLoginVerificationTypeLabel(verificationType) {
    const normalized = String(verificationType || '').trim();
    const labelMap = {
        face_verify: '人脸验证',
        sms_verify: '短信验证',
        qr_verify: '二维码验证',
        unknown: '身份验证'
    };
    return labelMap[normalized] || normalized || '身份验证';
}

async function cancelPasswordLoginSession(sessionId, flowLabel = '登录') {
    if (!sessionId || passwordLoginQRModalState.cancelInFlight) {
        return;
    }

    passwordLoginQRModalState.cancelInFlight = true;
    try {
        const response = await fetch(`${apiBase}/password-login/cancel/${sessionId}`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.success === false) {
            console.warn(`${flowLabel}取消请求返回异常:`, data);
            showToast(data.message || `已停止当前${flowLabel}轮询`, 'warning');
            return;
        }
        showToast(data.message || `${flowLabel}已取消`, 'info');
    } catch (error) {
        console.error(`取消${flowLabel}会话失败:`, error);
        showToast(`已停止当前${flowLabel}轮询，请稍后重试`, 'warning');
    } finally {
        passwordLoginQRModalState.cancelInFlight = false;
    }
}

function bindPasswordLoginQRModalEvents(modalElement) {
    if (!modalElement || passwordLoginQRModalEventsBound) {
        return;
    }

    modalElement.addEventListener('hidden.bs.modal', function () {
        if (passwordLoginQRModalState.systemClosing) {
            passwordLoginQRModalState.systemClosing = false;
            return;
        }

        if (passwordLoginPollingState.sessionId && !passwordLoginPollingState.completed) {
            const activeSessionId = passwordLoginPollingState.sessionId;
            passwordLoginPollingState.completed = true;
            passwordLoginPollingState.inFlight = false;
            resetPasswordLoginForm();
            void cancelPasswordLoginSession(activeSessionId, '登录');
            return;
        }

        if (refreshCookiePollingState.sessionId && !refreshCookiePollingState.completed) {
            const activeSessionId = refreshCookiePollingState.sessionId;
            stopRefreshCookiePolling(activeSessionId);
            refreshCookiePollingState.inFlight = false;
            toggleLoading(false);
            void cancelPasswordLoginSession(activeSessionId, '刷新Cookie');
            return;
        }

        if (manualCookieImportPollingState.sessionId && !manualCookieImportPollingState.completed) {
            manualCookieImportPollingState.completed = true;
            manualCookieImportPollingState.inFlight = false;
            resetManualCookieImportForm();
            showToast('已停止当前导入验证流程', 'info');
        }
    });

    passwordLoginQRModalEventsBound = true;
}

// 显示账号密码登录验证
function showPasswordLoginQRCode(verificationUrl, screenshotPath, verificationType) {
    // 使用现有的二维码登录模态框
    let modal = document.getElementById('passwordLoginQRModal');
    if (!modal) {
        // 如果模态框不存在，创建一个
        createPasswordLoginQRModal();
        modal = document.getElementById('passwordLoginQRModal');
    }
    bindPasswordLoginQRModalEvents(modal);
    
    // 更新模态框标题
    const modalTitle = document.getElementById('passwordLoginQRModalLabel');
    if (modalTitle) {
        modalTitle.innerHTML = '<i class="bi bi-shield-exclamation text-warning me-2"></i>闲鱼验证';
    }
    
    // 获取或创建模态框实例
    let modalInstance = bootstrap.Modal.getInstance(modal);
    if (!modalInstance) {
        modalInstance = new bootstrap.Modal(modal);
    }
    modalInstance.show();
    
    // 隐藏加载容器
    const qrContainer = document.getElementById('passwordLoginQRContainer');
    if (qrContainer) {
        qrContainer.style.display = 'none';
    }
    
    // 优先显示截图，如果没有截图则显示链接
    const screenshotImg = document.getElementById('passwordLoginScreenshotImg');
    const linkButton = document.getElementById('passwordLoginVerificationLink');
    const statusText = document.getElementById('passwordLoginQRStatusText');
    const verificationTypeLabel = getPasswordLoginVerificationTypeLabel(verificationType);
    
    if (screenshotPath) {
        // 显示截图
        if (screenshotImg) {
            screenshotImg.src = `${normalizeStaticAssetPath(screenshotPath)}?t=${new Date().getTime()}`;
            screenshotImg.style.display = 'block';
            screenshotImg.alt = `${verificationTypeLabel}截图`;
        }
        
        // 隐藏链接按钮
        if (linkButton) {
            linkButton.style.display = 'none';
        }
        
        // 更新状态文本
        if (statusText) {
            statusText.textContent = verificationTypeLabel === '二维码验证'
                ? '需要闲鱼二维码验证，请使用手机闲鱼APP扫描下方二维码完成验证'
                : `需要闲鱼${verificationTypeLabel}，请根据下方验证信息在手机闲鱼APP中完成操作`;
        }
    } else if (verificationUrl) {
        // 隐藏截图
        if (screenshotImg) {
            screenshotImg.style.display = 'none';
        }
        
        // 显示链接按钮
        if (linkButton) {
            linkButton.href = verificationUrl;
            linkButton.style.display = 'inline-block';
        }
        
        // 更新状态文本
        if (statusText) {
            statusText.textContent = `服务端已保持原始会话；如${verificationTypeLabel}入口暂未显示，可使用下方兜底入口`;
        }
    } else {
        // 都没有，显示等待
        if (screenshotImg) {
            screenshotImg.style.display = 'none';
        }
        if (linkButton) {
            linkButton.style.display = 'none';
        }
        if (statusText) {
            statusText.textContent = `需要闲鱼${verificationTypeLabel}，请等待验证信息...`;
        }
    }
}

function closePasswordLoginQRModal() {
    const modalElement = document.getElementById('passwordLoginQRModal');
    if (!modalElement) {
        passwordLoginQRModalState.systemClosing = false;
        return;
    }

    const modalTitle = document.getElementById('passwordLoginQRModalLabel');
    if (modalTitle) {
        modalTitle.innerHTML = '<i class="bi bi-shield-exclamation text-warning me-2"></i>闲鱼验证';
    }

    const screenshotImg = document.getElementById('passwordLoginScreenshotImg');
    if (screenshotImg) {
        screenshotImg.src = '';
        screenshotImg.style.display = 'none';
    }

    const linkButton = document.getElementById('passwordLoginVerificationLink');
    if (linkButton) {
        linkButton.href = '#';
        linkButton.style.display = 'none';
    }

    const statusText = document.getElementById('passwordLoginQRStatusText');
    if (statusText) {
        statusText.textContent = '需要闲鱼身份验证，请等待验证信息...';
    }

    const modalInstance = bootstrap.Modal.getInstance(modalElement);
    if (modalInstance && modalElement.classList.contains('show')) {
        passwordLoginQRModalState.systemClosing = true;
        modalInstance.hide();
    } else {
        passwordLoginQRModalState.systemClosing = false;
    }
}

function setPasswordLoginQRModalStatus(message) {
    const statusText = document.getElementById('passwordLoginQRStatusText');
    if (statusText) {
        statusText.textContent = message;
    }
}

// 创建账号密码登录二维码模态框
function createPasswordLoginQRModal() {
    const modalHtml = `
        <div class="modal fade" id="passwordLoginQRModal" tabindex="-1" aria-labelledby="passwordLoginQRModalLabel" aria-hidden="true">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title" id="passwordLoginQRModalLabel">
                            <i class="bi bi-shield-exclamation text-warning me-2"></i>闲鱼验证
                        </h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body text-center">
                        <p id="passwordLoginQRStatusText" class="text-muted mb-3">
                            需要闲鱼身份验证，请等待验证信息...
                        </p>
                        
                        <!-- 截图显示区域 -->
                        <div id="passwordLoginScreenshotContainer" class="mb-3 d-flex justify-content-center">
                            <img id="passwordLoginScreenshotImg" src="" alt="验证截图" 
                                 class="img-fluid" style="display: none; max-width: 400px; height: auto; border: 2px solid #ddd; border-radius: 8px;">
                        </div>
                        
                        <!-- 验证链接按钮（回退方案） -->
                        <div id="passwordLoginLinkContainer" class="mt-4">
                            <a id="passwordLoginVerificationLink" href="#" target="_blank" 
                               class="btn btn-warning btn-lg" style="display: none;">
                                <i class="bi bi-shield-check me-2"></i>
                                打开兜底验证页面
                            </a>
                        </div>
                        
                        <div class="alert alert-info mt-3">
                            <i class="bi bi-info-circle me-2"></i>
                            <small>验证完成后，系统将自动检测并继续登录流程</small>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `;
    
    document.body.insertAdjacentHTML('beforeend', modalHtml);
    bindPasswordLoginQRModalEvents(document.getElementById('passwordLoginQRModal'));
}

// 处理账号密码登录成功
function handlePasswordLoginSuccess(data) {
    // 关闭二维码模态框
    closePasswordLoginQRModal();
    
    showToast(`账号 ${data.account_id} 登录成功！`, 'success');
    
    // 隐藏表单
    togglePasswordLogin();
    
    // 刷新账号列表
    loadCookies();
    
    // 重置表单
    resetPasswordLoginForm();
}

// 处理账号密码登录失败
function handlePasswordLoginFailure(data) {
    console.log('账号密码登录失败，错误数据:', data); // 调试日志
    
    // 关闭二维码模态框
    closePasswordLoginQRModal();
    
    // 优先使用 message，如果没有则使用 error 字段
    const errorMessage = data.message || data.error || '登录失败，请检查账号密码是否正确';
    console.log('显示错误消息:', errorMessage); // 调试日志
    
    showToast(errorMessage, 'danger');  // 使用 'danger' 而不是 'error'，因为 Bootstrap 使用 'danger' 作为错误类型
    
    // 重置表单
    resetPasswordLoginForm();
}

// 清理账号密码登录检查
function clearPasswordLoginCheck() {
    if (passwordLoginCheckInterval) {
        clearInterval(passwordLoginCheckInterval);
        passwordLoginCheckInterval = null;
    }
}

// 重置账号密码登录表单
function resetPasswordLoginForm() {
    passwordLoginSessionId = null;
    clearPasswordLoginCheck();
    passwordLoginPollingState = {
        sessionId: null,
        inFlight: false,
        completed: false
    };
    
    const submitBtn = document.querySelector('#passwordLoginFormElement button[type="submit"]');
    if (submitBtn) {
        submitBtn.disabled = false;
        submitBtn.innerHTML = '<i class="bi bi-box-arrow-in-right me-1"></i>开始登录';
    }
}

// ========================= 扫码登录相关函数 =========================

let qrCodeCheckInterval = null;
let qrCodeSessionId = null;
let qrCodeModalEventsBound = false;
let qrLoginMode = 'standard'; // 'standard' = 原 Playwright；'lite' = 纯 HTTP (cv-cat 风格)
let qrCodeVerificationState = {
    renderKey: '',
    toastShown: false,
    inFlight: false,
    completed: false,
    activeSessionId: null
};

function getQRLoginEndpoints() {
    if (qrLoginMode === 'lite') {
        return {
            generate: `${apiBase}/qr-login-lite/generate`,
            checkPrefix: `${apiBase}/qr-login-lite/check/`,
        };
    }
    return {
        generate: `${apiBase}/qr-login/generate`,
        checkPrefix: `${apiBase}/qr-login/check/`,
    };
}

function applyQRLoginModeChrome() {
    const titleEl = document.getElementById('qrLoginModalTitleText');
    if (titleEl) {
        titleEl.textContent = qrLoginMode === 'lite' ? '轻量扫码登录闲鱼账号' : '扫码登录闲鱼账号';
    }
}

function normalizeStaticAssetPath(path) {
    if (!path) {
        return '';
    }
    if (path.startsWith('http://') || path.startsWith('https://') || path.startsWith('data:')) {
        return path;
    }
    return path.startsWith('/') ? path : `/${path}`;
}

function resetQRCodeVerificationState() {
    qrCodeVerificationState.renderKey = '';
    qrCodeVerificationState.toastShown = false;
    qrCodeVerificationState.inFlight = false;
    qrCodeVerificationState.completed = false;
    qrCodeVerificationState.activeSessionId = null;
}

function closeQRCodeLoginModal(delay = 3000) {
    setTimeout(() => {
        const modalElement = document.getElementById('qrCodeLoginModal');
        if (!modalElement) {
            loadCookies();
            return;
        }

        const modal = bootstrap.Modal.getInstance(modalElement) || new bootstrap.Modal(modalElement);
        modal.hide();
        loadCookies();
    }, delay);
}

function initializeQRCodeLoginModal() {
    const modalElement = document.getElementById('qrCodeLoginModal');
    if (!modalElement || qrCodeModalEventsBound) {
        return modalElement;
    }

    modalElement.addEventListener('shown.bs.modal', function () {
        generateQRCode();
    });

    modalElement.addEventListener('hidden.bs.modal', function () {
        clearQRCodeCheck();
    });

    qrCodeModalEventsBound = true;
    return modalElement;
}

// 显示扫码登录模态框
function showQRCodeLogin(mode = 'standard') {
    qrLoginMode = mode === 'lite' ? 'lite' : 'standard';
    applyQRLoginModeChrome();
    const modalElement = initializeQRCodeLoginModal();
    if (!modalElement) {
        showToast('扫码登录弹窗未找到，请刷新页面重试', 'danger');
        return;
    }

    const modal = bootstrap.Modal.getInstance(modalElement) || new bootstrap.Modal(modalElement);
    modal.show();
}

// 刷新二维码（兼容旧函数名）
async function refreshQRCode() {
    await generateQRCode();
}

// 生成二维码
async function generateQRCode() {
    try {
    resetQRCodeVerificationState();
    showQRCodeLoading();

    const endpoints = getQRLoginEndpoints();
    const response = await fetch(endpoints.generate, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        }
    });

    if (response.ok) {
        const data = await response.json();
        if (data.success) {
        qrCodeSessionId = data.session_id;
        qrCodeVerificationState.activeSessionId = data.session_id;
        showQRCodeImage(data.qr_code_url);
        startQRCodeCheck();
        } else {
        showQRCodeError(data.message || '生成二维码失败');
        }
    } else {
        showQRCodeError('生成二维码失败');
    }
    } catch (error) {
    console.error('生成二维码失败:', error);
    showQRCodeError('网络错误，请重试');
    }
}

// 显示二维码加载状态
function showQRCodeLoading() {
    resetQRCodeVerificationState();
    document.getElementById('qrCodeContainer').style.display = 'block';
    document.getElementById('qrCodeImage').style.display = 'none';
    document.getElementById('statusText').textContent = '正在生成二维码，请耐心等待...';
    document.getElementById('statusSpinner').style.display = 'none';

    // 隐藏验证容器
    const verificationContainer = document.getElementById('verificationContainer');
    if (verificationContainer) {
    verificationContainer.style.display = 'none';
    }
}

// 显示二维码图片
function showQRCodeImage(qrCodeUrl) {
    document.getElementById('qrCodeContainer').style.display = 'none';
    document.getElementById('qrCodeImage').style.display = 'block';
    document.getElementById('qrCodeImg').src = qrCodeUrl;
    document.getElementById('statusText').textContent = '等待扫码...';
    document.getElementById('statusSpinner').style.display = 'none';
}

// 显示二维码错误
function showQRCodeError(message) {
    document.getElementById('qrCodeContainer').innerHTML = `
    <div class="text-danger">
        <i class="bi bi-exclamation-triangle fs-1 mb-3"></i>
        <p>${message}</p>
    </div>
    `;
    document.getElementById('qrCodeImage').style.display = 'none';
    document.getElementById('statusText').textContent = '生成失败';
    document.getElementById('statusSpinner').style.display = 'none';
}

// 开始检查二维码状态
function startQRCodeCheck() {
    if (qrCodeCheckInterval) {
    clearInterval(qrCodeCheckInterval);
    }

    document.getElementById('statusSpinner').style.display = 'inline-block';
    document.getElementById('statusText').textContent = '等待扫码...';

    qrCodeCheckInterval = setInterval(checkQRCodeStatus, 2000); // 每2秒检查一次
}

// 检查二维码状态
async function checkQRCodeStatus() {
    if (!qrCodeSessionId || qrCodeVerificationState.inFlight || qrCodeVerificationState.completed) return;

    const requestSessionId = qrCodeSessionId;
    qrCodeVerificationState.inFlight = true;

    try {
    const endpoints = getQRLoginEndpoints();
    const response = await fetch(`${endpoints.checkPrefix}${requestSessionId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (requestSessionId !== qrCodeVerificationState.activeSessionId || qrCodeVerificationState.completed) {
        return;
    }

    if (response.ok) {
        const data = await response.json();

        if (requestSessionId !== qrCodeVerificationState.activeSessionId || qrCodeVerificationState.completed) {
        return;
        }

        switch (data.status) {
        case 'waiting':
            document.getElementById('statusText').textContent = '等待扫码...';
            break;
        case 'scanned':
            document.getElementById('statusText').textContent = '已扫码，请在手机上确认...';
            break;
        case 'confirmed':
            document.getElementById('statusText').textContent = '已确认，正在获取Cookie...';
            break;
        case 'success':
            qrCodeVerificationState.completed = true;
            document.getElementById('statusText').textContent = '登录成功！';
            document.getElementById('statusSpinner').style.display = 'none';
            clearQRCodeCheck();
            handleQRCodeSuccess(data);
            break;
        case 'error':
            qrCodeVerificationState.completed = true;
            document.getElementById('statusText').textContent = '登录失败';
            document.getElementById('statusSpinner').style.display = 'none';
            clearQRCodeCheck();
            showToast(data.message || '扫码登录失败', 'danger');
            break;
        case 'expired':
            document.getElementById('statusText').textContent = '二维码已过期';
            document.getElementById('statusSpinner').style.display = 'none';
            clearQRCodeCheck();
            showQRCodeError('二维码已过期，请刷新重试');
            break;
        case 'cancelled':
            document.getElementById('statusText').textContent = '用户取消登录';
            document.getElementById('statusSpinner').style.display = 'none';
            clearQRCodeCheck();
            break;
        case 'verification_required':
            document.getElementById('statusText').textContent = '需要闲鱼验证，系统正在等待验证完成...';
            document.getElementById('statusSpinner').style.display = 'inline-block';
            showVerificationRequired(data);
            break;
        case 'processing':
            document.getElementById('statusText').textContent = '正在处理中...';
            // 继续轮询，不清理检查
            break;
        case 'already_processed':
            qrCodeVerificationState.completed = true;
            document.getElementById('statusText').textContent = '登录已完成';
            document.getElementById('statusSpinner').style.display = 'none';
            clearQRCodeCheck();
            handleQRCodeSuccess(data);
            break;
        }
    }
    } catch (error) {
    console.error('检查二维码状态失败:', error);
    } finally {
    qrCodeVerificationState.inFlight = false;
    }
}

function escapeHtml(text) {
    return String(text ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// 显示需要验证的提示
function showVerificationRequired(data) {
    const screenshotPath = data.screenshot_path || '';
    const verificationUrl = data.verification_url || '';
    const endedElsewhere = !!data.verification_ended_elsewhere;
    const serverMessage = data.message || '';
    // 结构级 renderKey：不含动态 message，避免轮询时整页重绘清空用户已粘贴内容
    const structureKey = `${screenshotPath}|${verificationUrl}|${endedElsewhere ? '1' : '0'}`;
    const existingUrlInput = document.getElementById('qrUserCallbackUrlInput');
    const existingCookieInput = document.getElementById('qrUserCookieInput');
    const preservedUrlText = existingUrlInput ? existingUrlInput.value : '';
    const preservedCookieText = existingCookieInput ? existingCookieInput.value : '';
    const preservedHint = (document.getElementById('qrUserHandoffHint') || {}).textContent || '';
    const sameStructure = qrCodeVerificationState.renderKey === structureKey && structureKey;

    // 隐藏二维码区域
    document.getElementById('qrCodeContainer').style.display = 'none';
    document.getElementById('qrCodeImage').style.display = 'none';

    // 创建验证提示容器
    let verificationContainer = document.getElementById('verificationContainer');
    if (!verificationContainer) {
        verificationContainer = document.createElement('div');
        verificationContainer.id = 'verificationContainer';
        document.querySelector('#qrCodeLoginModal .modal-body').appendChild(verificationContainer);
    }

    if (sameStructure) {
        // 只更新提示文案，不重建表单
        const msgEl = verificationContainer.querySelector('[data-role="verification-message"]');
        if (msgEl && serverMessage) {
            msgEl.textContent = serverMessage;
        }
        verificationContainer.style.display = 'block';
        return;
    }

    qrCodeVerificationState.renderKey = structureKey;
    const safeMessage = escapeHtml(
        serverMessage || '检测到账号存在风控，系统已在服务端保持原始会话并等待验证完成'
    );
    const safeVerificationUrl = escapeHtml(verificationUrl);
    const safeScreenshotSrc = escapeHtml(
        screenshotPath ? `${normalizeStaticAssetPath(screenshotPath)}?t=${Date.now()}` : ''
    );

    const userHandoffPanel = `
        <div class="mt-4 text-start border rounded p-3 bg-light">
          <h6 class="mb-2">
            <i class="bi bi-link-45deg me-1"></i>
            已在手机/浏览器完成验证？把网址给我即可
          </h6>
          <p class="small text-muted mb-2">
            验证成功后浏览器地址栏或跳转页上的链接（goofish/淘宝相关）粘贴到下方。
            项目会在服务端会话里打开该 URL 并自动拿 Cookie，不必再手抠 Cookie。
          </p>
          <textarea id="qrUserCallbackUrlInput" class="form-control font-monospace mb-2" rows="2"
            placeholder="https://passport.goofish.com/... 或验证成功后的跳转链接"></textarea>
          <div class="d-flex gap-2 align-items-center mb-3">
            <button type="button" class="btn btn-primary btn-sm" id="qrSubmitUserUrlBtn">
              <i class="bi bi-check2-circle me-1"></i>提交回调网址
            </button>
            <small id="qrUserHandoffHint" class="text-muted"></small>
          </div>
          <details class="small">
            <summary class="text-muted" style="cursor:pointer">备用：直接粘贴完整 Cookie</summary>
            <p class="text-muted mt-2 mb-2">
              需含 <code>unb</code> 与 <code>cookie2</code>/<code>sgcookie</code> 等完整登录 Cookie。
            </p>
            <textarea id="qrUserCookieInput" class="form-control font-monospace mb-2" rows="3"
              placeholder="unb=...; cookie2=...; sgcookie=...; _tb_token_=... 或 JSON"></textarea>
            <button type="button" class="btn btn-outline-secondary btn-sm" id="qrSubmitUserCookieBtn">
              <i class="bi bi-key me-1"></i>提交成功侧 Cookie
            </button>
          </details>
        </div>
    `;

    let verificationHtml = `
        <div class="text-center">
        <div class="mb-4">
            <i class="bi bi-shield-exclamation text-warning" style="font-size: 4rem;"></i>
        </div>
        <h5 class="text-warning mb-3">账号需要闲鱼验证</h5>
        <div class="alert alert-warning border-0 mb-4">
            <i class="bi bi-info-circle me-2"></i>
            <strong data-role="verification-message">${safeMessage}</strong>
        </div>
        <div class="alert alert-info border-0">
            <i class="bi bi-lightbulb me-2"></i>
            <small>
            <strong>验证步骤：</strong><br>
            1. 优先用手机闲鱼 APP 扫描服务端截图二维码并完成验证<br>
            2. 保持当前弹窗打开，系统会自动继续登录<br>
            3. 若你已在其它浏览器完成验证：粘贴成功后的回调网址（推荐）
            </small>
        </div>
        ${userHandoffPanel}
        </div>
    `;

    if (screenshotPath) {
    verificationHtml = `
        <div class="text-center">
        <div class="mb-4">
            <i class="bi bi-shield-exclamation text-warning" style="font-size: 4rem;"></i>
        </div>
        <h5 class="text-warning mb-3">账号需要闲鱼验证</h5>
        <div class="alert alert-warning border-0 mb-4">
            <i class="bi bi-info-circle me-2"></i>
            <strong data-role="verification-message">${escapeHtml(serverMessage || '检测到账号存在风控，系统已在服务端保持原始会话并生成验证二维码')}</strong>
        </div>
        <div class="mb-4">
            <p class="text-muted mb-3">优先使用手机闲鱼 APP 扫描下方<strong>服务端</strong>二维码完成验证：</p>
            <img src="${safeScreenshotSrc}" alt="闲鱼验证二维码" class="img-fluid rounded border" style="max-width: 360px; width: 100%; height: auto;">
        </div>
        <div class="alert alert-info border-0">
            <i class="bi bi-lightbulb me-2"></i>
            <small>
            <strong>验证步骤：</strong><br>
            1. 扫描上方二维码并完成验证（人脸落在服务端会话，可自动回调）<br>
            2. 保持当前弹窗打开，系统会自动继续登录<br>
            3. 若你已在其它浏览器完成验证：下方粘贴成功后的回调网址
            </small>
        </div>
        ${userHandoffPanel}
        </div>
    `;
    } else if (verificationUrl) {
    verificationHtml = `
        <div class="text-center">
        <div class="mb-4">
            <i class="bi bi-shield-exclamation text-warning" style="font-size: 4rem;"></i>
        </div>
        <h5 class="text-warning mb-3">账号需要闲鱼验证</h5>
        <div class="alert alert-warning border-0 mb-4">
            <i class="bi bi-info-circle me-2"></i>
            <strong data-role="verification-message">${escapeHtml(serverMessage || '系统正在准备验证二维码，当前先保留一个兜底链接')}</strong>
        </div>
        <div class="mb-4">
            <p class="text-muted mb-3">二维码通常会自动出现。若你用兜底链接在本机完成了验证，把成功后的网址贴回来：</p>
            <a href="${safeVerificationUrl}" target="_blank" rel="noopener noreferrer" class="btn btn-outline-warning">
            <i class="bi bi-box-arrow-up-right me-2"></i>
            打开兜底验证页面
            </a>
        </div>
        <div class="alert alert-info border-0">
            <i class="bi bi-lightbulb me-2"></i>
            <small>
            兜底页在你浏览器完成验证后，把地址栏/跳转链接贴到下方即可，以你的成功为准。
            </small>
        </div>
        ${userHandoffPanel}
        </div>
    `;
    }

    verificationContainer.innerHTML = verificationHtml;
    verificationContainer.style.display = 'block';

    const urlInputEl = document.getElementById('qrUserCallbackUrlInput');
    if (urlInputEl && preservedUrlText) {
        urlInputEl.value = preservedUrlText;
    }
    const inputEl = document.getElementById('qrUserCookieInput');
    if (inputEl && preservedCookieText) {
        inputEl.value = preservedCookieText;
    }
    const hintEl = document.getElementById('qrUserHandoffHint');
    if (hintEl && preservedHint) {
        hintEl.textContent = preservedHint;
    }

    const submitUrlBtn = document.getElementById('qrSubmitUserUrlBtn');
    if (submitUrlBtn) {
        submitUrlBtn.addEventListener('click', submitQrUserCallbackUrl);
    }
    const submitBtn = document.getElementById('qrSubmitUserCookieBtn');
    if (submitBtn) {
        submitBtn.addEventListener('click', submitQrUserCookies);
    }

    // 显示Toast提示
    if (!qrCodeVerificationState.toastShown) {
    const toastMsg = endedElsewhere
        ? '服务端验证页已结束；请粘贴成功后的回调网址（推荐）'
        : '账号需要闲鱼验证：优先扫服务端二维码，或粘贴回调网址';
    showToast(toastMsg, 'warning');
    qrCodeVerificationState.toastShown = true;
    }
}

async function submitQrUserCallbackUrl() {
    const sessionId = qrCodeSessionId || qrCodeVerificationState.activeSessionId;
    const input = document.getElementById('qrUserCallbackUrlInput');
    const hint = document.getElementById('qrUserHandoffHint');
    const btn = document.getElementById('qrSubmitUserUrlBtn');
    const urlText = (input && input.value || '').trim();
    if (!sessionId) {
        showToast('会话已失效，请重新发起扫码登录', 'danger');
        return;
    }
    if (!urlText) {
        showToast('请先粘贴验证成功后的回调网址', 'warning');
        return;
    }
    if (btn && btn.dataset.submitting === '1') {
        return;
    }

    if (btn) {
        btn.dataset.submitting = '1';
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>换取中...';
    }
    if (hint) hint.textContent = '正在用回调网址换取登录态...';

    try {
        const response = await fetch(`${apiBase}/qr-login/submit-url/${sessionId}`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ url: urlText }),
        });
        const result = await response.json().catch(() => ({}));

        if (!response.ok && !result.message) {
            const msg = `提交失败（HTTP ${response.status}）`;
            if (hint) hint.textContent = msg;
            showToast(msg, 'danger');
            return;
        }

        if (!result.success) {
            const msg = result.message || '提交失败';
            if (hint) hint.textContent = msg;
            showToast(msg, 'danger');
            return;
        }

        if (hint) hint.textContent = result.message || '已接收，等待账号落地...';
        showToast(result.message || '已使用回调网址完成登录', 'success');
        document.getElementById('statusText').textContent = '已收到回调网址，正在完成登录...';
        document.getElementById('statusSpinner').style.display = 'inline-block';

        if (result.account_info) {
            qrCodeVerificationState.completed = true;
            clearQRCodeCheck();
            handleQRCodeSuccess(result);
            return;
        }

        if (typeof checkQRCodeStatus === 'function') {
            checkQRCodeStatus();
        }
    } catch (err) {
        console.error('提交回调网址失败:', err);
        if (hint) hint.textContent = '网络错误，请重试';
        showToast('提交回调网址失败: ' + (err.message || err), 'danger');
    } finally {
        if (btn) {
            btn.dataset.submitting = '0';
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-check2-circle me-1"></i>提交回调网址';
        }
    }
}

async function submitQrUserCookies() {
    const sessionId = qrCodeSessionId || qrCodeVerificationState.activeSessionId;
    const input = document.getElementById('qrUserCookieInput');
    const hint = document.getElementById('qrUserHandoffHint');
    const btn = document.getElementById('qrSubmitUserCookieBtn');
    const cookieText = (input && input.value || '').trim();
    if (!sessionId) {
        showToast('会话已失效，请重新发起扫码登录', 'danger');
        return;
    }
    if (!cookieText) {
        showToast('请先粘贴成功侧浏览器 Cookie', 'warning');
        return;
    }
    if (btn && btn.dataset.submitting === '1') {
        return;
    }

    if (btn) {
        btn.dataset.submitting = '1';
        btn.disabled = true;
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>提交中...';
    }
    if (hint) hint.textContent = '正在以用户成功为准写入...';

    try {
        const response = await fetch(`${apiBase}/qr-login/submit-cookies/${sessionId}`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ cookies: cookieText }),
        });
        const result = await response.json().catch(() => ({}));

        if (!response.ok && !result.message) {
            const msg = `提交失败（HTTP ${response.status}）`;
            if (hint) hint.textContent = msg;
            showToast(msg, 'danger');
            return;
        }

        if (!result.success) {
            const msg = result.message || '提交失败';
            if (hint) hint.textContent = msg;
            showToast(msg, 'danger');
            return;
        }

        if (hint) hint.textContent = result.message || '已接收，等待账号落地...';
        showToast(result.message || '已使用你的成功 Cookie，继续登录中', 'success');
        document.getElementById('statusText').textContent = '已收到用户侧Cookie，正在完成登录...';
        document.getElementById('statusSpinner').style.display = 'inline-block';

        // 若服务端已同步返回 account_info，直接走成功 UI
        if (result.account_info) {
            qrCodeVerificationState.completed = true;
            clearQRCodeCheck();
            handleQRCodeSuccess(result);
            return;
        }

        // 否则继续原有轮询，复用 check 成功收口
        if (typeof checkQRCodeStatus === 'function') {
            checkQRCodeStatus();
        }
    } catch (err) {
        console.error('提交用户侧Cookie失败:', err);
        if (hint) hint.textContent = '网络错误，请重试';
        showToast('提交 Cookie 失败: ' + (err.message || err), 'danger');
    } finally {
        if (btn) {
            btn.dataset.submitting = '0';
            btn.disabled = false;
            btn.innerHTML = '<i class="bi bi-key me-1"></i>提交成功侧 Cookie';
        }
    }
}

// 处理扫码成功
function handleQRCodeSuccess(data) {
    if (data.account_info) {
    const {
        account_id,
        is_new_account,
        real_cookie_refreshed,
        fallback_reason,
        cookie_length,
        token_prewarmed,
        task_restarted,
        warning_message
    } = data.account_info;

    // 构建成功消息
    let successMessage = '';
    if (is_new_account) {
        successMessage = `新账号添加成功！账号ID: ${account_id}`;
    } else {
        successMessage = `账号Cookie已更新！账号ID: ${account_id}`;
    }

    // 添加cookie长度信息
    if (cookie_length) {
        successMessage += `\nCookie长度: ${cookie_length}`;
    }

    // 添加真实cookie获取状态信息
    if (real_cookie_refreshed === true) {
        if (task_restarted === false) {
            successMessage += '\n✅ 真实Cookie已获取';
            if (warning_message) {
                successMessage += `\n⚠️ ${warning_message}`;
            }
            document.getElementById('statusText').textContent = '登录完成，但账号任务尚未切换';
            showToast(successMessage, 'warning');
        } else if (token_prewarmed === false) {
            successMessage += '\n✅ 真实Cookie获取并保存成功';
            if (warning_message) {
                successMessage += `\n⚠️ ${warning_message}`;
            }
            document.getElementById('statusText').textContent = '登录完成，账号任务已切换，Token将在后台继续初始化';
            showToast(successMessage, 'warning');
        } else {
            successMessage += '\n✅ 真实Cookie获取并保存成功';
            document.getElementById('statusText').textContent = '登录成功！真实Cookie已获取并保存';
            showToast(successMessage, 'success');
        }
    } else if (real_cookie_refreshed === false) {
        successMessage += '\n⚠️ 真实Cookie获取失败，已保存原始扫码Cookie';
        if (fallback_reason) {
            successMessage += `\n原因: ${fallback_reason}`;
        }
        document.getElementById('statusText').textContent = '登录成功，但使用原始Cookie';
        showToast(successMessage, 'warning');
    } else {
        // 兼容旧版本，没有真实cookie刷新信息
        document.getElementById('statusText').textContent = '登录成功！';
        showToast(successMessage, 'success');
    }

    closeQRCodeLoginModal(3000);
    return;
    }

    document.getElementById('statusText').textContent = '登录成功！';
    showToast(data.message || '扫码登录已完成，账号信息已同步', 'success');
    closeQRCodeLoginModal(1500);
}

// 清理二维码检查
function clearQRCodeCheck() {
    if (qrCodeCheckInterval) {
    clearInterval(qrCodeCheckInterval);
    qrCodeCheckInterval = null;
    }
    qrCodeSessionId = null;
    resetQRCodeVerificationState();
}

// 刷新二维码
function refreshQRCode() {
    clearQRCodeCheck();
    generateQRCode();
}

// ==================== 图片关键词管理功能 ====================

// 显示添加图片关键词模态框
function showAddImageKeywordModal() {
    if (!currentCookieId) {
        showToast('请先选择账号', 'warning');
        return;
    }

    // 加载商品列表到图片关键词模态框
    loadItemsListForImageKeyword();

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('addImageKeywordModal'));
    modal.show();

    // 清空表单
    document.getElementById('imageKeyword').value = '';
    const imageSelectElement = document.getElementById('imageItemIdSelect');
    if (imageSelectElement) {
        // 清除所有选中项
        Array.from(imageSelectElement.options).forEach(opt => opt.selected = false);
    }
    document.getElementById('imageFile').value = '';
    hideImagePreview();
}

// 为图片关键词模态框加载商品列表
async function loadItemsListForImageKeyword() {
    try {
        const response = await fetch(`${apiBase}/items/${currentCookieId}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            const items = data.items || [];

            // 更新商品选择下拉框
            const selectElement = document.getElementById('imageItemIdSelect');
            if (selectElement) {
                // 清空现有选项（保留第一个默认选项）
                selectElement.innerHTML = '<option value="">选择商品或留空表示通用关键词</option>';

                // 添加商品选项
                items.forEach(item => {
                    const option = document.createElement('option');
                    option.value = item.item_id;
                    option.textContent = `${item.item_id} - ${item.item_title}`;
                    selectElement.appendChild(option);
                });
            }

            console.log(`为图片关键词加载了 ${items.length} 个商品到选择列表`);
        } else {
            console.warn('加载商品列表失败:', response.status);
        }
    } catch (error) {
        console.error('加载商品列表时发生错误:', error);
    }
}

// 处理图片文件选择事件监听器
function initImageKeywordEventListeners() {
    const imageFileInput = document.getElementById('imageFile');
    if (imageFileInput && !imageFileInput.hasEventListener) {
        imageFileInput.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (file) {
                // 验证文件类型
                if (!file.type.startsWith('image/')) {
                    showToast('请选择图片文件', 'warning');
                    e.target.value = '';
                    hideImagePreview();
                    return;
                }

                // 验证文件大小（5MB）
                if (file.size > 5 * 1024 * 1024) {
                    showToast('❌ 图片文件大小不能超过 5MB，当前文件大小：' + (file.size / 1024 / 1024).toFixed(1) + 'MB', 'warning');
                    e.target.value = '';
                    hideImagePreview();
                    return;
                }

                // 验证图片尺寸
                validateImageDimensions(file, e.target);
            } else {
                hideImagePreview();
            }
        });
        imageFileInput.hasEventListener = true;
    }
}

// 验证图片尺寸
function validateImageDimensions(file, inputElement) {
    const img = new Image();
    const url = URL.createObjectURL(file);

    img.onload = function() {
        const width = this.naturalWidth;
        const height = this.naturalHeight;

        // 释放对象URL
        URL.revokeObjectURL(url);

        // 检查图片尺寸
        const maxDimension = 4096;
        const maxPixels = 8 * 1024 * 1024; // 8M像素
        const totalPixels = width * height;

        if (width > maxDimension || height > maxDimension) {
            showToast(`❌ 图片尺寸过大：${width}x${height}，最大允许：${maxDimension}x${maxDimension}像素`, 'warning');
            inputElement.value = '';
            hideImagePreview();
            return;
        }

        if (totalPixels > maxPixels) {
            showToast(`❌ 图片像素总数过大：${(totalPixels / 1024 / 1024).toFixed(1)}M像素，最大允许：8M像素`, 'warning');
            inputElement.value = '';
            hideImagePreview();
            return;
        }

        // 尺寸检查通过，显示预览和提示信息
        showImagePreview(file);

        // 如果图片较大，提示会被压缩
        if (width > 2048 || height > 2048) {
            showToast(`ℹ️ 图片尺寸较大（${width}x${height}），上传时将自动压缩以优化性能`, 'info');
        } else {
            showToast(`✅ 图片尺寸合适（${width}x${height}），可以上传`, 'success');
        }
    };

    img.onerror = function() {
        URL.revokeObjectURL(url);
        showToast('❌ 无法读取图片文件，请选择有效的图片', 'warning');
        inputElement.value = '';
        hideImagePreview();
    };

    img.src = url;
}

// 显示图片预览
function showImagePreview(file) {
    const reader = new FileReader();
    reader.onload = function(e) {
        const previewContainer = document.getElementById('imagePreview');
        const previewImg = document.getElementById('previewImg');

        previewImg.src = e.target.result;
        previewContainer.style.display = 'block';
    };
    reader.readAsDataURL(file);
}

// 隐藏图片预览
function hideImagePreview() {
    const previewContainer = document.getElementById('imagePreview');
    if (previewContainer) {
        previewContainer.style.display = 'none';
    }
}

// 添加图片关键词
async function addImageKeyword() {
    const keywordInput = document.getElementById('imageKeyword').value.trim();
    const selectElement = document.getElementById('imageItemIdSelect');
    const selectedOptions = Array.from(selectElement.selectedOptions);
    const fileInput = document.getElementById('imageFile');
    const file = fileInput.files[0];

    if (!keywordInput) {
        showToast('请填写关键词', 'warning');
        return;
    }

    if (!file) {
        showToast('请选择图片文件', 'warning');
        return;
    }

    // 解析多个关键词（支持竖线、换行符分隔）
    const keywords = keywordInput
        .split(/[\|\n]/)
        .map(k => k.trim())
        .filter(k => k.length > 0);
    
    if (keywords.length === 0) {
        showToast('请填写有效的关键词', 'warning');
        return;
    }

    // 获取选中的商品ID列表
    let itemIds = selectedOptions
        .map(opt => opt.value)
        .filter(id => id !== ''); // 过滤掉空值（通用关键词选项）
    
    // 如果没有选中任何商品，或者选中了空值，则作为通用关键词
    if (itemIds.length === 0) {
        itemIds = [''];
    }

    if (!currentCookieId) {
        showToast('请先选择账号', 'warning');
        return;
    }

    try {
        toggleLoading(true);

        // 检查重复关键词
        const allKeywords = keywordsData[currentCookieId] || [];
        const duplicates = [];
        for (const keyword of keywords) {
            for (const itemId of itemIds) {
                const existingKeyword = allKeywords.find(item =>
                    item.keyword === keyword &&
                    (item.item_id || '') === (itemId || '')
                );
                if (existingKeyword) {
                    const itemIdText = itemId ? `（商品ID: ${itemId}）` : '（通用关键词）';
                    duplicates.push(`"${keyword}" ${itemIdText}`);
                }
            }
        }

        if (duplicates.length > 0) {
            showToast(`以下关键词已存在：\n${duplicates.join('\n')}\n请修改后重试`, 'warning');
            toggleLoading(false);
            return;
        }

        const totalCount = keywords.length * itemIds.length;

        // 第一步：先上传一次图片获取URL
        const formData = new FormData();
        formData.append('image', file);

        const uploadResponse = await fetch(`${apiBase}/upload-image`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            },
            body: formData
        });

        if (!uploadResponse.ok) {
            const errorData = await uploadResponse.json().catch(() => ({}));
            showToast(`❌ 图片上传失败: ${errorData.detail || '请检查后重试'}`, 'danger');
            toggleLoading(false);
            return;
        }

        const uploadResult = await uploadResponse.json();
        const imageUrl = uploadResult.image_url;

        if (!imageUrl) {
            showToast('❌ 图片上传失败：未获取到图片URL', 'danger');
            toggleLoading(false);
            return;
        }

        // 第二步：使用批量API添加所有关键词
        const batchResponse = await fetch(`${apiBase}/keywords/${currentCookieId}/image-batch`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                image_url: imageUrl,
                keywords: keywords,
                item_ids: itemIds
            })
        });

        if (batchResponse.ok) {
            const result = await batchResponse.json();
            const successCount = result.success_count || 0;
            const failCount = result.fail_count || 0;

            if (successCount > 0) {
                const keywordText = keywords.length > 1 ? `${keywords.length}个关键词` : `"${keywords[0]}"`;
                const itemText = itemIds.length > 1 ? `${itemIds.length}个商品` : (itemIds[0] ? '指定商品' : '通用');
                
                if (failCount === 0) {
                    showToast(`✨ ${keywordText} 添加成功！（共${totalCount}条配置，应用于${itemText}）`, 'success');
                } else {
                    showToast(`⚠️ 部分添加成功：成功${successCount}条，失败${failCount}条`, 'warning');
                }

                // 关闭模态框
                const modal = bootstrap.Modal.getInstance(document.getElementById('addImageKeywordModal'));
                modal.hide();

                // 只刷新关键词列表，不重新加载整个界面
                await refreshKeywordsList();
            } else {
                showToast('❌ 所有图片关键词添加失败，请检查后重试', 'danger');
            }
        } else {
            const errorData = await batchResponse.json().catch(() => ({}));
            showToast(`❌ 添加图片关键词失败: ${errorData.detail || '请检查后重试'}`, 'danger');
        }
    } catch (error) {
        console.error('添加图片关键词失败:', error);
        showToast('添加图片关键词失败', 'danger');
    } finally {
        toggleLoading(false);
    }
}

// 显示图片模态框
function showImageModal(imageUrl) {
    // 创建模态框HTML
    const modalHtml = `
        <div class="modal fade" id="imageViewModal" tabindex="-1">
            <div class="modal-dialog modal-lg modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">图片预览</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body text-center">
                        <img src="${imageUrl}" alt="关键词图片" style="max-width: 100%; max-height: 70vh; border-radius: 8px;">
                    </div>
                </div>
            </div>
        </div>
    `;

    // 移除已存在的模态框
    const existingModal = document.getElementById('imageViewModal');
    if (existingModal) {
        existingModal.remove();
    }

    // 添加新模态框
    document.body.insertAdjacentHTML('beforeend', modalHtml);

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('imageViewModal'));
    modal.show();

    // 模态框关闭后移除DOM元素
    document.getElementById('imageViewModal').addEventListener('hidden.bs.modal', function() {
        this.remove();
    });
}

// 编辑图片关键词（不允许修改）
function editImageKeyword(index) {
    showToast('图片关键词不允许修改，请删除后重新添加', 'warning');
}

// 修改导出关键词函数，使用后端导出API
async function exportKeywords() {
    if (!currentCookieId) {
        showToast('请先选择账号', 'warning');
        return;
    }

    try {
        toggleLoading(true);

        // 使用后端导出API
        const response = await fetch(`${apiBase}/keywords-export/${currentCookieId}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            // 获取文件blob
            const blob = await response.blob();

            // 从响应头获取文件名
            const contentDisposition = response.headers.get('Content-Disposition');
            let fileName = `关键词数据_${currentCookieId}_${new Date().toISOString().slice(0, 10)}.xlsx`;

            if (contentDisposition) {
                const fileNameMatch = contentDisposition.match(/filename\*=UTF-8''(.+)/);
                if (fileNameMatch) {
                    fileName = decodeURIComponent(fileNameMatch[1]);
                }
            }

            // 创建下载链接
            const url = window.URL.createObjectURL(blob);
            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = url;
            a.download = fileName;
            document.body.appendChild(a);
            a.click();

            // 清理
            window.URL.revokeObjectURL(url);
            document.body.removeChild(a);

            showToast('✅ 关键词导出成功', 'success');
        } else {
            const errorText = await response.text();
            console.error('导出关键词失败:', errorText);
            showToast('导出关键词失败', 'danger');
        }
    } catch (error) {
        console.error('导出关键词失败:', error);
        showToast('导出关键词失败', 'danger');
    } finally {
        toggleLoading(false);
    }
}

// ==================== 备注管理功能 ====================

// 编辑备注
function editRemark(cookieId, currentRemark) {
    console.log('editRemark called:', cookieId, currentRemark); // 调试信息
    const remarkCell = document.querySelector(`[data-cookie-id="${cookieId}"] .remark-display`);
    if (!remarkCell) {
        console.log('remarkCell not found'); // 调试信息
        return;
    }

    // 创建输入框
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'form-control form-control-sm';
    input.value = currentRemark || '';
    input.placeholder = '请输入备注...';
    input.style.fontSize = '0.875rem';
    input.maxLength = 100; // 限制备注长度

    // 保存原始内容和原始值
    const originalContent = remarkCell.innerHTML;
    const originalValue = currentRemark || '';

    // 标记是否已经进行了编辑
    let hasChanged = false;
    let isProcessing = false; // 防止重复处理

    // 替换为输入框
    remarkCell.innerHTML = '';
    remarkCell.appendChild(input);

    // 监听输入变化
    input.addEventListener('input', () => {
        hasChanged = input.value.trim() !== originalValue;
    });

    // 保存函数
    const saveRemark = async () => {
        console.log('saveRemark called, isProcessing:', isProcessing, 'hasChanged:', hasChanged); // 调试信息
        if (isProcessing) return; // 防止重复调用

        const newRemark = input.value.trim();
        console.log('newRemark:', newRemark, 'originalValue:', originalValue); // 调试信息

        // 如果没有变化，直接恢复显示
        if (!hasChanged || newRemark === originalValue) {
            console.log('No changes detected, restoring original content'); // 调试信息
            remarkCell.innerHTML = originalContent;
            return;
        }

        isProcessing = true;

        try {
            const response = await fetch(`${apiBase}/cookies/${cookieId}/remark`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({ remark: newRemark })
            });

            if (response.ok) {
                // 更新显示
                remarkCell.innerHTML = `
                    <span class="remark-display" onclick="editRemark('${cookieId}', '${newRemark.replace(/'/g, '&#39;')}')" title="点击编辑备注" style="cursor: pointer; color: #6c757d; font-size: 0.875rem;">
                        ${newRemark || '<i class="bi bi-plus-circle text-muted"></i> 添加备注'}
                    </span>
                `;
                showToast('备注更新成功', 'success');
            } else {
                const errorData = await response.json();
                showToast(`备注更新失败: ${errorData.detail || '未知错误'}`, 'danger');
                // 恢复原始内容
                remarkCell.innerHTML = originalContent;
            }
        } catch (error) {
            console.error('更新备注失败:', error);
            showToast('备注更新失败', 'danger');
            // 恢复原始内容
            remarkCell.innerHTML = originalContent;
        } finally {
            isProcessing = false;
        }
    };

    // 取消函数
    const cancelEdit = () => {
        if (isProcessing) return;
        remarkCell.innerHTML = originalContent;
    };

    // 延迟绑定blur事件，避免立即触发
    setTimeout(() => {
        input.addEventListener('blur', saveRemark);
    }, 100);

    // 绑定键盘事件
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            saveRemark();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            cancelEdit();
        }
    });

    // 聚焦并选中文本
    input.focus();
    input.select();
}

// 编辑暂停时间
function editPauseDuration(cookieId, currentDuration) {
    console.log('editPauseDuration called:', cookieId, currentDuration); // 调试信息
    const pauseCell = document.querySelector(`[data-cookie-id="${cookieId}"] .pause-duration-display`);
    if (!pauseCell) {
        console.log('pauseCell not found'); // 调试信息
        return;
    }

    // 创建输入框
    const input = document.createElement('input');
    input.type = 'number';
    input.className = 'form-control form-control-sm';
    input.value = currentDuration !== undefined ? currentDuration : 10;
    input.placeholder = '请输入暂停时间...';
    input.style.fontSize = '0.875rem';
    input.min = 0;
    input.max = 60;
    input.step = 1;

    // 保存原始内容和原始值
    const originalContent = pauseCell.innerHTML;
    const originalValue = currentDuration !== undefined ? currentDuration : 10;

    // 标记是否已经进行了编辑
    let hasChanged = false;
    let isProcessing = false; // 防止重复处理

    // 替换为输入框
    pauseCell.innerHTML = '';
    pauseCell.appendChild(input);

    // 监听输入变化
    input.addEventListener('input', () => {
        const newValue = input.value === '' ? 10 : parseInt(input.value);
        hasChanged = newValue !== originalValue;
    });

    // 保存函数
    const savePauseDuration = async () => {
        console.log('savePauseDuration called, isProcessing:', isProcessing, 'hasChanged:', hasChanged); // 调试信息
        if (isProcessing) return; // 防止重复调用

        const newDuration = input.value === '' ? 10 : parseInt(input.value);
        console.log('newDuration:', newDuration, 'originalValue:', originalValue); // 调试信息

        // 验证范围
        if (isNaN(newDuration) || newDuration < 0 || newDuration > 60) {
            showToast('暂停时间必须在0-60分钟之间（0表示不暂停）', 'warning');
            input.focus();
            return;
        }

        // 如果没有变化，直接恢复显示
        if (!hasChanged || newDuration === originalValue) {
            console.log('No changes detected, restoring original content'); // 调试信息
            pauseCell.innerHTML = originalContent;
            return;
        }

        isProcessing = true;

        try {
            const response = await fetch(`${apiBase}/cookies/${cookieId}/pause-duration`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${authToken}`
                },
                body: JSON.stringify({ pause_duration: newDuration })
            });

            if (response.ok) {
                // 更新显示
                pauseCell.innerHTML = `
                    <span class="pause-duration-display" onclick="editPauseDuration('${cookieId}', ${newDuration})" title="点击编辑暂停时间" style="cursor: pointer; color: #6c757d; font-size: 0.875rem;">
                        <i class="bi bi-clock me-1"></i>${newDuration === 0 ? '不暂停' : newDuration + '分钟'}
                    </span>
                `;
                showToast('暂停时间更新成功', 'success');
            } else {
                const errorData = await response.json();
                showToast(`暂停时间更新失败: ${errorData.detail || '未知错误'}`, 'danger');
                // 恢复原始内容
                pauseCell.innerHTML = originalContent;
            }
        } catch (error) {
            console.error('更新暂停时间失败:', error);
            showToast('暂停时间更新失败', 'danger');
            // 恢复原始内容
            pauseCell.innerHTML = originalContent;
        } finally {
            isProcessing = false;
        }
    };

    // 取消函数
    const cancelEdit = () => {
        if (isProcessing) return;
        pauseCell.innerHTML = originalContent;
    };

    // 延迟绑定blur事件，避免立即触发
    setTimeout(() => {
        input.addEventListener('blur', savePauseDuration);
    }, 100);

    // 绑定键盘事件
    input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            savePauseDuration();
        } else if (e.key === 'Escape') {
            e.preventDefault();
            cancelEdit();
        }
    });

    // 聚焦并选中文本
    input.focus();
    input.select();
}

