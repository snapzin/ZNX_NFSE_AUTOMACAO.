// Recebe a notificacao de PIX recebido do Itau (payload padrao Bacen:
// { "pix": [{ txid, endToEndId, valor, horario, ... }, ...] }) e mantem a
// licenca sincronizada:
//   - txid sem licenca ainda, mas com "pending" (veio do signup.js) -> cria
//   - txid ja ligado a uma licenca (cobranca de renovacao)           -> renova
//
// O padrao PIX do Bacen nao define assinatura de payload (tipo Stripe/Asaas),
// entao a autenticidade e garantida por um token secreto na propria URL.
//
// ATENCAO ao path deste arquivo: a Itau documenta (spec OpenAPI, PUT /webhook/
// {chave}) que ela acrescenta o sufixo "/pix" na URL cadastrada. Ou seja, se
// voce registra o webhook no Itau Developers como
//   https://SEU-PROJETO.vercel.app/api/itau-webhook/<ITAU_WEBHOOK_TOKEN>
// a notificacao de verdade chega em
//   https://SEU-PROJETO.vercel.app/api/itau-webhook/<ITAU_WEBHOOK_TOKEN>/pix
// por isso o handler mora em itau-webhook/[token]/pix.js, nao em
// itau-webhook/[token].js.
import {
  getRecord, saveRecord, genUniqueKey, formatKey, setSecurityHeaders, safeEqual,
  buildLicenseRecord, getLicenseKeyByTxid, linkTxidToKey,
  getPendingCharge, deletePendingCharge,
  eventoItauJaProcessado, limparEventoProcessado,
  rateLimit, clientIp,
} from '../../_lib.js';
import { enviarChaveLicenca } from '../../_mail.js';
import { BILLING_POLICY } from '../../planos.js';

function autorizado(req) {
  const esperado = String(process.env.ITAU_WEBHOOK_TOKEN || '').trim();
  if (!esperado) return false; // exige configuracao explicita — nunca aceita sem token
  const recebido = String(req.query?.token || '');
  return safeEqual(recebido, esperado);
}

async function confirmarPagamento(txid) {
  const novaExpiracao = new Date(Date.now() + BILLING_POLICY.diasDeRenovacao * 86400_000).toISOString();
  const agoraIso = new Date().toISOString();

  const chaveExistente = await getLicenseKeyByTxid(txid);
  if (chaveExistente) {
    const rec = await getRecord(chaveExistente);
    if (!rec) return null;
    // So reativa sozinho um bloqueio que o PROPRIO sistema de cobranca fez
    // por atraso (blockedReason:'billing'). Bloqueio manual do admin fica intacto.
    if (rec.status !== 'blocked' || rec.blockedReason === 'billing') {
      rec.status = 'active';
      delete rec.blockedReason;
    }
    rec.billingStatus = 'em_dia';
    rec.expiresAt = novaExpiracao;
    if (rec.pendingChargeTxid === txid) delete rec.pendingChargeTxid;
    rec.lastPaidTxid = txid;
    rec.lastPaidAt = agoraIso;
    await saveRecord(chaveExistente, rec);
    return { key: chaveExistente, rec, novo: false };
  }

  // Primeira confirmacao deste txid: so cria licenca se veio de um signup
  // nosso (pending). Cobranca gerada fora do fluxo nao tem pending — ignoramos.
  const pending = await getPendingCharge(txid);
  if (!pending) return null;

  const key = await genUniqueKey();
  const rec = buildLicenseRecord({
    clientName: pending.clientName,
    maxMachines: pending.maxMachines,
    expiresAt: novaExpiracao,
    notes: pending.notes,
    plano: pending.plano,
    billingStatus: 'em_dia',
    billingProvider: 'itau_pix',
    itauPayerDoc: pending.cpfCnpj,
    email: pending.email,
  });
  rec.lastPaidTxid = txid;
  rec.lastPaidAt = agoraIso;

  await Promise.all([saveRecord(key, rec), linkTxidToKey(txid, key)]);
  await deletePendingCharge(txid);

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

export default async function handler(req, res) {
  setSecurityHeaders(res);
  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, message: 'Metodo nao permitido' });
  }

  if (await rateLimit(`itau-webhook:ip:${clientIp(req)}`, 120, 60)) {
    return res.status(429).json({ ok: false, message: 'Muitas requisicoes.' });
  }

  if (!autorizado(req)) {
    return res.status(401).json({ ok: false, message: 'Nao autorizado' });
  }

  const body = req.body || {};
  const eventos = Array.isArray(body.pix) ? body.pix : [];
  if (!eventos.length) {
    console.warn('itau-webhook: payload sem array "pix" (evento nao reconhecido) — ignorado.', JSON.stringify(body).slice(0, 300));
    return res.status(200).json({ ok: true, ignored: true });
  }

  const resultados = [];
  for (const evento of eventos) {
    const txid = String(evento?.txid || '');
    if (!txid) continue;

    // O Itau pode reentregar a notificacao em timeout/erro. Deduplica por
    // endToEndId (identificador unico do PIX) ou, na falta dele, pelo txid.
    const eventId = String(evento?.endToEndId || txid);
    if (await eventoItauJaProcessado(eventId)) {
      resultados.push({ txid, action: 'deduped' });
      continue;
    }

    try {
      const r = await confirmarPagamento(txid);
      resultados.push({ txid, action: r ? (r.novo ? 'created' : 'renewed') : 'ignored' });
    } catch (err) {
      // Falhou DEPOIS de marcar como processado — desfaz a marcacao pra o
      // proximo reenvio da Itau poder tentar de novo, em vez do pagamento
      // ficar "perdido" silenciosamente pelos proximos 7 dias.
      await limparEventoProcessado(eventId);
      console.error('itau-webhook error:', err?.message);
      return res.status(500).json({ ok: false, message: String(err?.message || err), resultados });
    }
  }

  return res.status(200).json({ ok: true, resultados });
}
