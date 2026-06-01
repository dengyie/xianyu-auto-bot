// ==================== 在线客服IM功能 ====================

let chatCurrentCookieId = '';
let chatCurrentChatId = '';
let chatCurrentToUserId = '';
let chatCurrentSenderName = '';
let chatCurrentItemId = '';
let chatSessionsCache = [];
let chatOldestMsgId = null;
let chatSseAbortController = null;
let chatSseRetryCount = 0;
let chatSseShouldRun = false;

function buildSafeCheckboxId(prefix, rawValue) {
    const normalized = String(rawValue || '')
        .trim()
        .replace(/[^a-zA-Z0-9_-]+/g, '_')
        .replace(/^_+|_+$/g, '');
    return `${prefix}_${normalized || 'item'}`;
}

function normalizeChatSessionPreview(content, contentType) {
    if (Number(contentType) === 2) return '[图片]';
    const text = String(content || '').trim();
    if (!text) return '[暂无文本内容]';
    const hiddenMarkers = new Set(['[系统消息]', '[空消息]', '点击补拉该会话历史消息']);
    if (hiddenMarkers.has(text)) return '[系统/占位消息]';
    return text;
}

function resolveSessionDisplayName(session) {
    return session?.fish_nick
        || session?.buyer_name_resolved
        || session?.buyer_name
        || (session?.direction === 2 ? (session?.sender_name || session?.sender_id || session?.chat_id) : (session?.sender_name || session?.chat_id))
        || session?.chat_id
        || '-';
}

function resolveSessionAvatar(session) {
    if (session?.avatar) {
        return { type: 'image', value: session.avatar };
    }
    const displayName = resolveSessionDisplayName(session);
    return { type: 'text', value: (displayName || '?').charAt(0).toUpperCase() };
}

function resolveSessionPreview(session) {
    return session?.item_title
        || session?.order_status_name
        || normalizeChatSessionPreview(session?.content, session?.content_type);
}

function getChatSessionState(session) {
    return {
        tag: '',
        preview: resolveSessionPreview(session),
        submeta: session?.order_status_name || session?.item_tips || '',
        className: ''
    };
}

function updateChatHeaderMeta(session) {
    const headerItemId = document.getElementById('chatHeaderItemId');
    const headerMeta = document.getElementById('chatHeaderMeta');
    if (headerItemId) {
        headerItemId.textContent = session?.item_id ? `商品: ${session.item_id}` : '';
    }
    if (!headerMeta) return;
    const parts = [];
    if (session?.item_title) parts.push(session.item_title);
    if (session?.item_price) parts.push(`￥${session.item_price}`);
    if (session?.order_status_name) parts.push(session.order_status_name);
    if (session?.item_tips) parts.push(session.item_tips);
    headerMeta.textContent = parts.join(' · ');
}

function scoreChatSession(session) {
    const preview = normalizeChatSessionPreview(session?.content, session?.content_type);
    let score = 0;
    if (preview !== '[系统/占位消息]' && preview !== '[暂无文本内容]') score += 20;
    if (String(session?.buyer_name || '').trim()) score += 8;
    if (String(session?.item_id || '').trim()) score += 4;
    if (String(session?.created_at || '').trim()) score += 2;
    return score;
}

function sortChatSessions(sessions) {
    return [...(sessions || [])].sort((a, b) => {
        const scoreDiff = scoreChatSession(b) - scoreChatSession(a);
        if (scoreDiff !== 0) return scoreDiff;
        return String(b?.created_at || '').localeCompare(String(a?.created_at || ''));
    });
}

function mergeChatSessionLists(primarySessions, secondarySessions) {
    const merged = [];
    const seen = new Set();
    [...(primarySessions || []), ...(secondarySessions || [])].forEach(session => {
        const chatId = String(session?.chat_id || '').trim();
        if (!chatId || seen.has(chatId)) return;
        seen.add(chatId);
        merged.push(session);
    });
    return sortChatSessions(merged);
}

async function refreshChatAccounts() {
    const body = document.getElementById('chatAccountsBody');
    if (!body) return;
    body.innerHTML = '<div class="text-center text-muted py-4 small"><div class="spinner-border spinner-border-sm"></div></div>';
    try {
        const result = await fetchJSON(`${apiBase}/api/chat/accounts`);
        if (!result.success) {
            body.innerHTML = '<div class="text-center text-muted py-4 small">加载失败</div>';
            return;
        }
        const accounts = result.accounts || [];
        if (!accounts.length) {
            body.innerHTML = '<div class="text-center text-muted py-4 small">暂无可用账号</div>';
            return;
        }
        body.innerHTML = '';
        accounts.forEach(account => {
            const div = document.createElement('div');
            div.className = 'chat-account-item' + (account.id === chatCurrentCookieId ? ' active' : '');
            div.innerHTML = `<div class="chat-account-dot ${account.connected ? 'online' : 'offline'}"></div><div class="chat-account-name" title="${escapeHtml(account.id)}">${escapeHtml(account.name || account.id)}</div>`;
            div.onclick = () => selectChatAccount(account.id);
            body.appendChild(div);
        });
    } catch (error) {
        console.error('加载账号列表失败:', error);
        body.innerHTML = '<div class="text-center text-muted py-4 small">加载失败</div>';
    }
}

async function selectChatAccount(cookieId) {
    chatCurrentCookieId = cookieId;
    chatCurrentChatId = '';
    chatCurrentToUserId = '';
    chatCurrentSenderName = '';
    chatCurrentItemId = '';
    chatOldestMsgId = null;
    const placeholder = document.getElementById('chatMainPlaceholder');
    const active = document.getElementById('chatActiveArea');
    if (placeholder) placeholder.classList.remove('d-none');
    if (active) active.classList.add('d-none');
    hideReplyPanel();
    await refreshChatAccounts();
    await refreshChatSessions();
}

async function refreshChatSessions() {
    const body = document.getElementById('chatSessionsBody');
    if (!body) return;
    if (!chatCurrentCookieId) {
        body.innerHTML = '<div class="text-center text-muted py-4 small">请先选择账号</div>';
        chatSessionsCache = [];
        return;
    }
    body.innerHTML = '<div class="text-center text-muted py-4 small"><div class="spinner-border spinner-border-sm"></div></div>';
    try {
        const result = await fetchJSON(`${apiBase}/api/chat/sessions?cookie_id=${encodeURIComponent(chatCurrentCookieId)}&include_order_fallback=true&limit=120`);
        if (!result.success) {
            body.innerHTML = '<div class="text-center text-muted py-4 small">加载失败</div>';
            return;
        }
        chatSessionsCache = sortChatSessions(result.sessions || []);
        chatSessionsCache = await enrichSessionsWithOrdersFallback(chatSessionsCache);
        if (!chatSessionsCache.length) {
            body.innerHTML = '<div class="text-center text-muted py-4 small">暂无会话记录；若该账号已有订单，会自动显示可补拉历史的会话入口</div>';
            return;
        }
        renderChatSessions(chatSessionsCache);
        mergeHydrationFallbackSessions();
    } catch (error) {
        console.error('获取会话列表失败:', error);
        body.innerHTML = '<div class="text-center text-muted py-4 small">加载失败</div>';
    }
}

function buildChatSessionsFromOrdersData(orders, cookieId) {
    const sessions = [];
    const seen = new Set();
    (orders || []).forEach(order => {
        if (String(order.cookie_id || '') !== String(cookieId || '')) return;
        const sid = String(order.sid || '').trim();
        if (!sid) return;
        const chatId = sid.split('@')[0];
        if (!chatId || seen.has(chatId)) return;
        seen.add(chatId);
        sessions.push({
            chat_id: chatId,
            sender_id: order.buyer_id || '',
            buyer_id: order.buyer_id || '',
            sender_name: order.buyer_nick || order.buyer_id || chatId,
            buyer_name: order.buyer_nick || '',
            content: '',
            content_type: 1,
            item_id: order.item_id || '',
            direction: 2,
            created_at: order.updated_at || order.platform_created_at || order.created_at || '',
        });
    });
    sessions.sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')));
    return sessions;
}

async function enrichSessionsWithOrdersFallback(existingSessions) {
    const sessions = Array.isArray(existingSessions) ? [...existingSessions] : [];
    if (!chatCurrentCookieId) return sessions;
    const hasOnlySparseLocalSessions = sessions.length <= 1;
    if (!hasOnlySparseLocalSessions) {
        return sortChatSessions(sessions);
    }
    try {
        const ordersResult = await fetchJSON(`${apiBase}/api/orders`);
        const orderSessions = buildChatSessionsFromOrdersData(ordersResult?.data || [], chatCurrentCookieId);
        return mergeChatSessionLists(sessions, orderSessions);
    } catch (error) {
        console.debug('从订单补充会话列表失败:', error);
    }
    return sortChatSessions(sessions);
}

function renderChatSessions(sessions) {
    const body = document.getElementById('chatSessionsBody');
    if (!body) return;
    if (!sessions.length) {
        body.innerHTML = '<div class="text-center text-muted py-4 small">暂无会话</div>';
        return;
    }
    body.innerHTML = '';
    sessions.forEach(session => {
        const div = document.createElement('div');
        div.className = 'chat-session-item' + (session.chat_id === chatCurrentChatId ? ' active' : '');
        const displayName = resolveSessionDisplayName(session);
        const avatar = resolveSessionAvatar(session);
        const sessionState = getChatSessionState(session);
        const preview = String(sessionState.preview || resolveSessionPreview(session)).substring(0, 30);
        const baseSubMeta = String(sessionState.submeta || '').trim();
        const priceMeta = session.item_price ? `<span class="chat-session-price">￥${escapeHtml(String(session.item_price))}</span>` : '';
        div.innerHTML = `
            <div class="chat-session-avatar">${avatar.type === 'image' ? `<img src="${escapeHtml(avatar.value)}" alt="avatar" class="chat-session-avatar-image">` : escapeHtml(avatar.value)}</div>
            <div class="chat-session-info">
                <div class="chat-session-name">${escapeHtml(displayName)}</div>
                <div class="chat-session-preview">${escapeHtml(preview)}</div>
                <div class="chat-session-submeta">${escapeHtml(baseSubMeta)}${priceMeta}</div>
            </div>
            <div class="chat-session-time">${escapeHtml(formatChatTime(session.created_at))}</div>
        `;
        div.onclick = () => selectChatSession(session);
        body.appendChild(div);
    });
}

function mergeHydrationFallbackSessions() {
    if (!chatCurrentCookieId) return;
    fetchJSON(`${apiBase}/api/chat/sessions?cookie_id=${encodeURIComponent(chatCurrentCookieId)}&include_order_fallback=true&limit=120`)
        .then(result => {
            if (!result?.success || !Array.isArray(result.sessions)) return;
            const mergedSessions = mergeChatSessionLists(chatSessionsCache, result.sessions);
            if (mergedSessions.length !== chatSessionsCache.length) {
                chatSessionsCache = mergedSessions;
                renderChatSessions(chatSessionsCache);
            }

            if (chatSessionsCache.length <= 1) {
                enrichSessionsWithOrdersFallback(chatSessionsCache)
                    .then(mergedSessions => {
                        if (Array.isArray(mergedSessions) && mergedSessions.length > chatSessionsCache.length) {
                            chatSessionsCache = sortChatSessions(mergedSessions);
                            renderChatSessions(chatSessionsCache);
                        }
                    })
                    .catch(error => {
                        console.debug('订单会话增强失败:', error);
                    });
            }
        })
        .catch(error => {
            console.debug('补充可补拉会话失败:', error);
        });
}

function filterChatSessions() {
    const keyword = (document.getElementById('chatSearchInput')?.value || '').toLowerCase();
    if (!keyword) {
        renderChatSessions(sortChatSessions(chatSessionsCache));
        return;
    }
    renderChatSessions(sortChatSessions(chatSessionsCache.filter(session =>
        String(session.sender_name || '').toLowerCase().includes(keyword)
        || String(session.buyer_name || '').toLowerCase().includes(keyword)
        || String(session.chat_id || '').includes(keyword)
        || String(normalizeChatSessionPreview(session.content, session.content_type) || '').toLowerCase().includes(keyword)
    )));
}

async function selectChatSession(session) {
    session = { ...session, content: normalizeChatSessionPreview(session?.content, session?.content_type) };
    chatCurrentChatId = session.chat_id;
    chatCurrentToUserId = session.buyer_id || (session.direction === 2 ? (session.sender_id || '') : '');
    chatCurrentSenderName = resolveSessionDisplayName(session);
    chatCurrentItemId = session.item_id || '';
    chatOldestMsgId = null;

    const placeholder = document.getElementById('chatMainPlaceholder');
    const active = document.getElementById('chatActiveArea');
    if (placeholder) placeholder.classList.add('d-none');
    if (active) active.classList.remove('d-none');

    const headerName = document.getElementById('chatHeaderName');
    if (headerName) headerName.textContent = chatCurrentSenderName;
    updateChatHeaderMeta(session);

    renderChatSessions(chatSessionsCache);
    await loadChatMessages(false);

    try {
        const result = await fetchJSON(`${apiBase}/api/chat/messages?cookie_id=${encodeURIComponent(chatCurrentCookieId)}&chat_id=${encodeURIComponent(chatCurrentChatId)}&limit=50`);
        if (result.success && Array.isArray(result.messages)) {
            const buyerMessage = result.messages.find(message => message.direction === 2);
            if (buyerMessage) {
                if (!chatCurrentToUserId) chatCurrentToUserId = buyerMessage.sender_id;
                if (!chatCurrentSenderName || chatCurrentSenderName === chatCurrentChatId) {
                    chatCurrentSenderName = buyerMessage.sender_name || buyerMessage.sender_id || chatCurrentChatId;
                    if (headerName) headerName.textContent = chatCurrentSenderName;
                }
            }
            const messageWithItem = [...result.messages].reverse().find(message => {
                const itemId = String(message.item_id || '');
                return itemId && itemId !== 'None' && !itemId.startsWith('auto_');
            });
            if (messageWithItem) {
                chatCurrentItemId = messageWithItem.item_id;
                updateChatHeaderMeta({ ...session, item_id: chatCurrentItemId });
            }
        }
    } catch (error) {
        console.debug('补充会话信息失败:', error);
    }

    if (!document.getElementById('chatReplyPanel')?.classList.contains('d-none') && chatCurrentItemId) {
        await loadItemKeywords();
    }

    document.getElementById('chatInputBox')?.focus();
}

function shouldForceHydrateSession(session) {
    return false;
}

function shouldRebuildEmptySession(messages) {
    return false;
}

function renderChatEmptyState(session) {
    return `<div class="text-center text-muted py-4"><div class="small">暂无消息记录</div></div>`;
}

async function loadChatMessages(append = false) {
    if (!chatCurrentCookieId || !chatCurrentChatId) return;
    const area = document.getElementById('chatMessagesArea');
    if (!area) return;
    if (!append) {
        area.innerHTML = '<div class="text-center text-muted py-4"><div class="spinner-border spinner-border-sm"></div></div>';
    }

    try {
        let url = `${apiBase}/api/chat/messages?cookie_id=${encodeURIComponent(chatCurrentCookieId)}&chat_id=${encodeURIComponent(chatCurrentChatId)}&limit=50`;
        if (append && chatOldestMsgId) {
            url += `&before_id=${chatOldestMsgId}`;
        }
        const result = await fetchJSON(url);
        if (!result.success) {
            if (!append) area.innerHTML = '<div class="text-center text-muted py-4">加载失败</div>';
            return;
        }
        const messages = result.messages || [];
        if (messages.length > 0) {
            chatOldestMsgId = messages[0].id;
        }
        if (append) {
            const previousHeight = area.scrollHeight;
            area.insertAdjacentHTML('afterbegin', renderChatMessages(messages));
            area.scrollTop = area.scrollHeight - previousHeight;
        } else {
            if (messages.length) {
                area.innerHTML = renderChatMessages(messages);
            } else {
                const currentSession = chatSessionsCache.find(item => item.chat_id === chatCurrentChatId) || {};
                area.innerHTML = renderChatEmptyState(currentSession);
            }
            area.scrollTop = area.scrollHeight;
        }
    } catch (error) {
        console.error('加载消息失败:', error);
        if (!append) area.innerHTML = '<div class="text-center text-muted py-4">加载失败</div>';
    }
}

function loadMoreChatMessages() {
    loadChatMessages(true);
}

function renderChatMessages(messages) {
    let html = '';
    let lastDate = '';
    messages.forEach(message => {
        const dateStr = String(message.created_at || '').substring(0, 10);
        if (dateStr && dateStr !== lastDate) {
            lastDate = dateStr;
            html += `<div class="chat-date-divider"><span>${escapeHtml(dateStr)}</span></div>`;
        }
        const isOutgoing = message.direction === 1;
        const timeStr = String(message.created_at || '').substring(11, 16);
        let contentHtml = '';
        const extra = (() => {
            try {
                return message.extra_json ? JSON.parse(message.extra_json) : null;
            } catch (error) {
                return null;
            }
        })();
        const itemShare = extra?.item_share || null;
        if (message.content_type === 2 && message.image_url) {
            contentHtml = `<img src="${escapeHtml(message.image_url)}" class="chat-msg-image" onclick="window.open(this.src, '_blank')">`;
            if (message.content && message.content !== '[图片]') {
                contentHtml += `<div class="mt-1">${escapeHtml(message.content)}</div>`;
            }
        } else if (message.content_type === 3) {
            const poster = message.image_url ? `<img src="${escapeHtml(message.image_url)}" class="chat-msg-image mb-2" onclick="window.open('${escapeHtml(message.media_url || message.image_url)}', '_blank')">` : '';
            const link = message.media_url ? `<a href="${escapeHtml(message.media_url)}" target="_blank" rel="noopener noreferrer" class="chat-rich-link">打开视频</a>` : '';
            contentHtml = `<div class="chat-rich-card">${poster}<div class="chat-rich-title">${escapeHtml(message.content || '[视频]')}</div>${link}</div>`;
        } else if (message.content_type === 4) {
            const linkTarget = message.link_url || extra?.payload?.targetUrl || '#';
            contentHtml = `<div class="chat-rich-card"><div class="chat-rich-title">${escapeHtml(message.content || '[链接]')}</div><a href="${escapeHtml(linkTarget)}" target="_blank" rel="noopener noreferrer" class="chat-rich-link">打开链接</a></div>`;
        } else if (message.content_type === 5) {
            const linkTarget = message.link_url || '#';
            const image = itemShare?.image_url || message.image_url;
            contentHtml = `<div class="chat-rich-card chat-item-share-card">${image ? `<img src="${escapeHtml(image)}" class="chat-msg-image mb-2" onclick="window.open('${escapeHtml(linkTarget === '#' ? image : linkTarget)}', '_blank')">` : ''}<div class="chat-rich-title">${escapeHtml(itemShare?.title || message.content || '[商品分享]')}</div>${itemShare?.item_id ? `<div class="chat-rich-subtitle">商品ID: ${escapeHtml(String(itemShare.item_id))}</div>` : ''}${linkTarget && linkTarget !== '#' ? `<a href="${escapeHtml(linkTarget)}" target="_blank" rel="noopener noreferrer" class="chat-rich-link">查看商品</a>` : ''}</div>`;
        } else if (message.content_type === 6) {
            const buttonText = extra?.button_text;
            const linkTarget = message.link_url || '#';
            contentHtml = `<div class="chat-rich-card"><div class="chat-rich-title">${escapeHtml(extra?.title || message.content || '[系统卡片]')}</div>${buttonText ? `<div class="chat-rich-subtitle">${escapeHtml(buttonText)}</div>` : ''}${linkTarget && linkTarget !== '#' ? `<a href="${escapeHtml(linkTarget)}" target="_blank" rel="noopener noreferrer" class="chat-rich-link">打开卡片</a>` : ''}</div>`;
        } else {
            const normalizedContent = String(message.content || '').trim() || '[空消息]';
            contentHtml = escapeHtml(normalizedContent).replace(/\n/g, '<br>');
        }
        const sourceHtml = message.reply_source ? `<span class="chat-msg-source">${escapeHtml(message.reply_source)}</span>` : '';
        html += `<div class="chat-msg-row ${isOutgoing ? 'outgoing' : 'incoming'}"><div><div class="chat-msg-bubble">${contentHtml}</div><div class="chat-msg-meta">${escapeHtml(timeStr)}${sourceHtml}</div></div></div>`;
    });
    return html;
}

async function sendChatMessage() {
    const input = document.getElementById('chatInputBox');
    const message = String(input?.value || '').trim();
    if (!message) return;
    if (!chatCurrentCookieId || !chatCurrentChatId || !chatCurrentToUserId) {
        showToast('无法发送：缺少会话信息', 'warning');
        return;
    }
    const button = document.getElementById('chatSendBtn');
    if (button) {
        button.disabled = true;
        button.textContent = '...';
    }
    try {
        const result = await fetchJSON(`${apiBase}/api/chat/send`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                cookie_id: chatCurrentCookieId,
                chat_id: chatCurrentChatId,
                to_user_id: chatCurrentToUserId,
                message,
            })
        });
        if (result.success) {
            if (input) input.value = '';
        } else {
            showToast(result.detail || result.message || '发送失败', 'danger');
        }
    } catch (error) {
        console.error('发送消息失败:', error);
        showToast('发送消息失败', 'danger');
    } finally {
        if (button) {
            button.disabled = false;
            button.textContent = '发送';
        }
    }
}

function appendChatMessage(message) {
    const area = document.getElementById('chatMessagesArea');
    if (!area) return;
    const emptyHint = area.querySelector('.text-center.text-muted');
    if (emptyHint) emptyHint.remove();
    area.insertAdjacentHTML('beforeend', renderChatMessages([message]));
    area.scrollTop = area.scrollHeight;
}

function handleChatInputKeydown(event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendChatMessage();
    }
}

function initChatSSE() {
    if (chatSseAbortController) {
        chatSseAbortController.abort();
        chatSseAbortController = null;
    }
    chatSseShouldRun = true;
    chatSseRetryCount = 0;
    connectChatStream();
}

async function connectChatStream() {
    if (!chatSseShouldRun) return;
    const controller = new AbortController();
    chatSseAbortController = controller;
    try {
        const token = getAuthToken();
        if (!token) {
            stopChatStream();
            return;
        }
        const response = await fetch(`${apiBase}/api/chat/stream`, {
            headers: {
                'Authorization': `Bearer ${token}`,
                'Accept': 'text/event-stream'
            },
            cache: 'no-store',
            signal: controller.signal
        });
        if (!response.ok) {
            if (response.status === 401) {
                stopChatStream();
                localStorage.removeItem('auth_token');
                showToast('登录已失效，请重新登录', 'warning');
                window.location.href = '/';
                return;
            }
            throw new Error(`HTTP ${response.status}`);
        }
        chatSseRetryCount = 0;
        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const parts = buffer.split('\n\n');
            buffer = parts.pop() || '';
            for (const part of parts) {
                processChatSSEEvent(part);
            }
        }
    } catch (error) {
        if (!controller.signal.aborted) {
            chatSseRetryCount += 1;
            setTimeout(() => connectChatStream(), Math.min(chatSseRetryCount * 3000, 30000));
        }
    }
}

function stopChatStream() {
    chatSseShouldRun = false;
    if (chatSseAbortController) {
        chatSseAbortController.abort();
        chatSseAbortController = null;
    }
}

function processChatSSEEvent(raw) {
    let eventType = 'message';
    let dataStr = '';
    for (const line of raw.split('\n')) {
        if (line.startsWith('event: ')) {
            eventType = line.substring(7).trim();
        } else if (line.startsWith('data: ')) {
            dataStr = line.substring(6);
        }
    }
    if (eventType === 'ping' || !dataStr) return;

    try {
        const event = JSON.parse(dataStr);
        const data = event.data || {};
        data.cookie_id = data.cookie_id || event.cookie_id;
        if (data.cookie_id !== chatCurrentCookieId) {
            return;
        }
        updateSessionFromSSE(data);
        if (data.chat_id === chatCurrentChatId) {
            appendChatMessage({
                msg_id: data.msg_id,
                chat_id: data.chat_id,
                sender_id: data.sender_id,
                sender_name: data.sender_name,
                content: data.content,
                content_type: data.content_type,
                image_url: data.image_url,
                item_id: data.item_id,
                direction: data.direction,
                reply_source: data.reply_source,
                media_url: data.media_url,
                link_url: data.link_url,
                extra_json: data.extra_json,
                created_at: data.created_at || new Date().toISOString().replace('T', ' ').substring(0, 19)
            });
        }
    } catch (error) {
        console.error('SSE解析失败:', error);
    }
}

function updateSessionFromSSE(data) {
    const preview = {
        chat_id: data.chat_id,
        sender_id: data.sender_id,
        sender_name: data.sender_name,
        buyer_id: data.direction === 2 ? data.sender_id : undefined,
        buyer_name: data.direction === 2 ? data.sender_name : undefined,
        content: data.content,
        content_type: data.content_type,
        image_url: data.image_url,
        item_id: data.item_id,
        direction: data.direction,
        created_at: data.created_at || new Date().toISOString().replace('T', ' ').substring(0, 19),
    };
    const index = chatSessionsCache.findIndex(session => session.chat_id === data.chat_id);
    if (index >= 0) {
        chatSessionsCache[index] = { ...chatSessionsCache[index], ...preview };
        chatSessionsCache.unshift(chatSessionsCache.splice(index, 1)[0]);
    } else {
        chatSessionsCache.unshift(preview);
    }
    renderChatSessions(chatSessionsCache);
}

function toggleReplyPanel() {
    const panel = document.getElementById('chatReplyPanel');
    if (!panel) return;
    panel.classList.toggle('d-none');
    if (!panel.classList.contains('d-none') && chatCurrentItemId) {
        loadItemKeywords();
    }
}

function hideReplyPanel() {
    document.getElementById('chatReplyPanel')?.classList.add('d-none');
}

async function loadItemKeywords() {
    const replyItemId = document.getElementById('replyItemId');
    const replyKeywordsList = document.getElementById('replyKeywordsList');
    const replyItemReply = document.getElementById('replyItemReply');
    if (!replyItemId || !replyKeywordsList || !replyItemReply) return;

    if (!chatCurrentCookieId || !chatCurrentItemId) {
        replyItemId.value = '未检测到商品';
        replyKeywordsList.innerHTML = '<div class="text-muted small">无商品ID</div>';
        replyItemReply.value = '';
        return;
    }

    replyItemId.value = chatCurrentItemId;
    replyKeywordsList.innerHTML = '<div class="text-muted small">加载中...</div>';

    try {
        const result = await fetchJSON(`${apiBase}/api/chat/keywords/${encodeURIComponent(chatCurrentCookieId)}/item/${encodeURIComponent(chatCurrentItemId)}`);
        if (!result.success) {
            replyKeywordsList.innerHTML = '<div class="text-danger small">加载失败</div>';
            return;
        }
        replyItemReply.value = result.item_reply || '';
        const keywords = result.keywords || [];
        replyKeywordsList.innerHTML = '';
        if (!keywords.length) {
            replyKeywordsList.innerHTML = '<div class="text-muted small">暂无关键词，点击“添加”创建</div>';
        } else {
            keywords.forEach(keyword => addKeywordRowWithData(keyword.keyword, keyword.reply || ''));
        }
        await loadCopyTargetItems();
    } catch (error) {
        console.error('加载商品关键词失败:', error);
        replyKeywordsList.innerHTML = '<div class="text-danger small">加载失败</div>';
    }
}

function addKeywordRow() {
    addKeywordRowWithData('', '');
}

function addKeywordRowWithData(keyword, reply) {
    const list = document.getElementById('replyKeywordsList');
    if (!list) return;
    const hint = list.querySelector('.text-muted');
    if (hint) hint.remove();
    const row = document.createElement('div');
    row.className = 'kw-row';
    row.innerHTML = `
        <input type="text" class="form-control form-control-sm" placeholder="关键词" value="${escapeHtml(keyword)}" style="flex:1;">
        <input type="text" class="form-control form-control-sm" placeholder="回复内容" value="${escapeHtml(reply)}" style="flex:2;">
        <button class="btn btn-outline-danger btn-sm" onclick="this.parentElement.remove()" title="删除"><i class="bi bi-trash"></i></button>
    `;
    list.appendChild(row);
}

async function saveItemKeywords() {
    if (!chatCurrentCookieId || !chatCurrentItemId) {
        showToast('缺少商品信息', 'warning');
        return;
    }
    const itemReply = document.getElementById('replyItemReply')?.value || '';
    const rows = document.querySelectorAll('#replyKeywordsList .kw-row');
    const keywords = [];
    rows.forEach(row => {
        const inputs = row.querySelectorAll('input');
        const keyword = inputs[0]?.value.trim();
        const reply = inputs[1]?.value.trim();
        if (keyword) {
            keywords.push({ keyword, reply, type: 'text' });
        }
    });

    try {
        const result = await fetchJSON(`${apiBase}/api/chat/keywords/${encodeURIComponent(chatCurrentCookieId)}/item/${encodeURIComponent(chatCurrentItemId)}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ keywords, item_reply: itemReply })
        });
        if (result.success) {
            showToast(`保存成功，${result.count} 条关键词`, 'success');
        } else {
            showToast(result.detail || result.message || '保存失败', 'danger');
        }
    } catch (error) {
        console.error('保存商品关键词失败:', error);
        showToast('保存失败', 'danger');
    }
}

async function loadCopyTargetItems() {
    if (!chatCurrentCookieId) return;
    const container = document.getElementById('copyTargetItems');
    if (!container) return;
    container.innerHTML = '<div class="text-muted small">加载商品...</div>';
    try {
        const result = await fetchJSON(`${apiBase}/api/chat/items/${encodeURIComponent(chatCurrentCookieId)}`);
        if (!result.success) {
            container.innerHTML = '<div class="text-muted small">加载失败</div>';
            return;
        }
        const items = (result.items || []).filter(item => item.item_id !== chatCurrentItemId);
        if (!items.length) {
            container.innerHTML = '<div class="text-muted small">无其他商品</div>';
            return;
        }
        container.innerHTML = '';
        items.forEach(item => {
            const div = document.createElement('div');
            div.className = 'copy-target-item';
            const safeValue = escapeHtml(item.item_id);
            const checkboxId = buildSafeCheckboxId('ct', item.item_id);
            div.innerHTML = `<input type="checkbox" value="${safeValue}" id="${checkboxId}"><label for="${checkboxId}">${escapeHtml(item.item_title || item.item_id)}</label>`;
            container.appendChild(div);
        });
    } catch (error) {
        console.error('加载可复用商品失败:', error);
        container.innerHTML = '<div class="text-muted small">加载失败</div>';
    }
}

async function copyKeywordsToSelected() {
    if (!chatCurrentCookieId || !chatCurrentItemId) {
        showToast('缺少源商品信息', 'warning');
        return;
    }
    const checks = document.querySelectorAll('#copyTargetItems input[type=checkbox]:checked');
    const targets = [...checks].map(check => check.value);
    if (!targets.length) {
        showToast('请先选择目标商品', 'warning');
        return;
    }
    try {
        const result = await fetchJSON(`${apiBase}/api/chat/keywords/${encodeURIComponent(chatCurrentCookieId)}/copy`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ source_item_id: chatCurrentItemId, target_item_ids: targets })
        });
        if (result.success) {
            showToast(`已复制到 ${targets.length} 个商品，共 ${result.total} 条关键词`, 'success');
        } else {
            showToast(result.detail || result.message || '复制失败', 'danger');
        }
    } catch (error) {
        console.error('复制关键词失败:', error);
        showToast('复制失败', 'danger');
    }
}

function formatChatTime(ts) {
    if (!ts) return '';
    const d = new Date(String(ts).replace(' ', 'T'));
    if (isNaN(d.getTime())) return String(ts || '').substring(11, 16);
    const now = new Date();
    if (d.toDateString() === now.toDateString()) return d.toTimeString().substring(0, 5);
    const yesterday = new Date(now);
    yesterday.setDate(yesterday.getDate() - 1);
    if (d.toDateString() === yesterday.toDateString()) return '昨天';
    return `${d.getMonth() + 1}/${d.getDate()}`;
}

function loadOnlineIm() {
    refreshChatAccounts();
    initChatSSE();
}

function loadImAccountList() {
    refreshChatAccounts();
}

function onImAccountChange() {}

function refreshImIframe() {
    refreshChatSessions();
}

function openGoofishImNewWindow() {
    window.open('https://www.goofish.com/im', '_blank');
}

function openGoofishIm() {
    openGoofishImNewWindow();
}

// ==================== 定时擦亮任务管理 ====================

const POLISH_SCHEDULE_RANDOM_MINUTES = 10;

async function loadScheduledTasks() {
    try {
        const data = await fetchJSON(`${apiBase}/scheduled-tasks`);
        if (data.success) {
            return data.tasks || [];
        }
        showToast(`加载定时任务失败: ${data.message || '未知错误'}`, 'danger');
        return [];
    } catch (error) {
        console.error('加载定时任务失败:', error);
        return [];
    }
}

async function createScheduledTask(accountId, runHour, enabled = true) {
    return fetchJSON(`${apiBase}/scheduled-tasks`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({
            account_id: accountId,
            run_hour: runHour,
            enabled,
            random_delay_max: POLISH_SCHEDULE_RANDOM_MINUTES
        })
    });
}

async function updateScheduledTask(taskId, payload) {
    return fetchJSON(`${apiBase}/scheduled-tasks/${taskId}`, {
        method: 'PUT',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(payload)
    });
}

function getPolishScheduledTask(tasks, accountId) {
    const matchedTasks = tasks
        .filter(task => task.account_id === accountId && task.task_type === 'item_polish')
        .sort((a, b) => Number(Boolean(b.enabled)) - Number(Boolean(a.enabled)) || Number(b.id) - Number(a.id));

    return matchedTasks[0] || null;
}

function formatPolishScheduleHour(hour) {
    const safeHour = Number.isFinite(Number(hour)) ? Number(hour) : 0;
    return `${String(safeHour).padStart(2, '0')}:00`;
}

function getPolishScheduleDescription(taskOrHour, randomDelayMax = POLISH_SCHEDULE_RANDOM_MINUTES) {
    const runHour = typeof taskOrHour === 'object' && taskOrHour !== null
        ? (taskOrHour.delay_minutes ?? taskOrHour.run_hour ?? 0)
        : taskOrHour;
    const safeRandomDelay = typeof taskOrHour === 'object' && taskOrHour !== null
        ? (taskOrHour.random_delay_max ?? randomDelayMax)
        : randomDelayMax;
    return `每日 ${formatPolishScheduleHour(runHour)} 后随机 0-${safeRandomDelay} 分钟擦亮一次`;
}

function closePolishScheduleModal() {
    const modalElement = document.getElementById('polishScheduleModal');
    if (!modalElement) return;

    const modalInstance = bootstrap.Modal.getInstance(modalElement);
    if (modalInstance) {
        modalInstance.hide();
    } else {
        modalElement.remove();
    }
}

function refreshPolishScheduleModalState() {
    const enabledInput = document.getElementById('polishScheduleEnabled');
    const hourSelect = document.getElementById('polishScheduleHour');
    const hint = document.getElementById('polishScheduleHint');

    if (!enabledInput || !hourSelect || !hint) return;

    const enabled = enabledInput.checked;
    const runHour = parseInt(hourSelect.value, 10);

    hint.className = `alert ${enabled ? 'alert-info' : 'alert-secondary'} py-2 mb-3`;
    hint.textContent = enabled
        ? getPolishScheduleDescription(runHour)
        : `当前已关闭，保存后会记住 ${formatPolishScheduleHour(runHour)} 的设置，但不会自动执行`;
}

async function openPolishScheduleModal(accountId) {
    try {
        const tasks = await loadScheduledTasks();
        const task = getPolishScheduledTask(tasks, accountId);
        const runHour = Number.isFinite(Number(task?.delay_minutes)) ? Number(task.delay_minutes) : 8;
        const enabled = task ? Boolean(task.enabled) : true;
        const hourOptions = Array.from({ length: 24 }, (_, hour) => `
            <option value="${hour}" ${hour === runHour ? 'selected' : ''}>${formatPolishScheduleHour(hour)}</option>
        `).join('');
        const statusText = task ? (task.enabled ? '已开启' : '未开启') : '保存后启用';
        const nextRunText = task ? (task.enabled ? (task.next_run_at || '保存后生成') : '已关闭') : '保存后生成';
        const lastRunText = task?.last_run_at || '暂无记录';

        const existingModal = document.getElementById('polishScheduleModal');
        if (existingModal) {
            existingModal.remove();
        }

        const modalHtml = `
            <div class="modal fade" id="polishScheduleModal" tabindex="-1" aria-labelledby="polishScheduleModalLabel" aria-hidden="true">
                <div class="modal-dialog modal-dialog-centered">
                    <div class="modal-content">
                        <div class="modal-header">
                            <h5 class="modal-title" id="polishScheduleModalLabel">
                                <i class="bi bi-clock-history text-info me-2"></i>定时擦亮 - ${accountId}
                            </h5>
                            <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                        </div>
                        <div class="modal-body">
                            <input type="hidden" id="polishScheduleAccountId" value="${accountId}">
                            <input type="hidden" id="polishScheduleTaskId" value="${task ? task.id : ''}">

                            <div class="form-check form-switch mb-3">
                                <input class="form-check-input" type="checkbox" role="switch" id="polishScheduleEnabled" ${enabled ? 'checked' : ''}>
                                <label class="form-check-label" for="polishScheduleEnabled">启用每日定时擦亮</label>
                            </div>

                            <div class="mb-3">
                                <label class="form-label" for="polishScheduleHour">每日几点开始擦亮</label>
                                <select class="form-select" id="polishScheduleHour">
                                    ${hourOptions}
                                </select>
                            </div>

                            <div class="alert alert-info py-2 mb-3" id="polishScheduleHint">
                                ${getPolishScheduleDescription(runHour)}
                            </div>

                            <div class="small text-muted">
                                <div>当前状态：${statusText}</div>
                                <div>下次执行：${nextRunText}</div>
                                <div>上次执行：${lastRunText}</div>
                            </div>
                        </div>
                        <div class="modal-footer">
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">取消</button>
                            <button type="button" class="btn btn-primary" onclick="savePolishSchedule()">保存设置</button>
                        </div>
                    </div>
                </div>
            </div>
        `;

        document.body.insertAdjacentHTML('beforeend', modalHtml);

        const modalElement = document.getElementById('polishScheduleModal');
        const modalInstance = new bootstrap.Modal(modalElement);
        modalElement.addEventListener('hidden.bs.modal', function () {
            modalElement.remove();
        });

        document.getElementById('polishScheduleEnabled').addEventListener('change', refreshPolishScheduleModalState);
        document.getElementById('polishScheduleHour').addEventListener('change', refreshPolishScheduleModalState);
        refreshPolishScheduleModalState();

        modalInstance.show();
    } catch (error) {
        console.error('打开定时擦亮设置失败:', error);
    }
}

async function savePolishSchedule() {
    const accountId = document.getElementById('polishScheduleAccountId')?.value;
    const taskId = parseInt(document.getElementById('polishScheduleTaskId')?.value || '', 10);
    const enabled = document.getElementById('polishScheduleEnabled')?.checked;
    const runHour = parseInt(document.getElementById('polishScheduleHour')?.value || '', 10);

    if (!accountId) {
        showToast('缺少账号ID', 'warning');
        return;
    }

    if (!Number.isInteger(runHour) || runHour < 0 || runHour > 23) {
        showToast('请选择有效的擦亮时间', 'warning');
        return;
    }

    try {
        let data;

        if (taskId) {
            data = await updateScheduledTask(taskId, {
                run_hour: runHour,
                enabled,
                random_delay_max: POLISH_SCHEDULE_RANDOM_MINUTES
            });
        } else {
            data = await createScheduledTask(accountId, runHour, enabled);
        }

        if (!data.success) {
            showToast(`保存失败: ${data.message || '未知错误'}`, 'danger');
            return;
        }

        const successMessage = enabled
            ? `${accountId} 已设置为 ${getPolishScheduleDescription(runHour)}`
            : `${accountId} 已保存 ${formatPolishScheduleHour(runHour)} 的定时擦亮时间，当前为关闭状态`;
        showToast(successMessage, 'success');
        closePolishScheduleModal();
    } catch (error) {
        console.error('保存定时擦亮设置失败:', error);
    }
}
