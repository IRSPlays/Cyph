//! All HTTPS to the Cyph server goes through here: a client that trusts only the pinned certificate.

use std::time::Duration;

use serde::Serialize;
use serde_json::Value;

/// `host` is "name-or-ip:port" exactly as in the pair link.
#[derive(Debug, Clone)]
pub struct Server {
    pub host: String,
    pub fp: String,
}

#[derive(Debug, Serialize)]
pub struct ApiError {
    /// HTTP status, or 0 for a network/TLS/encoding failure.
    pub status: u16,
    pub detail: String,
}

impl ApiError {
    pub fn net(e: impl std::fmt::Display) -> Self {
        ApiError { status: 0, detail: e.to_string() }
    }
}

pub fn client(fp: &str) -> Result<reqwest::Client, String> {
    let cfg = crate::pin::client_config(fp)?;
    reqwest::Client::builder()
        .tls_backend_preconfigured(cfg)
        .https_only(true)
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| e.to_string())
}

/// One camera frame from the arm: a JPEG, the server's sequence number for it, and how old it was at the server.
pub struct Frame {
    pub seq: u64,
    pub age_ms: u64,
    pub jpeg: Vec<u8>,
}

/// The next camera frame newer than `after`. The server holds the request open for up to a second and a half, which is
/// also what asks the arm's agent to send frames. `None` when the camera sent nothing in that time.
pub async fn frame(http: &reqwest::Client, server: &Server, token: &str, after: u64) -> Result<Option<Frame>, ApiError> {
    let resp = http
        .get(format!("https://{}/arm/frame?after={after}&wait=1.5", server.host))
        .bearer_auth(token)
        .send()
        .await
        .map_err(ApiError::net)?;
    let status = resp.status();
    if status.as_u16() == 204 {
        return Ok(None);
    }
    if !status.is_success() {
        return Err(ApiError { status: status.as_u16(), detail: status.to_string() });
    }
    let num = |name: &str| resp.headers().get(name).and_then(|v| v.to_str().ok()).and_then(|v| v.parse::<u64>().ok()).unwrap_or(0);
    let (seq, age_ms) = (num("x-seq"), num("x-age-ms"));
    let jpeg = resp.bytes().await.map_err(ApiError::net)?.to_vec();
    Ok(Some(Frame { seq, age_ms, jpeg }))
}

/// Standard base64, for putting a JPEG into a `data:` URL (the page's CSP allows `img-src data:` and nothing remote).
pub fn base64(data: &[u8]) -> String {
    const T: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity(data.len().div_ceil(3) * 4);
    for c in data.chunks(3) {
        let n = (c[0] as u32) << 16 | (*c.get(1).unwrap_or(&0) as u32) << 8 | *c.get(2).unwrap_or(&0) as u32;
        out.push(T[(n >> 18) as usize & 63] as char);
        out.push(T[(n >> 12) as usize & 63] as char);
        out.push(if c.len() > 1 { T[(n >> 6) as usize & 63] as char } else { '=' });
        out.push(if c.len() > 2 { T[n as usize & 63] as char } else { '=' });
    }
    out
}

pub async fn request(
    server: &Server,
    method: &str,
    path: &str,
    token: Option<&str>,
    body: Option<Value>,
) -> Result<Value, ApiError> {
    let http = client(&server.fp).map_err(ApiError::net)?;
    let method = reqwest::Method::from_bytes(method.as_bytes()).map_err(ApiError::net)?;
    let mut req = http.request(method, format!("https://{}{}", server.host, path));
    if let Some(token) = token {
        req = req.bearer_auth(token);
    }
    if let Some(body) = body {
        req = req.json(&body);
    }
    let resp = req.send().await.map_err(ApiError::net)?;
    let status = resp.status();
    let text = resp.text().await.map_err(ApiError::net)?;
    let json: Value = serde_json::from_str(&text).unwrap_or(Value::Null);
    if status.is_success() {
        return Ok(json);
    }
    let detail = json.get("detail").and_then(|d| d.as_str()).map(String::from).unwrap_or_else(|| status.to_string());
    Err(ApiError { status: status.as_u16(), detail })
}

#[cfg(test)]
mod tests {
    use super::base64;

    #[test]
    fn base64_matches_the_standard_vectors() {
        assert_eq!(base64(b""), "");
        assert_eq!(base64(b"f"), "Zg==");
        assert_eq!(base64(b"fo"), "Zm8=");
        assert_eq!(base64(b"foo"), "Zm9v");
        assert_eq!(base64(b"foobar"), "Zm9vYmFy");
        assert_eq!(base64(&[0xff, 0xd8, 0xff]), "/9j/");
    }
}
