// Recebe eventos de pagamento da Asaas e mantem a licenca sincronizada:
//   - pagamento confirmado + ainda nao existe licenca -> cria (veio do signup.js)
//   - pagamento confirmado + ja existe licenca         -> renova (estende expiresAt)
//   - pagamento atrasado                                -> marca billingStatus "atrasado" (grace; nao bloqueia sozinho)
//   - assinatura cancelada / pagamento estornado         -> bloqueia a licenca
//
// Configuracao no painel da Asaas: Integracoes -> Webhooks -> aponta pra esta
// URL e define o "Token de autenticacao" = mesmo valor de ASAAS_WEBHOOK_TOKEN.
import crypto from 'crypto';
import {
  getRecord, saveRecord, genKey, formatKey, setSecurityHeaders,
  getLicenseKeyBySubscription, linkSubscriptionToKey,
  getPendingSignup, deletePendingSignup,
  rateLimit, clientIp,
} from './_lib.js';
import { enviarChaveLicenca } from './_mail.js';

const CONFIRMED_EVENTS = new Set(['PAYMENT_CONFIRMED', 'PAYMENT_RECEIVED']);
const OVERDUE_EVENTS = new Set(['PAYMENT_OVERDUE']);
const CANCELLED_EVENTS = new Set(['SUBSCRIPTION_DELETED', 'PAYMENT_DELETED', 'PAYMENT_REFUNDED']);

// Dias adicionados a expiresAt a cada confirmacao, por ciclo — com margem de
// alguns dias sobre o ciclo nominal (grace contra pequeno atraso no envio do webhook).
const CYCLE_DAYS = {
  WEEKLY: 9, BIWEEKLY: 16, MONTHLY: 35, BIMONTHLY: 65,
  QUARTERLY: 95, SEMIANNUALLY: 185, YEARLY: 370,
};

function safeEqual(a, b) {
  const left = Buffer.from(String(a || ''));
  const right = Buffer.from(String(b || ''));
  if (left.length !== right.length) return false;
  return crypto.timingSafeEqual(left, right);
}

function authorized(req) {
  const expected = String(process.env.ASAAS_WEBHOOK_TOKEN || '').trim();
  if (!expected) return false; // exige configuracao explicita — nunca aceita sem token
  const got = req.headers['asaas-access-token'] || '';
  return safeEqual(got, expected);
}

async function criarOuRenovar(subscriptionId, cycle) {
  const dias = CYCLE_DAYS[String(cycle || 'MONTHLY').toUpperCase()] || CYCLE_DAYS.MONTHLY;
  const novaExpiracao = new Date(Date.now() + dias * 86400_000).toISOString();

  const chaveExistente = await getLicenseKeyBySubscription(subscriptionId);
  if (chaveExistente) {
    const rec = await getRecord(chaveExistente);
    if (!rec) return null;
    rec.status = 'active';
    rec.billingStatus = 'em_dia';
    rec.expiresAt = novaExpiracao;
    await saveRecord(chaveExistente, rec);
    return { key: chaveExistente, rec, novo: false };
  }

  // Primeira confirmacao desta subscription: so cria licenca se veio de um
  // signup nosso (pending). Subscription criada direto no painel da Asaas
  // (fora do fluxo de autoatendimento) nao tem pending — ignoramos.
  const pending = await getPendingSignup(subscriptionId);
  if (!pending) return null;

  const key = genKey();
  const rec = {
    clientName: pending.clientName || '',
    status: 'active',
    maxMachines: Math.max(1, Number(pending.maxMachines || 1)),
    machines: {},
    expiresAt: novaExpiracao,
    createdAt: new Date().toISOString(),
    lastSeen: null,
    notes: pending.notes || '',
    asaasCustomerId: pending.asaasCustomerId || '',
    asaasSubscriptionId: subscriptionId,
    plano: pending.plano || '',
    billingStatus: 'em_dia',
  };
  await saveRecord(key, rec);
  await linkSubscriptionToKey(subscriptionId, key);
  await deletePendingSignup(subscriptionId);

  if (pending.email) {
    await enviarChaveLicenca({
      to: pending.email,
      clientName: pending.clientName,
      keyFmt: formatKey(key),
      installerUrl: process.env.INSTALLER_URL || '',
    });
  }

  return { key, rec, novo: true };
}

async function marcarStatus(subscriptionId, billingStatus, bloquear) {
  const key = await getLicenseKeyBySubscription(subscriptionId);
  if (!key) return null;
  const rec = await getRecord(key);
  if (!rec) return null;
  rec.billingStatus = billingStatus;
  if (bloquear) rec.status = 'blocked';
  await saveRecord(key, rec);
  return { key, rec };
}

export default async function handler(req, res) {
  setSecurityHeaders(res);
  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, message: 'Metodo nao permitido' });
  }

  if (await rateLimit(`asaas-webhook:ip:${clientIp(req)}`, 120, 60)) {
    return res.status(429).json({ ok: false, message: 'Muitas requisicoes.' });
  }

  if (!authorized(req)) {
    return res.status(401).json({ ok: false, message: 'Nao autorizado' });
  }

  const body = req.body || {};
  const event = String(body.event || '');
  const payment = body.payment || {};
  const subscriptionId = payment.subscription || '';

  if (!subscriptionId) {
    // Evento sem subscription (ex.: cobranca avulsa) — nada a sincronizar aqui.
    return res.status(200).json({ ok: true, ignored: true });
  }

  try {
    if (CONFIRMED_EVENTS.has(event)) {
      const r = await criarOuRenovar(subscriptionId, payment.cycle || body?.subscription?.cycle);
      return res.status(200).json({ ok: true, action: r ? (r.novo ? 'created' : 'renewed') : 'ignored' });
    }

    if (OVERDUE_EVENTS.has(event)) {
      const r = await marcarStatus(subscriptionId, 'atrasado', false);
      return res.status(200).json({ ok: true, action: r ? 'marked_overdue' : 'ignored' });
    }

    if (CANCELLED_EVENTS.has(event)) {
      const r = await marcarStatus(subscriptionId, 'cancelado', true);
      return res.status(200).json({ ok: true, action: r ? 'blocked' : 'ignored' });
    }

    return res.status(200).json({ ok: true, ignored: true, event });
  } catch (err) {
    console.error('asaas-webhook error:', err?.message);
    return res.status(500).json({ ok: false, message: String(err?.message || err) });
  }
}
