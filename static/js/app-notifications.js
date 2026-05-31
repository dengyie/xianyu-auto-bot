// ================================
// 【通知渠道菜单】相关功能
// ================================

// 通知渠道类型配置
const channelTypeConfigs = {
    qq: {
    title: 'QQ通知',
    description: '需要添加QQ号 <code>3607695896</code> 为好友才能正常接收消息通知',
    icon: 'bi-chat-dots-fill',
    color: 'primary',
    fields: [
        {
        id: 'qq_number',
        label: '接收QQ号码',
        type: 'text',
        placeholder: '输入QQ号码',
        required: true,
        help: '用于接收通知消息的QQ号码'
        }
    ]
    },
    dingtalk: {
    title: '钉钉通知',
    description: '请设置钉钉机器人Webhook URL，支持自定义机器人和群机器人',
    icon: 'bi-bell-fill',
    color: 'info',
    fields: [
        {
        id: 'webhook_url',
        label: '钉钉机器人Webhook URL',
        type: 'url',
        placeholder: 'https://oapi.dingtalk.com/robot/send?access_token=...',
        required: true,
        help: '钉钉机器人的Webhook地址'
        },
        {
        id: 'secret',
        label: '加签密钥（可选）',
        type: 'text',
        placeholder: '输入加签密钥',
        required: false,
        help: '如果机器人开启了加签验证，请填写密钥'
        }
    ]
    },
    feishu: {
    title: '飞书通知',
    description: '请设置飞书机器人Webhook URL，支持自定义机器人和群机器人',
    icon: 'bi-chat-square-text-fill',
    color: 'warning',
    fields: [
        {
        id: 'webhook_url',
        label: '飞书机器人Webhook URL',
        type: 'url',
        placeholder: 'https://open.feishu.cn/open-apis/bot/v2/hook/...',
        required: true,
        help: '飞书机器人的Webhook地址'
        },
        {
        id: 'secret',
        label: '签名密钥（可选）',
        type: 'text',
        placeholder: '输入签名密钥',
        required: false,
        help: '如果机器人开启了签名验证，请填写密钥'
        }
    ]
    },
    bark: {
    title: 'Bark通知',
    description: 'iOS推送通知服务，支持自建服务器和官方服务器',
    icon: 'bi-phone-fill',
    color: 'dark',
    fields: [
        {
        id: 'device_key',
        label: '设备密钥',
        type: 'text',
        placeholder: '输入Bark设备密钥',
        required: true,
        help: 'Bark应用中显示的设备密钥'
        },
        {
        id: 'server_url',
        label: '服务器地址（可选）',
        type: 'url',
        placeholder: 'https://api.day.app',
        required: false,
        help: '自建Bark服务器地址，留空使用官方服务器'
        },
        {
        id: 'title',
        label: '通知标题（可选）',
        type: 'text',
        placeholder: '闲鱼管理系统通知',
        required: false,
        help: '推送通知的标题'
        },
        {
        id: 'sound',
        label: '提示音（可选）',
        type: 'text',
        placeholder: 'default',
        required: false,
        help: '通知提示音，如：alarm, anticipate, bell等'
        },
        {
        id: 'group',
        label: '分组（可选）',
        type: 'text',
        placeholder: 'xianyu',
        required: false,
        help: '通知分组名称，用于归类消息'
        }
    ]
    },
    email: {
    title: '邮件通知',
    description: '通过SMTP服务器发送邮件通知，支持各种邮箱服务商',
    icon: 'bi-envelope-fill',
    color: 'success',
    fields: [
        {
        id: 'smtp_server',
        label: 'SMTP服务器',
        type: 'text',
        placeholder: 'smtp.gmail.com',
        required: true,
        help: '邮箱服务商的SMTP服务器地址'
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
        id: 'email_user',
        label: '发送邮箱',
        type: 'email',
        placeholder: 'your-email@gmail.com',
        required: true,
        help: '用于发送通知的邮箱地址'
        },
        {
        id: 'email_password',
        label: '邮箱密码/授权码',
        type: 'password',
        placeholder: '输入密码或授权码',
        required: true,
        help: '邮箱密码或应用专用密码'
        },
        {
        id: 'recipient_email',
        label: '接收邮箱',
        type: 'email',
        placeholder: 'recipient@example.com',
        required: true,
        help: '用于接收通知的邮箱地址'
        }
    ]
    },
    webhook: {
    title: 'Webhook通知',
    description: '通过HTTP POST请求发送通知到自定义的Webhook地址',
    icon: 'bi-link-45deg',
    color: 'warning',
    fields: [
        {
        id: 'webhook_url',
        label: 'Webhook URL',
        type: 'url',
        placeholder: 'https://your-server.com/webhook',
        required: true,
        help: '接收通知的Webhook地址'
        },
        {
        id: 'http_method',
        label: 'HTTP方法',
        type: 'select',
        options: [
            { value: 'POST', text: 'POST' },
            { value: 'PUT', text: 'PUT' }
        ],
        required: true,
        help: '发送请求使用的HTTP方法'
        },
        {
        id: 'headers',
        label: '自定义请求头（可选）',
        type: 'textarea',
        placeholder: '{"Authorization": "Bearer token", "Content-Type": "application/json"}',
        required: false,
        help: 'JSON格式的自定义请求头'
        }
    ]
    },
    wechat: {
    title: '微信通知',
    description: '通过企业微信机器人发送通知消息',
    icon: 'bi-wechat',
    color: 'success',
    fields: [
        {
        id: 'webhook_url',
        label: '企业微信机器人Webhook URL',
        type: 'url',
        placeholder: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=...',
        required: true,
        help: '企业微信群机器人的Webhook地址'
        }
    ]
    },
    telegram: {
    title: 'Telegram通知',
    description: '通过Telegram机器人发送通知消息（需要海外服务器）',
    icon: 'bi-telegram',
    color: 'primary',
    fields: [
        {
        id: 'bot_token',
        label: 'Bot Token',
        type: 'text',
        placeholder: '123456789:ABCdefGHIjklMNOpqrsTUVwxyz',
        required: true,
        help: '从@BotFather获取的机器人Token'
        },
        {
        id: 'chat_id',
        label: 'Chat ID',
        type: 'text',
        placeholder: '123456789 或 @channel_name',
        required: true,
        help: '接收消息的用户ID或频道名'
        }
    ]
    }
};

// 显示添加渠道模态框
function showAddChannelModal(type) {
    const config = channelTypeConfigs[type];
    if (!config) {
    showToast('不支持的通知渠道类型', 'danger');
    return;
    }

    // 设置模态框标题和描述
    document.getElementById('addChannelModalTitle').textContent = `添加${config.title}`;
    document.getElementById('channelTypeDescription').innerHTML = config.description;
    document.getElementById('channelType').value = type;

    // 生成配置字段
    const fieldsContainer = document.getElementById('channelConfigFields');
    fieldsContainer.innerHTML = '';

    config.fields.forEach(field => {
    const fieldHtml = generateFieldHtml(field, 'add_');
    fieldsContainer.insertAdjacentHTML('beforeend', fieldHtml);
    });

    // 显示模态框
    const modal = new bootstrap.Modal(document.getElementById('addChannelModal'));
    modal.show();
}

// 生成表单字段HTML
function generateFieldHtml(field, prefix) {
    const fieldId = prefix + field.id;
    let inputHtml = '';

    switch (field.type) {
    case 'select':
        inputHtml = `<select class="form-select" id="${fieldId}" ${field.required ? 'required' : ''}>`;
        if (field.options) {
        field.options.forEach(option => {
            inputHtml += `<option value="${option.value}">${option.text}</option>`;
        });
        }
        inputHtml += '</select>';
        break;
    case 'textarea':
        inputHtml = `<textarea class="form-control" id="${fieldId}" placeholder="${field.placeholder}" rows="3" ${field.required ? 'required' : ''}></textarea>`;
        break;
    default:
        inputHtml = `<input type="${field.type}" class="form-control" id="${fieldId}" placeholder="${field.placeholder}" ${field.required ? 'required' : ''}>`;
    }

    return `
    <div class="mb-3">
        <label for="${fieldId}" class="form-label">
        ${field.label} ${field.required ? '<span class="text-danger">*</span>' : ''}
        </label>
        ${inputHtml}
        ${field.help ? `<small class="form-text text-muted">${field.help}</small>` : ''}
    </div>
    `;
}

// 保存通知渠道
async function saveNotificationChannel() {
    const type = document.getElementById('channelType').value;
    const name = document.getElementById('channelName').value;
    const enabled = document.getElementById('channelEnabled').checked;
    const form = document.getElementById('addChannelForm');

    if (!name.trim()) {
    showToast('请输入渠道名称', 'warning');
    return;
    }

    const config = channelTypeConfigs[type];
    if (!config) {
    showToast('无效的渠道类型', 'danger');
    return;
    }

    // 收集配置数据
    const configData = {};
    let hasError = false;

    config.fields.forEach(field => {
    const element = form ? form.querySelector(`#add_${field.id}`) : null;
    if (!element) {
        showToast(`找不到${field.label}输入框`, 'danger');
        hasError = true;
        return;
    }
    const value = element.value.trim();

    if (field.required && !value) {
        showToast(`请填写${field.label}`, 'warning');
        hasError = true;
        return;
    }

    if (value) {
        configData[field.id] = value;
    }
    });

    if (hasError) return;

    try {
    const response = await fetch(`${apiBase}/notification-channels`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify({
        name: name,
        type: type,
        config: JSON.stringify(configData),
        enabled: enabled
        })
    });

    if (response.ok) {
        showToast('通知渠道添加成功', 'success');
        const modal = bootstrap.Modal.getInstance(document.getElementById('addChannelModal'));
        modal.hide();
        loadNotificationChannels();
    } else {
        const error = await response.text();
        showToast(`添加失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('添加通知渠道失败:', error);
    showToast('添加通知渠道失败', 'danger');
    }
}

// 加载通知渠道列表
async function loadNotificationChannels() {
    try {
    const response = await fetch(`${apiBase}/notification-channels`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (!response.ok) {
        throw new Error('获取通知渠道失败');
    }

    const channels = await response.json();
    renderNotificationChannels(channels);
    } catch (error) {
    console.error('加载通知渠道失败:', error);
    showToast('加载通知渠道失败', 'danger');
    }
}

// 渲染通知渠道列表
function renderNotificationChannels(channels) {
    const tbody = document.getElementById('channelsTableBody');
    tbody.innerHTML = '';

    if (channels.length === 0) {
    tbody.innerHTML = `
        <tr>
        <td colspan="6" class="text-center py-4 text-muted">
            <i class="bi bi-bell fs-1 d-block mb-3"></i>
            <h5>暂无通知渠道</h5>
            <p class="mb-0">点击上方按钮添加通知渠道</p>
        </td>
        </tr>
    `;
    return;
    }

    channels.forEach(channel => {
    const tr = document.createElement('tr');

    const statusBadge = channel.enabled ?
        '<span class="badge bg-success">启用</span>' :
        '<span class="badge bg-secondary">禁用</span>';

    // 获取渠道类型配置（处理类型映射）
    let channelType = channel.type;
    if (channelType === 'ding_talk') {
        channelType = 'dingtalk';  // 兼容旧的类型名
    } else if (channelType === 'lark') {
        channelType = 'feishu';  // 兼容lark类型名
    }
    const typeConfig = channelTypeConfigs[channelType];
    const typeDisplay = typeConfig ? typeConfig.title : channel.type;
    const typeColor = typeConfig ? typeConfig.color : 'secondary';

    // 解析并显示配置信息
    let configDisplay = '';
    try {
        const configData = JSON.parse(channel.config || '{}');
        const configEntries = Object.entries(configData);

        if (configEntries.length > 0) {
        configDisplay = configEntries.map(([key, value]) => {
            // 隐藏敏感信息
            if (key.includes('password') || key.includes('token') || key.includes('secret')) {
            return `${key}: ****`;
            }
            // 截断过长的值
            const displayValue = value.length > 30 ? value.substring(0, 30) + '...' : value;
            return `${key}: ${displayValue}`;
        }).join('<br>');
        } else {
        configDisplay = channel.config || '无配置';
        }
    } catch (e) {
        // 兼容旧格式
        configDisplay = channel.config || '无配置';
        if (configDisplay.length > 30) {
        configDisplay = configDisplay.substring(0, 30) + '...';
        }
    }

    tr.innerHTML = `
        <td><strong class="text-primary">${channel.id}</strong></td>
        <td>
        <div class="d-flex align-items-center">
            <i class="bi ${typeConfig ? typeConfig.icon : 'bi-bell'} me-2 text-${typeColor}"></i>
            ${channel.name}
        </div>
        </td>
        <td><span class="badge bg-${typeColor}">${typeDisplay}</span></td>
        <td><small class="text-muted">${configDisplay}</small></td>
        <td>${statusBadge}</td>
        <td>
        <div class="btn-group" role="group">
            <button class="btn btn-sm btn-outline-primary" onclick="editNotificationChannel(${channel.id})" title="编辑">
            <i class="bi bi-pencil"></i>
            </button>
            <button class="btn btn-sm btn-outline-danger" onclick="deleteNotificationChannel(${channel.id})" title="删除">
            <i class="bi bi-trash"></i>
            </button>
        </div>
        </td>
    `;

    tbody.appendChild(tr);
    });
}



// 删除通知渠道
async function deleteNotificationChannel(channelId) {
    if (!confirm('确定要删除这个通知渠道吗？')) {
    return;
    }

    try {
    const response = await fetch(`${apiBase}/notification-channels/${channelId}`, {
        method: 'DELETE',
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        showToast('通知渠道删除成功', 'success');
        loadNotificationChannels();
    } else {
        const error = await response.text();
        showToast(`删除失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('删除通知渠道失败:', error);
    showToast('删除通知渠道失败', 'danger');
    }
}

// 编辑通知渠道
async function editNotificationChannel(channelId) {
    try {
    // 获取渠道详情
    const response = await fetch(`${apiBase}/notification-channels`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (!response.ok) {
        throw new Error('获取通知渠道失败');
    }

    const channels = await response.json();
    const channel = channels.find(c => c.id === channelId);

    if (!channel) {
        showToast('通知渠道不存在', 'danger');
        return;
    }

    // 处理类型映射
    let channelType = channel.type;
    if (channelType === 'ding_talk') {
        channelType = 'dingtalk';  // 兼容旧的类型名
    } else if (channelType === 'lark') {
        channelType = 'feishu';  // 兼容lark类型名
    }

    const config = channelTypeConfigs[channelType];
    if (!config) {
        showToast('不支持的渠道类型', 'danger');
        return;
    }

    // 填充基本信息
    document.getElementById('editChannelId').value = channel.id;
    document.getElementById('editChannelType').value = channelType;  // 使用映射后的类型
    document.getElementById('editChannelName').value = channel.name;
    document.getElementById('editChannelEnabled').checked = channel.enabled;

    // 解析配置数据
    let configData = {};
    try {
        configData = JSON.parse(channel.config || '{}');
    } catch (e) {
        // 兼容旧格式（直接字符串）
        if (channel.type === 'qq') {
        configData = { qq_number: channel.config };
        } else if (channel.type === 'dingtalk' || channel.type === 'ding_talk') {
        configData = { webhook_url: channel.config };
        } else if (channel.type === 'feishu' || channel.type === 'lark') {
        configData = { webhook_url: channel.config };
        } else if (channel.type === 'bark') {
        configData = { device_key: channel.config };
        } else {
        configData = { config: channel.config };
        }
    }

    // 生成编辑字段
    const fieldsContainer = document.getElementById('editChannelConfigFields');
    fieldsContainer.innerHTML = '';

    config.fields.forEach(field => {
        const fieldHtml = generateFieldHtml(field, 'edit_');
        fieldsContainer.insertAdjacentHTML('beforeend', fieldHtml);

        // 填充现有值
        const element = document.getElementById('edit_' + field.id);
        if (element && configData[field.id]) {
        element.value = configData[field.id];
        }
    });

    // 显示编辑模态框
    const modal = new bootstrap.Modal(document.getElementById('editChannelModal'));
    modal.show();
    } catch (error) {
    console.error('编辑通知渠道失败:', error);
    showToast('编辑通知渠道失败', 'danger');
    }
}

// 更新通知渠道
async function updateNotificationChannel() {
    const channelId = document.getElementById('editChannelId').value;
    const type = document.getElementById('editChannelType').value;
    const name = document.getElementById('editChannelName').value;
    const enabled = document.getElementById('editChannelEnabled').checked;

    if (!name.trim()) {
    showToast('请输入渠道名称', 'warning');
    return;
    }

    const config = channelTypeConfigs[type];
    if (!config) {
    showToast('无效的渠道类型', 'danger');
    return;
    }

    // 收集配置数据
    const configData = {};
    let hasError = false;

    config.fields.forEach(field => {
    const element = document.getElementById('edit_' + field.id);
    const value = element.value.trim();

    if (field.required && !value) {
        showToast(`请填写${field.label}`, 'warning');
        hasError = true;
        return;
    }

    if (value) {
        configData[field.id] = value;
    }
    });

    if (hasError) return;

    try {
    const response = await fetch(`${apiBase}/notification-channels/${channelId}`, {
        method: 'PUT',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify({
        name: name,
        config: JSON.stringify(configData),
        enabled: enabled
        })
    });

    if (response.ok) {
        showToast('通知渠道更新成功', 'success');
        const modal = bootstrap.Modal.getInstance(document.getElementById('editChannelModal'));
        modal.hide();
        loadNotificationChannels();
    } else {
        const error = await response.text();
        showToast(`更新失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('更新通知渠道失败:', error);
    showToast('更新通知渠道失败', 'danger');
    }
}

// ================================
// 【通知模板配置】相关功能
// ================================

// 通知模板预览数据
const templatePreviewData = {
    message: {
        account_id: 'test_account',
        buyer_name: '张三',
        buyer_id: '123456789',
        item_id: '987654321',
        chat_id: 'chat_001',
        message: '你好，这个商品还有吗？',
        time: new Date().toLocaleString('zh-CN')
    },
    token_refresh: {
        account_id: 'test_account',
        time: new Date().toLocaleString('zh-CN'),
        error_message: 'Token已过期，需要重新登录',
        verification_url: 'https://example.com/verify'
    },
    delivery: {
        account_id: 'test_account',
        buyer_name: '李四',
        buyer_id: '234567890',
        item_id: '876543210',
        chat_id: 'chat_002',
        result: '发货成功',
        time: new Date().toLocaleString('zh-CN')
    },
    slider_success: {
        account_id: 'test_account',
        time: new Date().toLocaleString('zh-CN'),
        status_text: 'cookies已自动更新到数据库'
    },
    face_verify: {
        account_id: 'test_account',
        time: new Date().toLocaleString('zh-CN'),
        verification_action: '请点击验证链接完成验证:',
        verification_url: 'https://passport.goofish.com/mini_login.htm?example=test',
        verification_type: '身份验证'
    },
    password_login_success: {
        account_id: 'test_account',
        time: new Date().toLocaleString('zh-CN'),
        cookie_count: '30'
    },
    cookie_refresh_success: {
        account_id: 'test_account',
        time: new Date().toLocaleString('zh-CN'),
        cookie_count: '30'
    }
};

// 加载通知模板
async function loadNotificationTemplates() {
    try {
        // 重置tab状态，确保只显示第一个tab
        const tabContent = document.getElementById('notificationTemplateTabContent');
        if (tabContent) {
            // 重置所有tab-pane
            tabContent.querySelectorAll('.tab-pane').forEach(pane => {
                pane.classList.remove('show', 'active');
            });
            // 激活第一个tab-pane
            const firstPane = tabContent.querySelector('#message-template');
            if (firstPane) {
                firstPane.classList.add('show', 'active');
            }

            // 重置所有tab按钮
            const tabList = document.getElementById('notificationTemplateTabs');
            if (tabList) {
                tabList.querySelectorAll('.nav-link').forEach(link => {
                    link.classList.remove('active');
                    link.setAttribute('aria-selected', 'false');
                });
                const firstTab = tabList.querySelector('#message-template-tab');
                if (firstTab) {
                    firstTab.classList.add('active');
                    firstTab.setAttribute('aria-selected', 'true');
                }
            }
        }

        const response = await fetch(`${apiBase}/notification-templates`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            throw new Error('获取通知模板失败');
        }

        const data = await response.json();
        const templates = data.templates || [];

        // 加载每个模板到编辑器
        templates.forEach(template => {
            const editor = document.getElementById(`${template.type}-template-editor`);
            if (editor) {
                editor.value = template.template;
                updateTemplatePreview(template.type);
            }
        });

        // 如果没有模板数据，加载默认模板
        ['message', 'token_refresh', 'delivery', 'slider_success', 'face_verify'].forEach(async (type) => {
            const editor = document.getElementById(`${type}-template-editor`);
            if (editor && !editor.value) {
                await loadDefaultTemplate(type);
            }
        });

        showToast('通知模板加载成功', 'success');
    } catch (error) {
        console.error('加载通知模板失败:', error);
        showToast('加载通知模板失败', 'danger');
    }
}

// 加载默认模板
async function loadDefaultTemplate(templateType) {
    try {
        const response = await fetch(`${apiBase}/notification-templates/${templateType}/default`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            const editor = document.getElementById(`${templateType}-template-editor`);
            if (editor) {
                editor.value = data.template;
                updateTemplatePreview(templateType);
            }
        }
    } catch (error) {
        console.error(`加载默认模板失败 (${templateType}):`, error);
    }
}

// 保存通知模板
async function saveNotificationTemplate(templateType) {
    try {
        const editor = document.getElementById(`${templateType}-template-editor`);
        if (!editor) {
            showToast('编辑器不存在', 'danger');
            return;
        }

        const template = editor.value;
        if (!template.trim()) {
            showToast('模板内容不能为空', 'warning');
            return;
        }

        const response = await fetch(`${apiBase}/notification-templates/${templateType}`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({ template })
        });

        if (!response.ok) {
            throw new Error('保存模板失败');
        }

        showToast('模板保存成功', 'success');
    } catch (error) {
        console.error('保存通知模板失败:', error);
        showToast('保存模板失败', 'danger');
    }
}

// 重置通知模板
async function resetNotificationTemplate(templateType) {
    if (!confirm('确定要恢复默认模板吗？当前修改将会丢失。')) {
        return;
    }

    try {
        const response = await fetch(`${apiBase}/notification-templates/${templateType}/reset`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (!response.ok) {
            throw new Error('重置模板失败');
        }

        const data = await response.json();
        const editor = document.getElementById(`${templateType}-template-editor`);
        if (editor && data.template) {
            editor.value = data.template.template;
            updateTemplatePreview(templateType);
        }

        showToast('模板已恢复默认', 'success');
    } catch (error) {
        console.error('重置通知模板失败:', error);
        showToast('重置模板失败', 'danger');
    }
}

// 插入模板变量
function insertTemplateVariable(templateType, variable) {
    const editor = document.getElementById(`${templateType}-template-editor`);
    if (!editor) return;

    const start = editor.selectionStart;
    const end = editor.selectionEnd;
    const text = editor.value;

    editor.value = text.substring(0, start) + variable + text.substring(end);
    editor.selectionStart = editor.selectionEnd = start + variable.length;
    editor.focus();

    updateTemplatePreview(templateType);
}

// 更新模板预览
function updateTemplatePreview(templateType) {
    const editor = document.getElementById(`${templateType}-template-editor`);
    const preview = document.getElementById(`${templateType}-template-preview`);

    if (!editor || !preview) return;

    let template = editor.value;
    const data = templatePreviewData[templateType] || {};

    // 替换变量
    for (const [key, value] of Object.entries(data)) {
        template = template.replace(new RegExp(`\\{${key}\\}`, 'g'), value);
    }

    preview.textContent = template;
}

// 发送测试通知
async function testNotificationTemplate(templateType) {
    const editor = document.getElementById(`${templateType}-template-editor`);
    if (!editor) {
        showToast('编辑器不存在', 'danger');
        return;
    }

    const template = editor.value;
    if (!template.trim()) {
        showToast('模板内容不能为空', 'warning');
        return;
    }

    // 显示发送中提示
    showToast('正在发送测试通知...', 'info');

    try {
        const response = await fetch(`${apiBase}/notification-templates/test`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`,
                'Content-Type': 'application/json'
            },
            body: JSON.stringify({
                template_type: templateType,
                template: template
            })
        });

        const data = await response.json();

        if (response.ok) {
            showToast(data.message || '测试通知发送成功', 'success');
            if (data.failed_channels && data.failed_channels.length > 0) {
                console.warn('部分渠道发送失败:', data.failed_channels);
            }
        } else {
            showToast(data.detail || '测试通知发送失败', 'danger');
        }
    } catch (error) {
        console.error('发送测试通知失败:', error);
        showToast('发送测试通知失败', 'danger');
    }
}

// ================================
// 【消息通知菜单】相关功能
// ================================

// 加载消息通知配置
async function loadMessageNotifications() {
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

    // 获取所有通知配置
    const notificationsResponse = await fetch(`${apiBase}/message-notifications`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    let notifications = {};
    if (notificationsResponse.ok) {
        notifications = await notificationsResponse.json();
    }

    renderMessageNotifications(accounts, notifications);
    } catch (error) {
    console.error('加载消息通知配置失败:', error);
    showToast('加载消息通知配置失败', 'danger');
    }
}

// 渲染消息通知配置
function renderMessageNotifications(accounts, notifications) {
    const tbody = document.getElementById('notificationsTableBody');
    tbody.innerHTML = '';

    if (accounts.length === 0) {
    tbody.innerHTML = `
        <tr>
        <td colspan="4" class="text-center py-4 text-muted">
            <i class="bi bi-chat-dots fs-1 d-block mb-3"></i>
            <h5>暂无账号数据</h5>
            <p class="mb-0">请先添加账号</p>
        </td>
        </tr>
    `;
    return;
    }

    accounts.forEach(accountId => {
    const accountNotifications = notifications[accountId] || [];
    const tr = document.createElement('tr');

    let channelsList = '';
    if (accountNotifications.length > 0) {
        channelsList = accountNotifications.map(n =>
        `<span class="badge bg-${n.enabled ? 'success' : 'secondary'} me-1">${n.channel_name}</span>`
        ).join('');
    } else {
        channelsList = '<span class="text-muted">未配置</span>';
    }

    const status = accountNotifications.some(n => n.enabled) ?
        '<span class="badge bg-success">启用</span>' :
        '<span class="badge bg-secondary">禁用</span>';

    tr.innerHTML = `
        <td><strong class="text-primary">${accountId}</strong></td>
        <td>${channelsList}</td>
        <td>${status}</td>
        <td>
        <div class="btn-group" role="group">
            <button class="btn btn-sm btn-outline-primary" onclick="configAccountNotification('${accountId}')" title="配置">
            <i class="bi bi-gear"></i> 配置
            </button>
            ${accountNotifications.length > 0 ? `
            <button class="btn btn-sm btn-outline-danger" onclick="deleteAccountNotification('${accountId}')" title="删除配置">
            <i class="bi bi-trash"></i>
            </button>
            ` : ''}
        </div>
        </td>
    `;

    tbody.appendChild(tr);
    });
}

// 配置账号通知
async function configAccountNotification(accountId) {
    try {
    // 获取所有通知渠道
    const channelsResponse = await fetch(`${apiBase}/notification-channels`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (!channelsResponse.ok) {
        throw new Error('获取通知渠道失败');
    }

    const channels = await channelsResponse.json();

    if (channels.length === 0) {
        showToast('请先添加通知渠道', 'warning');
        return;
    }

    // 获取当前账号的通知配置
    const notificationResponse = await fetch(`${apiBase}/message-notifications/${accountId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    let currentNotifications = [];
    if (notificationResponse.ok) {
        currentNotifications = await notificationResponse.json();
    }

    // 填充表单
    document.getElementById('configAccountId').value = accountId;
    document.getElementById('displayAccountId').value = accountId;

    // 填充通知渠道选项
    const channelSelect = document.getElementById('notificationChannel');
    channelSelect.innerHTML = '<option value="">请选择通知渠道</option>';

    // 获取当前配置的第一个通知渠道（如果存在）
    const currentNotification = currentNotifications.length > 0 ? currentNotifications[0] : null;

    channels.forEach(channel => {
        if (channel.enabled) {
        const option = document.createElement('option');
        option.value = channel.id;
        option.textContent = `${channel.name} (${channel.config})`;
        if (currentNotification && currentNotification.channel_id === channel.id) {
            option.selected = true;
        }
        channelSelect.appendChild(option);
        }
    });

    // 设置启用状态
    document.getElementById('notificationEnabled').checked =
        currentNotification ? currentNotification.enabled : true;

    // 显示配置模态框
    const modal = new bootstrap.Modal(document.getElementById('configNotificationModal'));
    modal.show();
    } catch (error) {
    console.error('配置账号通知失败:', error);
    showToast('配置账号通知失败', 'danger');
    }
}

// 删除账号通知配置
async function deleteAccountNotification(accountId) {
    if (!confirm(`确定要删除账号 ${accountId} 的通知配置吗？`)) {
    return;
    }

    try {
    const response = await fetch(`${apiBase}/message-notifications/account/${accountId}`, {
        method: 'DELETE',
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        showToast('通知配置删除成功', 'success');
        loadMessageNotifications();
    } else {
        const error = await response.text();
        showToast(`删除失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('删除通知配置失败:', error);
    showToast('删除通知配置失败', 'danger');
    }
}

// 保存账号通知配置
async function saveAccountNotification() {
    const accountId = document.getElementById('configAccountId').value;
    const channelId = document.getElementById('notificationChannel').value;
    const enabled = document.getElementById('notificationEnabled').checked;

    if (!channelId) {
    showToast('请选择通知渠道', 'warning');
    return;
    }

    try {
    const response = await fetch(`${apiBase}/message-notifications/${accountId}`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify({
        channel_id: parseInt(channelId),
        enabled: enabled
        })
    });

    if (response.ok) {
        showToast('通知配置保存成功', 'success');
        const modal = bootstrap.Modal.getInstance(document.getElementById('configNotificationModal'));
        modal.hide();
        loadMessageNotifications();
    } else {
        const error = await response.text();
        showToast(`保存失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('保存通知配置失败:', error);
    showToast('保存通知配置失败', 'danger');
    }
}

