# MCP Filesystem Server 功能设计文档

一个实现模型上下文协议（MCP）的精简且强大的文件系统服务。它提供安全的文件操作，包括文件读写、目录管理、文件搜索和图片处理等。

## 功能特点

*   **安全的文件系统操作**：所有文件操作都经过严格的路径检查，确保只在允许的目录中进行。
*   **全面的文件和目录管理**：支持读取、写入文件，创建、列出目录，移动/重命名文件/目录。
*   **强大的文件搜索能力**：支持基于glob模式的文件搜索，并可指定排除模式。
*   **详细的文件/目录信息获取**：能够获取文件或目录的类型、大小、创建/修改时间等详细信息。
*   **图片处理能力**：支持读取图片文件，并可根据大小限制自动创建缩略图，返回Image内容。
*   **批量操作支持**：支持批量读取多个文件或图片。
*   **文件内容编辑**：支持对文件内容进行查找替换式的编辑。
*   **多种服务器模式**：支持stdio（默认）、SSE和streamable HTTP模式。

## 安全性

服务器实现了几项安全措施：

1.  **路径白名单**：只有在初始化时指定的允许目录中的路径才能被访问和操作。
2.  **绝对路径校验**：所有路径操作都将路径标准化为绝对路径进行检查，防止路径遍历攻击。
3.  **无Shell注入风险**：文件操作直接通过Python的os和shutil模块执行，不涉及shell命令解析。

## 启动服务器

### stdio模式（默认）

```bash
# 基本用法（stdio模式）
ALLOWED_DIRS="/path/to/your/safe/dir" uvx mcp-filesystem-server

# 明确指定stdio模式
ALLOWED_DIRS="/path/to/your/safe/dir" uvx mcp-filesystem-server stdio
```

### SSE模式

以服务器发送事件（SSE）模式启动服务器：

```bash
# 使用默认设置（主机：127.0.0.1，端口：8000）
ALLOWED_DIRS="/path/to/your/safe/dir" uvx mcp-filesystem-server sse

# 使用自定义主机和端口
ALLOWED_DIRS="/path/to/your/safe/dir" uvx mcp-filesystem-server sse --host 0.0.0.0 --port 9000

# 使用自定义Web路径
ALLOWED_DIRS="/path/to/your/safe/dir" uvx mcp-filesystem-server sse --web-path /filesystem-web
```

### Streamable HTTP模式

以可流式HTTP模式启动服务器：

```bash
# 使用默认设置（主机：127.0.0.1，端口：8000，路径：/mcp）
ALLOWED_DIRS="/path/to/your/safe/dir" uvx mcp-filesystem-server http

# 使用自定义主机、端口和路径
ALLOWED_DIRS="/path/to/your/safe/dir" uvx mcp-filesystem-server http --host 0.0.0.0 --port 9000 --path /filesystem-api

# 使用自定义Web路径
ALLOWED_DIRS="/path/to/your/safe/dir" uvx mcp-filesystem-server http --web-path /filesystem-web
```

## 环境变量

`ALLOWED_DIRS` 环境变量指定了允许访问的目录列表。多个目录可以使用操作系统特定的路径分隔符（Windows上为`;`，Linux/macOS上为`:`）进行分隔。

ALLOWED_DIRS的有效格式：

```bash
# Linux/macOS
ALLOWED_DIRS="/home/user/data:/tmp"

# Windows
ALLOWED_DIRS="C:\Users\User\Documents;D:\Projects"
```

#### 可配置的环境变量

您可以使用以下环境变量自定义MCP Filesystem Server的行为：

| 环境变量         | 描述               | 默认值 | 示例                                        |
|----------------|------------------|--------|-------------------------------------------|
| ALLOWED_DIRS   | 允许访问的目录列表（路径分隔符分隔） | （空 - 不允许任何目录） | `ALLOWED_DIRS="/var/www/html,/home/user/docs"` |

#### 服务器启动示例

**基本启动（最小权限）：**
```bash
ALLOWED_DIRS="/tmp" uvx mcp-filesystem-server
```

**开发环境（扩展权限）：**
```bash
ALLOWED_DIRS="/home/dev/projects:/mnt/data" uvx mcp-filesystem-server
```

**生产环境（自定义端口和路径）：**
```bash
ALLOWED_DIRS="/srv/app_data" uvx mcp-filesystem-server http --host 0.0.0.0 --port 8080 --path /fs-api
```

## 服务器模式

服务器支持三种不同的运行模式：

### stdio模式（默认）

使用标准输入/输出进行通信，非常适合与Claude.app和其他MCP客户端集成。

### SSE模式

服务器发送事件（Server-Sent Events）模式允许服务器通过HTTP向客户端推送更新。这对于基于Web的集成和实时更新非常有用。

命令行选项：
- `--host`：服务器主机地址（默认：127.0.0.1）
- `--port`：服务器端口（默认：8000）
- `--web-path`：Web界面路径（默认：/web）

### Streamable HTTP模式

提供HTTP API端点进行通信。此模式适用于RESTful API集成。

命令行选项：
- `--host`：服务器主机地址（默认：127.0.0.1）
- `--port`：服务器端口（默认：8000）
- `--path`：API端点路径（默认：/mcp）
- `--web-path`：Web界面路径（默认：/web）

## Web管理界面

目前MCP Filesystem Server没有独立的Web管理界面。当作为更大MCP系统的一部分集成时，其功能可以通过宿主MCP服务器的Web界面进行管理和监控。

### 访问Web界面

（如果将来提供独立的Web界面，将在此处提供访问信息。）

## API参考

### 工具：fs_read_file

读取文件内容。

#### 请求格式

```json
{
    "path": "/path/to/your/file.txt"
}
```

#### 响应格式

```json
"文件内容"
```

#### 请求参数

| 字段   | 类型     | 必需  | 描述             |
|--------|----------|------|------------------|
| path   | string   | 是   | 要读取的文件路径 |

### 工具：fs_read_multiple_files

读取多个文件的内容。

#### 请求格式

```json
{
    "paths": ["/path/to/file1.txt", "/path/to/file2.txt"]
}
```

#### 响应格式

```json
{
    "/path/to/file1.txt": {
        "success": true,
        "content": "文件1的内容"
    },
    "/path/to/file2.txt": {
        "success": false,
        "error": "文件2读取失败的原因"
    }
}
```

#### 请求参数

| 字段   | 类型       | 必需  | 描述               |
|--------|------------|------|--------------------|
| paths  | string[]   | 是   | 要读取的文件路径列表 |

### 工具：fs_read_image

读取图片文件并返回为Image内容。

#### 请求格式

```json
{
    "path": "/path/to/your/image.jpg",
    "max_bytes": 10485760
}
```

#### 响应格式

```json
{
    "format": "JPEG",
    "data": "base64编码的图片二进制数据"
}
```

#### 请求参数

| 字段      | 类型      | 必需  | 描述                                       |
|-----------|-----------|------|------------------------------------------|
| path      | string    | 是   | 要读取的图片文件路径                     |
| max_bytes | integer   | 否   | 最大字节数限制，超过此大小将创建缩略图（默认：10485760） |

### 工具：fs_read_multiple_images

读取多个图片文件并返回为Image内容列表。

#### 请求格式

```json
{
    "paths": ["/path/to/image1.png", "/path/to/image2.gif"],
    "max_bytes": 5242880
}
```

#### 响应格式

```json
[
    {
        "format": "PNG",
        "data": "base64编码的图片1二进制数据"
    },
    {
        "format": "GIF",
        "data": "base64编码的图片2二进制数据"
    }
]
```

#### 请求参数

| 字段      | 类型       | 必需  | 描述                                       |
|-----------|------------|------|------------------------------------------|
| paths     | string[]   | 是   | 要读取的图片文件路径列表                 |
| max_bytes | integer    | 否   | 最大字节数限制，超过此大小将创建缩略图（默认：10485760） |

### 工具：fs_write_file

写入文件内容。

#### 请求格式

```json
{
    "path": "/path/to/new_file.txt",
    "content": "这是要写入文件的新内容。"
}
```

#### 响应格式

```json
"文件已成功写入: /path/to/new_file.txt"
```

#### 请求参数

| 字段    | 类型     | 必需  | 描述           |
|---------|----------|------|----------------|
| path    | string   | 是   | 要写入的文件路径 |
| content | string   | 是   | 要写入的内容     |

### 工具：fs_create_directory

创建目录。

#### 请求格式

```json
{
    "path": "/path/to/new_directory"
}
```

#### 响应格式

```json
"目录已成功创建: /path/to/new_directory"
```

#### 请求参数

| 字段   | 类型     | 必需  | 描述           |
|--------|----------|------|----------------|
| path   | string   | 是   | 要创建的目录路径 |

### 工具：fs_list_directory

列出目录内容。

#### 请求格式

```json
{
    "path": "/path/to/list"
}
```

#### 响应格式

```json
[
    {
        "name": "file1.txt",
        "type": "file",
        "path": "/path/to/list/file1.txt"
    },
    {
        "name": "subdir",
        "type": "directory",
        "path": "/path/to/list/subdir"
    }
]
```

#### 请求参数

| 字段   | 类型     | 必需  | 描述           |
|--------|----------|------|----------------|
| path   | string   | 是   | 要列出的目录路径 |

### 工具：fs_move_file

移动或重命名文件/目录。

#### 请求格式

```json
{
    "source": "/path/to/old_name.txt",
    "destination": "/path/to/new_name.txt"
}
```

#### 响应格式

```json
"文件已成功移动: /path/to/old_name.txt -> /path/to/new_name.txt"
```

#### 请求参数

| 字段        | 类型     | 必需  | 描述     |
|-------------|----------|------|----------|
| source      | string   | 是   | 源路径   |
| destination | string   | 是   | 目标路径 |

### 工具：fs_search_files

搜索文件。

#### 请求格式

```json
{
    "path": "/path/to/search",
    "pattern": "*.py",
    "exclude_patterns": ["temp_*"]
}
```

#### 响应格式

```json
[
    "/path/to/search/script.py",
    "/path/to/search/subdir/another_script.py"
]
```

#### 请求参数

| 字段             | 类型       | 必需  | 描述                       |
|------------------|------------|------|----------------------------|
| path             | string     | 是   | 搜索起始路径               |
| pattern          | string     | 是   | 搜索模式（支持glob模式，如 *.txt, *.py） |
| exclude_patterns | string[]   | 否   | 要排除的模式列表（可选）   |

### 工具：fs_get_file_info

获取文件或目录的详细信息。

#### 请求格式

```json
{
    "path": "/path/to/your/file_or_dir"
}
```

#### 响应格式

```json
{
    "type": "file",
    "size": 1234,
    "created": "2023-10-26T10:00:00.000000",
    "modified": "2023-10-26T11:30:00.000000",
    "accessed": "2023-10-26T12:00:00.000000",
    "permissions": "644",
    "absolute_path": "/path/to/your/file_or_dir"
}
```

#### 请求参数

| 字段   | 类型     | 必需  | 描述             |
|--------|----------|------|------------------|
| path   | string   | 是   | 文件或目录路径   |

### 工具：fs_edit_file

编辑文件内容。

#### 请求格式

```json
{
    "path": "/path/to/edit.txt",
    "edits": [
        {
            "oldText": "旧文本1",
            "newText": "新文本1"
        },
        {
            "oldText": "旧文本2",
            "newText": "新文本2"
        }
    ],
    "dry_run": false
}
```

#### 响应格式

```json
{
    "changes_made": [
        {
            "old": "旧文本1",
            "new": "新文本1",
            "applied": true
        },
        {
            "old": "旧文本2",
            "new": "新文本2",
            "applied": false,
            "error": "未找到匹配的文本"
        }
    ],
    "content_changed": true,
    "message": "文件已成功更新"
}
```

#### 请求参数

| 字段      | 类型       | 必需  | 描述                                       |
|-----------|------------|------|------------------------------------------|
| path      | string     | 是   | 要编辑的文件路径                         |
| edits     | object[]   | 是   | 编辑操作列表，每个操作包含 oldText 和 newText |
| dry_run   | boolean    | 否   | 是否为预览模式（不实际修改文件）（默认：false） |

### 工具：fs_get_filesystem_info

获取文件系统服务配置信息。

#### 请求格式

```json
{}
```

#### 响应格式

```json
{
    "server_name": "Filesystem Server",
    "version": "1.0.0",
    "work_dir": "/path/to/current/working/directory",
    "allowed_directories": [
        "/path/to/allowed/dir1",
        "/path/to/allowed/dir2"
    ],
    "capabilities": [
        "fs_read_file",
        "fs_read_image",
        "fs_read_multiple_images",
        "fs_write_file",
        "fs_create_directory",
        "fs_list_directory",
        "fs_move_file",
        "fs_search_files",
        "fs_get_file_info",
        "fs_edit_file"
    ]
}
```

#### 请求参数

无

### 资源：file://{path}

作为资源读取文件内容。

#### 访问格式

`file:///path/to/your/file.txt`

#### 响应格式

文件内容（字符串）

### 资源：directory://{path}

作为资源列出目录内容。

#### 访问格式

`directory:///path/to/your/directory`

#### 响应格式

JSON格式的目录内容列表。

```json
[
    {
        "name": "file1.txt",
        "type": "file",
        "path": "/path/to/your/directory/file1.txt"
    },
    {
        "name": "subdir",
        "type": "directory",
        "path": "/path/to/your/directory/subdir"
    }
]
```

### 资源：config://filesystem

获取文件系统服务配置信息。

#### 访问格式

`config://filesystem`

#### 响应格式

JSON格式的配置信息。

```json
{
    "server_name": "Filesystem Server",
    "version": "1.0.0",
    "work_dir": "/path/to/current/working/directory",
    "allowed_directories": [
        "/path/to/allowed/dir1",
        "/path/to/allowed/dir2"
    ],
    "capabilities": [
        "fs_read_file",
        "fs_read_image",
        "fs_read_multiple_images",
        "fs_write_file",
        "fs_create_directory",
        "fs_list_directory",
        "fs_move_file",
        "fs_search_files",
        "fs_get_file_info",
        "fs_edit_file"
    ]
}
```

## 开发

### 设置开发环境

1.  克隆仓库

```bash
git clone https://github.com/yourusername/mcp-os-server.git
cd mcp-os-server
```

2.  安装依赖项（包括测试需求）

```bash
uv --project . sync
```

### 运行测试

```bash
uv --project . run pytest tests/mcp_os_server/filesystem/
```

## 系统要求

*   Python 3.11或更高版本
*   mcp>=1.1.0
*   Pillow (用于图片处理)

## 许可证

MIT许可证 - 详情请参阅LICENSE文件
