// ================================
// 【自动回复菜单】相关功能
// ================================

// 刷新账号列表（用于自动回复页面）
async function refreshAccountList() {
    try {
    toggleLoading(true);

    // 获取账号列表
    const response = await fetch(`${apiBase}/cookies/details`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const accounts = await response.json();
        const select = document.getElementById('accountSelect');
        select.innerHTML = '<option value="">🔍 请选择一个账号开始配置...</option>';

        // 为每个账号获取关键词数量
        const accountsWithKeywords = await Promise.all(
        accounts.map(async (account) => {
            try {
            const keywordsResponse = await fetch(`${apiBase}/keywords/${account.id}`, {
                headers: {
                'Authorization': `Bearer ${authToken}`
                }
            });

            if (keywordsResponse.ok) {
                const keywordsData = await keywordsResponse.json();
                return {
                ...account,
                keywords: keywordsData,
                keywordCount: keywordsData.length
                };
            } else {
                return {
                ...account,
                keywordCount: 0
                };
            }
            } catch (error) {
            console.error(`获取账号 ${account.id} 关键词失败:`, error);
            return {
                ...account,
                keywordCount: 0
            };
            }
        })
        );

        // 渲染账号选项（显示所有账号，但标识禁用状态）
        if (accountsWithKeywords.length === 0) {
        select.innerHTML = '<option value="">❌ 暂无账号，请先添加账号</option>';
        return;
        }

        // 分组显示：先显示启用的账号，再显示禁用的账号
        const enabledAccounts = accountsWithKeywords.filter(account => {
        const enabled = account.enabled === undefined ? true : account.enabled;
        console.log(`账号 ${account.id} 过滤状态: enabled=${account.enabled}, 判断为启用=${enabled}`); // 调试信息
        return enabled;
        });
        const disabledAccounts = accountsWithKeywords.filter(account => {
        const enabled = account.enabled === undefined ? true : account.enabled;
        return !enabled;
        });

        // 渲染启用的账号
        enabledAccounts.forEach(account => {
        const option = document.createElement('option');
        option.value = account.id;

        // 根据关键词数量显示不同的图标和样式
        let icon = '📝';
        let status = '';
        if (account.keywordCount === 0) {
            icon = '⚪';
            status = ' (未配置)';
        } else if (account.keywordCount >= 5) {
            icon = '🟢';
            status = ` (${account.keywordCount} 个关键词)`;
        } else {
            icon = '🟡';
            status = ` (${account.keywordCount} 个关键词)`;
        }

        option.textContent = `${icon} ${account.id}${status}`;
        select.appendChild(option);
        });

        // 如果有禁用的账号，添加分隔线和禁用账号
        if (disabledAccounts.length > 0) {
        // 添加分隔线
        const separatorOption = document.createElement('option');
        separatorOption.disabled = true;
        separatorOption.textContent = `--- 禁用账号 (${disabledAccounts.length} 个) ---`;
        select.appendChild(separatorOption);

        // 渲染禁用的账号
        disabledAccounts.forEach(account => {
            const option = document.createElement('option');
            option.value = account.id;

            // 禁用账号使用特殊图标和样式
            let icon = '🔴';
            let status = '';
            if (account.keywordCount === 0) {
            status = ' (未配置) [已禁用]';
            } else {
            status = ` (${account.keywordCount} 个关键词) [已禁用]`;
            }

            option.textContent = `${icon} ${account.id}${status}`;
            option.style.color = '#6b7280';
            option.style.fontStyle = 'italic';
            select.appendChild(option);
        });
        }

        console.log('账号列表刷新完成，关键词统计:', accountsWithKeywords.map(a => ({id: a.id, keywords: a.keywordCount})));
    } else {
        showToast('获取账号列表失败', 'danger');
    }
    } catch (error) {
    console.error('刷新账号列表失败:', error);
    showToast('刷新账号列表失败', 'danger');
    } finally {
    toggleLoading(false);
    }
}

// 只刷新关键词列表（不重新加载商品列表等其他数据）
async function refreshKeywordsList() {
    if (!currentCookieId) {
        console.warn('没有选中的账号，无法刷新关键词列表');
        return;
    }

    try {
        const response = await fetch(`${apiBase}/keywords-with-item-id/${currentCookieId}`, {
            headers: {
                'Authorization': `Bearer ${authToken}`
            }
        });

        if (response.ok) {
            const data = await response.json();
            console.log('刷新关键词列表，从服务器获取的数据:', data);

            // 更新缓存数据
            keywordsData[currentCookieId] = data;

            // 只重新渲染关键词列表
            renderKeywordsList(data);

            // 清除关键词缓存
            clearKeywordCache();
        } else {
            console.error('刷新关键词列表失败:', response.status);
            showToast('刷新关键词列表失败', 'danger');
        }
    } catch (error) {
        console.error('刷新关键词列表失败:', error);
        showToast('刷新关键词列表失败', 'danger');
    }
}

// 加载账号关键词
async function loadAccountKeywords() {
    const accountId = document.getElementById('accountSelect').value;
    const keywordManagement = document.getElementById('keywordManagement');

    if (!accountId) {
    keywordManagement.style.display = 'none';
    return;
    }

    try {
    toggleLoading(true);
    currentCookieId = accountId;

    // 获取账号详情以检查状态
    const accountResponse = await fetch(`${apiBase}/cookies/details`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    let accountStatus = true; // 默认启用
    if (accountResponse.ok) {
        const accounts = await accountResponse.json();
        const currentAccount = accounts.find(acc => acc.id === accountId);
        accountStatus = currentAccount ? (currentAccount.enabled === undefined ? true : currentAccount.enabled) : true;
        console.log(`加载关键词时账号 ${accountId} 状态: enabled=${currentAccount?.enabled}, accountStatus=${accountStatus}`); // 调试信息
    }

    const response = await fetch(`${apiBase}/keywords-with-item-id/${accountId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        console.log('从服务器获取的关键词数据:', data); // 调试信息

        // 后端返回的是 [{keyword, reply, item_id, type, image_url}, ...] 格式，直接使用
        const formattedData = data;

        console.log('格式化后的关键词数据:', formattedData); // 调试信息
        keywordsData[accountId] = formattedData;
        renderKeywordsList(formattedData);

        // 加载商品列表
        await loadItemsList(accountId);

        // 更新账号徽章显示
        updateAccountBadge(accountId, accountStatus);

        keywordManagement.style.display = 'block';
    } else {
        showToast('加载关键词失败', 'danger');
    }
    } catch (error) {
    console.error('加载关键词失败:', error);
    showToast('加载关键词失败', 'danger');
    } finally {
    toggleLoading(false);
    }
}

// 更新账号徽章显示
function updateAccountBadge(accountId, isEnabled) {
    const badge = document.getElementById('currentAccountBadge');
    if (!badge) return;

    const statusIcon = isEnabled ? '🟢' : '🔴';
    const statusText = isEnabled ? '启用' : '禁用';
    const statusClass = isEnabled ? 'bg-success' : 'bg-warning';

    badge.innerHTML = `
    <span class="badge ${statusClass} me-2">
        ${statusIcon} ${accountId}
    </span>
    <small class="text-muted">
        状态: ${statusText}
        ${!isEnabled ? ' (配置的关键词不会参与自动回复)' : ''}
    </small>
    `;
}

// 显示添加关键词表单
function showAddKeywordForm() {
    const form = document.getElementById('addKeywordForm');
    form.style.display = form.style.display === 'none' ? 'block' : 'none';

    if (form.style.display === 'block') {
    document.getElementById('newKeyword').focus();
    }
}

// 加载商品列表
async function loadItemsList(accountId) {
    try {
    const response = await fetch(`${apiBase}/items/${accountId}`, {
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        const data = await response.json();
        const items = data.items || [];

        // 更新商品选择下拉框
        const selectElement = document.getElementById('newItemIdSelect');
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

        console.log(`加载了 ${items.length} 个商品到选择列表`);
    } else {
        console.warn('加载商品列表失败:', response.status);
    }
    } catch (error) {
    console.error('加载商品列表时发生错误:', error);
    }
}



// 添加或更新关键词
async function addKeyword() {
    const keywordInput = document.getElementById('newKeyword').value.trim();
    const reply = document.getElementById('newReply').value.trim();
    const selectElement = document.getElementById('newItemIdSelect');
    const selectedOptions = Array.from(selectElement.selectedOptions);

    if (!keywordInput) {
    showToast('请填写关键词', 'warning');
    return;
    }

    if (!currentCookieId) {
    showToast('请先选择账号', 'warning');
    return;
    }

    // 检查是否为编辑模式
    const isEditMode = typeof window.editingIndex !== 'undefined';
    const actionText = isEditMode ? '更新' : '添加';

    try {
    toggleLoading(true);

    // 解析多个关键词（支持竖线、换行符分隔）
    const keywords = keywordInput
        .split(/[\|\n]/)
        .map(k => k.trim())
        .filter(k => k.length > 0);
    
    if (keywords.length === 0) {
        showToast('请填写有效的关键词', 'warning');
        toggleLoading(false);
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

    // 获取当前关键词列表
    let currentKeywords = [...(keywordsData[currentCookieId] || [])];

    // 如果是编辑模式，先移除原关键词
    if (isEditMode) {
        currentKeywords.splice(window.editingIndex, 1);
    }

    // 准备要保存的关键词列表（只包含文本类型的关键字）
    let textKeywords = currentKeywords.filter(item => (item.type || 'text') === 'text');

    // 如果是编辑模式，先移除原关键词
    if (isEditMode && typeof window.editingIndex !== 'undefined') {
        // 需要重新计算在文本关键字中的索引
        const originalKeyword = keywordsData[currentCookieId][window.editingIndex];
        const textIndex = textKeywords.findIndex(item =>
            item.keyword === originalKeyword.keyword &&
            (item.item_id || '') === (originalKeyword.item_id || '')
        );
        if (textIndex !== -1) {
            textKeywords.splice(textIndex, 1);
        }
    }

    // 检查关键词是否已存在（考虑商品ID，检查所有类型的关键词）
    // 在编辑模式下，需要排除正在编辑的关键词本身
    let allKeywords = keywordsData[currentCookieId] || [];
    if (isEditMode && typeof window.editingIndex !== 'undefined') {
        // 创建一个副本，排除正在编辑的关键词
        allKeywords = allKeywords.filter((item, index) => index !== window.editingIndex);
    }

    // 检查重复关键词
    const duplicates = [];
    for (const keyword of keywords) {
        for (const itemId of itemIds) {
    const existingKeyword = allKeywords.find(item =>
        item.keyword === keyword &&
        (item.item_id || '') === (itemId || '')
    );
    if (existingKeyword) {
        const itemIdText = itemId ? `（商品ID: ${itemId}）` : '（通用关键词）';
        const typeText = existingKeyword.type === 'image' ? '图片' : '文本';
                duplicates.push(`"${keyword}" ${itemIdText}`);
            }
        }
    }

    if (duplicates.length > 0) {
        showToast(`以下关键词已存在：\n${duplicates.join('\n')}\n请修改后重试`, 'warning');
        toggleLoading(false);
        return;
    }

    // 展开添加多个关键词和多个商品ID的组合
    for (const keyword of keywords) {
        for (const itemId of itemIds) {
    const newKeyword = {
        keyword: keyword,
        reply: reply,
        item_id: itemId || ''
    };
    textKeywords.push(newKeyword);
        }
    }

    const response = await fetch(`${apiBase}/keywords-with-item-id/${currentCookieId}`, {
        method: 'POST',
        headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${authToken}`
        },
        body: JSON.stringify({
        keywords: textKeywords
        })
    });

    if (response.ok) {
        const totalAdded = keywords.length * itemIds.length;
        const keywordText = keywords.length > 1 ? `${keywords.length}个关键词` : `"${keywords[0]}"`;
        const itemText = itemIds.length > 1 ? `${itemIds.length}个商品` : (itemIds[0] ? '指定商品' : '通用');
        showToast(`✨ ${keywordText} ${actionText}成功！（共${totalAdded}条配置，应用于${itemText}）`, 'success');

        // 清空输入框并重置样式
        const keywordInputEl = document.getElementById('newKeyword');
        const replyInput = document.getElementById('newReply');
        const selectElement = document.getElementById('newItemIdSelect');
        const addBtn = document.querySelector('.add-btn');

        keywordInputEl.value = '';
        replyInput.value = '';
        if (selectElement) {
            // 清除所有选中项
            Array.from(selectElement.options).forEach(opt => opt.selected = false);
        }
        keywordInputEl.style.borderColor = '#e5e7eb';
        replyInput.style.borderColor = '#e5e7eb';
        addBtn.style.opacity = '0.7';
        addBtn.style.transform = 'scale(0.95)';

        // 如果是编辑模式，重置编辑状态
        if (isEditMode) {
        delete window.editingIndex;
        delete window.originalKeyword;

        // 恢复添加按钮
        addBtn.innerHTML = '<i class="bi bi-plus-lg"></i>添加';
        addBtn.style.background = 'linear-gradient(135deg, #10b981 0%, #059669 100%)';

        // 移除取消按钮
        const cancelBtn = document.getElementById('cancelEditBtn');
        if (cancelBtn) {
            cancelBtn.remove();
        }
        }

        // 聚焦到关键词输入框，方便连续添加
        setTimeout(() => {
        keywordInputEl.focus();
        }, 100);

        // 只刷新关键词列表，不重新加载整个界面
        await refreshKeywordsList();
    } else {
        try {
            const errorData = await response.json();
            const errorMessage = errorData.detail || '关键词添加失败';
            console.error('关键词添加失败:', errorMessage);

            // 检查是否是重复关键词的错误
            if (errorMessage.includes('关键词已存在') || errorMessage.includes('关键词重复') || errorMessage.includes('UNIQUE constraint')) {
                showToast(`❌ 关键词重复：${errorMessage}`, 'warning');
            } else {
                showToast(`❌ ${errorMessage}`, 'danger');
            }
        } catch (parseError) {
            // 如果无法解析JSON，使用原始文本
            const errorText = await response.text();
            console.error('关键词添加失败:', errorText);
            showToast('❌ 关键词添加失败', 'danger');
        }
    }
    } catch (error) {
    console.error('添加关键词失败:', error);
    showToast('添加关键词失败', 'danger');
    } finally {
    toggleLoading(false);
    }
}

// 渲染现代化关键词列表（分组显示）
function renderKeywordsList(keywords) {
    console.log('渲染关键词列表:', keywords);
    const container = document.getElementById('keywordsList');

    if (!container) {
    console.error('找不到关键词列表容器元素');
    return;
    }

    container.innerHTML = '';

    if (!keywords || keywords.length === 0) {
    console.log('关键词列表为空，显示空状态');
    container.innerHTML = `
        <div class="empty-state">
        <i class="bi bi-chat-dots"></i>
        <h3>还没有关键词</h3>
        <p>添加第一个关键词，让您的闲鱼店铺自动回复客户消息</p>
        <button class="quick-add-btn" onclick="focusKeywordInput()">
            <i class="bi bi-plus-lg me-2"></i>立即添加
        </button>
        </div>
    `;
    return;
    }

    // 按回复内容和类型分组
    const groups = groupKeywordsByReply(keywords);
    
    console.log(`开始渲染 ${groups.length} 个分组，共 ${keywords.length} 个关键词`);

    groups.forEach((group, groupIndex) => {
        const groupItem = document.createElement('div');
        groupItem.className = 'keyword-group-item';

        const isImageType = group.type === 'image';
    const typeBadge = isImageType ?
        '<span class="keyword-type-badge keyword-type-image"><i class="bi bi-image"></i> 图片</span>' :
        '<span class="keyword-type-badge keyword-type-text"><i class="bi bi-chat-text"></i> 文本</span>';

        // 回复内容显示
        let replyDisplay = '';
    if (isImageType) {
            const imageUrl = group.reply || group.image_url || '';
            replyDisplay = `
                <div class="keyword-group-reply">
                    <div class="d-flex align-items-center gap-3">
                <img src="${imageUrl}" alt="关键词图片" class="keyword-image-preview" onclick="showImageModal('${imageUrl}')">
                <div class="flex-grow-1">
                            <strong>回复图片：</strong>
                            <small class="text-muted d-block">点击图片查看大图</small>
                </div>
                    </div>
                </div>
            `;
    } else {
            replyDisplay = `
                <div class="keyword-group-reply" id="reply-display-${groupIndex}">
                    <div class="d-flex align-items-center">
                        <strong>回复内容：</strong>
                        <span class="reply-text-content">${group.reply || '<span class="text-muted">（空回复，不自动回复）</span>'}</span>
                        <button class="reply-edit-btn" onclick="editGroupReply(${groupIndex})" title="编辑回复内容">
                            <i class="bi bi-pencil"></i> 编辑
                        </button>
                    </div>
                </div>
            `;
    }

        // 关键词列表
        const keywordsList = group.keywords.map((kw, kwIndex) => `
            <span class="keyword-chip">
            <i class="bi bi-tag-fill"></i>
                ${kw}
                <button class="chip-remove-btn" onclick="deleteSpecificKeyword('${group.id}', ${kwIndex})" title="删除此关键词">
                    <i class="bi bi-x"></i>
            </button>
            </span>
        `).join('');

        // 商品列表
        const itemsList = group.items.map((itemInfo, itemIndex) => {
            const itemName = getItemName(itemInfo.item_id, itemInfo.item_title);
            const displayText = itemInfo.item_id ? 
                `${itemInfo.item_id} - ${itemName}` : 
                '通用关键词（所有商品）';
            const icon = itemInfo.item_id ? 'bi-box' : 'bi-globe';
            
            return `
                <span class="item-chip">
                    <i class="bi ${icon}"></i>
                    ${displayText}
                    <button class="chip-remove-btn" onclick="deleteSpecificItem('${group.id}', ${itemIndex})" title="删除此商品配置">
                        <i class="bi bi-x"></i>
            </button>
                </span>
            `;
        }).join('');

        groupItem.innerHTML = `
            <div class="keyword-group-header">
                <div class="keyword-group-title">
                    ${typeBadge}
                    <span class="keyword-count-badge">${group.keywords.length}个关键词 × ${group.items.length}个应用 = ${group.keywords.length * group.items.length}条配置</span>
        </div>
        </div>
            ${replyDisplay}
            <div class="keyword-group-content">
                <div class="keyword-section">
                    <div class="section-title"><i class="bi bi-tags"></i> 触发关键词</div>
                    <div class="chips-container">
                        ${keywordsList}
                    </div>
                </div>
                <div class="item-section">
                    <div class="section-title"><i class="bi bi-box-seam"></i> 应用范围</div>
                    <div class="chips-container">
                        ${itemsList}
                    </div>
                </div>
        </div>
    `;
        
        container.appendChild(groupItem);
    });

    console.log('关键词列表渲染完成');
}

// 按回复内容分组关键词
function groupKeywordsByReply(keywords) {
    const groupMap = new Map();
    
    keywords.forEach((item, index) => {
        // 使用回复内容+类型+图片URL作为分组键
        const key = `${item.type || 'text'}:${item.reply || ''}:${item.image_url || ''}`;
        
        if (!groupMap.has(key)) {
            groupMap.set(key, {
                id: `group_${groupMap.size}`,
                type: item.type || 'text',
                reply: item.reply || '',
                image_url: item.image_url || '',
                keywords: [],
                items: [],
                indices: [] // 保存原始索引
            });
        }
        
        const group = groupMap.get(key);
        
        // 添加关键词（去重）
        if (!group.keywords.includes(item.keyword)) {
            group.keywords.push(item.keyword);
        }
        
        // 添加商品（去重）
        const itemId = item.item_id || '';
        const existingItem = group.items.find(i => (i.item_id || '') === itemId);
        if (!existingItem) {
            group.items.push({
                item_id: itemId,
                item_title: item.item_title || '',  // 添加商品名称
                indices: [index]
            });
        } else {
            existingItem.indices.push(index);
        }
        
        // 记录原始索引
        group.indices.push(index);
    });
    
    return Array.from(groupMap.values());
}

// 获取商品名称（截取前30个字符）
function getItemName(itemId, itemTitle) {
    if (!itemId) return '';
    
    // 优先使用传入的商品名称
    if (itemTitle && itemTitle.trim()) {
        const name = itemTitle.trim();
        // 截取前30个字符
        return name.length > 30 ? name.substring(0, 30) + '...' : name;
    }
    
    // 从商品列表中查找商品名称
    const itemsSelect = document.getElementById('newItemIdSelect');
    if (itemsSelect) {
        const option = Array.from(itemsSelect.options).find(opt => opt.value === itemId);
        if (option && option.textContent) {
            // 提取商品名称（格式：itemId - 商品名称）
            const parts = option.textContent.split(' - ');
            if (parts.length > 1) {
                const name = parts.slice(1).join(' - ');
                // 截取前30个字符
                return name.length > 30 ? name.substring(0, 30) + '...' : name;
            }
        }
    }
    
    return '未知商品';
}

// 聚焦到关键词输入框
function focusKeywordInput() {
    document.getElementById('newKeyword').focus();
}

// 编辑分组回复内容（就地编辑）
function editGroupReply(groupIndex) {
    const keywords = keywordsData[currentCookieId] || [];
    const groups = groupKeywordsByReply(keywords);
    const group = groups[groupIndex];

    if (!group) {
        showToast('找不到关键词分组', 'warning');
        return;
    }

    const container = document.getElementById(`reply-display-${groupIndex}`);
    if (!container) return;

    // 转义HTML用于textarea
    const replyText = group.reply || '';

    container.innerHTML = `
        <strong>回复内容：</strong>
        <div class="reply-edit-area">
            <textarea class="reply-edit-textarea" id="reply-edit-input-${groupIndex}" rows="3" placeholder="请输入回复内容">${replyText}</textarea>
            <div class="reply-edit-actions">
                <button class="reply-cancel-btn" onclick="cancelGroupReplyEdit(${groupIndex})">
                    <i class="bi bi-x-lg"></i> 取消
                </button>
                <button class="reply-save-btn" onclick="saveGroupReply(${groupIndex})">
                    <i class="bi bi-check-lg"></i> 保存
                </button>
            </div>
        </div>
    `;

    // 聚焦并将光标移到末尾
    const textarea = document.getElementById(`reply-edit-input-${groupIndex}`);
    textarea.focus();
    textarea.setSelectionRange(textarea.value.length, textarea.value.length);
}

// 取消编辑分组回复
function cancelGroupReplyEdit(groupIndex) {
    const keywords = keywordsData[currentCookieId] || [];
    renderKeywordsList(keywords);
}

// 保存分组回复内容
async function saveGroupReply(groupIndex) {
    const keywords = keywordsData[currentCookieId] || [];
    const groups = groupKeywordsByReply(keywords);
    const group = groups[groupIndex];

    if (!group) {
        showToast('找不到关键词分组', 'warning');
        return;
    }

    const textarea = document.getElementById(`reply-edit-input-${groupIndex}`);
    if (!textarea) return;

    const newReply = textarea.value.trim();

    // 更新所有属于该分组的关键词回复内容
    const updatedKeywords = keywords.map((item, index) => {
        if (group.indices.includes(index)) {
            return { ...item, reply: newReply };
        }
        return item;
    });

    // 提取文本类型的关键词用于保存
    const textKeywords = updatedKeywords.filter(item => (item.type || 'text') === 'text');

    try {
        toggleLoading(true);

        const response = await fetch(`${apiBase}/keywords-with-item-id/${currentCookieId}`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({
                keywords: textKeywords
            })
        });

        if (response.ok) {
            showToast(`回复内容已更新（影响${group.indices.length}条配置）`, 'success');
            await refreshKeywordsList();
        } else {
            const errorText = await response.text();
            console.error('更新回复内容失败:', errorText);
            showToast('更新回复内容失败', 'danger');
        }
    } catch (error) {
        console.error('更新回复内容失败:', error);
        showToast('更新回复内容失败', 'danger');
    } finally {
        toggleLoading(false);
    }
}

// 编辑关键词 - 改进版本
function editKeyword(index) {
    const keywords = keywordsData[currentCookieId] || [];
    const keyword = keywords[index];

    if (!keyword) {
    showToast('关键词不存在', 'warning');
    return;
    }

    // 将关键词信息填入输入框
    document.getElementById('newKeyword').value = keyword.keyword;
    document.getElementById('newReply').value = keyword.reply;

    // 设置商品ID选择框
    const selectElement = document.getElementById('newItemIdSelect');
    if (selectElement) {
    selectElement.value = keyword.item_id || '';
    }

    // 设置编辑模式标识
    window.editingIndex = index;
    window.originalKeyword = keyword.keyword;
    window.originalItemId = keyword.item_id || '';

    // 更新按钮文本和样式
    const addBtn = document.querySelector('.add-btn');
    addBtn.innerHTML = '<i class="bi bi-check-lg"></i>更新';
    addBtn.style.background = 'linear-gradient(135deg, #f59e0b 0%, #d97706 100%)';

    // 显示取消按钮
    showCancelEditButton();

    // 聚焦到关键词输入框并选中文本
    setTimeout(() => {
    const keywordInput = document.getElementById('newKeyword');
    keywordInput.focus();
    keywordInput.select();
    }, 100);

    showToast('📝 编辑模式：修改后点击"更新"按钮保存', 'info');
}

// 显示取消编辑按钮
function showCancelEditButton() {
    // 检查是否已存在取消按钮
    if (document.getElementById('cancelEditBtn')) {
    return;
    }

    const addBtn = document.querySelector('.add-btn');
    const cancelBtn = document.createElement('button');
    cancelBtn.id = 'cancelEditBtn';
    cancelBtn.className = 'btn btn-outline-secondary';
    cancelBtn.style.marginLeft = '0.5rem';
    cancelBtn.innerHTML = '<i class="bi bi-x-lg"></i>取消';
    cancelBtn.onclick = cancelEdit;

    addBtn.parentNode.appendChild(cancelBtn);
}

// 取消编辑
function cancelEdit() {
    // 清空输入框
    document.getElementById('newKeyword').value = '';
    document.getElementById('newReply').value = '';

    // 清空商品ID选择框
    const selectElement = document.getElementById('newItemIdSelect');
    if (selectElement) {
    selectElement.value = '';
    }

    // 重置编辑状态
    delete window.editingIndex;
    delete window.originalKeyword;
    delete window.originalItemId;

    // 恢复添加按钮
    const addBtn = document.querySelector('.add-btn');
    addBtn.innerHTML = '<i class="bi bi-plus-lg"></i>添加';
    addBtn.style.background = 'linear-gradient(135deg, #10b981 0%, #059669 100%)';

    // 移除取消按钮
    const cancelBtn = document.getElementById('cancelEditBtn');
    if (cancelBtn) {
    cancelBtn.remove();
    }

    showToast('已取消编辑', 'info');
}

// 删除关键词
async function deleteKeyword(cookieId, index) {
    if (!confirm('确定要删除这个关键词吗？')) {
    return;
    }

    try {
    toggleLoading(true);

    // 使用新的删除API
    const response = await fetch(`${apiBase}/keywords/${cookieId}/${index}`, {
        method: 'DELETE',
        headers: {
        'Authorization': `Bearer ${authToken}`
        }
    });

    if (response.ok) {
        showToast('关键词删除成功', 'success');
        // 只刷新关键词列表，不重新加载整个界面
        await refreshKeywordsList();
    } else {
        const errorText = await response.text();
        console.error('关键词删除失败:', errorText);
        showToast('关键词删除失败', 'danger');
    }
    } catch (error) {
    console.error('删除关键词失败:', error);
    showToast('删除关键词删除失败', 'danger');
    } finally {
    toggleLoading(false);
    }
}

// 删除特定关键词（删除该关键词在所有商品中的配置）
async function deleteSpecificKeyword(groupId, keywordIndex) {
    const keywords = keywordsData[currentCookieId] || [];
    const groups = groupKeywordsByReply(keywords);
    const group = groups.find(g => g.id === groupId);
    
    if (!group) {
        showToast('找不到关键词分组', 'warning');
        return;
    }
    
    const targetKeyword = group.keywords[keywordIndex];
    if (!confirm(`确定要删除关键词 "${targetKeyword}" 在所有商品中的配置吗？`)) {
        return;
    }
    
    try {
        toggleLoading(true);
        
        // 找到所有需要删除的索引（从后往前删除，避免索引变化）
        const indicesToDelete = [];
        keywords.forEach((item, index) => {
            if (item.keyword === targetKeyword && 
                (item.type || 'text') === group.type &&
                (item.reply || '') === group.reply &&
                (item.image_url || '') === group.image_url) {
                indicesToDelete.push(index);
            }
        });
        
        // 从后往前删除
        indicesToDelete.sort((a, b) => b - a);
        
        for (const index of indicesToDelete) {
            const response = await fetch(`${apiBase}/keywords/${currentCookieId}/${index}`, {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${authToken}`
                }
            });
            
            if (!response.ok) {
                throw new Error('删除失败');
            }
        }
        
        showToast(`✅ 关键词 "${targetKeyword}" 已删除（${indicesToDelete.length}条配置）`, 'success');
        await refreshKeywordsList();
        
    } catch (error) {
        console.error('删除关键词失败:', error);
        showToast('删除关键词失败', 'danger');
    } finally {
        toggleLoading(false);
    }
}

// 删除特定商品的配置（删除该商品下所有关键词的配置）
async function deleteSpecificItem(groupId, itemIndex) {
    const keywords = keywordsData[currentCookieId] || [];
    const groups = groupKeywordsByReply(keywords);
    const group = groups.find(g => g.id === groupId);
    
    if (!group) {
        showToast('找不到关键词分组', 'warning');
        return;
    }
    
    const targetItem = group.items[itemIndex];
    const itemId = targetItem.item_id || '';
    const itemName = itemId ? `商品 ${itemId} - ${getItemName(itemId, targetItem.item_title)}` : '通用关键词（所有商品）';
    
    if (!confirm(`确定要删除 "${itemName}" 的所有关键词配置吗？\n将删除该商品下的 ${group.keywords.length} 个关键词。`)) {
        return;
    }
    
    try {
        toggleLoading(true);
        
        // 找到所有需要删除的索引
        const indicesToDelete = [];
        keywords.forEach((item, index) => {
            if ((item.item_id || '') === itemId &&
                (item.type || 'text') === group.type &&
                (item.reply || '') === group.reply &&
                (item.image_url || '') === group.image_url) {
                indicesToDelete.push(index);
            }
        });
        
        // 从后往前删除
        indicesToDelete.sort((a, b) => b - a);
        
        for (const index of indicesToDelete) {
            const response = await fetch(`${apiBase}/keywords/${currentCookieId}/${index}`, {
                method: 'DELETE',
                headers: {
                    'Authorization': `Bearer ${authToken}`
                }
            });
            
            if (!response.ok) {
                throw new Error('删除失败');
            }
        }
        
        showToast(`✅ ${itemName} 的配置已删除（${indicesToDelete.length}条）`, 'success');
        await refreshKeywordsList();
        
    } catch (error) {
        console.error('删除商品配置失败:', error);
        showToast('删除商品配置失败', 'danger');
    } finally {
    toggleLoading(false);
    }
}

// 显示/隐藏加载动画
function toggleLoading(show) {
    const loadingEl = document.getElementById('loading');
    if (!loadingEl) return;

    if (show) {
        loadingRequestCount += 1;

        if (loadingRequestCount === 1) {
            if (loadingShowTimer) {
                clearTimeout(loadingShowTimer);
            }

            loadingShowTimer = setTimeout(() => {
                if (loadingRequestCount > 0) {
                    loadingEl.classList.remove('d-none');
                }
                loadingShowTimer = null;
            }, LOADING_SHOW_DELAY);
        }
        return;
    }

    if (loadingRequestCount > 0) {
        loadingRequestCount -= 1;
    }

    if (loadingRequestCount === 0) {
        if (loadingShowTimer) {
            clearTimeout(loadingShowTimer);
            loadingShowTimer = null;
        }
        loadingEl.classList.add('d-none');
    }
}

