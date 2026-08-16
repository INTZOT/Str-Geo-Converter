@echo off
rem .geo.json -> .mcstructure  (drag a .geo.json file onto this batch file)
python "%~dp0mc_geo_converter.py" to-structure %*
if errorlevel 1 pause
