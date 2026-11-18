import { useEffect, useState } from "react";
import { authMode } from "./deploy";

export type ThemePref = "light" | "dark" | "system";
const KEY = "mep.theme";
const HOST_DARK_KEY = "hawee_dark_mode"; // web Tri thức (FE/components/Layout.tsx) lưu "true" | "false"

/** Chạy trong web Tri thức (deploy/trithuc): giao diện sáng/tối theo trang Tri thức, không có nút chọn riêng. */
export const followsHost = authMode;

/** Tri thức đang tối? Ưu tiên class "dark" trên <html> trang cha (iframe cùng origin), rồi localStorage. */
function hostDark(): boolean {
  if (window.parent !== window) {
    try {
      return window.parent.document.documentElement.classList.contains("dark");
    } catch {
      /* khác origin → đọc localStorage */
    }
  }
  try {
    return localStorage.getItem(HOST_DARK_KEY) === "true";
  } catch {
    return false;
  }
}

function read(): ThemePref {
  if (followsHost) return hostDark() ? "dark" : "light";
  try {
    const v = localStorage.getItem(KEY);
    return v === "dark" || v === "system" ? v : "light"; // mặc định Sáng, đồng bộ app Hawee
  } catch {
    return "light";
  }
}

/** Sáng (mặc định) / Tối / Theo hệ thống – gắn data-theme lên <html>, nhớ trong localStorage.
 *  Nhúng trong Tri thức: đổi theo trang cha ngay khi người dùng bấm nút sáng/tối của Tri thức. */
export function useTheme(): [ThemePref, (t: ThemePref) => void] {
  const [pref, setPref] = useState<ThemePref>(read);

  useEffect(() => {
    if (!followsHost) return;
    const sync = () => setPref(hostDark() ? "dark" : "light");
    let obs: MutationObserver | null = null;
    if (window.parent !== window) {
      try {
        obs = new MutationObserver(sync);
        obs.observe(window.parent.document.documentElement, { attributes: true, attributeFilter: ["class"] });
      } catch {
        obs = null;
      }
    }
    window.addEventListener("storage", sync); // tab Tri thức khác đổi theme
    return () => {
      obs?.disconnect();
      window.removeEventListener("storage", sync);
    };
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    if (pref === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", pref);
    if (followsHost) return;
    try {
      localStorage.setItem(KEY, pref);
    } catch {
      /* bỏ qua */
    }
  }, [pref]);

  return [pref, followsHost ? () => undefined : setPref];
}

/** Theme đang hiển thị thực tế (giải "system" theo hệ điều hành). */
export function isDarkNow(pref: ThemePref): boolean {
  return pref === "dark" || (pref === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
}
