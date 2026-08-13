use std::time::Duration;

pub const SHUTDOWN_POLLS: usize = 50;
pub const SHUTDOWN_POLL_INTERVAL: Duration = Duration::from_millis(100);

#[derive(Debug, PartialEq, Eq)]
pub enum StopMode {
    Graceful,
    Forced,
}

pub trait OwnedProcess {
    fn has_exited(&mut self) -> Result<bool, String>;
    fn force_kill(&mut self) -> Result<(), String>;
    fn wait_for_exit(&mut self) -> Result<(), String>;
}

pub fn stop_owned_process<P, R, S>(
    process: &mut P,
    token: &str,
    request_shutdown: R,
    mut sleep: S,
    polls: usize,
) -> Result<StopMode, String>
where
    P: OwnedProcess,
    R: FnOnce(&str) -> Result<(), String>,
    S: FnMut(Duration),
{
    if request_shutdown(token).is_ok() {
        for _ in 0..polls {
            match process.has_exited() {
                Ok(true) => return Ok(StopMode::Graceful),
                Ok(false) => sleep(SHUTDOWN_POLL_INTERVAL),
                Err(_) => break,
            }
        }
    }
    process.force_kill()?;
    process.wait_for_exit()?;
    Ok(StopMode::Forced)
}

#[cfg(test)]
mod tests {
    use super::{
        stop_owned_process, Duration, OwnedProcess, StopMode, SHUTDOWN_POLLS,
        SHUTDOWN_POLL_INTERVAL,
    };
    use std::cell::RefCell;
    use std::rc::Rc;

    struct FakeProcess {
        calls: Rc<RefCell<Vec<&'static str>>>,
        exits_after: Option<usize>,
        polls: usize,
    }

    impl FakeProcess {
        fn new(calls: Rc<RefCell<Vec<&'static str>>>, exits_after: Option<usize>) -> Self {
            Self {
                calls,
                exits_after,
                polls: 0,
            }
        }
    }

    impl OwnedProcess for FakeProcess {
        fn has_exited(&mut self) -> Result<bool, String> {
            self.calls.borrow_mut().push("poll");
            self.polls += 1;
            Ok(self
                .exits_after
                .is_some_and(|threshold| self.polls >= threshold))
        }

        fn force_kill(&mut self) -> Result<(), String> {
            self.calls.borrow_mut().push("kill");
            Ok(())
        }

        fn wait_for_exit(&mut self) -> Result<(), String> {
            self.calls.borrow_mut().push("wait");
            Ok(())
        }
    }

    #[test]
    fn production_graceful_wait_budget_is_five_seconds() {
        assert_eq!(
            SHUTDOWN_POLL_INTERVAL * SHUTDOWN_POLLS as u32,
            Duration::from_secs(5)
        );
    }

    #[test]
    fn graceful_shutdown_requests_then_waits_without_killing() {
        let calls = Rc::new(RefCell::new(Vec::new()));
        let mut process = FakeProcess::new(Rc::clone(&calls), Some(2));
        let request_calls = Rc::clone(&calls);
        let sleep_calls = Rc::clone(&calls);

        let mode = stop_owned_process(
            &mut process,
            "owned-token",
            move |token| {
                assert_eq!(token, "owned-token");
                request_calls.borrow_mut().push("request");
                Ok(())
            },
            move |_duration: Duration| sleep_calls.borrow_mut().push("sleep"),
            3,
        )
        .unwrap();

        assert_eq!(mode, StopMode::Graceful);
        assert_eq!(
            calls.borrow().as_slice(),
            ["request", "poll", "sleep", "poll"]
        );
    }

    #[test]
    fn rejected_shutdown_falls_back_to_kill_and_wait() {
        let calls = Rc::new(RefCell::new(Vec::new()));
        let mut process = FakeProcess::new(Rc::clone(&calls), None);
        let request_calls = Rc::clone(&calls);

        let mode = stop_owned_process(
            &mut process,
            "owned-token",
            move |_| {
                request_calls.borrow_mut().push("request");
                Err("offline".to_string())
            },
            |_| {},
            3,
        )
        .unwrap();

        assert_eq!(mode, StopMode::Forced);
        assert_eq!(calls.borrow().as_slice(), ["request", "kill", "wait"]);
    }

    #[test]
    fn graceful_timeout_falls_back_after_finite_polls() {
        let calls = Rc::new(RefCell::new(Vec::new()));
        let mut process = FakeProcess::new(Rc::clone(&calls), None);
        let request_calls = Rc::clone(&calls);

        let mode = stop_owned_process(
            &mut process,
            "owned-token",
            move |_| {
                request_calls.borrow_mut().push("request");
                Ok(())
            },
            |_| {},
            2,
        )
        .unwrap();

        assert_eq!(mode, StopMode::Forced);
        assert_eq!(
            calls.borrow().as_slice(),
            ["request", "poll", "poll", "kill", "wait"]
        );
    }
}
