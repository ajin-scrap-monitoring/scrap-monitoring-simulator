#[cfg(not(target_os = "linux"))]
compile_error!("scrap-synthetic-camera-bridge supports Linux only");

pub mod backoff;
pub mod bridge;
pub mod config;
pub mod descriptor;
pub mod device;
pub mod jpeg;

pub const FRAME_WIDTH: u16 = 1920;
pub const FRAME_HEIGHT: u16 = 1080;
pub const FRAME_RATE: u32 = 30;
pub const MAX_FRAME_BYTES: usize = 4 * 1024 * 1024;
pub const MAX_DESCRIPTOR_BYTES: usize = 4096;
