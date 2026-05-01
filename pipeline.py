"""
Pipeline (Agent Mantığı) v2
============================

3 AI bileşenini sırayla çalıştırıp birleşik bir karar üretir:
  1. Mail temizleme (preprocessing)
  2. Kategori tahmini (classifier — TF-IDF + LogReg)
  3. Aciliyet skorlaması (urgency — BERT sentiment + kural + stil)
  4. Benzer şikayet bulma (similarity — embeddings + ChromaDB)
  5. Aksiyon önerisi (kural tabanlı, AI çıktılarına göre)

Değişiklikler v2'de:
  - Analiz sonuçları daha açıklayıcı ve okunabilir
  - Hata mesajları kullanıcıya ne yapması gerektiğini söylüyor
  - Yeni: aciliyet_aciklamasi, kategori_aciklamasi, ozet alanları
  - Yeni: _neden_bu_kategori() ile tahmin gerekçesi
"""

from classifier import tahmin_et as kategori_tahmin
from urgency import aciliyet_hesapla
from similarity import benzer_bul
from preprocessing import temizle_mail, ham_maili_ayristir

# Güven eşiği — bunun altında "düşük güven" uyarısı verilir
GUVEN_ESIGI = 0.65

# Aksiyon şablonları (kategori bazlı)
DEPARTMAN_MAP = {
    "kargo": "Kargo & Lojistik Departmanı",
    "iade": "İade & Değişim Departmanı",
    "teknik": "Teknik Destek Departmanı",
    "fatura": "Ödeme & Muhasebe Departmanı",
}

ACILIYET_AÇIKLAMALARI = {
    1: "Bilgi talebi veya sakin şikayet. Standart süreç yeterli.",
    2: "Hafif memnuniyetsizlik. Normal iş akışında yanıtlanabilir.",
    3: "Orta şiddetli şikayet. Aynı gün yanıt verilmeli.",
    4: "Sinirli / kararlı müşteri. Öncelikli ilgilenilmeli.",
    5: "Kritik durum. Yasal süreç riski veya güvenlik sorunu. Anında müdahale!",
}

KATEGORI_AÇIKLAMALARI = {
    "kargo": "Sipariş takibi, teslimat gecikmesi veya kayıp kargo",
    "iade": "Ürün iadesi, değişim talebi veya para iadesi",
    "teknik": "Ürün arızası, eksik parça veya garanti",
    "fatura": "Hatalı ücretlendirme, çifte çekim veya fatura sorunu",
}


# ---------------------------------------------------------------------------
# ANA FONKSİYON
# ---------------------------------------------------------------------------

def analiz_et(konu: str, govde: str, k_benzer: int = 3) -> dict:
    """
    Bir mail için tam analiz çalıştırır.

    Returns: {
        "konu": str,
        "govde_temiz": str,
        "kategori": str,
        "kategori_aciklamasi": str,
        "kategori_guven": float,
        "kategori_guven_yuzdesi": str,
        "kategori_guven_yorumu": str,
        "kategori_skorlari": dict,
        "aciliyet": int (1-5),
        "aciliyet_etiketi": str,
        "aciliyet_aciklamasi": str,
        "aciliyet_ham": float,
        "aciliyet_detay": dict,
        "benzer_sikayetler": list,
        "benzer_sikayet_sayisi": int,
        "onerilen_aksiyon": str,
        "departman": str,
        "uyari": str | None,
        "ozet": str,       ← Tek cümle özet
    }
    """
    # 1. Temizleme
    govde_temiz = temizle_mail(govde)
    if not govde_temiz:
        return {
            "hata": "Mail içeriği boş veya okunamadı",
            "hata_aciklamasi": "Gövde metni temizlendikten sonra içerik kalmadı. Mail sadece imza veya alıntı içeriyor olabilir.",
            "cozum": "Daha uzun ve açıklayıcı bir mail içeriği gönderin.",
            "konu": konu,
        }

    # Konu + temiz gövde — tüm modüllere bu birleşik metin gidiyor
    metin = (konu + ". " + govde_temiz).strip()

    # 2. Kategori
    try:
        kat = kategori_tahmin(metin)
    except FileNotFoundError:
        return {
            "hata": "Sınıflandırıcı modeli bulunamadı",
            "cozum": "Terminal'de 'python classifier.py' komutunu çalıştırarak modeli eğitin.",
            "konu": konu,
        }
    except Exception as e:
        return {
            "hata": "Kategori tahmini başarısız",
            "detay": str(e),
            "cozum": "Loglara bakın ve 'python classifier.py' ile modeli yeniden eğitin.",
            "konu": konu,
        }

    # 3. Aciliyet
    try:
        aci = aciliyet_hesapla(metin)
    except Exception as e:
        # Aciliyet başarısız olsa bile kalan analizi sürdür
        aci = {"skor": 2, "ham_skor": 0.2, "detay": {"kural": 0, "stil": 0, "sentiment": None}}

    # 4. Benzerler
    try:
        benzerler = benzer_bul(metin, k=k_benzer)
    except RuntimeError:
        benzerler = []  # ChromaDB boşsa sessizce devam et
    except Exception:
        benzerler = []

    # 5. Aksiyon ve uyarı
    aksiyon, uyari = _aksiyon_oner(kat, aci)
    departman = DEPARTMAN_MAP.get(kat["kategori"], kat["kategori"].title())

    # 6. Güven yorumu
    guven = kat["guven"]
    if guven >= 0.85:
        guven_yorumu = "Yüksek güven — kategori neredeyse kesin"
    elif guven >= 0.65:
        guven_yorumu = "Orta güven — büyük olasılıkla doğru"
    else:
        guven_yorumu = "Düşük güven — manuel doğrulama önerilir"

    # 7. Kısa özet
    ozet = _ozet_olustur(kat["kategori"], aci["skor"], guven)

    return {
        "konu": konu,
        "govde_temiz": govde_temiz,
        # Kategori
        "kategori": kat["kategori"],
        "kategori_aciklamasi": KATEGORI_AÇIKLAMALARI.get(kat["kategori"], ""),
        "kategori_guven": round(guven, 3),
        "kategori_guven_yuzdesi": f"%{guven * 100:.0f}",
        "kategori_guven_yorumu": guven_yorumu,
        "kategori_skorlari": {k: round(v, 3) for k, v in kat["tum_skorlar"].items()},
        # Aciliyet
        "aciliyet": aci["skor"],
        "aciliyet_etiketi": _aciliyet_etiketi(aci["skor"]),
        "aciliyet_aciklamasi": ACILIYET_AÇIKLAMALARI.get(aci["skor"], ""),
        "aciliyet_ham": aci["ham_skor"],
        "aciliyet_detay": aci["detay"],
        # Benzerler
        "benzer_sikayetler": benzerler,
        "benzer_sikayet_sayisi": len(benzerler),
        # Karar
        "onerilen_aksiyon": aksiyon,
        "departman": departman,
        "uyari": uyari,
        # Özet
        "ozet": ozet,
    }


def tam_surec_analiz(ham_mail_metni: str) -> dict:
    """
    Ham (raw) mail metnini alır, parçalara ayırır, analiz eder.

    Ham mail örneği:
        From: musteri@mail.com
        Subject: Siparişim gelmedi
        
        Merhaba, 10 gündür siparişimi bekliyorum...
    """
    if not ham_mail_metni or not ham_mail_metni.strip():
        return {
            "hata": "Mail içeriği boş",
            "cozum": "Analiz edilecek mail metnini gönderin.",
        }

    if len(ham_mail_metni.strip()) < 10:
        return {
            "hata": "Mail içeriği çok kısa",
            "cozum": "En az 10 karakter içeren bir mail metni gönderin.",
        }

    # Maili parçalarına ayır
    mail_datalari = ham_maili_ayristir(ham_mail_metni)

    # Analiz
    analiz_sonucu = analiz_et(mail_datalari["konu"], mail_datalari["govde"])

    # Ham mail verilerini ekle
    analiz_sonucu["orijinal_konu"] = mail_datalari["konu"]
    analiz_sonucu["gonderen"] = mail_datalari["gonderen"]

    return analiz_sonucu


# ---------------------------------------------------------------------------
# YARDIMCI FONKSİYONLAR
# ---------------------------------------------------------------------------

def _aciliyet_etiketi(skor: int) -> str:
    etiketler = {
        1: "🟢 Düşük (1/5)",
        2: "🔵 Normal (2/5)",
        3: "🟡 Orta (3/5)",
        4: "🟠 Yüksek (4/5)",
        5: "🔴 Kritik (5/5)",
    }
    return etiketler.get(skor, str(skor))


def _ozet_olustur(kategori: str, aciliyet: int, guven: float) -> str:
    """İnsan okunabilir tek cümle özet."""
    kat_tr = {
        "kargo": "kargo/teslimat",
        "iade": "iade/değişim",
        "teknik": "teknik arıza",
        "fatura": "fatura/ödeme",
    }.get(kategori, kategori)

    aciliyet_tr = {
        1: "düşük öncelikli",
        2: "normal öncelikli",
        3: "orta öncelikli",
        4: "yüksek öncelikli",
        5: "kritik",
    }.get(aciliyet, "")

    guven_tr = "yüksek güvenle" if guven >= 0.85 else "orta güvenle" if guven >= 0.65 else "düşük güvenle"

    return f"Bu şikayet {guven_tr} '{kat_tr}' kategorisinde sınıflandırıldı ve {aciliyet_tr} ({aciliyet}/5) olarak değerlendirildi."


def _aksiyon_oner(kat: dict, aci: dict) -> tuple:
    """
    AI çıktılarına göre aksiyon önerisi + opsiyonel uyarı.
    Kural tabanlı: iş mantığı, AI değil.
    """
    skor = aci["skor"]
    kategori = kat["kategori"]
    guven = kat["guven"]
    departman = DEPARTMAN_MAP.get(kategori, kategori.title())

    # Yasal tehdit (en yüksek öncelik)
    yasal_not = aci.get("not", "")
    if "Yasal tehdit" in yasal_not:
        return (
            f"🚨 KRİTİK ESKALASYON — Yasal tehdit tespit edildi!\n"
            f"• Hukuk birimine DERHAL bildir\n"
            f"• Müşteri hizmetleri yöneticisini bilgilendir\n"
            f"• 1 saat içinde resmi ve belgelenmiş dönüş yapılmalı\n"
            f"• İlgili departman: {departman}",
            "⚖️ Yasal süreç riski — acil müdahale gerekiyor",
        )

    # Aciliyet 5 (kritik, yasal değil)
    if skor == 5:
        return (
            f"⚠️ ACİL ESKALASYON — Üst yönetim devreye alınmalı!\n"
            f"• {departman} yöneticisine eskalasyon yapın\n"
            f"• 2 saat içinde müşteriye dönüş sağlayın\n"
            f"• Durumu kayıt altına alın",
            "🔴 Yüksek müşteri kaybı riski",
        )

    # Aciliyet 4
    if skor == 4:
        return (
            f"⏰ ÖNCELİKLİ — Aynı iş günü içinde yanıt verilmeli\n"
            f"• {departman}'na yönlendirin\n"
            f"• Müşteriye gün içinde geri dönüş yapıldığını bildirin",
            None,
        )

    # Aciliyet 3
    if skor == 3:
        return (
            f"📋 ORTA ÖNCELİK — 24 saat içinde yanıt verilmeli\n"
            f"• {departman}'na yönlendirin",
            None,
        )

    # Düşük güvenli kategori (önce bunu yakala)
    if guven < GUVEN_ESIGI:
        return (
            f"🔍 MANUEL İNCELEME GEREKLİ\n"
            f"• Kategori tahmini düşük güvenli (%{guven * 100:.0f})\n"
            f"• Önce kategoriyi doğrulayın, ardından ilgili departmana yönlendirin\n"
            f"• Olası kategori: {kategori} → {departman}",
            f"⚠️ Düşük güven (%{guven * 100:.0f}) — otomatik yönlendirme riskli",
        )

    # Standart (aciliyet 1-2)
    return (
        f"✅ STANDART İŞLEM — Normal süreç yeterli\n"
        f"• {departman}'na yönlendirin\n"
        f"• Aciliyet: {skor}/5",
        None,
    )


# ---------------------------------------------------------------------------
# DEMO
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_mailleri = [
        {
            "konu": "Sipariş gelmedi",
            "govde": "Merhaba, 10 gün önce verdiğim sipariş hala elime ulaşmadı. "
                     "Kargo takip sisteminde de güncelleme yok. Lütfen bilgi.\n\n"
                     "Saygılarımla,\nAhmet"
        },
        {
            "konu": "TÜKETİCİ HAKEM HEYETİ",
            "govde": "Bir aydır iadem yapılmıyor. Avukatımla görüştüm, "
                     "tüketici hakem heyetine başvuruyorum. Bu son uyarımdır."
        },
        {
            "konu": "Beden değişimi",
            "govde": "Aldığım kazak büyük geldi, bir beden küçüğüyle değişim "
                     "yapabilir miyim?"
        },
    ]

    for i, mail in enumerate(test_mailleri, 1):
        print(f"\n{'='*70}")
        print(f"MAIL #{i}: {mail['konu']}")
        print(f"{'='*70}")

        sonuc = analiz_et(mail["konu"], mail["govde"])

        if "hata" in sonuc:
            print(f"❌ Hata: {sonuc['hata']}")
            print(f"   Çözüm: {sonuc.get('cozum', '-')}")
            continue

        print(f"\n{sonuc['ozet']}")
        print(f"\n📋 Kategori:  {sonuc['kategori'].upper()}  — {sonuc['kategori_aciklamasi']}")
        print(f"   Güven:    {sonuc['kategori_guven_yuzdesi']} ({sonuc['kategori_guven_yorumu']})")
        print(f"\n⚡ Aciliyet:  {sonuc['aciliyet_etiketi']}")
        print(f"   Açıklama: {sonuc['aciliyet_aciklamasi']}")
        print(f"\n🏢 Departman: {sonuc['departman']}")
        print(f"\n💼 Aksiyon:\n{sonuc['onerilen_aksiyon']}")
        if sonuc["uyari"]:
            print(f"\n⚠️  Uyarı: {sonuc['uyari']}")
        if sonuc["benzer_sikayetler"]:
            b = sonuc["benzer_sikayetler"][0]
            print(f"\n🔍 En benzer şikayet: [{b['kategori']}] '{b['konu']}' (benzerlik: {b['benzerlik']})")