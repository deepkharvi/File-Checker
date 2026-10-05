@echo off
REM Builds a standalone FileHealthChecker app using the venv from setup.bat.
REM The finished app will be in dist\FileHealthChecker\FileHealthChecker.exe
REM
REM Uses --onedir instead of --onefile: a --onefile exe silently re-extracts
REM the entire Python runtime, Qt, OpenCV, and LibRaw into a temp folder on
REM EVERY launch, which is why it was slow to open. --onedir runs directly
REM from its own folder with no extraction step, so launches are near-instant
REM after the first one. The trade-off is you hand people a folder instead
REM of a single file -- they still just double-click the .exe inside it.
REM
REM Also excludes large Qt modules this app never uses (WebEngine, QML/Quick,
REM Multimedia, 3D, Charts, etc.) to shrink the build and speed up startup
REM further, and disables UPX compression (--noupx), since decompressing a
REM UPX-packed exe on every launch adds startup time for very little size
REM benefit here.

cd /d "%~dp0"

if not exist venv\Scripts\activate.bat (
    echo Virtual environment not found. Run setup.bat first.
    pause
    exit /b 1
)

call venv\Scripts\activate.bat

echo Building FileHealthChecker (onedir, trimmed) ...
pyinstaller --noconfirm --onedir --windowed --noupx --name "FileHealthChecker" ^
    --exclude-module PySide6.QtWebEngineWidgets ^
    --exclude-module PySide6.QtWebEngineCore ^
    --exclude-module PySide6.QtQml ^
    --exclude-module PySide6.QtQuick ^
    --exclude-module PySide6.QtQuickWidgets ^
    --exclude-module PySide6.QtQuick3D ^
    --exclude-module PySide6.QtMultimedia ^
    --exclude-module PySide6.QtMultimediaWidgets ^
    --exclude-module PySide6.QtBluetooth ^
    --exclude-module PySide6.QtNfc ^
    --exclude-module PySide6.QtSerialPort ^
    --exclude-module PySide6.QtPositioning ^
    --exclude-module PySide6.QtLocation ^
    --exclude-module PySide6.QtSensors ^
    --exclude-module PySide6.QtCharts ^
    --exclude-module PySide6.QtDataVisualization ^
    --exclude-module PySide6.QtPdf ^
    --exclude-module PySide6.QtPdfWidgets ^
    --exclude-module PySide6.QtDesigner ^
    --exclude-module PySide6.QtHelp ^
    --exclude-module PySide6.QtTest ^
    --exclude-module PySide6.QtSql ^
    --exclude-module PySide6.QtSvg ^
    --exclude-module PySide6.QtSvgWidgets ^
    --exclude-module PySide6.QtXml ^
    --exclude-module PySide6.QtOpenGL ^
    --exclude-module PySide6.QtOpenGLWidgets ^
    --exclude-module PySide6.Qt3DCore ^
    --exclude-module PySide6.Qt3DRender ^
    --exclude-module PySide6.Qt3DInput ^
    --exclude-module PySide6.Qt3DLogic ^
    --exclude-module PySide6.Qt3DAnimation ^
    --exclude-module PySide6.Qt3DExtras ^
    main.py

if errorlevel 1 (
    echo.
    echo Build failed. See the error above.
    pause
    exit /b 1
)

echo.
echo Bundling VC++ runtime DLLs so target PCs don't need to install anything...
for %%D in (vcruntime140.dll vcruntime140_1.dll msvcp140.dll msvcp140_1.dll msvcp140_2.dll concrt140.dll) do (
    if exist "%SystemRoot%\System32\%%D" (
        copy /y "%SystemRoot%\System32\%%D" "dist\FileHealthChecker\_internal\%%D" >nul
        echo   copied %%D
    )
)

echo.
echo Done. Your app is at: dist\FileHealthChecker\FileHealthChecker.exe
echo Hand people the whole "dist\FileHealthChecker" folder -- the exe needs
echo the files next to it. Launches should now be near-instant.
pause
