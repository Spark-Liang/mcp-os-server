import logging
import os
import sys
import re
import urllib.parse
from typing import List, Optional, Sequence
from pathlib import Path
import anyio
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


async def resolve_paths_and_check_allowed(
    paths: Sequence[str | Path], 
    allowed_dirs: Optional[Sequence[str | Path]] = None, 
    context: Optional[Context] = None
) -> Sequence[Path]:
    """
    路径检查函数，检查路径是否在允许的目录中，并返回解析后的绝对路径。检查规则如下
    1. 如果路径是绝对路径，则直接检查是否在允许的目录中
    2. 如果路径是相对路径，则必须通过 MCP roots 解析，所以必须有 context，如果不提供 context 则不支持相对路径。
       MCP roots 解析相对路径规则：依次检查 allowed_dirs 中的相对路径，如果解析后的绝对路径存在且在允许的目录下，则返回解析后的绝对路径，如果所有 allowed_dirs 都解析失败，则抛出 PermissionError 异常。
    
    Args:
        path: 要检查的路径
        allowed_dirs: 允许的目录列表（可以是相对路径或绝对路径）. 如果为 None，则代表不限制路径
        context: MCP上下文（可选，用于获取roots信息）
        
    Returns:
        解析后的绝对路径
        
    Raises:
        PermissionError: 如果路径不被允许访问
        ValueError: 如果路径无法解析成存在的绝对路径
    """
    async def get_cached_root_infos() -> List[RootInfoItem]:
        cached_root_infos = []
        is_requested = False
        lock = anyio.Lock()
        if not is_requested:
            async with lock:
                if not is_requested:
                    if context:
                        cached_root_infos = await list_roots(context)
                        logger.debug("cached_root_infos: %s", cached_root_infos)
                    else:
                        logger.warning("no context, skip list_roots")
                    is_requested = True
        return cached_root_infos
    
    async def resolve_relative_path_from_roots(request_path: Path) -> Optional[Path]:
        """
        从 roots 中解析相对路径
        """
        root_infos = await get_cached_root_infos()
        for root_info in root_infos:
            if not(root_info.local_path and root_info.local_path.is_absolute()):
                logger.debug(
                    "root %s local_path is not absolute, skip: %s", root_info.root.name, root_info.local_path
                )
                continue
            
            resolved_request_path = root_info.local_path / request_path
            if resolved_request_path.exists():
                return resolved_request_path
            logger.debug(
                "resolved_request_path %s not exists, skip: %s", resolved_request_path, request_path
            )
        return None
    
    resolved_paths: List[Path] = []
    for path in paths:
        logger.debug("path: %s", path)
        request_path = try_resolve_win_path_in_url_format(path) if isinstance(path, str) else path
        is_request_absolute = request_path.is_absolute()

        resolved_path = None
        if allowed_dirs is not None:
            for allowed in [Path(d) for d in allowed_dirs]:
                if is_request_absolute != allowed.is_absolute():
                    logger.debug(
                        "allowed_path: %s, is_request_absolute: %s, is_allowed_absolute: %s, skip", 
                        allowed, is_request_absolute, allowed.is_absolute()
                    )
                    continue
                
                if is_request_absolute:
                    if request_path.is_relative_to(allowed):
                        resolved_path = request_path
                        break
                    else:
                        logger.debug(
                            "request_path %s is relative to allowed_path %s, return: %s", 
                            request_path, allowed, request_path
                        )
                        continue
                else:
                    if not (request_path.is_relative_to(allowed)):
                        logger.debug(
                            "request_path %s is not relative to allowed_path %s, skip", 
                            request_path, allowed
                        )
                        continue
                    
                    resolved_result = await resolve_relative_path_from_roots(request_path)
                    if resolved_result:
                        resolved_path = resolved_result
                        break
            if resolved_path is None:
                raise PermissionError(f"路径不在允许的目录中: {path}，allowed_dirs: {allowed_dirs}")
        else:
            logger.debug("allowed_dirs is None, resolve relative path from roots")
            if is_request_absolute:
                logger.debug("request_path %s is absolute, return: %s", request_path, request_path)
                resolved_path = request_path
            else:
                resolved_result = await resolve_relative_path_from_roots(request_path)
                if resolved_result:
                    resolved_path = resolved_result
                else:
                    logger.debug("request_path %s is not relative to any allowed_dirs, skip", request_path)
        
        if resolved_path is not None:
            logger.debug("resolved_path: %s", resolved_path)
            resolved_paths.append(resolved_path)
        else:
            raise ValueError(f"无法解析相对路径: {path}")
    
    return resolved_paths

async def resolve_path_and_check_allowed(
    path: str, 
    allowed_dirs: Optional[Sequence[str | Path]] = None, 
    context: Optional[Context] = None
) -> Path:
    """
    路径检查函数，检查路径是否在允许的目录中，并返回解析后的绝对路径。检查规则如下
    1. 如果路径是绝对路径，则直接检查是否在允许的目录中
    2. 如果路径是相对路径，则必须通过 MCP roots 解析，所以必须有 context，如果不提供 context 则不支持相对路径。
       MCP roots 解析相对路径规则：依次检查 allowed_dirs 中的相对路径，如果解析后的绝对路径存在且在允许的目录下，则返回解析后的绝对路径，如果所有 allowed_dirs 都解析失败，则抛出 PermissionError 异常。
    
    Args:
        path: 要检查的路径
        allowed_dirs: 允许的目录列表（可以是相对路径或绝对路径）. 如果为 None，则代表不限制路径
        context: MCP上下文（可选，用于获取roots信息）
        
    Returns:
        解析后的绝对路径
        
    Raises:
        PermissionError: 如果路径不被允许访问
        ValueError: 如果路径无法解析成存在的绝对路径
    """
    resolved_paths = await resolve_paths_and_check_allowed(
        [path], allowed_dirs, context
    )
    if len(resolved_paths) < 1:
        raise ValueError(f"无法解析路径: {path}")
    return resolved_paths[0]
    
    
    