"""Resumo da ata: envia o .md da reunião pro LLM (quebragalho) e grava
<mesmo_basename>_resumo.md com resumo executivo, decisões e encaminhamentos."""
import os, sys

BASE = "https://api.quebragalho.dev/v1"
MODELO = os.environ.get("QG_MODELO", "qwen3.8-omni-flash")
ALT = "muse-spark-1.3-contributor"  # fallback se o padrão estiver fora (502 upstream já visto)

SYSTEM = """Você transforma transcrições de reunião em resumos executivos. A transcrição usa os
rótulos 'Você' (participante local, dono da gravação) e 'Remotos' (demais participantes, não
identificados individualmente). Responda SOMENTE com Markdown, exatamente com estas seções:

## Resumo executivo
De três a cinco frases com o que foi tratado.

## Decisões
- Um item por decisão efetivamente tomada; se nenhuma, escreva "Nenhuma decisão registrada."

## Encaminhamentos
- Um item por ação pendente, no formato `ação — responsável (se citado) — prazo (se citado)`;
  se nenhum, escreva "Nenhum encaminhamento."

## Pontos em aberto
- Dúvidas, riscos ou temas que ficaram sem conclusão; se nenhum, escreva "Nada em aberto."

Regras: não invente fatos, nomes, números ou prazos que não estejam na transcrição; mantenha
termos técnicos e nomes próprios exatamente como escritos; se a transcrição estiver em outro
idioma, o resumo ainda deve sair em português do Brasil."""

def _chave():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    k = os.environ.get("QUEBRAGALHO_API_KEY")
    return k.strip() if k else None

def _chamada(chave, ata, modelo):
    import requests, time
    esperas = (2, 10, 30)  # 502 upstream já foi visto; a ata já está salva, resumo é best-effort
    erro = "?"
    for tent in range(len(esperas) + 1):
        try:
            r = requests.post(f"{BASE}/chat/completions",
                              headers={"Authorization": "Bearer " + chave}, timeout=180, json={
                                  "model": modelo,
                                  "messages": [{"role": "system", "content": SYSTEM},
                                               {"role": "user", "content": "ATA:\n\n" + ata}],
                                  "temperature": 0.2})
            if r.status_code == 200:
                return r.json()["choices"][0]["message"]["content"].strip()
            erro = f"HTTP {r.status_code}: {r.text[:120]}"
            if r.status_code != 429 and r.status_code < 500:
                break  # 4xx definitivo (chave/modelo): retantar não resolve
        except requests.RequestException as e:
            erro = str(e)[:80]
        if tent < len(esperas):
            print(f"resumo: {erro}; tentando de novo em {esperas[tent]}s...", flush=True)
            time.sleep(esperas[tent])
    raise RuntimeError(erro)

def resumir(ata, modelo=None):
    """Lê a ata <arquivo>.md e grava <mesmo_basename>_resumo.md via LLM.
    Retorna o caminho do resumo, ou None se não deu (sem chave, ata vazia, erro)."""
    global MODELO
    if modelo:
        MODELO = modelo
    chave = _chave()
    if not chave:
        print("QUEBRAGALHO_API_KEY não configurada (copie .env.example para .env) — resumo pulado",
              flush=True)
        return None
    with open(ata, encoding="utf-8") as f:
        texto = f.read().strip()
    if len(texto) < 40:  # só o título: reunião sem fala detectada
        print("ata vazia — nada a resumir", flush=True)
        return None
    # modelo pinado (parâmetro ou QG_MODELO) é respeitado; no padrão, tenta o alternativo
    cadeia = [MODELO] if (modelo or os.environ.get("QG_MODELO") or ALT == MODELO) \
             else [MODELO, ALT]
    saida = None
    for m in cadeia:
        print(f"resumindo com {m}...", flush=True)
        try:
            saida = _chamada(chave, texto, m)
            break
        except Exception as e:
            print(f"resumo falhou com {m} ({str(e)[:100]})", flush=True)
    if saida is None:  # falha limpa: nada de traceback no CLI
        print(f"resumo não gerado — rode depois: python reuniao.py resumir \"{ata}\"", flush=True)
        return None
    if "</think>" in saida:  # modelo raciocinante pode cuspir o raciocínio antes da resposta
        saida = saida.rsplit("</think>", 1)[1].strip()
    resumo = os.path.splitext(ata)[0] + "_resumo.md"
    fd, tmp = __import__("tempfile").mkstemp(dir=os.path.dirname(ata) or ".",
                                              suffix=".tmp")
    try:  # gravação atômica, como na ata
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"# Resumo — {os.path.splitext(os.path.basename(ata))[0]}\n\n{saida}\n")
        os.replace(tmp, resumo)
    except BaseException:
        os.unlink(tmp)
        raise
    print("resumo salvo em:", resumo, flush=True)
    return resumo

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print("uso: python reuniao_resumo.py <ata.md>")
        return 2
    return 0 if resumir(args[0]) else 1

if __name__ == "__main__":
    sys.exit(main())
