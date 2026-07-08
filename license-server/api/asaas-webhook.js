// Recebe eventos de pagamento da Asaas e mantem a licenca sincronizada:
//   - pagamento confirmado + ainda nao existe licenca -> cria (veio do signup.js)
//   - pagamento confirmado + ja existe licenca         -> renova (estende expiresAt)
//   - pagamento atrasado                                -> marca billingStatus "atrasado" (grace; nao bloqueia sozinho)
//   - assinatura cancelada / pagamento estornado         -> bloqueia a licenca
//
// Configuracao no painel da Asaas: Integracoes -> Webhooks -> aponta pra esta
// URL e define o "Token de autenticacao" = mesmo valor de ASAAS_WEBHOOK_TOKEN.
//
// Chamado servidor-a-servidor pela Asaas (nao pelo navegador do cliente),
// entao nao usa setCors — nao ha origem de browser pra liberar aqui.
import {
  getRecord, saveRecord, genKey, formatKey, setSecurityHeaders, safeEqual,
  buildLicenseRecord, getLicenseKeyBySubscription, linkSubscriptionToKey,
  getPendingSignup, deletePendingSignup, eventoAsaasJaProcessado,
  rateLimit, clientIp,
} from './_lib.js';
import { enviarChaveLicenca } from './_mail.js';

const CONFIRMED_EVENTS = new Set(['PAYMENT_CONFIRMED', 'PAYMENT_RECEIVED']);
const OVERDUE_EVENTS = new Set(['PAYMENT_OVERDUE']);
const CANCELLED_EVENTS = new Set(['SUBSCRIPTION_DELETED', 'PAYMENT_DELETED', 'PAYMENT_REFUNDED']);

// Unico ciclo oferecido hoje (ver signup.js: cycle sempre 'MONTHLY'), e o
// payload real da Asaas para eventos de pagamento nao traz o ciclo da
// assinatura de volta — entao nao ha como (nem por que, por ora) diferenciar
// por ciclo. Quando um 2o ciclo for vendido, isso vira um mapa por 'plano'.
const GRACE_DAYS = 35;

function authorized(req) {
  const expected = String(process.env.ASAAS_WEBHOOK_TOKEN || '').trim();
  if (!expected) return false; // exige configuracao explicita — nunca aceita sem token
  const got = req.headers['asaas-access-token'] || '';
  return safeEqual(got, expected);
}

// A Asaas manda o subscription id dentro de `payment.subscription` nos
// eventos de pagamento (PAYMENT_*), mas em eventos de ciclo de vida da
// assinatura (ex.: SUBSCRIPTION_DELETED) ele vem em `subscription.id` (ou
// `subscription` como string), SEM objeto `payment`. Checa os dois formatos.
function extrairSubscriptionId(body) {
  const doPayment = body?.payment?.subscription;
  if (doPayment) return doPayment;
  const sub = body?.subscription;
  if (typeof sub === 'string') return sub;
  if (sub?.id) return sub.id;
  return '';
}

async function criarOuRenovar(subscriptionId) {
  const novaExpiracao = new Date(Date.now() + GRACE_DAYS * 86400_000).toISOString();

  const chaveExistente = await getLicenseKeyBySubscription(subscriptionId);
  if (chaveExistente) {
    const rec = await getRecord(chaveExistente);
    if (!rec) return null;
    // So reativa sozinho um bloqueio que o PROPRIO webhook fez por atraso/
    // cancelamento (blockedReason:'billing'). Bloqueio manual do admin (ex.:
    // fraude) fica intacto — o pagamento so atualiza a parte de cobranca.
    if (rec.status !== 'blocked' || rec.blockedReason === 'billing') {
      rec.status = 'active';
      delete rec.blockedReason;
    }
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

  let key = genKey();
  for (let tentativas = 0; tentativas < 3 && (await getRecord(key)); tentativas += 1) {
    key = genKey(); // colisao e ~impossivel (32^16 chaves), mas e barato conferir
  }
  const rec = buildLicenseRecord({
    clientName: pending.clientName,
    maxMachines: pending.maxMachines,
    expiresAt: novaExpiracao,
    notes: pending.notes,
    plano: pending.plano,
    billingStatus: 'em_dia',
    asaasCustomerId: pending.asaasCustomerId,
    asaasSubscriptionId: subscriptionId,
  });

  await Promise.all([saveRecord(key, rec), linkSubscriptionToKey(subscriptionId, key)]);
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
  if (bloquear) {
    rec.status = 'blocked';
    rec.blockedReason = 'billing';
  }
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
  const subscriptionId = extrairSubscriptionId(body);

  // A Asaas reentrega o mesmo evento em caso de timeout/erro — sem isso, um
  // retry de PAYMENT_CONFIRMED criaria/renovaria a licenca duas vezes.
  const eventId = String(body.id || '');
  if (eventId && (await eventoAsaasJaProcessado(eventId))) {
    return res.status(200).json({ ok: true, deduped: true });
  }

  if (!subscriptionId) {
    // Evento sem subscription (ex.: cobranca avulsa) — nada a sincronizar aqui.
    return res.status(200).json({ ok: true, ignored: true });
  }

  try {
    if (CONFIRMED_EVENTS.has(event)) {
      const r = await criarOuRenovar(subscriptionId);
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
