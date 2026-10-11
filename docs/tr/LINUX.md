# Linux'ta kaynak koddan Fairbeam

Bu kurulum, CPU openEMS çözücüsünü, Python çalıştırma sunucusunu ve derlenmiş Fairbeam görüntüleyicisini yerel tarayıcıda çalıştırır. Kullanıcıya ait bir çalışma ortamı dizini kullanan kaynak kod/geliştirme kurulumudur. Yerel Tauri uygulaması, `.deb`, AppImage, güncelleme paketi veya herkese açık sürüm üretmez.

## Yerel masaüstü desteğinin hazırlık durumu

Aşağıdaki kaynak kod iş akışı Debian 13 x86_64 üzerinde test edilmiştir. Deneysel Tauri `.deb` paketi de 11 Ekim 2026'da Ubuntu 24.04 x86_64 CI üzerinde derleme ve paket içeriği denetimlerinden geçmiştir (kanıt aşağıdadır). Bunlar desteklenen bir Linux masaüstü sürümü olduğu anlamına gelmez: kurulum, ilk açılış ve grafik masaüstü davranışı henüz doğrulanmamıştır. Mevcut eksikler:

| Alan | Bugün uygulanan | Linux masaüstü sürümü için eksik olan |
| --- | --- | --- |
| Tauri kabuğu ve paketi | Deneysel Ubuntu 24.04 amd64 paket derlemesi ve çıkarılan çalıştırılabilir dosya/masaüstü girdisi denetimleri geçti; ortak Unix kapatma kodu ve harici Python bulma desteği mevcut. | Kurulum, açılış, dosya iletişim kutuları ve diğer arayüz davranışları gerçek masaüstünde doğrulanmalıdır. |
| İlk açılış çalışma ortamı | `scripts/install-openems-linux.sh`, geliştirme için CPU openEMS/CSXCAD ortamını kaynak koddan derler. | `runtime/pins.json` içinde `linux-x86_64` uv/openEMS girdileri yoktur; `runtime/setup-runtime.sh` yalnızca macOS arm64 kabul eder. Linux kaynak yükleyicisi, paketlenmiş ve taşınabilir bir yönetilen çalışma ortamı değildir. |
| Güncellemeler | Tauri güncelleyici eklentisi kuruludur. Üst proje, Linux AppImage güncelleme dosyalarını belgeler. | `scripts/publish-release.mjs` yalnızca `windows-x86_64` ve `darwin-aarch64` kabul eder; akışta Linux paketi/imzası yayımlanmaz. |
| İsteğe bağlı oturum açma | Misafir modu varsayılandır. | İsteğe bağlı hesaplar derlemesi Apple/Windows anahtarlık arka uçlarını kullanır; Linux'taki yedek çözüm bellektedir ve oturumu kalıcı olarak saklamaz ([ACCOUNTS.md](ACCOUNTS.md)). |

İlk desteklenen Linux masaüstü hedefi olarak **Ubuntu 24.04 LTS x86_64** ile başlayın. Ubuntu, 2029'a kadar standart destek belirtir ([sürüm döngüsü](https://ubuntu.com/about/release-cycle)). Tauri'nin güncel Linux derleme gereksinimleri WebKitGTK 4.1'i ve sistem geliştirme kütüphanelerini içerir ([Tauri gereksinimleri](https://v2.tauri.app/start/prerequisites/)); openEMS'in ayrı yerel bağımlılıkları vardır ([openEMS gereksinimleri](https://docs.openems.de/en/latest/install/requirements.html)). Masaüstü derleme bağımlılıkları Ubuntu CI çalıştırıcısında geçti; bu iş openEMS ortamını kurmadı veya doğrulamadı. 24.04'ten eski sistemler için AppImage yayımlanacaksa, desteklendiği belirtilen en eski sistem üzerinde derleyin: Tauri, daha yeni derleme sistemlerinin asgari glibc sürümünü yükseltebileceği konusunda uyarır ([AppImage kılavuzu](https://v2.tauri.app/distribute/appimage/)).

Linux kaynak tespiti, süreç CPU yakınlığını ve görünür cgroup v1/v2 sınırlarını zaten hesaba katar. Kullanılabilir CPU sayısı, yakınlık kümesinin büyüklüğü ile en az bir iş parçacığına yukarı yuvarlanan görünür CPU kotasından küçük olanıyla sınırlanır. Yakınlık bilgisi varsa fiziksel çekirdek tespiti yalnızca izin verilen CPU'ları sayar. Kullanılabilir bellek, sistemin `MemAvailable` değeri ile görünür cgroup sabit RAM sınırına kalan kapasiteden küçük olanıdır; takas belleği ve geri kazanılabilir önbellek bu kapasiteye eklenmez. Çözücünün varsayılan mesh sınırı 40 milyon hücredir; yaklaşık 90 bayt/hücre bellek ön denetimi, elde edilen kullanılabilir bellek tahmininin %60'ı üzerinde uyarır, %90'ı üzerinde çalıştırmayı reddeder. Uygulama CPU yakınlığını değiştirmeden gözlemler.

Bu davranışlar [resources.py](https://github.com/ismailakdag/fairbeam/blob/main/python/fairbeam/resources.py) ve [linux_resources.py](https://github.com/ismailakdag/fairbeam/blob/main/python/fairbeam/linux_resources.py) içinde uygulanmıştır. [test_linux_resources.py](https://github.com/ismailakdag/fairbeam/blob/main/python/tests/test_linux_resources.py) ve [test_resources.py](https://github.com/ismailakdag/fairbeam/blob/main/python/tests/test_resources.py), taklit edilen yakınlık, çekirdek topolojisi ve cgroup dosya sistemi verileriyle kapsam sağlar. Bu testler gerçek bir kaynak kısıtlı konteyner çalıştırmasının kanıtı değildir. Yetki verilmiş bağlama kökünün üstündeki sınırlar görünmez; okunamayan veya bozuk kontrol değerleri uydurulmaz, bilinmiyor kabul edilir. Cgroup verisi yoksa bilinen sistem/yakınlık değerleri kullanılır; dolayısıyla kaynak korumaları gizli kısıtlar altında güvenliği garanti edemez.

Önerilen uygulama sırası:

1. Yeni derlenmiş deneysel Ubuntu 24.04 x86_64 paketini masaüstüne kurup doğrulayın; CI yalnızca paketlemeyi doğruladı. İlk hedef çalıştıktan sonra ikinci bir Ubuntu LTS uyumluluk denetimi ekleyin.
2. Özeti sabitlenmiş Linux uv ve CPU openEMS dosyaları ile Linux ilk açılış/onarım betiği ekleyin. Uyumlu Python wheel paketlerini ve gerekli paylaşımlı kütüphaneleri içeren taşınabilir bir openEMS/CSXCAD paketi derleyin veya birleştirin; kurulu uygulama yerel kod derlememeli veya `sudo` çağırmamalıdır.
3. İlk paket biçimini seçin ve yayımlama/imzalama yolunu bağlayın. AppImage, mevcut otomatik güncelleme akışına en az değişiklikle uyar: Tauri, Linux güncelleme dosyaları olarak `.AppImage` ve `.AppImage.sig` biçimlerini belgeler ([güncelleyici kılavuzu](https://v2.tauri.app/plugin/updater/)). Sürüm betiğini `linux-x86_64` için genişletin; `.deb` dağıtımını ayrı bir paket yöneticisi yolu olarak ele alın.
4. Gerçek bir Ubuntu masaüstünde yeni kurulum ve çalışma ortamı indirmesini, görüntüleyici açılışını, yerel dosya iletişim kutularını, kaba CPU dipol çalıştırmasını, iptal/çıkış sırasında süreç temizliğini ve imzalı güncellemeyi doğrulayın; kullanıcının çalışma klasörünün korunduğunu da kontrol edin. Kısa işlev testini yeni bir geçici klasörde tutun, `--engine cpu --threads 1` veya `2` kullanın ve varsayılan mesh korumasını açık bırakın.

Genel kabuk, çalışma ortamı doğrulayıcısı, sürüm akışı arayüzü ve kaynak ön denetimi macOS ve Windows derlemeleriyle paylaşılabilir. Linux'un destekleniyor sayılması için yerel kurulum ve masaüstü doğrulaması, dağıtıma özgü bağımlılık denetimleri, Linux çalışma ortamı dosyaları ve Linux güncelleme hedefi hâlâ gereklidir.

## Deneysel masaüstü paketi derleme

`src-tauri/tauri.linux.conf.json`, Linux üzerinde macOS paket hedeflerinin yerine Debian paketini seçer. Mevcut uygulama kimliğini, sürümünü ve kaynaklarını korur; güncelleyici dosyası üretimini kapatır. Bu, **önceden kurulmuş harici openEMS Python ortamını** kullanan geliştirici paketidir; bağımsız Linux sürümü değildir. Yönetilen ilk açılış kurulumu, AppImage ve Linux güncellemeleri bu yapılandırmada uygulanmaz. `tauri.release.conf.json` ile birleştirmeyin.

Ubuntu 24.04 x86_64 üzerinde Rust ve [Tauri Linux gereksinimlerini](https://v2.tauri.app/start/prerequisites/) (WebKitGTK 4.1, GTK 3, OpenSSL, libxdo, Ayatana AppIndicator ve librsvg geliştirme paketleri), ayrıca `pkg-config` ve `patchelf` kurun. Kaynak kopyasında:

```bash
npm ci
npm run check:linux-desktop
npm run check:licenses
npm run desktop:build:linux -- -- --locked
# Inspect the developer package; this does not install it.
dpkg-deb --info src-tauri/target/release/bundle/deb/*.deb
```

Derleme komutu normal ön yüz derlemesini çalıştırır; `.deb` dosyası `src-tauri/target/release/bundle/deb/` dizinine yazılır. `check:linux-desktop` yalnızca yapılandırma sözleşmelerini denetler; Rust derlemez, kabuğu başlatmaz veya paketi doğrulamaz.

`.github/workflows/linux-desktop.yml` içindeki elle başlatılan **Linux desktop build (experimental)** iş akışı Ubuntu 24.04 kullanır, Cargo kilit dosyasıyla derler, paket bilgilerini ve çıkarılan çalıştırılabilir dosya/masaüstü girdisini inceler; Actions dosyasını yedi gün saklar. Depo izni salt okunurdur; push/PR tetikleyicisi, sürüm yüklemesi veya imzalama anahtarı yoktur. openEMS kurmaz, simülasyon çalıştırmaz ve grafik oturumunu sınamaz. Başarılı Actions derlemesi yalnızca paketleme kanıtıdır, masaüstü desteği doğrulaması değildir.

### Kaydedilen paket derlemesi

[Actions çalıştırması 38099239648](https://github.com/ismailakdag/fairbeam/actions/runs/38099239648), 11 Ekim 2026'da başarıyla tamamlandı. İndirilen `.deb` dosyasının özeti, Actions ZIP dosyasından ayrı hesaplandı.

| Kanıt | Sonuç |
| --- | --- |
| Kaynak / çalıştırıcı | `bedfffc0b68adc46fda4a0bcdced60cc77202d83` / Ubuntu 24.04, amd64 |
| Paket | `Fairbeam_0.7.2_amd64.deb`, 12,377,632 bayt |
| Paket SHA-256 | `3BFAFC45C4EB433EF21B7DA4FABBCB5F864978B12B0C28A354051F94214B3C6D` |
| Derleme / bildirimler | Kilitli Cargo release derlemesi, Debian paketleme ve lisans denetimi geçti |
| Çıkarılan paket | `dpkg-deb` mimarisi `amd64`; çalıştırılabilir `usr/bin/fairbeam`; `Exec=fairbeam` içeren tek masaüstü girdisi |
| Linux Rust testi | Bir klasörde gösterme yol testi geçti: yalnız mevcut dosyanın üst dizini tek bir değişmez argüman olarak aktarılır; dosya yöneticisi başlatılmadı |

Dosya adındaki `0.7.2`, paket bilgisidir; `v0.7.2` etiketinin yeni bir yayını değildir: bu kaynak commit'i etiketten daha yenidir. Dosya geliştirici incelemesi için yedi gün saklanır; resmi indirme veya güncelleme hedefi değildir. Bu çalıştırmada paket kurulmadı, arayüzü açılmadı, yönetilen çözücü ortamı kurulmadı ve yerel EM simülasyonu yapılmadı. Belgenin ilerleyen bölümündeki Debian kaynak/çalışma ortamı kanıtı, ayrı bir tarihsel testtir.

Elle masaüstü doğrulaması için önce aşağıdaki kaynak yükleyicisiyle CPU ortamını hazırlayın. Kabuk `~/opt/openEMS/venv/bin/python` yolunu bulabilir; özel bir dizin kullanıyorsanız Python yolunu kurulum ekranından seçin. `FAIRBEAM_PYTHON` masaüstü kabuğunun değil, tarayıcı başlatıcısının ortamını seçer. Geçici çalışma klasöründe açılışı, dosya iletişim kutularını, iptal/çıkış temizliğini ve kaynak bulmayı doğruladıktan sonra kaba çözücü çalıştırmasını deneyin. Bu denetimler bitene kadar mevcut tarayıcı iş akışı, test edilmiş kaynak kod yoludur.


İş akışı ayrıca `scripts/check-linux-package-install.py` denetiminin geçici bir
`ubuntu:24.04` amd64 Docker konteynerinde geçmesini gerektirir. Yerel `.deb` dosyasını bildirdiği
bağımlılıklarla kurar; tanılama araçlarını eklemeden **önce** çözümlenemeyen paylaşımlı kütüphaneleri
denetler, ELF/masaüstü/simge bilgilerini ve paket kaynaklarını doğrular. Ardından paketi kaldırır ve
kendisine ait çalışma klasöründeki deneme dosyasının korunduğunu kontrol eder. Günlük, kaynak
commit'ini, paket SHA-256/boyutunu ve konteyner imaj kimliğini kaydeder; yedi gün saklanır. Betik ana
makinede `apt` çalıştırmaz ve yalnızca kendi konteynerini temizler. Yalnızca güvenilir, yerelde
derlenmiş paketlerle kullanın: paket kurulumu, paketin bakım betiklerini konteynerde çalıştırır.
Başarı; arayüz açılışını, gerçek kullanıcı çalışma klasörünü, openEMS'yi, yönetilen çalışma ortamı
kurulumunu veya güncellemeleri doğrulamaz. Bu denetimin eklenmesi başarılı kurulum sonucu anlamına
gelmez; böyle bir sonuç belirtmeden önce başarılı iş akışı kaydını kanıt olarak saklayın.

Masaüstü Rust bildirimleri Linux x64 bağımlılık ağacını da kapsar; iş akışı yüklemeden önce bunları
denetler. Sistem GTK/WebKit kütüphaneleri ve harici çözücü ortamı dağıtım/kullanıcı tarafından
yönetilir. “Klasörde göster”, `xdg-open` ile dosyanın bulunduğu dizini açar; dosyayı seçmez.
Paket `xdg-utils` bağımlılığını içerir. Bu davranışı hedef grafik masaüstünde ayrıca doğrulayın;
kaynak/yol testleri dosya yöneticisi entegrasyonunun kanıtı değildir.

## Gereksinimler

- C/C++ derleyicisi ve geliştirme kütüphaneleri bulunan Linux (Debian 13 x86_64 test edilmiştir)
- Geliştirme başlıkları ve `venv` içeren Python 3.10+; Python 3.12.14 test edilmiştir
- Görüntüleyici ve depo denetimleri için Node.js 22.12.0+ ve npm; Node 24.19.0/npm 11.9.0 test edilmiştir
- Git, CMake, make; resmi GitHub kaynaklarına ve Python/npm paket kayıtlarına ağ erişimi
- Görüntüleyici için WebGL destekli modern bir tarayıcı

Debian/Ubuntu'da şu sistem paketi komutlarını gözden geçirip kendiniz çalıştırın:

```bash
sudo apt-get update
sudo apt-get install build-essential git cmake pkg-config python3-dev python3-venv \
  libhdf5-dev libtinyxml-dev libboost-all-dev libcgal-dev libvtk9-dev
```

Yükleyici hiçbir zaman `sudo`, `apt` veya üst projenin sistem paketi yükleyicisini çağırmaz. VTK paketi ek bağımlılıklar getirebilir; bu kurulum Qt/AppCSXCAD derlemez. Diğer Linux dağıtımlarında eşdeğer geliştirme paketleri gerekir; [üst proje gereksinimlerine](https://docs.openems.de/en/latest/install/requirements.html) bakın.

## Kurulum ve başlatma

Depo kökünden:

```bash
# Native CPU libraries and Python bindings, then this checkout's fairbeam package
scripts/install-openems-linux.sh

# Built viewer
npm ci
npm run build

# Viewer and Python API together; opens the local browser on an available port
scripts/run-linux.sh
```

Başlatıcı mevcut `fairbeam app` komutunu kullanır, `127.0.0.1` adresine bağlanır ve sunucuyu ön planda tutar. Ctrl+C ile durdurun. Ön yüzü düzenledikten sonra `npm run build` komutunu yeniden çalıştırın. Python bu kaynak kopyasından düzenlenebilir olarak kurulduğundan Python değişiklikleri yeniden kurulum gerektirmez.

Ekransız bir sistem veya sabit port için:

```bash
scripts/run-linux.sh --no-browser --port 5320
# Open http://127.0.0.1:5320 on the same machine
scripts/run-linux.sh --help
```

`--models`, `--projects`, `--jobs`, `--sim-root` ve `--ui` dahil tüm argümanlar `fairbeam app` komutuna aktarılır. Varsayılan model/proje dizinleri bu kaynak kopyasındadır; ham çözücü çıktısı ve iş geçmişi onun `.sim` dizinini kullanır. Ayrıntılar için [RUN-SERVER.md](RUN-SERVER.md) belgesine bakın. Sunucu Python modellerinizi hesabınızın yetkileriyle çalıştırır; bir Python korumalı ortamı değildir.

### Özel yollar ve mevcut kurulum

```bash
PREFIX="$HOME/opt/fairbeam Linux" SRC="$HOME/opt/openems Linux sources" JOBS=2 \
  scripts/install-openems-linux.sh
PREFIX="$HOME/opt/fairbeam Linux" scripts/run-linux.sh

# Or select an existing interpreter with working openEMS, CSXCAD and fairbeam imports
PREFIX="/path/to/openEMS" FAIRBEAM_PYTHON="/path/to/venv/bin/python" \
  scripts/run-linux.sh --no-browser
```

Yükleyici ayarları:

- `PREFIX`: yerel kütüphaneler ve `venv/`; varsayılan `~/opt/openEMS`
- `SRC`: önbelleğe alınan üst proje kaynak kopyası ve derleme dizinleri; varsayılan `~/opt/openems-src-linux`
- `PYTHON`: yeni sanal ortam oluştururken kullanılan yorumlayıcı; varsayılan `python3`
- `JOBS`: yerel derleme paralelliği; varsayılan 2, belleği az sistemlerde 1 kullanabilirsiniz
- `CC`/`CXX`: derleyiciler; varsayılan `gcc`/`g++`

İçe aktarılabilen mevcut bir `PREFIX/venv`, openEMS/CSXCAD yeniden derlenmeden veya yükseltilmeden kullanılır. Normal yükleyici çalıştırması yine de Fairbeam'i mevcut kaynak kopyasından kurar. Geçersiz bir sanal ortam, yerel dosyalar yazılmadan önce reddedilir. Sanal ortam geçerliyse ancak yerel bağlayıcılar içe aktarılamıyorsa yükleyici yerel bileşenleri yeniden derler ve kurulu dosyalarının üzerine yazabilir; eski kurulumu korumak için yeni `PREFIX` ve `SRC` seçin.

Farklı bir üst proje revizyonundaki kaynak kopyası, izlenen değişiklikler içeren kopya veya başka kurulum dizinine ait CMake önbelleği değiştirilmeden bırakılır ve reddedilir; başka bir `SRC` seçin. Farklı kurulum dizinleri arasında derleme önbelleği kullanmak eski kütüphane yollarını koruyabilir. Temiz yeniden derleme için hem yeni `PREFIX` hem yeni `SRC` seçin.

Yükleyici fparser, CSXCAD ve openEMS'i ayrı ayrı, ardından Python bağlayıcılarını derler. Yeni derlemeler aşağıda sabitlenmiş üst proje revizyonlarını kullanır; Python bağımlılıkları yapılandırılmış pip kaynağından gelir ve sürümleri kilitli değildir. Her iki betik de mevcut değeri koruyarak `PREFIX/lib` ve `PREFIX/lib64` yollarını `LD_LIBRARY_PATH` değişkenine ekler. Python CLI'yi özel bir yerel kurulum diziniyle doğrudan çalıştırırken içe aktarma paylaşımlı kütüphaneleri bulamazsa aynı kütüphane yolunu kullanın.

## Asgari doğrulama

İlk denetim salt okunurdur; indirme, kurulum veya simülasyon yapmaz ve sunucu başlatmaz:

```bash
scripts/install-openems-linux.sh --check

# Set PREFIX here if the installation is not in the default directory
export PREFIX="${PREFIX:-$HOME/opt/openEMS}"
export LD_LIBRARY_PATH="$PREFIX/lib:$PREFIX/lib64${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# The suite expects the default mesh limit, not a user-provided override
env -u FAIRBEAM_MAX_CELLS "$PREFIX/venv/bin/python" -m unittest discover -s python/tests -q

# One small CPU simulation; writes only into a fresh temporary folder
smoke_dir="$(mktemp -d)"
"$PREFIX/venv/bin/python" -m fairbeam run python/models/dipole.py \
  --set mesh_div=10 --points 101 --end-db=-30 --threads 2 --engine cpu \
  --name linux-smoke --out "$smoke_dir/projects" --sim-root "$smoke_dir/sim"
printf 'Smoke results: %s\n' "$smoke_dir"
```

Simülasyonları birer birer çalıştırın. Bilerek kaba seçilen bu dipol, çalışma ortamının kısa işlev testidir; mesh yakınsaması sağlanmış bir doğruluk karşılaştırması değildir. Deponun ön yüz denetimleri [FROM-SOURCE.md](FROM-SOURCE.md#tests) belgesinde açıklanır.

## Test edilen yapılandırma ve sınırlar

30 Eylül 2026'da Fairbeam kaynak commit'i `d3de9af23b6d8c31295387b346e395d46707d6e6` ve aşağıdaki resmi üst proje kaynaklarıyla test edilmiştir:

| Bileşen | Test edilen revizyon/sürüm |
| --- | --- |
| Linux | Debian GNU/Linux 13 (trixie), x86_64 |
| Python | 3.12.14 |
| Derleyici / CMake | GCC/G++ 14.2.0 / CMake 3.31.6 |
| openEMS-Project | `9f5cdd4d71cae312633ab0b2c1db64867f8c782b` |
| openEMS | `5b1ecb1244e6bd192d83efdf2bc84e5f83c96047` (`v0.37.0-rc3-16-g5b1ecb1`) |
| CSXCAD | `bd2c133392d93251b640da1f8e2367163f00b7f5` (`v0.7.0-rc3-1-gbd2c133`) |
| fparser | `4b9c845b449b520c4b8c5f23c74cd04820084f81` |
| Yerel bağımlılıklar | Boost 1.83, HDF5 1.14.5, VTK 9.3.0, TinyXML 2.6.2, CGAL 6.0.1 |
| Python bağımlılıkları | NumPy 2.5.3, h5py 3.16.0, Cython 3.3.0, setuptools 84.0.0 |
| Ön yüz araçları | Node 24.19.0, npm 11.9.0 |

Testte sistem paketleri kullanılmamıştır: Debian geliştirme/çalışma ortamı paketleri yerel bir dizine çıkarılmış, yolları `CMAKE_PREFIX_PATH`, yalnızca teste özgü `CMAKE_TOOLCHAIN_FILE`, derleyici bayrakları ve `LD_LIBRARY_PATH` üzerinden sağlanmıştır. Son yükleyici, başlangıçta boş olan ve adlarında boşluk bulunan kaynak ve çalışma ortamı dizinlerinde uçtan uca derlemeyi 177,4 saniyede tamamlamıştır. Bu süre; sabitlenmiş üst proje kaynaklarını klonlamayı, üç yerel kütüphaneyi ve iki Python bağlayıcısını derlemeyi, sanal ortam oluşturmayı, Fairbeam'i kurmayı ve içe aktarmaları kontrol etmeyi içerir. Başka bir sistemde olağan apt kurulumuyla oluşan bağımlılık yerleşimi ayrıca test edilmemiştir.

Yükleyicinin mevcut kurulumu yeniden kullanma yolu da, eksik `pip` paketinin sanal ortama ait `ensurepip` ile kurulması dahil, geçmiştir. Beş regresyon testi, salt okunur denetimleri ve geçersiz sanal ortamların ya da başka kurulum dizinine ait derleme önbelleklerinin yerel dosyalar yazılmadan reddedilmesini doğrular. Göreli sanal ortam yorumlayıcısı dahil başlatıcının yol/argüman işlemesi geçmiştir. Başlatıcı derlenmiş görüntüleyiciyi sunmuş, yerel sağlık/API denetimlerine yanıt vermiş ve düzgün kapanmıştır.

100,842 hücreli CPU dipol kısa işlev testi, iki iş parçacığıyla 7,200 zaman adımından sonra yakınsamıştır. Duvar saati süresi 13,8 saniye, en yüksek süreç RSS belleği yaklaşık 101 MiB; 2,4 GHz'de S11 yaklaşık −37,6 dB, en yüksek yönlülük 2,19 dBi ve ışıma verimliliği %98,17 olmuştur. Bu değerler yalnızca o çalıştırmayı tanımlar; performans garantisi değildir.

Son yeni kurulan çalışma ortamı aynı testi 14,9 saniyede tekrarlamış, 7,200 zaman adımında yakınsamış ve ilk S11/empedans dizileriyle `1e-8` bağıl ve `1e-10` mutlak tolerans içinde eşleşmiştir. Başlatıcısı sağlık, arayüz dosyası ve sonuç paketi HTTP denetimlerinden de geçmiş ve düzgün kapanmıştır.

`npm run build` (TypeScript denetimi dahil), `npm run check:designer` ve `npm run check:exports` geçmiştir. Derleme, ana kod parçasının boyutu ve statik/dinamik içe aktarmaların birlikte kullanılması hakkında uyarılar vermiştir.

Doğrudan içe aktarmalar ve geometri dışa aktarımı da geçmiştir. Varsayılan mesh sınırıyla tam Python test paketi 146,3 saniyede 799 testi tamamlamıştır: 778 başarılı, 20 atlandı, bir başarısız. Analitik dipol testi `DipoleTest.test_moment_method_reference`, beklenen 2,42–2,47 GHz aralığının dışında, yaklaşık 2,53 GHz'de rezonans öngörmektedir; MoM matrisi sayısal olarak tam ranklı değildir (2,44 GHz'de 100 üzerinden 99). Bu analitik referans, bilinen açık bir sorundur.

Elle çalıştırılan tarayıcı test düzeneği ve `npm run check:scenarios -- --skip-run` bu yapılandırmada tarayıcı başlatamamıştır; dolayısıyla uygulama tarayıcı doğrulamaları, grafik etkileşim veya WebGL çizimi için başarılı sonuç iddiası yoktur. 30 Eylül kaynak çalıştırmasında yerel Tauri paketlemesi test edilmemiştir; yukarıdaki daha sonraki Ubuntu CI paketleme kanıtı ayrıdır. GPU hızlandırması, kurulu masaüstü davranışı, diğer Linux dağıtımları/mimarileri ve resmi Linux sürüm dağıtımı hâlâ doğrulanmamıştır.
