// The shell's own texts (native menus, the update dialogs, the file picker title) in the viewer's
// language. The viewer resolves General settings › Language (System follows the OS) and sends the
// result with `set_language`; it is saved in settings.json, so the menus and the splash page start
// in that language next time too. English is the fallback. The viewer's texts are in src/i18n.

use crate::paths::Settings;

/// The saved UI language: "en" or "tr" (English until the viewer has sent one).
pub fn lang(s: &Settings) -> &'static str {
    match s.language.as_deref() {
        Some("tr") => "tr",
        _ => "en",
    }
}

pub fn valid(lang: &str) -> bool {
    matches!(lang, "en" | "tr")
}

/// (key, English, Turkish)
const TEXTS: &[(&str, &str, &str)] = &[
    ("file", "File", "Dosya"),
    ("edit", "Edit", "Düzen"),
    ("view", "View", "Görünüm"),
    ("window", "Window", "Pencere"),
    ("help", "Help", "Yardım"),
    ("open-recent", "Open Recent", "Son Kullanılanlar"),
    ("export", "Export", "Dışa Aktar"),
    ("export-cst", "Geometry…", "Geometri…"),
    ("export-python", "Python…", "Python…"),
    ("export-touchstone", "Touchstone of Current Result…", "Geçerli Sonucun Touchstone Dosyası…"),
    ("export-package", "Package…", "Paket…"),
    ("file-new", "New Design…", "Yeni Tasarım…"),
    ("file-import-cst", "Import VBA Macro…", "VBA Makrosunu İçe Aktar…"),
    ("file-import-pcb", "Import PCB Artwork…", "PCB Çizimini İçe Aktar…"),
    ("file-open", "Open…", "Aç…"),
    ("file-save", "Save", "Kaydet"),
    ("file-save-as", "Save As…", "Farklı Kaydet…"),
    ("file-close", "Close Design", "Tasarımı Kapat"),
    ("close-window", "Close Window", "Pencereyi Kapat"),
    ("settings", "Settings…", "Ayarlar…"),
    ("services", "Services", "Servisler"),
    ("hide-app", "Hide Fairbeam", "Fairbeam'i Gizle"),
    ("hide-others", "Hide Others", "Diğerlerini Gizle"),
    ("show-all", "Show All", "Tümünü Göster"),
    ("quit-macos", "Quit {app}", "{app} Uygulamasından Çık"),
    ("quit-windows", "E&xit", "Çı&kış"),
    ("quit-other", "&Quit", "Çı&k"),
    ("undo", "Undo", "Geri Al"),
    ("redo", "Redo", "Yinele"),
    ("cut", "Cut", "Kes"),
    ("copy", "Copy", "Kopyala"),
    ("paste", "Paste", "Yapıştır"),
    ("select-all", "Select All", "Tümünü Seç"),
    ("delete", "Delete", "Sil"),
    ("view-start", "Start", "Ana Ekran"),
    ("view-design", "Design", "Tasarım"),
    ("view-examples", "Examples", "Örnekler"),
    ("view-tree", "Toggle Tree", "Ağacı Göster/Gizle"),
    ("view-dock", "Toggle Dock", "Alt Paneli Göster/Gizle"),
    ("view-properties", "Toggle Properties", "Özellikleri Göster/Gizle"),
    ("view-ribbon", "Minimize Ribbon", "Şeridi Küçült"),
    ("view-iso", "Isometric", "İzometrik"),
    ("view-top", "Top", "Üst"),
    ("view-front", "Front", "Ön"),
    ("view-right", "Right", "Sağ"),
    ("view-bottom", "Bottom", "Alt"),
    ("view-back", "Back", "Arka"),
    ("view-left", "Left", "Sol"),
    ("view-zoom-in", "Zoom In", "Yakınlaştır"),
    ("view-zoom-out", "Zoom Out", "Uzaklaştır"),
    ("view-zoom-reset", "Reset Zoom", "Yakınlaştırmayı Sıfırla"),
    ("minimize", "Minimize", "Simge Durumuna Küçült"),
    ("maximize", "Zoom", "Büyüt"),
    ("window-fullscreen", "Full Screen", "Tam Ekran"),
    ("bring-all-to-front", "Bring All to Front", "Tümünü Öne Getir"),
    ("help-shortcuts", "Keyboard Shortcuts", "Klavye Kısayolları"),
    ("about", "About Fairbeam", "Fairbeam Hakkında"),
    ("help-docs", "Getting Started Guide", "Başlangıç Kılavuzu"),
    ("help-issues", "Report a Problem…", "Sorun Bildir…"),
    ("help-updates", "Check for Updates…", "Güncellemeleri Denetle…"),
    ("update-title", "Update available", "Güncelleme var"),
    ("update-check-title", "Update check", "Güncelleme denetimi"),
    ("update-up-to-date", "You're up to date. Current version: {version}.", "Güncelsiniz. Geçerli sürüm: {version}."),
    ("update-unavailable", "Could not reach the update service. Check your connection and try again.", "Güncelleme hizmetine ulaşılamadı. Bağlantınızı denetleyip yeniden deneyin."),
    ("update-preview-blocked", "This preview build cannot check for desktop updates.", "Bu önizleme derlemesi masaüstü güncellemelerini denetleyemez."),
    ("update-in-progress", "An update check is already in progress.", "Bir güncelleme denetimi zaten sürüyor."),
    ("update-disabled", "Update checks are disabled in this environment.", "Bu ortamda güncelleme denetimleri kapalı."),
    (
        "update-message",
        "Fairbeam {version} is available (you have {current}).\n\n{notes}\n\nInstall it now? The app restarts; running simulations are cancelled.",
        "Fairbeam {version} yayımlandı (sizdeki: {current}).\n\n{notes}\n\nŞimdi kurulsun mu? Uygulama yeniden başlar; süren simülasyonlar iptal edilir.",
    ),
    ("update-install", "Install and restart", "Kur ve yeniden başlat"),
    ("update-later", "Later", "Daha sonra"),
    ("update-ready", "The update is downloaded. Install and restart now? Running simulations, including those started during the download, will be cancelled. Choose Later to keep working.", "Güncelleme indirildi. Şimdi kurulup yeniden başlatılsın mı? İndirme sırasında başlatılanlar dahil, süren simülasyonlar iptal edilecek. Çalışmaya devam etmek için Daha sonra'yı seçin."),
    ("update-failed-title", "Update failed", "Güncelleme başarısız"),
    ("update-failed", "The update could not be installed: {error}", "Güncelleme kurulamadı: {error}"),
    ("update-move-title", "Move Fairbeam to Applications", "Fairbeam uygulamasını Uygulamalar klasörüne taşıyın"),
    (
        "update-move",
        "Fairbeam cannot update itself while macOS runs it from a temporary read-only copy. This happens when an app is opened from Downloads or a disk image without moving it first.\n\nYour copy: {path}\n\nQuit Fairbeam, drag it into the Applications folder, open it from there and choose “Help › Check for Updates…”. Your designs and settings stay where they are.",
        "macOS Fairbeam uygulamasını geçici ve salt okunur bir kopyadan çalıştırdığı için uygulama kendini güncelleyemiyor. Bu, bir uygulama İndirilenler'den ya da bir disk görüntüsünden taşınmadan açıldığında olur.\n\nSizdeki kopya: {path}\n\nFairbeam uygulamasından çıkın, onu Uygulamalar klasörüne sürükleyin, oradan açın ve “Yardım › Güncellemeleri Denetle…” seçeneğini seçin. Tasarımlarınız ve ayarlarınız yerinde kalır.",
    ),
    (
        "update-move-read-only",
        "Fairbeam cannot update itself here, because the disk that holds it is read-only:\n{path}\n\nQuit Fairbeam, copy it into the Applications folder, open it from there and choose “Help › Check for Updates…”. Your designs and settings stay where they are.",
        "Fairbeam uygulaması burada kendini güncelleyemiyor, çünkü bulunduğu disk salt okunur:\n{path}\n\nFairbeam uygulamasından çıkın, onu Uygulamalar klasörüne kopyalayın, oradan açın ve “Yardım › Güncellemeleri Denetle…” seçeneğini seçin. Tasarımlarınız ve ayarlarınız yerinde kalır.",
    ),
    ("update-move-unknown-path", "(macOS did not say where; usually your Downloads folder)", "(macOS yerini bildirmedi; genellikle İndirilenler klasörü)"),
    (
        "update-folder-not-writable",
        "The folder that holds Fairbeam cannot be written: {path}. Move Fairbeam into the Applications folder and update from there.",
        "Fairbeam uygulamasının bulunduğu klasöre yazılamıyor: {path}. Fairbeam uygulamasını Uygulamalar klasörüne taşıyıp oradan güncelleyin.",
    ),
    ("update-show-in-finder", "Show in Finder", "Finder'da göster"),
    ("update-ok", "OK", "Tamam"),
    (
        "choose-python-windows",
        "Choose the python.exe of an openEMS installation",
        "Bir openEMS kurulumunun python.exe dosyasını seçin",
    ),
    (
        "choose-workspace-title",
        "Choose a workspace folder for the next start",
        "Bir sonraki açılış için çalışma klasörünü seçin",
    ),
    (
        "choose-blender-title",
        "Choose the Blender executable (blender.exe, Blender.app or blender)",
        "Blender çalıştırılabilir dosyasını seçin (blender.exe, Blender.app veya blender)",
    ),
    (
        "choose-python-unix",
        "Choose the Python of an openEMS installation (…/venv/bin/python)",
        "Bir openEMS kurulumunun Python'unu seçin (…/venv/bin/python)",
    ),
];

/// The text of `key` in `lang`; the key itself when it is unknown (a test covers every use).
pub fn text(lang: &str, key: &str) -> String {
    TEXTS
        .iter()
        .find(|(k, _, _)| *k == key)
        .map(|(_, en, tr)| if lang == "tr" { *tr } else { *en })
        .unwrap_or(key)
        .to_string()
}

/// {name} placeholders of a text filled in.
pub fn fill(mut s: String, args: &[(&str, &str)]) -> String {
    for (name, value) in args {
        s = s.replace(&format!("{{{name}}}"), value);
    }
    s
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_used_key_has_both_languages() {
        let sources = [include_str!("main.rs"), include_str!("updater.rs")];
        let mut used = 0;
        for src in sources {
            // every shell text goes through tx("key") (a closure over the saved language)
            for part in src.split("tx(\"").skip(1) {
                let key = &part[..part.find('"').unwrap()];
                assert!(TEXTS.iter().any(|(k, _, _)| *k == key), "no text for {key}");
                used += 1;
            }
        }
        assert!(used > 30, "the menus use the table ({used} uses)");
        for (key, en, tr) in TEXTS {
            assert!(!en.is_empty() && !tr.is_empty(), "{key}");
            let holes = |s: &str| s.matches('{').count();
            assert_eq!(holes(en), holes(tr), "{key}: placeholders differ");
        }
        let mut keys: Vec<_> = TEXTS.iter().map(|(k, _, _)| *k).collect();
        keys.sort();
        keys.dedup();
        assert_eq!(keys.len(), TEXTS.len(), "duplicate keys");
    }

    #[test]
    fn update_dialogs_quote_the_current_help_menu_path() {
        for language in ["en", "tr"] {
            let menu_path = format!("{} › {}", text(language, "help"), text(language, "help-updates"));
            for dialog in ["update-move", "update-move-read-only"] {
                assert!(text(language, dialog).contains(&menu_path), "{language}: {dialog} quotes an outdated menu path");
            }
        }
    }

    #[test]
    fn falls_back_to_english_and_fills_placeholders() {
        assert_eq!(text("tr", "file"), "Dosya");
        assert_eq!(text("en", "file"), "File");
        assert_eq!(text("de", "file"), "File");
        assert_eq!(
            fill(text("tr", "quit-macos"), &[("app", "Fairbeam")]),
            "Fairbeam Uygulamasından Çık"
        );
        assert_eq!(
            fill(text("en", "update-failed"), &[("error", "x")]),
            "The update could not be installed: x"
        );
    }
}
