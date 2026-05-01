"""
Zamanlayıcı — Ana Süreç
========================

Bu dosyayı çalıştır, her şey otomatik döner:
  1. Her saat başında Gmail Inbox'ını kontrol eder
  2. Yeni mailleri pipeline'dan geçirir
  3. Sonuçları SQLite'a kaydeder
  4. API üzerinden dashboard'a yansır

Çalıştırma:
  python scheduler.py

Durdurmak için: Ctrl+C

Log dosyası: scheduler.log (proje klasöründe oluşur)
"""

import time
import logging
import traceback
from datetime import datetime, timedelta
from pathlib import Path

from database import tablolari_olustur, kaydet, message_id_var_mi
from mail_fetcher import mailleri_cek
from pipeline import analiz_et

# ---------------------------------------------------------------------------
# LOG AYARI
# ---------------------------------------------------------------------------

log_yolu = Path(__file__).parent / "scheduler.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(log_yolu, encoding="utf-8"),
        logging.StreamHandler(),   # terminale de yaz
    ]
)
log = logging.getLogger("scheduler")

KONTROL_ARALIGI_DAKIKA = 60   # her 60 dakikada bir


# ---------------------------------------------------------------------------
# ANALİZ DÖNGÜSÜ
# ---------------------------------------------------------------------------

def bir_tur_calistir() -> dict:
    """
    Tek bir kontrol turu:
    1. Gmail'den yeni mailleri çek
    2. Daha önce analiz edilmemiş olanları filtrele
    3. Pipeline'dan geçir
    4. DB'ye kaydet

    Returns: {"kontrol_edilen", "yeni", "kaydedilen", "hatali"}
    """
    sonuc = {"kontrol_edilen": 0, "yeni": 0, "kaydedilen": 0, "hatali": 0}

    # 1. Mailleri çek
    try:
        mailler = mailleri_cek(sadece_yeniler=True)
    except (ValueError, ConnectionError) as e:
        log.error(f"Gmail bağlantısı başarısız: {e}")
        return sonuc
    except Exception as e:
        log.error(f"Beklenmeyen hata (mail çekme): {e}")
        return sonuc

    sonuc["kontrol_edilen"] = len(mailler)

    if not mailler:
        log.info("Yeni mail yok.")
        return sonuc

    # 2. Daha önce analiz edilmemişleri filtrele
    yeni_mailler = [m for m in mailler if not message_id_var_mi(m["message_id"])]
    sonuc["yeni"] = len(yeni_mailler)

    if not yeni_mailler:
        log.info(f"{len(mailler)} mail kontrol edildi, hepsi zaten analiz edilmiş.")
        return sonuc

    log.info(f"{len(mailler)} mail kontrol edildi, {len(yeni_mailler)} yeni bulundu.")

    # 3. Her maili analiz et ve kaydet
    for i, mail in enumerate(yeni_mailler, 1):
        try:
            log.info(f"  [{i}/{len(yeni_mailler)}] Analiz ediliyor: {mail['konu'][:60]}")

            analiz = analiz_et(mail["konu"], mail["govde"])

            if "hata" in analiz:
                log.warning(f"  Pipeline hatası: {analiz['hata']}")
                sonuc["hatali"] += 1
                continue

            basarili = kaydet(
                message_id    = mail["message_id"],
                gonderen      = mail["gonderen"],
                konu          = mail["konu"],
                govde         = mail["govde"],
                analiz_sonucu = analiz,
                gelen_tarih   = mail["tarih"],
            )

            if basarili:
                log.info(
                    f"  ✓ Kaydedildi → [{analiz['kategori'].upper()}] "
                    f"aciliyet:{analiz['aciliyet']} "
                    f"güven:%{analiz['kategori_guven']*100:.0f}"
                )
                sonuc["kaydedilen"] += 1
            else:
                log.debug(f"  Zaten kayıtlı (race condition): {mail['message_id']}")

        except Exception as e:
            log.error(f"  ✗ Analiz hatası ({mail['konu'][:40]}): {e}")
            log.debug(traceback.format_exc())
            sonuc["hatali"] += 1

    return sonuc


# ---------------------------------------------------------------------------
# ANA DÖNGÜ
# ---------------------------------------------------------------------------

def main():
    log.info("=" * 60)
    log.info("Şikayet Agent Zamanlayıcısı başlatıldı")
    log.info(f"Kontrol aralığı: her {KONTROL_ARALIGI_DAKIKA} dakika")
    log.info("=" * 60)

    # DB'yi hazırla
    tablolari_olustur()

    while True:
        simdi = datetime.now()
        log.info(f"\n{'─'*50}")
        log.info(f"Kontrol turu başlıyor: {simdi.strftime('%H:%M:%S')}")

        try:
            sonuc = bir_tur_calistir()
            log.info(
                f"Tur tamamlandı → "
                f"kontrol:{sonuc['kontrol_edilen']} "
                f"yeni:{sonuc['yeni']} "
                f"kaydedilen:{sonuc['kaydedilen']} "
                f"hatalı:{sonuc['hatali']}"
            )
        except Exception as e:
            log.error(f"Tur sırasında kritik hata: {e}")
            log.debug(traceback.format_exc())

        # Bir sonraki tur
        sonraki = simdi + timedelta(minutes=KONTROL_ARALIGI_DAKIKA)
        log.info(f"Sonraki kontrol: {sonraki.strftime('%H:%M:%S')}")

        try:
            time.sleep(KONTROL_ARALIGI_DAKIKA * 60)
        except KeyboardInterrupt:
            log.info("\nZamanlayıcı durduruldu (Ctrl+C).")
            break


if __name__ == "__main__":
    main()