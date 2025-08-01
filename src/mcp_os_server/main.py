#!/usr/bin/env python3
"""
MCP OS Server - Main Entry Point

This module provides the main entry point for the MCP OS Server,
which includes a command server implementation, filesystem server implementation,
and web management interface.
"""

# 入口脚本不能使用相对导入，否则在打包时会报错
from mcp_os_server.server import main

if __name__ == "__main__":
    main()
