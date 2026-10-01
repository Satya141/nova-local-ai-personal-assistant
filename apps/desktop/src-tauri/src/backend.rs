//! Starts and supervises the Python backend.
//!
//! The shell picks a free loopback port and a random token, hands both to the
//! backend through its environment, and gives the same pair to the webview.
//! Nothing else on the machine learns the token, so nothing else can drive the agent.

use std::{
  fs::{self, File},
  net::TcpListener,
  path::{Path, PathBuf},
  process::{Child, Command, Stdio},
  sync::Mutex,
};

pub struct Backend {
  pub port: u16,
  pub token: String,
  child: Mutex<Option<Child>>,
  error: Mutex<Option<String>>,
}

impl Backend {
  pub fn new() -> Self {
    Self {
      port: free_port(),
      token: random_token(),
      child: Mutex::new(None),
      error: Mutex::new(None),
    }
  }

  pub fn url(&self) -> String {
    format!("http://127.0.0.1:{}", self.port)
  }

  /// Why the backend is not running, if it failed to start.
  pub fn error(&self) -> Option<String> {
    self.error.lock().unwrap().clone()
  }

  pub fn start(&self, log_dir: &Path, resource_dir: Option<PathBuf>) {
    match self.spawn(log_dir, resource_dir) {
      Ok(child) => {
        log::info!("backend started (pid {}) on port {}", child.id(), self.port);
        *self.child.lock().unwrap() = Some(child);
      }
      Err(message) => {
        log::error!("{message}");
        *self.error.lock().unwrap() = Some(message);
      }
    }
  }

  fn spawn(&self, log_dir: &Path, resource_dir: Option<PathBuf>) -> Result<Child, String> {
    let layout = Layout::find(resource_dir);
    if !layout.python.exists() {
      return Err(if layout.installed {
        format!("NOVA's installation is damaged: {} is missing. Reinstall NOVA.", layout.python.display())
      } else {
        format!("NOVA's backend is not set up: {} is missing. Run scripts\\setup.ps1.", layout.python.display())
      });
    }

    fs::create_dir_all(log_dir).map_err(|e| format!("Cannot create log folder: {e}"))?;
    let log = File::create(log_dir.join("backend.log")).map_err(|e| format!("Cannot create backend log: {e}"))?;
    let log_err = log.try_clone().map_err(|e| format!("Cannot create backend log: {e}"))?;

    let mut command = Command::new(&layout.python);
    command
      .args(["-m", "nova"])
      .current_dir(&layout.dir)
      .env("NOVA_API_TOKEN", &self.token)
      .env("NOVA_PORT", self.port.to_string())
      // The backend exits when this process does, even if we crash.
      .env("NOVA_PARENT_PID", std::process::id().to_string())
      .env("PYTHONUNBUFFERED", "1")
      .stdin(Stdio::null())
      .stdout(log)
      .stderr(log_err);
    if layout.installed {
      // What a checkout finds by its own layout, an installation is told.
      command
        .env("NOVA_MODELS_DIR", layout.dir.join("models"))
        .env("NOVA_PHONE_UI_DIR", layout.dir.join("phone-ui"));
    }

    #[cfg(windows)]
    {
      use std::os::windows::process::CommandExt;
      const CREATE_NO_WINDOW: u32 = 0x0800_0000;
      command.creation_flags(CREATE_NO_WINDOW);
    }

    command.spawn().map_err(|e| format!("Could not start NOVA's backend: {e}"))
  }

  pub fn stop(&self) {
    if let Some(mut child) = self.child.lock().unwrap().take() {
      let _ = child.kill();
      let _ = child.wait();
    }
  }
}

/// Where the backend and its Python are.
///
/// An installed NOVA carries them in its resources (see scripts/package.ps1):
/// ackend/python (Python's embeddable distribution, whose ._pth file points
/// at ackend/app and ackend/site-packages), ackend/models and
/// ackend/phone-ui. A development build uses the repository it was built from
/// and its virtual environment, or NOVA_BACKEND_DIR.
struct Layout {
  dir: PathBuf,
  python: PathBuf,
  installed: bool,
}

impl Layout {
  fn find(resource_dir: Option<PathBuf>) -> Self {
    if let Some(dir) = std::env::var_os("NOVA_BACKEND_DIR") {
      return Self::checkout(PathBuf::from(dir));
    }
    if let Some(resources) = resource_dir {
      let dir = resources.join("backend");
      let python = dir.join("python").join(if cfg!(windows) { "python.exe" } else { "python3" });
      if python.exists() {
        return Self { dir, python, installed: true };
      }
    }
    Self::checkout(PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../backend")))
  }

  fn checkout(dir: PathBuf) -> Self {
    let python = if cfg!(windows) {
      dir.join(".venv").join("Scripts").join("python.exe")
    } else {
      dir.join(".venv").join("bin").join("python")
    };
    Self { dir, python, installed: false }
  }
}

fn free_port() -> u16 {
  TcpListener::bind(("127.0.0.1", 0))
    .and_then(|listener| listener.local_addr())
    .map(|address| address.port())
    .unwrap_or(8765)
}

fn random_token() -> String {
  let mut bytes = [0u8; 32];
  getrandom::fill(&mut bytes).expect("the OS random number generator is unavailable");
  bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
