# Denetim: model-comparison-harness (30 Eylül 2026)

Yenilemeden önce `main` (0.1.0, `a6f97b9`) üzerinde, bu makinede (Windows 11, Python 3.12, uv 0.12.5, Git Bash + PowerShell 5.1) ölçüldü. Ölçülmeyen bir şey yazılmadı. Ham çıktılar depo dışında: `kanit/model-comparison-harness/{once,sonra}/komutlar.txt` (aynı 9 komut, iki sürüme karşı). Canlı model çağrısı yapılmadı ve hiçbir API anahtarı kullanılmadı: bütün koşular `mock` backend'leriyle; ekrandaki gecikmeler mock'un ayarlı gecikmeleridir, gerçek bir modelin ölçümü değildir.

## Temiz ortamda kurulum ve ilk sonuç

Her satır boş bir klasörde, taze klonla koşuldu.

| Yol | Süre | Sonuç |
|---|---|---|
| `git clone` | 1,6 s | |
| `uv run mch run examples/compare-mocks.yaml --input ...` (ilk; ortamı kuruyor, 15 paket) | 5,9 s | tablo, çıkış 0 |
| aynı komut, ortam hazır | 2,9 s | aynı |
| `uvx --from git+https://github.com/Furkiozknn/model-comparison-harness mch ...` (önbellek boş ve sıcak) | 9,4 s ve 9,8 s | çalışıyor; ama `mch --version` yoktu (aşağıda) |
| `python -m venv` + `pip install git+https://...` | 14,1 s | `mch validate` OK |

"Tek komutla çalıştır, bir dakikada ilk sonuç" tutuyor: klon + ilk çalıştırma ~7,5 s. `mch run` mock'un 1,2 s'lik gecikmesini bekliyor; `uv run` açılışı ~1,5 s ekliyor.

## README komutları

| Komut | Sonuç |
|---|---|
| Quick start (`git clone`, `uv sync`, `uv run mch run examples/compare-mocks.yaml --input ...`) | çalıştı, çıkış 0; tablo README'dekiyle aynı (gecikmeler ms oynuyor: 0,101 / 1,214 / 0,303 s) |
| `mch validate config.yaml` | çalıştı (`OK: 3 backend(s) ...`) |
| `--json`, `--csv`, `--fail-on-error`, `--timeout` | çalıştı; `--timeout 0.5 --fail-on-error` yavaş mock'u `TimeoutError` satırı yaptı, çıkış 1 |
| `--rubric` | anahtar yok: `error: --rubric given but no judge model ...`, çıkış 1, backend'ler hiç koşmadı (README'nin dediği gibi). Not tablosu **çalıştırılmadı** (anahtar kullanılmadı); README zaten "illustrative" diyor |
| `python3 arac/vendor-dogrula.py` | Windows'ta `python3` yok, `python` ile çalışır. Ağ gerektirir, ağsız atlar (çıkış 2) |
| `uv sync --group dev` + `uv run pytest` | 144 geçti, ~3 s |

Sayılar: "144 tests" koşudan 144 (uyuştu; bu yenilemede 150 oldu).

## Hata mesajları ve `--help` (önceki hâli)

Çıkış kodları hep doğruydu. Sorun sözlerdeydi:

| Girdi | Önce | Sorun |
|---|---|---|
| `mch --version` | `error: the following arguments are required: command`, çıkış 2 | Sürüm sorulamıyordu; `uvx`/`pip` kurulumundan sonra ilk yapılan şey bu |
| `mch --help` | iki alt komutun adı | Ne yaptığı, ilk komut ve çıkış kodları yok; çıkış kodu tablosu yalnız README'de |
| `mch run examples/yok.yaml --input {}` | `error: no such file: examples\yok.yaml` | Çalışan bir config'in neye benzediği söylenmiyor |
| PowerShell'de `--input '{"prompt": "x"}'` | `--input must be valid JSON: Expecting property name enclosed in double quotes` | **Windows'ta en olası ilk hata:** PowerShell 5.1 iç çift tırnakları siliyor (`'{\"prompt\": ...}'` gerekir; bu makinede doğrulandı). Mesaj sebebi söylemiyor; README'deki komutların hepsi bash sözdizimi |
| `config` argümanı | `--help`'te açıklamasız | |
| `mch run` (argümansız), `--timeout 0`, `--json --csv`, `mch validate examples` | açık mesajlar | doğru, değiştirilmedi |

## README bulguları

- İlk ekran: banner + kaynağı depoda olmayan 15 sn'lik reel (GIF + sesli MP4; üretici betik yok) + tek komutu gösteren `terminal-run.svg` (üreticisi `arac/terminal-goruntusu.py` vardı). Çalıştırma bir alt başlıktaydı; "ne zaman kullanılır / kullanılmaz" yoktu.
- Bu depoya ait "Ekosistem denetimi" (#19) bulguları yalnız meta-source ayrışması (summary/description, test sayısı 144 vs 74); yenilemeyle ilgisiz, kapatılmadı.

## Testler

144 geçti (yenileme öncesi). Yeni davranışlar için 6 test eklendi (150).
