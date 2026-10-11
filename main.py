import json
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from os.path import join

import arabic_reshaper
from bidi.algorithm import get_display
from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.graphics import Color, RoundedRectangle
from kivy.metrics import dp, sp
from kivy.properties import BooleanProperty, ListProperty
from kivy.storage.jsonstore import JsonStore
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.gridlayout import GridLayout
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.scrollview import ScrollView
from kivy.uix.textinput import TextInput
from kivy.utils import platform

# ---------------- settings ----------------
DEFAULT_SERVER = ""   # e.g. "https://parking.example.com" (can also be typed in the app)
PAY_LINK = "https://daramet.com/Parking?webintent&donate=50000"
BT_NAMES = ("HC-05", "HC-06", "HC", "JDY", "BT0", "HM-10", "Arduino")
FONT = "Persian.ttf"
DOOR_OPEN_SEC = 5
# ------------------------------------------

BG = (0.06, 0.08, 0.12, 1)
CARD = (0.11, 0.14, 0.20, 1)
FIELD = (0.15, 0.19, 0.27, 1)
BLUE = [0.25, 0.52, 1.0, 1]
GREEN = [0.18, 0.70, 0.40, 1]
RED = [0.86, 0.30, 0.30, 1]
AMBER = [0.98, 0.78, 0.20, 1]
PURPLE = [0.55, 0.42, 0.95, 1]
SLATE = [0.30, 0.36, 0.46, 1]
DISABLED = [0.17, 0.20, 0.26, 1]
WHITE = [1, 1, 1, 1]
DARK = [0.10, 0.10, 0.10, 1]
MUTED = [0.62, 0.68, 0.76, 1]

ERRORS = {
    "bad_username": ("نام کاربری باید ۳ تا ۳۰ حرف", "انگلیسی، عدد یا _ باشد"),
    "bad_password": ("رمز باید حداقل ۶ کاراکتر باشد",),
    "bad_email": ("ایمیل درست نیست",),
    "username_taken": ("این نام کاربری قبلاً گرفته شده",),
    "bad_credentials": ("نام کاربری یا رمز اشتباه است",),
    "too_many": ("تلاش زیاد بود", "چند دقیقه صبر کن"),
    "net": ("ارتباط با سرور برقرار نیست", "اینترنت یا آدرس سرور را چک کن"),
    "noserver": ("آدرس سرور را وارد کن",),
    "taken": ("این جا همین الان پر شد", "جای دیگری انتخاب کن"),
    "already_has": ("تو همین الان یک جای پارک داری",),
}

Window.clearcolor = BG
Window.softinput_mode = "below_target"


def fa(text):
    return get_display(arabic_reshaper.reshape(text))


def lines(*texts):
    return "\n".join(fa(t) for t in texts)


class Card(BoxLayout):
    def __init__(self, **kw):
        super().__init__(**kw)
        with self.canvas.before:
            Color(*CARD)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(20)])
        self.bind(pos=self._u, size=self._u)

    def _u(self, *a):
        self._r.pos, self._r.size = self.pos, self.size


class RButton(Button):
    bg = ListProperty(BLUE)
    fg = ListProperty(WHITE)
    keep_when_disabled = BooleanProperty(False)

    def __init__(self, **kw):
        super().__init__(**kw)
        self.background_normal = ""
        self.background_down = ""
        self.background_disabled_normal = ""
        self.background_color = (0, 0, 0, 0)
        with self.canvas.before:
            self._c = Color(*self.bg)
            self._r = RoundedRectangle(pos=self.pos, size=self.size, radius=[dp(16)])
        self.bind(pos=self._u, size=self._u, bg=self._u, fg=self._u, disabled=self._u)
        self._u()

    def _u(self, *a):
        self._r.pos, self._r.size = self.pos, self.size
        dim = self.disabled and not self.keep_when_disabled
        self._c.rgba = DISABLED if dim else self.bg
        self.color = [0.45, 0.50, 0.58, 1] if dim else self.fg


def make_label(text, size, color=WHITE, height=None):
    lb = Label(text=text, font_name=FONT, font_size=sp(size), color=color,
               halign="center", valign="middle")
    lb.bind(size=lambda w, s: setattr(w, "text_size", (s[0], None)))
    if height:
        lb.size_hint_y = None
        lb.height = dp(height)
    return lb


def make_input(hint, text="", password=False):
    return TextInput(text=text, hint_text=hint, password=password, multiline=False, write_tab=False,
                     size_hint_y=None, height=dp(54), font_name=FONT, font_size=sp(18),
                     padding=[dp(14), dp(14), dp(14), dp(10)], background_normal="",
                     background_active="", background_color=FIELD, foreground_color=WHITE,
                     hint_text_color=(0.5, 0.56, 0.66, 1), cursor_color=WHITE)


class Bluetooth:
    """Sends one character to the HC-05 module over classic Bluetooth (Android)."""

    def __init__(self):
        self.out = None

    def connect(self):
        from jnius import autoclass
        adapter = autoclass("android.bluetooth.BluetoothAdapter").getDefaultAdapter()
        uuid = autoclass("java.util.UUID").fromString("00001101-0000-1000-8000-00805F9B34FB")
        for dev in adapter.getBondedDevices().toArray():
            name = dev.getName() or ""
            if any(k.lower() in name.lower() for k in BT_NAMES):
                sock = dev.createRfcommSocketToServiceRecord(uuid)
                adapter.cancelDiscovery()
                sock.connect()
                self.out = sock.getOutputStream()
                return True
        return False

    def send(self, ch):
        try:
            if self.out is None and not self.connect():
                return False
            self.out.write(ord(ch))
            self.out.flush()
            return True
        except Exception:
            self.out = None
            return False


class ParkingApp(App):
    title = "پارکینگ من"

    # ================= start =================
    def build(self):
        if platform == "android":
            from android.permissions import request_permissions
            request_permissions([
                "android.permission.BLUETOOTH_CONNECT",
                "android.permission.BLUETOOTH_SCAN",
                "android.permission.ACCESS_FINE_LOCATION",
            ])
        self.bt = Bluetooth()
        self.store = JsonStore(join(self.user_data_dir, "parking.json"))
        a = self.store.get("auth") if self.store.exists("auth") else {}
        self.server = a.get("server", DEFAULT_SERVER)
        self.token = a.get("token")
        self.username = a.get("username", "")
        self.sel = None
        self.spot = None
        self.end = 0.0
        self.state = "select"  # select / enter / parked / exit
        self.pending = None
        self.offset = 0.0
        self.taken = [False] * 6
        self.flash = ([], 0)
        self.net_ok = True
        self.busy = False
        self.screen = None

        self.holder = BoxLayout()
        Clock.schedule_interval(lambda dt: self.refresh(), 5)
        Clock.schedule_interval(lambda dt: self.tick(), 1)
        if self.token and self.server:
            self.show_main()
        else:
            self.show_auth()
        return self.holder

    # ================= network =================
    def api(self, path, data=None, cb=None, auth=True):
        if not self.server:
            if cb:
                cb({"ok": False, "reason": "noserver"})
            return
        server, token = self.server, (self.token if auth else None)

        def work():
            try:
                body = json.dumps(data).encode() if data is not None else None
                headers = {"Content-Type": "application/json"}
                if token:
                    headers["Authorization"] = "Bearer " + token
                req = urllib.request.Request(server + path, data=body, headers=headers)
                with urllib.request.urlopen(req, timeout=10) as r:
                    res = json.loads(r.read())
                    res["_code"] = r.status
            except urllib.error.HTTPError as e:
                try:
                    res = json.loads(e.read())
                except Exception:
                    res = {}
                res["_code"] = e.code
                res.setdefault("ok", False)
            except Exception:
                res = {"ok": False, "reason": "net", "_code": 0}

            def done(dt):
                if auth and res.get("_code") == 401:
                    if self.token:
                        self.logout("نشست تمام شد، دوباره وارد شو")
                    return
                if cb:
                    cb(res)
            Clock.schedule_once(done)
        threading.Thread(target=work, daemon=True).start()

    def now(self):
        return time.time() + self.offset

    # ================= login / register screen =================
    def show_auth(self, message=""):
        self.screen = "auth"
        self.register_mode = False
        self.holder.clear_widgets()
        scroll = ScrollView(do_scroll_x=False)
        body = BoxLayout(orientation="vertical", size_hint_y=None, padding=[dp(20), dp(30)], spacing=dp(12))
        body.bind(minimum_height=body.setter("height"))
        body.add_widget(make_label(fa("پارکینگ من"), 34, WHITE, 70))
        self.auth_title = make_label(fa("ورود به حساب"), 20, MUTED, 34)
        body.add_widget(self.auth_title)
        self.f_server = make_input("Server  https://...", self.server)
        self.f_user = make_input("Username")
        self.f_pass = make_input("Password", password=True)
        self.f_mail = make_input("Email")
        for w in (self.f_server, self.f_user, self.f_pass, self.f_mail):
            body.add_widget(w)
        self.f_mail.opacity, self.f_mail.disabled, self.f_mail.height = 0, True, 0
        self.auth_msg = make_label(lines(message) if message else "", 16, AMBER, 60)
        body.add_widget(self.auth_msg)
        self.auth_btn = RButton(text=fa("ورود"), font_name=FONT, font_size=sp(22), size_hint_y=None,
                                height=dp(60), bg=BLUE)
        self.auth_btn.bind(on_press=lambda _b: self.submit_auth())
        body.add_widget(self.auth_btn)
        self.switch_btn = RButton(text=fa("حساب ندارم، ثبت نام"), font_name=FONT, font_size=sp(18),
                                  size_hint_y=None, height=dp(52), bg=SLATE)
        self.switch_btn.bind(on_press=lambda _b: self.toggle_mode())
        body.add_widget(self.switch_btn)
        scroll.add_widget(body)
        self.holder.add_widget(scroll)

    def toggle_mode(self):
        self.register_mode = not self.register_mode
        r = self.register_mode
        self.f_mail.opacity, self.f_mail.disabled = (1, False) if r else (0, True)
        self.f_mail.height = dp(54) if r else 0
        self.auth_title.text = fa("ساخت حساب جدید" if r else "ورود به حساب")
        self.auth_btn.text = fa("ثبت نام" if r else "ورود")
        self.switch_btn.text = fa("حساب دارم، ورود" if r else "حساب ندارم، ثبت نام")
        self.auth_msg.text = ""

    def submit_auth(self):
        url = self.f_server.text.strip().rstrip("/")
        if url and not url.startswith("http"):
            url = "https://" + url
        self.server = url
        user, pw = self.f_user.text.strip(), self.f_pass.text
        if not user or not pw:
            self.auth_msg.text = lines("نام کاربری و رمز را وارد کن")
            return
        data = {"username": user, "password": pw}
        path = "/login"
        if self.register_mode:
            path = "/register"
            data["email"] = self.f_mail.text.strip()
        self.auth_btn.disabled = True
        self.auth_msg.text = lines("لطفاً صبر کن...")
        self.api(path, data, self.after_auth, auth=False)

    def after_auth(self, res):
        self.auth_btn.disabled = False
        if res.get("ok"):
            self.token, self.username = res["token"], res["username"]
            self.store.put("auth", server=self.server, token=self.token, username=self.username)
            self.show_main()
        else:
            self.auth_msg.text = lines(*ERRORS.get(res.get("reason"), ("خطا، دوباره امتحان کن",)))

    def logout(self, message=""):
        if self.token and self.server:
            try:
                self.api("/logout", {}, None)
            except Exception:
                pass
        self.token = None
        self.store.put("auth", server=self.server, token=None, username=self.username)
        self.sel = self.spot = None
        self.state, self.pending, self.busy = "select", None, False
        self.show_auth(message)

    # ================= main screen =================
    def show_main(self):
        self.screen = "main"
        self.holder.clear_widgets()
        root = BoxLayout(orientation="vertical")

        header = BoxLayout(size_hint_y=None, height=dp(68), padding=[dp(12), dp(10)], spacing=dp(8))
        header.add_widget(BoxLayout(size_hint_x=None, width=dp(48)))
        header.add_widget(make_label(fa("پارکینگ من"), 28))
        gear = RButton(text="\u2699", font_name=FONT, font_size=sp(26),
                       size_hint=(None, None), size=(dp(48), dp(48)), bg=SLATE)
        gear.bind(on_press=lambda _b: self.open_settings())
        header.add_widget(gear)
        root.add_widget(header)

        scroll = ScrollView(do_scroll_x=False)
        body = BoxLayout(orientation="vertical", size_hint_y=None,
                         padding=[dp(16), dp(4), dp(16), dp(20)], spacing=dp(14))
        body.bind(minimum_height=body.setter("height"))

        card = Card(orientation="vertical", size_hint_y=None, height=dp(150), padding=dp(12), spacing=dp(2))
        self.status = make_label("", 19)
        self.timer = make_label("", 42, AMBER)
        card.add_widget(self.status)
        card.add_widget(self.timer)
        body.add_widget(card)

        body.add_widget(make_label(fa("جای پارک را انتخاب کن"), 17, MUTED, 28))
        grid = GridLayout(cols=3, spacing=dp(10), size_hint_y=None, height=dp(80) * 2 + dp(10))
        self.spot_btns = []
        for n in range(1, 7):
            b = RButton(text=str(n), font_name=FONT, font_size=sp(32), keep_when_disabled=True)
            b.bind(on_press=lambda _b, n=n: self.choose(n))
            grid.add_widget(b)
            self.spot_btns.append(b)
        body.add_widget(grid)

        self.pay_btn = self.mk(body, "پرداخت", self.on_pay, BLUE)
        self.paid_btn = self.mk(body, "پرداخت کردم", self.on_paid, GREEN)
        self.door_btn = self.mk(body, "باز کردن درب", self.on_door, PURPLE)
        self.ext_btn = self.mk(body, "تمدید یک ساعت", self.on_extend, SLATE)

        scroll.add_widget(body)
        root.add_widget(scroll)
        self.holder.add_widget(root)
        self.refresh()
        self.update_ui()

    def mk(self, body, text, cb, color):
        b = RButton(text=fa(text), font_name=FONT, font_size=sp(22), size_hint_y=None,
                    height=dp(60), bg=color)
        b.bind(on_press=lambda _b: cb())
        body.add_widget(b)
        return b

    def open_settings(self):
        box = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))
        box.add_widget(make_label(fa("حساب کاربری"), 20, WHITE, 32))
        box.add_widget(make_label(self.username, 18, AMBER, 28))
        out = RButton(text=fa("خروج از حساب"), font_name=FONT, font_size=sp(20), size_hint_y=None,
                      height=dp(54), bg=RED)
        box.add_widget(out)
        pop = Popup(title="", separator_height=0, content=box, size_hint=(0.92, None), height=dp(250),
                    background="", background_color=CARD)

        def do_out(_b):
            pop.dismiss()
            self.logout()
        out.bind(on_press=do_out)
        pop.open()

    # ================= spots =================
    def refresh(self):
        if self.screen == "main":
            self.api("/spots", cb=self.on_spots)

    def on_spots(self, res):
        if "taken" not in res:
            if res.get("reason") == "net" and self.net_ok:
                self.net_ok = False
                self.say_flash(*ERRORS["net"])
            return
        self.net_ok = True
        self.offset = res["now"] - time.time()
        self.taken = res["taken"]
        if not self.busy:
            m = res.get("mine")
            if m:
                self.spot, self.end = m["spot"], m["ends_at"]
                if not m["entered"]:
                    self.state = "enter"
                elif self.now() >= self.end:
                    self.state = "exit"
                else:
                    self.state = "parked"
                self.pending = None if self.pending == "reserve" else self.pending
            else:
                self.spot = None
                self.state = "select"
        self.update_ui()

    def choose(self, n):
        if self.state == "select":
            self.sel = n
            self.update_ui()

    def say_flash(self, *texts):
        self.flash = (texts, time.time() + 4)
        self.update_ui()

    # ================= actions =================
    def on_pay(self):
        if self.sel is None:
            return self.say_flash("اول جای پارک را انتخاب کن")
        self.pending = "reserve"
        webbrowser.open(PAY_LINK)
        self.update_ui()

    def on_extend(self):
        self.pending = "extend"
        webbrowser.open(PAY_LINK)
        self.update_ui()

    def on_paid(self):
        self.busy = True
        if self.pending == "reserve":
            self.api("/reserve", {"spot": self.sel}, self.after_reserve)
        elif self.pending == "extend":
            self.api("/extend", {}, self.after_extend)

    def after_reserve(self, res):
        self.busy = False
        if res.get("ok"):
            self.spot, self.end, self.state, self.pending = res["spot"], res["ends_at"], "enter", None
        else:
            self.pending = None if res.get("reason") in ("taken", "already_has") else self.pending
            self.say_flash(*ERRORS.get(res.get("reason"), ("خطا، دوباره امتحان کن",)))
        self.refresh()
        self.update_ui()

    def after_extend(self, res):
        self.busy = False
        if res.get("ok"):
            self.end, self.pending = res["ends_at"], None
            if self.state == "exit":
                self.state = "parked"
        else:
            self.say_flash(*ERRORS.get(res.get("reason"), ("خطا، دوباره امتحان کن",)))
        self.update_ui()

    def on_door(self):
        if self.state not in ("enter", "exit") or self.busy:
            return
        self.busy = True

        def work():
            ok = self.bt.send("1")
            if ok:
                time.sleep(DOOR_OPEN_SEC)
                self.bt.send("0")
            Clock.schedule_once(lambda dt: self.after_door(ok))
        threading.Thread(target=work, daemon=True).start()

    def after_door(self, ok):
        if not ok:
            self.busy = False
            return self.say_flash("اتصال بلوتوث برقرار نشد", "ماژول را در تنظیمات گوشی جفت کن")
        if self.state == "enter":
            self.api("/entered", {}, self.after_state_change)
        else:
            self.api("/release", {}, self.after_state_change)

    def after_state_change(self, res):
        self.busy = False
        if res.get("ok"):
            if self.state == "enter":
                self.state = "parked"
            else:
                self.spot, self.sel, self.state = None, None, "select"
        else:
            self.say_flash(*ERRORS.get(res.get("reason"), ("خطا در ارتباط با سرور",)))
        self.refresh()
        self.update_ui()

    def tick(self):
        if self.screen != "main":
            return
        if self.state == "parked" and self.now() >= self.end:
            self.state = "exit"
        self.update_ui()

    # ================= UI =================
    def update_ui(self):
        if self.screen != "main":
            return
        for i, b in enumerate(self.spot_btns):
            n = i + 1
            taken = self.taken[i] and n != self.spot
            b.disabled = taken or self.state != "select"
            b.fg = WHITE
            if n == self.spot:
                b.bg = BLUE
            elif taken:
                b.bg = RED
            elif n == self.sel:
                b.bg, b.fg = AMBER, DARK
            else:
                b.bg = GREEN

        s = self.state
        self.pay_btn.disabled = not (s == "select"