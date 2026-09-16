use serde::Deserialize;

use crate::{FRAME_HEIGHT, FRAME_RATE, FRAME_WIDTH, MAX_DESCRIPTOR_BYTES, MAX_FRAME_BYTES};

#[derive(Debug, Deserialize, Eq, PartialEq)]
#[serde(deny_unknown_fields)]
pub struct StreamDescriptor {
    #[serde(rename = "type")]
    message_type: String,
    version: u32,
    format: String,
    width: u16,
    height: u16,
    fps: u32,
    max_frame_bytes: usize,
}

pub fn parse_descriptor(text: &str) -> Result<StreamDescriptor, String> {
    if text.len() > MAX_DESCRIPTOR_BYTES {
        return Err(format!(
            "stream descriptor exceeds {MAX_DESCRIPTOR_BYTES} bytes"
        ));
    }
    let descriptor: StreamDescriptor = serde_json::from_str(text)
        .map_err(|error| format!("invalid stream descriptor: {error}"))?;
    descriptor.validate()?;
    Ok(descriptor)
}

impl StreamDescriptor {
    fn validate(&self) -> Result<(), String> {
        if self.message_type != "camera_stream_descriptor"
            || self.version != 1
            || self.format != "MJPEG"
            || self.width != FRAME_WIDTH
            || self.height != FRAME_HEIGHT
            || self.fps != FRAME_RATE
            || self.max_frame_bytes != MAX_FRAME_BYTES
        {
            return Err(format!(
                "unsupported stream descriptor: expected camera_stream_descriptor v1 MJPEG {}x{} {} FPS with {} max bytes",
                FRAME_WIDTH, FRAME_HEIGHT, FRAME_RATE, MAX_FRAME_BYTES
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const VALID: &str = r#"{"type":"camera_stream_descriptor","version":1,"format":"MJPEG","width":1920,"height":1080,"fps":30,"max_frame_bytes":4194304}"#;

    #[test]
    fn accepts_exact_v1_contract() {
        assert!(parse_descriptor(VALID).is_ok());
    }

    #[test]
    fn rejects_unknown_fields_and_mismatches() {
        let extra = VALID.replace("}", ",\"token\":\"unexpected\"}");
        assert!(
            parse_descriptor(&extra)
                .unwrap_err()
                .contains("unknown field")
        );

        let wrong_width = VALID.replace("1920", "1280");
        assert!(
            parse_descriptor(&wrong_width)
                .unwrap_err()
                .contains("unsupported stream descriptor")
        );
    }

    #[test]
    fn enforces_text_bound() {
        let oversized = " ".repeat(MAX_DESCRIPTOR_BYTES + 1);
        assert!(
            parse_descriptor(&oversized)
                .unwrap_err()
                .contains("exceeds")
        );
    }
}
