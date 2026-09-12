#!/usr/bin/env python3
"""日志收集器：loguru 函数 sink 直写内存环形队列，供面板实时日志接口消费。

旧实现每 0.5 秒轮询读回日志文件再正则解析，已废弃；现在内存队列与
文件输出由同一次 logger 调用直接驱动，无读回延迟与解析成本。
"""

import os
import threading
from collections import deque
from typing import Dict, List, Optional
from pathlib import Path


class FileLogCollector:
    """内存日志环形队列（loguru sink 直写）+ 实时/按日文件输出配置"""

    def __init__(self, max_logs: int = 2000, root: Optional[Path] = None):
        self.max_logs = max_logs
        self.logs = deque(maxlen=max_logs)
        self.lock = threading.Lock()
        self.root = (root or Path(__file__).resolve().parent).resolve()

        # 实时日志文件路径
        self.log_file = None

        self.setup_file_monitoring()

    def setup_file_monitoring(self):
        """选择实时日志文件路径并初始化 loguru 输出"""
        # 查找日志文件
        possible_files = [
            self.root / "xianyu.log",
            self.root / "app.log",
            self.root / "system.log",
            self.root / "logs" / "xianyu.log",
            self.root / "logs" / "app.log",
        ]

        for file_path in possible_files:
            if file_path.exists():
                self.log_file = str(file_path)
                break

        if not self.log_file:
            # 兼容旧版本的根目录日志，但不重新选择不可写的遗留文件。
            legacy_log_file = self.root / "realtime.log"
            if legacy_log_file.is_file() and os.access(legacy_log_file, os.W_OK):
                self.log_file = str(legacy_log_file)
            else:
                self.log_file = str(self.root / "logs" / "realtime.log")

        self.setup_loguru_output()

    def setup_loguru_output(self):
        """配置 loguru：文件输出（实时 + 按日轮转）+ 内存队列 sink"""
        try:
            from loguru import logger
        except ImportError:
            return

        # 确保logs目录存在
        logs_dir = self.root / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)

        # 实时日志文件输出（供离线排查；内存队列不经由文件回读）
        logger.add(
            self.log_file,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} - {message}",
            level="INFO",
            rotation="10 MB",
            retention="3 days",
            enqueue=False,
            buffering=1
        )

        # 按日期轮转的日志文件输出到logs目录
        logger.add(
            str(logs_dir / "xianyu_{time:YYYY-MM-DD}.log"),
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
            level="INFO",
            rotation="00:00",  # 每天午夜轮转
            retention="7 days",  # 保留7天
            compression="zip",  # 压缩旧日志
            enqueue=False,
            buffering=1,
            encoding="utf-8"
        )

        # 内存环形队列 sink：面板 /logs 接口直接读这里
        logger.add(self._enqueue_entry, level="INFO")

        logger.info("日志收集器已启动（内存队列 + 实时日志 + 按日期轮转日志）")

    def _enqueue_entry(self, message):
        """loguru 函数 sink：直接构造结构化日志条目写入内存队列"""
        record = message.record
        entry = {
            "timestamp": record["time"].isoformat(),
            "level": record["level"].name,
            "source": record["name"],
            "function": record["function"],
            "line": record["line"],
            "message": record["message"],
        }
        with self.lock:
            self.logs.append(entry)

    def get_logs(self, lines: int = 200, level_filter: str = None, source_filter: str = None) -> List[Dict]:
        """获取日志记录"""
        with self.lock:
            logs_list = list(self.logs)

        # 应用过滤器
        if level_filter:
            logs_list = [log for log in logs_list if log['level'] == level_filter]

        if source_filter:
            logs_list = [log for log in logs_list if source_filter.lower() in log['source'].lower()]

        # 返回最后N行
        return logs_list[-lines:] if len(logs_list) > lines else logs_list

    def clear_logs(self):
        """清空日志"""
        with self.lock:
            self.logs.clear()

    def get_stats(self) -> Dict:
        """获取日志统计信息"""
        with self.lock:
            total_logs = len(self.logs)

            # 统计各级别日志数量
            level_counts = {}
            source_counts = {}

            for log in self.logs:
                level = log['level']
                source = log['source']

                level_counts[level] = level_counts.get(level, 0) + 1
                source_counts[source] = source_counts.get(source, 0) + 1

            return {
                "total_logs": total_logs,
                "level_counts": level_counts,
                "source_counts": source_counts,
                "max_capacity": self.max_logs,
                "log_file": self.log_file
            }


# 全局文件日志收集器实例
_file_collector = None
_file_collector_lock = threading.Lock()


def get_file_log_collector(root: Optional[Path] = None) -> FileLogCollector:
    """获取全局文件日志收集器实例"""
    global _file_collector

    if _file_collector is None:
        with _file_collector_lock:
            if _file_collector is None:
                _file_collector = FileLogCollector(max_logs=2000, root=root)

    return _file_collector


def setup_file_logging(root: Optional[Path] = None):
    """设置文件日志系统"""
    collector = get_file_log_collector(root=root)
    return collector


if __name__ == "__main__":
    # 测试日志收集器
    collector = setup_file_logging()

    # 生成一些测试日志
    from loguru import logger

    logger.info("日志收集器测试开始")
    logger.debug("这是调试信息")
    logger.warning("这是警告信息")
    logger.error("这是错误信息")
    logger.info("日志收集器测试结束")

    # 获取日志
    logs = collector.get_logs(10)
    print(f"收集到 {len(logs)} 条日志:")
    for log in logs:
        print(f"  [{log['level']}] {log['source']}: {log['message']}")

    # 获取统计信息
    stats = collector.get_stats()
    print(f"\n统计信息: {stats}")
