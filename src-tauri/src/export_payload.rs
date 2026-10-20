//! Binary exports use Tauri's raw body; text retains the JSON UTF-8 path.
use serde::Deserialize;
use serde_json::{json, Value};

pub fn error(code: &str, message: impl Into<String>) -> Value {
    json!({"code":code, "error":message.into()})
}
use tauri::{http::HeaderMap, ipc::InvokeBody};

pub struct Export {
    pub name: String,
    pub extension: String,
    pub bytes: Vec<u8>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct TextExport { name: String, extension: String, text: String }

/// Header values use encodeURIComponent so Unicode filenames remain portable across webviews.
fn decode_name(value: &str) -> Result<String, Value> {
    if value.len() > 4096 { return Err(error("export_invalid_name", "Export name is too long")); }
    let mut bytes = Vec::with_capacity(value.len());
    let mut source = value.bytes();
    while let Some(byte) = source.next() {
        if byte == b'%' {
            let high = source.next().and_then(|b| (b as char).to_digit(16));
            let low = source.next().and_then(|b| (b as char).to_digit(16));
            let (Some(high), Some(low)) = (high, low) else { return Err(error("export_invalid_name", "Invalid export filename encoding")); };
            bytes.push((high * 16 + low) as u8);
        } else { bytes.push(byte); }
    }
    String::from_utf8(bytes).map_err(|_| error("export_invalid_name", "Invalid export filename encoding"))
}

pub fn decode(body: &InvokeBody, headers: &HeaderMap, limit: usize) -> Result<Export, Value> {
    let export = match body {
        InvokeBody::Raw(bytes) => {
            if bytes.len() > limit { return Err(error("export_too_large", "Export exceeds the save limit")); }
            let header = |name| headers.get(name).and_then(|value| value.to_str().ok())
                .ok_or_else(|| error("export_invalid_payload", "Missing export metadata"));
            Export { name: decode_name(header("x-fairbeam-name")?)?,
                extension: header("x-fairbeam-extension")?.to_owned(), bytes: bytes.clone() }
        }
        InvokeBody::Json(value) => {
            let export: TextExport = serde_json::from_value(value.clone())
                .map_err(|_| error("export_invalid_payload", "Provide exactly one text export payload"))?;
            if export.text.len() > limit { return Err(error("export_too_large", "Export exceeds the save limit")); }
            Export { name:export.name, extension:export.extension, bytes:export.text.into_bytes() }
        }
    };
    Ok(export)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn headers(name: &str) -> HeaderMap {
        let mut headers = HeaderMap::new();
        headers.insert("x-fairbeam-name", name.parse().unwrap());
        headers.insert("x-fairbeam-extension", "zip".parse().unwrap());
        headers
    }
    #[test]
    fn raw_body_preserves_binary_and_unicode_filename() {
        let request = InvokeBody::Raw(vec![0,255,128]);
        let export = decode(&request, &headers("%C4%B0zmir%20%F0%9F%98%80.zip"), 3).unwrap();
        assert_eq!(export.bytes, [0,255,128]);
        assert_eq!(export.name, "İzmir 😀.zip");
        assert_eq!(export.extension, "zip");
        assert!(decode(&request, &headers("binary.zip"), 2).is_err());
        assert!(decode(&InvokeBody::Raw(vec![]), &headers("empty.zip"), 0).unwrap().bytes.is_empty());
    }
    #[test]
    fn text_keeps_utf8_bom_and_exact_limit() {
        let text = "\u{feff}İzmir 😀\n";
        let request = InvokeBody::Json(json!({"name":"report.txt","extension":"txt","text":text}));
        assert_eq!(decode(&request,&HeaderMap::new(),text.len()).unwrap().bytes,text.as_bytes());
        assert!(decode(&request,&HeaderMap::new(),text.len()-1).is_err());
    }
    #[test]
    fn rejects_legacy_arrays_ambiguous_metadata_and_bad_encoding() {
        for value in [json!({"name":"x.zip","extension":"zip","bytes":[1,2]}),
            json!({"name":"x.txt","extension":"txt","text":"x","bytes":[]}),
            json!({"name":"x.txt","extension":"txt"})] {
            assert!(decode(&InvokeBody::Json(value),&HeaderMap::new(),10).is_err());
        }
        assert!(decode(&InvokeBody::Raw(vec![]),&HeaderMap::new(),10).is_err());
        for name in ["%", "%xz.zip", "%FF.zip"] {assert!(decode(&InvokeBody::Raw(vec![]),&headers(name),10).is_err());}
        // Decoding must not sanitize or hide traversal: the name admission layer rejects it.
        assert_eq!(decode_name("..%2Fescape.zip").unwrap(),"../escape.zip");
    }
}
