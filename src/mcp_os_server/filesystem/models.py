"""文件系统服务模型定义"""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class FileSystemServiceFeature(Enum):
    """文件系统服务功能特性枚举"""

    # 支持 Cursor 目录格式，例如 /e:/Programming/Demo/project/file.txt
    SupportCursorDirectoryFormat = "support_cursor_directory_format"


class FileReadResult(BaseModel):
    """文件读取结果"""

    success: bool = Field(..., description="是否成功读取文件")
    content: Optional[str] = Field(None, description="文件内容")
    error: Optional[str] = Field(None, description="错误信息")


class DirectoryItem(BaseModel):
    """目录项"""

    name: str = Field(..., description="文件或目录名称")
    type: str = Field(..., description="类型，file或directory")
    path: str = Field(..., description="完整路径")


class FileInfo(BaseModel):
    """文件信息"""

    type: str = Field(..., description="文件类型，file或directory")
    size: int = Field(..., description="文件大小")
    created: str = Field(..., description="创建时间")
    modified: str = Field(..., description="修改时间")
    accessed: str = Field(..., description="访问时间")
    permissions: str = Field(..., description="权限")
    absolute_path: str = Field(..., description="绝对路径")


class EditChange(BaseModel):
    """编辑变更"""

    old: str = Field(..., description="原始文本")
    new: str = Field(..., description="新文本")
    applied: bool = Field(..., description="是否应用成功")
    error: Optional[str] = Field(None, description="错误信息")


class FileEditResult(BaseModel):
    """文件编辑结果"""

    changes_made: List[EditChange] = Field(..., description="变更列表")
    content_changed: bool = Field(..., description="内容是否有变更")
    preview: Optional[str] = Field(None, description="预览内容（dry_run模式）")
    message: Optional[str] = Field(None, description="操作消息")
