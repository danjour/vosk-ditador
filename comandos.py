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
    """Devolve o resto após 'wake,' / 'wake ' (case-insensitive) ou None."""
    t = (texto or "").strip()
    if t.casefold().startswith(wake.casefold()):
        resto = t[len(wake):].lstrip(" \t,:")
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
