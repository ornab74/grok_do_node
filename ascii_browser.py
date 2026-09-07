from __future__ import annotations

import curses
import re
import textwrap
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlparse

from selenium.common.exceptions import (
    NoSuchFrameException,
    StaleElementReferenceException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys


ALLOWED_SUFFIXES = ("grok.com", "x.ai")
EXTRA_ALLOWED_HOSTS = {"challenges.cloudflare.com"}

# Dedicated anti-bot/challenge controls are deliberately rendered but not
# activated by this terminal frontend. Ordinary website controls remain usable.
PROTECTED_HOSTS = {"challenges.cloudflare.com"}
PROTECTED_MARKERS = (
    "turnstile",
    "captcha",
    "cf-chl",
    "cf_chl",
    "challenge-platform",
    "verify-you-are-human",
    "verify you are human",
    "security verification",
)

INTERACTIVE_ROLES = {
    "button",
    "checkbox",
    "combobox",
    "link",
    "menuitem",
    "option",
    "radio",
    "searchbox",
    "slider",
    "spinbutton",
    "switch",
    "tab",
    "textbox",
}

TEXT_TAGS = {
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "li", "label", "legend", "summary",
    "blockquote", "pre", "code",
}

JS_SNAPSHOT = r"""
const marker = arguments[0];

function visible(el) {
  if (!el || !(el instanceof Element)) return false;
  const s = getComputedStyle(el);
  if (s.display === "none" || s.visibility === "hidden" || s.opacity === "0") return false;
  const r = el.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) return false;
  return true;
}

function clean(v) {
  return String(v || "").replace(/\s+/g, " ").trim();
}

function roleOf(el) {
  const explicit = clean(el.getAttribute("role")).toLowerCase();
  if (explicit) return explicit;
  const tag = el.tagName.toLowerCase();
  const type = clean(el.getAttribute("type")).toLowerCase();
  if (tag === "a" && el.hasAttribute("href")) return "link";
  if (tag === "button") return "button";
  if (tag === "textarea") return "textbox";
  if (tag === "select") return "combobox";
  if (tag === "input") {
    if (type === "checkbox") return "checkbox";
    if (type === "radio") return "radio";
    if (type === "range") return "slider";
    if (type === "number") return "spinbutton";
    if (type === "search") return "searchbox";
    if (["submit","button","reset","image"].includes(type)) return "button";
    return "textbox";
  }
  if (el.isContentEditable) return "textbox";
  return "";
}

function labelOf(el) {
  const aria = clean(el.getAttribute("aria-label"));
  if (aria) return aria;

  const labelledby = clean(el.getAttribute("aria-labelledby"));
  if (labelledby) {
    const t = labelledby.split(/\s+/)
      .map(id => document.getElementById(id))
      .filter(Boolean)
      .map(x => clean(x.innerText || x.textContent))
      .filter(Boolean)
      .join(" ");
    if (t) return t;
  }

  if (el.labels && el.labels.length) {
    const t = [...el.labels].map(x => clean(x.innerText || x.textContent)).filter(Boolean).join(" ");
    if (t) return t;
  }

  const placeholder = clean(el.getAttribute("placeholder"));
  if (placeholder) return placeholder;

  const title = clean(el.getAttribute("title"));
  if (title) return title;

  const text = clean(el.innerText || el.textContent);
  if (text) return text;

  const name = clean(el.getAttribute("name"));
  if (name) return name;

  return roleOf(el) || el.tagName.toLowerCase();
}

function protectedHint(el) {
  return clean([
    el.id,
    el.className,
    el.getAttribute("name"),
    el.getAttribute("title"),
    el.getAttribute("aria-label"),
    el.getAttribute("src"),
    el.getAttribute("data-sitekey"),
    el.getAttribute("data-testid")
  ].join(" ")).toLowerCase();
}

let seq = 0;
const rows = [];
const all = [...document.querySelectorAll("*")];

for (const el of all) {
  if (!visible(el)) continue;

  const tag = el.tagName.toLowerCase();
  const role = roleOf(el);
  const isInteractive =
    !!role &&
    (
      ["button","input","textarea","select","a"].includes(tag) ||
      el.isContentEditable ||
      el.hasAttribute("tabindex") ||
      el.hasAttribute("role")
    );

  const r = el.getBoundingClientRect();
  const y = Math.round(r.top + window.scrollY);
  const x = Math.round(r.left + window.scrollX);

  if (isInteractive) {
    seq += 1;
    const id = `${marker}-${seq}`;
    el.setAttribute("data-grok-ascii-id", id);

    const type = clean(el.getAttribute("type")).toLowerCase();
    let value = "";
    if ("value" in el) value = clean(el.value);
    if (type === "password" && value) value = "•".repeat(Math.min(12, value.length));

    rows.push({
      kind: "control",
      dom_id: id,
      tag,
      role,
      type,
      label: labelOf(el),
      value,
      checked: !!el.checked,
      disabled: !!el.disabled || el.getAttribute("aria-disabled") === "true",
      href: clean(el.getAttribute("href")),
      hint: protectedHint(el),
      y, x
    });
    continue;
  }

  if (["h1","h2","h3","h4","h5","h6","p","li","label","legend","summary","blockquote","pre","code"].includes(tag)) {
    const text = clean(el.innerText || el.textContent);
    if (text && text.length <= 1500) {
      rows.push({kind: "text", tag, text, y, x});
    }
  }
}

const frames = [...document.querySelectorAll("iframe")].filter(visible).map((el, index) => {
  const r = el.getBoundingClientRect();
  return {
    index,
    src: clean(el.getAttribute("src")),
    title: clean(el.getAttribute("title")),
    name: clean(el.getAttribute("name")),
    id: clean(el.id),
    className: clean(el.className),
    y: Math.round(r.top + window.scrollY),
    x: Math.round(r.left + window.scrollX),
  };
});

return {
  title: document.title || "",
  url: location.href,
  body_text: clean(document.body ? document.body.innerText : ""),
  rows,
  frames,
  scroll_y: Math.round(window.scrollY),
  scroll_h: Math.max(
    document.body ? document.body.scrollHeight : 0,
    document.documentElement ? document.documentElement.scrollHeight : 0
  ),
  viewport_h: window.innerHeight,
};
"""


@dataclass
class Item:
    kind: str
    text: str
    frame_path: tuple[int, ...] = ()
    dom_id: str | None = None
    role: str = ""
    type: str = ""
    protected: bool = False
    disabled: bool = False
    checked: bool = False
    href: str = ""


def _host_allowed(host: str | None) -> bool:
    host = (host or "").lower().rstrip(".")
    if host in EXTRA_ALLOWED_HOSTS:
        return True
    return any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_SUFFIXES)


def _host_protected(host: str | None) -> bool:
    host = (host or "").lower().rstrip(".")
    return host in PROTECTED_HOSTS


def _looks_protected(text: str) -> bool:
    t = (text or "").lower()
    return any(marker in t for marker in PROTECTED_MARKERS)


class AsciiBrowser:
    """
    Terminal frontend for an already-running Selenium Chromium session.

    The browser process stays headless. The TUI consumes DOM semantics and
    accessibility-like labels rather than pixels. Dedicated anti-bot controls
    are visible/read-only; ordinary site controls are interactive.
    """

    def __init__(
        self,
        stdscr,
        driver,
        prompt: Callable[[str], str],
        status: Callable[[str], None] | None = None,
    ):
        self.s = stdscr
        self.driver = driver
        self.prompt = prompt
        self.status_cb = status or (lambda _x: None)
        self.items: list[Item] = []
        self.selected = 0
        self.top = 0
        self.message = ""
        self.page_title = ""
        self.page_url = ""
        self.last_refresh = 0.0

    # ---------- frame navigation ----------

    def _switch_path(self, path: tuple[int, ...]) -> bool:
        try:
            self.driver.switch_to.default_content()
            for index in path:
                frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe")
                if index < 0 or index >= len(frames):
                    return False
                self.driver.switch_to.frame(frames[index])
            return True
        except (NoSuchFrameException, StaleElementReferenceException, WebDriverException):
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass
            return False

    def _frame_protected(self, path: tuple[int, ...], frame_meta: dict | None = None) -> bool:
        if frame_meta:
            src = frame_meta.get("src") or ""
            host = urlparse(src).hostname if src else None
            hint = " ".join(str(frame_meta.get(k) or "") for k in ("src","title","name","id","className"))
            if _host_protected(host) or _looks_protected(hint):
                return True
        return False

    # ---------- snapshot ----------

    def _snapshot_frame(
        self,
        path: tuple[int, ...],
        *,
        protected_parent: bool = False,
        depth: int = 0,
    ) -> list[Item]:
        if depth > 4 or not self._switch_path(path):
            return []

        try:
            marker = "gta-" + ("root" if not path else "-".join(map(str, path)))
            snap = self.driver.execute_script(JS_SNAPSHOT, marker)
        except WebDriverException as exc:
            return [Item("text", f"[frame unavailable: {exc.__class__.__name__}]", frame_path=path)]

        frame_url = str(snap.get("url") or "")
        frame_host = urlparse(frame_url).hostname
        frame_is_protected = protected_parent or _host_protected(frame_host)

        out: list[Item] = []
        if path:
            prefix = "  " * min(depth, 4)
            label = f"{prefix}FRAME {frame_host or '(embedded content)'}"
            if frame_is_protected:
                label += "  [protected verification content: read-only]"
            out.append(Item("frame", label, frame_path=path, protected=frame_is_protected))

        rows = list(snap.get("rows") or [])
        rows.sort(key=lambda r: (int(r.get("y") or 0), int(r.get("x") or 0)))

        seen_text: set[str] = set()
        for row in rows:
            kind = row.get("kind")
            if kind == "text":
                text = str(row.get("text") or "").strip()
                key = re.sub(r"\s+", " ", text).strip()
                if not key or key in seen_text:
                    continue
                seen_text.add(key)
                tag = str(row.get("tag") or "")
                prefix = {
                    "h1": "# ",
                    "h2": "## ",
                    "h3": "### ",
                    "li": "• ",
                    "blockquote": "> ",
                }.get(tag, "")
                out.append(Item("text", prefix + key, frame_path=path, protected=frame_is_protected))
                continue

            if kind != "control":
                continue

            role = str(row.get("role") or "control").lower()
            typ = str(row.get("type") or "").lower()
            label = str(row.get("label") or role).strip()
            value = str(row.get("value") or "").strip()
            checked = bool(row.get("checked"))
            disabled = bool(row.get("disabled"))
            href = str(row.get("href") or "")
            hint = " ".join((label, str(row.get("hint") or ""), href))
            protected = frame_is_protected or _looks_protected(hint)

            if role in ("checkbox", "switch", "radio"):
                state = "[x]" if checked else "[ ]"
                display = f"{state} {label}"
            elif role in ("textbox", "searchbox", "combobox", "spinbutton"):
                suffix = f" = {value}" if value else ""
                display = f"<{role}> {label}{suffix}"
            elif role == "link":
                display = f"<link> {label}"
            elif role == "button":
                display = f"[ {label} ]"
            else:
                display = f"<{role}> {label}"

            if disabled:
                display += "  (disabled)"
            if protected:
                display += "  [read-only verification control]"

            out.append(Item(
                "control",
                display,
                frame_path=path,
                dom_id=str(row.get("dom_id") or ""),
                role=role,
                type=typ,
                protected=protected,
                disabled=disabled,
                checked=checked,
                href=href,
            ))

        # Recurse into frames. Selenium can switch into cross-origin frames;
        # we inspect their semantic content but do not activate protected ones.
        if not frame_is_protected:
            frames = list(snap.get("frames") or [])
            for frame_meta in frames:
                index = int(frame_meta.get("index") or 0)
                child_path = path + (index,)
                child_protected = self._frame_protected(child_path, frame_meta)
                out.extend(
                    self._snapshot_frame(
                        child_path,
                        protected_parent=child_protected,
                        depth=depth + 1,
                    )
                )
        else:
            # For a protected frame, show only semantic text/control state from
            # the frame itself. Do not recursively enumerate nested challenge UI.
            pass

        return out

    def refresh(self) -> None:
        try:
            self.driver.switch_to.default_content()
            self.page_title = self.driver.title or ""
            self.page_url = self.driver.current_url or ""
        except WebDriverException:
            self.page_title = ""
            self.page_url = ""

        items = self._snapshot_frame(())
        if not items:
            items = [Item("text", "(page has no terminal-renderable content)")]

        self.items = items
        self.selected = min(self.selected, max(0, len(self.items) - 1))
        self.last_refresh = time.monotonic()

    # ---------- actions ----------

    def _find_dom_element(self, item: Item):
        if not item.dom_id or not self._switch_path(item.frame_path):
            return None
        try:
            return self.driver.find_element(
                By.CSS_SELECTOR,
                f'[data-grok-ascii-id="{item.dom_id}"]',
            )
        except (StaleElementReferenceException, WebDriverException):
            return None

    def _blocked(self, item: Item) -> bool:
        if item.protected:
            self.message = (
                "This is a dedicated anti-bot verification control. "
                "It is rendered for visibility but is not activated by the terminal frontend."
            )
            return True
        if item.disabled:
            self.message = "Control is disabled."
            return True
        return False

    def activate(self, item: Item) -> None:
        if item.kind != "control" or self._blocked(item):
            return
        el = self._find_dom_element(item)
        if el is None:
            self.message = "Page changed; refreshing element map."
            self.refresh()
            return
        try:
            if item.role in ("textbox", "searchbox", "combobox", "spinbutton"):
                self.edit(item)
                return
            el.click()
            self.message = f"Activated {item.role}: {item.text}"
            time.sleep(0.35)
            self.refresh()
        except WebDriverException as exc:
            self.message = f"Activation failed: {exc.__class__.__name__}"
            self.refresh()

    def toggle(self, item: Item) -> None:
        if item.kind != "control" or item.role not in ("checkbox", "radio", "switch"):
            self.message = "Selected item is not a checkbox/radio/switch."
            return
        self.activate(item)

    def edit(self, item: Item) -> None:
        if item.kind != "control" or self._blocked(item):
            return
        if item.role not in ("textbox", "searchbox", "combobox", "spinbutton"):
            self.message = "Selected control is not text-editable."
            return

        value = self.prompt(f"Value for {item.text}")
        if value == "":
            self.message = "Edit cancelled/empty."
            return

        el = self._find_dom_element(item)
        if el is None:
            self.message = "Page changed; refreshing element map."
            self.refresh()
            return

        try:
            el.click()
            try:
                el.send_keys(Keys.CONTROL, "a")
                el.send_keys(Keys.BACKSPACE)
            except WebDriverException:
                try:
                    el.clear()
                except WebDriverException:
                    pass
            el.send_keys(value)
            self.message = "Text entered. Press Enter on the relevant submit/continue control when ready."
            self.refresh()
        except WebDriverException as exc:
            self.message = f"Edit failed: {exc.__class__.__name__}"
            self.refresh()

    def goto(self) -> None:
        raw = self.prompt("HTTPS URL (grok.com / x.ai only)").strip()
        if not raw:
            return
        parsed = urlparse(raw)
        if parsed.scheme != "https" or not _host_allowed(parsed.hostname):
            self.message = "Navigation refused: URL must be HTTPS on grok.com/x.ai or allowed verification origin."
            return
        try:
            self.driver.switch_to.default_content()
            self.driver.get(raw)
            self.message = f"Navigated to {parsed.hostname}"
            self.refresh()
        except WebDriverException as exc:
            self.message = f"Navigation failed: {exc.__class__.__name__}"

    # ---------- curses rendering ----------

    def _selectable_indices(self) -> list[int]:
        return [i for i, x in enumerate(self.items) if x.kind == "control"]

    def _next_control(self, direction: int) -> None:
        selectable = self._selectable_indices()
        if not selectable:
            return
        if self.selected not in selectable:
            self.selected = selectable[0 if direction > 0 else -1]
            return
        pos = selectable.index(self.selected)
        self.selected = selectable[(pos + direction) % len(selectable)]

    def _ensure_visible(self, body_rows: int) -> None:
        if self.selected < self.top:
            self.top = self.selected
        if self.selected >= self.top + body_rows:
            self.top = self.selected - body_rows + 1
        self.top = max(0, min(self.top, max(0, len(self.items) - body_rows)))

    def _draw(self) -> None:
        self.s.erase()
        h, w = self.s.getmaxyx()
        body_rows = max(1, h - 7)
        self._ensure_visible(body_rows)

        title = f" ASCII CHROMIUM / {self.page_title[:60]} "
        try:
            self.s.addnstr(0, 0, title, max(0, w - 1), curses.A_BOLD)
            self.s.addnstr(1, 0, self.page_url, max(0, w - 1), curses.A_DIM)
            self.s.hline(2, 0, curses.ACS_HLINE, max(0, w - 1))
        except curses.error:
            pass

        row = 3
        end = min(len(self.items), self.top + body_rows)
        for idx in range(self.top, end):
            item = self.items[idx]
            selected = idx == self.selected
            attr = curses.A_REVERSE if selected else curses.A_NORMAL
            if item.kind == "frame":
                attr |= curses.A_BOLD
            elif item.protected:
                attr |= curses.A_DIM

            marker = ">" if selected else " "
            number = f"{idx + 1:03d}"
            prefix = f"{marker}{number} "
            width = max(10, w - len(prefix) - 1)
            wrapped = textwrap.wrap(
                item.text,
                width=width,
                replace_whitespace=False,
                drop_whitespace=False,
            ) or [""]

            for line_no, line in enumerate(wrapped[:3]):
                if row >= h - 4:
                    break
                try:
                    pfx = prefix if line_no == 0 else " " * len(prefix)
                    self.s.addnstr(row, 0, pfx + line, max(0, w - 1), attr)
                except curses.error:
                    pass
                row += 1

        try:
            self.s.hline(h - 4, 0, curses.ACS_HLINE, max(0, w - 1))
            keys = "j/k move  Tab controls  Enter activate  e edit  Space toggle  r reload  b back  f forward  g URL  q exit"
            self.s.addnstr(h - 3, 0, keys, max(0, w - 1), curses.A_DIM)
            self.s.addnstr(
                h - 2,
                0,
                f"items={len(self.items)}  selected={self.selected + 1}/{len(self.items)}",
                max(0, w - 1),
                curses.A_DIM,
            )
            self.s.addnstr(h - 1, 0, self.message, max(0, w - 1), curses.A_DIM)
        except curses.error:
            pass
        self.s.refresh()

    def run(self) -> None:
        old_timeout = -1
        try:
            curses.curs_set(0)
            self.s.timeout(-1)
            self.refresh()

            while True:
                # A modest background refresh makes React/Cloudflare state
                # changes visible without hammering the page.
                if time.monotonic() - self.last_refresh > 3.0:
                    old_sel = self.selected
                    self.refresh()
                    self.selected = min(old_sel, len(self.items) - 1)

                self._draw()
                ch = self.s.get_wch()

                if isinstance(ch, str):
                    if ch.lower() == "q" or ch == "\x1b":
                        return
                    if ch.lower() == "j":
                        self.selected = min(len(self.items) - 1, self.selected + 1)
                    elif ch.lower() == "k":
                        self.selected = max(0, self.selected - 1)
                    elif ch == "\t":
                        self._next_control(+1)
                    elif ch in ("\n", "\r"):
                        self.activate(self.items[self.selected])
                    elif ch == " ":
                        self.toggle(self.items[self.selected])
                    elif ch.lower() == "e":
                        self.edit(self.items[self.selected])
                    elif ch.lower() == "r":
                        try:
                            self.driver.switch_to.default_content()
                            self.driver.refresh()
                            time.sleep(0.35)
                        except WebDriverException:
                            pass
                        self.refresh()
                    elif ch.lower() == "b":
                        try:
                            self.driver.switch_to.default_content()
                            self.driver.back()
                            time.sleep(0.35)
                        except WebDriverException:
                            pass
                        self.refresh()
                    elif ch.lower() == "f":
                        try:
                            self.driver.switch_to.default_content()
                            self.driver.forward()
                            time.sleep(0.35)
                        except WebDriverException:
                            pass
                        self.refresh()
                    elif ch.lower() == "g":
                        self.goto()
                else:
                    if ch == curses.KEY_UP:
                        self.selected = max(0, self.selected - 1)
                    elif ch == curses.KEY_DOWN:
                        self.selected = min(len(self.items) - 1, self.selected + 1)
                    elif ch == curses.KEY_NPAGE:
                        self.selected = min(len(self.items) - 1, self.selected + 10)
                    elif ch == curses.KEY_PPAGE:
                        self.selected = max(0, self.selected - 10)
                    elif ch == curses.KEY_BTAB:
                        self._next_control(-1)
                    elif ch == curses.KEY_ENTER:
                        self.activate(self.items[self.selected])
        finally:
            self.s.timeout(-1)
            try:
                self.driver.switch_to.default_content()
            except Exception:
                pass
            self.status_cb("Returned from ASCII Chromium.")
