@echo off
title Push Adroit ATS to GitHub (praveenvalipireddy-del/Adroit_ats)
echo =======================================================================
echo   Pushing Adroit ATS to GitHub
echo   Repository: https://github.com/praveenvalipireddy-del/Adroit_ats.git
echo =======================================================================
echo.

set PATH=%~dp0..\git\cmd;%PATH%
cd /d "%~dp0"

echo [*] Checking Git status...
git status

echo.
echo [*] Pushing branch 'main' to origin...
git push -u origin main

echo.
if %ERRORLEVEL% equ 0 (
    echo =======================================================================
    echo   SUCCESSFULLY PUSHED TO GITHUB!
    echo   View repository at: https://github.com/praveenvalipireddy-del/Adroit_ats
    echo   Now go to https://dashboard.render.com to deploy live!
    echo =======================================================================
) else (
    echo [!] Push encountered an issue. If GitHub prompted for a password, 
    echo     please use a GitHub Personal Access Token (PAT) from:
    echo     https://github.com/settings/tokens
)
echo.
pause
