import json
import ssl
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
DEFAULT_SERVER = ""   # can also be set inside the app (gear button); empty = no server
PAY_LINK = "https://daramet.com/Parking?webintent&donate=50000"
BT_NAMES = ("HC-05", "HC-06", "HC", "JDY", "BT0", "HM-10", "Arduino")
FONT = "Persian.ttf"
DOOR_OPEN_SEC = 5
CAR_CM = 60          # distance below this = a car is in front of the door
# ------------------------------------------

BG = (0.06, 0.08, 0.12, 1)
CARD = (0.11, 0.14, 0.20, 1)
BLUE = [0.25, 0.52, 1.0, 1]
GREEN = [0.18, 0.70, 0.40, 1]
RED = [0.86, 0.30, 0.30, 1]
AMBER = [0.98, 0.78, 0.20, 1]
PURPLE = [0.55, 0.42, 0.95, 1]
SLATE = [0.30, 0.36, 0.46, 1]
TEAL = [0.15, 0.50, 0.58, 1]
DISABLED = [0.17, 0.20, 0.26, 1]
WHITE = [1, 1, 1, 1]
DARK = [0.10, 0.10, 0.10, 1]
MUTED = [0.62, 0.68, 0.76, 1]

Window.clearcolor = BG
Window.softinput_mode = "below_target"


def ssl_context():
    """Android has no system CA bundle for Python, so use certifi's."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return None


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


class Bluetooth:
    """HC-05 over classic Bluetooth (Android).
    Sends: 0/1 = door close/open, 2/3 = LED on/off.
    Receives lines like "D:123" (ultrasonic distance in cm)."""

    def __init__(self):
        self.out = None
        self.inp = None
        self.on_line = None
        self.lock = threading.Lock()

    def connect(self):
        with self.lock:
            if self.out is not None:
                return True
            from jnius import autoclass
            adapter = autoclass("android.bluetooth.BluetoothAdapter").getDefaultAdapter()
            uuid = autoclass("java.util.UUID").fromString("00001101-0000-1000-8000-00805F9B34FB")
            for dev in adapter.getBondedDevices().toArray():
                name = dev.getName() or ""
                if any(k.lower() in name.lower() for k in BT_NAMES):
                    sock = dev.createRfcommSocketToServiceRecord(uuid)
                    adapter.cancelDiscovery()
                    sock.connect()
                    self.inp = sock.getInputStream()
                    self.out = sock.getOutputStream()
                    threading.Thread(target=self._reader, args=(self.inp,), daemon=True).start()
                    return True
            return False

    def try_connect(self):
        if self.out is not None or self.lock.locked():
            return
        try:
            self.connect()
        except Exception:
            pass

    def _reader(self, inp):
        buf = ""
        while self.inp is inp:
            try:
                if inp.available() > 0:
                    b = inp.read()
                    if b < 0:
                        break
                    ch = chr(b)
                    if ch == "\n":
                        line, buf = buf.strip(), ""
                        if line and self.on_line:
                            self.on_line(line)
                    else:
                        buf += ch
                else:
                    time.sleep(0.1)
            except Exception:
                break
        if self.inp is inp:
            self.inp = None
            self.out = None

    def send(self, ch):
        try:
            if self.out is None and not self.connect():
                return False
            self.out.write(ord(ch))
            self.out.flush()
            return True
        except Exception:
            self.out = None
            self.inp = None
            return False


class ParkingApp(App):
    title = "پارکینگ من"

    def build(self):
        if platform == "android":
            from android.permissions import request_permissions
            request_permissions([
                "android.permission.BLUETOOTH_CONNECT",
                "android.permission.BLUETOOTH_SCAN",
                "android.permission.ACCESS_FINE_LOCATION",
            ])
        self.bt = Bluetooth()
        self.bt.on_line = lambda line: Clock.schedule_once(lambda dt: self.on_bt_line(line))
        self.distance = None
        self.dist_time = 0.0
        self.last_post = 0.0
        self.led = False
        self.store = JsonStore(join(self.user_data_dir, "parking.json"))
        self.server = self.store.get("cfg")["server"] if self.store.exists("cfg") else DEFAULT_SERVER
        self.sel = None
        self.spot = None
        self.end = 0.0
        self.state = "select"  # select / enter / parked / exit
        if self.store.exists("me"):
            me = self.store.get("me")
            self.spot, self.end, self.state = me["spot"], me["end"], me["state"]
        self.pending = None
        self.offset = 0.0
        self.ends = [0.0] * 6
        self.flash = ([], 0)
        self.net_ok = True

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
        body = BoxLayout(orientation="vertical", size_hint_y=None, padding=[dp(16), dp(4), dp(16), dp(20)],
                         spacing=dp(14))
        body.bind(minimum_height=body.setter("height"))

        card = Card(orientation="vertical", size_hint_y=None, height=dp(150), padding=dp(12), spacing=dp(2))
        self.status = make_label("", 19)
        self.timer = make_label("", 42, AMBER)
        card.add_widget(self.status)
        card.add_widget(self.timer)
        body.add_widget(card)
        self.dist = make_label("", 15, MUTED, 26)
        body.add_widget(self.dist)

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
        self.led_btn = self.mk(body, "روشن کردن چراغ", self.on_led, TEAL)

        scroll.add_widget(body)
        root.add_widget(scroll)

        Clock.schedule_interval(lambda dt: self.refresh(), 5)
        Clock.schedule_interval(lambda dt: self.tick(), 1)
        Clock.schedule_interval(lambda dt: self.bt_keepalive(), 10)
        self.refresh()
        self.update_ui()
        return root

    def mk(self, body, text, cb, color):
        b = RButton(text=fa(text), font_name=FONT, font_size=sp(22), size_hint_y=None,
                    height=dp(60), bg=color)
        b.bind(on_press=lambda _b: cb())
        body.add_widget(b)
        return b

    # ---------- settings ----------
    def open_settings(self):
        box = BoxLayout(orientation="vertical", padding=dp(14), spacing=dp(10))
        box.add_widget(make_label(fa("آدرس سرور"), 20, WHITE, 32))
        box.add_widget(make_label(fa("اگر خالی بماند، بدون سرور کار می‌کند"), 14, MUTED, 24))
        ti = TextInput(text=self.server, multiline=False, hint_text="https://name.pythonanywhere.com",
                       font_size=sp(16), size_hint_y=None, height=dp(50), write_tab=False)
        box.add_widget(ti)
        save = RButton(text=fa("ذخیره"), font_name=FONT, font_size=sp(20), size_hint_y=None,
                       height=dp(54), bg=GREEN)
        box.add_widget(save)
        pop = Popup(title="", separator_height=0, content=box, size_hint=(0.92, None), height=dp(290),
                    background="", background_color=CARD, auto_dismiss=True)

        def do_save(_b):
            url = ti.text.strip().rstrip("/")
            if url and not url.startswith("http"):
                url = "https://" + url
            self.server = url
            self.store.put("cfg", server=url)
            pop.dismiss()
            self.net_ok = True
            self.refresh()
        save.bind(on_press=do_save)
        pop.open()

    # ---------- helpers ----------
    def now(self):
        return time.time() + self.offset

    def save(self):
        self.store.put("me", spot=self.spot, end=self.end, state=self.state)

    def say_flash(self, *texts):
        self.flash = (texts, time.time() + 4)
        self.update_ui()

    def local(self, path, data):
        """Works without a server: spots are saved on this phone."""
        ends = self.store.get("spots")["ends"] if self.store.exists("spots") else [0.0] * 6
        now = time.time()
        i = int((data or {}).get("spot") or 0) - 1
        res = {"ok": True}
        if path == "/spots":
            return {"now": now, "ends": ends}
        if not 0 <= i < 6:
            return {"ok": False}
        if path == "/reserve":
            if ends[i] > now:
                return {"ok": False, "reason": "taken"}
            ends[i] = now + 3600
        elif path == "/extend":
            ends[i] = max(ends[i], now) + 3600
        elif path == "/release":
            ends[i] = 0.0
        res["end"] = ends[i]
        self.store.put("spots", ends=ends)
        return res

    def api(self, path, data=None, cb=None):
        if not self.server:
            res = self.local(path, data)
            if cb:
                Clock.schedule_once(lambda dt: cb(res))
            return
        server = self.server

        def work():
            try:
                body = json.dumps(data).encode() if data is not None else None
                req = urllib.request.Request(
                    server + path, data=body, headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10, context=ssl_context()) as r:
                    res = json.loads(r.read())
            except urllib.error.HTTPError as e:
                try:
                    res = json.loads(e.read())
                except Exception:
                    res = {"ok": False}
            except Exception as e:
                res = {"ok": False, "reason": "net", "err": f"{type(e).__name__}: {e}"}
            if cb:
                Clock.schedule_once(lambda dt: cb(res))
        threading.Thread(target=work, daemon=True).start()

    def refresh(self):
        self.api("/spots", cb=self.on_spots)

    def on_spots(self, res):
        if "ends" in res:
            self.net_ok = True
            self.offset = res["now"] - time.time()
            self.ends = res["ends"]
            self.update_ui()
        elif res.get("reason") == "net" and self.net_ok:
            self.net_ok = False
            self.say_flash("ارتباط با سرور برقرار نیست", "اینترنت یا آدرس سرور را چک کن", res.get("err", "")[:80])

    def choose(self, n):
        if self.state == "select":
            self.sel = n
            self.update_ui()

    # ---------- actions ----------
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
        if self.pending == "reserve":
            self.api("/reserve", {"spot": self.sel}, self.after_reserve)
        elif self.pending == "extend":
            self.api("/extend", {"spot": self.spot}, self.after_extend)

    def after_reserve(self, res):
        if res.get("ok"):
            self.spot, self.end, self.state, self.pending = self.sel, res["end"], "enter", None
            self.save()
        elif res.get("reason") == "taken":
            self.pending = None
            self.say_flash("این جا همین الان پر شد", "جای دیگری انتخاب کن")
        else:
            self.say_flash("خطا در ارتباط با سرور", res.get("err", "")[:80])
        self.update_ui()

    def after_extend(self, res):
        if res.get("ok"):
            self.end, self.pending = res["end"], None
            if self.state == "exit":
                self.state = "parked"
            self.save()
        else:
            self.say_flash("خطا در ارتباط با سرور", res.get("err", "")[:80])
        self.update_ui()

    def on_door(self):
        if self.state not in ("enter", "exit"):
            return

        def work():
            ok = self.bt.send("1")
            if ok:
                time.sleep(DOOR_OPEN_SEC)
                self.bt.send("0")
            Clock.schedule_once(lambda dt: self.after_door(ok))
        threading.Thread(target=work, daemon=True).start()

    def after_door(self, ok):
        if not ok:
            return self.say_flash("اتصال بلوتوث برقرار نشد", "ماژول را در تنظیمات گوشی جفت کن")
        if self.state == "enter":
            self.state = "parked"
        elif self.state == "exit":
            self.api("/release", {"spot": self.spot})
            self.spot, self.sel, self.state = None, None, "select"
        self.save()
        self.update_ui()

    def on_led(self):
        want = not self.led

        def work():
            ok = self.bt.send("2" if want else "3")
            Clock.schedule_once(lambda dt: self.after_led(ok, want))
        threading.Thread(target=work, daemon=True).start()

    def after_led(self, ok, want):
        if not ok:
            return self.say_flash("اتصال بلوتوث برقرار نشد", "ماژول را در تنظیمات گوشی جفت کن")
        self.led = want
        self.update_ui()

    def bt_keepalive(self):
        """Keep the Bluetooth link up so distance readings keep arriving."""
        if platform == "android" and self.bt.out is None:
            threading.Thread(target=self.bt.try_connect, daemon=True).start()

    def on_bt_line(self, line):
        if not line.startswith("D:"):
            return
        try:
            self.distance = int(line[2:])
        except ValueError:
            return
        self.dist_time = time.time()
        if self.server and self.dist_time - self.last_post >= 5:
            self.last_post = self.dist_time
            self.api("/sensor", {"distance": self.distance})
        self.update_ui()

    def tick(self):
        if self.state == "parked" and self.now() >= self.end:
            self.state = "exit"
            self.save()
        self.update_ui()

    # ---------- UI ----------
    def update_ui(self):
        now = self.now()
        for i, b in enumerate(self.spot_btns):
            n = i + 1
            taken = self.ends[i] > now and n != self.spot
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
        self.pay_btn.disabled = not (s == "select" and self.sel and not self.pending)
        self.paid_btn.disabled = self.pending is None
        self.door_btn.disabled = s not in ("enter", "exit")
        self.ext_btn.disabled = not (s in ("parked", "exit") and not self.pending)

        left = max(0, int(self.end - now))
        timer = ""
        if time.time() < self.flash[1]:
            text = lines(*self.flash[0])
        elif self.pending:
            text = lines("بعد از پرداخت", "دکمه «پرداخت کردم» را بزن")
        elif s == "select":
            text = lines("جای پارک را انتخاب کن", "بعد پرداخت را بزن") if self.sel is None \
                else lines(f"جای {self.sel} انتخاب شد", "پرداخت را بزن")
        elif s == "enter":
            text = lines("پرداخت انجام شد", f"جای {self.spot} | درب را باز کن و وارد شو")
        elif s == "parked":
            text = lines(f"جای {self.spot}", "زمان باقی‌مانده")
            timer = f"{left // 60:02d}:{left % 60:02d}"
        else:
            text = lines("زمان تمام شد", "درب را باز کن و خارج شو یا تمدید کن")
        self.status.text = text
        self.led_btn.text = fa("خاموش کردن چراغ" if self.led else "روشن کردن چراغ")
        if self.distance is not None and time.time() - self.dist_time < 10:
            if 0 < self.distance < CAR_CM:
                self.dist.text = fa(f"فاصله: {self.distance} cm | ماشین جلوی درب است")
            else:
                self.dist.text = fa("جلوی درب خالی است")
        else:
            self.dist.text = ""
        self.timer.text = timer


if __name__ == "__main__":
    ParkingApp().run()
