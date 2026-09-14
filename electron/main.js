const { app, BrowserWindow, ipcMain, Menu, dialog } = require('electron');
const { spawn, exec } = require('child_process');
const { randomBytes } = require('crypto');
const net = require('net');
const path = require('path');
const fs = require('fs');
const { createLicenseClient, getLicenseSettings } = require('./license-client');

// Auto-update via GitHub Releases (repo público do projeto).
// Só roda no app empacotado; em dev é ignorado.
let autoUpdater = null;
try { ({ autoUpdater } = require('electron-updater')); } catch (_) {}

// Token gerado a cada startup — passado ao Python via env var e incluído em todas as chamadas à API local
const API_TOKEN = randomBytes(32).toString('hex');

let mainWindow;
let pythonProcess;
let licenseClient;
let backendRestartPromise = null;

// A porta e escolhida pelo Windows a cada inicializacao. Isso evita conectar
// acidentalmente a um backend residual, que possui outro API_TOKEN.
let API_PORT = null;
let API_URL = null;

const selectAvailableApiPort = () => new Promise((resolve, reject) => {
  const server = net.createServer();
  server.unref();
  server.once('error', reject);
  server.listen(0, '127.0.0.1', () => {
    const selectedPort = server.address().port;
    server.close(() => {
      API_PORT = selectedPort;
      API_URL = `http://127.0.0.1:${API_PORT}`;
      resolve(selectedPort);
    });
  });
});

const isLocalLicenseExemptEndpoint = (endpoint) => {
  if (endpoint === '/health') return true;
  if (/^\/executar\/[^/]+\/status$/.test(endpoint)) return true;
  if (/^\/executar\/[^/]+\/cancelar$/.test(endpoint)) return true;
  return false;
};

// Detectar se está em modo produção (empacotado)
const isProd = app.isPackaged;
const DEV_RENDERER_URL = process.env.VITE_DEV_SERVER_URL || 'http://127.0.0.1:5173';
const UPDATE_CHECK_INTERVAL_MS = 30 * 60 * 1000;
let updateCheckTimer = null;
let promptedUpdateVersion = null;
let updateDownloadInProgress = false;

const getProjectRoot = () => process.env.NFSE_PROJECT_ROOT || path.join(__dirname, '..');

// Caminho para o Python (apenas em desenvolvimento)
const getPythonPath = () => {
  if (process.env.NFSE_PYTHON_PATH) {
    return process.env.NFSE_PYTHON_PATH;
  }

  const projectRoot = getProjectRoot();
  const candidates = process.platform === 'win32'
    ? [path.join(projectRoot, '.venv', 'Scripts', 'python.exe')]
    : [
        path.join(projectRoot, '.venv_mac', 'bin', 'python'),
        path.join(projectRoot, '.venv', 'bin', 'python'),
      ];

  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }

  return process.platform === 'win32' ? 'python' : 'python3';
};

// ============================================================================
// Spawn Python backend
// ============================================================================
const startPythonBackend = () => {
  return new Promise((resolve, reject) => {
    let command, args, cwd;

    if (isProd && process.platform === 'win32') {
      // Producao Windows: prefere backend-vercel para permitir troca sem brigar
      // com um server.exe antigo que ainda esteja travado pelo Windows.
      const vercelBackend = path.join(process.resourcesPath, 'backend-vercel', 'server.exe');
      const defaultBackend = path.join(process.resourcesPath, 'backend', 'server.exe');
      command = fs.existsSync(vercelBackend) ? vercelBackend : defaultBackend;
      args = [];
      cwd = path.dirname(command);
    } else {
      // Desenvolvimento ou macOS: usa Python do projeto
      command = getPythonPath();
      const projectRoot = getProjectRoot();
      args = [path.join(projectRoot, 'api', 'server.py')];
      cwd = projectRoot;
    }

    console.log(`[Main] Iniciando backend: ${command}`);
    const licenseSettings = getLicenseSettings();

    pythonProcess = spawn(command, args, {
      stdio: ['ignore', 'pipe', 'pipe'],
      detached: false,
      cwd,
      env: {
        ...process.env,
        PYTHONIOENCODING: 'utf-8',
        PYTHONUTF8: '1',
        PYTHONUNBUFFERED: '1',
        NFSE_API_TOKEN: API_TOKEN,
        NFSE_API_PORT: String(API_PORT),
        NFSE_LICENSE_SERVER_URL: licenseSettings.serverUrl,
        NFSE_LICENSE_VALIDATE_URL: licenseSettings.validationUrl,
        NFSE_LICENSE_ADMIN_URL: licenseSettings.adminUrl,
        NFSE_CLIENTES_URL: licenseSettings.clientesUrl,
        NFSE_CLIENT_SECRET: licenseSettings.clientSecret,
      },
    });

    // Log do stdout/stderr do Python
    pythonProcess.stdout.on('data', (data) => {
      console.log(`[Python] ${data.toString().trim()}`);
    });

    pythonProcess.stderr.on('data', (data) => {
      console.error(`[Python stderr] ${data.toString().trim()}`);
    });

    pythonProcess.on('error', (err) => {
      console.error(`[Python] Erro ao iniciar: ${err}`);
      reject(err);
    });

    let processExited = false;
    pythonProcess.on('exit', (code, signal) => {
      processExited = true;
      if (code !== 0 && code !== null) {
        console.warn(`[Python] Processo encerrou inesperadamente (código ${code})`);
      }
    });

    // Polling /health até responder (max 60s)
    let retries = 0;
    const maxRetries = 120; // 120 * 500ms = 60s

    const checkHealth = async () => {
      if (processExited) {
        reject(new Error('Backend encerrou antes de responder'));
        return;
      }
      try {
        const response = await fetch(`${API_URL}/health/auth`, {
          headers: { 'x-api-token': API_TOKEN },
        });
        if (response.ok) {
          console.log('[Main] Backend respondendo e autenticado');
          resolve();
          return;
        }
      } catch (e) {
        // Esperado no início
      }

      retries++;
      if (retries >= maxRetries) {
        reject(new Error('Timeout ao inicializar backend'));
        return;
      }

      setTimeout(checkHealth, 500);
    };

    checkHealth();
  });
};

const stopPythonBackend = () => new Promise((resolve) => {
  const processToStop = pythonProcess;
  pythonProcess = null;
  if (!processToStop || processToStop.exitCode !== null) {
    resolve();
    return;
  }
  if (process.platform === 'win32') {
    exec(`taskkill /PID ${processToStop.pid} /T /F`, () => resolve());
    return;
  }
  try { processToStop.kill('SIGTERM'); } catch (_) {}
  setTimeout(resolve, 300);
});

const restartPythonBackend = async () => {
  if (backendRestartPromise) return backendRestartPromise;
  backendRestartPromise = (async () => {
    console.warn('[Main] Reiniciando backend local para renovar a autenticacao...');
    await stopPythonBackend();
    await startPythonBackend();
  })();
  try {
    await backendRestartPromise;
  } finally {
    backendRestartPromise = null;
  }
};

// ============================================================================
// Criar janela principal
// ============================================================================
const createWindow = () => {
  mainWindow = new BrowserWindow({
    width: 1180,
    height: 760,
    minWidth: 980,
    minHeight: 660,
    icon: path.join(__dirname, 'assets', 'icon.ico'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  });

  if (isProd) {
    mainWindow.loadFile(path.join(__dirname, 'renderer-dist', 'index.html'));
  } else {
    mainWindow.loadURL(DEV_RENDERER_URL).catch(() => {
      const builtIndex = path.join(__dirname, 'renderer-dist', 'index.html');
      const legacyIndex = path.join(__dirname, 'renderer', 'index.html');
      mainWindow.loadFile(fs.existsSync(builtIndex) ? builtIndex : legacyIndex);
    });
  }

  // DevTools desativado por padrao — abra manualmente com Ctrl+Shift+I se precisar

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
};

const setupAutoUpdates = () => {
  if (!autoUpdater || !app.isPackaged) return;

  // Cada push na main gera uma GitHub Release pelo workflow auto-release.yml.
  // O download so comeca depois da confirmacao do usuario.
  autoUpdater.autoDownload = false;
  autoUpdater.autoInstallOnAppQuit = true;

  autoUpdater.on('update-available', async (info) => {
    const version = info?.version || 'nova';
    if (
      promptedUpdateVersion === version
      || updateDownloadInProgress
      || !mainWindow
      || mainWindow.isDestroyed()
    ) {
      return;
    }

    promptedUpdateVersion = version;
    const result = await dialog.showMessageBox(mainWindow, {
      type: 'info',
      title: 'Nova atualizacao disponivel',
      message: `A versao ${version} esta disponivel.`,
      detail: 'Deseja baixar a atualizacao agora? O aplicativo avisara quando estiver pronta para instalar.',
      buttons: ['Atualizar agora', 'Depois'],
      defaultId: 0,
      cancelId: 1,
      noLink: true,
    });

    if (result.response !== 0) return;

    updateDownloadInProgress = true;
    try {
      await autoUpdater.downloadUpdate();
    } catch (error) {
      updateDownloadInProgress = false;
      console.warn('[AutoUpdate] Falha ao baixar:', error?.message);
      if (mainWindow && !mainWindow.isDestroyed()) {
        await dialog.showMessageBox(mainWindow, {
          type: 'error',
          title: 'Falha na atualizacao',
          message: 'Nao foi possivel baixar a atualizacao.',
          detail: 'Verifique a conexao com a internet e tente novamente ao reabrir o aplicativo.',
          buttons: ['OK'],
        });
      }
    }
  });

  autoUpdater.on('update-downloaded', (info) => {
    updateDownloadInProgress = false;
    if (mainWindow && !mainWindow.isDestroyed()) {
      dialog.showMessageBox(mainWindow, {
        type: 'info',
        title: 'Atualizacao pronta',
        message: `A versao ${info?.version || ''} foi baixada.`,
        detail: 'Reinicie agora para concluir a instalacao. Se escolher depois, ela sera aplicada ao fechar o aplicativo.',
        buttons: ['Reiniciar e instalar', 'Depois'],
        defaultId: 0,
        cancelId: 1,
        noLink: true,
      }).then((r) => { if (r.response === 0) autoUpdater.quitAndInstall(); });
    }
  });

  autoUpdater.on('error', (e) => console.warn('[AutoUpdate]', e?.message));

  const check = () => {
    autoUpdater.checkForUpdates().catch((e) => console.warn('[AutoUpdate]', e?.message));
  };

  check();
  updateCheckTimer = setInterval(check, UPDATE_CHECK_INTERVAL_MS);
};

// ============================================================================
// IPC handlers (comunicação renderer → main)
// ============================================================================
ipcMain.handle('select-folder', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory', 'createDirectory'],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('select-file', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile'],
    filters: [{ name: 'Planilhas', extensions: ['xlsx', 'xlsm', 'xls'] }],
  });
  return result.canceled ? null : result.filePaths[0];
});

ipcMain.handle('api-call', async (event, method, endpoint, body) => {
  try {
    const headers = {
      'Content-Type': 'application/json',
      'x-api-token': API_TOKEN,
    };

    if (!isLocalLicenseExemptEndpoint(endpoint)) {
      const key = licenseClient?.getSavedKey?.();
      if (!key) {
        throw new Error('Licenca nao ativada no servidor da Vercel.');
      }
      headers['x-license-key'] = key;
    }

    const requestBody = body && (typeof body !== 'object' || Object.keys(body).length > 0)
      ? JSON.stringify(body)
      : undefined;
    const callBackend = () => fetch(`${API_URL}${endpoint}`, {
      method,
      headers,
      signal: AbortSignal.timeout(30000),
      body: requestBody,
    });

    let response = await callBackend();
    if (response.status === 401) {
      // Token diferente significa backend residual ou reiniciado fora deste
      // Electron. Recupera automaticamente e repete a operacao uma unica vez.
      await restartPythonBackend();
      response = await callBackend();
    }

    if (!response.ok) {
      const error = await response.text();
      throw new Error(`HTTP ${response.status}: ${error}`);
    }

    return await response.json();
  } catch (error) {
    console.error(`[API Call] ${method} ${endpoint}:`, error);
    throw error;
  }
});

ipcMain.handle('license-status', async () => {
  return licenseClient.status();
});

ipcMain.handle('license-activate', async (event, key) => {
  return licenseClient.activate(key);
});

ipcMain.handle('license-deactivate', async () => {
  return licenseClient.deactivate();
});

// ============================================================================
// App lifecycle
// ============================================================================
app.on('ready', async () => {
  try {
    licenseClient = createLicenseClient(app);

    // Remove o menu padrao (File, Edit, View, Window, Help)
    Menu.setApplicationMenu(null);

    // Splash screen (janela pequena enquanto carrega)
    const splashWindow = new BrowserWindow({
      width: 480,
      height: 360,
      frame: false,
      resizable: false,
      center: true,
      alwaysOnTop: true,
      backgroundColor: '#000000',
      show: false,
      webPreferences: {
        nodeIntegration: false,
        contextIsolation: true,
        sandbox: true,
      },
    });

    splashWindow.once('ready-to-show', () => {
      if (!splashWindow.isDestroyed()) {
        splashWindow.show();
      }
    });

    const splashPath = path.join(__dirname, 'splash.html');
    if (fs.existsSync(splashPath)) {
      splashWindow.loadFile(splashPath);
    } else {
      splashWindow.loadURL('data:text/html,<h1>Iniciando...</h1>');
    }

    // Inicia backend (nao crasha o app se falhar — apenas loga)
    console.log('[Main] Iniciando backend Python...');
    try {
      await selectAvailableApiPort();
      await startPythonBackend();
    } catch (error) {
      console.warn('[Main] Backend nao respondeu (continuando sem ele):', error.message);
    }

    // Fecha splash e abre main
    if (!splashWindow.isDestroyed()) {
      splashWindow.close();
    }
    createWindow();

    setupAutoUpdates();
  } catch (error) {
    console.error('[Main] Erro fatal:', error);
    app.quit();
  }
});

app.on('window-all-closed', () => {
  if (updateCheckTimer) {
    clearInterval(updateCheckTimer);
    updateCheckTimer = null;
  }

  // Encerra processo Python ao fechar o app
  if (pythonProcess) {
    try {
      if (process.platform === 'win32') {
        exec(`taskkill /PID ${pythonProcess.pid} /T /F`);
      } else {
        pythonProcess.kill('SIGTERM');
      }
    } catch (e) {
      console.warn('[Main] Falha ao matar Python:', e);
    }
  }

  app.quit();
});

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});
