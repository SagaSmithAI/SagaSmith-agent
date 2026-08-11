@echo off
setlocal EnableExtensions
set "SAGASMITH_INSTALL_AGENT_ROOT=%~dp0"
"%ComSpec%" /d /c ""%~dp0scripts\install-all.bat" %*"
exit /b %ERRORLEVEL%
