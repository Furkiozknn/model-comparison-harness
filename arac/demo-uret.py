#!/usr/bin/env python3
"""README demosunu ve gunluk video hattinin ham terminal kaydini uretir.

Komutlari GERCEKTEN kosturur (bu deponun kokunden, `uv run mch ...`), ciktisini
ve cikis kodunu `docs/demo/komutlar.txt`'ye yazar, sonra ayni kayittan kareleri
cizer: yazma animasyonu + satir satir cikti. Cikti elle yazilmaz ya da
duzenlenmez; ekrandaki her satir bir komutun gercek ciktisidir.

    uv run --with pillow python arac/demo-uret.py [--video terminal.mp4] [--gif docs/demo/demo.gif]

Gereksinim: ffmpeg PATH'te, Pillow (yukaridaki `--with pillow`). Yazi tipi
`assets/yazi/` (JetBrains Mono, SIL OFL 1.1). Renkler FRK-OS paletinden; yalniz
boyama, metin degismez.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

KOK = Path(__file__).resolve().parent.parent
YAZI = KOK / "assets" / "yazi" / "JetBrainsMono-Regular.ttf"

ZEMIN, PANEL, YAZI_RENGI = "#0e0d0b", "#14120e", "#f1ece2"
SONUK, SARI, MERCAN, CAMGOBEGI = "#b6ae9d", "#ffc21a", "#ff4d6d", "#19d3e6"

# (gosterilecek mch argumanlari) - `uv run mch ...` olarak kosulur.
GOSTERILEN = [
    ["--version"],
    ["validate", "examples/compare-mocks.yaml"],
    ["run", "examples/compare-mocks.yaml", "--input", '{"prompt": "a cat riding a bike"}'],
    ["run", "examples/compare-mocks.yaml", "--input", '{"prompt": "a cat riding a bike"}',
     "--timeout", "0.5", "--fail-on-error"],
]
# Yalniz kayda girer (videoda gosterilmez): ilk yanlislar.
SADECE_KAYIT = [
    ["run", "examples/yok.yaml", "--input", "{}"],
    ["run", "examples/compare-mocks.yaml", "--input", "{prompt: x}"],
]


def kostur(arglar: list[str]) -> dict:
    komut = ["uv", "run", "mch", *arglar]
    t0 = time.perf_counter()
    # Baska bir venv'den (uvx/pipx) cagrilinca uv'nin "VIRTUAL_ENV does not match" uyarisi ciktiya karisir.
    env = {k: v for k, v in os.environ.items() if k != "VIRTUAL_ENV"}
    p = subprocess.run(komut, cwd=KOK, capture_output=True, text=True, encoding="utf-8", check=False, env=env)
    sure = time.perf_counter() - t0
    cikti = (p.stdout + p.stderr).rstrip("\n")
    return {"satir": "$ " + shlex.join(["uv", "run", "mch", *arglar]), "cikti": cikti,
            "kod": p.returncode, "sure": sure, "gorunur": None}


def kayit_yaz(kayitlar: list[dict], yol: Path) -> None:
    surum = subprocess.run(["uv", "run", "mch", "--version"], cwd=KOK, capture_output=True, text=True).stdout.strip()
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=KOK, capture_output=True, text=True).stdout.strip()
    parcalar = [
        "# model-comparison-harness terminal demo: real commands, real output (mock backends only, no API key)",
        f"# tarih: {dt.datetime.now().astimezone().isoformat(timespec='seconds')}",
        f"# surum: {surum} (depo kokunden `uv run mch`, HEAD {sha} + calisma agaci)",
        "# hicbir satir elle yazilmadi; arac/demo-uret.py komutlari kosturup ciktiyi oldugu gibi yazar",
        "# tum latency degerleri MOCK backend'lerinin ayarli gecikmeleridir (0.1 / 1.2 / 0.3 s); gercek bir modelin olcumu degildir",
        "",
    ]
    for k in kayitlar:
        parcalar += [k["satir"], k["cikti"], f"[cikis kodu {k['kod']}] ({k['sure']:.2f} s)", ""]
    yol.parent.mkdir(parents=True, exist_ok=True)
    yol.write_text("\n".join(parcalar), encoding="utf-8", newline="\n")


def satir_rengi(s: str) -> str:
    if s.startswith("$ "):
        return YAZI_RENGI
    if s.startswith(("error:", "INVALID")):
        return MERCAN
    if s.startswith("hint:"):
        return SARI
    if " error " in s and "ERROR:" in s:
        return MERCAN
    if s.startswith(("----------", "backend ")):
        return SONUK
    return YAZI_RENGI


def olay_dizisi(kayitlar: list[dict]) -> list[tuple[float, list[tuple[str, str]]]]:
    """(saniye, ekrandaki satirlar) - ekran her olayda tam yeniden cizilir."""
    ekran: list[tuple[str, str]] = []
    olaylar: list[tuple[float, list[tuple[str, str]]]] = []
    t = 0.4
    olaylar.append((t, list(ekran)))
    for k in kayitlar:
        # Ekran yalniz son iki komutun ciktisini tutsun diye eskiler kayar (asagida kirpilir).
        istem = k["satir"]
        for i in range(2, len(istem) + 1):
            olaylar.append((t, [*ekran, (istem[:i] + "▌", "yaz")]))
            t += 0.03
        ekran.append((istem, "yaz"))
        olaylar.append((t, list(ekran)))
        t += 0.35 + (min(k["sure"], 1.6) if k["gorunur"] == "bekle" else 0)
        for satir in k["cikti"].splitlines():
            ekran.append((satir, "cikti"))
            olaylar.append((t, list(ekran)))
            t += 0.10
        ekran.append((f"[cikis kodu {k['kod']}]", "kod"))
        olaylar.append((t, list(ekran)))
        t += 0.9
        ekran.append(("", "bos"))
    olaylar.append((t + 1.6, list(ekran)))
    return olaylar


def ciz(satirlar: list[tuple[str, str]], gen: int, yuk: int, punto: int, panel_kutu, font) -> Image.Image:
    im = Image.new("RGB", (gen, yuk), ZEMIN)
    d = ImageDraw.Draw(im)
    x0, y0, x1, y1 = panel_kutu
    d.rounded_rectangle((x0, y0, x1, y1), radius=10, fill=PANEL, outline="#2a261e")
    d.rectangle((x0, y0 + 12, x0 + 5, y1 - 12), fill=SARI)
    lh = int(punto * 1.45)
    y = y0 + 22
    for s, tur in satirlar:
        if tur == "yaz":
            renk = YAZI_RENGI
            if s.startswith("$ "):
                d.text((x0 + 24, y), "$", font=font, fill=SARI)
                d.text((x0 + 24 + font.getlength("$ "), y), s[2:], font=font, fill=renk)
            else:
                d.text((x0 + 24, y), s, font=font, fill=renk)
        elif tur == "kod":
            d.text((x0 + 24, y), s, font=font, fill=SONUK)
        elif s:
            d.text((x0 + 24, y), s, font=font, fill=satir_rengi(s))
        y += lh
    return im


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", type=Path, help="1080x1920 sessiz mp4 (yazisiz, ham)")
    ap.add_argument("--gif", type=Path, default=KOK / "docs" / "demo" / "demo.gif")
    ap.add_argument("--kayit", type=Path, default=KOK / "docs" / "demo" / "komutlar.txt")
    a = ap.parse_args()

    if shutil.which("ffmpeg") is None:
        sys.exit("ffmpeg PATH'te degil")
    kayitlar = [kostur(x) for x in GOSTERILEN]
    kayitlar[2]["gorunur"] = "bekle"  # gercek kosu ~1.2 s surer; ekranda da bekletilir
    kayitlar[3]["gorunur"] = "bekle"
    kayit_yaz(kayitlar + [kostur(x) for x in SADECE_KAYIT], a.kayit)

    # Satir genisligi: en uzun ekran satirina gore punto secilir (dikey videoda 1080 px'e sigsin).
    en_uzun = max(len(s) for k in kayitlar for s in [k["satir"], *k["cikti"].splitlines()])
    olaylar = olay_dizisi(kayitlar)
    max_satir = 22  # ekran bu kadar satiri tutar; fazlasi ustten kayar
    hedefler = []
    if a.video:
        hedefler.append(("video", 1080, 1920))
    hedefler.append(("gif", 0, 0))

    for ad, W, H in hedefler:
        if ad == "video":
            punto = int((W - 2 * 20 - 48) / (0.6 * (en_uzun + 1)))
            genis = W
        else:
            punto = 15
            genis = int(48 + 0.6 * punto * (en_uzun + 2) + 2 * 20)
        font = ImageFont.truetype(str(YAZI), punto)
        lh = int(punto * 1.45)
        panel_yuk = 44 + lh * max_satir
        if ad == "gif":
            W, H = genis, panel_yuk + 40
        kutu = (20, (H - panel_yuk) // 2 if ad == "video" else 20, W - 20,
                ((H - panel_yuk) // 2 if ad == "video" else 20) + panel_yuk)

        fps = 15 if ad == "video" else 10
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            toplam = olaylar[-1][0]
            n = int(toplam * fps) + 1
            j = 0
            for f in range(n):
                t = f / fps
                while j + 1 < len(olaylar) and olaylar[j + 1][0] <= t:
                    j += 1
                satirlar = olaylar[j][1][-max_satir:]
                ciz(satirlar, W, H, punto, kutu, font).save(td / f"f{f:05d}.png")
            if ad == "video":
                a.video.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(td / "f%05d.png"),
                                "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
                                "-r", str(fps), str(a.video)], check=True)
                print(f"video: {a.video} ({toplam:.1f} s, {W}x{H})")
            else:
                a.gif.parent.mkdir(parents=True, exist_ok=True)
                pal = td / "pal.png"
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(td / "f%05d.png"),
                                "-vf", "palettegen=max_colors=32", str(pal)], check=True)
                subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(td / "f%05d.png"),
                                "-i", str(pal), "-lavfi", "paletteuse=dither=none", str(a.gif)], check=True)
                print(f"gif: {a.gif} ({toplam:.1f} s, {W}x{H})")


if __name__ == "__main__":
    main()
