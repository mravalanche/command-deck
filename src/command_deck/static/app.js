const dateFormat = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
const timeFormat = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const fullDateFormat = new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
const backupTimeFormat = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const lastConnectedFormat = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const clockDialog = document.getElementById("clock-dialog");
const clockTrigger = document.getElementById("clock-trigger");
const clockClose = document.getElementById("clock-close");
const connectionPanel = document.getElementById("connection-panel");
const connectionTime = document.getElementById("last-connected-time");
const connectionAge = document.getElementById("last-connected-age");
const connectionAnnouncement = document.getElementById("connection-announcement");
let lastConnected = new Date(); // The current document was served by Command Deck.
let connectionLost = false;
let checkingConnection = false;

function relativeAge(then, now) {
  const seconds = Math.max(0, Math.floor((now - then) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  const days = Math.floor(hours / 24);
  return `${days} ${days === 1 ? "day" : "days"} ago`;
}

function refreshConnectionAge(now = new Date()) {
  if (!connectionLost || !connectionTime || !connectionAge) return;
  connectionTime.dateTime = lastConnected.toISOString();
  connectionTime.textContent = lastConnectedFormat.format(lastConnected);
  connectionAge.textContent = `(${relativeAge(lastConnected, now)})`;
}

function setConnectionLost(lost) {
  if (connectionLost === lost) return;
  connectionLost = lost;
  document.body.classList.toggle("connection-lost", lost);
  if (connectionPanel) connectionPanel.hidden = !lost;
  if (connectionAnnouncement) connectionAnnouncement.textContent = lost ? "Lost connectivity to Command Deck" : "Connection to Command Deck restored";
  refreshConnectionAge();
  if (!lost && window.htmx) {
    const active = document.querySelector("#overview-live, #servers-live, #services-live, #events-live");
    if (active) window.htmx.trigger(active, "refresh");
  }
}

async function checkConnection() {
  if (checkingConnection) return;
  checkingConnection = true;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch("/healthz", { cache: "no-store", credentials: "same-origin", signal: controller.signal });
    const body = response.ok && !response.redirected && response.headers.get("content-type")?.includes("application/json") ? await response.json() : null;
    if (body?.status !== "ok") throw new Error("Command Deck did not respond");
    lastConnected = new Date();
    setConnectionLost(false);
  } catch {
    setConnectionLost(true);
  } finally {
    clearTimeout(timeout);
    checkingConnection = false;
  }
}

function refreshClockAndAges() {
  const now = new Date();
  refreshConnectionAge(now);
  const date = document.getElementById("clock-date");
  const time = document.getElementById("clock-time");
  if (date && time) {
    date.textContent = dateFormat.format(now);
    time.textContent = timeFormat.format(now);
    date.dateTime = now.toISOString();
    time.dateTime = now.toISOString();
  }
  const fullDate = document.querySelector(".clock-face-date");
  const fullTime = document.getElementById("clock-face-time");
  if (fullDate && fullTime) {
    fullDate.textContent = fullDateFormat.format(now);
    fullTime.textContent = timeFormat.format(now);
    fullTime.dateTime = now.toISOString();
  }
  document.querySelectorAll("[data-local-datetime]").forEach((element) => {
    const instant = new Date(element.dataset.localDatetime);
    if (!Number.isNaN(instant.getTime())) element.textContent = `${element.dataset.timeLabel || "Last success"}: ${backupTimeFormat.format(instant)}`;
  });
  document.querySelectorAll("[data-updated-at]").forEach((element) => {
    const observed = Date.parse(element.dataset.updatedAt);
    if (Number.isNaN(observed)) {
      element.textContent = "Updated: unknown";
      return;
    }
    const ageMinutes = Math.max(0, Math.floor((now.getTime() - observed) / 60000));
    element.textContent = ageMinutes < 1 ? "Updated just now" : ageMinutes < 60 ? `Updated ${ageMinutes} min ago` : ageMinutes < 1440 ? `Updated ${Math.floor(ageMinutes / 60)} hr ago` : `Updated ${Math.floor(ageMinutes / 1440)} days ago`;
  });
}

refreshClockAndAges();
setInterval(refreshClockAndAges, 1000);
document.body.addEventListener("htmx:afterSwap", refreshClockAndAges);
let serviceListScroll = null;
document.body.addEventListener("htmx:beforeSwap", (event) => {
  if (event.detail.target?.id !== "services-live") return;
  const list = event.detail.target.querySelector("#service-list");
  serviceListScroll = { top: list?.scrollTop ?? 0, focused: document.activeElement === list };
});
document.body.addEventListener("htmx:afterSwap", (event) => {
  if (event.detail.target?.id !== "services-live" || !serviceListScroll) return;
  const list = document.getElementById("service-list");
  if (list) {
    list.scrollTop = serviceListScroll.top;
    if (serviceListScroll.focused) list.focus({ preventScroll: true });
  }
  serviceListScroll = null;
});
clockTrigger?.addEventListener("click", () => {
  refreshClockAndAges();
  clockDialog?.showModal();
  clockClose?.focus();
});
clockClose?.addEventListener("click", () => clockDialog?.close());
clockDialog?.addEventListener("click", (event) => { if (event.target === clockDialog) clockDialog.close(); });
clockDialog?.addEventListener("close", () => clockTrigger?.focus());
checkConnection();
setInterval(checkConnection, 15000);
window.addEventListener("online", checkConnection);
document.addEventListener("visibilitychange", () => { if (!document.hidden) checkConnection(); });
document.body.addEventListener("htmx:sendError", () => setConnectionLost(true));
document.body.addEventListener("htmx:timeout", () => setConnectionLost(true));

let focusSelectedServer = false;
document.body.addEventListener("click", (event) => {
  const arrow = event.target.closest("[data-scroll-servers]");
  if (arrow) {
    document.getElementById("server-tabs")?.scrollBy({ left: Number(arrow.dataset.scrollServers) * 270, behavior: "smooth" });
  }
  if (event.target.closest(".server-tab")) focusSelectedServer = true;
});
document.body.addEventListener("htmx:afterSwap", () => {
  if (focusSelectedServer) document.querySelector(".server-tab--selected")?.focus();
  focusSelectedServer = false;
});
