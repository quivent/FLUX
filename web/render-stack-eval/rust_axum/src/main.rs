// Rust/Axum leg of the motion-atlas 3-way render-stack comparison
// (Go html/template, Jinja2/FastAPI, Rust/Axum), mirroring
// ~/Oscillihue/studio/. Serves the real motion-atlas directory exactly as
// the production FLUX server does — everything (pages, CSS, JS) under one
// shared /motion-atlas/ prefix, since they're siblings in the same
// directory and reference each other with relative paths. Not wired into
// FLUX's production server.
//
// /events uses ONE shared 200ms ticker broadcasting via tokio::sync::broadcast
// to every connection, instead of each connection running its own
// independent tokio::time::interval — the same "one source, fan out to N"
// fix applied to FLUX's real telemetry SSE endpoints (server.go), which had
// N independent nvidia-smi subprocesses instead of one shared poller. Here
// the per-connection cost was just a timer, not a subprocess, but N
// independent timers all firing in the same 200ms window is exactly the
// "thundering herd" that Tokio's work-stealing scheduler contends on at
// high concurrency — a shared broadcast removes that entirely.

use std::convert::Infallible;
use std::net::SocketAddr;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::{
    extract::State,
    response::sse::{Event, KeepAlive, Sse},
    response::{IntoResponse, Json, Redirect},
    routing::get,
    Router,
};
use futures_core::Stream;
use serde_json::json;
use tokio::sync::broadcast;
use tokio_stream::wrappers::BroadcastStream;
use tokio_stream::StreamExt;
use tower_http::services::ServeDir;

/// Real production static directory — served directly, nothing is copied.
const STATIC_DIR: &str = "/Users/jay/FLUX/web/motion-atlas";
const LISTEN_ADDR: &str = "127.0.0.1:9203";

#[derive(Clone)]
struct AppState {
    tx: broadcast::Sender<String>,
}

async fn api_health() -> impl IntoResponse {
    Json(json!({
        "service": "rust-axum-comparison",
        "status": "ok",
    }))
}

async fn root() -> impl IntoResponse {
    Redirect::temporary("/motion-atlas/")
}

async fn events(
    State(state): State<AppState>,
) -> Sse<impl Stream<Item = Result<Event, Infallible>>> {
    let rx = state.tx.subscribe();
    let stream = BroadcastStream::new(rx).filter_map(|msg| match msg {
        Ok(payload) => Some(Ok(Event::default().data(payload))),
        Err(_lagged) => None,
    });
    Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(15)).text(""))
}

/// The single shared poller backing every /events connection — mirrors
/// FLUX's runTelemetryHub/runTelemetryProcessHub pattern.
fn spawn_heartbeat_hub() -> broadcast::Sender<String> {
    let (tx, _rx) = broadcast::channel::<String>(64);
    let hub_tx = tx.clone();
    tokio::spawn(async move {
        let mut seq: u64 = 0;
        let mut interval = tokio::time::interval(Duration::from_millis(200));
        loop {
            interval.tick().await;
            seq += 1;
            let ts = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0);
            let payload = json!({ "seq": seq, "ts": ts as u64, "event": "heartbeat" }).to_string();
            let _ = hub_tx.send(payload);
        }
    });
    tx
}

#[tokio::main]
async fn main() {
    let tx = spawn_heartbeat_hub();
    let state = AppState { tx };

    let app = Router::new()
        .route("/", get(root))
        .nest_service("/motion-atlas", ServeDir::new(STATIC_DIR))
        .route("/events", get(events))
        .route("/api/health", get(api_health))
        .with_state(state);

    let addr: SocketAddr = LISTEN_ADDR.parse().expect("valid listen address");
    println!("rust-axum-comparison listening on http://{addr}");

    let listener = tokio::net::TcpListener::bind(addr)
        .await
        .expect("failed to bind listen address");
    axum::serve(listener, app.into_make_service())
        .await
        .expect("server error");
}
