import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
// 顺序不能反：tokens.css 声明 --md-* 变量，styles.css 消费它们。
import './styles/tokens.css';
import './styles.css';
import { initTheme } from './lib/theme';

// 首屏前对齐主题状态（index.html 的内联脚本已先一步避免闪白，这里负责状态同步）
initTheme();

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
