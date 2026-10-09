"""Comandos de voz: wake-word, match local de mídia, JEV (decisão) e execução.
Nada de rede, microfone ou janela no import; `requests` só dentro de `decidir`."""
import os
import unicodedata
import webbrowser

WAKE_PADRAO = "computador"
LIMIAR_PADRAO = 0.6

MIDIA = {
    "pausar_musica": ("play/pause media", "Música pausada"),
    "continuar_musica": ("play/pause media", "Música retomada"),
    "proxima_faixa": ("next track", "Próxima faixa"),
    "faixa_anterior": ("previous track", "Faixa anterior"),
    "volume_mais": ("volume up", "Volume +"),
    "volume_menos": ("volume down", "Volume −"),
    "volume_mudo": ("volume mute", "Mudo alternado"),
}
# ponytail: play/pause é alternância — "continuar" com música tocando pausa;
# o usuário repete o comando; estado absoluto do player fica pro v2.

ABRIR = {
    "abrir_youtube": "https://www.youtube.com",
    "abrir_gmail": "https://mail.google.com",
    "abrir_whatsapp": "https://web.whatsapp.com",
    "abrir_github": "https://github.com",
}

COMANDOS = set(MIDIA) | set(ABRIR) | {"ditado"}


def normaliza(texto):
    """Minúsculas, sem acento, espaços colapsados, sem pontuação de borda."""
    t = unicodedata.normalize("NFKD", texto or "").lower()
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = " ".join(t.strip(" \t\n.,;:!?").split())
    return t


def tira_wake(texto, wake=WAKE_PADRAO):
    """Devolve o resto após 'wake,' / 'wake ' (case-insensitive) ou None.
    Exige fronteira de palavra: 'computadores' não dispara."""
    t = (texto or "").strip()
    if t.casefold().startswith(wake.casefold()):
        resto = t[len(wake):]
        if resto and resto[0] not in " \t,:":
            return None
        resto = resto.lstrip(" \t,:")
        return resto or None
    return None


def match_local(frase):
    """Id do comando de mídia por palavras-chave, ou None (vai pro JEV)."""
    n = " " + normaliza(frase) + " "
    if any(k in n for k in ("pausar", "pausa", "pause", "para a musica", "parar a musica")):
        return "pausar_musica"
    if any(k in n for k in ("continuar", "retomar", "seguir tocando", "play ")):
        return "continuar_musica"
    if "anterior" in n:
        return "faixa_anterior"
    if any(k in n for k in ("proxima", "seguinte", "pular musica")):
        return "proxima_faixa"
    if any(k in n for k in ("aumentar", "subir", "mais alto")) and "volume" in n:
        return "volume_mais"
    if any(k in n for k in ("diminuir", "baixar", "abaixar", "mais baixo")) and "volume" in n:
        return "volume_menos"
    if any(k in n for k in ("mudo", "mutar", "sem som", "silenciar")):
        return "volume_mudo"
    return None


def _teclado(tecla):
    import keyboard
    keyboard.send(tecla)


def _abrir_url(url):
    webbrowser.open(url)


def executar(comando):
    """Executa um id de COMANDOS; devolve mensagem p/ a bandeja."""
    if comando in MIDIA:
        tecla, msg = MIDIA[comando]
        _teclado(tecla)
        return msg
    if comando in ABRIR:
        _abrir_url(ABRIR[comando])
        return f"{comando[6:].capitalize()} aberto"
    raise ValueError(f"comando desconhecido: {comando}")


JEV_URL = "https://openrouter.ai/api/alpha/decisions"


def _env_modelo():
    m = os.environ.get("JEV_MODEL", "typesafe/jev-1.13").strip()
    return m or "typesafe/jev-1.13"


def limiar():
    """Confiança mínima p/ executar (env COMANDO_CONFIANCA, padrão 0.6)."""
    try:
        v = float(os.environ.get("COMANDO_CONFIANCA", str(LIMIAR_PADRAO)))
    except (TypeError, ValueError):
        return LIMIAR_PADRAO
    return min(1.0, max(0.0, v))


def decidir(frase, chave, timeout=8):
    """Pergunta ao JEV qual comando a frase é (+ditado). Falha → None."""
    try:
        import requests
    except ImportError:
        return None
    pergunta = {"intencao": {
        "type": "choice",
        "instructions": "A frase e um comando de voz ou ditado normal?",
        "criteria": {
            "pausar_musica": "Pausar, parar ou continuar musica/audio.",
            "continuar_musica": "Retomar a reproducao de musica/audio.",
            "proxima_faixa": "Pular para a proxima faixa/musica.",
            "faixa_anterior": "Voltar para a faixa/musica anterior.",
            "volume_mais": "Aumentar o volume.",
            "volume_menos": "Diminuir o volume.",
            "volume_mudo": "Mutar/desmutar o som.",
            "abrir_youtube": "Abrir o site do YouTube.",
            "abrir_gmail": "Abrir o Gmail.",
            "abrir_whatsapp": "Abrir o WhatsApp Web.",
            "abrir_github": "Abrir o GitHub.",
            "ditado": "Texto comum para digitar, nao e comando.",
        }}}
    try:
        r = requests.post(JEV_URL,
                          headers={"Authorization": "Bearer " + chave,
                                   "Content-Type": "application/json"},
                          json={"model": _env_modelo(),
                                "state": {"frase": frase},
                                "questions": pergunta},
                          timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        js = r.json()
        a = js["answers"]["intencao"]
        if a.get("type") != "choice":
            return None
        cmd, conf = a.get("choice"), a.get("confidence", 0.0)
        if cmd not in COMANDOS or not isinstance(conf, (int, float)):
            return None
        custo = (js.get("usage") or {}).get("cost")
        if custo is not None and not isinstance(custo, (int, float)):
            custo = None
        return (cmd, float(conf), custo)
    except (ValueError, KeyError, TypeError, AttributeError):
        return None


def wake():
    """Palavra de ativação (env COMANDO_WAKE, padrão 'computador')."""
    w = os.environ.get("COMANDO_WAKE", WAKE_PADRAO).strip()
    return w or WAKE_PADRAO


def tratar(texto):
    """Cascata wake → local → JEV. (True, status) executou;
    (False, texto) segue p/ ditado (wake removido se havia)."""
    resto = tira_wake(texto, wake())
    if resto is None:
        return (False, texto)
    cmd, custo = match_local(resto), None
    if cmd is None:
        chave = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if chave:
            r = decidir(resto, chave)
            if r and r[0] != "ditado" and r[1] >= limiar():
                cmd, custo = r[0], r[2]
    if cmd is None or cmd == "ditado":
        return (False, resto)
    try:
        msg = executar(cmd)
    except Exception:  # ponytail: fail-open total; nada pode matar o bombeia
        return (False, resto)
    if custo is not None:
        msg += f" (custo ${custo:.5f})"
    return (True, msg)
