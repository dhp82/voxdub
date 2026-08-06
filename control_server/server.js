/**
 * AutoDub control server — bật/tắt app từ xa (Node.js, zero dependency).
 *
 * Chạy trên VPS/máy chủ riêng, không liên quan gì tới code Python của app.
 * Xóa cả thư mục control_server/ là gỡ bỏ hoàn toàn phía server.
 *
 * Chạy (chỉ cần Node >= 18, KHÔNG cần npm install):
 *     export ADMIN_TOKEN=mat-khau-bi-mat   (Windows: set ADMIN_TOKEN=...)
 *     node server.js                        (mặc định 127.0.0.1:3001, đổi bằng PORT=/HOST=)
 *     Đứng sau nginx reverse-proxy (TLS) thì đặt thêm TRUST_PROXY=1.
 *
 * API:
 *     GET  /status                 -> {"enabled": true/false, "message": "..."}
 *          App gọi endpoint này lúc khởi động (public, không cần token).
 *     GET  /health                 -> {"ok": true, "uptime_s": ..., ...}
 *          Cho monitoring/uptime checker (public).
 *     POST /admin/set              -> đổi trạng thái
 *          Header:  X-Admin-Token: <ADMIN_TOKEN>
 *          Body:    {"enabled": false, "message": "Đang bảo trì"}
 *     GET  /admin/status           -> trạng thái + thống kê (cần token)
 *
 * An toàn:
 *     - So token bằng timingSafeEqual (chống timing attack).
 *     - Rate-limit: /admin/* tối đa 10 req/phút/IP; sai token 5 lần → khóa
 *       IP 15 phút. /status tối đa 60 req/phút/IP.
 *     - Body giới hạn 4 KB; state ghi kiểu atomic (tmp + rename) — không bao
 *       giờ hỏng file khi mất điện giữa chừng.
 *     - Log mọi thay đổi trạng thái + mọi lần sai token vào access.log.
 */
"use strict";

const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");

// ------------------------------------------------------------- config ----

const STATE_FILE = path.join(__dirname, "state.json");
const LOG_FILE = path.join(__dirname, "access.log");
const DEFAULT_STATE = { enabled: true, message: "" };

// KHÔNG có token mặc định — bắt buộc đặt ADMIN_TOKEN qua biến môi trường
// (openssl rand -hex 24). Thiếu token → mọi request /admin trả 503.
const ADMIN_TOKEN = process.env.ADMIN_TOKEN || "";
// Mặc định chỉ nghe loopback: server đứng sau nginx (TLS) trên cùng máy.
// Muốn nghe ra ngoài (không khuyến nghị) thì đặt HOST=0.0.0.0.
const PORT = Number(process.env.PORT) || 3001;
const HOST = process.env.HOST || "127.0.0.1";
const MAX_BODY_BYTES = 4096;
// access.log vượt cỡ này thì xoay sang access.log.1 (giữ đúng 1 bản cũ).
const MAX_LOG_BYTES = 5 * 1024 * 1024;

// Rate limits (per IP, sliding window đơn giản theo phút)
const STATUS_RPM = 60;
const ADMIN_RPM = 10;
const BAD_TOKEN_LIMIT = 5;          // sai token N lần...
const BAD_TOKEN_BAN_MS = 15 * 60_000;  // ...thì khóa IP 15 phút

const START_TIME = Date.now();
let statusHits = 0;
let adminChanges = 0;

// ------------------------------------------------------------- helpers ---

function ts() {
  return new Date().toISOString();
}

function logLine(msg) {
  const line = `${ts()} ${msg}`;
  console.log(line);
  // Ghi log best-effort — lỗi đĩa không được làm sập server. Trước khi ghi,
  // xoay file nếu đã quá cỡ để access.log không phình vô hạn trên VPS.
  fs.stat(LOG_FILE, (err, st) => {
    if (!err && st.size > MAX_LOG_BYTES) {
      try { fs.renameSync(LOG_FILE, LOG_FILE + ".1"); } catch { /* bỏ qua */ }
    }
    fs.appendFile(LOG_FILE, line + "\n", () => {});
  });
}

function clientIp(req) {
  // Chỉ tin X-Forwarded-For khi đứng sau reverse-proxy (TRUST_PROXY=1) —
  // header này client tự đặt được, tin mù sẽ bị vượt rate-limit/ban IP.
  if (process.env.TRUST_PROXY === "1") {
    const fwd = req.headers["x-forwarded-for"];
    if (fwd) return String(fwd).split(",")[0].trim();
  }
  return req.socket.remoteAddress || "?";
}

function tokenOk(given) {
  if (!ADMIN_TOKEN || typeof given !== "string" || !given) return false;
  const a = Buffer.from(given);
  const b = Buffer.from(ADMIN_TOKEN);
  // timingSafeEqual đòi hỏi cùng độ dài — so hash để chấp nhận mọi độ dài.
  const ha = crypto.createHash("sha256").update(a).digest();
  const hb = crypto.createHash("sha256").update(b).digest();
  return crypto.timingSafeEqual(ha, hb);
}

// --------------------------------------------------------- rate limiting -

const hitWindows = new Map();   // ip -> {minute, statusCount, adminCount}
const badTokens = new Map();    // ip -> {count, bannedUntil}

function rateLimited(ip, kind) {
  const minute = Math.floor(Date.now() / 60_000);
  let w = hitWindows.get(ip);
  if (!w || w.minute !== minute) {
    w = { minute, statusCount: 0, adminCount: 0 };
    hitWindows.set(ip, w);
  }
  if (kind === "status") {
    w.statusCount += 1;
    return w.statusCount > STATUS_RPM;
  }
  w.adminCount += 1;
  return w.adminCount > ADMIN_RPM;
}

function ipBanned(ip) {
  const b = badTokens.get(ip);
  return Boolean(b && b.bannedUntil > Date.now());
}

function recordBadToken(ip) {
  const b = badTokens.get(ip) || { count: 0, bannedUntil: 0 };
  b.count += 1;
  if (b.count >= BAD_TOKEN_LIMIT) {
    b.bannedUntil = Date.now() + BAD_TOKEN_BAN_MS;
    b.count = 0;
    logLine(`BAN ${ip} — sai token ${BAD_TOKEN_LIMIT} lần, khóa 15 phút`);
  }
  badTokens.set(ip, b);
}

// Dọn bộ nhớ rate-limit mỗi 10 phút để map không phình vô hạn.
setInterval(() => {
  const minute = Math.floor(Date.now() / 60_000);
  for (const [ip, w] of hitWindows) if (w.minute < minute - 2) hitWindows.delete(ip);
  for (const [ip, b] of badTokens) {
    if (b.bannedUntil < Date.now() && b.count === 0) badTokens.delete(ip);
  }
}, 10 * 60_000).unref();

// --------------------------------------------------------------- state ---

function readState() {
  try {
    const data = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    return {
      enabled: Boolean(data.enabled ?? true),
      message: String(data.message ?? ""),
      updated_at: String(data.updated_at ?? ""),
    };
  } catch {
    return { ...DEFAULT_STATE, updated_at: "" };
  }
}

function writeState(state) {
  // Atomic: ghi file tạm rồi rename — mất điện giữa chừng không hỏng state.
  const tmp = STATE_FILE + ".tmp";
  fs.writeFileSync(tmp, JSON.stringify(state, null, 2), "utf8");
  fs.renameSync(tmp, STATE_FILE);
}

// ------------------------------------------------------------- server ----

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    "Content-Type": "application/json; charset=utf-8",
    "Content-Length": Buffer.byteLength(body),
    "Cache-Control": "no-store",
  });
  res.end(body);
}

const server = http.createServer((req, res) => {
  const ip = clientIp(req);
  let url;
  try {
    url = new URL(req.url, `http://${req.headers.host || "localhost"}`);
  } catch {
    return sendJson(res, 400, { error: "Bad request" });
  }

  // ---- public: app AutoDub gọi lúc khởi động + định kỳ ----
  if (req.method === "GET" && url.pathname === "/status") {
    if (rateLimited(ip, "status")) {
      return sendJson(res, 429, { error: "Too many requests" });
    }
    statusHits += 1;
    const { enabled, message } = readState();
    return sendJson(res, 200, { enabled, message });
  }

  // ---- public: uptime monitoring ----
  if (req.method === "GET" && url.pathname === "/health") {
    return sendJson(res, 200, {
      ok: true,
      uptime_s: Math.round((Date.now() - START_TIME) / 1000),
      enabled: readState().enabled,
    });
  }

  // ---- admin ----
  if (url.pathname.startsWith("/admin/")) {
    if (ipBanned(ip)) {
      return sendJson(res, 403, { error: "IP tạm khóa do sai token nhiều lần" });
    }
    if (rateLimited(ip, "admin")) {
      return sendJson(res, 429, { error: "Too many requests" });
    }
    if (!ADMIN_TOKEN) {
      return sendJson(res, 503, { error: "Server chưa đặt biến môi trường ADMIN_TOKEN" });
    }
    if (!tokenOk(req.headers["x-admin-token"])) {
      recordBadToken(ip);
      logLine(`AUTH-FAIL ${ip} ${req.method} ${url.pathname}`);
      return sendJson(res, 401, { error: "Sai token" });
    }

    if (req.method === "GET" && url.pathname === "/admin/status") {
      return sendJson(res, 200, {
        ...readState(),
        uptime_s: Math.round((Date.now() - START_TIME) / 1000),
        status_hits: statusHits,
        admin_changes: adminChanges,
      });
    }

    if (req.method === "POST" && url.pathname === "/admin/set") {
      let raw = "";
      let overflow = false;
      req.on("data", (chunk) => {
        raw += chunk;
        if (raw.length > MAX_BODY_BYTES && !overflow) {
          overflow = true;
          sendJson(res, 413, { error: "Body quá lớn" });
          req.destroy();
        }
      });
      req.on("end", () => {
        if (overflow) return;
        let body;
        try {
          body = JSON.parse(raw);
        } catch {
          return sendJson(res, 400, { error: "Body phải là JSON" });
        }
        if (typeof body.enabled !== "boolean") {
          return sendJson(res, 400, { error: "Thiếu trường enabled (true/false)" });
        }
        const state = {
          enabled: body.enabled,
          message: String(body.message ?? "").slice(0, 500),
          updated_at: ts(),
        };
        try {
          writeState(state);
        } catch (e) {
          logLine(`ERROR ghi state.json: ${e.message}`);
          return sendJson(res, 500, { error: "Không ghi được state" });
        }
        adminChanges += 1;
        logLine(`SET ${ip} enabled=${state.enabled} message="${state.message}"`);
        return sendJson(res, 200, state);
      });
      return;
    }

    return sendJson(res, 404, { error: "Not found" });
  }

  sendJson(res, 404, { error: "Not found" });
});

// Header timeout ngắn chống slowloris cơ bản.
server.headersTimeout = 10_000;
server.requestTimeout = 15_000;

server.listen(PORT, HOST, () => {
  logLine(`AutoDub control server chạy tại http://${HOST}:${PORT}`);
  logLine(`Trạng thái hiện tại: ${JSON.stringify(readState())}`);
  if (!ADMIN_TOKEN) {
    logLine("CẢNH BÁO: chưa đặt ADMIN_TOKEN — /admin/* sẽ bị khóa (503).");
  }
});

// Tắt êm: đóng listener, cho request đang chạy 5s để xong.
for (const sig of ["SIGINT", "SIGTERM"]) {
  process.on(sig, () => {
    logLine(`Nhận ${sig} — đang tắt...`);
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 5000).unref();
  });
}
