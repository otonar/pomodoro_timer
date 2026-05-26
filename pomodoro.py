import customtkinter as ctk
import tkinter as tk
import time
import threading
import winsound
import ctypes
import ctypes.wintypes
import os
from PIL import Image, ImageDraw, ImageFont
import pystray

ctk.set_appearance_mode("dark")

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("pomodoro.timer.1")
except Exception:
    pass

MODES = {
    "work":        {"label": "集中タイム", "minutes": 25, "color": "#c8915c"},
    "short_break": {"label": "短い休憩",   "minutes": 5,  "color": "#6aaa8d"},
    "long_break":  {"label": "長い休憩",   "minutes": 15, "color": "#6f8fb4"},
}

BG         = "#0d0d0d"
SURFACE    = "#191919"
TRACK      = "#2a2a2a"
TEXT       = "#edeae1"
MUTED      = "#333333"
MUTED_TEXT = "#575757"


# ── taskbar progress (ITaskbarList3 via COM/ctypes) ───────────────────────────

class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_ulong), ("Data2", ctypes.c_ushort),
                ("Data3", ctypes.c_ushort), ("Data4", ctypes.c_ubyte * 8)]

_CLSID_TaskbarList = _GUID(0x56FDF344, 0xFD6D, 0x11D0,
    (ctypes.c_ubyte * 8)(0x95, 0x8A, 0x00, 0x60, 0x97, 0xC9, 0xA0, 0x90))
_IID_ITaskbarList3 = _GUID(0xEA1AFB91, 0x9E28, 0x4B86,
    (ctypes.c_ubyte * 8)(0x90, 0xE9, 0x9E, 0x9F, 0x8A, 0x5E, 0xEF, 0xAF))

_TBPF_NOPROGRESS = 0
_TBPF_NORMAL     = 2
_TBPF_PAUSED     = 8

class _TaskbarProgress:
    def __init__(self):
        ptr = ctypes.c_void_p()
        hr = ctypes.windll.ole32.CoCreateInstance(
            ctypes.byref(_CLSID_TaskbarList), None, 1,
            ctypes.byref(_IID_ITaskbarList3), ctypes.byref(ptr))
        if hr < 0:
            raise OSError(f"ITaskbarList3 unavailable ({hr:#010x})")
        self._p = ptr
        self._fn(3)(ptr)  # HrInit

    def _fn(self, idx, *argtypes):
        vptr = ctypes.cast(self._p, ctypes.POINTER(ctypes.c_void_p))
        vtbl = ctypes.cast(vptr[0], ctypes.POINTER(ctypes.c_void_p))
        return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtbl[idx])

    def set_value(self, hwnd, done, total):
        self._fn(9, ctypes.wintypes.HWND, ctypes.c_ulonglong, ctypes.c_ulonglong)(
            self._p, hwnd, done, total)

    def set_state(self, hwnd, flags):
        self._fn(10, ctypes.wintypes.HWND, ctypes.c_int)(self._p, hwnd, flags)


class _HUDBar:
    W   = 306
    H   = 36
    _BG  = "#1e1e1e"
    _FG  = "#f2f2f7"
    _SEC = "#8e8e93"
    _SEP = "#3a3a3c"
    _HOV = "#2c2c2e"

    def __init__(self, master):
        sw = master.winfo_screenwidth()
        x  = (sw - self.W) // 2

        self._win = tk.Toplevel(master)
        self._win.overrideredirect(True)
        self._win.wm_attributes("-topmost", True)
        self._win.wm_attributes("-alpha", 0.94)
        self._win.geometry(f"{self.W}x{self.H}+{x}+8")
        self._win.configure(bg=self._BG)

        f = tk.Frame(self._win, bg=self._BG)

        btn_kw = dict(bg=self._BG, fg=self._SEC,
                      activebackground=self._HOV, activeforeground=self._FG,
                      relief="flat", bd=0, font=("Segoe UI", 10), cursor="hand2")
        sep_kw = dict(bg=self._BG, fg=self._SEP, font=("Segoe UI", 9))

        tk.Button(f, text="↑", command=lambda: master._adjust(+1), **btn_kw).pack(side="left")
        tk.Button(f, text="↓", command=lambda: master._adjust(-1), **btn_kw).pack(side="left")
        tk.Label(f, text="│", **sep_kw).pack(side="left", padx=7)

        self._lbl = tk.Label(f, text="25:00", bg=self._BG, fg=self._FG,
                             font=("Segoe UI", 13), width=5, anchor="center")
        self._lbl.pack(side="left")

        tk.Label(f, text="│", **sep_kw).pack(side="left", padx=7)
        self._btn = tk.Button(f, text="▶", command=master._toggle, **btn_kw)
        self._btn.pack(side="left")
        tk.Button(f, text="↺", command=master._reset,         **btn_kw).pack(side="left", padx=(4, 0))
        tk.Button(f, text="□", command=master._toggle_window, **btn_kw).pack(side="left", padx=(8, 0))
        tk.Button(f, text="×", command=master._quit,          **btn_kw).pack(side="left", padx=(8, 0))

        self._accent = tk.Frame(self._win, bg=self._SEP, height=2)
        self._accent.pack(fill="x", side="bottom")

        self._win.update_idletasks()
        w = f.winfo_reqwidth() + 48
        self._win.geometry(f"{w}x{self.H}+{(sw - w) // 2}+8")
        f.place(relx=0.5, y=7, anchor="n")

        for w in [f, self._lbl]:
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>",     self._drag_move)

        self._win.bind_all("<space>", lambda e: master._toggle())
        self._win.bind_all("<r>",     lambda e: master._reset())
        self._win.bind_all("<R>",     lambda e: master._reset())
        self._win.bind_all("<Up>",    lambda e: master._adjust(+1))
        self._win.bind_all("<Down>",  lambda e: master._adjust(-1))

        master.after(150, self._apply_macos_style)

    def _apply_macos_style(self):
        try:
            hwnd = self._win.winfo_id()
            pref = ctypes.c_int(2)  # DWMWCP_ROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception:
            pass

    def _drag_start(self, e):
        self._ox = e.x_root - self._win.winfo_x()
        self._oy = e.y_root - self._win.winfo_y()

    def _drag_move(self, e):
        self._win.geometry(f"+{e.x_root - self._ox}+{e.y_root - self._oy}")

    def update(self, remaining, total, color, running):
        m, s = divmod(remaining, 60)
        self._lbl.config(text=f"{m:02d}:{s:02d}",
                         fg=color if running else self._FG)
        self._btn.config(text="⏸" if running else "▶")
        self._accent.config(bg=color if running else self._SEP)


class PomodoroTimer(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Pomodoro")
        self.geometry("400x620")
        self.resizable(False, False)
        self.configure(fg_color=BG)

        self.mode_key      = "work"
        self.is_running    = False
        self.session_count = 0
        self._remaining    = self._total()
        self._tray_icon    = None
        self._tbp          = None
        self._hwnd         = None

        self._build_ui()
        self._set_icon()
        self._hud = _HUDBar(self)
        self._draw_ring()
        self._start_tray()
        # self.after(200, self._init_taskbar)  # taskbar progress (ITaskbarList3)

        self.protocol("WM_DELETE_WINDOW", self._hide_to_tray)
        self.withdraw()

    # ── helpers ───────────────────────────────────────────────────────────

    def _total(self):
        return MODES[self.mode_key]["minutes"] * 60

    def _color(self):
        return MODES[self.mode_key]["color"]

    def _hover(self, hex_color):
        r = max(0, int(hex_color[1:3], 16) - 28)
        g = max(0, int(hex_color[3:5], 16) - 28)
        b = max(0, int(hex_color[5:7], 16) - 28)
        return f"#{r:02x}{g:02x}{b:02x}"

    # ── app icon ──────────────────────────────────────────────────────────

    def _set_icon(self):
        s = 64
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([4, 14, 60, 62], fill=(204, 51, 36, 255))
        d.ellipse([10, 18, 38, 44], fill=(224, 80, 60, 255))
        d.rectangle([29, 4, 35, 18], fill=(56, 142, 60, 255))
        d.ellipse([18, 6, 36, 20], fill=(56, 142, 60, 255))
        d.ellipse([30, 4, 50, 18], fill=(56, 142, 60, 255))
        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pomodoro.ico")
        img.save(ico_path, format="ICO")
        self.iconbitmap(ico_path)
        hwnd = self.winfo_id()
        hicon = ctypes.windll.user32.LoadImageW(
            0, ico_path, 1, 0, 0, 0x00000010 | 0x00000040)
        if hicon:
            ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 1, hicon)
            ctypes.windll.user32.SendMessageW(hwnd, 0x0080, 0, hicon)

    # ── UI construction ───────────────────────────────────────────────────

    def _build_ui(self):
        ctk.CTkLabel(
            self, text="P O M O D O R O",
            font=ctk.CTkFont(family="Segoe UI", size=9),
            text_color=MUTED_TEXT,
        ).pack(pady=(32, 0))

        self.mode_lbl = ctk.CTkLabel(
            self, text=MODES[self.mode_key]["label"],
            font=ctk.CTkFont(family="Segoe UI", size=13),
            text_color=self._color(),
        )
        self.mode_lbl.pack(pady=(14, 10))

        S = 264
        self._canvas = tk.Canvas(self, width=S, height=S,
                                 bg=BG, highlightthickness=0)
        self._canvas.pack()
        self._cx = self._cy = S // 2
        self._r = 108
        self._lw = 7

        self._canvas.create_oval(
            self._cx - self._r, self._cy - self._r,
            self._cx + self._r, self._cy + self._r,
            outline=TRACK, width=self._lw,
        )
        self._arc = self._canvas.create_arc(
            self._cx - self._r, self._cy - self._r,
            self._cx + self._r, self._cy + self._r,
            start=90, extent=0,
            style="arc", outline=self._color(), width=self._lw,
        )
        self._time_id = self._canvas.create_text(
            self._cx, self._cy,
            text="25:00",
            font=("Segoe UI", 54),
            fill=TEXT,
        )

        dot_canvas = tk.Canvas(self, width=76, height=12,
                               bg=BG, highlightthickness=0)
        dot_canvas.pack(pady=(18, 0))
        self._dot_canvas = dot_canvas
        self._dot_ids = []
        for i in range(4):
            x0 = i * 19
            oid = dot_canvas.create_rectangle(x0, 2, x0 + 10, 10,
                                              fill=MUTED, outline="")
            self._dot_ids.append(oid)

        tab_frame = ctk.CTkFrame(self, fg_color="transparent")
        tab_frame.pack(pady=(24, 0))
        self._mode_btns = {}

        for i, (key, label) in enumerate([("work", "集中"), ("short_break", "短休憩"), ("long_break", "長休憩")]):
            if i > 0:
                ctk.CTkLabel(tab_frame, text="|",
                             font=ctk.CTkFont(size=11),
                             text_color=MUTED).pack(side="left", padx=4)
            b = ctk.CTkButton(
                tab_frame, text=label,
                width=68, height=30, corner_radius=2,
                fg_color="transparent", hover_color=SURFACE,
                text_color=self._color() if key == self.mode_key else MUTED_TEXT,
                font=ctk.CTkFont(size=12),
                command=lambda k=key: self.set_mode(k),
            )
            b.pack(side="left", padx=1)
            self._mode_btns[key] = b

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(pady=(24, 0), padx=44, fill="x")

        self._start_btn = ctk.CTkButton(
            outer, text="スタート",
            height=50, corner_radius=4,
            fg_color=self._color(), hover_color=self._hover(self._color()),
            font=ctk.CTkFont(family="Segoe UI", size=15, weight="bold"),
            text_color=TEXT,
            command=self._toggle,
        )
        self._start_btn.pack(fill="x")

        self._reset_btn = ctk.CTkButton(
            self, text="↺  リセット",
            width=90, height=26, corner_radius=2,
            fg_color="transparent", hover_color=SURFACE,
            font=ctk.CTkFont(size=11), text_color=MUTED_TEXT,
            command=self._reset,
        )
        self._reset_btn.pack(pady=(10, 0))

    # ── ring rendering ────────────────────────────────────────────────────

    def _draw_ring(self):
        ratio  = self._remaining / self._total() if self._total() else 0
        extent = -360 * ratio
        self._canvas.itemconfig(self._arc, extent=extent, outline=self._color())
        m, s = divmod(self._remaining, 60)
        self._canvas.itemconfig(self._time_id, text=f"{m:02d}:{s:02d}")
        self.title(f"{m:02d}:{s:02d} — {MODES[self.mode_key]['label']}")
        self._update_tray_icon()
        # self._update_taskbar()  # taskbar progress (ITaskbarList3)
        self._hud.update(self._remaining, self._total(), self._color(), self.is_running)

    # ── system tray ───────────────────────────────────────────────────────

    def _make_tray_image(self):
        size = 64
        pad  = 5
        lw   = 5
        img  = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        d    = ImageDraw.Draw(img)

        # Dark background
        d.ellipse([0, 0, size-1, size-1], fill=(14, 14, 14, 230))

        # Track ring
        d.arc([pad, pad, size-pad-1, size-pad-1],
              start=0, end=360, fill=(50, 50, 50, 255), width=lw)

        # Progress arc (clockwise from top)
        c   = self._color()
        rgb = tuple(int(c[i:i+2], 16) for i in (1, 3, 5))
        ratio = self._remaining / self._total() if self._total() else 0
        if ratio > 0.01:
            span = int(360 * ratio)
            if span >= 358:
                d.arc([pad, pad, size-pad-1, size-pad-1],
                      start=0, end=360, fill=rgb+(255,), width=lw)
            else:
                d.arc([pad, pad, size-pad-1, size-pad-1],
                      start=270, end=270+span,
                      fill=rgb+(255,), width=lw)

        # Time text
        m, s = divmod(self._remaining, 60)
        text = f"{m}:{s:02d}"
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 14)
        except Exception:
            font = ImageFont.load_default()

        bbox = d.textbbox((0, 0), text, font=font)
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        d.text(((size-tw)//2 - bbox[0], (size-th)//2 - bbox[1]),
               text, fill=(237, 234, 225, 255), font=font)

        return img

    def _update_tray_icon(self):
        if self._tray_icon:
            self._tray_icon.icon = self._make_tray_image()
            m, s = divmod(self._remaining, 60)
            self._tray_icon.title = f"Pomodoro — {MODES[self.mode_key]['label']}  {m}:{s:02d}"

    def _start_tray(self):
        menu = pystray.Menu(
            pystray.MenuItem("スタート / 一時停止", self._tray_toggle),
            pystray.MenuItem("リセット", self._tray_reset),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("ウィンドウを表示", self._tray_show, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("終了", self._tray_quit),
        )
        self._tray_icon = pystray.Icon(
            "pomodoro", self._make_tray_image(), "Pomodoro", menu)
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _init_taskbar(self):
        try:
            self._tbp  = _TaskbarProgress()
            self._hwnd = self.winfo_id()
            self._update_taskbar()
        except Exception as e:
            with open("taskbar_error.log", "w") as f:
                f.write(str(e))

    def _update_taskbar(self):
        if not self._tbp or not self._hwnd:
            return
        total = self._total()
        try:
            state = _TBPF_NORMAL if self.is_running else _TBPF_PAUSED
            self._tbp.set_state(self._hwnd, state)
            self._tbp.set_value(self._hwnd, self._remaining, total)
            self._update_overlay()
        except Exception:
            pass

    def _update_overlay(self):
        m, s = divmod(self._remaining, 60)
        text = str(m) if s > 0 else "0"
        sz = 16
        img = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
        d   = ImageDraw.Draw(img)
        c   = tuple(int(self._color()[i:i+2], 16) for i in (1, 3, 5))
        d.ellipse([0, 0, sz-1, sz-1], fill=c + (230,))
        try:
            font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 9)
        except Exception:
            font = ImageFont.load_default()
        bb = d.textbbox((0, 0), text, font=font)
        tw, th = bb[2]-bb[0], bb[3]-bb[1]
        d.text(((sz-tw)//2 - bb[0], (sz-th)//2 - bb[1]),
               text, fill=(255, 255, 255, 255), font=font)

        ico_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_overlay.ico")
        img.save(ico_path, format="ICO")
        hicon = ctypes.windll.user32.LoadImageW(
            0, ico_path, 1, 0, 0, 0x00000010 | 0x00000040)
        if hicon:
            self._tbp._fn(18, ctypes.wintypes.HWND,
                          ctypes.c_void_p, ctypes.c_wchar_p)(
                self._tbp._p, self._hwnd, hicon,
                f"{m:02d}:{s:02d}")

    def _toggle_window(self):
        if self.winfo_viewable():
            self.withdraw()
        else:
            self.deiconify()
            self.lift()
            self.focus_force()

    def _quit(self):
        if self._tray_icon:
            self._tray_icon.stop()
        self.destroy()

    def _hide_to_tray(self):
        self.withdraw()

    def _tray_show(self, icon=None, item=None):
        self.after(0, self._do_show_window)

    def _do_show_window(self):
        self.deiconify()
        self.lift()
        self.focus_force()
        # self.after(100, self._update_taskbar)  # taskbar progress (ITaskbarList3)

    def _tray_toggle(self, icon=None, item=None):
        self.after(0, self._toggle)

    def _tray_reset(self, icon=None, item=None):
        self.after(0, self._reset)

    def _tray_quit(self, icon=None, item=None):
        if self._tray_icon:
            self._tray_icon.stop()
        self.after(0, self.destroy)

    # ── mode switching ────────────────────────────────────────────────────

    def set_mode(self, key):
        self.is_running = False
        self.mode_key   = key
        self._remaining = self._total()

        c = self._color()
        self.mode_lbl.configure(text=MODES[key]["label"], text_color=c)
        self._start_btn.configure(text="スタート",
                                  fg_color=c, hover_color=self._hover(c))
        for k, b in self._mode_btns.items():
            b.configure(text_color=c if k == key else MUTED_TEXT)
        self._draw_ring()

    # ── timer control ─────────────────────────────────────────────────────

    def _adjust(self, delta):
        if self.is_running:
            return
        MODES[self.mode_key]["minutes"] = max(1, MODES[self.mode_key]["minutes"] + delta)
        self._remaining = self._total()
        self._draw_ring()

    def _toggle(self):
        if self.is_running:
            self.is_running = False
            self._start_btn.configure(text="スタート")
        else:
            self.is_running = True
            self._start_btn.configure(text="一時停止")
            threading.Thread(target=self._tick, daemon=True).start()
        # self._update_taskbar()  # taskbar progress (ITaskbarList3)
        self._hud.update(self._remaining, self._total(), self._color(), self.is_running)

    def _tick(self):
        while self.is_running and self._remaining > 0:
            time.sleep(1)
            if self.is_running:
                self._remaining -= 1
                self.after(0, self._draw_ring)
        if self._remaining == 0:
            self.after(0, self._complete)

    def _complete(self):
        self.is_running = False
        self._start_btn.configure(text="スタート")
        try:
            winsound.PlaySound("SystemExclamation",
                               winsound.SND_ALIAS | winsound.SND_ASYNC)
        except Exception:
            self.bell()

        if self.mode_key == "work":
            self.session_count += 1
            self._refresh_dots()
            next_mode = "long_break" if self.session_count % 4 == 0 else "short_break"
        else:
            next_mode = "work"
        self.set_mode(next_mode)

    def _reset(self):
        self.is_running = False
        self._remaining = self._total()
        self._start_btn.configure(text="スタート")
        self._draw_ring()

    def _refresh_dots(self):
        filled = self.session_count % 4
        for i, oid in enumerate(self._dot_ids):
            self._dot_canvas.itemconfig(
                oid, fill=self._color() if i < filled else MUTED)


if __name__ == "__main__":
    app = PomodoroTimer()
    app.mainloop()
