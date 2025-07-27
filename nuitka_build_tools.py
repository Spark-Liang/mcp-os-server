"""
构建工具基础代码定义和工具Handler定义。具体可执行文件构建脚本可引用此文件。
"""

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from abc import ABC
from typing import Any, Dict, List, Sequence, Type
import platform



# region Handler 接口定义（不要修改）

@dataclass
class NuitkaBuildContext:
    """Nuitka构建上下文，持有参数、环境和Handler状态。"""
    args: List[str]
    envs: Dict[str, str]
    handler_state: Dict[Type['Handler'], Any]
    entry_module: str = ""

class Handler(ABC):
    """
    Handler协议定义，用于模块化处理Nuitka构建选项。

    每个Handler负责特定功能组，如环境设置或模式控制。
    
    Methods:
        setup_argparse: 设置ArgumentParser，添加本Handler的参数。
        build_nuitka_args_and_envs: 基于解析的参数构建Nuitka命令参数和环境变量。
        post_nuitka_build: Nuitka构建后的后处理，如验证或清理。
        run_build: 运行Nuitka构建过程，使用提供的Handlers。
    """

    def setup_argparse(self, parser: argparse.ArgumentParser) -> None:
        """
        设置ArgumentParser，添加本Handler的参数。

        Args:
            parser (argparse.ArgumentParser): 要添加参数的ArgumentParser实例。
        """
        pass

    def build_nuitka_args_and_envs(
        self, ctx: NuitkaBuildContext, args: argparse.Namespace
    ) -> None:
        """
        基于解析的参数构建Nuitka命令参数和环境变量。

        Args:
            ctx (NuitkaBuildContext): 构建上下文。
            args (argparse.Namespace): 解析后的命令行参数。
        """
        pass

    def post_nuitka_build(self, ctx: NuitkaBuildContext) -> None:
        """
        Nuitka构建后的后处理，如验证或清理。

        Args:
            ctx (NuitkaBuildContext): 构建上下文。
        """
        pass

    @staticmethod
    def run_build(handlers: Sequence['Handler']) -> None:
        """
        运行Nuitka构建过程，使用提供的Handlers。

        此方法是不可变的，不应被覆盖。

        Args:
            handlers (Sequence[Handler]): 要使用的 Handler 序列。
        """
        parser = argparse.ArgumentParser()
        for handler in handlers:
            handler.setup_argparse(parser)
        args, unknown = parser.parse_known_args()

        ctx = NuitkaBuildContext(
            args=[],
            envs={},
            handler_state={},
            entry_module=""
        )

        for handler in handlers:
            handler.build_nuitka_args_and_envs(ctx, args)

        ctx.args.extend(unknown)  # 添加未知选项

        # 构建基本命令
        ctx.args = [sys.executable, "-m", "nuitka"] + ctx.args + [ctx.entry_module]

        # 设置环境
        os.environ.update(ctx.envs)

        # 执行
        print("Executing:", " ".join(ctx.args))
        subprocess.call(ctx.args)  # 实际执行

        for handler in handlers:
            handler.post_nuitka_build(ctx)

# endregion

# region 工具 Handler 实现定义，通常放置通用的Handler实现，如环境设置、模式控制、验证等

class EnvironmentHandler(Handler):
    """
    EnvironmentHandler 处理环境相关的设置，如代理和并行任务数。

    继承自 Handler，用于模块化处理环境配置。
    """
    def setup_argparse(self, parser: argparse.ArgumentParser) -> None:
        """
        设置环境相关的命令行参数。

        Args:
            parser (argparse.ArgumentParser): ArgumentParser 实例。
        """
        parser.add_argument("--proxy", help="HTTP 代理地址")
        parser.add_argument("--jobs", "-j", type=int, default=1, help="并行编译的任务数量")

    def build_nuitka_args_and_envs(
        self, ctx: NuitkaBuildContext, args: argparse.Namespace
    ) -> None:
        """
        构建环境变量和 Nuitka 参数。

        Args:
            ctx (NuitkaBuildContext): 构建上下文。
            args (argparse.Namespace): 解析后的参数。
        """
        if args.proxy:
            ctx.envs["HTTP_PROXY"] = args.proxy
            ctx.envs["HTTPS_PROXY"] = args.proxy
        ctx.args.append(f"--jobs={args.jobs}")



class ModeHandler(Handler):
    """
    ModeHandler 管理构建模式、验证和调试选项。

    继承自 Handler，用于控制不同的构建模式。
    """
    def __init__(self, normal_options=None):
        """
        初始化 ModeHandler。

        Args:
            normal_options (list, optional): normal 模式下的 Nuitka 选项列表。
        """
        if normal_options is None:
            normal_options = [
                "--follow-imports",
                "--warn-unusual-code",
                "--plugin-enable=anti-bloat",
                "--plugin-enable=multiprocessing",
            ]
        self.normal_options = normal_options

    def setup_argparse(self, parser: argparse.ArgumentParser) -> None:
        """
        设置模式相关的命令行参数。

        Args:
            parser (argparse.ArgumentParser): ArgumentParser 实例。
        """
        parser.add_argument("--build-mode", choices=["normal", "quick", "test"], default="normal", help="构建模式")
        parser.add_argument("--verify", action="store_true", help="验证可执行文件")
        parser.add_argument("--debug", action="store_true", help="启用调试")

    def build_nuitka_args_and_envs(
        self, ctx: NuitkaBuildContext, args: argparse.Namespace
    ) -> None:
        """
        基于模式构建 Nuitka 参数。

        Args:
            ctx (NuitkaBuildContext): 构建上下文。
            args (argparse.Namespace): 解析后的参数。
        """
        if args.verify:
            ctx.handler_state[ModeHandler] = {"verify": True}
            return
        if args.build_mode == "quick":
            pass
        elif args.build_mode == "normal":
            ctx.args.extend(self.normal_options)
        if args.debug:
            ctx.args.append("--debug")
        if args.build_mode == "test":
            ctx.handler_state[ModeHandler] = {"test": True}

    def post_nuitka_build(self, ctx: NuitkaBuildContext) -> None:
        """
        执行构建后的处理，如验证。

        Args:
            ctx (NuitkaBuildContext): 构建上下文。
        """
        state = ctx.handler_state.get(ModeHandler, {})
        output_dir = next((arg.split('=')[1] for arg in ctx.args if arg.startswith('--output-dir=')), "dist")
        output_filename = next((arg.split('=')[1] for arg in ctx.args if arg.startswith('--output-filename=')), "mcp-os-server")
        output_path = os.path.join(output_dir, output_filename)
        if state.get("verify") or not state.get("test"):
            if self.verify_executable(output_path):
                print("验证通过!")
            else:
                print("验证失败!")
                sys.exit(1)

    @staticmethod
    def verify_executable(exe_path):
        """
        验证可执行文件是否存在且可执行。

        Args:
            exe_path (str): 可执行文件路径。

        Returns:
            bool: 验证是否通过。
        """
        if not os.path.exists(exe_path):
            print(f"可执行文件不存在: {exe_path}")
            return False

        print(f"验证可执行文件: {exe_path}")
        if not os.path.isfile(exe_path):
            print(f"路径不是一个文件: {exe_path}")
            return False

        # 检查文件大小
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"文件大小: {size_mb:.2f} MB")

        # 检查文件权限
        if not os.access(exe_path, os.X_OK) and platform.system() != "Windows":
            print(f"文件没有执行权限: {exe_path}")
            return False

        # 在Windows上，我们无法直接用--help参数测试，因为可能会启动实际服务
        # 所以只检查文件存在并且大小合理
        if size_mb < 1:
            print(f"文件太小，可能构建失败: {size_mb:.2f} MB")
            return False

        print(f"验证成功: {exe_path}")
        return True
    
# endregion