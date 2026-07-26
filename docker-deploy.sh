#!/bin/bash

# 闲鱼管理系统 Docker 部署脚本
# 支持快速部署和管理

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 项目配置
PROJECT_NAME="xianyu-auto-bot"
COMPOSE_FILE="docker-compose.yml"
SELECTED_COMPOSE_FILE="$COMPOSE_FILE"
# 生产默认镜像：GitHub Actions 推送到 GHCR；可用环境变量覆盖
GHCR_IMAGE="${XIANYU_IMAGE:-ghcr.io/dengyie/xianyu-auto-bot:latest}"
GHCR_LOGIN_USER="${GHCR_USER:-dengyie}"
# 登录 token 优先 env GHCR_TOKEN，否则读本地文件（勿提交仓库）
GHCR_TOKEN_FILE="${GHCR_TOKEN_FILE:-$HOME/.config/xianyu/ghcr_token}"

if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD="docker-compose"
else
    COMPOSE_CMD=""
fi

compose() {
    $COMPOSE_CMD -f "$SELECTED_COMPOSE_FILE" "$@"
}

get_web_port() {
    if [ "$SELECTED_COMPOSE_FILE" = "docker-compose-cn.yml" ]; then
        echo "8000"
    else
        echo "9000"
    fi
}

# 打印带颜色的消息
print_info() {
    echo -e "${BLUE}ℹ️  $1${NC}"
}

print_success() {
    echo -e "${GREEN}✅ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠️  $1${NC}"
}

print_error() {
    echo -e "${RED}❌ $1${NC}"
}

# 检查依赖
check_dependencies() {
    print_info "检查系统依赖..."
    
    if ! command -v docker &> /dev/null; then
        print_error "Docker 未安装，请先安装 Docker"
        exit 1
    fi
    
    if [ -z "$COMPOSE_CMD" ]; then
        print_error "Docker Compose 未安装，请先安装 Docker Compose"
        exit 1
    fi
    
    print_success "系统依赖检查通过"
}

# 初始化配置
init_config() {
    print_info "初始化配置文件..."

    # 检查关键文件
    if [ ! -f "entrypoint.sh" ]; then
        print_error "entrypoint.sh 文件不存在，Docker容器将无法启动"
        print_info "请确保项目文件完整"
        exit 1
    else
        print_success "entrypoint.sh 文件已存在"
    fi

    if [ ! -f "global_config.yml" ]; then
        print_error "global_config.yml 配置文件不存在"
        print_info "请确保配置文件存在"
        exit 1
    else
        print_success "global_config.yml 配置文件已存在"
    fi

    # 应用密钥：compose 从同目录 .env 做 ${VAR} 替换（不是 env_file:）
    if [ ! -f ".env" ]; then
        if [ -f ".env.example" ]; then
            cp .env.example .env
            chmod 600 .env 2>/dev/null || true
            print_warning "已从 .env.example 生成 .env（chmod 600）；请填写密钥后再启动"
        else
            print_warning "未找到 .env；compose 将使用空默认值（生产请创建 .env）"
        fi
    else
        print_success ".env 已存在（compose 变量替换用；勿提交 git）"
        chmod 600 .env 2>/dev/null || true
    fi

    # 创建必要的目录
    mkdir -p data logs backups static/uploads/images
    # GHCR token 目录（login-ghcr 用；token 文件本身由运维手写）
    mkdir -p "$(dirname "$GHCR_TOKEN_FILE")" 2>/dev/null || true
    print_success "已创建必要的目录"
}

# 是否已有 ghcr.io 登录态（只看 registry 名，不读 token）
ghcr_logged_in() {
    local cfg="${HOME}/.docker/config.json"
    [ -f "$cfg" ] || return 1
    python3 - "$cfg" <<'PY' 2>/dev/null
import json, sys
c = json.load(open(sys.argv[1]))
auths = c.get("auths") or {}
helpers = c.get("credHelpers") or {}
keys = set(auths) | set(helpers)
sys.exit(0 if any(k == "ghcr.io" or k.endswith("ghcr.io") for k in keys) else 1)
PY
}

# 用 GHCR_TOKEN 或 token 文件登录（固化路径，token 永不进仓库）
login_ghcr() {
    local token=""
    if [ -n "${GHCR_TOKEN:-}" ]; then
        token="$GHCR_TOKEN"
    elif [ -f "$GHCR_TOKEN_FILE" ]; then
        token=$(tr -d '\r\n' < "$GHCR_TOKEN_FILE")
    fi

    if [ -z "$token" ]; then
        print_error "未找到 GHCR token"
        print_info "任选其一："
        print_info "  1) export GHCR_TOKEN=<PAT with read:packages> && $0 login-ghcr"
        print_info "  2) mkdir -p \"\$(dirname \"$GHCR_TOKEN_FILE\")\" && umask 077 && printf '%s' '<PAT>' > \"$GHCR_TOKEN_FILE\""
        print_info "     然后: $0 login-ghcr"
        print_info "PAT: GitHub → Settings → Developer settings → Personal access tokens（classic: read:packages）"
        exit 1
    fi

    print_info "登录 ghcr.io（user=$GHCR_LOGIN_USER）..."
    if ! echo "$token" | docker login ghcr.io -u "$GHCR_LOGIN_USER" --password-stdin; then
        print_error "docker login ghcr.io 失败（检查 PAT 是否含 read:packages，用户名是否为 GitHub 用户名）"
        exit 1
    fi
    print_success "ghcr.io 登录已写入 ~/.docker/config.json"
}

ensure_ghcr_login() {
    # 匿名 pull 若仍可用则不强制登录；有 token 则主动固化登录
    if [ -n "${GHCR_TOKEN:-}" ] || [ -f "$GHCR_TOKEN_FILE" ]; then
        if ghcr_logged_in; then
            print_info "已检测到 ghcr.io 登录态，跳过 login"
            return 0
        fi
        login_ghcr
        return $?
    fi
    if ghcr_logged_in; then
        return 0
    fi
    print_warning "未配置 GHCR 登录；将尝试匿名 pull。失败时请: $0 login-ghcr"
    return 0
}

# 从 GHCR 拉取镜像（生产唯一路径；禁止在本机/VPS compose build）
pull_image() {
    print_info "从 GHCR 拉取镜像: $GHCR_IMAGE"
    print_warning "Docker 镜像只由 GitHub Actions 构建；本脚本不会 docker build"
    ensure_ghcr_login || true
    if ! docker pull "$GHCR_IMAGE"; then
        print_error "拉取失败。请确认 Actions「Build and Push Docker Image」已成功"
        print_info "若包为 private：配置 token 后执行 $0 login-ghcr，再 $0 pull"
        exit 1
    fi
    # 兼容旧 compose / 本地 tag 习惯
    if [ "$GHCR_IMAGE" != "dengyie/xianyu-auto-bot:latest" ]; then
        docker tag "$GHCR_IMAGE" dengyie/xianyu-auto-bot:latest 2>/dev/null || true
    fi
    print_success "镜像拉取完成: $GHCR_IMAGE"
}

# 兼容旧命令名：明确拒绝本地 build
build_image() {
    print_error "已禁用本地/VPS docker build"
    print_info "请 push 到 main，等待 GitHub Actions 推送 GHCR，再执行: $0 pull && $0 start"
    print_info "或: $0 update   # 自动 pull + recreate --no-build"
    exit 1
}

# 启动服务
start_services() {
    local profile=""
    if [ "$1" = "with-nginx" ]; then
        profile="--profile with-nginx"
        print_info "启动服务（包含 Nginx）..."
    else
        print_info "启动基础服务..."
    fi

    # 强制不在启动时 build
    compose $profile up -d --no-build --pull never
    print_success "服务启动完成"

    # 等待服务就绪
    print_info "等待服务就绪..."
    sleep 10

    # 检查服务状态
    if compose ps | grep -q "Up"; then
        print_success "服务运行正常"
        show_access_info "$1"
    else
        print_error "服务启动失败"
        compose logs
        exit 1
    fi
}

# 停止服务
stop_services() {
    print_info "停止服务..."
    compose down
    print_success "服务已停止"
}

# 重启服务
restart_services() {
    print_info "重启服务..."
    compose restart
    print_success "服务已重启"
}

# 查看日志
show_logs() {
    local service="$1"
    if [ -z "$service" ]; then
        compose logs -f
    else
        compose logs -f "$service"
    fi
}

# 查看状态
show_status() {
    print_info "服务状态:"
    compose ps
    
    print_info "资源使用:"
    docker stats --no-stream $(compose ps -q)
}

# 显示访问信息
show_access_info() {
    local with_nginx="$1"
    
    echo ""
    print_success "🎉 部署完成！"
    echo ""
    
    if [ "$with_nginx" = "with-nginx" ]; then
        echo "📱 访问地址:"
        echo "   HTTP:  http://localhost"
        echo "   HTTPS: https://localhost (如果配置了SSL)"
    else
        local web_port
        web_port=$(get_web_port)
        echo "📱 访问地址:"
        echo "   HTTP: http://localhost:${web_port}"
    fi
    
    echo ""
    echo "🔐 登录信息:"
    echo "   用户名: admin"
    echo "   密码:   请查看首次启动日志，或通过 ADMIN_PASSWORD 显式配置"
    echo ""
    echo "📊 管理命令:"
    echo "   查看状态: $0 status"
    echo "   查看日志: $0 logs"
    echo "   重启服务: $0 restart"
    echo "   停止服务: $0 stop"
    echo ""
}

# 健康检查
health_check() {
    print_info "执行健康检查..."
    
    local web_port
    web_port=$(get_web_port)
    local url="http://localhost:${web_port}/health"
    local max_attempts=30
    local attempt=1
    
    while [ $attempt -le $max_attempts ]; do
        if curl -f -s "$url" > /dev/null 2>&1; then
            print_success "健康检查通过"
            return 0
        fi
        
        print_info "等待服务就绪... ($attempt/$max_attempts)"
        sleep 2
        ((attempt++))
    done
    
    print_error "健康检查失败"
    return 1
}

# 备份数据
backup_data() {
    print_info "备份数据..."
    
    local backup_dir="backups/$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$backup_dir"
    
    # 备份数据库
    if [ -f "data/xianyu_data.db" ]; then
        cp data/xianyu_data.db "$backup_dir/"
        print_success "数据库备份完成"
    fi
    
    # 备份配置
    if [ -f ".env" ]; then
        cp .env "$backup_dir/"
    fi
    cp global_config.yml "$backup_dir/" 2>/dev/null || true
    
    print_success "数据备份完成: $backup_dir"
}

# 更新部署（GHCR pull + recreate，绝不本地 build）
update_deployment() {
    print_info "更新部署（GHCR only）..."

    # 备份数据
    backup_data

    # 生产目录通常无 .git；代码真相在 GitHub，镜像真相在 GHCR
    if [ -d ".git" ]; then
        print_warning "检测到 .git；生产推荐无 git 目录，仅 pull 镜像"
    fi

    pull_image

    print_info "用新镜像重建容器（--no-build）..."
    compose up -d --no-build --force-recreate --pull never

    print_info "等待服务就绪..."
    sleep 10
    if compose ps | grep -q "Up"; then
        print_success "服务运行正常"
        show_access_info
    else
        print_error "服务启动失败"
        compose logs
        exit 1
    fi

    # 回收上一版镜像：镜像 3.4G，不清理则每次发版都在 1GB VPS 上多堆一份悬空层
    # （2026-07-26 曾累积 8 个悬空镜像，磁盘 80%）。仅清 dangling，不动 tagged 镜像。
    print_info "清理旧版悬空镜像..."
    docker image prune -f 2>/dev/null || print_warning "悬空镜像清理跳过"

    print_success "更新完成"
}

# 清理环境
cleanup() {
    print_warning "这将删除所有容器、镜像和数据，确定要继续吗？(y/N)"
    read -r response
    
    if [[ "$response" =~ ^[Yy]$ ]]; then
        print_info "清理环境..."
        
        # 停止并删除容器
        compose down -v --rmi all
        
        # 删除数据目录
        rm -rf data logs backups
        
        print_success "环境清理完成"
    else
        print_info "取消清理操作"
    fi
}

# 显示帮助信息
show_help() {
    echo "闲鱼管理系统 Docker 部署脚本（GHCR only）"
    echo ""
    echo "镜像构建：GitHub Actions (.github/workflows/docker-image.yml) →"
    echo "  ghcr.io/dengyie/xianyu-auto-bot:latest"
    echo "本机/VPS 禁止 docker compose build（1GB 机器会卡死）。"
    echo ""
    echo "用法: $0 [命令] [选项]"
    echo ""
    echo "命令:"
    echo "  init                初始化配置文件"
    echo "  login-ghcr          用 PAT 登录 ghcr.io（写入 ~/.docker/config.json）"
    echo "  pull                从 GHCR 拉取最新镜像"
    echo "  build               已禁用（会提示改用 GHCR）"
    echo "  start [with-nginx]  启动服务（先 pull，--no-build）"
    echo "  stop                停止服务"
    echo "  restart             重启服务"
    echo "  status              查看服务状态"
    echo "  logs [service]      查看日志"
    echo "  health              健康检查"
    echo "  backup              备份数据"
    echo "  update              备份 + pull + recreate（推荐发版）"
    echo "  cleanup             清理环境"
    echo "  help                显示帮助信息"
    echo ""
    echo "环境变量 / 密钥文件:"
    echo "  XIANYU_IMAGE      默认 ghcr.io/dengyie/xianyu-auto-bot:latest"
    echo "  GHCR_TOKEN        PAT（read:packages）；或写入 $GHCR_TOKEN_FILE"
    echo "  GHCR_USER         默认 dengyie"
    echo "  应用密钥：项目目录 .env（compose 自动替换 \${VAR}；见 .env.example）"
    echo ""
    echo "示例:"
    echo "  $0 login-ghcr       # 首次固化 GHCR 登录"
    echo "  $0 init             # 初始化配置"
    echo "  $0 update           # 生产发版：pull GHCR 并重建"
    echo "  $0 pull && $0 start # 分步拉取并启动"
    echo "  $0 logs xianyu-app  # 查看应用日志"
    echo ""
}

# 主函数
main() {
    case "$1" in
        "init")
            check_dependencies
            init_config
            ;;
        "login-ghcr"|"login")
            check_dependencies
            login_ghcr
            ;;
        "pull")
            check_dependencies
            pull_image
            ;;
        "build")
            check_dependencies
            build_image
            ;;
        "start")
            check_dependencies
            init_config
            pull_image
            start_services "$2"
            ;;
        "stop")
            stop_services
            ;;
        "restart")
            restart_services
            ;;
        "status")
            show_status
            ;;
        "logs")
            show_logs "$2"
            ;;
        "health")
            health_check
            ;;
        "backup")
            backup_data
            ;;
        "update")
            check_dependencies
            update_deployment
            ;;
        "cleanup")
            cleanup
            ;;

        "help"|"--help"|"-h")
            show_help
            ;;
        "")
            print_info "快速部署模式（GHCR pull，不 build）"
            check_dependencies
            init_config
            pull_image
            start_services
            ;;
        *)
            print_error "未知命令: $1"
            show_help
            exit 1
            ;;
    esac
}

# 执行主函数
main "$@"
