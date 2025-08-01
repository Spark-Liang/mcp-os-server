import logging
import os
import sys
import urllib.parse
from typing import List, Optional
from pathlib import Path

from mcp.types import Root
from mcp.server.session import ServerSession
from mcp.server.fastmcp import FastMCP, Context
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)



def support_cursor_path_format() -> bool:
    """
    是否支持 Cursor 目录格式路径转换
    """
    return os.getenv("SUPPORT_CURSOR_PATH_FORMAT", "false").lower() == "true"


def try_resolve_cursor_path_format(path: str) -> Path:
    """
    尝试将 Cursor 格式的路径转换为本地路径。如果不启用或者不符合 Cursor 格式的路径，则返回原路径。
    """
    if not support_cursor_path_format():
        return Path(path)
    
    # 如果启用了 Cursor 目录格式支持，需要进行路径转换
    # 严格匹配 /<盘符>: 格式，其中盘符必须是字母，可以为多个字母的盘符，比如 /e:/... 或 /ef:/...
    if (
        sys.platform.startswith("win")
        and path.startswith("/")
        and len(path) > 3
        and path[2] == ":"
        and path[1].isalpha()
    ):
        # 处理 /e:/... 格式的路径，转换为 e:/...
        path = path[1:]
    return Path(path)


class RootInfoItem(BaseModel):
    root: Root = Field(description="root 信息")
    local_path: Optional[Path] = Field(None, description="root 对应的本地路径，如果为 None，则表示 root 对应的本地路径无法获取。")


async def list_roots(context: Context) -> List[RootInfoItem]:
    """
    列出当前上下文中的根目录信息
    """
    session = context.session
    roots = await session.list_roots()
    logger.debug("roots: %s", roots)
    root_info_items = []
    for root in roots.roots:
        if root.uri.scheme == "file":
            root_path_str = urllib.parse.unquote(root.uri.path) if root.uri.path else "/"
            root_path = try_resolve_cursor_path_format(root_path_str)
        root_info_item = RootInfoItem(root=root, local_path=root_path)
        root_info_items.append(root_info_item)
    return root_info_items