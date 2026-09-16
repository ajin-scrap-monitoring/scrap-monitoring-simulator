use std::fs::{File, OpenOptions};
use std::io::{self, Write};
use std::mem::size_of;
use std::os::fd::AsRawFd;
use std::os::unix::fs::{FileTypeExt, OpenOptionsExt};
use std::path::Path;

use crate::{FRAME_HEIGHT, FRAME_WIDTH};

const V4L2_BUF_TYPE_VIDEO_OUTPUT: u32 = 2;
const V4L2_CAP_VIDEO_OUTPUT: u32 = 0x0000_0002;
const V4L2_CAP_READWRITE: u32 = 0x0100_0000;
const V4L2_CAP_DEVICE_CAPS: u32 = 0x8000_0000;
const V4L2_PIX_FMT_MJPEG: u32 = u32::from_le_bytes(*b"MJPG");

const IOC_NRBITS: u32 = 8;
const IOC_TYPEBITS: u32 = 8;
const IOC_SIZEBITS: u32 = 14;
const IOC_NRSHIFT: u32 = 0;
const IOC_TYPESHIFT: u32 = IOC_NRSHIFT + IOC_NRBITS;
const IOC_SIZESHIFT: u32 = IOC_TYPESHIFT + IOC_TYPEBITS;
const IOC_DIRSHIFT: u32 = IOC_SIZESHIFT + IOC_SIZEBITS;
const IOC_WRITE: u32 = 1;
const IOC_READ: u32 = 2;

const fn ioctl_number(direction: u32, kind: u8, number: u8, size: usize) -> libc::c_ulong {
    ((direction << IOC_DIRSHIFT)
        | ((kind as u32) << IOC_TYPESHIFT)
        | ((number as u32) << IOC_NRSHIFT)
        | ((size as u32) << IOC_SIZESHIFT)) as libc::c_ulong
}

#[repr(C)]
#[derive(Clone, Copy, Default)]
struct V4l2Capability {
    driver: [u8; 16],
    card: [u8; 32],
    bus_info: [u8; 32],
    version: u32,
    capabilities: u32,
    device_caps: u32,
    reserved: [u32; 3],
}

#[repr(C, align(8))]
struct V4l2Format {
    type_: u32,
    padding: u32,
    raw_data: [u8; 200],
}

impl V4l2Format {
    fn video_output() -> Self {
        Self {
            type_: V4L2_BUF_TYPE_VIDEO_OUTPUT,
            padding: 0,
            raw_data: [0; 200],
        }
    }

    fn width(&self) -> u32 {
        u32::from_ne_bytes(self.raw_data[0..4].try_into().expect("fixed width slice"))
    }

    fn height(&self) -> u32 {
        u32::from_ne_bytes(self.raw_data[4..8].try_into().expect("fixed height slice"))
    }

    fn pixel_format(&self) -> u32 {
        u32::from_ne_bytes(self.raw_data[8..12].try_into().expect("fixed format slice"))
    }
}

const VIDIOC_QUERYCAP: libc::c_ulong = ioctl_number(IOC_READ, b'V', 0, size_of::<V4l2Capability>());
const VIDIOC_G_FMT: libc::c_ulong =
    ioctl_number(IOC_READ | IOC_WRITE, b'V', 4, size_of::<V4l2Format>());

pub trait FrameSink {
    fn write_frame(&mut self, frame: &[u8]) -> Result<(), String>;
}

pub struct V4l2Sink {
    file: File,
}

impl V4l2Sink {
    pub fn open(path: &Path) -> Result<Self, String> {
        let metadata = path
            .metadata()
            .map_err(|error| format!("cannot inspect {}: {error}", path.display()))?;
        if !metadata.file_type().is_char_device() {
            return Err(format!("{} is not a character device", path.display()));
        }

        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(libc::O_CLOEXEC)
            .open(path)
            .map_err(|error| format!("cannot open {}: {error}", path.display()))?;
        validate_device(&file, path)?;
        Ok(Self { file })
    }
}

impl FrameSink for V4l2Sink {
    fn write_frame(&mut self, frame: &[u8]) -> Result<(), String> {
        match self.file.write(frame) {
            Ok(written) if written == frame.len() => Ok(()),
            Ok(written) => Err(format!(
                "V4L2 short write: wrote {written} of {} bytes",
                frame.len()
            )),
            Err(error) => Err(format!("V4L2 frame write failed: {error}")),
        }
    }
}

fn validate_device(file: &File, path: &Path) -> Result<(), String> {
    let mut capability = V4l2Capability::default();
    ioctl(file, VIDIOC_QUERYCAP, &mut capability)
        .map_err(|error| format!("{} is not a usable V4L2 device: {error}", path.display()))?;
    let capabilities = if capability.capabilities & V4L2_CAP_DEVICE_CAPS != 0 {
        capability.device_caps
    } else {
        capability.capabilities
    };
    if capabilities & V4L2_CAP_VIDEO_OUTPUT == 0 || capabilities & V4L2_CAP_READWRITE == 0 {
        return Err(format!(
            "{} must support V4L2 video output and read/write I/O",
            path.display()
        ));
    }

    let mut format = V4l2Format::video_output();
    ioctl(file, VIDIOC_G_FMT, &mut format).map_err(|error| {
        format!(
            "cannot read V4L2 output format from {}: {error}",
            path.display()
        )
    })?;
    if format.width() != u32::from(FRAME_WIDTH)
        || format.height() != u32::from(FRAME_HEIGHT)
        || format.pixel_format() != V4L2_PIX_FMT_MJPEG
    {
        return Err(format!(
            "{} must be configured as MJPG {}x{}, observed {} {}x{}",
            path.display(),
            FRAME_WIDTH,
            FRAME_HEIGHT,
            fourcc(format.pixel_format()),
            format.width(),
            format.height()
        ));
    }
    Ok(())
}

fn ioctl<T>(file: &File, request: libc::c_ulong, value: &mut T) -> io::Result<()> {
    // SAFETY: `value` is a writable C-compatible structure for the request and
    // remains alive for the duration of the ioctl call. `file` owns a valid fd.
    let result = unsafe { libc::ioctl(file.as_raw_fd(), request, value) };
    if result < 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    }
}

fn fourcc(value: u32) -> String {
    value
        .to_le_bytes()
        .into_iter()
        .map(|byte| {
            if byte.is_ascii_graphic() {
                char::from(byte)
            } else {
                '?'
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn linux_v4l2_layout_and_request_numbers_match_uapi() {
        assert_eq!(size_of::<V4l2Capability>(), 104);
        assert_eq!(size_of::<V4l2Format>(), 208);
        assert_eq!(VIDIOC_QUERYCAP, 0x8068_5600);
        assert_eq!(VIDIOC_G_FMT, 0xc0d0_5604);
    }

    #[test]
    fn formats_fourcc_for_diagnostics() {
        assert_eq!(fourcc(V4L2_PIX_FMT_MJPEG), "MJPG");
    }
}
