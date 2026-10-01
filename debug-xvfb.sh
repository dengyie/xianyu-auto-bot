#!/bin/bash
# Xvfb 故障排查脚本

echo "========================================"
echo "  Xvfb 故障排查工具"
echo "========================================"
echo ""

# 1. 检查 Xvfb 进程
echo "1. 检查 Xvfb 进程："
if pgrep -x "Xvfb" > /dev/null; then
    echo "✓ Xvfb 进程正在运行"
    ps aux | grep Xvfb | grep -v grep
else
    echo "✗ Xvfb 进程未运行"
fi
echo ""

# 2. 检查锁文件
echo "2. 检查锁文件："
if ls /tmp/.X*-lock 2>/dev/null; then
    echo "⚠ 发现锁文件："
    ls -lh /tmp/.X*-lock
    echo ""
    echo "建议清理命令："
    echo "  rm -f /tmp/.X*-lock"
else
    echo "✓ 没有锁文件"
fi
echo ""

# 3. 检查 Unix socket
echo "3. 检查 X11 Unix socket："
if ls /tmp/.X11-unix/X* 2>/dev/null; then
    echo "⚠ 发现 socket 文件："
    ls -lh /tmp/.X11-unix/X*
    echo ""
    echo "建议清理命令："
    echo "  rm -f /tmp/.X11-unix/X*"
else
    echo "✓ 没有 socket 文件"
fi
echo ""

# 4. 检查 DISPLAY 环境变量
echo "4. 检查 DISPLAY 环境变量："
if [ -n "$DISPLAY" ]; then
    echo "✓ DISPLAY=$DISPLAY"
else
    echo "✗ DISPLAY 未设置"
fi
echo ""

# 5. 检查 VNC 进程
echo "5. 检查 VNC 进程："
if pgrep -x "X0tigervnc" > /dev/null; then
    echo "✓ VNC 服务器 (x0vncserver/X0tigervnc) 正在运行"
    ps aux | grep -E "X0tigervnc|x0vncserver" | grep -v grep
else
    echo "✗ VNC 服务器未运行"
fi
echo ""

# 6. 检查端口占用
echo "6. 检查端口占用："
echo "VNC 端口 (5900):"
if netstat -tuln 2>/dev/null | grep :5900 > /dev/null || ss -tuln 2>/dev/null | grep :5900 > /dev/null; then
    echo "✓ 端口 5900 正在监听"
else
    echo "✗ 端口 5900 未监听"
fi
echo ""

# 7. 查看日志
echo "7. 查看最近的日志："
if [ -f "/tmp/xvfb.log" ]; then
    echo "--- Xvfb 日志 (/tmp/xvfb.log) ---"
    tail -20 /tmp/xvfb.log
else
    echo "✗ Xvfb 日志文件不存在"
fi
echo ""

if [ -f "/tmp/x0vnc.log" ]; then
    echo "--- VNC 日志 (/tmp/x0vnc.log) ---"
    tail -20 /tmp/x0vnc.log
else
    echo "✗ VNC 日志文件不存在"
fi
echo ""

# 8. 测试显示连接
echo "8. 测试显示连接："
if [ -n "$DISPLAY" ]; then
    if command -v xdpyinfo > /dev/null 2>&1; then
        if xdpyinfo > /dev/null 2>&1; then
            echo "✓ 显示连接正常"
            xdpyinfo | head -5
        else
            echo "✗ 显示连接失败"
        fi
    else
        echo "⚠ xdpyinfo 未安装，无法测试"
    fi
else
    echo "✗ DISPLAY 未设置，无法测试"
fi
echo ""

# 9. 提供修复建议
echo "========================================"
echo "  修复建议"
echo "========================================"
echo ""
echo "如果 Xvfb 启动失败，请尝试："
echo ""
echo "1. 清理旧进程和锁文件："
echo "   pkill -9 Xvfb"
echo "   pkill -9 X0tigervnc x0vncserver"
echo "   rm -f /tmp/.X*-lock"
echo "   rm -f /tmp/.X11-unix/X*"
echo ""
echo "2. 手动启动 Xvfb："
echo "   Xvfb :99 -screen 0 1920x1080x24 -ac +extension GLX +render -noreset &"
echo "   export DISPLAY=:99"
echo ""
echo "3. 测试显示："
echo "   xdpyinfo"
echo ""
echo "4. 启动 VNC（可选）："
echo "   HOME=/tmp x0vncserver -display :99 -rfbport 5900 -SecurityTypes None &"
echo ""
echo "5. 重启容器："
echo "   docker-compose restart"
echo ""
echo "========================================"

