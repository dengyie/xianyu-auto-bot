"""
自动热更新模块

支持在不重新下载整个exe/容器的情况下，自动更新少量修改的文件。

更新机制：
1. 从远程服务器获取更新清单（包含文件列表、版本、MD5哈希）
2. 比较本地文件与远程文件的哈希值
3. 只下载有变化的文件
4. 备份旧文件，下载新文件
5. 需要时重启应用

支持更新的文件类型：
- Python 源文件 (.py)
- 前端文件 (.js, .ts, .tsx, .jsx, .vue, .css, .html, .txt, .json)
- 配置文件 (.yml, .json)
- 静态资源（图片、字体等）
"""

import os
import sys
import json
import hashlib
import shutil
import tempfile
import asyncio
import aiohttp
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Any
from loguru import logger
from dataclasses import dataclass, asdict
from enum import Enum


class UpdateStatus(Enum):
    """更新状态"""
    IDLE = "idle"                      # 空闲
    CHECKING = "checking"              # 检查中
    DOWNLOADING = "downloading"        # 下载中
    INSTALLING = "installing"          # 安装中
    COMPLETED = "completed"            # 完成
    FAILED = "failed"                  # 失败
    RESTART_REQUIRED = "restart_required"  # 需要重启


@dataclass
class FileUpdate:
    """文件更新信息"""
    path: str                 # 相对路径
    md5: str                  # MD5哈希
    size: int                 # 文件大小
    download_url: str         # 下载URL
    version: str              # 文件版本
    requires_restart: bool    # 是否需要重启
    description: str = ""     # 更新说明


@dataclass
class DeletedFile:
    """待删除文件信息"""
    path: str
    requires_restart: bool
    description: str = ""


@dataclass
class UpdateManifest:
    """更新清单"""
    version: str                      # 版本号
    release_date: str                 # 发布日期
    description: str                  # 版本说明
    files: List[FileUpdate]           # 文件列表
    deleted_files: List[DeletedFile] = None  # 待删除文件列表
    min_version: str = ""             # 最低兼容版本
    changelog: List[str] = None       # 更新日志


@dataclass
class UpdateProgress:
    """更新进度"""
    status: UpdateStatus
    current_file: str = ""
    current_index: int = 0
    total_files: int = 0
    downloaded_bytes: int = 0
    total_bytes: int = 0
    message: str = ""
    error: str = ""


class AutoUpdater:
    """自动更新器"""

    # 默认 GitHub 更新源配置
    DEFAULT_GITHUB_API_BASE = "https://api.github.com"
    DEFAULT_GITHUB_RAW_BASE = "https://raw.githubusercontent.com"
    DEFAULT_GITHUB_OWNER = "dengyie"
    DEFAULT_GITHUB_REPO = "xianyu-auto-bot"
    
    # 可热更新的静态文件类型（通常不需要重启）
    HOT_UPDATABLE_EXTENSIONS = {
        '.css', '.eot', '.gif', '.html', '.ico', '.jpeg', '.jpg',
        '.js', '.json', '.jsx', '.map', '.otf', '.png', '.svg',
        '.ts', '.tsx', '.ttf', '.vue',
        '.txt', '.webp', '.woff', '.woff2', '.yml', '.yaml',
    }
    
    # 需要重启的文件类型
    RESTART_REQUIRED_EXTENSIONS = {'.py', '.pyd', '.so', '.dll', '.exe'}
    
    # 不允许更新的文件/目录
    EXCLUDED_PATHS = {
        'data/',
        'logs/',
        'browser_data/',
        'uploads/',
        '__pycache__/',
        '.git/',
        '.github/',
        '.claude/',
        '.ace-tool/',
        '.pytest_cache/',
        'build/',
        'nginx/',
        'dist/',
        'node_modules/',
        'qr_screenshots/',
        'trajectory_history/',
        'update_backup/',
        'venv/',
        'global_config.yml',  # 用户配置文件不更新
        'update_files.json',
        'Dockerfile',
        'Dockerfile-cn',
        'docker-compose.yml',
        'docker-compose-cn.yml',
    }
    
    def __init__(self, 
                 app_dir: Optional[str] = None,
                 update_server: Optional[str] = None,
                 github_owner: Optional[str] = None,
                 github_repo: Optional[str] = None,
                 github_token: Optional[str] = None,
                 current_version: str = "1.0.0"):
        """
        初始化更新器
        
        Args:
            app_dir: 应用目录，默认为当前工作目录
            update_server: 兼容旧参数，保留但不再作为默认更新源
            github_owner: GitHub 仓库 owner
            github_repo: GitHub 仓库 repo
            github_token: GitHub API Token（可选，用于提升限额）
            current_version: 当前版本号
        """
        self.app_dir = Path(app_dir) if app_dir else Path.cwd()
        self.github_owner = github_owner or os.getenv("UPDATE_GITHUB_OWNER", self.DEFAULT_GITHUB_OWNER)
        self.github_repo = github_repo or os.getenv("UPDATE_GITHUB_REPO", self.DEFAULT_GITHUB_REPO)
        self.github_token = (github_token or os.getenv("UPDATE_GITHUB_TOKEN", "")).strip()
        self.update_server = update_server or f"{self.github_owner}/{self.github_repo}"
        self.current_version = current_version
        self.backup_dir = self.app_dir / "update_backup"
        
        # 更新状态
        self.progress = UpdateProgress(status=UpdateStatus.IDLE)
        self._update_callbacks: List[callable] = []
        
        # 确保备份目录存在
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(
            "自动更新器初始化: "
            f"app_dir={self.app_dir}, repo={self.github_owner}/{self.github_repo}, version={self.current_version}"
        )
    
    def add_progress_callback(self, callback: callable):
        """添加进度回调"""
        self._update_callbacks.append(callback)
    
    def _notify_progress(self):
        """通知进度更新"""
        for callback in self._update_callbacks:
            try:
                callback(self.progress)
            except Exception as e:
                logger.error(f"进度回调执行失败: {e}")
    
    def _update_progress(self, **kwargs):
        """更新进度"""
        for key, value in kwargs.items():
            if hasattr(self.progress, key):
                setattr(self.progress, key, value)
        self._notify_progress()
    
    def _calculate_file_md5(self, file_path: Path) -> str:
        """计算文件MD5"""
        if not file_path.exists():
            return ""
        
        md5_hash = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()
    
    def _is_excluded(self, path: str) -> bool:
        """检查路径是否被排除"""
        path_lower = path.lower().replace('\\', '/')
        for excluded in self.EXCLUDED_PATHS:
            if path_lower.startswith(excluded.lower()) or excluded.lower() in path_lower:
                return True
        return False
    
    def _needs_restart(self, file_path: str) -> bool:
        """检查文件更新是否需要重启"""
        ext = Path(file_path).suffix.lower()
        return ext in self.RESTART_REQUIRED_EXTENSIONS

    def _safe_join_under_app_dir(self, manifest_path: str) -> Optional[Path]:
        """把更新清单里的相对路径安全解析到 app_dir 内部；越界 / 空 / 绝对路径都返回 None。

        防御面：绝对路径、空字符串、`..` 上跳、symlink 越界、Windows 反斜杠混淆。
        所有调用方在 None 时必须放弃该项操作并记录失败原因。
        """
        if not manifest_path:
            logger.warning("更新清单包含空路径，已拒绝")
            return None

        try:
            cleaned = Path(str(manifest_path).replace('\\', '/').strip())
            if cleaned.is_absolute() or not cleaned.parts:
                logger.warning(f"更新清单包含绝对/空路径，已拒绝: {manifest_path}")
                return None
            target = (self.app_dir / cleaned).resolve()
            target.relative_to(self.app_dir.resolve())
        except (ValueError, OSError) as exc:
            logger.warning(f"更新清单路径越界或无法解析: {manifest_path} ({exc})")
            return None

        return target

    def refresh_current_version(self) -> str:
        """从本地版本文件刷新当前版本号，避免长生命周期进程读到旧版本"""
        version = self.current_version or "1.0.0"
        version_file = self.app_dir / "static" / "version.txt"

        try:
            if version_file.exists():
                file_version = version_file.read_text(encoding="utf-8").strip()
                if file_version:
                    version = file_version
        except Exception as e:
            logger.warning(f"读取本地版本文件失败，继续使用缓存版本: {e}")
            return self.current_version

        if version != self.current_version:
            logger.info(f"检测到本地版本变更: {self.current_version} -> {version}")
            self.current_version = version

        return self.current_version

    def _build_request_headers(self, accept_json: bool = True) -> Dict[str, str]:
        """构建 GitHub 请求头"""
        headers = {
            "User-Agent": f"XianyuAutoReplyUpdater/{self.current_version}",
        }
        if accept_json:
            headers["Accept"] = "application/vnd.github+json"
        if self.github_token:
            headers["Authorization"] = f"Bearer {self.github_token}"
        return headers

    def _build_latest_release_url(self) -> str:
        """构建 GitHub 最新 release API 地址"""
        return (
            f"{self.DEFAULT_GITHUB_API_BASE}/repos/"
            f"{self.github_owner}/{self.github_repo}/releases/latest"
        )

    def _build_raw_file_url(self, tag: str, relative_path: str) -> str:
        """构建 GitHub raw 文件地址"""
        relative_path = relative_path.replace("\\", "/").lstrip("/")
        return (
            f"{self.DEFAULT_GITHUB_RAW_BASE}/"
            f"{self.github_owner}/{self.github_repo}/{tag}/{relative_path}"
        )

    def _extract_changelog(self, release_data: Dict[str, Any]) -> List[str]:
        """从 release body 中提取简易更新日志"""
        body = (release_data.get("body") or "").strip()
        if not body:
            return []

        changelog = []
        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(("- ", "* ", "+ ")):
                line = line[2:].strip()
            changelog.append(line)
        return changelog

    def _find_asset_download_url(self, release_data: Dict[str, Any], asset_name: str) -> Optional[str]:
        """查找指定 release asset 的浏览器下载地址"""
        for asset in release_data.get("assets", []) or []:
            if asset.get("name") == asset_name:
                return asset.get("browser_download_url")
        return None
    
    async def check_for_updates(self) -> Optional[UpdateManifest]:
        """
        检查是否有可用更新
        
        Returns:
            UpdateManifest: 更新清单，如果没有更新则返回None
        """
        self.refresh_current_version()
        self._update_progress(status=UpdateStatus.CHECKING, message="正在检查更新...")
        
        try:
            async with aiohttp.ClientSession() as session:
                release_url = self._build_latest_release_url()
                async with session.get(
                    release_url,
                    headers=self._build_request_headers(),
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        logger.warning(f"获取 GitHub Release 失败: HTTP {response.status}")
                        self._update_progress(status=UpdateStatus.IDLE, message="检查更新失败")
                        return None

                    release_data = await response.json()

                release_tag = (release_data.get("tag_name") or "").strip()
                if not release_tag:
                    logger.warning("GitHub Release 缺少 tag_name，无法检查更新")
                    self._update_progress(status=UpdateStatus.IDLE, message="检查更新失败")
                    return None

                manifest_url = self._find_asset_download_url(release_data, "update_files.json")
                if not manifest_url:
                    manifest_url = self._build_raw_file_url(release_tag, "update_files.json")

                async with session.get(
                    manifest_url,
                    headers=self._build_request_headers(accept_json=False),
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status != 200:
                        logger.warning(f"获取更新清单失败: HTTP {response.status}")
                        self._update_progress(status=UpdateStatus.IDLE, message="检查更新失败")
                        return None

                    manifest_text = await response.text()
                    try:
                        manifest_data = json.loads(manifest_text)
                    except json.JSONDecodeError as exc:
                        logger.warning(f"解析更新清单失败: {exc}")
                        self._update_progress(status=UpdateStatus.IDLE, message="检查更新失败")
                        return None

                manifest_version = manifest_data.get("version") or release_tag
                changelog = manifest_data.get("changelog") or self._extract_changelog(release_data)

                files = []
                for file_info in manifest_data.get("files", []):
                    file_path = file_info["path"].replace("\\", "/")
                    files.append(FileUpdate(
                        path=file_path,
                        md5=file_info.get("md5", ""),
                        size=file_info.get("size", 0),
                        download_url=self._build_raw_file_url(release_tag, file_path),
                        version=file_info.get("version", manifest_version),
                        requires_restart=file_info.get("requires_restart", self._needs_restart(file_path)),
                        description=file_info.get("description", "")
                    ))

                deleted_files = []
                for file_info in manifest_data.get("deleted_files", []):
                    file_path = file_info["path"].replace("\\", "/")
                    deleted_files.append(DeletedFile(
                        path=file_path,
                        requires_restart=file_info.get("requires_restart", self._needs_restart(file_path)),
                        description=file_info.get("description", "")
                    ))

                manifest = UpdateManifest(
                    version=manifest_version,
                    release_date=manifest_data.get("release_date") or release_data.get("published_at", ""),
                    description=manifest_data.get("description") or release_data.get("name") or f"版本 {manifest_version} 更新",
                    files=files,
                    deleted_files=deleted_files,
                    min_version=manifest_data.get("min_version", ""),
                    changelog=changelog
                )

                logger.info(
                    f"发现发布版本: {manifest.version}, 共 {len(files)} 个文件可用于比对更新, "
                    f"{len(deleted_files)} 个文件待删除"
                )
                self._update_progress(status=UpdateStatus.IDLE, message=f"已获取版本 {manifest.version} 的更新清单")

                return manifest
                    
        except asyncio.TimeoutError:
            logger.error("检查更新超时")
            self._update_progress(status=UpdateStatus.FAILED, error="检查更新超时")
            return None
        except Exception as e:
            logger.error(f"检查更新失败: {e}")
            self._update_progress(status=UpdateStatus.FAILED, error=str(e))
            return None
    
    async def get_files_to_update(self, manifest: UpdateManifest) -> List[FileUpdate]:
        """
        获取需要更新的文件列表（排除已是最新的文件）
        
        Args:
            manifest: 更新清单
            
        Returns:
            需要更新的文件列表
        """
        files_to_update = []
        excluded_count = 0
        up_to_date_count = 0
        
        for file_update in manifest.files:
            # 跳过被排除的文件
            if self._is_excluded(file_update.path):
                logger.debug(f"跳过排除的文件: {file_update.path}")
                excluded_count += 1
                continue
            
            local_path = self.app_dir / file_update.path
            
            # 如果服务端没有提供MD5，则始终更新该文件
            if not file_update.md5 or not file_update.md5.strip():
                files_to_update.append(file_update)
                logger.debug(f"需要更新（无MD5校验）: {file_update.path}")
                continue
            
            local_md5 = self._calculate_file_md5(local_path)
            
            # 如果本地文件不存在或MD5不匹配，则需要更新
            if local_md5 != file_update.md5:
                files_to_update.append(file_update)
                logger.debug(f"需要更新: {file_update.path} (本地MD5: {local_md5}, 远程MD5: {file_update.md5})")
            else:
                up_to_date_count += 1

        logger.info(
            f"更新检查完成: 共 {len(manifest.files)} 个文件, 需要更新 {len(files_to_update)} 个, "
            f"已是最新 {up_to_date_count} 个, 排除 {excluded_count} 个"
        )
        
        return files_to_update

    async def get_files_to_delete(self, manifest: UpdateManifest) -> List[DeletedFile]:
        """获取本地实际存在且允许删除的旧文件列表"""
        files_to_delete = []

        for deleted_file in manifest.deleted_files or []:
            if self._is_excluded(deleted_file.path):
                logger.debug(f"跳过排除的删除路径: {deleted_file.path}")
                continue

            local_path = self._safe_join_under_app_dir(deleted_file.path)
            if local_path is None:
                continue
            if local_path.exists() and local_path.is_file():
                files_to_delete.append(deleted_file)
                logger.debug(f"需要删除旧文件: {deleted_file.path}")

        return files_to_delete
    
    # 非关键文件，MD5校验失败时可以继续更新（仅警告不报错）
    NON_CRITICAL_FILES = {'version.txt', 'update_log.txt', 'changelog.txt'}
    
    async def download_file(self, file_update: FileUpdate, session: aiohttp.ClientSession) -> Optional[bytes]:
        """
        下载单个文件
        
        Args:
            file_update: 文件更新信息
            session: aiohttp会话
            
        Returns:
            文件内容，失败返回None
        """
        try:
            async with session.get(file_update.download_url, timeout=aiohttp.ClientTimeout(total=60)) as response:
                if response.status != 200:
                    logger.error(f"下载文件失败: {file_update.path}, HTTP {response.status}")
                    return None
                
                content = await response.read()
                
                # 验证MD5（如果服务端提供了MD5值）
                if file_update.md5 and file_update.md5.strip():
                    downloaded_md5 = hashlib.md5(content).hexdigest()
                    if downloaded_md5 != file_update.md5:
                        # 检查是否为非关键文件
                        file_name = Path(file_update.path).name
                        if file_name in self.NON_CRITICAL_FILES:
                            logger.warning(f"非关键文件MD5不匹配（忽略）: {file_update.path}, 期望: {file_update.md5}, 实际: {downloaded_md5}")
                            # 非关键文件，继续更新
                        else:
                            logger.error(f"文件MD5校验失败: {file_update.path}, 期望: {file_update.md5}, 实际: {downloaded_md5}")
                            return None
                    else:
                        logger.debug(f"文件MD5校验通过: {file_update.path}")
                else:
                    logger.debug(f"跳过MD5校验（服务端未提供）: {file_update.path}")
                
                return content
                
        except Exception as e:
            logger.error(f"下载文件异常: {file_update.path}, {e}")
            return None
    
    def _backup_file(self, file_path: Path) -> bool:
        """
        备份文件
        
        Args:
            file_path: 要备份的文件路径
            
        Returns:
            是否成功
        """
        if not file_path.exists():
            return True
        
        try:
            relative_path = file_path.relative_to(self.app_dir)
            backup_path = self.backup_dir / f"{datetime.now().strftime('%Y%m%d_%H%M%S')}" / relative_path
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, backup_path)
            logger.debug(f"备份文件: {file_path} -> {backup_path}")
            return True
        except Exception as e:
            logger.error(f"备份文件失败: {file_path}, {e}")
            return False

    def _cleanup_empty_parent_dirs(self, directory: Path):
        """清理因删除文件产生的空目录"""
        current = directory

        while current != self.app_dir and current != current.parent:
            try:
                current.rmdir()
            except OSError:
                break

            current = current.parent
    
    async def apply_updates(self, files_to_update: List[FileUpdate]) -> Tuple[bool, List[str], bool]:
        """
        应用更新
        
        Args:
            files_to_update: 需要更新的文件列表
            
        Returns:
            (是否成功, 更新的文件列表, 是否需要重启)
        """
        if not files_to_update:
            return True, [], False
        
        updated_files = []
        needs_restart = False
        total_size = sum(f.size for f in files_to_update)
        downloaded_size = 0
        
        self._update_progress(
            status=UpdateStatus.DOWNLOADING,
            total_files=len(files_to_update),
            total_bytes=total_size,
            message=f"正在下载 {len(files_to_update)} 个文件..."
        )
        
        async with aiohttp.ClientSession() as session:
            for index, file_update in enumerate(files_to_update):
                self._update_progress(
                    current_file=file_update.path,
                    current_index=index + 1,
                    message=f"正在下载: {file_update.path}"
                )
                
                # 下载文件
                content = await self.download_file(file_update, session)
                if content is None:
                    self._update_progress(
                        status=UpdateStatus.FAILED,
                        error=f"下载文件失败: {file_update.path}"
                    )
                    return False, updated_files, needs_restart
                
                downloaded_size += len(content)
                self._update_progress(downloaded_bytes=downloaded_size)
                
                # 备份并安装：先做路径越界校验
                local_path = self._safe_join_under_app_dir(file_update.path)
                if local_path is None:
                    self._update_progress(
                        status=UpdateStatus.FAILED,
                        error=f"非法更新路径，已拒绝写入: {file_update.path}"
                    )
                    return False, updated_files, needs_restart

                # 备份旧文件
                if not self._backup_file(local_path):
                    logger.warning(f"备份失败，继续更新: {file_update.path}")
                
                # 确保目录存在
                local_path.parent.mkdir(parents=True, exist_ok=True)
                
                # 写入新文件
                try:
                    self._update_progress(
                        status=UpdateStatus.INSTALLING,
                        message=f"正在安装: {file_update.path}"
                    )
                    
                    with open(local_path, 'wb') as f:
                        f.write(content)
                    
                    updated_files.append(file_update.path)
                    
                    if file_update.requires_restart:
                        needs_restart = True
                    
                    logger.info(f"更新文件成功: {file_update.path}")
                    
                except Exception as e:
                    logger.error(f"写入文件失败: {file_update.path}, {e}")
                    self._update_progress(
                        status=UpdateStatus.FAILED,
                        error=f"安装文件失败: {file_update.path}"
                    )
                    return False, updated_files, needs_restart
        
        # 更新完成
        if needs_restart:
            self._update_progress(
                status=UpdateStatus.RESTART_REQUIRED,
                message=f"更新完成，共更新 {len(updated_files)} 个文件，需要重启应用"
            )
        else:
            self._update_progress(
                status=UpdateStatus.COMPLETED,
                message=f"更新完成，共更新 {len(updated_files)} 个文件"
            )
        
        return True, updated_files, needs_restart

    async def apply_deletions(self, files_to_delete: List[DeletedFile]) -> Tuple[bool, List[str], bool]:
        """
        删除 manifest 中声明的旧文件

        Args:
            files_to_delete: 待删除文件列表

        Returns:
            (是否成功, 删除的文件列表, 是否需要重启)
        """
        if not files_to_delete:
            return True, [], False

        deleted_paths = []
        needs_restart = False

        self._update_progress(
            status=UpdateStatus.INSTALLING,
            total_files=len(files_to_delete),
            message=f"正在清理 {len(files_to_delete)} 个旧文件..."
        )

        for index, deleted_file in enumerate(files_to_delete):
            self._update_progress(
                current_file=deleted_file.path,
                current_index=index + 1,
                message=f"正在删除旧文件: {deleted_file.path}"
            )

            local_path = self._safe_join_under_app_dir(deleted_file.path)
            if local_path is None:
                self._update_progress(
                    status=UpdateStatus.FAILED,
                    error=f"非法删除路径，已拒绝执行: {deleted_file.path}"
                )
                return False, deleted_paths, needs_restart
            if not local_path.exists():
                continue

            if not self._backup_file(local_path):
                logger.warning(f"删除前备份失败，继续删除: {deleted_file.path}")

            try:
                local_path.unlink()
                deleted_paths.append(deleted_file.path)

                if deleted_file.requires_restart:
                    needs_restart = True

                self._cleanup_empty_parent_dirs(local_path.parent)
                logger.info(f"删除旧文件成功: {deleted_file.path}")
            except Exception as e:
                logger.error(f"删除旧文件失败: {deleted_file.path}, {e}")
                self._update_progress(
                    status=UpdateStatus.FAILED,
                    error=f"删除旧文件失败: {deleted_file.path}"
                )
                return False, deleted_paths, needs_restart

        return True, deleted_paths, needs_restart
    
    async def perform_update(self, manifest: Optional[UpdateManifest] = None) -> Dict[str, Any]:
        """
        执行完整的更新流程
        
        Args:
            manifest: 更新清单，如果为None则自动检查
            
        Returns:
            更新结果
        """
        result = {
            "success": False,
            "message": "",
            "updated_files": [],
            "deleted_files": [],
            "needs_restart": False,
            "new_version": ""
        }
        
        try:
            self.refresh_current_version()
            # 检查更新
            if manifest is None:
                manifest = await self.check_for_updates()
            
            if manifest is None:
                result["message"] = "没有可用更新"
                result["success"] = True
                return result
            
            result["new_version"] = manifest.version
            
            # 获取需要更新的文件
            files_to_update = await self.get_files_to_update(manifest)
            files_to_delete = await self.get_files_to_delete(manifest)
            
            if not files_to_update and not files_to_delete:
                result["message"] = "所有文件已是最新"
                result["success"] = True
                return result
            
            logger.info(
                f"开始更新到版本 {manifest.version}: {len(files_to_update)} 个文件更新, "
                f"{len(files_to_delete)} 个文件待删除"
            )
            
            # 应用更新
            success, updated_files, needs_restart = await self.apply_updates(files_to_update)
            deleted_files: List[str] = []

            if success:
                success, deleted_files, delete_restart = await self.apply_deletions(files_to_delete)
                needs_restart = needs_restart or delete_restart
            
            result["success"] = success
            result["updated_files"] = updated_files
            result["deleted_files"] = deleted_files
            result["needs_restart"] = needs_restart
            
            if success:
                message_parts = []
                if updated_files:
                    message_parts.append(f"更新 {len(updated_files)} 个文件")
                if deleted_files:
                    message_parts.append(f"删除 {len(deleted_files)} 个旧文件")

                result["message"] = f"成功{'，'.join(message_parts) if message_parts else '处理变更'}到版本 {manifest.version}"
                if needs_restart:
                    result["message"] += "，需要重启应用生效"
                
                # 更新成功后，保存文件哈希清单（用于以后对比）
                self.save_file_hashes(manifest.version, updated_files, deleted_files)
            else:
                result["message"] = "更新过程中出现错误"
            
            return result
            
        except Exception as e:
            logger.error(f"更新失败: {e}")
            result["message"] = f"更新失败: {str(e)}"
            self._update_progress(status=UpdateStatus.FAILED, error=str(e))
            return result
    
    def get_local_file_hashes(self, file_patterns: List[str] = None) -> Dict[str, str]:
        """
        获取本地文件的MD5哈希值
        
        Args:
            file_patterns: 文件模式列表，默认为常见的可更新文件
            
        Returns:
            {文件路径: MD5哈希}
        """
        if file_patterns is None:
            try:
                from generate_update_manifest import collect_updatable_files

                file_hashes = {}
                for relative_path in collect_updatable_files(self.app_dir):
                    if self._is_excluded(relative_path):
                        continue

                    file_path = self.app_dir / relative_path
                    file_hashes[relative_path] = self._calculate_file_md5(file_path)

                return file_hashes
            except Exception as e:
                logger.debug(f"使用 manifest 扫描规则获取本地哈希失败，回退到后缀模式: {e}")

            file_patterns = [
                '*.py', '*.js', '*.ts', '*.tsx', '*.jsx', '*.vue',
                '*.css', '*.html', '*.txt', '*.json', '*.map',
                '*.yml', '*.yaml', '*.png', '*.jpg', '*.jpeg',
                '*.gif', '*.svg', '*.ico', '*.webp', '*.woff',
                '*.woff2', '*.eot', '*.otf', '*.ttf',
            ]
        
        file_hashes = {}
        
        for pattern in file_patterns:
            for file_path in self.app_dir.rglob(pattern):
                try:
                    relative_path = str(file_path.relative_to(self.app_dir)).replace('\\', '/')
                    
                    # 跳过排除的路径
                    if self._is_excluded(relative_path):
                        continue
                    
                    file_hashes[relative_path] = self._calculate_file_md5(file_path)
                except Exception as e:
                    logger.debug(f"计算文件哈希失败: {file_path}, {e}")
        
        return file_hashes
    
    def cleanup_old_backups(self, keep_days: int = 7):
        """
        清理旧的备份文件
        
        Args:
            keep_days: 保留天数
        """
        try:
            cutoff_time = datetime.now().timestamp() - (keep_days * 24 * 60 * 60)
            
            for backup_dir in self.backup_dir.iterdir():
                if backup_dir.is_dir():
                    if backup_dir.stat().st_mtime < cutoff_time:
                        shutil.rmtree(backup_dir)
                        logger.info(f"清理旧备份: {backup_dir}")
                        
        except Exception as e:
            logger.error(f"清理备份失败: {e}")
    
    def save_file_hashes(
        self,
        version: str,
        updated_files: List[str] = None,
        deleted_files: List[str] = None,
    ):
        """
        保存文件哈希清单到本地
        
        更新完成后调用此方法，记录所有文件的MD5哈希值，
        方便以后对比哪些文件发生了变化。
        
        Args:
            version: 当前版本号
            updated_files: 本次更新的文件列表（可选）
        """
        try:
            hash_file = self.app_dir / "data" / "file_hashes.json"
            hash_file.parent.mkdir(parents=True, exist_ok=True)
            
            # 获取所有可更新文件的哈希
            all_hashes = self.get_local_file_hashes()
            
            # 构建哈希清单
            manifest = {
                "version": version,
                "updated_at": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                "total_files": len(all_hashes),
                "files": all_hashes
            }
            
            # 如果有本次更新的文件列表，单独记录
            if updated_files:
                manifest["last_updated_files"] = updated_files
                manifest["last_updated_count"] = len(updated_files)
            if deleted_files:
                manifest["last_deleted_files"] = deleted_files
                manifest["last_deleted_count"] = len(deleted_files)
            
            # 保存到文件
            with open(hash_file, 'w', encoding='utf-8') as f:
                json.dump(manifest, f, ensure_ascii=False, indent=2)
            
            logger.info(f"已保存文件哈希清单: {hash_file}, 共 {len(all_hashes)} 个文件")
            
        except Exception as e:
            logger.error(f"保存文件哈希清单失败: {e}")
    
    def load_file_hashes(self) -> Optional[Dict[str, Any]]:
        """
        加载本地保存的文件哈希清单
        
        Returns:
            哈希清单字典，如果不存在则返回None
        """
        try:
            hash_file = self.app_dir / "data" / "file_hashes.json"
            
            if not hash_file.exists():
                return None
            
            with open(hash_file, 'r', encoding='utf-8') as f:
                return json.load(f)
                
        except Exception as e:
            logger.error(f"加载文件哈希清单失败: {e}")
            return None
    
    def compare_file_hashes(self) -> Dict[str, Any]:
        """
        比较当前文件与保存的哈希清单
        
        Returns:
            比较结果，包含变化的文件列表
        """
        result = {
            "has_changes": False,
            "saved_version": None,
            "changed_files": [],
            "new_files": [],
            "deleted_files": [],
            "unchanged_files": []
        }
        
        try:
            saved_manifest = self.load_file_hashes()
            
            if saved_manifest is None:
                result["message"] = "没有保存的哈希清单，无法比较"
                return result
            
            result["saved_version"] = saved_manifest.get("version")
            saved_hashes = saved_manifest.get("files", {})
            
            # 获取当前文件哈希
            current_hashes = self.get_local_file_hashes()
            
            # 比较文件
            all_files = set(saved_hashes.keys()) | set(current_hashes.keys())
            
            for file_path in all_files:
                saved_md5 = saved_hashes.get(file_path)
                current_md5 = current_hashes.get(file_path)
                
                if saved_md5 is None:
                    # 新增的文件
                    result["new_files"].append(file_path)
                elif current_md5 is None:
                    # 删除的文件
                    result["deleted_files"].append(file_path)
                elif saved_md5 != current_md5:
                    # 修改的文件
                    result["changed_files"].append({
                        "path": file_path,
                        "old_md5": saved_md5,
                        "new_md5": current_md5
                    })
                else:
                    # 未变化的文件
                    result["unchanged_files"].append(file_path)
            
            result["has_changes"] = bool(result["changed_files"] or result["new_files"] or result["deleted_files"])
            result["message"] = f"比较完成: {len(result['changed_files'])} 个文件修改, {len(result['new_files'])} 个新增, {len(result['deleted_files'])} 个删除"
            
        except Exception as e:
            logger.error(f"比较文件哈希失败: {e}")
            result["message"] = f"比较失败: {str(e)}"
        
        return result


# 全局更新器实例
_updater: Optional[AutoUpdater] = None


def get_updater() -> AutoUpdater:
    """获取全局更新器实例"""
    global _updater
    if _updater is None:
        # 尝试从版本文件读取当前版本
        version = "1.0.0"
        try:
            version_file = Path(__file__).parent / "static" / "version.txt"
            if version_file.exists():
                version = version_file.read_text().strip()
        except:
            pass
        
        _updater = AutoUpdater(current_version=version)
    
    return _updater


def init_updater(app_dir: str = None, update_server: str = None, current_version: str = None) -> AutoUpdater:
    """
    初始化全局更新器
    
    Args:
        app_dir: 应用目录
        update_server: 更新服务器地址
        current_version: 当前版本号
    """
    global _updater
    
    if current_version is None:
        try:
            version_file = Path(app_dir or ".") / "static" / "version.txt"
            if version_file.exists():
                current_version = version_file.read_text().strip()
            else:
                current_version = "1.0.0"
        except:
            current_version = "1.0.0"
    
    _updater = AutoUpdater(
        app_dir=app_dir,
        update_server=update_server,
        current_version=current_version
    )
    
    return _updater
