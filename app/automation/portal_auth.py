"""Autenticacao no Portal Nacional NFS-e por usuario/senha.

O modulo e independente do motor ADN: ele valida as credenciais no portal sem
alterar o fluxo de download mTLS existente.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any


class ErroLoginNFSe(RuntimeError):
    """Erro compreensivel e seguro (nunca inclui a senha)."""


@dataclass(frozen=True)
class CredenciaisNFSe:
    tipo_login: str
    usuario_nfse: str = ""
    senha_nfse: str = ""


def credenciais_para(config: object, documento: str) -> CredenciaisNFSe:
    """Resolve credenciais por empresa, com variaveis de ambiente como override."""
    doc = re.sub(r"\D", "", documento or "")
    cadastradas = getattr(config, "NFSE_CREDENCIAIS", {}) or {}
    dados = cadastradas.get(doc, {}) if isinstance(cadastradas, dict) else {}
    if not isinstance(dados, dict):
        dados = {}
    tipo = os.getenv("NFSE_TIPO_LOGIN", str(dados.get("tipo_login", "certificado")))
    usuario = os.getenv("NFSE_USUARIO", str(dados.get("usuario_nfse", doc)))
    senha = os.getenv("NFSE_SENHA", str(dados.get("senha_nfse", "")))
    tipo = tipo.strip().lower().replace("-", "_")
    if tipo not in {"certificado", "usuario_senha"}:
        raise ErroLoginNFSe(f"tipo_login invalido para {doc}: {tipo!r}")
    if tipo == "usuario_senha" and (not usuario.strip() or not senha):
        raise ErroLoginNFSe(
            f"Usuario/senha do portal NFS-e nao configurados para {doc}."
        )
    return CredenciaisNFSe(tipo, usuario.strip(), senha)


def _primeiro_visivel(page: Any, seletores: list[str]) -> Any | None:
    for seletor in seletores:
        try:
            item = page.locator(seletor).first
            if item.count() and item.is_visible():
                return item
        except Exception:
            continue
    return None


def login_usuario_senha(
    page: Any,
    usuario: str,
    senha: str,
    *,
    login_url: str,
    timeout_ms: int = 45_000,
    seletor_login_ok: str = "",
) -> None:
    """Acessa o portal, preenche CPF/CNPJ e senha e confirma a autenticacao."""
    if not usuario.strip() or not senha:
        raise ErroLoginNFSe("CPF/CNPJ e senha do portal NFS-e sao obrigatorios.")
    try:
        page.goto(login_url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as exc:
        raise ErroLoginNFSe("Portal NFS-e indisponivel ou excedeu o tempo de resposta.") from exc

    campo_usuario = _primeiro_visivel(page, [
        "input[placeholder*='CPF/CNPJ' i]", "input[name*='cpfcnpj' i]",
        "input[id*='cpfcnpj' i]", "input[name*='inscricao' i]",
        "input[type='text']",
    ])
    campo_senha = _primeiro_visivel(page, [
        "input[type='password']", "input[name*='senha' i]", "input[id*='senha' i]",
    ])
    if campo_usuario is None or campo_senha is None:
        raise ErroLoginNFSe(
            "Campos CPF/CNPJ ou senha nao encontrados; o layout do portal pode ter mudado."
        )
    campo_usuario.fill(usuario)
    campo_senha.fill(senha)

    botao = _primeiro_visivel(page, [
        "button:has-text('Entrar')", "input[type='submit'][value*='Entrar' i]",
        "button[type='submit']",
    ])
    if botao is None:
        raise ErroLoginNFSe("Botao Entrar nao encontrado; o layout do portal pode ter mudado.")
    try:
        botao.click(timeout=min(timeout_ms, 10_000))
        page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception as exc:
        raise ErroLoginNFSe("Timeout: login no portal NFS-e nao foi concluido.") from exc

    try:
        texto = page.locator("body").inner_text(timeout=5_000).lower()
    except Exception:
        texto = ""
    invalidos = ("usuario ou senha invalid", "usuário ou senha inválid", "senha incorreta",
                 "credenciais invalid", "cpf/cnpj ou senha")
    if any(msg in texto for msg in invalidos):
        raise ErroLoginNFSe("Usuario ou senha invalidos no portal NFS-e.")

    if seletor_login_ok:
        try:
            page.locator(seletor_login_ok).first.wait_for(state="visible", timeout=timeout_ms)
            return
        except Exception as exc:
            raise ErroLoginNFSe("Login nao concluido: indicador de acesso nao encontrado.") from exc
    if "/login" in str(page.url).lower():
        raise ErroLoginNFSe("Login nao concluido; o portal permaneceu na tela de acesso.")


def validar_login_portal(config: object, documento: str) -> None:
    """Abre um contexto temporario e valida o login configurado da empresa."""
    cred = credenciais_para(config, documento)
    if cred.tipo_login != "usuario_senha":
        return
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=bool(getattr(config, "PLAYWRIGHT_HEADLESS", False)),
                channel=str(getattr(config, "CHROME_CHANNEL", "chrome") or "chrome"),
            )
            try:
                page = browser.new_page()
                login_usuario_senha(
                    page, cred.usuario_nfse, cred.senha_nfse,
                    login_url=str(getattr(config, "NFSE_LOGIN_URL", "")),
                    timeout_ms=int(getattr(config, "PLAYWRIGHT_LOGIN_TIMEOUT_S", 45)) * 1000,
                    seletor_login_ok=str(getattr(config, "NFSE_SELECTOR_LOGIN_OK", "")),
                )
            finally:
                browser.close()
    except ErroLoginNFSe:
        raise
    except Exception as exc:
        raise ErroLoginNFSe(f"Nao foi possivel iniciar o navegador para login NFS-e: {exc}") from exc
