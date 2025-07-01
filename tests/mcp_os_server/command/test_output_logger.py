import asyncio
import os
import tempfile
from datetime import datetime, timedelta
from typing import AsyncGenerator, Generator, Type

import pytest
import yaml

from mcp_os_server.command.interfaces import IOutputLogger
from mcp_os_server.command.models import MessageEntry
from mcp_os_server.command.output_logger import YamlOutputLogger
from mcp_os_server.command.output_logger import SqliteOutputLogger


class BaseTestOutputLogger:
    """
    Base class for testing implementations of the IOutputLogger interface.
    Subclasses must provide a 'logger' fixture that yields an IOutputLogger instance.
    """

    @pytest.mark.asyncio
    async def test_add_single_message(self, logger: IOutputLogger):
        """Test adding a single message."""
        test_message = "Hello, World!"
        logger.add_message(test_message)
        logger.close()  # Flush buffer

        retrieved_logs: list[MessageEntry] = [log async for log in logger.get_logs()]
        assert len(retrieved_logs) == 1
        assert retrieved_logs[0].text == test_message
        assert isinstance(retrieved_logs[0].timestamp, datetime)

    @pytest.mark.asyncio
    async def test_add_multiple_messages(self, logger: IOutputLogger):
        """Test adding multiple messages."""
        messages = ["First message", "Second message", "Third message"]
        logger.add_messages(messages)
        logger.close()  # Flush buffer

        retrieved_logs: list[MessageEntry] = [log async for log in logger.get_logs()]
        assert len(retrieved_logs) == 3
        assert [entry.text for entry in retrieved_logs] == messages

    @pytest.mark.asyncio
    async def test_get_logs_all(self, logger: IOutputLogger):
        """Test getting all logs."""
        messages = ["Log 1", "Log 2"]
        logger.add_messages(messages)

        retrieved_logs: list[MessageEntry] = [log async for log in logger.get_logs()]
        
        assert len(retrieved_logs) == 2
        assert retrieved_logs[0].text == "Log 1"
        assert retrieved_logs[1].text == "Log 2"

    @pytest.mark.asyncio
    async def test_get_logs_with_tail(self, logger: IOutputLogger):
        """Test the 'tail' parameter for getting logs."""
        messages = [f"Message {i}" for i in range(10)]
        logger.add_messages(messages)

        retrieved_logs = [log async for log in logger.get_logs(tail=3)]
        
        assert len(retrieved_logs) == 3
        assert retrieved_logs[0].text == "Message 7"
        assert retrieved_logs[1].text == "Message 8"
        assert retrieved_logs[2].text == "Message 9"

    @pytest.mark.asyncio
    async def test_get_logs_with_since_until(self, logger: IOutputLogger):
        """Test filtering logs with 'since' and 'until' parameters."""
        logger.add_message("Message 1")
        await asyncio.sleep(0.01)  # Ensure timestamps are distinct
        logger.add_message("Message 2 (target)")
        await asyncio.sleep(0.01)
        logger.add_message("Message 3")
        
        all_logs = [log async for log in logger.get_logs()]
        
        assert len(all_logs) == 3
        
        since_time = all_logs[0].timestamp + timedelta(microseconds=1)
        until_time = all_logs[2].timestamp - timedelta(microseconds=1)
        
        retrieved_logs = [log async for log in logger.get_logs(since=since_time, until=until_time)]
        
        assert len(retrieved_logs) == 1
        assert retrieved_logs[0].text == "Message 2 (target)"

    @pytest.mark.asyncio
    async def test_add_message_with_special_characters_and_newlines(self, logger: IOutputLogger):
        """Test adding a message with special characters and newlines."""
        special_message = (
            "This is a test message with special characters:\n"
            "  - Quotes: \"Hello, World!\" and 'It's great!'\n"
            "  - YAML special: {key: value}, [list, items], :, #comments, >block\n"
            "  - Newlines: \n\nThis part is after a double newline.\n"
            "End of message."
        )
        
        logger.add_message(special_message)
        logger.close()

        retrieved_logs = [log async for log in logger.get_logs()]
        assert len(retrieved_logs) == 1
        assert retrieved_logs[0].text == special_message


class TestYamlOutputLogger(BaseTestOutputLogger):
    """
    Tests for the YamlOutputLogger implementation.
    This class inherits general interface tests from BaseTestOutputLogger
    and adds implementation-specific tests.
    """

    @pytest.fixture
    def temp_log_file(self) -> Generator[str, None, None]:
        """Provides a temporary file path for logging."""
        fd, file_path = tempfile.mkstemp(suffix=".yaml", text=True)
        os.close(fd)
        yield file_path
        if os.path.exists(file_path):
            os.remove(file_path)

    @pytest.fixture
    def logger(self, temp_log_file: str) -> Generator[IOutputLogger, None, None]:
        """Fixture to create a YamlOutputLogger instance and ensure it's closed."""
        log_instance = YamlOutputLogger(temp_log_file)
        yield log_instance
        log_instance.close()

    def _read_yaml_file(self, file_path: str):
        """Helper to read the yaml log file."""
        if not os.path.exists(file_path) or os.path.getsize(file_path) == 0:
            return []
        with open(file_path, 'r', encoding='utf-8') as f:
            try:
                data = yaml.safe_load(f)
                return data if data is not None else []
            except yaml.YAMLError:
                return []

    @pytest.mark.asyncio
    async def test_multi_line_format_in_yaml(self, logger: IOutputLogger, temp_log_file: str):
        """Test that multi-line messages are formatted correctly in YAML."""
        multi_line_message = "Line 1\nLine 2\nLine 3"
        logger.add_message(multi_line_message)
        logger.close()

        log_entries = self._read_yaml_file(temp_log_file)
        assert len(log_entries) == 1
        assert log_entries[0]['text'] == multi_line_message
        # Verify it was written as a literal block scalar
        with open(temp_log_file, 'r', encoding='utf-8') as f:
            content = f.read()
            assert "|-\n" in content

    @pytest.mark.asyncio
    async def test_close_and_reopen_logger(self, temp_log_file: str):
        """Test that the log file is handled correctly on close and reopen."""
        # First logger instance
        logger1 = YamlOutputLogger(temp_log_file)
        assert os.path.exists(temp_log_file)
        logger1.add_message("A message to create the file.")
        logger1.close()
        
        # After closing, the file should still exist and contain the message
        assert os.path.exists(temp_log_file)
        log_entries = self._read_yaml_file(temp_log_file)
        assert len(log_entries) == 1

        # A new logger instance should be able to read it and append to it
        logger2 = YamlOutputLogger(temp_log_file)
        logger2.add_message("Second message.")
        logger2.close()

        all_logs = self._read_yaml_file(temp_log_file)
        assert len(all_logs) == 2
        assert all_logs[0]['text'] == "A message to create the file."
        assert all_logs[1]['text'] == "Second message."


class TestSqliteOutputLogger(BaseTestOutputLogger):
    """
    Tests for the SqliteOutputLogger implementation.
    This class inherits general interface tests from BaseTestOutputLogger
    and adds implementation-specific tests.
    """

    @pytest.fixture
    def temp_db_file(self) -> Generator[str, None, None]:
        """Provides a temporary file path for SQLite database."""
        fd, file_path = tempfile.mkstemp(suffix=".db", text=False)
        os.close(fd)
        yield file_path
        if os.path.exists(file_path):
            os.remove(file_path)

    @pytest.fixture
    def logger(self, temp_db_file: str) -> Generator[IOutputLogger, None, None]:
        """Fixture to create a SqliteOutputLogger instance and ensure it's closed."""
        log_instance = SqliteOutputLogger(temp_db_file)
        yield log_instance
        log_instance.close()

    @pytest.mark.asyncio
    async def test_multi_sub_id_isolation(self, temp_db_file: str):
        """Test that different sub_ids create isolated tables."""
        logger1 = SqliteOutputLogger(temp_db_file, sub_id="proc1_stdout")
        logger2 = SqliteOutputLogger(temp_db_file, sub_id="proc2_stdout")
        logger3 = SqliteOutputLogger(temp_db_file, sub_id="proc1_stderr")

        logger1.add_message("Message for proc1 stdout")
        logger2.add_message("Message for proc2 stdout")
        logger3.add_message("Message for proc1 stderr")

        logger1.close()
        logger2.close()
        logger3.close()

        # Verify isolation
        retrieved_logs_1 = [log async for log in SqliteOutputLogger(temp_db_file, sub_id="proc1_stdout").get_logs()]
        assert len(retrieved_logs_1) == 1
        assert retrieved_logs_1[0].text == "Message for proc1 stdout"

        retrieved_logs_2 = [log async for log in SqliteOutputLogger(temp_db_file, sub_id="proc2_stdout").get_logs()]
        assert len(retrieved_logs_2) == 1
        assert retrieved_logs_2[0].text == "Message for proc2 stdout"
        
        retrieved_logs_3 = [log async for log in SqliteOutputLogger(temp_db_file, sub_id="proc1_stderr").get_logs()]
        assert len(retrieved_logs_3) == 1
        assert retrieved_logs_3[0].text == "Message for proc1 stderr" 