import os
import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
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

RELINQ_URL = os.getenv("RELINQ_URL")
RELINQ_TOKEN = os.getenv("RELINQ_TOKEN")
class ConsultaCPF(BaseModel):
    cpf: str

def calcular_idade_divida(data_str):
    try:
        data_obj = datetime.strptime(data_str, "%d/%m/%Y")
        return (datetime.now() - data_obj).days
    except:
        return 0

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
    # EXTRAÇÃO DE DADOS FINANCEIROS
    # ---------------------------------------------------------
    score_obj = consulta_credito.get("score") or {}
    score = score_obj.get("score", 0)
    
    pendencias_obj = resumo.get("pendenciasFinanceiras") or {}
    total_pendencias = pendencias_obj.get("valorTotal", 0.0)
    
    # NOVAS VARIÁVEIS PARA O FILTRO DE LOCADORAS
    qtd_dividas_locadora = 0
    palavras_locadora = ["MOTTU", "LOCALIZA", "MOVIDA", "ALUGUEL", "LOCADORA", "LOCACAO"]
    
    divida_nu = 0.0
    
    lista_pendencias = consulta_credito.get("pendenciasFinanceiras") or []
    for p in lista_pendencias:
        informante = str(p.get("informante", "")).upper()
        tipo = str(p.get("tipoPendencia", "")).upper()
        valor = float(p.get("valor") or 0.0)
        
        # Conta quantas dívidas de locadora o cliente possui
        is_locadora = any(palavra in informante or palavra in tipo for palavra in palavras_locadora)
        if is_locadora:
            qtd_dividas_locadora += 1
            
        if "NU " in informante or "NUBANK" in informante:
            divida_nu += valor

    # Para exibir no FrontEnd se teve algum atraso (booleano)
    teve_atraso_aluguel = qtd_dividas_locadora > 0

    lista_protestos = consulta_credito.get("protestos") or []
    qtd_protestos = len(lista_protestos)
    valor_protestos = sum(float(p.get("valor") or 0.0) for p in lista_protestos)

    # ---------------------------------------------------------
    # MOTOR DE PONTUAÇÃO E REGRAS
    # ---------------------------------------------------------
    pontos = 0.0

    # 1. Score Serasa
    if score > 500: pontos += 2
    elif score >= 351: pontos += 1

    # 2. Aluguel em Atraso
    if qtd_dividas_locadora == 0:
        pontos += 3  # Ganha 3 pontos se não tiver NENHUM registro
    # Se tiver 1, apenas deixa de ganhar os 3 pontos. 
    # Se tiver > 1, aplicaremos o Hard Stop mais abaixo.

    # 3. Pendências Financeiras (Valor Total)
    if total_pendencias <= 3144.96:
        pontos += 2
    elif total_pendencias <= 3500.00:
        pontos += 1

    # 4. Nu Financeira
    if divida_nu <= 107.40:
        pontos += 1
    elif divida_nu <= 500.00:
        pontos += 0.5

    # 5. Protestos (Quantidade e Tempo)
    if qtd_protestos == 0:
        pontos += 1
    elif qtd_protestos == 1:
        dias_protesto = calcular_idade_divida(lista_protestos[0].get("data", ""))
        if dias_protesto > 365:
            pontos += 0.5

    # 6. Protestos (Porcentagem baseada no Total de Pendências)
    if total_pendencias > 0:
        perc_protesto = (valor_protestos / total_pendencias) * 100
    else:
        # Se não tem pendências financeiras normais, mas tem protesto, o protesto representa 100% da dívida
        perc_protesto = 100.0 if valor_protestos > 0 else 0.0

    if perc_protesto < 25.0:
        pontos += 1
    elif perc_protesto <= 35.0:
        pontos += 0.5

    # ---------------------------------------------------------
    # GATILHOS DE REPROVAÇÃO (HARD STOPS)
    # ---------------------------------------------------------
    reprovacao_locadora = qtd_dividas_locadora > 1 # HARD STOP de devedor contumaz de locadora
    
    # O cliente precisa de pelo menos 5 pontos para ser aprovado financeiramente
    if reprovacao_locadora or pontos < 5.0:
        status_financeiro = "REPROVADO"
    else:
        status_financeiro = "APROVADO"

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
                "debitos_aluguel": f"Sim ({qtd_dividas_locadora} registros)" if teve_atraso_aluguel else "Não",
                "tem_processo_criminal": tem_criminal,
                "tempo_processo_criminal": "N/A"
            }
        }
    }