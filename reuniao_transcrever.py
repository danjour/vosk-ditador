"""Ata de reunião offline: WAV/FLAC estéreo (L=mic='Você', R=sistema='Remotos') vira
<basename>.md com falantes e timestamps; motor faster-whisper local (padrão) ou --groq.
--diarizar separa os remotos em Remoto 1, 2, ... por voz (pyannote, 100% local)."""
import os, sys

SR = 16000
CHUNK_S = 600  # Groq: 600s de 16k mono em FLAC fica bem abaixo do limite de 25 MB/requisição
GAP = 1.0  # lacuna (s) máxima p/ fundir fala consecutiva do mesmo falante
VOCE, REMOTOS = "Você", "Remotos"

_WHISPER = None  # (modelo, pipeline em batch) carregados uma única vez

def _prep_cuda():  # DLLs do cudnn/cublas instaladas via pip não estão no PATH por padrão
    import glob, importlib.util
    spec = importlib.util.find_spec("nvidia")
    if not spec:
        return
    base = spec.submodule_search_locations[0]
    for d in glob.glob(os.path.join(base, "*", "bin")):
        os.add_dll_directory(d)
        os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]

def _modelo_local():
    global _WHISPER
    if _WHISPER is None:
        import numpy as np
        from faster_whisper import BatchedInferencePipeline, WhisperModel
        _prep_cuda()
        for dev, ct in (("cuda", "int8_float16"), ("cpu", "int8")):
            try:
                m = WhisperModel("large-v3-turbo", device=dev, compute_type=ct)
                # aquecimento: consumir o gerador força o encode de verdade (erro de kernel sai aqui)
                list(m.transcribe(np.zeros(8000, dtype=np.float32), language="pt")[0])
                _WHISPER = (m, BatchedInferencePipeline(model=m))
                print("whisper large-v3-turbo em", dev, flush=True)
                break
            except Exception as e:
                print(f"whisper {dev} falhou ({str(e)[:80]}), tentando próximo...", flush=True)
        if _WHISPER is None:
            raise SystemExit("nenhum backend whisper disponível")
    return _WHISPER

def _canais(caminho):  # -> (Você 16k float32, Remotos 16k float32)
    import numpy as np, soundfile as sf, soxr
    dados, sr = sf.read(caminho, dtype="int16")  # int16 (N, 2): L=mic, R=sistema
    if dados.ndim != 2 or dados.shape[1] < 2:
        raise SystemExit(f"esperado áudio estéreo (L=mic, R=sistema): {caminho}")
    return tuple(soxr.resample(dados[:, c].astype(np.float32) / 32768.0, sr, SR)
                 for c in (0, 1))

def _local(sinal, falante, idioma="pt"):
    print(f"transcrevendo canal {falante} (local)...", flush=True)
    _, pipe = _modelo_local()
    segs, _ = pipe.transcribe(sinal, language=idioma, beam_size=3, vad_filter=True,
                              batch_size=8)  # sem without_timestamps: a ata precisa de start/end
    evs = [(s.start, s.end, falante, s.text.strip()) for s in segs if s.text.strip()]
    print(f"canal {falante}: {len(evs)} segmentos", flush=True)
    return evs

def _groq_post(chave, flac, nome):
    import requests, time
    esperas = (2, 8, 30)  # retentativas em erro de rede ou HTTP 429/5xx
    erro = "?"
    for tent in range(len(esperas) + 1):
        try:
            with open(flac, "rb") as fh:
                r = requests.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": "Bearer " + chave},
                    data={"model": "whisper-large-v3-turbo", "language": idioma,
                          "response_format": "verbose_json", "temperature": 0},
                    files={"file": (nome, fh, "audio/flac")}, timeout=300)
            if r.status_code == 200:
                return r.json()
            erro = f"HTTP {r.status_code}"
            if r.status_code != 429 and r.status_code < 500:
                break  # 4xx definitivo (chave/modelo): retentar não resolve
        except requests.RequestException as e:
            erro = str(e)[:80]
        if tent == len(esperas):
            break
        print(f"groq falhou ({erro}); tentando de novo em {esperas[tent]}s...", flush=True)
        time.sleep(esperas[tent])
    raise RuntimeError(f"groq falhou: {erro}")

def _groq(sinal, falante, chave, idioma="pt"):
    import numpy as np, soundfile as sf, tempfile
    nome = "voce" if falante == VOCE else "remotos"
    passo = CHUNK_S * SR
    evs, temps = [], []
    try:
        for i, ini in enumerate(range(0, len(sinal), passo)):
            chunk = sinal[ini:ini + passo]
            fd, tmp = tempfile.mkstemp(suffix=".flac", prefix=f"reuniao_{nome}_{i}_")
            os.close(fd)
            temps.append(tmp)
            sf.write(tmp, np.clip(chunk * 32768.0, -32768, 32767).astype(np.int16),
                     SR, subtype="PCM_16")
            print(f"transcrevendo canal {falante} (groq, chunk {i + 1})...", flush=True)
            js = _groq_post(chave, tmp, f"{nome}_{i}.flac")
            for s in js.get("segments", []):  # start/end relativos ao chunk
                t = s["text"].strip()
                if t:
                    evs.append((s["start"] + i * CHUNK_S, s["end"] + i * CHUNK_S, falante, t))
    finally:  # temporários apagados mesmo se a rede quebrou
        for t in temps:
            try:
                os.remove(t)
            except OSError:
                pass
    print(f"canal {falante}: {len(evs)} segmentos", flush=True)
    return evs

def _mescla(eventos):
    saida = []
    for ev in sorted(eventos, key=lambda e: e[0]):
        p = saida[-1] if saida else None
        if p and ev[2] == p[2] and ev[0] - p[1] <= GAP:
            saida[-1] = (p[0], ev[1], p[2], (p[3] + " " + ev[3]).strip())
        else:
            saida.append(ev)
    return saida

def _hhmmss(t):
    t = max(0, int(t))
    return f"{t // 3600:02d}:{t % 3600 // 60:02d}:{t % 60:02d}"

def _escreve_md(md, eventos, titulo):
    import tempfile
    corpo = "\n\n".join(f"[{_hhmmss(i)}] {f}: {t}" for i, _, f, t in eventos)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(md) or ".", suffix=".tmp")
    try:  # gravação atômica: o .md anterior fica intacto se algo falhar no meio
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(f"# Reunião {titulo}\n\n{corpo}\n")
        os.replace(tmp, md)
    except BaseException:
        os.unlink(tmp)
        raise

def _hub_compat():  # pyannote 3.3 chama use_auth_token; hub>=1.0 só aceita token
    import sys
    import huggingface_hub as hh
    if getattr(hh.hf_hub_download, "_r_compat", False):
        return
    orig = hh.hf_hub_download

    def wrap(*a, **k):
        if "use_auth_token" in k:
            k.setdefault("token", k.pop("use_auth_token"))
        return orig(*a, **k)

    wrap._r_compat = True
    hh.hf_hub_download = wrap
    for mod in list(sys.modules.values()):  # rebinda os from-import já feitos pelo pyannote
        if getattr(mod, "hf_hub_download", None) is orig and mod.__name__.startswith("pyannote"):
            mod.hf_hub_download = wrap

def _local_palavras(sinal, falante, idioma="pt"):
    """Palavras (ini, fim, texto) do canal, com timestamp por palavra: base do
    relabel fino do --diarizar (segmento do whisper funde troca de falantes)."""
    print(f"transcrevendo canal {falante} (local, por palavra)...", flush=True)
    m, pipe = _modelo_local()
    try:
        segs, _ = pipe.transcribe(sinal, language=idioma, beam_size=3, vad_filter=True,
                                  batch_size=8, word_timestamps=True)
        palavras = [(w.start, w.end, w.word) for s in segs for w in (s.words or [])]
    except TypeError:  # pipeline em lote sem word_timestamps: modo clássico
        segs, _ = m.transcribe(sinal, language=idioma, beam_size=3, vad_filter=True,
                               word_timestamps=True)
        palavras = [(w.start, w.end, w.word) for s in segs for w in (s.words or [])]
    print(f"canal {falante}: {len(palavras)} palavras", flush=True)
    return palavras

def _mais_proximo(ini, fim, turnos, alcance=2.0):
    # palavra que sobrou fora de todo turno (bordas do diarizador): falante vizinho
    # mais próximo dentro de 2s, senão None (vira "Remotos")
    melhor, dmin = None, alcance
    for s, e, lbl in turnos:
        d = ini - e if ini >= e else s - fim  # distância fora do turno; <=0 é dentro
        if d <= 0:
            return lbl
        if d < dmin:
            melhor, dmin = lbl, d
    return melhor

def _agrupa(palavras, turnos):
    """Palavras -> eventos; falante de cada palavra = turno com maior sobreposição.
    Sem turnos (diarização falhou), tudo vira 'Remotos' como sempre."""
    ordem = {}
    if turnos:
        tot = {}
        for s, e, lbl in turnos:
            tot[lbl] = tot.get(lbl, 0.0) + (e - s)
        ordem = {lbl: i + 1 for i, (lbl, _) in
                 enumerate(sorted(tot.items(), key=lambda x: -x[1]))}
    evs = []
    for ini, fim, txt in palavras:
        best, melhor = None, 0.0
        for s, e, lbl in turnos or []:
            ov = min(fim, e) - max(ini, s)
            if ov > melhor:
                best, melhor = lbl, ov
        if not best and turnos:  # órfã (entre turnos): vizinho mais próximo
            best = _mais_proximo(ini, fim, turnos)
        rot = f"Remoto {ordem[best]}" if best else REMOTOS
        p = evs[-1] if evs else None
        if p and rot == p[2] and ini - p[1] <= GAP:
            p[1], p[3] = fim, (p[3] + " " + txt.strip()).strip()
        else:
            evs.append([ini, fim, rot, txt.strip()])
    return [tuple(e) for e in evs]

def _diariza(sinal):  # turnos (inicio, fim, SPEAKER_xx) do canal dos remotos, ou None
    try:
        import torch
        _hub_compat()
        from pyannote.audio import Pipeline
        pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1")
        pipe.to(torch.device("cuda" if torch.cuda.is_available() else "cpu"))
        print(f"diarizando ({torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu'})...",
              flush=True)
        dio = pipe({"waveform": torch.from_numpy(sinal)[None], "sample_rate": SR})
        # pyannote 4 devolve DiarizeOutput; o 3.x já devolve Annotation direto
        ann = getattr(dio, "exclusive_speaker_diarization", dio)
        return [(t.start, t.end, lbl) for t, _, lbl in ann.itertracks(yield_label=True)]
    except Exception as e:
        print(f"diarização falhou ({str(e)[:100]}) — remotos sem separação de vozes", flush=True)
        return None

def _relabel(evs, turnos):  # troca 'Remotos' pelo falante dominante de cada trecho
    tot = {}  # quem fala mais no total vira Remoto 1
    for s, e, lbl in turnos:
        tot[lbl] = tot.get(lbl, 0.0) + (e - s)
    ordem = {lbl: i + 1 for i, (lbl, _) in
             enumerate(sorted(tot.items(), key=lambda x: -x[1]))}
    saida = []
    for ini, fim, _, txt in evs:
        best, melhor = None, 0.0
        for s, e, lbl in turnos:  # falante com maior sobreposição com o trecho
            ov = min(fim, e) - max(ini, s)
            if ov > melhor:
                best, melhor = lbl, ov
        if not best:  # trecho entre turnos: vizinho mais próximo
            best = _mais_proximo(ini, fim, turnos)
        saida.append((ini, fim, f"Remoto {ordem[best]}" if best else REMOTOS, txt))
    return saida

def transcrever(caminho, engine="local", diarizar=False, idioma="pt"):
    """Recebe WAV/FLAC estéreo 48k int16 (canal L=mic='Você', R=sistema='Remotos').
    Escreve <mesmo_basename>.md no mesmo diretório (sobrescreve). Retorna o caminho do .md."""
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    caminho = os.path.abspath(caminho)
    if engine == "groq" and not os.environ.get("GROQ_API_KEY"):
        print("GROQ_API_KEY não configurada — nada foi transcrito")
        return None
    esq, direita = _canais(caminho)
    try:
        if engine == "groq":
            chave = os.environ["GROQ_API_KEY"]
            evs_rem = _groq(direita, REMOTOS, chave, idioma)
            if diarizar:  # groq não tem timestamps por palavra: relabel por evento
                turnos = _diariza(direita)
                if turnos:
                    evs_rem = _relabel(evs_rem, turnos)
            eventos = _groq(esq, VOCE, chave, idioma) + evs_rem
        else:
            evs_voce = _local(esq, VOCE, idioma)
            if diarizar:
                palavras = _local_palavras(direita, REMOTOS, idioma)
                evs_rem = _agrupa(palavras, _diariza(direita))
            else:
                evs_rem = _local(direita, REMOTOS, idioma)
            eventos = evs_voce + evs_rem
    except RuntimeError as e:  # falha de rede após retentativas: nada de traceback no CLI
        print(f"groq falhou ({str(e)[:100]}) — nada foi transcrito")
        return None
    if diarizar:
        n = len({e[2] for e in evs_rem if e[2].startswith("Remoto")})
        if n:
            print(f"remotos separados em {n} falante(s)", flush=True)
    md = os.path.splitext(caminho)[0] + ".md"
    _escreve_md(md, _mescla(eventos), os.path.splitext(os.path.basename(caminho))[0])
    print("ata salva em:", md, flush=True)
    return md

def main():
    a = sys.argv[1:]
    engine = "groq" if "--groq" in a else "local"
    idioma = a[a.index("--idioma") + 1] if "--idioma" in a and a.index("--idioma") + 1 < len(a) else "pt"
    args = [x for x in a if not x.startswith("--") and x != idioma]
    if len(args) != 1:
        print("uso: python reuniao_transcrever.py <arquivo.wav|.flac> [--groq] [--diarizar] [--idioma xx]")
        return 2
    return 1 if transcrever(args[0], engine, "--diarizar" in a, idioma) is None else 0

if __name__ == "__main__":
    sys.exit(main())
