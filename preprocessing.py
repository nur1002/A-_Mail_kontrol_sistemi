"""
Mail Ön İşleme (Preprocessing)
==============================

Görev: Ham mail içeriğinden classifier'ı yanıltacak gürültüyü temizlemek.

Temizlenenler:
- Quote blokları ("----- Önceki Mesaj -----", "> X şunu yazdı:" ve sonrası)
- İmzalar ("Saygılarımla,", "Teşekkürler," ve sonrası)
- Telefon/mail satırları (signature kalıntısı)
- Fazla boşluklar

Neden önemli? Signature/quote içindeki kelimeler classifier'ı yanlış 
yönlendirebilir. Örn: fatura şikayetine eski bir kargo yazışması quote 
olarak yapışmışsa, model "kargo" diye etiketleyebilir.
"""


import re
# --- YENİ EKLENENLER ---
import email
from email import policy

# Türkçe imza başlangıç ifadeleri (büyük harfle başlayan)
SIGNATURE_BASLANGIC = [
    "Saygılarımla",
    "İyi günler dilerim",
    "İyi çalışmalar",
    "Kolay gelsin",
    "Teşekkürler",
    "Teşekkür ederim",
    "Sevgiler",
]

# Quote bloğu ayraçları (mail thread'lerde alıntılanmış eski mesajlar)
QUOTE_AYRAC_PATTERNS = [
    r"-{3,}\s*Önceki Mesaj\s*-{3,}",
    r"-{3,}\s*Original Message\s*-{3,}",
    r"-{3,}\s*Forwarded Message\s*-{3,}",
    r"\n>.*?(şunu\s+yazdı|wrote)\s*:",  # "> Ali şunu yazdı:" gibi
    r"\nOn\s+.{1,40}\s+wrote:",  # İngilizce mail clientları
]

def ham_maili_ayristir(ham_metin: str) -> dict:
    """
    Kullanıcının web arayüzüne yapıştırdığı ham (raw) mail verisini
    parçalara ayırır (Konu, Gönderen, Gövde).
    """
    try:
        # Mail metnini standartlara göre oku
        msg = email.message_from_string(ham_metin, policy=policy.default)
        
        konu = msg.get('Subject', '')
        gonderen = msg.get('From', '')
        
        # Mail gövdesini (Body) bulma
        govde = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    govde = part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8')
                    break
        else:
            govde = msg.get_content()
            
        return {
            "konu": konu.strip(),
            "gonderen": gonderen.strip(),
            "govde": govde.strip()
        }
    except Exception as e:
        # Eğer format mail formatı değilse, tüm metni gövde kabul et
        return {"konu": "", "gonderen": "", "govde": ham_metin}
    

def temizle_mail(govde: str) -> str:
    """Ham mail gövdesini classifier için temiz metne çevirir."""
    if not isinstance(govde, str) or not govde.strip():
        return ""

    metin = govde

    # 1. Quote ayraçları ve sonrasını sil
    for pattern in QUOTE_AYRAC_PATTERNS:
        metin = re.split(pattern, metin, maxsplit=1, flags=re.IGNORECASE)[0]

    # 2. > ile başlayan satırları sil (alıntı satırları)
    metin = "\n".join(
        satir for satir in metin.split("\n") if not satir.lstrip().startswith(">")
    )

    # 3. İmza bloğunu kaldır
    # En erken eşleşen imza başlangıcını bul, ondan itibaren kes
    en_erken_konum = len(metin)
    for sig in SIGNATURE_BASLANGIC:
        # Yeni satırla başlayan, virgüllü/virgülsüz hali yakala
        pattern = rf"\n+\s*{re.escape(sig)}[,\.]?\s*\n"
        match = re.search(pattern, metin)
        if match and match.start() < en_erken_konum:
            en_erken_konum = match.start()

    # "--" ile başlayan klasik imza ayracı
    sig_dash_match = re.search(r"\n--\s*\n", metin)
    if sig_dash_match and sig_dash_match.start() < en_erken_konum:
        en_erken_konum = sig_dash_match.start()

    metin = metin[:en_erken_konum]

    # 4. Telefon ve mail satırı kalıntıları (signature dışındakiler için)
    metin = re.sub(r"\n.*\bTel\s*:\s*\d.*", "", metin, flags=re.IGNORECASE)
    metin = re.sub(r"\n.*\b[\w.+-]+@[\w-]+\.[\w.-]+\b.*", "", metin)

    # 5. Boşluk normalizasyonu
    metin = re.sub(r"\n{2,}", "\n", metin)
    metin = re.sub(r"[ \t]+", " ", metin)
    metin = metin.strip()

    return metin


# ---------------------------------------------------------------------------
# DEMO / TEST
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    test_ornekleri = [
        # Örnek 1: Sadece imza
        """valiz siparişim için kargo görevlisi geldi, ben evdeyken zile bile basmadan 'adres bulunamadı' yazıp gitmiş. Bu üçüncü sefer oluyor. Kargo şirketinizi değiştirin artık!

İyi günler dilerim,
Elif Arslan""",

        # Örnek 2: İmza + alakasız quote (önceki turdaki kafa karıştıran örnek)
        """Sipariş tutarım 4750 TL idi ama faturada farklı bir tutar görünüyor. Kontrol eder misiniz? SP589887

Teşekkürler,
Hatice Özdemir
Tel: 05478463259

> Hatice Özdemir şunu yazdı:
> Siparişim hala elime ulaşmadı, lütfen yardımcı olun.""",

        # Örnek 3: -- ayraçlı imza
        """Aldığım telefon hiç açılmıyor, bozuk gelmiş galiba. Sipariş: SP123456

--
Mehmet Demir""",

        # Örnek 4: Önceki mesaj bloğu
        """Param hala iade edilmedi, 20 gün oldu. Lütfen acil müdahale.

----- Önceki Mesaj -----
Merhaba, talebiniz incelenmektedir.""",
    ]

    for i, ornek in enumerate(test_ornekleri, 1):
        print(f"\n{'='*70}\nÖRNEK {i} - HAM:\n{'='*70}")
        print(repr(ornek))
        print(f"\n{'─'*70}\nTEMİZLENMİŞ:\n{'─'*70}")
        print(repr(temizle_mail(ornek)))
        print()