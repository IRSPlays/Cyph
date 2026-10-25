//! The pair link printed by `python -m cyph.core pair`:  cyph://pair?host=<host:port>&fp=<sha256>&code=<XXXX-XXXX>

use url::Url;

#[derive(Debug, PartialEq)]
pub struct PairLink {
    pub host: String,
    pub fp: String,
    pub code: String,
}

pub fn parse(link: &str) -> Result<PairLink, String> {
    let bad = || "that is not a Cyph pair link".to_string();
    let url = Url::parse(link.trim()).map_err(|_| bad())?;
    if url.scheme() != "cyph" || url.host_str() != Some("pair") {
        return Err(bad());
    }
    let get = |key: &str| url.query_pairs().find(|(k, _)| k == key).map(|(_, v)| v.into_owned());
    let (host, fp, code) = (get("host").ok_or_else(bad)?, get("fp").ok_or_else(bad)?, get("code").ok_or_else(bad)?);
    if host.is_empty() || host.contains('/') || host.contains(' ') || code.is_empty() {
        return Err(bad());
    }
    crate::pin::normalize_fp(&fp)?;
    Ok(PairLink { host, fp, code })
}

#[cfg(test)]
mod tests {
    use super::*;

    const FP: &str = "e561069748c67beb0e05a9a0e8e5bd6a8f3a3f3d2f55c0c5b7a1b2c3d4e5f607";

    #[test]
    fn parses_a_good_link() {
        let l = parse(&format!("cyph://pair?host=abc.playit.gg:12345&fp={FP}&code=ABCD-EFGH")).unwrap();
        assert_eq!((l.host.as_str(), l.fp.as_str(), l.code.as_str()), ("abc.playit.gg:12345", FP, "ABCD-EFGH"));
    }

    #[test]
    fn rejects_bad_links() {
        for bad in [
            "".to_string(),
            "https://pair?host=a:1&fp=x&code=C".to_string(),
            format!("cyph://other?host=a:1&fp={FP}&code=C"),
            format!("cyph://pair?fp={FP}&code=C"),
            format!("cyph://pair?host=a:1&code=C"),
            format!("cyph://pair?host=a:1&fp={FP}"),
            format!("cyph://pair?host=a/b:1&fp={FP}&code=C"),
            "cyph://pair?host=a:1&fp=short&code=C".to_string(),
        ] {
            assert!(parse(&bad).is_err(), "{bad:?} should be rejected");
        }
    }

    #[test]
    fn surrounding_whitespace_is_fine() {
        assert!(parse(&format!("  cyph://pair?host=a:1&fp={FP}&code=C\n")).is_ok());
    }
}
