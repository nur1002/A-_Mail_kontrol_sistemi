"""
Veri Seti Adaptörü
==================

Dışarıdan gelen Türkçe şikayet veri setlerini projenin
synthetic_complaints.csv formatına dönüştürür ve birleştirir.

Kullanım:
  # Turkish Complaint Image Dataset v3 ile birleştir:
  python dataset_adapter.py \
      --kaynak csv \
      --dosya Turkish_Complaint_Image_Datset_v3_with_ids.csv \
      --birlestir data/synthetic_complaints.csv \
      --cikti data/merged.csv

  # Hugging Face veri seti:
  python dataset_adapter.py --kaynak hf --hf_repo "kullanici/repo" --cikti data/merged.csv

Çıktı formatı (synthetic_complaints.csv ile aynı):
  mail_id, gonderen_ad, konu, govde, kategori, aciliyet, tarih
"""

import argparse
import csv
import json
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter

# ---------------------------------------------------------------------------
# PROJE KATEGORİLERİ
# ---------------------------------------------------------------------------

GECERLI_KATEGORILER = {"kargo", "iade", "teknik", "fatura"}

KATEGORI_ACIKLAMALARI = {
    "kargo": "Sipariş takibi, teslimat gecikmesi veya kayıp kargo",
    "iade":  "Ürün iadesi, değişim talebi veya para iadesi",
    "teknik":"Ürün arızası, eksik parça, kalite sorunu veya garanti",
    "fatura":"Hatalı ücretlendirme, çifte çekim veya fatura sorunu",
}

# ---------------------------------------------------------------------------
# TURKISH COMPLAINT IMAGE DATASET v3 KATEGORİ EŞLEMESİ
# ---------------------------------------------------------------------------
# Kaynak veri ürün kalitesi / e-ticaret şikayetleri içeriyor.
# Projenin 4 kategorisine en uygun eşleme:

COMPLAINT_IMAGE_V3_MAP = {
    # Ürün bozuk / arızalı / kalite sorunu → teknik
    "Faulty":      "teknik",   # Yanmış, kırık, çalışmıyor
    "Inferior":    "teknik",   # Kalitesiz, ucuz malzeme
    "Discolored":  "teknik",   # Renk bozukluğu, leke
    "Refurbished": "teknik",   # Yenilenmiş/kullanılmış ürün yeni diye satılmış
    "Incomplete":  "teknik",   # Eksik parça / aksesuar

    # Yanlış / uyumsuz / beklenti karşılanmadı → iade
    "Ill-fitting":   "iade",   # Beden/boyut uyumsuzluğu
    "Incorrect":     "iade",   # Yanlış ürün/renk/model gönderilmiş
    "Disappointing": "iade",   # Fotoğrafla uyuşmuyor, beklenti altında
}

# ---------------------------------------------------------------------------
# ACİLİYET TAHMİN KURALLARI
# ---------------------------------------------------------------------------

ACILIYET_KURALLARI = {
    5: ["avukat", "mahkeme", "savcı", "tüketici hakem", "bddk",
        "dolandırıcılık", "suç duyurusu", "yasal", "helal etmiyorum"],
    4: ["rezalet", "skandal", "kabul edilemez", "bir daha asla",
        "sosyal medya", "tehlikeli", "yangın", "çocuğum", "kandırır gibi"],
    3: ["hala", "hâlâ", "acil", "bıktım", "ne zaman",
        "kaç gündür", "iade ediyorum", "neden böyle"],
    2: ["memnun değilim", "sorun var", "geç kaldı", "yanlış",
        "beden uymadı", "fotoğraf gibi değil", "leke", "eksik"],
}

ZORUNLU_SUTUNLAR = ["mail_id", "gonderen_ad", "konu", "govde",
                    "kategori", "aciliyet", "tarih"]

# ---------------------------------------------------------------------------
# YARDIMCI FONKSİYONLAR
# ---------------------------------------------------------------------------

def aciliyet_tahmin_et(metin: str) -> int:
    metin_kucuk = metin.lower()
    for skor in [5, 4, 3, 2]:
        if any(k in metin_kucuk for k in ACILIYET_KURALLARI[skor]):
            return skor
    return 1


def kategori_tahmin_et(ham_kategori: str, kategori_map: dict = None):
    # 1. Kullanıcı tanımlı map
    if kategori_map and ham_kategori in kategori_map:
        return kategori_map[ham_kategori]

    # 2. Yerleşik v3 map
    if ham_kategori in COMPLAINT_IMAGE_V3_MAP:
        return COMPLAINT_IMAGE_V3_MAP[ham_kategori]

    # 3. Otomatik küçük harf kısmi eşleme
    temiz = ham_kategori.lower().strip()
    oto_map = {
        "kargo": "kargo", "cargo": "kargo", "teslimat": "kargo",
        "delivery": "kargo", "shipping": "kargo",
        "iade": "iade", "return": "iade", "refund": "iade",
        "exchange": "iade", "degisim": "iade",
        "teknik": "teknik", "faulty": "teknik", "ariza": "teknik",
        "bozuk": "teknik", "garanti": "teknik",
        "fatura": "fatura", "invoice": "fatura", "odeme": "fatura",
    }
    for anahtar, hedef in oto_map.items():
        if anahtar in temiz:
            return hedef

    return None


def rastgele_tarih() -> str:
    gun = random.randint(0, 60)
    return (datetime.now() - timedelta(days=gun)).strftime("%Y-%m-%d %H:%M")


def konu_uret(metin: str, kategori: str) -> str:
    konu_prefixler = {
        "teknik": ["Ürün sorunu:", "Kalite şikayeti:", "Arıza:", "Hatalı ürün:"],
        "iade":   ["İade talebi:", "Yanlış ürün:", "Değişim:", "Beden sorunu:"],
        "kargo":  ["Kargo sorunu:", "Teslimat:", "Sipariş:"],
        "fatura": ["Fatura hatası:", "Ödeme sorunu:"],
    }
    prefix = random.choice(konu_prefixler.get(kategori, ["Şikayet:"]))
    kisaltilmis = metin[:50].rstrip()
    if len(metin) > 50:
        kisaltilmis += "..."
    return f"{prefix} {kisaltilmis}"


def satiri_dogrula(satir: dict, indeks: int):
    if not satir.get("govde", "").strip():
        return False, f"Satır {indeks}: 'govde' boş"
    if satir.get("kategori") not in GECERLI_KATEGORILER:
        return False, f"Satır {indeks}: Geçersiz kategori '{satir.get('kategori')}'"
    aciliyet = satir.get("aciliyet")
    if not isinstance(aciliyet, int) or not (1 <= aciliyet <= 5):
        return False, f"Satır {indeks}: Geçersiz aciliyet '{aciliyet}'"
    return True, ""


# ---------------------------------------------------------------------------
# KAYNAK OKUYUCULAR
# ---------------------------------------------------------------------------

def csv_oku(dosya_yolu: str, metin_sutun: str = None, kategori_sutun: str = None,
            konu_sutun: str = None, kategori_map: dict = None) -> list:
    yol = Path(dosya_yolu)
    if not yol.exists():
        print(f"HATA: Dosya bulunamadı: {dosya_yolu}")
        sys.exit(1)

    print(f"[CSV] '{dosya_yolu}' okunuyor...")
    with open(yol, encoding="utf-8", errors="replace") as f:
        satirlar = list(csv.DictReader(f))

    if not satirlar:
        print("HATA: CSV dosyası boş.")
        sys.exit(1)

    sutunlar = list(satirlar[0].keys())
    print(f"  {len(satirlar)} satır | Sütunlar: {sutunlar}")

    metin_sutun = metin_sutun or _sutun_bul(sutunlar,
        ["complaint_text_tr", "text", "metin", "govde", "body",
         "content", "complaint", "sikayet", "yorum", "review"])
    kategori_sutun = kategori_sutun or _sutun_bul(sutunlar,
        ["complaint_type", "label", "category", "kategori",
         "class", "sinif", "tip", "type"])
    konu_sutun = konu_sutun or _sutun_bul(sutunlar,
        ["subject", "konu", "baslik", "title"])

    if not metin_sutun:
        print(f"HATA: Metin sütunu bulunamadı. Sütunlar: {sutunlar}")
        sys.exit(1)

    print(f"  Metin → '{metin_sutun}' | Kategori → '{kategori_sutun}' | "
          f"Konu → '{konu_sutun or '-'}'")

    donusturulen = []
    eslesmeyen = Counter()

    for i, satir in enumerate(satirlar):
        metin = satir.get(metin_sutun, "").strip()
        if not metin or len(metin) < 5:
            continue

        ham_kat = satir.get(kategori_sutun, "").strip() if kategori_sutun else ""
        kategori = kategori_tahmin_et(ham_kat, kategori_map) if ham_kat else None

        if not kategori:
            eslesmeyen[ham_kat] += 1
            continue

        konu = (satir.get(konu_sutun, "").strip()
                if konu_sutun and satir.get(konu_sutun, "").strip()
                else konu_uret(metin, kategori))

        aciliyet_ham = satir.get("aciliyet",
                       satir.get("urgency", satir.get("priority", "")))
        try:
            aciliyet = max(1, min(5, int(aciliyet_ham)))
        except (ValueError, TypeError):
            aciliyet = aciliyet_tahmin_et(metin)

        donusturulen.append({
            "gonderen_ad": satir.get("gonderen_ad",
                           satir.get("sender", satir.get("author", "Anonim"))),
            "konu":        konu,
            "govde":       metin,
            "kategori":    kategori,
            "aciliyet":    aciliyet,
            "tarih":       satir.get("tarih", satir.get("date", rastgele_tarih())),
        })

    if eslesmeyen:
        print(f"\n  ⚠️  Eşleşmeyen kategoriler (atlandı):")
        for kat, sayi in sorted(eslesmeyen.items(), key=lambda x: -x[1]):
            print(f"     '{kat}': {sayi} satır — --kategori_map ile eşleyebilirsiniz")

    print(f"\n  ✓ Dönüştürülen: {len(donusturulen)} / {len(satirlar)} kayıt")
    return donusturulen


def hf_oku(repo: str, bolum: str = "train", metin_sutun: str = None,
           kategori_sutun: str = None, kategori_map: dict = None) -> list:
    try:
        from datasets import load_dataset  # type: ignore[import]
    except ImportError:
        print("HATA: pip install datasets --break-system-packages")
        sys.exit(1)

    print(f"[HF] '{repo}' yükleniyor (bölüm: {bolum})...")
    ds = load_dataset(repo, split=bolum)
    print(f"  {len(ds)} satır | Sütunlar: {ds.column_names}")

    metin_sutun = metin_sutun or _sutun_bul(ds.column_names,
        ["complaint_text_tr", "text", "metin", "govde", "body", "content"])
    kategori_sutun = kategori_sutun or _sutun_bul(ds.column_names,
        ["complaint_type", "label", "category", "kategori", "class"])

    if not metin_sutun:
        print(f"HATA: Metin sütunu bulunamadı. Sütunlar: {ds.column_names}")
        sys.exit(1)

    donusturulen = []
    eslesmeyen = Counter()

    for satir in ds:
        metin = str(satir.get(metin_sutun, "")).strip()
        if not metin:
            continue
        ham_kat = str(satir.get(kategori_sutun, "")) if kategori_sutun else ""
        kategori = kategori_tahmin_et(ham_kat, kategori_map) if ham_kat else None
        if not kategori:
            eslesmeyen[ham_kat] += 1
            continue
        donusturulen.append({
            "gonderen_ad": "Anonim",
            "konu":        konu_uret(metin, kategori),
            "govde":       metin,
            "kategori":    kategori,
            "aciliyet":    aciliyet_tahmin_et(metin),
            "tarih":       rastgele_tarih(),
        })

    if eslesmeyen:
        print(f"  ⚠️  Eşleşmeyen: {dict(eslesmeyen)}")
    print(f"  ✓ Dönüştürülen: {len(donusturulen)} kayıt")
    return donusturulen


def _sutun_bul(sutunlar: list, adaylar: list):
    sutun_map = {s.lower(): s for s in sutunlar}
    for aday in adaylar:
        if aday.lower() in sutun_map:
            return sutun_map[aday.lower()]
    return None


# ---------------------------------------------------------------------------
# BİRLEŞTİRME & KAYDETME
# ---------------------------------------------------------------------------

def mevcut_csv_oku(yol: str) -> list:
    with open(yol, encoding="utf-8") as f:
        satirlar = list(csv.DictReader(f))
    for s in satirlar:
        s["mail_id"] = int(s["mail_id"])
        s["aciliyet"] = int(s["aciliyet"])
    print(f"  Mevcut veri: {len(satirlar)} kayıt")
    return satirlar


def birlestir_ve_kaydet(yeni_veriler: list, cikti_yolu: str,
                         mevcut_yolu: str = None, karistir: bool = True):
    gecerli, hatali_sayisi = [], 0
    for i, satir in enumerate(yeni_veriler):
        ok, _ = satiri_dogrula(satir, i + 1)
        if ok:
            gecerli.append(satir)
        else:
            hatali_sayisi += 1

    print(f"\n  Doğrulama: {len(gecerli)} geçerli / {hatali_sayisi} hatalı")

    if mevcut_yolu and Path(mevcut_yolu).exists():
        mevcut = mevcut_csv_oku(mevcut_yolu)
        mevcut_temiz = [{k: v for k, v in s.items() if k != "mail_id"}
                        for s in mevcut]
        tum_veri = mevcut_temiz + gecerli
        print(f"  Mevcut ({len(mevcut_temiz)}) + Yeni ({len(gecerli)}) = "
              f"Toplam ({len(tum_veri)})")
    else:
        tum_veri = gecerli

    if karistir:
        random.shuffle(tum_veri)

    for i, satir in enumerate(tum_veri, 1):
        satir["mail_id"] = i

    Path(cikti_yolu).parent.mkdir(parents=True, exist_ok=True)
    with open(cikti_yolu, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=ZORUNLU_SUTUNLAR)
        writer.writeheader()
        writer.writerows(tum_veri)

    kat_sayim = Counter(s["kategori"] for s in tum_veri)
    aci_sayim = Counter(s["aciliyet"] for s in tum_veri)

    print(f"\n✅ Kaydedildi → {cikti_yolu}")
    print(f"   Toplam: {len(tum_veri)} kayıt\n")
    print("   Kategori dağılımı:")
    for k in sorted(kat_sayim):
        sayi = kat_sayim[k]
        bar = "█" * (sayi // 20)
        print(f"     {k:8s}: {sayi:4d}  {bar}")
    print("\n   Aciliyet dağılımı:")
    for a in sorted(aci_sayim):
        print(f"     Seviye {a}: {aci_sayim[a]}")

    return tum_veri


# ---------------------------------------------------------------------------
# HIZLI ENTEGRASYON (import ederek)
# ---------------------------------------------------------------------------

def entegre_et(kaynak_csv: str, mevcut_csv: str, cikti_csv: str,
               kategori_map: dict = None) -> int:
    """
    Tek fonksiyon ile entegrasyon — doğrudan import edip çağırabilirsin.

    Örnek:
        from dataset_adapter import entegre_et
        entegre_et(
            kaynak_csv="Turkish_Complaint_Image_Datset_v3_with_ids.csv",
            mevcut_csv="data/synthetic_complaints.csv",
            cikti_csv="data/merged.csv",
        )
    """
    yeni = csv_oku(kaynak_csv, kategori_map=kategori_map)
    sonuc = birlestir_ve_kaydet(yeni, cikti_csv, mevcut_yolu=mevcut_csv)
    return len(sonuc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Türkçe şikayet veri setlerini projeye entegre eder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Örnekler:
  # Turkish Complaint Image Dataset v3:
  python dataset_adapter.py \\
      --kaynak csv \\
      --dosya Turkish_Complaint_Image_Datset_v3_with_ids.csv \\
      --birlestir data/synthetic_complaints.csv \\
      --cikti data/merged.csv

  # Özel kategori eşlemesi:
  python dataset_adapter.py \\
      --kaynak csv --dosya veri.csv \\
      --kategori_map '{"OzelKat": "kargo"}' \\
      --cikti data/merged.csv
        """
    )
    parser.add_argument("--kaynak", choices=["hf", "csv"], required=True)
    parser.add_argument("--hf_repo")
    parser.add_argument("--hf_bolum", default="train")
    parser.add_argument("--dosya")
    parser.add_argument("--metin_sutun")
    parser.add_argument("--kategori_sutun")
    parser.add_argument("--konu_sutun")
    parser.add_argument("--kategori_map")
    parser.add_argument("--birlestir", help="Mevcut CSV yolu")
    parser.add_argument("--cikti", required=True)
    parser.add_argument("--karistirma", action="store_true")

    args = parser.parse_args()

    kategori_map = None
    if args.kategori_map:
        try:
            kategori_map = json.loads(args.kategori_map)
        except json.JSONDecodeError as e:
            print(f"HATA: JSON parse hatası: {e}")
            sys.exit(1)

    print(f"\n{'='*60}\nVERİ YÜKLEME\n{'='*60}")

    if args.kaynak == "hf":
        if not args.hf_repo:
            print("HATA: --hf_repo gerekli")
            sys.exit(1)
        yeni = hf_oku(args.hf_repo, args.hf_bolum,
                      args.metin_sutun, args.kategori_sutun, kategori_map)
    else:
        if not args.dosya:
            print("HATA: --dosya gerekli")
            sys.exit(1)
        yeni = csv_oku(args.dosya, args.metin_sutun,
                       args.kategori_sutun, args.konu_sutun, kategori_map)

    if not yeni:
        print("\nHATA: Hiç geçerli veri yok. COMPLAINT_IMAGE_V3_MAP zaten yerleşik — "
              "dosya adını ve sütunları kontrol edin.")
        sys.exit(1)

    print(f"\n{'='*60}\nBİRLEŞTİRME & KAYDETME\n{'='*60}")
    birlestir_ve_kaydet(yeni, args.cikti,
                        mevcut_yolu=args.birlestir,
                        karistir=not args.karistirma)

    print(f"""
{'='*60}
SONRAKİ ADIMLAR
{'='*60}
  1. Modeli yeniden eğit:   python classifier.py
  2. İndeksi güncelle:      python similarity.py
  3. API'yi başlat:         python -m uvicorn api:app --reload --port 8000
""")


if __name__ == "__main__":
    main()