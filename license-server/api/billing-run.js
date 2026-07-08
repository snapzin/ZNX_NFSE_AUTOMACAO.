// Disparado todo dia pelo Cron da Vercel (ver vercel.json -> "crons"). Nao
// existe "assinatura recorrente" no PIX — entao a recorrencia e simulada
// aqui: gera uma nova cobranca PIX pra quem esta perto de vencer, e marca
// atraso/bloqueio pra quem passou do vencimento sem pagar.
//
// A Vercel injeta automaticamente "Authorization: Bearer <CRON_SECRET>"
// nas chamadas do proprio Cron quando a env var CRON_SECRET existe — e
// assim que autenticamos que o disparo veio mesmo da Vercel, e nao de
// qualquer POST publico.
import {
  listRecords, getRecord, saveRecord, setSecurityHeaders, safeEqual, linkTxidToKey,
} from './_lib.js';
import { criarCobranca, gerarTxid } from './itau-client.js';
import { enviarCobrancaPix } from './_mail.js';
import { PLANOS, BILLING_POLICY } from './planos.js';

function autorizado(req) {
  const esperado = String(process.env.CRON_SECRET || '').trim();
  if (!esperado) return false; // exige configuracao explicita — nunca aceita sem token
  const recebido = String(req.headers.authorization || '');
  return safeEqual(recebido, `Bearer ${esperado}`);
}

export default async function handler(req, res) {
  setSecurityHeaders(res);
  if (!autorizado(req)) {
    return res.status(401).json({ ok: false, message: 'Nao autorizado' });
  }

  const agora = Date.now();
  const recs = await listRecords();
  let renovacoesGeradas = 0;
  let marcadosAtrasado = 0;
  let bloqueados = 0;
  const erros = [];

  for (const rec of recs) {
    if (rec.billingProvider !== 'itau_pix') continue;
    if (rec.billingStatus === 'cancelado') continue;
    if (!rec.expiresAt) continue;

    const diasParaVencer = (new Date(rec.expiresAt).getTime() - agora) / 86400_000;

    if (diasParaVencer < 0) {
      // Releitura fresca antes de escrever — reduz a janela de corrida com
      // um pagamento confirmado pelo webhook nesse meio-tempo (ver nota
      // abaixo, no bloco de geracao de cobranca, sobre o mesmo cuidado).
      const fresco = await getRecord(rec.key);
      if (!fresco) continue;
      let mudou = false;
      if (fresco.billingStatus !== 'atrasado' && fresco.billingStatus !== 'cancelado') {
        fresco.billingStatus = 'atrasado';
        mudou = true;
        marcadosAtrasado += 1;
      }
      if (diasParaVencer < -BILLING_POLICY.diasDeAtrasoPraBloquear && fresco.status !== 'blocked') {
        fresco.status = 'blocked';
        fresco.blockedReason = 'billing';
        mudou = true;
        bloqueados += 1;
      }
      if (mudou) await saveRecord(rec.key, fresco);
      continue;
    }

    if (diasParaVencer > BILLING_POLICY.diasAntesDeVencer) continue; // ainda nao e hora

    if (rec.pendingChargeTxid) {
      const geradaHaSegundos = rec.pendingChargeAt
        ? (agora - new Date(rec.pendingChargeAt).getTime()) / 1000
        : 0;
      // Cobranca anterior ainda dentro do prazo de pagamento -- nao gera outra.
      if (geradaHaSegundos < BILLING_POLICY.expiracaoCobrancaSegundos) continue;
      // Senao, a cobranca anterior expirou sem pagamento: segue e gera uma nova.
    }

    const plano = PLANOS[rec.plano];
    if (!plano || !rec.email) continue; // sem plano/e-mail conhecido — nao da pra cobrar sozinho

    try {
      const txid = gerarTxid();
      const cobranca = await criarCobranca({
        txid,
        valor: plano.valor,
        cpfCnpj: rec.itauPayerDoc,
        nome: rec.clientName,
        expiracaoSegundos: BILLING_POLICY.expiracaoCobrancaSegundos,
      });

      // So precisa do indice txid->chave (a licenca ja existe) — o webhook
      // encontra essa licenca direto por ele, sem passar pelo "pending" (que
      // e so pra quando a licenca ainda nao existe, no signup).
      await linkTxidToKey(txid, rec.key);

      // Releitura fresca logo antes de salvar: a unica parte demorada deste
      // loop e a chamada a Itau acima, entao e a unica janela relevante em
      // que o webhook poderia ter confirmado outro pagamento nesse meio-tempo.
      const fresco = await getRecord(rec.key);
      if (!fresco) continue;
      fresco.pendingChargeTxid = txid;
      fresco.pendingChargeAt = new Date().toISOString();
      await saveRecord(rec.key, fresco);

      await enviarCobrancaPix({
        to: rec.email, clientName: rec.clientName,
        copiaECola: cobranca.pixCopiaECola, valor: plano.valor,
      });
      renovacoesGeradas += 1;
    } catch (err) {
      console.error(`billing-run: falha ao gerar cobranca para ${rec.key}:`, err?.message);
      erros.push({ key: rec.key, erro: err?.message });
    }
  }

  return res.status(200).json({
    ok: true, renovacoesGeradas, marcadosAtrasado, bloqueados, erros,
  });
}
