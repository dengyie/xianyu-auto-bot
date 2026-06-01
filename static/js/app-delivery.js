// ================================
// 【自动发货菜单】相关功能
// ================================

// 加载发货规则列表
async function loadDeliveryRules() {
    try {
    const response = await fetch(`${apiBase}/delivery-rules`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const rules = await response.json();
        renderDeliveryRulesList(rules);
        updateDeliveryStats(rules);

        // 同时加载卡券列表用于下拉选择
        loadCardsForSelect();
    } else {
        showToast('加载发货规则失败', 'danger');
    }
    } catch (error) {
    console.error('加载发货规则失败:', error);
    showToast('加载发货规则失败', 'danger');
    }
}

// 渲染发货规则列表
function renderDeliveryRulesList(rules) {
    const tbody = document.getElementById('deliveryRulesTableBody');

    if (rules.length === 0) {
    tbody.innerHTML = `
        <tr>
        <td colspan="7" class="text-center py-4 text-muted">
            <i class="bi bi-truck fs-1 d-block mb-3"></i>
            <h5>暂无发货规则</h5>
            <p class="mb-0">点击"添加规则"开始配置自动发货规则</p>
        </td>
        </tr>
    `;
    return;
    }

    tbody.innerHTML = '';

    rules.forEach(rule => {
    const tr = document.createElement('tr');

    // 状态标签
    const statusBadge = rule.enabled ?
        '<span class="badge bg-success">启用</span>' :
        '<span class="badge bg-secondary">禁用</span>';

    // 卡券类型标签
    let cardTypeBadge = '<span class="badge bg-secondary">未知</span>';
    if (rule.card_type) {
        switch(rule.card_type) {
        case 'api':
            cardTypeBadge = '<span class="badge bg-info">API接口</span>';
            break;
        case 'yifan_api':
            cardTypeBadge = '<span class="badge bg-purple">亦凡卡劵API</span>';
            break;
        case 'text':
            cardTypeBadge = '<span class="badge bg-success">固定文字</span>';
            break;
        case 'data':
            cardTypeBadge = '<span class="badge bg-warning">批量数据</span>';
            break;
        case 'image':
            cardTypeBadge = '<span class="badge bg-primary">图片</span>';
            break;
        }
    }

    tr.innerHTML = `
        <td>
        <div class="fw-bold">${rule.keyword}</div>
        ${rule.description ? `<small class="text-muted">${rule.description}</small>` : ''}
        </td>
        <td>
        <div>
            <span class="badge bg-primary">${rule.card_name || '未知卡券'}</span>
            ${rule.is_multi_spec && rule.spec_name && rule.spec_value ?
            `<br><small class="text-muted mt-1 d-block"><i class="bi bi-tags"></i> ${rule.spec_name}: ${rule.spec_value}${rule.spec_name_2 && rule.spec_value_2 ? `<br><i class="bi bi-tags"></i> ${rule.spec_name_2}: ${rule.spec_value_2}` : ''}</small>` :
            ''}
        </div>
        </td>
        <td>${cardTypeBadge}</td>
        <!-- 隐藏发货数量列 -->
        <!-- <td><span class="badge bg-info">${rule.delivery_count || 1}</span></td> -->
        <td>${statusBadge}</td>
        <td>
        <span class="badge bg-warning">${rule.delivery_times || 0}</span>
        </td>
        <td>
        <div class="btn-group" role="group">
            <button class="btn btn-sm btn-outline-primary" onclick="editDeliveryRule(${rule.id})" title="编辑">
            <i class="bi bi-pencil"></i>
            </button>
            <button class="btn btn-sm btn-outline-info" onclick="testDeliveryRule(${rule.id})" title="测试">
            <i class="bi bi-play"></i>
            </button>
            <button class="btn btn-sm btn-outline-danger" onclick="deleteDeliveryRule(${rule.id})" title="删除">
            <i class="bi bi-trash"></i>
            </button>
        </div>
        </td>
    `;

    tbody.appendChild(tr);
    });
}

// 更新发货统计
async function updateDeliveryStats(rules) {
    const totalRules = rules.length;
    const activeRules = rules.filter(rule => rule.enabled).length;
    const totalDeliveries = rules.reduce((sum, rule) => sum + (rule.delivery_times || 0), 0);

    document.getElementById('totalRules').textContent = totalRules;
    document.getElementById('activeRules').textContent = activeRules;
    document.getElementById('totalDeliveries').textContent = totalDeliveries;

    // 刷新今日发货统计
    await refreshTodayDeliveryCount();
}

// 刷新今日发货统计（独立函数，可在发货后单独调用）
async function refreshTodayDeliveryCount() {
    try {
        const response = await fetch(`${apiBase}/delivery-rules/stats`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });
        if (response.ok) {
            const stats = await response.json();
            const todayEl = document.getElementById('todayDeliveries');
            if (todayEl) {
                todayEl.textContent = stats.today_delivery_count || 0;
            }
        }
    } catch (error) {
        console.error('获取今日发货统计失败:', error);
    }
}

// 显示添加发货规则模态框
function showAddDeliveryRuleModal() {
    document.getElementById('addDeliveryRuleForm').reset();
    loadCardsForSelect(); // 加载卡券选项
    const modal = new bootstrap.Modal(document.getElementById('addDeliveryRuleModal'));
    modal.show();
}

// 加载卡券列表用于下拉选择
async function loadCardsForSelect() {
    try {
    const response = await fetch(`${apiBase}/cards`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const cards = await response.json();
        const select = document.getElementById('selectedCard');

        // 清空现有选项
        select.innerHTML = '<option value="">请选择卡券</option>';

        cards.forEach(card => {
        if (card.enabled) { // 只显示启用的卡券
            const option = document.createElement('option');
            option.value = card.id;

            // 构建显示文本
            let displayText = card.name;

            // 添加类型信息
            let typeText;
            switch(card.type) {
                case 'api':
                    typeText = 'API';
                    break;
                case 'text':
                    typeText = '固定文字';
                    break;
                case 'data':
                    typeText = '批量数据';
                    break;
                case 'image':
                    typeText = '图片';
                    break;
                default:
                    typeText = '未知类型';
            }
            displayText += ` (${typeText})`;

            // 添加规格信息
            if (card.is_multi_spec && card.spec_name && card.spec_value) {
            let specInfo = `${card.spec_name}:${card.spec_value}`;
            if (card.spec_name_2 && card.spec_value_2) {
                specInfo += `, ${card.spec_name_2}:${card.spec_value_2}`;
            }
            displayText += ` [${specInfo}]`;
            }

            option.textContent = displayText;
            select.appendChild(option);
        }
        });
    }
    } catch (error) {
    console.error('加载卡券选项失败:', error);
    }
}

// 保存发货规则
async function saveDeliveryRule() {
    try {
    const keyword = document.getElementById('productKeyword').value;
    const cardId = document.getElementById('selectedCard').value;
    const deliveryCount = document.getElementById('deliveryCount').value || 1;
    const enabled = document.getElementById('ruleEnabled').checked;
    const description = document.getElementById('ruleDescription').value;

    if (!keyword || !cardId) {
        showToast('请填写必填字段', 'warning');
        return;
    }

    const ruleData = {
        keyword: keyword,
        card_id: parseInt(cardId),
        delivery_count: parseInt(deliveryCount),
        enabled: enabled,
        description: description
    };

    const response = await fetch(`${apiBase}/delivery-rules`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify(ruleData)
    });

    if (response.ok) {
        showToast('发货规则保存成功', 'success');
        bootstrap.Modal.getInstance(document.getElementById('addDeliveryRuleModal')).hide();
        loadDeliveryRules();
    } else {
        const error = await response.text();
        showToast(`保存失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('保存发货规则失败:', error);
    showToast('保存发货规则失败', 'danger');
    }
}

// 编辑卡券
async function editCard(cardId) {
    try {
    // 获取卡券详情
    const response = await fetch(`${apiBase}/cards/${cardId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const card = await response.json();

        // 填充编辑表单
        document.getElementById('editCardId').value = card.id;
        document.getElementById('editCardName').value = card.name;
        document.getElementById('editCardType').value = card.type;
        document.getElementById('editCardDescription').value = card.description || '';
        document.getElementById('editCardDelaySeconds').value = card.delay_seconds || 0;
        document.getElementById('editCardEnabled').checked = card.enabled;

        // 填充多规格字段
        const isMultiSpec = card.is_multi_spec || false;
        document.getElementById('editIsMultiSpec').checked = isMultiSpec;
        document.getElementById('editSpecName').value = card.spec_name || '';
        document.getElementById('editSpecValue').value = card.spec_value || '';
        document.getElementById('editSpecName2').value = card.spec_name_2 || '';
        document.getElementById('editSpecValue2').value = card.spec_value_2 || '';

        // 添加调试日志
        console.log('编辑卡券 - 多规格状态:', isMultiSpec);
        console.log('编辑卡券 - 规格1名称:', card.spec_name);
        console.log('编辑卡券 - 规格1值:', card.spec_value);
        console.log('编辑卡券 - 规格2名称:', card.spec_name_2);
        console.log('编辑卡券 - 规格2值:', card.spec_value_2);

        // 根据类型填充特定字段
        if (card.type === 'api' && card.api_config) {
        document.getElementById('editApiUrl').value = card.api_config.url || '';
        document.getElementById('editApiMethod').value = card.api_config.method || 'GET';
        document.getElementById('editApiTimeout').value = card.api_config.timeout || 10;
        document.getElementById('editApiHeaders').value = card.api_config.headers || '{}';
        document.getElementById('editApiParams').value = card.api_config.params || '{}';
        } else if (card.type === 'yifan_api' && card.api_config) {
        document.getElementById('editYifanUserId').value = card.api_config.user_id || '';
        document.getElementById('editYifanUserKey').value = card.api_config.user_key || '';
        document.getElementById('editYifanGoodsId').value = card.api_config.goods_id || '';
        document.getElementById('editYifanCallbackUrl').value = card.api_config.callback_url || '';
        document.getElementById('editYifanRequireAccount').checked = card.api_config.require_account || false;
        } else if (card.type === 'text') {
        document.getElementById('editTextContent').value = card.text_content || '';
        } else if (card.type === 'data') {
        document.getElementById('editDataContent').value = card.data_content || '';
        } else if (card.type === 'image') {
        // 处理图片类型
        const currentImagePreview = document.getElementById('editCurrentImagePreview');
        const currentImg = document.getElementById('editCurrentImg');
        const noImageText = document.getElementById('editNoImageText');

        if (card.image_url) {
            // 显示当前图片
            currentImg.src = card.image_url;
            currentImagePreview.style.display = 'block';
            noImageText.style.display = 'none';
        } else {
            // 没有图片
            currentImagePreview.style.display = 'none';
            noImageText.style.display = 'block';
        }

        // 清空文件选择器和预览
        document.getElementById('editCardImageFile').value = '';
        document.getElementById('editCardImagePreview').style.display = 'none';
        }

        // 显示对应的字段
        toggleEditCardTypeFields();

        // 使用延迟调用确保DOM更新完成后再显示多规格字段
        setTimeout(() => {
        console.log('延迟调用 toggleEditMultiSpecFields');
        toggleEditMultiSpecFields();

        // 验证多规格字段是否正确显示
        const multiSpecElement = document.getElementById('editMultiSpecFields');
        const isChecked = document.getElementById('editIsMultiSpec').checked;
        console.log('多规格元素存在:', !!multiSpecElement);
        console.log('多规格开关状态:', isChecked);
        console.log('多规格字段显示状态:', multiSpecElement ? multiSpecElement.style.display : 'element not found');
        }, 100);

        // 显示模态框
        const modal = new bootstrap.Modal(document.getElementById('editCardModal'));
        modal.show();
    } else {
        showToast('获取卡券详情失败', 'danger');
    }
    } catch (error) {
    console.error('获取卡券详情失败:', error);
    showToast('获取卡券详情失败', 'danger');
    }
}

// 切换编辑卡券类型字段显示
function toggleEditCardTypeFields() {
    const cardType = document.getElementById('editCardType').value;

    document.getElementById('editApiFields').style.display = cardType === 'api' ? 'block' : 'none';
    document.getElementById('editYifanApiFields').style.display = cardType === 'yifan_api' ? 'block' : 'none';
    document.getElementById('editTextFields').style.display = cardType === 'text' ? 'block' : 'none';
    document.getElementById('editDataFields').style.display = cardType === 'data' ? 'block' : 'none';
    document.getElementById('editImageFields').style.display = cardType === 'image' ? 'block' : 'none';

    // 如果是API类型，初始化API方法监听
    if (cardType === 'api') {
        toggleEditApiParamsHelp();
        // 添加API方法变化监听
        const editApiMethodSelect = document.getElementById('editApiMethod');
        if (editApiMethodSelect) {
            editApiMethodSelect.removeEventListener('change', toggleEditApiParamsHelp);
            editApiMethodSelect.addEventListener('change', toggleEditApiParamsHelp);
        }
    }
}

// 切换编辑API参数提示显示
function toggleEditApiParamsHelp() {
    const apiMethod = document.getElementById('editApiMethod').value;
    const editPostParamsHelp = document.getElementById('editPostParamsHelp');

    if (editPostParamsHelp) {
        editPostParamsHelp.style.display = apiMethod === 'POST' ? 'block' : 'none';

        // 如果显示参数提示，添加点击事件
        if (apiMethod === 'POST') {
            initParamClickHandlers('editApiParams', 'editPostParamsHelp');
        }
    }
}

// 更新卡券
async function updateCard() {
    try {
    const cardId = document.getElementById('editCardId').value;
    const cardType = document.getElementById('editCardType').value;
    const cardName = document.getElementById('editCardName').value;

    if (!cardType || !cardName) {
        showToast('请填写必填字段', 'warning');
        return;
    }

    // 检查多规格设置
    const isMultiSpec = document.getElementById('editIsMultiSpec').checked;
    const specName = document.getElementById('editSpecName').value;
    const specValue = document.getElementById('editSpecValue').value;
    const specName2 = document.getElementById('editSpecName2').value;
    const specValue2 = document.getElementById('editSpecValue2').value;

    // 调试日志
    console.log('[DEBUG] 更新卡券 - isMultiSpec:', isMultiSpec);
    console.log('[DEBUG] 更新卡券 - specName:', specName);
    console.log('[DEBUG] 更新卡券 - specValue:', specValue);
    console.log('[DEBUG] 更新卡券 - specName2:', specName2);
    console.log('[DEBUG] 更新卡券 - specValue2:', specValue2);

    // 验证多规格字段
    if (isMultiSpec && (!specName || !specValue)) {
        showToast('多规格卡券必须填写规格1名称和规格1值', 'warning');
        return;
    }

    const cardData = {
        name: cardName,
        type: cardType,
        description: document.getElementById('editCardDescription').value,
        delay_seconds: parseInt(document.getElementById('editCardDelaySeconds').value) || 0,
        enabled: document.getElementById('editCardEnabled').checked,
        is_multi_spec: isMultiSpec,
        spec_name: isMultiSpec ? specName : null,
        spec_value: isMultiSpec ? specValue : null,
        spec_name_2: isMultiSpec ? specName2 : null,
        spec_value_2: isMultiSpec ? specValue2 : null
    };

    // 调试日志 - 显示完整的 cardData
    console.log('[DEBUG] 发送的 cardData:', JSON.stringify(cardData, null, 2));

    // 根据类型添加特定配置
    switch(cardType) {
        case 'api':
        // 验证和解析JSON字段
        let headers = '{}';
        let params = '{}';

        try {
            const headersInput = document.getElementById('editApiHeaders').value.trim();
            if (headersInput) {
            JSON.parse(headersInput);
            headers = headersInput;
            }
        } catch (e) {
            showToast('请求头格式错误，请输入有效的JSON', 'warning');
            return;
        }

        try {
            const paramsInput = document.getElementById('editApiParams').value.trim();
            if (paramsInput) {
            JSON.parse(paramsInput);
            params = paramsInput;
            }
        } catch (e) {
            showToast('请求参数格式错误，请输入有效的JSON', 'warning');
            return;
        }

        cardData.api_config = {
            url: document.getElementById('editApiUrl').value,
            method: document.getElementById('editApiMethod').value,
            timeout: parseInt(document.getElementById('editApiTimeout').value),
            headers: headers,
            params: params
        };
        break;
        case 'yifan_api':
        // 验证必填字段
        const editYifanUserId = document.getElementById('editYifanUserId').value.trim();
        const editYifanUserKey = document.getElementById('editYifanUserKey').value.trim();
        const editYifanGoodsId = document.getElementById('editYifanGoodsId').value.trim();

        if (!editYifanUserId || !editYifanUserKey || !editYifanGoodsId) {
            showToast('请填写商户ID、商户KEY和商品ID', 'warning');
            return;
        }

        // 亦凡API配置也存储在api_config字段中
        cardData.api_config = {
            user_id: editYifanUserId,
            user_key: editYifanUserKey,
            goods_id: editYifanGoodsId,
            callback_url: document.getElementById('editYifanCallbackUrl').value.trim(),
            require_account: document.getElementById('editYifanRequireAccount').checked
        };
        break;
        case 'text':
        cardData.text_content = document.getElementById('editTextContent').value;
        break;
        case 'data':
        cardData.data_content = document.getElementById('editDataContent').value;
        break;
        case 'image':
        // 处理图片类型 - 如果有新图片则上传，否则保持原有图片
        const imageFile = document.getElementById('editCardImageFile').files[0];
        if (imageFile) {
            // 有新图片，需要上传
            await updateCardWithImage(cardId, cardData, imageFile);
            return; // 提前返回，因为上传图片是异步的
        }
        // 没有新图片，保持原有配置，继续正常更新流程
        break;
    }

    const response = await fetch(`${apiBase}/cards/${cardId}`, {
        method: 'PUT',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify(cardData)
    });

    if (response.ok) {
        showToast('卡券更新成功', 'success');
        bootstrap.Modal.getInstance(document.getElementById('editCardModal')).hide();
        loadCards();
    } else {
        const error = await response.text();
        showToast(`更新失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('更新卡券失败:', error);
    showToast('更新卡券失败', 'danger');
    }
}

// 更新带图片的卡券
async function updateCardWithImage(cardId, cardData, imageFile) {
    try {
        // 创建FormData对象
        const formData = new FormData();

        // 添加图片文件
        formData.append('image', imageFile);

        // 添加卡券数据
        Object.keys(cardData).forEach(key => {
            if (cardData[key] !== null && cardData[key] !== undefined) {
                if (typeof cardData[key] === 'object') {
                    formData.append(key, JSON.stringify(cardData[key]));
                } else {
                    formData.append(key, cardData[key]);
                }
            }
        });

        const response = await fetch(`${apiBase}/cards/${cardId}/image`, {
            method: 'PUT',
            headers: {
                'Authorization': `Bearer ${authToken}`
                // 不设置Content-Type，让浏览器自动设置multipart/form-data
            },
            body: formData
        });

        if (response.ok) {
            showToast('卡券更新成功', 'success');
            bootstrap.Modal.getInstance(document.getElementById('editCardModal')).hide();
            loadCards();
        } else {
            const error = await response.text();
            showToast(`更新失败: ${error}`, 'danger');
        }
    } catch (error) {
        console.error('更新带图片的卡券失败:', error);
        showToast('更新卡券失败', 'danger');
    }
}



// 测试卡券（占位函数）
function testCard(cardId) {
    showToast('测试功能开发中...', 'info');
}

// 删除卡券
async function deleteCard(cardId) {
    if (confirm('确定要删除这个卡券吗？删除后无法恢复！')) {
    try {
        const response = await fetch(`${apiBase}/cards/${cardId}`, {
        method: 'DELETE',
        headers: {
            'Authorization': `Bearer ${authToken}`
        }
        });

        if (response.ok) {
        showToast('卡券删除成功', 'success');
        loadCards();
        } else {
        const error = await response.text();
        showToast(`删除失败: ${error}`, 'danger');
        }
    } catch (error) {
        console.error('删除卡券失败:', error);
        showToast('删除卡券失败', 'danger');
    }
    }
}

// 编辑发货规则
async function editDeliveryRule(ruleId) {
    try {
    // 获取发货规则详情
    const response = await fetch(`${apiBase}/delivery-rules/${ruleId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const rule = await response.json();

        // 填充编辑表单
        document.getElementById('editRuleId').value = rule.id;
        document.getElementById('editProductKeyword').value = rule.keyword;
        document.getElementById('editDeliveryCount').value = rule.delivery_count || 1;
        document.getElementById('editRuleEnabled').checked = rule.enabled;
        document.getElementById('editRuleDescription').value = rule.description || '';

        // 加载卡券选项并设置当前选中的卡券
        await loadCardsForEditSelect();
        document.getElementById('editSelectedCard').value = rule.card_id;

        // 显示模态框
        const modal = new bootstrap.Modal(document.getElementById('editDeliveryRuleModal'));
        modal.show();
    } else {
        showToast('获取发货规则详情失败', 'danger');
    }
    } catch (error) {
    console.error('获取发货规则详情失败:', error);
    showToast('获取发货规则详情失败', 'danger');
    }
}

// 加载卡券列表用于编辑时的下拉选择
async function loadCardsForEditSelect() {
    try {
    const response = await fetch(`${apiBase}/cards`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const cards = await response.json();
        const select = document.getElementById('editSelectedCard');

        // 清空现有选项
        select.innerHTML = '<option value="">请选择卡券</option>';

        cards.forEach(card => {
        if (card.enabled) { // 只显示启用的卡券
            const option = document.createElement('option');
            option.value = card.id;

            // 构建显示文本
            let displayText = card.name;

            // 添加类型信息
            let typeText;
            switch(card.type) {
                case 'api':
                    typeText = 'API';
                    break;
                case 'text':
                    typeText = '固定文字';
                    break;
                case 'data':
                    typeText = '批量数据';
                    break;
                case 'image':
                    typeText = '图片';
                    break;
                default:
                    typeText = '未知类型';
            }
            displayText += ` (${typeText})`;

            // 添加规格信息
            if (card.is_multi_spec && card.spec_name && card.spec_value) {
            let specInfo = `${card.spec_name}:${card.spec_value}`;
            if (card.spec_name_2 && card.spec_value_2) {
                specInfo += `, ${card.spec_name_2}:${card.spec_value_2}`;
            }
            displayText += ` [${specInfo}]`;
            }

            option.textContent = displayText;
            select.appendChild(option);
        }
        });
    }
    } catch (error) {
    console.error('加载卡券选项失败:', error);
    }
}

// 更新发货规则
async function updateDeliveryRule() {
    try {
    const ruleId = document.getElementById('editRuleId').value;
    const keyword = document.getElementById('editProductKeyword').value;
    const cardId = document.getElementById('editSelectedCard').value;
    const deliveryCount = document.getElementById('editDeliveryCount').value || 1;
    const enabled = document.getElementById('editRuleEnabled').checked;
    const description = document.getElementById('editRuleDescription').value;

    if (!keyword || !cardId) {
        showToast('请填写必填字段', 'warning');
        return;
    }

    const ruleData = {
        keyword: keyword,
        card_id: parseInt(cardId),
        delivery_count: parseInt(deliveryCount),
        enabled: enabled,
        description: description
    };

    const response = await fetch(`${apiBase}/delivery-rules/${ruleId}`, {
        method: 'PUT',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify(ruleData)
    });

    if (response.ok) {
        showToast('发货规则更新成功', 'success');
        bootstrap.Modal.getInstance(document.getElementById('editDeliveryRuleModal')).hide();
        loadDeliveryRules();
    } else {
        const error = await response.text();
        showToast(`更新失败: ${error}`, 'danger');
    }
    } catch (error) {
    console.error('更新发货规则失败:', error);
    showToast('更新发货规则失败', 'danger');
    }
}

// 测试发货规则（占位函数）
function testDeliveryRule(ruleId) {
    showToast('测试功能开发中...', 'info');
}

// 删除发货规则
async function deleteDeliveryRule(ruleId) {
    if (confirm('确定要删除这个发货规则吗？删除后无法恢复！')) {
    try {
        const response = await fetch(`${apiBase}/delivery-rules/${ruleId}`, {
        method: 'DELETE',
        headers: {
            'Authorization': `Bearer ${authToken}`
        }
        });

        if (response.ok) {
        showToast('发货规则删除成功', 'success');
        loadDeliveryRules();
        } else {
        const error = await response.text();
        showToast(`删除失败: ${error}`, 'danger');
        }
    } catch (error) {
        console.error('删除发货规则失败:', error);
        showToast('删除发货规则失败', 'danger');
    }
    }
}



