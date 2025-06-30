# MCP Command Server 功能设计文档

一个实现模型上下文协议（MCP）的精简且强大的命令执行服务器。相比 `mcp-shell-server`，`mcp-command-server` 更专注于单个命令的生命周期管理，提供类似"无镜像Docker"的进程控制能力，并致力于将核心进程管理能力模块化。

## 功能特点

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

## 安全性

服务器实现了几项安全措施：

1.  **命令白名单**：只有明确允许的命令才能执行，且仅支持 `command + args` 格式，不解析复杂的 shell 操作符。
2.  **无Shell注入**：命令直接执行，不通过shell解释。

## 启动服务器

### stdio模式（默认）

```bash
# 基本用法（stdio模式）
ALLOWED_COMMANDS="ls,cat,echo" uvx mcp-command-server

# 明确指定stdio模式
ALLOWED_COMMANDS="ls,cat,echo" uvx mcp-command-server stdio
```

### SSE模式

以服务器发送事件（SSE）模式启动服务器：

```bash
# 使用默认设置（主机：127.0.0.1，端口：8000）
ALLOWED_COMMANDS="ls,cat,echo" uvx mcp-command-server sse

# 使用自定义主机和端口
ALLOWED_COMMANDS="ls,cat,echo" uvx mcp-command-server sse --host 0.0.0.0 --port 9000

# 使用自定义Web路径
ALLOWED_COMMANDS="ls,cat,echo" uvx mcp-command-server sse --web-path /command-web
```

### Streamable HTTP模式

以可流式HTTP模式启动服务器：

```bash
# 使用默认设置（主机：127.0.0.1，端口：8000，路径：/mcp）
ALLOWED_COMMANDS="ls,cat,echo" uvx mcp-command-server http

# 使用自定义主机、端口和路径
ALLOWED_COMMANDS="ls,cat,echo" uvx mcp-command-server http --host 0.0.0.0 --port 9000 --path /command-api

# 使用自定义Web路径
ALLOWED_COMMANDS="ls,cat,echo" uvx mcp-command-server http --web-path /command-web
```

## 环境变量

`ALLOWED_COMMANDS` 环境变量指定了允许执行的命令。命令可以用逗号分隔，逗号周围可以有可选的空格。

ALLOWED_COMMANDS的有效格式：

```bash
ALLOWED_COMMANDS="ls,cat,echo"          # 基本格式
ALLOWED_COMMANDS="ls ,echo, cat"      # 带空格
ALLOWED_COMMANDS="ls,  cat  , echo"     # 多个空格
```

#### 可配置的环境变量

您可以使用以下环境变量自定义MCP Command Server的行为：

| 环境变量                      | 描述               | 默认值           | 示例                                          |
| ------------------------- | ---------------- | ------------- | ------------------------------------------- |
| ALLOWED_COMMANDS          | 允许执行的命令列表（逗号分隔）  | （空 - 不允许任何命令） | `ALLOWED_COMMANDS="ls,cat,echo,npm,python"` |
| PROCESS_RETENTION_SECONDS | 清理前保留已完成进程的时间（秒） | 3600（1小时）     | `PROCESS_RETENTION_SECONDS=86400`           |
| DEFAULT_ENCODING          | 进程输出的默认字符编码      | 系统终端编码或utf-8  | `DEFAULT_ENCODING=gbk`                      |

#### 服务器启动示例

**基本启动（最小权限）：**
```bash
ALLOWED_COMMANDS="ls,cat,pwd" uvx mcp-command-server
```

**开发环境（扩展权限）：**
```bash
ALLOWED_COMMANDS="ls,cat,pwd,grep,wc,touch,find,npm,python,git" \
PROCESS_RETENTION_SECONDS=86400 \
uvx mcp-command-server
```

**生产环境（自定义编码和更长的进程保留时间）：**
```bash
ALLOWED_COMMANDS="ls,cat,echo,find,grep" \
DEFAULT_ENCODING=utf-8 \
PROCESS_RETENTION_SECONDS=172800 \
uvx mcp-command-server
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
*   **功能特点**：进程列表视图、进程详情视图、实时输出查看、进程控制（停止、终止、清理）、过滤功能。
*   **访问地址**：默认可在 `http://127.0.0.1:[随机可用端口]` 访问 (可通过 `--web-host` 和 `--web-port` 修改)。

## Web管理界面

Web界面提供了一种方便的方式来监控和管理后台进程：

### 功能特点

*   **进程列表视图**：查看所有正在运行和已完成的进程
*   **进程详情视图**：检查特定进程的详细信息
*   **实时输出查看**：监控正在运行进程的stdout和stderr
*   **进程控制**：停止、终止和清理进程
*   **过滤功能**：按状态或标签过滤进程

### 截图

（可用时插入截图）

### 访问Web界面

默认情况下，使用`--enable-web-manager`标志启动时，Web界面可在 `http://127.0.0.1:[随机可用端口]` 访问。

## API参考

### 工具：command_execute

执行单个shell命令并返回结果。此工具仅支持 `command + args` 形式的简单命令，不解析复杂的 shell 操作符。

#### 请求格式

```json
{
    "command": "ls",
    "args": ["-l", "/tmp"],
    "directory": "/path/to/working/directory",
    "stdin": "可选的输入数据",
    "timeout": 30,
    "encoding": "utf-8"
}
```

#### 响应格式

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

#### 请求参数

| 字段      | 类型       | 必需  | 描述                                      |
|----------|------------|------|------------------------------------------|
| command  | string     | 是   | 要执行的命令                             |
| args     | string[]   | 否   | 命令的参数列表                           |
| directory| string     | 是   | 命令执行的工作目录                           |
| stdin    | string     | 否   | 通过stdin传递给命令的输入                    |
| timeout  | integer    | 否   | 最大执行时间（秒）（默认：15）                 |
| envs     | object     | 否   | 命令的附加环境变量                           |
| encoding | string     | 否   | 命令输出的字符编码（例如：'utf-8', 'gbk', 'cp936'） |
| limit_lines | integer | 否   | 每个TextContent返回的最大行数（默认：500）     |

#### 响应字段

| 字段   | 类型     | 描述                                                                   |
| ---- | ------ | -------------------------------------------------------------------- |
| type | string | 始终为"text"                                                            |
| text | string | 包含退出状态、stdout和stderr的命令输出信息。**在命令执行超时时，也需要输出已经获取到的 stdout 和 stderr** |

### 工具：command_bg_start

启动单个命令的后台进程，并对其进行精细管理。

#### 请求格式

```json
{
    "command": "npm",
    "args": ["start"],
    "directory": "/path/to/project",
    "description": "启动Node.js应用",
    "labels": ["nodejs", "app"],
    "stdin": "可选的输入数据",
    "envs": {
        "NODE_ENV": "development"
    },
    "encoding": "utf-8",
    "timeout": 3600
}
```

#### 响应格式

```json
{
    "type": "text",
    "text": "已启动后台进程，ID: 123"
}
```

#### 请求参数

| 字段        | 类型       | 必需  | 描述                                 |
|------------|------------|------|-------------------------------------|
| command    | string     | 是   | 要执行的命令                         |
| args       | string[]   | 否   | 命令的参数列表                       |
| directory  | string     | 是   | 命令执行的工作目录                     |
| description| string     | 是   | 命令的描述                            |
| labels     | string[]   | 否   | 用于分类命令的标签                     |
| stdin      | string     | 否   | 通过stdin传递给命令的输入              |
| envs       | object     | 否   | 命令的附加环境变量                     |
| encoding   | string     | 否   | 命令输出的字符编码                     |
| timeout    | integer    | 否   | 最大执行时间（秒）                     |

#### 响应字段

| 字段      | 类型     | 描述                           |
|----------|---------|--------------------------------|
| type     | string  | 始终为"text"                    |
| text     | string  | 带有进程ID的确认消息             |

### 工具：command_ps_list

列出正在运行或已完成的后台进程。

#### 请求格式

```json
{
    "labels": ["nodejs"],
    "status": "running"
}
```

#### 响应格式

```json
{
    "type": "text",
    "text": "ID | 状态 | 开始时间 | 命令 | 描述 | 标签\n---------\n123 | running | 2023-05-06 14:30:00 | npm start | 启动Node.js应用 | nodejs"
}
```

#### 请求参数

| 字段    | 类型      | 必需  | 描述                                     |
|--------|-----------|------|------------------------------------------|
| labels | string[]  | 否   | 按标签过滤进程                             |
| status | string    | 否   | 按状态过滤('running', 'completed', 'failed', 'terminated', 'error') |

#### 响应字段

| 字段   | 类型    | 描述                  |
|-------|---------|----------------------|
| type  | string  | 始终为"text"          |
| text  | string  | 格式化的进程表格        |

### 工具：command_ps_stop

停止正在运行的进程。

#### 请求格式

```json
{
    "pid": 123,
    "force": false
}
```

#### 响应格式

```json
{
    "type": "text",
    "text": "进程123已被优雅地停止\n命令: npm start\n描述: 启动Node.js应用"
}
```

#### 请求参数

| 字段       | 类型     | 必需  | 描述                               |
|-----------|----------|------|-----------------------------------|
| pid       | string  | 是   | 要停止的进程ID                      |
| force     | boolean  | 否   | 是否强制停止进程（默认：false）       |

#### 响应字段

| 字段   | 类型    | 描述                      |
|-------|---------|--------------------------|
| type  | string  | 始终为"text"                    |
| text  | string  | 带有进程详情的确认消息      |

### 工具：command_ps_logs

获取进程的输出，并支持通过正则表达式进行筛选。

#### 请求格式

```json
{
    "pid": 123,
    "tail": 100,
    "since": "2023-05-06T14:30:00",
    "until": "2023-05-06T15:30:00",
    "with_stdout": true,
    "with_stderr": true,
    "add_time_prefix": true,
    "time_prefix_format": "%Y-%m-%d %H:%M:%S.%f",
    "follow_seconds": 5,
    "limit_lines": 500,
    "grep": "^服务器.*启动",
    "grep_mode": "line"
}
```

#### 响应格式

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

#### 请求参数

| 字段               | 类型      | 必需  | 描述                                       |
|-------------------|-----------|------|-------------------------------------------|
| pid               | string   | 是   | 获取输出的进程ID                            |
| tail              | integer   | 否   | 从末尾显示的行数                             |
| since             | string    | 否   | 显示从该时间戳开始的日志（ISO格式，例如：'2023-05-06T14:30:00'） |
| until             | string    | 否   | 显示到该时间戳为止的日志（ISO格式，例如：'2023-05-06T15:30:00'） |
| with_stdout       | boolean   | 否   | 显示标准输出（默认：true）                    |
| with_stderr       | boolean   | 否   | 显示错误输出（默认：false）                   |
| add_time_prefix   | boolean   | 否   | 为每行输出添加时间戳前缀（默认：true）          |
| time_prefix_format| string    | 否   | 时间戳前缀的格式（默认："%Y-%m-%d %H:%M:%S.%f"） |
| follow_seconds    | integer   | 否   | 等待指定秒数以获取新日志（默认：1）             |
| limit_lines       | integer   | 否   | 每个TextContent返回的最大行数（默认：500）     |
| grep              | string    | 否   | 用于筛选输出的**Perl标准正则表达式**           |
| grep_mode         | string    | 否   | 返回匹配内容的模式：`line`（匹配行）或`content`（匹配内容本身）。类比`grep`和`grep -o`，默认为`line`。 |

#### 响应字段

| 字段   | 类型    | 描述                              |
|-------|---------|----------------------------------|
| type  | string  | 始终为"text"                      |
| text  | string  | 进程信息和带有筛选及可选时间戳的输出       |

### 工具：command_ps_clean

清理已完成或失败的进程。

#### 请求格式

```json
{
    "pids": [123, 456]
}
```

#### 响应格式

```json
{
    "type": "text",
    "text": "**成功清理了1个进程:**\n- PID: 123 | 命令: npm start\n\n**无法清理1个运行中的进程:**\n注意: 无法清理正在运行的进程。请先使用`command_bg_stop()`停止它们。\n- PID: 456 | 命令: node server.js\n\n**清理1个进程失败:**\n- PID: 789 | 原因: 找不到进程"
}
```

#### 请求参数

| 字段        | 类型       | 必需  | 描述                         |
|------------|------------|------|----------------------------|
| pids       | string[]  | 是   | 要清理的进程ID列表            |

#### 响应字段

| 字段   | 类型    | 描述                    |
|-------|---------|------------------------|
| type  | string  | 始终为"text"            |
| text  | string  | 格式化的清理结果表格      |

### 工具：command_ps_detail

获取特定进程的详细信息。

#### 请求格式

```json
{
    "pid": 123
}
```

#### 响应格式

```json
{
    "type": "text",
    "text": "### 进程详情: 123\n\n#### 基本信息\n- **状态**: completed\n- **命令**: `npm start`\n- **描述**: 启动Node.js应用\n- **标签**: nodejs, app\n\n#### 时间信息\n- **开始时间**: 2023-05-06 14:30:00\n- **结束时间**: 2023-05-06 14:35:27\n- **持续时间**: 0:05:27\n\n#### 执行信息\n- **工作目录**: /path/to/project\n- **退出码**: 0\n\n#### 输出信息\n- 使用`command_bg_logs`工具查看进程输出\n- 示例: `command_bg_logs(pid=123)`"
}
```

#### 请求参数

| 字段       | 类型     | 必需  | 描述                   |
|-----------|----------|------|------------------------|
| pid       | string  | 是   | 获取详情的进程ID        |

#### 响应字段

| 字段   | 类型    | 描述                      |
|-------|---------|--------------------------|
| type  | string  | 始终为"text"              |
| text  | string  | 关于进程的格式化详细信息    |

## 开发

### 设置开发环境

1.  克隆仓库

```bash
git clone https://github.com/yourusername/mcp-command-server.git
cd mcp-command-server
```

2.  安装依赖项（包括测试需求）

```bash
pip install -e ".[test]"
```

### 运行测试

```bash
pytest
```

## 系统要求

*   Python 3.11或更高版本
*   mcp>=1.1.0

## 许可证

MIT许可证 - 详情请参阅LICENSE文件
