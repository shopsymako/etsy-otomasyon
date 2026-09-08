"""
ANA OTOMASYON SCRIPTI

Bu script:
1. Verilen tasarim gorselini (Claude'a) gonderip Etsy icin baslik,
   aciklama ve etiket ürettirir.
2. sablon_detay.json'daki sablonu kullanarak Gelato'da yeni bir urun
   olusturur (tum varyantlara ayni gorseli uygular).
3. isteğe bagli olarak urunu direkt Etsy'de yayinlar.

ONEMLI: Gelato'nun gorseli internetten cekebilmesi icin, gorselin
INTERNETTEN ERISILEBILIR bir URL'i olmasi gerekiyor (bilgisayarindaki
dosya yolu degil). Bu yuzden once gorseli GitHub reponuza (ornegin
'designs/' klasorune) yuklemen ve o dosyanin "raw" linkini kullanman
gerekiyor.

GitHub'da bir dosyanin raw linkini almak icin:
  https://raw.githubusercontent.com/KULLANICI_ADI/REPO_ADI/main/designs/dosya.png

Kullanim:
  python create_product.py --local-image "C:\\yol\\tasarim.png" --image-url "https://raw.githubusercontent.com/.../tasarim.png" --publish
  (--publish vermezsen urun Etsy'de "Draft" olarak kalir, ekstra ucret alinmaz)
"""

import argparse
import base64
import io
import json
import mimetypes
import os
import subprocess
import requests
from PIL import Image


def load_env(path=".env"):
    """.env dosyasi varsa oradan, yoksa ortam degiskenlerinden (GitHub Actions
    secrets gibi) okur. Ikisi de varsa .env oncelikli."""
    env = dict(os.environ)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                env[key.strip()] = value.strip()
    return env


def find_new_design_from_git():
    """GitHub Actions icinde, son commit'te 'designs/' altina eklenen
    gorseli bulur. Yerel (manuel) calistirmada kullanilmaz."""
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD~1", "HEAD"],
        capture_output=True, text=True,
    )
    changed_files = result.stdout.strip().split("\n")
    image_exts = (".png", ".jpg", ".jpeg")
    for f in changed_files:
        if f.startswith("designs/") and f.lower().endswith(image_exts):
            return f
    return None


def generate_listing_text(anthropic_key, image_path):
    """Gorseli Claude'a gonderip Etsy icin baslik/aciklama/etiket uretir.
    Claude'un 10MB limiti oldugu icin, gonderilecek kopyayi kucultur
    (orijinal dosya Gelato'ya baskı icin degismeden gonderilmeye devam eder)."""
    img = Image.open(image_path)
    if img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    # En uzun kenari 1568px'e indir (Claude'un onerdigi maksimum, hem hizli hem yeterli detay)
    max_dim = 1568
    if max(img.size) > max_dim:
        ratio = max_dim / max(img.size)
        new_size = (int(img.size[0] * ratio), int(img.size[1] * ratio))
        img = img.resize(new_size, Image.LANCZOS)

    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=85)
    image_b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
    mime_type = "image/jpeg"

    prompt = (
        "Bu bir tisort baski tasarimi. Bu tasarima gore Etsy'de satilacak "
        "bir tisort urunu icin SADECE asagidaki JSON formatinda, baska hicbir "
        "aciklama veya markdown olmadan cevap ver:\n\n"
        '{"title": "...", "tags": ["...", "...", ...]}\n\n'
        "2026 Etsy SEO kurallarina gore:\n"
        "- title: Ingilizce, DOGAL/konusma diline yakin (eski usul anahtar "
        "kelime doldurma DEGIL), en onemli anahtar kelime basta, 100-140 "
        "karakter arasi. 'beautiful', 'amazing', 'unique' gibi bos sifatlar "
        "KULLANMA. Marka/magaza adi ekleme. Urun tipi + tema + stil + hedef "
        "kitleyi dogal bir cumle gibi birlestir.\n"
        "- tags: Tam olarak 13 adet, her biri en fazla 20 karakter, Ingilizce. "
        "HER BIRI 2-5 kelimelik ANLAMLI OBEKLER olsun (tek kelime KULLANMA). "
        "Baslikta gecen kelimeleri TEKRARLAMA, etiketler baslkigi tamamlayan "
        "farkli arama ifadeleri olsun. Ayni kelimeyi birden fazla tag'de "
        "kullanma."
    )

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": anthropic_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-5",
            "max_tokens": 1500,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": mime_type,
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        },
    )

    if resp.status_code != 200:
        raise SystemExit(f"Claude API hatasi: {resp.status_code} {resp.text}")

    text = resp.json()["content"][0]["text"].strip()
    # Olasi markdown kod bloklarini temizle
    text = text.replace("```json", "").replace("```", "").strip()
    return json.loads(text)


def create_gelato_product(gelato_key, store_id, template_detail, image_url,
                           title, tags, publish):
    variants = []
    for v in template_detail.get("variants", []):
        placeholders = v.get("imagePlaceholders", [])
        image_placeholders = [
            {"name": ph["name"], "fileUrl": image_url} for ph in placeholders
        ]
        variants.append({
            "templateVariantId": v["id"],
            "imagePlaceholders": image_placeholders,
        })

    body = {
        "templateId": template_detail["id"],
        "title": title,
        # 'description' kasten gonderilmiyor: Gelato bos birakildiginda
        # sablonun kendi sabit aciklamasini otomatik kullanir.
        "isVisibleInTheOnlineStore": publish,
        "tags": tags[:13],
        "variants": variants,
    }

    resp = requests.post(
        f"https://ecommerce.gelatoapis.com/v1/stores/{store_id}/products:create-from-template",
        headers={"X-API-KEY": gelato_key, "Content-Type": "application/json"},
        json=body,
    )

    return resp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--local-image", required=False, help="Bilgisayarindaki tasarim dosyasinin yolu (verilmezse git'ten otomatik bulunur)")
    parser.add_argument("--image-url", required=False, help="Gorselin internetten erisilebilir URL'i (verilmezse otomatik olusturulur)")
    parser.add_argument("--publish", action="store_true", help="Verilirse Etsy'de aninda yayinlanir (ucretli), verilmezse Draft kalir")
    args = parser.parse_args()

    local_image = args.local_image
    image_url = args.image_url

    # Manuel calistirmada bu ikisi verilmemisse, GitHub Actions icinde
    # otomatik olarak son eklenen gorseli bul.
    if not local_image or not image_url:
        detected = find_new_design_from_git()
        if not detected:
            raise SystemExit("HATA: Yeni bir tasarim gorseli bulunamadi (designs/ altinda).")
        local_image = detected
        repo = os.environ.get("GITHUB_REPOSITORY", "shopsymako/etsy-otomasyon")
        image_url = f"https://raw.githubusercontent.com/{repo}/main/{detected}"
        print(f"Otomatik algilanan gorsel: {detected}")

    env = load_env()
    anthropic_key = env.get("ANTHROPIC_API_KEY")
    gelato_key = env.get("GELATO_API_KEY")
    store_id = env.get("GELATO_STORE_ID", "3633f2ce-fcd1-4574-b731-22969cb7daf2")

    if not anthropic_key:
        raise SystemExit("HATA: .env dosyasinda ANTHROPIC_API_KEY bulunamadi.")
    if not gelato_key:
        raise SystemExit("HATA: .env dosyasinda GELATO_API_KEY bulunamadi.")

    with open("sablon_detay.json", "r", encoding="utf-8") as f:
        template_detail = json.load(f)

    print("Gorsel analiz ediliyor, baslik/aciklama/etiket uretiliyor...")
    listing = generate_listing_text(anthropic_key, local_image)

    print("\n--- URETILEN METIN ---")
    print("Baslik:", listing["title"])
    print("Etiketler:", listing["tags"])
    print("(Aciklama sablondan sabit olarak kullanilacak)")
    print("----------------------\n")

    print("Gelato'da urun olusturuluyor...")
    resp = create_gelato_product(
        gelato_key, store_id, template_detail, image_url,
        listing["title"], listing["tags"],
        args.publish,
    )

    print("Durum kodu:", resp.status_code)
    print(resp.text)

    if resp.status_code in (200, 201):
        print("\nBASARILI! Urun olusturuldu.")
        if args.publish:
            print("Etsy'de yayinlanmasi icin arka planda islem devam ediyor.")
        else:
            print("Urun Draft olarak kaydedildi, Etsy'de yayinlamadi.")
    else:
        print("\nHATA olustu, yukaridaki mesaja bak.")


if __name__ == "__main__":
    main()
