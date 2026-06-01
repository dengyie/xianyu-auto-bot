// ==================== 默认回复管理功能 ====================

// 打开默认回复管理器
async function openDefaultReplyManager() {
    try {
    await loadDefaultReplies();
    const modal = new bootstrap.Modal(document.getElementById('defaultReplyModal'));
    modal.show();
    } catch (error) {
    console.error('打开默认回复管理器失败:', error);
    showToast('打开默认回复管理器失败', 'danger');
    }
}

// 加载默认回复列表
async function loadDefaultReplies() {
    try {
    // 获取所有账号
    const accountsResponse = await fetch(`${apiBase}/cookies`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (!accountsResponse.ok) {
        throw new Error('获取账号列表失败');
    }

    const accounts = await accountsResponse.json();

    // 获取所有默认回复设置
    const repliesResponse = await fetch(`${apiBase}/default-replies`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    let defaultReplies = {};
    if (repliesResponse.ok) {
        defaultReplies = await repliesResponse.json();
    }

    renderDefaultRepliesList(accounts, defaultReplies);
    } catch (error) {
    console.error('加载默认回复列表失败:', error);
    showToast('加载默认回复列表失败', 'danger');
    }
}

// 渲染默认回复列表
function renderDefaultRepliesList(accounts, defaultReplies) {
    const tbody = document.getElementById('defaultReplyTableBody');
    tbody.innerHTML = '';

    if (accounts.length === 0) {
    tbody.innerHTML = `
        <tr>
        <td colspan="5" class="text-center py-4 text-muted">
            <i class="bi bi-chat-text fs-1 d-block mb-3"></i>
            <h5>暂无账号数据</h5>
            <p class="mb-0">请先添加账号</p>
        </td>
        </tr>
    `;
    return;
    }

    accounts.forEach(accountId => {
    const replySettings = defaultReplies[accountId] || { enabled: false, reply_content: '', reply_once: false };
    const tr = document.createElement('tr');

    // 状态标签
    const statusBadge = replySettings.enabled ?
        '<span class="badge bg-success">启用</span>' :
        '<span class="badge bg-secondary">禁用</span>';

    // 只回复一次标签
    const replyOnceBadge = replySettings.reply_once ?
        '<span class="badge bg-warning">是</span>' :
        '<span class="badge bg-light text-dark">否</span>';

    // 回复内容预览
    let contentPreview = replySettings.reply_content || '未设置';
    if (contentPreview.length > 50) {
        contentPreview = contentPreview.substring(0, 50) + '...';
    }

    tr.innerHTML = `
        <td>
        <strong class="text-primary">${accountId}</strong>
        </td>
        <td>${statusBadge}</td>
        <td>${replyOnceBadge}</td>
        <td>
        <div class="text-truncate" style="max-width: 300px;" title="${replySettings.reply_content || ''}">
            ${contentPreview}
        </div>
        </td>
        <td>
        <div class="btn-group" role="group">
            <button class="btn btn-sm btn-outline-primary" onclick="editDefaultReply('${accountId}')" title="编辑">
            <i class="bi bi-pencil"></i>
            </button>
            <button class="btn btn-sm btn-outline-info" onclick="testDefaultReply('${accountId}')" title="测试">
            <i class="bi bi-play"></i>
            </button>
            ${replySettings.reply_once ? `
            <button class="btn btn-sm btn-outline-warning" onclick="clearDefaultReplyRecords('${accountId}')" title="清空记录">
            <i class="bi bi-arrow-clockwise"></i>
            </button>
            ` : ''}
        </div>
        </td>
    `;

    tbody.appendChild(tr);
    });
}

// 编辑默认回复
async function editDefaultReply(accountId) {
    try {
    // 获取当前设置
    const response = await fetch(`${apiBase}/default-replies/${accountId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    let settings = { enabled: false, reply_content: '', reply_once: false };
    if (response.ok) {
        settings = await response.json();
    }

    // 填充编辑表单
    document.getElementById('editDefaultReplyAccountId').value = accountId;
    document.getElementById('editDefaultReplyAccountIdDisplay').value = accountId;
    document.getElementById('editDefaultReplyEnabled').checked = settings.enabled;
    document.getElementById('editReplyContent').value = settings.reply_content || '';
    document.getElementById('editReplyOnce').checked = settings.reply_once || false;

    // 根据启用状态显示/隐藏内容输入框
    toggleReplyContentVisibility();

    // 显示编辑模态框
    const modal = new bootstrap.Modal(document.getElementById('editDefaultReplyModal'));
    modal.show();
    } catch (error) {
    console.error('获取默认回复设置失败:', error);
    showToast('获取默认回复设置失败', 'danger');
    }
}

// 切换回复内容输入框的显示/隐藏
function toggleReplyContentVisibility() {
    const enabled = document.getElementById('editDefaultReplyEnabled').checked;
    const contentGroup = document.getElementById('editReplyContentGroup');
    contentGroup.style.display = enabled ? 'block' : 'none';
}

// 保存默认回复设置
async function saveDefaultReply() {
    try {
    const accountId = document.getElementById('editDefaultReplyAccountId').value;
    const enabled = document.getElementById('editDefaultReplyEnabled').checked;
    const replyContent = document.getElementById('editReplyContent').value;
    const replyOnce = document.getElementById('editReplyOnce').checked;

    if (enabled && !replyContent.trim()) {
        showToast('启用默认回复时必须设置回复内容', 'warning');
        return;
    }

    const data = {
        enabled: enabled,
        reply_content: enabled ? replyContent : null,
        reply_once: replyOnce
    };

    const response = await fetch(`${apiBase}/default-replies/${accountId}`, {
        method: 'PUT',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
    });

    if (response.ok) {
        showToast('默认回复设置保存成功', 'success');
        bootstrap.Modal.getInstance(document.getElementById('editDefaultReplyModal')).hide();
        loadDefaultReplies(); // 刷新列表
        loadCookies(); // 刷新账号列表以更新默认回复状态显示
    } else {
        const error = await response.text();
        showToast(`保存失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('保存默认回复设置失败:', error);
    showToast('保存默认回复设置失败', 'danger');
    }
}

// 测试默认回复（占位函数）
function testDefaultReply(accountId) {
    showToast('测试功能开发中...', 'info');
}

// 清空默认回复记录
async function clearDefaultReplyRecords(accountId) {
    if (!confirm(`确定要清空账号 "${accountId}" 的默认回复记录吗？\n\n清空后，该账号将可以重新对之前回复过的对话进行默认回复。`)) {
        return;
    }

    try {
        const response = await fetch(`${apiBase}/default-replies/${accountId}/clear-records`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            }
        });

        if (response.ok) {
            showToast(`账号 "${accountId}" 的默认回复记录已清空`, 'success');
            loadDefaultReplies(); // 刷新列表
        } else {
            const error = await response.text();
            showToast(`清空失败: ${error}`, 'danger');
        }
    } catch (error) {
        console.error('清空默认回复记录失败:', error);
        showToast('清空默认回复记录失败', 'danger');
    }
}

// ==================== AI回复配置相关函数 ====================

// 配置AI回复
async function configAIReply(accountId) {
    try {
    // 获取当前AI回复设置
    const settings = await fetchJSON(`${apiBase}/ai-reply-settings/${accountId}`);

    // 填充表单
    document.getElementById('aiConfigAccountId').value = accountId;
    document.getElementById('aiConfigAccountIdDisplay').value = accountId;
    document.getElementById('aiReplyEnabled').checked = settings.ai_enabled;
    // 处理模型名称
    const modelSelect = document.getElementById('aiModelName');
    const customModelInput = document.getElementById('customModelName');
    const modelName = settings.model_name;
    // 检查是否是预设模型
    const presetModels = ['deepseek-v3.2', 'kimi-k2.5', 'qwen3-max-2026-01-23', 'qwen3.5-plus', 'gpt-4o-mini', 'gpt-4o'];
    if (presetModels.includes(modelName)) {
        modelSelect.value = modelName;
        customModelInput.style.display = 'none';
        customModelInput.value = '';
    } else {
        // 自定义模型
        modelSelect.value = 'custom';
        customModelInput.style.display = 'block';
        customModelInput.value = modelName;
    }
    document.getElementById('aiBaseUrl').value = settings.base_url;
    const normalizedApiType = settings.api_type === 'dashscope' ? '' : (settings.api_type || '');
    document.getElementById('aiApiType').value = normalizedApiType;
    document.getElementById('aiApiKey').value = settings.api_key;
    document.getElementById('maxDiscountPercent').value = settings.max_discount_percent;
    document.getElementById('maxDiscountAmount').value = settings.max_discount_amount;
    document.getElementById('maxBargainRounds').value = settings.max_bargain_rounds;
    // 解析自定义提示词 JSON，填入三个独立文本框
    let prompts = {};
    if (settings.custom_prompts) {
        try { prompts = JSON.parse(settings.custom_prompts); } catch (e) { prompts = {}; }
    }
    document.getElementById('promptPrice').value = prompts.price || '';
    document.getElementById('promptTech').value = prompts.tech || '';
    document.getElementById('promptDefault').value = prompts.default || '';

    // 切换设置显示状态
    toggleAIReplySettings();
    updateApiUrlPreview();
    await loadAIPresets();

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('aiReplyConfigModal'));
    modal.show();

    } catch (error) {
    console.error('获取AI回复设置失败:', error);
    showToast('获取AI回复设置失败', 'danger');
    }
}

// 更新API请求地址预览
function updateApiUrlPreview() {
    const baseUrl = (document.getElementById('aiBaseUrl').value || '').replace(/\/+$/, '');
    const apiType = document.getElementById('aiApiType').value;
    const preview = document.getElementById('apiUrlPreview');
    if (!preview || !baseUrl) {
        if (preview) preview.textContent = '';
        return;
    }

    const pathMap = {
        'openai':           '/v1/chat/completions',
        'openai_responses': '/v1/responses',
        'anthropic':        '/v1/messages',
        'azure_openai':     '/chat/completions',
        'ollama':           '/v1/chat/completions',
        'gemini':           '',
    };

    let path = pathMap[apiType];
    if (path === undefined) {
        // 自动识别 — 默认 chat/completions
        path = '/v1/chat/completions';
    }

    if (!path) {
        // Gemini 地址格式特殊，不追加路径
        preview.textContent = '请求端点预览: ' + baseUrl;
    } else if (apiType === 'azure_openai') {
        // Azure 不自动加 /v1
        const url = baseUrl.includes('/chat/completions') ? baseUrl : baseUrl + path;
        preview.textContent = '请求端点预览: ' + url;
    } else {
        const base = baseUrl.endsWith('/v1') ? baseUrl : baseUrl + '/v1';
        const suffix = path.replace('/v1', '');
        preview.textContent = '请求端点预览: ' + base + suffix;
    }
}

// 切换AI回复设置显示
function toggleAIReplySettings() {
    const enabled = document.getElementById('aiReplyEnabled').checked;
    const settingsDiv = document.getElementById('aiReplySettings');
    const bargainSettings = document.getElementById('bargainSettings');
    const promptSettings = document.getElementById('promptSettings');
    const testArea = document.getElementById('testArea');

    if (enabled) {
    settingsDiv.style.display = 'block';
    bargainSettings.style.display = 'block';
    promptSettings.style.display = 'block';
    testArea.style.display = 'block';
    } else {
    settingsDiv.style.display = 'none';
    bargainSettings.style.display = 'none';
    promptSettings.style.display = 'none';
    testArea.style.display = 'none';
    }
}

// 保存AI回复配置
async function saveAIReplyConfig() {
    try {
    const accountId = document.getElementById('aiConfigAccountId').value;
    const enabled = document.getElementById('aiReplyEnabled').checked;

    // 如果启用AI回复，验证必填字段
    if (enabled) {
        const apiKey = document.getElementById('aiApiKey').value.trim();
        if (!apiKey) {
        showToast('请输入API密钥', 'warning');
        return;
        }
    }
// 获取模型名称
    let modelName = document.getElementById('aiModelName').value;
    if (modelName === 'custom') {
        const customModelName = document.getElementById('customModelName').value.trim();
        if (!customModelName) {
        showToast('请输入自定义模型名称', 'warning');
        return;
        }
        modelName = customModelName;
    }
    // 从三个文本框组装自定义提示词 JSON
    const promptsObj = {};
    const priceVal = document.getElementById('promptPrice').value.trim();
    const techVal = document.getElementById('promptTech').value.trim();
    const defaultVal = document.getElementById('promptDefault').value.trim();
    if (priceVal) promptsObj.price = priceVal;
    if (techVal) promptsObj.tech = techVal;
    if (defaultVal) promptsObj.default = defaultVal;
    const customPromptsJson = Object.keys(promptsObj).length > 0 ? JSON.stringify(promptsObj) : '';

    // 构建设置对象
    const settings = {
        ai_enabled: enabled,
        model_name: modelName,
        api_key: document.getElementById('aiApiKey').value,
        base_url: document.getElementById('aiBaseUrl').value,
        api_type: document.getElementById('aiApiType').value,
        max_discount_percent: parseInt(document.getElementById('maxDiscountPercent').value),
        max_discount_amount: parseInt(document.getElementById('maxDiscountAmount').value),
        max_bargain_rounds: parseInt(document.getElementById('maxBargainRounds').value),
        custom_prompts: customPromptsJson
    };

    // 保存设置
    const response = await fetch(`${apiBase}/ai-reply-settings/${accountId}`, {
        method: 'PUT',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify(settings)
    });

    if (response.ok) {
        showToast('AI回复配置保存成功', 'success');
        bootstrap.Modal.getInstance(document.getElementById('aiReplyConfigModal')).hide();
        loadCookies(); // 刷新账号列表以更新AI回复状态显示
    } else {
        const error = await response.text();
        showToast(`保存失败: ${error}`, 'danger');
    }

    } catch (error) {
    console.error('保存AI回复配置失败:', error);
    showToast('保存AI回复配置失败', 'danger');
    }
}

// 测试AI回复
async function testAIReply() {
    const testBtn = document.querySelector('[onclick="testAIReply()"]');
    if (testBtn && testBtn.disabled) return;
    if (testBtn) { testBtn.disabled = true; testBtn.textContent = '测试中...'; }

    try {
    const accountId = document.getElementById('aiConfigAccountId').value;
    const testMessage = document.getElementById('testMessage').value.trim();
    const testItemPrice = document.getElementById('testItemPrice').value;

    if (!testMessage) {
        showToast('请输入测试消息', 'warning');
        return;
    }

    // 构建测试数据
    const testData = {
        message: testMessage,
        item_title: '测试商品',
        item_price: parseFloat(testItemPrice) || 100,
        item_desc: '这是一个用于测试AI回复功能的商品'
    };

    // 显示加载状态
    const testResult = document.getElementById('testResult');
    const testReplyContent = document.getElementById('testReplyContent');
    testResult.style.display = 'block';
    testReplyContent.innerHTML = '<i class="bi bi-hourglass-split"></i> 正在生成AI回复...';

    // 调用测试API
    const response = await fetch(`${apiBase}/ai-reply-test/${accountId}`, {
        method: 'POST',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify(testData)
    });

    if (response.ok) {
        const result = await response.json();
        testReplyContent.innerHTML = result.reply;
        showToast('AI回复测试成功', 'success');
    } else {
        const error = await response.text();
        testReplyContent.innerHTML = `<span class="text-danger">测试失败: ${error}</span>`;
        showToast(`测试失败: ${error}`, 'danger');
    }

    } catch (error) {
    console.error('测试AI回复失败:', error);
    const testReplyContent = document.getElementById('testReplyContent');
    testReplyContent.innerHTML = `<span class="text-danger">测试失败: ${error.message}</span>`;
    showToast('测试AI回复失败', 'danger');
    } finally {
    if (testBtn) { testBtn.disabled = false; testBtn.textContent = '测试回复'; }
    }
}

// 切换自定义模型输入框的显示/隐藏
function toggleCustomModelInput() {
    const modelSelect = document.getElementById('aiModelName');
    const customModelInput = document.getElementById('customModelName');
    if (modelSelect.value === 'custom') {
    customModelInput.style.display = 'block';
    customModelInput.focus();
    } else {
    customModelInput.style.display = 'none';
    customModelInput.value = '';
    }
}

// -------------------- AI配置预设功能 --------------------

let _aiPresets = []; // 缓存预设数据，避免依赖 option dataset

async function loadAIPresets() {
    try {
        const presets = await fetchJSON(`${apiBase}/ai-config-presets`);
        _aiPresets = presets || [];
        const select = document.getElementById('aiPresetSelect');
        const deleteBtn = document.getElementById('deletePresetBtn');
        select.innerHTML = '<option value="">-- 选择预设 --</option>';
        _aiPresets.forEach(p => {
            const opt = document.createElement('option');
            opt.value = p.id;
            opt.textContent = p.preset_name;
            select.appendChild(opt);
        });
        // 尝试自动匹配当前表单值对应的预设
        _autoSelectMatchingPreset();
        deleteBtn.style.display = select.value ? '' : 'none';
    } catch (e) {
        console.error('加载AI配置预设失败:', e);
    }
}

function _autoSelectMatchingPreset() {
    const select = document.getElementById('aiPresetSelect');
    const modelSelect = document.getElementById('aiModelName');
    const customModelInput = document.getElementById('customModelName');
    const curModel = modelSelect.value === 'custom' ? customModelInput.value : modelSelect.value;
    const curKey = document.getElementById('aiApiKey').value;
    const curUrl = document.getElementById('aiBaseUrl').value;
    const curApiType = document.getElementById('aiApiType').value;

    const match = _aiPresets.find(p => {
        const presetApiType = p.api_type === 'dashscope' ? '' : (p.api_type || '');
        return p.model_name === curModel && p.api_key === curKey && p.base_url === curUrl && presetApiType === curApiType;
    });
    select.value = match ? match.id : '';
}

function loadAIPreset() {
    const select = document.getElementById('aiPresetSelect');
    const deleteBtn = document.getElementById('deletePresetBtn');
    const presetId = select.value;

    if (!presetId) {
        deleteBtn.style.display = 'none';
        return;
    }
    deleteBtn.style.display = '';

    const preset = _aiPresets.find(p => String(p.id) === presetId);
    if (!preset) return;

    // 填充模型
    const modelSelect = document.getElementById('aiModelName');
    const customModelInput = document.getElementById('customModelName');
    const builtinModels = Array.from(modelSelect.options).map(o => o.value).filter(v => v && v !== 'custom');
    if (builtinModels.includes(preset.model_name)) {
        modelSelect.value = preset.model_name;
        customModelInput.style.display = 'none';
        customModelInput.value = '';
    } else {
        modelSelect.value = 'custom';
        customModelInput.style.display = 'block';
        customModelInput.value = preset.model_name;
    }

    document.getElementById('aiBaseUrl').value = preset.base_url;
    document.getElementById('aiApiKey').value = preset.api_key;
    const normalizedPresetApiType = preset.api_type === 'dashscope' ? '' : (preset.api_type || '');
    document.getElementById('aiApiType').value = normalizedPresetApiType;
    updateApiUrlPreview();

    showToast(`已切换到预设「${preset.preset_name}」`, 'success');
}

async function saveCurrentAsPreset() {
    const name = prompt('请输入预设名称：');
    if (!name || !name.trim()) return;

    const modelSelect = document.getElementById('aiModelName');
    const customModelInput = document.getElementById('customModelName');
    const modelName = modelSelect.value === 'custom' ? customModelInput.value : modelSelect.value;
    const apiKey = document.getElementById('aiApiKey').value;
    const baseUrl = document.getElementById('aiBaseUrl').value;

    if (!modelName) {
        showToast('请先选择或输入模型名称', 'warning');
        return;
    }

    try {
        await fetchJSON(`${apiBase}/ai-config-presets`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                preset_name: name.trim(),
                model_name: modelName,
                api_key: apiKey,
                base_url: baseUrl,
                api_type: document.getElementById('aiApiType').value
            })
        });
        showToast('预设保存成功', 'success');
        await loadAIPresets();
        // 自动选中刚保存的预设
        const select = document.getElementById('aiPresetSelect');
        const saved = _aiPresets.find(p => p.preset_name === name.trim());
        if (saved) {
            select.value = saved.id;
            document.getElementById('deletePresetBtn').style.display = '';
        }
    } catch (e) {
        console.error('保存预设失败:', e);
        showToast('保存预设失败', 'danger');
    }
}

async function deleteSelectedPreset() {
    const select = document.getElementById('aiPresetSelect');
    const presetId = select.value;
    if (!presetId) return;

    const preset = _aiPresets.find(p => String(p.id) === presetId);
    if (!preset) return;
    if (!confirm(`确定删除预设「${preset.preset_name}」吗？`)) return;

    try {
        await fetchJSON(`${apiBase}/ai-config-presets/${presetId}`, {
            method: 'DELETE'
        });
        showToast('预设已删除', 'success');
        await loadAIPresets();
    } catch (e) {
        console.error('删除预设失败:', e);
        showToast('删除预设失败', 'danger');
    }
}

// 监听默认回复启用状态变化
document.addEventListener('DOMContentLoaded', function() {
    const enabledCheckbox = document.getElementById('editDefaultReplyEnabled');
    if (enabledCheckbox) {
    enabledCheckbox.addEventListener('change', toggleReplyContentVisibility);
    }
});

// ================================
// 【外发配置菜单】相关功能
// ================================

// 外发配置类型配置
const outgoingConfigs = {
    smtp: {
        title: 'SMTP邮件配置',
        description: '配置SMTP服务器用于发送注册验证码等邮件通知',
        icon: 'bi-envelope-fill',
        color: 'primary',
        fields: [
            {
                id: 'smtp_server',
                label: 'SMTP服务器',
                type: 'text',
                placeholder: 'smtp.qq.com',
                required: true,
                help: '邮箱服务商的SMTP服务器地址，如：smtp.qq.com、smtp.gmail.com'
            },
            {
                id: 'smtp_port',
                label: 'SMTP端口',
                type: 'number',
                placeholder: '587',
                required: true,
                help: '通常为587（TLS）或465（SSL）'
            },
            {
                id: 'smtp_user',
                label: '发件邮箱',
                type: 'email',
                placeholder: 'your-email@qq.com',
                required: true,
                help: '用于发送邮件的邮箱地址'
            },
            {
                id: 'smtp_password',
                label: '邮箱密码/授权码',
                type: 'password',
                placeholder: '输入密码或授权码',
                required: true,
                help: '邮箱密码或应用专用密码（QQ邮箱需要授权码）'
            },
            {
                id: 'smtp_from',
                label: '发件人显示名（可选）',
                type: 'text',
                placeholder: '闲鱼管理系统',
                required: false,
                help: '邮件发件人显示的名称，留空则使用邮箱地址'
            },
            {
                id: 'smtp_use_tls',
                label: '启用TLS',
                type: 'select',
                options: [
                    { value: 'true', text: '是' },
                    { value: 'false', text: '否' }
                ],
                required: true,
                help: '是否启用TLS加密（推荐开启）'
            },
            {
                id: 'smtp_use_ssl',
                label: '启用SSL',
                type: 'select',
                options: [
                    { value: 'true', text: '是' },
                    { value: 'false', text: '否' }
                ],
                required: true,
                help: '是否启用SSL加密（与TLS二选一）'
            }
        ]
    }
};

