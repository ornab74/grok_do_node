from __future__ import annotations

import base64
import curses
import ctypes
import getpass
import hashlib
import hmac
import io
import json
import os
import re
import resource
import secrets
import shutil
import string
import sys
import tarfile
import tempfile
import textwrap
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, WebDriverException
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

from common.browser import browser_arguments
from common.security_gate import APPROVED_VERSION, SecurityGateError

VAULT_DIR = Path(os.environ.get("GROK_VAULT_DIR", "/vault"))
SESSION_ROOT = Path(os.environ.get("GROK_SESSION_ROOT", "/session"))
VAULT_FILE = VAULT_DIR / "vault.aesgcm.json"
PROFILE_DIR = VAULT_DIR / "profiles"
GROK_URL = "https://grok.com/"
SIGNUP_URL = "https://accounts.x.ai/sign-up?email=true&redirect=grok-com"
SIGNIN_URL = "https://accounts.x.ai/sign-in?email=true&redirect=grok-com"
RESPONSE_TIMEOUT = int(os.environ.get("GROK_RESPONSE_TIMEOUT", "180"))
AAD_VAULT = b"grok-vault:v1"
SALT_BYTES = 16
NONCE_BYTES = 12
SCRYPT_N = 2 ** 17
SCRYPT_R = 8
SCRYPT_P = 1
APP_VERSION = "6.0-from-scratch"

COMPOSER_SELECTORS = (
    (By.CSS_SELECTOR, '[data-testid="chat-input"] div.ProseMirror[role="textbox"]'),
    (By.CSS_SELECTOR, 'div.ProseMirror[role="textbox"][contenteditable="true"]'),
    (By.CSS_SELECTOR, '.tiptap[contenteditable="true"]'),
    (By.CSS_SELECTOR, '[aria-label="Ask Grok anything"][contenteditable="true"]'),
    (By.CSS_SELECTOR, '[contenteditable="true"][role="textbox"]'),
)
ASSISTANT_SELECTORS = (
    (By.CSS_SELECTOR, '[data-testid="assistant-message"]'),
    (By.CSS_SELECTOR, '.response-content-markdown'),
)


class VaultError(RuntimeError):
    pass


class GrokUiError(RuntimeError):
    pass


def harden_process() -> None:
    """Reduce accidental secret exposure from this live vault process."""
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except Exception:
        pass
    # PR_SET_DUMPABLE=0 blocks ordinary same-UID ptrace/proc-memory access.
    # It is defense in depth only; host root/kernel compromise still wins.
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl(4, 0, 0, 0, 0)
    except Exception:
        pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def b64e(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def b64d(value: str) -> bytes:
    return base64.b64decode(value.encode("ascii"), validate=True)


def atomic_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, sort_keys=True, separators=(",", ":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def derive_master(passphrase: str, salt: bytes) -> bytearray:
    if not passphrase:
        raise VaultError("empty vault passphrase")
    kdf = Scrypt(salt=salt, length=32, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)
    return bytearray(kdf.derive(passphrase.encode("utf-8")))


def profile_key(master: bytes | bytearray, account_id: str) -> bytes:
    return hmac.new(bytes(master), b"grok-profile-v1:" + account_id.encode("ascii"), hashlib.sha256).digest()


def zero_bytearray(buf: bytearray | None) -> None:
    if buf is None:
        return
    for i in range(len(buf)):
        buf[i] = 0


class Vault:
    def __init__(self, directory: Path = VAULT_DIR):
        self.dir = directory
        self.file = directory / "vault.aesgcm.json"
        self.profiles = directory / "profiles"
        self.master: bytearray | None = None
        self.salt: bytes | None = None
        self.data: dict = {}

    def exists(self) -> bool:
        return self.file.is_file()

    def create(self, passphrase: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.profiles.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, 0o700)
        os.chmod(self.profiles, 0o700)
        self.salt = secrets.token_bytes(SALT_BYTES)
        self.master = derive_master(passphrase, self.salt)
        self.data = {"version": 1, "created_at": utcnow(), "active_account_id": None, "accounts": []}
        self.save()

    def unlock(self, passphrase: str) -> None:
        try:
            outer = json.loads(self.file.read_text(encoding="utf-8"))
            if outer.get("magic") != "GROKVAULT" or outer.get("version") != 1:
                raise VaultError("unsupported vault format")
            kdf = outer["kdf"]
            if kdf.get("name") != "scrypt" or int(kdf["n"]) != SCRYPT_N or int(kdf["r"]) != SCRYPT_R or int(kdf["p"]) != SCRYPT_P:
                raise VaultError("unexpected vault KDF parameters")
            salt = b64d(kdf["salt"])
            nonce = b64d(outer["nonce"])
            ciphertext = b64d(outer["ciphertext"])
            master = derive_master(passphrase, salt)
            plain = AESGCM(bytes(master)).decrypt(nonce, ciphertext, AAD_VAULT)
            data = json.loads(plain.decode("utf-8"))
        except InvalidTag as exc:
            raise VaultError("wrong vault passphrase or corrupted vault") from exc
        except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
            raise VaultError(f"could not read vault: {exc}") from exc
        self.salt = salt
        self.master = master
        self.data = data
        self.profiles.mkdir(parents=True, exist_ok=True)
        os.chmod(self.profiles, 0o700)

    def save(self) -> None:
        if self.master is None or self.salt is None:
            raise VaultError("vault is locked")
        nonce = secrets.token_bytes(NONCE_BYTES)
        plain = json.dumps(self.data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ciphertext = AESGCM(bytes(self.master)).encrypt(nonce, plain, AAD_VAULT)
        outer = {
            "magic": "GROKVAULT",
            "version": 1,
            "cipher": "AES-256-GCM",
            "kdf": {"name": "scrypt", "salt": b64e(self.salt), "n": SCRYPT_N, "r": SCRYPT_R, "p": SCRYPT_P},
            "nonce": b64e(nonce),
            "ciphertext": b64e(ciphertext),
        }
        atomic_json(self.file, outer)

    def lock(self) -> None:
        zero_bytearray(self.master)
        self.master = None
        self.salt = None
        self.data = {}

    def accounts(self) -> list[dict]:
        return list(self.data.get("accounts", []))

    def get(self, account_id: str | None) -> dict | None:
        if not account_id:
            return None
        for account in self.data.get("accounts", []):
            if account.get("id") == account_id:
                return account
        return None

    def active(self) -> dict | None:
        return self.get(self.data.get("active_account_id"))

    def add_account(self, *, alias: str, email: str, password: str | None, auth_mode: str, status: str = "pending") -> dict:
        alias_key = alias.strip().casefold()
        if not alias_key:
            raise VaultError("alias is required")
        if any(a.get("alias", "").casefold() == alias_key for a in self.accounts()):
            raise VaultError("that alias already exists")
        account = {
            "id": uuid.uuid4().hex,
            "alias": alias.strip(),
            "email": email.strip(),
            "password": password or None,
            "auth_mode": auth_mode,
            "status": status,
            "created_at": utcnow(),
            "last_login_at": None,
        }
        self.data.setdefault("accounts", []).append(account)
        if not self.data.get("active_account_id"):
            self.data["active_account_id"] = account["id"]
        self.save()
        return account

    def update(self, account: dict) -> None:
        account["updated_at"] = utcnow()
        self.save()

    def set_active(self, account_id: str) -> None:
        if not self.get(account_id):
            raise VaultError("unknown account")
        self.data["active_account_id"] = account_id
        self.save()

    def remove(self, account_id: str) -> None:
        self.data["accounts"] = [a for a in self.accounts() if a.get("id") != account_id]
        if self.data.get("active_account_id") == account_id:
            self.data["active_account_id"] = self.data["accounts"][0]["id"] if self.data["accounts"] else None
        try:
            self.profile_path(account_id).unlink()
        except FileNotFoundError:
            pass
        self.save()

    def profile_path(self, account_id: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{32}", account_id):
            raise VaultError("invalid account id")
        return self.profiles / f"{account_id}.profile.aesgcm.json"

    def encrypt_profile_bytes(self, account_id: str, archive: bytes) -> None:
        if self.master is None:
            raise VaultError("vault is locked")
        key = profile_key(self.master, account_id)
        nonce = secrets.token_bytes(NONCE_BYTES)
        aad = b"grok-profile:v1:" + account_id.encode("ascii")
        ciphertext = AESGCM(key).encrypt(nonce, archive, aad)
        outer = {"magic": "GROKPROFILE", "version": 1, "cipher": "AES-256-GCM", "nonce": b64e(nonce), "ciphertext": b64e(ciphertext)}
        atomic_json(self.profile_path(account_id), outer)

    def decrypt_profile_bytes(self, account_id: str) -> bytes | None:
        path = self.profile_path(account_id)
        if not path.exists():
            return None
        if self.master is None:
            raise VaultError("vault is locked")
        outer = json.loads(path.read_text(encoding="utf-8"))
        if outer.get("magic") != "GROKPROFILE" or outer.get("version") != 1:
            raise VaultError("unsupported encrypted profile")
        key = profile_key(self.master, account_id)
        aad = b"grok-profile:v1:" + account_id.encode("ascii")
        try:
            return AESGCM(key).decrypt(b64d(outer["nonce"]), b64d(outer["ciphertext"]), aad)
        except InvalidTag as exc:
            raise VaultError("encrypted browser profile failed authentication") from exc

    def change_passphrase(self, new_passphrase: str) -> None:
        if self.master is None:
            raise VaultError("vault is locked")
        # Decrypt one profile at a time, then rotate the master and rewrite.
        archives: dict[str, bytes] = {}
        for account in self.accounts():
            blob = self.decrypt_profile_bytes(account["id"])
            if blob is not None:
                archives[account["id"]] = blob
        old = self.master
        self.salt = secrets.token_bytes(SALT_BYTES)
        self.master = derive_master(new_passphrase, self.salt)
        self.save()
        for account_id, blob in archives.items():
            self.encrypt_profile_bytes(account_id, blob)
        zero_bytearray(old)


def safe_tar_profile(profile: Path) -> bytes:
    skip_parts = {"Cache", "Code Cache", "GPUCache", "ShaderCache", "GrShaderCache", "DawnCache", "Crashpad"}
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:gz", compresslevel=6) as tar:
        for root, dirs, files in os.walk(profile):
            root_path = Path(root)
            dirs[:] = [d for d in dirs if d not in skip_parts]
            for name in files:
                if name.startswith("Singleton") or name in {"DevToolsActivePort", "LOCK"}:
                    continue
                p = root_path / name
                if p.is_symlink() or not p.is_file():
                    continue
                rel = p.relative_to(profile)
                tar.add(p, arcname=str(rel), recursive=False)
    return out.getvalue()


def extract_tar_profile(blob: bytes, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError as exc:
                raise VaultError("encrypted profile archive contains an unsafe path") from exc
            if member.issym() or member.islnk() or member.isdev():
                raise VaultError("encrypted profile archive contains an unsafe entry")
        tar.extractall(destination, filter="data")


def generated_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*_-+="
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(length))
        if any(c.islower() for c in p) and any(c.isupper() for c in p) and any(c.isdigit() for c in p):
            return p


def create_driver(profile: Path) -> webdriver.Chrome:
    binary = Path("/opt/chromium/chrome")
    driver_path = Path("/opt/chromium/chromedriver")
    profile.mkdir(parents=True, exist_ok=True, mode=0o700)
    options = webdriver.ChromeOptions()
    options.binary_location = str(binary)
    for arg in browser_arguments(os.environ.get("SCRAPER_PROXY", "").strip()):
        options.add_argument(arg)
    options.add_argument(f"--user-data-dir={profile}")
    options.add_experimental_option("prefs", {
        "download_restrictions": 3,
        "profile.default_content_setting_values.automatic_downloads": 2,
        "profile.default_content_setting_values.clipboard": 2,
        "profile.default_content_setting_values.geolocation": 2,
        "profile.default_content_setting_values.media_stream": 2,
        "profile.default_content_setting_values.notifications": 2,
        "profile.default_content_setting_values.sensors": 2,
    })
    options.set_capability("acceptInsecureCerts", False)
    service = ChromeService(executable_path=str(driver_path), log_output=os.devnull)
    driver = webdriver.Chrome(service=service, options=options)
    if driver.capabilities.get("browserVersion") != APPROVED_VERSION:
        driver.quit()
        raise SecurityGateError("running browser version is not approved")
    driver.set_page_load_timeout(35)
    return driver


class AccountSession:
    def __init__(self, vault: Vault, account: dict):
        self.vault = vault
        self.account = account
        self.profile = SESSION_ROOT / account["id"]
        self.driver: webdriver.Chrome | None = None

    def __enter__(self) -> "AccountSession":
        if self.profile.exists():
            shutil.rmtree(self.profile)
        self.profile.mkdir(parents=True, mode=0o700)
        blob = self.vault.decrypt_profile_bytes(self.account["id"])
        if blob:
            extract_tar_profile(blob, self.profile)
        self.driver = create_driver(self.profile)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
        try:
            if self.profile.exists():
                self.vault.encrypt_profile_bytes(self.account["id"], safe_tar_profile(self.profile))
        finally:
            shutil.rmtree(self.profile, ignore_errors=True)

    def _body(self) -> str:
        try:
            return self.driver.find_element(By.TAG_NAME, "body").text
        except Exception:
            return ""

    def _visible(self, selectors):
        for by, selector in selectors:
            for elem in self.driver.find_elements(by, selector):
                try:
                    if elem.is_displayed():
                        return elem
                except StaleElementReferenceException:
                    continue
        return None

    def composer(self):
        return self._visible(COMPOSER_SELECTORS)

    def wait_composer(self, timeout: int = 30):
        try:
            return WebDriverWait(self.driver, timeout).until(lambda _d: self.composer() or False)
        except TimeoutException:
            return None

    def _captcha_guard(self) -> None:
        body = self._body().lower()
        if any(s in body for s in ("verify you are human", "captcha", "cloudflare", "security check")):
            raise GrokUiError("xAI/Cloudflare requested human verification. This client will not bypass CAPTCHA or anti-bot checks.")

    def _first_input(self, kinds: tuple[str, ...]):
        selectors = []
        for kind in kinds:
            selectors.extend([
                (By.CSS_SELECTOR, f'input[type="{kind}"]'),
                (By.CSS_SELECTOR, f'input[name*="{kind}" i]'),
                (By.CSS_SELECTOR, f'input[autocomplete*="{kind}" i]'),
            ])
        return self._visible(selectors)

    def _click_text(self, labels: tuple[str, ...]) -> bool:
        wanted = tuple(x.casefold() for x in labels)
        for elem in self.driver.find_elements(By.CSS_SELECTOR, "button, a, [role=button]"):
            try:
                if not elem.is_displayed() or not elem.is_enabled():
                    continue
                txt = (elem.text or "").strip().casefold()
                aria = (elem.get_attribute("aria-label") or "").strip().casefold()
                if any(label in txt or label in aria for label in wanted):
                    elem.click()
                    return True
            except (StaleElementReferenceException, WebDriverException):
                continue
        return False

    def _submit_current(self) -> None:
        if self._click_text(("next", "continue", "sign up", "create account", "confirm", "verify", "log in", "login", "sign in")):
            return
        active = self.driver.switch_to.active_element
        active.send_keys(Keys.ENTER)

    def _fill_email(self, email: str) -> None:
        email_input = WebDriverWait(self.driver, 20).until(lambda _d: self._first_input(("email",)) or False)
        email_input.clear()
        email_input.send_keys(email)
        self._submit_current()

    def _maybe_password(self, password: str | None) -> bool:
        fields = []
        for elem in self.driver.find_elements(By.CSS_SELECTOR, 'input[type="password"]'):
            try:
                if elem.is_displayed() and elem.is_enabled():
                    fields.append(elem)
            except StaleElementReferenceException:
                pass
        if not fields:
            return False
        if not password:
            raise GrokUiError("xAI requested a password but this account has no password stored")
        for field in fields:
            field.clear()
            field.send_keys(password)
        self._submit_current()
        return True

    def _verification_stage(self, ask_secret: Callable[[str], str]) -> bool:
        body = self._body().lower()
        code_fields = []
        for elem in self.driver.find_elements(By.CSS_SELECTOR, 'input[autocomplete="one-time-code"], input[name*="code" i], input[inputmode="numeric"]'):
            try:
                if elem.is_displayed() and elem.is_enabled() and elem not in code_fields:
                    code_fields.append(elem)
            except StaleElementReferenceException:
                pass
        if code_fields:
            code = ask_secret("Email verification code").strip()
            if not code:
                raise GrokUiError("verification code was empty")
            one_char = len(code_fields) > 1 and all((f.get_attribute("maxlength") or "") == "1" for f in code_fields)
            if one_char:
                if len(code) < len(code_fields):
                    raise GrokUiError("verification code is shorter than the visible OTP fields")
                for field, char in zip(code_fields, code):
                    field.clear()
                    field.send_keys(char)
            else:
                code_fields[0].clear()
                code_fields[0].send_keys(code)
            self._submit_current()
            return True
        if any(s in body for s in ("check your email", "verification link", "verify your email", "confirm your email")):
            raw = ask_secret("Paste xAI verification URL from the email")
            parsed = urlparse(raw.strip())
            if parsed.scheme != "https" or parsed.hostname not in {"accounts.x.ai", "x.ai", "grok.com"}:
                raise GrokUiError("verification URL must be HTTPS on accounts.x.ai, x.ai, or grok.com")
            self.driver.get(raw.strip())
            return True
        return False

    def signup(self, email: str, password: str, ask_secret: Callable[[str], str], progress: Callable[[str], None]) -> str:
        progress("Opening xAI email signup...")
        self.driver.get(SIGNUP_URL)
        time.sleep(1.0)
        self._captcha_guard()
        if self._first_input(("email",)) is None:
            self._click_text(("sign up with email", "email"))
        self._fill_email(email)
        password_used = False
        for _ in range(12):
            time.sleep(1.0)
            self._captcha_guard()
            if self.composer() or self.wait_composer(timeout=1):
                return "email_password" if password_used else "email_session"
            if self._maybe_password(password):
                password_used = True
                progress("Submitted xAI password stage...")
                continue
            if self._verification_stage(ask_secret):
                progress("Submitted email verification...")
                continue
            if "grok.com" in (self.driver.current_url or ""):
                if self.wait_composer(timeout=12):
                    return "email_password" if password_used else "email_session"
            body = self._body().lower()
            if any(s in body for s in ("existing account", "already exists")):
                raise GrokUiError("xAI says this email already has an account; import/login instead")
        self.driver.get(GROK_URL)
        if self.wait_composer(timeout=20):
            return "email_password" if password_used else "email_session"
        raise GrokUiError(f"registration did not reach an authenticated Grok composer (url={self.driver.current_url!r})")

    def login(self, email: str, password: str | None, ask_secret: Callable[[str], str], progress: Callable[[str], None]) -> None:
        self.driver.get(GROK_URL)
        if self.wait_composer(timeout=8):
            return
        progress("Session needs authentication; opening xAI email sign-in...")
        self.driver.get(SIGNIN_URL)
        time.sleep(1.0)
        self._captcha_guard()
        if self._first_input(("email",)) is None:
            self._click_text(("login with email", "sign in with email", "email"))
        self._fill_email(email)
        for _ in range(12):
            time.sleep(1.0)
            self._captcha_guard()
            if self.composer() or self.wait_composer(timeout=1):
                return
            if self._maybe_password(password):
                progress("Submitted xAI password stage...")
                continue
            if self._verification_stage(ask_secret):
                progress("Submitted email verification...")
                continue
            if "grok.com" in (self.driver.current_url or "") and self.wait_composer(timeout=12):
                return
        self.driver.get(GROK_URL)
        if not self.wait_composer(timeout=20):
            raise GrokUiError(f"login did not reach a Grok composer (url={self.driver.current_url!r})")

    def assistant_elements(self):
        for by, selector in ASSISTANT_SELECTORS:
            elems = self.driver.find_elements(by, selector)
            if elems:
                return elems
        return []

    def assistant_text(self) -> str:
        elems = self.assistant_elements()
        if not elems:
            return ""
        try:
            return elems[-1].text.strip()
        except StaleElementReferenceException:
            return ""

    def generation_active(self) -> bool:
        for by, selector in ((By.CSS_SELECTOR, '[data-testid="stop-button"]'), (By.CSS_SELECTOR, 'button[aria-label*="Stop" i]')):
            for elem in self.driver.find_elements(by, selector):
                try:
                    if elem.is_displayed() and elem.is_enabled():
                        return True
                except StaleElementReferenceException:
                    pass
        return False

    def send(self, prompt: str, on_update: Callable[[str], None]) -> str:
        composer = self.wait_composer(timeout=30)
        if not composer:
            raise GrokUiError("Grok composer disappeared; session may have expired")
        before_count = len(self.assistant_elements())
        before_text = self.assistant_text()
        composer.click()
        composer.send_keys(prompt)
        composer.send_keys(Keys.ENTER)
        start = time.monotonic()
        last = ""
        changed = start
        observed = False
        while time.monotonic() - start < RESPONSE_TIMEOUT:
            time.sleep(0.3)
            elems = self.assistant_elements()
            current = self.assistant_text()
            if len(elems) > before_count or (current and current != before_text):
                observed = True
            if observed and current != last:
                last = current
                changed = time.monotonic()
                on_update(last)
            quiet = time.monotonic() - changed
            if observed and last and time.monotonic() - start >= 3:
                if (not self.generation_active() and quiet >= 1.5) or quiet >= 5:
                    return last
        if last:
            return last
        raise GrokUiError("no Grok reply detected before timeout")


@dataclass
class Message:
    role: str
    text: str


class App:
    def __init__(self, stdscr, vault: Vault):
        self.s = stdscr
        self.vault = vault
        self.status = ""
        curses.curs_set(0)
        self.s.keypad(True)
        try:
            curses.use_default_colors()
        except curses.error:
            pass

    def center(self, lines: list[str], title: str = "GROK VAULT") -> None:
        self.s.erase()
        h, w = self.s.getmaxyx()
        try:
            self.s.addnstr(0, 0, f" {title} ", max(0, w - 1), curses.A_BOLD)
        except curses.error:
            pass
        row = 2
        for line in lines:
            if row >= h - 2:
                break
            try:
                self.s.addnstr(row, 2, line, max(0, w - 4))
            except curses.error:
                pass
            row += 1
        if self.status:
            try:
                self.s.addnstr(h - 1, 0, self.status.replace("\n", " "), max(0, w - 1), curses.A_DIM)
            except curses.error:
                pass
        self.s.refresh()

    def prompt(self, label: str, *, secret: bool = False, default: str = "") -> str:
        self.s.erase()
        h, w = self.s.getmaxyx()
        self.s.addnstr(0, 0, " GROK VAULT / INPUT ", max(0, w - 1), curses.A_BOLD)
        self.s.addnstr(2, 2, label, max(0, w - 4))
        text = default
        curses.curs_set(1)
        while True:
            shown = ("*" * len(text)) if secret else text
            self.s.move(4, 2)
            self.s.clrtoeol()
            self.s.addnstr(4, 2, "> " + shown, max(0, w - 4))
            self.s.refresh()
            ch = self.s.get_wch()
            if isinstance(ch, str):
                if ch in ("\n", "\r"):
                    curses.curs_set(0)
                    return text
                if ch == "\x1b":
                    curses.curs_set(0)
                    return ""
                if ch in ("\x7f", "\b"):
                    text = text[:-1]
                elif ch.isprintable():
                    text += ch
            elif ch == curses.KEY_BACKSPACE:
                text = text[:-1]

    def yesno(self, question: str) -> bool:
        self.center([question, "", "y = yes    n = no"], "GROK VAULT / CONFIRM")
        while True:
            ch = self.s.get_wch()
            if isinstance(ch, str) and ch.lower() in ("y", "n"):
                return ch.lower() == "y"

    def progress(self, text: str) -> None:
        self.status = text
        self.center([text, "", "Browser activity stays on the DigitalOcean node."], "GROK VAULT / WORKING")

    def ask_secret(self, label: str) -> str:
        return self.prompt(label + " (input is not stored unless it is the account password)", secret=True)

    def accounts_screen(self) -> None:
        idx = 0
        while True:
            accounts = self.vault.accounts()
            if accounts:
                idx = max(0, min(idx, len(accounts) - 1))
            active_id = self.vault.data.get("active_account_id")
            lines = ["Accounts are encrypted at rest. Browser profiles/cookies are encrypted separately with AES-GCM.", ""]
            for i, a in enumerate(accounts):
                mark = ">" if i == idx else " "
                active = "*" if a["id"] == active_id else " "
                email = a.get("email", "")
                masked = (email[:2] + "…" + email[email.find("@"):] if "@" in email and len(email) > 4 else email)
                lines.append(f"{mark}{active} {a['alias']:<18} {masked:<30} {a.get('status','?')}")
            if not accounts:
                lines.append("No accounts stored.")
            lines += ["", "N register new   I import existing   Enter select   L login/test", "R reveal selected credentials   D delete   P change vault passphrase", "Esc back"]
            self.center(lines, "GROK VAULT / ACCOUNTS")
            ch = self.s.get_wch()
            if ch == curses.KEY_UP and accounts:
                idx = (idx - 1) % len(accounts)
            elif ch == curses.KEY_DOWN and accounts:
                idx = (idx + 1) % len(accounts)
            elif isinstance(ch, str):
                if ch == "\x1b":
                    return
                if ch in ("\n", "\r") and accounts:
                    self.vault.set_active(accounts[idx]["id"])
                    self.status = f"Active account: {accounts[idx]['alias']}"
                elif ch.lower() == "n":
                    self.register_account()
                elif ch.lower() == "i":
                    self.import_account()
                elif ch.lower() == "l" and accounts:
                    self.login_test(accounts[idx])
                elif ch.lower() == "r" and accounts:
                    self.reveal_credentials(accounts[idx])
                elif ch.lower() == "d" and accounts:
                    a = accounts[idx]
                    if self.yesno(f"Delete {a['alias']} and its encrypted browser profile?"):
                        self.vault.remove(a["id"])
                        self.status = "Account removed."
                        idx = 0
                elif ch.lower() == "p":
                    self.rotate_passphrase()

    def register_account(self) -> None:
        alias = self.prompt("Local alias for this account")
        if not alias:
            return
        email = self.prompt("Email address you control")
        if not email or "@" not in email:
            self.status = "Registration cancelled: invalid email."
            return
        password = generated_password()
        if not self.yesno("Generate and store a random 24-character password for xAI if the signup flow asks for one?"):
            password = self.prompt("Password to use if xAI requests one", secret=True)
            if not password:
                return
        if not self.yesno("Proceed with xAI/Grok's normal email signup flow from the DigitalOcean browser?"):
            return
        try:
            account = self.vault.add_account(alias=alias, email=email, password=password, auth_mode="email", status="registering")
            with AccountSession(self.vault, account) as session:
                mode = session.signup(email, password, self.ask_secret, self.progress)
                account["auth_mode"] = mode
                account["status"] = "ready"
                account["last_login_at"] = utcnow()
                if mode != "email_password":
                    # Do not retain an unused generated password for a code/session-only account.
                    account["password"] = None
                self.vault.update(account)
                self.vault.set_active(account["id"])
            self.status = f"Registered and stored {alias}."
        except Exception as exc:
            self.status = f"Registration stopped: {exc}"
            a = self.vault.get(account["id"]) if 'account' in locals() else None
            if a:
                a["status"] = "pending"
                self.vault.update(a)

    def import_account(self) -> None:
        alias = self.prompt("Local alias for existing xAI/Grok account")
        if not alias:
            return
        email = self.prompt("xAI account email")
        if not email:
            return
        password = self.prompt("xAI password (leave blank for email-code/session login)", secret=True)
        try:
            account = self.vault.add_account(alias=alias, email=email, password=password or None, auth_mode="email_password" if password else "email_session", status="imported")
            self.login_test(account)
        except Exception as exc:
            self.status = f"Import failed: {exc}"

    def login_test(self, account: dict) -> None:
        try:
            with AccountSession(self.vault, account) as session:
                session.login(account["email"], account.get("password"), self.ask_secret, self.progress)
                account["status"] = "ready"
                account["last_login_at"] = utcnow()
                self.vault.update(account)
            self.status = f"Login verified for {account['alias']}."
        except Exception as exc:
            self.status = f"Login failed: {exc}"

    def reveal_credentials(self, account: dict) -> None:
        if not self.yesno(f"Reveal stored credentials for {account['alias']} on this SSH terminal?"):
            return
        password = account.get("password") or "(no password stored; session/code auth)"
        self.center([
            f"Alias: {account.get('alias','')}",
            f"Email: {account.get('email','')}",
            f"Auth mode: {account.get('auth_mode','')}",
            f"Password: {password}",
            "",
            "This screen is not written to the Docker log by the application.",
            "Press any key to hide it.",
        ], "GROK VAULT / CREDENTIALS")
        self.s.get_wch()
        self.status = "Credentials hidden."

    def rotate_passphrase(self) -> None:
        p1 = self.prompt("New vault master passphrase (14+ characters)", secret=True)
        if len(p1) < 14:
            self.status = "Passphrase unchanged: use at least 14 characters."
            return
        p2 = self.prompt("Repeat new vault master passphrase", secret=True)
        if p1 != p2:
            self.status = "Passphrases did not match."
            return
        self.progress("Re-encrypting vault and all browser profiles...")
        try:
            self.vault.change_passphrase(p1)
            self.status = "Vault passphrase changed."
        except Exception as exc:
            self.status = f"Passphrase rotation failed: {exc}"

    def chat(self, account: dict) -> None:
        messages: list[Message] = []
        input_text = ""
        cursor = 0
        try:
            self.progress(f"Opening encrypted session for {account['alias']}...")
            with AccountSession(self.vault, account) as session:
                session.login(account["email"], account.get("password"), self.ask_secret, self.progress)
                account["status"] = "ready"
                account["last_login_at"] = utcnow()
                self.vault.update(account)
                curses.curs_set(1)
                while True:
                    self.s.erase()
                    h, w = self.s.getmaxyx()
                    self.s.addnstr(0, 0, f" GROK / {account['alias']} / AES-GCM VAULT ", max(0, w - 1), curses.A_BOLD)
                    rendered: list[str] = []
                    for msg in messages:
                        prefix = "YOU  > " if msg.role == "user" else "GROK > "
                        subsequent = " " * len(prefix)
                        first = True
                        for para in (msg.text or "").splitlines() or [""]:
                            for line in textwrap.wrap(para, width=max(10, w - len(prefix) - 2), replace_whitespace=False, drop_whitespace=False) or [""]:
                                rendered.append((prefix if first else subsequent) + line)
                                first = False
                        rendered.append("")
                    for row, line in enumerate(rendered[-max(1, h - 4):], 1):
                        if row >= h - 3:
                            break
                        try:
                            self.s.addnstr(row, 0, line, max(0, w - 1))
                        except curses.error:
                            pass
                    self.s.addnstr(h - 3, 0, "/new /accounts /quit", max(0, w - 1), curses.A_DIM)
                    self.s.hline(h - 2, 0, curses.ACS_HLINE, max(0, w - 1))
                    avail = max(1, w - 3)
                    start = max(0, cursor - avail + 1)
                    visible = input_text[start:start + avail]
                    self.s.addnstr(h - 1, 0, "> " + visible, max(0, w - 1))
                    self.s.move(h - 1, min(w - 1, 2 + cursor - start))
                    self.s.refresh()
                    ch = self.s.get_wch()
                    if isinstance(ch, str):
                        if ch in ("\n", "\r"):
                            prompt = input_text.strip()
                            input_text = ""
                            cursor = 0
                            if not prompt:
                                continue
                            if prompt == "/quit":
                                curses.curs_set(0)
                                return
                            if prompt == "/accounts":
                                curses.curs_set(0)
                                return
                            if prompt == "/new":
                                session.driver.get(GROK_URL)
                                messages.clear()
                                continue
                            messages.append(Message("user", prompt))
                            messages.append(Message("assistant", ""))
                            def update(text: str) -> None:
                                messages[-1].text = text
                            try:
                                messages[-1].text = session.send(prompt, update)
                            except Exception as exc:
                                messages[-1].text = f"[client error] {exc}"
                        elif ch in ("\x7f", "\b"):
                            if cursor:
                                input_text = input_text[:cursor-1] + input_text[cursor:]
                                cursor -= 1
                        elif ch == "\x03":
                            curses.curs_set(0)
                            return
                        elif ch.isprintable():
                            input_text = input_text[:cursor] + ch + input_text[cursor:]
                            cursor += 1
                    else:
                        if ch == curses.KEY_LEFT:
                            cursor = max(0, cursor - 1)
                        elif ch == curses.KEY_RIGHT:
                            cursor = min(len(input_text), cursor + 1)
                        elif ch == curses.KEY_BACKSPACE and cursor:
                            input_text = input_text[:cursor-1] + input_text[cursor:]
                            cursor -= 1
        except Exception as exc:
            self.status = f"Chat failed: {exc}"
            curses.curs_set(0)

    def run(self) -> None:
        while True:
            active = self.vault.active()
            lines = [
                f"DigitalOcean-only Grok client — {APP_VERSION}",
                "",
                f"Active account: {active['alias'] if active else '(none)'}",
                f"Status: {active.get('status','-') if active else '-'}",
                "",
                "C  chat with active account",
                "A  account manager / register / login",
                "V  vault security information",
                "Q  quit and lock vault",
            ]
            self.center(lines, "GROK / SECURE ACCOUNT VAULT")
            ch = self.s.get_wch()
            if not isinstance(ch, str):
                continue
            if ch.lower() == "q":
                return
            if ch.lower() == "a":
                self.accounts_screen()
            elif ch.lower() == "c":
                active = self.vault.active()
                if not active:
                    self.status = "Create or import an account first."
                else:
                    self.chat(active)
            elif ch.lower() == "v":
                self.center([
                    "Vault metadata + account credentials: AES-256-GCM",
                    f"Master KDF: scrypt N={SCRYPT_N}, r={SCRYPT_R}, p={SCRYPT_P}, 16-byte random salt",
                    "Per-account Chromium profile/cookies: separate AES-256-GCM ciphertext",
                    "Profile subkey: HMAC-SHA-256(master, account-id domain separation)",
                    "Live profile location: /session tmpfs only; removed after browser exit",
                    "Master passphrase: TTY prompt only; never Docker env/argv/file",
                    "",
                    "This protects secrets at rest. A root-compromised live Droplet can still observe an unlocked session.",
                    "",
                    "Press any key.",
                ], "GROK VAULT / SECURITY")
                self.s.get_wch()


def unlock_vault() -> Vault:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_ROOT.mkdir(parents=True, exist_ok=True)
    os.chmod(VAULT_DIR, 0o700)
    os.chmod(SESSION_ROOT, 0o700)
    vault = Vault(VAULT_DIR)
    if not vault.exists():
        print("No Grok vault exists. Create one. The master passphrase is never stored.", file=sys.stderr)
        while True:
            p1 = getpass.getpass("New vault master passphrase (14+ chars): ")
            if len(p1) < 14:
                print("Use at least 14 characters.", file=sys.stderr)
                continue
            p2 = getpass.getpass("Repeat master passphrase: ")
            if p1 != p2:
                print("Passphrases did not match.", file=sys.stderr)
                continue
            vault.create(p1)
            break
    else:
        for attempt in range(3):
            p = getpass.getpass("Vault master passphrase: ")
            try:
                vault.unlock(p)
                break
            except VaultError as exc:
                print(str(exc), file=sys.stderr)
        else:
            raise SystemExit("vault unlock failed")
    return vault


def main() -> None:
    os.umask(0o077)
    harden_process()
    vault = unlock_vault()
    try:
        curses.wrapper(lambda s: App(s, vault).run())
    finally:
        vault.lock()
        shutil.rmtree(SESSION_ROOT, ignore_errors=True)


if __name__ == "__main__":
    main()
