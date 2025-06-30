@echo off
REM Test CMD file for PATH lookup verification
REM This script demonstrates that PATH lookup is working correctly

if "%1"=="" (
    echo Hello from test_cmd_file.cmd!
    echo Usage: test_cmd_file.cmd [message]
) else (
    echo Message from test_cmd_file.cmd: %*
)

REM Return success exit code
exit /b 0 