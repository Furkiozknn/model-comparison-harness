#!/usr/bin/env python3
"""README'deki terminal goruntusunu (assets/terminal-run.svg) uretir.

Komutu gercekten calistirir ve stdout'u oldugu gibi bir SVG'ye yazar; elle
cizilmis ya da duzenlenmis bir cikti degildir. `mch` hic renk basmadigi icin
goruntu de renksizdir, yalniz istem satiri renkli.

    uv run python arac/terminal-goruntusu.py

Gecikme sutunu kosudan kosuya bir iki milisaniye oynar; dosya bu yuzden her
calistirmada biraz degisir.
"""

from __future__ import annotations

import shlex
import subprocess
from html import escape
from pathlib import Path

KOK = Path(__file__).resolve().parent.parent
KOMUT = ["mch", "run", "examples/compare-mocks.yaml", "--input", '{"prompt": "a cat riding a bike"}']
CIKIS = KOK / "assets" / "terminal-run.svg"

KARAKTER_GENISLIGI = 7.83  # 13px monospace
SATIR_YUKSEKLIGI = 18
KENAR = 16
UST_CUBUK = 28


def main() -> None:
    sonuc = subprocess.run(["uv", "run", *KOMUT], cwd=KOK, capture_output=True, text=True, check=False)
    satirlar = sonuc.stdout.rstrip("\n").splitlines()
    istem = "$ uv run " + shlex.join(KOMUT)
    tum = [istem, *satirlar]

    # +4 karakter pay: yedek es genislikli yazitipleri biraz daha genis olabilir.
    genislik = int(KENAR * 2 + KARAKTER_GENISLIGI * (max(len(s) for s in tum) + 4))
    yukseklik = UST_CUBUK + KENAR + SATIR_YUKSEKLIGI * len(tum) + KENAR // 2

    parcalar = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {genislik} {yukseklik}" '
        f'width="{genislik}" height="{yukseklik}" role="img" '
        f'aria-label="Real output of: {escape(istem, quote=True)}">',
        f"<title>{escape(istem)}</title>",
        f'<rect width="{genislik}" height="{yukseklik}" rx="8" fill="#0d1117"/>',
        f'<rect width="{genislik}" height="{UST_CUBUK}" rx="8" fill="#161b22"/>',
        f'<rect y="{UST_CUBUK - 8}" width="{genislik}" height="8" fill="#161b22"/>',
        '<circle cx="18" cy="14" r="5" fill="#ff5f56"/>',
        '<circle cx="36" cy="14" r="5" fill="#ffbd2e"/>',
        '<circle cx="54" cy="14" r="5" fill="#27c93f"/>',
        '<g font-family="ui-monospace,SFMono-Regular,Menlo,Consolas,monospace" font-size="13" '
        'xml:space="preserve" style="white-space:pre">',
    ]
    for i, satir in enumerate(tum):
        y = UST_CUBUK + KENAR + SATIR_YUKSEKLIGI * i + 4
        renk = "#7ee787" if i == 0 else "#e6edf3"
        # Bosluklar U+00A0: tarayicilar SVG metnindeki ardisik bosluklari
        # xml:space/white-space'e ragmen tek bosluga indirebiliyor ve
        # sutunlar kayiyor. Es genislikli yazitipinde genislik ayni.
        metin = escape(satir).replace(" ", "\u00a0")
        parcalar.append(f'<text x="{KENAR}" y="{y}" fill="{renk}">{metin}</text>')
    parcalar += ["</g>", "</svg>", ""]

    CIKIS.write_text("\n".join(parcalar), encoding="utf-8")
    print(f"{CIKIS.relative_to(KOK)}: {len(satirlar)} satir, cikis kodu {sonuc.returncode}")


if __name__ == "__main__":
    main()
