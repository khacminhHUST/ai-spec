/**
 * Chạy nhúng trong web Tri thức (deploy/trithuc): đường dẫn gốc, tiền tố API, đăng nhập dùng chung.
 *
 * Cấu hình lúc build (frontend/.env.production hoặc biến môi trường):
 *   VITE_BASE            "/thongtinduan/"  – đường dẫn gốc của app (vite.config.ts › base)
 *   VITE_API_PREFIX      "api"             – API nằm ở <base>api/… (gateway bỏ tiền tố trước khi vào backend)
 *   VITE_AUTH_TOKEN_KEY  tên khóa token của web Tri thức trong sessionStorage/localStorage (STORAGE_KEY_TOKEN)
 *   VITE_AUTH_USER_CLAIM trường email trong payload JWT (mặc định thử "email" rồi "sub")
 * Không đặt gì (chạy local `npm run dev`) → không đổi hành vi: gọi thẳng /chat, /knowledge… như cũ.
 *
 * Gắn vào window.fetch một lần (installDeployAdapter) để mọi lời gọi API hiện có tự đi đúng đường và kèm token,
 * không phải sửa từng component. Link tải file (thẻ <a>) không gửi được header nên được chặn và tải qua fetch.
 */

const BASE = import.meta.env.BASE_URL || "/";
const API_PREFIX = String(import.meta.env.VITE_API_PREFIX ?? "").replace(/^\/+|\/+$/g, "");
const TOKEN_KEY = String(import.meta.env.VITE_AUTH_TOKEN_KEY ?? "");
const USER_CLAIM = String(import.meta.env.VITE_AUTH_USER_CLAIM ?? "");
// Làm mới token giống web Tri thức (FE/services/authService.ts): POST {refresh_token} → {access_token, refresh_token},
// ghi lại cùng kho; khóa navigator.locks trùng tên để trang cha và iframe không làm mới chồng nhau (refresh token
// xoay vòng). Trang cha chỉ làm mới khi chính nó gặp 401 → người dùng chỉ thao tác trong iframe sẽ bị hết phiên.
const REFRESH_KEY = String(import.meta.env.VITE_AUTH_REFRESH_KEY ?? "");
const REFRESH_URL = String(import.meta.env.VITE_AUTH_REFRESH_URL ?? "");
const REFRESH_LOCK = String(import.meta.env.VITE_AUTH_REFRESH_LOCK ?? "");
const REFRESH_BEFORE_S = 60; // token còn ít hơn 60 s → làm mới trước khi gửi
// Các nhóm route của backend (giống proxy trong vite.config.ts)
const API_ROOTS = ["/chat", "/conversations", "/mcp", "/knowledge", "/projects"];

/** Đăng nhập dùng chung với web Tri thức (user_id lấy từ token, không cho sửa tay). */
export const authMode = TOKEN_KEY !== "";
export const AUTH_EXPIRED_EVENT = "mep:auth-expired";

const isApiPath = (p: string) => API_ROOTS.some((r) => p === r || p.startsWith(r + "/") || p.startsWith(r + "?"));

/** "/knowledge/tree" → "/thongtinduan/api/knowledge/tree" (chạy local: giữ nguyên). */
export function apiUrl(path: string): string {
  if (!isApiPath(path)) return path;
  return BASE.replace(/\/+$/, "") + (API_PREFIX ? `/${API_PREFIX}` : "") + path;
}

const ok = (v: string | null): v is string => !!v && v !== "undefined" && v !== "null";
const unquote = (v: string) => (v.startsWith('"') ? (JSON.parse(v) as string) : v);

/** Token của web Tri thức: sessionStorage (không "ghi nhớ") trước, rồi localStorage. */
export function readToken(): string | null {
  if (!TOKEN_KEY) return null;
  // Như Tri thức (FE/services/authService.ts › khoPhien, coGiaTri): "Ghi nhớ" → localStorage, không → sessionStorage
  // của tab; bỏ giá trị rác "undefined"/"null".
  try {
    const s = sessionStorage.getItem(TOKEN_KEY);
    const raw = ok(s) ? s : localStorage.getItem(TOKEN_KEY);
    return ok(raw) ? unquote(raw) : null;
  } catch {
    return null;
  }
}

/** Kho đang giữ phiên – cùng cách chọn với Tri thức: sessionStorage nếu có token/refresh ở đó, không thì localStorage. */
function sessionStore(): Storage {
  return ok(sessionStorage.getItem(REFRESH_KEY)) || ok(sessionStorage.getItem(TOKEN_KEY)) ? sessionStorage : localStorage;
}

/** Số giây token còn hiệu lực (không có exp → Infinity). */
function secondsLeft(token: string): number {
  const exp = jwtPayload(token)?.exp;
  return typeof exp === "number" ? exp - Date.now() / 1000 : Infinity;
}

let refreshing: Promise<string | null> | null = null;

/** Làm mới token → token mới; null nếu không làm mới được (refresh token hết hạn / lỗi mạng / chưa cấu hình).
 * `stale`: token vừa bị từ chối – trong lúc chờ khóa mà nơi khác (trang cha) đã làm mới thì dùng luôn token đó. */
function refreshToken(stale: string | null, fetchRaw: typeof fetch): Promise<string | null> {
  if (!REFRESH_KEY || !REFRESH_URL) return Promise.resolve(null);
  if (refreshing) return refreshing;
  const run = async (): Promise<string | null> => {
    const cur = readToken();
    if (cur && cur !== stale && secondsLeft(cur) > REFRESH_BEFORE_S) return cur;
    const store = sessionStore();
    const rt = store.getItem(REFRESH_KEY);
    if (!ok(rt)) return null;
    try {
      const r = await fetchRaw(REFRESH_URL, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: unquote(rt) }),
      });
      if (!r.ok) return null;
      const j = (await r.json()) as { access_token?: string; refresh_token?: string };
      if (!j.access_token) return null;
      store.setItem(TOKEN_KEY, j.access_token);
      if (j.refresh_token) store.setItem(REFRESH_KEY, j.refresh_token);
      return j.access_token;
    } catch {
      return null;
    }
  };
  const locks = (navigator as Navigator & { locks?: LockManager }).locks;
  refreshing = (REFRESH_LOCK && locks?.request ? locks.request(REFRESH_LOCK, run) : run()).finally(() => {
    refreshing = null;
  });
  return refreshing;
}

function jwtPayload(token: string): Record<string, unknown> | null {
  try {
    const part = token.split(".")[1];
    const b64 = part.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(part.length / 4) * 4, "=");
    const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    return JSON.parse(new TextDecoder().decode(bytes)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

/** Email người dùng trong token (chỉ để hiển thị / gửi kèm; backend tự đọc lại từ token, không tin giá trị này). */
export function tokenUser(): string | null {
  const t = readToken();
  const p = t ? jwtPayload(t) : null;
  if (!p) return null;
  for (const k of USER_CLAIM ? [USER_CLAIM] : ["email", "sub"]) {
    const v = p[k];
    if (typeof v === "string" && v) return v;
  }
  return null;
}

const USER_KEY = "mep.user_id"; // App.tsx đọc khóa này khi khởi động

/** Ghi email trong token vào chỗ App.tsx đọc user_id (backend vẫn tự lấy lại từ token, không tin giá trị này). */
function syncUser() {
  const u = tokenUser();
  try {
    if (u) localStorage.setItem(USER_KEY, u);
  } catch {
    /* bỏ qua */
  }
  if (!u) showBanner(NO_SESSION);
}

// Tab mới mở thẳng /thongtinduan/ không thấy phiên "không ghi nhớ" (sessionStorage chỉ thuộc tab đã đăng nhập)
const NO_SESSION =
  "Chưa thấy phiên đăng nhập Tri thức ở tab này – mở từ mục Thông tin dự án trên web Tri thức " +
  "(hoặc đăng nhập có tick “Ghi nhớ”) rồi tải lại trang.";
const EXPIRED = "Phiên đăng nhập web Tri thức đã hết hạn – đăng nhập lại rồi tải lại trang.";

function showBanner(text: string) {
  let el = document.getElementById("mep-auth-banner");
  if (!el) {
    el = document.createElement("div");
    el.id = "mep-auth-banner";
    el.setAttribute("role", "alert");
    el.style.cssText =
      "position:fixed;top:0;left:0;right:0;z-index:9999;padding:10px 16px;font:14px system-ui,sans-serif;" +
      "background:#fef2f2;color:#991b1b;border-bottom:1px solid #fecaca;text-align:center;cursor:pointer";
    el.title = "Bấm để ẩn";
    el.onclick = () => el?.remove();
    document.body.appendChild(el);
  }
  el.textContent = text;
}

function notifyExpired() {
  showBanner(readToken() ? EXPIRED : NO_SESSION);
  window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  // Trang cha (web Tri thức) có thể làm mới token rồi app đọc lại ở request sau.
  if (window.parent !== window) window.parent.postMessage({ type: AUTH_EXPIRED_EVENT }, window.location.origin);
}

/** Tải file qua fetch (kèm token) thay cho thẻ <a href> của API. */
async function openApiLink(a: HTMLAnchorElement, href: string) {
  const save = a.hasAttribute("download");
  const win = save ? null : window.open("", "_blank"); // mở ngay trong cú click để không bị chặn popup
  try {
    const r = await fetch(href);
    if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
    const url = URL.createObjectURL(await r.blob());
    if (win) {
      win.location.href = url;
    } else {
      const tmp = document.createElement("a");
      tmp.href = url;
      tmp.download = a.getAttribute("download") || href.split("/").pop()?.split("?")[0] || "file";
      tmp.click();
    }
    setTimeout(() => URL.revokeObjectURL(url), 60_000);
  } catch (e) {
    win?.close();
    alert(`Không tải được file: ${e instanceof Error ? e.message : e}`);
  }
}

let installed = false;

export function installDeployAdapter() {
  if (installed || (!API_PREFIX && BASE === "/" && !authMode)) return;
  installed = true;
  if (authMode) {
    syncUser();
    // Đổi tài khoản / đăng nhập lại ở trang cha → lần mở lại tab này dùng đúng user
    window.addEventListener("focus", () => {
      const u = tokenUser();
      try {
        if (u && localStorage.getItem(USER_KEY) !== u) window.location.reload();
      } catch {
        /* bỏ qua */
      }
    });
    // Trang Tri thức đã làm mới token sau "mep:auth-expired" (FE/pages/ThongTinDuAnThuNghiem.tsx) → ẩn thông báo;
    // request sau tự đọc token mới.
    window.addEventListener("message", (e) => {
      if (e.origin === window.location.origin && e.source === window.parent && e.data?.type === "mep:auth-refreshed")
        document.getElementById("mep-auth-banner")?.remove();
    });
  }
  const orig = window.fetch.bind(window);
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit) => {
    if (typeof input !== "string" || !isApiPath(input)) return orig(input, init);
    const headers = new Headers(init?.headers);
    let token = readToken();
    if (token && authMode && secondsLeft(token) < REFRESH_BEFORE_S) token = (await refreshToken(token, orig)) ?? token;
    if (token) headers.set("Authorization", `Bearer ${token}`);
    let r = await orig(apiUrl(input), { ...init, headers });
    if (r.status === 401 && authMode) {
      // Token bị từ chối (hết hạn giữa chừng) → làm mới rồi gửi lại 1 lần (body của app là chuỗi/FormData – gửi lại được)
      const fresh = await refreshToken(token, orig);
      if (fresh && fresh !== token) {
        headers.set("Authorization", `Bearer ${fresh}`);
        r = await orig(apiUrl(input), { ...init, headers });
        token = fresh;
      }
    }
    if (r.status === 401 && authMode) {
      notifyExpired();
      // nginx server 1 trả trang HTML 401 → thay bằng câu ngắn để giao diện không in nguyên HTML
      return new Response(token ? "Phiên đăng nhập Tri thức đã hết hạn" : "Chưa đăng nhập web Tri thức", {
        status: 401,
        headers: { "Content-Type": "text/plain; charset=utf-8" },
      });
    }
    return r;
  };
  document.addEventListener(
    "click",
    (e) => {
      const a = (e.target as Element | null)?.closest?.("a");
      const href = a?.getAttribute("href");
      if (!a || !href || !isApiPath(href) || e.defaultPrevented || e.button !== 0) return;
      e.preventDefault();
      void openApiLink(a, href);
    },
    true,
  );
}
