/**
 * localdb.js — drop-in replacement for the Firebase Realtime Database client
 * used by admin/overlay. Same function names, backed by the local SQLite
 * server (REST for reads/writes, one shared WebSocket for live updates).
 *
 * Migration: change only the imports, e.g.
 *   import { initializeApp } from "./js/localdb.js";
 *   import { getDatabase, ref, set, get, update, onValue, push, remove } from "./js/localdb.js";
 */

export function initializeApp(_cfg) { return {}; }
export function getDatabase(_app) { return { __local: true }; }

export function ref(_db, path = "") {
  return { path: String(path).replace(/^\/+|\/+$/g, "") };
}

class Snapshot {
  constructor(value, r) { this._v = value ?? null; this.ref = r; this.key = r?.path?.split("/").pop() || null; }
  val() { return this._v; }
  exists() { return this._v !== null && this._v !== undefined; }
  forEach(cb) {
    if (this._v && typeof this._v === "object")
      for (const [k, v] of Object.entries(this._v)) if (cb(new Snapshot(v, ref(null, `${this.ref.path}/${k}`))) === true) return;
  }
}

const url = (p) => `/api/db/${p.split("/").map(encodeURIComponent).join("/")}`;

async function call(method, path, body) {
  const res = await fetch(url(path), {
    method,
    credentials: "same-origin",
    headers: body === undefined ? {} : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) {
    if (!location.pathname.endsWith("login.html")) location.href = "/login.html";
    throw new Error("Not authenticated");
  }
  if (!res.ok) throw new Error(`DB ${method} ${path} failed (${res.status})`);
  return res.json();
}

export async function get(r) { return new Snapshot(await call("GET", r.path), r); }
export async function set(r, value) { await call("PUT", r.path, value === undefined ? null : value); }
export async function update(r, patch) { await call("PATCH", r.path, patch); }
export async function remove(r) { await call("DELETE", r.path); }
export async function push(r, value) {
  const { key } = await call("POST", r.path, value === undefined ? null : value);
  return { path: `${r.path}/${key}`, key };
}

// ---------------------------------------------------------------- live updates
const listeners = new Map(); // path -> Set(callback)
const connListeners = new Set();
let ws = null, wsOpen = false, retry = 500;

function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  ws = new WebSocket(`${proto}://${location.host}/ws/db`);
  ws.onopen = () => {
    wsOpen = true; retry = 500;
    for (const p of listeners.keys()) ws.send(JSON.stringify({ type: "sub", path: p }));
    connListeners.forEach((cb) => cb(new Snapshot(true, ref(null, ".info/connected"))));
  };
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type !== "value") return;
    const set_ = listeners.get(m.path);
    if (set_) set_.forEach((cb) => cb(new Snapshot(m.value, ref(null, m.path))));
  };
  ws.onclose = () => {
    wsOpen = false;
    connListeners.forEach((cb) => cb(new Snapshot(false, ref(null, ".info/connected"))));
    setTimeout(connect, retry);
    retry = Math.min(retry * 2, 5000);
  };
  ws.onerror = () => ws.close();
}

export function onValue(r, cb) {
  if (r.path === ".info/connected") {
    connListeners.add(cb);
    if (!ws) connect();
    cb(new Snapshot(wsOpen, r));
    return () => connListeners.delete(cb);
  }
  if (!listeners.has(r.path)) listeners.set(r.path, new Set());
  listeners.get(r.path).add(cb);
  if (!ws) connect();
  else if (wsOpen) ws.send(JSON.stringify({ type: "sub", path: r.path }));
  return () => {
    const s = listeners.get(r.path);
    if (!s) return;
    s.delete(cb);
    if (!s.size) {
      listeners.delete(r.path);
      if (wsOpen) ws.send(JSON.stringify({ type: "unsub", path: r.path }));
    }
  };
}
