/**
 * 主题（浅色 / 深色）切换 —— 唯一入口。
 *
 * 颜色本身全部在 styles/tokens.css 里，这里只负责一件事：
 * 把类挂到 <html> 上，让 html.dark 覆盖生效。
 *
 * ⚠️ 类必须挂在 documentElement 上。
 * styles.css 顶部的兼容别名（--bg: var(--md-surface-0) 等）在「声明处」就完成了
 * 变量替换，只在某个后代 div 上加 .dark 不会让这些别名重算 —— 结果是
 * 「类加上了、颜色一个没变」。
 */

const LS_THEME = 'theme';

export type Theme = 'light' | 'dark';

/**
 * 读取偏好。没存过就按浅色来。
 * 刻意不跟随 prefers-color-scheme：首屏观感被系统设置突然改掉属于意外行为，
 * 要跟系统也应该是一个显式开关，而不是默认行为。
 */
export function loadTheme(): Theme {
  try {
    return localStorage.getItem(LS_THEME) === 'dark' ? 'dark' : 'light';
  } catch {
    return 'light'; // 隐私模式 / 禁用存储时 localStorage 会抛错
  }
}

export function applyTheme(t: Theme) {
  document.documentElement.classList.toggle('dark', t === 'dark');
}

export function saveTheme(t: Theme) {
  try {
    localStorage.setItem(LS_THEME, t);
  } catch {
    /* 存不下就算了，本次会话内仍然生效 */
  }
}

/**
 * 首屏前调用，避免深色用户看到一闪而过的白屏。
 * index.html 里还有一段等价的内联脚本，跑得比任何模块都早 —— 两处都保留：
 * 内联脚本负责「不闪」，这里负责「状态对齐」。
 */
export function initTheme(): Theme {
  const t = loadTheme();
  applyTheme(t);
  return t;
}

export function nextTheme(t: Theme): Theme {
  return t === 'dark' ? 'light' : 'dark';
}
