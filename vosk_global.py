"""Ditador global offline (Whisper medium local): Ctrl+Alt+E ouve; ao pausar a fala,
cola a frase pontuada no app focado. Ctrl+Alt+Q encerra. --preview mostra só o orbe."""
import array, math, os, queue, random, sys, threading, time
import tkinter as tk  # ponytail: stdlib p/ overlay + clipboard
from PIL import Image, ImageDraw, ImageFilter, ImageTk  # ponytail: só o visual do orbe
import keyboard
import sounddevice as sd

PREVIEW = "--preview" in sys.argv  # preview: só o orbe animado, sem modelo/mic
_HERE = os.path.dirname(os.path.abspath(__file__))
try:  # VOSK_DEVICE etc. podem vir do .env do projeto (não sobrescreve o shell)
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_HERE, ".env"))
except ImportError:
    pass
SR = 16000
DEVS = [int(os.environ.get("VOSK_DEVICE", "21")), None]  # ponytail: AirPods 16k nativo; None=default

# ponytail: heuristica p/ "?" mantida só p/ test_pontuar; o Whisper já pontua nativamente
_Q = ("quem", "o que", "oque", "qual", "quais", "quando", "onde", "como",
      "quanto", "quantos", "quanta", "quantas", "por que", "porque", "cadê", "cade")
def pontuar(t):
    t = t.strip()
    if not t:
        return t
    t = t[0].upper() + t[1:]
    return t + ("?" if t.lower().startswith(_Q) else ".")

# ---------- motor: faster-whisper medium (GPU int8_float16, CPU int8 de fallback) ----------
_WHISPER = None
def _prep_cuda():  # DLLs do cudnn/cublas instaladas via pip não estão no PATH por padrão
    import glob, importlib.util
    spec = importlib.util.find_spec("nvidia")
    if not spec:
        return
    base = spec.submodule_search_locations[0]
    for d in glob.glob(os.path.join(base, "*", "bin")):
        os.add_dll_directory(d)
        os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]

def get_model():
    global _WHISPER
    if _WHISPER is None:
        import numpy as np
        from faster_whisper import WhisperModel
        _prep_cuda()
        for dev, ct in (("cuda", "int8_float16"), ("cpu", "int8")):
            try:
                m = WhisperModel("medium", device=dev, compute_type=ct)
                # aquecimento: erro de kernel/GPU aparece aqui, não na 1ª frase
                m.transcribe(np.zeros(SR // 2, dtype=np.float32), language="pt")
                _WHISPER = m
                print("whisper medium em", dev, flush=True)
                break
            except Exception as e:
                print(f"whisper {dev} falhou ({str(e)[:80]}), tentando próximo...", flush=True)
        if _WHISPER is None:
            raise SystemExit("nenhum backend whisper disponível")
    return _WHISPER

def _transcreve(buf):
    if len(buf) < SR * 2 * 0.3:  # menos de 0.3s de áudio: ruído/click
        return
    import numpy as np
    pcm = np.frombuffer(bytes(buf), dtype=np.int16).astype(np.float32) / 32768.0
    try:
        segs, _ = get_model().transcribe(pcm, language="pt", beam_size=3,
                                         vad_filter=True,
                                         condition_on_previous_text=False,
                                         without_timestamps=True)
        txt = " ".join(s.text.strip() for s in segs).strip()
    except Exception as e:
        print("whisper falhou:", str(e)[:100], flush=True)
        return
    if txt:
        paste_q.put(txt)
        print(">>", txt, flush=True)

listening = False
audio_q, paste_q = queue.Queue(), queue.Queue()
BUF = bytearray()  # áudio da frase em curso (int16 mono 16k)
state = {"flash_until": 0.0, "level": 0.0, "level_raw": 0.0, "e": 0.25,
         "sil": 0.006, "sp": 0.012, "calib": None, "amb": None,
         "falando": False, "ult": 0.0}

root = tk.Tk(); root.withdraw()
ov = tk.Toplevel(root)
ov.overrideredirect(True); ov.attributes("-topmost", True)
ov.configure(bg="black"); ov.attributes("-transparentcolor", "black")
ov.attributes("-alpha", 0.8)  # translucidez real: o fundo da tela atravessa o orbe
S = 140
ov.geometry(f"{S}x{S}+{root.winfo_screenwidth()-S-20}+{root.winfo_screenheight()-S-60}")
cv = tk.Canvas(ov, width=S, height=S, bg="black", highlightthickness=0)
cv.pack()

# ponytail: orbe estilo ChatGPT — gradiente vertical branco->azul com blur + glow,
# pré-renderizado em 96 quadros de "energia". A animação é interpolação contínua de
# uma deriva pseudo-aleatória (fases sorteadas por sessão) + nível de voz (RMS),
# com borda que ondula e luz interna que nada. Nada disso mexe no áudio do Whisper.
def orb_img(e, top, bot, wob=0.0):
    r = 30 + 18 * e
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    glow = Image.new("L", (S, S), 0)
    ImageDraw.Draw(glow).ellipse([S/2-r-6, S/2-r-6, S/2+r+6, S/2+r+6], fill=110)
    img.paste(Image.new("RGBA", (S, S), bot + (255,)), (0, 0),
              glow.filter(ImageFilter.GaussianBlur(9)))
    a, b = 0.10 + 0.15 * e, 0.62 + 0.15 * e  # faixa do degradê desce com a energia
    grad = Image.new("L", (1, S))
    grad.putdata([int(255 * max(0.0, min(1.0, (y/(S-1)-a)/(b-a)))) for y in range(S)])
    grad = grad.resize((S, S)).filter(ImageFilter.GaussianBlur(7))
    orb = Image.composite(Image.new("RGB", (S, S), bot),  # mask 0 (topo) -> top
                          Image.new("RGB", (S, S), top), grad).convert("RGBA")
    # brilho interno fora de centro (gira com a energia): interior vivo, nao foto
    hx = S/2 + 0.34*r*math.cos(2.1*e + 0.8)
    hy = S/2 - 0.30*r*math.sin(1.7*e)
    hl = Image.new("L", (S, S), 0)
    ImageDraw.Draw(hl).ellipse([hx-r*0.45, hy-r*0.45, hx+r*0.45, hy+r*0.45], fill=80)
    orb.paste((255, 255, 255), (0, 0), hl.filter(ImageFilter.GaussianBlur(10)))
    mask = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(mask)
    ww = wob * (0.55 + 0.9 * e / E_MAX)  # quanto mais voz, mais ondula a borda
    pts = []  # borda ondulada (blob liquido); a fase varia com a energia entre quadros
    for k in range(64):
        th = 6.283185 * k / 64
        rr = r * (1 + ww*math.sin(3*th + 9*e) + 0.6*ww*math.sin(5*th - 18*e))
        pts.append((S/2 + rr*math.cos(th), S/2 + rr*math.sin(th)))
    d.polygon(pts, fill=255)
    orb.putalpha(mask.filter(ImageFilter.GaussianBlur(2.0)))
    img.alpha_composite(orb)
    return img

E_MAX = 1.28
N_F = 96
FRAMES = [ImageTk.PhotoImage(orb_img(E_MAX*i/(N_F-1), (248, 251, 255), (18, 102, 235),
                                     wob=0.04)) for i in range(N_F)]
IDLE_FR = [ImageTk.PhotoImage(orb_img(0.20 + 0.06*math.sin(6.283*i/32),
                                      (168, 178, 192), (86, 96, 116), wob=0.03))
           for i in range(32)]
FLASH_FR = [ImageTk.PhotoImage(orb_img(e, (236, 255, 240), (34, 210, 110), wob=0.02))
            for e in (0.50, 0.68, 0.86, 0.68)]
PH = [random.uniform(0, 6.283) for _ in range(3)]
NTK = [0]  # ticks desde o último diag (preview)
orb = cv.create_image(S/2, S/2, image=IDLE_FR[0])

def _hl_img():  # luz difusa que nada dentro do orbe (item separado, animado por tempo)
    m = Image.new("L", (56, 56), 0)
    ImageDraw.Draw(m).ellipse([8, 8, 48, 48], fill=90)
    im = Image.new("RGBA", (56, 56), (0, 0, 0, 0))
    im.paste((255, 255, 255), (0, 0), m.filter(ImageFilter.GaussianBlur(8)))
    return ImageTk.PhotoImage(im)
HL_IMG = _hl_img()
hl = cv.create_image(S/2, S/2, image=HL_IMG)
if PREVIEW:  # amostras do orbe p/ conferir o render sem abrir o app
    import tempfile
    for _e in (0.05, 0.6, 1.2):
        orb_img(_e, (248, 251, 255), (18, 102, 235), wob=0.04).save(
            os.path.join(tempfile.gettempdir(), f"orb_e{int(_e*100):03d}.png"))

def tick():
    now = time.time()
    NTK[0] += 1
    t, ph = now, PH
    if now < state["flash_until"]:
        k = min(3, int((0.4 - (state["flash_until"] - now)) / 0.4 * 4))
        cv.itemconfig(orb, image=FLASH_FR[k])
        e = max(0.06, min(E_MAX, state["e"]))
    elif listening:
        if PREVIEW:  # voz fake: passeio aleatório que oscila em torno do meio
            state["level_raw"] = max(0.0, min(1.0, state["level_raw"]
                                              + (0.35-state["level_raw"])*0.3
                                              + random.uniform(-0.3, 0.3)))
        raw = min(1.0, 9*state["level_raw"])
        # ballística de VU: sobe rápido quando a voz chega, desce devagar
        state["level"] += (raw - state["level"]) * (0.5 if raw > state["level"] else 0.06)
        # deriva pseudo-aleatória (senos de períodos incomensuráveis, fases por sessão)
        # mantém o orbe azul e quieto; a voz (level) é que o faz inchar e clarear
        alvo = (0.25 + 0.15*math.sin(t*2.1 + ph[0]) + 0.10*math.sin(t*3.7 + ph[1])
                + 0.08*math.sin(t*0.83 + ph[2]) + 1.1*state["level"])
        state["e"] += (alvo - state["e"]) * 0.18  # interpolação contínua: sem degraus
        e = max(0.06, min(E_MAX, state["e"]))
        # shimmer: oscilação sempre presente, pra nunca parecer uma foto parada
        e += (0.04*math.sin(t*4.4 + ph[0]) + 0.03*math.sin(t*2.6 + ph[1])) * 0.5
        cv.itemconfig(orb, image=FRAMES[min(N_F-1, int(e/E_MAX*(N_F-1)))])
    else:
        cv.itemconfig(orb, image=IDLE_FR[int(now*0.5 % 1 * 32)])  # respiração lenta
        e = 0.22
    rr = 30 + 18 * e  # a luz nada dentro do blob, vivo até em silêncio
    cv.coords(hl, S/2 + rr*0.30*math.sin(t*0.9 + ph[2]),
                 S/2 + rr*0.26*math.sin(t*0.63 + ph[1]))
    while not paste_q.empty():  # main thread: clipboard + Ctrl+V no app focado
        t = paste_q.get()
        root.clipboard_clear(); root.clipboard_append(t); root.update()
        time.sleep(0.05)
        keyboard.send("ctrl+v")
        state["flash_until"] = time.time() + 0.4
        print(">>", t, flush=True)
    ov.after(16, tick)

def toggle():
    global listening
    listening = not listening
    if listening:
        get_model()  # 1ª vez carrega o medium (pode levar alguns segundos)
        state["amb"], state["calib"] = [], time.time() + 0.6  # calibra ruído ambiente
        BUF.clear()
        while not audio_q.empty():
            audio_q.get()  # descarta audio velho
    print("OUVINDO (whisper medium)" if listening else "pausado", flush=True)

def worker():
    def cb(indata, frames, tinfo, status):
        if listening:
            pcm = bytes(indata)  # cdata de char: sem bytes() o array quebra por elemento
            audio_q.put(pcm)
            m = array.array("h", pcm)[::8]  # RMS de 1/8 das amostras, só p/ orbe+VAD
            acc = 0
            for v in m:
                acc += v * v
            state["level_raw"] = (acc / len(m)) ** 0.5 / 32768
    stream = None  # ponytail: BT dorme — tenta devices em ordem, repete até achar
    while stream is None:
        for dev in DEVS:
            try:
                s = sd.RawInputStream(samplerate=SR, blocksize=4000, dtype="int16",
                                      channels=1, device=dev, callback=cb)
                s.start()  # é aqui que o BT morto falha, não no construtor
                stream = s
                print("mic:", dev if dev else "default", flush=True)
                break
            except Exception as e:
                print(f"mic {dev} falhou ({str(e)[:80]}), tentando próximo...", flush=True)
        if stream is None:
            time.sleep(3)
    with stream:
        while True:
            drenou = False
            try:
                while True:
                    BUF += audio_q.get_nowait()
                    drenou = True
            except queue.Empty:
                pass
            if not drenou:
                time.sleep(0.02)
            lvl, agora = state["level_raw"], time.time()
            if not listening:
                if len(BUF) > SR * 2 * 0.5:  # sobrou fala ao pausar: cospe antes de parar
                    _transcreve(BUF)
                BUF.clear()
                state["falando"] = False
                continue
            if state["calib"]:  # primeiros 0.6s medem o silêncio deste micro
                if agora < state["calib"]:
                    state["amb"].append(lvl)
                    BUF.clear()
                    continue
                amb = max(state["amb"] or [0.0])
                state["sil"] = max(0.004, amb * 2.5)
                state["sp"] = state["sil"] * 1.8
                state["calib"] = None
                print(f"calibrado: silêncio < {state['sil']:.4f}", flush=True)
                continue
            if lvl > state["sp"]:
                state["falando"] = True
                state["ult"] = agora
            elif (state["falando"] and agora - state["ult"] > 0.7
                  and len(BUF) > SR * 2 * 0.5):
                _transcreve(BUF); BUF.clear(); state["falando"] = False
            elif len(BUF) > SR * 2 * 10:  # monólogo longo: descarrega a cada ~10s
                _transcreve(BUF); BUF.clear(); state["falando"] = False

if PREVIEW:
    listening = True
    print("preview: orbe pronto", flush=True)
    root.after(12000, root.destroy)  # preview se fecha sozinho
    def _diag():
        print(f"diag: ticks={NTK[0]}/s geom={ov.geometry()} visible={ov.winfo_viewable()}",
              flush=True)
        NTK[0] = 0
    root.after(1000, _diag)
    root.after(3000, _diag)
else:
    keyboard.add_hotkey("ctrl+alt+e", toggle)
    keyboard.add_hotkey("ctrl+alt+q", lambda: root.after(0, root.destroy))
    print("Pronto. Ctrl+Alt+E ouve/pausa, Ctrl+Alt+Q encerra.", flush=True)
    threading.Thread(target=worker, daemon=True).start()
ov.after(16, tick)
root.mainloop()
