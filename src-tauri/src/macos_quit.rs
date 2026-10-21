//! AppKit termination (Dock Quit, logout, restart) must use the same consent as Cmd+Q.
//!
//! tao 0.35.3's delegate has applicationWillTerminate:, but not applicationShouldTerminate:.
//! Add only the missing method to that delegate's class; never replace an existing implementation.
//! NSTerminateLater keeps the OS request open until the page answers. Replying NO cancels logout;
//! replying YES follows tao's normal applicationWillTerminate -> RunEvent::Exit cleanup.
use std::ffi::{c_char, c_void};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::OnceLock;
use tauri::{AppHandle, Manager};

type Id = *mut c_void;
type Sel = *const c_void;
// Objective-C BOOL is a one-byte bool on arm64 and signed char on x86_64.
type Bool = i8;

#[link(name = "objc")]
extern "C" {
    fn objc_getClass(name: *const c_char) -> Id;
    fn object_getClass(object: Id) -> Id;
    fn sel_registerName(name: *const c_char) -> Sel;
    fn class_getInstanceMethod(class: Id, selector: Sel) -> Id;
    fn class_addMethod(class: Id, selector: Sel, implementation: *const c_void, types: *const c_char) -> Bool;
    fn objc_msgSend();
}
#[link(name = "AppKit", kind = "framework")]
extern "C" {}

static APP: OnceLock<AppHandle> = OnceLock::new();
static PENDING: AtomicBool = AtomicBool::new(false);

unsafe fn application() -> Id {
    let send: unsafe extern "C" fn(Id, Sel) -> Id = std::mem::transmute(objc_msgSend as unsafe extern "C" fn());
    send(objc_getClass(c"NSApplication".as_ptr()), sel_registerName(c"sharedApplication".as_ptr()))
}

extern "C" fn should_terminate(_: Id, _: Sel, _: Id) -> usize {
    // An Objective-C boundary must never unwind. Failure cancels, never approves an exit.
    std::panic::catch_unwind(|| {
        let Some(app) = APP.get() else { return 0; }; // NSTerminateCancel
        if super::update_committing(app) { return 0; }
        if !PENDING.swap(true, Ordering::SeqCst) {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.show();
                let _ = window.set_focus();
            }
            super::request_leave(app, super::Leave::Quit);
        }
        // Repeated Dock/system requests share this one pending AppKit reply. They must not
        // turn a pending save or run question into the menu's hung-page second-quit fallback.
        2 // NSTerminateLater: reply only once, when all save/run questions settle.
    }).unwrap_or_else(|_| { PENDING.store(false, Ordering::SeqCst); 0 })
}

/// Called from Tauri setup on the AppKit thread, before accepting external quit events.
pub fn install(app: &AppHandle) -> Result<(), String> {
    // SAFETY: this runs on the main thread; selectors and signatures are AppKit's documented
    // NSApplication/delegate methods. NSUInteger is pointer-sized, Q on the supported 64-bit Macs.
    unsafe {
        let application = application();
        let send: unsafe extern "C" fn(Id, Sel) -> Id = std::mem::transmute(objc_msgSend as unsafe extern "C" fn());
        let delegate = send(application, sel_registerName(c"delegate".as_ptr()));
        if delegate.is_null() { return Err("macOS quit guard: missing application delegate".into()); }
        let class = object_getClass(delegate);
        let selector = sel_registerName(c"applicationShouldTerminate:".as_ptr());
        if !class_getInstanceMethod(class, selector).is_null() {
            return Err("macOS quit guard: applicationShouldTerminate already exists; review its consent behavior".into());
        }
        APP.set(app.clone()).map_err(|_| "macOS quit guard was installed twice")?;
        if class_addMethod(class, selector, should_terminate as *const c_void, c"Q@:@".as_ptr()) == 0 {
            return Err("macOS quit guard: could not register applicationShouldTerminate".into());
        }
        // Refresh AppKit's optional delegate-method cache after adding the selector. tao keeps
        // a strong IdRef to this delegate for the event loop's lifetime; this setup-time reset
        // does not replace it or release its owner.
        let set_delegate: unsafe extern "C" fn(Id, Sel, Id) = std::mem::transmute(objc_msgSend as unsafe extern "C" fn());
        let selector = sel_registerName(c"setDelegate:".as_ptr());
        set_delegate(application, selector, std::ptr::null_mut());
        set_delegate(application, selector, delegate);
    }
    Ok(())
}

/// Return true if an AppKit request owns termination. Menu/window exits use the usual Tauri path.
pub fn reply(app: &AppHandle, approved: bool) -> bool {
    if !PENDING.load(Ordering::SeqCst) { return false; }
    let result = app.run_on_main_thread(move || {
        if !PENDING.swap(false, Ordering::SeqCst) { return; }
        // SAFETY: NSApplication is live on its main thread; BOOL has the ABI documented above.
        unsafe {
            let send: unsafe extern "C" fn(Id, Sel, Bool) = std::mem::transmute(objc_msgSend as unsafe extern "C" fn());
            send(application(), sel_registerName(c"replyToApplicationShouldTerminate:".as_ptr()), if approved { 1 } else { 0 });
        }
    });
    if let Err(error) = result {
        // Stay open if the reply cannot reach AppKit. Do not fall back to an unguarded exit.
        super::shelllog::write(app, &format!("macOS quit reply could not reach the main thread: {error}"));
    }
    true
}
