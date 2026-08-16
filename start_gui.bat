@echo off
rem Start the optional graphical interface
python "%~dp0mc_geo_converter_gui.py"
if errorlevel 1 pause
