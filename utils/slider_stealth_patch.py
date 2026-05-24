#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NoCaptcha 反检测注入模块
在 Playwright 浏览器启动时注入 JS 脚本，隐藏自动化特征。
参考: Botright (Vinyzu/Botright) 的反检测思路
"""

# Chromium 反检测启动参数
STEALTH_LAUNCH_ARGS = [
    '--disable-blink-features=AutomationControlled',
    '--disable-features=IsolateOrigins,site-per-process',
    '--disable-site-isolation-trials',
    '--no-sandbox',
    '--disable-gpu-sandbox',
    '--disable-dev-shm-usage',
    '--disable-setuid-sandbox',
    '--disable-infobars',
    '--disable-background-timer-throttling',
    '--disable-backgrounding-occluded-windows',
    '--disable-renderer-backgrounding',
    '--disable-features=TranslateUI',
    '--disable-ipc-flooding-protection',
    '--disable-hang-monitor',
    '--disable-prompt-on-repost',
    '--disable-sync',
    '--disable-default-apps',
    '--disable-crash-reporter',
    '--disable-component-extensions-with-background-pages',
    '--password-store=basic',
    '--use-mock-keychain',
    '--disable-breakpad',
    '--disable-client-side-phishing-detection',
    '--disable-component-update',
    '--disable-domain-reliability',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-features=InterestFeedContentSuggestions',
    '--disable-features=CalculateNativeWinOcclusion',
    '--enable-features=NetworkService,NetworkServiceInProcess',
    '--force-color-profile=srgb',
    '--metrics-recording-only',
    '--mute-audio',
    '--hide-scrollbars',
    '--disable-notifications',
]

# 反检测 JS 脚本 - 在 page.add_init_script() 中注入
STEALTH_INIT_SCRIPT = r"""
// ====== NoCaptcha 反检测脚本 ======

// 1. 隐藏 webdriver 标志
Object.defineProperty(navigator, 'webdriver', {
    get: () => false,
});
delete navigator.__proto__.webdriver;

// 2. 伪造 chrome.runtime
window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {},
};

// 3. 伪造 plugins 数组（真实的 Chrome 通常有5个）
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const plugins = [
            { name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format', length: 1 },
            { name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '', length: 1 },
            { name: 'Native Client', filename: 'internal-nacl-plugin', description: '', length: 2 },
        ];
        plugins.item = (i) => plugins[i] || null;
        plugins.namedItem = (name) => plugins.find(p => p.name === name) || null;
        plugins.refresh = () => {};
        return Object.setPrototypeOf(plugins, PluginArray.prototype);
    },
});

// 4. 伪造 languages
Object.defineProperty(navigator, 'languages', {
    get: () => ['zh-CN', 'zh', 'en-US', 'en'],
});

// 5. Canvas 指纹噪声 - 轻微扰乱 toDataURL
const _originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
HTMLCanvasElement.prototype.toDataURL = function(type) {
    const ctx = this.getContext('2d');
    if (ctx) {
        const imageData = ctx.getImageData(0, 0, this.width, this.height);
        // 对第一个像素做极微扰动（不影响视觉）
        if (imageData.data.length > 3) {
            imageData.data[0] = imageData.data[0] ^ 1;
        }
        ctx.putImageData(imageData, 0, 0);
    }
    return _originalToDataURL.apply(this, arguments);
};

// 6. WebGL 伪装 - 返回常见的 GPU 渲染器
const _getParameter = WebGLRenderingContext.prototype.getParameter;
WebGLRenderingContext.prototype.getParameter = function(parameter) {
    if (parameter === 37445) {
        return 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0, D3D11)';
    }
    if (parameter === 37446) {
        return 'WebKit WebGL';
    }
    return _getParameter.call(this, parameter);
};

// 7. 权限 API 伪造
const _query = window.navigator.permissions.query;
window.navigator.permissions.query = function(parameters) {
    if (parameters.name === 'notifications') {
        return Promise.resolve({ state: Notification.permission, onchange: null });
    }
    return _query.call(this, parameters);
};

// 8. 隐藏 headless 特征
if (navigator.userAgent.includes('Headless')) {
    Object.defineProperty(navigator, 'userAgent', {
        get: () => navigator.userAgent.replace('Headless', ''),
    });
}

// 9. 伪造屏幕属性（防止指纹不一致）
Object.defineProperty(screen, 'availWidth', { get: () => screen.width });
Object.defineProperty(screen, 'availHeight', { get: () => screen.height });
Object.defineProperty(screen, 'colorDepth', { get: () => 24 });
Object.defineProperty(screen, 'pixelDepth', { get: () => 24 });

// 10. 伪造 connection.rtt
if (navigator.connection) {
    Object.defineProperty(navigator.connection, 'rtt', { get: () => 50 });
}
""".strip()

__all__ = ['STEALTH_LAUNCH_ARGS', 'STEALTH_INIT_SCRIPT']
