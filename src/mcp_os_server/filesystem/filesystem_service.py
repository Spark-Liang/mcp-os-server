"""核心文件系统服务类"""

import asyncio
import glob
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
import fnmatch

from .models import (
    FileReadResult, 
    DirectoryItem, 
    FileInfo, 
    EditChange, 
    FileEditResult
)

logger = logging.getLogger(__name__)


class FilesystemService:
    """提供安全的文件系统操作服务"""

    def __init__(self, allowed_dirs: List[str], features: Optional[List] = None):
        """
        初始化文件系统服务

        Args:
            allowed_dirs: 允许访问的目录列表
            features: 文件系统服务功能特性列表
        """
        if not allowed_dirs:
            raise ValueError("至少需要指定一个允许的目录")

        # 保存功能特性
        self._features = features or []

        # 标准化路径并确保是绝对路径
        resolved_dirs = []
        for dir_path in allowed_dirs:
            # 将所有允许的目录转换为Path对象并进行标准化处理
            abs_path = Path(dir_path).resolve()
            resolved_dirs.append(abs_path)
        self._allowed_dirs = resolved_dirs
        
        logger.info("初始化文件系统服务，允许的目录: %s", self._allowed_dirs)
        if self._features:
            logger.info("启用的功能特性: %s", [f.name for f in self._features])

    @property
    def features(self) -> Sequence:
        """获取功能特性列表"""
        return tuple(self._features)

    @property
    def allowed_dirs(self) -> Sequence[Path]:
        """获取允许访问的目录列表"""
        return tuple(self._allowed_dirs)

    def assert_is_allowed_and_resolve(self, path: str) -> Path:
        """
        检查路径是否在允许的目录中，如果允许则返回解析后的绝对路径

        Args:
            path: 要检查的路径

        Returns:
            Path: 解析后的绝对路径

        Raises:
            PermissionError: 如果路径不在允许的目录中
        """
        # 检查是否启用了 SupportCursorDirectoryFormat 特性
        from .models import FileSystemServiceFeature
        support_cursor_format = any(
            f == FileSystemServiceFeature.SupportCursorDirectoryFormat
            for f in self._features
        )
        
        # 如果启用了 Cursor 目录格式支持，需要进行路径转换
        # 严格匹配 /<盘符>: 格式，其中盘符必须是字母
        if (support_cursor_format and 
            path.startswith('/') and 
            len(path) > 3 and 
            path[2] == ':' and 
            path[1].isalpha()):
            # 处理 /e:/... 格式的路径，转换为 e:/...
            path = path[1:]
        
        # 将传入路径转换为Path对象并标准化
        abs_path = Path(path).resolve()

        for allowed_dir_path in self._allowed_dirs:
            try:
                # 使用Path.is_relative_to进行检查
                if abs_path.is_relative_to(allowed_dir_path):
                    return abs_path
            except ValueError:
                # Path.is_relative_to可能会在不同驱动器上抛出ValueError，这里捕获并跳过
                continue

        raise PermissionError(f"路径不在允许的目录中: {path}")

    def is_path_allowed(self, path: str) -> bool:
        """
        检查路径是否在允许的目录中

        Args:
            path: 要检查的路径

        Returns:
            是否允许访问该路径
        """
        try:
            self.assert_is_allowed_and_resolve(path)
            return True
        except PermissionError:
            return False

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
        resolved_path = self.assert_is_allowed_and_resolve(path)

        # 使用asyncio在线程中执行IO操作
        def _read_sync():
            with open(resolved_path, "r", encoding="utf-8") as f:
                return f.read()

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _read_sync)

    async def read_multiple_files(self, paths: List[str]) -> Dict[str, FileReadResult]:
        """
        读取多个文件内容

        Args:
            paths: 文件路径列表

        Returns:
            字典，键为路径，值为FileReadResult对象
        """
        results = {}

        for path in paths:
            try:
                content = await self.read_file(path)
                results[path] = FileReadResult(success=True, content=content, error=None)
            except Exception as e:
                results[path] = FileReadResult(success=False, content=None, error=str(e))

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
        resolved_path = self.assert_is_allowed_and_resolve(path)

        def _write_sync():
            # 确保父目录存在
            os.makedirs(os.path.dirname(resolved_path), exist_ok=True)
            with open(resolved_path, "w", encoding="utf-8") as f:
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
        resolved_path = self.assert_is_allowed_and_resolve(path)

        def _create_sync():
            os.makedirs(resolved_path, exist_ok=True)

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, _create_sync)

    async def list_directory(self, path: str) -> List[DirectoryItem]:
        """
        列出目录内容

        Args:
            path: 目录路径

        Returns:
            目录内容列表

        Raises:
            PermissionError: 路径不被允许
            FileNotFoundError: 目录不存在
        """
        resolved_path = self.assert_is_allowed_and_resolve(path)

        def _list_sync():
            items = []
            for item_name in os.listdir(resolved_path):
                item_path = os.path.join(resolved_path, item_name)
                is_dir = os.path.isdir(item_path)
                items.append(DirectoryItem(
                    name=item_name,
                    type="directory" if is_dir else "file",
                    path=item_path
                ))
            return sorted(items, key=lambda x: (x.type == "file", x.name))

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
        resolved_source = self.assert_is_allowed_and_resolve(source)
        resolved_destination = self.assert_is_allowed_and_resolve(destination)

        def _move_sync():
            if os.path.exists(resolved_destination):
                raise FileExistsError(f"目标路径已存在: {destination}")

            # 确保目标目录存在
            os.makedirs(os.path.dirname(resolved_destination), exist_ok=True)
            shutil.move(resolved_source, resolved_destination)

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
        resolved_path = self.assert_is_allowed_and_resolve(path)

        def _search_sync():
            results = []
            search_pattern = os.path.join(resolved_path, "**", pattern)

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

    async def get_file_info(self, path: str) -> FileInfo:
        """
        获取文件/目录信息

        Args:
            path: 文件或目录路径

        Returns:
            文件信息对象

        Raises:
            PermissionError: 路径不被允许
            FileNotFoundError: 文件不存在
        """
        resolved_path = self.assert_is_allowed_and_resolve(path)

        def _get_info_sync():
            stat_result = os.stat(resolved_path)

            return FileInfo(
                type="directory" if os.path.isdir(resolved_path) else "file",
                size=stat_result.st_size,
                created=datetime.fromtimestamp(stat_result.st_ctime).isoformat(),
                modified=datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
                accessed=datetime.fromtimestamp(stat_result.st_atime).isoformat(),
                permissions=oct(stat_result.st_mode)[-3:],
                absolute_path=os.path.abspath(resolved_path)
            )

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _get_info_sync)

    def list_allowed_directories(self) -> List[str]:
        """
        返回允许访问的目录列表

        Returns:
            允许访问的目录列表
        """
        # 将Path对象转换回字符串列表返回
        return [str(p) for p in self._allowed_dirs.copy()]

    async def edit_file(
        self, path: str, edits: List[Dict[str, str]], dry_run: bool = False
    ) -> FileEditResult:
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
        resolved_path = self.assert_is_allowed_and_resolve(path)

        def _edit_sync():
            # 读取原始内容
            with open(resolved_path, "r", encoding="utf-8") as f:
                original_content = f.read()

            modified_content = original_content
            changes_made = []

            # 应用编辑操作
            for edit in edits:
                old_text = edit.get("oldText", "")
                new_text = edit.get("newText", "")

                if old_text in modified_content:
                    modified_content = modified_content.replace(old_text, new_text, 1)
                    changes_made.append(EditChange(
                        old=old_text,
                        new=new_text,
                        applied=True,
                        error=None
                    ))
                else:
                    changes_made.append(EditChange(
                        old=old_text,
                        new=new_text,
                        applied=False,
                        error="未找到匹配的文本"
                    ))

            result_data = {
                "changes_made": changes_made,
                "content_changed": modified_content != original_content
            }

            if dry_run:
                result_data["preview"] = modified_content
            else:
                # 实际写入文件
                if modified_content != original_content:
                    with open(resolved_path, "w", encoding="utf-8") as f:
                        f.write(modified_content)
                    result_data["message"] = "文件已成功更新"
                else:
                    result_data["message"] = "没有进行任何更改"

            return FileEditResult(**result_data)

        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _edit_sync)
