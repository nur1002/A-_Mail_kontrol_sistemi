@echo off
REM Şikayet Agent — Başlangıç Script
REM
REM Bu dosyayı çalıştır: start.bat
REM Otomatik olarak:
REM   1. API'yi başlatır (port 8000)
REM   2. Scheduler'ı background'da çalıştırır
REM   3. http://localhost:8000 adresini açar

cd /d "%~dp0"

REM Veri tabanını kontrol et
if not exist "sikayet_agent.db" (
  echo Veri tabanı oluşturuluyor...
  python -c "from database import tablolari_olustur; tablolari_olustur()"
)

REM .env dosyasını kontrol et
if not exist ".env" (
  echo.
  echo [UYARI] .env dosyası bulunamadı
  echo Dosyayı oluşturun veya dashboard'dan Gmail ayarlarını yapın
  echo.
)

REM Scheduler'ı background'da başlat
echo Scheduler başlatılıyor...
start /B python scheduler.py > scheduler.log 2>&1

REM API'yi başlat
echo API başlatılıyor... http://localhost:8000
echo.
echo Tarayıcı otomatik açılacak...
timeout /t 3 /nobreak
start http://localhost:8000/index.html

python -m uvicorn api:app --host 0.0.0.0 --port 8000 --reload

# ./start.bat