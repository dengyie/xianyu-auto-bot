// ================================
// 【卡券管理菜单】相关功能
// ================================

// 加载卡券列表
async function loadCards() {
    try {
    const response = await fetch(`${apiBase}/cards`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const cards = await response.json();
        renderCardsList(cards);
        updateCardsStats(cards);
    } else {
        showToast('加载卡券列表失败', 'danger');
    }
    } catch (error) {
    console.error('加载卡券列表失败:', error);
    showToast('加载卡券列表失败', 'danger');
    }
}

// 渲染卡券列表
function renderCardsList(cards) {
    const tbody = document.getElementById('cardsTableBody');

    if (cards.length === 0) {
    tbody.innerHTML = `
        <tr>
        <td colspan="8" class="text-center py-4 text-muted">
            <i class="bi bi-credit-card fs-1 d-block mb-3"></i>
            <h5>暂无卡券数据</h5>
            <p class="mb-0">点击"添加卡券"开始创建您的第一个卡券</p>
        </td>
        </tr>
    `;
    return;
    }

    tbody.innerHTML = '';

    cards.forEach(card => {
    const tr = document.createElement('tr');

    // 类型标签
    let typeBadge = '';
    switch(card.type) {
        case 'api':
        typeBadge = '<span class="badge bg-info">API接口</span>';
        break;
        case 'yifan_api':
        typeBadge = '<span class="badge bg-purple">亦凡卡劵API</span>';
        break;
        case 'text':
        typeBadge = '<span class="badge bg-success">固定文字</span>';
        break;
        case 'data':
        typeBadge = '<span class="badge bg-warning">批量数据</span>';
        break;
        case 'image':
        typeBadge = '<span class="badge bg-primary">图片</span>';
        break;
    }

    // 状态标签
    const statusBadge = card.enabled ?
        '<span class="badge bg-success">启用</span>' :
        '<span class="badge bg-secondary">禁用</span>';

    // 数据量显示
    let dataCount = '-';
    if (card.type === 'data' && card.data_content) {
        const lines = card.data_content.split('\n').filter(line => line.trim());
        dataCount = lines.length;
    } else if (card.type === 'api') {
        dataCount = '∞';
    } else if (card.type === 'text') {
        dataCount = '1';
    } else if (card.type === 'image') {
        dataCount = '1';
    }

    // 延时时间显示
    const delayDisplay = card.delay_seconds > 0 ?
        `${card.delay_seconds}秒` :
        '<span class="text-muted">立即</span>';

    // 规格信息显示
    let specDisplay = '<span class="text-muted">普通卡券</span>';
    if (card.is_multi_spec && card.spec_name && card.spec_value) {
        let specInfo = `${card.spec_name}: ${card.spec_value}`;
        if (card.spec_name_2 && card.spec_value_2) {
            specInfo += `<br>${card.spec_name_2}: ${card.spec_value_2}`;
        }
        specDisplay = `<span class="badge bg-primary">${specInfo}</span>`;
    }

    tr.innerHTML = `
        <td>
        <div class="fw-bold">${card.name}</div>
        ${card.description ? `<small class="text-muted">${card.description}</small>` : ''}
        </td>
        <td>${typeBadge}</td>
        <td>${specDisplay}</td>
        <td>${dataCount}</td>
        <td>${delayDisplay}</td>
        <td>${statusBadge}</td>
        <td>
        <small class="text-muted">${formatDateTime(card.created_at)}</small>
        </td>
        <td>
        <div class="btn-group" role="group">
            <button class="btn btn-sm btn-outline-primary" onclick="editCard(${card.id})" title="编辑">
            <i class="bi bi-pencil"></i>
            </button>
            <button class="btn btn-sm btn-outline-info" onclick="testCard(${card.id})" title="测试">
            <i class="bi bi-play"></i>
            </button>
            <button class="btn btn-sm btn-outline-danger" onclick="deleteCard(${card.id})" title="删除">
            <i class="bi bi-trash"></i>
            </button>
        </div>
        </td>
    `;

    tbody.appendChild(tr);
    });
}

// 更新卡券统计
function updateCardsStats(cards) {
    const totalCards = cards.length;
    const apiCards = cards.filter(card => card.type === 'api').length;
    const textCards = cards.filter(card => card.type === 'text').length;
    const dataCards = cards.filter(card => card.type === 'data').length;

    document.getElementById('totalCards').textContent = totalCards;
    document.getElementById('apiCards').textContent = apiCards;
    document.getElementById('textCards').textContent = textCards;
    document.getElementById('dataCards').textContent = dataCards;
}

// 显示添加卡券模态框
function showAddCardModal() {
    document.getElementById('addCardForm').reset();
    toggleCardTypeFields();
    const modal = new bootstrap.Modal(document.getElementById('addCardModal'));
    modal.show();
}

// 切换卡券类型字段显示
function toggleCardTypeFields() {
    const cardType = document.getElementById('cardType')?.value || 'text';

    // 安全地设置元素显示状态
    const setDisplay = (id, condition) => {
        const element = document.getElementById(id);
        if (element) {
            element.style.display = condition ? 'block' : 'none';
        }
    };

    setDisplay('apiFields', cardType === 'api');
    setDisplay('yifanApiFields', cardType === 'yifan_api');
    setDisplay('textFields', cardType === 'text');
    setDisplay('dataFields', cardType === 'data');
    setDisplay('imageFields', cardType === 'image');

    // 如果是API类型，初始化API方法监听
    if (cardType === 'api') {
        toggleApiParamsHelp();
        // 添加API方法变化监听
        const apiMethodSelect = document.getElementById('apiMethod');
        if (apiMethodSelect) {
            apiMethodSelect.removeEventListener('change', toggleApiParamsHelp);
            apiMethodSelect.addEventListener('change', toggleApiParamsHelp);
        }
    }
}

// 切换API参数提示显示
function toggleApiParamsHelp() {
    const apiMethodElement = document.getElementById('apiMethod');
    if (!apiMethodElement) return;
    
    const apiMethod = apiMethodElement.value;
    const postParamsHelp = document.getElementById('postParamsHelp');

    if (postParamsHelp) {
        postParamsHelp.style.display = apiMethod === 'POST' ? 'block' : 'none';

        // 如果显示参数提示，添加点击事件
        if (apiMethod === 'POST') {
            initParamClickHandlers('apiParams', 'postParamsHelp');
        }
    }
}

// 初始化参数点击处理器
function initParamClickHandlers(textareaId, containerId) {
    const container = document.getElementById(containerId);
    const textarea = document.getElementById(textareaId);

    if (!container || !textarea) return;

    // 移除现有的点击事件监听器
    const paramNames = container.querySelectorAll('.param-name');
    paramNames.forEach(paramName => {
        paramName.removeEventListener('click', handleParamClick);
    });

    // 添加新的点击事件监听器
    paramNames.forEach(paramName => {
        paramName.addEventListener('click', function() {
            handleParamClick(this, textarea);
        });
    });
}

// 处理参数点击事件
function handleParamClick(paramElement, textarea) {
    const paramName = paramElement.textContent.trim();
    const paramValue = `{${paramName}}`;

    try {
        // 获取当前textarea的值
        let currentValue = textarea.value.trim();

        // 如果当前值为空或不是有效的JSON，创建新的JSON对象
        if (!currentValue || currentValue === '{}') {
            const newJson = {};
            newJson[paramName] = paramValue;
            textarea.value = JSON.stringify(newJson, null, 2);
        } else {
            // 尝试解析现有的JSON
            let jsonObj;
            try {
                jsonObj = JSON.parse(currentValue);
            } catch (e) {
                // 如果解析失败，创建新的JSON对象
                jsonObj = {};
            }

            // 添加新参数
            jsonObj[paramName] = paramValue;

            // 更新textarea
            textarea.value = JSON.stringify(jsonObj, null, 2);
        }

        // 触发change事件
        textarea.dispatchEvent(new Event('change'));

        // 显示成功提示
        showToast(`已添加参数: ${paramName}`, 'success');

    } catch (error) {
        console.error('添加参数时出错:', error);
        showToast('添加参数失败', 'danger');
    }
}

// 切换多规格字段显示
function toggleMultiSpecFields() {
    const isMultiSpec = document.getElementById('isMultiSpec').checked;
    document.getElementById('multiSpecFields').style.display = isMultiSpec ? 'block' : 'none';
}

// 初始化卡券图片文件选择器
function initCardImageFileSelector() {
    const fileInput = document.getElementById('cardImageFile');
    if (fileInput) {
        fileInput.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (file) {
                // 验证文件类型
                if (!file.type.startsWith('image/')) {
                    showToast('❌ 请选择图片文件，当前文件类型：' + file.type, 'warning');
                    e.target.value = '';
                    hideCardImagePreview();
                    return;
                }

                // 验证文件大小（5MB）
                if (file.size > 5 * 1024 * 1024) {
                    showToast('❌ 图片文件大小不能超过 5MB，当前文件大小：' + (file.size / 1024 / 1024).toFixed(1) + 'MB', 'warning');
                    e.target.value = '';
                    hideCardImagePreview();
                    return;
                }

                // 验证图片尺寸
                validateCardImageDimensions(file, e.target);
            } else {
                hideCardImagePreview();
            }
        });
    }
}

// 验证卡券图片尺寸
function validateCardImageDimensions(file, inputElement) {
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
            hideCardImagePreview();
            return;
        }

        if (totalPixels > maxPixels) {
            showToast(`❌ 图片像素总数过大：${(totalPixels / 1024 / 1024).toFixed(1)}M像素，最大允许：8M像素`, 'warning');
            inputElement.value = '';
            hideCardImagePreview();
            return;
        }

        // 尺寸检查通过，显示预览和提示信息
        showCardImagePreview(file);

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
        hideCardImagePreview();
    };

    img.src = url;
}

// 显示卡券图片预览
function showCardImagePreview(file) {
    const reader = new FileReader();
    reader.onload = function(e) {
        const previewContainer = document.getElementById('cardImagePreview');
        const previewImg = document.getElementById('cardPreviewImg');

        previewImg.src = e.target.result;
        previewContainer.style.display = 'block';
    };
    reader.readAsDataURL(file);
}

// 隐藏卡券图片预览
function hideCardImagePreview() {
    const previewContainer = document.getElementById('cardImagePreview');
    if (previewContainer) {
        previewContainer.style.display = 'none';
    }
}

// 初始化编辑卡券图片文件选择器
function initEditCardImageFileSelector() {
    const fileInput = document.getElementById('editCardImageFile');
    if (fileInput) {
        fileInput.addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (file) {
                // 验证文件类型
                if (!file.type.startsWith('image/')) {
                    showToast('❌ 请选择图片文件，当前文件类型：' + file.type, 'warning');
                    e.target.value = '';
                    hideEditCardImagePreview();
                    return;
                }

                // 验证文件大小（5MB）
                if (file.size > 5 * 1024 * 1024) {
                    showToast('❌ 图片文件大小不能超过 5MB，当前文件大小：' + (file.size / 1024 / 1024).toFixed(1) + 'MB', 'warning');
                    e.target.value = '';
                    hideEditCardImagePreview();
                    return;
                }

                // 验证图片尺寸
                validateEditCardImageDimensions(file, e.target);
            } else {
                hideEditCardImagePreview();
            }
        });
    }
}

// 验证编辑卡券图片尺寸
function validateEditCardImageDimensions(file, inputElement) {
    const img = new Image();
    const url = URL.createObjectURL(file);

    img.onload = function() {
        const width = this.naturalWidth;
        const height = this.naturalHeight;

        URL.revokeObjectURL(url);

        // 检查尺寸限制
        if (width > 4096 || height > 4096) {
            showToast(`❌ 图片尺寸过大（${width}x${height}），最大支持 4096x4096 像素`, 'warning');
            inputElement.value = '';
            hideEditCardImagePreview();
            return;
        }

        // 显示图片预览
        showEditCardImagePreview(file);

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
        hideEditCardImagePreview();
    };

    img.src = url;
}

// 显示编辑卡券图片预览
function showEditCardImagePreview(file) {
    const reader = new FileReader();
    reader.onload = function(e) {
        const previewImg = document.getElementById('editCardPreviewImg');
        const previewContainer = document.getElementById('editCardImagePreview');

        if (previewImg && previewContainer) {
            previewImg.src = e.target.result;
            previewContainer.style.display = 'block';
        }
    };
    reader.readAsDataURL(file);
}

// 隐藏编辑卡券图片预览
function hideEditCardImagePreview() {
    const previewContainer = document.getElementById('editCardImagePreview');
    if (previewContainer) {
        previewContainer.style.display = 'none';
    }
}

// 切换编辑多规格字段显示
function toggleEditMultiSpecFields() {
    const checkbox = document.getElementById('editIsMultiSpec');
    const fieldsDiv = document.getElementById('editMultiSpecFields');

    if (!checkbox) {
    console.error('编辑多规格开关元素未找到');
    return;
    }

    if (!fieldsDiv) {
    console.error('编辑多规格字段容器未找到');
    return;
    }

    const isMultiSpec = checkbox.checked;
    const displayStyle = isMultiSpec ? 'block' : 'none';

    console.log('toggleEditMultiSpecFields - 多规格状态:', isMultiSpec);
    console.log('toggleEditMultiSpecFields - 设置显示样式:', displayStyle);

    fieldsDiv.style.display = displayStyle;

    // 验证设置是否生效
    console.log('toggleEditMultiSpecFields - 实际显示样式:', fieldsDiv.style.display);
}

// 清空添加卡券表单
function clearAddCardForm() {
    try {
    // 安全地清空表单字段
    const setElementValue = (id, value) => {
        const element = document.getElementById(id);
        if (element) {
        if (element.type === 'checkbox') {
            element.checked = value;
        } else {
            element.value = value;
        }
        } else {
        console.warn(`Element with id '${id}' not found`);
        }
    };

    const setElementDisplay = (id, display) => {
        const element = document.getElementById(id);
        if (element) {
        element.style.display = display;
        } else {
        console.warn(`Element with id '${id}' not found`);
        }
    };

    // 清空基本字段
    setElementValue('cardName', '');
    setElementValue('cardType', 'text');
    setElementValue('cardDescription', '');
    setElementValue('cardDelaySeconds', '0');
    setElementValue('isMultiSpec', false);
    setElementValue('specName', '');
    setElementValue('specValue', '');
    setElementValue('specName2', '');
    setElementValue('specValue2', '');

    // 隐藏多规格字段
    setElementDisplay('multiSpecFields', 'none');

    // 清空类型相关字段
    setElementValue('textContent', '');
    setElementValue('dataContent', '');
    setElementValue('apiUrl', '');
    setElementValue('apiMethod', 'GET');
    setElementValue('apiHeaders', '');
    setElementValue('apiParams', '');
    setElementValue('apiTimeout', '10');
    setElementValue('yifanUserId', '');
    setElementValue('yifanUserKey', '');
    setElementValue('yifanGoodsId', '');
    setElementValue('yifanCallbackUrl', '');
    setElementValue('yifanRequireAccount', false);

    // 重置字段显示
    toggleCardTypeFields();
    } catch (error) {
    console.error('清空表单时出错:', error);
    }
}

// 保存卡券
async function saveCard() {
    try {
    const cardType = document.getElementById('cardType').value;
    const cardName = document.getElementById('cardName').value;

    if (!cardType || !cardName) {
        showToast('请填写必填字段', 'warning');
        return;
    }

    // 检查多规格设置
    const isMultiSpec = document.getElementById('isMultiSpec').checked;
    const specName = document.getElementById('specName').value;
    const specValue = document.getElementById('specValue').value;
    const specName2 = document.getElementById('specName2').value;
    const specValue2 = document.getElementById('specValue2').value;

    // 调试日志
    console.log('[DEBUG] 创建卡券 - isMultiSpec:', isMultiSpec);
    console.log('[DEBUG] 创建卡券 - specName:', specName);
    console.log('[DEBUG] 创建卡券 - specValue:', specValue);
    console.log('[DEBUG] 创建卡券 - specName2:', specName2);
    console.log('[DEBUG] 创建卡券 - specValue2:', specValue2);

    // 验证多规格字段
    if (isMultiSpec && (!specName || !specValue)) {
        showToast('多规格卡券必须填写规格1名称和规格1值', 'warning');
        return;
    }

    const cardData = {
        name: cardName,
        type: cardType,
        description: document.getElementById('cardDescription').value,
        delay_seconds: parseInt(document.getElementById('cardDelaySeconds').value) || 0,
        enabled: true,
        is_multi_spec: isMultiSpec,
        spec_name: isMultiSpec ? specName : null,
        spec_value: isMultiSpec ? specValue : null,
        spec_name_2: isMultiSpec ? specName2 : null,
        spec_value_2: isMultiSpec ? specValue2 : null
    };

    // 调试日志 - 显示完整的 cardData
    console.log('[DEBUG] 创建卡券 - 发送的 cardData:', JSON.stringify(cardData, null, 2));

    // 根据类型添加特定配置
    switch(cardType) {
        case 'api':
        // 验证和解析JSON字段
        let headers = '{}';
        let params = '{}';

        try {
            const headersInput = document.getElementById('apiHeaders').value.trim();
            if (headersInput) {
            JSON.parse(headersInput); // 验证JSON格式
            headers = headersInput;
            }
        } catch (e) {
            showToast('请求头格式错误，请输入有效的JSON', 'warning');
            return;
        }

        try {
            const paramsInput = document.getElementById('apiParams').value.trim();
            if (paramsInput) {
            JSON.parse(paramsInput); // 验证JSON格式
            params = paramsInput;
            }
        } catch (e) {
            showToast('请求参数格式错误，请输入有效的JSON', 'warning');
            return;
        }

        cardData.api_config = {
            url: document.getElementById('apiUrl').value,
            method: document.getElementById('apiMethod').value,
            timeout: parseInt(document.getElementById('apiTimeout').value),
            headers: headers,
            params: params
        };
        break;
        case 'yifan_api':
        // 验证必填字段
        const yifanUserId = document.getElementById('yifanUserId').value.trim();
        const yifanUserKey = document.getElementById('yifanUserKey').value.trim();
        const yifanGoodsId = document.getElementById('yifanGoodsId').value.trim();

        if (!yifanUserId || !yifanUserKey || !yifanGoodsId) {
            showToast('请填写商户ID、商户KEY和商品ID', 'warning');
            return;
        }

        // 亦凡API配置也存储在api_config字段中
        cardData.api_config = {
            user_id: yifanUserId,
            user_key: yifanUserKey,
            goods_id: yifanGoodsId,
            callback_url: document.getElementById('yifanCallbackUrl').value.trim(),
            require_account: document.getElementById('yifanRequireAccount').checked
        };
        break;
        case 'text':
        cardData.text_content = document.getElementById('textContent').value;
        break;
        case 'data':
        cardData.data_content = document.getElementById('dataContent').value;
        break;
        case 'image':
        // 处理图片上传
        const imageFile = document.getElementById('cardImageFile').files[0];
        if (!imageFile) {
            showToast('请选择图片文件', 'warning');
            return;
        }

        // 上传图片
        const formData = new FormData();
        formData.append('image', imageFile);

        const uploadResponse = await fetch(`${apiBase}/upload-image`, {
            method: 'POST',
            headers: {
                'Authorization': `Bearer ${authToken}`
            },
            body: formData
        });

        if (!uploadResponse.ok) {
            const errorData = await uploadResponse.json();
            showToast(`图片上传失败: ${errorData.detail || '未知错误'}`, 'danger');
            return;
        }

        const uploadResult = await uploadResponse.json();
        cardData.image_url = uploadResult.image_url;
        break;
    }

    // 获取"生成对应发货规则"开关状态
    const generateDeliveryRule = document.getElementById('generateDeliveryRule').checked;
    
    const response = await fetch(`${apiBase}/cards`, {
        method: 'POST',
        headers: {
        'Authorization': `Bearer ${authToken}`,
        'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            ...cardData,
            generate_delivery_rule: generateDeliveryRule
        })
    });

    if (response.ok) {
        showToast('卡券保存成功', 'success');
        bootstrap.Modal.getInstance(document.getElementById('addCardModal')).hide();
        // 清空表单
        clearAddCardForm();
        loadCards();
    } else {
        let errorMessage = '保存失败';
        try {
        const errorData = await response.json();
        errorMessage = errorData.error || errorData.detail || errorMessage;
        } catch (e) {
        // 如果不是JSON格式，尝试获取文本
        try {
            const errorText = await response.text();
            errorMessage = errorText || errorMessage;
        } catch (e2) {
            errorMessage = `HTTP ${response.status}: ${response.statusText}`;
        }
        }
        showToast(`保存失败: ${errorMessage}`, 'danger');
    }
    } catch (error) {
    console.error('保存卡券失败:', error);
    showToast(`网络错误: ${error.message}`, 'danger');
    }
}
