"""Ditador global offline (só Vosk): Ctrl+Alt+E ouve, frase pontuada cola no app focado. Ctrl+Alt+Q encerra."""
import json, math, os, queue, threading, time
import tkinter as tk  # ponytail: stdlib p/ overlay + clipboard, sem dep extra
import keyboard
import sounddevice as sd
from vosk import Model, KaldiRecognizer

_HERE = os.path.dirname(os.path.abspath(__file__))
def _find_model():
    cands = [os.environ.get("VOSK_MODEL") or "",
             os.path.join(_HERE, "models", "vosk-model-pt-fb-v0.1.1-20220516_2113"),
             os.path.join(_HERE, "models", "vosk-model-small-pt-0.3")]
    for p in cands:
        if p and os.path.isdir(p):
            return p
    raise SystemExit("modelo não achado — rode: python setup_model.py [--big]")
MODEL = _find_model()
SR = 16000
DEVS = [int(os.environ.get("VOSK_DEVICE", "21")), None]  # ponytail: AirPods 16k nativo; None=default

# ponytail: heuristica p/ "?" — cobre pergunta direta; JEV-noul ou LLM local se precisar de entonação
_Q = ("quem", "o que", "oque", "qual", "quais", "quando", "onde", "como",
      "quanto", "quantos", "quanta", "quantas", "por que", "porque", "cadê", "cade")
def pontuar(t):
    t = t.strip()
    if not t:
        return t
    t = t[0].upper() + t[1:]
    return t + ("?" if t.lower().startswith(_Q) else ".")

print("modelo:", MODEL, flush=True)
model = Model(MODEL)
listening, rec = False, None
audio_q, paste_q = queue.Queue(), queue.Queue()
state = {"flash_until": 0.0}

root = tk.Tk(); root.withdraw()
ov = tk.Toplevel(root)
ov.overrideredirect(True); ov.attributes("-topmost", True)
ov.configure(bg="black"); ov.attributes("-transparentcolor", "black")
S = 90
ov.geometry(f"{S}x{S}+{root.winfo_screenwidth()-S-20}+{root.winfo_screenheight()-S-60}")
cv = tk.Canvas(ov, width=S, height=S, bg="black", highlightthickness=0)
cv.pack()
orb = cv.create_oval(0, 0, 0, 0, fill="gray30", outline="")

def tick():
    now = time.time()
    if now < state["flash_until"]:
        color, r = "lawn green", 30
    elif listening:
        r = 24 + 8 * math.sin(now * 6)  # pulso estilo Siri
        color = "dodger blue"
    else:
        color, r = "gray30", 20
    cv.coords(orb, S/2-r, S/2-r, S/2+r, S/2+r)
    cv.itemconfig(orb, fill=color)
    while not paste_q.empty():  # main thread: clipboard + Ctrl+V no app focado
        t = paste_q.get()
        root.clipboard_clear(); root.clipboard_append(t); root.update()
        time.sleep(0.05)
        keyboard.send("ctrl+v")
        state["flash_until"] = time.time() + 0.4
        print(">>", t, flush=True)
    ov.after(50, tick)

def toggle():
    global listening, rec
    listening = not listening
    if listening:
        rec = KaldiRecognizer(model, SR)
        while not audio_q.empty():
            audio_q.get()  # descarta audio velho
    print("OUVINDO" if listening else "pausado", flush=True)

def worker():
    def cb(indata, frames, tinfo, status):
        if listening:
            audio_q.put(bytes(indata))
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
            data = audio_q.get()
            if rec is not None and rec.AcceptWaveform(data):
                t = json.loads(rec.Result()).get("text", "").strip()
                if t and listening:
                    paste_q.put(pontuar(t))

keyboard.add_hotkey("ctrl+alt+e", toggle)
keyboard.add_hotkey("ctrl+alt+q", lambda: root.after(0, root.destroy))
print("Pronto. Ctrl+Alt+E ouve/pausa, Ctrl+Alt+Q encerra.", flush=True)
threading.Thread(target=worker, daemon=True).start()
ov.after(50, tick)
root.mainloop()
