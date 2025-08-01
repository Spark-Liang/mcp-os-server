"""文件系统服务测试模块"""

import os
import pytest
import tempfile
from pathlib import Path

from mcp_os_server.filesystem.filesystem_service import FilesystemService
from mcp_os_server.filesystem.models import FileSystemServiceFeature


class TestFilesystemService:
    """文件系统服务测试类"""

    @pytest.mark.anyio
    async def test_init_with_features(self, temp_dir):
        """测试使用功能特性初始化服务"""
        service = FilesystemService(features=[])
        assert service.features == ()

    @pytest.mark.anyio
    async def test_read_text_file_success(self, sample_files):
        """测试成功读取文件"""
        service = FilesystemService()
        content = await service.read_text_file(sample_files["test.txt"], encoding='gbk')
        assert content == "测试内容"

    @pytest.mark.anyio
    async def test_read_text_file_not_found(self, sample_files):
        """测试读取不存在的文件"""
        service = FilesystemService()
        with pytest.raises(FileNotFoundError):
            await service.read_text_file(os.path.join(sample_files["dir"], "nonexistent.txt"))

    @pytest.mark.anyio
    async def test_write_text_file_success(self, sample_files):
        """测试成功写入文件"""
        service = FilesystemService()
        test_file = os.path.join(sample_files["dir"], "new_file.txt")
        content = "新文件内容"
        
        await service.write_text_file(test_file, content)
        
        # 验证文件已写入
        written_content = await service.read_text_file(test_file)
        assert written_content == content

    @pytest.mark.anyio
    async def test_list_directory_success(self, sample_files):
        """测试成功列出目录内容"""
        service = FilesystemService()
        items = await service.list_directory(sample_files["dir"])
        
        assert len(items) >= 1
        # 检查是否包含测试文件
        file_names = [item.name for item in items]
        assert "test.txt" in file_names
        
        # 检查文件类型
        test_file_item = next(item for item in items if item.name == "test.txt")
        assert test_file_item.type == "file"

    @pytest.mark.anyio
    async def test_create_directory_success(self, sample_files):
        """测试成功创建目录"""
        service = FilesystemService()
        new_dir = os.path.join(sample_files["dir"], "new_directory")
        
        await service.create_directory(new_dir)
        
        # 验证目录已创建
        assert os.path.exists(new_dir)
        assert os.path.isdir(new_dir)

    @pytest.mark.anyio
    async def test_move_file_success(self, sample_files):
        """测试成功移动文件"""
        service = FilesystemService()
        source = sample_files["test.txt"]
        destination = os.path.join(sample_files["dir"], "moved_test.txt")
        
        await service.move_file(source, destination)
        
        # 验证文件已移动
        assert not os.path.exists(source)
        assert os.path.exists(destination)
        
        # 验证内容不变
        content = await service.read_text_file(destination, encoding='gbk')
        assert content == "测试内容"

    @pytest.mark.anyio
    async def test_search_files_success(self, sample_files):
        """测试成功搜索文件"""
        service = FilesystemService()
        results = await service.search_files(sample_files["dir"], "*.txt")
        
        assert len(results) >= 1
        # 检查是否找到测试文件
        assert any("test.txt" in result for result in results)

    @pytest.mark.anyio
    async def test_get_file_info_success(self, sample_files):
        """测试成功获取文件信息"""
        service = FilesystemService()
        file_info = await service.get_file_info(sample_files["test.txt"])
        
        assert file_info.type == "file"
        assert file_info.size > 0
        assert file_info.absolute_path.endswith("test.txt")
