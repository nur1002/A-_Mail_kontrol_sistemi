# 📧 Şikayet Agent — Türkçe E-Ticaret Şikayet Sınıflandırma Sistemi

E-ticaret firmalarının müşterilerinden gelen şikayet maillerini **otomatik olarak okuyan, sınıflandıran ve önceliklendiren** bir yapay zeka sistemi.

**🔒 Tamamen yerel çalışır** — Müşteri mailleri hiçbir bulut servisine gönderilmez. Tüm AI modelleri lokal makinede çalışır.

---

## 🎯 Ne Yapar?

Müşteri hizmetleri ekibi sabah geldiğinde mail kutusunda 200 mail görmek yerine, **kategorize edilmiş ve aciliyet sırasına dizilmiş** halde dashboard'da görür:

- ✅ Kategoriye ayırır: **kargo / iade / teknik / fatura**
- ✅ Aciliyet skorlar: **1 (düşük) — 5 (kritik)**
- ✅ Hangi departmana gideceğini söyler
- ✅ Geçmiş benzer şikayetleri bulur
- ✅ Önerilen aksiyon sunar

---

## 🏗️ Sistem Mimarisi

```
Gmail Inbox
    ↓ (her saat otomatik)
Mail Çekici (mail_fetcher.py)
    ↓
Önişleme (preprocessing.py)
    ↓
PIPELINE — 4 modül birleşir:
    ├── Sınıflandırıcı  → kategori
    ├── Aciliyet Skor   → 1-5
    ├── Benzer Bul      → ChromaDB
    └── Aksiyon Üret    → öneri
    ↓
Veritabanı (SQLite)
    ↓
API (FastAPI) → Dashboard (HTML+JS)
```

---

## 📊 Sonuçlar

- **Test Başarısı:** %98.16
- **Veri Seti:** 1630 mail (500 sentetik + 1130 Kaggle)
- **Kategoriler:** kargo, iade, teknik, fatura

---

## 🛠️ Kullanılan Teknolojiler

| Katman | Teknoloji |
|--------|-----------|
| Backend | Python 3.10+ |
| Web Framework | FastAPI + Uvicorn |
| Sınıflandırma | scikit-learn (TF-IDF + Logistic Regression) |
| Sentiment | Hugging Face Transformers (Türkçe BERT) |
| Embedding | sentence-transformers |
| Vektör DB | ChromaDB |
| Veritabanı | SQLite |
| Mail | imaplib (IMAP) |
| Frontend | Vanilla HTML/CSS/JS + Chart.js |

---

## 📁 Proje Yapısı

```
yz-mailkontrol/
├── api.py                    # FastAPI sunucu
├── pipeline.py               # 4 modülü birleştiren akış
├── classifier.py             # Sınıflandırma modeli
├── urgency.py                # Aciliyet skorlama (3 katmanlı)
├── similarity.py             # Benzer şikayet (ChromaDB)
├── preprocessing.py          # Mail temizleme
├── mail_fetcher.py           # Gmail IMAP bağlantısı
├── database.py               # SQLite katmanı
├── scheduler.py              # Saatlik otomatik çekici
├── data_generator.py         # Sentetik veri üretici
├── dataset_adapter.py        # Veri seti birleştirici
├── static/
│   └── index.html            # Dashboard arayüzü
├── models/
│   └── classifier.pkl        # Eğitilmiş model (gitignore)
├── chroma_db/                # Vektör veritabanı (gitignore)
├── merged.csv                # Birleştirilmiş veri seti (gitignore)
├── synthetic_complaints.csv  # Üretilmiş veriler (gitignore)
├── sikayet_agent.db          # SQLite (gitignore)
├── .env                      # Gmail kimlik bilgileri (gitignore)
├── .gitignore
├── requirements.txt
└── README.md
```

---

## 🚀 Kurulum

### 1️⃣ Repoyu Klonla
```bash
git clone https://github.com/nur1002/A-_Mail_kontrol_sistemi.git
cd A-_Mail_kontrol_sistemi
```

### 2️⃣ Gerekli Kütüphaneleri Kur
```bash
pip install -r requirements.txt
```

### 3️⃣ Veri Setini Hazırla
```bash
# Sentetik veri üret
python data_generator.py

# Kaggle veri seti ile birleştir
python dataset_adapter.py --kaynak csv --dosya Turkish_Complaint_Image_Datset_v3_with_ids.csv --birlestir synthetic_complaints.csv --cikti merged.csv
```

### 4️⃣ Modeli Eğit
```bash
python classifier.py
```

### 5️⃣ Vektör DB'yi Kur
```bash
python similarity.py
```

### 6️⃣ Gmail Bağlantısı Hazırla

1. https://myaccount.google.com/apppasswords adresine git
2. **2 Adımlı Doğrulama**'nın açık olduğundan emin ol
3. "Uygulama şifresi oluştur" → Gmail seç → 16 haneli şifreyi kopyala
4. Proje klasöründe `.env` dosyası oluştur:

```env
GMAIL_USER=mail@gmail.com
GMAIL_APP_PASSWORD=abcd efgh ijkl mnop
```

### 7️⃣ Sistemi Başlat
```bash
python -m uvicorn api:app --port 8000 --reload
```

Tarayıcıda aç: **http://localhost:8000**

---

## 💻 Kullanım

1. Dashboard açılınca **⚙️ Gmail Ayarları**'na tıkla
2. Email + Uygulama Şifresi gir → **Bağlan & Test Et**
3. Mailler otomatik çekilir ve analiz edilir
4. Sistem her saat başında otomatik kontrol yapar
5. Tabloda mailleri filtreleyebilir, üzerine tıklayıp detayları görebilirsin

---

## 📡 API Endpoint'leri

| Endpoint | Method | Açıklama |
|----------|--------|----------|
| `/saglik` | GET | Sistem durumu |
| `/analizler` | GET | Mail listesi (filtreleme + arama) |
| `/analizler/{id}` | GET | Tek mail detayı |
| `/analizler/istatistik/ozet` | GET | Dashboard istatistikleri |
| `/analizler/istatistik/gunluk` | GET | Son 7 gün dağılımı |
| `/analyze` | POST | Manuel mail analizi |
| `/gmail/baglan` | POST | Gmail kimlik kayıt |
| `/mail/sync` | POST | Manuel senkronizasyon |
| `/mail/reset` | POST | Veritabanı sıfırla & yeniden analiz |

API dokümantasyonu: **http://localhost:8000/docs**

---

## 🔒 Güvenlik & Gizlilik

- ✅ Tüm AI modelleri **lokal** çalışır (`~/.cache/huggingface`)
- ✅ Veritabanı **lokal** SQLite dosyası
- ✅ ChromaDB **lokal** vektör DB
- ✅ API sadece **localhost:8000**
- ✅ Şifreler `.env` dosyasında (Git'e gitmez)
- ⚠️ Gmail IMAP bağlantısı zorunlu (kendi mailini çekmek için)

**Hiçbir mail içeriği OpenAI, Google, Anthropic gibi 3. parti servislere gönderilmez.**

---

## 🧠 Aciliyet Skorlama Algoritması

3 katmanlı hibrit sistem:

| Katman | Ağırlık | Ne Ölçer |
|--------|---------|----------|
| **Kural** | %60 | Anahtar kelimeler ("acil", "yasal süreç", "şikayet edeceğim") |
| **Stil** | %25 | BÜYÜK HARF oranı, ünlem (!!!) sayısı, kaba ifadeler |
| **Sentiment** | %15 | Türkçe BERT ile duygu analizi |

Üçü birleştirilip 1-5 arası skor üretilir.

---

## 🎓 Akademik Özet

Bu proje, **TF-IDF + Logistic Regression** klasik ML yaklaşımını **transformer tabanlı sentiment analizi** ve **vektör benzerlik araması** ile birleştirerek hibrit bir mimari sunar. Sistem, KVKK ve veri gizliliği gerekliliklerini karşılayacak şekilde tamamen yerel çalışacak biçimde tasarlanmıştır.

**Kullanılan Modeller:**
- `savasy/bert-base-turkish-sentiment-cased` — Sentiment
- `paraphrase-multilingual-MiniLM-L12-v2` — Embedding

---

## 📝 Lisans

MIT

---

## 👤 Geliştirici

**Hatice Nur Sakarya**

GitHub: [@nur1002](https://github.com/nur1002)
