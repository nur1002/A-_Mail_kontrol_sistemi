"""
Veritabanı Katmanı
==================

Analiz edilen mailleri SQLite'a kaydeder.
Tablo: analizler (mail başına bir satır, tekrar analiz engellenir)

Neden SQLite?
- Kurulum gerektirmez, tek dosya
- Yerel çalışır, dışarıya veri gitmez
- Dashboard sorguları için yeterince hızlı
"""

import sqlite3
import json
from pathlib import Path
from datetime import datetime
from contextlib import contextmanager

DB_YOLU = Path(__file__).parent / "sikayet_agent.db"


@contextmanager
def baglanti():
    """Thread-safe SQLite bağlantısı."""
    con = sqlite3.connect(DB_YOLU, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")  # eş zamanlı okuma için
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def tablolari_olustur():
    """İlk çalıştırmada tabloları oluşturur."""
    with baglanti() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS analizler (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id    TEXT UNIQUE,          -- Gmail message-id (tekrar analiz engeller)
                gonderen      TEXT,
                konu          TEXT,
                govde         TEXT,
                govde_temiz   TEXT,
                kategori      TEXT,
                kategori_guven REAL,
                aciliyet      INTEGER,
                aciliyet_etiketi TEXT,
                onerilen_aksiyon TEXT,
                departman     TEXT,
                uyari         TEXT,
                ozet          TEXT,
                benzer_sikayetler TEXT,             -- JSON string
                kategori_skorlari TEXT,             -- JSON string
                aciliyet_detay TEXT,                -- JSON string
                gelen_tarih   TEXT,                 -- mailin gönderildiği tarih
                analiz_tarihi TEXT,                 -- sistemin analiz ettiği tarih
                ham_skor      REAL
            );

            CREATE INDEX IF NOT EXISTS idx_kategori  ON analizler(kategori);
            CREATE INDEX IF NOT EXISTS idx_aciliyet  ON analizler(aciliyet);
            CREATE INDEX IF NOT EXISTS idx_gelen     ON analizler(gelen_tarih);
            CREATE INDEX IF NOT EXISTS idx_analiz    ON analizler(analiz_tarihi);
        """)
    print(f"[DB] Veritabanı hazır: {DB_YOLU}")


def kaydet(message_id: str, gonderen: str, konu: str, govde: str,
           analiz_sonucu: dict, gelen_tarih: str = None) -> bool:
    """
    Analiz sonucunu kaydeder.
    Aynı message_id daha önce kaydedilmişse False döner (duplicate engel).
    """
    try:
        with baglanti() as con:
            con.execute("""
                INSERT INTO analizler (
                    message_id, gonderen, konu, govde, govde_temiz,
                    kategori, kategori_guven, aciliyet, aciliyet_etiketi,
                    onerilen_aksiyon, departman, uyari, ozet,
                    benzer_sikayetler, kategori_skorlari, aciliyet_detay,
                    gelen_tarih, analiz_tarihi, ham_skor
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                message_id,
                gonderen,
                konu,
                govde,
                analiz_sonucu.get("govde_temiz", ""),
                analiz_sonucu.get("kategori", ""),
                analiz_sonucu.get("kategori_guven", 0),
                analiz_sonucu.get("aciliyet", 1),
                analiz_sonucu.get("aciliyet_etiketi", ""),
                analiz_sonucu.get("onerilen_aksiyon", ""),
                analiz_sonucu.get("departman", ""),
                analiz_sonucu.get("uyari"),
                analiz_sonucu.get("ozet", ""),
                json.dumps(analiz_sonucu.get("benzer_sikayetler", []), ensure_ascii=False),
                json.dumps(analiz_sonucu.get("kategori_skorlari", {}), ensure_ascii=False),
                json.dumps(analiz_sonucu.get("aciliyet_detay", {}), ensure_ascii=False),
                gelen_tarih or datetime.now().isoformat(),
                datetime.now().isoformat(),
                analiz_sonucu.get("aciliyet_ham", 0),
            ))
        return True
    except sqlite3.IntegrityError:
        return False  # message_id zaten var


def listele(kategori: str = None, aciliyet: int = None,
            ara: str = None, skip: int = 0, limit: int = 50,
            siralama: str = "analiz_tarihi") -> dict:
    """Dashboard için filtrelenmiş mail listesi."""
    kosullar = []
    parametreler = []

    if kategori:
        kosullar.append("kategori = ?")
        parametreler.append(kategori)
    if aciliyet:
        kosullar.append("aciliyet = ?")
        parametreler.append(aciliyet)
    if ara:
        kosullar.append("(konu LIKE ? OR govde_temiz LIKE ? OR gonderen LIKE ?)")
        parametreler.extend([f"%{ara}%"] * 3)

    where = ("WHERE " + " AND ".join(kosullar)) if kosullar else ""

    guvenli_siralama = {
        "analiz_tarihi": "analiz_tarihi DESC",
        "gelen_tarih":   "gelen_tarih DESC",
        "aciliyet":      "aciliyet DESC",
        "kategori":      "kategori ASC",
    }.get(siralama, "analiz_tarihi DESC")

    with baglanti() as con:
        toplam = con.execute(
            f"SELECT COUNT(*) FROM analizler {where}", parametreler
        ).fetchone()[0]

        satirlar = con.execute(
            f"""SELECT id, message_id, gonderen, konu, kategori,
                       kategori_guven, aciliyet, aciliyet_etiketi,
                       onerilen_aksiyon, departman, uyari, ozet,
                       gelen_tarih, analiz_tarihi, ham_skor
                FROM analizler {where}
                ORDER BY {guvenli_siralama}
                LIMIT ? OFFSET ?""",
            parametreler + [limit, skip]
        ).fetchall()

    return {
        "toplam": toplam,
        "mailler": [dict(s) for s in satirlar],
    }


def detay_getir(mail_id: int) -> dict | None:
    """Tek mail için tüm alanları döner (benzerler ve detay dahil)."""
    with baglanti() as con:
        satir = con.execute(
            "SELECT * FROM analizler WHERE id = ?", (mail_id,)
        ).fetchone()

    if not satir:
        return None

    d = dict(satir)
    # JSON alanları parse et
    for alan in ["benzer_sikayetler", "kategori_skorlari", "aciliyet_detay"]:
        try:
            d[alan] = json.loads(d[alan] or "[]")
        except Exception:
            d[alan] = {}
    return d


def istatistik() -> dict:
    """Dashboard istatistik kartları için özet veriler."""
    with baglanti() as con:
        toplam = con.execute("SELECT COUNT(*) FROM analizler").fetchone()[0]

        if toplam == 0:
            return {
                "toplam": 0,
                "kategori_dagilimi": {},
                "aciliyet_dagilimi": {},
                "kritik_sayisi": 0,
                "son_24_saat": 0,
                "dusuk_guven_sayisi": 0,
                "ortalama_guven": 0,
            }

        kategori_rows = con.execute("""
            SELECT kategori, COUNT(*) as sayi
            FROM analizler GROUP BY kategori ORDER BY sayi DESC
        """).fetchall()

        aciliyet_rows = con.execute("""
            SELECT aciliyet, COUNT(*) as sayi
            FROM analizler GROUP BY aciliyet ORDER BY aciliyet
        """).fetchall()

        kritik = con.execute(
            "SELECT COUNT(*) FROM analizler WHERE aciliyet >= 4"
        ).fetchone()[0]

        son_24 = con.execute("""
            SELECT COUNT(*) FROM analizler
            WHERE analiz_tarihi >= datetime('now', '-24 hours')
        """).fetchone()[0]

        dusuk_guven = con.execute(
            "SELECT COUNT(*) FROM analizler WHERE kategori_guven < 0.65"
        ).fetchone()[0]

        ort_guven = con.execute(
            "SELECT AVG(kategori_guven) FROM analizler"
        ).fetchone()[0] or 0

    return {
        "toplam": toplam,
        "kategori_dagilimi": {r["kategori"]: r["sayi"] for r in kategori_rows},
        "aciliyet_dagilimi": {r["aciliyet"]: r["sayi"] for r in aciliyet_rows},
        "kritik_sayisi": kritik,
        "son_24_saat": son_24,
        "dusuk_guven_sayisi": dusuk_guven,
        "ortalama_guven": round(ort_guven, 3),
    }


def gunluk_dagilim(gun_sayisi: int = 7) -> list[dict]:
    """
    Son N günün günlük mail sayısını döner.
    Returns: [{"tarih": "2026-04-21", "sayi": 5}, ...]
    """
    with baglanti() as con:
        rows = con.execute(f"""
            SELECT DATE(analiz_tarihi) as tarih, COUNT(*) as sayi
            FROM analizler
            WHERE analiz_tarihi >= datetime('now', '-{gun_sayisi} days')
            GROUP BY DATE(analiz_tarihi)
            ORDER BY tarih ASC
        """).fetchall()

    return [{"tarih": r["tarih"], "sayi": r["sayi"]} for r in rows]


def sifirla() -> int:
    """Tüm analizleri siler. Silinen kayıt sayısını döner."""
    with baglanti() as con:
        sayi = con.execute("SELECT COUNT(*) FROM analizler").fetchone()[0]
        con.execute("DELETE FROM analizler")
        con.execute("DELETE FROM sqlite_sequence WHERE name='analizler'")
    print(f"[DB] {sayi} kayıt silindi.")
    return sayi


def message_id_var_mi(message_id: str) -> bool:
    """Bu mail daha önce analiz edildi mi?"""
    with baglanti() as con:
        sonuc = con.execute(
            "SELECT 1 FROM analizler WHERE message_id = ?", (message_id,)
        ).fetchone()
    return sonuc is not None