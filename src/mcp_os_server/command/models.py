"""
This module defines the Pydantic data models for the MCP Command Server.
"""
import enum
from datetime import datetime
from typing import Optional, Sequence
from collections.abc import Mapping

from pydantic import BaseModel, Field


class OutputMessageEntry(BaseModel):
    """
    Represents a single log entry for process output.
    """
    timestamp: datetime = Field(..., description="Timestamp of the log entry")
    text: str = Field("", description="Content of the log entry")
    output_key: Optional[str] = Field(None, description="Type of output, e.g., stdout or stderr")


class MessageEntry(BaseModel):
    """
    Represents a single log entry.
    """
    timestamp: datetime = Field(..., description="Timestamp of the log entry")
    text: str = Field("", description="Content of the log entry")


class ProcessStatus(str, enum.Enum):
    """
    Enumeration for process status.
    """
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TERMINATED = "terminated"
    ERROR = "error"


class ProcessInfo(BaseModel):
    """
    Detailed information about a process.
    """
    pid: str = Field(..., description="Unique identifier for the process")
    command: Sequence[str] = Field(..., description="The command and its arguments")
    directory: str = Field(..., description="Working directory for the command execution")
    envs: Mapping[str, str] = Field(..., description="Environment variables for the process")
    description: str = Field(..., description="Description of the process")
    status: ProcessStatus = Field(..., description="Current status of the process")
    start_time: datetime = Field(..., description="Start time of the process")
    end_time: Optional[datetime] = Field(None, description="End time of the process")
    exit_code: Optional[int] = Field(None, description="Exit code of the process")
    labels: Sequence[str] = Field([], description="List of labels for process classification")
    timeout: Optional[int] = Field(None, description="Maximum execution time in seconds")
    error_message: Optional[str] = Field(None, description="Error message encountered by the process")

class CommandResult(BaseModel):
    """
    Result of a command execution.
    """
    stdout: str = Field(..., description="Standard output string")
    stderr: str = Field(..., description="Standard error string")
    exit_code: int = Field(..., description="Process exit code")
    execution_time: float = Field(..., description="Command execution time in seconds") 