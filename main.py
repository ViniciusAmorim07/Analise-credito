import os
import unicodedata
import httpx
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RELINQ_URL = (os.getenv("RELINQ_URL") or "").strip()
RELINQ_TOKEN = (os.getenv("RELINQ_TOKEN") or "").strip()


def normalizar_texto(texto: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(texto or ""))
    sem_acento = "".join(c for c in nfkd if not unicodedata.combining(c))
    return sem_acento.upper().strip()


def parse_data_flexivel(data_str: str):
    if not data_str:
        return None
    limpa = str(data_str).strip()
    formatos = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y"]
    for fmt in formatos:
        try:
            return datetime.strptime(limpa, fmt)
        except ValueError:
            continue
    return None


def calcular_idade_divida(data_str: str) -> int:
    data_obj = parse_data_flexivel(data_str)
    if not data_obj:
        return 0
    return (datetime.now() - data_obj).days


def extrair_processos_unicos(dados_data: dict) -> list:
    
    processos = []
    vistos = set()

    def varrer(obj, caminho=""):
        if isinstance(obj, dict):
            if "crednet" in caminho:
                return

            num_unico = obj.get("numeroProcessoUnico") or obj.get("numeroProcesso")
            tem_metadados_proc = any(
                k in obj for k in ["assuntosCNJ", "classeProcessual", "statusPj", "area"]
            )

            if num_unico and tem_metadados_proc:
                chave_id = str(num_unico).strip()
                if chave_id not in vistos:
                    vistos.add(chave_id)
                    processos.append(obj)
                return

            for k, v in obj.items():
                varrer(v, f"{caminho}.{k}")

        elif isinstance(obj, list):
            for idx, item in enumerate(obj):
                varrer(item, f"{caminho}[{idx}]")

    varrer(dados_data)
    return processos


def classificar_processo_criminal(proc: dict, cpf_consultado: str) -> dict:
    # 1. Verificação de Responsabilidade (Polo Passivo ou Ativo)
    infos_parte = proc.get("informacoesPartePessoaConsultada") or []
    tipos_parte = [normalizar_texto(t) for t in (proc.get("tipoPartePessoaConsultada") or [])]

    polos_passivos_validos = {
        "PASSIVO", "AVERIGUADO", "INDICIADO", "REU", "AUTOR DO FATO",
        "INVESTIGADO", "ACUSADO", "CONDENADO", "EXECUTADO", "FLAGRANTEADO"
    }
    polos_excluidos = {"VITIMA", "TESTEMUNHA", "TERCEIRO", "ADVOGADO", "PERITO"}

    eh_passivo = False
    papel_desc = "Não identificado"

    for info in infos_parte:
        polo = normalizar_texto(info.get("polo", ""))
        tipo = normalizar_texto(info.get("tipo", ""))
        if polo == "PASSIVO" or tipo in polos_passivos_validos:
            eh_passivo = True
            papel_desc = f"Passivo ({tipo or 'Réu/Averiguado'})"
            break
        if tipo in polos_excluidos:
            papel_desc = tipo

    if not eh_passivo and any(t in polos_passivos_validos for t in tipos_parte):
        eh_passivo = True
        papel_desc = f"Passivo ({', '.join(tipos_parte)})"

    # Fallback olhando a lista completa de partes pelo CPF
    if not eh_passivo and not infos_parte and not tipos_parte:
        for parte in (proc.get("partes") or []):
            doc_parte = "".join(filter(str.isdigit, str(parte.get("cpf", ""))))
            if doc_parte == cpf_consultado:
                polo_p = normalizar_texto(parte.get("polo", ""))
                tipo_p = normalizar_texto(parte.get("tipo", ""))
                if polo_p == "PASSIVO" or tipo_p in polos_passivos_validos:
                    eh_passivo = True
                    papel_desc = f"Passivo ({tipo_p or 'Réu/Averiguado'})"
                elif tipo_p in polos_excluidos:
                    papel_desc = tipo_p

    if not eh_passivo:
        return {"ignorar": True, "motivo": f"Consultado figura como {papel_desc}"}

    # 2. Extração de Assuntos CNJ, Classe e Órgão Julgador
    assuntos = proc.get("assuntosCNJ") or []
    titulos_assuntos = [normalizar_texto(a.get("titulo", "")) for a in assuntos if a.get("titulo")]
    codigos_assuntos = {str(a.get("codigo", "")).strip() for a in assuntos if a.get("codigo")}
    texto_assuntos = " | ".join(titulos_assuntos)

    assunto_principal = next(
        (a.get("titulo") for a in assuntos if a.get("principal") and a.get("titulo")),
        assuntos[0].get("titulo") if assuntos else "Natureza Criminal"
    )

    classe_nome = normalizar_texto((proc.get("classeProcessual") or {}).get("nome", ""))
    orgao_julgador = normalizar_texto(proc.get("orgaoJulgador", ""))
    base_completa = f"{texto_assuntos} {classe_nome} {orgao_julgador}"

    # 3. Classificação: Contra a Pessoa vs Contra o Patrimônio vs Outros
    termos_pessoa = [
        "CONTRA A PESSOA", "HOMICIDIO", "LESAO CORPORAL", "AMEACA", "CALUNIA",
        "INJURIA", "DIFAMACAO", "SEQUESTRO", "CARCERE PRIVADO", "CONSTRANGIMENTO ILEGAL",
        "VIOLENCIA DOMESTICA", "FEMINICIDIO", "VIAS DE FATO", "MAUS TRATOS",
        "PERICLITACAO DA VIDA", "ABORTO", "INFANTICIDIO"
    ]
    termos_patrimonio = [
        "CONTRA O PATRIMONIO", "ESTELIONATO", "FURTO", "ROUBO", "APROPRIACAO INDEBITA",
        "RECEPTACAO", "EXTORSAO", "DANO", "USURPACAO", "LATROCINIO", "FRAUDE"
    ]

    if "3369" in codigos_assuntos or any(t in texto_assuntos for t in termos_pessoa):
        natureza = "Contra a Pessoa"
    elif "3415" in codigos_assuntos or "3431" in codigos_assuntos or any(t in texto_assuntos for t in termos_patrimonio):
        natureza = "Contra o Patrimônio"
    else:
        natureza = "Outros Bens Jurídicos"

    # 4. Classificação: Doloso vs Culposo
    elemento_subjetivo = "Culposo" if "CULPOS" in texto_assuntos else "Doloso"

    # 5. Classificação: Simples vs Qualificado
    modalidade = (
        "Qualificado"
        if any(t in texto_assuntos for t in ["QUALIFICAD", "MAJORAD", "HEDIONDO"])
        else "Simples"
    )

    # 6. Verificação Lei nº 9.099/1995 (Infração de Menor Potencial Ofensivo)
    crimes_menor_potencial = [
        "VIAS DE FATO", "AMEACA", "LESAO CORPORAL LEVE", "LESAO CORPORAL CULPOSA",
        "INJURIA", "CALUNIA", "DIFAMACAO", "DESACATO", "DESOBEDIENCIA",
        "PERTURBACAO DO SOSSEGO", "POSSE DE DROGAS PARA CONSUMO", "USO DE DROGAS",
        "CONSTRANGIMENTO ILEGAL", "DANO SIMPLES", "DIRECAO PERIGOSA", "DIRIGIR SEM HABILITACAO"
    ]
    eh_lei_9099 = (
        modalidade == "Simples"
        and (
            "JUIZADO ESPECIAL" in orgao_julgador
            or "JECRIM" in orgao_julgador
            or "TERMO CIRCUNSTANCIADO" in classe_nome
            or "MENOR POTENCIAL OFENSIVO" in base_completa
            or "9.099" in base_completa
            or "9099" in base_completa
            or any(c in texto_assuntos for c in crimes_menor_potencial)
        )
    )

    # 7. Verificação Temporal (> 5 anos) e Status (Extinto / Encerrado / Sem movimentação)
    status_pj = proc.get("statusPj") or {}
    status_proc = normalizar_texto(status_pj.get("statusProcesso", ""))
    status_obs = normalizar_texto(proc.get("statusObservacao", ""))
    status_unificado = f"{status_proc} {status_obs}".strip()

    termos_encerrado = [
        "EXTINT", "ARQUIVAD", "ENCERRAD", "BAIXAD", "PRESCRIT",
        "ABSOLVID", "CUMPRID", "TRANSITADO EM JULGADO"
    ]
    esta_encerrado = (
        any(t in status_unificado for t in termos_encerrado)
        and "TRAMITACAO" not in status_unificado
    )

    data_ref = parse_data_flexivel(proc.get("dataDistribuicao") or proc.get("dataAutuacao") or "")
    anos_decorridos = round((datetime.now() - data_ref).days / 365.25, 1) if data_ref else 0.0
    mais_de_5_anos = anos_decorridos > 5.0

    # 8. Decisão Final da Matriz Jurídica
    if eh_lei_9099:
        decisao = "APONTAMENTO"
        motivo_regra = "Infração de menor potencial ofensivo (Lei nº 9.099/1995) — Apontamento sem reprovação automática"
    elif mais_de_5_anos and esta_encerrado:
        if elemento_subjetivo == "Doloso" or modalidade == "Qualificado" or natureza == "Contra a Pessoa":
            decisao = "REPROVADO"
            motivo_regra = f"Crime > 5 anos encerrado, mas enquadrado na exceção ({natureza} / {elemento_subjetivo} / {modalidade})"
        else:
            decisao = "APONTAMENTO"
            motivo_regra = "Crime > 5 anos extinto/encerrado (Culposo/Simples fora de crime contra a pessoa) — Apontamento sem reprovação"
    else:
        decisao = "REPROVADO"
        motivo_regra = f"Processo criminal ativo ou recente ({status_proc or 'Em andamento'})"

    resumo_classificacao = f"{assunto_principal} ({natureza} | {elemento_subjetivo} | {modalidade})"

    return {
        "ignorar": False,
        "numero_processo": proc.get("numeroProcessoUnico", "N/A"),
        "tribunal": proc.get("tribunal", "N/A"),
        "crime_principal": assunto_principal,
        "responsabilidade": papel_desc,
        "natureza": natureza,
        "elemento_subjetivo": elemento_subjetivo,
        "modalidade": modalidade,
        "lei_9099": eh_lei_9099,
        "anos_decorridos": anos_decorridos,
        "status_processo": status_proc or "N/A",
        "decisao": decisao,
        "motivo_regra": motivo_regra,
        "resumo_classificacao": resumo_classificacao,
    }


@app.post("/analisar-cpf/")
async def analisar_cpf_endpoint(dados: dict = Body(...)):
    cpf = "".join(filter(str.isdigit, str(dados.get("cpf", ""))))

    if not cpf:
        return {"erro": "CPF não informado."}

    if not RELINQ_URL.startswith("http"):
        return {"erro": "RELINQ_URL inválida ou não configurada no servidor."}

    if not RELINQ_TOKEN:
        return {"erro": "RELINQ_TOKEN não configurado no servidor."}

    headers = {
        "Authorization": f"Bearer {RELINQ_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    url_final = (
        RELINQ_URL.replace("{cpf}", cpf)
        if "{cpf}" in RELINQ_URL
        else f"{RELINQ_URL.rstrip('/')}/{cpf}"
    )

    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            response = await client.get(url_final, headers=headers)
            dados_api = response.json()
        except Exception as e:
            return {"erro": f"Falha de conexão com a Relinq: {str(e)}"}

    if "data" not in dados_api or not dados_api["data"]:
        return {"erro": f"A Relinq não retornou os dados esperados. Resposta bruta: {dados_api}"}

    data = dados_api.get("data") or {}
    crednet = data.get("crednet") or {}
    consulta_credito = crednet.get("consultaCredito") or {}
    resumo = consulta_credito.get("resumoConsulta") or {}

    # ---------------------------------------------------------
    # 1. EXTRAÇÃO E PONTUAÇÃO FINANCEIRA
    # ---------------------------------------------------------
    score_obj = consulta_credito.get("score") or {}
    score = score_obj.get("score", 0)

    pendencias_obj = resumo.get("pendenciasFinanceiras") or {}
    total_pendencias = pendencias_obj.get("valorTotal", 0.0)

    qtd_dividas_locadora = 0
    palavras_locadora = [
        "LOCAC", "LOCADOR", "ALUG", "RENT A CAR", "RENTACAR", "FROTAS", "MOBILIDADE",
        "MOTTU", "LOCALIZA", "MOVIDA", "UNIDAS", "VAMOS", "TURBI", "KAVAK",
        "VOU DE MOTO", "VOUDEMOTO", "RAPI", "LEVE MOTOS", "MOTO FACIL", "MOTOFACIL",
        "LOCA SIM", "LOCASIM", "LOCA9", "MOTOCA", "HERTZ", "AVIS", "BUDGET"
    ]
    divida_nu = 0.0

    lista_pendencias = consulta_credito.get("pendenciasFinanceiras") or []
    for p in lista_pendencias:
        informante = normalizar_texto(p.get("informante", ""))
        tipo = normalizar_texto(p.get("tipoPendencia", ""))
        valor = float(p.get("valor") or 0.0)

        if any(palavra in informante or palavra in tipo for palavra in palavras_locadora):
            qtd_dividas_locadora += 1

        if any(nu in informante for nu in ["NU ", "NUBANK", "NU PAGAMENTOS", "NU FINANCEIRA"]):
            divida_nu += valor

    teve_atraso_aluguel = qtd_dividas_locadora > 0

    lista_protestos = consulta_credito.get("protestos") or []
    qtd_protestos = len(lista_protestos)
    valor_protestos = sum(float(p.get("valor") or 0.0) for p in lista_protestos)

    pontos = 0.0

    # Score Serasa
    if score > 500:
        pontos += 2
    elif score >= 351:
        pontos += 1

    # Aluguel em Atraso
    if qtd_dividas_locadora == 0:
        pontos += 3

    # Pendências Financeiras Totais
    if total_pendencias <= 3144.96:
        pontos += 2
    elif total_pendencias <= 3500.00:
        pontos += 1

    # Nu Financeira
    if divida_nu <= 107.40:
        pontos += 1
    elif divida_nu <= 500.00:
        pontos += 0.5

    # Protestos (Quantidade e Antiguidade)
    if qtd_protestos == 0:
        pontos += 1
    elif qtd_protestos == 1:
        dias_protesto = calcular_idade_divida(lista_protestos[0].get("data", ""))
        if dias_protesto > 365:
            pontos += 0.5

    # Protestos (% sobre o Total de Pendências)
    if total_pendencias > 0:
        perc_protesto = (valor_protestos / total_pendencias) * 100
    else:
        perc_protesto = 100.0 if valor_protestos > 0 else 0.0

    if perc_protesto < 25.0:
        pontos += 1
    elif perc_protesto <= 35.0:
        pontos += 0.5

    reprovacao_locadora = qtd_dividas_locadora > 1
    status_financeiro = "REPROVADO" if (reprovacao_locadora or pontos < 5.0) else "APROVADO"

    # ---------------------------------------------------------
    # 2. ANÁLISE JUDICIAL E ANTECEDENTES CRIMINAIS
    # ---------------------------------------------------------
    todos_processos = extrair_processos_unicos(data)

    processos_civeis_ativos = []
    analises_criminais = []
    tem_polo_passivo_civel = False

    for proc in todos_processos:
        area = normalizar_texto(proc.get("area", ""))
        ramo = normalizar_texto((proc.get("statusPj") or {}).get("ramoDireito", ""))
        status_p = normalizar_texto((proc.get("statusPj") or {}).get("statusProcesso", ""))

        eh_criminal = "CRIMINAL" in area or "PENAL" in ramo

        if eh_criminal:
            resultado_crim = classificar_processo_criminal(proc, cpf)
            if not resultado_crim.get("ignorar"):
                analises_criminais.append(resultado_crim)
        else:
            esta_arquivado = "ARQUIVAD" in status_p or "EXTINT" in status_p or "BAIXAD" in status_p
            if not esta_arquivado:
                processos_civeis_ativos.append(proc)
                infos_p = proc.get("informacoesPartePessoaConsultada") or []
                if any(normalizar_texto(i.get("polo", "")) == "PASSIVO" for i in infos_p):
                    tem_polo_passivo_civel = True

    reprovados_criminais = [c for c in analises_criminais if c["decisao"] == "REPROVADO"]
    apontamentos_criminais = [c for c in analises_criminais if c["decisao"] == "APONTAMENTO"]

    status_judicial = "REPROVADO" if len(reprovados_criminais) > 0 else "APROVADO"

    # Formatação para os campos exibidos na interface
    qtd_total_processos = len(processos_civeis_ativos) + len(analises_criminais)

    if reprovados_criminais:
        tem_criminal_texto = f"SIM ({len(reprovados_criminais)} Reprovado)"
    elif apontamentos_criminais:
        tem_criminal_texto = f"SIM ({len(apontamentos_criminais)} Apontamento)"
    else:
        tem_criminal_texto = "Não"

    if analises_criminais:
        mais_grave = reprovados_criminais[0] if reprovados_criminais else apontamentos_criminais[0]
        tempo_criminal_texto = f"{mais_grave['anos_decorridos']} anos ({mais_grave['status_processo']})"
        tipos_listados = [c["resumo_classificacao"] for c in analises_criminais]
        if processos_civeis_ativos:
            tipos_listados.append(f"Cível ({len(processos_civeis_ativos)} ativos)")
        tipos_processos_texto = " | ".join(tipos_listados)
    elif processos_civeis_ativos:
        tempo_criminal_texto = "N/A"
        tipos_processos_texto = f"Ações Cíveis ({len(processos_civeis_ativos)} ativos)"
    else:
        tempo_criminal_texto = "N/A"
        tipos_processos_texto = "Nenhum"

    polaridade = (
        "Passivo (Réu)"
        if (len(analises_criminais) > 0 or tem_polo_passivo_civel)
        else ("Ativo" if processos_civeis_ativos else "Nenhum")
    )

    # ---------------------------------------------------------
    # 3. PARECER FINAL
    # ---------------------------------------------------------
    status_geral = (
        "REPROVADO"
        if (status_financeiro == "REPROVADO" or status_judicial == "REPROVADO")
        else "APROVADO"
    )

    return {
        "status_geral": status_geral,
        "serasa": {
            "status": status_financeiro,
            "pontuacao_total": pontos,
            "detalhes": {
                "score": score,
                "teve_atraso_aluguel": teve_atraso_aluguel,
                "divida_nu_financeira": round(divida_nu, 2),
                "qtd_protestos": qtd_protestos,
                "total_pendencias": round(total_pendencias, 2),
            },
        },
        "detetive": {
            "status": status_judicial,
            "detalhes": {
                "qtd_processos": qtd_total_processos,
                "tipos_processos": tipos_processos_texto,
                "polaridade_processual": polaridade,
                "alienacao_fiduciaria": "Não consultado nesta base",
                "debitos_aluguel": f"Sim ({qtd_dividas_locadora} registros)" if teve_atraso_aluguel else "Não",
                "tem_processo_criminal": tem_criminal_texto,
                "tempo_processo_criminal": tempo_criminal_texto,
                "apontamentos_judiciais": [c["motivo_regra"] for c in apontamentos_criminais],
                "classificacao_criminal": analises_criminais,
            },
        },
    }