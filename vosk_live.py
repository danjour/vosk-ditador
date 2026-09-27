"""Live PT transcription (console). Ctrl+C para parar."""
import json, os, queue
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
DEV = int(os.environ.get("VOSK_DEVICE", "21"))
print("modelo:", MODEL, flush=True)
rec = KaldiRecognizer(Model(MODEL), SR)
q = queue.Queue()
try:
    stream = sd.RawInputStream(samplerate=SR, blocksize=4000, dtype="int16",
                               channels=1, device=DEV, callback=lambda d, f, t, s: q.put(bytes(d)))
    stream.start()
except Exception as e:
    print(f"dev {DEV} falhou ({e}), usando default", flush=True)
    stream = sd.RawInputStream(samplerate=SR, blocksize=4000, dtype="int16",
                               channels=1, callback=lambda d, f, t, s: q.put(bytes(d)))
    stream.start()
print("OUVINDO... fale. Ctrl+C para parar.", flush=True)
with stream:
    try:
        while True:
            data = q.get()
            if rec.AcceptWaveform(data):
                t = json.loads(rec.Result()).get("text", "")
                if t:
                    print(">>", t, flush=True)
    except KeyboardInterrupt:
        print(">>", json.loads(rec.FinalResult()).get("text", ""), flush=True)
