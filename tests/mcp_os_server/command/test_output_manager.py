import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta
from mcp_os_server.command.output_logger import OutputManager, YamlOutputLogger
from mcp_os_server.command.models import OutputMessageEntry, MessageEntry
from mcp_os_server.command.exceptions import ProcessNotFoundError, OutputRetrievalError, OutputClearError, StorageError, CommandServerException

@pytest.fixture
def output_manager(tmp_path):
    """为每个测试提供一个OutputManager实例"""
    om = OutputManager(output_storage_path=tmp_path / "command_outputs")
    yield om
    # 清理操作，确保每个测试后状态干净
    # om.shutdown() # Shutdown is now handled by the test itself or next fixture

@pytest.mark.asyncio
async def test_initialize(output_manager):
    """测试OutputManager的初始化"""
    # 初始化已经在fixture中完成，这里可以进一步验证内部状态或无异常抛出
    await output_manager.initialize()
    assert True # 如果没有抛出异常，则认为初始化成功

@pytest.mark.asyncio
async def test_store_and_get_output(output_manager):
    """测试为指定进程存储stdout和stderr，并验证是否能正确获取"""
    process_id = "test_pid_1"
    output_key_stdout = "stdout"
    output_key_stderr = "stderr"
    message_stdout_1 = "This is stdout message 1."
    message_stdout_2 = "This is stdout message 2."
    message_stderr_1 = "This is stderr message 1."

    await output_manager.store_output(process_id, output_key_stdout, message_stdout_1)
    await output_manager.store_output(process_id, output_key_stdout, message_stdout_2)
    await output_manager.store_output(process_id, output_key_stderr, message_stderr_1)

    # 验证stdout
    stored_stdout = []
    async for entry in output_manager.get_output(process_id, output_key_stdout):
        stored_stdout.append(entry.text)
    assert len(stored_stdout) == 2
    assert message_stdout_1 in stored_stdout
    assert message_stdout_2 in stored_stdout

    # 验证stderr
    stored_stderr = []
    async for entry in output_manager.get_output(process_id, output_key_stderr):
        stored_stderr.append(entry.text)
    assert len(stored_stderr) == 1
    assert message_stderr_1 in stored_stderr

    # 验证输出消息列表
    await output_manager.store_output(process_id, output_key_stdout, ["list_msg_1", "list_msg_2"])
    stored_stdout_with_list = []
    async for entry in output_manager.get_output(process_id, output_key_stdout):
        stored_stdout_with_list.append(entry.text)
    assert "list_msg_1" in stored_stdout_with_list
    assert "list_msg_2" in stored_stdout_with_list


@pytest.mark.asyncio
async def test_get_output_with_filters(output_manager):
    """测试带有时间、行数和grep筛选的获取输出"""
    process_id = "test_pid_2"
    output_key = "stdout"

    # 模拟时间流逝
    now = datetime.now()
    await output_manager.store_output(process_id, output_key, "line 1", timestamp=now - timedelta(seconds=10))
    await output_manager.store_output(process_id, output_key, "line 2 keyword", timestamp=now - timedelta(seconds=5))
    await output_manager.store_output(process_id, output_key, "line 3", timestamp=now)
    await output_manager.store_output(process_id, output_key, "another keyword line 4", timestamp=now + timedelta(seconds=5))

    # Test 'since' filter
    output_since = []
    async for entry in output_manager.get_output(process_id, output_key, since=(now - timedelta(seconds=1)).timestamp()):
        output_since.append(entry.text)
    assert len(output_since) == 2
    assert "line 3" in output_since
    assert "another keyword line 4" in output_since

    # Test 'until' filter
    output_until = []
    async for entry in output_manager.get_output(process_id, output_key, until=(now + timedelta(seconds=1)).timestamp()):
        output_until.append(entry.text)
    assert len(output_until) == 3
    assert "line 1" in output_until
    assert "line 2 keyword" in output_until
    assert "line 3" in output_until

    # Test 'tail' filter
    output_tail = []
    async for entry in output_manager.get_output(process_id, output_key, tail=2):
        output_tail.append(entry.text)
    assert len(output_tail) == 2
    assert "line 3" in output_tail
    assert "another keyword line 4" in output_tail

    # Test 'grep_pattern' filter (line mode)
    output_grep_line = []
    async for entry in output_manager.get_output(process_id, output_key, grep_pattern="keyword"):
        output_grep_line.append(entry.text)
    assert len(output_grep_line) == 2
    assert "line 2 keyword" in output_grep_line
    assert "another keyword line 4" in output_grep_line

    # Test 'grep_pattern' filter (content mode)
    output_grep_content = []
    async for entry in output_manager.get_output(process_id, output_key, grep_pattern="keyword", grep_mode="content"):
        output_grep_content.append(entry.text)
    assert len(output_grep_content) == 2
    assert "keyword" in output_grep_content[0]
    assert "keyword" in output_grep_content[1]


@pytest.mark.asyncio
async def test_clear_output(output_manager):
    """测试清理特定进程的所有输出"""
    process_id = "test_pid_3"
    output_key = "stdout"
    await output_manager.store_output(process_id, output_key, "message to be cleared")

    # 确认存在输出
    initial_output = []
    async for entry in output_manager.get_output(process_id, output_key):
        initial_output.append(entry.text)
    assert len(initial_output) > 0

    await output_manager.clear_output(process_id)

    # 验证输出已被清理，并且尝试获取会抛出ProcessNotFoundError
    with pytest.raises(ProcessNotFoundError):
        async for _ in output_manager.get_output(process_id, output_key):
            pass

@pytest.mark.asyncio
async def test_shutdown(output_manager):
    """测试OutputManager的关闭"""
    await output_manager.shutdown()
    # 验证关闭后尝试操作是否抛出异常或正常（取决于实现）
    # 例如，尝试存储或获取输出可能会抛出错误
    with pytest.raises(Exception): # 假设shutdown后会禁用进一步操作
        await output_manager.store_output("some_pid", "stdout", "after shutdown")

@pytest.mark.asyncio
async def test_process_not_found_error(output_manager):
    """测试操作不存在的进程ID时是否抛出ProcessNotFoundError"""
    with pytest.raises(ProcessNotFoundError):
        async for _ in output_manager.get_output("non_existent_pid", "stdout"):
            pass

    with pytest.raises(ProcessNotFoundError):
        await output_manager.clear_output("non_existent_pid")

@pytest.mark.asyncio
async def test_invalid_parameters_store_output(output_manager):
    """测试store_output无效参数"""
    with pytest.raises(ValueError):
        await output_manager.store_output("", "stdout", "msg")
    with pytest.raises(ValueError):
        await output_manager.store_output("pid", "", "msg")

@pytest.mark.asyncio
async def test_storage_error_handling(mocker, output_manager):
    """测试store_output遇到存储错误时抛出StorageError"""
    process_id = "pid1_storage_error"
    output_key = "stdout"
    # Pre-create the logger instance in the manager's internal state
    # This bypasses the _get_logger_for_process call and directly sets up the mock
    output_manager._loggers[process_id] = {output_key: MagicMock(spec=YamlOutputLogger)}

    mocker.patch.object(output_manager._loggers[process_id][output_key], 'add_message',
                        side_effect=StorageError("Simulated storage error"))
    mocker.patch.object(output_manager._loggers[process_id][output_key], 'add_messages',
                        side_effect=StorageError("Simulated storage error"))

    with pytest.raises(StorageError):
        await output_manager.store_output(process_id, output_key, "msg")

@pytest.mark.asyncio
async def test_output_retrieval_error_handling(mocker, output_manager):
    """测试get_output遇到获取输出错误时抛出OutputRetrievalError"""
    process_id = "pid1_retrieval_error"
    output_key = "stdout"
    # Pre-create the logger instance in the manager's internal state
    output_manager._loggers[process_id] = {output_key: AsyncMock(spec=YamlOutputLogger)}

    mock_logger = output_manager._loggers[process_id][output_key]
    mock_logger.get_logs.side_effect = OutputRetrievalError("Simulated retrieval error")
    
    with pytest.raises(OutputRetrievalError):
        async for _ in output_manager.get_output(process_id, output_key):
            pass

@pytest.mark.asyncio
async def test_output_clear_error_handling(mocker, output_manager):
    """测试clear_output遇到清理错误时抛出OutputClearError"""
    process_id = "pid1_clear_error"
    output_key = "stdout"
    # Pre-create the logger instance in the manager's internal state
    output_manager._loggers[process_id] = {output_key: MagicMock(spec=YamlOutputLogger)}

    mock_logger = output_manager._loggers[process_id][output_key]
    mock_logger.close.side_effect = OutputClearError("Simulated clear error")
    
    with pytest.raises(OutputClearError):
        await output_manager.clear_output(process_id)

@pytest.mark.asyncio
async def test_shutdown_error_handling(mocker, output_manager):
    """测试shutdown遇到错误时抛出Exception"""
    # 模拟 _loggers 字典中存在 logger，并使其 close 方法抛出异常
    mock_logger_instance_stdout = MagicMock(spec=YamlOutputLogger)
    mock_logger_instance_stderr = MagicMock(spec=YamlOutputLogger)
    mock_logger_instance_stdout.close.side_effect = Exception("Simulated stdout shutdown error")
    mock_logger_instance_stderr.close.side_effect = Exception("Simulated stderr shutdown error")

    output_manager._loggers["dummy_pid"] = {
        "stdout": mock_logger_instance_stdout,
        "stderr": mock_logger_instance_stderr
    }

    # Modify the shutdown method to re-raise the exception for testing purposes
    # This part is no longer needed as we are directly mocking the inner logger
    # original_shutdown = output_manager.shutdown
    # async def mock_shutdown():
    #     try:
    #         await original_shutdown()
    #     except Exception as e:
    #         raise e
    # output_manager.shutdown = mock_shutdown

    with pytest.raises(Exception):
        # Call the actual shutdown on the instance, which will then call mocked close on loggers
        await output_manager.shutdown()

@pytest.mark.asyncio
async def test_store_output_list_message(output_manager):
    """测试存储列表消息"""
    process_id = "test_pid_list"
    output_key = "stdout"
    messages = ["line A", "line B", "line C"]
    
    await output_manager.store_output(process_id, output_key, messages)
    
    retrieved_messages = []
    async for entry in output_manager.get_output(process_id, output_key):
        retrieved_messages.append(entry.text)
    assert retrieved_messages == messages 