# -*- mode: python ; coding: utf-8 -*-
"""
Spec PyInstaller para NFSe Automacao (gui_app.py).
Usa Chrome do sistema (CHROME_CHANNEL=chrome) — nao bundeia browser.
config.py fica FORA do bundle para poder ser editado sem rebuild.
"""
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

# Caminhos base: spec fica em app/gui/, a raiz real do projeto e 2 niveis acima.
SPEC_DIR = Path(SPECPATH)
ROOT_DIR = SPEC_DIR.parent.parent

# Localiza o playwright instalado no Python ativo
import playwright as _pw
PW_DIR = Path(_pw.__file__).parent

# Dados a incluir no bundle
datas = []
datas += collect_data_files("customtkinter")
datas += collect_data_files("tkcalendar")
datas += collect_data_files("babel")

# Driver do Playwright (node.exe + package/) — necessário para controle do Chrome
datas += [(str(PW_DIR / "driver"), "playwright/driver")]

# Imports ocultos
hiddenimports = []
hiddenimports += collect_submodules("babel")
hiddenimports += collect_submodules("playwright")
hiddenimports += [
    "tkinter", "tkinter.ttk", "tkinter.messagebox", "tkinter.filedialog",
    "customtkinter", "tkcalendar",
    "PIL._tkinter_finder",
    "cryptography", "cryptography.hazmat.primitives",
    "cryptography.hazmat.backends.openssl",
    "openpyxl", "openpyxl.styles", "openpyxl.utils",
    # modulos do projeto (exceto config.py — fica externo)
    "app.automation.cert_reader", "app.automation.nfse_automacao",
    "app.automation.dominio_importer", "app.automation.adn_client",
    "app.reports.relatorio",
    "app.gui.ui_widgets", "app.gui.ui_animations",
]

# Arquivos do projeto a incluir como dado
project_datas = [
    (str(ROOT_DIR / "runtime_settings.json"), "."),
]
# Inclui apenas se existir
for f in project_datas:
    if Path(f[0]).exists():
        datas.append(f)

# Analise
a = Analysis(
    [str(SPEC_DIR / "gui_app.py")],
    pathex=[str(ROOT_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # config fica externo ao bundle (copiado pelo build_exe.bat)
    excludes=[
        "config",
        "flask", "fastapi", "uvicorn", "selenium",
        "notebook", "IPython", "matplotlib",
    ],
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NFSe_Automacao",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,           # sem janela de console
    icon=None,               # coloque o caminho de um .ico aqui se quiser
    version=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="NFSe_Automacao",
    contents_directory=".",
)
