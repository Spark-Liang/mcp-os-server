
import pytest
import logging


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"




# 获取根日志器
# 如果你的应用程序有特定的日志器名称，请替换 'your_app_logger'
# 否则，可以使用 None 或 '' 来获取根日志器
# 例如：logger = logging.getLogger('my_app')
# 或者：logger = logging.getLogger() # 获取根日志器
logger = logging.getLogger()

def pytest_configure(config):
    """
    pytest_configure 是 pytest 提供的一个钩子函数，
    在 pytest 配置完成后，但在收集测试之前被调用。
    """
    # 获取 'verbose' 选项的值。
    # -v 对应 verbose=1
    # -vv 对应 verbose=2，以此类推
    verbose_level = config.getoption("verbose")

    if verbose_level > 0:
        # 如果 -v 或 -vv 等被使用，则将日志级别设置为 DEBUG
        print("\nPytest running in verbose mode, setting log level to DEBUG.")
        logger.setLevel(logging.DEBUG)

        # 可选：配置日志格式和处理器
        # 这部分取决于你希望日志输出到哪里（控制台、文件等）以及什么格式
        if not logger.handlers: # 避免重复添加处理器
            handler = logging.StreamHandler() # 输出到控制台
            formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
            handler.setFormatter(formatter)
            logger.addHandler(handler)
    

    