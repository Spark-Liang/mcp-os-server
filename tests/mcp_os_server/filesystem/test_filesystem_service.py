"""测试核心文件系统服务功能"""

import os
from pathlib import Path

import pytest

from mcp_os_server.filesystem.filesystem_service import FilesystemService
from mcp_os_server.filesystem.models import FileSystemServiceFeature


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
        item_names = [item.name for item in items]
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

        assert info.type == "file"
        assert info.size > 0
        assert info.created is not None
        assert info.modified is not None
        assert info.accessed is not None

    @pytest.mark.skipif(os.name != "nt", reason="Cursor 目录格式只在 Windows 上支持")
    def test_cursor_directory_format_enabled(self, temp_dir):
        """测试启用 SupportCursorDirectoryFormat 功能时的路径转换"""
        # 启用 SupportCursorDirectoryFormat 功能
        features = [FileSystemServiceFeature.SupportCursorDirectoryFormat]
        service = FilesystemService([str(temp_dir)], features=features)
        
        # 获取当前盘符
        current_drive = str(temp_dir)[:2]  # 例如：'C:'
        drive_letter = current_drive[0].lower()  # 例如：'c'
        
        # 测试 Cursor 格式路径应该被转换并允许访问
        cursor_path = f"/{drive_letter}:{str(temp_dir)[2:]}/test.txt"
        assert service.is_path_allowed(cursor_path)
        
        # 测试不同盘符的情况
        if drive_letter != 'e':
            cursor_path_e = f"/e:{str(temp_dir)[2:]}/test.txt"
            # 这个路径应该不被允许，因为盘符不匹配
            assert not service.is_path_allowed(cursor_path_e)
    
    @pytest.mark.skipif(os.name != "nt", reason="Cursor 目录格式只在 Windows 上支持")
    def test_cursor_directory_format_disabled(self, temp_dir):
        """测试不启用 SupportCursorDirectoryFormat 功能时的行为"""
        # 不启用 SupportCursorDirectoryFormat 功能
        service = FilesystemService([str(temp_dir)])
        
        # 获取当前盘符
        current_drive = str(temp_dir)[:2]  # 例如：'C:'
        drive_letter = current_drive[0].lower()  # 例如：'c'
        
        # 测试 Cursor 格式路径应该被当作普通路径处理，不被允许
        cursor_path = f"/{drive_letter}:{str(temp_dir)[2:]}/test.txt"
        assert not service.is_path_allowed(cursor_path)
    
    @pytest.mark.skipif(os.name != "nt", reason="Cursor 目录格式只在 Windows 上支持")
    def test_cursor_directory_format_edge_cases(self, temp_dir):
        """测试 Cursor 目录格式的边界情况"""
        # 启用 SupportCursorDirectoryFormat 功能
        features = [FileSystemServiceFeature.SupportCursorDirectoryFormat]
        service = FilesystemService([str(temp_dir)], features=features)
        
        # 测试只是 / 开头但不是盘符格式的路径，应该不被转换
        assert not service.is_path_allowed("/usr/local/test.txt")
        assert not service.is_path_allowed("/home/user/test.txt")
        
        # 测试格式不正确的路径
        assert not service.is_path_allowed("/e/test.txt")  # 缺少冒号
        assert not service.is_path_allowed("/12:/test.txt")  # 非字母盘符
        assert not service.is_path_allowed("/")  # 只有根路径
        assert not service.is_path_allowed("/e:")  # 只有盘符，没有路径
    
    def test_cursor_directory_format_pattern_validation(self, temp_dir):
        """测试 Cursor 目录格式的模式验证"""
        # 启用 SupportCursorDirectoryFormat 功能
        features = [FileSystemServiceFeature.SupportCursorDirectoryFormat]
        service = FilesystemService([str(temp_dir)], features=features)
        
        # 测试严格的模式匹配：开头必须是 /<盘符>:
        test_cases = [
            ("/e:/Programming/Demo/file.txt", True),  # 正确格式
            ("/c:/Users/test.txt", True),  # 正确格式
            ("/d:/Projects/app.py", True),  # 正确格式
            ("/usr/local/bin", False),  # 普通 Unix 路径
            ("/home/user", False),  # 普通 Unix 路径
            ("/e/test.txt", False),  # 缺少冒号
            ("e:/test.txt", False),  # 不以 / 开头
            ("/12:/test.txt", False),  # 非字母盘符
            ("/:/test.txt", False),  # 空盘符
            ("/", False),  # 只有根路径
        ]
        
        for path, should_match_pattern in test_cases:
            # 检查是否匹配 Cursor 格式模式
            matches_cursor_pattern = (
                path.startswith('/') and 
                len(path) > 3 and 
                path[2] == ':' and 
                path[1].isalpha()
            )
            assert matches_cursor_pattern == should_match_pattern, f"路径 {path} 的模式匹配结果不正确"
