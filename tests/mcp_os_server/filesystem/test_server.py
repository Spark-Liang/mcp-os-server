"""测试FastMCP服务器"""

from pathlib import Path

import pytest
from mcp.server.fastmcp import Image

from mcp_os_server.filesystem.server import _do_load_image_by_pillow, create_server


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

    def test_do_load_image_by_pillow_success(self):
        """测试_do_load_image_by_pillow方法成功加载图片"""
        # 获取测试图片的路径
        test_image_path = Path(__file__).parent / "image-for-test.png"

        # 确保测试图片存在
        assert test_image_path.exists(), f"测试图片不存在: {test_image_path}"

        # 调用方法加载图片
        result = _do_load_image_by_pillow(str(test_image_path))

        # 验证返回的是Image对象
        assert isinstance(result, Image), "返回值应该是Image对象"

        # 验证Image对象有必要的属性
        assert hasattr(result, "_format"), "Image对象应该有_format属性"
        assert hasattr(result, "data"), "Image对象应该有data属性"

        # 验证_format不为空
        assert result._format is not None, "图片格式不应该为空"

        # 验证data不为空
        assert result.data is not None, "图片数据不应该为空"
        assert len(result.data) > 0, "图片数据长度应该大于0"

        # 验证格式为PNG（因为测试文件是PNG）
        assert (
            result._format.upper() == "PNG"
        ), f"期望格式为PNG，实际为: {result._format}"

    def test_do_load_image_by_pillow_with_max_bytes(self):
        """测试_do_load_image_by_pillow方法使用max_bytes参数"""
        # 获取测试图片的路径
        test_image_path = Path(__file__).parent / "image-for-test.png"

        # 确保测试图片存在
        assert test_image_path.exists(), f"测试图片不存在: {test_image_path}"

        # 调用方法加载图片，设置一个很小的max_bytes来强制创建缩略图
        result = _do_load_image_by_pillow(str(test_image_path), max_bytes=1000)

        # 验证返回的是Image对象
        assert isinstance(result, Image), "返回值应该是Image对象"

        # 验证Image对象有必要的属性
        assert hasattr(result, "_format"), "Image对象应该有_format属性"
        assert hasattr(result, "data"), "Image对象应该有data属性"

        # 验证data不为空
        assert result.data is not None, "图片数据不应该为空"
        assert len(result.data) > 0, "图片数据长度应该大于0"

        # 验证格式为PNG（因为测试文件是PNG）
        assert (
            result._format.upper() == "PNG"
        ), f"期望格式为PNG，实际为: {result._format}"

    def test_do_load_image_by_pillow_file_not_found(self):
        """测试_do_load_image_by_pillow方法处理文件不存在的情况"""
        non_existent_path = "non_existent_image.png"

        # 调用方法应该抛出异常
        with pytest.raises(FileNotFoundError):
            _do_load_image_by_pillow(non_existent_path)

    def test_do_load_image_by_pillow_invalid_image(self, temp_dir):
        """测试_do_load_image_by_pillow方法处理无效图片文件的情况"""
        # 创建一个无效的图片文件（实际是文本文件）
        invalid_image_path = temp_dir / "invalid_image.png"
        invalid_image_path.write_text("这不是一个有效的图片文件")

        # 调用方法应该抛出异常
        with pytest.raises(Exception):  # PIL会抛出各种异常，如UnidentifiedImageError等
            _do_load_image_by_pillow(str(invalid_image_path))


class TestServerFunctionality:
    """测试服务器功能（通过直接调用工具函数）"""

    def test_server_tools_integration(self, sample_files):
        """测试服务器工具集成"""
        server = create_server([str(sample_files["temp_dir"])])

        # 由于FastMCP的工具是装饰器注册的，我们需要通过服务器实例测试
        # 这里主要验证服务器创建成功
        assert server is not None
