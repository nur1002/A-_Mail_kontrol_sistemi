"""
Gmail Mail Çekici
=================

Gmail Inbox'ındaki okunmamış mailleri IMAP ile çeker,
pipeline'dan geçirip SQLite'a kaydeder.

Kurulum (bir kez yapılır):
  1. Gmail hesabında "2 Adımlı Doğrulama" açık olmalı
  2. https://myaccount.google.com/apppasswords adresinden
     "Uygulama Şifresi" oluştur (16 karakter)
  3. .env dosyası oluştur:
       GMAIL_USER=sizinmail@gmail.com
       GMAIL_APP_PASSWORD=abcd efgh ijkl mnop

Güvenlik notu:
  - Normal Gmail şifresi KULLANILMAZ, sadece uygulama şifresi
  - .env dosyasını asla Git'e commit etme
  - Şifreler sadece yerel makinede kalır

Çalıştırma:
  python mail_fetcher.py          # tek seferlik test
  python scheduler.py             # saatlik otomatik mod
"""

import imaplib
import email
import os
import re
from email.header import decode_header
from email.utils import parsedate_to_datetime
from datetime import datetime
from pathlib import Path

# .env dosyasını yükle (python-dotenv yoksa manuel oku)
def _env_yukle():
    env_yolu = Path(__file__).parent / ".env"
    if env_yolu.exists():
        with open(env_yolu, encoding="utf-8") as f:
            for satir in f:
                satir = satir.strip()
                if satir and not satir.startswith("#") and "=" in satir:
                    anahtar, deger = satir.split("=", 1)
                    os.environ.setdefault(anahtar.strip(), deger.strip())

_env_yukle()

GMAIL_USER         = os.environ.get("GMAIL_USER", "")
GMAIL_APP_PASSWORD = os.environ.get("GMAIL_APP_PASSWORD", "")
IMAP_SUNUCU        = "imap.gmail.com"
IMAP_PORT          = 993
KLASOR             = "INBOX"
MAX_MAIL           = 50   # tek seferde en fazla bu kadar mail işlenir


# ---------------------------------------------------------------------------
# YARDIMCI FONKSİYONLAR
# ---------------------------------------------------------------------------

def _baslik_coz(baslik: str) -> str:
    """MIME encoded mail başlığını düz metne çevirir."""
    if not baslik:
        return ""
    parcalar = decode_header(baslik)
    sonuc = []
    for parca, kodlama in parcalar:
        if isinstance(parca, bytes):
            try:
                sonuc.append(parca.decode(kodlama or "utf-8", errors="replace"))
            except Exception:
                sonuc.append(parca.decode("latin-1", errors="replace"))
        else:
            sonuc.append(str(parca))
    return " ".join(sonuc).strip()


def _govde_cez(msg) -> str:
    """Mail gövdesini (text/plain öncelikli) çıkarır."""
    govde = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                charset = part.get_content_charset() or "utf-8"
                try:
                    govde = part.get_payload(decode=True).decode(charset, errors="replace")
                    break
                except Exception:
                    continue
        # text/plain yoksa text/html'den düz metin çıkar
        if not govde:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    charset = part.get_content_charset() or "utf-8"
                    try:
                        html = part.get_payload(decode=True).decode(charset, errors="replace")
                        govde = re.sub(r"<[^>]+>", " ", html)
                        govde = re.sub(r"\s+", " ", govde).strip()
                        break
                    except Exception:
                        continue
    else:
        charset = msg.get_content_charset() or "utf-8"
        try:
            govde = msg.get_payload(decode=True).decode(charset, errors="replace")
        except Exception:
            govde = str(msg.get_payload())

    return govde.strip()


def _tarih_coz(msg) -> str:
    """Mail tarihini ISO formatına çevirir."""
    tarih_ham = msg.get("Date", "")
    try:
        return parsedate_to_datetime(tarih_ham).isoformat()
    except Exception:
        return datetime.now().isoformat()


# ---------------------------------------------------------------------------
# ANA FONKSİYON
# ---------------------------------------------------------------------------

def mailleri_cek(sadece_yeniler: bool = True) -> list[dict]:
    """
    Gmail Inbox'ından mailleri çeker.

    Args:
        sadece_yeniler: True → sadece UNSEEN mailler
                        False → tüm mailler (test için)

    Returns:
        [{"message_id", "gonderen", "konu", "govde", "tarih"}, ...]
    """
    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        raise ValueError(
            "Gmail kimlik bilgileri eksik!\n"
            "Proje klasörüne .env dosyası oluşturun:\n"
            "  GMAIL_USER=sizinmail@gmail.com\n"
            "  GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx"
        )

    print(f"[IMAP] {GMAIL_USER} → {IMAP_SUNUCU}:{IMAP_PORT} bağlanılıyor...")

    try:
        imap = imaplib.IMAP4_SSL(IMAP_SUNUCU, IMAP_PORT)
        imap.login(GMAIL_USER, GMAIL_APP_PASSWORD.replace(" ", ""))
    except imaplib.IMAP4.error as e:
        raise ConnectionError(
            f"Gmail bağlantısı başarısız: {e}\n"
            "Uygulama şifresini kontrol edin: https://myaccount.google.com/apppasswords"
        )

    imap.select(KLASOR)

    # Okunmamış veya tüm mailler
    kriter = "UNSEEN" if sadece_yeniler else "ALL"
    _, mesaj_idleri = imap.search(None, kriter)
    id_listesi = mesaj_idleri[0].split()

    if not id_listesi:
        print(f"[IMAP] {'Okunmamış' if sadece_yeniler else 'Toplam'} mail yok.")
        imap.logout()
        return []

    # En yeni MAX_MAIL kadar al
    id_listesi = id_listesi[-MAX_MAIL:]
    print(f"[IMAP] {len(id_listesi)} mail bulundu, işleniyor...")

    mailler = []
    for mid in id_listesi:
        try:
            _, veri = imap.fetch(mid, "(RFC822)")
            raw = veri[0][1]
            msg = email.message_from_bytes(raw)

            message_id = msg.get("Message-ID", f"local_{mid.decode()}").strip()
            gonderen   = _baslik_coz(msg.get("From", ""))
            konu       = _baslik_coz(msg.get("Subject", "(Konusuz)"))
            govde      = _govde_cez(msg)
            tarih      = _tarih_coz(msg)

            if not govde.strip():
                continue  # boş gövde, atla

            mailler.append({
                "message_id": message_id,
                "gonderen":   gonderen,
                "konu":       konu,
                "govde":      govde,
                "tarih":      tarih,
            })

        except Exception as e:
            print(f"  ⚠️  Mail {mid} okunamadı: {e}")
            continue

    imap.logout()
    print(f"[IMAP] {len(mailler)} mail başarıyla okundu.")
    return mailler


# ---------------------------------------------------------------------------
# TEST
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("Gmail bağlantı testi...\n")

    try:
        mailler = mailleri_cek(sadece_yeniler=True)
        print(f"\n✅ {len(mailler)} okunmamış mail çekildi\n")
        for m in mailler[:3]:
            print(f"  Gönderen : {m['gonderen']}")
            print(f"  Konu     : {m['konu']}")
            print(f"  Tarih    : {m['tarih']}")
            print(f"  Gövde    : {m['govde'][:80]}...")
            print()
    except (ValueError, ConnectionError) as e:
        print(f"\n❌ {e}")