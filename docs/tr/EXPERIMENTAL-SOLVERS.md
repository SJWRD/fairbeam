# Deneysel çözücüler

Varsayılan çözücü openEMS'tir. **Elmer FEM — Deneysel özmodlar** ve **Elmer FEM · Deneysel anten**, geometri ve sonuç desteği daha sınırlı, isteğe bağlı araştırma yollarıdır. Arka ucun kurulması veya bir çalıştırmanın tamamlanması, tasarımınızın sayısal doğruluğunu kanıtlamaz.

## Masaüstü uygulamasında kurulum

**Simülasyon › Deneysel çözücüler** komutunu veya Simülasyon çalıştır penceresindeki **Elmer kurulumu ve geçmiş** bağlantısını açın. Elmer'i seçip çalıştırmayı göndermeden önce kullanılabilirliğini denetleyin.

Windows'ta **Elmer'i kur**, siz istediğinizde Fairbeam'in sabitlediği taşınabilir paketi indirir. Paket doğrulanır ve openEMS çalışma ortamından ayrı olarak uygulamanın veri klasörüne kaydedilir. Sistem genelinde kurulmaz. Yönetilen kopyayı kullanmak için yolu boş bırakın. Girdiğiniz klasör, `FAIRBEAM_ELMER_ROOT` veya `PATH` üzerindeki Elmer önceliklidir.

macOS ve Linux'ta Elmer'i kendiniz kurun. `bin/ElmerSolver` ve `bin/ElmerGrid` içeren kurulumu veya ikili dosya klasörünü seçin. Kullanılabilirlik denetimi yürütülebilir dosyaları bulur; fiziksel modelin veya tüm yerel bağımlılıkların yeterliliğini doğrulamaz. Hiçbir arka uç otomatik kurulmaz.

Kurulu uygulama `No module named fairbeam_elmer` bildirirse Fairbeam'i güncelleyin veya onarın. Yalnızca Elmer ikili dosyalarını kurmak, Fairbeam'in Python bağdaştırıcısını sağlamaz. Kaynak üzerinde çalışan katkıcılar ayrı [araştırma kurulumu ve kanıtları](RESEARCH-SOLVERS.md) kullanmalıdır.

## Mevcut tasarımın özmodları

Tasarımı kaydedin, **Simülasyon çalıştır** penceresini açın ve **Elmer FEM — Deneysel özmodlar** seçeneğini seçin. Milimetre cinsinden çevreleyen kutu sınırlarını, en büyük hücre boyutunu ve hedef frekansı gözden geçirin. Kapalı PEC kutusunu açıkça kabul edin: altı yüzün tamamı iletken duvardır; açık ışınım sınırı değildir.

Bu yol, sabit, pozitif, kayıpsız izotropik dielektrik özelliklerine veya PEC malzemeye sahip eksenlere paralel kutu hacimlerini kabul eder. Örtüşmeler, portlar, toplu elemanlar, levhalar, eğriler, dönüşümler, kesiler, kayıp ve dispersiyon reddedilir. Çözücü hedef frekansa yakın iki mod arar; bunların en düşük modlar olması gerekmez.

Sınırlandırılmış çözüm en fazla 4.000 eleman ve 10.000 düğüm, bir CPU iş parçacığı ve 120 saniye yerel yürütme kullanır. Sonuçlar özfrekanslar ile saklanan alanlar/günlüklerdir; anten S parametreleri, kazanç veya ışınım örüntüsü değildir. Tamamlanma `results_validated` olarak değil, `results_exported` olarak kaydedilir.

## Uyarılmış anten deneyleri

Eksenlere paralel dielektrik kutular, PEC levhalar ve tek ideal çizgi ayrık porttan oluşan tasarım için **Elmer FEM · Deneysel anten** seçeneğini kullanın. Sonlu dikdörtgen FEM terminal genişliğini kabul edin; hava payını, mesh boyutunu ve frekans örneklerini gözden geçirin. Terminalin iki ucu da PEC levhalara temas etmelidir. Bu ayarlar FEM anlık kopyasına aittir; özgün tasarım ve openEMS ayarları değişmez.

Açık bölge PML değil, birinci dereceden soğurucu sınırlar kullanır. Sonlu iletkenlikli metaller FEM anlık kopyasında PEC ile yaklaşıklanır; iletken kaybı dışarıda bırakılır ve etkilenen malzemeler kaydedilir. Eğriler, dönüşümler, kesiler, ek toplu elemanlar ve birden fazla port reddedilir.

Sınırlar 10.000 hücre, istenen uzak alan frekansına ek olarak 3–21 tarama örneği, bir CPU iş parçacığı ve frekans başına 180 saniyedir. Sonuçlar karmaşık S11, normalize 3B örüntü ve yönlülük kesitlerini içerir; kazanç ve verim verilmez. Dışa aktarılan sonuçlar yakınsama belgesi değildir. Yeni bir modeli yorumlamadan önce [kayıtlı deneyleri ve kalan yeterlilik çalışmalarını](RESEARCH-SOLVERS.md) inceleyin.

## Kuyruk, sonuçlar ve iptal

Deneysel çalıştırmalar ortak seri kuyruğu kullanır. Hataları, ham girdileri, ayarları, mesh dosyalarını ve günlükleri çözücü geçmişinden inceleyin. Paneli kapatmak çalıştırmayı iptal etmez; kuyruğun iptal komutunu kullanın. Kesilen iş otomatik yeniden başlatılmak yerine saklanır.

Geçmiş her çalıştırmanın girdi anlık kopyasını ve hash değerlerini korur. Deneysel sonuçlar görüntüleyicideki mevcut openEMS anten sonucunun yerini almaz. Mühendislik sonucu çıkarmadan önce mesh düzeylerini ve uygun fiziksel referansları karşılaştırın.

## Diğer deneysel iş akışları

**Koaksiyel TEM (deneysel)**, belirli geometri ve yayın sınırları olan bir openEMS port iş akışıdır; [Koaksiyel portlar](COAXIAL-PORTS.md) sayfasına bakın. Elmer kurulum seçeneği değildir.

Periyodik hücre deneyi ayrı derlenen bir araştırma arka ucu gerektirir. Standart openEMS ve normal GPU motoru bunun yerine geçmez. Desteklenen kapsam normal geliş, sıfır x/y faz kayması ve ilk boşluk kırınım eşiğinin altında eş kutuplu temel yansıma/iletimdir. Genel Floquet portları, eğik geliş ve yüksek mertebeli modlar uygulanmamıştır. Önkoşullar ve saklanan sayısal kanıtlar için [araştırma notlarına](RESEARCH-SOLVERS.md#periodic-unit-cell) bakın.
