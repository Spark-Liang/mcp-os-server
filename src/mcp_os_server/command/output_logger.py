import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from typing import AsyncGenerator, List, Optional

import yaml

from .interfaces import IOutputLogger
from .models import MessageEntry


class YamlOutputLogger(IOutputLogger):
    """
    An implementation of IOutputLogger that stores logs in a YAML file.
    Each entry is a separate YAML document. This implementation opens and
    closes the file for each operation to ensure simplicity and reliability.
    """

    def __init__(self, log_file_path: str):
        self.log_file_path = log_file_path
        os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)
        self._ensure_log_file_exists()

    def _ensure_log_file_exists(self):
        if not os.path.exists(self.log_file_path):
            with open(self.log_file_path, "w", encoding="utf-8") as f:
                yaml.dump([], f)

    def add_message(self, message: str, timestamp: Optional[datetime] = None) -> None:
        self.add_messages([message], timestamp)

    def add_messages(
        self, messages: List[str], timestamp: Optional[datetime] = None
    ) -> None:
        current_data = self._read_logs()
        for message in messages:
            entry = MessageEntry(
                timestamp=timestamp if timestamp else datetime.now(), text=message
            )
            current_data.append(entry.model_dump(mode="json"))
        with open(self.log_file_path, "w", encoding="utf-8") as f:
            yaml.dump(
                current_data,
                f,
                sort_keys=False,
                default_flow_style=False,
                default_style="|",
                allow_unicode=True,
            )

    async def get_logs(
        self,
        tail: Optional[int] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> AsyncGenerator[MessageEntry, None]:
        all_logs = self._read_logs()
        filtered_logs = []

        for entry_data in all_logs:
            entry = MessageEntry(**entry_data)
            if since and entry.timestamp < since:
                continue
            if until and entry.timestamp > until:
                continue
            filtered_logs.append(entry)

        if tail is not None:
            filtered_logs = filtered_logs[-tail:]

        for entry in filtered_logs:
            yield entry

    def _read_logs(self) -> List[dict]:
        if not os.path.exists(self.log_file_path):
            return []
        with open(self.log_file_path, "r", encoding="utf-8") as f:
            try:
                data = yaml.safe_load(f)
                return data if data is not None else []
            except yaml.YAMLError:
                return []

    def close(self) -> None:
        pass  # No specific resources to close for file-based logger

    def __del__(self):
        """No-op as file handles are managed within methods."""
        pass


class SqliteOutputLogger(IOutputLogger):
    """
    An implementation of IOutputLogger that stores logs in an SQLite database.
    Each sub_id corresponds to a table within the database.
    """

    def __init__(self, log_file_path: str, sub_id: Optional[str] = None):
        self.log_file_path = log_file_path
        self.sub_id = sub_id if sub_id else "default"
        self.table_name = f"logs_{re.sub(r'[^a-zA-Z0-9_]', '', self.sub_id)}"
        os.makedirs(os.path.dirname(self.log_file_path), exist_ok=True)
        self._lock = threading.Lock()
        self._ensure_table_exists()

    def _create_connection(self):
        """Create a new database connection for each operation to ensure thread safety."""
        conn = sqlite3.connect(self.log_file_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_table_exists(self):
        with self._lock:
            conn = self._create_connection()
            try:
                with conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        f"""
                        CREATE TABLE IF NOT EXISTS {self.table_name} (
                            timestamp REAL, -- Changed to REAL for Unix timestamp
                            text TEXT
                        )
                    """
                    )
                    conn.commit()
            finally:
                conn.close()

    def add_message(self, message: str, timestamp: Optional[datetime] = None) -> None:
        self.add_messages([message], timestamp)

    def add_messages(
        self, messages: List[str], timestamp: Optional[datetime] = None
    ) -> None:
        with self._lock:
            conn = self._create_connection()
            try:
                with conn:
                    cursor = conn.cursor()
                    current_timestamp = (
                        timestamp if timestamp else datetime.now(timezone.utc)
                    )
                    data_to_insert = [
                        (current_timestamp.timestamp(), message)
                        for message in messages  # Store as Unix timestamp
                    ]
                    cursor.executemany(
                        f"INSERT INTO {self.table_name} (timestamp, text) VALUES (?, ?)",
                        data_to_insert,
                    )
                    conn.commit()
            finally:
                conn.close()

    async def get_logs(
        self,
        tail: Optional[int] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> AsyncGenerator[MessageEntry, None]:
        with self._lock:
            conn = self._create_connection()
            try:
                query = f"SELECT timestamp, text FROM {self.table_name}"
                params = []
                where_clauses = []

                if since:
                    where_clauses.append("timestamp >= ?")
                    params.append(since.timestamp())  # Compare with Unix timestamp
                if until:
                    where_clauses.append("timestamp <= ?")
                    params.append(until.timestamp())  # Compare with Unix timestamp

                if where_clauses:
                    query += " WHERE " + " AND ".join(where_clauses)

                query += " ORDER BY timestamp ASC"

                if tail is not None:
                    pass  # We will handle tail in Python after fetching all relevant logs

                all_logs = []
                with conn:
                    cursor = conn.execute(query, params)
                    for row in cursor.fetchall():
                        # Convert Unix timestamp back to datetime
                        timestamp = datetime.fromtimestamp(
                            row["timestamp"], tz=timezone.utc
                        )
                        all_logs.append(
                            MessageEntry(timestamp=timestamp, text=row["text"])
                        )

                if tail is not None:
                    all_logs = all_logs[-tail:]

                for entry in all_logs:
                    yield entry
            finally:
                conn.close()

    def close(self) -> None:
        """Close is now a no-op since we don't maintain persistent connections."""
        pass

    def __del__(self):
        """No cleanup needed since we don't maintain persistent connections."""
        pass
