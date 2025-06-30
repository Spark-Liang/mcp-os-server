"""pytest配置文件"""

import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_dir():
    """创建临时目录用于测试"""
    temp_path = tempfile.mkdtemp()
    yield Path(temp_path)
    shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def sample_files(temp_dir):
    """创建示例文件用于测试"""
    # 创建一些测试文件和目录
    test_file = temp_dir / "test.txt"
    test_file.write_text("Hello, World!")

    subdir = temp_dir / "subdir"
    subdir.mkdir()

    nested_file = subdir / "nested.txt"
    nested_file.write_text("Nested content")

    json_file = temp_dir / "data.json"
    json_file.write_text('{"key": "value"}')

    return {
        "temp_dir": temp_dir,
        "test_file": test_file,
        "subdir": subdir,
        "nested_file": nested_file,
        "json_file": json_file,
    }


@pytest.fixture
def allowed_dirs(temp_dir):
    """允许访问的目录列表"""
    return [str(temp_dir)]
