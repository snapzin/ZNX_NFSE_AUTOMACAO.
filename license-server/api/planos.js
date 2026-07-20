// Planos e precos definidos SO aqui (nao confiar em valor vindo do
// cliente, nao duplicar em outro arquivo) — usado tanto pelo signup (1a
// cobranca) quanto pelo billing-run (cobranca de renovacao mensal).
export const PLANOS = {
  basico: { label: 'Basico', valor: 149.9, maxMachines: 1 },
  pro: { label: 'Pro', valor: 249.9, maxMachines: 3 },
};

// Politica de cobranca/renovacao PIX — definida SO aqui (billing-run.js e
// itau-webhook/[token]/pix.js importam daqui, em vez de cada um ter sua propria
// constante que pode divergir da outra silenciosamente).
export const BILLING_POLICY = {
  // Cobranca de renovacao e gerada com essa antecedencia do vencimento.
  diasAntesDeVencer: 5,
  // Cobranca de renovacao fica valida por esse tempo.
  expiracaoCobrancaSegundos: 5 * 86400,
  // Folga apos o vencimento antes de bloquear a licenca por atraso.
  diasDeAtrasoPraBloquear: 10,
  // Margem sobre o ciclo mensal ao estender expiresAt numa confirmacao de
  // pagamento (cobre pequeno atraso na geracao da proxima cobranca).
  diasDeRenovacao: 35,
};
