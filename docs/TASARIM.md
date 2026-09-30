# Tasarım: model-comparison-harness ilk kullanım ve README yenilemesi (30 Eylül 2026)

## Hedef

Profilden ya da videodan gelen biri ilk dakikada şunu yapabilmeli: aracın ne yaptığını tek cümlede anlamak, tek komutla çalıştırmak (API anahtarı olmadan, mock backend'lerle), ilk komutu yanlış yazarsa doğrusunu ekranda görmek. Çekirdek davranış (runner, backend türleri, `--json`/`--csv` alanları, çıkış kodları) değişmedi; sürüm numarası artmadı (0.1.0). Mevcut testler gevşetilmedi.

## Önce / sonra

| Konu | Önce | Sonra |
|---|---|---|
| README ilk ekranı | banner, kaynağı olmayan 15 sn reel (GIF + sesli MP4), uzun tanım, tek komutlu SVG; çalıştırma altta | banner, tek cümlelik tanım, tek komutluk çalıştırma (`git clone && cd` + `uv run mch run ...`), ölçülmüş süre, 20 sn gerçek çıktılı terminal kaydı, "ne zaman kullanılır / kullanılmaz" tablosu, PowerShell notu |
| Demo | `docs/reel/` (üretici yok), `assets/terminal-run.svg` | `arac/demo-uret.py`: 4 komut gerçekten koşulur, `docs/demo/komutlar.txt` kayıttır, GIF ve dikey `terminal.mp4` o kayıttan çizilir. Eski reel, SVG ve `terminal-goruntusu.py` kaldırıldı; git geçmişinde duruyor |
| `mch --version` | yok (çıkış 2) | `mch 0.1.0` |
| `mch --help` | iki alt komut | tanım + ilk çalıştırma + çıkış kodları |
| Yok config | `no such file: ...` | aynı satır + "try examples/compare-mocks.yaml" |
| Bozuk `--input` | JSON hatası | aynı satır + `hint:` (bash ve PowerShell 5.1 tırnak biçimi) |
| Test | 144 | 150 (+6: `--version`, üst düzey `--help`, JSON ipucu, iki config ipucu, README ilk komutu) |
| CI | quick start adımı | + `mch --version` ve `--help` denetimi |

## CLI akışı

```
çalıştır     git clone ... && cd model-comparison-harness
             uv run mch run examples/compare-mocks.yaml --input '{"prompt": "..."}'    çıkış 0
doğrula      mch validate config.yaml                                                   çıkış 0 / 1
CI           mch run ... --fail-on-error                                                çıkış 1 = bir backend düştü
yanlış komut error: ... + hint: doğru biçim                                             çıkış 1 (kullanım hatası 2)
```

Kurulum ve ilk sonuç süresi ölçüldü (`DENETIM.md`): klon 1,6 s, ilk `uv run` 5,9 s.

## Görsel dil (video sisteminden alınanlar)

| Ne | Nereden | Nerede |
|---|---|---|
| zemin `#0e0d0b`, panel `#14120e`, yazı `#f1ece2`, vurgu `#ffc21a` | `sosyal/uret/tema.mjs` klasik | terminal zemini/paneli, `$` istemi, sol şerit, `hint:` |
| mercan `#ff4d6d` | `tema.mjs` klasik vurgular | `error` satırları (yalnız boyama; metin değişmez) |
| JetBrains Mono | `tema.mjs` `F.jb` | tüm terminal metni; SIL OFL 1.1, `assets/yazi/` (yerel dosyadan; indirme yok) |
| Yazma animasyonu, satır satır çıktı | `sahne.js` terminal tekniğinin sadeleşmişi | `arac/demo-uret.py` |

Bilerek alınmayanlar: League Gothic başlık (README'de görsel başlık yok; banner mevcut), geçişler (iris/glitch): bir CLI demosunda çıktı okunmalı.

Kontrast (panel `#14120e` üstünde, WCAG göreli parlaklıktan hesaplandı): krem 15,9:1, sönük `#b6ae9d` 8,5:1, sarı 11,6:1, mercan 5,8:1; hepsi ≥ 4,5:1.

## Sınırlar

Video/GIF'teki gecikmeler mock'un ayarlı gecikmeleridir (0,1 / 1,2 / 0,3 s); README ve `komutlar.txt` bunu açıkça söylüyor. Gerçek modelle karşılaştırma ve `--rubric` notları ölçülmedi (API anahtarı kullanılmadı). Dikey videoda tablo 132 karakter genişliğinde olduğu için punto küçük (14 px): ham malzeme, günlük video hattı yazı/ölçek ekler.
