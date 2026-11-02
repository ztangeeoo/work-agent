/* 简历解析：txt/md 直读 · 图片走视觉模型（失败回退 tesseract OCR）· PDF / docx */

import type { ModelCfg } from '../types';

export const IMG_EXT = ['png', 'jpg', 'jpeg', 'webp', 'bmp', 'gif'];

export function loadScript(src: string): Promise<void> {
  return new Promise((res, rej) => {
    if (document.querySelector(`script[src="${src}"]`)) return res();
    const s = document.createElement('script');
    s.src = src;
    s.onload = () => res();
    s.onerror = () => rej(new Error('脚本加载失败：' + src));
    document.head.appendChild(s);
  });
}

export async function parseResume(file: File, cfg: ModelCfg, onHint?: (msg: string) => void): Promise<string> {
  const ext = file.name.split('.').pop()!.toLowerCase();
  if (ext === 'txt' || ext === 'md') return await file.text();
  if (IMG_EXT.includes(ext)) return await imageToText(file, cfg, onHint);
  if (ext === 'pdf') {
    await loadScript('https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.min.js');
    const pdfjsLib = (window as any).pdfjsLib;
    pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdn.jsdelivr.net/npm/pdfjs-dist@3.11.174/build/pdf.worker.min.js';
    const buf = await file.arrayBuffer();
    const pdf = await pdfjsLib.getDocument({ data: buf }).prompt;
    let text = '';
    for (let i = 1; i <= pdf.numPages; i++) {
      const page = await pdf.getPage(i);
      const c = await page.getTextContent();
      text += c.items.map((it: any) => it.str).join(' ') + '\n';
    }
    return text;
  }
  if (ext === 'docx') {
    await loadScript('https://cdn.jsdelivr.net/npm/mammoth@1.6.0/mammoth.browser.min.js');
    const mammoth = (window as any).mammoth;
    const buf = await file.arrayBuffer();
    const r = await mammoth.extractRawText({ arrayBuffer: buf });
    return r.value;
  }
  throw new Error('不支持的文件类型');
}

/* 图片压缩到 1600px 以内，走视觉模型识别；失败回退本地 OCR */
async function imageToText(file: File, cfg: ModelCfg, onHint?: (msg: string) => void): Promise<string> {
  const dataUrl = await compressImage(file);
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        base: cfg.base, key: cfg.key, model: cfg.model,
        stream: false, temperature: 0, max_tokens: 3000,
        messages: [
          { role: 'system', content: '你是简历文字提取工具。只输出图片中的原始文字，保留换行与分节结构，不要总结、不要解释、不要加任何前后缀。' },
          { role: 'user', content: [
            { type: 'text', text: '请提取这张简历图片中的全部文字内容。' },
            { type: 'image_url', image_url: { url: dataUrl } }
          ] }
        ]
      })
    });
    if (res.ok) {
      const j = await res.json();
      const text = (j.choices?.[0]?.message?.content || '').trim();
      if (text.length >= 20) return text;
      throw new Error('模型未返回有效文字（当前模型可能不支持图片输入）');
    }
    let msg = 'HTTP ' + res.status;
    try { const e = await res.json(); msg = e.error?.message || msg; } catch (_) {}
    throw new Error(msg);
  } catch (err: any) {
    onHint?.('模型识别失败，改用本地 OCR…');
    try {
      return await ocrImage(file, onHint);
    } catch (e2: any) {
      throw new Error('图片识别失败。模型原因：' + err.message + '；OCR 原因：' + e2.message);
    }
  }
}

async function compressImage(file: File): Promise<string> {
  const bmp = await createImageBitmap(file);
  const scale = Math.min(1, 1600 / Math.max(bmp.width, bmp.height));
  const cv = document.createElement('canvas');
  cv.width = Math.round(bmp.width * scale);
  cv.height = Math.round(bmp.height * scale);
  const ctx = cv.getContext('2d')!;
  ctx.drawImage(bmp, 0, 0, cv.width, cv.height);
  return cv.toDataURL('image/jpeg', 0.82);
}

async function ocrImage(file: File, onHint?: (msg: string) => void): Promise<string> {
  await loadScript('https://cdn.jsdelivr.net/npm/tesseract.js@5.0.4/dist/tesseract.min.js');
  const Tesseract = (window as any).Tesseract;
  if (!Tesseract) throw new Error('OCR 组件加载失败（需联网）');
  const url = URL.createObjectURL(file);
  try {
    const r = await Tesseract.recognize(url, 'chi_sim+eng', {
      logger: (m: any) => {
        if (m.status === 'recognizing text') onHint?.('OCR 识别中 ' + Math.round((m.progress || 0) * 100) + '%');
      }
    });
    const text = (r?.data?.text || '').trim();
    if (!text) throw new Error('未识别到文字');
    return text;
  } finally {
    URL.revokeObjectURL(url);
  }
}
