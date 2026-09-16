#[tokio::main]
async fn main() {
    if let Err(error) = scrap_monitoring_simulation_server::cli::execute().await {
        eprintln!("error: {error}");
        std::process::exit(2);
    }
}
