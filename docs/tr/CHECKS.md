# Katkıda bulunanlar için denetimler

Genel önkoşullar [CHECKS.md](../CHECKS.md) dosyasındadır.

## Senaryo denetimleri

Odaklı denetimler `npm run check:eta-presentation`, `npm run check:run-profiles` (saf mantık), ayrıca `npm run check:modal-focus`, `npm run check:run-profile-dialog` komutlarıdır (geçici Vite/çalıştırma sunucusu, Chrome ve openEMS içeren Python ortamı gerekir; çözücü çalıştırılmaz). İsteğe bağlı `scripts/check-home-accessibility.mjs`, çalışan Vite sunucusunda yakalanan API test verileri kullanır; ortam değişkenleri başlığındadır. Dar düzenler, üst menü, proje arama/sıralama/favoriler, hata durumları ve masaüstü panel tercihlerini geri yüklemeyi kapsar.

`npm run check:scenarios`, gerçek arayüzde headless Chrome ile gerçekçi ilk kullanım görevlerini İngilizce/Türkçe yürütür, kullanıcının takılacağı ilk noktada durur. `check:designer` denetiminden ağırdır (yaklaşık 3 dakika, tek kaba çözücü çalıştırması); onun parçası değildir.

- **S1**, yalnızca arayüzle boş tasarımdan yama kurar: FR4 kutu, toprak levhası, yama; sağ tık › Boolean › Çıkar ile yarık; Dönüştür penceresinin canlı önizlemesiyle aynalanmış katı; yüz ekstrüzyonu önizlemesi; ayrık port/bant; katı ve bileşende Renk…; Kaydet/yeniden yükleme, ardından **tek kaba çalıştırma** (aşağıda), S-parametreleri/Özet.
- **S2**, VBA makrosu (`examples/cst/patch-antenna.bas`) ve PCB çizimi (`python/tests/fixtures/pcb/*.gtl, *.gko`) içe aktarır; katıların/raporun göründüğünü denetler.
- **S3**, Tarama penceresi (varsayılan ±%10; gönderim yakalanır, asla gönderilmez), Optimize et (doğrulama mesajları) ve Simülasyon ayarlarını (Değişiklikleri at) açar.
- **S4**, dahil edilen iki örnek paketini tasarım çalıştırması olarak kopyalar (birinde sentetik alan haritası); Δ (A ile), Veriyi kopyala/CSV içeren Özet'i, çalıştırma Özellikler'ini, Faz/Canlandır içeren Alan haritası'nı ve yükleme hatasından sonra Yeniden dene'yi denetler.
- **S5**, Python panelini açar, betikte parametre değiştirip Uygula seçer (tek geri alma adımı); hata veren betik uygular (satır belirtilir, tasarım kalır); uygulanmamış değişikliklerle Düzenle'den çıkar (onay).
- **S6**, Ana ekran'dan Python modeli oluşturur, kaynağı Python panelinde olan bağlı Tasarım'ı açar; Ana ekran'a dönüp **Tasarım olarak aç** seçiminin kopya oluşturmadan aynı Tasarım'ı kullandığını doğrular.
- **S7**, yinelenen dönüşüm önizlemelerini, tek Uygula/geri al işlemini, bileşen genelinde dönüşümleri, katıların bileşen klasörleri arasında taşınmasını, bağımsız ızgara görünürlüğünü ve yerel çizim çerçevelerini denetler.

Her adım beklenen durumu doğrular ve sayfayı inceler: kapsayıcı dışına taşan öğe, araç ipucusuz kırpılmış metin, ham i18n anahtarı, pencere dışı iletişim kutusu, konsol hata/uyarısı (`[i18n]` eksik anahtar dahil) olmamalıdır. Başarısız adım `$TMPDIR/fairbeam-scenario-failures/` altına ekran görüntüsü kaydeder. İnceleme önce bilinen hatalı sayfada kendisini sınar.

Çalıştırıcı, geçici klasör üzerinden boş portlarda Vite ve çalıştırma sunucusu başlatır (`public/` kopyası; `projects/` aynı zamanda sunucunun `--projects` klasörüdür. Vite sonuçları public klasöründen sunar, sunucu `--projects` içine yazar; başka klasör yüklenmez, bkz. [RUN-SERVER.md](RUN-SERVER.md)). Sonunda ikisini de PID ile durdurur. Çözücü `/tmp/fairbeam-sim.lock` dizin kilidini alır (`mkdir` alır, `rmdir` bırakır), `nice -n 10` ile çalışır; kilit doluysa veya `--skip-run` varsa atlanır. Seçenekler: `S1 S3` (yalnızca bunlar), `--lang en|tr`, `--skip-run`. `FAIRBEAM_CHROME`, `FAIRBEAM_PYTHON` başka Chrome/Python gösterir. Tarayıcının yapamadığı denetimler [DESKTOP-CHECKLIST.md](DESKTOP-CHECKLIST.md) içindedir.

### Minimum pencere senaryo denetimi

`node scripts/check-scenarios.mjs S8 S9 S10 --compact --skip-run`, yerel masaüstü minimum boyutunda EN/TR Görünüm, görünür yüzey çıktıları ve RF formlarını çalıştırır. Rust pencere boyutlandırması 1024 × 700 mantıksal nokta kullanır; 728 noktalık çalışma alanında başlık çubuğu payıyla 1024 × 688'e iner. RF formları iki yükseklikte çalışır; İptal/Oluştur işlemleri iletişim kutusu içinde ve Tab ile ekranda erişilebilir kalmalıdır. Bu genişliklerde gezinti/özellikler panelleri üst katmandır; senaryolar gerektiğinde gerçek kenar şeritlerini açar. Varsayılan senaryo boyutları ve adım sayısı değişmez. Komut FDTD çözümü istemez.

`node scripts/check-scenarios.mjs S9 --compact --skip-run --ribbon-file-audit`, EN/TR dillerinde 1024, 1280, 1440 pikselde Görünüm/Simülasyon simge hizasını da denetler; Diğer menüsünün dosya seçicisinden gerçek sonuç paketi açar. Ek denetim isteğe bağlıdır.
