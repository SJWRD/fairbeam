/// Text exports travel as UTF-8 strings rather than JSON arrays of byte numbers.
/// Retain the binary payload for images and archives; never accept an ambiguous request.
pub fn decode(bytes: Option<Vec<u8>>, text: Option<String>, limit: usize) -> Result<Vec<u8>, String> {
    let data = match (bytes, text) {
        (Some(bytes), None) => bytes,
        (None, Some(text)) => text.into_bytes(),
        _ => return Err("Provide exactly one export payload".into()),
    };
    if data.len() > limit {
        return Err("Export exceeds the save limit".into());
    }
    Ok(data)
}

#[cfg(test)]
mod tests {
    use super::decode;

    #[test]
    fn preserves_text_and_binary_bytes() {
        let text = "\u{feff}İzmir 😀\n";
        assert_eq!(decode(None, Some(text.into()), 100).unwrap(), text.as_bytes());
        assert_eq!(decode(Some(vec![0, 255, 128]), None, 3).unwrap(), [0, 255, 128]);
        assert!(decode(None, Some(String::new()), 0).unwrap().is_empty());
    }

    #[test]
    fn limit_counts_utf8_bytes_and_accepts_exact_boundary() {
        assert_eq!(decode(None, Some("é".into()), 2).unwrap(), [0xc3, 0xa9]);
        assert!(decode(None, Some("é".into()), 1).is_err());
        assert!(decode(Some(vec![0; 3]), None, 2).is_err());
    }

    #[test]
    fn rejects_missing_or_ambiguous_payload_even_when_empty() {
        assert!(decode(None, None, 10).is_err());
        assert!(decode(Some(vec![]), Some(String::new()), 10).is_err());
    }
}
