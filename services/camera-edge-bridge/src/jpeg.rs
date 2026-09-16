use crate::{FRAME_HEIGHT, FRAME_WIDTH, MAX_FRAME_BYTES};

pub fn validate_jpeg(frame: &[u8]) -> Result<(), String> {
    if frame.len() > MAX_FRAME_BYTES {
        return Err(format!("JPEG exceeds {MAX_FRAME_BYTES} bytes"));
    }
    if frame.len() < 4 || !frame.starts_with(&[0xff, 0xd8]) || !frame.ends_with(&[0xff, 0xd9]) {
        return Err("JPEG must start with SOI and end with EOI".to_owned());
    }

    let (width, height) = dimensions(frame)?;
    if width != FRAME_WIDTH || height != FRAME_HEIGHT {
        return Err(format!(
            "JPEG dimensions must be {}x{}, received {width}x{height}",
            FRAME_WIDTH, FRAME_HEIGHT
        ));
    }
    Ok(())
}

fn dimensions(frame: &[u8]) -> Result<(u16, u16), String> {
    let mut index = 2;
    while index < frame.len() - 1 {
        if frame[index] != 0xff {
            return Err("invalid JPEG marker sequence".to_owned());
        }
        while index < frame.len() && frame[index] == 0xff {
            index += 1;
        }
        if index >= frame.len() {
            break;
        }

        let marker = frame[index];
        index += 1;
        if marker == 0x00 {
            return Err("unexpected stuffed byte before JPEG scan data".to_owned());
        }
        if marker == 0xd9 {
            break;
        }
        if marker == 0xd8 || marker == 0x01 || (0xd0..=0xd7).contains(&marker) {
            continue;
        }
        if index + 2 > frame.len() {
            return Err("truncated JPEG segment length".to_owned());
        }
        let segment_length = usize::from(u16::from_be_bytes([frame[index], frame[index + 1]]));
        if segment_length < 2 || index + segment_length > frame.len() {
            return Err("invalid JPEG segment length".to_owned());
        }

        if is_start_of_frame(marker) {
            if segment_length < 8 {
                return Err("truncated JPEG start-of-frame segment".to_owned());
            }
            let height = u16::from_be_bytes([frame[index + 3], frame[index + 4]]);
            let width = u16::from_be_bytes([frame[index + 5], frame[index + 6]]);
            if width == 0 || height == 0 {
                return Err("JPEG dimensions must be nonzero".to_owned());
            }
            return Ok((width, height));
        }
        if marker == 0xda {
            return Err("JPEG has no start-of-frame dimensions".to_owned());
        }
        index += segment_length;
    }
    Err("JPEG has no start-of-frame dimensions".to_owned())
}

fn is_start_of_frame(marker: u8) -> bool {
    matches!(
        marker,
        0xc0..=0xc3 | 0xc5..=0xc7 | 0xc9..=0xcb | 0xcd..=0xcf
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn jpeg(width: u16, height: u16) -> Vec<u8> {
        let [height_high, height_low] = height.to_be_bytes();
        let [width_high, width_low] = width.to_be_bytes();
        vec![
            0xff,
            0xd8,
            0xff,
            0xe0,
            0x00,
            0x04,
            0x00,
            0x00,
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
            0x00,
            0xff,
            0xd9,
        ]
    }

    #[test]
    fn accepts_expected_dimensions_without_decoding() {
        assert!(validate_jpeg(&jpeg(FRAME_WIDTH, FRAME_HEIGHT)).is_ok());
    }

    #[test]
    fn rejects_wrong_dimensions_and_markers() {
        assert!(
            validate_jpeg(&jpeg(1280, 720))
                .unwrap_err()
                .contains("dimensions")
        );
        assert!(
            validate_jpeg(&[0xff, 0xd8, 0xff, 0xd9])
                .unwrap_err()
                .contains("start-of-frame")
        );
        assert!(validate_jpeg(&[0, 1, 2, 3]).unwrap_err().contains("SOI"));
    }

    #[test]
    fn rejects_oversized_frame_before_parsing() {
        let frame = vec![0; MAX_FRAME_BYTES + 1];
        assert!(validate_jpeg(&frame).unwrap_err().contains("exceeds"));
    }
}
