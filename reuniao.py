"""Gravador de reunião: Ctrl+Alt+R liga/desliga a gravação de reunião (microfone +
áudio do sistema em estéreo, L=mic R=sistema); ao parar, comprime pra FLAC 48 kHz em
reunioes/<data-hora>.flac e dispara a transcrição sozinho (o encerramento espera a ata
sair). Ctrl+Alt+W encerra — de propósito NÃO é Ctrl+Alt+Q, que é do ditador (rodar os
dois juntos é o cenário normal). --teste-captura N roda o pipeline headless."""
import os, queue, sys, tempfile, threading, time, wave

_HERE = os.path.dirname(os.path.abspath(__file__))
DOT = 26  # px da bolinha vermelha
TOL = 0.2  # deriva de relógio abaixo disso não vira silêncio preenchido

# estado da gravação em curso (vazio no import — import reuniao é inócuo)
S = {"gravando": False, "root": None, "ov": None}

def _ts():
    return time.strftime("%Y-%m-%d-%H%M")

def _drena(fila):
    buf = bytearray()
    try:
        while True:
            buf += fila.get_nowait()
    except queue.Empty:
        pass
    return bytes(buf)

def _reamostra(pcm, de, para):  # reconexão pode achar device com taxa diferente do wave
    if de == para:
        return pcm
    import numpy as np, soxr
    return soxr.resample(np.frombuffer(pcm, dtype=np.int16), de, para).tobytes()

def _grava_rel(wav, pcm, sr, rel, agora):
    # escreve preenchendo com silêncio todo tempo morto (stream caiu, mic demorou a
    # abrir): os 2 canais ficam no mesmo relógio de parede — sem isso, uma queda do
    # loopback desloca o canal R contra o L dali em diante e a ata dessincroniza
    deficit = (agora - S["t0"]) - len(pcm) // 2 / sr - rel["escrito"]
    # o bloco cobre (agora-dur, agora]: o déficit é contra o INÍCIO dele
    if deficit > TOL:
        silencio = min(deficit, 3600.0)
        wav.writeframes(b"\x00\x00" * int(silencio * sr))
        rel["escrito"] += int(silencio * sr) / sr
    wav.writeframes(pcm)
    rel["escrito"] += len(pcm) // 2 / sr

def _sr_mic_inicial():
    import sounddevice as sd
    try:
        dev = int(os.environ["REUNIAO_MIC"]) if "REUNIAO_MIC" in os.environ \
            else sd.query_devices(kind="input")["index"]
        return int(sd.query_devices(dev)["default_samplerate"])
    except Exception:
        return 16000

def _sr_sys_inicial():
    try:
        import pyaudiowpatch as pyaudio
        p = pyaudio.PyAudio()
        try:
            return int(p.get_default_wasapi_loopback()["defaultSampleRate"])
        finally:
            p.terminate()
    except Exception:
        return 48000

def _mic_thread(wav, fila):
    import sounddevice as sd
    sr = S["sr_mic"]  # taxa do device em curso; se reabrir noutro device, grava reamostra

    def grava(bloco):
        if bloco:
            _grava_rel(wav, _reamostra(bloco, sr, S["sr_mic"]), S["sr_mic"],
                       S["rel_mic"], time.time())

    aviso = True  # mic morto no início não bloqueia: avisa 1x e segue só com o sistema
    while S["gravando"]:
        try:
            dev = int(os.environ["REUNIAO_MIC"]) if "REUNIAO_MIC" in os.environ \
                else sd.query_devices(kind="input")["index"]
            info = sd.query_devices(dev)
            sr = int(info["default_samplerate"])  # BT fala 16k, mic de placa 44.1/48k

            def cb(indata, frames, t, status):
                fila.put(bytes(indata))  # cdata de char: sem bytes() o array quebra por elemento

            st = sd.RawInputStream(samplerate=sr, channels=1, dtype="int16",
                                   device=dev, callback=cb)
            st.start()  # é aqui que o BT morto falha, não no construtor
        except Exception as e:
            print((f"mic não abriu ({str(e)[:60]}) — gravando só o sistema" if aviso else
                   f"mic não abriu ({str(e)[:60]}) — tentando de novo..."), flush=True)
            aviso = False
            time.sleep(3)
            continue
        aviso = False
        print(f"mic: [{info['index']}] {info['name']} @ {sr} Hz", flush=True)
        try:
            with st:
                while S["gravando"] and st.active:
                    time.sleep(1)
                    grava(_drena(fila))
        except Exception as e:
            print(f"mic caiu ({str(e)[:60]}) — tentando reabrir...", flush=True)
    grava(_drena(fila))

def _mono(data, ch):  # downmix N canais -> mono (média das amostras)
    import numpy as np
    a = np.frombuffer(data, dtype=np.int16)
    a = a[:len(a) // ch * ch].astype(np.int32).reshape(-1, ch)
    return np.clip(a.mean(axis=1), -32768, 32767).astype(np.int16).tobytes()

def _sys_thread(wav, fila):
    import pyaudiowpatch as pyaudio
    sr = S["sr_sys"]  # taxa do wave; se reabrir noutro output, grava reamostra

    def grava(bloco):
        if bloco:
            _grava_rel(wav, _reamostra(bloco, sr, S["sr_sys"]), S["sr_sys"],
                       S["rel_sys"], time.time())

    while S["gravando"]:
        p = st = None
        try:
            p = pyaudio.PyAudio()
            dev = p.get_default_wasapi_loopback()  # reabre sempre o output padrão ATUAL
            ch, sr = int(dev["maxInputChannels"]), int(dev["defaultSampleRate"])
            st = p.open(format=pyaudio.paInt16, channels=ch, rate=sr, input=True,
                        input_device_index=int(dev["index"]))
            print(f"sistema (loopback): [{dev['index']}] {dev['name']} @ {sr} Hz", flush=True)
            while S["gravando"]:
                fila.put(_mono(st.read(int(sr * 0.5), exception_on_overflow=False), ch))
                grava(_drena(fila))
        except Exception as e:
            if S["gravando"]:
                print(f"loopback caiu ({str(e)[:60]}) — reabrindo em 2 s...", flush=True)
                time.sleep(2)  # fones BT podem ter entrado/saído: o padrão pode ter trocado
        finally:
            if st:
                try:
                    st.stop_stream()
                    st.close()
                except Exception:
                    pass
            if p:
                p.terminate()
            grava(_drena(fila))
    grava(_drena(fila))

def iniciar_gravacao():
    tmp = tempfile.gettempdir()
    S["ts"] = _ts()
    S["pmic"] = os.path.join(tmp, f"reuniao_mic_{S['ts']}.wav")
    S["psys"] = os.path.join(tmp, f"reuniao_sys_{S['ts']}.wav")
    S["q_mic"], S["q_sys"] = queue.Queue(), queue.Queue()
    S["sr_mic"] = _sr_mic_inicial()
    S["sr_sys"] = _sr_sys_inicial()
    for chave, caminho, sr in (("f_mic", S["pmic"], S["sr_mic"]),
                               ("f_sys", S["psys"], S["sr_sys"])):
        S[chave] = wave.open(caminho, "wb")
        S[chave].setnchannels(1)
        S[chave].setsampwidth(2)
        S[chave].setframerate(sr)
    S["gravando"] = True
    S["t0"] = time.time()  # relógio de parede que os 2 canais seguem (ver _grava_rel)
    S["rel_mic"] = {"escrito": 0.0}
    S["rel_sys"] = {"escrito": 0.0}
    S["t_mic"] = threading.Thread(target=_mic_thread, args=(S["f_mic"], S["q_mic"]), daemon=True)
    S["t_sys"] = threading.Thread(target=_sys_thread, args=(S["f_sys"], S["q_sys"]), daemon=True)
    S["t_mic"].start()
    S["t_sys"].start()
    print("gravando (mic + sistema)", flush=True)
    if S["ov"]:
        S["ov"].deiconify()

def _mescla(pmic, psys):
    import numpy as np, soxr, soundfile as sf
    def mono(caminho):
        try:
            pcm, sr = sf.read(caminho, dtype="int16")
        except Exception:
            return np.zeros(0, dtype=np.int16)  # lado morto (ex.: sem mic) não derruba a ata
        if pcm.ndim > 1:  # por garantia; gravamos mono
            pcm = pcm.mean(axis=1).astype(np.int16)
        return pcm if len(pcm) == 0 else soxr.resample(pcm, sr, 48000)
    m, s = mono(pmic), mono(psys)
    n = max(len(m), len(s))
    est = np.zeros((n, 2), dtype=np.int16)  # L=mic, R=sistema; o mais curto cala
    est[:len(m), 0] = m
    est[:len(s), 1] = s
    return est

def finalize_gravacao(destino=None):
    normal = destino is None  # destino só é passado no teste-captura, que não transcreve
    if not S["gravando"]:
        return None
    S["gravando"] = False
    S["t_mic"].join(timeout=15)
    S["t_sys"].join(timeout=15)
    for f in (S["f_mic"], S["f_sys"]):
        try:
            f.close()
        except Exception:
            pass
    if S["ov"]:
        S["ov"].withdraw()
    print("pausado", flush=True)
    try:
        est = _mescla(S["pmic"], S["psys"])
        import soundfile as sf
        if destino is None:
            pasta = os.path.join(_HERE, "reunioes")
            destino = os.path.join(pasta, S["ts"] + ".flac")
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        sf.write(destino, est, 48000, subtype="PCM_16")
    except Exception as e:
        print(f"falha ao gerar o FLAC ({str(e)[:100]}) — temporários mantidos:",
              S["pmic"], S["psys"], flush=True)
        return None
    for p in (S["pmic"], S["psys"]):
        try:
            os.remove(p)
        except OSError:
            pass
    print("ata:", destino, flush=True)
    if normal:
        S["t_proc"] = threading.Thread(target=_transcreve, args=(destino,), daemon=True)
        S["t_proc"].start()
    return destino

def _transcreve(flac):
    try:
        import reuniao_transcrever  # pesado (faster_whisper): só na hora de usar
        md = reuniao_transcrever.transcrever(flac, "local")
    except Exception as e:
        print(f"transcrição falhou ({str(e)[:100]}) — o FLAC ficou em {flac}", flush=True)
        return
    if not md:
        return
    try:  # resumo é best-effort: a ata já está salva no disco
        import reuniao_resumo
        reuniao_resumo.resumir(md)
    except Exception as e:
        print(f"resumo falhou ({str(e)[:100]}) — a ata ficou em {md}", flush=True)

def alternar():
    if S["gravando"]:
        finalize_gravacao()
    else:
        iniciar_gravacao()

def _sair():
    finalize_gravacao()  # se estiver gravando, finaliza antes de encerrar
    t = S.get("t_proc")
    if t and t.is_alive():  # encerrar agora mataria a ata/resumo no meio
        print("aguardando ata e resumo terminarem (~1 min por hora de reunião; "
              "Ctrl+C no console força)...", flush=True)
        t.join(timeout=900)  # teto de segurança; se estourar, o FLAC fica pra rodar manual
    S["root"].destroy()

def _teste_captura(segundos):
    destino = os.path.join(tempfile.gettempdir(), "reuniao_teste", "teste-captura.flac")
    print(f"teste de captura: gravando {segundos} s (toque algo no sistema)...", flush=True)
    iniciar_gravacao()
    time.sleep(segundos)
    flac = finalize_gravacao(destino=destino)
    if flac is None:
        sys.exit(1)
    import numpy as np, soundfile as sf
    est, sr = sf.read(flac, dtype="int16")
    rms = lambda c: float(np.sqrt(np.mean(c.astype(np.float64) ** 2))) if len(c) else 0.0
    print(f"flac: {sr} Hz, {est.shape[1]} canal(is), {len(est) / sr:.1f} s", flush=True)
    print(f"RMS L (mic): {rms(est[:, 0]):.0f} | RMS R (sistema): {rms(est[:, 1]):.0f}", flush=True)
    print("flac em:", flac, flush=True)

def _cli_modulo(modulo, argv):
    sys.argv = [modulo] + argv  # o main() do módulo lê sys.argv
    try:
        m = __import__(modulo)
    except ImportError:
        print(f"{modulo}.py não encontrado ao lado do reuniao.py")
        return 1
    return m.main()

def _app():
    import keyboard
    import tkinter as tk
    # Q fica pro ditador: os dois apps rodam juntos, e um Ctrl+Alt+Q não pode parar
    # a gravação da reunião sem ninguém perceber
    hk_g = os.environ.get("REUNIAO_HOTKEY_GRAVAR", "ctrl+alt+r")
    hk_s = os.environ.get("REUNIAO_HOTKEY_SAIR", "ctrl+alt+w")
    root = tk.Tk(); root.withdraw()
    ov = tk.Toplevel(root)
    ov.overrideredirect(True); ov.attributes("-topmost", True)
    ov.configure(bg="black"); ov.attributes("-transparentcolor", "black")
    ov.geometry(f"{DOT}x{DOT}+{root.winfo_screenwidth() - DOT - 12}"
                f"+{root.winfo_screenheight() - DOT - 12}")
    cv = tk.Canvas(ov, width=DOT, height=DOT, bg="black", highlightthickness=0)
    cv.create_oval(3, 3, DOT - 3, DOT - 3, fill="red", outline="")
    cv.pack()
    ov.withdraw()  # só aparece durante a gravação
    S["root"], S["ov"] = root, ov
    keyboard.add_hotkey(hk_g, lambda: root.after(0, alternar))
    keyboard.add_hotkey(hk_s, lambda: root.after(0, _sair))
    print(f"Pronto. {hk_g} grava/pausa a reunião, {hk_s} encerra "
          "(o ditador segue em Ctrl+Alt+E / Ctrl+Alt+Q).", flush=True)
    root.mainloop()

def _main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_HERE, ".env"))  # REUNIAO_MIC etc. podem vir do .env
    a = sys.argv[1:]
    if "--teste-captura" in a:
        i = a.index("--teste-captura")
        _teste_captura(int(a[i + 1]) if i + 1 < len(a) else 10)
        return 0
    if a[:1] == ["transcrever"]:
        return _cli_modulo("reuniao_transcrever", a[1:])
    if a[:1] == ["resumir"]:
        return _cli_modulo("reuniao_resumo", a[1:])
    _app()
    return 0

if __name__ == "__main__":
    sys.exit(_main())
