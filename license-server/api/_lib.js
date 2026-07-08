// Biblioteca compartilhada do servidor de licencas.
// Banco: Upstash Redis (Vercel Marketplace -> Upstash, plano gratuito).
// Env necessarias: UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN
import crypto from 'crypto';
import { Redis } from '@upstash/redis';

let _redis = null;
const _memoryHits = new Map();
export function redis() {
  if (_redis) return _redis;
  // Aceita os dois padroes de nome: integracao Vercel (KV_*) e Upstash direto.
  const url = process.env.KV_REST_API_URL || process.env.UPSTASH_REDIS_REST_URL;
  const token = process.env.KV_REST_API_TOKEN || process.env.UPSTASH_REDIS_REST_TOKEN;
  if (url && token) {
    _redis = new Redis({ url, token });
  }
  return _redis;
}

function memoryRateLimit(id, limit, windowSecs) {
  const now = Date.now();
  const windowMs = windowSecs * 1000;
  const e = _memoryHits.get(id) || { count: 0, start: now };
  if (now - e.start > windowMs) {
    _memoryHits.set(id, { count: 1, start: now });
    return false;
  }
  e.count += 1;
  _memoryHits.set(id, e);
  return e.count > limit;
}

export async function rateLimit(id, limit = 60, windowSecs = 60) {
  const safeId = String(id || 'unknown').replace(/[^a-zA-Z0-9:._-]/g, '_').slice(0, 160);
  const r = redis();
  if (!r) return memoryRateLimit(safeId, limit, windowSecs);
  const bucket = Math.floor(Date.now() / (windowSecs * 1000));
  const key = `rl:${safeId}:${bucket}`;
  try {
    const count = await r.incr(key);
    if (Number(count) === 1) {
      await r.expire(key, windowSecs + 10);
    }
    return Number(count) > limit;
  } catch {
    return memoryRateLimit(safeId, limit, windowSecs);
  }
}

export function clientIp(req) {
  return req.headers['x-forwarded-for']?.split(',')[0]?.trim()
    || req.headers['x-real-ip']
    || req.socket?.remoteAddress
    || 'unknown';
}

export const INDEX_SET = 'lic:index';
export const keyPath = (k) => `lic:${k}`;
export const ADMIN_USER_INDEX_SET = 'admin:user:index';
export const adminUserPath = (u) => `admin:user:${u}`;

// ── Chaves ──────────────────────────────────────────────────────────────────
export function normalizeKey(k) {
  return String(k || '').toUpperCase().replace(/[^A-Z0-9]/g, '').trim();
}

export function formatKey(k) {
  // Exibicao amigavel: grupos de 4 (ex.: NFSE-XXXX-XXXX-XXXX-XXXX)
  const n = normalizeKey(k);
  return (n.match(/.{1,4}/g) || []).join('-');
}

export function maskKey(k) {
  const n = normalizeKey(k);
  return n.length > 10 ? n.slice(0, 4) + '...' + n.slice(-4) : n.slice(0, 3) + '***';
}

export function genKey() {
  // 20 caracteres alfanumericos (sem 0/O/1/I para evitar confusao), prefixo NFSE.
  const alf = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let s = 'NFSE';
  for (let i = 0; i < 16; i++) s += alf[Math.floor(Math.random() * alf.length)];
  return s; // normalizado (sem tracos)
}

// ── Registros ─────────────────────────────────────────────────────────────────
// Formato do registro (JSON em lic:<KEY>):
// { clientName, status: "active"|"blocked", maxMachines,
//   machines: {<id>:{firstSeen,lastSeen,status:"active"|"blocked",blockedAt?}},
//   expiresAt: ISO|null, createdAt: ISO, notes,
//   // 'blockedReason' so existe quando status é "blocked": "billing" (o
//   // proprio webhook bloqueou por atraso/cancelamento — pode reativar
//   // sozinho num pagamento confirmado) ou ausente (bloqueio manual do
//   // admin — o webhook NUNCA reativa esse sozinho).
//   blockedReason?: "billing",
//   // Cobranca (Itau PIX) — presentes so em licencas criadas via signup/webhook:
//   billingProvider?: "itau_pix", itauPayerDoc? (CPF/CNPJ de quem paga), email?,
//   pendingChargeTxid?, pendingChargeAt? (cobranca de renovacao gerada e
//   ainda nao paga — evita o cron gerar duplicada na mesma competencia),
//   lastPaidTxid?, lastPaidAt? (ultimo pagamento confirmado),
//   plano?, billingStatus?: "manual"|"em_dia"|"atrasado"|"cancelado" }
export async function getRecord(k) {
  const r = redis();
  if (!r) return null;
  const rec = await r.get(keyPath(k));
  if (!rec) return null;
  return typeof rec === 'string' ? JSON.parse(rec) : rec;
}

// Gera uma chave de licenca garantindo que nao colide com uma ja existente
// (colisao e ~impossivel com genKey() sozinho — 32^16 chaves — mas e barato
// conferir). Usado por toda criacao de licenca (manual no admin.js, ou via
// pagamento confirmado no itau-webhook/[token].js) pra ter a mesma garantia.
export async function genUniqueKey() {
  let key = genKey();
  for (let tentativas = 0; tentativas < 3 && (await getRecord(key)); tentativas += 1) {
    key = genKey();
  }
  return key;
}

export async function saveRecord(k, rec) {
  const r = redis();
  if (!r) throw new Error('Banco (Upstash Redis) nao configurado.');
  await r.set(keyPath(k), JSON.stringify(rec));
  await r.sadd(INDEX_SET, k);
}

export async function deleteRecord(k) {
  const r = redis();
  if (!r) throw new Error('Banco (Upstash Redis) nao configurado.');
  await r.del(keyPath(k));
  await r.srem(INDEX_SET, k);
}

export async function listRecords() {
  const r = redis();
  if (!r) return [];
  const keys = await r.smembers(INDEX_SET);
  if (!keys || keys.length === 0) return [];
  const out = [];
  for (const k of keys) {
    const rec = await getRecord(k);
    if (rec) out.push({ key: k, ...rec });
  }
  return out;
}

// Usuarios administrativos do painel.
export function normalizeAdminUser(u) {
  return String(u || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9._@-]/g, '')
    .slice(0, 80);
}

export async function getAdminUser(username) {
  const r = redis();
  if (!r) return null;
  const user = normalizeAdminUser(username);
  if (!user) return null;
  const rec = await r.get(adminUserPath(user));
  if (!rec) return null;
  return typeof rec === 'string' ? JSON.parse(rec) : rec;
}

export async function saveAdminUser(username, rec) {
  const r = redis();
  if (!r) throw new Error('Banco (Upstash Redis) nao configurado.');
  const user = normalizeAdminUser(username);
  if (!user) throw new Error('Usuario invalido.');
  await r.set(adminUserPath(user), JSON.stringify({ ...rec, username: user }));
  await r.sadd(ADMIN_USER_INDEX_SET, user);
}

export async function deleteAdminUser(username) {
  const r = redis();
  if (!r) throw new Error('Banco (Upstash Redis) nao configurado.');
  const user = normalizeAdminUser(username);
  if (!user) throw new Error('Usuario invalido.');
  await r.del(adminUserPath(user));
  await r.srem(ADMIN_USER_INDEX_SET, user);
}

export async function listAdminUsers() {
  const r = redis();
  if (!r) return [];
  const users = await r.smembers(ADMIN_USER_INDEX_SET);
  if (!users || users.length === 0) return [];
  const out = [];
  for (const username of users) {
    const rec = await getAdminUser(username);
    if (rec) {
      out.push({
        username: rec.username || username,
        createdAt: rec.createdAt || null,
        createdBy: rec.createdBy || '',
      });
    }
  }
  out.sort((a, b) => a.username.localeCompare(b.username));
  return out;
}

// ── Indice cobranca PIX (Itau) -> chave de licenca ───────────────────────────
// Permite ao webhook achar a licenca certa a partir do txid da cobranca, sem
// varrer todas as licencas.
const itauTxidPath = (txid) => `itau:txid:${txid}`;

export async function getLicenseKeyByTxid(txid) {
  const r = redis();
  if (!r || !txid) return null;
  return (await r.get(itauTxidPath(txid))) || null;
}

export async function linkTxidToKey(txid, key) {
  const r = redis();
  if (!r) throw new Error('Banco (Upstash Redis) nao configurado.');
  await r.set(itauTxidPath(txid), key);
}

// Dados do signup (clientName, maxMachines, plano, email) guardados entre a
// criacao da 1a cobranca PIX e a confirmacao do pagamento (webhook). So
// depois do pagamento confirmado a licenca de fato nasce.
const itauPendingPath = (txid) => `itau:pending:${txid}`;

export async function savePendingCharge(txid, data) {
  const r = redis();
  if (!r) throw new Error('Banco (Upstash Redis) nao configurado.');
  await r.set(itauPendingPath(txid), JSON.stringify(data));
}

export async function getPendingCharge(txid) {
  const r = redis();
  if (!r || !txid) return null;
  const rec = await r.get(itauPendingPath(txid));
  if (!rec) return null;
  return typeof rec === 'string' ? JSON.parse(rec) : rec;
}

export async function deletePendingCharge(txid) {
  const r = redis();
  if (!r) return;
  await r.del(itauPendingPath(txid));
}

// Deduplicacao de webhooks do Itau: reentregas em timeout/erro podem repetir
// a mesma notificacao. Marca-e-checa atomico via SET NX; TTL de 7 dias cobre
// a janela de retry com folga.
const itauEventPath = (eventId) => `itau:event:${eventId}`;

export async function eventoItauJaProcessado(eventId) {
  const r = redis();
  if (!r || !eventId) return false; // sem Redis/id: nao da pra deduplicar, segue normalmente
  const resultado = await r.set(itauEventPath(eventId), '1', { nx: true, ex: 7 * 86400 });
  return resultado !== 'OK'; // 'OK' = 1a vez (marcado agora); null = ja existia (duplicado)
}

// Desfaz a marcacao de "ja processado". Chame isso se o processamento do
// evento falhar DEPOIS de eventoItauJaProcessado ja ter marcado — senao o
// reenvio da Itau fica preso como "duplicado" por 7 dias e o pagamento
// nunca chega a criar/renovar a licenca, mesmo tendo sido confirmado pelo banco.
export async function limparEventoProcessado(eventId) {
  const r = redis();
  if (!r || !eventId) return;
  await r.del(itauEventPath(eventId));
}

// Monta o registro de uma licenca nova com os valores padrao — usado tanto
// pela criacao manual (admin.js) quanto pela criacao via 1o pagamento PIX
// confirmado (itau-webhook.js), pra nao ter duas formas de montar o mesmo
// formato e correrem o risco de divergir.
export function buildLicenseRecord({
  clientName = '',
  maxMachines = 1,
  expiresAt = null,
  notes = '',
  plano = '',
  billingStatus = 'manual',
  billingProvider = '',
  itauPayerDoc = '',
  email = '',
} = {}) {
  return {
    clientName: String(clientName || '').trim(),
    status: 'active',
    maxMachines: Math.max(1, Number(maxMachines || 1)),
    machines: {},
    expiresAt: expiresAt || null,
    createdAt: new Date().toISOString(),
    lastSeen: null,
    notes: String(notes || ''),
    plano: String(plano || ''),
    billingStatus,
    billingProvider: String(billingProvider || ''),
    itauPayerDoc: String(itauPayerDoc || ''),
    // E-mail do responsavel pela cobranca — so preenchido em licencas via
    // Itau PIX (o billing-run precisa disso pra mandar a cobranca de
    // renovacao). Licenca manual nao tem.
    email: String(email || ''),
  };
}

// Comparacao de tempo constante (evita leak de timing em comparacao de
// segredos: tokens de admin, de webhook, etc.) — compartilhada entre
// admin.js, itau-webhook/[token].js e billing-run.js.
export function safeEqual(a, b) {
  const left = Buffer.from(String(a || ''));
  const right = Buffer.from(String(b || ''));
  if (left.length !== right.length) return false;
  return crypto.timingSafeEqual(left, right);
}

// Chaves legadas em VALID_KEYS (compatibilidade com a versao antiga).
export function legacyKeys() {
  if (process.env.ALLOW_LEGACY_KEYS !== '1') return [];
  return (process.env.VALID_KEYS || '')
    .split(',')
    .map(normalizeKey)
    .filter(Boolean);
}

// ── HTTP ────────────────────────────────────────────────────────────────────
export function setCors(req, res, methods = 'POST, OPTIONS') {
  const allowed = (process.env.ALLOWED_ORIGINS || 'https://license-server-sigma-topaz.vercel.app')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  const origin = req?.headers?.origin || '';
  if (origin && allowed.includes(origin)) {
    res.setHeader('Access-Control-Allow-Origin', origin);
  }
  res.setHeader('Access-Control-Allow-Methods', methods);
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type, x-client-secret, x-admin-token, x-admin-user, x-admin-pass');
}

export function setSecurityHeaders(res) {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Referrer-Policy', 'no-referrer');
  res.setHeader('Permissions-Policy', 'camera=(), microphone=(), geolocation=(), payment=()');
}
