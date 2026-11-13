import { useEffect, useState } from "react";

export type ThemePref = "light" | "dark" | "system";
const KEY = "mep.theme";

function read(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "dark" || v === "system" ? v : "light"; // mặc định Sáng, đồng bộ app Hawee
  } catch {
    return "light";
  }
}

/** Sáng (mặc định) / Tối / Theo hệ thống – gắn data-theme lên <html>, nhớ trong localStorage. */
export function useTheme(): [ThemePref, (t: ThemePref) => void] {
  const [pref, setPref] = useState<ThemePref>(read);
  useEffect(() => {
    const root = document.documentElement;
    if (pref === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", pref);
    try {
      localStorage.setItem(KEY, pref);
    } catch {
      /* bỏ qua */
    }
  }, [pref]);
  return [pref, setPref];
}

/** Theme đang hiển thị thực tế (giải "system" theo hệ điều hành). */
export function isDarkNow(pref: ThemePref): boolean {
  return pref === "dark" || (pref === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
}
