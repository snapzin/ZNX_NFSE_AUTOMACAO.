// Envio de e-mail (nodemailer) para o escritorio-cliente quando a licenca e
// criada/renovada. Igual ao padrao do resto do projeto: se o SMTP nao estiver
// configurado, so loga e segue (nao derruba o webhook/signup por causa disso).
import nodemailer from 'nodemailer';

function smtpConfigured() {
  return Boolean(
    process.env.SMTP_HOST && process.env.SMTP_USER && process.env.SMTP_PASSWORD
      && process.env.MAIL_FROM,
  );
}

function transporter() {
  return nodemailer.createTransport({
    host: process.env.SMTP_HOST,
    port: Number(process.env.SMTP_PORT || 587),
    secure: String(process.env.SMTP_SECURE || '').trim() === '1',
    auth: { user: process.env.SMTP_USER, pass: process.env.SMTP_PASSWORD },
  });
}

export async function enviarChaveLicenca({ to, clientName, keyFmt, installerUrl }) {
  if (!smtpConfigured() || !to) {
    console.warn('[mail] SMTP nao configurado ou destinatario ausente — e-mail nao enviado.');
    return false;
  }
  const linhas = [
    `Ola, ${clientName || ''}!`.trim(),
    '',
    'Sua licenca do TaBatido (ZNX) foi ativada.',
    '',
    `Chave de licenca: ${keyFmt}`,
    installerUrl ? `Instalador: ${installerUrl}` : '',
    '',
    'Guarde esta chave — ela e usada para ativar o aplicativo na sua maquina.',
  ].filter(Boolean);

  try {
    await transporter().sendMail({
      from: process.env.MAIL_FROM,
      to,
      subject: 'Sua licenca do TaBatido esta ativa',
      text: linhas.join('\n'),
    });
    return true;
  } catch (err) {
    console.error('[mail] Falha ao enviar e-mail:', err?.message);
    return false;
  }
}
