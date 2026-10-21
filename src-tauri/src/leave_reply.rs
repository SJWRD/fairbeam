//! A missing or invalid callback cancels consent; only a live callback can stay pending.
use std::sync::mpsc::{Receiver, RecvTimeoutError};
use std::time::Duration;

#[derive(Debug, PartialEq)]
pub enum Reply {
    Pending,
    Cancel,
    Close,
}

pub fn wait(receiver: Receiver<String>, update: bool, mut set_silent: impl FnMut(bool)) -> Reply {
    let answer = match receiver.recv_timeout(Duration::from_secs(2)) {
        Ok(answer) => answer,
        Err(RecvTimeoutError::Timeout) => {
            if update { return Reply::Cancel; }
            // A live but hung page can still answer. A second menu/window request may leave.
            set_silent(true);
            match receiver.recv() {
                Ok(answer) => {
                    set_silent(false);
                    answer
                }
                Err(_) => return Reply::Cancel,
            }
        }
        Err(RecvTimeoutError::Disconnected) => return Reply::Cancel,
    };
    // The callback normally returns JSON (a quoted string), as before.
    match answer.trim().trim_matches('"') {
        "pending" => Reply::Pending,
        "cancel" => Reply::Cancel,
        "close" => Reply::Close,
        _ => Reply::Cancel,
    }
}

#[cfg(test)]
mod tests {
    use super::{wait, Reply};
    use std::sync::mpsc;
    use std::thread;

    #[test]
    fn explicit_viewer_answers_keep_their_meaning() {
        for (answer, expected) in [
            ("\"pending\"", Reply::Pending),
            (" \"cancel\" ", Reply::Cancel),
            ("\"close\"", Reply::Close),
            ("close", Reply::Close),
        ] {
            let (send, receive) = mpsc::channel();
            send.send(answer.into()).unwrap();
            assert_eq!(wait(receive, false, |_| panic!("a ready answer is not silent")), expected);
        }
    }

    #[test]
    fn missing_or_invalid_script_answers_cancel() {
        for update in [false, true] {
            for answer in ["", "\"\"", "undefined", "null", "\"script error\""] {
                let (send, receive) = mpsc::channel();
                send.send(answer.into()).unwrap();
                assert_eq!(wait(receive, update, |_| panic!("an invalid answer is terminal")), Reply::Cancel);
            }
        }
    }

    #[test]
    fn dropped_callback_cancels_without_becoming_silent() {
        for update in [false, true] {
            let (send, receive) = mpsc::channel();
            drop(send);
            assert_eq!(wait(receive, update, |_| panic!("a dropped callback is terminal")), Reply::Cancel);
        }
    }

    #[test]
    fn callback_dropped_after_timeout_also_cancels() {
        let (send, receive) = mpsc::channel();
        let (silent, observed) = mpsc::channel();
        let worker = thread::spawn(move || wait(receive, false, |value| silent.send(value).unwrap()));
        assert!(observed.recv().unwrap(), "the unchanged two-second wait marks a live channel silent");
        drop(send);
        assert_eq!(worker.join().unwrap(), Reply::Cancel);
    }

    #[test]
    fn live_hung_callback_can_still_answer() {
        let (send, receive) = mpsc::channel();
        let (silent, observed) = mpsc::channel();
        let worker = thread::spawn(move || wait(receive, false, |value| silent.send(value).unwrap()));
        assert!(observed.recv().unwrap());
        assert!(!worker.is_finished(), "a live callback is still awaited after the deadline");
        send.send("\"close\"".into()).unwrap();
        assert!(!observed.recv().unwrap(), "a late answer clears the hung-page marker");
        assert_eq!(worker.join().unwrap(), Reply::Close);
    }

    #[test]
    fn update_timeout_cancels_without_enabling_second_quit() {
        let (_send, receive) = mpsc::channel();
        assert_eq!(wait(receive, true, |_| panic!("update must not use the hung-page fallback")), Reply::Cancel);
    }
}
