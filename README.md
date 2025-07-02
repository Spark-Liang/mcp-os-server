# MCP OS Server

一个实现模型上下文协议（MCP）的精简且强大的操作系统操作服务器。它集成了命令执行、文件系统管理以及两者的统一服务，旨在提供安全、可控且高效的OS交互能力。

## 功能特点

### MCP Command Server

*   **安全的命令执行**：仅支持 `command + args` 的简单命令格式，不解析或支持复杂的 shell 操作符（如 `|`, `&&`, `||`, `;`）。
*   **标准输入支持**：通过stdin向命令传递输入。
*   **全面的输出信息**：返回stdout、stderr、退出状态和执行时间。
*   **强大的单个进程管理**：对单个命令的执行生命周期提供精细控制，包括：
    *   **超时控制**：设置命令的最大执行时间。
    *   **实时输出获取**：能够实时监控进程的stdout和stderr。
    *   **详细状态查询**：获取进程的运行状态、资源使用等详细信息。
    *   **生命周期控制**：支持停止、终止和清理进程。
*   **模块化进程管理接口**：核心进程管理能力设计为可复用模块，方便其他MCP服务器或应用集成使用。
*   **后台进程管理**：支持在后台运行长时间运行的命令并进行管理。
*   **Web管理界面**：通过Web UI监控和管理后台进程。
*   **多种服务器模式**：支持stdio（默认）、SSE和streamable HTTP模式。

### MCP Filesystem Server

*   **安全的文件系统操作**：所有文件操作都经过严格的路径检查，确保只在允许的目录中进行。
*   **全面的文件和目录管理**：支持读取、写入文件，创建、列出目录，移动/重命名文件/目录。
*   **强大的文件搜索能力**：支持基于glob模式的文件搜索，并可指定排除模式。
*   **详细的文件/目录信息获取**：能够获取文件或目录的类型、大小、创建/修改时间等详细信息。
*   **图片处理能力**：支持读取图片文件，并可根据大小限制自动创建缩略图，返回Image内容。
*   **批量操作支持**：支持批量读取多个文件或图片。
*   **文件内容编辑**：支持对文件内容进行查找替换式的编辑。
*   **多种服务器模式**：支持stdio（默认）、SSE和streamable HTTP模式。

### MCP Unified Server

*   **集成功能**：在一个服务器中同时提供命令执行和文件系统管理功能。
*   **统一接口**：通过一套统一的MCP协议接口访问两种能力。
*   **多模式支持**：同样支持stdio、SSE和streamable HTTP模式。

## 安全性

服务器实现了以下几项安全措施：

1.  **命令白名单**：Command Server 仅允许执行明确列出的命令，并限制复杂的 shell 操作符。
2.  **路径白名单**：Filesystem Server 仅允许访问和操作初始化时指定的目录中的路径，防止路径遍历攻击。
3.  **无Shell注入风险**：所有操作都直接通过 Python 模块执行，不涉及 shell 命令解析，从而避免了常见的注入风险。
4.  **绝对路径校验**：所有文件路径操作都将路径标准化为绝对路径进行检查。

## 启动服务器

MCP OS Server 支持多种运行模式：`stdio` (默认), `sse` (Server-Sent Events) 和 `http` (Streamable HTTP)。你可以通过命令行参数来指定启动模式和相关配置。

### MCP Command Server

启动命令服务器以执行和管理系统命令。

#### stdio模式 (默认)

```bash
# 基本用法（stdio模式）
ALLOWED_COMMANDS="ls,cat,echo" uv --project . run mcp-os-server command-server

# 明确指定stdio模式
ALLOWED_COMMANDS="ls,cat,echo" uv --project . run mcp-os-server command-server --mode stdio
```

#### SSE模式

以服务器发送事件（SSE）模式启动命令服务器。

```bash
# 使用默认设置（主机：127.0.0.1，端口：8000）
ALLOWED_COMMANDS="ls,cat,echo" uv --project . run mcp-os-server command-server --mode sse

# 使用自定义主机和端口
ALLOWED_COMMANDS="ls,cat,echo" uv --project . run mcp-os-server command-server --mode sse --host 0.0.0.0 --port 9000

# 使用自定义Web路径
ALLOWED_COMMANDS="ls,cat,echo" uv --project . run mcp-os-server command-server --mode sse --web-path /command-web
```

#### Streamable HTTP模式

以可流式HTTP模式启动命令服务器。

```bash
# 使用默认设置（主机：127.0.0.1，端口：8000，路径：/mcp）
ALLOWED_COMMANDS="ls,cat,echo" uv --project . run mcp-os-server command-server --mode http

# 使用自定义主机、端口和路径
ALLOWED_COMMANDS="ls,cat,echo" uv --project . run mcp-os-server command-server --mode http --host 0.0.0.0 --port 9000 --path /command-api

# 使用自定义Web路径
ALLOWED_COMMANDS="ls,cat,echo" uv --project . run mcp-os-server command-server --mode http --web-path /command-web
```

### MCP Filesystem Server

启动文件系统服务器以进行文件操作。

#### stdio模式 (默认)

```bash
# 基本用法（stdio模式）
ALLOWED_DIRS="/path/to/your/safe/dir" uv --project . run mcp-os-server filesystem-server

# 明确指定stdio模式
ALLOWED_DIRS="/path/to/your/safe/dir" uv --project . run mcp-os-server filesystem-server --mode stdio
```

#### SSE模式

以服务器发送事件（SSE）模式启动文件系统服务器。

```bash
# 使用默认设置（主机：127.0.0.1，端口：8000）
ALLOWED_DIRS="/path/to/your/safe/dir" uv --project . run mcp-os-server filesystem-server --mode sse

# 使用自定义主机和端口
ALLOWED_DIRS="/path/to/your/safe/dir" uv --project . run mcp-os-server filesystem-server --mode sse --host 0.0.0.0 --port 9000

# 使用自定义Web路径
ALLOWED_DIRS="/path/to/your/safe/dir" uv --project . run mcp-os-server filesystem-server --mode sse --web-path /filesystem-web
```

#### Streamable HTTP模式

以可流式HTTP模式启动文件系统服务器。

```bash
# 使用默认设置（主机：127.0.0.1，端口：8000，路径：/mcp）
ALLOWED_DIRS="/path/to/your/safe/dir" uv --project . run mcp-os-server filesystem-server --mode http

# 使用自定义主机、端口和路径
ALLOWED_DIRS="/path/to/your/safe/dir" uv --project . run mcp-os-server filesystem-server --mode http --host 0.0.0.0 --port 9000 --path /filesystem-api

# 使用自定义Web路径
ALLOWED_DIRS="/path/to/your/safe/dir" uv --project . run mcp-os-server filesystem-server --mode http --web-path /filesystem-web
```

### MCP Unified Server

启动统一服务器，同时提供命令执行和文件系统管理功能。

#### stdio模式 (默认)

```bash
# 基本用法（stdio模式）
ALLOWED_COMMANDS="ls,cat,echo" ALLOWED_DIRS="/tmp" uv --project . run mcp-os-server unified-server

# 明确指定stdio模式
ALLOWED_COMMANDS="ls,cat,echo" ALLOWED_DIRS="/tmp" uv --project . run mcp-os-server unified-server --mode stdio
```

#### SSE模式

以服务器发送事件（SSE）模式启动统一服务器。

```bash
# 使用默认设置（主机：127.0.0.1，端口：8000）
ALLOWED_COMMANDS="ls,cat,echo" ALLOWED_DIRS="/tmp" uv --project . run mcp-os-server unified-server --mode sse

# 使用自定义主机和端口
ALLOWED_COMMANDS="ls,cat,echo" ALLOWED_DIRS="/tmp" uv --project . run mcp-os-server unified-server --mode sse --host 0.0.0.0 --port 9000

# 使用自定义Web路径
ALLOWED_COMMANDS="ls,cat,echo" ALLOWED_DIRS="/tmp" uv --project . run mcp-os-server unified-server --mode sse --web-path /unified-web
```

#### Streamable HTTP模式

以可流式HTTP模式启动统一服务器。

```bash
# 使用默认设置（主机：127.0.0.1，端口：8000，路径：/mcp）
ALLOWED_COMMANDS="ls,cat,echo" ALLOWED_DIRS="/tmp" uv --project . run mcp-os-server unified-server --mode http

# 使用自定义主机、端口和路径
ALLOWED_COMMANDS="ls,cat,echo" ALLOWED_DIRS="/tmp" uv --project . run mcp-os-server unified-server --mode http --host 0.0.0.0 --port 9000 --path /unified-api

# 使用自定义Web路径
ALLOWED_COMMANDS="ls,cat,echo" ALLOWED_DIRS="/tmp" uv --project . run mcp-os-server unified-server --mode http --web-path /unified-web
```

## 环境变量

您可以使用以下环境变量自定义 MCP OS Server 的行为：

| 环境变量                        | 描述                                              | 默认值            | 适用服务器               | 示例                                                                                                                               |
| --------------------------- | ----------------------------------------------- | -------------- | ------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| `ALLOWED_COMMANDS`          | 允许执行的命令列表（逗号分隔）                                 | （空 - 不允许任何命令）  | Command, Unified    | `ALLOWED_COMMANDS="ls,cat,echo,npm,python"`                                                                                      |
| `ALLOWED_DIRS`              | 允许访问的目录列表（操作系统路径分隔符分隔）                          | （空 - 不允许任何目录）  | Filesystem, Unified | `ALLOWED_DIRS="/var/www/html,/home/user/docs"` (Linux/macOS) <br> `ALLOWED_DIRS="C:\Users\User\Documents;D:\Projects"` (Windows) |
| `PROCESS_RETENTION_SECONDS` | 清理前保留已完成进程的时间（秒）                                | `3600` (1小时)   | Command, Unified    | `PROCESS_RETENTION_SECONDS=86400`                                                                                                |
| `DEFAULT_ENCODING`          | 进程输出的默认字符编码                                     | 系统终端编码或`utf-8` | Command, Unified    | `DEFAULT_ENCODING=gbk`                                                                                                           |
| `OUTPUT_STORAGE_PATH`       | 命令输出日志的存储路径                                     | 临时目录           | Command, Unified    | `OUTPUT_STORAGE_PATH=/var/log/mcp-os-server`                                                                                     |
| `DISABLE_TOOLS`             | 逗号分隔的要禁用的工具列表                                   | （空）            | 所有服务器               | `DISABLE_TOOLS="read_file,command_execute"`                                                                                      |
| `ENABLE_TOOLS_ONLY`         | 逗号分隔的只允许启用的工具列表（如果设置，优先级高于 `DISABLE_TOOLS`）     | （空）            | 所有服务器               | `ENABLE_TOOLS_ONLY="write_file,command_bg_start"`                                                                                |
| `DISABLE_RESOURCES`         | 逗号分隔的要禁用的资源列表                                   | （空）            | 所有服务器               | `DISABLE_RESOURCES="file,directory"`                                                                                             |
| `ENABLE_RESOURCES_ONLY`     | 逗号分隔的只允许启用的资源列表（如果设置，优先级高于 `DISABLE_RESOURCES`） | （空）            | 所有服务器               | `ENABLE_RESOURCES_ONLY="config"`                                                                                                 |

## API参考

MCP OS Server 暴露了以下 MCP 工具，客户端可以通过它们与服务器进行交互。

### MCP Command Server 工具

#### `command_execute`

执行单个 shell 命令并返回结果。此工具仅支持 `command + args` 形式的简单命令，不解析复杂的 shell 操作符。

**请求参数**

| 字段        | 类型       | 必需  | 描述                                       |
|-------------|------------|------|------------------------------------------|
| `command`   | string     | 是   | 要执行的命令                             |
| `args`      | string[]   | 否   | 命令的参数列表                           |
| `directory` | string     | 是   | 命令执行的工作目录                       |
| `stdin`     | string     | 否   | 通过stdin传递给命令的输入                |
| `timeout`   | integer    | 否   | 最大执行时间（秒）（默认：15）           |
| `envs`      | object     | 否   | 命令的附加环境变量                       |
| `encoding`  | string     | 否   | 命令输出的字符编码（例如：'utf-8', 'gbk'） |
| `limit_lines` | integer  | 否   | 每个TextContent返回的最大行数（默认：500） |

**响应格式**

```json
{
    "type": "text",
    "text": "**exit with 0**"
},
{
    "type": "text",
    "text": "---\nstdout:\n---\n命令输出内容\n"
},
{
    "type": "text",
    "text": "---\nstderr:\n---\n错误输出内容\n"
}
```

#### `command_bg_start`

启动单个命令的后台进程，并对其进行精细管理。

**请求参数**

| 字段         | 类型       | 必需  | 描述                                 |
|--------------|------------|------|-------------------------------------|
| `command`    | string     | 是   | 要执行的命令                         |
| `args`       | string[]   | 否   | 命令的参数列表                       |
| `directory`  | string     | 是   | 命令执行的工作目录                     |
| `description`| string     | 是   | 命令的描述                            |
| `labels`     | string[]   | 否   | 用于分类命令的标签                     |
| `stdin`      | string     | 否   | 通过stdin传递给命令的输入              |
| `envs`       | object     | 否   | 命令的附加环境变量                     |
| `encoding`   | string     | 否   | 命令输出的字符编码                     |
| `timeout`    | integer    | 否   | 最大执行时间（秒）                     |

**响应格式**

```json
{
    "type": "text",
    "text": "已启动后台进程，ID: 123"
}
```

#### `command_ps_list`

列出正在运行或已完成的后台进程。

**请求参数**

| 字段     | 类型       | 必需  | 描述                                     |
|----------|------------|------|------------------------------------------|
| `labels` | string[]   | 否   | 按标签过滤进程                             |
| `status` | string     | 否   | 按状态过滤('running', 'completed', 'failed', 'terminated', 'error') |

**响应格式**

```json
{
    "type": "text",
    "text": "ID | 状态 | 开始时间 | 命令 | 描述 | 标签\n---------\n123 | running | 2023-05-06 14:30:00 | npm start | 启动Node.js应用 | nodejs"
}
```

#### `command_ps_stop`

停止正在运行的进程。

**请求参数**

| 字段    | 类型     | 必需  | 描述                               |
|---------|----------|------|-----------------------------------|
| `pid`   | string   | 是   | 要停止的进程ID                      |
| `force` | boolean  | 否   | 是否强制停止进程（默认：false）       |

**响应格式**

```json
{
    "type": "text",
    "text": "进程123已被优雅地停止\n命令: npm start\n描述: 启动Node.js应用"
}
```

#### `command_ps_logs`

获取进程的输出，并支持通过正则表达式进行筛选。

**请求参数**

| 字段                | 类型      | 必需  | 描述                                       |
|---------------------|-----------|------|-------------------------------------------|
| `pid`               | string    | 是   | 获取输出的进程ID                            |
| `tail`              | integer   | 否   | 从末尾显示的行数                             |
| `since`             | string    | 否   | 显示从该时间戳开始的日志（ISO格式，例如：'2023-05-06T14:30:00'） |
| `until`             | string    | 否   | 显示到该时间戳为止的日志（ISO格式，例如：'2023-05-06T15:30:00'） |
| `with_stdout`       | boolean   | 否   | 显示标准输出（默认：true）                    |
| `with_stderr`       | boolean   | 否   | 显示错误输出（默认：false）                   |
| `add_time_prefix`   | boolean   | 否   | 为每行输出添加时间戳前缀（默认：true）          |
| `time_prefix_format`| string    | 否   | 时间戳前缀的格式（默认："%Y-%m-%d %H:%M:%S.%f"） |
| `follow_seconds`    | integer   | 否   | 等待指定秒数以获取新日志（默认：1）             |
| `limit_lines`       | integer   | 否   | 每个TextContent返回的最大行数（默认：500）      |
| `grep`              | string    | 否   | 用于筛选输出的**Perl标准正则表达式**           |
| `grep_mode`         | string    | 否   | 返回匹配内容的模式：`line`（匹配行）或`content`（匹配内容本身）。类比`grep`和`grep -o`，默认为`line`。 |

**响应格式**

```json
[
    {
        "type": "text",
        "text": "**进程123（状态：running）**\n命令: npm start\n描述: 启动Node.js应用\n状态: 进程仍在运行"
    },
    {
        "type": "text",
        "text": "---\nstdout: 匹配内容（根据grep_mode）\n---\n[2023-05-06 14:35:27.123456] 服务器在端口3000上启动\n"
    }
]
```

#### `command_ps_clean`

清理已完成或失败的进程。

**请求参数**

| 字段    | 类型       | 必需  | 描述                         |
|---------|------------|------|----------------------------|
| `pids`  | string[]   | 是   | 要清理的进程ID列表            |

**响应格式**

```json
{
    "type": "text",
    "text": "**成功清理了1个进程:**\n- PID: 123 | 命令: npm start\n\n**无法清理1个运行中的进程:**\n注意: 无法清理正在运行的进程。请先使用`command_bg_stop()`停止它们。\n- PID: 456 | 命令: node server.js\n\n**清理1个进程失败:**\n- PID: 789 | 原因: 找不到进程"
}
```

#### `command_ps_detail`

获取特定进程的详细信息。

**请求参数**

| 字段    | 类型     | 必需  | 描述                   |
|---------|----------|------|------------------------|
| `pid`   | string   | 是   | 获取详情的进程ID        |

**响应格式**

```json
{
    "type": "text",
    "text": "### 进程详情: 123\n\n#### 基本信息\n- **状态**: completed\n- **命令**: `npm start`\n- **描述**: 启动Node.js应用\n- **标签**: nodejs, app\n\n#### 时间信息\n- **开始时间**: 2023-05-06 14:30:00\n- **结束时间**: 2023-05-06 14:35:27\n- **持续时间**: 0:05:27\n
#### 执行信息\n- **工作目录**: /path/to/project\n- **退出码**: 0\n
#### 输出信息\n- 使用`command_bg_logs`工具查看进程输出\n- 示例: `command_bg_logs(pid=123)`"
}
```

### MCP Filesystem Server 工具

#### `fs_read_file`

读取文件内容。

**请求参数**

| 字段   | 类型     | 必需  | 描述             |
|--------|----------|------|------------------|
| `path` | string   | 是   | 要读取的文件路径 |

**响应格式**

```json
"文件内容"
```

#### `fs_read_multiple_files`

读取多个文件的内容。

**请求参数**

| 字段    | 类型       | 必需  | 描述               |
|---------|------------|------|--------------------|
| `paths` | string[]   | 是   | 要读取的文件路径列表 |

**响应格式**

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

#### `fs_read_image`

读取图片文件并返回为Image内容。

**请求参数**

| 字段      | 类型      | 必需  | 描述                                       |
|-----------|-----------|------|------------------------------------------|
| `path`      | string    | 是   | 要读取的图片文件路径                     |
| `max_bytes` | integer   | 否   | 最大字节数限制，超过此大小将创建缩略图（默认：10485760） |

**响应格式**

```json
{
    "format": "JPEG",
    "data": "base64编码的图片二进制数据"
}
```

#### `fs_read_multiple_images`

读取多个图片文件并返回为Image内容列表。

**请求参数**

| 字段      | 类型       | 必需  | 描述                                       |
|-----------|------------|------|------------------------------------------|
| `paths`     | string[]   | 是   | 要读取的图片文件路径列表                 |
| `max_bytes` | integer    | 否   | 最大字节数限制，超过此大小将创建缩略图（默认：10485760） |

**响应格式**

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

#### `fs_write_file`

写入文件内容。

**请求参数**

| 字段    | 类型     | 必需  | 描述           |
|---------|----------|------|----------------|
| `path`    | string   | 是   | 要写入的文件路径 |
| `content` | string   | 是   | 要写入的内容     |

**响应格式**

```json
"文件已成功写入: /path/to/new_file.txt"
```

#### `fs_create_directory`

创建目录。

**请求参数**

| 字段   | 类型     | 必需  | 描述           |
|--------|----------|------|----------------|
| `path` | string   | 是   | 要创建的目录路径 |

**响应格式**

```json
"目录已成功创建: /path/to/new_directory"
```

#### `fs_list_directory`

列出目录内容。

**请求参数**

| 字段   | 类型     | 必需  | 描述           |
|--------|----------|------|----------------|
| `path` | string   | 是   | 要列出的目录路径 |

**响应格式**

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

#### `fs_move_file`

移动或重命名文件/目录。

**请求参数**

| 字段         | 类型     | 必需  | 描述     |
|--------------|----------|------|----------|
| `source`     | string   | 是   | 源路径   |
| `destination`| string   | 是   | 目标路径 |

**响应格式**

```json
"文件已成功移动: /path/to/old_name.txt -> /path/to/new_name.txt"
```

#### `fs_search_files`

搜索文件。

**请求参数**

| 字段             | 类型       | 必需  | 描述                       |
|------------------|------------|------|----------------------------|
| `path`           | string     | 是   | 搜索起始路径               |
| `pattern`        | string     | 是   | 搜索模式（支持glob模式，如 *.txt, *.py） |
| `exclude_patterns` | string[]   | 否   | 要排除的模式列表（可选）   |

**响应格式**

```json
[
    "/path/to/search/script.py",
    "/path/to/search/subdir/another_script.py"
]
```

#### `fs_get_file_info`

获取文件或目录的详细信息。

**请求参数**

| 字段   | 类型     | 必需  | 描述             |
|--------|----------|------|------------------|
| `path` | string   | 是   | 文件或目录路径   |

**响应格式**

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

#### `fs_edit_file`

编辑文件内容。

**请求参数**

| 字段      | 类型       | 必需  | 描述                                       |
|-----------|------------|------|------------------------------------------|
| `path`    | string     | 是   | 要编辑的文件路径                         |
| `edits`   | object[]   | 是   | 编辑操作列表，每个操作包含 oldText 和 newText |
| `dry_run` | boolean    | 否   | 是否为预览模式（不实际修改文件）（默认：false） |

**响应格式**

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

#### `fs_get_filesystem_info`

获取文件系统服务配置信息。

**请求参数**

无

**响应格式**

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

### MCP Unified Server 工具

统一服务器集成了命令服务器和文件系统服务器的所有工具。

#### `command_open_web_manager`

在浏览器中打开 Web 管理界面。

**请求参数**

无

**响应格式**

```json
[
    {
        "type": "text",
        "text": "已在浏览器中打开 Web 管理界面: http://127.0.0.1:[随机可用端口] 🚀"
    }
]
```

## Web管理界面

### MCP Command Server Web管理界面

当启动 `mcp-os-server command-server` 或 `mcp-os-server unified-server` 时，如果启用了 `--enable-web-manager` 选项，将提供一个Web管理界面。

*   **功能特点**：进程列表视图、进程详情视图、实时输出查看、进程控制（停止、终止、清理）、过滤功能。
*   **访问地址**：默认可在 `http://127.0.0.1:[随机可用端口]` 访问 (可通过 `--web-host` 和 `--web-port` 修改)。

### MCP Filesystem Server Web管理界面

目前 MCP Filesystem Server 没有独立的Web管理界面。当作为更大 MCP 系统的一部分集成时，其功能可以通过宿主 MCP 服务器的 Web 界面进行管理和监控。

## 开发

### 设置开发环境

1.  **克隆仓库**

    ```bash
    git clone https://github.com/Spark-Liang/mcp-os-server.git
    cd mcp-os-server
    ```

2.  **安装依赖项** (包括测试需求)

    ```bash
    uv --project . sync
    ```

### 运行测试

*   **运行所有测试**

    ```bash
    uv --project . run pytest
    ```

*   **运行 Command Server 相关测试**

    ```bash
    uv --project . run pytest tests/mcp_os_server/command/
    ```

*   **运行 Filesystem Server 相关测试**

    ```bash
    uv --project . run pytest tests/mcp_os_server/filesystem/
    ```

*   **运行集成测试**

    ```bash
    uv --project . run pytest tests/mcp_os_server/test_main_integration.py
    ```

*  **使用 mcp inspector 测试**

    ```bash
    npx -y @modelcontextprotocol/inspector -e ALLOWED_COMMANDS=echo,dir -e ALLOWED_DIRS=E: uv run mcp-os-server command-server --mode stdio --enable-web-manager
    ```

### 构建可执行文件

要将 `mcp-os-server` 打包成单个可执行文件，可以使用 `build_executable.py` 脚本。

```bash
uv run --extra dev python build_executable.py --help
```

这会显示所有可用的构建选项。

**常用构建命令:**

*   **构建 Release 版本 (单文件):**

    ```bash
    uv run --extra dev python build_executable.py
    ```

*   **快速构建 Debug 版本 (单文件，不优化):**

    ```bash
    uv run --extra dev python build_executable.py --quick --debug
    ```

*   **仅测试构建命令 (不实际构建):**

    ```bash
    uv run --extra dev python build_executable.py --test
    ```

*   **指定输出目录:**

    ```bash
    uv run --extra dev python build_executable.py --output-dir ./bin/
    ```

*   **使用代理构建 (例如，如果Nuitka下载组件需要):**

    ```bash
    uv run --extra dev python build_executable.py --proxy http://127.0.0.1:1080
    ```

## 系统要求

*   Python 3.11 或更高版本
*   `mcp>=1.1.0`
*   `Pillow` (用于图片处理，仅 Filesystem Server 需要)

## 许可证

本项目根据 MIT 许可证发布。详情请参阅 [LICENSE](LICENSE) 文件。
