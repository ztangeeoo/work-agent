import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
// 顺序不能反：tokens.css 声明 --md-* 变量，styles.css 消费它们。
import './styles/tokens.css';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
