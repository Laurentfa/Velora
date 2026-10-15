@echo off
REM Signature du programme Windows (évite l'avertissement SmartScreen). NON TESTE : il faut un certificat de signature de code (fichier .pfx).
REM Variables : PFX_FILE (chemin du certificat), PFX_PASSWORD. Outil : signtool (Windows SDK).
if "%PFX_FILE%"=="" (echo Definir PFX_FILE et PFX_PASSWORD & exit /b 1)
signtool sign /f "%PFX_FILE%" /p "%PFX_PASSWORD%" /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 "dist\Velora\Velora.exe"
signtool verify /pa "dist\Velora\Velora.exe"
