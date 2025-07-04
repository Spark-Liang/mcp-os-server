"""核心文件系统服务类"""

import asyncio
import glob
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
import fnmatch

logger = logging.getLogger(__name__)


class FilesystemService:
    """提供安全的文件系统操作服务"""

    def __init__(self, allowed_dirs: List[str]):
        """
        初始化文件系统服务

        Args:
            allowed_dirs: 允许访问的目录列表
        """
        if not allowed_dirs:
            raise ValueError("至少需要指定一个允许的目录")

        # 标准化路径并确保是绝对路径
        self.allowed_dirs = []
        for dir_path in allowed_dirs:
            # 将所有允许的目录转换为Path对象并进行标准化处理
            abs_path = Path(dir_path).resolve()
            self.allowed_dirs.append(abs_path)
        logger.info("初始化文件系统服务，允许的目录: %s", self.allowed_dirs)

    def is_path_allowed(self, path: str) -> bool:
        """
        检查路径是否在允许的目录中

        Args:
            path: 要检查的路径

        Returns:
            是否允许访问该路径
        """
        # 将传入路径转换为Path对象并标准化
        abs_path = Path(path).resolve()

        for allowed_dir_path in self.allowed_dirs:
            try:
                # 使用Path.is_relative_to进行检查
                if abs_path.is_relative_to(allowed_dir_path):
                    return True
            except ValueError:
                # Path.is_relative_to可能会在不同驱动器上抛出ValueError，这里捕获并跳过
                continue

        return False

    def _ensure_path_allowed(self, path: str) -> None:
        """确保路径被允许访问，否则抛出异常"""
        if not self.is_path_allowed(path):
            raise PermissionError(f"路径不在允许的目录中: {path}")

    async def read_file(self, path: str) -> str:
        """
        读取文件内容

        Args:
            path: 文件路径

        Returns:
            文件内容

        Raises:
            PermissionError: 路径不被允许
            FileNotFoundError: 文件不存在
        """
        self._ensure_path_allowed(path)

        # 使用asyncio在线程中执行IO操作
        def _read_sync():
            with open(path, "r", encoding="utf-8") as f:
                return f.read()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _read_sync)

    async def read_multiple_files(self, paths: List[str]) -> Dict[str, Any]:
        """
        读取多个文件内容

        Args:
            paths: 文件路径列表

        Returns:
            字典，键为路径，值为内容或错误信息
        """
        results = {}

        for path in paths:
            try:
                content = await self.read_file(path)
                results[path] = {"success": True, "content": content}
            except Exception as e:
                results[path] = {"success": False, "error": str(e)}

        return results

    async def write_file(self, path: str, content: str) -> None:
        """
        写入文件内容

        Args:
            path: 文件路径
            content: 文件内容

        Raises:
            PermissionError: 路径不被允许
        """
        self._ensure_path_allowed(path)

        def _write_sync():
            # 确保父目录存在
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _write_sync)

    async def create_directory(self, path: str) -> None:
        """
        创建目录

        Args:
            path: 目录路径

        Raises:
            PermissionError: 路径不被允许
        """
        self._ensure_path_allowed(path)

        def _create_sync():
            os.makedirs(path, exist_ok=True)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _create_sync)

    async def list_directory(self, path: str) -> List[Dict[str, Any]]:
        """
        列出目录内容

        Args:
            path: 目录路径

        Returns:
            目录内容列表，每个项目包含名称和类型

        Raises:
            PermissionError: 路径不被允许
            FileNotFoundError: 目录不存在
        """
        self._ensure_path_allowed(path)

        def _list_sync():
            items = []
            for item_name in os.listdir(path):
                item_path = os.path.join(path, item_name)
                is_dir = os.path.isdir(item_path)
                items.append(
                    {
                        "name": item_name,
                        "type": "directory" if is_dir else "file",
                        "path": item_path,
                    }
                )
            return sorted(items, key=lambda x: (x["type"] == "file", x["name"]))

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _list_sync)

    async def move_file(self, source: str, destination: str) -> None:
        """
        移动或重命名文件/目录

        Args:
            source: 源路径
            destination: 目标路径

        Raises:
            PermissionError: 路径不被允许
            FileNotFoundError: 源文件不存在
            FileExistsError: 目标文件已存在
        """
        self._ensure_path_allowed(source)
        self._ensure_path_allowed(destination)

        def _move_sync():
            if os.path.exists(destination):
                raise FileExistsError(f"目标路径已存在: {destination}")

            # 确保目标目录存在
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.move(source, destination)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _move_sync)

    async def search_files(
        self, path: str, pattern: str, exclude_patterns: Optional[List[str]] = None
    ) -> List[str]:
        """
        搜索文件

        Args:
            path: 搜索起始路径
            pattern: 搜索模式（支持glob模式）
            exclude_patterns: 排除模式列表

        Returns:
            匹配的文件路径列表

        Raises:
            PermissionError: 路径不被允许
        """
        self._ensure_path_allowed(path)

        def _search_sync():
            results = []
            search_pattern = os.path.join(path, "**", pattern)

            for match in glob.glob(search_pattern, recursive=True):
                # 检查是否应该排除
                should_exclude = False
                if exclude_patterns:
                    for exclude_pattern in exclude_patterns:
                        if fnmatch.fnmatch(
                            os.path.basename(match), exclude_pattern
                        ):
                            should_exclude = True
                            break

                if not should_exclude:
                    results.append(match)

            return results

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _search_sync)

    async def get_file_info(self, path: str) -> Dict[str, Any]:
        """
        获取文件/目录信息

        Args:
            path: 文件或目录路径

        Returns:
            文件信息字典

        Raises:
            PermissionError: 路径不被允许
            FileNotFoundError: 文件不存在
        """
        self._ensure_path_allowed(path)

        def _get_info_sync():
            stat_result = os.stat(path)

            return {
                "type": "directory" if os.path.isdir(path) else "file",
                "size": stat_result.st_size,
                "created": datetime.fromtimestamp(stat_result.st_ctime).isoformat(),
                "modified": datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
                "accessed": datetime.fromtimestamp(stat_result.st_atime).isoformat(),
                "permissions": oct(stat_result.st_mode)[-3:],
                "absolute_path": os.path.abspath(path),
            }

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _get_info_sync)

    def list_allowed_directories(self) -> List[str]:
        """
        返回允许访问的目录列表

        Returns:
            允许访问的目录列表
        """
        # 将Path对象转换回字符串列表返回
        return [str(p) for p in self.allowed_dirs.copy()]

    async def edit_file(
        self, path: str, edits: List[Dict[str, str]], dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        编辑文件内容

        Args:
            path: 文件路径
            edits: 编辑操作列表，每个操作包含oldText和newText
            dry_run: 是否为预览模式（不实际修改文件）

        Returns:
            编辑结果信息

        Raises:
            PermissionError: 路径不被允许
            FileNotFoundError: 文件不存在
        """
        self._ensure_path_allowed(path)

        def _edit_sync():
            # 读取原始内容
            with open(path, "r", encoding="utf-8") as f:
                original_content = f.read()

            modified_content = original_content
            changes_made = []

            # 应用编辑操作
            for edit in edits:
                old_text = edit.get("oldText", "")
                new_text = edit.get("newText", "")

                if old_text in modified_content:
                    modified_content = modified_content.replace(old_text, new_text, 1)
                    changes_made.append(
                        {"old": old_text, "new": new_text, "applied": True}
                    )
                else:
                    changes_made.append(
                        {
                            "old": old_text,
                            "new": new_text,
                            "applied": False,
                            "error": "未找到匹配的文本",
                        }
                    )

            result = {
                "changes_made": changes_made,
                "content_changed": modified_content != original_content,
            }

            if dry_run:
                result["preview"] = modified_content
            else:
                # 实际写入文件
                if modified_content != original_content:
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(modified_content)
                    result["message"] = "文件已成功更新"
                else:
                    result["message"] = "没有进行任何更改"

            return result

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _edit_sync)
