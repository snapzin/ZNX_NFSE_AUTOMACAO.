"""Relatorio de conferencia (Excel) a partir dos XMLs baixados.

Le as notas em  PASTA_SAIDA/<Cliente>/{Emitidas,Recebidas,Canceladas}/*.xml,
extrai TODOS os campos fiscais relevantes (valores, base de calculo, aliquota,
ISS, PIS, COFINS, IRRF, CSLL, INSS, retencoes, deducoes, descricao do servico,
codigos de tributacao...) e gera:

  1. Um Excel POR CLIENTE  (<Cliente>/_Relatorio_<Cliente>.xlsx):
     uma aba por tipo (Emitidas/Recebidas/Canceladas), detalhada nota a nota,
     + uma aba Resumo com os totais e a CONFERENCIA de completude por NSU.

  2. Um Excel CONSOLIDADO (PASTA_SAIDA/_Relatorio_Consolidado.xlsx):
     uma linha por cliente com quantidade, valores e o veredito da conferencia.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

log = logging.getLogger("nfse.relatorio")

TIPOS = ("Emitidas", "Recebidas", "Canceladas")

# Catalogo de campos do relatorio detalhado.
#   (rotulo, largura, chave, tipo)  — tipo: "txt" | "money" | "pct" | "code"
# 'chave' e a coluna interna; para 'money'/'pct' o valor sai do XML como numero.
_CAMPOS = [
    ("Numero",            12, "numero",       "txt"),
    ("Competencia",       11, "competencia",  "txt"),
    ("Emissao",           11, "emissao",      "txt"),
    ("Prestador CNPJ/CPF", 17, "prest_doc",   "txt"),
    ("Prestador",         28, "prest_nome",   "txt"),
    ("Tomador CNPJ/CPF",  17, "toma_doc",     "txt"),
    ("Tomador",           28, "toma_nome",    "txt"),
    ("Municipio Incid.",  18, "municipio",    "txt"),
    ("Cod.Trib.Nac",      12, "cTribNac",     "txt"),
    ("Descricao Servico", 45, "xDescServ",    "txt"),
    ("Valor Servico",     14, "vServ",        "money"),
    ("Desc.Incond",       12, "vDescIncond",  "money"),
    ("Desc.Cond",         12, "vDescCond",    "money"),
    ("Deducoes",          12, "vDR",          "money"),
    ("Base Calculo",      14, "vBC",          "money"),
    ("Aliq ISS %",        10, "pAliqAplic",   "pct"),
    ("Valor ISS",         12, "vISSQN",       "money"),
    ("ISS Retido",        10, "issRetido",    "code"),
    ("PIS",               12, "vPis",         "money"),
    ("COFINS",            12, "vCofins",      "money"),
    ("IRRF",              12, "vRetIRRF",     "money"),
    ("CSLL",              12, "vRetCSLL",     "money"),
    ("INSS/CP",           12, "vRetCP",       "money"),
    ("Total Retencoes",   14, "vTotalRet",    "money"),
    ("Valor Liquido",     14, "vLiq",         "money"),
    ("Situacao",          11, "situacao",     "txt"),
    ("Chave de Acesso",   46, "chave",        "txt"),
    ("Arquivo",           38, "arquivo",      "txt"),
]
_CAMPOS_CONFERENCIA = [
    ("Descricao contrib. sociais retidas", 49, "descricaoRetSociais", "txt"),
    ("ISS Retido (valor)", 18, "vISSRetido", "money"),
    ("Total Retencoes XML", 21, "vTotalRetXML", "money"),
    ("Conferencia Retencoes", 85, "conferenciaRetencoes", "txt"),
]
_MONEY = [c[2] for c in _CAMPOS + _CAMPOS_CONFERENCIA if c[3] == "money"]
# NT 007/2026: vRetCSLL agrega contribuicoes sociais retidas. vPis/vCofins
# representam debitos de apuracao propria, e nao devem ser somados novamente
# ao total retido. As posicoes monetarias originais sao mantidas.
_ROTULOS_TRIBUTOS = {
    "vPis": ("PIS - Apuracao propria", 25),
    "vCofins": ("COFINS - Apuracao propria", 28),
    "vRetCSLL": ("Contrib. sociais retidas (PIS/COFINS/CSLL)", 42),
}
_RETENCOES_SOCIAIS = {
    "0": "PIS/COFINS/CSLL Nao Retidos",
    "1": "PIS/COFINS Retido",
    "2": "PIS/COFINS Nao Retido",
    "3": "PIS/COFINS/CSLL Retidos",
    "4": "PIS/COFINS Retidos, CSLL Nao Retido",
    "5": "PIS Retido, COFINS/CSLL Nao Retido",
    "6": "COFINS Retido, PIS/CSLL Nao Retido",
    "7": "PIS Nao Retido, COFINS/CSLL Retidos",
    "8": "PIS/COFINS Nao Retidos, CSLL Retido",
    "9": "COFINS Nao Retido, PIS/CSLL Retidos",
}
_NOTA_CONTRIBUICOES = (
    "Campo vRetCSLL do XML: contribuicoes sociais retidas (PIS, COFINS e/ou CSLL), "
    "conforme tpRetPisCofins e NT 007/2026. O XML nao discrimina as parcelas "
    "dessa soma. PIS/COFINS de apuracao propria nao sao somados novamente."
)
# Impostos mostrados no Resumo (rotulo -> chave)
_RESUMO_IMPOSTOS = [
    ("Valor Servico", "vServ"), ("Base Calculo", "vBC"), ("Valor ISS", "vISSQN"),
    ("PIS", "vPis"), ("COFINS", "vCofins"), ("IRRF", "vRetIRRF"),
    ("CSLL", "vRetCSLL"), ("INSS/CP", "vRetCP"),
    ("Total Retencoes", "vTotalRet"), ("Valor Liquido", "vLiq"),
]
_RESUMO_IMPOSTOS = [(_ROTULOS_TRIBUTOS.get(k, (rot, 0))[0], k)
                    for rot, k in _RESUMO_IMPOSTOS]


# parsing do XML

def _localname(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _txt(root, *nomes: str) -> str:
    if root is None:
        return ""
    alvo = {n.lower() for n in nomes}
    for el in root.iter():
        if _localname(el.tag).lower() in alvo and (el.text or "").strip():
            return el.text.strip()
    return ""


def _num(root, *nomes: str) -> float:
    v = _txt(root, *nomes)
    try:
        return float(v) if v else 0.0
    except ValueError:
        return 0.0


def _bloco(root, *nomes: str):
    alvo = {n.lower() for n in nomes}
    for el in root.iter():
        if _localname(el.tag).lower() in alvo:
            return el
    return None


def _filho(root, nome: str):
    """Filho direto: impede confundir valores do ISS com IBSCBS/valores."""
    if root is None:
        return None
    return next((el for el in root if _localname(el.tag) == nome), None)


def normalizar_iss_retido(valor: object) -> str:
    """Normaliza o indicador ``tpRetISSQN`` do XML nacional para Sim/Não.

    No leiaute nacional o campo nao e booleano: 1 = nao retido, 2 = retido
    pelo tomador e 3 = retido pelo intermediario. Tambem aceitamos as formas
    textuais/booleanas encontradas em XMLs convertidos por outros provedores.
    Valor ausente ou desconhecido e tratado como nao retido, mantendo a coluna
    do relatorio sempre binaria.
    """
    if isinstance(valor, bool):
        return "Sim" if valor else "Não"
    if valor is None:
        return "Não"

    texto = str(valor).strip()
    if not texto:
        return "Não"
    texto = "".join(
        ch for ch in unicodedata.normalize("NFKD", texto.lower())
        if not unicodedata.combining(ch)
    )
    texto = re.sub(r"[\s_-]+", " ", texto).strip()

    # Dominio oficial de tpRetISSQN.
    if texto in {"1", "nao", "n", "false", "0", "nao retido", "sem retencao"}:
        return "Não"
    if texto in {
        "2", "3", "sim", "s", "true", "retido", "com retencao",
        "retido pelo tomador", "retido pelo intermediario",
    }:
        return "Sim"
    log.warning("Valor desconhecido para tpRetISSQN: %r; considerando nao retido.", valor)
    return "Não"


_INVALIDO = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _sanitizar(nome: str) -> str:
    return _INVALIDO.sub("", nome or "").strip(". ")[:80] or "cliente"


def _conferir_retencoes(nota: dict, valores, valores_dps, federal, codigo_iss: str,
                       codigo_sociais: str) -> None:
    """Ajuste restrito: ISS nao retido indevido, confirmado tambem pelo liquido.

    Outras divergencias nao autorizam inventar valores fiscais. O total original
    permanece disponivel; casos ambiguos exigem conferencia, inclusive o legado.
    """
    def decimal_xml(bloco, tag, obrigatorio=False):
        texto = _txt(bloco, tag)
        if not texto and obrigatorio:
            raise ValueError(tag)
        valor = Decimal(texto or '0')
        if not valor.is_finite() or valor < 0:
            raise ValueError(tag)
        return valor

    nota['vISSRetido'] = nota['vISSQN'] if codigo_iss in {'2', '3'} else (0 if codigo_iss == '1' else None)
    nota['vTotalRetXML'] = None
    nota['conferenciaRetencoes'] = 'CONFERIR: dados fiscais ausentes ou invalidos; total nao ajustado'
    try:
        total = decimal_xml(valores, 'vTotalRet')
        nota['vTotalRetXML'] = float(total) if _txt(valores, 'vTotalRet') else None
    except (InvalidOperation, ValueError):
        nota['vTotalRet'] = None
        return
    try:
        iss = decimal_xml(valores, 'vISSQN')
        federal_retido = sum(decimal_xml(federal, k) for k in ('vRetIRRF', 'vRetCSLL', 'vRetCP'))
        servico = decimal_xml(valores_dps, 'vServ', obrigatorio=True)
        descontos = sum(decimal_xml(valores_dps, k) for k in ('vDescIncond', 'vDescCond'))
        liquido = decimal_xml(valores, 'vLiq', obrigatorio=True)
        pis_cofins = sum(decimal_xml(federal, k) for k in ('vPis', 'vCofins'))
    except (InvalidOperation, ValueError):
        return
    if codigo_iss not in {'1', '2', '3'}:
        nota['conferenciaRetencoes'] = 'CONFERIR: indicador de ISS ausente/desconhecido; total nao ajustado'
        return
    if (codigo_sociais == '1' and pis_cofins > 0) or (
        codigo_sociais and codigo_sociais not in _RETENCOES_SOCIAIS
    ):
        nota['conferenciaRetencoes'] = 'CONFERIR: dominio de contribuicoes legado/ambiguo; total nao ajustado'
        return
    if codigo_sociais == '0' and decimal_xml(federal, 'vRetCSLL') > 0:
        nota['conferenciaRetencoes'] = 'CONFERIR: contribuicoes declaradas sem retencao, mas com valor retido'
        return
    esperado = federal_retido + (iss if codigo_iss in {'2', '3'} else Decimal('0'))
    if total == esperado and liquido == servico - descontos - esperado:
        nota['conferenciaRetencoes'] = 'OK'
    elif (codigo_iss == '1' and iss > 0 and total == esperado + iss
          and liquido == servico - descontos - esperado):
        nota['vTotalRet'] = float(esperado)
        nota['conferenciaRetencoes'] = 'AJUSTADO: ISS nao retido excluido do total; valor original na coluna Total Retencoes XML'
    else:
        nota['conferenciaRetencoes'] = 'CONFERIR: total/liquido diverge das retencoes discriminadas; valor XML preservado'


def extrair_nota(xml_path: Path, situacao: str = "") -> dict | None:
    """Le um XML de NFS-e e devolve um dict {chave_do_campo: valor} com todos
    os campos fiscais do relatorio. None se nao parsear."""
    try:
        root = ET.fromstring(xml_path.read_text(encoding="utf-8", errors="replace"))
    except (ET.ParseError, OSError):
        return None
    emit = _bloco(root, "emit", "prest")
    toma = _bloco(root, "toma", "tomador")
    inf = _bloco(root, "infNFSe")
    compet = _txt(root, "dCompet")
    emi = _txt(root, "dhEmi", "dhProc", "dhEvento")
    chave = (inf.get("Id") if inf is not None else "") or ""
    tp_ret_iss = _txt(root, "tpRetISSQN")
    valores_nfse = _filho(inf, "valores")
    # XML nacional: ISS e totais pertencem a infNFSe/valores. O mesmo nome
    # vBC tambem existe em infNFSe/IBSCBS/valores, sem representar base de ISS.
    valores_iss = valores_nfse if inf is not None else root
    federal = _bloco(root, "tribFed")
    codigo_ret_sociais = _txt(federal, "tpRetPisCofins")
    descricao_ret_sociais = (
        f"{codigo_ret_sociais} - {_RETENCOES_SOCIAIS.get(codigo_ret_sociais, 'Codigo nao reconhecido')}"
        if codigo_ret_sociais else "Nao informado"
    )
    nota = {
        "numero": _txt(root, "nNFSe", "nDPS"),
        "competencia": compet[:7] if compet else (emi[:7] if emi else ""),
        "emissao": emi[:10] if emi else "",
        "prest_doc": _txt(emit, "CNPJ", "CPF", "NIF"),
        "prest_nome": _txt(emit, "xNome", "xRazSoc", "xFant"),
        "toma_doc": _txt(toma, "CNPJ", "CPF", "NIF"),
        "toma_nome": _txt(toma, "xNome", "xRazSoc", "xFant"),
        "municipio": _txt(root, "xLocIncid", "xLocEmi"),
        "cTribNac": _txt(root, "cTribNac"),
        "xDescServ": _txt(root, "xDescServ", "xTribNac"),
        "vServ": _num(root, "vServ"),
        "vDescIncond": _num(root, "vDescIncond"),
        "vDescCond": _num(root, "vDescCond"),
        "vDR": _num(root, "vDR"),
        "vBC": _num(valores_iss, "vBC"),
        "pAliqAplic": _num(valores_iss, "pAliqAplic"),
        "vISSQN": _num(valores_iss, "vISSQN"),
        "issRetido": normalizar_iss_retido(tp_ret_iss),
        "vPis": _num(root, "vPis"),
        "vCofins": _num(root, "vCofins"),
        "vRetIRRF": _num(root, "vRetIRRF"),
        "vRetCSLL": _num(root, "vRetCSLL"),
        "vRetCP": _num(root, "vRetCP"),
        "vTotalRet": _num(valores_iss, "vTotalRet"),
        "vLiq": _num(valores_iss, "vLiq"),
        "descricaoRetSociais": descricao_ret_sociais,
        "situacao": situacao or "Normal",
        "chave": re.sub(r"\D", "", chave),
        "arquivo": xml_path.name,
    }
    dps = _bloco(root, 'infDPS')
    valores_dps = _filho(dps, 'valores') if dps is not None else root
    _conferir_retencoes(nota, valores_iss, valores_dps, federal, tp_ret_iss, codigo_ret_sociais)
    return nota


# montagem do Excel

_HDR_FILL = PatternFill("solid", fgColor="1F4E78")
_HDR_FONT = Font(bold=True, color="FFFFFF")
_TOT_FILL = PatternFill("solid", fgColor="D9E1F2")
_TOT_FONT = Font(bold=True)
_TITULO_FONT = Font(bold=True, size=13)
_VERDE, _VERMELHO = "1E7D34", "C00000"


def _escrever_aba(ws, notas: list[dict]) -> dict:
    """Preenche uma aba com as notas (detalhadas) + linha de TOTAL.
    Retorna {qtd, <chave_money>: soma, ...}."""
    campos = [(*_ROTULOS_TRIBUTOS.get(k, (rot, larg)), k, t)
              for rot, larg, k, t in _CAMPOS] + _CAMPOS_CONFERENCIA
    rotulos = [c[0] for c in campos]
    ws.append(rotulos)
    for ci, (rot, larg, _k, _t) in enumerate(campos, start=1):
        cell = ws.cell(row=1, column=ci)
        cell.fill = _HDR_FILL
        cell.font = _HDR_FONT
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(ci)].width = larg
        if _k == "vRetCSLL":
            cell.comment = Comment(_NOTA_CONTRIBUICOES, "NFSe")
        elif _k in {"vPis", "vCofins"}:
            cell.comment = Comment("Debito de apuracao propria, nao valor retido. Nao somar ao Total Retencoes.", "NFSe")
        elif _k == "vTotalRet":
            cell.comment = Comment("Total com conferencia de retencoes. Ajustes e divergencias constam nas colunas finais; o XML original e o valor liquido sao preservados.", "NFSe")

    tot = {"qtd": 0, "retencoesAjustadas": 0, "retencoesConferir": 0}
    for k in _MONEY:
        tot[k] = 0.0
    for nota in notas:
        ws.append([nota.get(c[2], "") for c in campos])
        status_retencoes = nota.get('conferenciaRetencoes', '')
        tot['retencoesAjustadas'] += int(status_retencoes.startswith('AJUSTADO'))
        tot['retencoesConferir'] += int(status_retencoes.startswith('CONFERIR'))
        if nota.get('conferenciaRetencoes', 'OK') != 'OK':
            for ci in (24, 31, 32):
                ws.cell(ws.max_row, ci).fill = PatternFill('solid', fgColor='FFF2CC')
        tot["qtd"] += 1
        for k in _MONEY:
            tot[k] += float(nota.get(k) or 0)

    # formato numerico nas colunas de valor/percentual
    fmt_idx = [(i + 1, c[3]) for i, c in enumerate(campos) if c[3] in ("money", "pct")]
    for r in range(2, ws.max_row + 1):
        for ci, _t in fmt_idx:
            ws.cell(row=r, column=ci).number_format = "#,##0.00"

    # linha TOTAL
    linha = [""] * len(campos)
    linha[0] = f"TOTAL ({tot['qtd']})"
    for i, c in enumerate(campos):
        if c[3] == "money":
            linha[i] = tot[c[2]]
    ws.append(linha)
    rt = ws.max_row
    for ci in range(1, len(campos) + 1):
        ws.cell(row=rt, column=ci).fill = _TOT_FILL
        ws.cell(row=rt, column=ci).font = _TOT_FONT
    for ci, _t in fmt_idx:
        ws.cell(row=rt, column=ci).number_format = "#,##0.00"
    ws.freeze_panes = "A2"
    return tot


@dataclass
class ResumoCliente:
    nome: str = ""
    cnpj: str = ""
    totais: dict = field(default_factory=dict)       # tipo -> {qtd, vServ, ...}
    conferencia: dict = field(default_factory=dict)  # {total_caixa, recebidos, faltando, completo}


def _veredito(conf: dict) -> tuple[str, str]:
    if not conf:
        return "—", "808080"
    if conf.get("completo", True):
        return "BATEU — caixa completa, sem notas faltando", _VERDE
    faltam = conf.get("faltando") or []
    return f"NAO BATEU — faltam {len(faltam)} NSU: {', '.join(map(str, faltam[:20]))}", _VERMELHO


def gerar_excel_cliente(pasta_cliente: Path, nome_cliente: str, cnpj: str = "",
                        conferencia: dict | None = None) -> ResumoCliente | None:
    """Gera o Excel detalhado de UM cliente. Retorna o resumo (totais por tipo)
    ou None se nao houver nenhuma nota."""
    pasta_cliente = Path(pasta_cliente)
    resumo = ResumoCliente(nome=nome_cliente, cnpj=cnpj, conferencia=conferencia or {})
    wb = Workbook()
    wb.remove(wb.active)

    houve = False
    for tipo in TIPOS:
        sub = pasta_cliente / tipo
        if not sub.is_dir():
            continue
        situacao = "Cancelada" if tipo == "Canceladas" else "Normal"
        notas = []
        for xml in sorted(sub.glob("*.xml")):
            if xml.name.lower().startswith("cancelamento"):
                continue  # XML do evento (nao e a nota) — nao entra nos valores
            nota = extrair_nota(xml, situacao=situacao)
            if nota is not None:
                notas.append(nota)
        if not notas:
            continue
        houve = True
        ws = wb.create_sheet(title=tipo)
        resumo.totais[tipo] = _escrever_aba(ws, notas)

    if not houve:
        return None

    _montar_resumo(wb, resumo)

    destino = pasta_cliente / f"_Relatorio_{_sanitizar(nome_cliente)}.xlsx"
    try:
        wb.save(destino)
        log.info("[%s] Relatorio gerado: %s", cnpj or nome_cliente, destino.name)
    except OSError as exc:
        log.warning("Falha ao salvar relatorio de %s: %s", nome_cliente, exc)
    return resumo


def _montar_resumo(wb: Workbook, resumo: ResumoCliente) -> None:
    """Aba Resumo: totais de impostos por tipo + conferencia de completude."""
    ws = wb.create_sheet(title="Resumo", index=0)
    ws["A1"] = f"Relatorio de conferencia — {resumo.nome}"
    ws["A1"].font = _TITULO_FONT
    if resumo.cnpj:
        ws["A2"] = f"CNPJ: {resumo.cnpj}"
    ws.append([])

    cab = ["Tipo", "Qtd"] + [rotulo for rotulo, chave in _RESUMO_IMPOSTOS]
    ws.append(cab)
    hr = ws.max_row
    for ci in range(1, len(cab) + 1):
        ws.cell(row=hr, column=ci).fill = _HDR_FILL
        ws.cell(row=hr, column=ci).font = _HDR_FONT
        if cab[ci - 1] == _ROTULOS_TRIBUTOS['vRetCSLL'][0]:
            ws.cell(row=hr, column=ci).comment = Comment(_NOTA_CONTRIBUICOES, "NFSe")
    for tipo in TIPOS:
        t = resumo.totais.get(tipo)
        if not t:
            continue
        ws.append([tipo, t["qtd"]] + [t.get(chave, 0) for _rot, chave in _RESUMO_IMPOSTOS])
    for r in range(hr + 1, ws.max_row + 1):
        for ci in range(3, len(cab) + 1):
            ws.cell(row=r, column=ci).number_format = "#,##0.00"
    ws.column_dimensions["A"].width = 13
    ws.column_dimensions["B"].width = 7
    for ci in range(3, len(cab) + 1):
        ws.column_dimensions[get_column_letter(ci)].width = 15
        if cab[ci - 1] in {v[0] for v in _ROTULOS_TRIBUTOS.values()}:
            ws.column_dimensions[get_column_letter(ci)].width = 42

    ws.append([])
    ws.append(['Conferencia fiscal das retencoes (independente da completude do download)'])
    for rotulo, chave in (('Notas com retencoes ajustadas', 'retencoesAjustadas'),
                          ('Notas com retencoes a conferir', 'retencoesConferir')):
        quantidade = sum(t.get(chave, 0) for t in resumo.totais.values())
        ws.append([rotulo, quantidade])
        if quantidade:
            ws.cell(ws.max_row, 1).fill = PatternFill('solid', fgColor='FFF2CC')
    ws.append(['Consulte Total Retencoes XML e Conferencia Retencoes nas abas detalhadas.'])

    # Bloco CONFERENCIA (completude por NSU) — veredito BATEU / NAO BATEU
    if resumo.conferencia:
        conf = resumo.conferencia
        ws.append([])
        ws.append(["Conferencia (completude por NSU)"])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, size=12)
        ws.append(["Total de documentos no ADN", conf.get("total_caixa", 0)])
        ws.append(["Documentos recebidos", conf.get("recebidos", 0)])
        ws.append(["NSU faltando", len(conf.get("faltando") or [])])
        texto, cor = _veredito(conf)
        ws.append([texto])
        cell = ws.cell(row=ws.max_row, column=1)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=cor)


def gerar_consolidado(base_saida: Path, resumos: list[ResumoCliente]) -> Path | None:
    """Excel consolidado: uma linha por cliente com qtd, valores e veredito."""
    resumos = [r for r in resumos if r and r.totais]
    if not resumos:
        return None
    wb = Workbook()
    ws = wb.active
    ws.title = "Consolidado"
    ws["A1"] = "Relatorio Consolidado — NFS-e por cliente"
    ws["A1"].font = _TITULO_FONT
    ws.append([])

    cab = ["Cliente", "CNPJ", "Emit Qtd", "Emit Valor", "Receb Qtd", "Receb Valor",
           "Canc Qtd", "Canc Valor", "Total Notas", "Total Servico",
           "Total ISS", "Total Retencoes", "Conferencia", "Retencoes a conferir"]
    ws.append(cab)
    hr = ws.max_row
    for ci in range(1, len(cab) + 1):
        ws.cell(row=hr, column=ci).fill = _HDR_FILL
        ws.cell(row=hr, column=ci).font = _HDR_FONT

    def _g(r: ResumoCliente, tipo: str, chave: str):
        return (r.totais.get(tipo) or {}).get(chave, 0)

    soma = {"qtd": 0, "serv": 0.0}
    divergentes = 0
    for r in sorted(resumos, key=lambda x: x.nome.lower()):
        e_q, e_v = _g(r, "Emitidas", "qtd"), _g(r, "Emitidas", "vServ")
        r_q, r_v = _g(r, "Recebidas", "qtd"), _g(r, "Recebidas", "vServ")
        c_q, c_v = _g(r, "Canceladas", "qtd"), _g(r, "Canceladas", "vServ")
        tot_q = e_q + r_q + c_q
        tot_v = e_v + r_v + c_v
        iss = _g(r, "Emitidas", "vISSQN") + _g(r, "Recebidas", "vISSQN") + _g(r, "Canceladas", "vISSQN")
        ret = _g(r, "Emitidas", "vTotalRet") + _g(r, "Recebidas", "vTotalRet") + _g(r, "Canceladas", "vTotalRet")
        soma["qtd"] += tot_q
        soma["serv"] += tot_v
        completo = (r.conferencia or {}).get("completo", True)
        falt = len((r.conferencia or {}).get("faltando") or [])
        verdito = "BATEU" if completo else f"NAO BATEU ({falt} NSU)"
        if not completo:
            divergentes += 1
        pendencias = sum(t.get('retencoesConferir', 0) for t in r.totais.values())
        ws.append([r.nome, r.cnpj, e_q, e_v, r_q, r_v, c_q, c_v, tot_q, tot_v, iss, ret, verdito, pendencias])
        cv = ws.cell(row=ws.max_row, column=13)
        cv.font = Font(bold=True, color="FFFFFF")
        cv.fill = PatternFill("solid", fgColor=(_VERDE if completo else _VERMELHO))

    ws.append(["TOTAL GERAL", "", "", "", "", "", "", "", soma["qtd"], soma["serv"], "", "",
               "TODOS OK" if divergentes == 0 else f"{divergentes} divergente(s)",
               sum(t.get('retencoesConferir', 0) for r in resumos for t in r.totais.values())])
    rt = ws.max_row
    for ci in range(1, len(cab) + 1):
        ws.cell(row=rt, column=ci).fill = _TOT_FILL
        ws.cell(row=rt, column=ci).font = _TOT_FONT

    for r in range(hr + 1, ws.max_row + 1):
        for ci in (4, 6, 8, 10, 11, 12):
            ws.cell(row=r, column=ci).number_format = "#,##0.00"
    for ci, larg in enumerate((34, 18, 9, 14, 9, 14, 9, 14, 11, 15, 14, 15, 20, 24), start=1):
        ws.column_dimensions[get_column_letter(ci)].width = larg
    ws.freeze_panes = "A4"

    destino = Path(base_saida) / "_Relatorio_Consolidado.xlsx"
    try:
        wb.save(destino)
        log.info("Relatorio consolidado gerado: %s (%d cliente(s))", destino, len(resumos))
    except OSError as exc:
        log.warning("Falha ao salvar consolidado: %s", exc)
        return None
    return destino
