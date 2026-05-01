"""
E-ticaret Şikayet Sentetik Veri Üreticisi
==========================================

Strateji: Elle yazılmış gerçekçi şablonlar + rastgele varyasyonlarla çoğaltma.

Üretilen alanlar:
- mail_id: benzersiz id
- gonderen_ad: müşteri adı
- konu: mail başlığı
- govde: mail içeriği (signature, quote, vs. dahil ham haliyle)
- kategori: iade / kargo / teknik / fatura
- aciliyet: 1 (düşük) - 5 (kritik)
- tarih: gönderim tarihi
"""

import csv
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)  # tekrarlanabilirlik için

# ---------------------------------------------------------------------------
# RASTGELE BİLEŞENLER
# ---------------------------------------------------------------------------

ISIMLER = [
    "Ayşe Yılmaz", "Mehmet Demir", "Fatma Kaya", "Ahmet Çelik", "Zeynep Şahin",
    "Mustafa Öztürk", "Elif Arslan", "Hüseyin Doğan", "Emine Aydın", "Ali Yıldız",
    "Hatice Özdemir", "İbrahim Kurt", "Merve Polat", "Hasan Koç", "Selin Aksoy",
    "Burak Erdoğan", "Gamze Çetin", "Onur Yıldırım", "Esra Acar", "Kerem Tekin",
]

URUNLER_KARGO_IADE = [
    "Bluetooth kulaklık", "kahve makinesi", "spor ayakkabı", "akıllı saat",
    "elektrikli süpürge", "blender", "yatak takımı", "mont", "kazak", "valiz",
]

URUNLER_TEKNIK = [
    "telefon", "laptop", "kulaklık", "saç kurutma makinesi", "ütü",
    "tost makinesi", "monitör", "tablet", "kahve makinesi", "robot süpürge",
]

# Mail kuyruk şablonları (preprocessing'de temizlenecek)
SIGNATURELAR = [
    "\n\nİyi günler dilerim,\n{ad}",
    "\n\nSaygılarımla,\n{ad}",
    "\n\nTeşekkürler,\n{ad}\nTel: 0{tel}",
    "\n\n--\n{ad}",
    "",  # bazıları imza içermesin
]

QUOTE_BLOKLARI = [
    "\n\n----- Önceki Mesaj -----\nMerhaba {ad},\nMüşteri hizmetlerimize ulaştığınız için teşekkür ederiz. Talebinizi inceleyip size dönüş yapacağız.\nİyi günler.",
    "\n\n> {ad} şunu yazdı:\n> Siparişim hala elime ulaşmadı, lütfen yardımcı olun.",
    "",  # quote içermesin
    "",
    "",
]

# ---------------------------------------------------------------------------
# KATEGORİ ŞABLONLARI
# Her şablon: (konu, gövde_template, aciliyet_aralık)
# Gövdede {ad}, {urun}, {siparis_no}, {tarih}, {tutar} placeholder'ları olur
# ---------------------------------------------------------------------------

KARGO_SABLONLAR = [
    # Sakin (aciliyet 1-2)
    ("Kargo durumu hakkında", 
     "Merhaba, {tarih} tarihinde verdiğim {siparis_no} numaralı siparişimin durumunu öğrenmek istiyorum. Kargo takip linki çalışmıyor. Bilgi verirseniz sevinirim.", 
     (1, 2)),
    ("Sipariş takibi",
     "İyi günler, sipariş ettiğim {urun} için kargo numarası tarafıma iletilmedi. Yardımcı olabilir misiniz? Sipariş no: {siparis_no}",
     (1, 2)),
    
    # Orta (aciliyet 3)
    ("Sipariş gelmedi",
     "Merhaba, {tarih} tarihinde sipariş ettiğim {urun} hala elime ulaşmadı. Kargo takip sisteminde 5 gündür hareket yok. Lütfen acil bilgi verin. Sipariş no: {siparis_no}",
     (3, 3)),
    ("Geç teslimat",
     "Siparişim {siparis_no} 3 gün önce teslim edilmiş görünüyor ama bana ulaşmadı. Apartman görevlisi de almamış. Bu konuda ne yapmamı önerirsiniz?",
     (3, 4)),
    
    # Yüksek (aciliyet 4)
    ("Kargom kayıp!!",
     "Bu nasıl bir hizmet anlayışı? 10 GÜNDÜR siparişimi bekliyorum. Kargo şirketini aradım, sizden bilgi gelmediğini söylüyor. {siparis_no} numaralı siparişim derhal teslim edilsin yoksa iptal istiyorum.",
     (4, 4)),
    ("Kargo şirketi rezalet",
     "{urun} siparişim için kargo görevlisi geldi, ben evdeyken zile bile basmadan 'adres bulunamadı' yazıp gitmiş. Bu üçüncü sefer oluyor. Kargo şirketinizi değiştirin artık!",
     (3, 4)),
    
    # Kritik (aciliyet 5)
    ("YASAL İŞLEM BAŞLATACAĞIM",
     "15 gündür siparişim gelmedi, paramı da iade etmiyorsunuz. Tüketici hakem heyetine başvuruyorum. Avukatımla görüştüm, bu konuyu yargıya taşıyacağız. Sipariş no: {siparis_no} Tutar: {tutar} TL",
     (5, 5)),
]

IADE_SABLONLAR = [
    # Sakin
    ("İade prosedürü hakkında",
     "Merhaba, {siparis_no} numaralı siparişimdeki {urun} ürününü iade etmek istiyorum. İade sürecini nasıl başlatabilirim? Teşekkürler.",
     (1, 2)),
    ("Ürün beğenilmedi",
     "İyi günler, aldığım {urun} beklediğim gibi çıkmadı. 14 gün içinde olduğum için iade etmek istiyorum. Yönlendirme rica ederim.",
     (1, 2)),
    ("Beden değişimi",
     "Sipariş ettiğim ürün bana büyük geldi. Bir beden küçüğüyle değişim yapmak mümkün mü? Sipariş: {siparis_no}",
     (1, 2)),
    
    # Orta
    ("İade onayı bekliyorum",
     "{tarih} tarihinde iade kargosu gönderdim, ulaşmasına rağmen 7 gündür onay gelmedi. Param ne zaman iade olacak? {siparis_no}",
     (3, 3)),
    ("İade kabul edilmedi",
     "İade ettiğim ürünüm 'kullanılmış' diye geri gönderildi. Oysa sadece denedim, etiketleri bile durmaktaydı. Bu kabul edilemez.",
     (3, 4)),
    
    # Yüksek
    ("Param hala iade edilmedi",
     "20 GÜN ÖNCE iade ettim, hala param hesabıma gelmedi. Her aradığımda 'işleme alındı' diyorsunuz. Artık ne demek istediğinizi anlamıyorum. Tutar: {tutar} TL",
     (4, 5)),
    
    # Kritik
    ("Tüketici Hakem Heyeti",
     "İade işlemim 1 ay önce yapıldı, paramı vermiyorsunuz. {tutar} TL tutarındaki iadem için tüketici hakem heyetine bugün başvuruyorum. Mahkeme masraflarını da sizden talep edeceğim. Sipariş: {siparis_no}",
     (5, 5)),

    # Yanlış ürün / farklı ürün gönderildi
    ("Yanlış ürün gönderildi",
     "Merhaba, sipariş ettiğim {urun} yerine farklı bir ürün gönderilmiş. Bu hatanın düzeltilmesini rica ediyorum. Nasıl bir yol izlemem gerekiyor? Sipariş: {siparis_no}",
     (2, 3)),
    ("Sipariş etmediğim ürün geldi",
     "İyi günler, {siparis_no} numaralı siparişimi teslim aldım ancak içinden {urun} değil bambaşka bir ürün çıktı. Yanlış ürün gönderilmiş, değişim yapmak istiyorum.",
     (2, 3)),
    ("Farklı renk/model geldi",
     "Sipariş ettiğim ürünün rengi/modeli farklı geldi. Seçtiğim ürünü değil başkasını göndermişsiniz. İade veya değişim talep ediyorum. {siparis_no}",
     (2, 3)),
    ("Yanlış ürün - acil değişim",
     "SİPARİŞİMİ YANLIŞ GÖNDERDİNİZ! {urun} siparişi verdim, etiket bile doğru değil. Derhal doğru ürünü gönderin ya da paramı iade edin. {siparis_no}",
     (4, 4)),


]

TEKNIK_SABLONLAR = [
    # Sakin
    ("Ürün kullanımı",
     "Merhaba, yeni aldığım {urun} kutudan çıktığında nasıl şarj edilmesi gerekiyor? Kullanım kılavuzunda net bilgi yok.",
     (1, 1)),
    ("Garanti sorusu",
     "İyi günler, {tarih} tarihinde aldığım {urun} için garanti belgesini nereden temin edebilirim?",
     (1, 2)),
    
    # Orta
    ("Ürün arızalı",
     "Aldığım {urun} ilk açılışta çalıştı ama 2 gün sonra hiç açılmıyor. Tuşa basıyorum tepki vermiyor. Sipariş: {siparis_no}",
     (3, 3)),
    ("Eksik parça",
     "{urun} kutusundan eksik parça çıktı. Şarj kablosu yok. Hemen gönderilmesini rica ediyorum.",
     (2, 3)),
    ("Ürün tarif edildiği gibi değil",
     "Ürün açıklamasında 'su geçirmez' yazıyordu, oysa elime aldığım modelde böyle bir özellik yok. Yanıltıcı bilgi vermişsiniz. Sipariş: {siparis_no}",
     (3, 4)),
    
    # Yüksek
    ("Ürün TEHLİKELİ",
     "Aldığım {urun} kullanırken kıvılcım çıkardı, neredeyse yangın çıkıyordu! Çocuğumun yanında kullanıyordum, bir şey olsa kim sorumlu olacaktı? Bu ürün geri çağrılmalı!!",
     (5, 5)),
    ("Bozuk ürün geldi değişim",
     "İkinci defa bozuk ürün geliyor. İlkini değiştirdiniz, gelen de aynı sorunu veriyor. {urun} modeli kalitesizmiş. Param iade edilsin.",
     (4, 4)),
]

FATURA_SABLONLAR = [
    # Sakin
    ("Fatura talebi",
     "Merhaba, {siparis_no} numaralı siparişim için e-fatura tarafıma ulaşmadı. Tekrar gönderebilir misiniz?",
     (1, 1)),
    ("Fatura bilgileri",
     "İyi günler, faturada şirket bilgilerimi güncellemek istiyorum. Yeni vergi numaramı nereden bildirebilirim?",
     (1, 2)),
    
    # Orta
    ("Fatura tutarı yanlış",
     "Sipariş tutarım {tutar} TL idi ama faturada farklı bir tutar görünüyor. Kontrol eder misiniz? {siparis_no}",
     (2, 3)),
    ("İndirim uygulanmadı",
     "Kupon kodumu kullandım ama faturaya yansımamış. Kupon kodu: HOSGELDIN20. Fark olan {tutar} TL'yi iade etmenizi rica ederim.",
     (2, 3)),
    
    # Yüksek
    ("ÇİFT ÇEKİM YAPILDI",
     "Hesabımdan AYNI siparişin tutarı İKİ KEZ çekildi! {tutar} TL fazladan paramı alın geri verin. Bankayı arıyorum şu an. Sipariş: {siparis_no}",
     (4, 5)),
    ("Hatalı ücretlendirme",
     "İptal ettiğim siparişin tutarı hala kartımdan tahsil edilmiş görünüyor. {tutar} TL nerede? 5 gündür arıyorum kimse cevap vermiyor.",
     (4, 4)),
    
    # Kritik
    ("DOLANDIRICILIK",
     "Hiç sipariş vermediğim halde kartımdan {tutar} TL çekilmiş! Kart bilgilerim sizden mi sızdı? BDDK'ya ve savcılığa şikayette bulunuyorum. Acil müdahale!",
     (5, 5)),
]

# ---------------------------------------------------------------------------
# YARDIMCI FONKSİYONLAR
# ---------------------------------------------------------------------------

def rastgele_siparis_no():
    return f"SP{random.randint(100000, 999999)}"

def rastgele_tarih():
    gun_oncesi = random.randint(1, 60)
    tarih = datetime.now() - timedelta(days=gun_oncesi)
    return tarih.strftime("%d.%m.%Y")

def rastgele_tutar():
    return random.choice([149, 299, 459, 799, 1250, 1899, 2450, 3299, 4750, 7999])

def rastgele_telefon():
    return f"5{random.randint(30, 59)}{random.randint(1000000, 9999999)}"

def doldur_govde(template, ad, kategori):
    """Şablonu doldur ve signature/quote ekle."""
    if kategori in ("kargo", "iade"):
        urun = random.choice(URUNLER_KARGO_IADE)
    else:
        urun = random.choice(URUNLER_TEKNIK)
    
    govde = template.format(
        ad=ad.split()[0],
        urun=urun,
        siparis_no=rastgele_siparis_no(),
        tarih=rastgele_tarih(),
        tutar=rastgele_tutar(),
    )
    
    # Signature ekle
    sig = random.choice(SIGNATURELAR).format(ad=ad, tel=rastgele_telefon())
    
    # Quote bloğu ekle (bazılarına)
    quote = random.choice(QUOTE_BLOKLARI).format(ad=ad)
    
    return govde + sig + quote

# ---------------------------------------------------------------------------
# ANA ÜRETİCİ
# ---------------------------------------------------------------------------

def uret_veriseti(toplam=500):
    kategori_sablon_map = {
        "kargo": KARGO_SABLONLAR,
        "iade": IADE_SABLONLAR,
        "teknik": TEKNIK_SABLONLAR,
        "fatura": FATURA_SABLONLAR,
    }
    
    veriler = []
    mail_id = 1
    
    # Her kategoriden dengeli sayıda üret
    her_kategori_adet = toplam // 4
    
    for kategori, sablonlar in kategori_sablon_map.items():
        for _ in range(her_kategori_adet):
            sablon = random.choice(sablonlar)
            konu, govde_t, aciliyet_aralik = sablon
            
            ad = random.choice(ISIMLER)
            govde = doldur_govde(govde_t, ad, kategori)
            aciliyet = random.randint(*aciliyet_aralik)
            
            tarih = (datetime.now() - timedelta(days=random.randint(0, 30))).strftime("%Y-%m-%d %H:%M")
            
            veriler.append({
                "mail_id": mail_id,
                "gonderen_ad": ad,
                "konu": konu,
                "govde": govde,
                "kategori": kategori,
                "aciliyet": aciliyet,
                "tarih": tarih,
            })
            mail_id += 1
    
    random.shuffle(veriler)
    
    # Mail id'leri yeniden numaralandır
    for i, v in enumerate(veriler, 1):
        v["mail_id"] = i
    
    return veriler


def kaydet_csv(veriler, dosya_yolu):
    Path(dosya_yolu).parent.mkdir(parents=True, exist_ok=True)
    with open(dosya_yolu, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=veriler[0].keys())
        writer.writeheader()
        writer.writerows(veriler)
    print(f"✓ {len(veriler)} adet sentetik şikayet üretildi: {dosya_yolu}")


if __name__ == "__main__":
    veriler = uret_veriseti(toplam=500)
    kaydet_csv(veriler, "../data/synthetic_complaints.csv")
    
    # İstatistik özet
    from collections import Counter
    kat_sayim = Counter(v["kategori"] for v in veriler)
    aci_sayim = Counter(v["aciliyet"] for v in veriler)
    
    print("\nKategori dağılımı:")
    for k, s in sorted(kat_sayim.items()):
        print(f"  {k:10s}: {s}")
    
    print("\nAciliyet dağılımı:")
    for a, s in sorted(aci_sayim.items()):
        print(f"  Seviye {a}: {s}")
    
    print("\nÖrnek 3 mail:")
    print("=" * 70)
    for v in veriler[:3]:
        print(f"[{v['kategori'].upper()}] [Aciliyet: {v['aciliyet']}] {v['konu']}")
        print(f"Gönderen: {v['gonderen_ad']}")
        print(f"---\n{v['govde']}\n")
        print("=" * 70)