//! Native .fb selection grants. The webview receives an opaque handle, never permission to
//! provide a filesystem path. Only an OS open request or a native dialog creates a grant.
use std::collections::{HashMap, HashSet, VecDeque};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde::Deserialize;
use serde_json::{json, Value};
use tauri::{AppHandle, Manager};
use tauri_plugin_dialog::DialogExt;

use crate::server::{DocumentClient, DocumentOperation};
use crate::Shell;

const MAX_PENDING_OPENS: usize = 64;
const MAX_JSON_IMPORT: u64 = 40 * 1024 * 1024;

#[derive(Clone)]
struct Grant {
    path: PathBuf,
    document_id: String,
    revision: String,
    model_id: String,
    client: DocumentClient,
    /// Selection metadata only; a renderer reload must never restore an older design body.
    binding: Value,
}

#[derive(Default)]
struct Selections {
    grants: HashMap<String, Grant>,
    grant_order: VecDeque<String>,
    pending: VecDeque<PendingOpen>,
    saving: HashSet<String>,
}

struct PendingOpen {
    handle: String,
    path: PathBuf,
}

#[derive(Default)]
pub struct NativeDocuments(Mutex<Selections>);

struct Saving {
    app: AppHandle,
    model_id: String,
}

impl Drop for Saving {
    fn drop(&mut self) {
        self.app
            .state::<NativeDocuments>()
            .0
            .lock()
            .unwrap()
            .saving
            .remove(&self.model_id);
    }
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SaveRequest {
    handle: Option<String>,
    model_id: String,
    workspace_hash: String,
    #[serde(default)]
    save_as: bool,
    suggested_name: Option<String>,
}

fn error(message: impl Into<String>, status: u16) -> Value {
    json!({"error": message.into(), "status": status})
}

fn string_fields(value: &Value, fields: &[&str]) -> Value {
    Value::Object(
        fields
            .iter()
            .filter_map(|field| {
                value
                    .get(*field)
                    .and_then(Value::as_str)
                    .map(|text| ((*field).to_owned(), json!(text)))
            })
            .collect(),
    )
}

/// Whitelist scalar selection/reference fields. In particular, neither file.design nor any
/// unknown response field is retained in a grant, even if the backend later adds one.
fn binding_metadata(response: &Value, handle: &str, path: &Path) -> Value {
    let mut document = string_fields(
        &response["document"],
        &["id", "revision", "results_directory"],
    );
    document["path"] = json!(path.to_string_lossy());
    let results: Vec<Value> = response["results"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|result| {
            string_fields(
                result,
                &[
                    "file",
                    "sha256",
                    "design_sha256",
                    "provenance",
                    "status",
                    "project_file",
                    "error",
                ],
            )
        })
        .collect();
    let warnings: Vec<&str> = response["warnings"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .collect();
    json!({
        "handle": handle,
        "path": path.to_string_lossy(),
        "file": string_fields(&response["file"], &["id", "backup_scope"]),
        "document": document,
        "results": results,
        "warnings": warnings,
    })
}

fn validate_name(name: &str) -> Result<(), Value> {
    let stem = name.split('.').next().unwrap_or("").to_uppercase();
    let reserved_number = |prefix: &str| {
        stem.strip_prefix(prefix).is_some_and(|n| {
            n.chars().count() == 1 && n.chars().all(|c| matches!(c, '0'..='9' | '¹' | '²' | '³'))
        })
    };
    if name.is_empty()
        || name.len() > 180
        || name == "."
        || name == ".."
        || name.ends_with(['.', ' '])
        || name
            .chars()
            .any(|c| c.is_control() || "/\\:*?\"<>|".contains(c))
        || matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        || reserved_number("COM")
        || reserved_number("LPT")
        || Path::new(name)
            .extension()
            .and_then(|v| v.to_str())
            .map(|extension| extension.eq_ignore_ascii_case("fb"))
            != Some(true)
    {
        return Err(error(
            "A Fairbeam document needs a portable .fb filename",
            400,
        ));
    }
    Ok(())
}

fn linked(metadata: &fs::Metadata) -> bool {
    if metadata.file_type().is_symlink() {
        return true;
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return true;
        }
    }
    false
}

fn selected_path(path: &Path, allow_missing: bool) -> Result<PathBuf, Value> {
    if !path.is_absolute() {
        return Err(error("The selected document path must be absolute", 400));
    }
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| error("The selected document filename is invalid", 400))?;
    validate_name(name)?;
    let parent = path
        .parent()
        .and_then(|parent| parent.canonicalize().ok())
        .filter(|parent| parent.is_dir())
        .ok_or_else(|| error("The selected document folder is unavailable", 400))?;
    let selected = parent.join(name);
    if selected.to_str().is_none() {
        return Err(error("The selected document path must use UTF-8 text", 400));
    }
    match fs::symlink_metadata(&selected) {
        Ok(metadata) if metadata.is_file() && !linked(&metadata) => {}
        Err(e) if allow_missing && e.kind() == std::io::ErrorKind::NotFound => {}
        _ => {
            return Err(error(
                "The selected document must be a regular file, not a link",
                400,
            ))
        }
    }
    Ok(selected)
}

fn read_json_import(path: &Path) -> Result<Value, Value> {
    if !path.is_absolute()
        || !path
            .extension()
            .is_some_and(|extension| extension.eq_ignore_ascii_case("json"))
    {
        return Err(error("Select a JSON file to import", 400));
    }
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| error("The selected filename must use UTF-8 text", 400))?;
    let metadata = fs::symlink_metadata(path)
        .map_err(|_| error("The selected JSON file is unavailable", 400))?;
    if !metadata.is_file() || linked(&metadata) || metadata.len() > MAX_JSON_IMPORT {
        return Err(error("Select a regular JSON file of at most 40 MiB", 400));
    }
    let mut options = fs::OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        options.custom_flags(0x0020_0000); // FILE_FLAG_OPEN_REPARSE_POINT: never follow a swapped link.
    }
    let file = options
        .open(path)
        .map_err(|_| error("The selected JSON file could not be read", 400))?;
    let metadata = file
        .metadata()
        .map_err(|_| error("The selected JSON file is unavailable", 400))?;
    if !metadata.is_file() || linked(&metadata) {
        return Err(error("Select a regular JSON file, not a link", 400));
    }
    let mut bytes = Vec::new();
    file.take(MAX_JSON_IMPORT + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| error("The selected JSON file could not be read", 400))?;
    if bytes.len() as u64 > MAX_JSON_IMPORT {
        return Err(error("JSON import exceeds 40 MiB", 413));
    }
    let content = String::from_utf8(bytes)
        .map_err(|_| error("The selected JSON file must use UTF-8 text", 400))?;
    Ok(json!({"status":"import", "name":name, "content":content}))
}

impl Selections {
    fn queue(&mut self, path: PathBuf) {
        if self.pending.len() >= MAX_PENDING_OPENS
            || self.pending.iter().any(|pending| pending.path == path)
        {
            return;
        }
        if path
            .extension()
            .and_then(|e| e.to_str())
            .is_some_and(|e| e.eq_ignore_ascii_case("fb"))
        {
            self.pending.push_back(PendingOpen {
                handle: format!("fb-{}", crate::server::token()),
                path,
            });
        }
    }

    fn pending_path(&self, handle: &str) -> Result<PathBuf, Value> {
        self.pending
            .iter()
            .find(|pending| pending.handle == handle)
            .map(|pending| pending.path.clone())
            .ok_or_else(|| error("This pending document selection has expired", 400))
    }

    fn next_open(&self, exclude: &[String]) -> Value {
        self.pending
            .iter()
            .find(|pending| !exclude.contains(&pending.handle))
            .map(|pending| {
                json!({
                    "handle": pending.handle,
                    "displayName": pending.path.file_name().map(|name| name.to_string_lossy())
                })
            })
            .unwrap_or(Value::Null)
    }

    fn grant(&self, handle: &str) -> Result<Grant, Value> {
        self.grants
            .get(handle)
            .cloned()
            .ok_or_else(|| error("This document selection has expired; open it again", 400))
    }

    fn restore(&self, model_id: &str, client: &DocumentClient) -> Value {
        self.grant_order
            .iter()
            .rev()
            .filter_map(|handle| self.grants.get(handle))
            .find(|grant| grant.model_id == model_id && grant.client == *client)
            .map(|grant| grant.binding.clone())
            .unwrap_or(Value::Null)
    }

    fn begin_save(
        &mut self,
        handle: Option<&str>,
        model_id: &str,
        client: &DocumentClient,
    ) -> Result<Option<Grant>, Value> {
        let grant = handle.map(|handle| self.grant(handle)).transpose()?;
        if let Some(grant) = &grant {
            if grant.model_id != model_id || grant.client != *client {
                return Err(error(
                    "This document belongs to another workspace session; open it again",
                    409,
                ));
            }
        }
        if !self.saving.insert(model_id.to_owned()) {
            return Err(error("This document is already being saved", 409));
        }
        Ok(grant)
    }

    fn remember(
        &mut self,
        path: PathBuf,
        mut response: Value,
        handle: Option<String>,
        status: &str,
        client: DocumentClient,
    ) -> Result<Value, Value> {
        let document = response
            .get("document")
            .and_then(Value::as_object)
            .ok_or_else(|| error("The document server returned no document identity", 502))?;
        let document_id = document
            .get("id")
            .and_then(Value::as_str)
            .filter(|id| !id.is_empty())
            .ok_or_else(|| error("The document server returned no document identity", 502))?
            .to_owned();
        let revision = document
            .get("revision")
            .and_then(Value::as_str)
            .filter(|hash| {
                hash.len() == 64
                    && hash
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            })
            .ok_or_else(|| error("The document server returned an invalid revision", 502))?
            .to_owned();
        let handle = handle.unwrap_or_else(|| format!("fb-{}", crate::server::token()));
        let model_id = response
            .get("file")
            .and_then(|file| file.get("id"))
            .and_then(Value::as_str)
            .filter(|id| !id.is_empty())
            .ok_or_else(|| error("The document server returned no workspace identity", 502))?
            .to_owned();
        let binding = binding_metadata(&response, &handle, &path);
        let object = response
            .as_object_mut()
            .ok_or_else(|| error("Invalid document response", 502))?;
        object.insert("handle".into(), json!(handle));
        object.insert("path".into(), json!(path.to_string_lossy()));
        object.insert("status".into(), json!(status));
        self.grant_order.retain(|existing| existing != &handle);
        self.grant_order.push_back(handle.clone());
        self.grants.insert(
            handle,
            Grant {
                path,
                document_id,
                revision,
                model_id,
                client,
                binding,
            },
        );
        Ok(response)
    }
}

/// Called only by Rust's setup/OS event handlers. The queue is intentionally independent of
/// page load and server readiness; the viewer drains it after its document handler is ready.
pub fn queue_open_paths(app: &AppHandle, paths: impl IntoIterator<Item = PathBuf>) {
    let state = app.state::<NativeDocuments>();
    let mut selections = state.0.lock().unwrap();
    for path in paths {
        selections.queue(path);
    }
    drop(selections);
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.eval("window.dispatchEvent(new Event('fairbeam:native-document-open'))");
    }
}

/// Windows/Linux file associations pass file arguments when launching the application.
pub fn queue_startup_arguments(app: &AppHandle) {
    #[cfg(not(target_os = "macos"))]
    {
        let directory = std::env::current_dir().ok();
        queue_open_paths(
            app,
            argument_paths(std::env::args_os().map(PathBuf::from), directory.as_deref()),
        );
    }
    #[cfg(target_os = "macos")]
    let _ = app;
}

/// The official single-instance plugin provides argv (including the executable) and the
/// second process's working directory. These are native launch inputs, never webview paths.
pub fn queue_forwarded_arguments(app: &AppHandle, arguments: Vec<String>, directory: &str) {
    queue_open_paths(
        app,
        argument_paths(
            arguments.into_iter().map(PathBuf::from),
            Some(Path::new(directory)),
        ),
    );
}

fn argument_paths(
    arguments: impl IntoIterator<Item = PathBuf>,
    directory: Option<&Path>,
) -> Vec<PathBuf> {
    arguments
        .into_iter()
        .skip(1)
        .filter_map(|path| {
            if !path
                .extension()
                .and_then(|extension| extension.to_str())
                .is_some_and(|extension| extension.eq_ignore_ascii_case("fb"))
            {
                return None;
            }
            if path.is_absolute() {
                Some(path)
            } else {
                directory
                    .filter(|directory| directory.is_absolute())
                    .map(|directory| directory.join(path))
            }
        })
        .take(MAX_PENDING_OPENS)
        .collect()
}

fn client(app: &AppHandle) -> Result<DocumentClient, Value> {
    app.state::<Shell>()
        .server
        .lock()
        .unwrap()
        .as_ref()
        .map(|server| server.document_client())
        .ok_or_else(|| error("The document server is not ready", 503))
}

async fn open_selected(
    app: AppHandle,
    path: PathBuf,
    client: DocumentClient,
    pending: Option<String>,
) -> Result<Value, Value> {
    let selected = selected_path(&path, false)?;
    let request_path = selected.clone();
    let request_client = client.clone();
    let response = tauri::async_runtime::spawn_blocking(move || {
        request_client.request(DocumentOperation::Open, &json!({"path": request_path}))
    })
    .await
    .map_err(|_| error("The document request could not finish", 503))??;
    let state = app.state::<NativeDocuments>();
    let mut selections = state.0.lock().unwrap();
    let result = selections.remember(selected, response, pending.clone(), "opened", client)?;
    // Cancellation and failures leave the OS request queued. Only a successful open consumes it.
    if let Some(handle) = pending {
        selections.pending.retain(|entry| entry.handle != handle);
    }
    Ok(result)
}

#[tauri::command]
pub async fn native_document_open(app: AppHandle, handle: Option<String>) -> Result<Value, Value> {
    let client = client(&app)?;
    if let Some(handle) = handle {
        let path = app
            .state::<NativeDocuments>()
            .0
            .lock()
            .unwrap()
            .pending_path(&handle)?;
        return open_selected(app, path, client, Some(handle)).await;
    }
    let dialog_app = app.clone();
    let path = tauri::async_runtime::spawn_blocking(move || {
        let parent = dialog_app
            .get_webview_window("main")
            .ok_or_else(|| error("The document window is unavailable", 503))?;
        dialog_app
            .dialog()
            .file()
            .set_parent(&parent)
            .add_filter("Fairbeam", &["fb", "json"])
            .blocking_pick_file()
            .map(|path| {
                path.into_path()
                    .map_err(|_| error("Invalid selected document path", 400))
            })
            .transpose()
    })
    .await
    .map_err(|_| error("The file dialog could not finish", 503))??;
    match path {
        Some(path)
            if path
                .extension()
                .is_some_and(|extension| extension.eq_ignore_ascii_case("json")) =>
        {
            tauri::async_runtime::spawn_blocking(move || read_json_import(&path))
                .await
                .map_err(|_| error("The JSON import could not finish", 503))?
        }
        Some(path) => open_selected(app, path, client, None).await,
        None => Ok(json!({"status": "canceled"})),
    }
}

#[tauri::command]
pub fn native_document_next_open(
    app: AppHandle,
    exclude: Option<Vec<String>>,
) -> Result<Value, Value> {
    // This is only a peek: no filesystem read or backend mutation precedes the viewer's
    // unsaved-work confirmation. The same handle remains pending when the viewer cancels.
    let exclude = exclude.unwrap_or_default();
    if exclude.len() > MAX_PENDING_OPENS || exclude.iter().any(|handle| handle.len() > 80) {
        return Err(error("Invalid pending-document exclusions", 400));
    }
    Ok(app
        .state::<NativeDocuments>()
        .0
        .lock()
        .unwrap()
        .next_open(&exclude))
}

#[tauri::command]
pub fn native_document_restore(app: AppHandle, model_id: String) -> Value {
    // Keep the server snapshot stable through this lookup. No filesystem operation, backend
    // request, pending-open consumption, or model hydration is part of restoring a binding.
    let shell = app.state::<Shell>();
    let server = shell.server.lock().unwrap();
    let Some(server) = server.as_ref() else {
        return Value::Null;
    };
    app.state::<NativeDocuments>()
        .0
        .lock()
        .unwrap()
        .restore(&model_id, &server.document_client())
}

#[tauri::command]
pub async fn native_document_save(app: AppHandle, request: SaveRequest) -> Result<Value, Value> {
    let client = client(&app)?;
    let grant = app
        .state::<NativeDocuments>()
        .0
        .lock()
        .unwrap()
        .begin_save(request.handle.as_deref(), &request.model_id, &client)?;
    let _saving = Saving {
        app: app.clone(),
        model_id: request.model_id.clone(),
    };
    let save_as = request.save_as || grant.is_none();
    let path = if save_as {
        let name = request
            .suggested_name
            .as_deref()
            .filter(|name| validate_name(name).is_ok())
            .unwrap_or("Untitled.fb")
            .to_owned();
        let dialog_app = app.clone();
        let path = tauri::async_runtime::spawn_blocking(move || {
            let parent = dialog_app
                .get_webview_window("main")
                .ok_or_else(|| error("The document window is unavailable", 503))?;
            dialog_app
                .dialog()
                .file()
                .set_parent(&parent)
                .set_file_name(name)
                .add_filter("Fairbeam", &["fb"])
                .blocking_save_file()
                .map(|path| {
                    path.into_path()
                        .map_err(|_| error("Invalid selected document path", 400))
                })
                .transpose()
        })
        .await
        .map_err(|_| error("The file dialog could not finish", 503))??;
        let Some(mut path) = path else {
            return Ok(json!({"status": "canceled"}));
        };
        if path.extension().is_none() {
            path.set_extension("fb");
        }
        selected_path(&path, true)?
    } else {
        selected_path(&grant.as_ref().unwrap().path, true)?
    };
    let mut body = json!({"path": path, "model_id": request.model_id,
        "workspace_hash": request.workspace_hash, "save_as": save_as});
    if let Some(grant) = &grant {
        body["document_id"] = json!(grant.document_id);
        if !save_as {
            body["base_hash"] = json!(grant.revision);
        }
    }
    let request_client = client.clone();
    let response = tauri::async_runtime::spawn_blocking(move || {
        request_client.request(DocumentOperation::Save, &body)
    })
    .await
    .map_err(|_| error("The document request could not finish", 503))??;
    // The named lock guard must end before `_saving`: its Drop locks this same state to
    // clear the in-flight marker. A tail-expression temporary survives that local in Rust 2021.
    let result = {
        let state = app.state::<NativeDocuments>();
        let mut selections = state.0.lock().unwrap();
        selections.remember(
            path,
            response,
            if save_as { None } else { request.handle },
            "saved",
            client,
        )
    };
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    fn document_response(model_id: &str, revision: char) -> Value {
        json!({
            "file": {"id": model_id, "backup_scope": "workspace-scope"},
            "document": {"id": "document-id", "revision": revision.to_string().repeat(64),
                "results_directory": "example.results"},
            "results": [], "warnings": []
        })
    }

    #[test]
    fn restore_uses_latest_successful_binding_for_the_exact_server_session_and_model() {
        let mut selections = Selections::default();
        let client = crate::server::test_document_client(12345);
        for (handle, path) in [("old-handle", "old.fb"), ("new-handle", "new.fb")] {
            selections
                .remember(
                    PathBuf::from(path),
                    document_response("example", 'a'),
                    Some(handle.into()),
                    "opened",
                    client.clone(),
                )
                .unwrap();
        }
        assert_eq!(
            selections.restore("example", &client)["handle"],
            "new-handle"
        );
        assert_eq!(selections.restore("unknown-model", &client), Value::Null);
        // A new child token invalidates a binding even when the OS reuses its port.
        let restarted_client = crate::server::test_document_client(12345);
        assert_eq!(
            selections.restore("example", &restarted_client),
            Value::Null
        );
        selections
            .remember(
                PathBuf::from("old.fb"),
                document_response("example", 'b'),
                Some("old-handle".into()),
                "saved",
                client.clone(),
            )
            .unwrap();
        let restored = selections.restore("example", &client);
        assert_eq!(restored["handle"], "old-handle");
        assert_eq!(restored["document"]["revision"], "b".repeat(64));
        assert_eq!(selections.grant_order.len(), 2);
        assert!(selections
            .remember(
                PathBuf::from("bad.fb"),
                document_response("example", 'z'),
                Some("bad-handle".into()),
                "saved",
                client.clone()
            )
            .is_err());
        selections
            .remember(
                PathBuf::from("other.fb"),
                document_response("other", 'a'),
                None,
                "opened",
                client.clone(),
            )
            .unwrap();
        assert_eq!(selections.restore("example", &client), restored);
    }

    #[test]
    fn restore_caches_only_compact_binding_and_result_reference_metadata() {
        let mut selections = Selections::default();
        let client = crate::server::test_document_client(12345);
        let mut response = document_response("example", 'a');
        let design =
            json!({"model": {"name": "NEVER-CACHE-DESIGN"}, "geometry": "x".repeat(128 * 1024)});
        response["file"]["design"] = design.clone();
        response["file"]["hash"] = json!("workspace-design-hash");
        response["document"]["extra"] = design.clone();
        response["extra"] = design.clone();
        response["results"] = json!([{"file":"result.json", "sha256":"checksum", "design_sha256":"design-checksum",
            "provenance":"source-design", "status":"current", "project_file":"cached.json", "payload":design.clone()}]);
        response["warnings"] = json!(["One warning", design]);
        selections
            .remember(
                PathBuf::from("selected.fb"),
                response,
                Some("handle".into()),
                "saved",
                client.clone(),
            )
            .unwrap();
        let binding = selections.restore("example", &client);
        assert_eq!(
            binding["file"],
            json!({"id":"example", "backup_scope":"workspace-scope"})
        );
        assert_eq!(binding["document"]["path"], "selected.fb");
        assert_eq!(
            binding["results"],
            json!([{"file":"result.json", "sha256":"checksum", "design_sha256":"design-checksum",
            "provenance":"source-design", "status":"current", "project_file":"cached.json"}])
        );
        assert_eq!(binding["warnings"], json!(["One warning"]));
        let serialized = serde_json::to_string(&binding).unwrap();
        assert!(serialized.len() < 1024);
        assert!(!serialized.contains("NEVER-CACHE-DESIGN"));
        assert!(binding.get("status").is_none());
        assert!(binding.get("extra").is_none());
    }

    #[test]
    fn restore_never_opens_a_path_or_consumes_pending_requests() {
        let mut selections = Selections::default();
        let client = crate::server::test_document_client(12345);
        let absent = std::env::temp_dir()
            .join(format!("fairbeam-absent-{}", crate::server::token()))
            .join("missing.fb");
        assert!(!absent.exists());
        selections.queue(absent.clone());
        let pending = selections.next_open(&[]);
        selections
            .remember(
                absent.clone(),
                document_response("example", 'a'),
                Some("authorized-handle".into()),
                "opened",
                client.clone(),
            )
            .unwrap();
        assert_eq!(
            selections.restore("example", &client)["handle"],
            "authorized-handle"
        );
        assert_eq!(
            selections.restore(absent.to_str().unwrap(), &client),
            Value::Null
        );
        assert_eq!(selections.next_open(&[]), pending);
        assert!(selections.saving.is_empty());
        assert!(!absent.exists());
    }

    #[test]
    fn forwarded_arguments_use_the_senders_directory_and_only_fb_files() {
        let directory = std::env::temp_dir().join("fairbeam-forwarded-launch");
        let absolute = directory.join("absolute.fb");
        let arguments = vec![
            PathBuf::from("launcher.fb"),
            PathBuf::from("relative model.FB"),
            absolute.clone(),
            PathBuf::from("legacy.json"),
            PathBuf::from("--ignored"),
        ];
        assert_eq!(
            argument_paths(arguments.clone(), Some(&directory)),
            vec![directory.join("relative model.FB"), absolute.clone()]
        );
        assert_eq!(
            argument_paths(arguments.clone(), None),
            vec![absolute.clone()]
        );
        assert_eq!(
            argument_paths(arguments, Some(Path::new("relative-cwd"))),
            vec![absolute]
        );
        let many = std::iter::once(PathBuf::from("launcher.exe"))
            .chain((0..MAX_PENDING_OPENS + 5).map(|index| directory.join(format!("{index}.fb"))));
        assert_eq!(
            argument_paths(many, Some(&directory)).len(),
            MAX_PENDING_OPENS
        );
    }

    #[test]
    fn native_json_import_reads_only_selected_regular_bounded_utf8_files() {
        let temporary = std::env::temp_dir();
        let directory = temporary.join(format!(
            "fairbeam-native-import-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        fs::create_dir(&directory).unwrap();
        let path = directory.join("legacy.JSON");
        fs::write(&path, b"{\"schema\":\"fairbeam.design/1\"}").unwrap();
        let imported = read_json_import(&path).unwrap();
        assert_eq!(imported["status"], "import");
        assert_eq!(imported["name"], "legacy.JSON");
        assert_eq!(imported["content"], "{\"schema\":\"fairbeam.design/1\"}");
        fs::write(&path, [0xff]).unwrap();
        assert!(read_json_import(&path).is_err());
        let file = fs::File::create(&path).unwrap();
        file.set_len(MAX_JSON_IMPORT + 1).unwrap();
        drop(file);
        assert!(read_json_import(&path).is_err());
        assert!(read_json_import(&directory).is_err());
        let document = directory.join("selected.fb");
        assert!(selected_path(&document, true).is_ok());
        assert!(selected_path(&document, false).is_err());
        fs::write(&document, b"{}").unwrap();
        assert!(selected_path(&document, false).is_ok());
        assert!(read_json_import(&document).is_err());
        assert!(directory.starts_with(&temporary));
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn webview_save_requests_cannot_supply_paths_or_private_revisions() {
        let valid =
            json!({"modelId":"example", "workspaceHash":"123", "suggestedName":"Example.fb"});
        assert!(serde_json::from_value::<SaveRequest>(valid.clone()).is_ok());
        for name in ["path", "baseHash", "documentId", "results", "token"] {
            let mut request = valid.clone();
            request[name] = json!("injected");
            assert!(serde_json::from_value::<SaveRequest>(request).is_err());
        }
    }

    #[test]
    fn selections_bind_handles_and_revisions_to_native_paths() {
        let mut selections = Selections::default();
        let response = json!({"document":{"id":"document-id", "revision":"a".repeat(64)}, "file":{"id":"example"}});
        let client = crate::server::test_document_client(12345);
        let result = selections
            .remember(
                PathBuf::from("native-selected.fb"),
                response,
                None,
                "opened",
                client.clone(),
            )
            .unwrap();
        let handle = result["handle"].as_str().unwrap();
        let grant = selections.grant(handle).unwrap();
        assert_eq!(grant.path, PathBuf::from("native-selected.fb"));
        assert_eq!(grant.document_id, "document-id");
        assert_eq!(grant.revision, "a".repeat(64));
        assert_eq!(grant.model_id, "example");
        assert!(selections.grant("native-selected.fb").is_err());
        assert!(selections.grant("unknown-handle").is_err());
        assert!(selections
            .remember(
                PathBuf::from("bad.fb"),
                json!({"document":{"id":"x","revision":"bad"}}),
                None,
                "saved",
                client.clone(),
            )
            .is_err());
        assert!(selections
            .begin_save(Some(handle), "another_model", &client)
            .is_err());
        assert!(selections
            .begin_save(
                Some(handle),
                "example",
                &crate::server::test_document_client(12346)
            )
            .is_err());
        assert!(selections
            .begin_save(Some(handle), "example", &client)
            .is_ok());
        assert!(selections
            .begin_save(Some(handle), "example", &client)
            .is_err());
        selections.saving.remove("example");
        assert!(selections
            .begin_save(Some(handle), "example", &client)
            .is_ok());
    }

    #[test]
    fn os_opens_queue_before_viewer_ready_without_duplicates() {
        let mut selections = Selections::default();
        selections.queue(PathBuf::from("first.fb"));
        selections.queue(PathBuf::from("first.fb"));
        selections.queue(PathBuf::from("ignored.py"));
        selections.queue(PathBuf::from("second.FB"));
        let first = selections.next_open(&[]);
        assert_eq!(first["displayName"], "first.fb");
        assert_eq!(selections.next_open(&[]), first); // peeking/canceling never consumes a request
        let exclude = [first["handle"].as_str().unwrap().to_owned()];
        assert_eq!(selections.next_open(&exclude)["displayName"], "second.FB");
        assert_eq!(selections.pending.len(), 2); // excluded requests remain available for retry
        assert_eq!(
            selections
                .pending_path(first["handle"].as_str().unwrap())
                .unwrap(),
            PathBuf::from("first.fb")
        );
        assert!(selections.pending_path("first.fb").is_err());
        assert_eq!(
            selections.pending.pop_front().unwrap().path,
            PathBuf::from("first.fb")
        );
        assert_eq!(
            selections.pending.pop_front().unwrap().path,
            PathBuf::from("second.FB")
        );
        assert!(selections.pending.is_empty());
        for i in 0..MAX_PENDING_OPENS + 5 {
            selections.queue(PathBuf::from(format!("{i}.fb")));
        }
        assert_eq!(selections.pending.len(), MAX_PENDING_OPENS);
    }

    #[test]
    fn filenames_reject_paths_streams_device_names_and_wrong_extensions() {
        for name in ["Patch.fb", "Türkçe model.FB"] {
            assert!(validate_name(name).is_ok());
        }
        for name in [
            "../Patch.fb",
            "C:\\Patch.fb",
            "Patch.fb:stream",
            "NUL.fb",
            "COM0.fb",
            "LPT¹.fb",
            "Patch.json",
            "Patch.fb ",
            ".fb",
            "a\n.fb",
        ] {
            assert!(validate_name(name).is_err(), "{name}");
        }
        assert!(selected_path(Path::new("relative.fb"), true).is_err());
    }
}
