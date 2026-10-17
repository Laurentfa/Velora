@echo off
REM Construit "Velora" pour Windows (dossier dist\Velora\). A lancer sur un PC, dans ce dossier.
REM Pre-requis : Python 3.11+ installe, puis :  py -m venv .venv  &&  .venv\Scripts\pip install -r requirements.txt
for /f %%i in ('.venv\Scripts\python -c "import nicegui,os;print(os.path.dirname(nicegui.__file__))"') do set NG=%%i
.venv\Scripts\pyinstaller main.py --name "Velora" --windowed --noconfirm --clean ^
  --icon assets\icon.ico ^
  --add-data "%NG%;nicegui" ^
  --collect-all exchangelib --collect-all webview --collect-all keyring --collect-all mammoth --collect-all openpyxl --collect-all pptx --collect-all pillow_heif --collect-all pypdf --collect-all zeroconf --collect-all segno --collect-all sqlcipher3 --collect-all dns --collect-all pywhispercpp --collect-all sounddevice --collect-submodules mailia ^
  --collect-data tzdata
echo Termine : dist\Velora\Velora.exe
