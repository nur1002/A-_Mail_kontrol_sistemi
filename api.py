"""
FastAPI Backend (v2)
====================

Pipeline'ı HTTP API olarak sunar.

Endpoints:
  GET  /              → Sağlık kontrolü + sistem bilgisi
  GET  /mails         → CSV'deki mailleri listele (sayfalı, filtrelenebilir)
  GET  /mail/{id}     → Tek bir maili getir
  POST /analyze       → Yeni mail analiz et (yapılandırılmış VEYA ham)
  GET  /mail/{id}/analyze → CSV'deki maili analiz et
  GET  /istatistik    → Veri seti istatistikleri
  GET  /saglik        → Sistem bileşenlerinin durumu
  GET  /docs          → Swagger UI

Çalıştırma:
  pip install fastapi uvicorn
  python -m uvicorn api:app --reload --port 8000
"""

import csv
import time
from pathlib import Path
from datetime import datetime
from collections import Counter

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from pydantic import BaseModel, validator
from typing import Optional, Union

from pipeline import analiz_et, tam_surec_analiz

# ---------------------------------------------------------------------------
# UYGULAMA TANIMI
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Şikayet Agent API",
    description="""
## Lokal AI Tabanlı E-Ticaret Şikayet Analiz Sistemi

Her mail için otomatik olarak:
- **Kategori** tespiti (kargo / iade / teknik / fatura)
- **Aciliyet** skoru (1-5 arası)
- **Benzer şikayetler** önerisi
- **Aksiyon** tavsiyesi

> Tüm işlemler yerel olarak çalışır, hiçbir veri dışarı gönderilmez.
    """,
    version="2.0.0",
    contact={"name": "Şikayet Agent", "url": "http://localhost:8000"},
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# VERİ
# ---------------------------------------------------------------------------

CSV_YOLU = Path(__file__).parent / "synthetic_complaints.csv"
_mailler_cache = None
_baslangic_zamani = datetime.now()


def _csv_yukle():
    """CSV'yi RAM'e yükle (lazy + cache)."""
    global _mailler_cache
    if _mailler_cache is None:
        if not CSV_YOLU.exists():
            raise HTTPException(
                status_code=503,
                detail={
                    "hata": "Veri dosyası bulunamadı",
                    "cozum": f"'{CSV_YOLU}' dosyasını oluşturun: python data_generator.py",
                }
            )
        with open(CSV_YOLU, encoding="utf-8") as f:
            _mailler_cache = list(csv.DictReader(f))
        for m in _mailler_cache:
            m["mail_id"] = int(m["mail_id"])
            m["aciliyet"] = int(m["aciliyet"])
    return _mailler_cache


# ---------------------------------------------------------------------------
# REQUEST / RESPONSE MODELLERİ
# ---------------------------------------------------------------------------

class MailYapili(BaseModel):
    """Yapılandırılmış: konu ve gövde ayrı ayrı"""
    konu: str = ""
    govde: str

    @validator("govde")
    def govde_bos_olamaz(cls, v):
        if not v or not v.strip():
            raise ValueError("Mail gövdesi boş olamaz")
        return v.strip()

    @validator("konu")
    def konu_temizle(cls, v):
        return v.strip() if v else ""

    class Config:
        schema_extra = {
            "example": {
                "konu": "Siparişim hala gelmedi",
                "govde": "Merhaba, 10 gün önce verdiğim sipariş SP123456 hala elime ulaşmadı. Kargo takip çalışmıyor. Yardım eder misiniz?"
            }
        }


class MailHam(BaseModel):
    """Ham: müşterinin direkt yapıştırdığı tam mail metni"""
    icerik: str

    @validator("icerik")
    def icerik_bos_olamaz(cls, v):
        if not v or not v.strip():
            raise ValueError("Mail içeriği boş olamaz")
        if len(v.strip()) < 10:
            raise ValueError("Mail içeriği çok kısa (en az 10 karakter)")
        return v.strip()

    class Config:
        schema_extra = {
            "example": {
                "icerik": "From: musteri@mail.com\nSubject: Siparişim gelmedi\n\nMerhaba, 10 gündür siparişimi bekliyorum..."
            }
        }


class AnalizSonucu(BaseModel):
    """Standart analiz çıktısı"""
    kategori: str
    kategori_guven: float
    kategori_guven_yuzdesi: str
    aciliyet: int
    aciliyet_etiketi: str
    onerilen_aksiyon: str
    uyari: Optional[str]
    benzer_sikayet_sayisi: int


# ---------------------------------------------------------------------------
# YARDIMCI FONKSİYONLAR
# ---------------------------------------------------------------------------

ACILIYET_ETİKETLERİ = {
    1: "🟢 Düşük",
    2: "🔵 Normal",
    3: "🟡 Orta",
    4: "🟠 Yüksek",
    5: "🔴 Kritik",
}

KATEGORI_ACIKLAMALARI = {
    "kargo": "Kargo ve teslimat sorunları",
    "iade": "İade ve değişim talepleri",
    "teknik": "Teknik arıza ve garanti",
    "fatura": "Ödeme ve fatura sorunları",
}


def sonucu_zenginlestir(sonuc: dict) -> dict:
    """Ham pipeline çıktısına kullanıcı dostu alanlar ekler."""
    if "hata" in sonuc:
        return sonuc

    aciliyet = sonuc.get("aciliyet", 1)
    guven = sonuc.get("kategori_guven", 0)
    kategori = sonuc.get("kategori", "")

    sonuc["kategori_guven_yuzdesi"] = f"%{guven * 100:.0f}"
    sonuc["aciliyet_etiketi"] = ACILIYET_ETİKETLERİ.get(aciliyet, str(aciliyet))
    sonuc["kategori_aciklamasi"] = KATEGORI_ACIKLAMALARI.get(kategori, kategori)
    sonuc["benzer_sikayet_sayisi"] = len(sonuc.get("benzer_sikayetler", []))

    # Güven seviyesine göre yorum
    if guven >= 0.85:
        sonuc["kategori_guven_yorumu"] = "Yüksek güven"
    elif guven >= 0.65:
        sonuc["kategori_guven_yorumu"] = "Orta güven"
    else:
        sonuc["kategori_guven_yorumu"] = "Düşük güven — manuel doğrulama önerilir"

    return sonuc


def mail_bulunamadi_hatasi(mail_id: int):
    raise HTTPException(
        status_code=404,
        detail={
            "hata": f"Mail #{mail_id} bulunamadı",
            "cozum": f"Geçerli aralık: 1 - {len(_csv_yukle())}",
            "ipucu": "GET /mails ile tüm mailleri listeleyebilirsiniz",
        }
    )


# ---------------------------------------------------------------------------
# ENDPOINTS
# ---------------------------------------------------------------------------

@app.get(
    "/",
    summary="Sistem durumu",
    tags=["Sistem"],
)
def kok():
    """API'nin çalışıp çalışmadığını ve genel sistem bilgisini döner."""
    try:
        mailler = _csv_yukle()
        veri_durumu = f"{len(mailler)} mail yüklü"
    except Exception:
        veri_durumu = "Veri dosyası bulunamadı"

    calisma_suresi = datetime.now() - _baslangic_zamani
    saat = int(calisma_suresi.total_seconds() // 3600)
    dakika = int((calisma_suresi.total_seconds() % 3600) // 60)

    return {
        "durum": "✅ Çalışıyor",
        "versiyon": "2.0.0",
        "sistem": "Şikayet Agent — Lokal AI Tabanlı Sınıflandırma",
        "veri": veri_durumu,
        "calisma_suresi": f"{saat}s {dakika}dk",
        "kategoriler": list(KATEGORI_ACIKLAMALARI.keys()),
        "endpoint_ozeti": {
            "GET /mails": "Tüm mailleri listele",
            "GET /mail/{id}": "Tek mail getir",
            "POST /analyze": "Yeni mail analiz et",
            "GET /mail/{id}/analyze": "Mevcut maili analiz et",
            "GET /istatistik": "Veri seti istatistikleri",
            "GET /saglik": "Bileşen sağlık kontrolü",
        }
    }


@app.get(
    "/saglik",
    summary="Bileşen sağlık kontrolü",
    tags=["Sistem"],
)
def saglik_kontrolu():
    """Classifier, ChromaDB ve veri dosyasının durumunu kontrol eder."""
    durum = {}

    # Veri dosyası
    if CSV_YOLU.exists():
        try:
            mailler = _csv_yukle()
            durum["veri_dosyasi"] = {"durum": "✅ OK", "kayit_sayisi": len(mailler)}
        except Exception as e:
            durum["veri_dosyasi"] = {"durum": "❌ Hata", "detay": str(e)}
    else:
        durum["veri_dosyasi"] = {
            "durum": "❌ Bulunamadı",
            "cozum": "python data_generator.py komutunu çalıştırın"
        }

    # Classifier modeli
    model_yolu = Path(__file__).parent / "models" / "classifier.pkl"
    if model_yolu.exists():
        durum["classifier"] = {"durum": "✅ OK", "yol": str(model_yolu)}
    else:
        durum["classifier"] = {
            "durum": "❌ Bulunamadı",
            "cozum": "python classifier.py komutunu çalıştırın"
        }

    # ChromaDB
    chroma_yolu = Path(__file__).parent / "chroma_db"
    if chroma_yolu.exists():
        try:
            import chromadb
            client = chromadb.PersistentClient(path=str(chroma_yolu))
            collection = client.get_collection("sikayetler")
            durum["chromadb"] = {"durum": "✅ OK", "indekslenen_kayit": collection.count()}
        except Exception as e:
            durum["chromadb"] = {
                "durum": "⚠️ Erişim hatası",
                "detay": str(e),
                "cozum": "python similarity.py komutunu çalıştırın"
            }
    else:
        durum["chromadb"] = {
            "durum": "❌ Bulunamadı",
            "cozum": "python similarity.py komutunu çalıştırın"
        }

    # Genel durum özeti
    hatali = [k for k, v in durum.items() if "❌" in v["durum"]]
    uyari = [k for k, v in durum.items() if "⚠️" in v["durum"]]

    if not hatali and not uyari:
        genel = "✅ Tüm bileşenler hazır"
    elif hatali:
        genel = f"❌ {len(hatali)} bileşen eksik: {', '.join(hatali)}"
    else:
        genel = f"⚠️ {len(uyari)} bileşende uyarı: {', '.join(uyari)}"

    return {"genel_durum": genel, "bileskenler": durum}


@app.get(
    "/mails",
    summary="Mailleri listele",
    tags=["Mailler"],
)
def mailleri_listele(
    skip: int = Query(0, ge=0, description="Kaç kayıt atlanacak"),
    limit: int = Query(50, ge=1, le=200, description="Sayfa başına kayıt (max 200)"),
    kategori: Optional[str] = Query(None, description="Filtre: kargo | iade | teknik | fatura"),
    aciliyet: Optional[int] = Query(None, ge=1, le=5, description="Filtre: aciliyet seviyesi (1-5)"),
    ara: Optional[str] = Query(None, description="Konu veya gövdede metin arama"),
):
    """
    Veri setindeki mailleri sayfalı olarak döner.

    Filtreleme seçenekleri:
    - **kategori**: kargo, iade, teknik, fatura
    - **aciliyet**: 1 (düşük) - 5 (kritik)
    - **ara**: Konu veya gövde içinde arama
    """
    # Kategori doğrulama
    GECERLI_KATEGORILER = {"kargo", "iade", "teknik", "fatura"}
    if kategori and kategori not in GECERLI_KATEGORILER:
        raise HTTPException(
            status_code=400,
            detail={
                "hata": f"Geçersiz kategori: '{kategori}'",
                "gecerli_kategoriler": list(GECERLI_KATEGORILER),
            }
        )

    mailler = _csv_yukle()

    # Filtreler
    if kategori:
        mailler = [m for m in mailler if m["kategori"] == kategori]
    if aciliyet:
        mailler = [m for m in mailler if m["aciliyet"] == aciliyet]
    if ara:
        ara_kucuk = ara.lower()
        mailler = [
            m for m in mailler
            if ara_kucuk in m.get("konu", "").lower()
            or ara_kucuk in m.get("govde", "").lower()
        ]

    toplam = len(mailler)
    sayfa = mailler[skip: skip + limit]

    return {
        "toplam": toplam,
        "sayfa_bilgisi": {
            "skip": skip,
            "limit": limit,
            "gelen": len(sayfa),
            "sonraki_skip": skip + limit if skip + limit < toplam else None,
        },
        "filtreler": {
            "kategori": kategori,
            "aciliyet": aciliyet,
            "arama": ara,
        },
        "mailler": sayfa,
    }


@app.get(
    "/mail/{mail_id}",
    summary="Tek mail getir",
    tags=["Mailler"],
)
def mail_getir(mail_id: int):
    """ID'ye göre tek bir mail döner."""
    if mail_id < 1:
        raise HTTPException(
            status_code=400,
            detail={"hata": "mail_id 1'den küçük olamaz"}
        )
    for m in _csv_yukle():
        if m["mail_id"] == mail_id:
            return m
    mail_bulunamadi_hatasi(mail_id)


@app.post(
    "/analyze",
    summary="Yeni mail analiz et",
    tags=["Analiz"],
)
def yeni_mail_analiz_et(mail: Union[MailYapili, MailHam]):
    """
    Yeni bir mail üzerinde tam analiz çalıştırır.

    **MOD 1 — Yapılandırılmış** (`MailYapili`):
    ```json
    {"konu": "Siparişim gelmedi", "govde": "10 gündür bekliyorum..."}
    ```

    **MOD 2 — Ham mail** (`MailHam`):
    ```json
    {"icerik": "From: musteri@mail.com\\nSubject: Sorun var\\n\\nMail içeriği..."}
    ```

    Dönen sonuçlar:
    - `kategori`: Şikayet türü (kargo/iade/teknik/fatura)
    - `aciliyet`: 1 (düşük) - 5 (kritik)
    - `onerilen_aksiyon`: Ne yapılması gerektiği
    - `benzer_sikayetler`: Geçmişteki benzer vakalar
    """
    baslangic = time.time()

    try:
        if isinstance(mail, MailHam):
            sonuc = tam_surec_analiz(mail.icerik)
        else:
            if not mail.govde.strip():
                raise HTTPException(
                    status_code=400,
                    detail={"hata": "Mail gövdesi boş olamaz"}
                )
            sonuc = analiz_et(mail.konu, mail.govde)

        sonuc = sonucu_zenginlestir(sonuc)
        sonuc["islem_suresi_ms"] = round((time.time() - baslangic) * 1000, 1)
        return sonuc

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"hata": str(e)})
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail={
                "hata": "Model veya veritabanı hazır değil",
                "detay": str(e),
                "cozum": "GET /saglik endpoint'ini kontrol edin",
            }
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={
                "hata": "Beklenmeyen bir hata oluştu",
                "detay": str(e),
                "ipucu": "Loglara bakın veya GET /saglik ile sistem durumunu kontrol edin",
            }
        )


@app.get(
    "/mail/{mail_id}/analyze",
    summary="Mevcut maili analiz et",
    tags=["Analiz"],
)
def mail_analiz_et(mail_id: int):
    """
    CSV'deki bir maili analiz eder ve gerçek etiketlerle karşılaştırır.

    Dönen ek alanlar:
    - `gercek_kategori`: Veri setindeki doğru kategori
    - `gercek_aciliyet`: Veri setindeki doğru aciliyet
    - `kategori_dogru_mu`: Tahmin doğru mu?
    """
    m = mail_getir(mail_id)
    baslangic = time.time()

    try:
        sonuc = analiz_et(m["konu"], m["govde"])
        sonuc = sonucu_zenginlestir(sonuc)
        sonuc["mail_id"] = mail_id
        sonuc["gercek_kategori"] = m["kategori"]
        sonuc["gercek_aciliyet"] = m["aciliyet"]
        sonuc["kategori_dogru_mu"] = sonuc["kategori"] == m["kategori"]
        sonuc["aciliyet_farki"] = abs(sonuc["aciliyet"] - m["aciliyet"])
        sonuc["islem_suresi_ms"] = round((time.time() - baslangic) * 1000, 1)
        return sonuc

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"hata": "Analiz sırasında hata oluştu", "detay": str(e)}
        )


@app.get(
    "/istatistik",
    summary="Veri seti istatistikleri",
    tags=["İstatistik"],
)
def istatistik():
    """Veri setinin detaylı istatistiklerini döner."""
    mailler = _csv_yukle()

    kat_sayim = Counter(m["kategori"] for m in mailler)
    aci_sayim = Counter(m["aciliyet"] for m in mailler)

    # Kategori × aciliyet çapraz tablo
    capraz = {}
    for m in mailler:
        kat = m["kategori"]
        aci = m["aciliyet"]
        if kat not in capraz:
            capraz[kat] = {}
        capraz[kat][aci] = capraz[kat].get(aci, 0) + 1

    # Kritik (aciliyet 4-5) mail sayısı
    kritik_sayisi = sum(1 for m in mailler if m["aciliyet"] >= 4)

    return {
        "toplam_mail": len(mailler),
        "kritik_mail_sayisi": kritik_sayisi,
        "kritik_oran": f"%{kritik_sayisi / len(mailler) * 100:.1f}",
        "kategori_dagilimi": {
            k: {
                "sayi": v,
                "oran": f"%{v / len(mailler) * 100:.1f}"
            }
            for k, v in sorted(kat_sayim.items())
        },
        "aciliyet_dagilimi": {
            f"seviye_{k}": {
                "sayi": v,
                "etiket": ACILIYET_ETİKETLERİ.get(k, str(k)),
                "oran": f"%{v / len(mailler) * 100:.1f}"
            }
            for k, v in sorted(aci_sayim.items())
        },
        "kategori_aciliyet_capraz": capraz,
    }


# ---------------------------------------------------------------------------
# VERİTABANI ENDPOİNT'LERİ (otomatik analiz edilen mailler)
# ---------------------------------------------------------------------------

import database as db_module

@app.get("/analizler", summary="Analiz edilmiş mailleri listele", tags=["Analizler"])
def analizleri_listele(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    kategori: Optional[str] = Query(None),
    aciliyet: Optional[int] = Query(None, ge=1, le=5),
    ara: Optional[str] = Query(None),
    siralama: Optional[str] = Query("analiz_tarihi"),
):
    """Gmail'den otomatik çekilen ve analiz edilen maillerin listesi."""
    return db_module.listele(
        kategori=kategori, aciliyet=aciliyet,
        ara=ara, skip=skip, limit=limit, siralama=siralama
    )


@app.get("/analizler/istatistik/ozet", summary="Dashboard istatistikleri", tags=["Analizler"])
def analiz_istatistik():
    """Dashboard için özet istatistikler."""
    return db_module.istatistik()


@app.get("/analizler/{mail_id}", summary="Tek analiz detayı", tags=["Analizler"])
def analiz_detay(mail_id: int):
    """Tek bir analiz kaydının tüm detayları."""
    sonuc = db_module.detay_getir(mail_id)
    if not sonuc:
        raise HTTPException(status_code=404, detail={"hata": f"Analiz #{mail_id} bulunamadı"})
    return sonuc


class GmailBaglanRequest(BaseModel):
    gmail_user: str
    gmail_password: str

@app.post("/gmail/baglan", summary="Gmail bağlantısını test et", tags=["Gmail"])
def gmail_baglan(req: GmailBaglanRequest):
    """
    Gmail uygulama şifresini kaydet ve mailleri çek.
    .env dosyasına otomatik yazılır.
    """
    import os
    from pathlib import Path
    
    gmail_user = req.gmail_user.strip()
    gmail_password = req.gmail_password.strip()
    
    if not gmail_user or not gmail_password:
        return {
            "basarili": False,
            "hata": "Email ve şifre boş olamaz",
            "cozum": "Tüm alanları doldurun",
        }
    
    env_path = Path(__file__).parent / ".env"
    
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(f"GMAIL_USER={gmail_user}\n")
            f.write(f"GMAIL_APP_PASSWORD={gmail_password}\n")
    except Exception as e:
        return {
            "basarili": False,
            "hata": f".env yazılamadı: {e}",
            "cozum": "Dosya izinlerini kontrol edin",
        }
    
    os.environ["GMAIL_USER"] = gmail_user
    os.environ["GMAIL_APP_PASSWORD"] = gmail_password
    
    try:
        import importlib
        import mail_fetcher
        importlib.reload(mail_fetcher)
        
        mailler = mail_fetcher.mailleri_cek(sadece_yeniler=True)
        return {
            "basarili": True,
            "mesaj": f"{len(mailler)} okunmamış mail bulundu",
            "mail_sayisi": len(mailler),
        }
    except (ValueError, ConnectionError) as e:
        return {
            "basarili": False,
            "hata": str(e),
            "cozum": "Uygulama şifresini kontrol edin: https://myaccount.google.com/apppasswords",
        }
    except Exception as e:
        return {
            "basarili": False,
            "hata": f"Beklenmeyen hata: {str(e)}",
            "cozum": "Konsol loglarını kontrol edin",
        }


@app.get("/analizler/istatistik/gunluk", summary="Son N günün dağılımı", tags=["Analizler"])
def analiz_gunluk(gun: int = 7):
    """Son N gündeki günlük mail sayıları (chart için)."""
    return {"gunler": db_module.gunluk_dagilim(gun)}


@app.post("/mail/reset", summary="Veritabanını sıfırla ve maillleri yeniden analiz et", tags=["Gmail"])
def mail_reset(tum_mailler: bool = False):
    """
    Veritabanındaki tüm analizleri siler ve Gmail'den mailleri yeniden çekip analiz eder.
    
    tum_mailler=False → Sadece okunmamış mailler (default)
    tum_mailler=True  → Inbox'taki TÜM mailler (test için, dikkat!)
    """
    import importlib
    import mail_fetcher
    
    # 1. Veritabanını sıfırla
    silinen = db_module.sifirla()
    
    # 2. Mailleri çek
    try:
        importlib.reload(mail_fetcher)
        mailler = mail_fetcher.mailleri_cek(sadece_yeniler=not tum_mailler)
    except (ValueError, ConnectionError) as e:
        return {
            "basarili": False,
            "silinen": silinen,
            "hata": str(e),
            "cozum": "Önce Gmail Ayarları'ndan bağlantıyı yapın",
        }
    except Exception as e:
        return {
            "basarili": False,
            "silinen": silinen,
            "hata": f"Mail çekilemedi: {str(e)}",
        }
    
    # 3. Hepsini yeniden analiz et
    kaydedilen = 0
    hatali = 0
    
    for mail in mailler:
        try:
            analiz = analiz_et(mail["konu"], mail["govde"])
            if "hata" in analiz:
                hatali += 1
                continue
            
            if db_module.kaydet(
                message_id=mail["message_id"],
                gonderen=mail["gonderen"],
                konu=mail["konu"],
                govde=mail["govde"],
                analiz_sonucu=analiz,
                gelen_tarih=mail["tarih"],
            ):
                kaydedilen += 1
        except Exception as e:
            hatali += 1
            print(f"[RESET] Hata: {e}")
    
    return {
        "basarili": True,
        "silinen": silinen,
        "kontrol_edilen": len(mailler),
        "kaydedilen": kaydedilen,
        "hatali": hatali,
        "mesaj": f"{silinen} eski kayıt silindi, {kaydedilen} mail yeni ağırlıklarla analiz edildi",
    }


@app.post("/mail/sync", summary="Manuel mail senkronizasyonu (tetikle)", tags=["Gmail"])
def mail_sync():
    """
    Gmail'den yeni mailleri çeker, analiz eder ve DB'ye kaydeder.
    """
    return _mailleri_cek_ve_kaydet()


def _mailleri_cek_ve_kaydet() -> dict:
    """İç fonksiyon: mail çekme + analiz + kaydetme."""
    import importlib
    import mail_fetcher
    
    try:
        importlib.reload(mail_fetcher)
        mailler = mail_fetcher.mailleri_cek(sadece_yeniler=True)
    except (ValueError, ConnectionError) as e:
        return {
            "basarili": False,
            "hata": str(e),
            "cozum": "Önce Gmail Ayarları'ndan bağlantıyı yapın",
        }
    except Exception as e:
        return {
            "basarili": False,
            "hata": f"Mail çekilemedi: {str(e)}",
        }
    
    kontrol_edilen = len(mailler)
    yeni_sayisi = 0
    kaydedilen = 0
    hatali = 0
    
    for mail in mailler:
        if db_module.message_id_var_mi(mail["message_id"]):
            continue
        yeni_sayisi += 1
        
        try:
            analiz = analiz_et(mail["konu"], mail["govde"])
            if "hata" in analiz:
                hatali += 1
                continue
            
            if db_module.kaydet(
                message_id=mail["message_id"],
                gonderen=mail["gonderen"],
                konu=mail["konu"],
                govde=mail["govde"],
                analiz_sonucu=analiz,
                gelen_tarih=mail["tarih"],
            ):
                kaydedilen += 1
        except Exception as e:
            hatali += 1
            print(f"[SYNC] Hata: {e}")
    
    return {
        "basarili": True,
        "kontrol_edilen": kontrol_edilen,
        "yeni": yeni_sayisi,
        "kaydedilen": kaydedilen,
        "hatali": hatali,
    }


# ---------------------------------------------------------------------------
# OTOMATİK BACKGROUND SCHEDULER (her saat mail çeker)
# ---------------------------------------------------------------------------

import threading as _threading
import time as _time

_scheduler_baslatildi = False

def _background_scheduler():
    """Her saat başında otomatik mail kontrolü."""
    global _scheduler_baslatildi
    print("[SCHEDULER] Background mail çekici başlatıldı (her 60 dakika)")
    
    # İlk başlangıçta 30 saniye bekle (API hazır olsun)
    _time.sleep(30)
    
    while True:
        try:
            print(f"[SCHEDULER] Otomatik mail kontrolü başlıyor...")
            sonuc = _mailleri_cek_ve_kaydet()
            if sonuc.get("basarili"):
                print(f"[SCHEDULER] Tamamlandı → kaydedilen: {sonuc.get('kaydedilen', 0)}")
            else:
                print(f"[SCHEDULER] Atlandı: {sonuc.get('hata', 'bilinmiyor')}")
        except Exception as e:
            print(f"[SCHEDULER] Hata: {e}")
        
        # 1 saat bekle
        _time.sleep(3600)


@app.on_event("startup")
def baslangicta_scheduler_baslat():
    """API başlayınca background scheduler'ı başlat."""
    global _scheduler_baslatildi
    if not _scheduler_baslatildi:
        _scheduler_baslatildi = True
        t = _threading.Thread(target=_background_scheduler, daemon=True)
        t.start()


# ---------------------------------------------------------------------------
# FRONTEND (EN SONDA OLMALI — yoksa tüm rotaları yutar!)
# ---------------------------------------------------------------------------

frontend_yolu = Path(__file__).parent / "frontend" / "dist"
if frontend_yolu.exists() and (frontend_yolu / "index.html").exists():
    app.mount("", StaticFiles(directory=frontend_yolu, html=True), name="frontend")
else:
    static_yolu = Path(__file__).parent / "static"
    if static_yolu.exists():
        app.mount("/", StaticFiles(directory=static_yolu, html=True), name="static")