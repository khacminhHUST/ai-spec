// Đổi công thức LaTeX ($…$, $$…$$) mà LLM đôi khi viết ra (dù prompt cấm) thành chữ thường dễ đọc:
// "$16\text{ mm}^2$" → "16 mm²", "$S \le 35$" → "S ≤ 35", "$\cos\varphi$" → "cos φ".
// Chỉ đổi đoạn giữa hai dấu $ có dấu hiệu LaTeX (\, ^, _, {) để không đụng ký hiệu tiền tệ.

const SYMBOLS: Record<string, string> = {
  le: "≤", leq: "≤", ge: "≥", geq: "≥", ne: "≠", neq: "≠", times: "×", cdot: "·", pm: "±", approx: "≈",
  div: "÷", infty: "∞", to: "→", rightarrow: "→", leftarrow: "←", degree: "°", circ: "°", "%": "%",
  alpha: "α", beta: "β", gamma: "γ", delta: "δ", Delta: "Δ", eta: "η", theta: "θ", lambda: "λ", mu: "µ",
  pi: "π", rho: "ρ", sigma: "σ", Sigma: "Σ", tau: "τ", phi: "φ", varphi: "φ", omega: "ω", Omega: "Ω",
  cos: "cos", sin: "sin", tan: "tan", log: "log", ln: "ln", max: "max", min: "min",
};
const SUP: Record<string, string> = { "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴", "5": "⁵", "6": "⁶",
  "7": "⁷", "8": "⁸", "9": "⁹", "-": "⁻", "+": "⁺" };

function sup(s: string): string {
  return [...s].every((c) => c in SUP) ? [...s].map((c) => SUP[c]).join("") : `^${s}`;
}

export function latexToText(tex: string): string {
  let s = tex;
  for (let i = 0; i < 3; i++) {
    s = s.replace(/\\(?:d|t)?frac\{([^{}]*)\}\{([^{}]*)\}/g, "$1/$2")
      .replace(/\\sqrt\{([^{}]*)\}/g, "√$1")
      .replace(/\\(?:text|mathrm|mathbf|textbf|mathit|operatorname|boldsymbol)\{([^{}]*)\}/g, "$1");
  }
  s = s.replace(/\^\{\\circ\}|\^\\circ/g, "°")
    .replace(/\^\{([^{}]*)\}/g, (_, x) => sup(x))
    .replace(/\^([0-9+-])/g, (_, x) => sup(x))
    .replace(/_\{([^{}]*)\}/g, "$1")
    .replace(/\\(left|right)\b/g, "")
    .replace(/\\[,;:! ]/g, " ")
    .replace(/\\([A-Za-z]+|%)/g, (m, name) => (name in SYMBOLS ? ` ${SYMBOLS[name]} ` : name))
    .replace(/[{}]/g, "")
    .replace(/\s+/g, " ")
    .replace(/ ([²³¹⁰-⁹°%])/g, "$1")
    .trim();
  return s;
}

const LATEX_HINT = /[\\^_{]/;
const SIMPLE_VAR = /^[A-Za-z][A-Za-z0-9]{0,3}$/; // "$S$", "$In$": biến 1–4 ký tự

export function plainMath(text: string): string {
  if (!text.includes("$")) return text;
  return text
    .replace(/\$\$([\s\S]+?)\$\$/g, (m, inner) => (LATEX_HINT.test(inner) ? latexToText(inner) : m))
    .replace(/\$([^$\n]{1,200}?)\$/g, (m, inner) =>
      LATEX_HINT.test(inner) ? latexToText(inner) : SIMPLE_VAR.test(inner) ? inner : m);
}
