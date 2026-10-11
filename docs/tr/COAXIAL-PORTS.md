> 0.7.3 sürüm kararı: proje sahibi, yerleşik CPU entegrasyonunu port düzenleyicisinde ve tasarım kontrollerinde görünür mesh yakınsama uyarısıyla deneysel olarak yayımlamayı onayladı. Aşağıdaki başarısız analitik kontroller geçmiş sayılmıyor. Eşdeğerlik yalnızca kayıtlı sentetik hava hattında gösterildi. Basamaklı geometri veya sonuçlara uydurulmuş faz düzeltmesi gönderilmiyor; GPU reddedilmeye devam ediyor. Diğer platformlar kendi çalışma zamanı kontrollerini gerektirir.

# Deneysel koaksiyel TEM portları

Bu uygulama taslak bir yetenektir; genel kullanımı doğrulanmış bir port modeli değildir.
Doğrudan `openEMS.AddCoaxialPort` kullanır, ayrık portla değiştirmez. Windows'ta oluşturma ve
sınırlı hava dolgulu hat kontrolleri denenmiştir. macOS, GPU motorları, dielektrik dolgulu hatlar
ve genel RF yapıları yayımdan önce ayrıca doğrulanmalıdır.

Çalıştırma API'si koaksiyel port içeren GPU çalıştırmalarını reddeder. CUDA veya Metal'in mevcut
olması radyal uyarım ve iletim hattı problarının eşdeğerliğini doğrulamaz. CPU yolu da deneyseldir;
yerel API'nin bulunması yalnızca bir yetenek kontrolüdür, doğruluk onayı değildir.

## Sözleşme

Düzenlenebilir `coaxial` port ekseni, merkez çizgisi uçlarını, iç iletken yarıçapı `r_i`, kılıfın
iç/dış yarıçapları `r_o`/`r_os`, PEC malzeme referansını, isteğe bağlı dielektrik referansını,
uyarım durumunu, besleme/ölçüm kaydırmalarını ve `open`/`short` sonlandırmasını saklar.
Uzunluklar tasarımdaki milimetre birimindedir. `0 < r_i < r_o < r_os` koşulu gerekir; uçlar tek
eksen üzerinde olmalıdır. Kaydırmalar, ters yönlü hatlarda da başlangıçtan bitişe doğru ölçülür.

Port iletken, kılıf ve dielektrik geometrisini oluşturur. Önce geometri mesh için hazırlanır,
ardından son mesh üzerinde yerel iletim hattı probları oluşturulur. Halkasal boşlukta en az üç,
iç yarıçapta en az iki hücre gerekir; bu alt sınır yakınsama garantisi değildir. Üç komşu ölçüm
düzlemi port içinde ve PML dışında olmalıdır. Uyarım ölçümden önce ve PML dışında kalmalıdır.
Pakete gerçek mesh'e oturtulmuş ölçüm düzlemi yazılır.

Yerel kare akım halkası kılıfın içinde kalmalıdır:
`sqrt(2) * (r_i + 0.1 * (r_o - r_i)) < r_o`. Daha dar halka dönüş akımını da çevreleyebileceğinden
reddedilir. Yerel iletim hattı hesabı simetrik fark ve aritmetik akım ortalaması kullandığı için
üç eksenel prob düzleminin aralıkları eşit olmalıdır. Fiziksel kısa devre de eksenel PML dışında
kalmalıdır. Bu kontroller mesh yakınsamasının yerini tutmaz.

Sonlu besleme direnci üst projede desteklenmediğinden reddedilir. `open`/`short`, fiziksel
besleme sonlandırmasıdır; uyumlu soğurucu yük anlamına gelmez. Geçiş hattı genellikle soğurucu
sınıra uzatılır. Dispersif dolgu, sonlu iletkenlikli iletken, silindirik mesh, gruplu beslemeler
ve özel kompleks referans empedansı bu taslağın dışındadır. Yerel API yoksa başka kaynak
kullanılmak yerine anlaşılır hata verilir.

Çıkış dalgaları, besleme sonlandırmasından bağımsız pozitif gerçek kayıpsız TEM referansı
`Zref = eta0/(2*pi) * sqrt(mu_r/eps_r) * ln(r_o/r_i)` kullanır. Yerel diferansiyel iletim hattı
kompleks empedansı `native_tem_z_re`/`native_tem_z_im` alanlarında korunur. Fiziksel yükleme,
sayısal empedans tahmini ve çıkış normalizasyonu birbirinden ayrılır.

CST uyumlu VBA makro dışa aktarımı üretilen malzeme geometrisini korur; yerel koaksiyel portun
atlanacağını açıkça bildirir. Makro eşdeğer çalışan simülasyon değildir. Makro çalıştırma veya
çözücüler arası eşdeğerlik doğrulanmamıştır.

## Tekrarlanabilir kontroller

`scripts/validate-coaxial-native.py`, 1/3/3,5 mm yarıçaplı, 100 mm uzunluklu hava dolgulu PEC
koaksiyeli 1–2 GHz bandında, iki CPU iş parçacığı ve -50 dB enerji ölçütüyle kurar. Manifest,
ham çözüm klasörü ve kompleks CSV değerlerini saklar. Her çalıştırmada yeni klasör kullanın:

```sh
python scripts/validate-coaxial-native.py --case through --step .25 --out /yol/yeni/calisma
```

Kontroller geçiş, açık (PMC uç düzlemi) ve kısa devredir (PEC uç düzlemi). Yorumlamadan önce
ardışık enine mesh inceltmelerini ve bilinen TEM hat fazını karşılaştırın. `--reuse-solve` saklanan
probları yeni çözüm başlatmadan işler ve kaynak klasörünü kaydeder.

İlk geçiş hattı inceltmesi analitik hatta göre kompleks hatayı azalttı; kalan faz ve empedans
hatası hâlâ önemlidir. Bu bulgular yayım kabulü değildir. Aşağıdaki kontrol noktası inceltme,
referans düzlemi, sınır ve dolgulu hat bulgularını ayırır. Pasiflik, yakınsama ve çözücünün
bitmesi ayrı ayrı değerlendirilmelidir.

## Doğrulama kontrol noktası: Windows CPU, 11 Ekim 2026

**Bu taslağı doğrulanmış bir yerel port olarak birleştirmeyin.** Aynı hava dolgulu hat kontrolünde
Fairbeam port oluşturması ile kurulu yerel API arasında fark bulunmadı; ancak aşağıdaki analitik
uyum ve mesh yakınsaması ölçütlerinin tamamı sağlanmadı. Çözücü enerjisinin sönmesi S-parametresi
doğruluğunu belgelemez.

Önceden belirlenen sınırlar: mutlak kompleks yanıt hatası 0.05, genlik hatası %1, güç aşımı %1,
son iki enine mesh arasında kompleks değişim 0.025, yan etken kontrollerinde değişim 0.005.
Kompleks hatalar mutlak farktır; **yüzde doğruluk değildir**.

| Sentetik kontrol | Enine adım (mm) | En büyük kompleks hata | En büyük faz hatası |
| --- | ---: | ---: | ---: |
| Hava dolgulu geçiş | 0.25 | 0.11469 | 6.574 derece |
| Hava dolgulu geçiş | 0.125 | 0.06211 | 3.559 derece |
| Hava dolgulu geçiş | 0.0625 | 0.03389 | 1.942 derece |
| Hava dolgulu açık, PMC uç düzlemi | 0.125 | 0.14181 | 8.134 derece |
| Hava dolgulu kısa devre, PEC uç düzlemi | 0.125 | 0.18929 | 10.859 derece |
| Homojen dielektrik geçiş, epsilon = 2.1 | 0.125 | 0.08285 | 4.748 derece |

En ince geçiş koşusu tek koşuluk yanıt sınırını sağlıyor, fakat önceki mesh'e göre 0.02823 değişim
0.025 yakınsama sınırının üzerinde. Eksenel aralığı 1 mm'den 0.5 mm'ye indirmek yanıtı yalnızca
0.000189 değiştiriyor. Fairbeam'i atlayıp doğrudan `AddCoaxialPort` kullanmak kaba meshli kontrolle
karşılaştırıldığında sıfır kompleks fark üretiyor.

Eksenel PML'yi 8 hücreden 10 hücreye çıkarmak yanıtı 0.00002714 değiştiriyor; hattı z ekseninden
x eksenine alıp ters çevirmek 0.00000136 değişim veriyor. Ölçüm düzlemini analitik beta ile
taşımak 0.01230 fark bırakıyor; ölçülen sayısal beta ile fark 0.000130 oluyor. İkincisi yalnızca
sayısal iç tutarlılık kontrolüdür, doğruluk düzeltmesi değildir. Tam iki uyarımlı `B A^-1`
matrisinin en büyük güç özdeğeri 1.000110 (%0.0110 aşım, %1 toleransın altında), karşılıklılık
hatası 0.00000133. İletim kompleks hatası 0.06195 kaldığı için tam matris çıkarımı yayılım
hatasını kaldırmıyor.

[Kompakt sonuçlar ve özetler](../benchmarks/coaxial-tem-windows-20261011/summary.json),
[önceden belirlenen çalışma sözleşmesi](../benchmarks/coaxial-tem-windows-20261011/study-contract.json)
ve [kompleks tam S matrisi](../benchmarks/coaxial-tem-windows-20261011/full-s.csv), ayrı kompleks
CSV dosyaları ve iki ham gelen/yansıyan dalga dizisiyle korunuyor. Aynı klasörde yakınsama grafiği
de bulunuyor. Süreler bir performans kıyaslaması değildir.

Tanı amaçlı mesh'e hizalı basamaklı PEC kesiti kaba geçiş faz hatasını 0.249 dereceye indiriyor;
ancak tahmini empedansı dairesel hattın 65.87 ohm referansı yerine 59.88–59.90 ohm yapıyor.
Geometriyi değiştirir ve **üretim düzeltmesi değildir**. Enine inceltme eğilimiyle birlikte eğri
PEC sınırlarının sayısal temsilini işaret eder; genel bir çözücü düzeltmesini kanıtlamaz.
Sonuçlara uydurulmuş faz veya empedans düzeltmesi uygulanmadı. İdeal PMC açık düzlem kontrolü,
ışıma yapan açık koaksiyel uç değildir.

Çalıştırıcı `--axial-step`, `--eps`, `--axis`, `--reverse`, `--measurement`, `--pml`, `--drive 2`
ve tanı amaçlı `--native` / `--staircase-control` seçeneklerini de kabul eder. İki geçiş uyarımı,
tam `S = B A^-1` pasiflik kontrolü için gelen ve yansıyan dalgaları saklar. `--cell-constant`
malzeme ortalamasını ayrı sınar; kaba hava hattındaki faz hatasını kaldırmadı. Özgün problar,
üretilen model, çalıştırıcının tam kopyası ve kompleks örnekler her koşuda korunur.

Bu Windows CPU kontrollerini macOS, GPU, kayıplı dolgu, keyfi cihazlar veya bütün koaksiyel
boyutlara genellemeyin. Sonraki mühendislik kararı, hem yayılımı hem empedansı koruyan tutarlı
ve yakınsamış bir eğri PEC ayrıklaştırması, ardından aynı kontrollerin tekrarıdır.

İncelenen çalışma zamanı: openEMS Python `0.37.0rc3`, `ports.py` SHA-256:
`0daec44373d6a06395f1c47b252b004df4e99ee2fca5525b486e934451dcc1c1`.
Sürüm metni tek başına başka pakette API bulunduğunu kanıtlamaz.

Üst proje API'si: <https://docs.openems.de/en/latest/python/openEMS/ports.html#coaxial-port>.

### Ek kontrol: mesh yerleşimi ve tutarlı PEC kesiti

Dört yeni yerel kontrol [ek sonuç kaydında](../benchmarks/coaxial-tem-windows-20261011/followup/summary.json)
saklanıyor. Gerçek daire ve portlar sabitken 0.25 mm enine mesh'i yarım veya çeyrek hücre
kaydırmak, geçiş fazında 6.148 ve 8.276 derece hata veriyor. Uygun görünen tek bir mesh
yerleşimi genel bir düzeltme oluşturmuyor.

Tanı amaçlı basamaklı kesitin 0.125 ve 0.0625 mm inceltmelerinde faz hatası 0.259 ve 0.263
derece. Kompleks empedansın 65.871 ohm referansına göre en büyük bağıl hatası %5.36'dan
%2.42'ye düşüyor, fakat önceden ayrıca belirlenen %2 empedans sınırını hâlâ sağlamıyor.
Bu, dairesel referansa doğru inceltme bulgusudur; üretim geometrisini değiştirme gerekçesi
değildir. İnce koşu iki yerine altı CPU iş parçacığı kullandı; performans veya iş parçacığı
eşdeğerliği iddiası yok. Özgün açık/kısa devre, dielektrik ve referans düzlemi doğrulaması
henüz tamamlanmadı.

Çalışan Windows sürecinin yüklediği `openEMS.dll` ve `CSXCAD.dll` dosyaları belirlendi;
SHA-256 değerleri saklanan orijinal dağıtımla aynı çıktı. İki hash ek sonuç kaydında bulunuyor.
Çalıştırıcı artık yüklenen DLL kimliklerini kaydediyor; sınırlı `--transverse-offset` denemesi
ve pozitif `--threads` sayısı kabul ediyor. Çalışma zamanı dosyaları, üretim mesh yöntemi,
kabul sınırları ve yayımlanan sürüm değiştirilmedi.
