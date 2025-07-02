"""测试核心文件系统服务功能"""

from pathlib import Path

import pytest

from mcp_os_server.filesystem.filesystem_service import FilesystemService


class TestFilesystemService:
    """测试FilesystemService类"""

    def test_init_with_allowed_dirs(self, temp_dir):
        """测试使用允许目录初始化服务"""
        service = FilesystemService([str(temp_dir)])
        assert len(service.allowed_dirs) == 1
        assert Path(temp_dir).resolve() in service.allowed_dirs

    def test_init_empty_dirs_raises_error(self):
        """测试没有允许目录时抛出错误"""
        with pytest.raises(ValueError, match="至少需要指定一个允许的目录"):
            FilesystemService([])

    def test_is_path_allowed(self, temp_dir):
        """测试路径权限检查"""
        service = FilesystemService([str(temp_dir)])

        # 允许的路径
        assert service.is_path_allowed(str(temp_dir / "test.txt"))
        assert service.is_path_allowed(str(temp_dir / "subdir" / "file.txt"))

        # 不允许的路径
        assert not service.is_path_allowed("/root/forbidden.txt")
        assert not service.is_path_allowed(str(temp_dir.parent / "outside.txt"))

    @pytest.mark.asyncio
    async def test_read_file_success(self, sample_files):
        """测试成功读取文件"""
        service = FilesystemService([str(sample_files["temp_dir"])])
        content = await service.read_file(str(sample_files["test_file"]))
        assert content == "Hello, World!"

    @pytest.mark.asyncio
    async def test_read_file_not_allowed(self, sample_files):
        """测试读取不允许的文件"""
        service = FilesystemService([str(sample_files["temp_dir"])])
        with pytest.raises(PermissionError, match="路径不在允许的目录中"):
            await service.read_file("/etc/passwd")

    @pytest.mark.asyncio
    async def test_read_file_not_found(self, sample_files):
        """测试读取不存在的文件"""
        service = FilesystemService([str(sample_files["temp_dir"])])
        with pytest.raises(FileNotFoundError):
            await service.read_file(str(sample_files["temp_dir"] / "not_exist.txt"))

    @pytest.mark.asyncio
    async def test_write_file_success(self, sample_files):
        """测试成功写入文件"""
        service = FilesystemService([str(sample_files["temp_dir"])])
        new_file = sample_files["temp_dir"] / "new_file.txt"

        await service.write_file(str(new_file), "New content")

        assert new_file.exists()
        assert new_file.read_text() == "New content"

    @pytest.mark.asyncio
    async def test_write_file_not_allowed(self, sample_files):
        """测试写入不允许的文件"""
        service = FilesystemService([str(sample_files["temp_dir"])])
        with pytest.raises(PermissionError, match="路径不在允许的目录中"):
            await service.write_file("/tmp/forbidden.txt", "content")

    @pytest.mark.asyncio
    async def test_list_directory_success(self, sample_files):
        """测试成功列出目录内容"""
        service = FilesystemService([str(sample_files["temp_dir"])])
        items = await service.list_directory(str(sample_files["temp_dir"]))

        # 应该包含我们创建的文件和目录
        item_names = [item["name"] for item in items]
        assert "test.txt" in item_names
        assert "subdir" in item_names
        assert "data.json" in item_names

    @pytest.mark.asyncio
    async def test_create_directory_success(self, sample_files):
        """测试成功创建目录"""
        service = FilesystemService([str(sample_files["temp_dir"])])
        new_dir = sample_files["temp_dir"] / "new_directory"

        await service.create_directory(str(new_dir))

        assert new_dir.exists()
        assert new_dir.is_dir()

    @pytest.mark.asyncio
    async def test_move_file_success(self, sample_files):
        """测试成功移动文件"""
        service = FilesystemService([str(sample_files["temp_dir"])])
        source = sample_files["test_file"]
        destination = sample_files["temp_dir"] / "moved_test.txt"

        await service.move_file(str(source), str(destination))

        assert not source.exists()
        assert destination.exists()
        assert destination.read_text() == "Hello, World!"

    @pytest.mark.asyncio
    async def test_search_files_success(self, sample_files):
        """测试成功搜索文件"""
        service = FilesystemService([str(sample_files["temp_dir"])])
        results = await service.search_files(str(sample_files["temp_dir"]), "*.txt")

        # 应该找到txt文件
        result_names = [Path(result).name for result in results]
        assert "test.txt" in result_names
        assert "nested.txt" in result_names

    @pytest.mark.asyncio
    async def test_get_file_info_success(self, sample_files):
        """测试成功获取文件信息"""
        service = FilesystemService([str(sample_files["temp_dir"])])
        info = await service.get_file_info(str(sample_files["test_file"]))

        assert info["type"] == "file"
        assert info["size"] > 0
        assert "created" in info
        assert "modified" in info
        assert "accessed" in info
