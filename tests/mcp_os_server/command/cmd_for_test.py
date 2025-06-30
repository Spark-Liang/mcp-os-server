import argparse
import os
import re
import sys
import time


def echo():
    # 简单实现echo命令，输出所有参数
    parser = argparse.ArgumentParser(description="Echo command")
    parser.add_argument("text", nargs="*", help="Text to echo")
    args = parser.parse_args(sys.argv[1:])
    print(" ".join(args.text))


def encode_echo():
    # 实现encode_echo命令，用于验证不同编码的字符串
    parser = argparse.ArgumentParser(description="Encode echo command")
    parser.add_argument("text", nargs="*", help="Text to echo with encoding")
    args = parser.parse_args(sys.argv[1:])

    # 仅使用基本ASCII字符和一些安全的中文字符
    safe_chars = "Basic ASCII and Safe Chinese: 你好，世界！"

    if args.text:
        print(" ".join(args.text))

    # 避免在不支持的编码下使用特殊字符
    try:
        print(safe_chars)
    except UnicodeEncodeError:
        # 如果无法编码，只输出ASCII部分
        print("Basic ASCII only (encoding error occurred)")


def grep():
    # 实现简化版grep命令，支持基本模式匹配
    parser = argparse.ArgumentParser(description="Grep command")
    parser.add_argument("pattern", help="Pattern to search for")
    parser.add_argument("file", nargs="?", help="File to search in")
    args = parser.parse_args(sys.argv[1:])

    pattern = args.pattern
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            content = f.readlines()
    else:
        content = sys.stdin.readlines()

    for line in content:
        if re.search(pattern, line):
            print(line.rstrip())


def sleep():
    # 实现sleep命令，暂停指定秒数
    parser = argparse.ArgumentParser(description="Sleep command")
    parser.add_argument("seconds", type=float, help="Seconds to sleep")
    args = parser.parse_args(sys.argv[1:])
    time.sleep(args.seconds)


def cat():
    # 实现cat命令，输出文件内容
    parser = argparse.ArgumentParser(description="Cat command")
    parser.add_argument("file", help="File to display")
    args = parser.parse_args(sys.argv[1:])

    with open(args.file, "r", encoding="utf-8") as f:
        print(f.read(), end="")


def binary_cat():
    # 实现二进制cat命令，以二进制模式读取并输出文件内容
    parser = argparse.ArgumentParser(description="Binary cat command")
    parser.add_argument("file", help="File to display in binary mode")
    args = parser.parse_args(sys.argv[1:])

    # 以二进制模式读取文件内容
    with open(args.file, "rb") as f:
        content = f.read()

    # 将二进制内容直接写入标准输出的缓冲区
    # 这可以确保二进制数据不会因编码转换而丢失或损坏
    if hasattr(sys.stdout, "buffer"):
        sys.stdout.buffer.write(content)
    else:
        # 如果stdout没有buffer属性（某些环境下），尝试其他方法
        try:
            os.write(1, content)  # 1 是stdout的文件描述符
        except:
            # 最后尝试直接打印，可能会有编码问题
            print(content.decode("utf-8", errors="replace"), end="")


def exit_cmd():
    # 实现exit命令，以指定的退出码退出
    parser = argparse.ArgumentParser(description="Exit command")
    parser.add_argument("code", type=int, help="Exit code")
    args = parser.parse_args(sys.argv[1:])
    sys.exit(args.code)


def loop():
    # 实现一个死循环命令，用于测试超时
    parser = argparse.ArgumentParser(description="Loop command to simulate high CPU load")
    parser.add_argument("seconds", type=float, help="Seconds to loop for")
    args = parser.parse_args(sys.argv[1:])

    end_time = time.time() + args.seconds
    while time.time() < end_time:
        # 密集计算，模拟高负载
        _ = 1 * 1
        
    print("Loop finished naturally.")


# 命令路由
if __name__ == "__main__":
    # Simplified command detection from script name
    cmd_name = os.path.basename(sys.argv[0]).replace(".py", "")

    # Command detection from arguments
    if len(sys.argv) > 1 and sys.argv[1] in [
        "echo", "grep", "sleep", "cat", "binary_cat", "exit", "encode_echo", "loop"
    ]:
        command = sys.argv[1]
        # Re-arrange sys.argv for argparse in functions
        sys.argv = [f"cmd_for_test.py {command}"] + sys.argv[2:]
    else:
        command = cmd_name

    if command == "echo":
        echo()
    elif command == "grep":
        grep()
    elif command == "sleep":
        sleep()
    elif command == "cat":
        cat()
    elif command == "binary_cat":
        binary_cat()
    elif command == "exit":
        exit_cmd()
    elif command == "encode_echo":
        encode_echo()
    elif command == "loop":
        loop()
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print(
            "Available commands: echo, encode_echo, grep, sleep, cat, binary_cat, exit, loop",
            file=sys.stderr,
        )
        sys.exit(1)
