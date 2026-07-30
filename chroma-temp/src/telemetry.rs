use std::{fs::*, io};
use std::fmt::Display;
use std::io::{Result, Write};


pub struct TelemetryFile {
    //csv format or something
    file: Option<File>,
    header: Vec<String>,
    pending: usize,
    buffer_size: usize,
}

impl TelemetryFile {
    pub fn new(path: String, header: Vec<String>, buffer_size: usize) -> Self {

        let mut file = File::create(path).ok();
        if let Some(file) = &mut file {
            for col_header in &header {
                write!(file, "{}", col_header).ok();
            }
        }

        Self {
            file,
            header,
            pending: 0,
            buffer_size
        }
    }

    pub fn write<T: Display> (&mut self, contents: Vec<T>) -> std::io::Result<()>{
        if let Some(file) = &mut self.file {
            for data in contents {
                self.pending += data.to_string().len();
                write!(file, "{},", data).expect("Write panicked with successful file creation");
            }
            write!(file, "\n").expect("Write panicked with successful file creation");

            if self.pending >= self.buffer_size {
                // NOT hotloop compatible but ehh fuck it
                file.flush().expect("File failed to flush!");
                self.pending = 0
            }

            Ok(())
        }
        else {
            Err(io::Error::from(io::ErrorKind::PermissionDenied))
        }
    }
}