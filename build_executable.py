#!/usr/bin/env python3
"""
构建脚本，使用 Nuitka 将 mcp-shell-server 打包成单个可执行文件。
"""

import argparse
import os
import subprocess
import sys
import platform
import time
import shutil
import multiprocessing  # 导入多处理模块获取CPU核心数

def verify_executable(exe_path):
    """验证可执行文件是否存在且可执行"""
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

def main():
    start_time = time.time()
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="使用 Nuitka 构建 mcp-shell-server 可执行文件")
    parser.add_argument("--proxy", help="HTTP 代理地址 (例如 http://127.0.0.1:1080)")
    parser.add_argument("--output-dir", default="dist/", help="输出目录")
    parser.add_argument("--debug", action="store_true", help="启用调试模式")
    parser.add_argument("--quick", action="store_true", help="快速构建模式，减少优化")
    parser.add_argument("--test", action="store_true", help="测试模式，仅输出命令不执行")
    parser.add_argument("--verify", action="store_true", help="仅验证可执行文件")
    parser.add_argument("--jobs", "-j", type=int, default=1, 
                      help=f"并行编译的任务数量 (默认: 1")
    args = parser.parse_args()
    
    # 确定输出文件名
    exe_extension = ".exe" if platform.system() == "Windows" else ""
    output_filename = f"mcp-os-server{exe_extension}"
    output_dir = os.path.abspath(args.output_dir)
    output_path = os.path.join(output_dir, output_filename)
    
    # 只验证模式
    if args.verify:
        print(f"[{time.time() - start_time:.2f}s] 仅验证可执行文件")
        if verify_executable(output_path):
            print(f"[{time.time() - start_time:.2f}s] 验证通过!")
            return
        else:
            print(f"[{time.time() - start_time:.2f}s] 验证失败!")
            sys.exit(1)
    
    print(f"[{time.time() - start_time:.2f}s] 开始构建流程...")
    
    # 设置代理环境变量（如果提供）
    if args.proxy:
        os.environ["HTTP_PROXY"] = args.proxy
        os.environ["HTTPS_PROXY"] = args.proxy
        print(f"[{time.time() - start_time:.2f}s] 代理已设置为: {args.proxy}")
    
    # 确保输出目录存在
    if not args.test:
        os.makedirs(output_dir, exist_ok=True)
    print(f"[{time.time() - start_time:.2f}s] 输出目录: {output_dir}")
    
    # 确定入口模块路径
    entry_module = os.path.join("src", "mcp_os_server", "main.py")
    if not os.path.exists(entry_module) and not args.test:
        print(f"[{time.time() - start_time:.2f}s] 错误: 入口模块不存在: {entry_module}")
        sys.exit(1)
    print(f"[{time.time() - start_time:.2f}s] 入口模块: {entry_module}")
    
    print(f"[{time.time() - start_time:.2f}s] 输出文件: {output_path}")
    print(f"[{time.time() - start_time:.2f}s] 并行任务数: {args.jobs}")
    
    # 基础 Nuitka 命令及参数
    nuitka_cmd = [
        sys.executable, "-m", "nuitka",
        "--onefile",  # 创建单个可执行文件
        "--standalone",  # 独立模式，不依赖 Python
        "--no-pyi-file",  # 不生成 .pyi 文件
        "--assume-yes-for-downloads",  # 自动下载所需组件
        "--include-package=mcp_os_server",  # 包含主包
        f"--jobs={args.jobs}",  # 设置并行编译的任务数量
        # 包含模板文件
        "--include-data-dir=src/mcp_os_server/command/web_manager_templates=mcp_os_server/command/web_manager_templates",
    ]
    
    # 快速构建模式减少优化级别
    if args.quick:
        print(f"[{time.time() - start_time:.2f}s] 启用快速构建模式")
    else:
        print(f"[{time.time() - start_time:.2f}s] 启用完整构建模式")
        nuitka_cmd.extend([
            "--follow-imports",  # 跟踪所有导入
            "--include-package=mcp",  # 包含依赖包
            "--include-package=asyncio",
            "--include-package=click",
            "--include-package=flask",
            "--include-package=loguru",
            "--include-package=pydantic",
            "--include-package=waitress",
            "--warn-unusual-code",  # 对不寻常的代码发出警告
            "--plugin-enable=anti-bloat",  # 减少生成文件大小
            "--plugin-enable=multiprocessing",  # 支持多进程
        ])
    
    # 添加输出目录和文件名及移除中间输出选项
    nuitka_cmd.extend([
        f"--output-dir={output_dir}",  # 输出目录
        f"--output-filename={output_filename}",  # 输出文件名
        "--remove-output",  # 删除中间输出
        entry_module  # 入口模块
    ])
    
    # 调试模式选项
    if args.debug:
        nuitka_cmd.append("--debug")
        print(f"[{time.time() - start_time:.2f}s] 已启用调试模式")
    
    # 测试模式下仅输出命令
    if args.test:
        print(f"[{time.time() - start_time:.2f}s] 测试模式 - 将执行的命令:")
        cmd_str = ' '.join(nuitka_cmd)
        print(f"\n{cmd_str}\n")
        print(f"[{time.time() - start_time:.2f}s] 测试模式完成，未实际执行构建")
        return
    
    # 执行 Nuitka 命令
    cmd_str = ' '.join(nuitka_cmd)
    print(f"[{time.time() - start_time:.2f}s] 开始构建: {cmd_str}")
    try:
        process = subprocess.Popen(
            nuitka_cmd, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            bufsize=1
        )
        
        # 实时打印输出
        if process.stdout:
            for line in process.stdout:
                elapsed = time.time() - start_time
                print(f"[{elapsed:.2f}s] {line.strip()}")
        
        process.wait()
        
        if process.returncode == 0:
            print(f"[{time.time() - start_time:.2f}s] 构建成功! 可执行文件位于: {output_path}")
            # 验证生成的可执行文件
            if verify_executable(output_path):
                print(f"[{time.time() - start_time:.2f}s] 验证通过!")
            else:
                print(f"[{time.time() - start_time:.2f}s] 验证失败!")
                sys.exit(1)
        else:
            print(f"[{time.time() - start_time:.2f}s] 构建失败, 返回代码: {process.returncode}")
            sys.exit(1)
    except Exception as e:
        print(f"[{time.time() - start_time:.2f}s] 构建失败: {e}")
        sys.exit(1)
    
    total_time = time.time() - start_time
    print(f"[{total_time:.2f}s] 构建完成，总耗时: {total_time:.2f}秒")

if __name__ == "__main__":
    main()
