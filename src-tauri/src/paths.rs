// Settings and folders (docs/DESKTOP.md "Folders"): the app's read-only resources, the per-user
// app data (runtime, logs), and the workspace the server works on.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager};

/// The repository checkout a debug build runs from (`npm run desktop`).
pub const BUILD_REPO: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/..");

#[derive(Serialize, Deserialize, Clone)]
pub struct Settings {
    /// "managed" (the app's own runtime) or "external" (`python`); unset: managed, or an existing
    /// openEMS install found on this machine while the managed runtime is not installed
    #[serde(default)]
    pub runtime: Option<String>,
    /// Python of an existing openEMS installation (external runtime)
    #[serde(default)]
    pub python: Option<String>,
    /// Use the separately managed Windows GPU runtime on the next start when it is installed and
    /// valid; otherwise continue with the saved external or CPU runtime.
    #[serde(default)]
    pub gpu_runtime_enabled: bool,
    /// use the GPU build (~/opt/openEMS-gpu/venv, Windows C:\opt\openEMS-gpu or \opt\openEMS-gpu on
    /// another fixed drive) when it offers the gpu
    /// engine, before the managed runtime; on by default (settings written by 0.4.1 and older say
    /// false unless the setup screen's box was ticked, and keep that)
    #[serde(default = "default_prefer_gpu")]
    pub prefer_gpu: bool,
    /// workspace folder (projects, models, jobs, raw data); unset: ~/Documents/Fairbeam, or the
    /// repository checkout in debug builds. A folder containing python/fairbeam is used with the
    /// repository layout.
    #[serde(default)]
    pub workspace: Option<String>,
    /// Check for desktop updates after the viewer starts. Older settings default to enabled.
    #[serde(default = "default_check_updates_on_start")]
    pub check_updates_on_start: bool,
    #[serde(default)]
    pub recent_designs: Vec<String>,
    /// Saved display names for recent IDs; no paths or authority to open files.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub recent_design_titles: BTreeMap<String, String>,
    /// the viewer's UI language, "en" or "tr" (General settings › Language, resolved): the native
    /// menus and the splash page use it; unset until the viewer first sends it (English)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub language: Option<String>,
    /// optional sign-in (docs/ACCOUNTS.md): the non-secret profile of who signed in (tokens are in
    /// the OS credential store). Raw JSON, so a build without the accounts feature keeps it.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub account: Option<serde_json::Value>,
    /// the run server's port from the last start (release builds). The viewer keeps its General
    /// settings in the WebView's localStorage, which belongs to the origin `http://127.0.0.1:<port>`:
    /// a new port on every start would lose them, so the next start reuses this one while it is free
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub server_port: Option<u16>,
    /// the one-time import from the previous app (the importer module): {state, at, steps}
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub legacy_import: Option<serde_json::Value>,
    /// viewer preferences from that import, handed to the viewer once (take_imported_viewer_prefs)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub imported_viewer_prefs: Option<serde_json::Value>,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            runtime: None,
            python: None,
            gpu_runtime_enabled: false,
            prefer_gpu: true,
            workspace: None,
            check_updates_on_start: true,
            recent_designs: Vec::new(),
            recent_design_titles: BTreeMap::new(),
            language: None,
            account: None,
            server_port: None,
            legacy_import: None,
            imported_viewer_prefs: None,
        }
    }
}

fn default_prefer_gpu() -> bool {
    true
}

fn default_check_updates_on_start() -> bool {
    true
}

impl Settings {
    pub fn external(&self) -> Option<PathBuf> {
        match (self.runtime.as_deref(), &self.python) {
            (Some("external"), Some(p)) | (None, Some(p)) => Some(PathBuf::from(p)),
            _ => None,
        }
    }

    pub fn clear_recent_designs(&mut self) {
        self.recent_designs.clear();
        self.recent_design_titles.clear();
    }

    fn prune_recent_titles(&mut self) {
        let mut titles = BTreeMap::new();
        for id in self.recent_designs.iter().filter(|id| crate::valid_design_id(id)).take(8) {
            if let Some(title) = self.recent_design_titles.get(id).and_then(|title| recent_title(title)) {
                titles.insert(id.clone(), title);
            }
        }
        self.recent_design_titles = titles;
    }

    /// False means no settings write or menu rebuild is needed. An older viewer can
    /// still send only the ID without erasing a title learned from a newer viewer.
    pub fn remember_recent_design(&mut self, id: String, title: Option<&str>) -> bool {
        let before = (self.recent_designs.clone(), self.recent_design_titles.clone());
        self.recent_designs.retain(|entry| entry != &id);
        self.recent_designs.insert(0, id.clone());
        self.recent_designs.truncate(8);
        if let Some(title) = title {
            if let Some(title) = recent_title(title) {
                self.recent_design_titles.insert(id, title);
            } else {
                self.recent_design_titles.remove(&id);
            }
        }
        self.prune_recent_titles();
        before != (self.recent_designs.clone(), self.recent_design_titles.clone())
    }

    pub fn recent_design_label(&self, id: &str) -> String {
        let title = self.recent_design_titles.get(id).and_then(|title| recent_title(title));
        literal_menu_label(title.as_deref().unwrap_or(id), cfg!(any(target_os = "macos", target_os = "linux")))
    }
}

/// Store a short, single-line Unicode title. Trim/collapse controls and whitespace
/// before truncation; mnemonic escaping happens later so an && pair cannot split.
fn recent_title(title: &str) -> Option<String> {
    let plain: String = title.chars().map(|c| if c.is_control() { ' ' } else { c }).collect();
    let plain = plain.split_whitespace().collect::<Vec<_>>().join(" ");
    if plain.is_empty() {
        None
    } else if plain.chars().count() > 80 {
        Some(plain.chars().take(79).chain(std::iter::once('…')).collect())
    } else {
        Some(plain)
    }
}

fn literal_menu_label(title: &str, muda_sentinel: bool) -> String {
    // Pinned Muda 0.19.3 uses [~~] internally when processing macOS/GTK
    // mnemonics. Separate that exact token for display; keep the stored name.
    let title = if muda_sentinel {
        title.replace("[~~]", "[~ ~]")
    } else {
        title.to_owned()
    };
    title.replace('&', "&&")
}

pub fn settings_path(app: &AppHandle) -> Option<PathBuf> {
    app.path()
        .app_config_dir()
        .ok()
        .map(|d| d.join("settings.json"))
}

pub fn load_settings(app: &AppHandle) -> Settings {
    let mut settings: Settings = settings_path(app)
        .and_then(|p| fs::read_to_string(p).ok())
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or_default();
    settings.prune_recent_titles();
    settings
}

pub fn save_settings(app: &AppHandle, s: &Settings) -> Result<(), String> {
    let path = settings_path(app).ok_or("no app config directory")?;
    if let Some(dir) = path.parent() {
        fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    }
    let tmp = path.with_extension("json.tmp");
    fs::write(&tmp, serde_json::to_vec_pretty(s).unwrap()).map_err(|e| e.to_string())?;
    fs::rename(&tmp, &path).map_err(|e| e.to_string())
}

/// The app's read-only resources: `root` holds runtime/ and python/fairbeam (what the runtime
/// installer calls --resources).
pub struct Res {
    pub root: PathBuf,
    pub ui: Option<PathBuf>,
    pub models: PathBuf,
    pub templates: PathBuf,
    pub projects: PathBuf,
    pub dev: bool,
}

impl Res {
    pub fn locate(app: &AppHandle) -> Res {
        if cfg!(debug_assertions) {
            let repo = canonical(Path::new(BUILD_REPO));
            return Res {
                ui: Some(repo.join("dist")).filter(|d| d.join("index.html").is_file()),
                models: repo.join("python").join("models"),
                templates: repo.join("python").join("templates"),
                projects: repo.join("public").join("projects"),
                root: repo,
                dev: true,
            };
        }
        let root = app
            .path()
            .resource_dir()
            .map(|d| canonical(&d))
            .unwrap_or_else(|_| PathBuf::from("."));
        Res {
            ui: Some(root.join("ui")).filter(|d| d.join("index.html").is_file()),
            models: root.join("models"),
            templates: root.join("templates"),
            projects: root.join("projects"),
            root,
            dev: false,
        }
    }

    /// Version of the bundled fairbeam package (python/fairbeam/_meta.py).
    pub fn fairbeam_version(&self) -> Option<String> {
        let text =
            fs::read_to_string(self.root.join("python").join("fairbeam").join("_meta.py")).ok()?;
        let line = text
            .lines()
            .find(|l| l.trim_start().starts_with("__version__"))?;
        Some(line.split(['"', '\'']).nth(1)?.to_string())
    }
}

/// Where the server keeps its data.
pub struct Workspace {
    pub root: PathBuf,
    pub projects: PathBuf,
    pub models: PathBuf,
    pub jobs: PathBuf,
    pub sim: PathBuf,
    /// the repository layout (a checkout): nothing is seeded
    pub checkout: bool,
}

impl Workspace {
    fn checkout(repo: PathBuf) -> Workspace {
        Workspace {
            projects: repo.join("public").join("projects"),
            models: repo.join("python").join("models"),
            jobs: repo.join(".sim").join("jobs"),
            sim: repo.join(".sim"),
            root: repo,
            checkout: true,
        }
    }

    fn folder(root: PathBuf) -> Workspace {
        // models and templates are siblings: the server finds templates next to models
        Workspace {
            projects: root.join("projects"),
            models: root.join("models"),
            jobs: root.join("jobs"),
            sim: root.join(".sim"),
            root,
            checkout: false,
        }
    }

    pub fn templates(&self) -> PathBuf {
        self.models
            .parent()
            .map(|p| p.join("templates"))
            .unwrap_or_else(|| self.root.join("templates"))
    }
}

pub fn workspace(app: &AppHandle, s: &Settings, res: &Res) -> Workspace {
    if let Some(w) = &s.workspace {
        let p = canonical(Path::new(w));
        return if p.join("python").join("fairbeam").is_dir() {
            Workspace::checkout(p)
        } else {
            Workspace::folder(p)
        };
    }
    if res.dev {
        return Workspace::checkout(res.root.clone());
    }
    let docs = app.path().document_dir().unwrap_or_else(|_| home());
    Workspace::folder(docs.join("Fairbeam"))
}

/// Per-user app data: <root>/runtime and <root>/logs.
pub fn local_data(app: &AppHandle) -> PathBuf {
    app.path()
        .app_local_data_dir()
        .unwrap_or_else(|_| std::env::temp_dir().join("fairbeam"))
}

pub fn runtime_root(app: &AppHandle) -> PathBuf {
    local_data(app).join("runtime")
}

pub fn logs_dir(app: &AppHandle) -> PathBuf {
    let d = local_data(app).join("logs");
    let _ = fs::create_dir_all(&d);
    d
}

pub fn home() -> PathBuf {
    std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/"))
}

/// Canonical path without the Windows `\\?\` prefix (which Python and cmd do not always accept).
pub fn canonical(p: &Path) -> PathBuf {
    let c = fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
    #[cfg(windows)]
    {
        let s = c.to_string_lossy();
        if let Some(rest) = s.strip_prefix(r"\\?\") {
            if !rest.starts_with("UNC\\") {
                return PathBuf::from(rest);
            }
        }
    }
    c
}

#[cfg(test)]
mod settings_tests {
    use super::{literal_menu_label, recent_title, Settings};

    #[test]
    fn recent_titles_are_optional_and_round_trip_without_changing_ids() {
        let mut settings: Settings = serde_json::from_str(r#"{"recent_designs":["fb_123","patch"]}"#).unwrap();
        assert!(settings.recent_design_titles.is_empty());
        assert_eq!(settings.recent_design_label("fb_123"), "fb_123");
        assert!(settings.remember_recent_design("fb_123".into(), Some("Türkçe 文档 🛰")));
        let restored: Settings = serde_json::from_str(&serde_json::to_string(&settings).unwrap()).unwrap();
        assert_eq!(restored.recent_designs, ["fb_123", "patch"]);
        assert_eq!(restored.recent_design_label("fb_123"), "Türkçe 文档 🛰");
    }

    #[test]
    fn unchanged_recent_title_and_legacy_call_need_no_write() {
        let mut settings = Settings::default();
        assert!(settings.remember_recent_design("patch".into(), Some("Saved name")));
        assert!(!settings.remember_recent_design("patch".into(), Some("  Saved\tname  ")));
        assert!(!settings.remember_recent_design("patch".into(), None));
        assert_eq!(settings.recent_design_label("patch"), "Saved name");
        assert!(settings.remember_recent_design("patch".into(), Some("Saved rename")));
        assert_eq!(settings.recent_design_label("patch"), "Saved rename");
        assert!(settings.remember_recent_design("other".into(), Some("Other")));
        assert!(settings.remember_recent_design("patch".into(), None));
        assert_eq!(settings.recent_designs, ["patch", "other"]);
    }

    #[test]
    fn recent_titles_follow_the_eight_ids_and_clear_together() {
        let mut settings = Settings::default();
        for number in 0..10 {
            settings.remember_recent_design(format!("fb_{number}"), Some("Saved"));
        }
        assert_eq!(settings.recent_designs.len(), 8);
        assert_eq!(settings.recent_design_titles.len(), 8);
        assert!(!settings.recent_design_titles.contains_key("fb_0"));
        assert!(!settings.recent_design_titles.contains_key("fb_1"));
        settings.clear_recent_designs();
        assert!(settings.recent_designs.is_empty());
        assert!(settings.recent_design_titles.is_empty());
    }

    #[test]
    fn loaded_recent_titles_are_pruned_and_sanitized_without_file_access() {
        let mut settings: Settings = serde_json::from_value(serde_json::json!({
            "recent_designs": ["patch", "../escape", "empty"],
            "recent_design_titles": {"patch": "Saved\nname", "../escape": "Unsafe ID", "orphan": "Old", "empty": "\t"}
        })).unwrap();
        settings.prune_recent_titles();
        assert_eq!(settings.recent_design_titles.into_iter().collect::<Vec<_>>(), [("patch".into(), "Saved name".into())]);
    }

    #[test]
    fn controls_and_blank_titles_cannot_add_menu_lines_or_accelerators() {
        assert_eq!(recent_title(" \0Patch\t&\r\nantenna\u{0085} 867 \u{2003}"), Some("Patch & antenna 867".into()));
        let mut settings = Settings::default();
        settings.remember_recent_design("patch".into(), Some("Named"));
        assert!(settings.remember_recent_design("patch".into(), Some("\0\r\n\t ")));
        assert_eq!(settings.recent_design_label("patch"), "patch");
    }

    #[test]
    fn recent_titles_bound_unicode_before_escaping_ampersands() {
        assert_eq!(recent_title(&"🛰".repeat(80)).unwrap(), "🛰".repeat(80));
        let title = recent_title(&"文".repeat(81)).unwrap();
        assert_eq!(title.chars().count(), 80);
        assert!(title.ends_with('…'));
        let title = recent_title(&format!("{}&&long", "a".repeat(78))).unwrap();
        assert_eq!(literal_menu_label(&title, false), format!("{}&&…", "a".repeat(78)));
        assert_eq!(literal_menu_label("R&D && A_B", false), "R&&D &&&& A_B");
    }

    #[test]
    fn muda_sentinel_is_only_adjusted_at_the_native_display_boundary() {
        let mut settings = Settings::default();
        settings.remember_recent_design("patch".into(), Some("Patch [~~] & loop"));
        let title = &settings.recent_design_titles["patch"];
        assert_eq!(title, "Patch [~~] & loop");
        assert_eq!(literal_menu_label(title, false), "Patch [~~] && loop");
        assert_eq!(literal_menu_label(title, true), "Patch [~ ~] && loop");
    }

    #[test]
    fn old_settings_keep_update_checks_enabled() {
        let settings: Settings =
            serde_json::from_str(r#"{"runtime":"managed","workspace":"/tmp/work"}"#).unwrap();
        assert!(settings.check_updates_on_start);
        assert_eq!(settings.workspace.as_deref(), Some("/tmp/work"));
        assert!(Settings::default().check_updates_on_start);
    }

    #[test]
    fn update_preference_round_trips() {
        let settings: Settings =
            serde_json::from_str(r#"{"check_updates_on_start":false}"#).unwrap();
        assert!(!settings.check_updates_on_start);
    }

    #[test]
    fn gpu_build_preferred_unless_turned_off() {
        let settings: Settings = serde_json::from_str("{}").unwrap();
        assert!(settings.prefer_gpu);
        assert!(!settings.gpu_runtime_enabled);
        assert!(Settings::default().prefer_gpu);
        let settings: Settings = serde_json::from_str(r#"{"prefer_gpu":false}"#).unwrap();
        assert!(!settings.prefer_gpu);
    }

    #[test]
    fn managed_gpu_preference_is_opt_in_and_round_trips_separately() {
        let settings: Settings = serde_json::from_str(r#"{"runtime":"external","python":"C:\\cpu\\python.exe"}"#).unwrap();
        assert!(!settings.gpu_runtime_enabled);
        let settings: Settings = serde_json::from_str(r#"{"gpu_runtime_enabled":true}"#).unwrap();
        assert!(settings.gpu_runtime_enabled);
    }
}
