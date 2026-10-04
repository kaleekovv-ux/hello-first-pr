@echo off
chcp 65001 >nul
cd /d "%~dp0"

where autoedit >nul 2>nul
if errorlevel 1 (
    echo Не найдена команда autoedit — похоже, программа ещё не установлена в этой папке.
    echo Откройте эту папку в PowerShell и выполните один раз:
    echo     pip install -e .
    echo После этого запустите этот ярлык снова.
    pause
    exit /b 1
)

autoedit gui
if errorlevel 1 (
    echo.
    echo AutoEdit завершился с ошибкой ^(сообщение выше^).
    pause
)
