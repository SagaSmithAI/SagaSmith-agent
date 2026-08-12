@echo off
call "%~dp0scripts\stop-all.bat" %*
exit /b %ERRORLEVEL%
