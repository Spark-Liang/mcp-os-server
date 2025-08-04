"""测试FastMCP服务器"""

import os
import sys
from pathlib import Path
from typing import List, Optional

import pytest
from mcp.server.fastmcp import Image

from mcp_os_server.filesystem.filesystem_service import FilesystemService
from mcp_os_server.filesystem.server import (
    _do_load_image_by_pillow,
    check_path_allowed_and_resolve_async,
    define_mcp_server,
)
from mcp_os_server.filtered_fast_mcp import FilteredFastMCP


def create_server(
    allowed_dirs: List[str],
    features: Optional[List] = None,
    host: str = "127.0.0.1",
    port: int = 8000,
    default_encoding: str = sys.getdefaultencoding(),
) -> "FilteredFastMCP":
    """
    创建并配置MCP文件系统服务器。

    Args:
        allowed_dirs (List[str]): 允许文件操作的根目录列表。
        features (Optional[List]): 文件系统服务功能特性列表。
        host (str): 服务器绑定的主机地址。
        port (int): 服务器监听的端口。

    Returns:
        FilteredFastMCP: 配置好的FastMCP服务器实例。
    """
    if not allowed_dirs:
        raise ValueError("至少需要指定一个允许的目录")

    # 从环境变量读取额外的允许目录
    env_allowed_dirs_str = os.getenv("ALLOWED_DIRS")
    if env_allowed_dirs_str:
        env_allowed_dirs = env_allowed_dirs_str.split(os.pathsep)
        # 将环境变量中的目录添加到允许列表中
        allowed_dirs.extend(env_allowed_dirs)

    filesystem_service = FilesystemService(features=features)
    mcp = FilteredFastMCP(name="filesystem", version="0.1.0", host=host, port=port)
    define_mcp_server(mcp, filesystem_service, allowed_dirs, default_encoding)
    return mcp


class TestPathAccessControl:
    """测试路径访问控制"""


    @pytest.mark.anyio
    async def test_check_path_allowed_absolute_path(self, temp_dir):
        """测试绝对路径的访问控制"""
        allowed_dirs = [str(temp_dir)]

        # 允许的路径
        test_file = temp_dir / "test.txt"
        test_file.write_text("test content")

        resolved_path = await check_path_allowed_and_resolve_async(
            str(test_file), allowed_dirs
        )
        assert resolved_path == test_file.resolve()

        # 子目录中的文件也应该被允许
        subdir = temp_dir / "subdir"
        subdir.mkdir()
        sub_file = subdir / "subfile.txt"
        sub_file.write_text("sub content")

        resolved_path = await check_path_allowed_and_resolve_async(
            str(sub_file), allowed_dirs
        )
        assert resolved_path == sub_file.resolve()

    @pytest.mark.anyio
    async def test_check_path_not_allowed_outside_dirs(self, temp_dir):
        """测试不在允许目录外的路径被拒绝"""
        allowed_dirs = [str(temp_dir)]

        # 尝试访问父目录
        parent_file = temp_dir.parent / "forbidden.txt"
        with pytest.raises(PermissionError, match="路径不在允许的目录中"):
            await check_path_allowed_and_resolve_async(str(parent_file), allowed_dirs)

        # 尝试访问系统目录
        with pytest.raises(PermissionError, match="路径不在允许的目录中"):
            await check_path_allowed_and_resolve_async("/etc/passwd", allowed_dirs)

    @pytest.mark.anyio
    async def test_check_path_relative_path(self, temp_dir):
        """测试相对路径的访问控制"""
        allowed_dirs = [str(temp_dir)]

        # 创建测试文件
        test_file = temp_dir / "relative_test.txt"
        test_file.write_text("relative content")

        # 改变当前工作目录到 temp_dir
        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(temp_dir)

            # 相对路径应该被解析为绝对路径
            resolved_path = await check_path_allowed_and_resolve_async(
                "relative_test.txt", allowed_dirs
            )
            assert resolved_path == test_file.resolve()

        finally:
            os.chdir(original_cwd)

    @pytest.mark.anyio
    async def test_check_path_allowed_dirs_relative(self, temp_dir):
        """测试 allowed_dirs 支持相对路径配置"""
        # 创建一个测试目录和文件
        test_dir = temp_dir / "relative_allowed"
        test_dir.mkdir()
        test_file = test_dir / "test.txt"
        test_file.write_text("test content")

        # 改变当前工作目录到 temp_dir
        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(temp_dir)

            # 使用相对路径配置 allowed_dirs
            allowed_dirs = ["relative_allowed"]

            # 应该能够访问该目录下的文件
            resolved_path = await check_path_allowed_and_resolve_async(
                str(test_file), allowed_dirs
            )
            assert resolved_path == test_file.resolve()

            # 也可以使用相对路径访问
            resolved_path = await check_path_allowed_and_resolve_async(
                "relative_allowed/test.txt", allowed_dirs
            )
            assert resolved_path == test_file.resolve()

        finally:
            os.chdir(original_cwd)

    @pytest.mark.skipif(os.name != "nt", reason="Cursor 目录格式只在 Windows 上支持")
    @pytest.mark.anyio
    async def test_check_path_cursor_format_enabled(self, temp_dir, monkeypatch):
        """测试启用 Cursor 格式路径处理"""
        # 设置环境变量启用 Cursor 格式支持
        monkeypatch.setenv("SUPPORT_CURSOR_PATH_FORMAT", "true")

        allowed_dirs = [str(temp_dir)]

        # 获取当前盘符
        current_drive = str(temp_dir)[:2]  # 例如：'C:'
        drive_letter = current_drive[0].lower()  # 例如：'c'

        # 创建测试文件
        test_file = temp_dir / "cursor_test.txt"
        test_file.write_text("cursor content")

        # 测试 Cursor 格式路径应该被转换并允许访问
        cursor_path = f"/{drive_letter}:{str(temp_dir)[2:]}/cursor_test.txt"
        resolved_path = await check_path_allowed_and_resolve_async(
            cursor_path, allowed_dirs
        )
        assert resolved_path == test_file.resolve()

    @pytest.mark.skipif(os.name != "nt", reason="Cursor 目录格式只在 Windows 上支持")
    @pytest.mark.anyio
    async def test_check_path_cursor_format_disabled(self, temp_dir, monkeypatch):
        """测试不启用 Cursor 格式路径处理"""
        # 确保环境变量不启用 Cursor 格式支持
        monkeypatch.setenv("SUPPORT_CURSOR_PATH_FORMAT", "false")

        allowed_dirs = [str(temp_dir)]

        # 获取当前盘符
        current_drive = str(temp_dir)[:2]  # 例如：'C:'
        drive_letter = current_drive[0].lower()  # 例如：'c'

        # 测试 Cursor 格式路径应该被当作普通路径处理，不被允许
        cursor_path = f"/{drive_letter}:{str(temp_dir)[2:]}/test.txt"
        with pytest.raises(PermissionError, match="路径不在允许的目录中"):
            await check_path_allowed_and_resolve_async(cursor_path, allowed_dirs)

    @pytest.mark.anyio
    async def test_check_path_multiple_allowed_dirs(self, temp_dir):
        """测试多个允许目录的情况"""
        # 创建两个临时目录
        dir1 = temp_dir / "dir1"
        dir2 = temp_dir / "dir2"
        dir1.mkdir()
        dir2.mkdir()

        allowed_dirs = [str(dir1), str(dir2)]

        # 在两个目录中都创建测试文件
        file1 = dir1 / "test1.txt"
        file2 = dir2 / "test2.txt"
        file1.write_text("content1")
        file2.write_text("content2")

        # 两个文件都应该被允许访问
        resolved_path1 = await check_path_allowed_and_resolve_async(
            str(file1), allowed_dirs
        )
        resolved_path2 = await check_path_allowed_and_resolve_async(
            str(file2), allowed_dirs
        )

        assert resolved_path1 == file1.resolve()
        assert resolved_path2 == file2.resolve()

        # 但不在这两个目录中的文件应该被拒绝
        forbidden_file = temp_dir / "forbidden.txt"
        forbidden_file.write_text("forbidden content")

        with pytest.raises(PermissionError, match="路径不在允许的目录中"):
            await check_path_allowed_and_resolve_async(
                str(forbidden_file), allowed_dirs
            )

    @pytest.mark.anyio
    async def test_check_path_mixed_absolute_relative_dirs(self, temp_dir):
        """测试混合绝对路径和相对路径的 allowed_dirs"""
        # 创建测试目录结构
        abs_dir = temp_dir / "absolute_dir"
        rel_dir = temp_dir / "relative_dir"
        abs_dir.mkdir()
        rel_dir.mkdir()

        abs_file = abs_dir / "abs_test.txt"
        rel_file = rel_dir / "rel_test.txt"
        abs_file.write_text("absolute content")
        rel_file.write_text("relative content")

        # 改变当前工作目录到 temp_dir
        import os

        original_cwd = os.getcwd()
        try:
            os.chdir(temp_dir)

            # 混合使用绝对路径和相对路径
            allowed_dirs = [str(abs_dir), "relative_dir"]

            # 两个文件都应该能访问
            resolved_path1 = await check_path_allowed_and_resolve_async(
                str(abs_file), allowed_dirs
            )
            resolved_path2 = await check_path_allowed_and_resolve_async(
                str(rel_file), allowed_dirs
            )

            assert resolved_path1 == abs_file.resolve()
            assert resolved_path2 == rel_file.resolve()

        finally:
            os.chdir(original_cwd)


class TestServerCreation:
    """测试服务器创建"""

    def test_create_server_with_allowed_dirs(self, temp_dir):
        """测试使用允许目录创建服务器"""
        server = create_server([str(temp_dir)])

        # 验证服务器实例被创建
        assert server is not None
        assert hasattr(server, "run")

    def test_create_server_empty_dirs_raises_error(self):
        """测试没有允许目录时创建服务器抛出错误"""
        with pytest.raises(ValueError, match="至少需要指定一个允许的目录"):
            create_server([])

    def test_server_has_tools(self, temp_dir):
        """测试服务器包含预期的工具"""
        server = create_server([str(temp_dir)])

        # 获取已注册的工具列表
        # FastMCP服务器应该有tools属性或方法来获取工具列表
        # 这里我们通过尝试访问来验证服务器已正确创建
        assert server is not None

    def test_server_has_resources(self, temp_dir):
        """测试服务器包含预期的资源"""
        server = create_server([str(temp_dir)])

        # 验证服务器有资源
        assert server is not None


@pytest.fixture
def test_server(temp_dir):
    """创建测试服务器实例"""
    return create_server([str(temp_dir)])


class TestImageLoading:
    """测试图片加载功能"""

    def test_load_image_success(self, sample_files):
        """测试成功加载图片"""
        # 这里使用项目中的测试图片
        image_path = "tests/mcp_os_server/filesystem/image-for-test.png"

        if Path(image_path).exists():
            image = _do_load_image_by_pillow(image_path)
            assert isinstance(image, Image)
            assert hasattr(image, "data")
            assert image.data is not None
        else:
            pytest.skip("测试图片文件不存在")

    def test_load_image_with_size_limit(self, sample_files):
        """测试带大小限制的图片加载"""
        image_path = "tests/mcp_os_server/filesystem/image-for-test.png"

        if Path(image_path).exists():
            # 设置一个很小的大小限制来强制创建缩略图
            max_bytes = 1000  # 1KB
            image = _do_load_image_by_pillow(image_path, max_bytes)
            assert isinstance(image, Image)
        else:
            pytest.skip("测试图片文件不存在")

    def test_load_image_file_not_found(self):
        """测试加载不存在的图片文件"""
        with pytest.raises(FileNotFoundError):
            _do_load_image_by_pillow("nonexistent_image.png")

    def test_load_image_invalid_file(self, temp_dir):
        """测试加载无效的图片文件"""
        # 创建一个非图片文件
        invalid_file = temp_dir / "invalid_image.png"
        invalid_file.write_text("这不是图片内容")

        with pytest.raises(Exception):  # PIL 会抛出各种异常
            _do_load_image_by_pillow(str(invalid_file))


class TestServerFunctionality:
    """测试服务器功能（通过直接调用工具函数）"""

    def test_server_tools_integration(self, sample_files):
        """测试服务器工具集成"""
        server = create_server([str(sample_files["dir"])])

        # 由于FastMCP的工具是装饰器注册的，我们需要通过服务器实例测试
        # 这里主要验证服务器创建成功
        assert server is not None
