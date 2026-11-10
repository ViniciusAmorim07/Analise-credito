import re
import pdfplumber

def limpar_valor(valor_str):
    """Transforma '1.500,00' em float 1500.00 (igual ao ARRUMAR e substituições no Excel)"""
    return float(valor_str.replace(".", "").replace(",", "."))

def extrair_dados_pdf(arquivo_pdf):
    texto_completo = ""
    with pdfplumber.open(arquivo_pdf) as pdf:
        for pagina in pdf.pages:
            texto = pagina.extract_text()
            if texto:
                texto_completo += texto + "\n"

    # 1. Extrair Score
    match_score = re.search(r"Serasa score:\s*(\d+)", texto_completo)
    score = int(match_score.group(1)) if match_score else 0

    # 2. Identificar dívidas de Aluguel (ex: MOTTU, LOCALIZA, palavra ALUGUEL)
    teve_atraso_aluguel = bool(re.search(r"(ALUGUEL|MOTTU|LOCALIZA)", texto_completo, re.IGNORECASE))

    # 3. Extrair Dívida Nu Financeira
    # Procura 'NU FINANCEIRA' e captura o primeiro valor em R$ que aparecer perto
    match_nu = re.search(r"NU FINANCEIRA.*?R\$\s*([\d\.,]+)", texto_completo, re.IGNORECASE | re.DOTALL)
    divida_nu = limpar_valor(match_nu.group(1)) if match_nu else 0.0

    # 4. Soma de Pendências (Internas + Financeiras)
    # Busca os totais nos cabeçalhos das tabelas
    soma_pendencias = 0.0
    match_internas = re.search(r"Pendências Internas\s*\|\s*\d+\s*\|\s*R\$\s*([\d\.,]+)", texto_completo)
    if match_internas:
        soma_pendencias += limpar_valor(match_internas.group(1))
        
    match_financeiras = re.search(r"Pendencias Financeiras\s*\|\s*\d+\s*\|\s*R\$\s*([\d\.,]+)", texto_completo)
    if match_financeiras:
        soma_pendencias += limpar_valor(match_financeiras.group(1))

    # 5. Protestos
    match_protesto = re.search(r"Protesto Estadual\s*\|\s*(\d+)\s*\|\s*R\$\s*([\d\.,]+)", texto_completo)
    qtd_protestos = int(match_protesto.group(1)) if match_protesto else 0
    valor_protestos = limpar_valor(match_protesto.group(2)) if match_protesto else 0.0

    # Calcular porcentagem do protesto em relação à dívida total
    divida_total = soma_pendencias + valor_protestos
    perc_protesto = (valor_protestos / divida_total * 100) if divida_total > 0 else 0.0

    return calcular_pontos(score, teve_atraso_aluguel, soma_pendencias, qtd_protestos, perc_protesto, divida_nu)

def calcular_pontos(score, aluguel, pendencias, qtd_protestos, perc_protesto, divida_nu):
    pontos = 0.0

    # Score
    if score > 500: pontos += 2
    elif score >= 351: pontos += 1

    # Aluguel
    if not aluguel: pontos += 3

    # Pendências Somadas
    if pendencias <= 2144.96: pontos += 2
    elif pendencias <= 3144.96: pontos += 1.5
    elif pendencias <= 3500.00: pontos += 0.5

    # Quantidade de Protestos
    if qtd_protestos == 0: pontos += 1
    elif qtd_protestos == 1: pontos += 0.5 # Assumindo 1 como "antigo/isolado" para o MVP

    # Percentual de Protestos
    if perc_protesto == 0: pontos += 0.5
    elif perc_protesto < 25: pontos += 0.5
    elif perc_protesto <= 35: pontos += 0.25

    # Nu Financeira
    if divida_nu == 0: pontos += 1
    elif divida_nu < 107.40: pontos += 0.5
    elif divida_nu <= 500.00: pontos += 0.25

    aprovado = pontos >= 5.0 # Defina aqui a nota de corte da empresa

    return {
        "pontuacao_total": pontos,
        "status": "APROVADO" if aprovado else "REPROVADO",
        "detalhes": {
            "score": score,
            "teve_atraso_aluguel": aluguel,
            "total_pendencias": pendencias,
            "qtd_protestos": qtd_protestos,
            "divida_nu_financeira": divida_nu
        }
    }