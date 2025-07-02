import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta
import threading
import concurrent.futures
import asyncio
import time
from mcp_os_server.command.output_logger import YamlOutputLogger, SqliteOutputLogger
from mcp_os_server.command.output_manager import OutputManager
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
    # Modified: Pre-create the logger instance in the manager's internal state
    # The manager now stores SqliteOutputLogger directly by process_id
    mock_logger = MagicMock(spec=SqliteOutputLogger) # Mock SqliteOutputLogger
    output_manager._loggers[process_id] = {output_key: mock_logger} # Assign as nested dict

    mocker.patch.object(mock_logger, 'add_message',
                        side_effect=StorageError("Simulated storage error"))
    mocker.patch.object(mock_logger, 'add_messages',
                        side_effect=StorageError("Simulated storage error"))

    with pytest.raises(StorageError):
        await output_manager.store_output(process_id, output_key, "msg")

@pytest.mark.asyncio
async def test_output_retrieval_error_handling(mocker, output_manager):
    """测试get_output遇到获取输出错误时抛出OutputRetrievalError"""
    process_id = "pid1_retrieval_error"
    output_key = "stdout"
    # Modified: Pre-create the logger instance in the manager's internal state
    mock_logger = AsyncMock(spec=SqliteOutputLogger) # Mock SqliteOutputLogger
    output_manager._loggers[process_id] = {output_key: mock_logger} # Assign as nested dict

    mock_logger.get_logs.side_effect = OutputRetrievalError("Simulated retrieval error")
    
    with pytest.raises(OutputRetrievalError):
        async for _ in output_manager.get_output(process_id, output_key):
            pass

@pytest.mark.asyncio
async def test_output_clear_error_handling(mocker, output_manager):
    """测试clear_output遇到清理错误时抛出OutputClearError"""
    process_id = "pid1_clear_error"
    output_key = "stdout"
    # Modified: Pre-create the logger instance in the manager's internal state
    mock_logger = MagicMock(spec=SqliteOutputLogger) # Mock SqliteOutputLogger
    output_manager._loggers[process_id] = {output_key: mock_logger} # Assign as nested dict

    mock_logger.close.side_effect = OutputClearError("Simulated clear error")
    
    with pytest.raises(OutputClearError):
        await output_manager.clear_output(process_id)

@pytest.mark.asyncio
async def test_shutdown_error_handling(mocker, output_manager):
    """测试shutdown遇到错误时抛出Exception"""
    # 模拟 _loggers 字典中存在 logger，并使其 close 方法抛出异常
    mock_logger_instance_stdout = MagicMock(spec=SqliteOutputLogger) # Use SqliteOutputLogger
    mock_logger_instance_stderr = MagicMock(spec=SqliteOutputLogger) # Need another mock for stderr
    mock_logger_instance_stdout.close.side_effect = Exception("Simulated stdout shutdown error")
    mock_logger_instance_stderr.close.side_effect = Exception("Simulated stderr shutdown error")

    output_manager._loggers["dummy_pid"] = {
        "stdout": mock_logger_instance_stdout,
        "stderr": mock_logger_instance_stderr
    } # Assign as nested dict

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


@pytest.mark.asyncio
async def test_timeout_process_output_retention(output_manager):
    """测试超时进程的输出保留功能 - 验证在 process retention time 内，进程即使超时了，也能查到信息获取到日志"""
    process_id = "timeout_test_pid"
    output_key_stdout = "stdout"
    output_key_stderr = "stderr"
    
    # 模拟超时进程产生的输出
    timeout_stdout_messages = [
        "Process starting...",
        "Processing data...", 
        "Timeout occurred, but output should be retained"
    ]
    timeout_stderr_messages = [
        "Warning: Process is taking longer than expected",
        "ERROR: Process timed out after specified timeout period"
    ]
    
    # 存储超时进程的输出
    await output_manager.store_output(process_id, output_key_stdout, timeout_stdout_messages)
    await output_manager.store_output(process_id, output_key_stderr, timeout_stderr_messages)
    
    # 验证即使进程超时，仍然可以检索到完整的输出日志
    retrieved_stdout = []
    async for entry in output_manager.get_output(process_id, output_key_stdout):
        retrieved_stdout.append(entry.text)
    
    retrieved_stderr = []
    async for entry in output_manager.get_output(process_id, output_key_stderr):
        retrieved_stderr.append(entry.text)
    
    # 断言所有输出都被正确保留
    assert len(retrieved_stdout) == 3
    assert retrieved_stdout == timeout_stdout_messages
    
    assert len(retrieved_stderr) == 2  
    assert retrieved_stderr == timeout_stderr_messages
    
    # 测试时间过滤功能 - 超时后仍能根据时间过滤日志
    import time
    from datetime import datetime, timedelta
    
    # 添加一条超时后的消息
    post_timeout_message = "Process cleanup after timeout"
    await output_manager.store_output(process_id, output_key_stdout, post_timeout_message)
    
    # 验证可以获取超时后的消息
    all_stdout = []
    async for entry in output_manager.get_output(process_id, output_key_stdout):
        all_stdout.append(entry.text)
    
    assert len(all_stdout) == 4
    assert post_timeout_message in all_stdout
    
    # 测试 tail 功能 - 获取最后几条日志
    last_two_messages = []
    async for entry in output_manager.get_output(process_id, output_key_stdout, tail=2):
        last_two_messages.append(entry.text)
    
    assert len(last_two_messages) == 2
    assert last_two_messages[-1] == post_timeout_message


# ===========================================
# 多线程测试用例 - 验证 SQLite 线程安全修复
# ===========================================

@pytest.mark.asyncio
async def test_concurrent_store_operations(output_manager):
    """测试多线程并发存储操作的线程安全性"""
    process_id = "concurrent_store_test"
    output_key = "stdout"
    num_threads = 10
    messages_per_thread = 20
    
    def store_messages_sync(thread_id):
        """同步存储消息的辅助函数"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            for i in range(messages_per_thread):
                message = f"Thread-{thread_id}-Message-{i}"
                loop.run_until_complete(
                    output_manager.store_output(process_id, output_key, message)
                )
                # 添加小延迟以增加线程竞争
                time.sleep(0.001)
        finally:
            loop.close()
    
    # 使用 ThreadPoolExecutor 并发执行存储操作
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(store_messages_sync, i) for i in range(num_threads)]
        concurrent.futures.wait(futures)
        
        # 检查是否有任何线程抛出异常
        for future in futures:
            if future.exception():
                pytest.fail(f"Thread execution failed with exception: {future.exception()}")
    
    # 验证所有消息都被正确存储
    all_messages = []
    async for entry in output_manager.get_output(process_id, output_key):
        all_messages.append(entry.text)
    
    # 应该有 num_threads * messages_per_thread 条消息
    expected_total = num_threads * messages_per_thread
    assert len(all_messages) == expected_total, f"Expected {expected_total} messages, got {len(all_messages)}"
    
    # 验证每个线程的所有消息都存在
    for thread_id in range(num_threads):
        for msg_id in range(messages_per_thread):
            expected_message = f"Thread-{thread_id}-Message-{msg_id}"
            assert expected_message in all_messages, f"Missing message: {expected_message}"


@pytest.mark.asyncio
async def test_concurrent_read_write_operations(output_manager):
    """测试多线程并发读写操作的线程安全性"""
    process_id = "concurrent_rw_test"
    output_key = "stdout"
    num_writer_threads = 5
    num_reader_threads = 3
    messages_per_writer = 10
    
    results = {
        'write_errors': [],
        'read_errors': [],
        'read_counts': []
    }
    
    def writer_thread(thread_id):
        """写入线程函数"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            for i in range(messages_per_writer):
                message = f"Writer-{thread_id}-Msg-{i}"
                loop.run_until_complete(
                    output_manager.store_output(process_id, output_key, message)
                )
                time.sleep(0.002)  # 模拟实际写入延迟
        except Exception as e:
            results['write_errors'].append(f"Writer-{thread_id}: {e}")
        finally:
            loop.close()
    
    def reader_thread(thread_id):
        """读取线程函数"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            for _ in range(5):  # 每个读取线程读取5次
                messages = []
                async def collect_messages():
                    try:
                        async for entry in output_manager.get_output(process_id, output_key):
                            messages.append(entry.text)
                        return len(messages)
                    except ProcessNotFoundError:
                        # 如果进程还不存在，返回 0，这是正常的并发情况
                        return 0
                
                count = loop.run_until_complete(collect_messages())
                results['read_counts'].append(count)
                time.sleep(0.005)  # 读取间隔
        except Exception as e:
            results['read_errors'].append(f"Reader-{thread_id}: {e}")
        finally:
            loop.close()
    
    # 启动写入和读取线程，但先让写入线程运行一小段时间
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_writer_threads + num_reader_threads) as executor:
        # 先提交写入线程
        writer_futures = [executor.submit(writer_thread, i) for i in range(num_writer_threads)]
        
        # 等待一小段时间，让写入线程有机会创建进程数据
        time.sleep(0.01)
        
        # 然后提交读取线程
        reader_futures = [executor.submit(reader_thread, i) for i in range(num_reader_threads)]
        
        # 等待所有线程完成
        concurrent.futures.wait(writer_futures + reader_futures)
    
    # 验证没有线程错误
    assert len(results['write_errors']) == 0, f"Write errors occurred: {results['write_errors']}"
    assert len(results['read_errors']) == 0, f"Read errors occurred: {results['read_errors']}"
    
    # 验证最终数据一致性
    final_messages = []
    async for entry in output_manager.get_output(process_id, output_key):
        final_messages.append(entry.text)
    
    expected_total = num_writer_threads * messages_per_writer
    assert len(final_messages) == expected_total
    
    # 验证读取计数的合理性（读取计数应该是非递减的）
    assert len(results['read_counts']) > 0, "No read counts recorded"
    assert max(results['read_counts']) <= expected_total, "Read count exceeded expected total"


@pytest.mark.asyncio 
async def test_concurrent_different_processes(output_manager):
    """测试多线程操作不同进程的线程安全性"""
    num_processes = 8
    messages_per_process = 15
    
    def process_worker(process_index):
        """为单个进程存储消息的工作函数"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            process_id = f"process_{process_index}"
            for msg_index in range(messages_per_process):
                message = f"Process-{process_index}-Message-{msg_index}"
                loop.run_until_complete(
                    output_manager.store_output(process_id, "stdout", message)
                )
                # 为了增加并发压力，交替存储到 stderr
                if msg_index % 2 == 0:
                    stderr_message = f"Process-{process_index}-Error-{msg_index//2}"
                    loop.run_until_complete(
                        output_manager.store_output(process_id, "stderr", stderr_message)
                    )
        finally:
            loop.close()
    
    # 并发操作多个进程
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_processes) as executor:
        futures = [executor.submit(process_worker, i) for i in range(num_processes)]
        concurrent.futures.wait(futures)
        
        # 检查线程执行是否成功
        for i, future in enumerate(futures):
            if future.exception():
                pytest.fail(f"Process {i} worker failed: {future.exception()}")
    
    # 验证每个进程的数据完整性
    for process_index in range(num_processes):
        process_id = f"process_{process_index}"
        
        # 验证 stdout
        stdout_messages = []
        async for entry in output_manager.get_output(process_id, "stdout"):
            stdout_messages.append(entry.text)
        assert len(stdout_messages) == messages_per_process
        
        # 验证 stderr  
        stderr_messages = []
        async for entry in output_manager.get_output(process_id, "stderr"):
            stderr_messages.append(entry.text)
        expected_stderr_count = (messages_per_process + 1) // 2
        assert len(stderr_messages) == expected_stderr_count
        
        # 验证消息内容的正确性
        for msg_index in range(messages_per_process):
            expected_message = f"Process-{process_index}-Message-{msg_index}"
            assert expected_message in stdout_messages


@pytest.mark.asyncio
async def test_stress_concurrent_operations(output_manager):
    """压力测试：高并发读写操作"""
    process_id = "stress_test"
    num_concurrent_operations = 50
    operations_per_thread = 10
    
    operation_results = {
        'success_count': 0,
        'error_count': 0,
        'errors': []
    }
    lock = threading.Lock()
    
    def stress_operation(op_id):
        """执行混合读写操作"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            for i in range(operations_per_thread):
                try:
                    # 交替进行读写操作
                    if i % 2 == 0:
                        # 写操作
                        message = f"StressOp-{op_id}-{i}"
                        loop.run_until_complete(
                            output_manager.store_output(process_id, "stdout", message)
                        )
                    else:
                        # 读操作
                        messages = []
                        async def read_all():
                            async for entry in output_manager.get_output(process_id, "stdout"):
                                messages.append(entry.text)
                        loop.run_until_complete(read_all())
                    
                    with lock:
                        operation_results['success_count'] += 1
                        
                except Exception as e:
                    with lock:
                        operation_results['error_count'] += 1
                        operation_results['errors'].append(f"Op-{op_id}-{i}: {e}")
        finally:
            loop.close()
    
    # 启动高并发操作
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_concurrent_operations) as executor:
        futures = [executor.submit(stress_operation, i) for i in range(num_concurrent_operations)]
        concurrent.futures.wait(futures)
    
    # 验证操作结果
    total_expected_operations = num_concurrent_operations * operations_per_thread
    total_actual_operations = operation_results['success_count'] + operation_results['error_count']
    
    assert total_actual_operations == total_expected_operations, \
        f"Expected {total_expected_operations} operations, got {total_actual_operations}"
    
    # 验证错误率在可接受范围内（应该没有SQLite线程安全错误）
    error_rate = operation_results['error_count'] / total_expected_operations
    assert error_rate == 0, \
        f"Error rate {error_rate:.2%} too high. Errors: {operation_results['errors'][:5]}"  # 只显示前5个错误
    
    print(f"Stress test completed: {operation_results['success_count']} successful operations")


@pytest.mark.asyncio
async def test_thread_safety_sqlite_connections(output_manager):
    """特定测试：验证 SQLite 连接的线程安全性"""
    process_id = "sqlite_thread_safety"
    output_key = "stdout"
    
    # 这个测试专门用于检测 "SQLite objects created in a thread can only be used in that same thread" 错误
    def sqlite_thread_operation(thread_id):
        """执行可能触发 SQLite 线程错误的操作"""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            # 快速连续的存储和读取操作，更容易触发线程问题
            for i in range(20):
                message = f"SQLiteTest-{thread_id}-{i}"
                loop.run_until_complete(
                    output_manager.store_output(process_id, output_key, message)
                )
                
                # 立即尝试读取，增加线程竞争
                messages = []
                async def immediate_read():
                    async for entry in output_manager.get_output(process_id, output_key, tail=5):
                        messages.append(entry.text)
                loop.run_until_complete(immediate_read())
                
                # 短暂延迟，模拟真实场景
                time.sleep(0.001)
        finally:
            loop.close()
    
    # 使用多个线程同时操作同一个进程
    num_threads = 15
    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(sqlite_thread_operation, i) for i in range(num_threads)]
        
        # 等待所有线程完成，捕获任何异常
        for i, future in enumerate(futures):
            try:
                future.result()  # 这会重新抛出线程中的任何异常
            except Exception as e:
                error_msg = str(e)
                # 检查是否包含 SQLite 线程安全错误
                if "SQLite objects created in a thread can only be used in that same thread" in error_msg:
                    pytest.fail(f"SQLite thread safety error detected in thread {i}: {error_msg}")
                else:
                    pytest.fail(f"Unexpected error in thread {i}: {error_msg}")
    
    # 验证最终数据一致性
    final_messages = []
    async for entry in output_manager.get_output(process_id, output_key):
        final_messages.append(entry.text)
    
    expected_total = num_threads * 20
    assert len(final_messages) == expected_total, \
        f"Expected {expected_total} messages, got {len(final_messages)} - data integrity issue"
    
    print(f"SQLite thread safety test passed: {len(final_messages)} messages stored and retrieved successfully") 