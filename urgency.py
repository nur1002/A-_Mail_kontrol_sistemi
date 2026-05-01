"""
Aciliyet Skorlama (Urgency Scoring)
====================================

3 katmanlı hibrit skorlama:
  1. SENTIMENT (%50 ağırlık): Türkçe BERT sentiment modeli ile negatiflik
     yoğunluğu. Model: savasy/bert-base-turkish-sentiment-cased
  2. KURAL TABANLI (%35 ağırlık): Yasal/tehdit kelime tespiti.
     "tüketici hakem heyeti", "avukat", "savcılık" gibi ifadeler.
  3. STİL (%15 ağırlık): Büyük harf oranı, ünlem sayısı, bekleme süresi.

Çıktı: 1-5 arası tam sayı
  1 = bilgi sorusu / sakin
  2 = düşük öncelikli
  3 = orta öncelikli  
  4 = yüksek öncelikli (sinirli müşteri)
  5 = kritik (yasal tehdit / dolandırıcılık şüphesi)

Hız: ~150-300ms / mail (CPU'da, ilk çağrıda model yüklemesi hariç)

Neden böyle hibrit? Çünkü sadece sentiment yetmez:
- "Tüketici hakem heyetine başvuruyorum" çok sakin yazılabilir ama 
  aciliyet 5'tir. Sentiment bunu yakalayamaz, kural katmanı yakalar.
- "REZALET!!!" sentiment modelinde negatif çıkar ama 
  yasal tehdit yoktur, aciliyet 4 olmalı 5 değil.
"""

import re
from pathlib import Path

# transformers ilk import'ta yavaş (PyTorch'u yükler), lazy import yapalım
_sentiment_pipeline = None


def _sentiment_yukle():
    """Sentiment modelini ilk çağrıda yükler ve cache'ler."""
    global _sentiment_pipeline
    if _sentiment_pipeline is None:
        print("[urgency] Türkçe sentiment modeli yükleniyor (ilk çağrı)...")
        from transformers import pipeline
        _sentiment_pipeline = pipeline(
            "sentiment-analysis",
            model="savasy/bert-base-turkish-sentiment-cased",
            tokenizer="savasy/bert-base-turkish-sentiment-cased",
        )
        print("[urgency] Model hazır.\n")
    return _sentiment_pipeline


# ---------------------------------------------------------------------------
# KURAL TABANLI KELİME LİSTELERİ
# Ağırlıklı: hangi kelime hangi şiddet skorunu verir
# ---------------------------------------------------------------------------

# Çok güçlü sinyal (yasal süreç başlatma) → skor 1.0
YASAL_KELIMELER = [
    "tüketici hakem", "tüketici mahkemesi", "tuketici hakem",
    "savcılık", "savciya", "savci",
    "mahkeme", "dava açacağım", "dava acacagim",
    "avukat", "avukatım",
    "yasal işlem", "yasal yola", "hukuki süreç",
    "bddk", "spk", "kvkk",
    "dolandırıcılık", "dolandirici", "suç duyurusu",
    "şikayetvar", "sikayetvar",
]

# Güçlü sinyal (kararlı öfke) → skor 0.7
TEHDIT_KELIMELER = [
    "iptal ediyorum", "iptal istiyorum", "üyeliğimi iptal",
    "bir daha alışveriş yapmam", "bir daha asla",
    "sosyal medyada", "twitter'da paylaşacağım",
    "rezalet", "skandal", "kabul edilemez",
    "iade edin paramı", "param geri",
    "tehlikeli", "yangın çıkıyordu",
]

# Orta sinyal (sıkıntı / şikayet) → skor 0.4
RAHATSIZLIK_KELIMELER = [
    "şikayetçiyim", "sikayetciyim",
    "bıktım", "biktim", "bezdim",
    "yeter artık", "yeter artik",
    "yardım edin", "acil",
    "ne zaman", "hala", "hâlâ",
]


def kural_skoru(metin: str) -> float:
    """Kelime listelerine göre 0-1 arası kural skoru üretir."""
    metin_kucuk = metin.lower()
    
    # En güçlü sinyali esas al (toplam değil)
    if any(k in metin_kucuk for k in YASAL_KELIMELER):
        return 1.0
    if any(k in metin_kucuk for k in TEHDIT_KELIMELER):
        return 0.7
    if any(k in metin_kucuk for k in RAHATSIZLIK_KELIMELER):
        return 0.4
    return 0.0


# ---------------------------------------------------------------------------
# STİL SKORU
# ---------------------------------------------------------------------------

def stil_skoru(metin: str) -> float:
    """Büyük harf oranı + ünlem yoğunluğu + bekleme süresi sinyali."""
    if not metin.strip():
        return 0.0
    
    skor = 0.0
    
    # 1. Büyük harf oranı (Türkçe dahil)
    harfler = [c for c in metin if c.isalpha()]
    if harfler:
        buyuk_oran = sum(1 for c in harfler if c.isupper()) / len(harfler)
        if buyuk_oran > 0.3:  # %30+ büyük harf = bağırma
            skor += 0.5
        elif buyuk_oran > 0.15:
            skor += 0.2
    
    # 2. Ünlem işareti yoğunluğu
    unlem_sayisi = metin.count("!")
    if unlem_sayisi >= 3:
        skor += 0.4
    elif unlem_sayisi >= 1:
        skor += 0.15
    
    # 3. Bekleme süresi sinyali (X gündür / Y gün önce şeklinde sayılar)
    gun_match = re.search(r"(\d+)\s*g[üu]n", metin.lower())
    if gun_match:
        gun = int(gun_match.group(1))
        if gun >= 15:
            skor += 0.3
        elif gun >= 7:
            skor += 0.15
    
    return min(skor, 1.0)


# ---------------------------------------------------------------------------
# SENTIMENT SKORU
# ---------------------------------------------------------------------------

def sentiment_skoru(metin: str) -> float:
    """
    Türkçe BERT sentiment ile 0-1 arası kalibre edilmiş negatiflik skoru.
    
    NOT: savasy/bert-base-turkish-sentiment-cased modeli Twitter/ürün 
    yorumu verisiyle eğitildiği için müşteri hizmetleri maillerinde
    aşırı negatife meyilli. Şikayet maillerinin neredeyse tümünü %99
    olarak işaretliyor; bu yüzden ayrım sağlamıyor.
    
    Daha sıkı kalibrasyon: 0.95 baseline, [0.95, 1.00] → [0, 1].
    Yani sadece çok yoğun negatiflik 1.0'a yaklaşır, normal şikayet
    metinleri 0.2-0.6 aralığında kalır.
    """
    sp = _sentiment_yukle()
    metin_kisa = metin[:1500]
    sonuc = sp(metin_kisa)[0]
    
    label = sonuc["label"].lower()
    raw = sonuc["score"]
    
    if "neg" in label:
        # Sıkı kalibrasyon: 0.95 baseline, [0.95, 1.00] → [0, 1]
        kalibre = max(0.0, min(1.0, (raw - 0.95) / 0.05))
        return kalibre
    elif "pos" in label:
        return 0.0  # pozitif sinyal = aciliyet yok
    else:  # neutral
        return 0.05  # nötr = çok hafif sinyal


# ---------------------------------------------------------------------------
# BİRLEŞİK SKORLAYICI
# ---------------------------------------------------------------------------

AGIRLIKLAR = {
    "sentiment": 0.15,   # eskiden 0.30 — modelin aşırı negatife meyili nedeniyle azaltıldı
    "kural": 0.60,       # eskiden 0.50 — anahtar kelime sinyali daha güvenilir
    "stil": 0.25,        # eskiden 0.20 — yazım stili (caps, ünlem) önemli işaret
}


def aciliyet_hesapla(metin: str, sentiment_kullan: bool = True) -> dict:
    """
    Bir mail metninden aciliyet skoru hesaplar.
    
    Args:
        metin: Temizlenmiş mail metni
        sentiment_kullan: False ise sentiment atlanır (hızlı test için)
    
    Returns:
        {
            "skor": int (1-5),
            "ham_skor": float (0-1),
            "detay": {sentiment, kural, stil}
        }
    """
    detay = {
        "kural": kural_skoru(metin),
        "stil": stil_skoru(metin),
        "sentiment": sentiment_skoru(metin) if sentiment_kullan else None,
    }
    
    # OVERRIDE: Yasal kelime varsa (kural=1.0) sentiment'ten bağımsız kritik
    # "Avukatımla görüşeceğim" sakin yazılsa bile aciliyet 5'tir
    if detay["kural"] >= 1.0:
        return {
            "skor": 5,
            "ham_skor": 1.0,
            "detay": detay,
            "not": "Yasal tehdit tespit edildi (override)",
        }
    
    # Ağırlıklı toplam (0-1)
    if sentiment_kullan:
        ham = (
            AGIRLIKLAR["sentiment"] * detay["sentiment"]
            + AGIRLIKLAR["kural"] * detay["kural"]
            + AGIRLIKLAR["stil"] * detay["stil"]
        )
    else:
        # Sentiment yoksa kalan iki katmanı yeniden ağırlıklandır
        kalan = AGIRLIKLAR["kural"] + AGIRLIKLAR["stil"]
        ham = (
            (AGIRLIKLAR["kural"] / kalan) * detay["kural"]
            + (AGIRLIKLAR["stil"] / kalan) * detay["stil"]
        )
    
    # 1-5 mapping
    if ham < 0.20:
        skor = 1
    elif ham < 0.40:
        skor = 2
    elif ham < 0.55:
        skor = 3
    elif ham < 0.75:
        skor = 4
    else:
        skor = 5
    
    return {"skor": skor, "ham_skor": round(ham, 3), "detay": detay}


# ---------------------------------------------------------------------------
# DEMO / DEĞERLENDİRME
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import csv
    
    # Önce kural ve stil katmanlarını sentiment olmadan test et (hızlı)
    print("=" * 70)
    print("KURAL + STİL KATMANI TESTİ (sentiment KAPALI)")
    print("=" * 70)
    
    test_metinleri = [
        ("Merhaba, sipariş durumumu öğrenebilir miyim?", 1),
        ("Sipariş 3 gündür gelmedi, ne zaman gelir?", 2),
        ("İade ettiğim ürünün parası 10 gündür gelmedi.", 3),
        ("REZALET! 15 GÜNDÜR siparişim gelmedi!!!", 4),
        ("Tüketici hakem heyetine başvuruyorum, avukatım hazırlanıyor.", 5),
    ]
    
    for metin, beklenen in test_metinleri:
        sonuc = aciliyet_hesapla(metin, sentiment_kullan=False)
        durum = "✓" if sonuc["skor"] == beklenen else "✗"
        print(f"\n{durum} '{metin[:60]}...' " if len(metin) > 60 else f"\n{durum} '{metin}'")
        print(f"  Beklenen: {beklenen} | Tahmin: {sonuc['skor']} (ham: {sonuc['ham_skor']})")
        print(f"  Detay: kural={sonuc['detay']['kural']:.2f}  stil={sonuc['detay']['stil']:.2f}")
    
    # Şimdi sentiment'le birlikte tam test
    print("\n" + "=" * 70)
    print("TAM PIPELINE TESTİ (sentiment AÇIK - model indirilecek)")
    print("=" * 70)
    
    print("\nTüm 3 katman aktif - örnekler:\n")
    for metin, beklenen in test_metinleri:
        sonuc = aciliyet_hesapla(metin, sentiment_kullan=True)
        durum = "✓" if sonuc["skor"] == beklenen else "✗"
        print(f"{durum} [{beklenen}→{sonuc['skor']}] {metin[:55]}")
        print(f"   sentiment={sonuc['detay']['sentiment']:.2f}  "
              f"kural={sonuc['detay']['kural']:.2f}  "
              f"stil={sonuc['detay']['stil']:.2f}  "
              f"→ ham={sonuc['ham_skor']}")
    
    # CSV ile değerlendirme (eğer dosya varsa)
    csv_yolu = Path(__file__).parent / "synthetic_complaints.csv"
    if csv_yolu.exists():
        from collections import defaultdict
        from preprocessing import temizle_mail
        
        print("\n" + "=" * 70)
        print("CSV DEĞERLENDİRMESİ (TÜM 500 ÖRNEK, preprocessing + konu dahil)")
        print("=" * 70)
        
        with open(csv_yolu, encoding="utf-8") as f:
            okuyucu = csv.DictReader(f)
            satirlar = list(okuyucu)
        
        toplam_hata = 0
        tam_isabet = 0
        bir_sapma = 0
        
        # Per-seviye istatistik
        seviye_stats = defaultdict(lambda: {"toplam": 0, "isabet": 0, "hata_top": 0})
        
        # Confusion matrix tarzı
        cm = defaultdict(lambda: defaultdict(int))
        
        for satir in satirlar:
            gercek = int(satir["aciliyet"])
            # ÖNEMLİ: konu + temizlenmiş gövde birlikte sentiment'e gidiyor
            metin = satir["konu"] + ". " + temizle_mail(satir["govde"])
            tahmin = aciliyet_hesapla(metin)["skor"]
            
            hata = abs(gercek - tahmin)
            toplam_hata += hata
            if hata == 0:
                tam_isabet += 1
            if hata <= 1:
                bir_sapma += 1
            
            seviye_stats[gercek]["toplam"] += 1
            seviye_stats[gercek]["hata_top"] += hata
            if gercek == tahmin:
                seviye_stats[gercek]["isabet"] += 1
            
            cm[gercek][tahmin] += 1
        
        n = len(satirlar)
        mae = toplam_hata / n
        
        print(f"\nGenel sonuçlar (n={n}):")
        print(f"  Tam isabet:        %{tam_isabet/n*100:.1f}")
        print(f"  ±1 sapma içinde:   %{bir_sapma/n*100:.1f}  ← üretim için bu metrik önemli")
        print(f"  MAE:               {mae:.2f} / 5")
        
        print(f"\nHer seviye için isabet:")
        for sv in sorted(seviye_stats.keys()):
            s = seviye_stats[sv]
            isabet_pct = s["isabet"] / s["toplam"] * 100
            ort_hata = s["hata_top"] / s["toplam"]
            print(f"  Seviye {sv}: {isabet_pct:5.1f}% isabet  "
                  f"(MAE: {ort_hata:.2f})  [{s['isabet']}/{s['toplam']}]")
        
        print(f"\nConfusion Matrix (satır=gerçek, sütun=tahmin):")
        print("       " + "".join(f"  T{t} " for t in range(1, 6)))
        for g in range(1, 6):
            satir_str = f"  G{g}: "
            for t in range(1, 6):
                deger = cm[g][t]
                if g == t:
                    satir_str += f" \033[1m{deger:3d}\033[0m "  # bold diagonal
                else:
                    satir_str += f" {deger:3d} "
            print(satir_str)