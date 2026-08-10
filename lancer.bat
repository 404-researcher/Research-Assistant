@echo off
setlocal
title Research Assistant
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERREUR] Environnement virtuel introuvable dans .venv\
    echo.
    echo Pour le creer :
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo Lancement de Research Assistant...
echo Le navigateur va s'ouvrir automatiquement dans quelques secondes.
echo.
echo Rappel : pour utiliser Ollama (mode local gratuit), lancez "ollama serve" avant de chercher.
echo Pour arreter l'application : fermez cette fenetre, ou Ctrl+C.
echo.

".venv\Scripts\python.exe" -m streamlit run app.py

echo.
echo L'application s'est arretee.
pause
