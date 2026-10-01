mod backend;

use std::sync::Mutex;

use serde::Serialize;
use tauri::{
  image::Image,
  menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem},
  tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
  AppHandle, Emitter, LogicalSize, Manager, PhysicalPosition, RunEvent, State, WebviewWindow, WindowEvent, Wry,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt as _};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt as _, Modifiers, Shortcut, ShortcutState};

use backend::Backend;

const LAUNCHER: &str = "launcher";
const LAUNCHER_WIDTH: f64 = 720.0;
const LAUNCHER_MIN_HEIGHT: f64 = 64.0;
const LAUNCHER_MAX_HEIGHT: f64 = 600.0;
/// How far down the screen the launcher's top edge sits. It grows downward from there.
const LAUNCHER_TOP: f64 = 0.2;

/// Reminders pop up in the "toast" window: bottom-right, above the taskbar, never taking focus.
const TOAST_WIDTH: f64 = 380.0;
const TOAST_MAX_HEIGHT: f64 = 560.0;
const TOAST_MARGIN: f64 = 16.0;

const TRAY: &str = "nova";
/// Shown in the tray whenever the microphone is open, so listening is never invisible.
const TRAY_LISTENING_ICON: &[u8] = include_bytes!("../icons/tray-listening.png");

/// The shortcut that is actually registered, e.g. "Alt+Space". Empty if none could be.
struct ActiveShortcut(Mutex<String>);

/// The tray's "Listen for Hey Nova" item, kept so its tick can follow the real setting.
struct WakeWordItem(Mutex<Option<CheckMenuItem<Wry>>>);

#[derive(Serialize)]
struct Session {
  url: String,
  token: String,
  error: Option<String>,
  shortcut: String,
}

/// Everything the webview needs to talk to the backend.
#[tauri::command]
fn session(backend: State<'_, Backend>, shortcut: State<'_, ActiveShortcut>) -> Session {
  Session {
    url: backend.url(),
    token: backend.token.clone(),
    error: backend.error(),
    shortcut: shortcut.0.lock().unwrap().clone(),
  }
}

#[tauri::command]
fn hide_launcher(window: WebviewWindow) {
  let _ = window.hide();
}

/// Bring the launcher up when a voice command arrives while it is hidden.
#[tauri::command]
fn summon_launcher(app: AppHandle) {
  show_launcher(&app);
}

/// The launcher reports the voice state; the tray shows it. `listening` means the microphone is open.
#[tauri::command]
fn set_voice_indicator(
  app: AppHandle,
  wake_word: bool,
  listening: bool,
  shortcut: State<'_, ActiveShortcut>,
  item: State<'_, WakeWordItem>,
) {
  if let Some(item) = item.0.lock().unwrap().as_ref() {
    let _ = item.set_checked(wake_word);
  }
  let Some(tray) = app.tray_by_id(TRAY) else {
    return;
  };
  let icon = if listening { Image::from_bytes(TRAY_LISTENING_ICON).ok() } else { app.default_window_icon().cloned() };
  let _ = tray.set_icon(icon);
  let _ = tray.set_tooltip(Some(tray_tooltip(&shortcut.0.lock().unwrap(), listening)));
}

fn tray_tooltip(shortcut: &str, listening: bool) -> String {
  match (listening, shortcut.is_empty()) {
    (true, _) => "NOVA: microphone on, listening for \"Hey Nova\"".to_string(),
    (false, true) => "NOVA".to_string(),
    (false, false) => format!("NOVA ({shortcut})"),
  }
}

/// The webview reports its content height and the window follows it.
#[tauri::command]
fn resize_launcher(window: WebviewWindow, height: f64) {
  let height = height.clamp(LAUNCHER_MIN_HEIGHT, LAUNCHER_MAX_HEIGHT);
  let _ = window.set_size(LogicalSize::new(LAUNCHER_WIDTH, height));
}

/// Size the reminder pop-up to its content and pin it to the bottom-right of the primary screen.
/// Showing it does not activate it (the window is created unfocusable), so typing elsewhere carries on.
#[tauri::command]
fn show_toast(window: WebviewWindow, height: f64) {
  let height = height.clamp(1.0, TOAST_MAX_HEIGHT);
  let _ = window.set_size(LogicalSize::new(TOAST_WIDTH, height));
  if let Ok(Some(monitor)) = window.primary_monitor() {
    let scale = monitor.scale_factor();
    let area = monitor.work_area();
    let margin = (TOAST_MARGIN * scale) as i32;
    let x = area.position.x + area.size.width as i32 - (TOAST_WIDTH * scale) as i32 - margin;
    let y = area.position.y + area.size.height as i32 - (height * scale) as i32 - margin;
    let _ = window.set_position(PhysicalPosition::new(x, y));
  }
  let _ = window.show();
}

#[tauri::command]
fn hide_toast(window: WebviewWindow) {
  let _ = window.hide();
}

fn show_launcher(app: &AppHandle) {
  let Some(window) = app.get_webview_window(LAUNCHER) else {
    return;
  };
  // Open on whichever monitor the user is working on.
  if let (Ok(cursor), Ok(size)) = (app.cursor_position(), window.outer_size()) {
    if let Ok(Some(monitor)) = app.monitor_from_point(cursor.x, cursor.y) {
      let (origin, area) = (monitor.position(), monitor.size());
      let x = origin.x + (area.width as i32 - size.width as i32) / 2;
      let y = origin.y + (area.height as f64 * LAUNCHER_TOP) as i32;
      let _ = window.set_position(PhysicalPosition::new(x, y));
    }
  }
  let _ = window.show();
  let _ = window.set_focus();
  let _ = window.emit("launcher-shown", ());
}

fn toggle_launcher(app: &AppHandle) {
  let Some(window) = app.get_webview_window(LAUNCHER) else {
    return;
  };
  if window.is_visible().unwrap_or(false) && window.is_focused().unwrap_or(false) {
    let _ = window.hide();
  } else {
    show_launcher(app);
  }
}

/// Alt+Space, or Ctrl+Alt+Space when another app (PowerToys Run, for one) already owns it.
fn register_shortcut(app: &AppHandle) -> String {
  let candidates = [
    ("Alt+Space", Shortcut::new(Some(Modifiers::ALT), Code::Space)),
    ("Ctrl+Alt+Space", Shortcut::new(Some(Modifiers::CONTROL | Modifiers::ALT), Code::Space)),
  ];
  for (label, shortcut) in candidates {
    match app.global_shortcut().register(shortcut) {
      Ok(()) => return label.to_string(),
      Err(error) => log::warn!("could not register {label}: {error}"),
    }
  }
  String::new()
}

fn build_tray(app: &AppHandle, shortcut: &str) -> tauri::Result<()> {
  let open_label = if shortcut.is_empty() { "Open NOVA".to_string() } else { format!("Open NOVA\t{shortcut}") };
  let open = MenuItem::with_id(app, "open", open_label, true, None::<&str>)?;
  // A debug build loads the UI from the dev server, so it cannot start on its own at login.
  let autostart = CheckMenuItem::with_id(
    app,
    "autostart",
    "Start with Windows",
    !cfg!(debug_assertions),
    app.autolaunch().is_enabled().unwrap_or(false),
    None::<&str>,
  )?;
  // Unticked until the backend reports the real setting.
  let wake_word = CheckMenuItem::with_id(app, "wake_word", "Listen for \u{201c}Hey Nova\u{201d}", true, false, None::<&str>)?;
  *app.state::<WakeWordItem>().0.lock().unwrap() = Some(wake_word.clone());
  let quit = MenuItem::with_id(app, "quit", "Quit NOVA", true, None::<&str>)?;
  let menu = Menu::with_items(
    app,
    &[&open, &wake_word, &autostart, &PredefinedMenuItem::separator(app)?, &quit],
  )?;

  let mut tray = TrayIconBuilder::with_id(TRAY)
    .tooltip(tray_tooltip(shortcut, false))
    .menu(&menu)
    .show_menu_on_left_click(false)
    .on_menu_event(move |app, event| match event.id().as_ref() {
      "open" => show_launcher(app),
      // The setting lives in the backend; the launcher changes it and reports back.
      "wake_word" => {
        let _ = app.emit_to(LAUNCHER, "toggle-wake-word", ());
      }
      "autostart" => {
        let manager = app.autolaunch();
        let result = if manager.is_enabled().unwrap_or(false) { manager.disable() } else { manager.enable() };
        if let Err(error) = result {
          log::error!("could not change autostart: {error}");
        }
        // Show what is actually registered, not what was clicked.
        let _ = autostart.set_checked(manager.is_enabled().unwrap_or(false));
      }
      "quit" => app.exit(0),
      _ => {}
    })
    .on_tray_icon_event(|tray, event| {
      if let TrayIconEvent::Click { button: MouseButton::Left, button_state: MouseButtonState::Up, .. } = event {
        show_launcher(tray.app_handle());
      }
    });
  if let Some(icon) = app.default_window_icon() {
    tray = tray.icon(icon.clone());
  }
  tray.build(app)?;
  Ok(())
}

/// NOVA's own pages: the bundled UI, or the dev server in a debug build.
fn is_app_page(url: &tauri::Url) -> bool {
  match (url.scheme(), url.host_str()) {
    ("tauri", _) => true,
    ("http" | "https", Some("tauri.localhost")) => true,
    ("http", Some("localhost")) => cfg!(debug_assertions) && url.port() == Some(3000),
    _ => false,
  }
}

/// Open a web link in the user's own browser. Only http and https: a reply could contain any link.
fn open_in_browser(url: &tauri::Url) {
  if !matches!(url.scheme(), "http" | "https") {
    log::warn!("refused to open a {} link", url.scheme());
    return;
  }
  #[cfg(windows)]
  {
    // The shell's own URL handler, given the address as one argument: nothing is parsed by a shell.
    if let Err(error) = std::process::Command::new("rundll32").args(["url.dll,FileProtocolHandler", url.as_str()]).spawn() {
      log::error!("could not open a link: {error}");
    }
  }
}

/// Links in NOVA's replies must never turn the launcher into a browser window: the page would sit
/// inside NOVA looking like NOVA. Leaving NOVA's own pages opens the link in the user's browser.
fn navigation_guard() -> tauri::plugin::TauriPlugin<Wry> {
  tauri::plugin::Builder::new("navigation-guard")
    .on_navigation(|_webview, url| {
      if is_app_page(url) {
        return true;
      }
      open_in_browser(url);
      false
    })
    .build()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    // Must be first: a second launch just brings up the running instance.
    .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_launcher(app)))
    .plugin(navigation_guard())
    .plugin(tauri_plugin_log::Builder::default().level(log::LevelFilter::Info).build())
    .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, None))
    .plugin(
      tauri_plugin_global_shortcut::Builder::new()
        .with_handler(|app, _shortcut, event| {
          if event.state() == ShortcutState::Pressed {
            toggle_launcher(app);
          }
        })
        .build(),
    )
    // Managed before any window exists, so the webview's first call always finds them.
    .manage(Backend::new())
    .manage(ActiveShortcut(Mutex::new(String::new())))
    .manage(WakeWordItem(Mutex::new(None)))
    .invoke_handler(tauri::generate_handler![
      session,
      hide_launcher,
      resize_launcher,
      show_toast,
      hide_toast,
      summon_launcher,
      set_voice_indicator
    ])
    .on_window_event(|window, event| match event {
      // A launcher gets out of the way as soon as the user clicks elsewhere.
      // Reminders stay until they are dismissed.
      WindowEvent::Focused(false) if window.label() == LAUNCHER => {
        let _ = window.hide();
      }
      // Alt+F4 hides; NOVA keeps running in the tray.
      WindowEvent::CloseRequested { api, .. } => {
        api.prevent_close();
        let _ = window.hide();
      }
      _ => {}
    })
    .setup(|app| {
      let handle = app.handle();
      handle.state::<Backend>().start(&handle.path().app_log_dir()?, handle.path().resource_dir().ok());

      let shortcut = register_shortcut(handle);
      *handle.state::<ActiveShortcut>().0.lock().unwrap() = shortcut.clone();
      build_tray(handle, &shortcut)?;
      Ok(())
    })
    .build(tauri::generate_context!())
    .expect("failed to start NOVA")
    .run(|app, event| {
      if let RunEvent::Exit = event {
        app.state::<Backend>().stop();
      }
    });
}
