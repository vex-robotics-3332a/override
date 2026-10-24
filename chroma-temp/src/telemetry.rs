use alloc::rc::{Rc, Weak};
use core::fmt::Write as FmtWrite;
use std::fmt::Display;
use std::fs::File;
use std::io::Write;
use vexide::prelude::*;
use vexide::sync::Mutex;

struct TelemetryState {
    file: Option<File>,
    pending: usize,
    writes_since_flush: usize,
    buffer_size: usize,
    iter_timeout: usize,
}

impl TelemetryState {
    fn maybe_flush(&mut self) {
        let should_flush =
            self.pending >= self.buffer_size || self.writes_since_flush >= self.iter_timeout;

        if !should_flush {
            return;
        }

        if let Some(file) = &mut self.file {
            file.flush().ok();
        }

        self.pending = 0;
        self.writes_since_flush = 0;
    }
}

pub struct TelemetryFile {
    state: Rc<Mutex<TelemetryState>>,
}

impl TelemetryFile {
    pub const DEFAULT_BUFFER_SIZE: usize = 4096;
    pub const DEFAULT_ITER_TIMEOUT: usize = 30;

    pub fn new_csv(
        path: &str,
        columns: &[&str],
        buffer_size: usize,
        iter_timeout: usize,
    ) -> Self {
        Self::new(
            path.to_string(),
            columns.iter().map(|col| (*col).to_string()).collect(),
            buffer_size,
            iter_timeout,
        )
    }

    pub fn new(path: String, header: Vec<String>, buffer_size: usize, iter_timeout: usize) -> Self {
        let mut file = File::create(path).ok();

        if let Some(file) = &mut file {
            for (i, col) in header.iter().enumerate() {
                if i > 0 {
                    write!(file, ",").ok();
                }
                write!(file, "{}", col).ok();
            }
            writeln!(file).ok();
            file.flush().ok();
        }

        let state = Rc::new(Mutex::new(TelemetryState {
            file,
            pending: 0,
            writes_since_flush: 0,
            buffer_size,
            iter_timeout,
        }));

        spawn(Self::flush_loop(Rc::downgrade(&state))).detach();

        Self { state }
    }

    /// Holds a `Weak` so the file closes once the `TelemetryFile` and pending writes drop;
    /// VEXos only allows a few open files at once.
    async fn flush_loop(state: Weak<Mutex<TelemetryState>>) {
        loop {
            sleep(crate::SLEEP_INTERVAL).await;

            let Some(state) = state.upgrade() else {
                return;
            };
            let Some(mut s) = state.try_lock() else {
                continue;
            };
            s.maybe_flush();
        }
    }

    /// Schedules a write on a background task and returns immediately.
    /// Safe to call from hot loops; SD I/O never blocks the caller.
    pub fn write<T: Display + Send + 'static>(&self, contents: Vec<T>) {
        let state = Rc::clone(&self.state);

        spawn(async move {
            let Some(mut s) = state.try_lock() else {
                return;
            };

            let Some(file) = &mut s.file else {
                return;
            };

            let mut line = String::new();
            for (i, data) in contents.iter().enumerate() {
                if i > 0 {
                    line.push(',');
                }
                write!(line, "{}", data).ok();
            }
            line.push('\n');

            if file.write_all(line.as_bytes()).is_err() {
                return;
            }

            s.pending += line.len();
            s.writes_since_flush += 1;
            s.maybe_flush();
        })
        .detach();
    }
}

/// Log one CSV row. Usage: `telemetry!(ct_e, time, error);`
#[macro_export]
macro_rules! telemetry {
    ($file:expr $(, $val:expr)* $(,)?) => {
        $file.write(vec![$(::alloc::string::ToString::to_string(&$val)),*])
    };
}
