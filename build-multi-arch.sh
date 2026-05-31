#!/bin/bash
# 多架构 Docker 镜像构建脚本 (AMD64 + ARM64)
# 直接使用当前目录构建，无需加密
#
# 用法:
#   ./build-multi-arch.sh

set -e

echo "========================================"
echo "  多架构 Docker 镜像构建脚本"
echo "========================================"
echo

# 设置镜像标签（可通过环境变量覆盖）
IMAGE_NAME="${IMAGE_NAME:-xianyu-auto-bot}"
IMAGE_TAG="${IMAGE_TAG:-latest}"
DOCKERFILE="${DOCKERFILE:-Dockerfile-cn}"

# ==================== 自动化配置 ====================
PUSH_IMAGE="${PUSH_IMAGE:-n}"
REGISTRY="${REGISTRY:-}"

if [ "$PUSH_IMAGE" = "y" ]; then
    if [ -z "$REGISTRY" ]; then
        echo "[错误] PUSH_IMAGE=y 时必须通过环境变量 REGISTRY 指定镜像仓库前缀"
        echo "示例: REGISTRY=ghcr.io/your-name PUSH_IMAGE=y ./build-multi-arch.sh"
        exit 1
    fi
    FULL_IMAGE_NAME="${REGISTRY}/${IMAGE_NAME}:${IMAGE_TAG}"
    OUTPUT_FLAG="--push"
    PUSH_STATUS="是"
else
    FULL_IMAGE_NAME="${IMAGE_NAME}:${IMAGE_TAG}"
    OUTPUT_FLAG="--load"
    PUSH_STATUS="否（仅加载到本地）"
fi

echo "配置信息："
echo "  - 镜像名: $FULL_IMAGE_NAME"
echo "  - Dockerfile: $DOCKERFILE"
echo "  - 推送到仓库: $PUSH_STATUS"
echo

# 显示当前构建目录
echo "========================================"
echo "构建目录: $(pwd)"
echo "========================================"
echo "Python 文件:"
ls -la *.py 2>/dev/null | head -5 || echo "(无 Python 文件)"
echo

echo "========================================"
echo "步骤 1: 检查 Docker 服务"
echo "========================================"
if ! docker ps >/dev/null 2>&1; then
    echo "[错误] Docker 服务未运行，请先启动 Docker"
    exit 1
fi
echo "[✓] Docker 服务正常运行"

echo
echo "========================================"
echo "步骤 2: 基础镜像源配置"
echo "========================================"
BASE_IMAGE="docker.1panel.live/library/python:3.11-slim-bookworm"
BASE_IMAGE_ARG="--build-arg BASE_IMAGE=$BASE_IMAGE"
echo "基础镜像: $BASE_IMAGE"
echo

echo "========================================"
echo "步骤 3: 安装 QEMU 模拟器（支持 ARM64）"
echo "========================================"
echo "检查 QEMU 是否已安装..."
if docker run --rm --privileged tonistiigi/binfmt --version >/dev/null 2>&1; then
    echo "安装/更新 QEMU 模拟器..."
    docker run --rm --privileged tonistiigi/binfmt --install all
    if [ $? -eq 0 ]; then
        echo "[✓] QEMU 模拟器安装成功"
    else
        echo "[⚠] QEMU 模拟器安装失败，继续尝试构建"
    fi
else
    echo "[⚠] 无法安装 QEMU 模拟器，ARM64 构建可能需要其他方式"
fi

echo
echo "========================================"
echo "步骤 4: 检查并创建 buildx builder"
echo "========================================"
if ! docker buildx inspect multiarch-builder >/dev/null 2>&1; then
    echo "创建新的 buildx builder..."
    docker buildx create --name multiarch-builder --driver docker-container --use --bootstrap --driver-opt network=host
    if [ $? -ne 0 ]; then
        echo "尝试使用默认 driver..."
        docker buildx create --name multiarch-builder --use --bootstrap
        if [ $? -ne 0 ]; then
            echo "[错误] 创建 buildx builder 失败"
            exit 1
        fi
    fi
    echo "[✓] buildx builder 创建成功"
else
    echo "使用现有的 buildx builder"
    docker buildx use multiarch-builder
    docker buildx inspect --bootstrap >/dev/null 2>&1
    echo "[✓] buildx builder 已就绪"
fi

echo
echo "========================================"
echo "步骤 5: 查看支持的平台"
echo "========================================"
PLATFORMS=$(docker buildx inspect --bootstrap | grep "Platforms:" | sed 's/Platforms://' | xargs)
echo "支持的平台: $PLATFORMS"

# 检查是否支持 ARM64
if echo "$PLATFORMS" | grep -q "linux/arm64"; then
    echo "[✓] 检测到 ARM64 支持"
    SUPPORT_ARM64=true
    PLATFORMS="linux/amd64,linux/arm64"
else
    echo "[⚠] 未检测到 ARM64 支持，只构建 AMD64"
    SUPPORT_ARM64=false
    PLATFORMS="linux/amd64"
fi
echo

LOCAL_PLATFORM="linux/amd64"
case "$(uname -m)" in
    x86_64|amd64)
        LOCAL_PLATFORM="linux/amd64"
        ;;
    arm64|aarch64)
        LOCAL_PLATFORM="linux/arm64"
        ;;
esac

if [ "$PUSH_IMAGE" = "y" ]; then
    TARGET_PLATFORMS="$PLATFORMS"
else
    TARGET_PLATFORMS="$LOCAL_PLATFORM"
fi

echo "========================================"
echo "步骤 6: 开始构建镜像"
echo "========================================"
echo "镜像名称: $FULL_IMAGE_NAME"
echo "Dockerfile: $DOCKERFILE"
echo "平台: $TARGET_PLATFORMS"
echo

if [ "$SUPPORT_ARM64" = "true" ]; then
    echo "[提示] ARM64 构建使用 QEMU 模拟，速度较慢，请耐心等待..."
fi

if [ "$PUSH_IMAGE" != "y" ]; then
    echo "[提示] 未启用推送，当前仅构建并加载本机架构镜像"
fi

docker buildx build --platform "$TARGET_PLATFORMS" -t "$FULL_IMAGE_NAME" -f "$DOCKERFILE" . $OUTPUT_FLAG $BASE_IMAGE_ARG
if [ $? -ne 0 ]; then
    echo ""
    echo "[错误] 构建失败"
    exit 1
fi

echo
echo "========================================"
echo "✓ 构建完成！"
echo "========================================"
if [ "$PUSH_IMAGE" = "y" ]; then
    echo "镜像已推送到: $FULL_IMAGE_NAME"
else
    echo "镜像已加载到本地: $FULL_IMAGE_NAME"
fi
echo
echo "使用方法:"
if [ "$PUSH_IMAGE" = "y" ]; then
    echo "  docker pull $FULL_IMAGE_NAME"
fi
echo "  docker run -d -p 8090:8090 --name xianyu-auto-bot $FULL_IMAGE_NAME"
echo
echo "验证多架构镜像:"
if [ "$PUSH_IMAGE" = "y" ]; then
    echo "  docker buildx imagetools inspect $FULL_IMAGE_NAME"
else
    echo "  docker image inspect $FULL_IMAGE_NAME"
fi
echo
