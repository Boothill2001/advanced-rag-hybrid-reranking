@echo off
setlocal
set PYTHONUTF8=1
cd /d "%~dp0"
if not exist "..\AI_AGENT_LANGGHAPH\start.bat" (
  echo Clone the companion Copilot into sibling folder AI_AGENT_LANGGHAPH.
  echo See README.md for standalone service instructions.
  if not defined DEMO_NO_BROWSER pause
  exit /b 1
)
call "..\AI_AGENT_LANGGHAPH\start.bat"
exit /b %errorlevel%
