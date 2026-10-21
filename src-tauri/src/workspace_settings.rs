use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, PartialEq, Eq)]
pub enum WorkspaceError {
    NotFolder,
    ProtectedPath,
    NotWritable,
}

impl WorkspaceError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::NotFolder => "workspace_not_folder",
            Self::ProtectedPath => "workspace_protected_path",
            Self::NotWritable => "workspace_not_writable",
        }
    }
}

/// A remembered custom workspace is user data, not a request to create a new workspace.
/// In particular, a renamed directory or an absent volume must never be silently re-seeded.
pub fn check_saved(path: &Path) -> Result<(), String> {
    if !path.is_dir() {
        return Err("workspace_not_folder".into());
    }
    fs::read_dir(path).map(|_| ()).map_err(|e| e.to_string())
}

/// Validate a folder before saving it as the workspace for the next app start.
/// The workspace must be an existing writable directory outside app resources and runtime data.
pub fn validate(
    candidate: &Path,
    resources: &Path,
    runtime: &Path,
) -> Result<PathBuf, WorkspaceError> {
    if !candidate.is_dir() {
        return Err(WorkspaceError::NotFolder);
    }

    let candidate = canonical(candidate);
    let resources = canonical(resources);
    let runtime = canonical(runtime);
    if overlaps(&candidate, &resources) || overlaps(&candidate, &runtime) {
        return Err(WorkspaceError::ProtectedPath);
    }
    if !probe_writable(&candidate) {
        return Err(WorkspaceError::NotWritable);
    }
    Ok(candidate)
}

fn canonical(path: &Path) -> PathBuf {
    crate::paths::canonical(path)
}

fn overlaps(a: &Path, b: &Path) -> bool {
    #[cfg(windows)]
    {
        let normalized = |path: &Path| {
            path.to_string_lossy()
                .replace('/', "\\")
                .trim_end_matches('\\')
                .to_lowercase()
        };
        let a = normalized(a);
        let b = normalized(b);
        return path_contains(&a, &b) || path_contains(&b, &a);
    }
    #[cfg(not(windows))]
    {
        a.starts_with(b) || b.starts_with(a)
    }
}

#[cfg(windows)]
fn path_contains(parent: &str, child: &str) -> bool {
    child == parent || child.starts_with(&format!("{parent}\\"))
}

fn probe_writable(directory: &Path) -> bool {
    let nonce = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_nanos())
        .unwrap_or_default();
    let probe = directory.join(format!(
        ".fairbeam-workspace-check-{}-{nonce}",
        std::process::id()
    ));
    let result = (|| {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&probe)
            .ok()?;
        file.write_all(b"").ok()?;
        drop(file);
        fs::remove_file(&probe).ok()?;
        Some(())
    })();
    if result.is_none() {
        let _ = fs::remove_file(&probe);
    }
    result.is_some()
}

#[cfg(test)]
mod tests {
    use super::{canonical, check_saved, validate, WorkspaceError};
    use std::fs;
    use std::path::PathBuf;

    struct TempRoot(PathBuf);

    impl TempRoot {
        fn new() -> Self {
            let path = std::env::temp_dir().join(format!(
                "fairbeam-workspace-settings-{}-{}",
                std::process::id(),
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap()
                    .as_nanos()
            ));
            fs::create_dir_all(&path).unwrap();
            Self(path)
        }
    }

    impl Drop for TempRoot {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn accepts_a_writable_folder_outside_resources_and_runtime() {
        let temp = TempRoot::new();
        let candidate = temp.0.join("workspace");
        let resources = temp.0.join("resources");
        let runtime = temp.0.join("app-data").join("runtime");
        fs::create_dir_all(&candidate).unwrap();
        fs::create_dir_all(&resources).unwrap();

        assert_eq!(
            validate(&candidate, &resources, &runtime),
            Ok(canonical(&candidate))
        );
        assert_eq!(
            fs::read_dir(&candidate).unwrap().count(),
            0,
            "the write probe is removed"
        );
    }

    #[test]
    fn rejects_missing_and_overlapping_folders() {
        let temp = TempRoot::new();
        let resources = temp.0.join("resources");
        let runtime = temp.0.join("app-data").join("runtime");
        fs::create_dir_all(resources.join("ui")).unwrap();
        fs::create_dir_all(&runtime).unwrap();

        assert_eq!(
            validate(&temp.0.join("missing"), &resources, &temp.0.join("runtime")),
            Err(WorkspaceError::NotFolder)
        );
        assert_eq!(
            validate(&resources.join("ui"), &resources, &temp.0.join("runtime")),
            Err(WorkspaceError::ProtectedPath)
        );
        assert_eq!(
            validate(&resources, &resources.join("ui"), &temp.0.join("runtime")),
            Err(WorkspaceError::ProtectedPath)
        );
        assert_eq!(
            validate(&temp.0.join("app-data"), &resources, &runtime),
            Err(WorkspaceError::ProtectedPath)
        );
    }

    #[test]
    fn missing_saved_workspace_is_not_created_and_existing_files_are_untouched() {
        let temp = TempRoot::new();
        let old = temp.0.join("Research");
        let moved = temp.0.join("Research-old");
        fs::create_dir(&old).unwrap();
        fs::write(old.join("my-design.fb"), b"user data").unwrap();
        fs::rename(&old, &moved).unwrap();
        assert_eq!(check_saved(&old), Err("workspace_not_folder".into()));
        assert!(!old.exists());
        assert!(check_saved(&moved).is_ok());
        assert_eq!(fs::read(moved.join("my-design.fb")).unwrap(), b"user data");
        assert_eq!(fs::read_dir(&moved).unwrap().count(), 1);
        assert_eq!(check_saved(&moved.join("my-design.fb")), Err("workspace_not_folder".into()));
    }
}
