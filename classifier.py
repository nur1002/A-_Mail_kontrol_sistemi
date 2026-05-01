"""
Şikayet Kategori Sınıflandırıcısı
==================================

Mimari: TF-IDF (word + character n-gram) → Logistic Regression
Kategori sayısı: 4 (kargo / iade / teknik / fatura)

Neden bu yaklaşım?
- TF-IDF: Türkçe sondan eklemeli olduğu için karakter n-gram (3-5)
  kelime köklerini yakalar (örn. "kargom", "kargosu" benzer vektörlenir)
- LogisticRegression: hızlı, yorumlanabilir, küçük veride iyi çalışır
- Pipeline: tek nesne hâlinde kaydedilebilir, inference'da pratik

Hız: Tek mail tahmini ~5-10ms (CPU'da)
"""

from pathlib import Path

import joblib
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import FeatureUnion, Pipeline

from preprocessing import temizle_mail

VERI_YOLU = Path(__file__).parent / "merged.csv"
MODEL_YOLU = Path(__file__).parent.parent / "models" / "classifier.pkl"


def veri_hazirla():
    """CSV'yi oku, mailları temizle, eğitim için hazır metin döndür."""
    df = pd.read_csv(VERI_YOLU)
    df["govde_temiz"] = df["govde"].apply(temizle_mail)
    # Konu da güçlü bir sinyal — birleştir
    df["metin"] = df["konu"].fillna("") + " " + df["govde_temiz"]
    return df


def pipeline_olustur():
    """TF-IDF (word + char n-gram) + LogReg pipeline'ı."""
    return Pipeline([
        ("ozellikler", FeatureUnion([
            # Kelime n-gramları: "iade etmek", "param iade" gibi ifadeler
            ("word", TfidfVectorizer(
                analyzer="word",
                ngram_range=(1, 2),
                min_df=2,
                max_df=0.9,
                sublinear_tf=True,
            )),
            # Karakter n-gramları: Türkçe çekim eklerini yakalamak için
            # ("kargom", "kargosu", "kargonun" hepsi benzer şekilde vektörlenir)
            ("char", TfidfVectorizer(
                analyzer="char_wb",
                ngram_range=(3, 5),
                min_df=2,
                max_df=0.95,
                sublinear_tf=True,
            )),
        ])),
        ("siniflandirici", LogisticRegression(
            max_iter=1000,
            C=1.0,
            class_weight="balanced",
            random_state=42,
        )),
    ])


def egit():
    print("Veri yükleniyor ve temizleniyor...")
    df = veri_hazirla()
    print(f"  Toplam: {len(df)} mail")
    print(f"  Kategoriler: {dict(df['kategori'].value_counts())}\n")

    X = df["metin"].values
    y = df["kategori"].values

    # Stratified split — her kategoriden eşit oranda test'e ayır
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )
    print(f"Train: {len(X_train)}, Test: {len(X_test)}")

    print("\nModel eğitiliyor...")
    pipeline = pipeline_olustur()
    pipeline.fit(X_train, y_train)

    # Değerlendirme
    y_pred = pipeline.predict(X_test)
    acc = accuracy_score(y_test, y_pred)
    print(f"\n{'='*60}")
    print(f"Test Accuracy: {acc:.4f}")
    print(f"{'='*60}")

    print("\nDetaylı Rapor:")
    print(classification_report(y_test, y_pred, digits=3))

    print("Confusion Matrix:")
    etiketler = sorted(set(y))
    cm = confusion_matrix(y_test, y_pred, labels=etiketler)
    print(f"{'':>10}" + "".join(f"{e:>10}" for e in etiketler))
    for i, e in enumerate(etiketler):
        print(f"{e:>10}" + "".join(f"{cm[i][j]:>10}" for j in range(len(etiketler))))

    # Modeli kaydet
    MODEL_YOLU.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipeline, MODEL_YOLU)
    print(f"\nModel kaydedildi: {MODEL_YOLU}")

    return pipeline


def tahmin_et(metin: str, model=None):
    """Tek bir mail için kategori tahmini + güven skoru.

    Returns:
        dict: {
            'kategori': str,
            'guven': float (0-1),
            'tum_skorlar': {kategori: skor}
        }
    """
    if model is None:
        model = joblib.load(MODEL_YOLU)

    metin_temiz = temizle_mail(metin)
    if not metin_temiz:
        return {"kategori": "bilinmeyen", "guven": 0.0, "tum_skorlar": {}}

    pred = model.predict([metin_temiz])[0]
    proba = model.predict_proba([metin_temiz])[0]
    classes = model.classes_

    return {
        "kategori": pred,
        "guven": float(max(proba)),
        "tum_skorlar": {classes[i]: float(proba[i]) for i in range(len(classes))},
    }


if __name__ == "__main__":
    model = egit()

    # Gerçek dünyadan 6 örnek üzerinde test
    print(f"\n{'='*60}")
    print("ÖRNEK TAHMİNLER (modelin görmediği yeni metinler)")
    print(f"{'='*60}")

    ornekler = [
        "Merhaba, sipariş ettiğim ürün 1 haftadır gelmedi, kargo nerede kaldı?",
        "Hesabımdan iki kez para çekildi, fazla tutarı iade etmenizi istiyorum.",
        "Aldığım laptop hiç açılmıyor, kutudan bozuk çıkmış galiba.",
        "Ürünü iade etmek istiyorum, beden uymadı.",
        "Tüketici hakem heyetine başvurdum, paramı vermiyorsunuz!",
        # Zorlu: belirsiz/karma örnek
        "Ürün geldi ama bozuk, ne yapacağımı bilmiyorum.",
    ]

    for ornek in ornekler:
        sonuc = tahmin_et(ornek, model)
        guven_pct = sonuc["guven"] * 100
        bar = "█" * int(guven_pct / 5)
        print(f"\n  '{ornek}'")
        print(f"  → {sonuc['kategori'].upper():8s} {bar} %{guven_pct:.1f}")