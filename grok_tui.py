from __future__ import annotations

import curses
import os
import textwrap
import time
from dataclasses import dataclass
from typing import Callable

from selenium.common.exceptions import StaleElementReferenceException, TimeoutException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait

# This comes from the pinned chrome-patch image. It enforces the image's fixed
# Chromium/ChromeDriver paths, exact browser version, sandbox flags, and proxy.
from common.browser import create_driver

GROK_URL = os.environ.get("GROK_URL", "https://grok.com/")
RESPONSE_TIMEOUT = int(os.environ.get("GROK_RESPONSE_TIMEOUT", "180"))

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

USER_SELECTORS = (
    (By.CSS_SELECTOR, '[data-testid="user-message"]'),
)


class GrokUiError(RuntimeError):
    pass


class GrokBrowser:
    def __init__(self) -> None:
        self.driver = create_driver(user_agent="Grok-TUI/1.0 chrome-patch")
        self.driver.get(GROK_URL)

    def close(self) -> None:
        try:
            self.driver.quit()
        except Exception:
            pass

    def new_chat(self) -> None:
        self.driver.get(GROK_URL)
        self.wait_for_composer(timeout=40)

    def reload(self) -> None:
        self.driver.refresh()
        self.wait_for_composer(timeout=40)

    def _find_first_visible(self, selectors):
        for by, selector in selectors:
            for element in self.driver.find_elements(by, selector):
                try:
                    if element.is_displayed():
                        return element
                except StaleElementReferenceException:
                    continue
        return None

    def wait_for_composer(self, timeout: int = 40):
        def ready(_driver):
            return self._find_first_visible(COMPOSER_SELECTORS) or False

        try:
            return WebDriverWait(self.driver, timeout).until(ready)
        except TimeoutException as exc:
            title = self.driver.title
            url = self.driver.current_url
            body = ""
            try:
                body = self.driver.find_element(By.TAG_NAME, "body").text.lower()[:4000]
            except Exception:
                pass
            if any(x in body for x in ("verify you are human", "captcha", "cloudflare")):
                raise GrokUiError(
                    "Grok/Cloudflare is asking for interactive verification. "
                    "This client does not bypass CAPTCHA or anti-bot checks."
                ) from exc
            if any(x in body for x in ("sign in", "log in", "login")):
                raise GrokUiError(
                    "No Grok composer appeared and the page looks authentication-gated. "
                    "This hardened headless profile is intentionally ephemeral and does not inject login cookies."
                ) from exc
            raise GrokUiError(f"Grok composer not found. title={title!r} url={url!r}") from exc

    def _assistant_elements(self):
        for by, selector in ASSISTANT_SELECTORS:
            elems = self.driver.find_elements(by, selector)
            if elems:
                return elems
        return []

    def _assistant_text(self) -> str:
        elems = self._assistant_elements()
        if not elems:
            return ""
        try:
            return elems[-1].text.strip()
        except StaleElementReferenceException:
            return ""


    def _generation_active(self) -> bool:
        selectors = (
            (By.CSS_SELECTOR, '[data-testid="stop-button"]'),
            (By.CSS_SELECTOR, 'button[aria-label*="Stop" i]'),
        )
        for by, selector in selectors:
            for element in self.driver.find_elements(by, selector):
                try:
                    if element.is_displayed() and element.is_enabled():
                        return True
                except StaleElementReferenceException:
                    continue
        return False

    def send(self, prompt: str, on_update: Callable[[str], None]) -> str:
        prompt = prompt.strip()
        if not prompt:
            raise GrokUiError("Empty prompt")

        before_elements = self._assistant_elements()
        before_count = len(before_elements)
        before_text = self._assistant_text()

        composer = self.wait_for_composer(timeout=40)
        try:
            composer.click()
            composer.send_keys(prompt)
            composer.send_keys(Keys.ENTER)
        except WebDriverException as exc:
            raise GrokUiError(f"Could not submit prompt: {exc.msg}") from exc

        start = time.monotonic()
        last = ""
        last_change = start
        observed_new_turn = False

        while time.monotonic() - start < RESPONSE_TIMEOUT:
            time.sleep(0.30)
            elems = self._assistant_elements()
            current = self._assistant_text()

            if len(elems) > before_count or (current and current != before_text):
                observed_new_turn = True

            if observed_new_turn and current != last:
                last = current
                last_change = time.monotonic()
                on_update(last)

            # Streaming responses can pause. When Grok exposes a Stop control,
            # do not finish until it disappears. Without one, require a longer
            # quiet window before treating the last bubble as complete.
            quiet = time.monotonic() - last_change
            age = time.monotonic() - start
            active = self._generation_active()
            if observed_new_turn and last and age >= 3.0:
                if (not active and quiet >= 1.5) or quiet >= 5.0:
                    return last

        if last:
            return last
        raise GrokUiError(f"No assistant reply detected within {RESPONSE_TIMEOUT}s")


@dataclass
class Message:
    role: str
    text: str


class ChatTUI:
    def __init__(self, stdscr, browser: GrokBrowser):
        self.stdscr = stdscr
        self.browser = browser
        self.messages: list[Message] = []
        self.status = "Ready. Enter sends. /new /reload /clear /quit"
        self.input_text = ""
        self.cursor = 0
        curses.curs_set(1)
        stdscr.keypad(True)
        try:
            curses.use_default_colors()
        except curses.error:
            pass

    def _wrap_message(self, msg: Message, width: int) -> list[str]:
        prefix = "YOU  > " if msg.role == "user" else "GROK > "
        subsequent = " " * len(prefix)
        text = msg.text or ""
        paras = text.splitlines() or [""]
        out: list[str] = []
        first = True
        for para in paras:
            lines = textwrap.wrap(
                para,
                width=max(10, width - len(prefix)),
                replace_whitespace=False,
                drop_whitespace=False,
            ) or [""]
            for line in lines:
                out.append((prefix if first else subsequent) + line)
                first = False
        return out

    def draw(self) -> None:
        s = self.stdscr
        h, w = s.getmaxyx()
        s.erase()
        title = " GROK / PATCHED CHROMIUM / REMOTE NODE "
        try:
            s.addnstr(0, 0, title, max(0, w - 1), curses.A_BOLD)
        except curses.error:
            pass

        transcript_height = max(1, h - 4)
        rendered: list[str] = []
        for msg in self.messages:
            rendered.extend(self._wrap_message(msg, max(20, w - 1)))
            rendered.append("")
        rendered = rendered[-transcript_height:]
        for row, line in enumerate(rendered, start=1):
            if row >= h - 3:
                break
            try:
                s.addnstr(row, 0, line, max(0, w - 1))
            except curses.error:
                pass

        status = self.status.replace("\n", " ")
        try:
            s.addnstr(h - 3, 0, status, max(0, w - 1), curses.A_DIM)
            s.hline(h - 2, 0, curses.ACS_HLINE, max(0, w - 1))
        except curses.error:
            pass

        prompt_prefix = "> "
        available = max(1, w - len(prompt_prefix) - 1)
        start = max(0, self.cursor - available + 1)
        visible = self.input_text[start:start + available]
        try:
            s.addnstr(h - 1, 0, prompt_prefix + visible, max(0, w - 1))
            cursor_x = len(prompt_prefix) + self.cursor - start
            s.move(h - 1, min(max(0, cursor_x), max(0, w - 1)))
        except curses.error:
            pass
        s.refresh()

    def _stream_update(self, text: str) -> None:
        if self.messages and self.messages[-1].role == "assistant":
            self.messages[-1].text = text
        else:
            self.messages.append(Message("assistant", text))
        self.status = "Grok is replying..."
        self.draw()

    def submit(self, prompt: str) -> None:
        if prompt == "/quit":
            raise KeyboardInterrupt
        if prompt == "/clear":
            self.messages.clear()
            self.status = "Local transcript cleared."
            return
        if prompt == "/new":
            self.status = "Opening new Grok chat..."
            self.draw()
            self.browser.new_chat()
            self.messages.clear()
            self.status = "New chat ready."
            return
        if prompt == "/reload":
            self.status = "Reloading Grok..."
            self.draw()
            self.browser.reload()
            self.status = "Reloaded."
            return

        self.messages.append(Message("user", prompt))
        self.messages.append(Message("assistant", ""))
        self.status = "Submitting..."
        self.draw()
        try:
            reply = self.browser.send(prompt, self._stream_update)
            self.messages[-1].text = reply
            self.status = "Ready. Enter sends. /new /reload /clear /quit"
        except GrokUiError as exc:
            if self.messages and self.messages[-1].role == "assistant" and not self.messages[-1].text:
                self.messages.pop()
            self.messages.append(Message("assistant", f"[client error] {exc}"))
            self.status = "Request failed; see transcript."

    def run(self) -> None:
        self.browser.wait_for_composer(timeout=45)
        self.draw()
        while True:
            self.draw()
            ch = self.stdscr.get_wch()
            if isinstance(ch, str):
                if ch in ("\n", "\r"):
                    prompt = self.input_text.strip()
                    self.input_text = ""
                    self.cursor = 0
                    if prompt:
                        self.submit(prompt)
                elif ch in ("\x03", "\x04"):
                    raise KeyboardInterrupt
                elif ch in ("\x7f", "\b"):
                    if self.cursor > 0:
                        self.input_text = self.input_text[:self.cursor - 1] + self.input_text[self.cursor:]
                        self.cursor -= 1
                elif ch.isprintable():
                    self.input_text = self.input_text[:self.cursor] + ch + self.input_text[self.cursor:]
                    self.cursor += 1
            else:
                if ch == curses.KEY_BACKSPACE and self.cursor > 0:
                    self.input_text = self.input_text[:self.cursor - 1] + self.input_text[self.cursor:]
                    self.cursor -= 1
                elif ch == curses.KEY_DC and self.cursor < len(self.input_text):
                    self.input_text = self.input_text[:self.cursor] + self.input_text[self.cursor + 1:]
                elif ch == curses.KEY_LEFT:
                    self.cursor = max(0, self.cursor - 1)
                elif ch == curses.KEY_RIGHT:
                    self.cursor = min(len(self.input_text), self.cursor + 1)
                elif ch == curses.KEY_HOME:
                    self.cursor = 0
                elif ch == curses.KEY_END:
                    self.cursor = len(self.input_text)
                elif ch == curses.KEY_RESIZE:
                    pass


def main(stdscr) -> None:
    browser = GrokBrowser()
    try:
        tui = ChatTUI(stdscr, browser)
        tui.run()
    finally:
        browser.close()


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
    except GrokUiError as exc:
        print(f"grok-tui: {exc}")
        raise SystemExit(2)
