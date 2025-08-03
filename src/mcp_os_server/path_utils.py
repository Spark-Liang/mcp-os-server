import logging
import os
import sys
import re
import urllib.parse
from typing import List, Optional
from pathlib import Path

from mcp.types import Root
from mcp.server.session import ServerSession
from mcp.server.fastmcp import FastMCP, Context
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)




def try_resolve_win_path_in_url_format(path: str) -> Path:
    """
    尝试将 Windows 的 URL 格式路径转换为本地路径。

    Example:
        /e:/... -> e:/...
        /E:/... -> E:/...
        /ef:/... -> ef:/...
        /ef:/...-> ef:/...
    """
    # 使用正则表达式，严格匹配 /<盘符>: 格式，其中盘符必须是字母，可以为多个字母的盘符，比如 /e:/... 或 /ef:/...
    match = re.match(r"^/([a-zA-Z]+):/(.*)", path)    
    logger.debug("match: %s", match)
    if (
        sys.platform.startswith("win")
        and match
    ):
        # 处理 /e:/... 格式的路径，转换为 e:/...
        return Path(match.group(1)+":/") / Path(match.group(2))
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
            logger.debug("root.uri.path: %s", root.uri.path)
            root_path_str = urllib.parse.unquote(root.uri.path) if root.uri.path else "/"
            logger.debug("root_path_str: %s", root_path_str)
            root_path = try_resolve_win_path_in_url_format(root_path_str).resolve()
            logger.debug("root_path: %s", root_path)
        root_info_item = RootInfoItem(root=root, local_path=root_path)
        root_info_items.append(root_info_item)
    logger.debug("root_info_items: %s", root_info_items)
    return root_info_items