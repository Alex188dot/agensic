use reqwest::blocking::Client;
use serde::Serialize;
use std::env;
use std::time::Duration;

const PROTOCOL_VERSION: &str = "agensic_predict_v2";

#[derive(Default)]
struct ClientArgs {
    daemon_url: String,
    auth_token: String,
    buffer: String,
    cursor: usize,
    cwd: String,
    shell: String,
    allow_ai: bool,
    trigger_source: String,
    request_id: String,
    timeout_ms: u64,
}

#[derive(Serialize)]
struct PredictRequest<'a> {
    command_buffer: &'a str,
    cursor_position: usize,
    working_directory: &'a str,
    shell: &'a str,
    allow_ai: bool,
    trigger_source: &'a str,
    request_id: &'a str,
}

fn option_value(raw: &[String], index: &mut usize) -> Result<String, String> {
    *index += 1;
    raw.get(*index)
        .cloned()
        .ok_or_else(|| "missing client option value".to_string())
}

fn parse_args(raw: &[String]) -> Result<ClientArgs, String> {
    let mut args = ClientArgs {
        daemon_url: "http://127.0.0.1:22000".to_string(),
        shell: "zsh".to_string(),
        trigger_source: "unknown".to_string(),
        timeout_ms: 3_000,
        ..ClientArgs::default()
    };
    let mut index = usize::from(matches!(raw.first().map(String::as_str), Some("predict")));
    while index < raw.len() {
        match raw[index].as_str() {
            "--daemon-url" => args.daemon_url = option_value(raw, &mut index)?,
            "--auth-token" => args.auth_token = option_value(raw, &mut index)?,
            "--buffer" => args.buffer = option_value(raw, &mut index)?,
            "--cursor" => {
                args.cursor = option_value(raw, &mut index)?
                    .parse()
                    .map_err(|_| "invalid cursor".to_string())?
            }
            "--cwd" => args.cwd = option_value(raw, &mut index)?,
            "--shell" => args.shell = option_value(raw, &mut index)?,
            "--allow-ai" => args.allow_ai = option_value(raw, &mut index)? == "1",
            "--trigger-source" => args.trigger_source = option_value(raw, &mut index)?,
            "--request-id" => args.request_id = option_value(raw, &mut index)?,
            "--timeout-ms" => {
                args.timeout_ms = option_value(raw, &mut index)?
                    .parse()
                    .map_err(|_| "invalid timeout".to_string())?
            }
            other => return Err(format!("unknown client option: {}", other)),
        }
        index += 1;
    }
    if args.auth_token.is_empty() {
        args.auth_token = env::var("AGENSIC_AUTH_TOKEN").unwrap_or_default();
    }
    args.timeout_ms = args.timeout_ms.clamp(100, 30_000);
    Ok(args)
}

fn safe_field(value: &str) -> String {
    value
        .replace(['\r', '\n', '\x1f'], " ")
        .chars()
        .take(256)
        .collect()
}

fn error_payload(request_id: &str, code: &str) -> String {
    format!(
        "{}\nrequest_id={}\nok=0\nerror_code={}\nused_ai=0\nai_agent=\nai_provider=\nai_model=\npool=\ndisplay=\nmodes=\nkinds=\n",
        PROTOCOL_VERSION,
        safe_field(request_id),
        safe_field(code),
    )
}

pub(crate) fn run_from_env(raw: &[String]) -> Result<(), String> {
    let args = match parse_args(raw) {
        Ok(value) => value,
        Err(err) => {
            print!("{}", error_payload("", "bad_client_args"));
            return Err(err);
        }
    };
    let client = Client::builder()
        .timeout(Duration::from_millis(args.timeout_ms))
        .build()
        .map_err(|err| format!("client initialization failed: {}", err))?;
    let endpoint = format!("{}/predict-lines", args.daemon_url.trim_end_matches('/'));
    let payload = PredictRequest {
        command_buffer: &args.buffer,
        cursor_position: args.cursor,
        working_directory: &args.cwd,
        shell: &args.shell,
        allow_ai: args.allow_ai,
        trigger_source: &args.trigger_source,
        request_id: &args.request_id,
    };
    let mut request = client.post(endpoint).json(&payload);
    if !args.auth_token.is_empty() {
        request = request
            .header("Authorization", format!("Bearer {}", args.auth_token))
            .header("X-Agensic-Auth", &args.auth_token);
    }
    let response = match request.send() {
        Ok(value) => value,
        Err(err) => {
            let code = if err.is_timeout() {
                "predict_timeout"
            } else {
                "predict_error"
            };
            print!("{}", error_payload(&args.request_id, code));
            return Ok(());
        }
    };
    if !response.status().is_success() {
        print!("{}", error_payload(&args.request_id, "predict_http_error"));
        return Ok(());
    }
    match response.text() {
        Ok(body) if body.starts_with(PROTOCOL_VERSION) => print!("{}", body),
        _ => print!(
            "{}",
            error_payload(&args.request_id, "bad_response_protocol")
        ),
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{error_payload, parse_args, PROTOCOL_VERSION};

    #[test]
    fn parses_predict_arguments() {
        let raw = vec![
            "predict".to_string(),
            "--buffer".to_string(),
            "git st".to_string(),
            "--cursor".to_string(),
            "6".to_string(),
            "--allow-ai".to_string(),
            "1".to_string(),
        ];
        let args = parse_args(&raw).expect("parse client args");
        assert_eq!(args.buffer, "git st");
        assert_eq!(args.cursor, 6);
        assert!(args.allow_ai);
    }

    #[test]
    fn errors_are_valid_line_protocol() {
        let payload = error_payload("42", "predict_timeout");
        assert!(payload.starts_with(PROTOCOL_VERSION));
        assert!(payload.contains("request_id=42\n"));
        assert!(payload.contains("ok=0\n"));
    }
}
