# Başlarken

<!--
  Web sitesi bu dosyayı Türkçe Başlarken kılavuzu olarak yayımlar.
-->

Bu kılavuz, Fairbeam masaüstü uygulamasını denemek isteyenler içindir: uygulamayı kurun, ardından bir yama anten tasarlayın, simüle edin ve sonuçlarını inceleyin. Tasarımcının tüm özellikleri [DESIGNER.md](DESIGNER.md) sayfasında açıklanır. Fairbeam'i kaynak kodundan derlemek için [Fairbeam'i kaynak kodundan çalıştırma](FROM-SOURCE.md) sayfasına bakın. Uygulamada Yardım › Başlarken kılavuzu bu sayfanın yayımlanmış sürümünü açar.

## 1. Kurulum

Kurulum dosyasını [fairbeam.org](https://fairbeam.org) veya [sürümler sayfasından](https://github.com/ismailakdag/fairbeam-releases/releases) indirin.

- **macOS** (Apple silicon, macOS 27 veya üzeri): `.dmg` dosyasını açın ve Fairbeam'i Uygulamalar klasörüne sürükleyin. Uygulama Developer ID ile imzalanmış ve Apple tarafından doğrulanmıştır.
- **Windows** (10/11, x64): `-setup.exe` dosyasını çalıştırın. Yönetici izni istemeden kullanıcı hesabınıza kurulur. Kurulum dosyası kod imzalı olmadığından SmartScreen “Windows bilgisayarınızı korudu” mesajını gösterebilir: **Ek bilgi › Yine de çalıştır** seçeneğini kullanın. `.fb` dosyasına çift tıkladığınızda Fairbeam önerilmiyorsa [.fb dosyalarını açma](WINDOWS.md#known-limitations) bölümüne bakın.

**İlk açılış.** Fairbeam'in simülasyon çalışma ortamına (Python, openEMS ve Fairbeam paketi) ihtiyacı vardır. Kurulum ekranında çalışma ortamının kurulması gerektiği belirtilir: **Çalışma ortamını kur** seçeneğini kullanın (düğmede indirme boyutu gösterilir; süre bağlantınıza ve sisteme bağlıdır). Mevcut bir openEMS kurulumunuz varsa **Mevcut bir Python kullan…** ile Fairbeam'i bu kuruluma yönlendirin. Uygulama ardından **Ana ekran** ile açılır. Sonraki açılışlarda bu adım atlanır; yeni sürümler uygulamada sunulur (Yardım › Güncellemeleri denetle).

## 2. İlk tasarım uygulaması

### Tasarım oluşturun

1. **Ana ekran** üzerinde **Yeni tasarım** altında bir ad yazın (örneğin `My patch`), Baskılı grubundaki **Yama anten şablonu** seçeneğini (“Toprak düzlemi üzerinde prob beslemeli yama”) seçin ve **Oluştur ve tasarımcıyı aç** düğmesine tıklayın. Diğer şablonlar çalıştırılmaya hazır parametrik tasarımlardır: **Boş tasarım** (frekans bandı ve bakır içerir, geometri içermez), Temel antenler altında **Yarım dalga dipol**, **Çeyrek dalga monopol** ve **Açık uçlu dalga kılavuzu**; Baskılı altında **Baskılı kılıflı dipol** (867 MHz, toprak düzlemi yok); Devreler altında **Mikroşerit hat (iki portlu)** (FR-4 üzerinde 50 Ω, S11 ve S21 için). Her biri saniyeler içinde simüle edilir; bu kılavuz yama antenle devam eder.
2. Tasarımcı açılır. Çalışma alanını inceleyin:
   - üstteki **şerit**: Giriş, Modelleme, Dönüştür, Görünüm, Simülasyon, Optimizasyon, Son işlem;
   - soldaki **gezinti ağacı**: tasarım, **Parametreler** (6 adet: `f0`, `W`, `L`, `h`, `G`, `feed`), **Katılar** (Alttaş, Toprak düzlemi, Yama; her birinin şekliyle birlikte), **Malzemeler**, **Portlar** (Port 1, 50 Ω) ve **Sonuçlar** (henüz boş);
   - ortadaki **3B görünüm**: döndürmek için sürükleyin, yakınlaştırmak için kaydırın, görünümü kaydırmak için sağ veya orta düğmeyle sürükleyin; İzometrik / Üst / Ön / Sağ / Alt kamerayı ayarlar, hedef düğmesi (veya Boşluk / F) modeli ekrana sığdırır;
   - sağdaki **Özellikler**: seçtiğiniz öğenin alanları;
   - alttaki **alt panel**: Denetimler ve Parametreler; daha sonra Çalıştırma (canlı ilerleme), Çalıştırmalar (tasarımın çalıştırmaları yan yana) ve Günlük. Çalıştırmanın grafikleri 3B görünümün yanında sekme olarak açılır.
3. Ağacın veya modelin üzerindeki **Yama** öğesine tıklayıp kutu şeklini inceleyin: her değer bir sayı veya `W/2` gibi parametrelere bağlı bir ifade olabilir.
4. Ağaçta veya 3B görünümde bir katıya sağ tıklayarak Gizle, Yeniden adlandır, Dönüştür…, Çoğalt, Sil, **Ayrık port ekle**, **Bileşene taşı ›** ve Boolean işlemlerine ulaşın. Bileşene taşınan katı ağaçta bir klasörde görünür; klasörü yeniden adlandırmak, grubu çözmek veya silmek için sağ tıklayın.

### Simülasyon ayarları

Şeritte **Simülasyon** sekmesini açın:

- **Frekans bandı**: GHz cinsinden `f min` ve `f max`. Şablonda tasarım frekansı `f0` = 2,45 GHz çevresinde `f0 * 0.6` ile `f0 * 1.3` kullanılır.
- **Sınırlar**: simülasyon kutusunun altı yüzü; her biri açık (MUR veya PML), elektrik duvarı (PEC) veya manyetik duvar (PMC) olabilir. Yama şablonunda altı yüzün tamamında MUR kullanılır.
- **Mesh**: **Hücre / λ** otomatik mesh yoğunluğunu ayarlar; ayrıntılar **Mesh ayarları** bölümündedir. **Mesh yakınsaması…**, mesh'in yeterince ince olup olmadığını kontrol eder (aşağıya bakın); **Mesh görünümü** mesh'i 3B görünümde çizer. Durum çubuğu hücre sayısını ve en küçük hücreyi gösterir.
- **Portlar**: **Ayrık** ve **Dalga kılavuzu** bir port; **Toplu eleman ekle…** bir R/L/C yükü ekler. Deneysel koaksiyel portun [kurulumu ve sınırları](COAXIAL-PORTS.md) ayrıca açıklanır.
- **Monitörler**:
  - **Uzak alan**: listelenen frekanslarda ışıma örüntüsü, yönlülük ve kazanç (şablon bunları `f0` frekansında kaydeder).
  - **Yüzey akımı**: belirttiğiniz frekanslarda metal levhalara yüzey akımı haritaları ekler.
  - **Verimlilik**: f min ile f max arasında eşit aralıklı frekanslarda ışıma ve toplam verimliliği gösteren **Bant boyunca verimlilik** monitörünü ekler (son işlem; ek çözücü süresi gerekmez).
  - **Alan düzlemi**: modelden geçen kesit düzlemine E veya H alanı haritası ekler (en fazla dört düzlem, her birinde bir ila dört frekans); yeni düzlem modelin hemen üzerinde başlar.

  Bu düğmelerin her biri **Simülasyon ayarları** penceresini ilgili bölümde açar; **Tamam** değişiklikleri korur, **İptal** önceki ayarları geri getirir.
- **Çözücü sınırları**: durdurma ölçütü (şablonlarda −60 dB; boş tasarımda −50 dB) ve en fazla zaman adımı sayısı.

Alt panelin **Denetimler** sekmesi çalıştırmayı engelleyen sorunları listeler; düzeltilecek bir şey yoksa durum çubuğunda “Denetimler geçti” yazar.

### Çalıştırın

**Çalıştır** düğmesine tıklayın (Simülasyon sekmesi, üstteki Çalıştır düğmesi veya Ctrl/⌘+Enter). **Simülasyonu çalıştır** penceresi şunları gösterir:

- **Çözücü**: bu uygulamada **openEMS** olarak bırakın. Deneysel Elmer seçeneklerinin geometri ve doğrulama sınırları farklıdır; bkz. [Deneysel çözücüler](EXPERIMENTAL-SOLVERS.md).
- **Motor**: **CPU (çok iş parçacıklı)** veya openEMS'in GPU sürümü kuruluysa **GPU (Metal veya CUDA sürümü)**. CPU için **İş parçacığı** varsayılan olarak **Otomatik** konumdadır: ipucu mesh boyutuna ve fiziksel çekirdeklere göre seçilen sayıyı gösterir. Belirli bir sayıya ihtiyacınız yoksa Otomatik'te bırakın;
- **S-parametresi örnek noktaları** (varsayılan 801) ve isteğe bağlı **Sonuç adı**;
- tahmini çözücü süresi.

**Çalıştır** düğmesine tıklayın (kaydedilmemiş değişiklikler varken **Kaydet ve çalıştır** yazar). Önce tasarım kaydedilir; ilerleme (zaman adımları, enerji sönümü) alt panelde gösterilir. Şablon, bilgisayara bağlı olarak birkaç saniye ile bir dakika arasında tamamlanır.

### Sonuçlar

Çalıştırma tamamlanınca ağaçta **Sonuçlar** altında süresi ve motoruyla birlikte görünür:

- **1B Sonuçlar › S-parametreleri**, |S11|'i 3B görünümün yanında sekme olarak açar. **İşaretçi ekle**, **Önceki minimum** / **Sonraki minimum**, eşik altındaki **Otomatik işaretçiler** ve kopyalanabilir tablo için grafikte **İşaretçiler** düğmesine tıklayın (veya M'ye basın).
- **1B Sonuçlar › Smith abağı**, **Empedans**, **VSWR** ve **Verimlilik** (bant boyunca uyumsuzluk verimliliği ile monitörün ışıma ve toplam verimliliği).
- **Uzak alanlar › 3B örüntü (f = …)**, örüntüyü 3B görünümde model üzerine çizer; yanındaki uzak alan kartından **Yönlülük**, **Kazanç** veya **Gerçekleşen kazanç** seçin; maksimum değeri, kazancı, gerçekleşen kazancı, ışıma ve uyumsuzluk verimliliğini okuyun. **Uzak alan (f = …)** örüntü kesitlerini sekme olarak açar.
- **2B/3B Sonuçlar › Yüzey akımı** (monitör varsa) metal üzerindeki akım haritasını gösterir. **E alanı (z = … mm, … GHz)** gibi alan düzlemi düğümü (Alan düzlemi monitörü varsa), E veya H haritasını 3B görünümde dB veya doğrusal renk ölçekli bir ısı haritası düzlemi olarak çizer. **2B harita** düğümü bunu eksenler, imleç altında değer okuma ve modelin dış çizgisiyle bir ısı haritası sekmesinde açar; **Faz** / **Canlandır** bir periyot boyunca fazı ve alanı gösterir.
- **Tablolar › Özet** (veya Son işlem sekmesindeki **Özet**), çalıştırma içeriyorsa rezonansı, en düşük |S11| değerini, bant genişliğini, kazancı ve verimi gösterir. Eksik büyüklükler kullanılabilir gibi gösterilmez.
- **Tablolar** ve **Günlük**.

Şeridin **Son işlem** sekmesi aynı görünümleri açar. Bir geometri sekmesini (Giriş, Modelleme, …) seçmek veya bir katıya tıklamak, örüntüyü 3B görünümden kaldırıp tasarımı yeniden gösterir.

### Bir parametreyi değiştirip çalıştırmaları karşılaştırın

1. Ağaçta **Parametreler** öğesine tıklayın (alt panelin Parametreler sekmesini açar) ve yama genişliği `W` değerini örneğin 32 mm'den 30 mm'ye değiştirin. 3B görünüm anında güncellenir.
2. Yeniden **Çalıştır** seçeneğini kullanın. Sonuçlar artık en yenisi üstte olacak şekilde iki çalıştırma listeler.
3. Ağaçta iki çalıştırmaya **Ctrl/⌘ basılıyken tıklayın** (veya sonuç sekmesinin araç çubuğunda **Karşılaştır** seçeneğini kullanın ya da alt panelin **Çalıştırmalar** sekmesinde etiketlerine tıklayın): grafikler çalıştırmaları birlikte, her biri ayrı bir renkle çizer (en fazla sekiz çalıştırma).

### Mesh yeterince ince mi?

**Simülasyon › Mesh yakınsaması…**, tasarımı gittikçe incelen otomatik mesh'lerle çalıştırır (varsayılan olarak dalga boyu başına 15, 20, 30 ve 40 hücre) ve her çalıştırmayı bir öncekiyle karşılaştırır: rezonans frekansı, rezonanstaki |S11| ve uzak alan açıksa maksimum yönlülük. Başlamadan önce pencere her yoğunluk için hücre sayısını ve tahmini süreyi gösterir. **Başlat** tasarımı kaydeder ve ilk çalıştırmayı kuyruğa alır; sonrakini ancak önceki tamamlanınca ve sonuçlar hâlâ değişiyorsa kuyruğa ekler. Çalışma, tüm değişikliklerin toleransların altına indiği ilk adımda durur ve **N hücre/λ'da yakınsadı** sonucunu bildirir; **Tasarıma N hücre/λ uygula** bu yoğunluğu ayarlar (Geri Al ile geri dönebilirsiniz). Çalıştırmalar Sonuçlar altında tek bir **Mesh yakınsaması** klasöründe görünür; **Yakınsama raporu** tablo ve grafikleri yeniden açar. **Yakınsamadı** sonucu çıkarsa mesh'i daha da inceltin veya modeli kontrol edin. Ayrıntılar: [DESIGNER.md, Mesh yakınsaması](DESIGNER.md#mesh-convergence).

### Parametre taraması

**Optimizasyon › Parametre taraması**, **Parametre taraması** penceresini açar: **Parametre ekle** ile bir parametre seçin (örneğin `Yama genişliği (x) (W)`), ardından **Başlangıç**, **Bitiş** ve **Örnek sayısı** ile **Eşit aralıklı değerler** veya **Değer listesi** kullanın. **Doğrula**, kaç simülasyon yapılacağını gösterir; **Başlat** tasarımı kaydeder ve çalıştırmaları kuyruğa alır. Çalıştırmalar Sonuçlar altında tek bir **Tarama** klasöründe görünür; **Tüm çalıştırmaları karşılaştır** için klasöre sağ tıklayın.

### Optimizasyon aracı

**Optimizasyon › Optimizasyon aracı**, **Tasarımı optimize et** penceresini açar:

- **Değişkenler**: başlangıç değeri ve Min / Maks sınırlarıyla değiştirilecek parametreler;
- **Hedefler**: örneğin 2,45 GHz'e **Rezonansı ayarla**, **Frekansta uyum**, **Bant genişliğini büyüt** veya **Yönlülük en az**; her hedefin bir ağırlığı vardır;
- **Değerlendirme ≤**, **İş parçacığı** ve **Yöntem** (Otomatik, tek parametreyi bir frekansa ayarlarken sekant yöntemini; daha fazlası için Nelder–Mead, Bayes, CMA-ES ve diğer yöntemleri kullanır).

**Optimizasyonu başlat**, her değerlendirmede bir simülasyon çalıştırır; ilerleme alt panelde görünür. Optimizasyon daha sonra Sonuçlar altında **Optimizasyon geçmişi**, **En iyiyi aç**, **En iyiyi çalıştırma olarak kaydet** ve **En iyi parametreleri tasarıma uygula** seçenekleriyle görünür. Bkz. [OPTIMIZE.md](OPTIMIZE.md).

## 3. VBA makrosu içe aktarın

**Ana ekran** üzerindeki **VBA makrosunu içe aktar…** (ayrıca Giriş › Tasarım › VBA makrosunu içe aktar veya masaüstü uygulamasında Dosya › VBA makrosunu içe aktar…), CST uyumlu VBA makrosunu (`.bas`, `.mcs`) ya da metin olarak kaydedilmiş geçmiş listesini okur. Kaydetmeden önce oluşturulanları ve atlanan veya değiştirilen her komutu satırıyla birlikte bir raporda gösterir. Tasarıma ad verip **Oluştur ve aç** seçeneğini kullanın. Okunan ve okunmayan özellikler: [DESIGNER.md, VBA makrosu içe aktarma](DESIGNER.md#importing-a-vba-macro).

## 4. Dosyalarınızın konumu

Masaüstü uygulamasında **Kaydet**, seçtiğiniz konuma `<name>.fb` yazar. **Farklı Kaydet…** ayrı bir proje oluşturur; **Proje aç…** projeyi yeniden açar. Yanındaki `<name>.results/` klasörü kaydedilmiş sonuç paketlerini tutar. Projeyi taşırken, kopyalarken veya paylaşırken `.fb` dosyasıyla bu klasörü birlikte tutun.

Tamamlanan çalıştırma çalışma alanında kullanılabilir kalır. “Yeni sonuçlar hazır” bildirimi çıktığında sonuçları eklemek için projeyi kaydedin; otomatik sonuç ekleme kaydedilmiş tasarım anlık görüntüsünü kullanır, daha yeni düzenlemeleri sessizce kaydetmez. Proje dosyası ayrıntıları sonucun bu anlık görüntüyle eşleştiğini, eski bir tasarıma ait olduğunu, eksik veya geçersiz olduğunu gösterir. Bunlar kimlik denetimleridir; yakınsama kanıtı değildir.

Tasarımı kaldırmak normalde çalışma alanındaki kopyayı kaldırır. Masaüstündeki kaldırma penceresinde **.fb dosyasını ve sonuç klasörünü de mevcut konumlarından kaldır** seçilirse proje ve ona ait sonuçlar, projenin yanındaki `Fairbeam Deleted/` klasörüne taşınır. Kalıcı olarak silinmezler. Kurtarma ve sınırlamalar [Proje dosyaları (.fb)](FB-DOCUMENT.md) sayfasındadır.

### Çalışma klasörü

Yerel çalışma klasörü varsayılan olarak `Documents/Fairbeam` konumundadır (Windows: `%USERPROFILE%\Documents\Fairbeam`). Yalnızca JSON kullanan tasarımları ve Python modellerini de destekler:

| Klasör | İçerik |
| --- | --- |
| `models/` | Çalışma tasarımları (`<name>.design.json`), taşınabilir `fb_<UUID>` kopyaları ve Python modelleri |
| `projects/` | Çalışma alanının sonuç paketleri ve `index.json` |
| `templates/` | Model şablonları |
| `jobs/` | Çalıştırma kuyruğu durumu ve geçmişi |
| `.sim/` | Çözücünün çalışma verileri |
| `renders/<design-id>/` | Uygulamadan kaydedilen render görüntüleri |
| `model-history/` | Kaydedilmiş veya kaldırılmış tasarım/Python kurtarma kopyaları |

**Genel ayarlar › Çalışma klasörü** konumu gösterir; **Klasörü aç** bu konumu açar. **Dil** ve **Ondalık ayırıcı**, arayüzü ve gösterilen sayıları etkiler. Ana ekrandaki örnekler salt okunur özgün dosyalardır: **Yeni tasarım olarak aç…** size ait kopyayı oluşturur.

## 5. Sorun bildirin

**Ana ekran** altındaki **Geri bildirim gönder: Sorun bildir** (veya **Özellik öner**), uygulama sürümü ve işletim sisteminiz doldurulmuş olarak herkese açık [fairbeam-releases/issues](https://github.com/ismailakdag/fairbeam-releases/issues) takip sayfasının bildirim formunu tarayıcınızda açar. Üstteki konuşma balonu düğmesi (**Geri bildirim gönder**) ve Yardım › Sorun Bildir… her ekrandan aynı işlemi yapar. Fairbeam kendiliğinden hiçbir şey göndermez.

Şunları eklemek yararlıdır: yaptıklarınız ve beklediğiniz sonuç, ekran görüntüsü (Görünüm › Görünüm › **Ekran görüntüsü** 3B görünümü PNG olarak kaydeder), `.fb` projesi ve yanındaki `.results` klasörü (yalnızca JSON ile çalışıyorsanız çalışma alanındaki tasarım) ve çalıştırmanın günlüğü (Sonuçlar › çalıştırmanız › Günlük).
