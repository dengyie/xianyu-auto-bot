# 闲鱼滑块 CDP 反向隧道守护（用户 PC 侧独立运行，不依赖任何会话）
#
# 作用：把本机 Chrome 调试端口(9222)反向暴露到 VPS 内网网桥地址(172.19.0.1:9222)，
#       xianyu-auto-bot 容器经 XY_SLIDER_CDP_ENDPOINT=http://172.19.0.1:9222 连接
#       本机 Chrome 拖滑块（真实设备指纹 + 本机家宽直连）。断线自动重连。
#
# 前提：
#   1. 本机 ~/.ssh/config 已配置 hk 主机（VPS），可免密登录；
#   2. VPS sshd GatewayPorts=clientspecified（客户端指定内网绑定地址）；
#   3. Chrome 已带调试端口启动（独立 profile，勿用默认 profile，Chrome 136+ 禁 CDP）：
#      chrome.exe --remote-debugging-port=9222 --user-data-dir="%LOCALAPPDATA%\xianyu-cdp"
#
# 运行：
#   powershell -ExecutionPolicy Bypass -File scripts\cdp_tunnel.ps1
#
# 开机自启（shell:startup 快捷方式）：
#   1. Win+R 输入 shell:startup 回车，打开启动文件夹；
#   2. 新建快捷方式，目标填：
#      powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File "<仓库路径>\scripts\cdp_tunnel.ps1"

param(
    [string]$SshHost = "tencent-lh",
    [int]$LocalPort = 9222,
    # 只绑定 VPS 的 docker 网桥内网地址：容器可达、公网不可达（无鉴权 CDP 严禁 0.0.0.0 暴露）
    [string]$RemoteBind = "172.19.0.1:9222"
)

# 提取远端端口号供自愈清理
$remotePort = if ($RemoteBind -match ':(\d+)$') { $Matches[1] } else { $LocalPort }

Write-Host "[cdp-tunnel] reverse tunnel ${SshHost}:${RemoteBind} -> localhost:$LocalPort"
while ($true) {
    # 1. 检查本地 Chrome 调试端口可用性
    try {
        $null = Invoke-WebRequest -Uri "http://127.0.0.1:$LocalPort/json/version" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
    } catch {
        Write-Warning "[cdp-tunnel] $(Get-Date -Format 'HH:mm:ss') 本地 Chrome 端口 $LocalPort 未响应，请确保 Chrome 已启动（带 --remote-debugging-port=$LocalPort）"
    }

    # 2. 远端自愈预检：清理 VPS 上可能残留的僵尸 sshd 端口占用，防止 'remote port forwarding failed'
    try {
        ssh -o ConnectTimeout=5 -o BatchMode=yes $SshHost "fuser -k ${remotePort}/tcp 2>/dev/null || true" | Out-Null
    } catch {
        # 忽略网络抖动导致的预检报错
    }

    Write-Host "[cdp-tunnel] $(Get-Date -Format 'HH:mm:ss') tunnel starting..."
    ssh -N -R "$($RemoteBind):localhost:$($LocalPort)" `
        -o ServerAliveInterval=15 -o ServerAliveCountMax=3 `
        -o ExitOnForwardFailure=yes -o ConnectTimeout=20 $SshHost
    Write-Host "[cdp-tunnel] $(Get-Date -Format 'HH:mm:ss') tunnel exited (code=$LASTEXITCODE), reconnecting in 5s..."
    Start-Sleep -Seconds 5
}
