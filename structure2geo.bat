@echo off
rem .mcstructure -> .geo.json  (drag a .mcstructure file onto this batch file)
python "%~dp0mc_geo_converter.py" to-geo %*
if errorlevel 1 pause
