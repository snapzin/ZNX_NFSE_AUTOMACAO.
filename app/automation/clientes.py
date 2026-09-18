"""Compatibilidade dos cadastros antigos com o acesso somente por certificado."""
import re


def clientes_por_certificado(clientes: list[dict]) -> list[dict]:
    unicos = {}
    preferidos = set()
    for cliente in clientes:
        if not isinstance(cliente, dict):
            continue
        doc = re.sub(r"\D", "", str(cliente.get("documento", "")))
        ativo = cliente.get("ativo", True)
        if isinstance(ativo, str):
            ativo = ativo.strip().lower() not in {"nao", "não", "false", "0", "n", ""}
        anterior = unicos.get(doc)
        nome = str(cliente.get("nome") or doc).strip()
        certificado = cliente.get("tipo_login", "certificado") == "certificado"
        if anterior:
            anterior["ativo"] = anterior["ativo"] or bool(ativo)
            if certificado and doc not in preferidos:
                anterior["nome"] = nome
        else:
            unicos[doc] = {"documento": doc, "nome": nome, "ativo": bool(ativo), "tipo_login": "certificado"}
        if certificado:
            preferidos.add(doc)
    return list(unicos.values())
