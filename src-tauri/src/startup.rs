//! Ownership of one startup attempt and its child. Slow work and process shutdown stay outside
//! the state mutex. The separate handoff lock is only for taking/stopping or spawning/publishing
//! a child; callers must not hold it while choosing/downloading a runtime, seeding or showing UI.

use std::path::PathBuf;
use std::sync::{Mutex, MutexGuard};

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Ready {
    pub workspace: PathBuf,
    pub gpu_runtime: bool,
}

struct Owned<T> {
    generation: u64,
    server: T,
    ready: Option<Ready>,
}

struct State<T> {
    generation: u64,
    stopping: bool,
    auto_install_started: bool,
    server: Option<Owned<T>>,
}

pub struct Startup<T> {
    state: Mutex<State<T>>,
    handoff: Mutex<()>,
}

impl<T> Default for Startup<T> {
    fn default() -> Self {
        Self {
            state: Mutex::new(State { generation: 0, stopping: false, auto_install_started: false, server: None }),
            handoff: Mutex::new(()),
        }
    }
}

impl<T> Startup<T> {
    /// Reserve an attempt. A finishing installer may only restart the attempt it belongs to.
    /// In the shell, generation changes and guarded UI callbacks run on the main thread.
    pub fn begin(&self, expected: Option<u64>) -> Option<u64> {
        let mut state = self.state.lock().unwrap();
        if state.stopping || expected.is_some_and(|generation| generation != state.generation) {
            return None;
        }
        state.generation += 1;
        Some(state.generation)
    }

    pub fn current(&self, generation: u64) -> bool {
        let state = self.state.lock().unwrap();
        !state.stopping && state.generation == generation
    }

    pub fn auto_install_available(&self, generation: u64) -> bool {
        let state = self.state.lock().unwrap();
        !state.stopping && state.generation == generation && !state.auto_install_started
    }

    /// Consume the unattended-install aid only when its current main-thread callback is accepted.
    pub fn begin_auto_install(&self, generation: u64) -> Option<u64> {
        let mut state = self.state.lock().unwrap();
        if state.stopping || state.generation != generation || state.auto_install_started {
            return None;
        }
        state.auto_install_started = true;
        state.generation += 1;
        Some(state.generation)
    }

    /// Workers acquire handoff before state; UI callbacks never acquire handoff.
    pub fn handoff(&self) -> MutexGuard<'_, ()> {
        self.handoff.lock().unwrap()
    }

    /// The caller owns the removed child and stops it after releasing the state mutex.
    /// A stale worker must not take a newer worker's child or clear its ready metadata.
    pub fn take_current(&self, generation: u64) -> Result<Option<T>, ()> {
        let mut state = self.state.lock().unwrap();
        if state.stopping || state.generation != generation {
            return Err(());
        }
        Ok(state.server.take().map(|owned| owned.server))
    }

    /// Interrupt startup for an updater transition. A failed update may start again after
    /// its worker finishes reaping the removed child under handoff.
    pub fn interrupt(&self) -> Result<Option<T>, ()> {
        let mut state = self.state.lock().unwrap();
        if state.stopping { return Err(()); }
        state.generation += 1;
        Ok(state.server.take().map(|owned| owned.server))
    }

    /// Terminal application shutdown invalidates unpublished starts and rejects new ones while
    /// the removed child is reaped outside the mutex. Runtime transitions use take_current.
    pub fn stop(&self) -> Option<T> {
        let mut state = self.state.lock().unwrap();
        state.stopping = true;
        state.generation += 1;
        state.server.take().map(|owned| owned.server)
    }

    /// Return rejected children to their creating worker. Never drop or stop another child here.
    pub fn publish(&self, generation: u64, server: T) -> Result<(), T> {
        let mut state = self.state.lock().unwrap();
        if state.stopping || state.generation != generation || state.server.is_some() {
            return Err(server);
        }
        state.server = Some(Owned { generation, server, ready: None });
        Ok(())
    }

    /// Only a short, nonblocking inspection (such as Child::try_wait), never an HTTP request.
    pub fn inspect_current<R>(&self, generation: u64, inspect: impl FnOnce(&mut T) -> R) -> Option<R> {
        let mut state = self.state.lock().unwrap();
        if state.stopping || state.generation != generation {
            return None;
        }
        let owned = state.server.as_mut().filter(|owned| owned.generation == generation)?;
        Some(inspect(&mut owned.server))
    }

    pub fn mark_ready(&self, generation: u64, ready: Ready) -> bool {
        let mut state = self.state.lock().unwrap();
        if state.stopping || state.generation != generation {
            return false;
        }
        let Some(owned) = state.server.as_mut().filter(|owned| owned.generation == generation) else {
            return false;
        };
        owned.ready = Some(ready);
        true
    }

    pub fn ready(&self) -> Option<Ready> {
        self.state.lock().unwrap().server.as_ref().and_then(|owned| owned.ready.clone())
    }

    /// Clone a document client or inspect a binding while the server snapshot is stable.
    pub fn with_server<R>(&self, inspect: impl FnOnce(&T) -> R) -> Option<R> {
        self.state.lock().unwrap().server.as_ref().map(|owned| inspect(&owned.server))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::{Arc, Barrier};
    use std::thread;

    fn ready(name: &str) -> Ready {
        Ready { workspace: PathBuf::from(name), gpu_runtime: true }
    }

    #[test]
    fn superseded_before_stop_cannot_take_or_clear_newer_server() {
        let startup = Startup::default();
        let old = startup.begin(None).unwrap();
        let new = startup.begin(None).unwrap();
        startup.publish(new, "new").unwrap();
        assert!(startup.mark_ready(new, ready("new-workspace")));
        assert_eq!(startup.take_current(old), Err(()));
        assert_eq!(startup.with_server(|server| *server), Some("new"));
        assert_eq!(startup.ready(), Some(ready("new-workspace")));
    }

    #[test]
    fn superseded_during_spawn_returns_only_abandoned_child() {
        let startup = Arc::new(Startup::default());
        let old = startup.begin(None).unwrap();
        let spawned = Arc::new(Barrier::new(2));
        let publish = Arc::new(Barrier::new(2));
        let worker = {
            let (startup, spawned, publish) = (startup.clone(), spawned.clone(), publish.clone());
            thread::spawn(move || {
                assert!(startup.current(old));
                let child = "old-child";
                spawned.wait();
                publish.wait();
                startup.publish(old, child).unwrap_err()
            })
        };
        spawned.wait();
        let new = startup.begin(None).unwrap();
        startup.publish(new, "new-child").unwrap();
        publish.wait();
        assert_eq!(worker.join().unwrap(), "old-child");
        assert_eq!(startup.with_server(|server| *server), Some("new-child"));
    }

    #[test]
    fn late_health_response_cannot_publish_newer_child_metadata() {
        let startup = Startup::default();
        let old = startup.begin(None).unwrap();
        startup.publish(old, "old").unwrap();
        let new = startup.begin(None).unwrap();
        assert_eq!(startup.take_current(new), Ok(Some("old")));
        startup.publish(new, "new").unwrap();
        assert!(startup.mark_ready(new, ready("new-workspace")));
        assert!(!startup.mark_ready(old, ready("old-workspace")));
        assert_eq!(startup.inspect_current(old, |_| panic!("must not inspect the newer child")), None::<()>);
        assert_eq!(startup.ready(), Some(ready("new-workspace")));
    }

    #[test]
    fn occupied_slot_and_pending_generation_retain_actual_child() {
        let startup = Startup::default();
        let old = startup.begin(None).unwrap();
        startup.publish(old, "retained").unwrap();
        assert!(startup.mark_ready(old, ready("retained-workspace")));
        assert_eq!(startup.publish(old, "duplicate"), Err("duplicate"));
        let new = startup.begin(None).unwrap();
        // Until the worker actually takes the child, document readers still see its own metadata.
        assert_eq!(startup.ready(), Some(ready("retained-workspace")));
        assert_eq!(startup.publish(new, "replacement"), Err("replacement"));
        assert_eq!(startup.take_current(new), Ok(Some("retained")));
        assert_eq!(startup.ready(), None);
    }

    #[test]
    fn global_stop_invalidates_unpublished_workers_and_late_installer_completion() {
        let startup = Startup::default();
        let generation = startup.begin(None).unwrap();
        assert_eq!(startup.stop(), None::<&str>);
        assert!(!startup.current(generation));
        assert_eq!(startup.publish(generation, "late"), Err("late"));
        assert_eq!(startup.begin(Some(generation)), None);
        assert_eq!(startup.begin(None), None);
        assert_eq!(startup.with_server(|server| *server), None);
    }

    #[test]
    fn global_stop_refuses_fresh_start_while_removed_child_is_reaped() {
        let startup = Arc::new(Startup::default());
        let generation = startup.begin(None).unwrap();
        startup.publish(generation, "child").unwrap();
        let reaping = Arc::new(Barrier::new(2));
        let finish = Arc::new(Barrier::new(2));
        let worker = {
            let (startup, reaping, finish) = (startup.clone(), reaping.clone(), finish.clone());
            thread::spawn(move || {
                let removed = startup.stop().unwrap();
                reaping.wait();
                finish.wait();
                assert_eq!(removed, "child");
            })
        };
        reaping.wait();
        assert_eq!(startup.begin(None), None);
        assert_eq!(startup.begin_auto_install(generation), None);
        assert_eq!(startup.publish(generation, "late-child"), Err("late-child"));
        finish.wait();
        worker.join().unwrap();
    }

    #[test]
    fn stale_setup_does_not_consume_automatic_install() {
        let startup = Startup::<()>::default();
        let old = startup.begin(None).unwrap();
        assert!(startup.auto_install_available(old));
        let current = startup.begin(None).unwrap();
        assert_eq!(startup.begin_auto_install(old), None);
        assert!(startup.auto_install_available(current));
        let installing = startup.begin_auto_install(current).unwrap();
        assert!(!startup.auto_install_available(installing));
        assert_eq!(startup.begin_auto_install(installing), None);
        assert!(startup.current(installing));
    }

    #[test]
    fn interrupted_update_allows_recovery_but_exit_does_not() {
        let startup = Startup::default();
        let generation = startup.begin(None).unwrap();
        startup.publish(generation, "before-update").unwrap();
        assert_eq!(startup.interrupt(), Ok(Some("before-update")));
        assert!(!startup.current(generation));
        assert_eq!(startup.publish(generation, "stale"), Err("stale"));
        let recovery = startup.begin(None).unwrap();
        startup.publish(recovery, "recovered").unwrap();
        assert_eq!(startup.stop(), Some("recovered"));
        assert_eq!(startup.interrupt(), Err(()));
        assert_eq!(startup.begin(None), None);
    }

    /// This test binary doubles as a tiny isolated child, so the ownership regression needs no
    /// Python installation or solver. It uses the production Server Windows job/stop handling.
    #[cfg(windows)]
    #[test]
    fn windows_child_fixture() {
        if std::env::var("FAIRBEAM_STARTUP_TEST_CHILD").as_deref() != Ok("1") { return; }
        use std::io::{Read, Write};
        use std::net::TcpListener;
        use std::os::windows::fs::OpenOptionsExt;
        let lock = std::env::var_os("FAIRBEAM_STARTUP_TEST_LOCK").unwrap();
        let shutdown = std::env::var_os("FAIRBEAM_STARTUP_TEST_SHUTDOWN").unwrap();
        let port: u16 = std::env::var("FAIRBEAM_STARTUP_TEST_PORT").unwrap().parse().unwrap();
        let token = std::env::var("FAIRBEAM_SHUTDOWN_TOKEN").unwrap();
        let _lock = std::fs::OpenOptions::new().read(true).write(true).create(true).truncate(false)
            .share_mode(0).open(lock).expect("another test child still owns the lock");
        let listener = TcpListener::bind(("127.0.0.1", port)).unwrap();
        for stream in listener.incoming() {
            let mut stream = stream.unwrap();
            stream.set_read_timeout(Some(std::time::Duration::from_secs(3))).unwrap();
            let mut request = Vec::new();
            let mut byte = [0u8; 1];
            while !request.ends_with(b"\r\n\r\n") {
                assert_eq!(stream.read(&mut byte).unwrap(), 1);
                request.push(byte[0]);
                assert!(request.len() < 8192);
            }
            let text = String::from_utf8(request).unwrap();
            let stop = text.starts_with("POST /api/shutdown ")
                && text.contains(&format!("X-Fairbeam-Token: {token}\r\n"));
            if stop {
                let mut body = [0u8; 2];
                stream.read_exact(&mut body).unwrap();
                assert_eq!(body, *b"{}");
                std::fs::write(&shutdown, "graceful").unwrap();
            }
            let status = if stop { "202 Accepted" } else { "200 OK" };
            stream.write_all(format!("HTTP/1.1 {status}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").as_bytes()).unwrap();
            if stop { break; }
        }
    }

    #[cfg(windows)]
    #[test]
    fn superseded_windows_child_is_stopped_before_next_spawn_and_current_child_survives() {
        use crate::server::{self, Server};
        use std::os::windows::fs::OpenOptionsExt;
        use std::process::{Command, Stdio};
        use std::sync::mpsc;
        use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
        let directory = std::env::temp_dir().join(format!("fairbeam-startup-test-{}-{}",
            std::process::id(), SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos()));
        std::fs::create_dir(&directory).unwrap();
        let lock = directory.join("jobs.lock");
        let command = |port: u16, name: &str| {
            let mut command = Command::new(std::env::current_exe().unwrap());
            command.args(["--exact", "startup::tests::windows_child_fixture", "--nocapture"])
                .env("FAIRBEAM_STARTUP_TEST_CHILD", "1")
                .env("FAIRBEAM_STARTUP_TEST_PORT", port.to_string())
                .env("FAIRBEAM_STARTUP_TEST_LOCK", &lock)
                .env("FAIRBEAM_STARTUP_TEST_SHUTDOWN", directory.join(name))
                .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null());
            command
        };
        let healthy = |port| {
            let deadline = Instant::now() + Duration::from_secs(10);
            while !server::http_ok(port, "/api/health") {
                assert!(Instant::now() < deadline, "test child did not become ready");
                thread::sleep(Duration::from_millis(10));
            }
        };
        let startup = Arc::new(Startup::default());
        let old = startup.begin(None).unwrap();
        let old_port = server::free_port().unwrap();
        let old_command = command(old_port, "old-shutdown");
        let (spawned_tx, spawned_rx) = mpsc::channel();
        let (resume_tx, resume_rx) = mpsc::channel();
        let worker = {
            let startup = startup.clone();
            thread::spawn(move || {
                let _handoff = startup.handoff();
                assert!(startup.current(old));
                let child = Server::spawn(old_command, old_port).unwrap();
                healthy(old_port);
                spawned_tx.send(child.child.id()).unwrap();
                resume_rx.recv_timeout(Duration::from_secs(10)).unwrap();
                let abandoned = match startup.publish(old, child) {
                    Err(child) => child,
                    Ok(()) => panic!("stale child was published"),
                };
                // This waits only on our own child, outside the state mutex. A UI generation
                // change is still possible while the worker holds the narrow handoff lock.
                abandoned.stop();
            })
        };
        let old_pid = spawned_rx.recv_timeout(Duration::from_secs(10)).unwrap();
        let new = startup.begin(None).unwrap();
        let new_port = server::free_port().unwrap();
        let new_command = command(new_port, "new-shutdown");
        let (waiting_tx, waiting_rx) = mpsc::channel();
        let replacement = {
            let (startup, directory, lock) = (startup.clone(), directory.clone(), lock.clone());
            thread::spawn(move || {
                waiting_tx.send(()).unwrap();
                let handoff = startup.handoff();
                // A real concurrent replacement must wait for the stale child's graceful
                // cleanup before attempting the same exclusive jobs lock.
                assert_eq!(std::fs::read_to_string(directory.join("old-shutdown")).unwrap(), "graceful");
                assert!(!server::http_ok(old_port, "/api/health"));
                drop(std::fs::OpenOptions::new().read(true).write(true).share_mode(0).open(&lock).unwrap());
                assert!(startup.take_current(new).unwrap().is_none());
                let child = Server::spawn(new_command, new_port).unwrap();
                let pid = child.child.id();
                assert!(startup.publish(new, child).is_ok());
                drop(handoff);
                healthy(new_port);
                pid
            })
        };
        waiting_rx.recv_timeout(Duration::from_secs(10)).unwrap();
        assert!(startup.handoff.try_lock().is_err());
        resume_tx.send(()).unwrap();
        worker.join().unwrap();
        let new_pid = replacement.join().unwrap();
        assert_eq!(std::fs::read_to_string(directory.join("old-shutdown")).unwrap(), "graceful");
        assert!(!server::http_ok(old_port, "/api/health"));
        assert!(startup.mark_ready(new, ready("new-workspace")));
        assert!(startup.take_current(old).is_err());
        assert!(!startup.mark_ready(old, ready("stale-workspace")));
        assert_eq!(startup.with_server(|server| server.child.id()), Some(new_pid));
        assert_eq!(startup.ready(), Some(ready("new-workspace")));
        assert!(server::http_ok(new_port, "/api/health"));
        assert!(!directory.join("new-shutdown").exists());
        startup.stop().unwrap().stop();
        assert!(!server::http_ok(new_port, "/api/health"));
        assert_eq!(std::fs::read_to_string(directory.join("new-shutdown")).unwrap(), "graceful");
        eprintln!("stale child {old_pid} stopped gracefully; current child {new_pid} survived stale stop/readiness and stopped only at global shutdown");
        for file in ["old-shutdown", "new-shutdown", "jobs.lock"] { std::fs::remove_file(directory.join(file)).unwrap(); }
        std::fs::remove_dir(directory).unwrap();
    }
}
