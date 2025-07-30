#!/usr/bin/env python3
"""
构建脚本，使用 Nuitka 将 mcp-shell-server 打包成单个可执行文件。
"""
import argparse
import os
import platform

from nuitka_build_tools import Handler, NuitkaBuildContext, ModeHandler, EnvironmentHandler


# Handler 实现
class EntrypointSetupHandler(Handler):
    def setup_argparse(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("--entry-module", default=os.path.join("src", "mcp_os_server", "main.py"), help="入口Python模块")
        parser.add_argument("--output-dir", default="dist", help="输出目录")
        parser.add_argument("--program-name", default="mcp-os-server", help="程序名称")

    def build_nuitka_args_and_envs(
        self, ctx: NuitkaBuildContext, args: argparse.Namespace
    ) -> None:
        ctx.entry_module = args.entry_module
        exe_extension = ".exe" if platform.system() == "Windows" else ""
        output_filename = f"{args.program_name}{exe_extension}"
        ctx.args.extend([f"--output-dir={args.output_dir}", f"--output-filename={output_filename}"])
        ctx.args.extend([
            "--onefile",
            "--standalone",
            "--no-pyi-file",
            "--assume-yes-for-downloads",
            "--include-package=mcp_os_server",
            "--include-data-dir=src/mcp_os_server/command/web_manager_templates=mcp_os_server/command/web_manager_templates",
            "--include-package=mcp",
            "--include-package=asyncio",
            "--include-package=click",
            "--include-package=loguru",
            "--include-package=pydantic",
        ])
        ctx.args.extend([
            '--lto=yes',
            '--enable-plugin=upx',
            '--low-memory',
            '--remove-output',
        ])


if __name__ == "__main__":
    handlers = [
        EntrypointSetupHandler(),
        EnvironmentHandler(),
        ModeHandler(),
    ]
    Handler.run_build(handlers)
