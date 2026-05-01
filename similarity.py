"""
Benzer Şikayet Bulma (Semantic Similarity Search)
==================================================

Görev: Yeni bir şikayet geldiğinde, geçmişteki en benzer 5 şikayeti bul.

Neden AI? Anahtar kelime aramasıyla bu yapılamaz çünkü:
- "Kargom gelmedi" ile "siparişim hâlâ ulaşmadı" tek ortak kelime yok
  ama anlamca aynı.
- "Param iade edilmedi" ile "ücretim hesabıma yatmadı" eş anlamlı.
- Embedding modeli kelimeleri değil ANLAMI vektörleştirir.

Mimari:
  1. SentenceTransformer (paraphrase-multilingual-MiniLM-L12-v2) → 
     metni 384 boyutlu vektöre çevirir
  2. ChromaDB → lokal vector veritabanı (SQLite tabanlı, kurulum gerektirmez)
  3. Cosine similarity → en yakın komşuları bulur

Hız: 
  - İlk indeksleme (500 mail): ~30 saniye
  - Tek sorgu: ~50ms
  - Tamamen lokal, dış servis yok
"""

import csv
from pathlib import Path

# Lazy import (model yüklemesi yavaş)
_model = None
_collection = None

MODEL_ADI = "paraphrase-multilingual-MiniLM-L12-v2"
DB_YOLU = Path(__file__).parent / "chroma_db"
KOLLEKSIYON_ADI = "sikayetler"
CSV_YOLU = Path(__file__).parent / "merged.csv"

def _model_yukle():
    """Embedding modelini cache'li yükle."""
    global _model
    if _model is None:
        print("[similarity] Embedding modeli yükleniyor (ilk çağrı)...")
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(MODEL_ADI)
        print("[similarity] Model hazır.\n")
    return _model


def _kolleksiyon_al(temizle=False):
    """ChromaDB koleksiyonunu al (ya da temizleyip yenile)."""
    global _collection
    import chromadb
    
    client = chromadb.PersistentClient(path=str(DB_YOLU))
    
    if temizle:
        try:
            client.delete_collection(KOLLEKSIYON_ADI)
        except Exception:
            pass
        _collection = None
    
    if _collection is None:
        _collection = client.get_or_create_collection(
            name=KOLLEKSIYON_ADI,
            metadata={"hnsw:space": "cosine"},  # cosine similarity kullan
        )
    return _collection


# ---------------------------------------------------------------------------
# İNDEKSLEME
# ---------------------------------------------------------------------------

def indeksle(csv_yolu=CSV_YOLU, batch_size=64):
    """CSV'deki tüm şikayetleri ChromaDB'ye indeksler."""
    from preprocessing import temizle_mail
    
    print(f"İndeksleme başlıyor: {csv_yolu}")
    
    model = _model_yukle()
    collection = _kolleksiyon_al(temizle=True)  # baştan başla
    
    # Veriyi yükle ve temizle
    with open(csv_yolu, encoding="utf-8") as f:
        satirlar = list(csv.DictReader(f))
    
    print(f"  {len(satirlar)} mail yükleniyor...")
    
    # Embedding üretmek için metin hazırla (konu + temiz gövde)
    metinler = [
        s["konu"] + ". " + temizle_mail(s["govde"]) for s in satirlar
    ]
    ids = [f"mail_{s['mail_id']}" for s in satirlar]
    metadatalar = [
        {
            "kategori": s["kategori"],
            "aciliyet": int(s["aciliyet"]),
            "konu": s["konu"],
            "gonderen": s["gonderen_ad"],
            "tarih": s["tarih"],
        }
        for s in satirlar
    ]
    
    # Batch'ler hâlinde encode et (RAM dostu)
    print(f"  Embeddings üretiliyor (batch_size={batch_size})...")
    for i in range(0, len(metinler), batch_size):
        batch_metin = metinler[i : i + batch_size]
        batch_id = ids[i : i + batch_size]
        batch_meta = metadatalar[i : i + batch_size]
        
        embeddings = model.encode(
            batch_metin,
            convert_to_numpy=True,
            show_progress_bar=False,
        ).tolist()
        
        collection.add(
            ids=batch_id,
            embeddings=embeddings,
            documents=batch_metin,
            metadatas=batch_meta,
        )
        print(f"    {min(i + batch_size, len(metinler))}/{len(metinler)} işlendi")
    
    print(f"\n✓ İndeksleme tamamlandı: {collection.count()} mail vektörlendi")
    return collection


# ---------------------------------------------------------------------------
# ARAMA
# ---------------------------------------------------------------------------

def benzer_bul(sorgu_metin: str, k: int = 5, kategori_filtre: str = None) -> list:
    """
    Sorgu metnine en benzer k şikayeti döndür.
    
    Args:
        sorgu_metin: Aranacak şikayet metni (konu + gövde birlikte)
        k: Kaç benzer şikayet döndürülsün
        kategori_filtre: Sadece bu kategorideki şikayetleri ara (opsiyonel)
    
    Returns:
        [{"id", "metin", "benzerlik", "kategori", "aciliyet", "konu", ...}, ...]
        Benzerlik: 0-1 arası, 1 = aynı, 0 = alakasız
    """
    from preprocessing import temizle_mail
    
    model = _model_yukle()
    collection = _kolleksiyon_al()
    
    if collection.count() == 0:
        raise RuntimeError(
            "Veritabanı boş. Önce 'python similarity.py' ile indeksleme yap."
        )
    
    # Sorguyu temizle ve embedding üret
    sorgu_temiz = temizle_mail(sorgu_metin) if "\n" in sorgu_metin else sorgu_metin
    sorgu_emb = model.encode([sorgu_temiz], convert_to_numpy=True).tolist()
    
    # Filtreyi hazırla
    where = {"kategori": kategori_filtre} if kategori_filtre else None
    
    # Ara
    sonuclar = collection.query(
        query_embeddings=sorgu_emb,
        n_results=k,
        where=where,
    )
    
    # Sonuçları paketle (cosine distance → similarity'ye çevir)
    paketli = []
    for i in range(len(sonuclar["ids"][0])):
        mesafe = sonuclar["distances"][0][i]  # cosine distance: 0=aynı, 2=zıt
        benzerlik = max(0, 1 - mesafe)  # similarity'ye çevir
        meta = sonuclar["metadatas"][0][i]
        paketli.append({
            "id": sonuclar["ids"][0][i],
            "metin": sonuclar["documents"][0][i],
            "benzerlik": round(benzerlik, 3),
            "kategori": meta["kategori"],
            "aciliyet": meta["aciliyet"],
            "konu": meta["konu"],
            "gonderen": meta["gonderen"],
        })
    
    return paketli


# ---------------------------------------------------------------------------
# DEMO
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 1) İndeksleme (sadece gerekirse)
    collection = _kolleksiyon_al()
    if collection.count() == 0:
        indeksle()
    else:
        print(f"Veritabanı zaten dolu: {collection.count()} kayıt.")
        print("Yeniden indekslemek için chroma_db klasörünü silebilirsin.\n")
    
    # 2) Test sorguları
    print("=" * 70)
    print("BENZER ŞİKAYET ARAMA TESTLERİ")
    print("=" * 70)
    
    test_sorgulari = [
        # Anahtar kelime YOK ama anlamca eşleşmeli
        "Verdiğim sipariş bir türlü ulaşmadı, kargo şirketi de bilgi vermiyor.",
        # Eş anlamlılar
        "Param hesabıma geri yatmadı, iade işlemim 2 hafta önce yapıldı.",
        # Yasal tehdit
        "Avukatımla görüştüm, hukuki süreç başlatıyoruz.",
        # Teknik arıza
        "Yeni aldığım cihaz açılmıyor, bozuk geldi sanırım.",
        # Belirsiz
        "Üyeliğimi iptal etmek istiyorum.",
    ]
    
    for sorgu in test_sorgulari:
        print(f"\n{'─' * 70}")
        print(f"SORGU: '{sorgu}'")
        print(f"{'─' * 70}")
        
        sonuclar = benzer_bul(sorgu, k=3)
        for i, s in enumerate(sonuclar, 1):
            bar_uzunluk = int(s["benzerlik"] * 30)
            bar = "█" * bar_uzunluk
            print(f"\n  #{i} [{s['kategori']:6s}] [Aciliyet: {s['aciliyet']}] "
                  f"benzerlik={s['benzerlik']:.3f} {bar}")
            print(f"     Konu: {s['konu']}")
            kisa_metin = s["metin"][:120].replace("\n", " ")
            print(f"     Metin: {kisa_metin}...")
    
    # 3) Kategoriye göre filtreleme örneği
    print(f"\n{'=' * 70}")
    print("KATEGORİ FİLTRELİ ARAMA (sadece 'kargo' kategorisi)")
    print(f"{'=' * 70}")
    
    sorgu = "Param iade edildi mi acaba?"
    print(f"\nSorgu: '{sorgu}' (filtre: kargo)\n")
    sonuclar = benzer_bul(sorgu, k=3, kategori_filtre="kargo")
    for i, s in enumerate(sonuclar, 1):
        print(f"  #{i} [{s['kategori']}] benzerlik={s['benzerlik']:.3f}")
        print(f"     {s['konu']}")