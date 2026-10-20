# Proje dosyaları (.fb)

Masaüstü uygulaması düzenlenebilir tasarımı taşınabilir `.fb` projesi olarak, kaydedilmiş sonuçları da yanındaki klasöre kaydeder. Mevcut JSON tasarımları ve sonuç paketleri desteklenmeye devam eder. Proje, tasarımı ve sonuçlarının kimliğini birlikte tutar; simülasyon doğruluğunu belgelemez.

## Kaydetme ve açma

Yeni `<name>.fb` konumunu seçmek için **Kaydet** kullanın. İlk kayıt ayrı bir düzenlenebilir çalışma kopyası oluşturur; özgün JSON tasarımı bağımsız kalır. Sonraki kayıtlar seçilen projeyi günceller. Seçiciyi iptal etmek taslağı korur ve proje dosyası oluşturmaz.

Yeniden açmak için **Proje aç…** veya Dosya › Aç kullanın. **Farklı Kaydet…**, yeni proje kimliği ve ayrı çalışma kopyası oluşturur. Yeni bir dosya adı seçin: işletim sisteminin seçicisi üzerine yazma istemi sunsa bile Farklı Kaydet ile var olan projenin üzerine yazılması reddedilir. Proje Fairbeam dışında değiştiyse başka değişikliklerin üzerine yazmak yerine yeniden açın veya ayrı bir kopya kaydedin.

Ağaçtaki proje dosyası ayrıntıları konumu gösterir. Ana ekran da taşınabilir çalışma kopyalarını proje yoluyla tanımlar. Aynı projeyi açmak yeni içe aktarma yapmak yerine kimliğini yeniden kullanır.

## Projenin yanındaki sonuçlar

`<name>.results/`, `<name>.fb` yanında bulunur. Sonuçlar kayıt sırasında çalışma alanından kopyalanır. Yeni tamamlanan çalıştırma, ekleme başarısız olsa bile orada kullanılabilir kalır; uygulama uyarı gösterir ve Kaydet yeniden deneyebilir. Otomatik sonuç ekleme kaydedilmiş tasarım anlık görüntüsünü kullanır; geçerli düzenlemeleri sessizce kaydetmez. Uygulama **Yeni sonuçlar hazır. Sonuçları .fb dosyasına eklemek için projeyi kaydedin.** diyorsa açıkça kaydedin.

Her sonuç, dosya baytlarının sağlamasını ve o çalıştırmada kullanılan tasarım anlık görüntüsünü kaydeder. Ağaç, eşleşen, eski tasarıma ait, eksik ve geçersiz sonuçları ayırır. Tasarımın eşleşmesi; çalıştırma ayarlarının eşleştiğini, mesh yakınsamasını veya fiziksel doğruluğu kanıtlamaz. Eksik veya geçersiz sonuçlar gömülü tasarımın açılmasını engellemez.

Kaydedilmiş sonuçların geometri, kaynak, üst veri ve sonuç verileri içeren türetilmiş `runs/` klasörleri de olabilir. Sağlaması kaydedilen özgün JSON paketi esas alınır. Ayrı dışa aktarılan şekiller ve render görüntüleri, proje kaydedilince otomatik oluşturulmaz.

## Taşıma, paylaşma ve kurtarma

`.fb` dosyasıyla yanındaki `.results` klasörünü klasör adını koruyarak birlikte kopyalayın veya taşıyın. Böylece göreli sonuç başvuruları kullanılabilir kalır. Yalnızca sonuç klasörünü yeniden adlandırmak bu başvuruları bozar. Modeller, iş geçmişi ve render görüntüleri gibi çalışma dosyaları [Başlarken](GETTING-STARTED.md#4-where-your-files-are) bölümünde açıklanır.

Ana ekrandan tasarımı kaldırmak normalde çalışma alanındaki kopyayı kaldırır. Masaüstündeki kaldırma penceresi `.fb` ve ona ait sonuçları projenin yanındaki **Fairbeam Deleted** klasörüne de taşıyabilir. Pencere bunun çalışma alanı geçmişini ve önbellekteki çalıştırmaları koruduğunu, disk alanı boşaltmadığını belirtir. Kaldırma; başka projenin dosyalarını riske atmak yerine belirsiz sahipliği, paylaşılan sonuçları veya okunamayan sahiplik verilerini reddeder.

Kurtarmak için bildirilen `Fairbeam Deleted` konumunu açın, proje ve sonuç klasörünü birlikte seçtiğiniz konuma taşıyın, ardından **Proje aç…** kullanın. Önce varsa hedefi inceleyin. Yalnızca JSON kullanan tasarımlar ve kaydedilmiş Python sürümleri için [çalışma alanı kurtarma](DESIGNER.md#recovering-saved-or-deleted-workspace-designs) adımlarını izleyin.

## Mevcut JSON dosyaları

`.design.json` açmak veya bırakmak, kaynağın üzerine yazmadan düzenlenebilir çalışma kopyası içe aktarır. Masaüstünde Kaydet daha sonra ayrı bir `.fb` projesi oluşturabilir. JSON dışa aktarımı veri alışverişi için kullanılabilir kalır. Yalnızca sonuç içeren bir paketi açmak düzenlenebilir tasarım oluşturmaz.

Tarayıcı projenin gömülü tasarımını içe aktarabilir, ancak bırakılan dosyanın yanındaki sonuç klasörünü okuyamaz. Tam taşınabilir proje iş akışı için masaüstü uygulamasını kullanın.

## Bilinen sınırlamalar

- Bazı Windows kurulumları başlangıçta **Birlikte aç** listesinde Fairbeam'i sunmaz. [Windows açma adımlarını](WINDOWS.md#known-limitations) izleyin veya projeyi Fairbeam içinden açın.
- Farklı Kaydet şu anda yeni hedef dosya adı gerektirir. Arşivlenmiş dosyaları elle kurtarın; arşiv için uygulama içi Geri yükle arayüzü yoktur.
- Dosya sistemlerinin atomik oluşturma ve üst veri desteği farklıdır. Reddedilen kayıt özgün dosyayı korur; desteklenmeyen çıkarılabilir/ağ biriminde yeni kopyayı desteklenen yerel birime kaydedip proje ve sonuçları birlikte kopyalayın. Bu, her birimin desteklendiği anlamına gelmez.
- Projeyi kaydetmek aygıt arızasına karşı yedek değildir. Önemli çalışmaların bağımsız kopyalarını tutun. Depolama denetimleri sıradan dosyaları korur; tüm güvenlik üst verisi ve güç kaybına dayanıklılık garanti kapsamında değildir.

## Biçim başvurusu

UTF-8 JSON dosyası `schema: "fairbeam.document/1"` ve şu alanları kullanır:

| Alan | Anlamı |
| --- | --- |
| `id` | Projeyi tanımlayan kalıcı küçük harfli UUID |
| `design` | Gömülü düzenlenebilir `fairbeam.design/1` tasarımı |
| `results_directory` | Projenin yanındaki taşınabilir klasör adı |
| `results` | `file`, `sha256` ve `design_sha256` içeren başvurular |

Proje sınırı 40 MiB'dir; her sonuç en fazla 200 MiB, başvuru sayısı en fazla 10.000'dir. Sağlamalar kimliği gösterir, sayısal doğrulama değildir. Geliştiriciler [depolama sözleşmeleri ve platform denetimlerini](FB-DOCUMENT-DEVELOPMENT.md) okuyabilir.
