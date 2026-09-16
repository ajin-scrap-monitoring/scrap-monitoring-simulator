use std::cmp::min;
use std::io;
use std::net::{TcpStream, ToSocketAddrs};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use tungstenite::client::client_with_config;
use tungstenite::protocol::WebSocketConfig;
use tungstenite::{Bytes, Error as WebSocketError, Message, WebSocket};

use crate::backoff::ExponentialBackoff;
use crate::config::Config;
use crate::descriptor::parse_descriptor;
use crate::device::{FrameSink, V4l2Sink};
use crate::jpeg::validate_jpeg;
use crate::{FRAME_RATE, MAX_FRAME_BYTES};

const PACED_READ_POLL: Duration = Duration::from_millis(10);
const SHUTDOWN_POLL: Duration = Duration::from_millis(100);

fn frame_interval() -> Duration {
    Duration::from_secs_f64(1.0 / f64::from(FRAME_RATE))
}

fn next_periodic_deadline(deadline: Instant, now: Instant) -> Instant {
    let interval = frame_interval();
    let elapsed_periods = now.saturating_duration_since(deadline).as_nanos() / interval.as_nanos();
    let advance_nanoseconds = interval
        .as_nanos()
        .checked_mul(elapsed_periods + 1)
        .and_then(|value| u64::try_from(value).ok());
    advance_nanoseconds
        .and_then(|value| deadline.checked_add(Duration::from_nanos(value)))
        .unwrap_or(now + interval)
}

pub fn run(config: Config, shutdown: Arc<AtomicBool>) -> Result<(), String> {
    V4l2Sink::open(&config.device)?;
    let mut backoff = ExponentialBackoff::new(config.reconnect_initial, config.reconnect_max);

    while !shutdown.load(Ordering::Relaxed) {
        println!("connecting to {}", config.server_url);
        match connect(&config) {
            Ok(mut socket) => {
                println!("connected to {}", config.server_url);
                let mut sink = V4l2Sink::open(&config.device)?;
                match run_session(&mut socket, &mut sink, &config, &shutdown, &mut backoff) {
                    Ok(SessionOutcome::Shutdown) => return Ok(()),
                    Ok(SessionOutcome::Disconnected { wrote_frame }) => {
                        if wrote_frame {
                            backoff.reset();
                        }
                        eprintln!("camera stream disconnected");
                    }
                    Err(SessionError::Connection(error)) => {
                        eprintln!("camera stream connection failed: {error}");
                    }
                    Err(SessionError::Protocol(error)) => {
                        eprintln!("camera stream protocol rejected: {error}");
                    }
                    Err(SessionError::Device(error)) => return Err(error),
                }
            }
            Err(error) => eprintln!("camera stream connection failed: {error}"),
        }

        let delay = backoff.next_delay();
        eprintln!("reconnecting in {} ms", delay.as_millis());
        if sleep_until_shutdown(delay, &shutdown) {
            return Ok(());
        }
    }
    Ok(())
}

#[derive(Debug, Eq, PartialEq)]
enum SessionOutcome {
    Shutdown,
    Disconnected { wrote_frame: bool },
}

#[derive(Debug, Eq, PartialEq)]
enum SessionError {
    Connection(String),
    Protocol(String),
    Device(String),
}

fn connect(config: &Config) -> Result<WebSocket<TcpStream>, String> {
    let host = config.server_url.host();
    let port = config.server_url.port();
    let addresses = (host, port)
        .to_socket_addrs()
        .map_err(|error| format!("cannot resolve {host}:{port}: {error}"))?;

    let mut last_error = None;
    for address in addresses {
        match TcpStream::connect_timeout(&address, config.connect_timeout) {
            Ok(stream) => {
                stream
                    .set_nodelay(true)
                    .map_err(|error| format!("cannot enable TCP_NODELAY: {error}"))?;
                stream
                    .set_read_timeout(Some(config.connect_timeout))
                    .map_err(|error| format!("cannot set handshake read timeout: {error}"))?;
                stream
                    .set_write_timeout(Some(config.connect_timeout))
                    .map_err(|error| format!("cannot set handshake write timeout: {error}"))?;

                let websocket_config = WebSocketConfig::default()
                    .read_buffer_size(8 * 1024)
                    .write_buffer_size(0)
                    .max_write_buffer_size(8 * 1024)
                    .max_message_size(Some(MAX_FRAME_BYTES))
                    .max_frame_size(Some(MAX_FRAME_BYTES));
                let (mut socket, _) =
                    client_with_config(config.server_url.as_str(), stream, Some(websocket_config))
                        .map_err(|error| format!("WebSocket handshake failed: {error}"))?;

                let read_timeout = min(config.io_timeout, PACED_READ_POLL);
                socket
                    .get_mut()
                    .set_read_timeout(Some(read_timeout))
                    .map_err(|error| format!("cannot set stream read timeout: {error}"))?;
                socket
                    .get_mut()
                    .set_write_timeout(Some(config.io_timeout))
                    .map_err(|error| format!("cannot set stream write timeout: {error}"))?;
                return Ok(socket);
            }
            Err(error) => last_error = Some(error),
        }
    }

    Err(match last_error {
        Some(error) => format!("cannot connect to {host}:{port}: {error}"),
        None => format!("{host}:{port} resolved to no addresses"),
    })
}

fn run_session<S: FrameSink>(
    socket: &mut WebSocket<TcpStream>,
    sink: &mut S,
    config: &Config,
    shutdown: &AtomicBool,
    backoff: &mut ExponentialBackoff,
) -> Result<SessionOutcome, SessionError> {
    let mut processor = SessionProcessor::new();
    let mut last_message_at = Instant::now();
    loop {
        if shutdown.load(Ordering::Relaxed) {
            let _ = socket.close(None);
            return Ok(SessionOutcome::Shutdown);
        }
        processor.poll(Instant::now(), sink)?;

        match socket.read() {
            Ok(Message::Text(text)) => {
                last_message_at = Instant::now();
                processor.handle_text(text.as_str())?;
            }
            Ok(Message::Binary(frame)) => {
                let received_at = Instant::now();
                last_message_at = received_at;
                processor.handle_binary(frame, received_at, sink)?;
                if processor.wrote_frame {
                    backoff.reset();
                }
            }
            Ok(Message::Close(_)) => {
                return Ok(SessionOutcome::Disconnected {
                    wrote_frame: processor.wrote_frame,
                });
            }
            Ok(Message::Ping(_) | Message::Pong(_) | Message::Frame(_)) => {
                last_message_at = Instant::now();
                socket.flush().map_err(map_websocket_error)?;
            }
            Err(WebSocketError::ConnectionClosed | WebSocketError::AlreadyClosed) => {
                return Ok(SessionOutcome::Disconnected {
                    wrote_frame: processor.wrote_frame,
                });
            }
            Err(WebSocketError::Io(error)) if is_timeout(&error) => {
                if stream_timed_out(last_message_at, Instant::now(), config.io_timeout) {
                    return Ok(SessionOutcome::Disconnected {
                        wrote_frame: processor.wrote_frame,
                    });
                }
            }
            Err(error) => return Err(map_websocket_error(error)),
        }
        processor.poll(Instant::now(), sink)?;
    }
}

fn stream_timed_out(last_message_at: Instant, now: Instant, timeout: Duration) -> bool {
    now.saturating_duration_since(last_message_at) >= timeout
}

fn map_websocket_error(error: WebSocketError) -> SessionError {
    SessionError::Connection(error.to_string())
}

fn is_timeout(error: &io::Error) -> bool {
    matches!(
        error.kind(),
        io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
    )
}

struct SessionProcessor {
    descriptor_received: bool,
    latest_frame: Option<Bytes>,
    next_write: Option<Instant>,
    wrote_frame: bool,
}

impl SessionProcessor {
    fn new() -> Self {
        Self {
            descriptor_received: false,
            latest_frame: None,
            next_write: None,
            wrote_frame: false,
        }
    }

    fn handle_text(&mut self, text: &str) -> Result<(), SessionError> {
        if self.descriptor_received {
            return Err(SessionError::Protocol(
                "text message is only allowed as the initial descriptor".to_owned(),
            ));
        }
        parse_descriptor(text).map_err(SessionError::Protocol)?;
        self.descriptor_received = true;
        Ok(())
    }

    fn handle_binary<S: FrameSink>(
        &mut self,
        frame: Bytes,
        now: Instant,
        sink: &mut S,
    ) -> Result<(), SessionError> {
        if !self.descriptor_received {
            return Err(SessionError::Protocol(
                "binary frame arrived before stream descriptor".to_owned(),
            ));
        }
        validate_jpeg(frame.as_ref()).map_err(SessionError::Protocol)?;
        self.latest_frame = Some(frame);
        self.next_write.get_or_insert(now);
        self.poll(now, sink)
    }

    fn poll<S: FrameSink>(&mut self, now: Instant, sink: &mut S) -> Result<(), SessionError> {
        let Some(deadline) = self.next_write else {
            return Ok(());
        };
        if now < deadline {
            return Ok(());
        }
        let Some(frame) = self.latest_frame.as_ref() else {
            return Ok(());
        };
        sink.write_frame(frame.as_ref())
            .map_err(SessionError::Device)?;
        self.wrote_frame = true;
        self.next_write = Some(next_periodic_deadline(deadline, now));
        Ok(())
    }
}

fn sleep_until_shutdown(duration: Duration, shutdown: &AtomicBool) -> bool {
    let deadline = Instant::now() + duration;
    loop {
        if shutdown.load(Ordering::Relaxed) {
            return true;
        }
        let now = Instant::now();
        if now >= deadline {
            return false;
        }
        thread::sleep(min(deadline - now, SHUTDOWN_POLL));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{FRAME_HEIGHT, FRAME_WIDTH};

    const DESCRIPTOR: &str = r#"{"type":"camera_stream_descriptor","version":1,"format":"MJPEG","width":1920,"height":1080,"fps":30,"max_frame_bytes":4194304}"#;

    #[derive(Default)]
    struct MockSink {
        frames: Vec<Vec<u8>>,
    }

    impl FrameSink for MockSink {
        fn write_frame(&mut self, frame: &[u8]) -> Result<(), String> {
            self.frames.push(frame.to_vec());
            Ok(())
        }
    }

    fn jpeg(tag: u8) -> Vec<u8> {
        let [height_high, height_low] = FRAME_HEIGHT.to_be_bytes();
        let [width_high, width_low] = FRAME_WIDTH.to_be_bytes();
        vec![
            0xff,
            0xd8,
            0xff,
            0xc0,
            0x00,
            0x0b,
            0x08,
            height_high,
            height_low,
            width_high,
            width_low,
            0x01,
            0x01,
            0x11,
            tag,
            0xff,
            0xd9,
        ]
    }

    #[test]
    fn requires_descriptor_before_binary_frame() {
        let mut processor = SessionProcessor::new();
        let mut sink = MockSink::default();

        let error = processor
            .handle_binary(jpeg(0).into(), Instant::now(), &mut sink)
            .unwrap_err();
        assert!(matches!(error, SessionError::Protocol(_)));
        assert!(sink.frames.is_empty());
    }

    #[test]
    fn stream_timeout_requires_the_full_io_deadline() {
        let last_message_at = Instant::now();

        assert!(!stream_timed_out(
            last_message_at,
            last_message_at + Duration::from_millis(999),
            Duration::from_secs(1),
        ));
        assert!(stream_timed_out(
            last_message_at,
            last_message_at + Duration::from_secs(1),
            Duration::from_secs(1),
        ));
    }

    #[test]
    fn bridge_cadence_keeps_only_latest_frame_and_repeats_it() {
        let start = Instant::now();
        let mut processor = SessionProcessor::new();
        let mut sink = MockSink::default();
        processor.handle_text(DESCRIPTOR).expect("descriptor");

        let first = jpeg(1);
        let latest = jpeg(2);
        processor
            .handle_binary(first.clone().into(), start, &mut sink)
            .expect("first frame");
        processor
            .handle_binary(latest.clone().into(), start, &mut sink)
            .expect("latest frame");
        processor
            .poll(start + Duration::from_millis(34), &mut sink)
            .expect("paced frame");

        assert_eq!(sink.frames, vec![first, latest.clone()]);
        assert_eq!(
            processor.latest_frame.as_ref().map(Bytes::as_ref),
            Some(latest.as_slice())
        );
    }

    #[test]
    fn bridge_cadence_does_not_accumulate_poll_delay() {
        let start = Instant::now();
        let mut processor = SessionProcessor::new();
        let mut sink = MockSink::default();
        processor.handle_text(DESCRIPTOR).expect("descriptor");
        processor
            .handle_binary(jpeg(1).into(), start, &mut sink)
            .expect("first frame");

        for milliseconds in (10..=1_000).step_by(10) {
            processor
                .poll(start + Duration::from_millis(milliseconds), &mut sink)
                .expect("paced frame");
        }

        assert_eq!(sink.frames.len(), 31);
    }

    #[test]
    fn bridge_cadence_skips_missed_slots_without_a_burst() {
        let start = Instant::now();
        let mut processor = SessionProcessor::new();
        let mut sink = MockSink::default();
        processor.handle_text(DESCRIPTOR).expect("descriptor");
        processor
            .handle_binary(jpeg(1).into(), start, &mut sink)
            .expect("first frame");

        let delayed = start + Duration::from_millis(250);
        processor.poll(delayed, &mut sink).expect("delayed frame");
        processor.poll(delayed, &mut sink).expect("no burst");
        assert_eq!(sink.frames.len(), 2);

        processor
            .poll(start + Duration::from_millis(267), &mut sink)
            .expect("next periodic frame");
        assert_eq!(sink.frames.len(), 3);
    }

    #[test]
    fn duplicate_descriptor_is_rejected() {
        let mut processor = SessionProcessor::new();
        processor.handle_text(DESCRIPTOR).expect("descriptor");

        let error = processor.handle_text(DESCRIPTOR).unwrap_err();
        assert!(matches!(error, SessionError::Protocol(_)));
    }

    #[test]
    fn backoff_sleep_observes_shutdown() {
        let shutdown = AtomicBool::new(true);
        assert!(sleep_until_shutdown(Duration::from_secs(1), &shutdown));
    }
}
