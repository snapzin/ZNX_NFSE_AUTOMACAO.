"""Auditoria local e somente leitura no ADN. Nunca imprime nomes/senhas dos PFX.

Uso: python tools/conferir_certificados.py arquivo.zip --saida PASTA --consultar
Baixa uma amostra (primeiro lote) por certificado, nao uma caixa completa.
Os XMLs/Excel privados ficam apenas na pasta de saida explicitamente informada.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, pkcs12
from cryptography.x509 import NameOID
from openpyxl import load_workbook

from app.automation.cert_reader import _parse_nome_arquivo, _extrair_documento
from app.automation.adn_client import ADN_BASE_URL, decode_arquivo_xml, parse_meta, classify_nota
from app.reports.relatorio import extrair_nota, gerar_excel_cliente


def texto(root, nome):
    return next(((e.text or '').strip() for e in root.iter() if e.tag.rsplit('}', 1)[-1] == nome), '')


def conferir_xml(path: Path) -> dict:
    root = ET.fromstring(path.read_bytes())
    codigo = texto(root, 'tpRetISSQN')
    esperado = {'1': 'Não', '2': 'Sim', '3': 'Sim'}.get(codigo)
    nota = extrair_nota(path)
    campos = ('vServ', 'vDescIncond', 'vDescCond', 'vISSQN', 'vRetCP', 'vRetIRRF', 'vRetCSLL', 'vTotalRet', 'vLiq')
    valores = {k: Decimal(texto(root, k) or '0') for k in campos}
    diferencas = [k for k in campos if abs(Decimal(str(nota[k])) - valores[k]) > Decimal('0.001')]
    liquido = valores['vServ'] - valores['vDescIncond'] - valores['vDescCond'] - valores['vTotalRet']
    # Identifica inconsistencia no documento de origem; nunca altera o XML.
    retencoes = valores['vRetCP'] + valores['vRetIRRF'] + valores['vRetCSLL']
    if esperado == 'Sim':
        retencoes += valores['vISSQN']
    return {
        'codigo': codigo, 'indicador_confere': esperado is not None and nota['issRetido'] == esperado,
        'campos_divergentes': diferencas,
        'liquido_xml_confere': abs(liquido - valores['vLiq']) <= Decimal('0.02'),
        'retencoes_basicas_xml_conferem': abs(retencoes - valores['vTotalRet']) <= Decimal('0.02'),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('zip', type=Path)
    parser.add_argument('--saida', type=Path, required=True)
    parser.add_argument('--consultar', action='store_true')
    parser.add_argument('--limite', type=int, default=0)
    args = parser.parse_args()
    args.saida.mkdir(parents=True, exist_ok=True)
    logging.disable(logging.CRITICAL)
    resultados = []
    with ZipFile(args.zip) as archive:
        entries = [e for e in archive.infolist() if Path(e.filename).suffix.lower() in {'.pfx', '.p12'}]
        if args.limite:
            entries = entries[:args.limite]
        for indice, entry in enumerate(entries, 1):
            alias = f'certificado-{indice:03d}'
            item = {'id': alias, 'extensao': Path(entry.filename).suffix.lower()}
            resultados.append(item)
            try:
                parsed = _parse_nome_arquivo(Path(entry.filename).stem)
                if not parsed:
                    item['status'] = 'senha_nao_identificada_no_nome'
                    continue
                _, senha = parsed
                try:
                    key, cert, chain = pkcs12.load_key_and_certificates(archive.read(entry), senha.encode('utf-8'))
                except (ValueError, TypeError):
                    item['status'] = 'senha_ou_pkcs12_invalido'
                    continue
                if key is None or cert is None:
                    item['status'] = 'sem_chave_ou_certificado'
                    continue
                cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
                serial = cert.subject.get_attributes_for_oid(NameOID.SERIAL_NUMBER)
                doc = _extrair_documento(cn[0].value if cn else '', serial[0].value if serial else '')
                item.update(aberto=True, documento_identificado=bool(doc), validade=cert.not_valid_after_utc.date().isoformat())
                if not cert.not_valid_before_utc <= datetime.now(timezone.utc) <= cert.not_valid_after_utc:
                    item['status'] = 'fora_da_validade'
                    continue
                if not args.consultar:
                    item['status'] = 'aberto_sem_consulta'
                    continue
                # Nomes controlados; nenhum arquivo do ZIP e extraido por caminho.
                with TemporaryDirectory(prefix='nfse-mtls-') as temp, requests.Session() as session:
                    cp, kp = Path(temp) / 'cert.pem', Path(temp) / 'key.pem'
                    cp.write_bytes(cert.public_bytes(Encoding.PEM))
                    kp.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
                    session.cert = (str(cp), str(kp))
                    response = session.get(f'{ADN_BASE_URL}/0?lote=true', headers={'Accept': 'application/json'}, timeout=(15, 40), allow_redirects=False)
                    item['http'] = response.status_code
                    if response.status_code != 200:
                        item['status'] = 'sem_documentos' if response.status_code == 404 else 'falha_http'
                        continue
                    data = response.json()
                lote = data.get('LoteDFe') or []
                item['documentos_lote'] = len(lote)
                pasta = args.saida / alias
                audits = []
                for numero, registro in enumerate(lote, 1):
                    xml = decode_arquivo_xml(registro.get('ArquivoXml', ''))
                    root = ET.fromstring(xml)
                    if root.tag.rsplit('}', 1)[-1] != 'NFSe':
                        continue
                    tipo = classify_nota(parse_meta(xml), doc)
                    if tipo not in {'Emitidas', 'Recebidas'}:
                        continue
                    destino = pasta / tipo / f'nota-{numero:04d}.xml'
                    destino.parent.mkdir(parents=True, exist_ok=True)
                    destino.write_text(xml, encoding='utf-8')
                    audit = conferir_xml(destino)
                    audit['arquivo'] = str(destino.relative_to(args.saida))
                    audits.append(audit)
                gerar_excel_cliente(pasta, alias, conferencia={})
                excel_divergencias = 0
                xlsx = pasta / f'_Relatorio_{alias}.xlsx'
                if xlsx.exists():
                    wb = load_workbook(xlsx, data_only=True)
                    try:
                        for tipo in ('Emitidas', 'Recebidas'):
                            if tipo not in wb:
                                continue
                            ws = wb[tipo]
                            headers = [c.value for c in ws[1]]
                            for row, xmlpath in zip(list(ws.iter_rows(min_row=2, values_only=True)), sorted((pasta / tipo).glob('*.xml'))):
                                root = ET.fromstring(xmlpath.read_bytes())
                                expected = {'1': 'Não', '2': 'Sim', '3': 'Sim'}.get(texto(root, 'tpRetISSQN'))
                                excel_divergencias += row[headers.index('ISS Retido')] != expected
                                for label, tag in (('Valor Servico', 'vServ'), ('Valor ISS', 'vISSQN'), ('Total Retencoes', 'vTotalRet'), ('Valor Liquido', 'vLiq')):
                                    expected_number = Decimal(texto(root, tag) or '0')
                                    excel_divergencias += abs(Decimal(str(row[headers.index(label)] or 0)) - expected_number) > Decimal('0.001')
                    finally:
                        wb.close()
                item.update(status='consultado', notas=len(audits), codigos=dict(Counter(a['codigo'] for a in audits)), divergencias_excel=excel_divergencias, auditoria=audits)
            except Exception as exc:
                # Mensagens de bibliotecas podem conter paths/identidades; imprime somente classe.
                item.update(status='erro', classe_erro=type(exc).__name__)
            finally:
                print(json.dumps({k: v for k, v in item.items() if k != 'auditoria'}, ensure_ascii=True), flush=True)
                (args.saida / 'auditoria.json').write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'resumo': dict(Counter(i['status'] for i in resultados))}), flush=True)


if __name__ == '__main__':
    main()
