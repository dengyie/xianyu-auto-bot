#!/usr/bin/env python3
"""
更新清单生成工具

此脚本用于生成更新清单，包含所有可更新文件的MD5哈希值和大小信息。
生成的清单可用于 GitHub Releases + tag 文件热更新方案。

使用方法：
    python generate_update_manifest.py

输出：
    - 生成 update_files.json 文件
"""

import os
import sys
import json
import hashlib
import subprocess
from urllib import request
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

DEFAULT_GITHUB_OWNER = "dengyie"
DEFAULT_GITHUB_REPO = "xianyu-auto-bot"

# static 目录下允许热更新的静态资源类型
STATIC_ASSET_EXTENSIONS = {
    '.css', '.eot', '.gif', '.html', '.ico', '.jpeg', '.jpg',
    '.js', '.json', '.map', '.otf', '.png', '.svg', '.ttf',
    '.txt', '.webp', '.woff', '.woff2',
}

# 前端源码目录下允许热更新的源码类型
FRONTEND_SOURCE_DIRS = {'static', 'frontend'}
FRONTEND_SOURCE_EXTENSIONS = {'.ts', '.tsx', '.jsx', '.vue'}

# 不需要重启的文件扩展名
NO_RESTART_EXTENSIONS = (
    STATIC_ASSET_EXTENSIONS
    | FRONTEND_SOURCE_EXTENSIONS
    | {'.yml', '.yaml'}
)

# 扫描时排除的目录
EXCLUDED_DIR_NAMES = {
    '.pytest_cache',
    '.git',
    '.github',
    '.claude',
    '.ace-tool',
    '__pycache__',
    'browser_data',
    'build',
    'data',
    'dist',
    'logs',
    'nginx',
    'node_modules',
    'qr_screenshots',
    'trajectory_history',
    'update_backup',
    'uploads',
    'venv',
}

# 即使文件类型匹配也不纳入热更新的文件
EXCLUDED_FILE_NAMES = {
    'Dockerfile',
    'Dockerfile-cn',
    'docker-compose.yml',
    'docker-compose-cn.yml',
    'global_config.yml',
    'update_files.json',
}


def calculate_md5(file_path: Path) -> str:
    """计算文件MD5"""
    if not file_path.exists():
        return ""
    
    md5_hash = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()


def get_file_size(file_path: Path) -> int:
    """获取文件大小"""
    if not file_path.exists():
        return 0
    return file_path.stat().st_size


def needs_restart(file_path: str) -> bool:
    """判断文件更新是否需要重启"""
    ext = Path(file_path).suffix.lower()
    return ext not in NO_RESTART_EXTENSIONS


def normalize_manifest_path(path: str) -> str:
    """规范化清单中的路径字符串"""
    return path.replace('\\', '/').lstrip('/')


def normalize_relative_path(path: Path) -> str:
    """规范化相对路径"""
    return path.as_posix()


def is_excluded_path(relative_path: Path) -> bool:
    """检查路径是否属于排除项"""
    if relative_path.name.startswith('.'):
        return True

    if relative_path.name in EXCLUDED_FILE_NAMES:
        return True

    return any(part in EXCLUDED_DIR_NAMES for part in relative_path.parts)


def is_updatable_file(relative_path: Path) -> bool:
    """判断文件是否应纳入热更新清单"""
    if is_excluded_path(relative_path):
        return False

    suffix = relative_path.suffix.lower()
    if suffix == '.py':
        return True

    if suffix == '.html':
        return True

    if not relative_path.parts:
        return False

    root_dir = relative_path.parts[0]
    if root_dir == 'static' and suffix in STATIC_ASSET_EXTENSIONS:
        return True

    return root_dir in FRONTEND_SOURCE_DIRS and suffix in FRONTEND_SOURCE_EXTENSIONS


def collect_updatable_files(base_dir: Path) -> List[str]:
    """自动扫描可热更新的业务文件和静态资源"""
    files: List[str] = []

    for root, dirnames, filenames in os.walk(base_dir, topdown=True):
        root_path = Path(root)
        relative_root = root_path.relative_to(base_dir)

        dirnames[:] = sorted(
            dirname
            for dirname in dirnames
            if not is_excluded_path(relative_root / dirname)
        )

        for filename in sorted(filenames):
            relative_path = relative_root / filename
            if is_updatable_file(relative_path):
                files.append(normalize_relative_path(relative_path))

    return sorted(files)


def run_git_command(base_dir: Path, args: List[str]) -> str:
    """执行 git 命令并返回标准输出"""
    result = subprocess.run(
        ['git', '-C', str(base_dir), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def get_previous_release_tag(base_dir: Path, current_version: str) -> Optional[str]:
    """获取当前版本之前的最近一个 tag"""
    try:
        output = run_git_command(base_dir, ['tag', '--list', 'v*', '--sort=-version:refname'])
    except Exception:
        return None

    for raw_tag in output.splitlines():
        tag = raw_tag.strip()
        if tag and tag != current_version:
            return tag

    return None


def load_manifest_from_tag(base_dir: Path, tag: Optional[str]) -> Optional[Dict[str, Any]]:
    """从历史 tag 中加载 update_files.json"""
    if not tag:
        return None

    owner, repo = read_repo_config()
    manifest_url = f"https://github.com/{owner}/{repo}/releases/download/{tag}/update_files.json"
    token = (
        os.getenv('UPDATE_GITHUB_TOKEN')
        or os.getenv('GITHUB_TOKEN')
        or os.getenv('GH_TOKEN')
    )
    headers = {
        'User-Agent': f'update-manifest-loader/{tag}',
        'Accept': 'application/octet-stream',
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'

    try:
        req = request.Request(manifest_url, headers=headers)
        with request.urlopen(req, timeout=30) as response:
            manifest_text = response.read().decode('utf-8')
    except Exception as exc:
        print(f"提示: 无法从 release {tag} 读取 update_files.json: {exc}")

        try:
            manifest_text = run_git_command(base_dir, ['show', f'{tag}:update_files.json'])
        except Exception as git_exc:
            print(f"提示: 无法从 tag {tag} 读取 update_files.json: {git_exc}")
            return None

    try:
        return json.loads(manifest_text)
    except json.JSONDecodeError as exc:
        print(f"提示: tag {tag} 的 update_files.json 解析失败: {exc}")
        return None


def extract_manifest_paths(entries: List[Any]) -> Set[str]:
    """从 manifest 条目中提取路径集合"""
    paths: Set[str] = set()

    for entry in entries or []:
        if isinstance(entry, dict):
            path = entry.get('path', '')
        else:
            path = str(entry)

        path = normalize_manifest_path(path).strip()
        if path:
            paths.add(path)

    return paths


def build_deleted_files(current_paths: Set[str], previous_manifest: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """基于历史 manifest 生成累积删除列表"""
    if not previous_manifest:
        return []

    previous_paths = extract_manifest_paths(previous_manifest.get('files', []))
    historical_deleted_paths = extract_manifest_paths(previous_manifest.get('deleted_files', []))
    deleted_paths = sorted((historical_deleted_paths | (previous_paths - current_paths)) - current_paths)

    return [
        {
            'path': path,
            'requires_restart': needs_restart(path),
            'description': '',
        }
        for path in deleted_paths
    ]


def build_raw_download_url(owner: str, repo: str, version: str, relative_path: str) -> str:
    """构建 GitHub raw 文件下载地址"""
    relative_path = relative_path.replace('\\', '/').lstrip('/')
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{version}/{relative_path}"


def read_version(base_dir: Path, fallback: str = "v1.0.0") -> str:
    """读取版本号"""
    version_file = base_dir / "static" / "version.txt"
    if version_file.exists():
        version = version_file.read_text(encoding='utf-8').strip()
        if version:
            return version
    return fallback


def read_repo_config() -> tuple[str, str]:
    """读取 GitHub 仓库配置，优先环境变量，其次默认值"""
    owner = os.environ.get("UPDATE_GITHUB_OWNER", "").strip()
    repo = os.environ.get("UPDATE_GITHUB_REPO", "").strip()
    if owner and repo:
        return owner, repo

    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    if repository and "/" in repository:
        repo_owner, repo_name = repository.split("/", 1)
        if repo_owner and repo_name:
            return repo_owner, repo_name

    return DEFAULT_GITHUB_OWNER, DEFAULT_GITHUB_REPO


def generate_manifest(
    base_dir: Path,
    version: str = "v1.0.0",
    owner: str = DEFAULT_GITHUB_OWNER,
    repo: str = DEFAULT_GITHUB_REPO,
    previous_manifest: Optional[Dict[str, Any]] = None,
) -> dict:
    """生成更新清单"""
    files = []

    for file_path in collect_updatable_files(base_dir):
        full_path = base_dir / file_path

        md5 = calculate_md5(full_path)
        size = get_file_size(full_path)

        files.append({
            'path': file_path.replace('\\', '/'),
            'md5': md5,
            'size': size,
            'download_url': build_raw_download_url(owner, repo, version, file_path),
            'requires_restart': needs_restart(file_path),
            'description': '',
        })

    current_paths = {file_info['path'] for file_info in files}
    deleted_files = build_deleted_files(current_paths, previous_manifest)
    
    manifest = {
        'version': version,
        'release_date': datetime.now().strftime('%Y-%m-%d'),
        'description': f'版本 {version} 更新',
        'min_version': 'v1.0.0',
        'changelog': [
            'GitHub Releases 热更新清单',
        ],
        'files': files,
        'deleted_files': deleted_files,
    }
    
    return manifest


def print_manifest_summary(manifest: dict):
    """打印清单摘要"""
    print("\n" + "=" * 60)
    print("更新清单摘要")
    print("=" * 60 + "\n")

    print(f"版本号: {manifest['version']}")
    print(f"发布日期: {manifest['release_date']}")
    print(f"文件数量: {len(manifest['files'])}")
    print(f"待删除文件数量: {len(manifest.get('deleted_files', []))}")
    total_size = sum(f['size'] for f in manifest['files'])
    print(f"总大小: {total_size / 1024:.2f} KB")
    print("示例下载地址:")
    if manifest['files']:
        print(f"  {manifest['files'][0]['download_url']}")


def main():
    # 获取项目根目录
    if len(sys.argv) > 1:
        base_dir = Path(sys.argv[1])
    else:
        base_dir = Path(__file__).parent
    
    # 获取版本号
    version = read_version(base_dir)
    if len(sys.argv) > 2:
        version = sys.argv[2]

    owner, repo = read_repo_config()
    if len(sys.argv) > 3:
        owner = sys.argv[3]
    if len(sys.argv) > 4:
        repo = sys.argv[4]

    previous_tag = get_previous_release_tag(base_dir, version)
    previous_manifest = load_manifest_from_tag(base_dir, previous_tag)
    
    print(f"项目目录: {base_dir}")
    print(f"版本号: {version}")
    print(f"GitHub 仓库: {owner}/{repo}")
    print(f"上一版本标签: {previous_tag or '无'}")
    
    # 生成清单
    manifest = generate_manifest(base_dir, version, owner, repo, previous_manifest=previous_manifest)
    
    # 保存JSON文件
    output_file = base_dir / "update_files.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\n已生成: {output_file}")
    
    # 打印摘要
    print_manifest_summary(manifest)
    
    print("\n" + "=" * 60)
    print("使用说明")
    print("=" * 60)
    print("""
1. 修改业务 Python 文件、HTML 页面或 static/ 下的静态资源
2. 更新 static/version.txt 为新的版本号后提交并 push 到 main
3. GitHub Actions 会自动扫描可热更新文件，生成 update_files.json 并创建同名 Release
4. 用户在前端点击"一键热更新"后，会先读取 GitHub Releases 最新版本，再从对应 tag 下载文件
""")


if __name__ == '__main__':
    main()
