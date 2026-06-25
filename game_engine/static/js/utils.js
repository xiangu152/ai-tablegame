/**
 * utils.js — Shared helpers, API, constants (~80 lines)
 * All components depend on this. Load FIRST.
 */
const API = '';

async function apiGet(path) {
  try { const r = await fetch(API + path); return r.ok ? r.json() : null; }
  catch (e) { console.error('GET', path, e); return null; }
}
async function apiPost(path, body) {
  try {
    const r = await fetch(API + path, { method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) });
    return r.ok ? r.json() : null;
  } catch (e) { console.error('POST', path, e); return null; }
}

function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function roomIcon(type) { return type === 'public' ? '🌐' : type === 'team' ? '👥' : '🔒'; }
function hpClass(pct) { return pct < 30 ? 'low' : pct < 60 ? 'mid' : 'good'; }
