@echo off
set "PYTHON_EXE=..\python\audio\venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" set "PYTHON_EXE=python"

echo Running COMPLETE Syntheta Audit...
"%PYTHON_EXE%" comprehensive_test.py
pause