// The `fairbeam serve` child: started in its own process group (Windows: also in a job object,
// so it dies with the app), stopped gracefully through POST /api/shutdown with a per-start token,
// then by signal / job termination after a grace period.

use std::collections::hash_map::RandomState;
use std::hash::{BuildHasher, Hasher};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpListener, TcpStream};
use std::process::{Child, Command};
use std::thread;
use std::time::{Duration, Instant};

pub struct Server {
    pub child: Child,
    pub port: u16,
    token: String,
    #[cfg(windows)]
    job: Option<win::Job>,
}

/// The desktop document channel stays in Rust. In particular, its token is never returned to
/// the webview, and the only destinations are the two fixed document routes on our child.
#[derive(Clone, PartialEq, Eq)]
pub struct DocumentClient {
    port: u16,
    token: String,
}

#[cfg(test)]
pub(crate) fn test_document_client(port: u16) -> DocumentClient {
    DocumentClient { port, token: token() }
}

#[derive(Clone, Copy)]
pub enum DocumentOperation {
    Open,
    Save,
    Archive,
}

const DOCUMENT_RESPONSE_LIMIT: usize = 64 * 1024 * 1024;
const DOCUMENT_REQUEST_LIMIT: usize = 16 * 1024;
const DOCUMENT_SAVE_REQUEST_LIMIT: usize = 32 * 1024 * 1024;
const DOCUMENT_REQUEST_TIMEOUT: Duration = Duration::from_secs(120);

impl DocumentClient {
    pub fn request(
        &self,
        operation: DocumentOperation,
        body: &serde_json::Value,
    ) -> Result<serde_json::Value, serde_json::Value> {
        let error = |message: &str| serde_json::json!({"error": message, "status": 503});
        let body = serde_json::to_vec(body).map_err(|_| error("Invalid document request"))?;
        let limit = match operation {
            DocumentOperation::Open | DocumentOperation::Archive => DOCUMENT_REQUEST_LIMIT,
            DocumentOperation::Save => DOCUMENT_SAVE_REQUEST_LIMIT,
        };
        if body.len() > limit {
            return Err(error("Document request exceeds its size limit"));
        }
        let path = match operation {
            DocumentOperation::Open => "/api/native-document/open",
            DocumentOperation::Save => "/api/native-document/save",
            DocumentOperation::Archive => "/api/native-document/archive",
        };
        let address = SocketAddr::from(([127, 0, 0, 1], self.port));
        let mut stream = TcpStream::connect_timeout(&address, Duration::from_secs(2))
            .map_err(|_| error("The document server is unavailable"))?;
        stream.set_write_timeout(Some(Duration::from_secs(10)))
            .map_err(|_| error("Could not configure the document connection"))?;
        let head = format!(
            "POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\nX-Fairbeam-Token: {token}\r\nContent-Length: {length}\r\nConnection: close\r\n\r\n",
            port = self.port, token = self.token, length = body.len()
        );
        stream.write_all(head.as_bytes()).and_then(|_| stream.write_all(&body))
            .map_err(|_| error("Could not send the document request"))?;
        let deadline = Instant::now() + DOCUMENT_REQUEST_TIMEOUT;
        let mut answer = Vec::new();
        let mut buffer = [0u8; 16 * 1024];
        loop {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err(error("The document request timed out; reopen the file before retrying"));
            }
            stream.set_read_timeout(Some(remaining))
                .map_err(|_| error("Could not configure the document connection"))?;
            match stream.read(&mut buffer) {
                Ok(0) => break,
                Ok(count) => {
                    if answer.len() + count > DOCUMENT_RESPONSE_LIMIT {
                        return Err(error("Document response exceeds its size limit"));
                    }
                    answer.extend_from_slice(&buffer[..count]);
                }
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(_) => return Err(error("The document response was interrupted; reopen the file before retrying")),
            }
        }
        parse_document_response(&answer)
    }
}

fn parse_document_response(answer: &[u8]) -> Result<serde_json::Value, serde_json::Value> {
    let invalid = || serde_json::json!({"error": "Invalid document server response", "status": 502});
    let boundary = answer.windows(4).position(|part| part == b"\r\n\r\n").ok_or_else(invalid)?;
    if boundary > 16 * 1024 {
        return Err(invalid());
    }
    let head = std::str::from_utf8(&answer[..boundary]).map_err(|_| invalid())?;
    let mut lines = head.split("\r\n");
    let mut status_line = lines.next().ok_or_else(invalid)?.split_whitespace();
    if !matches!(status_line.next(), Some("HTTP/1.0" | "HTTP/1.1")) {
        return Err(invalid());
    }
    let status = status_line.next().and_then(|s| s.parse::<u16>().ok()).ok_or_else(invalid)?;
    let mut length = None;
    let mut json = false;
    for line in lines {
        let (name, value) = line.split_once(':').ok_or_else(invalid)?;
        if name.eq_ignore_ascii_case("content-length") {
            if length.is_some() { return Err(invalid()); }
            length = Some(value.trim().parse::<usize>().map_err(|_| invalid())?);
        } else if name.eq_ignore_ascii_case("transfer-encoding") {
            return Err(invalid());
        } else if name.eq_ignore_ascii_case("content-type") {
            json = value.split(';').next().unwrap_or("").trim().eq_ignore_ascii_case("application/json");
        }
    }
    let body = &answer[boundary + 4..];
    if length != Some(body.len()) || !json {
        return Err(invalid());
    }
    let mut value: serde_json::Value = serde_json::from_slice(body).map_err(|_| invalid())?;
    let object = value.as_object_mut().ok_or_else(invalid)?;
    if (200..300).contains(&status) {
        Ok(value)
    } else {
        object.insert("status".into(), serde_json::json!(status));
        Err(value)
    }
}

pub fn free_port() -> Option<u16> {
    TcpListener::bind("127.0.0.1:0").ok()?.local_addr().ok().map(|a| a.port())
}

/// `preferred` while it can be bound on 127.0.0.1, else a free port: the viewer's origin, and with
/// it its localStorage, then stays the same from one start to the next.
pub fn stable_port(preferred: Option<u16>) -> Option<u16> {
    preferred
        .filter(|&p| p >= 1024 && TcpListener::bind(("127.0.0.1", p)).is_ok())
        .or_else(free_port)
}

/// A random token (std only: RandomState is seeded from the OS).
pub fn token() -> String {
    let mut s = String::new();
    for _ in 0..2 {
        let mut h = RandomState::new().build_hasher();
        h.write_u128(Instant::now().elapsed().as_nanos() ^ u128::from(std::process::id()));
        s.push_str(&format!("{:016x}", h.finish()));
    }
    s
}

const READ_LIMIT: Duration = Duration::from_secs(3);
const READ_CAP: usize = 64 * 1024;

/// Send a `Connection: close` request and return the answer's status code. The whole answer is
/// read (to EOF, at most READ_CAP bytes within READ_LIMIT) before the socket closes: closing it
/// with unread data makes the OS reset the connection, which the server sees as an error.
fn request(port: u16, raw: &str) -> Option<u16> {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let mut s = TcpStream::connect_timeout(&addr, Duration::from_millis(500)).ok()?;
    let _ = s.set_read_timeout(Some(READ_LIMIT));
    s.write_all(raw.as_bytes()).ok()?;
    let deadline = Instant::now() + READ_LIMIT;
    let mut answer = Vec::new();
    let mut buf = [0u8; 4096];
    while answer.len() < READ_CAP {
        let left = deadline.saturating_duration_since(Instant::now());
        if left.is_zero() || s.set_read_timeout(Some(left)).is_err() {
            break;
        }
        match s.read(&mut buf) {
            Ok(0) => break,
            Ok(n) => answer.extend_from_slice(&buf[..n]),
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(_) => break,
        }
    }
    // "HTTP/1.1 202 Accepted"
    if answer.len() < 12 || !answer.starts_with(b"HTTP/") {
        return None;
    }
    std::str::from_utf8(&answer[9..12]).ok()?.parse().ok()
}

/// GET a path on 127.0.0.1 and return true for a 2xx/3xx answer.
pub fn http_ok(port: u16, path: &str) -> bool {
    let req = format!("GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n");
    matches!(request(port, &req), Some(c) if (200..400).contains(&c))
}

impl Server {
    pub fn document_client(&self) -> DocumentClient {
        DocumentClient { port: self.port, token: self.token.clone() }
    }

    /// Spawn `cmd` (already configured with arguments and output) as the server.
    pub fn spawn(mut cmd: Command, port: u16) -> std::io::Result<Server> {
        let token = token();
        cmd.env("FAIRBEAM_SHUTDOWN_TOKEN", &token);
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            cmd.process_group(0);
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            // CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | CREATE_SUSPENDED: the process is put
            // into the job before it runs, so everything it starts is in the job too (the venv
            // python.exe is a launcher that starts the real interpreter right away)
            cmd.creation_flags(0x0000_0200 | 0x0800_0000 | 0x0000_0004);
        }
        let child = cmd.spawn()?;
        #[cfg(windows)]
        let (child, job) = {
            let mut child = child;
            let job = win::Job::new().filter(|j| j.assign(&child));
            if !win::resume(&child) {
                // never leave a suspended process behind
                let _ = child.kill();
                let _ = child.wait();
                return Err(std::io::Error::new(std::io::ErrorKind::Other, "could not resume the server process"));
            }
            (child, job)
        };
        Ok(Server {
            child,
            port,
            token,
            #[cfg(windows)]
            job,
        })
    }

    pub fn exited(&mut self) -> bool {
        matches!(self.child.try_wait(), Ok(Some(_)) | Err(_))
    }

    fn wait_exit(&mut self, limit: Duration) -> bool {
        let start = Instant::now();
        while start.elapsed() < limit {
            if self.exited() {
                return true;
            }
            thread::sleep(Duration::from_millis(100));
        }
        self.exited()
    }

    /// Graceful stop (the server cancels its runs and shuts down), forced after the grace period.
    pub fn stop(mut self) {
        let req = format!(
            "POST /api/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{p}\r\nContent-Type: application/json\r\nX-Fairbeam-Token: {t}\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{{}}",
            p = self.port,
            t = self.token
        );
        let asked = request(self.port, &req) == Some(202);
        if asked && self.wait_exit(Duration::from_secs(8)) {
            self.cleanup();
            return;
        }
        #[cfg(unix)]
        {
            let pgid = self.child.id() as i32;
            unsafe {
                libc::kill(-pgid, libc::SIGTERM);
            }
            self.wait_exit(Duration::from_secs(5));
            unsafe {
                libc::kill(-pgid, libc::SIGKILL); // stragglers of the group, or a hung server
            }
        }
        #[cfg(windows)]
        {
            match &self.job {
                Some(j) => j.terminate(),
                None => {
                    let _ = self.child.kill();
                }
            }
        }
        let _ = self.child.wait();
        self.cleanup();
    }

    fn cleanup(&mut self) {
        #[cfg(windows)]
        {
            self.job = None; // closing the job handle kills anything left in it
        }
    }
}

#[cfg(windows)]
mod win {
    use std::os::windows::io::AsRawHandle;
    use std::process::Child;

    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    #[link(name = "ntdll")]
    extern "system" {
        // undocumented but stable since Windows XP; fairbeam/procutil.py uses it the same way.
        // std::process::Child has no handle to the main thread for ResumeThread.
        fn NtResumeProcess(process: HANDLE) -> i32;
    }

    /// Resume a process started with CREATE_SUSPENDED.
    pub fn resume(child: &Child) -> bool {
        unsafe { NtResumeProcess(child.as_raw_handle() as HANDLE) >= 0 }
    }

    /// A job object whose processes die when it is terminated or its last handle closes (also
    /// when the app itself dies).
    pub struct Job(HANDLE);

    unsafe impl Send for Job {}

    impl Job {
        pub fn new() -> Option<Job> {
            unsafe {
                let h = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if h.is_null() {
                    return None;
                }
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let ok = SetInformationJobObject(
                    h,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const core::ffi::c_void,
                    std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                );
                if ok == 0 {
                    CloseHandle(h);
                    return None;
                }
                Some(Job(h))
            }
        }

        pub fn assign(&self, child: &Child) -> bool {
            unsafe { AssignProcessToJobObject(self.0, child.as_raw_handle() as HANDLE) != 0 }
        }

        pub fn terminate(&self) {
            unsafe {
                TerminateJobObject(self.0, 1);
            }
        }
    }

    impl Drop for Job {
        fn drop(&mut self) {
            unsafe {
                CloseHandle(self.0);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::Shutdown;
    use std::thread::JoinHandle;

    fn document_answer(status: u16, body: &str) -> Vec<u8> {
        format!("HTTP/1.0 {status} Answer\r\nContent-Type: application/json; charset=utf-8\r\nContent-Length: {}\r\n\r\n{body}", body.len()).into_bytes()
    }

    #[test]
    fn document_responses_preserve_conflicts_and_require_complete_json_objects() {
        let value = parse_document_response(&document_answer(200, r#"{"file":{"id":"example"}}"#)).unwrap();
        assert_eq!(value["file"]["id"], "example");
        let conflict = parse_document_response(&document_answer(409, r#"{"error":"changed on disk","status":200}"#)).unwrap_err();
        assert_eq!(conflict["status"], 409);
        assert_eq!(conflict["error"], "changed on disk");
        for body in ["[]", "null", "{", "{\"n\":NaN}"] {
            assert!(parse_document_response(&document_answer(200, body)).is_err());
        }
        let mut truncated = document_answer(200, r#"{"ok":true}"#);
        truncated.pop();
        assert!(parse_document_response(&truncated).is_err());
        assert!(parse_document_response(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}").is_err());
        assert!(parse_document_response(b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: 2\r\n\r\n{}").is_err());
        assert!(parse_document_response(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n{}").is_err());
    }

    #[test]
    fn document_channel_uses_only_its_child_and_private_token() {
        for (operation, path, body) in [
            (DocumentOperation::Open, "/api/native-document/open", serde_json::json!({"path":"example.fb"})),
            (DocumentOperation::Archive, "/api/native-document/archive", serde_json::json!({"model_id":"fb_project"})),
            (DocumentOperation::Save, "/api/native-document/save", serde_json::json!({"design":{"name":"x".repeat(20000)}})),
        ] {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let expected_body = serde_json::to_string(&body).unwrap();
        let task = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            stream.set_read_timeout(Some(Duration::from_secs(3))).unwrap();
            let mut request = Vec::new();
            let mut buffer = [0u8; 1024];
            loop {
                let count = stream.read(&mut buffer).unwrap();
                assert_ne!(count, 0);
                request.extend_from_slice(&buffer[..count]);
                if let Some(boundary) = request.windows(4).position(|p| p == b"\r\n\r\n") {
                    let head = std::str::from_utf8(&request[..boundary]).unwrap();
                    let length = head.split("\r\n").find_map(|line| line.strip_prefix("Content-Length: ")).unwrap().parse::<usize>().unwrap();
                    if request.len() >= boundary + 4 + length { break; }
                }
            }
            let request = String::from_utf8(request).unwrap();
            assert!(request.starts_with(&format!("POST {path} HTTP/1.1\r\n")));
            assert!(request.contains("\r\nX-Fairbeam-Token: test-private-token\r\n"));
            assert!(request.ends_with(&expected_body));
            stream.write_all(&document_answer(200, r#"{"file":{"id":"example"}}"#)).unwrap();
        });
        let client = DocumentClient { port, token: "test-private-token".into() };
        let answer = client.request(operation, &body).unwrap();
        assert_eq!(answer["file"]["id"], "example");
        task.join().unwrap();
        }
    }

    #[test]
    fn document_request_limits_are_operation_specific() {
        let client = DocumentClient { port: 0, token: "test".into() };
        let open = client.request(DocumentOperation::Open, &serde_json::json!({"path":"x".repeat(DOCUMENT_REQUEST_LIMIT)}));
        assert_eq!(open.unwrap_err()["error"], "Document request exceeds its size limit");
        let save = client.request(DocumentOperation::Save, &serde_json::json!({"design":"x".repeat(DOCUMENT_SAVE_REQUEST_LIMIT)}));
        assert_eq!(save.unwrap_err()["error"], "Document request exceeds its size limit");
    }

    /// A one-shot HTTP server on 127.0.0.1: reads the request head, sends `parts` with a pause
    /// between them, closes its side and returns what its next read gives: Ok(0) when the client
    /// closed in order, an error when the client reset the connection.
    fn serve(parts: Vec<Vec<u8>>) -> (u16, JoinHandle<std::io::Result<usize>>) {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let handle = thread::spawn(move || {
            let (mut s, _) = listener.accept()?;
            let (mut head, mut buf) = (Vec::new(), [0u8; 1024]);
            while !head.windows(4).any(|w| w == b"\r\n\r\n") {
                match s.read(&mut buf)? {
                    0 => break,
                    n => head.extend_from_slice(&buf[..n]),
                }
            }
            for part in parts {
                s.write_all(&part)?;
                thread::sleep(Duration::from_millis(50));
            }
            s.shutdown(Shutdown::Write)?;
            s.set_read_timeout(Some(Duration::from_secs(5)))?;
            s.read(&mut buf)
        });
        (port, handle)
    }

    fn get(port: u16) -> String {
        format!("GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n")
    }

    #[test]
    fn stable_port_keeps_a_free_port_and_moves_off_a_taken_one() {
        let free = free_port().unwrap();
        assert_eq!(stable_port(Some(free)), Some(free));
        let taken = TcpListener::bind("127.0.0.1:0").unwrap();
        let p = taken.local_addr().unwrap().port();
        let other = stable_port(Some(p)).unwrap();
        assert_ne!(other, p);
        assert!(stable_port(None).is_some());
        assert_ne!(stable_port(Some(80)), Some(80));
    }

    #[test]
    fn reads_the_answer_to_the_end() {
        let (port, server) = serve(vec![
            b"HTTP/1.1 202 Accepted\r\n".to_vec(),
            b"Content-Type: application/json\r\nContent-Length: 13\r\nConnection: close\r\n\r\n".to_vec(),
            b"{\"ok\": true}\n".to_vec(),
        ]);
        assert_eq!(request(port, &get(port)), Some(202));
        // the client read everything and closed in order: no reset reaches the server
        assert_eq!(server.join().unwrap().unwrap(), 0);
    }

    #[test]
    fn health_poll() {
        let (port, server) = serve(vec![b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nok".to_vec()]);
        let start = Instant::now();
        assert!(http_ok(port, "/api/health"));
        assert!(start.elapsed() < Duration::from_secs(1));
        assert_eq!(server.join().unwrap().unwrap(), 0);
    }

    #[test]
    fn stops_at_the_size_cap() {
        let mut body = b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n".to_vec();
        body.resize(4 * READ_CAP, b'x');
        let (port, server) = serve(vec![body]);
        let start = Instant::now();
        assert_eq!(request(port, &get(port)), Some(200));
        assert!(start.elapsed() < READ_LIMIT);
        let _ = server.join(); // the server's write fails once the client closes: expected
    }

    #[test]
    fn not_http() {
        let (port, server) = serve(vec![b"hello there\r\n".to_vec()]);
        assert_eq!(request(port, &get(port)), None);
        let _ = server.join();
    }
}
