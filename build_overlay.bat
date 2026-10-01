@echo off
setlocal
cd /d "%~dp0"
set "FX=%WINDIR%\Microsoft.NET\Framework64\v4.0.30319"
if not exist "%FX%\csc.exe" set "FX=%WINDIR%\Microsoft.NET\Framework\v4.0.30319"
if not exist "%FX%\csc.exe" (
  echo Install .NET Framework 4.8, then run this script again.
  exit /b 1
)
"%FX%\csc.exe" /nologo /langversion:5 /codepage:65001 /platform:x64 /target:winexe /out:esp_overlay.exe /reference:"%FX%\System.dll" /reference:"%FX%\System.Core.dll" /reference:"%FX%\WPF\WindowsBase.dll" /reference:"%FX%\System.Xaml.dll" /reference:"%FX%\WPF\PresentationCore.dll" /reference:"%FX%\WPF\PresentationFramework.dll" /reference:"%FX%\System.Web.Extensions.dll" esp_overlay.cs ktweaks.cs ktweaksl.cs ks_menu.cs last_hit_menu.cs zoom_menu.cs
if errorlevel 1 (
  echo BUILD FAILED. Check .NET 4.8 and close the previous overlay.
  exit /b 1
)
echo [KS] Overlay built successfully.
exit /b 0
