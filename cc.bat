@echo off
set ANTHROPIC_BASE_URL=http://localhost:20128/v1
rem token is read from the gitignored .env (line:  omniroute=sk-...) so it stays out of git
for /f "usebackq tokens=1,* delims==" %%a in ("%~dp0.env") do if /i "%%a"=="omniroute" set ANTHROPIC_AUTH_TOKEN=%%b
set ANTHROPIC_API_KEY=
set ANTHROPIC_MODEL=anymodel
set ANTHROPIC_SMALL_FAST_MODEL=kr/glm-5
set CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
claude %*