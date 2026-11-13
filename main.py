import httpx
from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

RELINQ_URL = "https://painel.relinqscore.com.br/api/v2/cpf-relinq-plus/"
RELINQ_TOKEN = "rsk_a2458eac526b3efc2040758093de9b376b8651904ddf8318197e5e41c9f592f9"

class ConsultaCPF(BaseModel):
    cpf: str

@app.post("/analisar-cpf/")
async def analisar_cpf_endpoint(request: ConsultaCPF):
    cpf = request.cpf.strip()
    
    headers = {
        "Authorization": f"Bearer {RELINQ_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            response = await client.get(f"{RELINQ_URL}{cpf}", headers=headers)
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
    # BLOCO FINANCEIRO
    # ---------------------------------------------------------
    score_obj = consulta_credito.get("score") or {}
    score = score_obj.get("score", 0)
    
    protestos_obj = resumo.get("protestos") or {}
    qtd_protestos = protestos_obj.get("quantidadeTotal", 0)
    
    pendencias_obj = resumo.get("pendenciasFinanceiras") or {}
    total_pendencias = pendencias_obj.get("valorTotal", 0)
    
    teve_atraso_aluguel = False
    divida_nu = 0.0
    
    lista_pendencias = consulta_credito.get("pendenciasFinanceiras") or []
    for p in lista_pendencias:
        informante = str(p.get("informante", "")).upper()
        tipo = str(p.get("tipoPendencia", "")).upper()
        valor = float(p.get("valor") or 0.0)
        
        if "MOTTU" in informante or "ALUGUEL" in tipo or "LOCACAO" in tipo:
            teve_atraso_aluguel = True
            
        if "NU " in informante or "NUBANK" in informante:
            divida_nu += valor

    pontos = 0
    if score > 500: pontos += 2
    elif score >= 351: pontos += 1

    status_financeiro = "REPROVADO" if teve_atraso_aluguel else "APROVADO"

    # ---------------------------------------------------------
    # BLOCO JUDICIAL E ANTECEDENTES
    # ---------------------------------------------------------
    civeis_obj = resumo.get("acoesCiveis") or {}
    qtd_civeis = civeis_obj.get("quantidadeTotal", 0)
    
    criminais_obj = resumo.get("processosCriminais") or {}
    qtd_criminais = criminais_obj.get("quantidadeTotal", 0)
    
    tem_criminal = "SIM (ATENÇÃO)" if qtd_criminais > 0 else "Não"
    polaridade = "Passivo (Réu)" if qtd_civeis > 0 else "Nenhum"
    status_judicial = "REPROVADO" if qtd_criminais > 0 else "APROVADO"

    # ---------------------------------------------------------
    # PARECER FINAL
    # ---------------------------------------------------------
    status_geral = "APROVADO"
    if status_financeiro == "REPROVADO" or status_judicial == "REPROVADO":
        status_geral = "REPROVADO"

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
                "total_pendencias": round(total_pendencias, 2)
            }
        },
        "detetive": {
            "status": status_judicial,
            "detalhes": {
                "qtd_processos": qtd_civeis + qtd_criminais,
                "tipos_processos": "Ações Cíveis" if qtd_civeis > 0 else "Nenhum",
                "polaridade_processual": polaridade,
                "alienacao_fiduciaria": "Não consultado nesta base",
                "debitos_aluguel": "Sim" if teve_atraso_aluguel else "Não",
                "tem_processo_criminal": tem_criminal,
                "tempo_processo_criminal": "N/A"
            }
        }
    }