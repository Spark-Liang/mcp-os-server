"""MCP Filesystem Server using FastMCP"""

import io
import json
import logging
import os
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP, Image
from PIL import Image as PILImage
from pydantic import Field

from .filesystem_service import FilesystemService

logger = logging.getLogger(__name__)


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
            f"图片大小 {current_size} 字节超过限制 {max_bytes} 字节，创建缩略图"
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


def define_mcp_server(mcp: FastMCP, filesystem_service: FilesystemService):
    """定义MCP服务器相关配置"""

    def __load_image_by_pillow(path: str, max_bytes: Optional[int] = None) -> Image:
        """读取图片文件并转换为Image对象"""
        logger.info(f"读取图片文件: {path}")
        is_allowed = filesystem_service.is_path_allowed(path)
        if not is_allowed:
            raise PermissionError(f"路径不在允许的目录中: {path}")

        return _do_load_image_by_pillow(path, max_bytes)

    async def _do_get_filesystem_info() -> Dict[str, Any]:
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
            "allowed_directories": filesystem_service.list_allowed_directories(),
            "capabilities": [
                "read_file",
                "read_image",
                "read_multiple_images",
                "write_file",
                "create_directory",
                "list_directory",
                "move_file",
                "search_files",
                "get_file_info",
                "edit_file",
            ],
        }

    # ===== 工具 (Tools) =====

    @mcp.tool()
    async def fs_read_file(path: str = Field(..., description="要读取的文件路径")) -> str:
        """
        读取文件内容

        Args:
            path: 要读取的文件路径

        Returns:
            文件的文本内容
        """
        return await filesystem_service.read_file(path)

    @mcp.tool()
    async def fs_read_multiple_files(
        paths: List[str] = Field(..., description="要读取的文件路径列表")
    ) -> Dict[str, Any]:
        """
        读取多个文件的内容

        Args:
            paths: 要读取的文件路径列表

        Returns:
            包含每个文件读取结果的字典
        """
        return await filesystem_service.read_multiple_files(paths)

    default_max_bytes = 1024 * 1024 * 10

    @mcp.tool()
    async def fs_read_image(
        path: str = Field(..., description="要读取的图片文件路径"),
        max_bytes: int = Field(
            default=default_max_bytes,
            description="最大字节数限制，超过此大小将创建缩略图",
            ge=0,
        ),
    ) -> Image:
        """
        读取图片文件并返回为Image内容

        Args:
            path: 要读取的图片文件路径
            max_bytes: 最大字节数限制，如果超过此大小将创建缩略图

        Returns:
            图片内容（ImageContent对象）
        """

        try:
            return __load_image_by_pillow(path, max_bytes)
        except BaseException as e:
            logger.error(f"读取图片文件失败: {e}", exc_info=True)
            raise e

    @mcp.tool()
    async def fs_read_multiple_images(
        paths: List[str] = Field(..., description="要读取的图片文件路径列表"),
        max_bytes: int = Field(
            default=default_max_bytes,
            description="最大字节数限制，超过此大小将创建缩略图，默认10MB",
            ge=0,
        ),
    ) -> List[Image]:
        """
        读取多个图片文件并返回为Image内容列表

        Args:
            paths: 要读取的图片文件路径列表
            max_bytes: 最大字节数限制，如果超过此大小将创建缩略图

        Returns:
            图片内容列表（ImageContent对象列表）
        """
        images = []
        for path in paths:
            try:
                images.append(__load_image_by_pillow(path, max_bytes))
            except BaseException as e:
                logger.error(f"读取图片文件失败: {e}", exc_info=True)
                raise e
        return images

    @mcp.tool()
    async def fs_write_file(
        path: str = Field(..., description="要写入的文件路径"),
        content: str = Field(..., description="要写入的内容"),
    ) -> str:
        """
        写入文件内容

        Args:
            path: 要写入的文件路径
            content: 要写入的内容

        Returns:
            操作结果消息
        """
        await filesystem_service.write_file(path, content)
        return f"文件已成功写入: {path}"

    @mcp.tool()
    async def fs_create_directory(
        path: str = Field(..., description="要创建的目录路径")
    ) -> str:
        """
        创建目录

        Args:
            path: 要创建的目录路径

        Returns:
            操作结果消息
        """
        await filesystem_service.create_directory(path)
        return f"目录已成功创建: {path}"

    @mcp.tool()
    async def fs_list_directory(
        path: str = Field(..., description="要列出的目录路径")
    ) -> List[Dict[str, Any]]:
        """
        列出目录内容

        Args:
            path: 要列出的目录路径

        Returns:
            目录内容列表，包含文件和子目录信息
        """
        return await filesystem_service.list_directory(path)

    @mcp.tool()
    async def fs_move_file(
        source: str = Field(..., description="源路径"),
        destination: str = Field(..., description="目标路径"),
    ) -> str:
        """
        移动或重命名文件/目录

        Args:
            source: 源路径
            destination: 目标路径

        Returns:
            操作结果消息
        """
        await filesystem_service.move_file(source, destination)
        return f"文件已成功移动: {source} -> {destination}"

    @mcp.tool()
    async def fs_search_files(
        path: str = Field(..., description="搜索起始路径"),
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
            path: 搜索起始路径
            pattern: 搜索模式（支持glob模式，如 *.txt, *.py）
            exclude_patterns: 要排除的模式列表（可选）

        Returns:
            匹配的文件路径列表
        """
        return await filesystem_service.search_files(path, pattern, exclude_patterns)

    @mcp.tool()
    async def fs_get_file_info(
        path: str = Field(..., description="文件或目录路径")
    ) -> Dict[str, Any]:
        """
        获取文件或目录的详细信息

        Args:
            path: 文件或目录路径

        Returns:
            包含文件信息的字典（类型、大小、时间等）
        """
        return await filesystem_service.get_file_info(path)

    @mcp.tool()
    async def fs_edit_file(
        path: str = Field(..., description="要编辑的文件路径"),
        edits: List[Dict[str, str]] = Field(
            ..., description="编辑操作列表，每个操作包含 oldText 和 newText"
        ),
        dry_run: bool = Field(False, description="是否为预览模式（不实际修改文件）"),
    ) -> Dict[str, Any]:
        """
        编辑文件内容

        Args:
            path: 要编辑的文件路径
            edits: 编辑操作列表，每个操作包含 oldText 和 newText
            dry_run: 是否为预览模式（不实际修改文件）

        Returns:
            编辑结果信息
        """
        return await filesystem_service.edit_file(path, edits, dry_run)

    @mcp.tool()
    async def fs_get_filesystem_info() -> Dict[str, Any]:
        """
        获取文件系统服务配置信息
        """
        return await _do_get_filesystem_info()

    # ===== 资源 (Resources) =====

    @mcp.resource("file://{path}")
    async def read_file_resource(path: str) -> str:
        """
        作为资源读取文件内容

        Args:
            path: 文件路径

        Returns:
            文件内容
        """
        return await filesystem_service.read_file(path)

    @mcp.resource("directory://{path}")
    async def list_directory_resource(path: str) -> str:
        """
        作为资源列出目录内容

        Args:
            path: 目录路径

        Returns:
            JSON格式的目录内容列表
        """
        items = await filesystem_service.list_directory(path)
        return json.dumps(items, indent=2, ensure_ascii=False)

    @mcp.resource("config://filesystem")
    async def get_config_resource() -> str:
        """
        获取文件系统服务配置信息

        Returns:
            JSON格式的配置信息
        """

        return json.dumps(await _do_get_filesystem_info(), indent=2, ensure_ascii=False)


def create_server(allowed_dirs: List[str], host: str = "127.0.0.1", port: int = 8000) -> FastMCP:
    """创建MCP服务器实例"""

    # 从环境变量读取额外的允许目录
    env_allowed_dirs_str = os.getenv("ALLOWED_DIRS")
    if env_allowed_dirs_str:
        env_allowed_dirs = env_allowed_dirs_str.split(os.pathsep)
        # 将环境变量中的目录添加到允许列表中
        allowed_dirs.extend(env_allowed_dirs)

    # 初始化文件系统服务
    filesystem_service = FilesystemService(allowed_dirs)

    # 创建FastMCP服务器
    mcp = FastMCP("Filesystem Server", host=host, port=port)

    define_mcp_server(mcp, filesystem_service)

    return mcp
