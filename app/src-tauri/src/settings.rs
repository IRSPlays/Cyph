//! What the app remembers between runs. v1 keeps the device token in this file in the per-user app data folder
//! (not encrypted); moving it to the OS keystore is a recorded follow-up.

use std::path::Path;

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Settings {
    pub host: String,
    pub fp: String,
    pub token: Option<String>,
    pub device_name: String,
    #[serde(default)]
    pub device_id: String,
}

/// What the UI may see: never the token. The fingerprint is the server certificate's public hash (shown in Settings and
/// needed to build a pair link for another device); it is not a secret.
#[derive(Debug, Serialize)]
pub struct Public {
    pub paired: bool,
    pub host: String,
    pub device_name: String,
    pub fp: String,
    pub device_id: String,
}

impl Settings {
    pub fn load(path: &Path) -> Settings {
        std::fs::read_to_string(path).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or_default()
    }

    pub fn save(&self, path: &Path) -> Result<(), String> {
        let tmp = path.with_extension("tmp");
        std::fs::write(&tmp, serde_json::to_string_pretty(self).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
        std::fs::rename(&tmp, path).map_err(|e| e.to_string())
    }

    pub fn paired(&self) -> bool {
        self.token.is_some() && !self.host.is_empty() && !self.fp.is_empty()
    }

    pub fn public(&self) -> Public {
        Public { paired: self.paired(), host: self.host.clone(), device_name: self.device_name.clone(), fp: self.fp.clone(), device_id: self.device_id.clone() }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trip_and_defaults() {
        let dir = std::env::temp_dir().join(format!("cyph-settings-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("settings.json");
        assert!(!Settings::load(&path).paired(), "a missing file means not paired");
        let s = Settings { host: "h:1".into(), fp: "f".into(), token: Some("t".into()), device_name: "PC".into(), device_id: "d".into() };
        s.save(&path).unwrap();
        let back = Settings::load(&path);
        assert!(back.paired());
        assert_eq!((back.host.as_str(), back.device_name.as_str()), ("h:1", "PC"));
        std::fs::write(&path, "{broken").unwrap();
        assert!(!Settings::load(&path).paired(), "a corrupt file means not paired, never a crash");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn the_token_never_leaves_in_the_public_view() {
        let s = Settings { host: "h:1".into(), fp: "f".into(), token: Some("secret".into()), device_name: "PC".into(), device_id: "d".into() };
        let json = serde_json::to_string(&s.public()).unwrap();
        assert!(!json.contains("secret"));
        assert!(json.contains("\"paired\":true"));
    }
}
