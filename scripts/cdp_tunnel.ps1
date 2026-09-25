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
    [string]$SshHost = "hk",
    [int]$LocalPort = 9222,
    # 只绑定 VPS 的 docker 网桥内网地址：容器可达、公网不可达（无鉴权 CDP 严禁 0.0.0.0 暴露）
    [string]$RemoteBind = "172.19.0.1:9222"
)

Write-Host "[cdp-tunnel] reverse tunnel ${SshHost}:${RemoteBind} -> localhost:$LocalPort"
while ($true) {
    Write-Host "[cdp-tunnel] $(Get-Date -Format 'HH:mm:ss') tunnel starting..."
    ssh -N -R "$($RemoteBind):localhost:$($LocalPort)" `
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 `
        -o ExitOnForwardFailure=yes -o ConnectTimeout=20 $SshHost
    Write-Host "[cdp-tunnel] $(Get-Date -Format 'HH:mm:ss') tunnel exited (code=$LASTEXITCODE), reconnecting in 5s..."
    Start-Sleep -Seconds 5
}
