"""MCP Filesystem Server using FastMCP"""

import anyio
import io
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
import functools
    
from mcp.server.fastmcp import FastMCP, Image, Context
from mcp.types import TextContent
from PIL import Image as PILImage
from pydantic import Field

from ..path_utils import list_roots, RootInfoItem, try_resolve_win_path_in_url_format, resolve_path_and_check_allowed
from .filesystem_service import FilesystemService
from .models import DirectoryItem, FileEditResult, FileInfo, TextFileReadResult

logger = logging.getLogger(__name__)


async def check_path_allowed_and_resolve_async(
    path: str, allowed_dirs: Sequence[str | Path], context: Optional[Context] = None
) -> Path:
    """
    路径检查函数，检查路径是否在允许的目录中，并返回解析后的绝对路径。检查规则如下
    1. 如果路径是绝对路径，则直接检查是否在允许的目录中
    2. 如果路径是相对路径，则必须通过 MCP roots 解析，所以必须有 context，如果不提供 context 则不支持相对路径。
       MCP roots 解析相对路径规则：依次检查 allowed_dirs 中的相对路径，如果解析后的绝对路径存在且在允许的目录下，则返回解析后的绝对路径，如果所有 allowed_dirs 都解析失败，则抛出 PermissionError 异常。
    
    Args:
        path: 要检查的路径
        allowed_dirs: 允许的目录列表（可以是相对路径或绝对路径）
        context: MCP上下文（可选，用于获取roots信息）
        
    Returns:
        解析后的绝对路径
        
    Raises:
        PermissionError: 如果路径不被允许访问
    """
    return await resolve_path_and_check_allowed(path, allowed_dirs, context)
    

def _do_load_image_by_pillow(path: str, max_bytes: Optional[int] = None) -> Image:
    """读取图片文件并转换为Image对象

    Args:
        path: 图片文件路径
        max_bytes: 最大字节数限制，如果超过此大小将创建缩略图

    Returns:
        Image对象
    """

    with PILImage.open(path) as img:
        original_format = img.format

        # 如果没有设置max_bytes限制，直接返回原图
        if max_bytes is None:
            return Image(
                format=original_format,
                data=img.tobytes(),
            )

        # 首先尝试获取当前图片的字节大小
        img_buffer = io.BytesIO()
        # 保存为原格式来估算大小
        save_format = original_format if original_format else "PNG"
        img.save(img_buffer, format=save_format)
        current_size = img_buffer.tell()

        # 如果当前大小未超过限制，直接返回
        if current_size <= max_bytes:
            img_buffer.seek(0)
            return Image(
                format=original_format,
                data=img_buffer.getvalue(),
            )

        # 需要创建缩略图
        logger.info(
            "图片大小 %s 字节超过限制 %s 字节，创建缩略图", current_size, max_bytes
        )

        # 计算缩放比例
        scale_ratio = (max_bytes / current_size) ** 0.5  # 开平方根获得线性缩放比例
        new_width = int(img.width * scale_ratio)
        new_height = int(img.height * scale_ratio)

        # 确保尺寸不会太小
        new_width = max(new_width, 1)
        new_height = max(new_height, 1)

        # 创建缩略图
        thumbnail = img.resize((new_width, new_height), PILImage.Resampling.LANCZOS)

        # 保存缩略图到内存
        thumb_buffer = io.BytesIO()
        thumbnail.save(thumb_buffer, format=save_format)

        return Image(
            format=original_format,
            data=thumb_buffer.getvalue(),
        )


def define_mcp_server(
        mcp: FastMCP, 
        filesystem_service: FilesystemService, 
        allowed_dirs: List[str], 
        default_encoding: str = sys.getdefaultencoding()
    ):
    """
    定义MCP服务器相关配置
    
    Args:
        mcp: MCP服务器实例
        filesystem_service: 文件系统服务实例
        allowed_dirs: 允许的目录列表
        default_encoding: 默认文件编码
    """

    async def _do_get_filesystem_info(
        context: Optional[Context] = None,  # mcp 1.9.4 中，resource 的 context 参数注入有问题，暂时允许为空
    ) -> Dict[str, Any]:
        """
        获取文件系统服务配置信息

        Returns:
            JSON格式的配置信息
        """
        from ..version import __version__

        return {
            "server_name": "Filesystem Server",
            "version": __version__,
            "work_dir": os.getcwd(),
            "allowed_directories": [str(Path(d).resolve()) for d in allowed_dirs],
            "roots": (
                [root.model_dump(mode="json") for root in await list_roots(context)]
                if context
                else []
            ),
        }

    def auto_handle_exception(func):
        """
        自动处理异常，将异常转换为 TextContent 对象
        
        Args:
            func: 要自动处理异常的函数
            
        Returns:
            TextContent 对象
        """
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error("Error executing tool %s: %s", func.__name__, e, exc_info=True)
                return TextContent(text=f"Error executing tool {func.__name__}: {str(e)}", type="text")
        return wrapper

    # ===== 工具 (Tools) =====

    @mcp.tool()
    @auto_handle_exception
    async def fs_read_text_file(
        context: Context,
        path: str = Field(..., description="要读取的文件的路径。如果路径是相对路径，则是相对 roots 的相对路径"),
        encoding: Optional[str] = Field(default_encoding, description="文件编码"),
    ) -> str:
        """
        读取文件内容

        Args:
            path: 要读取的文件的绝对路径

        Returns:
            文件的文本内容
        """
        # 创建一个简化的context来传递给异步函数
        # 这里我们暂时不使用MCP context，因为工具函数中无法直接获取
        resolved_path = await check_path_allowed_and_resolve_async(path, allowed_dirs, context)
        return await filesystem_service.read_text_file(str(resolved_path), encoding or default_encoding)

    @mcp.tool()
    @auto_handle_exception
    async def fs_read_multiple_text_files(
        context: Context,
        paths: List[str] = Field(..., description="要读取的文件的路径列表，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径"),
        encoding: Optional[str] = Field(default_encoding, description="文件编码"),
    ) -> Dict[str, TextFileReadResult]:
        """
        读取多个文件的内容

        Args:
            paths: 要读取的文件的绝对路径列表

        Returns:
            包含每个文件读取结果的字典
        """
        # 检查所有路径权限
        results = {}
        for path in paths:
            try:
                resolved_path = await check_path_allowed_and_resolve_async(path, allowed_dirs, context)
                content = await filesystem_service.read_text_file(str(resolved_path), encoding or default_encoding)
                results[path] = TextFileReadResult(success=True, content=content, error=None)
            except Exception as e:
                results[path] = TextFileReadResult(success=False, content=None, error=str(e))
        return results


    @mcp.tool()
    @auto_handle_exception
    async def fs_write_text_file(
        context: Context,
        path: str = Field(..., description="要写入的文件的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径"),
        content: str = Field(..., description="要写入的内容"),
        encoding: Optional[str] = Field(default_encoding, description="文件编码"),
    ) -> str:
        """
        写入文件内容

        Args:
            path: 要写入的文件的绝对路径
            content: 要写入的内容

        Returns:
            操作结果消息
        """
        resolved_path = await check_path_allowed_and_resolve_async(path, allowed_dirs, context)
        await filesystem_service.write_text_file(str(resolved_path), content, encoding or default_encoding)
        return f"文件已成功写入: {path}"

    @mcp.tool()
    @auto_handle_exception
    async def fs_edit_text_file(
        context: Context,
        path: str = Field(..., description="要编辑的文件的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径"),
        edits: List[Dict[str, str]] = Field(
            ..., description="编辑操作列表，每个操作包含 oldText 和 newText"
        ),
        dry_run: bool = Field(False, description="是否为预览模式（不实际修改文件）"),
        encoding: Optional[str] = Field(default_encoding, description="文件编码"),
    ) -> FileEditResult:
        """
        编辑文件内容

        Args:
            path: 要编辑的文件的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径
            edits: 编辑操作列表，每个操作包含 oldText 和 newText
            dry_run: 是否为预览模式（不实际修改文件）

        Returns:
            编辑结果信息
        """
        resolved_path = await check_path_allowed_and_resolve_async(path, allowed_dirs, context)
        return await filesystem_service.edit_text_file(str(resolved_path), edits, dry_run, encoding or default_encoding)

    default_max_bytes = 1024 * 1024 * 10

    @mcp.tool()
    @auto_handle_exception
    async def fs_read_image(
        context: Context,
        path: str = Field(..., description="要读取的图片文件的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径"),
        max_bytes: int = Field(
            default=default_max_bytes,
            description="如果超过此大小将创建缩略图",
            ge=0,
        ),
    ) -> Image:
        """
        读取图片文件并返回为Image内容

        Args:
            path: 要读取的图片文件的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径
            max_bytes: 最大字节数限制，如果超过此大小将创建缩略图

        Returns:
            图片内容（ImageContent对象）
        """

        try:
            # 检查路径权限
            resolved_path = await check_path_allowed_and_resolve_async(path, allowed_dirs, context)
            return _do_load_image_by_pillow(str(resolved_path), max_bytes)
        except BaseException as e:
            logger.error("读取图片文件失败: %s", e, exc_info=True)
            raise e

    @mcp.tool()
    @auto_handle_exception
    async def fs_read_multiple_images(
        context: Context,
        paths: List[str] = Field(..., description="要读取的图片文件的路径列表，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径"),
        max_bytes: int = Field(
            default=default_max_bytes,
            description="最大字节数限制，如果超过此大小将创建缩略图，默认10MB",
            ge=0,
        ),
    ) -> List[Image]:
        """
        读取多个图片文件并返回为Image内容列表

        Args:
            paths: 要读取的图片文件的路径列表，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径
            max_bytes: 最大字节数限制，如果超过此大小将创建缩略图

        Returns:
            图片内容列表（ImageContent对象列表）
        """
        images = []
        for path in paths:
            try:
                resolved_path = await check_path_allowed_and_resolve_async(path, allowed_dirs, context)
                images.append(_do_load_image_by_pillow(str(resolved_path), max_bytes))
            except BaseException as e:
                logger.error("读取图片文件失败: %s", e, exc_info=True)
                raise e
        return images

    @mcp.tool()
    @auto_handle_exception
    async def fs_create_directory(
        context: Context,
        path: str = Field(..., description="要创建的目录的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径")
    ) -> str:
        """
        创建目录

        Args:
            path: 要创建的目录的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径

        Returns:
            操作结果消息
        """
        resolved_path = await check_path_allowed_and_resolve_async(path, allowed_dirs, context)
        await filesystem_service.create_directory(str(resolved_path))
        return f"目录已成功创建: {path}"

    @mcp.tool()
    @auto_handle_exception
    async def fs_list_directory(
        context: Context,
        path: str = Field(..., description="要列出的目录的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径")
    ) -> List[DirectoryItem]:
        """
        列出目录内容

        Args:
            path: 要列出的目录的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径

        Returns:
            目录内容列表，包含文件和子目录信息
        """
        resolved_path = await check_path_allowed_and_resolve_async(path, allowed_dirs, context)
        return await filesystem_service.list_directory(str(resolved_path))

    @mcp.tool()
    @auto_handle_exception
    async def fs_move_file(
        context: Context,
        source: str = Field(..., description="源文件的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径"),
        destination: str = Field(..., description="目标文件的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径"),
    ) -> str:
        """
        移动或重命名文件/目录

        Args:
            source: 源文件的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径
            destination: 目标文件的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径

        Returns:
            操作结果消息
        """
        resolved_source = await check_path_allowed_and_resolve_async(source, allowed_dirs, context)
        resolved_dest = await check_path_allowed_and_resolve_async(destination, allowed_dirs, context)
        await filesystem_service.move_file(str(resolved_source), str(resolved_dest))
        return f"文件已成功移动: {source} -> {destination}"

    @mcp.tool()
    @auto_handle_exception
    async def fs_search_files(
        context: Context,
        path: str = Field(..., description="搜索起始目录的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径"),
        pattern: str = Field(
            ..., description="搜索模式（支持glob模式，如 *.txt, *.py）"
        ),
        exclude_patterns: Optional[List[str]] = Field(
            None, description="要排除的模式列表（可选）"
        ),
    ) -> List[str]:
        """
        搜索文件

        Args:
            path: 搜索起始目录的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径
            pattern: 搜索模式（支持glob模式，如 *.txt, *.py）
            exclude_patterns: 要排除的模式列表（可选）

        Returns:
            匹配的文件路径列表
        """
        resolved_path = await check_path_allowed_and_resolve_async(path, allowed_dirs, context)
        return await filesystem_service.search_files(str(resolved_path), pattern, exclude_patterns)

    @mcp.tool()
    @auto_handle_exception
    async def fs_get_file_info(
        context: Context,
        path: str = Field(..., description="文件或目录的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径")
    ) -> FileInfo:
        """
        获取文件或目录的详细信息

        Args:
            path: 文件或目录的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径

        Returns:
            包含文件信息的字典（类型、大小、时间等）
        """
        resolved_path = await check_path_allowed_and_resolve_async(path, allowed_dirs, context)
        return await filesystem_service.get_file_info(str(resolved_path))

    @mcp.tool()
    @auto_handle_exception
    async def fs_get_filesystem_info(context: Context) -> Dict[str, Any]:
        """
        获取文件系统服务配置信息
        """
        return await _do_get_filesystem_info(context)

    # ===== 资源 (Resources) =====

    @mcp.resource("file://{path}")
    async def read_file_resource(
        # context: Context, # mcp 1.9.4 中，resource 的 context 参数注入有问题，暂时不使用
        path: str
    ) -> str:
        """
        作为资源读取文件内容

        Args:
            path: 文件的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径

        Returns:
            文件内容
        """
        resolved_path = await check_path_allowed_and_resolve_async(
            path, allowed_dirs,
            # context # mcp 1.9.4 中，context 参数注入有问题，暂时不使用
        )
        return await filesystem_service.read_text_file(str(resolved_path))

    @mcp.resource("directory://{path}")
    async def list_directory_resource(
        # context: Context, # mcp 1.9.4 中，resource 的 context 参数注入有问题，暂时不使用
        path: str
    ) -> str:
        """
        作为资源列出目录内容

        Args:
            path: 目录的路径，可以是绝对路径或相对路径。如果路径是相对路径，则是相对 roots 的相对路径

        Returns:
            JSON格式的目录内容列表
        """
        resolved_path = await check_path_allowed_and_resolve_async(
            path, allowed_dirs,
            # context # mcp 1.9.4 中，resource 的 context 参数注入有问题，暂时不使用
        )
        items = await filesystem_service.list_directory(str(resolved_path))
        return json.dumps(
            [item.model_dump() for item in items], indent=2, ensure_ascii=False
        )

    @mcp.resource("config://filesystem")
    async def get_config_resource(
        # context: Context, # mcp 1.9.4 中，resource 的 context 参数注入有问题，暂时不使用
    ) -> str:
        """
        获取文件系统服务配置信息

        Returns:
            JSON格式的配置信息
        """

        return json.dumps(
            await _do_get_filesystem_info(
                # context # mcp 1.9.4 中，resource 的 context 参数注入有问题，暂时不使用
            ),
            indent=2,
            ensure_ascii=False,
        )
