/**
 * splash.js - Gerencia splash screen
 */
class Splash {
  static async waitForBackend() {
    const splash = document.getElementById('splash');
    const text = splash.querySelector('.splash-text');
    const messages = [
      'Inicializando...',
      'Carregando configurações...',
      'Validando certificados...',
      'Finalizando...',
    ];
    let msgIndex = 0;

    const updateMessage = () => {
      text.textContent = messages[Math.floor((msgIndex++) / 3) % messages.length];
    };

    const inicio = Date.now();
    updateMessage();
    const msgInterval = setInterval(updateMessage, 400);

    // Aguarda backend estar pronto
    let ready = false;
    while (!ready) {
      try {
        await API.get('/health');
        ready = true;
      } catch (e) {
        await new Promise(r => setTimeout(r, 500));
      }
    }

    // Garante que a barra (animacao CSS de 1.2s, width 0->100%) chegue a 100%
    // antes de sair. O backend costuma JA estar pronto quando esta tela carrega
    // (subiu durante o splash nativo), entao sem isso a barra sumiria no comeco.
    // Esperamos o tempo restante da animacao para o preenchimento aparecer.
    const MIN_SPLASH_MS = 1300;
    const restante = MIN_SPLASH_MS - (Date.now() - inicio);
    if (restante > 0) {
      await new Promise(r => setTimeout(r, restante));
    }

    clearInterval(msgInterval);
    text.textContent = 'Tudo pronto!';

    // Anima saída
    splash.classList.add('exit');
    await new Promise(r => setTimeout(r, 400));
    splash.style.display = 'none';

    // Mostra app
    document.getElementById('app').style.display = 'flex';

    ToastManager.show('Tudo pronto!', 'success', 2000);
  }
}
