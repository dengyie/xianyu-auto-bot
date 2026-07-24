@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM 闲鱼管理系统 Docker 部署脚本 (Windows)
REM 镜像只由 GitHub Actions 构建并推送到 GHCR；本脚本禁止本地 compose build

title 闲鱼管理系统 Docker 部署 (GHCR only)

set PROJECT_NAME=xianyu-auto-bot
set COMPOSE_FILE=docker-compose.yml
set "COMPOSE_CMD=docker-compose"
if defined XIANYU_IMAGE (
    set "GHCR_IMAGE=%XIANYU_IMAGE%"
) else (
    set "GHCR_IMAGE=ghcr.io/dengyie/xianyu-auto-bot:latest"
)

set "INFO_PREFIX=[INFO]"
set "SUCCESS_PREFIX=[SUCCESS]"
set "WARNING_PREFIX=[WARNING]"
set "ERROR_PREFIX=[ERROR]"

echo %INFO_PREFIX% 检查系统依赖...

where docker >nul 2>&1
if %errorlevel% neq 0 (
    echo %ERROR_PREFIX% Docker 未安装，请先安装 Docker Desktop
    echo 下载地址: https://www.docker.com/products/docker-desktop
    pause
    exit /b 1
)

where docker-compose >nul 2>&1
if %errorlevel% neq 0 (
    docker compose version >nul 2>&1
    if %errorlevel% neq 0 (
        echo %ERROR_PREFIX% Docker Compose 未安装，请先安装 Docker Compose
        pause
        exit /b 1
    ) else (
        set "COMPOSE_CMD=docker compose"
    )
)

echo %SUCCESS_PREFIX% 系统依赖检查通过

echo %INFO_PREFIX% 初始化配置文件...

if not exist "entrypoint.sh" (
    echo %ERROR_PREFIX% entrypoint.sh 文件不存在，Docker容器将无法启动
    pause
    exit /b 1
) else (
    echo %SUCCESS_PREFIX% entrypoint.sh 文件已存在
)

if not exist "global_config.yml" (
    echo %ERROR_PREFIX% global_config.yml 配置文件不存在
    pause
    exit /b 1
) else (
    echo %SUCCESS_PREFIX% global_config.yml 配置文件已存在
)

if not exist "data" mkdir data
if not exist "logs" mkdir logs
if not exist "backups" mkdir backups
if not exist "static\uploads\images" mkdir static\uploads\images
echo %SUCCESS_PREFIX% 已创建必要的目录

if "%1"=="" goto quick_deploy
if "%1"=="help" goto show_help
if "%1"=="--help" goto show_help
if "%1"=="/?" goto show_help
if "%1"=="pull" goto pull_image
if "%1"=="start" goto start_services
if "%1"=="stop" goto stop_services
if "%1"=="restart" goto restart_services
if "%1"=="status" goto show_status
if "%1"=="logs" goto show_logs
if "%1"=="build" goto build_image
if "%1"=="update" goto update_deployment
if "%1"=="cleanup" goto cleanup
goto unknown_command

:pull_image
echo %INFO_PREFIX% 从 GHCR 拉取镜像: %GHCR_IMAGE%
echo %WARNING_PREFIX% Docker 镜像只由 GitHub Actions 构建；本脚本不会 docker build
docker pull %GHCR_IMAGE%
if %errorlevel% neq 0 (
    echo %ERROR_PREFIX% 拉取失败。请确认 Actions「Build and Push Docker Image」已成功，且已 docker login ghcr.io
    pause
    exit /b 1
)
if /i not "%GHCR_IMAGE%"=="dengyie/xianyu-auto-bot:latest" (
    docker tag %GHCR_IMAGE% dengyie/xianyu-auto-bot:latest 2>nul
)
echo %SUCCESS_PREFIX% 镜像拉取完成: %GHCR_IMAGE%
exit /b 0

:build_image
echo %ERROR_PREFIX% 已禁用本地/VPS docker build
echo %INFO_PREFIX% 请 push 到 main，等待 GitHub Actions 推送 GHCR，再执行: %~nx0 pull ^&^& %~nx0 start
echo %INFO_PREFIX% 或: %~nx0 update
exit /b 1

:quick_deploy
echo %INFO_PREFIX% 快速部署模式（GHCR pull，不 build）
call :pull_image
if %errorlevel% neq 0 exit /b 1
goto start_services_only

:update_deployment
echo %INFO_PREFIX% 更新部署（GHCR only）...
call :pull_image
if %errorlevel% neq 0 exit /b 1
echo %INFO_PREFIX% 用新镜像重建容器（--no-build）...
%COMPOSE_CMD% -f %COMPOSE_FILE% up -d --no-build --force-recreate --pull never
if %errorlevel% neq 0 (
    echo %ERROR_PREFIX% 服务启动失败
    %COMPOSE_CMD% -f %COMPOSE_FILE% logs
    pause
    exit /b 1
)
echo %SUCCESS_PREFIX% 更新完成
call :show_access_info
goto end

:start_services
call :pull_image
if %errorlevel% neq 0 exit /b 1

:start_services_only
echo %INFO_PREFIX% 启动服务...
%COMPOSE_CMD% -f %COMPOSE_FILE% up -d --no-build --pull never
if %errorlevel% neq 0 (
    echo %ERROR_PREFIX% 服务启动失败
    %COMPOSE_CMD% -f %COMPOSE_FILE% logs
    pause
    exit /b 1
)

echo %SUCCESS_PREFIX% 服务启动完成
echo %INFO_PREFIX% 等待服务就绪...
timeout /t 10 /nobreak >nul

%COMPOSE_CMD% -f %COMPOSE_FILE% ps | findstr "Up" >nul
if %errorlevel% equ 0 (
    echo %SUCCESS_PREFIX% 服务运行正常
    call :show_access_info
) else (
    echo %ERROR_PREFIX% 服务启动失败
    %COMPOSE_CMD% -f %COMPOSE_FILE% logs
    pause
    exit /b 1
)
goto end

:stop_services
echo %INFO_PREFIX% 停止服务...
%COMPOSE_CMD% -f %COMPOSE_FILE% down
echo %SUCCESS_PREFIX% 服务已停止
goto end

:restart_services
echo %INFO_PREFIX% 重启服务...
%COMPOSE_CMD% -f %COMPOSE_FILE% restart
echo %SUCCESS_PREFIX% 服务已重启
goto end

:show_status
echo %INFO_PREFIX% 服务状态:
%COMPOSE_CMD% -f %COMPOSE_FILE% ps
echo.
echo %INFO_PREFIX% 资源使用:
for /f "tokens=*" %%i in ('%COMPOSE_CMD% -f %COMPOSE_FILE% ps -q') do (
    docker stats --no-stream %%i
)
goto end

:show_logs
if "%2"=="" (
    %COMPOSE_CMD% -f %COMPOSE_FILE% logs -f
) else (
    %COMPOSE_CMD% -f %COMPOSE_FILE% logs -f %2
)
goto end

:cleanup
echo %WARNING_PREFIX% 这将删除所有容器、镜像和数据，确定要继续吗？
set /p confirm="请输入 y 确认: "
if /i "!confirm!"=="y" (
    echo %INFO_PREFIX% 清理环境...
    %COMPOSE_CMD% -f %COMPOSE_FILE% down -v --rmi all
    rmdir /s /q data logs backups 2>nul
    echo %SUCCESS_PREFIX% 环境清理完成
) else (
    echo %INFO_PREFIX% 取消清理操作
)
goto end

:show_access_info
echo.
echo %SUCCESS_PREFIX% 部署完成！
echo.
set "WEB_PORT=9000"
if /i "%COMPOSE_FILE%"=="docker-compose-cn.yml" set "WEB_PORT=8000"
echo 访问地址:
echo    HTTP: http://localhost:%WEB_PORT%
echo.
echo 登录信息:
echo    用户名: admin
echo    密码:   请查看首次启动日志，或通过 ADMIN_PASSWORD 显式配置
echo.
echo 管理命令:
echo    查看状态: %~nx0 status
echo    查看日志: %~nx0 logs
echo    更新发版: %~nx0 update
echo    重启服务: %~nx0 restart
echo    停止服务: %~nx0 stop
echo.
goto :eof

:show_help
echo 闲鱼管理系统 Docker 部署脚本 (Windows · GHCR only)
echo.
echo 镜像构建: GitHub Actions → ghcr.io/dengyie/xianyu-auto-bot:latest
echo 本机禁止 docker compose build。
echo.
echo 用法: %~nx0 [命令]
echo.
echo 命令:
echo   pull      从 GHCR 拉取最新镜像
echo   start     拉取并启动（--no-build）
echo   update    拉取并 force-recreate（推荐发版）
echo   stop      停止服务
echo   restart   重启服务
echo   status    查看服务状态
echo   logs      查看日志
echo   build     已禁用（会提示改用 GHCR）
echo   cleanup   清理环境
echo   help      显示帮助信息
echo.
echo 环境变量:
echo   XIANYU_IMAGE   默认 ghcr.io/dengyie/xianyu-auto-bot:latest
echo.
echo 示例:
echo   %~nx0 update   # 生产发版
echo   %~nx0 pull
echo   %~nx0 start
echo.
goto end

:unknown_command
echo %ERROR_PREFIX% 未知命令: %1
call :show_help
exit /b 1

:end
if "%1"=="" (
    echo.
    echo 按任意键退出...
    pause >nul
)
