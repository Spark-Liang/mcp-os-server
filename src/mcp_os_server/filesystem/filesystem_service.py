"""核心文件系统服务类"""

import fnmatch
import glob
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

import anyio
import anyio.to_thread

from .models import (
    DirectoryItem,
    EditChange,
    FileEditResult,
    FileInfo,
    TextFileReadResult,
)

logger = logging.getLogger(__name__)

StrOrPath = Union[str, Path]


class FilesystemService:
    """提供安全的文件系统操作服务"""

    def __init__(self, features: Optional[List] = None):
        """
        初始化文件系统服务

        Args:
            features: 文件系统服务功能特性列表
        """
        # 保存功能特性
        self._features = features or []

        logger.info("初始化文件系统服务")
        if self._features:
            logger.info("启用的功能特性: %s", [f.name for f in self._features])

    @property
    def features(self) -> Sequence:
        """获取功能特性列表"""
        return tuple(self._features)

    def _resolve_path(self, path: StrOrPath) -> Path:
        """
        解析路径为绝对路径

        Args:
            path: 要解析的路径

        Returns:
            Path: 解析后的绝对路径
        """
        # 将传入路径转换为Path对象并标准化
        return Path(path).resolve()

    async def create_directory(self, path: StrOrPath) -> None:
        """
        创建目录

        Args:
            path: 目录路径

        Raises:
            PermissionError: 权限不足
        """
        resolved_path = self._resolve_path(path)

        def _create_sync():
            resolved_path.mkdir(parents=True, exist_ok=True)

        await anyio.to_thread.run_sync(_create_sync)

    async def list_directory(self, path: StrOrPath) -> List[DirectoryItem]:
        """
        列出目录内容

        Args:
            path: 目录路径

        Returns:
            目录内容列表

        Raises:
            PermissionError: 权限不足
            FileNotFoundError: 目录不存在
            NotADirectoryError: 路径不是目录
        """
        resolved_path = self._resolve_path(path)

        def _list_sync():
            items = []
            for item in resolved_path.iterdir():
                items.append(
                    DirectoryItem(
                        name=item.name,
                        type="directory" if item.is_dir() else "file",
                        path=str(item),
                    )
                )
            return sorted(items, key=lambda x: (x.type == "file", x.name))

        return await anyio.to_thread.run_sync(_list_sync)

    async def move_file(self, source: StrOrPath, destination: StrOrPath) -> None:
        """
        移动文件或目录

        Args:
            source: 源路径
            destination: 目标路径

        Raises:
            PermissionError: 权限不足
            FileNotFoundError: 源文件不存在
        """
        source_path = self._resolve_path(source)
        dest_path = self._resolve_path(destination)

        def _move_sync():
            # 确保目标目录存在
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source_path), str(dest_path))

        await anyio.to_thread.run_sync(_move_sync)

    async def search_files(
        self,
        path: StrOrPath,
        pattern: str,
        exclude_patterns: Optional[List[str]] = None,
    ) -> List[str]:
        """
        搜索文件

        Args:
            path: 搜索起始路径
            pattern: 搜索模式
            exclude_patterns: 排除模式列表

        Returns:
            匹配的文件路径列表

        Raises:
            PermissionError: 权限不足
            FileNotFoundError: 路径不存在
        """
        resolved_path = self._resolve_path(path)

        def _search_sync():
            # 构建glob模式
            glob_pattern = os.path.join(resolved_path, "**", pattern)
            matches = glob.glob(glob_pattern, recursive=True)

            # 过滤结果
            if exclude_patterns:
                filtered_matches = []
                for match in matches:
                    should_exclude = False
                    for exclude_pattern in exclude_patterns:
                        if fnmatch.fnmatch(match, exclude_pattern):
                            should_exclude = True
                            break
                    if not should_exclude:
                        filtered_matches.append(match)
                return filtered_matches
            return matches

        return await anyio.to_thread.run_sync(_search_sync)

    async def get_file_info(self, path: StrOrPath) -> FileInfo:
        """
        获取文件信息

        Args:
            path: 文件路径

        Returns:
            文件信息

        Raises:
            PermissionError: 权限不足
            FileNotFoundError: 文件不存在
        """
        resolved_path = self._resolve_path(path)

        def _get_info_sync():
            stat_result = os.stat(resolved_path)
            return FileInfo(
                type="directory" if resolved_path.is_dir() else "file",
                size=stat_result.st_size,
                created=datetime.fromtimestamp(stat_result.st_ctime).isoformat(),
                modified=datetime.fromtimestamp(stat_result.st_mtime).isoformat(),
                accessed=datetime.fromtimestamp(stat_result.st_atime).isoformat(),
                permissions=oct(stat_result.st_mode)[-3:],
                absolute_path=os.path.abspath(resolved_path),
            )

        return await anyio.to_thread.run_sync(_get_info_sync)

    async def read_text_file(self, path: StrOrPath, encoding: str = "utf-8") -> str:
        """
        读取文本文件内容

        Args:
            path: 文件路径
            encoding: 文件编码

        Returns:
            文件内容字符串

        Raises:
            FileNotFoundError: 文件不存在
        """
        resolved_path = self._resolve_path(path)
        async with await anyio.open_file(resolved_path, "r", encoding=encoding) as f:
            return await f.read()

    async def read_multiple_text_files(
        self, paths: List[StrOrPath], encoding: str = "utf-8"
    ) -> Dict[StrOrPath, TextFileReadResult]:
        """
        批量读取多个文本文件

        Args:
            paths: 文件路径列表
            encoding: 文件编码

        Returns:
            包含每个文件读取结果的字典
        """
        results = {}
        for path in paths:
            try:
                content = await self.read_text_file(path, encoding)
                results[path] = TextFileReadResult(
                    success=True, content=content, error=None
                )
            except Exception as e:
                results[path] = TextFileReadResult(
                    success=False, content=None, error=str(e)
                )
        return results

    async def write_text_file(
        self, path: StrOrPath, content: str, encoding: str = "utf-8"
    ) -> None:
        """
        写入文件内容

        Args:
            path: 文件路径
            content: 要写入的内容
            encoding: 文件编码
        Raises:
            PermissionError: 权限不足
        """
        resolved_path = self._resolve_path(path)
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        async with await anyio.open_file(resolved_path, "w", encoding=encoding) as f:
            await f.write(content)

    async def edit_text_file(
        self,
        path: StrOrPath,
        edits: List[Dict[str, str]],
        dry_run: bool = False,
        encoding: str = "utf-8",
    ) -> FileEditResult:
        """
        编辑文件内容

        Args:
            path: 文件路径
            edits: 编辑操作列表，每个操作包含oldText和newText
            dry_run: 是否为预览模式（不实际修改文件）
            encoding: 文件编码
        Returns:
            编辑结果信息

        Raises:
            PermissionError: 路径不被允许
            FileNotFoundError: 文件不存在
        """
        resolved_path = self._resolve_path(path)
        async with await anyio.open_file(resolved_path, "r", encoding=encoding) as f:
            content = await f.read()
        original_content = content
        changes_made = []
        for edit in edits:
            old_text = edit.get("oldText", "")
            new_text = edit.get("newText", "")
            if old_text in content:
                content = content.replace(old_text, new_text, 1)
                changes_made.append(
                    EditChange(old=old_text, new=new_text, applied=True, error=None)
                )
            else:
                changes_made.append(
                    EditChange(
                        old=old_text,
                        new=new_text,
                        applied=False,
                        error=f"文本不存在: {old_text[:50]}...",
                    )
                )
        content_changed = content != original_content
        if not dry_run and content_changed:
            async with await anyio.open_file(
                resolved_path, "w", encoding=encoding
            ) as f:
                await f.write(content)
        return FileEditResult(
            changes_made=changes_made,
            content_changed=content_changed,
            preview=content if dry_run else None,
            message=f"{'预览模式：' if dry_run else ''}应用了 {len([c for c in changes_made if c.applied])} 个变更",
        )
