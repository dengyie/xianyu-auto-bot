// ==UserScript==
// @name         闲鱼聊天暗色模式
// @namespace    https://github.com/dengyie/xianyu-auto-bot
// @version      2.0.0
// @description  为闲鱼聊天页面添加暗色模式支持
// @author       XianyuAutoBot
// @match        https://www.goofish.com/im*
// @match        https://www.goofish.com/personal*
// @match        https://goofish.com/im*
// @grant        GM_addStyle
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @run-at       document-start
// ==/UserScript==

(function() {
    'use strict';

    const STORAGE_KEY = 'goofish_dark_mode';

    function isSystemDarkMode() {
        return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;
    }

    function getDarkModeSetting() {
        const saved = GM_getValue(STORAGE_KEY, 'auto');
        if (saved === 'auto') return isSystemDarkMode();
        return saved === 'true';
    }

    // 简洁的暗色配色 - 参考 macOS/Discord 风格
    const darkModeCSS = `
        /* ==================== 闲鱼聊天暗色模式 v2.0 ==================== */

        /* CSS 变量定义 */
        :root {
            --dark-bg-1: #1a1a1a;
            --dark-bg-2: #242424;
            --dark-bg-3: #2d2d2d;
            --dark-bg-4: #363636;
            --dark-bg-hover: #3d3d3d;
            --dark-border: #404040;
            --dark-text-1: #ffffff;
            --dark-text-2: #b3b3b3;
            --dark-text-3: #808080;
            --dark-accent: #0a84ff;
            --dark-accent-hover: #409cff;
            --dark-msg-self: #0a84ff;
            --dark-msg-other: #363636;
            --dark-success: #30d158;
            --dark-warning: #ff9f0a;
            --dark-danger: #ff453a;
        }

        /* ===== 全局重置 ===== */
        html, body {
            background-color: var(--dark-bg-1) !important;
            color: var(--dark-text-1) !important;
        }

        /* ===== 所有容器 ===== */
        body > div,
        #ice-container,
        #ice-container *,
        #root,
        [class*="content-container"],
        [class*="page-im"],
        [class*="im-main"],
        [class*="chat-container"],
        [class*="chat-main"],
        .ant-layout,
        .ant-layout-has-sider,
        .ant-layout-content,
        .ant-layout-sider,
        .ant-layout-sider-children {
            background-color: var(--dark-bg-1) !important;
        }

        /* ===== 会话列表 ===== */
        [class*="conversation-list"],
        [class*="conv-list-scroll"],
        [class*="conv-header"] {
            background-color: var(--dark-bg-1) !important;
            border-color: var(--dark-border) !important;
        }

        [class*="conversation-item"] {
            background-color: var(--dark-bg-1) !important;
            border-color: var(--dark-border) !important;
            transition: background-color 0.15s ease;
        }

        [class*="conversation-item"]:hover {
            background-color: var(--dark-bg-3) !important;
        }

        /* ===== 聊天区域 ===== */
        [class*="message-list"],
        [class*="scroll-container"],
        .infinite-scroll-component,
        .infinite-scroll-component__outerdiv {
            background-color: var(--dark-bg-2) !important;
        }

        /* ===== 消息气泡 ===== */
        [class*="message-text-left"],
        [style*="background-color: rgb(242, 242, 244)"],
        [style*="background-color: rgb(242,242,244)"] {
            background-color: var(--dark-msg-other) !important;
            color: var(--dark-text-1) !important;
            border-radius: 16px !important;
        }

        [class*="message-text-right"],
        [style*="background-color: rgb(255, 230, 15)"],
        [style*="background-color: rgb(255,230,15)"] {
            background-color: var(--dark-msg-self) !important;
            color: #ffffff !important;
            border-radius: 16px !important;
        }

        [class*="message-text"] *,
        [class*="message-text-left"] *,
        [class*="message-text-right"] * {
            color: inherit !important;
        }

        /* ===== 文字颜色 ===== */
        /* 用户昵称等主要文字 */
        [class*="text1--"],
        [class*="nick"],
        [class*="name"] {
            color: var(--dark-text-1) !important;
        }

        /* 次要文字（摘要、时间等）*/
        [class*="text2--"],
        [class*="time"],
        [class*="desc"] {
            color: var(--dark-text-2) !important;
        }

        /* 强制覆盖内联深色文字 */
        [style*="color: rgb(0, 0, 0)"],
        [style*="color: rgb(31, 31, 31)"],
        [style*="color: rgb(51, 51, 51)"],
        [style*="color:rgb(0,"],
        [style*="color:rgb(31,"],
        [style*="color:rgb(51,"] {
            color: var(--dark-text-1) !important;
        }

        [style*="color: rgb(102, 102, 102)"],
        [style*="color: rgb(153, 153, 153)"],
        [style*="color: rgb(163, 163, 163)"] {
            color: var(--dark-text-2) !important;
        }

        /* ===== 发送框 ===== */
        [class*="sendbox"] {
            background-color: var(--dark-bg-2) !important;
            border-color: var(--dark-border) !important;
        }

        [class*="sendbox-bottom"],
        [class*="sendbox-topbar"] {
            background-color: var(--dark-bg-2) !important;
        }

        /* ===== 输入框 ===== */
        textarea,
        input[type="text"],
        .ant-input,
        [class*="textarea"] {
            background-color: var(--dark-bg-3) !important;
            color: var(--dark-text-1) !important;
            border-color: var(--dark-border) !important;
            border-radius: 8px !important;
        }

        textarea::placeholder,
        .ant-input::placeholder {
            color: var(--dark-text-3) !important;
        }

        textarea:focus,
        .ant-input:focus {
            border-color: var(--dark-accent) !important;
            box-shadow: 0 0 0 2px rgba(10, 132, 255, 0.2) !important;
        }

        /* ===== 按钮 ===== */
        .ant-btn {
            background-color: var(--dark-bg-3) !important;
            color: var(--dark-text-1) !important;
            border-color: var(--dark-border) !important;
        }

        .ant-btn:hover {
            background-color: var(--dark-bg-hover) !important;
        }

        [class*="sendbox-bottom"] .ant-btn:not([disabled]) {
            background-color: var(--dark-accent) !important;
            color: #ffffff !important;
            border: none !important;
        }

        [class*="sendbox-bottom"] .ant-btn:not([disabled]):hover {
            background-color: var(--dark-accent-hover) !important;
        }

        [class*="sendbox-bottom"] .ant-btn[disabled] {
            background-color: var(--dark-bg-4) !important;
            color: var(--dark-text-3) !important;
            opacity: 0.6;
        }

        /* ===== 订单状态标签 ===== */
        [class*="order-wait"] {
            background-color: rgba(255, 159, 10, 0.15) !important;
            color: var(--dark-warning) !important;
        }

        [class*="order-success"] {
            background-color: rgba(48, 209, 88, 0.15) !important;
            color: var(--dark-success) !important;
        }

        [class*="order-close"] {
            background-color: rgba(255, 69, 58, 0.15) !important;
            color: var(--dark-danger) !important;
        }

        /* ===== 顶部商品栏 ===== */
        [class*="message-topbar"],
        [class*="messageHeadBtnContainer"] {
            background-color: var(--dark-bg-2) !important;
            border-color: var(--dark-border) !important;
        }

        /* ===== 价格 ===== */
        [class*="price"],
        [class*="money"] {
            color: var(--dark-warning) !important;
        }

        /* ===== 链接 ===== */
        a {
            color: var(--dark-accent) !important;
        }

        a:hover {
            color: var(--dark-accent-hover) !important;
        }

        /* ===== 下拉菜单 ===== */
        .ant-dropdown,
        .ant-dropdown-menu {
            background-color: var(--dark-bg-3) !important;
            border-color: var(--dark-border) !important;
        }

        .ant-dropdown-menu-item {
            color: var(--dark-text-1) !important;
        }

        .ant-dropdown-menu-item:hover {
            background-color: var(--dark-bg-hover) !important;
        }

        /* ===== 右侧工具栏 ===== */
        [class*="sidebar-container"],
        [class*="sidebar-item-wrap"],
        [class*="sidebar-item-container"] {
            background-color: var(--dark-bg-1) !important;
        }

        [class*="sidebar-item-wrap"]:hover {
            background-color: var(--dark-bg-3) !important;
        }

        [class*="sidebar-item-text"] {
            color: var(--dark-text-2) !important;
        }

        /* ===== 图标 ===== */
        .anticon,
        [class*="Icon"],
        [class*="icon"] {
            color: var(--dark-text-1) !important;
            opacity: 0.85;
        }

        /* 图片类图标（如清除未读等）增加亮度 */
        img[alt],
        img[src*="icon"],
        img[src*="Icon"],
        [class*="icon"] img,
        [class*="header"] img {
            filter: brightness(1.3) contrast(1.1) !important;
        }

        /* 头像图片不处理 */
        img[style*="border-radius"],
        [class*="avatar"] img {
            filter: none !important;
        }

        /* ===== 骨架屏 ===== */
        .ant-skeleton-avatar,
        .ant-skeleton-button {
            background: linear-gradient(90deg, var(--dark-bg-3) 25%, var(--dark-bg-4) 50%, var(--dark-bg-3) 75%) !important;
            background-size: 200% 100% !important;
        }

        /* ===== 滚动条 ===== */
        ::-webkit-scrollbar {
            width: 6px;
            height: 6px;
        }

        ::-webkit-scrollbar-track {
            background: transparent;
        }

        ::-webkit-scrollbar-thumb {
            background: var(--dark-bg-4);
            border-radius: 3px;
        }

        ::-webkit-scrollbar-thumb:hover {
            background: var(--dark-text-3);
        }

        .rc-virtual-list-scrollbar-thumb {
            background-color: var(--dark-bg-4) !important;
        }

        /* ===== 强制覆盖白色背景 ===== */
        [style*="background-color: rgb(255, 255, 255)"],
        [style*="background-color: rgb(252, 252, 252)"],
        [style*="background-color: rgb(247, 248, 250)"],
        [style*="background: rgb(255, 255, 255)"],
        [style*="background: white"],
        [style*="background-color: white"] {
            background-color: var(--dark-bg-2) !important;
        }

        /* ===== 图片容器 ===== */
        .ant-image,
        [class*="image-container"] {
            background-color: var(--dark-bg-3) !important;
            border-radius: 8px;
        }

        /* ===== 文字通用继承 ===== */
        p, span, div, li, label, h1, h2, h3, h4, h5, h6 {
            color: inherit;
        }

        /* ===== 表单元素 ===== */
        .ant-select,
        .ant-select-selector,
        .ant-input-group-addon {
            background-color: var(--dark-bg-3) !important;
            border-color: var(--dark-border) !important;
            color: var(--dark-text-1) !important;
        }

        /* ===== 模态框 ===== */
        .ant-modal-content,
        .ant-modal-header,
        .ant-modal-body,
        .ant-modal-footer {
            background-color: var(--dark-bg-2) !important;
            border-color: var(--dark-border) !important;
            color: var(--dark-text-1) !important;
        }

        .ant-modal-close-x {
            color: var(--dark-text-2) !important;
        }
    `;

    function injectDarkMode() {
        if (getDarkModeSetting()) {
            GM_addStyle(darkModeCSS);
            console.log('[闲鱼暗色模式] v2.0 已启用');
        }
    }

    function toggleDarkMode() {
        const current = GM_getValue(STORAGE_KEY, 'auto');
        let next;
        if (current === 'auto') {
            next = 'true';
        } else if (current === 'true') {
            next = 'false';
        } else {
            next = 'auto';
        }
        GM_setValue(STORAGE_KEY, next);
        location.reload();
    }

    const currentSetting = GM_getValue(STORAGE_KEY, 'auto');
    const statusText = currentSetting === 'auto' ? '跟随系统' : (currentSetting === 'true' ? '已开启' : '已关闭');
    GM_registerMenuCommand(`切换暗色模式 (当前: ${statusText})`, toggleDarkMode);

    if (window.matchMedia) {
        window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
            if (GM_getValue(STORAGE_KEY, 'auto') === 'auto') {
                location.reload();
            }
        });
    }

    injectDarkMode();
})();
