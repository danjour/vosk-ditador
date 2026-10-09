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

APPS = {
    "bloco de notas": "notepad",
    "calculadora": "calc",
    "paint": "mspaint",
    "explorador": "explorer",
}

_NOMES_SITES = {"youtube": "abrir_youtube", "gmail": "abrir_gmail",
                "whatsapp": "abrir_whatsapp", "github": "abrir_github"}

COMANDOS = (set(MIDIA) | set(ABRIR) | {"abrir_programa", "tocar_musica",
                                       "dizer_horas", "pesquisar_web", "ditado"})


def _tira_artigo(nome):
    n = normaliza(nome)
    for art in ("o ", "a ", "os ", "as ", "um ", "uma "):
        if n.startswith(art):
            return n[len(art):]
    return n


def _resolve_abrir(nome):
    nome = _tira_artigo(nome)
    for pedaco, cid in _NOMES_SITES.items():
        if pedaco in nome:
            return cid
    if nome in APPS:
        return "abrir_programa"
    return None


def _abrir_app(executavel):
    import os as _os
    _os.startfile(executavel)


MPV_PIPE = r"\\.\pipe\mpv-ditador"
_MPV = None  # Popen do demônio mpv (seam de módulo p/ testes)


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
    n2 = normaliza(frase)
    if n2.startswith(("toca ", "tocar ", "ouve ", "ouvir ", "ponha ",
                      "poe ", "bota pra tocar ", "quero ouvir ",
                      "quero escutar ")):
        return "tocar_musica"
    if n2.startswith(("que horas", "que hora e", "me diz as horas", "diga as horas")):
        return "dizer_horas"
    if n2.startswith(("pesquisa ", "pesquisar ", "busca ", "buscar ", "procura por ")):
        return "pesquisar_web"
    if n2.startswith(("abre ", "abrir ", "abra ")):
        return _resolve_abrir(n2.split(" ", 1)[1])  # None cai adiante
    return None


def _teclado(tecla):
    import keyboard
    keyboard.send(tecla)


def _abrir_url(url):
    webbrowser.open(url)


def executar(comando, argumento=""):
    """Executa um id de COMANDOS; devolve mensagem p/ a bandeja."""
    if comando in MIDIA:
        if _mpv_vivo():
            try:
                _mpv_send({"command": ["cycle", "pause"]}
                          if comando in ("pausar_musica", "continuar_musica")
                          else {"command": ["playlist-next"]}
                          if comando == "proxima_faixa"
                          else {"command": ["playlist-prev"]})
                return MIDIA[comando][1]
            except Exception:
                pass
        tecla, msg = MIDIA[comando]
        _teclado(tecla)
        return msg
    if comando == "tocar_musica":
        return tocar(argumento)
    if comando in ABRIR:
        _abrir_url(ABRIR[comando])
        return f"{comando[6:].capitalize()} aberto"
    if comando == "abrir_programa":
        nome = _tira_artigo(argumento)
        if nome not in APPS:
            raise ValueError(f"programa desconhecido: {argumento}")
        _abrir_app(APPS[nome])
        return f"{nome.capitalize()} aberto"
    if comando == "dizer_horas":
        import datetime
        agora = datetime.datetime.now()
        return f"São {agora.hour}h{agora.minute:02d}"
    if comando == "pesquisar_web":
        import urllib.parse
        q = (argumento or "").strip(" \t\n.,;:!?")
        if not q:
            raise ValueError("pesquisa vazia")
        _abrir_url("https://www.google.com/search?q=" + urllib.parse.quote_plus(q))
        return f"Pesquisando {q}"
    raise ValueError(f"comando desconhecido: {comando}")


def _yt_run(args, timeout=15):
    """Roda yt-dlp e devolve stdout; qualquer falha levanta."""
    import subprocess
    return subprocess.run(args, capture_output=True, text=True,
                          timeout=timeout, check=True).stdout


def _yt_id(consulta):
    """Primeiro ID de `ytsearch1` (keyless) ou None."""
    try:
        for linha in _yt_run(["yt-dlp", f"ytsearch1:{consulta}",
                              "--flat-playlist", "--print", "id",
                              "--no-warnings"]).splitlines():
            if linha.strip():
                return linha.strip()
    except Exception:
        pass
    return None


def _mpv_vivo():
    return _MPV is not None and _MPV.poll() is None


def _mpv_spawn():
    """Sobe o demônio mpv (áudio, idle, IPC). Devolve Popen."""
    import subprocess
    return subprocess.Popen(
        ["mpv", "--no-video", "--idle=yes", f"--input-ipc-server={MPV_PIPE}"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)


def _mpv_ensure():
    """Garante o demônio de pé; True se pronto."""
    global _MPV
    try:
        if not _mpv_vivo():
            _MPV = _mpv_spawn()
        return _mpv_vivo()
    except Exception:
        return False


def _mpv_send(obj):
    """Um comando JSON ao IPC (seam mockável; exceção escapa p/ o chamador)."""
    import json
    with open(MPV_PIPE, "r+b", buffering=0) as pipe:
        pipe.write((json.dumps(obj) + "\n").encode())
        pipe.readline()


def _limpa_consulta(resto):
    """Tira verbo líder ('toca X'→'X'); sem verbo, o resto inteiro."""
    n = normaliza(resto)
    for verbo in ("toca ", "tocar ", "ouve ", "ouvir ", "ponha ", "poe ",
                  "bota pra tocar ", "quero ouvir ", "quero escutar "):
        if n.startswith(verbo):
            return n[len(verbo):].strip() or n
    return resto.strip()


def tocar(consulta):
    """Toca o 1º resultado no mpv; sem ID, abre a busca (fail-open)."""
    import urllib.parse
    consulta = _limpa_consulta(consulta)
    vid = _yt_id(consulta)
    if vid and _mpv_ensure():
        try:
            _mpv_send({"command": ["loadfile",
                                   f"https://www.youtube.com/watch?v={vid}",
                                   "append-play"]})
            return f"Tocando {consulta}"
        except Exception:
            pass
    _abrir_url("https://www.youtube.com/results?search_query=" +
               urllib.parse.quote_plus(consulta))
    return f"Busca aberta: {consulta}"


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
            "abrir_programa": "Abrir um programa do computador.",
            "tocar_musica": "Tocar uma música/áudio do YouTube.",
            "dizer_horas": "Dizer que horas são.",
            "pesquisar_web": "Pesquisar algo na web.",
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
        msg = executar(cmd, resto)
    except Exception:  # ponytail: fail-open total; nada pode matar o bombeia
        return (False, resto)
    if custo is not None:
        msg += f" (custo ${custo:.5f})"
    return (True, msg)


def _voz_ativa():
    return os.environ.get("COMANDO_VOZ", "1").strip() not in ("0", "não", "nao", "off", "false")


def _sintetizar(texto):
    """mp3 temporário com Edge-TTS pt-BR; exceção escapa p/ _falar_sync."""
    import asyncio
    import edge_tts
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".mp3", prefix="ditador_")
    os.close(fd)
    asyncio.run(edge_tts.Communicate(texto, voice="pt-BR-AntonioNeural").save(path))
    return path


def _tocar_arquivo(path):
    import playsound
    playsound.playsound(path, block=True)


def _falar_sync(texto):
    try:
        path = _sintetizar(texto)
        try:
            _tocar_arquivo(path)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
    except Exception:
        pass  # voz nunca quebra o fluxo


def falar(texto):
    """Fala o status em thread daemon (nunca bloqueia a bandeja)."""
    if not texto or not _voz_ativa():
        return
    import threading
    threading.Thread(target=_falar_sync, args=(texto,), daemon=True).start()
